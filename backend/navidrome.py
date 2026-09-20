import asyncio
import hashlib
import logging
import secrets
from typing import List, Optional, Tuple

import httpx

from utils import normalize as _normalize
from utils import strip_year_suffix as _strip_year_suffix

logger = logging.getLogger(__name__)

_API_VERSION = "1.16.1"
_CLIENT_NAME = "digarr"

# Subsonic error code for "the requested data was not found".
_NOT_FOUND = 70


class SubsonicError(ValueError):
    """A non-ok Subsonic response. Subclasses ValueError so existing callers
    that catch ValueError keep working; `code` lets new ones tell a missing
    playlist apart from a real failure."""

    def __init__(self, code, message: str):
        self.code = int(code) if str(code).isdigit() else None
        super().__init__(f"Subsonic error {code}: {message}")


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
            raise SubsonicError(err.get("code", "?"), err.get("message", "unknown"))
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
            raise SubsonicError(err.get("code", "?"), err.get("message", "unknown"))
        return body

    async def test_connection(self) -> dict:
        data = await self._get("ping")
        return {"status": "ok", "version": data.get("version", "")}

    async def search_track(self, artist: str, title: str) -> Optional[str]:
        if not title:
            return None
        match = await self._search_once(artist, title)
        if match is not None:
            return match
        # Subsonic search3 ANDs its query tokens, so a year tag carried over from
        # the source matches nothing at all - the library holds no "83" for
        # "Little Red Corvette (83)" to hit, and the search comes back empty.
        # normalize() strips punctuation but keeps digits, so even a lucky result
        # would fail every comparison below. Retrying without the tag, rather than
        # stripping it up front, leaves a title that genuinely ends in a
        # parenthesised number - and the exact match it would have made - alone.
        bare = _strip_year_suffix(title)
        if bare and bare != title:
            return await self._search_once(artist, bare)
        return None

    async def _search_once(self, artist: str, title: str) -> Optional[str]:
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
        self, tracks: List[dict], on_progress=None
    ) -> Tuple[List[str], List[dict], int]:
        semaphore = asyncio.Semaphore(3)

        done = 0

        async def search_one(track):
            nonlocal done
            result = None
            async with semaphore:
                try:
                    result = await self.search_track(
                        track.get("artist", ""), track.get("title", "")
                    )
                except Exception as exc:
                    logger.warning(
                        "Navidrome search error for %r / %r: %s",
                        track.get("artist"),
                        track.get("title"),
                        exc,
                    )
                    result = None
            done += 1
            if on_progress:
                try:
                    on_progress(done, len(tracks))
                except Exception:
                    pass
            return result

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

    async def find_playlist_id(self, name: str) -> Optional[str]:
        """Id of the caller's playlist with this exact name, or None."""
        data = await self._get("getPlaylists")
        for pl in (data.get("playlists") or {}).get("playlist") or []:
            if pl.get("name") == name:
                return str(pl["id"])
        return None

    async def upsert_playlist(
        self, playlist_id: Optional[str], name: str, song_ids: List[str]
    ) -> str:
        """Update the playlist we remember, or recover if it no longer exists.

        A stored id can stop resolving without anything on our side changing:
        the user deletes the playlist in Navidrome, or Navidrome rewrites its
        ids (0.64.0 migrated every id to a new canonical format). Subsonic
        answers both with error 70. Treating that as fatal would leave the
        playlist failing on every scheduled sync with no way back, so on a 70
        we re-resolve by name and fall back to creating it. Returns the id that
        is now live, which the caller must persist.
        """
        if playlist_id:
            try:
                await self.update_playlist(playlist_id, name, song_ids)
                return playlist_id
            except SubsonicError as e:
                if e.code != _NOT_FOUND:
                    raise
                not_found = e
            found = await self.find_playlist_id(name)
            if found == playlist_id:
                # The playlist is there, so the 70 was about something else
                # (a song id, most likely). Recreating would only duplicate it.
                raise not_found
            logger.warning(
                "Navidrome playlist %r (id %s) no longer exists; re-resolving by name",
                name, playlist_id,
            )
            if found:
                await self.update_playlist(found, name, song_ids)
                return found
        return await self.create_playlist(name, song_ids)

    async def delete_playlist(self, playlist_id: str) -> None:
        await self._get("deletePlaylist", {"id": playlist_id})
