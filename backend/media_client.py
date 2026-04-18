"""
Media server client abstraction — Plex, Jellyfin, Navidrome.
"""
from abc import ABC, abstractmethod
from typing import List
import hashlib
import json
import secrets
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


class JellyfinMediaClient(MediaClient):
    source = "jellyfin"

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._user_id: str = None

    def _headers(self) -> dict:
        return {"X-MediaBrowser-Token": self.api_key, "Accept": "application/json"}

    async def _get_user_id(self) -> str:
        if self._user_id:
            return self._user_id
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self.base_url}/Users/Me", headers=self._headers())
            r.raise_for_status()
            self._user_id = r.json()["Id"]
        return self._user_id

    def _to_track(self, item: dict) -> dict:
        artists = item.get("Artists") or []
        return {
            "external_id": item["Id"],
            "title": item.get("Name", ""),
            "artist": artists[0] if artists else item.get("AlbumArtist", ""),
            "album": item.get("Album", ""),
        }

    async def get_all_tracks(self) -> List[dict]:
        user_id = await self._get_user_id()
        results = []
        start = 0
        page = 500
        async with httpx.AsyncClient(timeout=120) as client:
            while True:
                r = await client.get(
                    f"{self.base_url}/Items",
                    headers=self._headers(),
                    params={
                        "IncludeItemTypes": "Audio",
                        "Recursive": "true",
                        "UserId": user_id,
                        "StartIndex": start,
                        "Limit": page,
                        "Fields": "Artists,Album",
                    },
                )
                r.raise_for_status()
                data = r.json()
                items = data.get("Items", [])
                results.extend(self._to_track(i) for i in items)
                total = data.get("TotalRecordCount", 0)
                start += len(items)
                if not items or start >= total:
                    break
        return results

    async def search_tracks(self, query: str, limit: int = 20) -> List[dict]:
        user_id = await self._get_user_id()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.base_url}/Items",
                headers=self._headers(),
                params={
                    "searchTerm": query,
                    "IncludeItemTypes": "Audio",
                    "Recursive": "true",
                    "UserId": user_id,
                    "Limit": limit,
                    "Fields": "Artists,Album",
                },
            )
            r.raise_for_status()
        return [self._to_track(i) for i in r.json().get("Items", [])]


class NavidromeMediaClient(MediaClient):
    source = "navidrome"
    _API_VERSION = "1.16.1"

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password

    def _auth_params(self) -> dict:
        salt = secrets.token_hex(6)
        token = hashlib.md5((self.password + salt).encode()).hexdigest()
        return {"u": self.username, "t": token, "s": salt, "v": self._API_VERSION, "c": "digarr", "f": "json"}

    def _to_track(self, item: dict) -> dict:
        return {
            "external_id": str(item["id"]),
            "title": item.get("title", ""),
            "artist": item.get("artist", ""),
            "album": item.get("album", ""),
        }

    async def get_all_tracks(self) -> List[dict]:
        results = []
        offset = 0
        page = 500
        async with httpx.AsyncClient(timeout=120) as client:
            while True:
                r = await client.get(
                    f"{self.base_url}/rest/search3.view",
                    params={**self._auth_params(), "query": "", "songCount": page, "songOffset": offset, "albumCount": 0, "artistCount": 0},
                )
                r.raise_for_status()
                songs = r.json().get("subsonic-response", {}).get("searchResult3", {}).get("song", [])
                if isinstance(songs, dict):
                    songs = [songs]
                results.extend(self._to_track(s) for s in songs)
                if len(songs) < page:
                    break
                offset += len(songs)
        return results

    async def search_tracks(self, query: str, limit: int = 20) -> List[dict]:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.base_url}/rest/search3.view",
                params={**self._auth_params(), "query": query, "songCount": limit, "albumCount": 0, "artistCount": 0},
            )
            r.raise_for_status()
        songs = r.json().get("subsonic-response", {}).get("searchResult3", {}).get("song", [])
        if isinstance(songs, dict):
            songs = [songs]
        return [self._to_track(s) for s in songs]
