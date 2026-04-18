import asyncio
import hashlib
import logging
import secrets
from typing import List, Optional, Tuple

import httpx

from utils import normalize as _normalize

logger = logging.getLogger(__name__)

_API_VERSION = "1.16.1"
_CLIENT_NAME = "digarr"


class NavidromeClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password

    def _auth_params(self) -> dict:
        salt = secrets.token_hex(6)
        token = hashlib.md5((self.password + salt).encode()).hexdigest()
        return {
            "u": self.username,
            "t": token,
            "s": salt,
            "v": _API_VERSION,
            "c": _CLIENT_NAME,
            "f": "json",
        }

    async def _get(self, method: str, extra: dict = None) -> dict:
        params = {**self._auth_params(), **(extra or {})}
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{self.base_url}/rest/{method}.view", params=params
            )
            r.raise_for_status()
        body = r.json().get("subsonic-response", {})
        if body.get("status") != "ok":
            err = body.get("error", {})
            raise ValueError(
                f"Subsonic error {err.get('code', '?')}: {err.get('message', 'unknown')}"
            )
        return body

    async def _get_multi(
        self,
        method: str,
        base_params: dict,
        list_key: str,
        list_values: List[str],
    ) -> dict:
        """GET with repeated query params (e.g. songId=1&songId=2)."""
        combined = {**self._auth_params(), **base_params}
        params = list(combined.items())
        for v in list_values:
            params.append((list_key, v))
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{self.base_url}/rest/{method}.view", params=params
            )
            r.raise_for_status()
        body = r.json().get("subsonic-response", {})
        if body.get("status") != "ok":
            err = body.get("error", {})
            raise ValueError(
                f"Subsonic error {err.get('code', '?')}: {err.get('message', 'unknown')}"
            )
        return body

    async def test_connection(self) -> dict:
        data = await self._get("ping")
        return {"status": "ok", "version": data.get("version", "")}

    async def search_track(self, artist: str, title: str) -> Optional[str]:
        if not title:
            return None
        title_norm = _normalize(title)
        artist_norm = _normalize(artist)

        data = await self._get(
            "search3",
            {"query": title, "songCount": 20, "albumCount": 0, "artistCount": 0},
        )
        songs = data.get("searchResult3", {}).get("song", [])
        if isinstance(songs, dict):
            songs = [songs]

        def _artist_match(song: dict) -> bool:
            if not artist:
                return True
            return artist_norm in _normalize(song.get("artist", ""))

        for s in songs:
            if _normalize(s.get("title", "")) == title_norm and _artist_match(s):
                return str(s["id"])
        for s in songs:
            if title_norm in _normalize(s.get("title", "")) and _artist_match(s):
                return str(s["id"])
        for s in songs:
            if _normalize(s.get("title", "")) == title_norm:
                return str(s["id"])
        return None

    async def match_tracks(
        self, tracks: List[dict]
    ) -> Tuple[List[str], List[dict], int]:
        semaphore = asyncio.Semaphore(3)

        async def search_one(track):
            async with semaphore:
                try:
                    return await self.search_track(
                        track.get("artist", ""), track.get("title", "")
                    )
                except Exception as exc:
                    logger.warning(
                        "Navidrome search error for %r / %r: %s",
                        track.get("artist"),
                        track.get("title"),
                        exc,
                    )
                    return None

        results = await asyncio.gather(*[search_one(t) for t in tracks])
        matched = [r for r in results if r is not None]
        unmatched = [dict(t) for r, t in zip(results, tracks) if r is None]
        return matched, unmatched, len(tracks)

    async def create_playlist(self, name: str, song_ids: List[str]) -> str:
        if song_ids:
            data = await self._get_multi(
                "createPlaylist", {"name": name}, "songId", song_ids
            )
        else:
            data = await self._get("createPlaylist", {"name": name})
        return str(data["playlist"]["id"])

    async def update_playlist(
        self, playlist_id: str, name: str, song_ids: List[str]
    ) -> None:
        """Replace playlist contents — createPlaylist with playlistId overwrites."""
        if song_ids:
            await self._get_multi(
                "createPlaylist",
                {"playlistId": playlist_id, "name": name},
                "songId",
                song_ids,
            )
        else:
            await self._get(
                "createPlaylist", {"playlistId": playlist_id, "name": name}
            )

    async def delete_playlist(self, playlist_id: str) -> None:
        await self._get("deletePlaylist", {"id": playlist_id})
