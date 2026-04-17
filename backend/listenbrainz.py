import re
import httpx

BASE = "https://api.listenbrainz.org/1"

# Playlist title prefixes as returned by the LB recommendations endpoint
_PLAYLIST_TYPES = {
    "weekly_jams":       "Weekly Jams",
    "daily_jams":        "Daily Jams",
    "weekly_exploration": "Weekly Exploration",
}


def _extract_mbid(identifier) -> str | None:
    """Pull a UUID from a MusicBrainz/ListenBrainz URL or plain UUID string."""
    if isinstance(identifier, list):
        identifier = identifier[0] if identifier else ""
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", str(identifier))
    return m.group(1) if m else None


async def get_recommendation_playlist(username: str, playlist_type: str = "weekly_jams") -> dict:
    """
    Fetch a ListenBrainz-generated recommendation playlist for a user.

    playlist_type: weekly_jams | daily_jams | weekly_exploration

    Flow:
      1. GET /1/user/{username}/playlists/recommendations  → find the most recent
         playlist whose title starts with the matching prefix.
      2. GET /1/playlist/{mbid}  → fetch full JSPF with tracks.

    Returns {name, artists: [{name}], tracks: [{artist, title, album}]}
    Raises httpx.HTTPStatusError on API errors (caller handles 404 as "user not found").
    Raises ValueError if no matching playlist is found.
    """
    type_label = _PLAYLIST_TYPES.get(playlist_type, "Weekly Jams")

    async with httpx.AsyncClient(timeout=20) as client:
        # Step 1 — find the playlist
        r = await client.get(
            f"{BASE}/user/{username}/playlists/recommendations",
            params={"count": 25},
        )
        r.raise_for_status()
        data = r.json()

    playlists = data.get("playlists", [])
    playlist_mbid = None
    playlist_title = None

    for entry in playlists:
        pl = entry.get("playlist", entry)  # LB wraps in {"playlist": {...}}
        title = pl.get("title", "")
        if title.lower().startswith(type_label.lower()):
            raw_id = pl.get("identifier", "")
            mbid = _extract_mbid(raw_id)
            if mbid:
                playlist_mbid = mbid
                playlist_title = title
                break

    if not playlist_mbid:
        raise ValueError(
            f"No '{type_label}' playlist found for '{username}'. "
            "ListenBrainz generates these weekly — the account may be too new or have too few listens."
        )

    # Step 2 — fetch the full playlist with tracks
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BASE}/playlist/{playlist_mbid}")
        r.raise_for_status()
        data = r.json()

    jspf = data.get("playlist", data)
    raw_tracks = jspf.get("track", [])

    seen_tracks: set = set()
    tracks = []
    for t in raw_tracks:
        artist = (t.get("creator") or "").strip()
        title  = (t.get("title") or "").strip()
        album  = (t.get("album") or "") or None
        key = (artist.lower(), title.lower())
        if artist and title and key not in seen_tracks:
            seen_tracks.add(key)
            tracks.append({"artist": artist, "title": title, **({"album": album} if album else {})})

    seen: set = set()
    artists = []
    for t in tracks:
        name = t["artist"]
        if name not in seen:
            seen.add(name)
            artists.append({"name": name})

    return {
        "name": playlist_title or f"ListenBrainz {type_label}",
        "artists": artists,
        "tracks": tracks,
    }
