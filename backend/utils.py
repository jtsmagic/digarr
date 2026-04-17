import re


def normalize(s: str) -> str:
    """Lowercase, strip leading 'the ', remove punctuation for fuzzy matching."""
    s = s.lower().strip()
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def deduplicate_artists(artists: list) -> list:
    """Return a list of artist dicts with unique names (case-insensitive)."""
    seen = set()
    result = []
    for a in artists:
        name = a.get("name") if isinstance(a, dict) else a
        if name and name not in seen:
            seen.add(name)
            result.append(a)
    return result
