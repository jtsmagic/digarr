"""
slskd (Soulseek) client for Digarr.

Search flow:
1. POST /api/v0/searches  → searchId
2. Poll GET /api/v0/searches/{id} until state==Completed or timeout
3. Score each file result against MusicBrainz canonical duration
4. Auto-queue files above threshold; flag below-threshold for manual review

Rate limiting:
  - asyncio.Semaphore caps concurrent P2P searches (Soulseek is slow)
  - MusicBrainz calls go through the existing rate-limited helper in musicbrainz.py
"""

import asyncio
import logging
import re

import httpx

from musicbrainz import lookup_track as mb_lookup_track

logger = logging.getLogger(__name__)

_SEARCH_SEMAPHORE_LIMIT = 2  # max concurrent slskd searches
_search_semaphore: asyncio.Semaphore | None = None

_SEARCH_TIMEOUT = 60       # seconds to wait for slskd search completion
_POLL_INTERVAL = 2.0       # seconds between poll attempts
_PREFERRED_EXTS = {".flac", ".mp3", ".ogg", ".m4a", ".opus"}


def _get_semaphore() -> asyncio.Semaphore:
    global _search_semaphore
    if _search_semaphore is None:
        _search_semaphore = asyncio.Semaphore(_SEARCH_SEMAPHORE_LIMIT)
    return _search_semaphore


def _score_candidate(
    file: dict,
    target_artist: str,
    target_title: str,
    mb_duration_ms: int | None,
) -> float:
    """
    Score a slskd file candidate 0–100.

    Weights:
      title similarity  40%
      artist match      25%
      format/quality    15%
      duration match    20%  (only when MB duration available)
    """
    filename: str = file.get("filename", "").lower()
    ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""

    # --- title match (40%) ---
    ta = _norm(target_title)
    fn_stem = filename.rsplit(".", 1)[0].rsplit("/", 1)[-1] if "/" in filename or "\\" in filename else filename.rsplit(".", 1)[0]
    fn_stem = _norm(fn_stem)
    title_score = 40.0 * _token_overlap(ta, fn_stem)

    # --- artist match (25%) ---
    artist_norm = _norm(target_artist)
    path_norm = _norm(filename)
    artist_score = 25.0 * _token_overlap(artist_norm, path_norm)

    # --- format/quality (15%) ---
    format_score = 0.0
    if ext == ".flac":
        format_score = 15.0
    elif ext in (".mp3", ".m4a"):
        format_score = 10.0
    elif ext in (".ogg", ".opus"):
        format_score = 8.0
    elif ext in _PREFERRED_EXTS:
        format_score = 5.0

    # --- duration match (20%) ---
    duration_score = 0.0
    if mb_duration_ms and mb_duration_ms > 0:
        # slskd reports duration via attribute type 4 (seconds)
        attrs = file.get("fileAttributes") or []
        file_dur_s = None
        for attr in attrs:
            if isinstance(attr, dict) and attr.get("type") == 4:
                file_dur_s = attr.get("value")
                break
        if file_dur_s is not None:
            mb_s = mb_duration_ms / 1000.0
            diff = abs(file_dur_s - mb_s)
            if diff <= 3:
                duration_score = 20.0
            elif diff <= 10:
                duration_score = 12.0
            elif diff <= 20:
                duration_score = 5.0
    else:
        # No MB data — give partial credit so threshold isn't artificially lowered
        duration_score = 10.0

    return title_score + artist_score + format_score + duration_score


def _norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _token_overlap(a: str, b: str) -> float:
    """Fraction of tokens in *a* that appear in *b* (Jaccard-ish)."""
    ta = set(a.split())
    tb = set(b.split())
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


class SlskdClient:
    def __init__(self, base_url: str, api_key: str, confidence_threshold: int = 85):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.threshold = confidence_threshold

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key}

    async def test_connection(self) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.base_url}/api/v0/application",
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json()
            return {
                "connected": True,
                "version": (data.get("version") or {}).get("current", ""),
            }

    async def _start_search(self, query: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{self.base_url}/api/v0/searches",
                json={"searchText": query},
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json().get("id", "")

    async def _poll_search(self, search_id: str) -> list[dict]:
        deadline = asyncio.get_event_loop().time() + _SEARCH_TIMEOUT
        last_responses: list[dict] = []
        async with httpx.AsyncClient(timeout=10) as client:
            while asyncio.get_event_loop().time() < deadline:
                r = await client.get(
                    f"{self.base_url}/api/v0/searches/{search_id}",
                    headers=self._headers(),
                )
                if r.status_code == 404:
                    return []
                r.raise_for_status()
                data = r.json()
                state = data.get("state", "")
                last_responses = data.get("responses") or []
                if state in ("Completed", "TimedOut", "Cancelled"):
                    logger.info("slskd search %s done (state=%s, responses=%d)", search_id[:8], state, len(last_responses))
                    return last_responses
                await asyncio.sleep(_POLL_INTERVAL)
        logger.warning("slskd search %s timed out locally, returning %d partial responses", search_id[:8], len(last_responses))
        return last_responses

    async def _queue_download(self, username: str, file: dict) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{self.base_url}/api/v0/transfers/downloads/{username}",
                json=[{"filename": file["filename"], "size": file.get("size", 0)}],
                headers=self._headers(),
            )
            r.raise_for_status()

    async def search_and_queue(
        self, artist: str, title: str
    ) -> dict:
        """
        Search slskd for artist+title, score candidates via MusicBrainz,
        and queue the best match if score >= threshold.

        Returns:
          status: "queued" | "flagged" | "not_found"
          score: float
          candidates: list of scored candidates (for the review UI)
          mb_duration_ms: int | None
        """
        async with _get_semaphore():
            query = f"{artist} {title}"
            try:
                search_id = await self._start_search(query)
            except Exception as exc:
                logger.warning("slskd search start failed for %r / %r: %s", artist, title, exc)
                return {"status": "error", "error": str(exc), "score": 0, "candidates": []}

            responses = await self._poll_search(search_id)

        # Flatten files from all peers
        flat_files: list[dict] = []
        for resp in responses:
            username = resp.get("username", "")
            for f in resp.get("files") or []:
                ext = "." + f.get("filename", "").rsplit(".", 1)[-1] if "." in f.get("filename", "") else ""
                if ext.lower() not in _PREFERRED_EXTS:
                    continue
                flat_files.append({**f, "_username": username})

        logger.info("slskd: %d usable files for %r / %r", len(flat_files), artist, title)
        if not flat_files:
            return {"status": "not_found", "score": 0, "candidates": [], "mb_duration_ms": None}

        # MusicBrainz duration lookup (rate-limited internally)
        mb_duration_ms: int | None = None
        try:
            mb = await mb_lookup_track(artist, title)
            mb_duration_ms = mb.get("duration_ms")
        except Exception:
            pass

        # Score all candidates
        scored = []
        for f in flat_files:
            s = _score_candidate(f, artist, title, mb_duration_ms)
            scored.append({
                "filename": f.get("filename", ""),
                "username": f.get("_username", ""),
                "size": f.get("size", 0),
                "score": round(s, 1),
                "attributes": f.get("fileAttributes") or [],
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        best = scored[0]
        logger.info("slskd: best score %.1f (threshold %d) for %r / %r — %s", best["score"], self.threshold, artist, title, best["filename"][-60:])

        if best["score"] >= self.threshold:
            try:
                # Re-construct the original file dict for the download call
                orig = next(
                    f for f in flat_files
                    if f.get("filename") == best["filename"] and f.get("_username") == best["username"]
                )
                await self._queue_download(best["username"], orig)
                return {
                    "status": "queued",
                    "score": best["score"],
                    "candidates": scored[:5],
                    "mb_duration_ms": mb_duration_ms,
                }
            except Exception as exc:
                logger.warning("slskd download failed for %r / %r: %s", artist, title, exc)
                return {
                    "status": "flagged",
                    "score": best["score"],
                    "candidates": scored[:5],
                    "mb_duration_ms": mb_duration_ms,
                    "error": str(exc),
                }
        else:
            return {
                "status": "flagged",
                "score": best["score"],
                "candidates": scored[:5],
                "mb_duration_ms": mb_duration_ms,
            }

    async def queue_tracks(self, tracks: list[dict]) -> dict:
        """
        Search and optionally queue a list of tracks.
        Returns summary + per-track results.
        """
        queued = 0
        flagged = 0
        not_found = 0
        results = []

        async def process_one(t):
            nonlocal queued, flagged, not_found
            artist = t.get("artist", "")
            title = t.get("title", "")
            r = await self.search_and_queue(artist, title)
            r["artist"] = artist
            r["title"] = title
            results.append(r)
            if r["status"] == "queued":
                queued += 1
            elif r["status"] == "flagged":
                flagged += 1
            else:
                not_found += 1

        # Process sequentially to respect the search semaphore — gather would
        # pile up tasks that then all block on the semaphore anyway.
        for t in tracks:
            await process_one(t)

        return {
            "queued": queued,
            "flagged": flagged,
            "not_found": not_found,
            "results": results,
        }
