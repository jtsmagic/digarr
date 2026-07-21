"""
MCP server exposing Digarr's playlist import/search functionality to MCP clients
(e.g. Claude Desktop) running on the same machine as the Digarr container.

Runs as a separate process from the main FastAPI app (invoked via `docker exec`),
but reuses the exact same business-logic functions from main.py directly — no HTTP
calls, no auth tokens. Reads (playlists, config, library cache) go straight to the
shared SQLite DB / config.json on disk. Actions (import, refresh, sync) call the
same async functions the web UI's routes call, awaited synchronously instead of
fired off as a background job, since there is no long-lived process here to poll.

Launch (from the container, CWD=/app):
    python -m mcp_server.server
"""
from typing import Optional

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

import main as digarr
from ai.errors import AIProviderError
from utils import deduplicate_artists

digarr.init_db()

mcp = FastMCP("digarr")


async def _unwrap(coro):
    """Run an awaitable from main.py, turning its HTTPException into a plain error message."""
    try:
        return await coro
    except HTTPException as e:
        raise ValueError(e.detail) from None


def _playlist_summary(pl: dict) -> dict:
    """
    Summarize a row from get_playlists(), which omits the full artists/tracks lists
    for performance — use get_playlist(id) for those.
    """
    return {
        "id": pl["id"],
        "name": pl["name"],
        "source_type": pl.get("source_type"),
        "source_url": pl.get("source_url"),
        "artists_added_count": len(pl.get("artists_added") or []),
        "plex": {"synced": bool(pl.get("plex_playlist_id")), "matched": pl.get("plex_matched_count"), "total": pl.get("plex_total_count")},
        "jellyfin": {"synced": bool(pl.get("jellyfin_playlist_id")), "matched": pl.get("jellyfin_matched_count"), "total": pl.get("jellyfin_total_count")},
        "navidrome": {"synced": bool(pl.get("navidrome_playlist_id")), "matched": pl.get("navidrome_matched_count"), "total": pl.get("navidrome_total_count")},
        "created_at": pl.get("created_at"),
        "last_refreshed_at": pl.get("last_refreshed_at"),
    }


@mcp.tool()
def list_playlists() -> list[dict]:
    """List every playlist Digarr has imported, with sync status per media server."""
    return [_playlist_summary(p) for p in digarr.get_playlists()]


@mcp.tool()
def get_playlist(playlist_id: int) -> dict:
    """Get full details for one Digarr playlist, including its track list."""
    pl = digarr.get_playlist(playlist_id)
    if not pl:
        raise ValueError(f"Playlist {playlist_id} not found")
    return pl


@mcp.tool()
async def parse_source(input_type: str, content: str, playlist_name: Optional[str] = None) -> dict:
    """
    Deterministically extract a playlist's artists/tracks via Digarr's own parsers —
    NOT an AI call for Spotify playlist URLs or M3U content (both auto-detected here).
    Prefer this tool for those two cases, since it reuses exact structured data instead
    of re-deriving it.

    For any other URL or pasted text, DO NOT call this tool — it would route through
    Digarr's own separately-configured AI provider (Claude/OpenAI), which may not be
    configured or funded. Instead, fetch/read the content yourself and build the
    artists/tracks lists directly, then pass them straight to import_playlist.

    input_type: "url" or "text".
    """
    if input_type not in ("url", "text"):
        raise ValueError('input_type must be "url" or "text"')

    config = digarr.load_config()

    if input_type == "url":
        spotify_playlist_id = digarr.extract_playlist_id(content)
        if spotify_playlist_id:
            token = await digarr.get_oauth_token(config)
            if not token:
                client_id = config.get("spotify_client_id", "")
                client_secret = config.get("spotify_client_secret", "")
                if not client_id or not client_secret:
                    raise ValueError("Spotify not configured in Digarr Settings.")
                if spotify_playlist_id.startswith("37i9dQZF1E"):
                    raise ValueError(
                        "This is a Spotify-curated playlist (Discover Weekly, Daily Mix, etc.); "
                        "connect your Spotify account in Digarr Settings to import it."
                    )
                token = await digarr.get_access_token(client_id, client_secret)
            data = await digarr.fetch_playlist(spotify_playlist_id, token)
            return {
                "artists": data["artists"],
                "tracks": data["tracks"],
                "raw_source": content,
                "playlist_name": playlist_name or data["name"],
                "detected_source_type": "spotify",
            }

    text = content
    if input_type == "url":
        text = await digarr.fetch_url_content(content)

    if text.strip().startswith("#EXTM3U"):
        tracks = digarr.parse_m3u_content(text)
        artists = list({t["artist"]: {"name": t["artist"]} for t in tracks if t.get("artist")}.values())
        return {
            "artists": artists,
            "tracks": tracks,
            "raw_source": text[:500],
            "playlist_name": playlist_name,
            "detected_source_type": "m3u_url" if input_type == "url" else "file",
        }

    try:
        ai = digarr.make_ai_provider(config)
        result = await ai.extract_artists_and_tracks(text)
    except ValueError as e:
        raise ValueError(str(e)) from None
    except AIProviderError as e:
        raise ValueError(f"{e.provider} error: {e}") from None
    result["artists"] = deduplicate_artists(result.get("artists", []))
    result["playlist_name"] = playlist_name
    result["detected_source_type"] = input_type
    return result


@mcp.tool()
async def import_playlist(
    name: str,
    artists: list[dict],
    tracks: list[dict],
    source_url: Optional[str] = None,
    source_type: str = "url",
    sync_targets: Optional[list[str]] = None,
) -> dict:
    """
    Actually create the playlist in Digarr: adds every artist to Lidarr and pushes the
    playlist to any configured media servers (Plex/Jellyfin/Navidrome) and Spotify/Deemix
    if enabled. Call parse_source first to get the artists/tracks lists to pass in here.

    sync_targets restricts which media servers to push to (e.g. ["plex"]); omit for
    every configured target.
    """
    req = digarr.ImportJobRequest(
        artists=artists,
        tracks=tracks,
        playlist_name=name,
        source_url=source_url,
        source_type=source_type,
        include_in_refresh=True,
        sync_targets=sync_targets or [],
    )
    artist_names = [a["name"] if isinstance(a, dict) else a for a in req.artists]
    playlist_id = digarr.save_playlist(
        name=name,
        source_url=req.source_url,
        source_type=req.source_type,
        artists=artist_names,
        tracks=req.tracks,
        artists_added=[],
        lidarr_results=[],
    )
    job = digarr._new_job(name, len(req.artists))
    job["playlist_id"] = playlist_id
    digarr._jobs[job["id"]] = job
    digarr.db_save_import_job(job)
    await digarr._run_import_job(job["id"], req, playlist_id)
    return digarr._jobs[job["id"]]


@mcp.tool()
async def refresh_playlist(playlist_id: int) -> dict:
    """Re-fetch a playlist's original source and add any new artists/tracks found."""
    return await _unwrap(digarr._do_refresh_playlist(playlist_id))


_SYNC_FUNCS = {
    "plex": (digarr.sync_plex_playlist, digarr.sync_all_plex),
    "jellyfin": (digarr.sync_jellyfin_playlist, digarr.sync_all_jellyfin),
    "navidrome": (digarr.sync_navidrome_playlist, digarr.sync_all_navidrome),
}


@mcp.tool()
async def sync_playlist(playlist_id: int, target: str) -> dict:
    """Push/re-push one playlist to a media server. target: plex, jellyfin, or navidrome."""
    if target not in _SYNC_FUNCS:
        raise ValueError(f"Unknown target {target!r}; must be plex, jellyfin, or navidrome")
    return await _unwrap(_SYNC_FUNCS[target][0](playlist_id))


@mcp.tool()
async def sync_all(target: str) -> dict:
    """Push/re-push every eligible playlist to a media server. target: plex, jellyfin, or navidrome."""
    if target not in _SYNC_FUNCS:
        raise ValueError(f"Unknown target {target!r}; must be plex, jellyfin, or navidrome")
    return await _unwrap(_SYNC_FUNCS[target][1]())


@mcp.tool()
async def search_library(query: str, source: str = "plex", limit: int = 20) -> dict:
    """Search your media library (Plex/Jellyfin/Navidrome track cache) for a song or artist."""
    return await _unwrap(digarr.search_library(q=query, source=source, limit=limit))


@mcp.tool()
async def lidarr_check_artists(artists: list[str]) -> dict:
    """Check which of the given artist names already exist in your Lidarr library."""
    return await _unwrap(digarr.check_artists_in_library(digarr.CheckArtistsRequest(artists=artists)))


@mcp.tool()
async def lidarr_wanted() -> dict:
    """List albums Lidarr is missing/wanted, restricted to artists Digarr has imported."""
    return await _unwrap(digarr.get_wanted_missing())


if __name__ == "__main__":
    mcp.run()
