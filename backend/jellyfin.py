import asyncio
import logging
from typing import List, Optional, Tuple

import httpx

from utils import normalize as _normalize

logger = logging.getLogger(__name__)


class JellyfinClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._user_id: Optional[str] = None

    def _headers(self) -> dict:
        return {
            "X-MediaBrowser-Token": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def get_user_id(self) -> str:
        if self._user_id:
            return self._user_id
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self.base_url}/Users/Me", headers=self._headers())
            r.raise_for_status()
            self._user_id = r.json()["Id"]
        return self._user_id

    async def test_connection(self) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.base_url}/System/Info/Public", headers=self._headers()
            )
            r.raise_for_status()
            info = r.json()
        user_id = await self.get_user_id()
        return {
            "server_name": info.get("ServerName", ""),
            "version": info.get("Version", ""),
            "user_id": user_id,
        }

    async def search_track(self, artist: str, title: str) -> Optional[str]:
        if not title:
            return None
        user_id = await self.get_user_id()
        title_norm = _normalize(title)
        artist_norm = _normalize(artist)

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.base_url}/Items",
                headers=self._headers(),
                params={
                    "searchTerm": title,
                    "IncludeItemTypes": "Audio",
                    "Recursive": "true",
                    "UserId": user_id,
                    "Limit": 20,
                    "Fields": "ArtistItems",
                },
            )
            r.raise_for_status()
        items = r.json().get("Items", [])

        def _artist_match(item: dict) -> bool:
            if not artist:
                return True
            item_artists = [_normalize(a) for a in (item.get("Artists") or [])]
            return any(artist_norm in a or a in artist_norm for a in item_artists)

        for item in items:
            if _normalize(item.get("Name", "")) == title_norm and _artist_match(item):
                return item["Id"]
        for item in items:
            if title_norm in _normalize(item.get("Name", "")) and _artist_match(item):
                return item["Id"]
        for item in items:
            if _normalize(item.get("Name", "")) == title_norm:
                return item["Id"]
        return None

    async def match_tracks(
        self, tracks: List[dict]
    ) -> Tuple[List[str], List[dict], int]:
        semaphore = asyncio.Semaphore(5)

        async def search_one(track):
            async with semaphore:
                try:
                    return await self.search_track(
                        track.get("artist", ""), track.get("title", "")
                    )
                except Exception as exc:
                    logger.warning(
                        "Jellyfin search error for %r / %r: %s",
                        track.get("artist"),
                        track.get("title"),
                        exc,
                    )
                    return None

        results = await asyncio.gather(*[search_one(t) for t in tracks])
        matched = [r for r in results if r is not None]
        unmatched = [dict(t) for r, t in zip(results, tracks) if r is None]
        return matched, unmatched, len(tracks)

    async def create_playlist(self, name: str, item_ids: List[str]) -> str:
        user_id = await self.get_user_id()
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{self.base_url}/Playlists",
                headers=self._headers(),
                json={
                    "Name": name,
                    "Ids": item_ids,
                    "MediaType": "Audio",
                    "UserId": user_id,
                },
            )
            r.raise_for_status()
        return r.json()["Id"]

    async def update_playlist(self, playlist_id: str, item_ids: List[str]) -> None:
        """Replace all items in an existing Jellyfin playlist."""
        user_id = await self.get_user_id()
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{self.base_url}/Playlists/{playlist_id}/Items",
                headers=self._headers(),
                params={"UserId": user_id},
            )
            if r.status_code == 200:
                entry_ids = [
                    i["PlaylistItemId"] for i in r.json().get("Items", [])
                ]
                if entry_ids:
                    await client.delete(
                        f"{self.base_url}/Playlists/{playlist_id}/Items",
                        headers=self._headers(),
                        params={"EntryIds": ",".join(entry_ids)},
                    )
            if item_ids:
                await client.post(
                    f"{self.base_url}/Playlists/{playlist_id}/Items",
                    headers=self._headers(),
                    params={"Ids": ",".join(item_ids), "UserId": user_id},
                )

    async def delete_playlist(self, playlist_id: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.delete(
                f"{self.base_url}/Items/{playlist_id}", headers=self._headers()
            )
            r.raise_for_status()
