"""
RJM Outreach Agent — SQLite Database Layer

Contact lifecycle:
  new → verified → queued → sent → followup_sent → [responded | lost]
                                ↘ bounced (pre-check failed)
"""

import sqlite3
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from contextlib import contextmanager
from config import DB_PATH

log = logging.getLogger("outreach.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    email                   TEXT    UNIQUE NOT NULL,
    name                    TEXT    NOT NULL,
    type                    TEXT    NOT NULL,   -- label|curator|youtube|festival|podcast
    genre                   TEXT,
    notes                   TEXT,
    status                  TEXT    DEFAULT 'new',
    -- new | verified | warm_up | queued | sent | followup_sent | responded | bounced | skip | invalid
    -- warm_up: agent_discovered contacts that passed MX check but are throttled to WARM_UP_DAILY_CAP/day
    bounce                  TEXT    DEFAULT 'no',  -- no | pre-check | actual
    date_added              TEXT,
    date_verified           TEXT,
    date_queued             TEXT,
    date_sent               TEXT,
    gmail_message_id        TEXT,
    gmail_thread_id         TEXT,
    sent_subject            TEXT,
    sent_body_snippet       TEXT,   -- first 300 chars of sent body
    template_type           TEXT,
    date_followup_sent      TEXT,
    followup_message_id     TEXT,
    date_response_received  TEXT,
    response_snippet        TEXT,   -- first 300 chars of reply
    send_attempts           INTEGER DEFAULT 0,
    source                  TEXT DEFAULT 'manual',  -- manual | csv_import | agent_discovered
    research_notes          TEXT,   -- fetched facts about the recipient used to personalise email
    research_done           INTEGER DEFAULT 0,  -- 0=not researched, 1=done
    website                 TEXT,   -- homepage URL for research
    playlist_size           TEXT    -- 'small' | 'medium' | 'large' | NULL (curators only)
);

CREATE TABLE IF NOT EXISTS discovery_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    search_query    TEXT    NOT NULL,
    contact_type    TEXT    NOT NULL,
    results_found   INTEGER DEFAULT 0,
    searched_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS email_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_email   TEXT    NOT NULL,
    direction       TEXT    NOT NULL,   -- sent | received
    email_type      TEXT    NOT NULL,   -- initial | followup | reply
    subject         TEXT,
    body_snippet    TEXT,
    gmail_message_id TEXT,
    gmail_thread_id  TEXT,
    timestamp       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date                TEXT    PRIMARY KEY,
    emails_sent         INTEGER DEFAULT 0,
    emails_bounced      INTEGER DEFAULT 0,
    replies_received    INTEGER DEFAULT 0,
    last_send_ts        TEXT,
    content_posts_today INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS template_performance (
    template_type   TEXT    NOT NULL,
    contact_type    TEXT    NOT NULL,
    total_sent      INTEGER DEFAULT 0,
    total_replies   INTEGER DEFAULT 0,
    last_reply_ts   TEXT,
    PRIMARY KEY (template_type, contact_type)
);

CREATE TABLE IF NOT EXISTS learning_insights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at    TEXT    NOT NULL,
    insight_type    TEXT    NOT NULL,   -- pattern | recommendation | subject_line_winner
    content         TEXT    NOT NULL,
    based_on_n      INTEGER             -- how many replies this was derived from
);

CREATE TABLE IF NOT EXISTS dead_domains (
    domain      TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL,
    reason      TEXT
);

CREATE TABLE IF NOT EXISTS dead_addresses (
    email       TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL,
    reason      TEXT
);

CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(status);
CREATE INDEX IF NOT EXISTS idx_contacts_type   ON contacts(type);
CREATE INDEX IF NOT EXISTS idx_contacts_date_sent ON contacts(date_sent);
CREATE INDEX IF NOT EXISTS idx_email_log_ts    ON email_log(timestamp);

CREATE TABLE IF NOT EXISTS instagram_outreach (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    instagram_handle TEXT    NOT NULL,
    playlist_name    TEXT,
    playlist_id      TEXT,
    dm_text          TEXT,
    status           TEXT    DEFAULT 'pending',
    date_sent        TEXT,
    date_replied     TEXT,
    reply_snippet    TEXT,
    error_msg        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ig_handle
    ON instagram_outreach(instagram_handle);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    payload     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    consumed_by TEXT    DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

CREATE TABLE IF NOT EXISTS fleet_state (
    agent_name      TEXT    PRIMARY KEY,
    last_heartbeat  TEXT    NOT NULL,
    status          TEXT    DEFAULT 'ok',
    last_result     TEXT    DEFAULT NULL,
    run_count       INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS content_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    posted_at       TEXT    NOT NULL,
    platform        TEXT    NOT NULL,    -- tiktok | instagram | instagram_story | youtube
    format          TEXT    NOT NULL,    -- reels | short | story
    track           TEXT,
    angle           TEXT,                -- contrast | body-drop | identity
    hook            TEXT,                -- full hook text as displayed
    hook_mechanism  TEXT,                -- tension | identity | scene | claim | rupture | dare | question
    buffer_id       TEXT,                -- Buffer's internal post id
    filename        TEXT,                -- path to produced clip
    bpm             REAL,                -- detected BPM of source track
    bar_duration    REAL,                -- seconds per 4/4 bar
    clip_length     INTEGER,             -- 7 | 15 | 28
    segment_count   INTEGER,             -- number of beat-synced segments
    source_videos   TEXT,                -- JSON array of {path, category, start_s}
    lead_category   TEXT,                -- perf | broll | phone — which category led the clip
    cloudinary_url  TEXT,                -- public video URL used for Buffer upload
    scheduled_at    TEXT,                -- ISO UTC time Buffer was asked to post
    tiktok_caption  TEXT,
    instagram_caption TEXT,
    youtube_title   TEXT,
    youtube_desc    TEXT,
    exploration     INTEGER DEFAULT 0,   -- 1 if this post was an explore (random) pick, 0 if exploit (best arm)
    batch_id        TEXT                 -- groups the 3 clips from one run together
);
CREATE INDEX IF NOT EXISTS idx_content_log_date ON content_log(posted_at);

CREATE TABLE IF NOT EXISTS content_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    buffer_id       TEXT    NOT NULL,    -- joins to content_log.buffer_id
    platform        TEXT    NOT NULL,
    fetched_at      TEXT    NOT NULL,
    platform_post_id TEXT,               -- platform-native post id (IG media id, YT video id)
    views           INTEGER,
    likes           INTEGER,
    comments        INTEGER,
    shares          INTEGER,
    saves           INTEGER,
    reach           INTEGER,
    completion_rate REAL,                -- 0.0 to 1.0
    avg_watch_s     REAL,                -- seconds
    follows_from    INTEGER,             -- follows attributed to this post (if exposed)
    raw             TEXT                 -- full JSON of API response for later re-parsing
);
CREATE INDEX IF NOT EXISTS idx_metrics_buffer_id ON content_metrics(buffer_id);
CREATE INDEX IF NOT EXISTS idx_metrics_fetched ON content_metrics(fetched_at);
CREATE INDEX IF NOT EXISTS idx_metrics_platform ON content_metrics(platform);

CREATE TABLE IF NOT EXISTS content_weights_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_at     TEXT    NOT NULL,
    window_days     INTEGER NOT NULL,
    sample_size     INTEGER NOT NULL,
    weights_json    TEXT    NOT NULL,   -- full PromptWeights as JSON
    exploration_eps REAL    NOT NULL,   -- epsilon used when these weights were computed
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_weights_computed ON content_weights_history(computed_at);

CREATE TABLE IF NOT EXISTS release_calendar (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_name      TEXT    NOT NULL,
    release_date    TEXT    NOT NULL,
    platforms       TEXT    DEFAULT 'spotify,tiktok,instagram',
    campaign_fired  INTEGER DEFAULT 0,
    fired_at        TEXT    DEFAULT NULL,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS form_submissions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id     TEXT    NOT NULL,
    playlist_name   TEXT,
    form_url        TEXT    NOT NULL,
    form_type       TEXT,
    status          TEXT    DEFAULT 'pending',
    date_submitted  TEXT,
    error_msg       TEXT,
    fields_filled   INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_form_playlist
    ON form_submissions(playlist_id, form_url);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # daily_stats migrations
        for col, definition in [
            ("content_posts_today",   "INTEGER DEFAULT 0"),
            ("contacts_found_today",  "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE daily_stats ADD COLUMN {col} {definition}")
                log.info("Migrated daily_stats: added %s column", col)
            except Exception:
                pass  # column already exists

        for col, definition in [
            ("playlist_size",          "TEXT DEFAULT NULL"),
            ("date_followup2_sent",    "TEXT DEFAULT NULL"),
            ("sent_body",              "TEXT DEFAULT NULL"),  # full sent body (no truncation)
            ("followup_body",          "TEXT DEFAULT NULL"),  # full follow-up body
            ("reply_message_id",       "TEXT DEFAULT NULL"),  # Gmail message ID of the reply
            ("reply_intent",           "TEXT DEFAULT NULL"),  # classified intent
            ("reply_action",           "TEXT DEFAULT NULL"),  # suggested action from classifier
            ("reply_classified_at",    "TEXT DEFAULT NULL"),  # when classification ran
        ]:
            try:
                conn.execute(f"ALTER TABLE contacts ADD COLUMN {col} {definition}")
                log.info("Migrated: added %s column", col)
            except Exception:
                pass  # column already exists

        # content_log migrations — learning loop fields
        for col, definition in [
            ("hook_mechanism",    "TEXT"),
            ("bpm",               "REAL"),
            ("bar_duration",      "REAL"),
            ("clip_length",       "INTEGER"),
            ("segment_count",     "INTEGER"),
            ("source_videos",     "TEXT"),
            ("lead_category",     "TEXT"),
            ("cloudinary_url",    "TEXT"),
            ("scheduled_at",      "TEXT"),
            ("tiktok_caption",    "TEXT"),
            ("instagram_caption", "TEXT"),
            ("youtube_title",     "TEXT"),
            ("youtube_desc",      "TEXT"),
            ("exploration",       "INTEGER DEFAULT 0"),
            ("batch_id",          "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE content_log ADD COLUMN {col} {definition}")
                log.info("Migrated content_log: added %s column", col)
            except Exception:
                pass  # column already exists

        # Indexes that depend on migrated columns — safe to create now
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_content_log_buffer_id ON content_log(buffer_id)",
            "CREATE INDEX IF NOT EXISTS idx_content_log_batch ON content_log(batch_id)",
        ]:
            try:
                conn.execute(idx_sql)
            except Exception as _exc:
                log.debug("Index create skipped: %s", _exc)
    log.info("Database initialised at %s", DB_PATH)


def get_verified_by_playlist_size(size: str, limit: int = 10) -> list[dict]:
    """Return verified contacts tagged with a specific playlist size."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM contacts
            WHERE status = 'verified'
              AND playlist_size = ?
            ORDER BY date_added ASC
            LIMIT ?
        """, (size, limit)).fetchall()
        return [dict(r) for r in rows]


# ─── Contact CRUD ─────────────────────────────────────────────────────────────

def add_contact(email, name, ctype, genre="", notes="", source="manual"):
    """Insert a new contact. Returns (True, id) or (False, reason_str)."""
    email = email.strip().lower()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, status FROM contacts WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            return False, f"duplicate — already in DB as status={existing['status']}"

        # Org-level duplicate: same custom domain already contacted
        domain = email.split("@")[-1]
        shared = {
            "gmail.com","googlemail.com","hotmail.com","outlook.com","live.com",
            "yahoo.com","mail.com","gmx.com","gmx.ch","gmx.de","icloud.com",
            "protonmail.com","aol.com","zoho.com","msn.com",
        }
        if domain not in shared:
            conflict = conn.execute(
                "SELECT email FROM contacts WHERE email LIKE ? AND status IN ('sent','followup_sent','responded')",
                (f"%@{domain}",)
            ).fetchone()
            if conflict:
                return False, f"org duplicate — already contacted {conflict['email']}"

        conn.execute("""
            INSERT INTO contacts (email, name, type, genre, notes, status, date_added, source)
            VALUES (?, ?, ?, ?, ?, 'new', ?, ?)
        """, (email, name, ctype, genre, notes, str(date.today()), source))
        row = conn.execute("SELECT last_insert_rowid() as id").fetchone()
        return True, row["id"]


def get_contacts_by_status(status, limit=None):
    with get_conn() as conn:
        q = "SELECT * FROM contacts WHERE status = ? ORDER BY date_added ASC"
        args = (status,)
        if limit:
            q += " LIMIT ?"
            args += (limit,)
        return [dict(r) for r in conn.execute(q, args).fetchall()]


def get_verified_contacts_prioritized(limit: int) -> list[dict]:
    """
    Return verified contacts ordered so researched ones go first.
    research_done=1 contacts lead — they get personalised emails and higher reply rates.
    research_done=0/NULL fill remaining slots so the queue never stalls.
    Within each tier, oldest-added goes first (FIFO).
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM contacts
            WHERE status = 'verified'
            ORDER BY COALESCE(research_done, 0) DESC, date_added ASC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_contact(email):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE email = ?", (email.lower(),)).fetchone()
        return dict(row) if row else None


def update_contact(email, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [email.lower()]
    with get_conn() as conn:
        conn.execute(f"UPDATE contacts SET {sets} WHERE email = ?", vals)


def mark_verified(email):
    update_contact(email, status="verified", date_verified=str(date.today()))


def mark_warm_up(email):
    """Mark as warm_up — agent_discovered contacts that passed verification but
    are throttled to WARM_UP_DAILY_CAP sends/day to protect sender reputation."""
    update_contact(email, status="warm_up", date_verified=str(date.today()))


def get_warm_up_contacts(limit: int) -> list[dict]:
    """Return warm_up contacts prioritised by research_done, FIFO within tier."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM contacts
            WHERE status = 'warm_up'
            ORDER BY COALESCE(research_done, 0) DESC, date_added ASC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_warm_up_sent_today() -> int:
    """Count of agent_discovered contacts already sent to today (for cap enforcement)."""
    today = str(date.today())
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as cnt FROM contacts
            WHERE source = 'agent_discovered'
              AND status IN ('sent', 'followup_sent', 'responded', 'bounced', 'skip')
              AND date_sent = ?
        """, (today,)).fetchone()
        return row["cnt"] if row else 0


def mark_bounced_full(email, reason="", bounce_type="pre-check"):
    """Mark as bounced, appending to existing notes."""
    with get_conn() as conn:
        row = conn.execute("SELECT notes FROM contacts WHERE email = ?", (email.lower(),)).fetchone()
        existing_notes = row["notes"] if row else ""
        new_notes = (existing_notes or "") + f" | BOUNCE({bounce_type}): {reason}"
        conn.execute("""
            UPDATE contacts SET status='bounced', bounce=?, notes=?, date_verified=?
            WHERE email=?
        """, (bounce_type, new_notes, str(date.today()), email.lower()))


def mark_queued(email):
    with get_conn() as conn:
        conn.execute("""
            UPDATE contacts
            SET status='queued', date_queued=?,
                send_attempts = COALESCE(send_attempts, 0) + 1
            WHERE email=?
        """, (datetime.now().isoformat(), email.lower()))


def mark_sent(email, message_id, thread_id, subject, body_snippet, template_type):
    update_contact(
        email,
        status="sent",
        date_sent=str(date.today()),
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        sent_subject=subject,
        sent_body_snippet=body_snippet[:300] if body_snippet else "",
        sent_body=body_snippet,  # full body, no truncation
        template_type=template_type,
    )
    log_email(
        contact_email=email,
        direction="sent",
        email_type="initial",
        subject=subject,
        body_snippet=body_snippet[:300] if body_snippet else "",
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
    )


def mark_followup_sent(email, message_id, subject, body_snippet):
    update_contact(
        email,
        status="followup_sent",
        date_followup_sent=str(date.today()),
        followup_message_id=message_id,
        followup_body=body_snippet,  # full body
    )
    log_email(
        contact_email=email,
        direction="sent",
        email_type="followup",
        subject=subject,
        body_snippet=body_snippet[:300] if body_snippet else "",
        gmail_message_id=message_id,
    )


def mark_followup2_sent(email, message_id, subject, body_snippet):
    """Mark second follow-up as sent."""
    update_contact(
        email,
        status="followup2_sent",
        date_followup2_sent=str(date.today()),
    )
    log_email(
        contact_email=email,
        direction="sent",
        email_type="followup2",
        subject=subject,
        body_snippet=body_snippet[:300] if body_snippet else "",
        gmail_message_id=message_id,
    )


def mark_responded(email, reply_snippet, reply_message_id=None, thread_id=None):
    update_contact(
        email,
        status="responded",
        date_response_received=str(datetime.now().isoformat()),
        response_snippet=reply_snippet[:300],
        reply_message_id=reply_message_id,
    )
    log_email(
        contact_email=email,
        direction="received",
        email_type="reply",
        body_snippet=reply_snippet[:300],
        gmail_message_id=reply_message_id,
        gmail_thread_id=thread_id,
    )


def get_unclassified_replies() -> list[dict]:
    """
    Return all contacts in 'responded' status whose reply hasn't been classified yet.
    Used by reply_classifier.classify_pending().
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM contacts
            WHERE status = 'responded'
              AND reply_classified_at IS NULL
            ORDER BY date_response_received ASC
        """).fetchall()
        return [dict(r) for r in rows]


def update_reply_classification(email: str, intent: str, suggested_action: str = ""):
    """Persist a reply classification result. Called by reply_classifier."""
    update_contact(
        email,
        reply_intent=intent,
        reply_action=suggested_action,
        reply_classified_at=str(datetime.now().isoformat()),
    )


# ─── Follow-up queue ─────────────────────────────────────────────────────────

def get_followup_candidates(days_since_send=5):
    """First follow-up candidates: sent >N days ago, no follow-up yet, no reply."""
    cutoff = date.fromordinal(date.today().toordinal() - days_since_send).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM contacts
            WHERE status = 'sent'
              AND date_sent <= ?
              AND (date_followup_sent IS NULL OR date_followup_sent = '')
              AND (date_response_received IS NULL OR date_response_received = '')
              AND bounce = 'no'
            ORDER BY date_sent ASC
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]


def get_followup2_candidates(days_since_followup1=7):
    """Second follow-up candidates: first follow-up sent >N days ago, no reply yet."""
    cutoff = date.fromordinal(date.today().toordinal() - days_since_followup1).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM contacts
            WHERE status = 'followup_sent'
              AND date_followup_sent <= ?
              AND (date_followup2_sent IS NULL OR date_followup2_sent = '')
              AND (date_response_received IS NULL OR date_response_received = '')
              AND bounce = 'no'
            ORDER BY date_followup_sent ASC
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]


# ─── Email log ────────────────────────────────────────────────────────────────

def log_email(contact_email, direction, email_type, subject=None,
              body_snippet=None, gmail_message_id=None, gmail_thread_id=None):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO email_log
                (contact_email, direction, email_type, subject, body_snippet,
                 gmail_message_id, gmail_thread_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (contact_email, direction, email_type, subject, body_snippet,
              gmail_message_id, gmail_thread_id, datetime.now().isoformat()))


# ─── Daily rate tracking ──────────────────────────────────────────────────────

def today_send_count():
    today = str(date.today())
    with get_conn() as conn:
        row = conn.execute(
            "SELECT emails_sent FROM daily_stats WHERE date = ?", (today,)
        ).fetchone()
        return row["emails_sent"] if row else 0


def increment_today_count():
    today = str(date.today())
    ts = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO daily_stats (date, emails_sent, last_send_ts)
            VALUES (?, 1, ?)
            ON CONFLICT(date) DO UPDATE SET
                emails_sent = emails_sent + 1,
                last_send_ts = excluded.last_send_ts
        """, (today, ts))


def get_last_send_timestamp():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_send_ts FROM daily_stats WHERE date = ? AND last_send_ts IS NOT NULL",
            (str(date.today()),)
        ).fetchone()
        if not row:
            # Check yesterday
            row = conn.execute(
                "SELECT last_send_ts FROM daily_stats ORDER BY date DESC LIMIT 1"
            ).fetchone()
        return row["last_send_ts"] if row else None


# ─── Content post tracking ───────────────────────────────────────────────────

def today_content_count() -> int:
    """How many content batches have been posted to Buffer today."""
    today = str(date.today())
    with get_conn() as conn:
        row = conn.execute(
            "SELECT content_posts_today FROM daily_stats WHERE date = ?", (today,)
        ).fetchone()
        return row["content_posts_today"] if row else 0


def increment_content_count():
    """Increment today's content post counter after a successful Buffer batch."""
    today = str(date.today())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO daily_stats (date, content_posts_today)
            VALUES (?, 1)
            ON CONFLICT(date) DO UPDATE SET
                content_posts_today = content_posts_today + 1
        """, (today,))


def today_contacts_found() -> int:
    """How many new contacts have been discovered today via find_contacts."""
    today = str(date.today())
    with get_conn() as conn:
        row = conn.execute(
            "SELECT contacts_found_today FROM daily_stats WHERE date = ?", (today,)
        ).fetchone()
        return row["contacts_found_today"] if row else 0


def increment_contacts_found():
    """Increment today's discovered-contacts counter."""
    today = str(date.today())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO daily_stats (date, contacts_found_today)
            VALUES (?, 1)
            ON CONFLICT(date) DO UPDATE SET
                contacts_found_today = contacts_found_today + 1
        """, (today,))


# ─── Template performance tracking ───────────────────────────────────────────

def record_send_for_template(template_type, contact_type):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO template_performance (template_type, contact_type, total_sent)
            VALUES (?, ?, 1)
            ON CONFLICT(template_type, contact_type) DO UPDATE SET
                total_sent = total_sent + 1
        """, (template_type, contact_type))


def record_reply_for_template(template_type, contact_type):
    ts = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO template_performance (template_type, contact_type, total_sent, total_replies, last_reply_ts)
            VALUES (?, ?, 0, 1, ?)
            ON CONFLICT(template_type, contact_type) DO UPDATE SET
                total_replies = total_replies + 1,
                last_reply_ts = excluded.last_reply_ts
        """, (template_type, contact_type, ts))


def get_template_stats():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT template_type, contact_type, total_sent, total_replies,
                   ROUND(CAST(total_replies AS REAL) / NULLIF(total_sent, 0) * 100, 1) as reply_rate
            FROM template_performance
            ORDER BY reply_rate DESC NULLS LAST
        """).fetchall()
        return [dict(r) for r in rows]


def get_best_template_type(contact_type: str, min_sends: int = 5) -> str | None:
    """
    Return the template_type with the highest reply rate for a given contact_type,
    provided it has at least min_sends sends. Returns None if no qualifying data.
    """
    with get_conn() as conn:
        row = conn.execute("""
            SELECT template_type,
                   ROUND(total_replies * 1.0 / NULLIF(total_sent, 0) * 100, 1) as reply_rate
            FROM template_performance
            WHERE contact_type = ?
              AND total_sent >= ?
            ORDER BY reply_rate DESC
            LIMIT 1
        """, (contact_type, min_sends)).fetchone()
    return row["template_type"] if row else None


# ─── Learning ─────────────────────────────────────────────────────────────────

def save_insight(insight_type, content, based_on_n):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO learning_insights (generated_at, insight_type, content, based_on_n)
            VALUES (?, ?, ?, ?)
        """, (datetime.now().isoformat(), insight_type, content, based_on_n))


def get_recent_insights(limit=5):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM learning_insights ORDER BY generated_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


# ─── Status summary ───────────────────────────────────────────────────────────

def get_pipeline_summary():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT status, COUNT(*) as count FROM contacts GROUP BY status
        """).fetchall()
        summary = {r["status"]: r["count"] for r in rows}

        total_sent_today = today_send_count()
        summary["_today_sent"] = total_sent_today

        responded = summary.get("responded", 0)
        sent_total = sum(summary.get(s, 0) for s in ("sent", "followup_sent", "responded"))
        summary["_reply_rate"] = (
            f"{round(responded / sent_total * 100, 1)}%" if sent_total else "—"
        )
        return summary


# ─── Dead domain / address registry (persisted bounce suppression) ─────────────

def save_dead_domain(domain: str, reason: str = ""):
    domain = domain.strip().lower()
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO dead_domains (domain, added_at, reason)
            VALUES (?, ?, ?)
        """, (domain, datetime.now().isoformat(), reason))


def save_dead_address(email: str, reason: str = ""):
    email = email.strip().lower()
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO dead_addresses (email, added_at, reason)
            VALUES (?, ?, ?)
        """, (email, datetime.now().isoformat(), reason))


def get_confirmed_dead_domains() -> set:
    with get_conn() as conn:
        rows = conn.execute("SELECT domain FROM dead_domains").fetchall()
        return {r["domain"] for r in rows}


def get_confirmed_dead_addresses() -> set:
    with get_conn() as conn:
        rows = conn.execute("SELECT email FROM dead_addresses").fetchall()
        return {r["email"] for r in rows}


def today_bounce_count() -> int:
    """Return number of contacts marked bounced today (actual + pre-check)."""
    today = str(date.today())
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) FROM contacts
            WHERE status = 'bounced' AND date_sent = ?
        """, (today,)).fetchone()
        return row[0] if row else 0


def store_research(email: str, research_notes: str):
    """Store researched facts about a contact. Marks research_done=1."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE contacts SET research_notes=?, research_done=1 WHERE email=?
        """, (research_notes[:2000], email.lower()))


def get_unresearched_verified(limit: int = 10) -> list:
    """Return verified contacts that haven't been researched yet."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM contacts
            WHERE status = 'verified' AND (research_done IS NULL OR research_done = 0)
            ORDER BY date_added ASC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def log_discovery(search_query: str, contact_type: str, results_found: int):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO discovery_log (search_query, contact_type, results_found, searched_at)
            VALUES (?, ?, ?, ?)
        """, (search_query, contact_type, results_found, datetime.now().isoformat()))


def recently_searched(query: str, within_hours: int = 48) -> bool:
    """Return True if this query was already searched recently."""
    cutoff = (datetime.now() - timedelta(hours=within_hours)).isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT id FROM discovery_log WHERE search_query=? AND searched_at > ?
        """, (query, cutoff)).fetchone()
        return row is not None
