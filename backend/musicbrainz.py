"""
MusicBrainz lookup helpers for Digarr.

Rate limit: MusicBrainz allows 1 request/second from the same IP.
All calls go through _rate_limited_get() which enforces a 1.1-second
minimum interval using an asyncio.Lock + timestamp. The lock serialises
concurrent callers so bursts never violate the limit.
"""

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
USER_AGENT = "Digarr/1.0.1 (self-hosted music library tool; https://github.com/jtsmagic/digarr)"

_MIN_INTERVAL = 1.1  # seconds — MB policy is 1/sec; small buffer avoids edge cases

# Module-level state; works correctly inside a single FastAPI process / event loop.
_lock: asyncio.Lock | None = None
_last_call: float = 0.0


def _get_lock() -> asyncio.Lock:
    """Lazily create the lock so it is always bound to the running event loop."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def _rate_limited_get(url: str, params: dict) -> dict | None:
    """Fetch a MusicBrainz URL, blocking until the rate-limit window has passed."""
    global _last_call
    lock = _get_lock()

    async with lock:
        elapsed = time.monotonic() - _last_call
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        _last_call = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, params=params, headers={"User-Agent": USER_AGENT})
            if r.status_code == 200:
                return r.json()
            if r.status_code == 503:
                # MB sometimes 503s under load; treat as soft failure rather than crash
                logger.warning("MusicBrainz 503 for %s — skipping enrichment", url)
            else:
                logger.warning("MusicBrainz returned HTTP %d for %s", r.status_code, url)
            return None
        except httpx.TimeoutException:
            logger.warning("MusicBrainz request timed out for %s", url)
            return None
        except Exception as exc:
            logger.warning("MusicBrainz request failed: %s", exc)
            return None


# Release types to skip when picking an album — prefer actual studio releases
_SKIP_TYPES = {"Compilation", "Live", "Soundtrack", "Interview", "Spokenword", "Audiobook", "Remix"}


async def lookup_track(artist: str, title: str) -> dict:
    """
    Look up a recording on MusicBrainz by artist name + track title.

    Returns a dict containing any subset of:
      canonical_artist (str) — MB's authoritative artist name
      album (str)            — release/album title the track appears on

    Returns {} on any failure or when no match is found. Never raises.
    """
    safe_title = title.replace('"', '\\"')
    safe_artist = artist.replace('"', '\\"')
    query = f'recording:"{safe_title}" AND artist:"{safe_artist}"'

    data = await _rate_limited_get(
        f"{MB_BASE}/recording/",
        {"query": query, "fmt": "json", "limit": 5, "inc": "releases+artist-credits"},
    )
    if not data:
        return {}

    recordings = data.get("recordings", [])
    if not recordings:
        logger.info("MusicBrainz: no match for %r by %r", title, artist)
        return {}

    rec = recordings[0]
    result: dict = {}

    # --- canonical artist name ---
    credits = rec.get("artist-credit", [])
    if credits and isinstance(credits[0], dict):
        canonical = (
            credits[0].get("artist", {}).get("name")
            or credits[0].get("name")
        )
        if canonical:
            result["canonical_artist"] = canonical

    # --- duration (ms) ---
    duration_ms = rec.get("length")  # MusicBrainz returns duration in ms
    if duration_ms:
        result["duration_ms"] = duration_ms

    # --- best album ---
    # Prefer studio/EP releases; skip compilations and live albums.
    # release-group.primary-type may or may not be present in search results —
    # handle gracefully if absent.
    releases = rec.get("releases", [])
    album: str | None = None
    for rel in releases:
        rg = rel.get("release-group") or {}
        ptype = rg.get("primary-type", "")
        stypes = set(rg.get("secondary-types") or [])
        if ptype not in _SKIP_TYPES and not (stypes & _SKIP_TYPES):
            album = rel.get("title")
            break
    if not album and releases:
        album = releases[0].get("title")
    if album:
        result["album"] = album

    if result:
        logger.info(
            "MusicBrainz enriched %r / %r → canonical_artist=%r album=%r",
            artist, title, result.get("canonical_artist"), result.get("album"),
        )
    return result
