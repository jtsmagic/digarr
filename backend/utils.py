import re
import difflib
import unicodedata
from typing import Optional

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


# Words that legitimately pad a title into a credit: "Original Broadway Cast of
# Hamilton", "The Color Purple Ensemble", "Finian's Rainbow Chorus". Anything a
# candidate adds beyond the query must come from this set, otherwise
# "Beggar In The Heights" would qualify for "In The Heights".
_CREDIT_WORDS = frozenset({
    "cast", "ensemble", "chorus", "company", "orchestra", "original", "broadway",
    "revival", "soundtrack", "musical", "theatre", "theater", "west", "end", "of",
    "the", "a", "recording", "players", "singers", "nl", "uk", "us", "tour",
})

# Separators that mark a list of collaborators. Deliberately excludes & + and /
# because those overwhelmingly appear *inside* single artist names -- this
# library has 780 such artists (Bob Marley & The Wailers, AC/DC, Dan + Shay)
# versus 147 containing a comma. Splitting on & destroys far more than it fixes.
_CREDIT_SEP = re.compile(r"\s*(?:,| feat\.? | featuring | with )\s*", re.I)
# "Harry Connick, Jr." and "10,000 Maniacs" are single names, not lists.
_NAME_SUFFIX = re.compile(r",\s*(jr|sr|ii|iii|iv)\.?\s*$", re.I)
_NUMERIC_COMMA = re.compile(r"\d,\d")


def acceptable_inexact(query: str, candidate: str) -> Optional[str]:
    """Return why candidate is an acceptable non-exact match for query, or None.

    Two shapes, both measured against every inexact match digarr has ever made:

    credit-expansion - the candidate is the query plus credit vocabulary, e.g.
        'Original Broadway Cast of Dreamgirls' for 'Dreamgirls'.

    near-match - near-identical strings: playlist typos ('Barbra Steisand'),
        punctuation and spacing variants ('One Republic' / 'OneRepublic',
        'half alive' / 'half-alive'), and trailing performance-credit letters
        from radio metadata ('Abe Lyman v'). The 8-character floor matters:
        below it, unrelated short names sit within one edit of each other.
    """
    nq, nc = normalize(query), normalize(candidate)
    if not nq or not nc:
        return None
    if nq in nc:
        extra = [w for w in nc.replace(nq, " ").split() if w]
        if extra and all(w in _CREDIT_WORDS for w in extra):
            return "credit-expansion"
    if len(nq) >= 8 and difflib.SequenceMatcher(None, nq, nc).ratio() >= 0.90:
        return "near-match"
    return None


def primary_credit(name: str) -> Optional[str]:
    """First credited artist from a collaboration string, or None if not a list.

    'Bazzi, Camila Cabello' -> 'Bazzi'.  Only ever used as a retry *after* a
    lookup of the whole string has failed, so single artists whose names contain
    a comma are never reached -- of the 43 such names these playlists request,
    none has ever failed lookup.
    """
    if not name or _NAME_SUFFIX.search(name) or _NUMERIC_COMMA.search(name):
        return None
    if not _CREDIT_SEP.search(name):
        return None
    first = _CREDIT_SEP.split(name)[0].strip()
    return first if first and normalize(first) else None
