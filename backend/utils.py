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


# xmplaylist tags the titles on some channels with the year the record charted
# or the production opened - "Little Red Corvette (83)" on 80s on 8, "La Vie
# Boheme (1996)" on On Broadway. Both the two- and four-digit forms show up.
_YEAR_SUFFIX_RE = re.compile(r"\s*\((?:19|20)?\d{2}\)\s*$")


def strip_year_suffix(title: str) -> str:
    """Drop a trailing year tag like "(84)" or "(1996)" from a track title."""
    return _YEAR_SUFFIX_RE.sub("", title or "").strip()


# A trailing bare "v"/"p" is a performer-credit marker left over from how some
# sources write 1940s big-band billing - "Glenn Miller v Ray Eberle" is vocal by,
# "Freddy Martin p Jack Fina" is piano by - not part of the artist's name.
_CREDIT_SUFFIX_RE = re.compile(r"\s+[vp]\.?$", re.I)


def strip_credit_suffix(artist: str) -> str:
    """Drop a dangling "v"/"p" performer-credit marker from an artist name."""
    return _CREDIT_SUFFIX_RE.sub("", artist or "").strip()


def normalize_extraction(data: dict) -> dict:
    """Clean performer-credit artefacts out of an AI extraction result.

    A record billed as "Bandleader v Vocalist" reads as two performers, and the
    extraction then emits the same title once per name - so the playlist repeats the
    song back to back, and the second entry, having an artist the library never
    matches, falls through to a title-only match on an unrelated recording.

    The prompt asks for one track per airing, but the model only mostly complies -
    measured across runs it still split some - so the guarantee lives here instead.

    Consecutive runs of a title collapse to their first entry. Consecutive only, and
    deliberately so: a station really can play a song twice in an hour, but those
    plays have other tracks between them. Two in a row is a split credit or a
    double-logged play, and either way belongs in the playlist once.
    """
    artists, seen = [], set()
    for a in data.get("artists") or []:
        name = strip_credit_suffix(a.get("name", "")) if isinstance(a, dict) else strip_credit_suffix(a)
        if not name:
            continue
        key = normalize(name)
        if key in seen:
            continue
        seen.add(key)
        artists.append({**a, "name": name} if isinstance(a, dict) else name)

    tracks = []
    for t in data.get("tracks") or []:
        if not isinstance(t, dict):
            continue
        title = (t.get("title") or "").strip()
        if title and tracks and normalize(title) == normalize(tracks[-1].get("title") or ""):
            continue
        tracks.append({**t, "artist": strip_credit_suffix(t.get("artist", "")), "title": title})

    return {**data, "artists": artists, "tracks": tracks}


def artists_from_tracks(tracks: list) -> list:
    """Unique artist dicts from track rows, secondary credits included.

    A track row names a single primary artist so a playlist gets one entry per
    airing, but everyone credited on the record still belongs in the library - so
    the extras ride along on the row and are collected here rather than by giving
    each performer a row of their own.
    """
    out = {}
    for t in tracks or []:
        names = [t.get("artist")] + list(t.get("extra_artists") or [])
        for name in names:
            name = (name or "").strip()
            if name and name not in out:
                out[name] = {"name": name}
    return list(out.values())


_LIDARR_COMMON = re.compile(r"(?<!^)\b(?:a|an|the|and|or|of)\b\s*(?!$)", re.I)
# .NET's \w is Unicode letters, decimal digits, underscore and connector
# punctuation. Python's \w is wider - it also accepts subscript and other
# non-decimal numerals - so the categories are spelled out rather than relying
# on the shorthand.
_WORD_CATEGORIES = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo", "Nd", "Pc"})


def lidarr_clean_name(name: str) -> str:
    """Reproduce Lidarr's Artists.CleanName for a display name.

    Lidarr resolves a finished download by artist *name*, and the string it
    compares is CleanName - not the display name, and not normalize() above. Two
    artist records whose CleanName collides make it raise
    MultipleArtistsFoundException, which wedges ProcessMonitoredDownloads and
    every disk command queued behind it, so a duplicate check that wants to
    prevent that has to predict Lidarr's normalisation rather than its own.

    The rule, derived by comparing against the CleanName Lidarr itself stored for
    every artist in a production library: fold accents, drop the common words
    a|an|the|and|or|of but only where they are interior - a leading or trailing
    one is kept, so "A Tribe Called Quest" -> atribecalledquest and "Empress Of"
    -> empressof - then remove every character .NET's \\w rejects and lowercase.

    Two names in that library disagree, both degenerate: "H2O" spelled with a
    subscript, and a name consisting only of punctuation and digits.
    """
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _LIDARR_COMMON.sub("", s)
    return "".join(c for c in s if unicodedata.category(c) in _WORD_CATEGORIES).lower()


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
