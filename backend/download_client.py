"""
Download client abstraction.

Add Headphones, Beets, Navidrome/Airsonic downloaders, etc. by subclassing
DownloadClient.  Lidarr is the first implementation.

Relationship to MediaClient:
  - MediaClient owns what you *have* (the music library).
  - DownloadClient owns what you *want* (the download queue / catalog search).

When a playlist is imported and tracks don't match in the media library,
the orchestration layer in main.py passes those unmatched tracks to the
active DownloadClient so it can search and queue them for download.
"""
from abc import ABC, abstractmethod
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


class DownloadClient(ABC):
    """
    Media-server-agnostic interface for searching and queuing downloads.
    The `source` class attribute identifies the backend (e.g. "lidarr")
    and is used as the discriminator in download_search_cache /
    download_queue_cache.
    """

    source: str  # e.g. "lidarr", "headphones"

    @abstractmethod
    async def search(self, artist: str, album: Optional[str] = None) -> List[dict]:
        """
        Search the download catalog.
        Returns a list of result dicts, each with at least:
          { artist, album, id (catalog ID), status, ... }
        Results are suitable for caching in download_search_cache.
        """
        raise NotImplementedError

    @abstractmethod
    async def queue_download(self, artist: str, album: Optional[str] = None) -> dict:
        """
        Add an artist/album to the download queue.
        Returns a status dict with at least:
          { status: "queued"|"already_queued"|"error", artist, album, message }
        """
        raise NotImplementedError

    @abstractmethod
    async def get_queue_status(self) -> List[dict]:
        """
        Return the current download queue.
        Each item has at least:
          { id, artist, album, status, progress, ... }
        Suitable for caching in download_queue_cache.
        """
        raise NotImplementedError


class LidarrDownloadClient(DownloadClient):
    """Lidarr implementation of DownloadClient."""

    source = "lidarr"

    def __init__(self, lidarr_client):
        """
        Parameters
        ----------
        lidarr_client : LidarrClient
            An already-constructed LidarrClient instance.
        """
        self._lidarr = lidarr_client

    async def search(self, artist: str, album: Optional[str] = None) -> List[dict]:
        """Search MusicBrainz via Lidarr's artist lookup endpoint."""
        results = await self._lidarr.search_artist(artist)
        mapped = []
        for r in results:
            mapped.append({
                "artist": r.get("artistName", ""),
                "album": album,
                "id": r.get("foreignArtistId", ""),
                "status": "found",
                "data": r,
            })
        return mapped

    async def queue_download(self, artist: str, album: Optional[str] = None) -> dict:
        """
        Add the artist to Lidarr and monitor the target album.
        Delegates to LidarrClient.add_artist() / ensure_album_monitored().
        """
        result = await self._lidarr.add_artist(artist, album_hint=album)
        status_map = {
            "added": "queued",
            "already_exists": "already_queued",
            "not_found": "not_found",
            "error": "error",
        }
        return {
            "status": status_map.get(result.get("status"), "error"),
            "artist": result.get("artist", artist),
            "album": result.get("album_monitored") or album,
            "message": result.get("message", ""),
        }

    async def get_queue_status(self) -> List[dict]:
        """Fetch current Lidarr wanted/missing as a proxy for queue status."""
        try:
            data = await self._lidarr.get_wanted_missing(page_size=50)
            records = data.get("records") or []
            return [
                {
                    "id": str(r.get("id", "")),
                    "artist": r.get("artist", {}).get("artistName", ""),
                    "album": r.get("title", ""),
                    "status": "wanted",
                    "progress": 0,
                }
                for r in records
            ]
        except Exception as exc:
            logger.warning("LidarrDownloadClient.get_queue_status failed: %s", exc)
            return []
