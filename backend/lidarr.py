import re
import asyncio
import logging
import httpx
from typing import Optional
from utils import normalize as _normalize

logger = logging.getLogger(__name__)


class LidarrClient:
    def __init__(self, base_url: str, api_key: str, quality_profile_id: int = 1,
                 metadata_profile_id: int = 1, root_folder: str = "/music"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.quality_profile_id = quality_profile_id
        self.metadata_profile_id = metadata_profile_id
        self.root_folder = root_folder
        self.headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, **kwargs) -> dict | list:
        url = f"{self.base_url}/api/v1{path}"
        for attempt in range(2):
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.request(method, url, headers=self.headers, **kwargs)
                if r.status_code == 503 and attempt == 0:
                    logger.warning("Lidarr returned 503 for %s %s — retrying in 3s", method, path)
                    await asyncio.sleep(3)
                    continue
                r.raise_for_status()
                return r.json()

    async def _get(self, path: str) -> dict | list:
        return await self._request("GET", path)

    async def _post(self, path: str, data: dict) -> dict:
        return await self._request("POST", path, json=data)

    async def _put(self, path: str, data: dict) -> dict:
        return await self._request("PUT", path, json=data)

    async def get_all_artists(self) -> list:
        return await self._get("/artist")

    async def get_wanted_missing(self, page: int = 1, page_size: int = 100) -> dict:
        return await self._get(
            f"/wanted/missing?page={page}&pageSize={page_size}"
            "&sortKey=albums.title&sortDirection=ascending&monitored=true"
        )

    async def get_quality_profiles(self) -> list:
        return await self._get("/qualityprofile")

    async def get_metadata_profiles(self) -> list:
        return await self._get("/metadataprofile")

    async def get_root_folders(self) -> list:
        return await self._get("/rootfolder")

    async def validate_config(self) -> list[str]:
        """Return a list of human-readable errors if the configured profile IDs / root folder
        don't exist in this Lidarr instance. Empty list means config is valid."""
        import asyncio
        quality_task = asyncio.create_task(self.get_quality_profiles())
        metadata_task = asyncio.create_task(self.get_metadata_profiles())
        folders_task = asyncio.create_task(self.get_root_folders())
        try:
            quality_profiles, metadata_profiles, root_folders = await asyncio.gather(
                quality_task, metadata_task, folders_task
            )
        except Exception as exc:
            return [f"Could not reach Lidarr to validate profile config: {exc}"]

        errors = []
        quality_ids = {p["id"] for p in quality_profiles}
        if self.quality_profile_id not in quality_ids:
            names = ", ".join(f"{p['name']} (id={p['id']})" for p in quality_profiles)
            errors.append(
                f"Quality profile ID {self.quality_profile_id} not found in Lidarr. "
                f"Go to Settings → Lidarr and click 'Load Profiles'. Available: {names or 'none'}"
            )

        metadata_ids = {p["id"] for p in metadata_profiles}
        if self.metadata_profile_id not in metadata_ids:
            names = ", ".join(f"{p['name']} (id={p['id']})" for p in metadata_profiles)
            errors.append(
                f"Metadata profile ID {self.metadata_profile_id} not found in Lidarr. "
                f"Go to Settings → Lidarr and click 'Load Profiles'. Available: {names or 'none'}"
            )

        folder_paths = {f["path"] for f in root_folders}
        if self.root_folder not in folder_paths:
            available = ", ".join(f["path"] for f in root_folders) or "none"
            errors.append(
                f"Root folder '{self.root_folder}' not found in Lidarr. "
                f"Go to Settings → Lidarr and click 'Load Profiles'. Available: {available}"
            )

        return errors

    async def search_artist(self, name: str) -> list:
        import urllib.parse
        encoded = urllib.parse.quote(name)
        results = await self._get(f"/artist/lookup?term={encoded}")
        return results

    async def get_artist_albums(self, artist_id: int) -> list:
        return await self._get(f"/album?artistId={artist_id}")

    async def _fetch_albums_with_retry(self, artist_id: int, retries: int = 4, delay: float = 2.5) -> list:
        """Poll for artist albums — Lidarr may take a few seconds to sync from MusicBrainz."""
        for attempt in range(retries):
            try:
                albums = await self.get_artist_albums(artist_id)
                if albums:
                    logger.info("Fetched %d albums for artist_id=%d (attempt %d)", len(albums), artist_id, attempt + 1)
                    return albums
            except Exception as exc:
                logger.warning("Album fetch attempt %d failed for artist_id=%d: %s", attempt + 1, artist_id, exc)
            if attempt < retries - 1:
                await asyncio.sleep(delay)
        logger.warning("No albums found for artist_id=%d after %d attempts", artist_id, retries)
        return []

    async def _monitor_and_search_album(self, album: dict) -> str:
        """Mark one album monitored and trigger a search. Returns the album title."""
        updated = {**album, "monitored": True}
        result = await self._put(f"/album/{album['id']}", updated)

        # Lidarr's initial RefreshArtist job (queued on artist add) can race with this PUT
        # and reset the monitored flag. Verify the response and retry once if it didn't stick.
        if isinstance(result, dict) and not result.get("monitored"):
            logger.warning("Album %r (id=%s): PUT returned monitored=False — retrying",
                           album.get("title"), album["id"])
            result = await self._put(f"/album/{album['id']}", {**result, "monitored": True})
            if isinstance(result, dict) and not result.get("monitored"):
                logger.error("Album %r (id=%s): monitored still False after retry",
                             album.get("title"), album["id"])

        # Fire a background re-monitor after a delay: AlbumSearch can grab regardless of the
        # monitored flag, but Lidarr only imports the completed download when monitored=True.
        # If RefreshArtist resets the flag after our PUT, the re-monitor ensures it's set
        # before the download finishes and Lidarr's import handler runs.
        # Use the latest PUT result so the re-monitor doesn't overwrite fields Lidarr updated.
        latest = result if isinstance(result, dict) and result.get("id") else album
        asyncio.create_task(self._remonitor_after_delay(latest))

        try:
            await self._post("/command", {"name": "AlbumSearch", "albumIds": [album["id"]]})
        except Exception as exc:
            logger.warning("AlbumSearch command failed for album_id=%d: %s", album["id"], exc)
        return album.get("title", "")

    async def _remonitor_after_delay(self, album: dict, delay: float = 30.0) -> None:
        """Re-set monitored=True after a delay to survive Lidarr's background RefreshArtist."""
        await asyncio.sleep(delay)
        try:
            await self._put(f"/album/{album['id']}", {**album, "monitored": True})
            logger.info("Re-monitored album %r (id=%s) after %.0fs delay",
                        album.get("title"), album["id"], delay)
        except Exception as exc:
            logger.debug("Background re-monitor for album_id=%s failed: %s", album.get("id"), exc)

    def _find_album_by_hint(self, albums: list, hint: str) -> Optional[dict]:
        norm = _normalize(hint)
        for album in albums:
            if _normalize(album.get("title", "")) == norm:
                return album
        # Partial match fallback
        for album in albums:
            if norm in _normalize(album.get("title", "")):
                return album
        return None

    def _most_recent_album(self, albums: list) -> Optional[dict]:
        """Return the most recently released non-single album, or any album if none found."""
        # Prefer album types that aren't singles/EPs
        studio = [a for a in albums if a.get("albumType", "").lower() not in ("single", "ep")]
        candidates = studio if studio else albums
        dated = [a for a in candidates if a.get("releaseDate")]
        if dated:
            return max(dated, key=lambda a: a["releaseDate"])
        return candidates[0] if candidates else None

    def _match_in_library(self, name: str, all_artists: list) -> Optional[dict]:
        """Check a pre-fetched artist list — no HTTP call."""
        norm = _normalize(name)
        for a in all_artists:
            if _normalize(a.get("artistName", "")) == norm:
                return a
        return None

    async def is_artist_in_library(self, name: str) -> Optional[dict]:
        artists = await self.get_all_artists()
        return self._match_in_library(name, artists)

    async def get_artist_by_foreign_id(self, foreign_id: str) -> Optional[dict]:
        artists = await self.get_all_artists()
        for a in artists:
            if a.get("foreignArtistId") == foreign_id:
                return a
        return None

    async def get_track_statuses(self, tracks: list) -> list:
        """
        Given a list of {artist, title, album} dicts, return the same list
        with a 'status' field added: 'green' | 'yellow' | 'red'.

        green  — artist in Lidarr AND has at least one downloaded track file
        yellow — artist in Lidarr but no files downloaded yet
        red    — artist not in Lidarr at all
        """
        all_artists = await self.get_all_artists()
        artist_map = {_normalize(a.get("artistName", "")): a for a in all_artists}

        result = []
        for track in tracks:
            artist_name = (track.get("artist") or "").strip()
            key = _normalize(artist_name)
            artist = artist_map.get(key)

            if artist is None:
                status = "red"
            else:
                stats = artist.get("statistics", {})
                track_file_count = stats.get("trackFileCount", 0)
                status = "green" if track_file_count > 0 else "yellow"

            result.append({**track, "status": status})

        return result

    async def check_artists_in_library(self, names: list) -> dict:
        """Returns {name: bool} — True if the artist is already monitored in Lidarr."""
        all_artists = await self.get_all_artists()
        norm_map = {_normalize(a.get("artistName", "")): True for a in all_artists}
        return {name: (_normalize(name) in norm_map) for name in names}

    async def _ensure_album_monitored_for_artist(self, artist: dict, album_hint: Optional[str] = None) -> dict:
        """
        Given an already-fetched artist dict, ensure the artist and target album are monitored
        and trigger a search. Returns a status dict with keys: status, artist, album.
        """
        artist_name = artist.get("artistName", "")
        artist_id = artist["id"]

        # Ensure the artist itself is monitored, otherwise Lidarr won't import downloads
        if not artist.get("monitored"):
            logger.warning("ensure_album_monitored: artist %r is unmonitored — fixing", artist_name)
            try:
                await self._put(f"/artist/{artist_id}", {**artist, "monitored": True})
            except Exception as exc:
                logger.error("Failed to re-monitor artist %r: %s", artist_name, exc)

        albums = await self.get_artist_albums(artist_id)
        if not albums:
            return {"status": "no_albums", "artist": artist_name, "album": None}

        target = None
        if album_hint:
            target = self._find_album_by_hint(albums, album_hint)
        if not target:
            target = self._most_recent_album(albums)
        if not target:
            return {"status": "album_not_found", "artist": artist_name, "album": None}

        if target.get("monitored"):
            # Album already monitored — still search in case it was never successfully grabbed
            try:
                await self._post("/command", {"name": "AlbumSearch", "albumIds": [target["id"]]})
                logger.info("AlbumSearch triggered for already-monitored %r / %r",
                            artist_name, target.get("title"))
            except Exception as exc:
                logger.warning("AlbumSearch failed for already-monitored album_id=%s: %s",
                               target.get("id"), exc)
            return {"status": "already_monitored", "artist": artist_name, "album": target.get("title")}

        album_title = await self._monitor_and_search_album(target)
        logger.info("ensure_album_monitored: monitored %r for %r", album_title, artist_name)
        return {"status": "monitored", "artist": artist_name, "album": album_title}

    async def ensure_album_monitored(self, artist_name: str, album_hint: Optional[str] = None) -> dict:
        """Public entry point — looks up artist by name then delegates."""
        artist = await self.is_artist_in_library(artist_name)
        if not artist:
            return {"status": "artist_not_found", "artist": artist_name, "album": None}
        return await self._ensure_album_monitored_for_artist(artist, album_hint)

    async def ensure_album_monitored_with_library(self, artist_name: str, album_hint: Optional[str], all_artists: list) -> dict:
        """Like ensure_album_monitored but uses a pre-fetched library — no extra get_all_artists() call."""
        artist = self._match_in_library(artist_name, all_artists)
        if not artist:
            return {"status": "artist_not_found", "artist": artist_name, "album": None}
        return await self._ensure_album_monitored_for_artist(artist, album_hint)

    async def _already_exists_response(self, name: str, existing: dict, album_hint: Optional[str]) -> dict:
        """Build the response for an artist that's already in the library.
        If album_hint is provided, ensures that album is monitored."""
        album_monitored = None
        album_match_type = None
        if album_hint:
            album_result = await self._ensure_album_monitored_for_artist(existing, album_hint)
            if album_result["status"] in ("monitored", "already_monitored"):
                album_monitored = album_result["album"]
                album_match_type = "hint_match"
        return {
            "artist": name,
            "status": "already_exists",
            "message": f"{name} is already in your Lidarr library",
            "album_monitored": album_monitored,
            "album_match_type": album_match_type,
            "data": existing,
        }

    async def trigger_manual_import(self, folder: str) -> dict:
        """
        Ask Lidarr to scan a folder and auto-import whatever it finds.
        Uses the ManualImport command with importMode="auto" so no UI approval needed.
        """
        # Step 1: get the list of importable files in the folder
        files = await self._get(f"/manualimport?folder={folder}&filterExistingFiles=true&replaceExistingFiles=false")
        if not files:
            logger.info("Lidarr manual import: no importable files found in %s", folder)
            return {"imported": 0, "folder": folder}

        # Only include files that Lidarr matched to something
        matched = [f for f in files if f.get("artist") or f.get("album")]
        if not matched:
            logger.info("Lidarr manual import: %d files found but none matched in %s", len(files), folder)
            return {"imported": 0, "unmatched": len(files), "folder": folder}

        result = await self._post("/command", {
            "name": "ManualImport",
            "files": matched,
            "importMode": "auto",
        })
        logger.info("Lidarr manual import triggered for %d files in %s: %s", len(matched), folder, result.get("status"))
        return {"imported": len(matched), "folder": folder, "command": result}

    async def add_artist(self, name: str, album_hint: Optional[str] = None, _library: list = None) -> dict:
        logger.info("add_artist called: name=%r album_hint=%r", name, album_hint)

        # Check if already exists — use pre-fetched library if provided to avoid redundant HTTP calls
        existing = (self._match_in_library(name, _library) if _library is not None
                    else await self.is_artist_in_library(name))
        if existing:
            return await self._already_exists_response(name, existing, album_hint)

        # Search MusicBrainz
        results = await self.search_artist(name)
        if not results:
            return {
                "artist": name,
                "status": "not_found",
                "message": f"Could not find {name} in MusicBrainz",
                "album_monitored": None,
            }

        top = results[0]
        logger.info("MusicBrainz top result for %r: %r (id=%s)",
                    name, top.get("artistName"), top.get("foreignArtistId"))

        payload = {
            "artistName": top.get("artistName"),
            "foreignArtistId": top.get("foreignArtistId"),
            "qualityProfileId": self.quality_profile_id,
            "metadataProfileId": self.metadata_profile_id,
            "rootFolderPath": self.root_folder,
            # monitored:true = artist is monitored; addOptions.monitor:"none" = don't
            # auto-monitor any albums on add (we'll target a specific one below).
            # These are independent fields — artist-level monitored controls whether
            # Lidarr tracks the artist at all; addOptions.monitor controls which albums
            # get their monitored flag set during the initial add.
            "monitored": True,
            "addOptions": {
                "monitor": "none",
                "searchForMissingAlbums": False,
            },
            "images": top.get("images", []),
            "links": top.get("links", []),
            "genres": top.get("genres", []),
        }

        try:
            result = await self._post("/artist", payload)
        except httpx.HTTPStatusError as e:
            body = e.response.text
            if "ArtistExistsValidator" in body or "already been added" in body.lower():
                foreign_id = top.get("foreignArtistId")
                if foreign_id:
                    existing = await self.get_artist_by_foreign_id(foreign_id)
                    if existing:
                        return await self._already_exists_response(name, existing, album_hint)
                existing = await self.is_artist_in_library(top.get("artistName", name))
                if existing:
                    return await self._already_exists_response(name, existing, album_hint)
            return {
                "artist": name,
                "status": "error",
                "message": f"Failed to add {name}: {body}",
                "album_monitored": None,
            }

        # --- artist-monitored safety check ---
        # Some Lidarr versions return monitored:false when addOptions.monitor:"none"
        # is used, ignoring the artist-level monitored:true in the payload.
        # Detect and fix this with a follow-up PUT before doing any album work.
        artist_id = result.get("id")
        is_monitored = result.get("monitored", True)
        logger.info("Artist %r added (id=%s) — Lidarr returned monitored=%s",
                    name, artist_id, is_monitored)

        if artist_id and not is_monitored:
            logger.warning("Artist %r came back unmonitored — sending follow-up PUT to fix", name)
            try:
                result = await self._put(f"/artist/{artist_id}", {**result, "monitored": True})
                logger.info("Artist %r monitored corrected to %s", name, result.get("monitored"))
            except Exception as exc:
                logger.error("Failed to re-monitor artist %r: %s", name, exc)

        # --- album monitoring ---
        album_monitored = None
        album_match_type = None

        if artist_id:
            albums = await self._fetch_albums_with_retry(artist_id)
            logger.info("Albums available for %r (id=%d): %d total", name, artist_id, len(albums))

            if albums:
                target = None

                if album_hint:
                    target = self._find_album_by_hint(albums, album_hint)
                    if target:
                        album_match_type = "hint_match"
                        logger.info("Matched album hint %r → %r for %r", album_hint, target.get("title"), name)
                    else:
                        logger.info("Album hint %r not found for %r — falling back to most recent", album_hint, name)

                if not target:
                    target = self._most_recent_album(albums)
                    album_match_type = "most_recent"
                    if target:
                        logger.info("Using most recent album %r for %r", target.get("title"), name)

                if target:
                    try:
                        album_monitored = await self._monitor_and_search_album(target)
                        logger.info("Monitoring album %r for %r (match_type=%s)",
                                    album_monitored, name, album_match_type)
                    except Exception as exc:
                        logger.error("Failed to monitor album for %r: %s", name, exc)
            else:
                logger.warning("No albums returned for %r — skipping album monitoring", name)

        msg = f"Added {name}"
        if album_monitored:
            msg += f" — monitoring \"{album_monitored}\""
            if album_match_type == "most_recent":
                msg += " (most recent)"

        return {
            "artist": name,
            "status": "added",
            "message": msg,
            "album_monitored": album_monitored,
            "album_match_type": album_match_type,
            "data": result,
        }
