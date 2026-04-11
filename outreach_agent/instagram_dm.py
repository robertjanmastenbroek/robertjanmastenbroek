#!/usr/bin/env python3
"""
RJM Instagram DM Outreach Agent

Reads curators with Instagram handles from playlists.db (contact_found status),
generates short personalised DMs via Claude CLI, sends via instagrapi.

Rate: 20 DMs/day max, 5–15 min between sends.
Active window: 08:00–23:00 CET (same as email agent).

Usage:
  python3 instagram_dm.py run         # Send DMs up to daily limit
  python3 instagram_dm.py status      # Show pipeline status
  python3 instagram_dm.py preview     # Preview next 5 DMs without sending
"""

import argparse
import logging
import os
import random
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))

from config import ACTIVE_HOUR_END, ACTIVE_HOUR_START, CLAUDE_MODEL_FAST, DB_PATH
import scheduler
from story import TRACKS

log = logging.getLogger("outreach.instagram_dm")
logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)

CET = ZoneInfo("Europe/Madrid")

# ─── Settings ─────────────────────────────────────────────────────────────────
MAX_DMS_PER_DAY   = 20
MIN_INTERVAL_SECS = 300   # 5 min
MAX_INTERVAL_SECS = 900   # 15 min
IG_USERNAME       = os.getenv("IG_USERNAME", "")
IG_PASSWORD       = os.getenv("IG_PASSWORD", "")
IG_SESSION_PATH   = Path(__file__).parent / "ig_session.json"

# ─── DB: instagram_outreach table ─────────────────────────────────────────────

_IG_SCHEMA = """
CREATE TABLE IF NOT EXISTS instagram_outreach (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    instagram_handle TEXT    NOT NULL,
    playlist_name    TEXT,
    playlist_id      TEXT,
    dm_text          TEXT,
    status           TEXT    DEFAULT 'pending',
    -- pending | sent | failed | replied
    date_sent        TEXT,
    date_replied     TEXT,
    reply_snippet    TEXT,
    error_msg        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ig_handle
    ON instagram_outreach(instagram_handle);
"""


def _init_ig_table():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(_IG_SCHEMA)
    conn.commit()
    conn.close()


def _get_sent_today() -> int:
    today = str(date.today())
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT COUNT(*) FROM instagram_outreach WHERE date_sent = ? AND status = 'sent'",
        (today,),
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def _already_dmd(handle: str) -> bool:
    handle = handle.lstrip("@").lower()
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT id FROM instagram_outreach WHERE instagram_handle = ? AND status IN ('sent','replied')",
        (handle,),
    ).fetchone()
    conn.close()
    return row is not None


def _mark_sent(handle: str, playlist_name: str, playlist_id: str, dm_text: str):
    handle = handle.lstrip("@").lower()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """
        INSERT OR REPLACE INTO instagram_outreach
            (instagram_handle, playlist_name, playlist_id, dm_text, status, date_sent)
        VALUES (?, ?, ?, ?, 'sent', ?)
        """,
        (handle, playlist_name, playlist_id, dm_text, str(date.today())),
    )
    conn.commit()
    conn.close()


def _mark_failed(handle: str, error: str):
    handle = handle.lstrip("@").lower()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """
        INSERT OR REPLACE INTO instagram_outreach
            (instagram_handle, status, error_msg, date_sent)
        VALUES (?, 'failed', ?, ?)
        """,
        (handle, error[:500], str(date.today())),
    )
    conn.commit()
    conn.close()


# ─── Candidate loading ────────────────────────────────────────────────────────

def _get_candidates(limit: int = 60) -> list[dict]:
    """Return contact_found playlists with an IG handle not yet DM'd."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT spotify_id, name, curator_name, curator_instagram,
               genre_tags, best_track_match, follower_count
        FROM playlists
        WHERE status = 'contact_found'
          AND curator_instagram IS NOT NULL
          AND curator_instagram != ''
        ORDER BY relevance_score DESC, follower_count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    candidates = []
    for r in rows:
        handle = (r["curator_instagram"] or "").lstrip("@").lower()
        if handle and not _already_dmd(handle):
            candidates.append(dict(r))
    return candidates


# ─── Claude CLI ───────────────────────────────────────────────────────────────

_CLAUDE_CLI = None


def _get_cli() -> str:
    global _CLAUDE_CLI
    if _CLAUDE_CLI:
        return _CLAUDE_CLI
    env_path = os.getenv("CLAUDE_CLI_PATH")
    if env_path and Path(env_path).is_file():
        _CLAUDE_CLI = env_path
        return _CLAUDE_CLI
    base = Path(os.path.expanduser("~/Library/Application Support/Claude/claude-code"))
    if base.exists():
        for ver_dir in sorted(base.iterdir(), reverse=True):
            candidate = ver_dir / "claude.app" / "Contents" / "MacOS" / "claude"
            if candidate.is_file():
                _CLAUDE_CLI = str(candidate)
                return _CLAUDE_CLI
    for name in ("claude", "claude-code"):
        result = subprocess.run(["which", name], capture_output=True, text=True)
        if result.returncode == 0:
            _CLAUDE_CLI = result.stdout.strip()
            return _CLAUDE_CLI
    raise FileNotFoundError("Cannot find Claude CLI. Set CLAUDE_CLI_PATH env var.")


def _pick_track(best_track_match: str, genre_tags: str) -> dict:
    """Pick the most relevant track for this playlist's genre."""
    all_tracks = (
        TRACKS["tribal_techno"]
        + TRACKS["psytrance"]
        + TRACKS["melodic_techno"]
    )
    # Try exact match on best_track_match first
    if best_track_match:
        for t in all_tracks:
            if t["title"].lower() == best_track_match.lower():
                return t
    # Genre heuristic
    tags = (genre_tags or "").lower()
    if any(w in tags for w in ["psy", "trance", "140"]):
        return TRACKS["psytrance"][0]   # Halleluyah
    if any(w in tags for w in ["melodic", "house", "minimal"]):
        return TRACKS["melodic_techno"][0]  # Living Water
    return TRACKS["tribal_techno"][0]   # Renamed — default


def generate_dm(playlist: dict) -> str:
    """Generate a short Instagram DM via Claude CLI. Falls back to template."""
    name          = (playlist.get("curator_name") or "").strip() or "there"
    playlist_name = playlist.get("name", "your playlist")
    genre_tags    = playlist.get("genre_tags", "") or ""
    followers     = playlist.get("follower_count") or 0
    track         = _pick_track(playlist.get("best_track_match", ""), genre_tags)

    prompt = f"""You are Robert-Jan Mastenbroek (Dutch DJ/producer, Tenerife, 290K IG @robertjanmastenbroek).
Write a SHORT Instagram DM to a Spotify playlist curator. 60–80 words total. Plain text only.

CURATOR: {name}
PLAYLIST: "{playlist_name}" ({followers:,} followers)
GENRE TAGS: {genre_tags}

TRACK TO PITCH:
  Title:   {track['title']}
  BPM:     {track['bpm']}
  Notes:   {track['notes']}
  Spotify: {track['spotify']}

RULES:
- Open with one specific observation about their playlist (sound, energy, BPM feel, vibe) — not a compliment
- One sentence about the track + Spotify link inline
- Close with one short question ("Worth a listen?" / "Fits the lane?" / "Add it?")
- NO "Hi I'm a producer" opener, NO "I hope this message finds you well"
- NO hashtags, NO emojis, NO markdown, NO asterisks
- Sign: Robert-Jan

Return ONLY the DM text. No JSON, no explanation."""

    try:
        result = subprocess.run(
            [_get_cli(), "--model", CLAUDE_MODEL_FAST, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ},
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        log.warning("Claude CLI DM generation failed: %s", e)

    # Fallback — short template
    return (
        f'"{playlist_name}" is exactly the lane I\'m working in.\n\n'
        f"{track['title']}: {track['spotify']}\n"
        f"{track['bpm']} BPM. {track['notes']}.\n\n"
        f"Worth a listen?\n\nRobert-Jan"
    )


# ─── Instagram client ─────────────────────────────────────────────────────────

def _get_ig_client():
    """Return authenticated instagrapi Client. Session persisted in ig_session.json."""
    from instagrapi import Client  # type: ignore

    if not IG_USERNAME or not IG_PASSWORD:
        raise ValueError(
            "IG_USERNAME and IG_PASSWORD must be set in outreach_agent/.env\n"
            "  IG_USERNAME=holyraveofficial\n"
            "  IG_PASSWORD=your_password"
        )

    cl = Client()
    cl.delay_range = [2, 5]  # natural delay between API calls

    if IG_SESSION_PATH.exists():
        try:
            cl.load_settings(str(IG_SESSION_PATH))
            cl.login(IG_USERNAME, IG_PASSWORD)
            log.info("Instagram: session restored for @%s", IG_USERNAME)
            return cl
        except Exception as e:
            log.warning("Session restore failed (%s) — logging in fresh", e)
            IG_SESSION_PATH.unlink(missing_ok=True)

    cl.login(IG_USERNAME, IG_PASSWORD)
    cl.dump_settings(str(IG_SESSION_PATH))
    log.info("Instagram: fresh login as @%s", IG_USERNAME)
    return cl


def _send_dm(cl, handle: str, text: str) -> bool:
    """Send a DM. Returns True on success."""
    handle = handle.lstrip("@")
    try:
        user_id = cl.user_id_from_username(handle)
        cl.direct_send(text, user_ids=[user_id])
        return True
    except Exception as e:
        log.error("DM to @%s failed: %s", handle, e)
        return False


# ─── Commands ─────────────────────────────────────────────────────────────────

def run():
    _init_ig_table()

    if not scheduler.is_within_active_window():
        log.info(
            "Outside active window (%d:00–%d:00 CET) — exiting",
            ACTIVE_HOUR_START,
            ACTIVE_HOUR_END,
        )
        return

    sent_today = _get_sent_today()
    if sent_today >= MAX_DMS_PER_DAY:
        log.info("Daily DM limit reached (%d/%d) — done for today", sent_today, MAX_DMS_PER_DAY)
        return

    candidates = _get_candidates()
    remaining  = MAX_DMS_PER_DAY - sent_today
    to_send    = candidates[:remaining]

    log.info(
        "%d candidates | %d sent today | sending up to %d this cycle",
        len(candidates), sent_today, len(to_send),
    )

    if not to_send:
        log.info("No new IG candidates — run find_contacts.py to discover more handles")
        return

    cl = _get_ig_client()

    for i, playlist in enumerate(to_send, 1):
        handle = (playlist.get("curator_instagram") or "").lstrip("@").lower()
        if not handle:
            continue

        log.info("[%d/%d] @%s — %s", i, len(to_send), handle, playlist.get("name", ""))

        dm_text = generate_dm(playlist)
        success = _send_dm(cl, handle, dm_text)

        if success:
            _mark_sent(
                handle,
                playlist.get("name", ""),
                playlist.get("spotify_id", ""),
                dm_text,
            )
            # Mark playlist as contacted in playlists table
            import playlist_db
            playlist_db.mark_contacted(playlist["spotify_id"])
            log.info("  ✅ DM sent to @%s", handle)
        else:
            _mark_failed(handle, "send_failed")
            log.warning("  ❌ Failed to send to @%s", handle)

        if i < len(to_send):
            wait = random.randint(MIN_INTERVAL_SECS, MAX_INTERVAL_SECS)
            log.info("  Waiting %dm %ds...", wait // 60, wait % 60)
            time.sleep(wait)

    log.info("Run complete — %d DMs sent today total", _get_sent_today())


def preview():
    """Preview the next 5 DMs without sending anything."""
    _init_ig_table()
    candidates = _get_candidates()[:5]
    if not candidates:
        print("No candidates. Run find_contacts.py first.")
        return
    for p in candidates:
        handle = (p.get("curator_instagram") or "").lstrip("@")
        dm = generate_dm(p)
        print(f"\n{'─'*55}")
        print(f"TO:  @{handle}")
        print(f"FOR: {p.get('name', '')} ({p.get('follower_count', 0):,} followers)")
        print(f"{'─'*55}")
        print(dm)
    print(f"\n{'─'*55}")
    print(f"Total ready: {len(_get_candidates())} curators with IG handles")


def status():
    _init_ig_table()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT status, COUNT(*) as n FROM instagram_outreach GROUP BY status"
    ).fetchall()
    today = conn.execute(
        "SELECT COUNT(*) FROM instagram_outreach WHERE date_sent = ? AND status = 'sent'",
        (str(date.today()),),
    ).fetchone()[0]
    candidates = conn.execute(
        """
        SELECT COUNT(*) FROM playlists
        WHERE status = 'contact_found'
          AND curator_instagram IS NOT NULL AND curator_instagram != ''
        """
    ).fetchone()[0]
    conn.close()

    pending = len(_get_candidates())

    print(f"\n{'='*42}")
    print(f"  Instagram DM Pipeline")
    print(f"{'='*42}")
    for r in rows:
        print(f"  {r['status']:<16} {r['n']}")
    print(f"{'─'*42}")
    print(f"  Sent today:      {today}/{MAX_DMS_PER_DAY}")
    print(f"  Ready to send:   {pending} (of {candidates} with handles)")
    print(f"{'='*42}\n")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Instagram DM outreach for RJM")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run",     help="Send DMs (up to daily limit)")
    sub.add_parser("status",  help="Show pipeline status")
    sub.add_parser("preview", help="Preview next 5 DMs without sending")
    args = parser.parse_args()

    if args.cmd == "run":
        run()
    elif args.cmd == "status":
        status()
    elif args.cmd == "preview":
        preview()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
