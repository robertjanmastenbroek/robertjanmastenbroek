"""
registry.py — Dedup + primary-listen-link instrumentation.

The registry is a flat JSONL log. Each publish appends one row so daily
status reports can count uploads without hitting the YouTube API.

Primary listen link policy (2026-04-22 Spotify-first mandate):
  North Star = 1M Spotify monthly listeners. Every outbound link from
  the long-form pipeline points at SPOTIFY by default — track URL if
  we know it, artist URL otherwise. No Odesli aggregator in the primary
  slot (kills Spotify conversion: users pick their preferred DSP, which
  for the majority of our TAM is NOT Spotify).

  Opt-in to the old Odesli / Feature.fm aggregator behavior by setting
  HOLYRAVE_PRIMARY_LINK=smart  (defaults to "spotify" when unset).

UTM convention:
  ?utm_source=youtube&utm_medium=holyrave_longform&utm_campaign=<track-slug>
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from content_engine.audio_engine import TRACK_APPLE_MUSIC_URLS, TRACK_SPOTIFY_URLS
from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform.types import PublishResult

logger = logging.getLogger(__name__)

REGISTRY_FILE = cfg.REGISTRY_DIR / "youtube_longform.jsonl"


# ─── Dedup registry ──────────────────────────────────────────────────────────

def already_published(track_title: str) -> Optional[dict]:
    """
    Return a registry row for this track, preferring a SUCCESSFUL
    publish (youtube_id set, no error, not a dry-run) over any
    failure/dry-run row that happens to precede it in the JSONL.
    Used by the publisher's dedup guard.

    The earlier version returned the FIRST row by track name. That was
    wrong whenever a dry-run or errored row preceded a successful
    publish — the publisher's guard (`not dry_run and not error`)
    would pass on the first row, and the cron would re-render the
    track and waste money. 2026-04-22 Selah re-run bug: worktree
    registry had the success, main's registry had only
    dry-run + error rows preceding that, dedup returned the first
    (dry-run) row, publisher decided the track wasn't really published
    yet, and $3.77 evaporated re-generating what already existed.

    Return priority:
      1. First row where youtube_id is set and error is None and
         dry_run is False  (the real "already published" signal)
      2. First row matching the track name otherwise (may be a
         failure/dry-run — lets callers inspect prior-attempt context)
      3. None if the track never appears.
    """
    if not REGISTRY_FILE.exists():
        return None
    key = track_title.lower().strip()
    first_seen: Optional[dict] = None
    with open(REGISTRY_FILE, "r") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("track_title", "").lower().strip() != key:
                continue
            if (row.get("youtube_id")
                    and not row.get("error")
                    and not row.get("dry_run")):
                return row   # Successful publish — use this
            if first_seen is None:
                first_seen = row
    return first_seen


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


def track_spotify_url(track_title: str) -> str:
    """Per-track Spotify URL if known, else the artist URL as fallback."""
    url = TRACK_SPOTIFY_URLS.get(track_title.lower().strip(), "")
    return url if url else cfg.SPOTIFY_ARTIST_URL


def track_apple_music_url(track_title: str) -> str:
    """Per-track Apple Music URL if known, else the artist URL as fallback."""
    url = TRACK_APPLE_MUSIC_URLS.get(track_title.lower().strip(), "")
    return url if url else cfg.APPLE_MUSIC_URL


def _build_utm_spotify_url(track_title: str) -> str:
    """Spotify TRACK URL (or artist fallback) + UTM suffix."""
    base = track_spotify_url(track_title)
    return f"{base}{_utm_suffix(track_title)}"


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


def _primary_link_mode() -> str:
    """
    "spotify" (default) | "smart"

    Controls what build_smart_link returns. "spotify" is the 2026-04-22
    North-Star default: every CTA funnels straight to Spotify so the
    1M-monthly-listeners goal gets the full conversion. "smart" restores
    Feature.fm → Odesli → Spotify priority for rare cases where
    multi-DSP discovery matters more than Spotify funnel conversion.
    """
    return os.getenv("HOLYRAVE_PRIMARY_LINK", "spotify").strip().lower() or "spotify"


def build_smart_link(track_title: str) -> str:
    """
    Resolve the primary "listen now" link for a track.

    Default mode ("spotify", 2026-04-22 North-Star mandate): return the
    track-specific Spotify URL with UTM, or the artist Spotify URL as
    fallback. This is THE call-to-action everywhere the long-form
    pipeline shows a single link — description top line, pinned comment,
    end slate, etc. Every click converts directly on Spotify.

    Legacy "smart" mode (set HOLYRAVE_PRIMARY_LINK=smart):
      1. Feature.fm         — paid tier, tracked per-platform analytics
      2. Odesli/Songlink    — free, cross-DSP, routes to user's preferred DSP
      3. Track-specific Spotify URL + UTM — final fallback

    Why Spotify-direct by default: Odesli/Feature.fm landing pages offer
    a DSP-picker, which splits listener attention. ~40% of visitors pick
    Apple Music / YouTube Music / etc instead of Spotify. For a Spotify-
    first growth strategy (1M monthly listeners), that split is pure
    leakage. We still surface Apple Music as a SECONDARY link in the
    description for the minority who actively want it.
    """
    utm_spotify = _build_utm_spotify_url(track_title)

    mode = _primary_link_mode()
    if mode != "smart":
        # Default path: Spotify-direct, no aggregator middleman.
        return utm_spotify

    # Legacy smart-link behavior (opt-in via env var).
    track_sp_url = track_spotify_url(track_title)
    is_track_url = "/track/" in track_sp_url

    feature_url = _build_featurefm(track_title, utm_spotify)
    if feature_url:
        return feature_url

    if is_track_url:
        odesli_url = _build_odesli(track_sp_url)
        if odesli_url:
            return f"{odesli_url}{_utm_suffix(track_title)}"

    return utm_spotify
