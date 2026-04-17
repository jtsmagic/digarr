import httpx

BASE = "https://api.discogs.com"
# Discogs requires a User-Agent and personal access token for all requests
HEADERS = {"User-Agent": "Digarr/1.0"}

_SKIP_ARTISTS = {"various", "various artists", "unknown artist", "va"}


async def get_wantlist(token: str, username: str, limit: int = 500) -> dict:
    """
    Fetch a user's Discogs wantlist.
    Returns {name, artists: [{name}], tracks: [{artist, title, album}]}
    where each "track" represents a release (artist + album title).
    Raises httpx.HTTPStatusError on API errors.
    """
    headers = {**HEADERS, "Authorization": f"Discogs token={token}"}
    releases = []
    page = 1

    async with httpx.AsyncClient(timeout=20) as client:
        while len(releases) < limit:
            r = await client.get(
                f"{BASE}/users/{username}/wants",
                headers=headers,
                params={"page": page, "per_page": 100},
            )
            r.raise_for_status()
            data = r.json()

            wants = data.get("wants", [])
            for want in wants:
                info = want.get("basic_information") or {}
                title = (info.get("title") or "").strip()
                year  = info.get("year")
                raw_artists = info.get("artists") or []
                for a in raw_artists:
                    name = (a.get("name") or "").strip()
                    # Strip trailing " (N)" disambiguation Discogs adds e.g. "Blur (2)"
                    import re
                    name = re.sub(r"\s*\(\d+\)$", "", name).strip()
                    if name and name.lower() not in _SKIP_ARTISTS and title:
                        releases.append({"artist": name, "title": title, "year": year})
                        break  # one primary artist per release is enough

            pagination = data.get("pagination") or {}
            total_pages = int(pagination.get("pages") or 1)
            if page >= total_pages:
                break
            page += 1

    # Deduplicate by (artist, title)
    seen: set = set()
    tracks = []
    for r in releases:
        key = (r["artist"].lower(), r["title"].lower())
        if key not in seen:
            seen.add(key)
            tracks.append({"artist": r["artist"], "title": r["title"], "album": r["title"]})

    # Unique artists preserving order
    seen_artists: set = set()
    artists = []
    for t in tracks:
        name = t["artist"]
        if name not in seen_artists:
            seen_artists.add(name)
            artists.append({"name": name})

    return {
        "name": "Discogs Wantlist",
        "artists": artists,
        "tracks": tracks,
    }
