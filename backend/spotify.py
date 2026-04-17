import asyncio
import base64
import hashlib
import re
import secrets
import httpx
from datetime import datetime, timedelta, timezone

PLAYLIST_RE = re.compile(r"open\.spotify\.com/playlist/([A-Za-z0-9]+)")


def extract_playlist_id(url: str) -> str | None:
    m = PLAYLIST_RE.search(url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Client Credentials (kept for fallback / public playlists without login)
# ---------------------------------------------------------------------------

async def get_access_token(client_id: str, client_secret: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://accounts.spotify.com/api/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]


# ---------------------------------------------------------------------------
# OAuth / PKCE helpers
# ---------------------------------------------------------------------------

def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _is_token_expired(expires_at_str: str | None) -> bool:
    if not expires_at_str:
        return True
    try:
        expires = datetime.fromisoformat(expires_at_str)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expires - timedelta(seconds=60)
    except Exception:
        return True


async def _do_refresh(client_id: str, refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://accounts.spotify.com/api/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )
        r.raise_for_status()
        data = r.json()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))
        ).isoformat()
        result = {
            "spotify_access_token": data["access_token"],
            "spotify_token_expires_at": expires_at,
        }
        # Spotify may or may not return a new refresh token
        if data.get("refresh_token"):
            result["spotify_refresh_token"] = data["refresh_token"]
        return result


async def get_oauth_token(config: dict) -> str | None:
    """Return a valid OAuth access token, refreshing inline if needed. None if not configured."""
    access_token = config.get("spotify_access_token", "")
    refresh_token = config.get("spotify_refresh_token", "")
    if not access_token and not refresh_token:
        return None

    if not _is_token_expired(config.get("spotify_token_expires_at")):
        return access_token

    if not refresh_token:
        return None

    client_id = config.get("spotify_client_id", "")
    if not client_id:
        return None

    from config import save_config  # avoid circular at module level
    new_data = await _do_refresh(client_id, refresh_token)
    save_config(new_data)
    return new_data["spotify_access_token"]


async def exchange_code(client_id: str, code: str, redirect_uri: str, code_verifier: str) -> dict:
    """Exchange auth code for tokens. Returns dict to merge into config."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://accounts.spotify.com/api/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
        )
        r.raise_for_status()
        data = r.json()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))
        ).isoformat()
        return {
            "spotify_access_token": data["access_token"],
            "spotify_refresh_token": data.get("refresh_token", ""),
            "spotify_token_expires_at": expires_at,
        }


async def get_current_user(token: str) -> dict:
    """Return {id, display_name} for the authenticated Spotify user."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            "https://api.spotify.com/v1/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        data = r.json()
        return {"id": data.get("id", ""), "display_name": data.get("display_name", data.get("id", ""))}


# ---------------------------------------------------------------------------
# Playlist fetching
# ---------------------------------------------------------------------------

async def fetch_playlist(playlist_id: str, token: str) -> dict:
    """Return {name, tracks: [{artist, title, album}], artists}. Works with both token types."""
    headers = {"Authorization": f"Bearer {token}"}
    tracks = []

    async with httpx.AsyncClient(timeout=30) as client:
        meta_r = await client.get(
            f"https://api.spotify.com/v1/playlists/{playlist_id}",
            headers=headers,
            params={"market": "US"},
        )
        meta_r.raise_for_status()
        playlist_name = meta_r.json().get("name", "Spotify Playlist")

        url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
        params: dict = {"limit": 100, "market": "US"}

        while url:
            r = await client.get(url, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            params = {}

            for item in data.get("items", []):
                track = item.get("track")
                if not track or track.get("type") == "episode":
                    continue
                artist = ", ".join(a["name"] for a in track.get("artists", []))
                title = track.get("name", "")
                album = track.get("album", {}).get("name", "")
                if artist and title:
                    tracks.append({"artist": artist, "title": title, "album": album or None})

            url = data.get("next")

    seen: set = set()
    artists = []
    for t in tracks:
        name = t["artist"]
        if name not in seen:
            seen.add(name)
            artists.append({"name": name})

    return {"name": playlist_name, "artists": artists, "tracks": tracks}


async def fetch_liked_songs(token: str) -> dict:
    """Return {name, tracks, artists} for the user's Liked Songs."""
    headers = {"Authorization": f"Bearer {token}"}
    tracks = []

    async with httpx.AsyncClient(timeout=30) as client:
        url = "https://api.spotify.com/v1/me/tracks"
        params: dict = {"limit": 50, "market": "US"}

        while url:
            r = await client.get(url, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            params = {}

            for item in data.get("items", []):
                track = item.get("track")
                if not track:
                    continue
                artist = ", ".join(a["name"] for a in track.get("artists", []))
                title = track.get("name", "")
                album = track.get("album", {}).get("name", "")
                if artist and title:
                    tracks.append({"artist": artist, "title": title, "album": album or None})

            url = data.get("next")

    seen: set = set()
    artists = []
    for t in tracks:
        name = t["artist"]
        if name not in seen:
            seen.add(name)
            artists.append({"name": name})

    return {"name": "Liked Songs", "artists": artists, "tracks": tracks}


async def get_all_playlists(token: str) -> list[dict]:
    """
    Return all playlists from the user's Spotify library: user-owned playlists,
    followed/editorial ones (Discover Weekly, Daily Mixes, etc.), and Liked Songs.
    """
    headers = {"Authorization": f"Bearer {token}"}
    results = [{"id": "liked_songs", "name": "Liked Songs", "description": "Your saved tracks", "type": "liked_songs"}]
    editorial: list[dict] = []
    user_playlists: list[dict] = []

    async with httpx.AsyncClient(timeout=30) as client:
        # Fetch the current user's ID so we can tag their playlists
        me_r = await client.get("https://api.spotify.com/v1/me", headers=headers)
        me_r.raise_for_status()
        current_user_id = me_r.json().get("id", "")

        url = "https://api.spotify.com/v1/me/playlists"
        params: dict = {"limit": 50}

        while url:
            r = await client.get(url, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            params = {}

            for pl in data.get("items", []):
                if not pl:
                    continue
                name = pl.get("name", "")
                owner_id = pl.get("owner", {}).get("id", "")
                playlist_id = pl.get("id", "")
                description = pl.get("description", "")

                if owner_id == current_user_id:
                    user_playlists.append({
                        "id": playlist_id,
                        "name": name,
                        "description": description,
                        "type": "user",
                    })
                else:
                    editorial.append({
                        "id": playlist_id,
                        "name": name,
                        "description": description,
                        "type": "editorial",
                    })

            url = data.get("next")

    results.extend(editorial)
    results.extend(user_playlists)
    return results


# ---------------------------------------------------------------------------
# Push to Spotify (Plex → Spotify / any playlist → Spotify)
# ---------------------------------------------------------------------------

async def _search_track_uri(artist: str, title: str, client: httpx.AsyncClient, headers: dict) -> str | None:
    """Search Spotify for a track, return its URI or None."""
    q = f'track:"{title}" artist:"{artist}"'
    r = await client.get(
        "https://api.spotify.com/v1/search",
        headers=headers,
        params={"q": q, "type": "track", "limit": 1, "market": "US"},
    )
    if r.status_code != 200:
        return None
    items = r.json().get("tracks", {}).get("items", [])
    return items[0]["uri"] if items else None


async def push_to_spotify(
    user_id: str,
    name: str,
    tracks: list[dict],
    token: str,
    existing_playlist_id: str | None = None,
) -> dict:
    """
    Create or replace a Spotify playlist with the given tracks.
    Returns {playlist_id, matched_count, total_count, playlist_url}.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    sem = asyncio.Semaphore(5)

    async def search(track: dict) -> str | None:
        async with sem:
            async with httpx.AsyncClient(timeout=10) as c:
                return await _search_track_uri(track["artist"], track["title"], c, headers)

    uris = await asyncio.gather(*[search(t) for t in tracks])
    matched_uris = [u for u in uris if u]
    matched_count = len(matched_uris)

    async with httpx.AsyncClient(timeout=30) as client:
        if existing_playlist_id:
            playlist_id = existing_playlist_id
            # Replace all tracks: clear first, then add
            await client.put(
                f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
                headers=headers,
                json={"uris": []},
            )
        else:
            r = await client.post(
                f"https://api.spotify.com/v1/users/{user_id}/playlists",
                headers=headers,
                json={"name": name, "public": False, "description": "Created by Digarr"},
            )
            r.raise_for_status()
            playlist_id = r.json()["id"]

        # Add tracks in batches of 100
        for i in range(0, len(matched_uris), 100):
            batch = matched_uris[i:i + 100]
            await client.post(
                f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
                headers=headers,
                json={"uris": batch},
            )

    return {
        "playlist_id": playlist_id,
        "matched_count": matched_count,
        "total_count": len(tracks),
        "playlist_url": f"https://open.spotify.com/playlist/{playlist_id}",
    }
