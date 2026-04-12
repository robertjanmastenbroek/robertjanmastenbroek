"""
Release trigger system.

Track releases are the highest-leverage moments in an artist's calendar.
This module:
  1. Stores upcoming releases in the DB
  2. Detects when a release is due (within N days)
  3. Publishes a release.campaign_fired event so downstream agents can react
  4. master_agent reads this during briefings

When a release fires, downstream agents should:
  - run_cycle: prioritise curator contacts in the next batch
  - post_today: tag today's content with the release track
  - playlist_run: prioritise playlist submission for the track
"""

import logging
from datetime import date, datetime, timedelta

import db

log = logging.getLogger("outreach.release_trigger")


def add_release(
    track_name: str,
    release_date: str,
    platforms: str = "spotify,tiktok,instagram",
    notes: str = "",
) -> int:
    """Add a release to the calendar. Returns new row id."""
    with db.get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO release_calendar
               (track_name, release_date, platforms, notes)
               VALUES (?,?,?,?)""",
            (track_name, release_date, platforms, notes),
        )
        return cursor.lastrowid


def get_pending_releases() -> list[dict]:
    """Return all releases where campaign_fired = 0, ordered by release_date."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM release_calendar WHERE campaign_fired=0 ORDER BY release_date ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def check_due(days_window: int = 7) -> list[dict]:
    """
    Return pending releases whose release_date is within
    [today - days_window, today + days_window].
    days_window=0 means today only.
    """
    today = date.today()
    window_start = (today - timedelta(days=days_window)).isoformat()
    window_end   = (today + timedelta(days=days_window)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM release_calendar
               WHERE campaign_fired=0
                 AND release_date >= ?
                 AND release_date <= ?
               ORDER BY release_date ASC""",
            (window_start, window_end),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_fired(release_id: int) -> None:
    """Mark a release campaign as fired."""
    now = datetime.utcnow().isoformat()
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE release_calendar SET campaign_fired=1, fired_at=? WHERE id=?",
            (now, release_id),
        )


def fire_due_campaigns(days_window: int = 7, dry_run: bool = False) -> list[dict]:
    """
    Check for due releases and fire campaign events for those not yet fired.
    Returns list of releases that were (or would be in dry_run) fired.
    """
    due = check_due(days_window=days_window)
    fired = []
    for release in due:
        log.info("Release due: %s (date=%s)", release["track_name"], release["release_date"])
        if not dry_run:
            try:
                import events as _events
                _events.publish("release.campaign_fired", "release_trigger", {
                    "track_name": release["track_name"],
                    "release_date": release["release_date"],
                    "platforms": release["platforms"],
                    "notes": release["notes"],
                })
            except ImportError:
                pass
            mark_fired(release["id"])
        fired.append(release)
        print(
            f"{'[DRY RUN] ' if dry_run else ''}Release campaign fired: "
            f"{release['track_name']} ({release['release_date']})"
        )
    return fired
