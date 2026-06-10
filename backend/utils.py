import re

# Keywords indicating a cast recording / musical / soundtrack context.
# 'cast' is matched as a whole word to avoid 'podcast'/'broadcast'.
# Generic words like 'recording' and 'score' are intentionally excluded.
_CAST_KEYWORDS = frozenset({"broadway", "musical", "soundtrack", "original cast", "theatre", "theater", "west end"})
_CAST_WORD_RE = re.compile(r'\bcast\b')


def is_cast_context(name: str) -> bool:
    """Return True if name suggests a cast recording / musical / soundtrack."""
    n = (name or "").lower()
    return bool(_CAST_WORD_RE.search(n)) or any(kw in n for kw in _CAST_KEYWORDS)


def cast_score(name: str) -> int:
    """Count how many cast/musical keywords appear in name (higher = more likely cast)."""
    n = (name or "").lower()
    return int(bool(_CAST_WORD_RE.search(n))) + sum(1 for kw in _CAST_KEYWORDS if kw in n)


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
