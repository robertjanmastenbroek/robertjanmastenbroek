#!/usr/bin/env python3.13
"""
post_queue.py — Persistent queue for failed Buffer posts.

When a post fails (upload or Buffer API error), call save_failed_post() to
persist it to data/failed_posts.json. On the next run, call load_failed_posts()
and retry via buffer_poster.upload_video_and_queue().

Usage:
  from post_queue import save_failed_post, load_failed_posts, clear_failed_post
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# Path to the queue file — relative to project root
QUEUE_PATH = Path(__file__).parent.parent / "data" / "failed_posts.json"


def save_failed_post(
    clip_path: str,
    tiktok_caption: str,
    instagram_caption: str,
    youtube_title: str,
    youtube_desc: str,
    scheduled_at: str,
    error: str,
) -> None:
    """Append a failed post to the queue file."""
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    posts = load_failed_posts()
    posts.append({
        "clip_path":          clip_path,
        "tiktok_caption":     tiktok_caption,
        "instagram_caption":  instagram_caption,
        "youtube_title":      youtube_title,
        "youtube_desc":       youtube_desc,
        "scheduled_at":       scheduled_at,
        "error":              error,
        "failed_at":          datetime.now(timezone.utc).isoformat(),
        "retry_count":        0,
    })
    QUEUE_PATH.write_text(json.dumps(posts, indent=2))


def load_failed_posts() -> list:
    """Return all queued failed posts, or [] if none."""
    if not QUEUE_PATH.exists():
        return []
    try:
        return json.loads(QUEUE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def clear_failed_post(index: int) -> None:
    """Remove entry at `index` from the queue (after successful retry)."""
    posts = load_failed_posts()
    if 0 <= index < len(posts):
        posts.pop(index)
    QUEUE_PATH.write_text(json.dumps(posts, indent=2))


def queue_depth() -> int:
    """Return the number of posts waiting to be retried."""
    return len(load_failed_posts())
