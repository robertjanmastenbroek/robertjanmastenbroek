"""
⚠️  PARTIALLY DEPRECATED — 2026-04-16 ⚠️
========================================================================
The "foundation of the learning loop" role of this module is DEPRECATED.
The canonical post registry is now data/performance/YYYY-MM-DD_posts.json
written by content_engine/pipeline.py, which content_engine/learning_loop.py
reads directly. metrics_fetcher.py + weights_learner.py are both retired.

What still uses this file:
  - outreach_agent/post_today.py (the legacy Claude-driven daily pipeline)
    still calls log_content_post() to record content_log rows so the
    master agent's dashboard shows "what was published this week".
  - outreach_agent/master_agent.py / the weekly report read content_log
    for the "recent posts" UI only.

Do NOT add new callers to this module for learning-loop purposes — the
new loop does not read from SQLite content_log / content_metrics. If a
future pipeline needs to log posts, write to data/performance/*_posts.json
instead (see content_engine/pipeline.py for the schema).
========================================================================

Cross-platform content state log.

Every content post (Buffer → TikTok/IG/YouTube) is recorded here.
Master agent reads this to know what has been published this week.
Prevents duplicate posts and enables coordinated content drops.

This is also the foundation of the learning loop — log_content_post() persists
every creative decision that went into a clip (hook mechanism, source videos,
BPM, segments, captions) so metrics_fetcher + weights_learner can later
correlate "what did we do?" with "what worked?".
"""

import json
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
    # ─── Learning loop fields (all optional for backwards compat) ──────────
    hook_mechanism: str | None = None,
    bpm: float | None = None,
    bar_duration: float | None = None,
    clip_length: int | None = None,
    segment_count: int | None = None,
    source_videos: list | None = None,
    lead_category: str | None = None,
    cloudinary_url: str | None = None,
    scheduled_at: str | None = None,
    tiktok_caption: str | None = None,
    instagram_caption: str | None = None,
    youtube_title: str | None = None,
    youtube_desc: str | None = None,
    exploration: bool = False,
    batch_id: str | None = None,
) -> int:
    """
    Record a content post and publish a content.post_published event.
    Returns the new content_log row id.

    All creative metadata is persisted so the learning loop can later
    correlate decisions with performance.
    """
    now = datetime.utcnow().isoformat()
    source_videos_json = json.dumps(source_videos) if source_videos else None
    with db.get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO content_log
               (posted_at, platform, format, track, angle, hook, buffer_id, filename,
                hook_mechanism, bpm, bar_duration, clip_length, segment_count,
                source_videos, lead_category, cloudinary_url, scheduled_at,
                tiktok_caption, instagram_caption, youtube_title, youtube_desc,
                exploration, batch_id)
               VALUES (?,?,?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?)""",
            (
                now, platform, format, track, angle, hook, buffer_id, filename,
                hook_mechanism, bpm, bar_duration, clip_length, segment_count,
                source_videos_json, lead_category, cloudinary_url, scheduled_at,
                tiktok_caption, instagram_caption, youtube_title, youtube_desc,
                1 if exploration else 0, batch_id,
            ),
        )
        row_id = cursor.lastrowid

    try:
        import events as _events
        _events.publish("content.post_published", "post_today", {
            "platform": platform,
            "format": format,
            "track": track,
            "angle": angle,
            "hook_mechanism": hook_mechanism,
        })
    except ImportError:
        pass

    log.info("Content logged: %s on %s (track=%s, mech=%s, batch=%s)",
             format, platform, track, hook_mechanism, batch_id)
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


def get_posts_needing_metrics(min_age_hours: int = 24, max_age_days: int = 60) -> list[dict]:
    """
    Return posts that are old enough for metrics to have stabilised but not
    so old that we'd miss the window. Excludes posts we already have metrics for.

    Used by metrics_fetcher.py.
    """
    cutoff_old = (datetime.utcnow() - timedelta(hours=min_age_hours)).isoformat()
    cutoff_new = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT cl.*
            FROM content_log cl
            WHERE cl.posted_at <= ?
              AND cl.posted_at >= ?
              AND cl.buffer_id IS NOT NULL
              AND cl.platform IN ('tiktok','instagram','youtube')
            ORDER BY cl.posted_at DESC
        """, (cutoff_old, cutoff_new)).fetchall()
    return [dict(r) for r in rows]


def get_weekly_summary() -> dict:
    """Return a summary dict for the current week."""
    posts = get_cross_platform_state(days=7)
    by_platform: dict[str, int] = {}
    by_track: dict[str, int] = {}
    by_angle: dict[str, int] = {}
    by_mechanism: dict[str, int] = {}

    for p in posts:
        by_platform[p["platform"]] = by_platform.get(p["platform"], 0) + 1
        if p["track"]:
            by_track[p["track"]] = by_track.get(p["track"], 0) + 1
        if p["angle"]:
            by_angle[p["angle"]] = by_angle.get(p["angle"], 0) + 1
        if p.get("hook_mechanism"):
            by_mechanism[p["hook_mechanism"]] = by_mechanism.get(p["hook_mechanism"], 0) + 1

    return {
        "total_posts": len(posts),
        "by_platform": by_platform,
        "by_track": by_track,
        "by_angle": by_angle,
        "by_hook_mechanism": by_mechanism,
        "latest_post_at": posts[0]["posted_at"] if posts else None,
    }
