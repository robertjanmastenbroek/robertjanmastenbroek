"""
registry.py — Dedup + Feature.fm / Odesli smart-link instrumentation.

The registry is a flat JSONL log. Each publish appends one row so daily
status reports can count uploads without hitting the YouTube API.

Smart links:
  - If FEATUREFM_API_KEY is set → create a Feature.fm smart link
  - Else → fall back to Odesli/Songlink (free, no auth) and append UTM
    params to the Spotify URL manually

UTM convention:
  ?utm_source=youtube&utm_medium=holyrave_longform&utm_campaign=<track-slug>
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform.types import PublishResult

logger = logging.getLogger(__name__)

REGISTRY_FILE = cfg.REGISTRY_DIR / "youtube_longform.jsonl"


# ─── Dedup registry ──────────────────────────────────────────────────────────

def already_published(track_title: str) -> Optional[dict]:
    """
    Return the first registered upload for this track if one exists.
    Used to prevent accidental double-publish on retry.
    """
    if not REGISTRY_FILE.exists():
        return None
    key = track_title.lower().strip()
    with open(REGISTRY_FILE, "r") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("track_title", "").lower().strip() == key:
                return row
    return None


def append(result: PublishResult) -> None:
    """Append one row describing a completed publish to the JSONL log."""
    cfg.ensure_workspace()
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "track_title":     result.request.track_title,
        "youtube_id":      result.youtube_id,
        "youtube_url":     result.youtube_url,
        "smart_link":      result.smart_link,
        "thumbnails":      [str(t.local_path) for t in result.thumbnails],
        "hero_image":      str(result.hero_image.local_path) if result.hero_image else None,
        "video":           str(result.video.local_path) if result.video else None,
        "dry_run":         result.request.dry_run,
        "elapsed_seconds": result.elapsed_seconds,
        "cost_usd":        result.cost_usd,
        "error":           result.error,
    }
    with open(REGISTRY_FILE, "a") as f:
        f.write(json.dumps(row) + "\n")
    logger.info("Registry appended for %s", result.request.track_title)


def count_today() -> int:
    """Count successful uploads logged today (local time)."""
    if not REGISTRY_FILE.exists():
        return 0
    today = datetime.now().date().isoformat()
    n = 0
    with open(REGISTRY_FILE, "r") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("error"):
                continue
            if row.get("dry_run"):
                continue
            if row.get("timestamp", "")[:10] == today:
                n += 1
    return n


# ─── Smart-link generation ───────────────────────────────────────────────────

def _utm_suffix(track_title: str) -> str:
    slug = "".join(c if c.isalnum() else "_" for c in track_title.lower()).strip("_")
    return (
        f"?utm_source={cfg.UTM_SOURCE}"
        f"&utm_medium={cfg.UTM_MEDIUM}"
        f"&utm_campaign=hr_{slug}"
    )


def _build_utm_spotify_url(track_title: str) -> str:
    return f"{cfg.SPOTIFY_ARTIST_URL}{_utm_suffix(track_title)}"


def _build_featurefm(track_title: str, spotify_url: str) -> Optional[str]:
    """Create a Feature.fm smart link. Returns None on failure (caller falls back)."""
    if not cfg.FEATUREFM_API_KEY:
        return None
    url = "https://api.feature.fm/v1/links"
    headers = {
        "Authorization": f"Bearer {cfg.FEATUREFM_API_KEY}",
        "Content-Type":  "application/json",
    }
    body = {
        "title":       f"{cfg.ARTIST_FULL_NAME} — {track_title}",
        "shortId":     f"holyrave-{track_title.lower().replace(' ', '-')[:40]}",
        "destinationUrl": spotify_url,
    }
    if cfg.FEATUREFM_ACCOUNT_ID:
        body["accountId"] = cfg.FEATUREFM_ACCOUNT_ID
    try:
        r = requests.post(url, headers=headers, json=body, timeout=20)
        if r.status_code in (200, 201):
            return r.json().get("url") or r.json().get("shortUrl")
        logger.warning("Feature.fm returned %d: %s", r.status_code, r.text[:300])
    except Exception as e:
        logger.warning("Feature.fm request failed: %s", e)
    return None


def _build_odesli(spotify_url: str) -> Optional[str]:
    """Free cross-DSP smart link via Odesli/Songlink."""
    try:
        r = requests.get(
            cfg.ODESLI_API_BASE + "/links",
            params={"url": spotify_url},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("pageUrl")
    except Exception as e:
        logger.warning("Odesli request failed: %s", e)
    return None


def build_smart_link(track_title: str) -> str:
    """
    Resolve the best smart link for a track, in priority order:
      1. Feature.fm  (tracked, paid tier only if subscription active)
      2. Odesli/Songlink (free)
      3. Raw Spotify URL with UTM suffix (always works)
    """
    utm_url = _build_utm_spotify_url(track_title)

    feature_url = _build_featurefm(track_title, utm_url)
    if feature_url:
        return feature_url

    odesli_url = _build_odesli(cfg.SPOTIFY_ARTIST_URL)
    if odesli_url:
        return f"{odesli_url}{_utm_suffix(track_title)}"

    return utm_url
