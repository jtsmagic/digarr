import re
import unicodedata

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
    """Lowercase, fold accents, strip leading 'the ', remove punctuation.

    Accent folding matters because the same artist is spelled inconsistently across
    sources - a playlist may say "Beyonce" where the library holds "Beyonce" with an
    acute accent. Python's \\w is Unicode-aware, so stripping punctuation alone leaves
    accented characters intact and the two spellings compare as different artists.

    '&' and '$' are mapped to word/letter equivalents rather than deleted, so
    "Hall & Oates" matches "Hall and Oates" and "A$AP" matches "ASAP".
    """
    s = (s or "").lower().strip()
    s = s.replace("&", " and ").replace("$", "s")
    # NFKD splits an accented character into base letter + combining mark; dropping
    # the combining marks leaves the plain base letter behind.
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
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
