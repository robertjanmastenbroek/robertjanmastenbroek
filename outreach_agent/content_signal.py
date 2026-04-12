"""
Cross-platform content state log.

Every content post (Buffer → TikTok/IG/YouTube) is recorded here.
Master agent reads this to know what has been published this week.
Prevents duplicate posts and enables coordinated content drops.
"""

import logging
from datetime import datetime, timedelta

import db

log = logging.getLogger("outreach.content_signal")


def log_content_post(
    platform: str,
    format: str,
    track: str | None = None,
    angle: str | None = None,
    hook: str | None = None,
    buffer_id: str | None = None,
    filename: str | None = None,
) -> int:
    """
    Record a content post and publish a content.post_published event.
    Returns the new content_log row id.
    """
    now = datetime.utcnow().isoformat()
    with db.get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO content_log
               (posted_at, platform, format, track, angle, hook, buffer_id, filename)
               VALUES (?,?,?,?,?,?,?,?)""",
            (now, platform, format, track, angle, hook, buffer_id, filename),
        )
        row_id = cursor.lastrowid

    try:
        import events as _events
        _events.publish("content.post_published", "post_today", {
            "platform": platform,
            "format": format,
            "track": track,
            "angle": angle,
        })
    except ImportError:
        pass

    log.info("Content logged: %s on %s (track=%s)", format, platform, track)
    return row_id


def get_cross_platform_state(days: int = 7) -> list[dict]:
    """Return all posts from the last N days, newest first."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM content_log WHERE posted_at >= ? ORDER BY posted_at DESC",
            (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_weekly_summary() -> dict:
    """Return a summary dict for the current week."""
    posts = get_cross_platform_state(days=7)
    by_platform: dict[str, int] = {}
    by_track: dict[str, int] = {}
    by_angle: dict[str, int] = {}

    for p in posts:
        by_platform[p["platform"]] = by_platform.get(p["platform"], 0) + 1
        if p["track"]:
            by_track[p["track"]] = by_track.get(p["track"], 0) + 1
        if p["angle"]:
            by_angle[p["angle"]] = by_angle.get(p["angle"], 0) + 1

    return {
        "total_posts": len(posts),
        "by_platform": by_platform,
        "by_track": by_track,
        "by_angle": by_angle,
        "latest_post_at": posts[0]["posted_at"] if posts else None,
    }
