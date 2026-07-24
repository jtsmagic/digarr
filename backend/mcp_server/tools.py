"""
Digarr's MCP tool implementations, shared between the local stdio entrypoint
(server.py, launched via `docker exec`) and the remote OAuth-protected HTTP
entrypoint (http_server.py). Each entrypoint constructs its own FastMCP
instance (different transport/auth settings) and registers ALL_TOOLS onto it,
so the actual business logic lives here exactly once.

Reuses main.py's functions directly — no HTTP calls between this process and
the main app. Reads (playlists, config, library cache) go straight to the
shared SQLite DB / config.json on disk. Most actions (refresh, sync) call the
same async functions the web UI's routes call, awaited synchronously and
returned in one response. import_playlist is the exception: adding many new
artists to Lidarr is sequential and can take minutes, which is long enough to
trip client/proxy timeouts over the remote MCP transport, so it's fired off
as a background task (like the web UI's /api/import/start) and paired with
get_import_status for polling.
"""
import asyncio
from typing import Optional

from fastapi import HTTPException

import main as digarr
from ai.errors import AIProviderError
from utils import deduplicate_artists

digarr.init_db()


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


def list_playlists() -> list[dict]:
    """List every playlist Digarr has imported, with sync status per media server."""
    return [_playlist_summary(p) for p in digarr.get_playlists()]


def get_playlist(playlist_id: int) -> dict:
    """Get full details for one Digarr playlist, including its track list."""
    pl = digarr.get_playlist(playlist_id)
    if not pl:
        raise ValueError(f"Playlist {playlist_id} not found")
    return pl


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

    Runs in the background and returns immediately with a job_id — adding artists to
    Lidarr is sequential and can take several minutes for playlists with many new
    artists. Poll get_import_status(job_id) until status is "done" or "error" to see
    the final results.

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
    asyncio.create_task(digarr._run_import_job(job["id"], req, playlist_id))
    return job


def get_import_status(job_id: str) -> dict:
    """Poll a background import job started by import_playlist or replace_playlist.
    status is "running", "done", or "error"; once done, includes per-artist Lidarr
    results and media-server sync results."""
    job = digarr._jobs.get(job_id)
    if not job:
        raise ValueError(f"Import job {job_id} not found")
    return job


async def replace_playlist(
    playlist_id: int,
    artists: list[dict],
    tracks: list[dict],
    sync_targets: Optional[list[str]] = None,
) -> dict:
    """
    Overwrite an EXISTING playlist's artist and track list with fresh data — use this
    instead of import_playlist when you're re-pulling a playlist you already imported
    (e.g. re-scraping its source page yourself) and want to update it in place rather
    than create a duplicate. Find the playlist_id first via list_playlists/get_playlist.

    The playlist's artists/tracks become exactly what you pass in here — anything
    missing from the new list is dropped from the playlist (artists already added to
    Lidarr by a prior import are never removed from Lidarr itself, just no longer
    tracked by this playlist). New artists in the list get added to Lidarr, and the
    playlist is re-pushed to sync_targets (or every configured target if omitted).

    Runs in the background and returns immediately with a job_id, same as
    import_playlist — poll get_import_status(job_id) for progress/results.
    """
    pl = digarr.get_playlist(playlist_id)
    if not pl:
        raise ValueError(f"Playlist {playlist_id} not found")

    req = digarr.ImportJobRequest(
        artists=artists,
        tracks=tracks,
        playlist_name=pl["name"],
        source_url=pl.get("source_url"),
        source_type=pl.get("source_type"),
        include_in_refresh=True,
        sync_targets=sync_targets or [],
    )
    artist_names = [a["name"] if isinstance(a, dict) else a for a in req.artists]
    digarr.update_playlist(playlist_id, artist_names, req.tracks, pl.get("artists_added") or [])
    digarr.touch_playlist_refreshed(playlist_id)

    job = digarr._new_job(pl["name"], len(req.artists))
    job["playlist_id"] = playlist_id
    digarr._jobs[job["id"]] = job
    digarr.db_save_import_job(job)
    asyncio.create_task(digarr._run_import_job(job["id"], req, playlist_id))
    return job


async def append_playlist(
    playlist_id: int,
    artists: Optional[list] = None,
    tracks: Optional[list[dict]] = None,
    sync_targets: Optional[list[str]] = None,
) -> dict:
    """
    Add artists/tracks to an EXISTING playlist without dropping what's already there —
    use this instead of replace_playlist when you want to grow a playlist rather than
    overwrite its contents. Find the playlist_id first via list_playlists/get_playlist.

    Artists already in the playlist (case-insensitive name match) and tracks already in
    it (case-insensitive artist+title match) are left alone; only genuinely new ones are
    added. New artists get added to Lidarr, and the playlist is re-pushed to sync_targets
    (or every configured target if omitted) with the merged contents.

    Runs in the background and returns immediately with a job_id, same as
    import_playlist/replace_playlist — poll get_import_status(job_id) for progress/results.
    """
    pl = digarr.get_playlist(playlist_id)
    if not pl:
        raise ValueError(f"Playlist {playlist_id} not found")

    existing_names = {
        (a if isinstance(a, str) else a.get("name", "")).lower()
        for a in (pl.get("artists") or [])
    }
    merged_artists = list(pl.get("artists") or [])
    for a in artists or []:
        name = a["name"] if isinstance(a, dict) else a
        if name and name.lower() not in existing_names:
            merged_artists.append(a)
            existing_names.add(name.lower())

    existing_track_keys = {
        ((t.get("artist") or "").lower(), (t.get("title") or "").lower())
        for t in (pl.get("tracks") or [])
    }
    merged_tracks = list(pl.get("tracks") or [])
    for t in tracks or []:
        key = ((t.get("artist") or "").lower(), (t.get("title") or "").lower())
        if key not in existing_track_keys:
            merged_tracks.append(t)
            existing_track_keys.add(key)

    return await replace_playlist(playlist_id, merged_artists, merged_tracks, sync_targets)


async def refresh_playlist(playlist_id: int) -> dict:
    """Re-fetch a playlist's original source and add any new artists/tracks found."""
    return await _unwrap(digarr._do_refresh_playlist(playlist_id))


async def delete_playlist(playlist_id: int) -> dict:
    """
    Permanently delete a Digarr playlist, including its pushed playlist on any media
    server for which the corresponding *_delete_on_remove config flag is enabled
    (e.g. plex_delete_on_remove). Artists already added to Lidarr by this playlist are
    NOT removed from Lidarr. Find the playlist_id first via list_playlists/get_playlist
    — this cannot be undone, so confirm you have the right id before calling it.
    """
    return await _unwrap(digarr.delete_playlist_route(playlist_id))


_SYNC_FUNCS = {
    "plex": (digarr.sync_plex_playlist, digarr.sync_all_plex),
    "jellyfin": (digarr.sync_jellyfin_playlist, digarr.sync_all_jellyfin),
    "navidrome": (digarr.sync_navidrome_playlist, digarr.sync_all_navidrome),
}


async def sync_playlist(playlist_id: int, target: str) -> dict:
    """Push/re-push one playlist to a media server. target: plex, jellyfin, or navidrome."""
    if target not in _SYNC_FUNCS:
        raise ValueError(f"Unknown target {target!r}; must be plex, jellyfin, or navidrome")
    return await _unwrap(_SYNC_FUNCS[target][0](playlist_id))


async def sync_all(target: str) -> dict:
    """Push/re-push every eligible playlist to a media server. target: plex, jellyfin, or navidrome."""
    if target not in _SYNC_FUNCS:
        raise ValueError(f"Unknown target {target!r}; must be plex, jellyfin, or navidrome")
    return await _unwrap(_SYNC_FUNCS[target][1]())


async def search_library(query: str, source: str = "plex", limit: int = 20) -> dict:
    """Search your media library (Plex/Jellyfin/Navidrome track cache) for a song or artist."""
    return await _unwrap(digarr.search_library(q=query, source=source, limit=limit))


async def lidarr_check_artists(artists: list[str]) -> dict:
    """Check which of the given artist names already exist in your Lidarr library."""
    return await _unwrap(digarr.check_artists_in_library(digarr.CheckArtistsRequest(artists=artists)))


async def lidarr_wanted() -> dict:
    """List albums Lidarr is missing/wanted, restricted to artists Digarr has imported."""
    return await _unwrap(digarr.get_wanted_missing())


ALL_TOOLS = [
    list_playlists,
    get_playlist,
    parse_source,
    import_playlist,
    get_import_status,
    replace_playlist,
    append_playlist,
    refresh_playlist,
    delete_playlist,
    sync_playlist,
    sync_all,
    search_library,
    lidarr_check_artists,
    lidarr_wanted,
]
