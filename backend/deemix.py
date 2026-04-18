"""
Deemix client for Digarr.

Rate limiting: asyncio.Semaphore caps concurrent Deemix API calls.
Deemix proxies the Deezer API internally so the rate limit is generous,
but we cap parallel calls to avoid hammering it.
"""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_SEMAPHORE_LIMIT = 3
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_SEMAPHORE_LIMIT)
    return _semaphore


class DeemixClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def test_connection(self) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            # Try /api/settings first; fall back to root — different Deemix builds
            # return different things (some return empty bodies, some return HTML).
            for path in ("/api/settings", "/api/ping", "/"):
                try:
                    r = await client.get(f"{self.base_url}{path}")
                    r.raise_for_status()
                    try:
                        data = r.json()
                        version = (
                            data.get("version")
                            or data.get("settings", {}).get("version", "")
                        )
                    except Exception:
                        data = {}
                        version = ""
                    return {"connected": True, "version": version}
                except httpx.HTTPStatusError:
                    raise
                except Exception:
                    continue
        raise ConnectionError("Deemix did not respond on any known endpoint")

    async def search_track(self, artist: str, title: str) -> list[dict]:
        """Search Deezer via Deemix for a specific track. Returns up to 5 candidates."""
        query = f"{artist} {title}"
        async with _get_semaphore():
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{self.base_url}/api/search",
                    params={"term": query, "type": "track"},
                )
                r.raise_for_status()
                data = r.json()

        results = data.get("data") or data.get("results") or []
        candidates = []
        for item in results[:5]:
            candidates.append({
                "id": str(item.get("id", "")),
                "title": item.get("title", ""),
                "artist": (item.get("artist") or {}).get("name", ""),
                "album": (item.get("album") or {}).get("title", ""),
                "duration": item.get("duration", 0),
                "url": item.get("link", "") or f"https://www.deezer.com/track/{item.get('id', '')}",
            })
        return candidates

    async def get_user_playlists(self) -> list[dict]:
        """Return the logged-in Deezer user's playlists via deemix."""
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self.base_url}/api/getUserPlaylists")
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise ValueError(data["error"])
            playlists = data.get("playlists", data) if isinstance(data, dict) else data
            if isinstance(playlists, dict):
                playlists = playlists.get("data", list(playlists.values()))
            return [
                {
                    "id": str(p.get("id", "")),
                    "name": p.get("title") or p.get("name") or "Untitled",
                    "nb_tracks": p.get("nb_tracks") or p.get("track_count") or 0,
                    "picture": p.get("picture_medium") or p.get("picture") or "",
                }
                for p in (playlists or [])
                if p.get("id")
            ]

    async def get_playlist_tracks(self, playlist_id: str) -> dict:
        """Fetch tracks for a Deezer playlist via the public Deezer API."""
        tracks = []
        url = f"https://api.deezer.com/playlist/{playlist_id}/tracks"
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            while url:
                r = await client.get(url, params={"limit": 200})
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    raise ValueError(data["error"].get("message", str(data["error"])))
                for item in data.get("data", []):
                    tracks.append({
                        "id": str(item.get("id", "")),
                        "title": item.get("title", ""),
                        "artist": (item.get("artist") or {}).get("name", ""),
                        "album": (item.get("album") or {}).get("title", ""),
                        "duration": item.get("duration", 0),
                    })
                url = data.get("next")  # pagination
        return {"tracks": tracks, "total": len(tracks)}

    async def queue_track(self, deezer_url: str, bitrate: str = "FLAC") -> dict:
        """Add a Deezer track URL to the Deemix download queue."""
        async with _get_semaphore():
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{self.base_url}/api/addToQueue",
                    json={"url": deezer_url, "bitrate": bitrate},
                )
                r.raise_for_status()
                return r.json()

    async def queue_tracks(self, tracks: list[dict]) -> dict:
        """
        Search and queue a list of tracks. Each track dict needs 'artist' and 'title'.
        Returns {"queued": int, "failed": int, "results": list[dict]}.
        """
        queued = 0
        failed = 0
        results = []

        async def process_one(t):
            nonlocal queued, failed
            artist = t.get("artist", "")
            title = t.get("title", "")
            try:
                candidates = await self.search_track(artist, title)
                if not candidates:
                    failed += 1
                    results.append({"artist": artist, "title": title, "status": "not_found"})
                    return
                best = candidates[0]
                await self.queue_track(best["url"])
                queued += 1
                results.append({
                    "artist": artist, "title": title, "status": "queued",
                    "matched_artist": best["artist"], "matched_title": best["title"],
                })
            except Exception as exc:
                failed += 1
                logger.warning("Deemix queue failed for %r / %r: %s", artist, title, exc)
                results.append({"artist": artist, "title": title, "status": "error", "error": str(exc)})

        await asyncio.gather(*[process_one(t) for t in tracks])
        return {"queued": queued, "failed": failed, "results": results}
