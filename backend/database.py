import hashlib
import sqlite3
import json
import os
from datetime import datetime
from typing import List

DB_PATH = os.environ.get("DB_PATH", "/data/digarr.db")

SCHEMA_VERSION = 4  # Bump this when adding new migrations below


def _get_schema_version(c) -> int:
    try:
        row = c.execute("SELECT version FROM schema_version").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            id      INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            source_url TEXT,
            source_type TEXT,
            artists TEXT,
            tracks TEXT,
            artists_added TEXT,
            created_at TEXT,
            plex_playlist_id TEXT,
            plex_matched_count INTEGER,
            plex_total_count INTEGER,
            plex_unmatched_tracks TEXT,
            lidarr_results TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS artist_notes (
            artist_name TEXT PRIMARY KEY,
            notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
    """)
    # --- track_cache: owned library tracks from any media server ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS track_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            album TEXT,
            title_norm TEXT NOT NULL DEFAULT '',
            artist_norm TEXT NOT NULL DEFAULT '',
            cached_at TEXT NOT NULL,
            UNIQUE(external_id, source)
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_track_cache_search
        ON track_cache(title_norm, artist_norm)
    """)

    # --- manual_matches: user-confirmed track→library mappings ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS manual_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_norm TEXT NOT NULL,
            title_norm TEXT NOT NULL,
            external_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'plex',
            matched_at TEXT NOT NULL,
            UNIQUE(artist_norm, title_norm, source)
        )
    """)

    # --- ignored_tracks: user-dismissed unmatched tracks ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS ignored_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            artist_norm TEXT NOT NULL,
            title_norm TEXT NOT NULL,
            ignored_at TEXT NOT NULL,
            UNIQUE(artist_norm, title_norm)
        )
    """)

    # --- download_search_cache: cached artist/album lookup results ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS download_search_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            source TEXT NOT NULL,
            results_json TEXT NOT NULL DEFAULT '[]',
            cached_at TEXT NOT NULL,
            UNIQUE(query, source)
        )
    """)

    # --- download_queue_cache: snapshot of the download queue ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS download_queue_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT NOT NULL,
            source TEXT NOT NULL,
            artist TEXT NOT NULL DEFAULT '',
            album TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            progress INTEGER NOT NULL DEFAULT 0,
            cached_at TEXT NOT NULL,
            UNIQUE(external_id, source)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS oauth_states (
            state      TEXT PRIMARY KEY,
            verifier   TEXT NOT NULL DEFAULT '',
            flow       TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS import_jobs (
            id           TEXT PRIMARY KEY,
            job_json     TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            status       TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            key        TEXT PRIMARY KEY,
            value_int  INTEGER NOT NULL DEFAULT 0,
            value_text TEXT,
            updated_at TEXT NOT NULL
        )
    """)

    try:
        c.execute("ALTER TABLE sessions ADD COLUMN expires_at TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    # Migrations for older tables
    for col, typedef in [
        ("plex_playlist_id", "TEXT"),
        ("plex_matched_count", "INTEGER"),
        ("plex_total_count", "INTEGER"),
        ("plex_unmatched_tracks", "TEXT"),
        ("last_refreshed_at", "TEXT"),
        ("plex_playlist_name", "TEXT"),
        ("lidarr_results", "TEXT"),
        ("spotify_playlist_id", "TEXT"),
        ("spotify_matched_count", "INTEGER"),
        ("spotify_total_count", "INTEGER"),
        ("merge_tracks", "INTEGER"),
        ("jellyfin_playlist_id", "TEXT"),
        ("jellyfin_matched_count", "INTEGER"),
        ("jellyfin_total_count", "INTEGER"),
        ("navidrome_playlist_id", "TEXT"),
        ("navidrome_matched_count", "INTEGER"),
        ("navidrome_total_count", "INTEGER"),
        ("deemix_queued_count", "INTEGER"),
        ("deemix_total_count", "INTEGER"),
        ("last_refresh_new_artists", "TEXT"),
        ("refresh_started_at", "TEXT"),
    ]:
        try:
            c.execute(f"ALTER TABLE playlists ADD COLUMN {col} {typedef}")
        except Exception:
            pass  # Column already exists

    c.execute(
        "INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_PATH)


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

_SESSION_TTL_DAYS = 30


def _hash_token(token: str) -> str:
    """SHA-256 of the raw token. Only the hash is stored; the plaintext stays with the client."""
    return hashlib.sha256(token.encode()).hexdigest()


def db_save_session(token: str) -> None:
    from datetime import timedelta
    now = datetime.utcnow()
    expires = (now + timedelta(days=_SESSION_TTL_DAYS)).isoformat()
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
              (_hash_token(token), now.isoformat(), expires))
    conn.commit()
    conn.close()


def db_is_valid_session(token: str) -> bool:
    if not token:
        return False
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT expires_at FROM sessions WHERE token = ?", (_hash_token(token),))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    expires_at = row[0]
    if not expires_at:
        return True  # legacy rows without expiry — still valid
    return expires_at > datetime.utcnow().isoformat()


def db_revoke_session(token: str) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE token = ?", (_hash_token(token),))
    conn.commit()
    conn.close()



def db_prune_expired_sessions() -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE expires_at != '' AND expires_at <= ?",
              (datetime.utcnow().isoformat(),))
    conn.commit()
    conn.close()

def save_playlist(name, source_url, source_type, artists, tracks, artists_added, lidarr_results=None) -> int:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO playlists (name, source_url, source_type, artists, tracks, artists_added, created_at, lidarr_results)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        source_url,
        source_type,
        json.dumps(artists),
        json.dumps(tracks),
        json.dumps(artists_added),
        datetime.utcnow().isoformat(),
        json.dumps(lidarr_results or []),
    ))
    conn.commit()
    playlist_id = c.lastrowid
    conn.close()
    return playlist_id

def touch_playlist_refreshed(playlist_id: int) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE playlists SET last_refreshed_at = ? WHERE id = ?",
              (datetime.utcnow().isoformat(), playlist_id))
    conn.commit()
    conn.close()

def get_all_playlist_artist_names() -> set:
    """Return the normalized-ready set of every artist name stored in any playlist.
    Combines the full `artists` list AND `artists_added` so we catch artists that
    were already in the Lidarr library at import time (they land in `artists` but
    not in `artists_added`)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT artists, artists_added FROM playlists")
    rows = c.fetchall()
    conn.close()
    names = set()
    for artists_json, added_json in rows:
        for name in json.loads(artists_json or "[]"):
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
            elif isinstance(name, dict) and name.get("name"):
                names.add(name["name"].strip())
        for name in json.loads(added_json or "[]"):
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
    return names


def get_playlists() -> list:
    conn = get_db()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""SELECT id, name, source_url, source_type, created_at, artists_added,
                        plex_playlist_id, plex_matched_count, plex_total_count,
                        plex_unmatched_tracks, last_refreshed_at, plex_playlist_name,
                        lidarr_results, spotify_playlist_id, spotify_matched_count, spotify_total_count,
                        merge_tracks,
                        jellyfin_playlist_id, jellyfin_matched_count, jellyfin_total_count,
                        navidrome_playlist_id, navidrome_matched_count, navidrome_total_count,
                        deemix_queued_count, deemix_total_count,
                        last_refresh_new_artists
                 FROM playlists ORDER BY created_at DESC""")
    rows = c.fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["artists_added"] = json.loads(d["artists_added"] or "[]")
        d["plex_unmatched_tracks"] = json.loads(d["plex_unmatched_tracks"] or "[]")
        d["lidarr_results"] = json.loads(d["lidarr_results"] or "[]")
        d["last_refresh_new_artists"] = json.loads(d["last_refresh_new_artists"] or "null") if d.get("last_refresh_new_artists") else None
        result.append(d)
    return result


def update_playlist_plex_result(
    playlist_id: int,
    plex_playlist_id: str,
    matched_count: int,
    total_count: int,
    unmatched_tracks: list,
    plex_playlist_name: str = None,
) -> None:
    conn = get_db()
    c = conn.cursor()
    # Also record the name Plex knows this playlist by, if provided
    if plex_playlist_name is not None:
        c.execute(
            """UPDATE playlists
               SET plex_playlist_id = ?, plex_matched_count = ?, plex_total_count = ?,
                   plex_unmatched_tracks = ?, plex_playlist_name = ?
               WHERE id = ?""",
            (plex_playlist_id, matched_count, total_count, json.dumps(unmatched_tracks),
             plex_playlist_name, playlist_id),
        )
    else:
        c.execute(
            """UPDATE playlists
               SET plex_playlist_id = ?, plex_matched_count = ?, plex_total_count = ?,
                   plex_unmatched_tracks = ?
               WHERE id = ?""",
            (plex_playlist_id, matched_count, total_count, json.dumps(unmatched_tracks), playlist_id),
        )
    conn.commit()
    conn.close()

def update_playlist_spotify_result(
    playlist_id: int,
    spotify_playlist_id: str,
    matched_count: int,
    total_count: int,
) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE playlists SET spotify_playlist_id = ?, spotify_matched_count = ?, spotify_total_count = ? WHERE id = ?",
        (spotify_playlist_id, matched_count, total_count, playlist_id),
    )
    conn.commit()
    conn.close()


def update_playlist_jellyfin_result(
    playlist_id: int,
    jellyfin_playlist_id: str,
    matched_count: int,
    total_count: int,
) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE playlists SET jellyfin_playlist_id = ?, jellyfin_matched_count = ?, jellyfin_total_count = ? WHERE id = ?",
        (jellyfin_playlist_id, matched_count, total_count, playlist_id),
    )
    conn.commit()
    conn.close()


def update_playlist_navidrome_result(
    playlist_id: int,
    navidrome_playlist_id: str,
    matched_count: int,
    total_count: int,
) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE playlists SET navidrome_playlist_id = ?, navidrome_matched_count = ?, navidrome_total_count = ? WHERE id = ?",
        (navidrome_playlist_id, matched_count, total_count, playlist_id),
    )
    conn.commit()
    conn.close()


def update_playlist_last_refresh_artists(playlist_id: int, new_artists: list) -> None:
    conn = get_db()
    conn.execute("UPDATE playlists SET last_refresh_new_artists = ? WHERE id = ?",
                 (json.dumps(new_artists), playlist_id))
    conn.commit()
    conn.close()


def try_claim_refresh(playlist_id: int, timeout_seconds: int = 600) -> bool:
    """Atomically claim a refresh slot. Returns True if claimed, False if already running."""
    from datetime import datetime, timezone, timedelta
    conn = get_db()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=timeout_seconds)).isoformat()
    cur = conn.execute(
        "UPDATE playlists SET refresh_started_at = ? WHERE id = ? AND (refresh_started_at IS NULL OR refresh_started_at < ?)",
        (now.isoformat(), playlist_id, cutoff),
    )
    claimed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return claimed


def clear_refresh_lock(playlist_id: int) -> None:
    conn = get_db()
    conn.execute("UPDATE playlists SET refresh_started_at = NULL WHERE id = ?", (playlist_id,))
    conn.commit()
    conn.close()


def update_playlist_import_results(playlist_id: int, artists_added: list, lidarr_results: list) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE playlists SET artists_added = ?, lidarr_results = ? WHERE id = ?",
        (json.dumps(artists_added), json.dumps(lidarr_results), playlist_id),
    )
    conn.commit()
    conn.close()


def update_playlist(playlist_id: int, artists: list, tracks: list, artists_added: list) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """UPDATE playlists SET artists = ?, tracks = ?, artists_added = ? WHERE id = ?""",
        (json.dumps(artists), json.dumps(tracks), json.dumps(artists_added), playlist_id),
    )
    conn.commit()
    conn.close()

def update_playlist_tracks(playlist_id: int, tracks: list) -> None:
    conn = get_db()
    conn.execute("UPDATE playlists SET tracks = ? WHERE id = ?",
                 (json.dumps(tracks), playlist_id))
    conn.commit()
    conn.close()

def update_plex_unmatched_tracks(playlist_id: int, unmatched: list) -> None:
    conn = get_db()
    conn.execute("UPDATE playlists SET plex_unmatched_tracks = ? WHERE id = ?",
                 (json.dumps(unmatched), playlist_id))
    conn.commit()
    conn.close()

def set_playlist_merge_tracks(playlist_id: int, value) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE playlists SET merge_tracks = ? WHERE id = ?", (value, playlist_id))
    conn.commit()
    conn.close()

def rename_playlist(playlist_id: int, new_name: str, plex_playlist_name: str = None) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE playlists SET name = ?, plex_playlist_name = ? WHERE id = ?",
        (new_name, plex_playlist_name, playlist_id),
    )
    updated = c.rowcount > 0
    conn.commit()
    conn.close()
    return updated

def delete_playlist(playlist_id: int) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def get_playlist(playlist_id: int) -> dict:
    conn = get_db()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["artists"] = json.loads(d["artists"] or "[]")
    d["tracks"] = json.loads(d["tracks"] or "[]")
    d["artists_added"] = json.loads(d["artists_added"] or "[]")
    d["plex_unmatched_tracks"] = json.loads(d["plex_unmatched_tracks"] or "[]")
    d["lidarr_results"] = json.loads(d["lidarr_results"] or "[]")
    return d


# ---------------------------------------------------------------------------
# track_cache helpers
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Inline normalize to avoid circular import with utils.py."""
    import re
    s = s.lower().strip()
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def db_upsert_track_cache(source: str, tracks: List[dict]) -> None:
    """Replace all cached tracks for *source* with the provided list.

    Each track dict must contain: external_id, title, artist, album.
    """
    now = datetime.utcnow().isoformat()
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM track_cache WHERE source = ?", (source,))
    c.executemany(
        """INSERT INTO track_cache
               (external_id, source, title, artist, album, title_norm, artist_norm, cached_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                t["external_id"],
                source,
                t["title"],
                t["artist"],
                t.get("album") or "",
                _norm(t["title"]),
                _norm(t["artist"]),
                now,
            )
            for t in tracks
        ],
    )
    conn.commit()
    conn.close()


def db_search_track_cache(query: str, source: str = None, limit: int = 20) -> List[dict]:
    """Search cached tracks by free-text query (matched against title_norm / artist_norm)."""
    norm_q = _norm(query)
    like = f"%{norm_q}%"
    conn = get_db()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if source:
        c.execute(
            """SELECT external_id, source, title, artist, album FROM track_cache
               WHERE source = ? AND (title_norm LIKE ? OR artist_norm LIKE ?)
               LIMIT ?""",
            (source, like, like, limit),
        )
    else:
        c.execute(
            """SELECT external_id, source, title, artist, album FROM track_cache
               WHERE title_norm LIKE ? OR artist_norm LIKE ?
               LIMIT ?""",
            (like, like, limit),
        )
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def db_lookup_track_cache(artist: str, title: str, source: str) -> str:
    """Exact-match lookup for a single track. Returns external_id or None.

    Used by the sync path to avoid live API calls when the cache is warm.
    Matching uses the same normalisation as the fuzzy search so results are
    consistent with PlexClient.search_track().
    """
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """SELECT external_id FROM track_cache
           WHERE source = ? AND artist_norm = ? AND title_norm = ?
           LIMIT 1""",
        (source, _norm(artist), _norm(title)),
    )
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def db_get_cache_stats(source: str = None) -> dict:
    conn = get_db()
    c = conn.cursor()
    if source:
        c.execute(
            "SELECT COUNT(*), MAX(cached_at) FROM track_cache WHERE source = ?",
            (source,),
        )
    else:
        c.execute("SELECT COUNT(*), MAX(cached_at) FROM track_cache")
    count, last = c.fetchone()
    conn.close()
    return {"track_count": count or 0, "cached_at": last}


# ---------------------------------------------------------------------------
# manual_matches helpers
# ---------------------------------------------------------------------------

def db_set_manual_match(artist: str, title: str, external_id: str, source: str) -> None:
    """Upsert a user-confirmed match for (artist, title) → external_id on *source*."""
    now = datetime.utcnow().isoformat()
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """INSERT INTO manual_matches (artist_norm, title_norm, external_id, source, matched_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(artist_norm, title_norm, source) DO UPDATE SET
               external_id = excluded.external_id,
               matched_at  = excluded.matched_at""",
        (_norm(artist), _norm(title), external_id, source, now),
    )
    conn.commit()
    conn.close()


def db_get_manual_matches(artist_title_pairs: List[tuple], source: str) -> dict:
    """Return {(artist_norm, title_norm): external_id} for any known manual matches."""
    if not artist_title_pairs:
        return {}
    conn = get_db()
    c = conn.cursor()
    results = {}
    for artist, title in artist_title_pairs:
        an, tn = _norm(artist), _norm(title)
        c.execute(
            "SELECT external_id FROM manual_matches WHERE artist_norm=? AND title_norm=? AND source=?",
            (an, tn, source),
        )
        row = c.fetchone()
        if row:
            results[(an, tn)] = row[0]
    conn.close()
    return results


# ---------------------------------------------------------------------------
# ignored_tracks helpers
# ---------------------------------------------------------------------------

def db_ignore_track(artist: str, title: str) -> None:
    now = datetime.utcnow().isoformat()
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """INSERT INTO ignored_tracks (artist, title, artist_norm, title_norm, ignored_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(artist_norm, title_norm) DO NOTHING""",
        (artist, title, _norm(artist), _norm(title), now),
    )
    conn.commit()
    conn.close()


def db_unignore_track(artist: str, title: str) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "DELETE FROM ignored_tracks WHERE artist_norm = ? AND title_norm = ?",
        (_norm(artist), _norm(title)),
    )
    conn.commit()
    conn.close()


def db_get_ignored_tracks() -> List[dict]:
    conn = get_db()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT artist, title, artist_norm, title_norm FROM ignored_tracks ORDER BY ignored_at DESC")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# download_search_cache helpers
# ---------------------------------------------------------------------------

def db_upsert_download_search_cache(query: str, source: str, results: list) -> None:
    now = datetime.utcnow().isoformat()
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """INSERT INTO download_search_cache (query, source, results_json, cached_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(query, source) DO UPDATE SET
               results_json = excluded.results_json,
               cached_at    = excluded.cached_at""",
        (query.lower().strip(), source, json.dumps(results), now),
    )
    conn.commit()
    conn.close()


def db_get_download_search_cache(query: str, source: str, max_age_seconds: int = 3600) -> list:
    conn = get_db()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT results_json, cached_at FROM download_search_cache WHERE query=? AND source=?",
        (query.lower().strip(), source),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    from datetime import timezone
    cached_at = row["cached_at"]
    age = (datetime.utcnow() - datetime.fromisoformat(cached_at)).total_seconds()
    if age > max_age_seconds:
        return None
    return json.loads(row["results_json"])


# ---------------------------------------------------------------------------
# download_queue_cache helpers
# ---------------------------------------------------------------------------

def db_upsert_download_queue_cache(source: str, items: list) -> None:
    """Replace the cached queue snapshot for *source*."""
    now = datetime.utcnow().isoformat()
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM download_queue_cache WHERE source = ?", (source,))
    c.executemany(
        """INSERT INTO download_queue_cache
               (external_id, source, artist, album, status, progress, cached_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                item.get("id", ""),
                source,
                item.get("artist", ""),
                item.get("album", ""),
                item.get("status", ""),
                item.get("progress", 0),
                now,
            )
            for item in items
        ],
    )
    conn.commit()
    conn.close()


def db_get_download_queue_cache(source: str) -> list:
    conn = get_db()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT external_id, artist, album, status, progress, cached_at FROM download_queue_cache WHERE source=?",
        (source,),
    )
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# OAuth state persistence (survives container restarts; 10-minute TTL)
# ---------------------------------------------------------------------------

_OAUTH_STATE_TTL_SECONDS = 600


def save_oauth_state(state: str, flow: str, verifier: str = "") -> None:
    from datetime import timezone as _tz
    expires = (datetime.now(_tz.utc).replace(tzinfo=None) +
               __import__('datetime').timedelta(seconds=_OAUTH_STATE_TTL_SECONDS)).isoformat()
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO oauth_states (state, verifier, flow, expires_at) VALUES (?, ?, ?, ?)",
        (state, verifier, flow, expires),
    )
    conn.commit()
    conn.close()


def consume_oauth_state(state: str, flow: str) -> str | None:
    """Look up and delete the state token. Returns verifier (may be '') or None if missing/expired."""
    from datetime import timezone as _tz
    now = datetime.now(_tz.utc).replace(tzinfo=None).isoformat()
    conn = get_db()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT verifier, expires_at FROM oauth_states WHERE state=? AND flow=?",
        (state, flow),
    ).fetchone()
    conn.execute("DELETE FROM oauth_states WHERE state=?", (state,))
    # Also purge any expired states while we're here
    conn.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now,))
    conn.commit()
    conn.close()
    if not row:
        return None
    if row["expires_at"] < now:
        return None
    return row["verifier"]


# ---------------------------------------------------------------------------
# Import job persistence
# ---------------------------------------------------------------------------

_MAX_PERSISTED_JOBS = 30


def db_save_import_job(job: dict) -> None:
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO import_jobs (id, job_json, created_at, status)
           VALUES (?, ?, ?, ?)""",
        (job["id"], json.dumps(job), job["created_at"], job["status"]),
    )
    conn.commit()
    conn.close()


def db_load_recent_import_jobs() -> list[dict]:
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT job_json FROM import_jobs ORDER BY created_at DESC LIMIT ?",
        (_MAX_PERSISTED_JOBS,),
    ).fetchall()
    conn.close()
    return [json.loads(row["job_json"]) for row in rows]


def db_delete_import_job(job_id: str) -> None:
    conn = get_db()
    conn.execute("DELETE FROM import_jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()


def db_delete_import_jobs_for_playlist(playlist_id: int) -> None:
    conn = get_db()
    # import_jobs stores JSON blobs; filter by playlist_id stored in the JSON
    rows = conn.execute("SELECT id, job_json FROM import_jobs").fetchall()
    to_delete = [
        row[0] for row in rows
        if json.loads(row[1]).get("playlist_id") == playlist_id
    ]
    if to_delete:
        placeholders = ",".join("?" * len(to_delete))
        conn.execute(f"DELETE FROM import_jobs WHERE id IN ({placeholders})", to_delete)
        conn.commit()
    conn.close()


def update_playlist_deemix_result(
    playlist_id: int,
    queued_count: int,
    total_count: int,
) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE playlists SET deemix_queued_count = ?, deemix_total_count = ? WHERE id = ?",
        (queued_count, total_count, playlist_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Stats — hidden key/value counters for future feature use
# ---------------------------------------------------------------------------

def db_increment_stat(key: str, amount: int = 1) -> None:
    """Atomically increment an integer stat counter. Creates the key if it doesn't exist."""
    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        """INSERT INTO stats (key, value_int, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET
               value_int  = value_int + excluded.value_int,
               updated_at = excluded.updated_at""",
        (key, amount, now),
    )
    conn.commit()
    conn.close()


def db_set_stat_text(key: str, value: str) -> None:
    """Set or update a text stat (e.g. a timestamp or label)."""
    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        """INSERT INTO stats (key, value_int, value_text, updated_at)
           VALUES (?, 0, ?, ?)
           ON CONFLICT(key) DO UPDATE SET
               value_text = excluded.value_text,
               updated_at = excluded.updated_at""",
        (key, value, now),
    )
    conn.commit()
    conn.close()


def db_set_stat_text_if_unset(key: str, value: str) -> None:
    """Set a text stat only if it hasn't been set before (e.g. first_import_at)."""
    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO stats (key, value_int, value_text, updated_at)
           VALUES (?, 0, ?, ?)""",
        (key, value, now),
    )
    conn.commit()
    conn.close()


def db_get_all_stats() -> dict:
    """Return all stats as a flat dict: {key: int or text}."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT key, value_int, value_text FROM stats").fetchall()
    conn.close()
    result = {}
    for row in rows:
        result[row["key"]] = row["value_text"] if row["value_text"] is not None else row["value_int"]
    return result


def db_prune_import_jobs() -> None:
    conn = get_db()
    keep_ids = conn.execute(
        "SELECT id FROM import_jobs ORDER BY created_at DESC LIMIT ?",
        (_MAX_PERSISTED_JOBS,),
    ).fetchall()
    if keep_ids:
        placeholders = ",".join("?" * len(keep_ids))
        conn.execute(
            f"DELETE FROM import_jobs WHERE id NOT IN ({placeholders})",
            [row[0] for row in keep_ids],
        )
    conn.commit()
    conn.close()

