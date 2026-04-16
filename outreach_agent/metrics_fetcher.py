#!/usr/bin/env python3.13
"""
⚠️  DEPRECATED — 2026-04-16 ⚠️
========================================================================
This module has been SUPERSEDED by content_engine/learning_loop.py,
which now does metrics fetch + weight recompute in a single daily pass
against the post registry at data/performance/*_posts.json (not the
legacy outreach_agent/content_log SQLite table).

Entry points that used to invoke THIS file now route to the new loop:
  - rjm.py learning {fetch,recompute,show,report}
  - scripts/run_agent.sh metrics-fetch|weights-learn|learning
  - launchd: com.rjm.viral-learning.plist (daily 18:00 CET)

The old launchd plist com.rjm.metrics-fetch has been unloaded and
renamed *.disabled. Do not re-enable it.

This file is kept only for historical reference. It is NOT part of the
active learning pipeline and should not be imported by new code.
========================================================================

metrics_fetcher.py — Pull performance metrics for recent Holy Rave content.

Reads content_log rows aged 24h–60d (via content_signal.get_posts_needing_metrics)
and writes the results to the content_metrics table so weights_learner.py can
correlate creative decisions (hook_mechanism, lead_category, bpm, clip_length)
with real performance.

Design choices:
  - Matches content_log rows to platform-native posts by publication window.
    This avoids needing Buffer to expose serviceId — we list the last N posts
    on each platform and pair each to the closest scheduled_at.
  - Instagram Graph API v21 → insights for plays/reach/saved/shares.
  - YouTube Data API v3 → statistics + contentDetails.duration. Analytics API
    (v2 reports) is a future upgrade when we have OAuth w/ yt-analytics scope.
  - TikTok has no retrieval API available for our account — skipped.
  - Every HTTP failure is logged but non-fatal: one bad post must not block
    the whole sweep. Errors are persisted in content_metrics.raw for debugging.

Schedule: run daily around 03:00 CET via launchd so posts published the prior
day have had at least 12 hours to accumulate watch-time + saves.

Usage:
  python3.13 metrics_fetcher.py           # fetch what's due
  python3.13 metrics_fetcher.py --dry-run # show matches, don't write
  python3.13 metrics_fetcher.py --all     # ignore already-fetched flag
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ─── Paths / env ─────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
PROJECT_DIR = BASE_DIR.parent

def _load_env():
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

import db
import content_signal

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  metrics_fetcher: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("metrics_fetcher")

# ─── Config ───────────────────────────────────────────────────────────────────

IG_GRAPH_BASE   = "https://graph.instagram.com/v21.0"
YT_DATA_BASE    = "https://www.googleapis.com/youtube/v3"
IG_MATCH_WINDOW = timedelta(hours=6)   # allow ±6h between scheduled_at and actual publish
YT_MATCH_WINDOW = timedelta(hours=6)


# ─── Token management ────────────────────────────────────────────────────────

def _ig_token() -> str:
    return os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()


def _ig_user_id() -> str:
    return os.environ.get("INSTAGRAM_USER_ID", "").strip()


def _yt_refresh_token() -> str:
    """Refresh YouTube OAuth using client_secret.json + stored refresh token."""
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
    if not refresh_token:
        return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")

    secret_path = PROJECT_DIR / "client_secret.json"
    if not secret_path.exists():
        return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")

    try:
        data  = json.loads(secret_path.read_text())
        creds = data.get("installed") or data.get("web") or {}
        resp  = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id":     creds.get("client_id", ""),
                "client_secret": creds.get("client_secret", ""),
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            },
            timeout=15,
        )
        token = resp.json().get("access_token", "")
        if token:
            log.info("YouTube OAuth token refreshed")
            return token
    except Exception as e:
        log.warning(f"YouTube token refresh failed: {e}")

    return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _parse_iso(s: str) -> datetime | None:
    """Parse an ISO timestamp from content_log into UTC-aware datetime."""
    if not s:
        return None
    # content_log stores naive UTC ISO ('2026-04-15T21:30:00') — assume UTC.
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _iso8601_duration_to_seconds(d: str) -> float:
    """Convert PT1M2S → 62.0. YouTube Data API returns ISO-8601 durations."""
    if not d or not d.startswith("PT"):
        return 0.0
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", d)
    if not m:
        return 0.0
    h = int(m.group(1) or 0)
    mn = int(m.group(2) or 0)
    s = float(m.group(3) or 0)
    return h * 3600 + mn * 60 + s


# ─── Instagram discovery ─────────────────────────────────────────────────────

def _list_recent_instagram(days: int = 60, limit: int = 50) -> list[dict]:
    """Fetch recent IG media (reels + posts) with timestamps for matching."""
    uid   = _ig_user_id()
    token = _ig_token()
    if not uid or not token:
        log.warning("Instagram credentials missing — skipping IG")
        return []

    out: list[dict] = []
    url = f"{IG_GRAPH_BASE}/{uid}/media"
    params = {
        "fields":       "id,media_type,media_product_type,caption,permalink,timestamp",
        "access_token": token,
        "limit":        limit,
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            log.warning(f"IG /media {resp.status_code}: {resp.text[:200]}")
            return []
        for item in resp.json().get("data", []):
            ts = item.get("timestamp")
            if not ts:
                continue
            try:
                # IG returns ISO with tz offset, e.g. 2026-04-15T09:30:00+0000
                if ts.endswith("+0000"):
                    ts = ts[:-5] + "+00:00"
                item["_dt"] = datetime.fromisoformat(ts)
            except ValueError:
                continue
            out.append(item)
    except requests.RequestException as e:
        log.warning(f"IG /media fetch failed: {e}")
    return out


def _fetch_instagram_insights(media_id: str, media_product_type: str) -> dict:
    """
    Pull insights for one IG media item.
    Returns dict with the fields content_metrics cares about.
    Reels metrics (v21): plays, reach, saved, shares, likes, comments, total_interactions.
    """
    token = _ig_token()
    if not token:
        return {}

    # Metric sets differ by media product type
    if media_product_type == "REELS":
        metrics = "plays,reach,saved,shares,likes,comments,total_interactions,ig_reels_avg_watch_time"
    elif media_product_type == "STORY":
        metrics = "reach,impressions,replies,shares,exits,taps_forward,taps_back"
    else:
        metrics = "reach,likes,comments,saved,shares"

    try:
        resp = requests.get(
            f"{IG_GRAPH_BASE}/{media_id}/insights",
            params={"metric": metrics, "access_token": token},
            timeout=30,
        )
        if resp.status_code != 200:
            return {"_error": resp.text[:300]}
        body   = resp.json()
        values = {d["name"]: (d.get("values") or [{}])[0].get("value") for d in body.get("data", [])}
        return values
    except requests.RequestException as e:
        return {"_error": str(e)}


# ─── YouTube discovery ───────────────────────────────────────────────────────

def _list_recent_youtube(token: str, limit: int = 25) -> list[dict]:
    """List recent videos on the authenticated channel with publishedAt timestamps."""
    if not token:
        return []

    out: list[dict] = []
    try:
        # 1. Get channel's uploads playlist ID
        resp = requests.get(
            f"{YT_DATA_BASE}/channels",
            params={"part": "contentDetails", "mine": "true"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code != 200:
            log.warning(f"YT /channels {resp.status_code}: {resp.text[:200]}")
            return []
        items = resp.json().get("items", [])
        if not items:
            return []
        uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # 2. Get items from uploads playlist
        resp = requests.get(
            f"{YT_DATA_BASE}/playlistItems",
            params={"part": "snippet,contentDetails", "playlistId": uploads_id, "maxResults": limit},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code != 200:
            log.warning(f"YT /playlistItems {resp.status_code}: {resp.text[:200]}")
            return []

        for pi in resp.json().get("items", []):
            snippet = pi.get("snippet", {})
            cd      = pi.get("contentDetails", {})
            vid_id  = cd.get("videoId")
            ts      = cd.get("videoPublishedAt") or snippet.get("publishedAt")
            if not (vid_id and ts):
                continue
            try:
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            out.append({
                "id":          vid_id,
                "_dt":         dt,
                "title":       snippet.get("title", ""),
                "description": snippet.get("description", ""),
            })
    except requests.RequestException as e:
        log.warning(f"YT uploads discovery failed: {e}")
    return out


def _fetch_youtube_stats(token: str, video_id: str) -> dict:
    """Fetch statistics (views, likes, comments) + duration for one video."""
    if not token or not video_id:
        return {}
    try:
        resp = requests.get(
            f"{YT_DATA_BASE}/videos",
            params={"part": "statistics,contentDetails", "id": video_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code != 200:
            return {"_error": resp.text[:300]}
        items = resp.json().get("items", [])
        if not items:
            return {}
        stats = items[0].get("statistics", {}) or {}
        cd    = items[0].get("contentDetails", {}) or {}
        return {
            "views":        int(stats.get("viewCount", 0) or 0),
            "likes":        int(stats.get("likeCount", 0) or 0),
            "comments":     int(stats.get("commentCount", 0) or 0),
            "duration_s":   _iso8601_duration_to_seconds(cd.get("duration", "")),
        }
    except requests.RequestException as e:
        return {"_error": str(e)}


# ─── Matching ────────────────────────────────────────────────────────────────

def _match_post(
    content_row: dict,
    candidates: list[dict],
    window: timedelta,
) -> dict | None:
    """
    Find the candidate whose publish time is closest to the content row's
    scheduled_at / posted_at, within `window`. Returns None if no match.
    """
    target = _parse_iso(content_row.get("scheduled_at") or content_row.get("posted_at"))
    if target is None:
        return None

    best = None
    best_delta = window
    for cand in candidates:
        cand_dt = cand.get("_dt")
        if not cand_dt:
            continue
        delta = abs(cand_dt - target)
        if delta <= best_delta:
            best_delta = delta
            best       = cand
    return best


# ─── Writer ──────────────────────────────────────────────────────────────────

def _already_fetched(buffer_id: str, platform: str) -> bool:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM content_metrics WHERE buffer_id = ? AND platform = ? LIMIT 1",
            (buffer_id, platform),
        ).fetchone()
    return row is not None


def _write_metrics_row(
    buffer_id: str,
    platform:  str,
    platform_post_id: str | None,
    metrics: dict,
    raw: dict,
) -> int:
    """Insert one row into content_metrics."""
    with db.get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO content_metrics
               (buffer_id, platform, fetched_at, platform_post_id,
                views, likes, comments, shares, saves, reach,
                completion_rate, avg_watch_s, follows_from, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                buffer_id,
                platform,
                datetime.utcnow().isoformat(),
                platform_post_id,
                metrics.get("views"),
                metrics.get("likes"),
                metrics.get("comments"),
                metrics.get("shares"),
                metrics.get("saves"),
                metrics.get("reach"),
                metrics.get("completion_rate"),
                metrics.get("avg_watch_s"),
                metrics.get("follows_from"),
                json.dumps(raw)[:10000],  # cap raw blob
            ),
        )
        return cursor.lastrowid


# ─── Main sweep ──────────────────────────────────────────────────────────────

def run(dry_run: bool = False, include_existing: bool = False) -> dict:
    """
    Single pass: pull metrics for every content_log row that's due.
    Returns a summary dict for the caller.
    """
    db.init_db()

    due_posts = content_signal.get_posts_needing_metrics(min_age_hours=24, max_age_days=60)
    log.info(f"{len(due_posts)} post(s) in the 24h–60d window")

    # Pre-fetch recent platform catalogues once (cheaper than per-post lookups)
    ig_recent   = _list_recent_instagram()
    yt_token    = _yt_refresh_token()
    yt_recent   = _list_recent_youtube(yt_token)
    log.info(f"IG recent={len(ig_recent)}  YT recent={len(yt_recent)}")

    summary = {
        "checked":       0,
        "matched":       0,
        "skipped_cache": 0,
        "no_match":      0,
        "errors":        0,
        "by_platform":   {},
    }

    for post in due_posts:
        buf_id   = post.get("buffer_id")
        platform = post.get("platform", "")
        if not buf_id:
            continue

        summary["checked"] += 1

        if not include_existing and _already_fetched(buf_id, platform):
            summary["skipped_cache"] += 1
            continue

        platform_post_id: str | None = None
        metrics: dict                = {}
        raw:     dict                = {"content_log_id": post.get("id"), "platform": platform}

        if platform == "instagram":
            match = _match_post(post, ig_recent, IG_MATCH_WINDOW)
            if not match:
                summary["no_match"] += 1
                log.info(f"IG  {buf_id}: no match in last {len(ig_recent)} media")
                continue
            platform_post_id = match["id"]
            mpt              = match.get("media_product_type", "FEED")
            raw["ig_media"]  = {k: match[k] for k in ("id", "permalink", "timestamp", "media_product_type") if k in match}

            ins = _fetch_instagram_insights(platform_post_id, mpt)
            raw["insights"] = ins
            if "_error" in ins:
                summary["errors"] += 1
                log.warning(f"IG {platform_post_id}: {ins['_error'][:120]}")
            else:
                plays       = ins.get("plays") or ins.get("reach") or 0
                reach       = ins.get("reach") or 0
                saves       = ins.get("saved") or 0
                shares      = ins.get("shares") or 0
                likes       = ins.get("likes") or 0
                comments    = ins.get("comments") or 0
                avg_watch_s = ins.get("ig_reels_avg_watch_time")
                avg_watch_s = float(avg_watch_s) / 1000.0 if isinstance(avg_watch_s, (int, float)) else None
                clip_length = post.get("clip_length") or 0
                completion  = (avg_watch_s / clip_length) if (avg_watch_s and clip_length) else None
                metrics = {
                    "views":           int(plays) if plays else None,
                    "likes":           int(likes) if likes else None,
                    "comments":        int(comments) if comments else None,
                    "shares":          int(shares) if shares else None,
                    "saves":           int(saves) if saves else None,
                    "reach":           int(reach) if reach else None,
                    "completion_rate": completion,
                    "avg_watch_s":     avg_watch_s,
                }

        elif platform == "youtube":
            match = _match_post(post, yt_recent, YT_MATCH_WINDOW)
            if not match:
                summary["no_match"] += 1
                log.info(f"YT  {buf_id}: no match in last {len(yt_recent)} videos")
                continue
            platform_post_id = match["id"]
            raw["yt_item"]   = {k: match[k] for k in ("id", "title") if k in match}

            stats = _fetch_youtube_stats(yt_token, platform_post_id)
            raw["stats"] = stats
            if "_error" in stats:
                summary["errors"] += 1
                log.warning(f"YT {platform_post_id}: {stats['_error'][:120]}")
            else:
                metrics = {
                    "views":    stats.get("views"),
                    "likes":    stats.get("likes"),
                    "comments": stats.get("comments"),
                    # YouTube Data API does not expose shares/saves/reach or
                    # watch-time to non-Analytics-API callers. Leave as NULL
                    # so weights_learner knows to use only what exists.
                }

        elif platform == "tiktok":
            # No retrieval API available for our account. Skipped entirely.
            continue
        else:
            continue

        if platform_post_id and not dry_run:
            try:
                row_id = _write_metrics_row(buf_id, platform, platform_post_id, metrics, raw)
                summary["matched"] += 1
                summary["by_platform"][platform] = summary["by_platform"].get(platform, 0) + 1
                log.info(
                    f"{platform[:2].upper()}  {buf_id} → {platform_post_id}  "
                    f"views={metrics.get('views')}  saves={metrics.get('saves')}  "
                    f"shares={metrics.get('shares')}  row={row_id}"
                )
            except Exception as e:
                summary["errors"] += 1
                log.error(f"write failed: {e}")
        elif platform_post_id:
            summary["matched"] += 1
            summary["by_platform"][platform] = summary["by_platform"].get(platform, 0) + 1
            log.info(f"[dry] {platform[:2].upper()} {buf_id} → {platform_post_id}  {metrics}")

    log.info(f"Sweep done: {summary}")
    return summary


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch metrics for recent Holy Rave content.")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to content_metrics.")
    parser.add_argument("--all",     action="store_true", help="Ignore cache, refetch everything.")
    args = parser.parse_args()

    summary = run(dry_run=args.dry_run, include_existing=args.all)
    # Non-zero exit only on hard failures (errors == checked means everything broke)
    if summary["checked"] > 0 and summary["errors"] >= summary["checked"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
