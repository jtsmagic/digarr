"""
MusicBrainz lookup helpers for Digarr.

Rate limit: MusicBrainz allows 1 request/second from the same IP.
All calls go through _rate_limited_get() which enforces a 1.1-second
minimum interval using an asyncio.Lock + timestamp. The lock serialises
concurrent callers so bursts never violate the limit.
"""

import asyncio
import difflib
import logging
import re
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


async def _rate_limited_get(url: str, params: dict, tries: int = 3) -> dict | None:
    """Fetch a MusicBrainz URL, blocking until the rate-limit window has passed.

    503 is MusicBrainz shedding load, not a real answer, so it is retried with a
    backoff rather than reported as "no match" — roughly 15% of requests came back
    503 or timed out, and silently treating those as misses loses real albums.
    """
    global _last_call
    lock = _get_lock()

    for attempt in range(tries):
        async with lock:
            elapsed = time.monotonic() - _last_call
            if elapsed < _MIN_INTERVAL:
                await asyncio.sleep(_MIN_INTERVAL - elapsed)
            _last_call = time.monotonic()

            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(url, params=params,
                                         headers={"User-Agent": USER_AGENT})
                if r.status_code == 200:
                    return r.json()
                retryable = r.status_code in (503, 502, 504, 429)
                if not retryable:
                    logger.warning("MusicBrainz returned HTTP %d for %s", r.status_code, url)
                    return None
                last_desc = "HTTP %d" % r.status_code
            except httpx.TimeoutException:
                last_desc = "timeout"
            except Exception as exc:
                logger.warning("MusicBrainz request failed: %s", exc)
                return None

        if attempt < tries - 1:
            await asyncio.sleep(1.5 * (attempt + 1))

    logger.warning("MusicBrainz unavailable after %d tries (%s) for %s", tries, last_desc, url)
    return None


# Secondary release-group types that mean "not the album this song belongs to".
# Soundtrack is deliberately NOT here: for a show tune or a Disney song the
# soundtrack IS the canonical album, and excluding it loses the right answer.
_BAD_SECONDARY = {
    "Live", "Compilation", "Remix", "Interview", "Spokenword",
    "Audiobook", "DJ-mix", "Mixtape/Street", "Demo",
}

# Prefer a full album, but a song may only ever have been an EP or single track.
_PRIMARY_RANK = {"Album": 0, "EP": 1, "Single": 2}

# Bootleg concert releases are titled by date: "2005-06-17: Molson, Toronto, ON".
_DATE_TITLE = re.compile(r"^\s*\d{4}[-/]\d{2}[-/]\d{2}")

# Sampler/karaoke/hits-collection titles that slip through as primary-type Album.
_JUNK_TITLE = re.compile(
    r"(?i)\b(karaoke|tribute|made popular by|now that.s what i call|kidz bop|"
    r"sampler|greatest hits|the best of)\b|\bvol(ume)?\.? ?\d+\b"
)

# Narrow the search to official studio albums. Used only as a second pass, because
# it also filters out recordings MB has not fully typed, which loses real answers.
_STUDIO_FILTER = (
    " AND status:Official AND primarytype:Album"
    " AND NOT secondarytype:live AND NOT secondarytype:compilation"
)


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _artist_matches(wanted: str, got: str | None) -> bool:
    """Does the recording MB returned actually belong to the artist we asked about?

    MusicBrainz treats the artist term as a hint, not a constraint, so a query for
    Noah Kahan / "Up All Night" happily comes back as a James Bay recording with
    score 100. Accepting that renames the artist and monitors the wrong discography.
    Compared loosely so real spelling differences still pass (P!nk vs Pink).
    """
    a, b = _norm(wanted), _norm(got)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def _candidates(data: dict | None, artist: str) -> list[dict]:
    """Collect plausible release-groups from a recording search, best first.

    Aggregates by release-group rather than taking a release, because the number of
    releases carrying a track is a good canonicality signal: the real album has many
    pressings, a bootleg or a one-off sampler has one. It also picks the canonical
    title over a localised one - MB will otherwise hand back 'Enrolados' or
    '겨울왕국 II', which Lidarr can never satisfy.
    """
    groups: dict = {}
    for rec in (data or {}).get("recordings") or []:
        credits = rec.get("artist-credit") or []
        credited = None
        if credits and isinstance(credits[0], dict):
            credited = (credits[0].get("artist") or {}).get("name") or credits[0].get("name")
        if not _artist_matches(artist, credited):
            continue
        for rel in rec.get("releases") or []:
            rg = rel.get("release-group") or {}
            if rel.get("status") != "Official":
                continue
            primary = rg.get("primary-type")
            if primary not in _PRIMARY_RANK:
                continue
            if set(rg.get("secondary-types") or []) & _BAD_SECONDARY:
                continue
            title = rg.get("title") or rel.get("title") or ""
            if not title or _DATE_TITLE.match(title) or _JUNK_TITLE.search(title):
                continue
            key = rg.get("id") or title
            g = groups.setdefault(key, {"title": title, "count": 0, "date": None,
                                        "primary": primary, "artist": credited})
            g["count"] += 1
            date = rel.get("date") or ""
            if date and (g["date"] is None or date < g["date"]):
                g["date"] = date
    return sorted(groups.values(),
                  key=lambda g: (_PRIMARY_RANK[g["primary"]], -g["count"], g["date"] or "9999"))


async def _search(artist: str, title: str, studio_only: bool) -> dict | None:
    safe_title = title.replace('"', '\\"')
    safe_artist = artist.replace('"', '\\"')
    query = f'recording:"{safe_title}" AND artist:"{safe_artist}"'
    if studio_only:
        query += _STUDIO_FILTER
    return await _rate_limited_get(
        f"{MB_BASE}/recording/", {"query": query, "fmt": "json", "limit": 100}
    )


async def lookup_track(artist: str, title: str) -> dict:
    """
    Look up a recording on MusicBrainz by artist name + track title.

    Returns a dict containing any subset of:
      canonical_artist (str) — MB's authoritative artist name
      album (str)            — release/album title the track appears on
      duration_ms (int)      — recording length

    Returns {} on any failure or when no match is found. Never raises.

    Deliberately returns no album rather than a doubtful one. A wrong hint becomes
    an album Lidarr monitors whose track count nothing obtainable will satisfy, so
    it re-grabs on every RSS cycle; a missing hint just leaves the album unmonitored.
    """
    data = await _search(artist, title, studio_only=False)
    if data is None:
        return {}

    recordings = data.get("recordings") or []
    if not recordings:
        logger.info("MusicBrainz: no match for %r by %r", title, artist)
        return {}

    result: dict = {}

    # Only take metadata from a recording that is actually this artist's.
    matched = None
    for rec in recordings:
        credits = rec.get("artist-credit") or []
        credited = None
        if credits and isinstance(credits[0], dict):
            credited = (credits[0].get("artist") or {}).get("name") or credits[0].get("name")
        if _artist_matches(artist, credited):
            matched = rec
            if credited:
                result["canonical_artist"] = credited
            break
    if matched is None:
        logger.info("MusicBrainz: match for %r by %r was a different artist (%s) — ignored",
                    title, artist,
                    ((recordings[0].get("artist-credit") or [{}])[0].get("name")))
        return {}

    if matched.get("length"):
        result["duration_ms"] = matched["length"]

    # Broad search first; it sees every release and is usually decisive. Fall back to
    # the studio-only query when the evidence is thin - for heavily bootlegged songs
    # the broad result is all live recordings and the narrow one finds the real album.
    best = None
    candidates = _candidates(data, artist)
    if candidates and candidates[0]["count"] >= 2:
        best = candidates[0]
    else:
        narrow = await _search(artist, title, studio_only=True)
        narrow_candidates = _candidates(narrow, artist)
        if narrow_candidates:
            best = narrow_candidates[0]
        elif candidates:
            best = candidates[0]

    if best:
        result["album"] = best["title"]

    if result:
        logger.info(
            "MusicBrainz enriched %r / %r → canonical_artist=%r album=%r",
            artist, title, result.get("canonical_artist"), result.get("album"),
        )
        try:
            from database import db_increment_stat
            db_increment_stat("mb_enrichments_total")
        except Exception:
            pass
    return result
