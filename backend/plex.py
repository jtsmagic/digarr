import re
import httpx
import asyncio
import logging
from typing import List, Optional, Tuple
from utils import normalize as _normalize

logger = logging.getLogger(__name__)


class PlexClient:
    def __init__(self, base_url: str, token: str, section_id: str):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.section_id = str(section_id)
        self._machine_id = None

    def _params(self, extra: dict = None) -> dict:
        p = {'X-Plex-Token': self.token}
        if extra:
            p.update(extra)
        return p

    async def get_machine_id(self) -> str:
        if self._machine_id:
            return self._machine_id
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.base_url}/",
                params=self._params(),
                headers={'Accept': 'application/json'},
            )
            r.raise_for_status()
            self._machine_id = r.json()['MediaContainer']['machineIdentifier']
        return self._machine_id

    async def get_sections(self) -> list:
        """Return all library sections as [{"id": "1", "title": "Music", "type": "artist"}, ...]."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.base_url}/library/sections",
                params=self._params(),
                headers={'Accept': 'application/json'},
            )
            r.raise_for_status()
        sections = r.json().get('MediaContainer', {}).get('Directory', [])
        return [{"id": str(s['key']), "title": s['title'], "type": s['type']} for s in sections]

    async def search_track(self, artist: str, title: str) -> Optional[str]:
        """Search Plex music library for a track. Returns ratingKey or None."""
        if not title:
            return None

        artist_norm = _normalize(artist)

        async def _hubs_search(query: str) -> list:
            """Global hub search — same endpoint the Plex UI uses; handles apostrophes correctly."""
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self.base_url}/hubs/search",
                    params=self._params({'query': query, 'sectionId': self.section_id, 'limit': 20}),
                    headers={'Accept': 'application/json'},
                )
                r.raise_for_status()
                hubs = r.json().get('MediaContainer', {}).get('Hub', [])
                track_hub = next((h for h in hubs if h.get('type') == 'track'), None)
                return (track_hub.get('Metadata') or []) if track_hub else []

        async def _filter_endpoint(query: str) -> list:
            """Substring title filter on the library section — fallback."""
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self.base_url}/library/sections/{self.section_id}/all",
                    params=self._params({'type': 10, 'title': query}),
                    headers={'Accept': 'application/json'},
                )
                r.raise_for_status()
                return r.json().get('MediaContainer', {}).get('Metadata', [])

        def _best_match(candidates: list, query_norm: str) -> Optional[str]:
            # Exact title + exact artist (punctuation-normalized)
            for t in candidates:
                if (_normalize(t.get('title', '')) == query_norm and
                        artist_norm in _normalize(t.get('grandparentTitle', ''))):
                    return t['ratingKey']
            # Query is a substring of result title + artist matches
            # (catches "I'm Alive" matching "I'm Alive (Life Sounds Like)")
            for t in candidates:
                if (query_norm in _normalize(t.get('title', '')) and
                        artist_norm in _normalize(t.get('grandparentTitle', ''))):
                    return t['ratingKey']
            # Exact title only (any artist)
            for t in candidates:
                if _normalize(t.get('title', '')) == query_norm:
                    return t['ratingKey']
            return None

        def _title_variants(t: str) -> list:
            """Full title first, then progressively strip parentheticals and dash suffixes."""
            variants = [t]
            # "I'm Alive (Life Sounds Like)" → "I'm Alive"
            m = re.match(r'^(.+?)\s*\(.*\)\s*$', t)
            if m:
                base = m.group(1).strip()
                if base and base not in variants:
                    variants.append(base)
            # "Moves Like Jagger - Studio Recording From ..." → "Moves Like Jagger"
            m = re.match(r'^(.+?)\s+-\s+.+$', t)
            if m:
                base = m.group(1).strip()
                if base and base not in variants:
                    variants.append(base)
            return variants

        # Try each title variant through /hubs/search
        for variant in _title_variants(title):
            variant_norm = _normalize(variant)
            tracks = await _hubs_search(variant)
            logger.debug("Plex /hubs/search query=%r artist=%r → %d result(s)", variant, artist, len(tracks))
            key = _best_match(tracks, variant_norm)
            if key:
                return key

        # Final fallback: /all?title= with punctuation-free words as substring
        clean_words = [w for w in title.split() if re.match(r'^\w+$', w)]
        if clean_words and len(' '.join(clean_words)) >= 4:
            clean_query = ' '.join(clean_words)
            tracks = await _filter_endpoint(clean_query)
            logger.debug("Plex /all title=%r → %d result(s)", clean_query, len(tracks))
            key = _best_match(tracks, _normalize(title))
            if key:
                return key

        return None

    async def match_tracks(self, tracks: List[dict]) -> Tuple[List[str], List[dict], int]:
        """
        Match a list of {artist, title} dicts against the Plex library.
        Returns (matched_ratingKeys, unmatched_tracks, total_track_count).
        """
        semaphore = asyncio.Semaphore(5)

        async def search_one(track):
            async with semaphore:
                try:
                    return await self.search_track(
                        track.get('artist', ''),
                        track.get('title', ''),
                    )
                except Exception as exc:
                    logger.warning("Plex search error for %r / %r: %s", track.get('artist'), track.get('title'), exc)
                    return None

        results = await asyncio.gather(*[search_one(t) for t in tracks])
        matched = [r for r, t in zip(results, tracks) if r is not None]
        unmatched = [dict(t) for r, t in zip(results, tracks) if r is None]
        return matched, unmatched, len(tracks)

    async def create_playlist(self, name: str, track_keys: List[str]) -> str:
        """Create a Plex audio playlist with the given track ratingKeys. Returns playlist ratingKey."""
        machine_id = await self.get_machine_id()
        uri = (
            f"server://{machine_id}/com.plexapp.plugins.library"
            f"/library/metadata/{','.join(track_keys)}"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{self.base_url}/playlists",
                params=self._params({'title': name, 'type': 'audio', 'smart': '0', 'uri': uri}),
                headers={'Accept': 'application/json'},
            )
            r.raise_for_status()
            metadata = r.json()['MediaContainer']['Metadata']
            return str(metadata[0]['ratingKey'])

    async def rename_playlist(self, plex_playlist_id: str, new_name: str) -> None:
        """Rename an existing Plex playlist in place by its ratingKey."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(
                f"{self.base_url}/playlists/{plex_playlist_id}",
                params=self._params({'title': new_name}),
                headers={'Accept': 'application/json'},
            )
            r.raise_for_status()

    async def delete_playlist(self, plex_playlist_id: str) -> None:
        """Delete a Plex playlist by its ratingKey."""
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.delete(
                f"{self.base_url}/playlists/{plex_playlist_id}",
                params=self._params(),
                headers={'Accept': 'application/json'},
            )
            r.raise_for_status()
