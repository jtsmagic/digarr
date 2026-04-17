"""
Media server client abstraction.

Add Jellyfin, Emby, Navidrome, etc. by subclassing MediaClient.
Plex is the first implementation.
"""
from abc import ABC, abstractmethod
from typing import List, Optional
import json
import httpx
import logging

logger = logging.getLogger(__name__)


def _parse_json(r: httpx.Response) -> dict:
    """Parse a Plex JSON response, tolerating non-UTF-8 bytes in track/artist names.

    Plex occasionally returns ISO-8859-1 characters (e.g. accented letters)
    even when the Content-Type claims UTF-8.  Using 'replace' keeps the JSON
    structure intact; the few garbled characters in names don't affect matching
    because our normaliser strips punctuation anyway.
    """
    try:
        return r.json()
    except (UnicodeDecodeError, ValueError):
        text = r.content.decode("utf-8", errors="replace")
        return json.loads(text)


class MediaClient(ABC):
    """
    Media-server-agnostic interface for reading a music library.
    The `source` class attribute identifies which server the implementation
    talks to — it is used as the discriminator in track_cache and manual_matches.
    """

    source: str  # e.g. "plex", "jellyfin"

    @abstractmethod
    async def get_all_tracks(self) -> List[dict]:
        """
        Fetch every track in the library.
        Each dict must contain: external_id, title, artist, album.
        """
        raise NotImplementedError

    @abstractmethod
    async def search_tracks(self, query: str, limit: int = 20) -> List[dict]:
        """
        Live search against the media server.
        Returns the same shape as get_all_tracks().
        Used by the manual-match search modal.
        """
        raise NotImplementedError


class PlexMediaClient(MediaClient):
    source = "plex"

    def __init__(self, base_url: str, token: str, section_id: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.section_id = str(section_id)

    def _params(self, extra: dict = None) -> dict:
        p = {"X-Plex-Token": self.token}
        if extra:
            p.update(extra)
        return p

    def _to_track(self, item: dict) -> dict:
        return {
            "external_id": str(item.get("ratingKey", "")),
            "title": item.get("title", ""),
            "artist": item.get("grandparentTitle", ""),
            "album": item.get("parentTitle", ""),
        }

    async def get_all_tracks(self) -> List[dict]:
        """Paginate through the Plex library section, returning all tracks."""
        results = []
        page_size = 500
        offset = 0
        async with httpx.AsyncClient(timeout=120) as client:
            while True:
                r = await client.get(
                    f"{self.base_url}/library/sections/{self.section_id}/all",
                    params=self._params({
                        "type": 10,
                        "X-Plex-Container-Start": offset,
                        "X-Plex-Container-Size": page_size,
                    }),
                    headers={"Accept": "application/json"},
                )
                r.raise_for_status()
                mc = _parse_json(r).get("MediaContainer", {})
                items = mc.get("Metadata") or []
                results.extend(self._to_track(item) for item in items)
                total = mc.get("totalSize") or mc.get("size") or 0
                offset += len(items)
                if not items or offset >= total:
                    break
        return results

    async def search_tracks(self, query: str, limit: int = 20) -> List[dict]:
        """Live hub search — the same endpoint the Plex UI uses."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.base_url}/hubs/search",
                params=self._params({
                    "query": query,
                    "sectionId": self.section_id,
                    "limit": limit,
                }),
                headers={"Accept": "application/json"},
            )
            r.raise_for_status()
            hubs = _parse_json(r).get("MediaContainer", {}).get("Hub", [])
            track_hub = next((h for h in hubs if h.get("type") == "track"), None)
            items = (track_hub.get("Metadata") or []) if track_hub else []
            return [self._to_track(item) for item in items]
