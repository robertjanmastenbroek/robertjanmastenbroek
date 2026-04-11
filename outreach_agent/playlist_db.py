"""
RJM Playlist Discovery Database

Separate table in the same outreach.db — tracks Spotify playlists
through the pipeline from discovery → contact found → outreach sent.

Playlist lifecycle:
  discovered → verified (follower count checked) → contact_found → contacted → responded
"""

import sqlite3
import logging
from datetime import datetime, date
from contextlib import contextmanager
from pathlib import Path
from config import DB_PATH

log = logging.getLogger("outreach.playlist_db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS playlists (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_id          TEXT    UNIQUE NOT NULL,        -- Spotify playlist ID (from URL)
    playlist_url        TEXT    NOT NULL,               -- https://open.spotify.com/playlist/...
    name                TEXT    NOT NULL,
    curator_name        TEXT,                           -- Display name of playlist owner
    curator_spotify_url TEXT,                           -- https://open.spotify.com/user/...
    follower_count      INTEGER,                        -- Saves / followers
    track_count         INTEGER,
    genre_tags          TEXT,                           -- comma-separated: tribal,psytrance,ethnic
    relevance_score     INTEGER DEFAULT 0,              -- 1-10: how well it fits RJM's tracks
    best_track_match    TEXT,                           -- Which RJM track fits best
    status              TEXT    DEFAULT 'discovered',
    -- discovered | verified | contact_found | contacted | responded | rejected
    curator_email       TEXT,
    curator_website     TEXT,
    curator_instagram   TEXT,
    curator_contact_url TEXT,                           -- any contact URL found
    contact_notes       TEXT,                           -- how to reach them
    date_discovered     TEXT,
    date_verified       TEXT,
    date_contact_found  TEXT,
    date_contacted      TEXT,
    notes               TEXT,                           -- anything useful found during research
    source_query        TEXT                            -- which search query found this
);

CREATE INDEX IF NOT EXISTS idx_playlists_status    ON playlists(status);
CREATE INDEX IF NOT EXISTS idx_playlists_followers ON playlists(follower_count);
CREATE INDEX IF NOT EXISTS idx_playlists_spotify   ON playlists(spotify_id);

CREATE TABLE IF NOT EXISTS playlist_search_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query       TEXT    NOT NULL,
    results_found INTEGER DEFAULT 0,
    searched_at TEXT    NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_playlist_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    log.info("Playlist DB initialised")


def add_playlist(spotify_id, playlist_url, name, curator_name="",
                 curator_spotify_url="", follower_count=None, track_count=None,
                 genre_tags="", relevance_score=0, best_track_match="",
                 notes="", source_query=""):
    """Add a discovered playlist. Returns (True, id) or (False, reason)."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, status FROM playlists WHERE spotify_id = ?", (spotify_id,)
        ).fetchone()
        if existing:
            return False, f"duplicate — already in DB as status={existing['status']}"

        conn.execute("""
            INSERT INTO playlists
                (spotify_id, playlist_url, name, curator_name, curator_spotify_url,
                 follower_count, track_count, genre_tags, relevance_score,
                 best_track_match, notes, source_query, status, date_discovered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?)
        """, (spotify_id, playlist_url, name, curator_name, curator_spotify_url,
              follower_count, track_count, genre_tags, relevance_score,
              best_track_match, notes, source_query, str(date.today())))
        row = conn.execute("SELECT last_insert_rowid() as id").fetchone()
        return True, row["id"]


def update_playlist(spotify_id, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [spotify_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE playlists SET {sets} WHERE spotify_id = ?", vals)


def mark_verified(spotify_id, follower_count, track_count):
    update_playlist(spotify_id,
                    status="verified",
                    follower_count=follower_count,
                    track_count=track_count,
                    date_verified=str(date.today()))


def mark_contact_found(spotify_id, email=None, website=None,
                       instagram=None, contact_url=None, contact_notes=""):
    update_playlist(spotify_id,
                    status="contact_found",
                    curator_email=email or "",
                    curator_website=website or "",
                    curator_instagram=instagram or "",
                    curator_contact_url=contact_url or "",
                    contact_notes=contact_notes,
                    date_contact_found=str(date.today()))


def mark_contacted(spotify_id):
    update_playlist(spotify_id, status="contacted", date_contacted=str(date.today()))


def get_playlists_by_status(status, limit=50):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM playlists WHERE status = ?
            ORDER BY relevance_score DESC, follower_count ASC
            LIMIT ?
        """, (status, limit)).fetchall()
        return [dict(r) for r in rows]


def get_all_spotify_ids():
    """Return set of all known Spotify IDs — for deduplication."""
    with get_conn() as conn:
        rows = conn.execute("SELECT spotify_id FROM playlists").fetchall()
        return {r["spotify_id"] for r in rows}


def log_search(query, results_found):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO playlist_search_log (query, results_found, searched_at)
            VALUES (?, ?, ?)
        """, (query, results_found, datetime.now().isoformat()))


def recently_searched(query, within_hours=72):
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(hours=within_hours)).isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT id FROM playlist_search_log
            WHERE query = ? AND searched_at > ?
        """, (query, cutoff)).fetchone()
        return row is not None


def get_summary():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT status, COUNT(*) as n FROM playlists GROUP BY status
        """).fetchall()
        summary = {r["status"]: r["n"] for r in rows}
        total = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
        summary["_total"] = total

        # Follower distribution
        dist = conn.execute("""
            SELECT
                SUM(CASE WHEN follower_count < 1000 THEN 1 ELSE 0 END) as tiny,
                SUM(CASE WHEN follower_count BETWEEN 1000 AND 5000 THEN 1 ELSE 0 END) as small,
                SUM(CASE WHEN follower_count BETWEEN 5001 AND 20000 THEN 1 ELSE 0 END) as medium,
                SUM(CASE WHEN follower_count > 20000 THEN 1 ELSE 0 END) as large
            FROM playlists WHERE follower_count IS NOT NULL
        """).fetchone()
        summary["_size_dist"] = dict(dist)
        return summary
