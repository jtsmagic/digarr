import httpx

BASE = "https://ws.audioscrobbler.com/2.0/"


async def get_similar_artists(api_key: str, artist_name: str, limit: int = 15) -> list:
    """
    Fetch artists similar to artist_name via Last.fm artist.getSimilar.
    Returns [{name, match}] where match is 0.0–1.0.
    Raises httpx.HTTPStatusError on API errors.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(BASE, params={
            "method": "artist.getSimilar",
            "artist": artist_name,
            "api_key": api_key,
            "format": "json",
            "limit": limit,
            "autocorrect": 1,
        })
        r.raise_for_status()
        data = r.json()

    if "error" in data:
        return []  # Unknown artist — treat as no results, not a hard error

    items = data.get("similarartists", {}).get("artist", [])
    results = []
    for item in items:
        name = (item.get("name") or "").strip()
        try:
            match = float(item.get("match") or 0)
        except (TypeError, ValueError):
            match = 0.0
        if name:
            results.append({"name": name, "match": match})
    return results
