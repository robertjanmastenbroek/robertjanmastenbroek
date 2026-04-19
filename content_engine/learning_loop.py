"""
Module 5: Learning Loop
Runs daily at 18:00 CET (9 hours after morning posts).
Pulls performance metrics from Instagram Graph API + YouTube Analytics API.
Recalculates prompt_weights.json so tomorrow's generation biases toward what worked today.
Writes breakthrough analyses for any video hitting 2× rolling average.
"""
import json
import logging
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, date as _date
from pathlib import Path

import requests

PROJECT_DIR     = Path(__file__).parent.parent
PERFORMANCE_DIR = PROJECT_DIR / "data" / "performance"
LEARNING_DIR    = PROJECT_DIR / "learning"

logger = logging.getLogger(__name__)

# Use graph.facebook.com (business IG accounts go through the Facebook Graph API,
# not the deprecated graph.instagram.com subdomain). Fix re-applied from
# commit 8746606 after the 2026-04-15 full-rewrite of this module regressed it.
INSTAGRAM_GRAPH_BASE = "https://graph.facebook.com/v21.0"
YOUTUBE_ANALYTICS    = "https://youtubeanalytics.googleapis.com/v2/reports"
YOUTUBE_TOKEN_URL    = "https://oauth2.googleapis.com/token"

# How aggressively weights shift each day. 0 = no change, 1 = full replacement.
LEARNING_RATE = 0.3

# Buffer post IDs are 24-char lowercase hex strings (e.g. "69e1dc17bf79a8a2f2e4c743").
# Real IG media IDs are long numeric strings (e.g. "18179953573388740").
import re as _re
_BUFFER_ID_RE = _re.compile(r'^[0-9a-f]{24}$')


def _is_buffer_id(post_id: str) -> bool:
    """Return True if post_id looks like a Buffer internal ID (not a real IG media ID)."""
    return bool(_BUFFER_ID_RE.match(post_id.strip()))


def _refresh_youtube_token() -> str:
    """Refresh the YouTube OAuth access token using the refresh token in .env.

    Updates YOUTUBE_OAUTH_TOKEN in os.environ so the rest of this process
    uses the fresh token, and writes it back to .env so the next process
    also picks it up without manual intervention.
    Returns the new access token, or the existing one on failure.
    """
    refresh_token  = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
    client_id      = os.environ.get("YOUTUBE_CLIENT_ID", "")
    client_secret  = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
    if not (refresh_token and client_id and client_secret):
        logger.warning("[learning_loop] YouTube refresh: missing credentials in env — skipping")
        return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")
    try:
        resp = requests.post(
            YOUTUBE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            try:
                err = resp.json().get("error", "")
            except Exception:
                err = ""
            if err == "invalid_grant":
                logger.critical(
                    "[learning_loop] YouTube refresh token is expired or revoked — "
                    "YouTube analytics will be skipped this run. "
                    "Re-auth required: python3 scripts/setup_youtube_oauth.py"
                )
                return ""
            logger.warning(f"[learning_loop] YouTube token refresh failed: {resp.status_code} {resp.text}")
            return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")
        new_token = resp.json()["access_token"]
        os.environ["YOUTUBE_OAUTH_TOKEN"] = new_token
        # Persist to .env so future processes don't need an immediate re-auth
        _write_env_token("YOUTUBE_OAUTH_TOKEN", new_token)
        logger.info("[learning_loop] YouTube token refreshed successfully")
        return new_token
    except Exception as e:
        logger.warning(f"[learning_loop] YouTube token refresh error: {e}")
        return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")


def _write_env_token(key: str, value: str):
    """Overwrite a single key=value line in .env (always without quotes). Safe for tokens."""
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    new_lines = []
    replaced = False
    for line in lines:
        # Match KEY= (with or without surrounding quotes on the value side)
        if line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} ="):
            new_lines.append(f"{key}={value}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n")


def _refresh_instagram_token(access_token: str = "") -> str:
    """Refresh the long-lived Meta/Instagram User access token (60-day window).

    Uses the fb_exchange_token grant — correct for Business Login tokens (EAA*).
    Updates os.environ + .env so the refreshed token is available to subsequent
    code in this process and to the next process without operator intervention.
    Returns the refreshed token on success, or the original token on failure.
    """
    token = access_token or os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    if not token:
        return token
    app_id     = os.environ.get("META_APP_ID", "")
    app_secret = os.environ.get("META_APP_SECRET", "")
    if not (app_id and app_secret):
        return token  # can't refresh without app creds, but token may still be valid
    try:
        resp = requests.get(
            f"{INSTAGRAM_GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type":        "fb_exchange_token",
                "client_id":         app_id,
                "client_secret":     app_secret,
                "fb_exchange_token": token,
            },
            timeout=15,
        )
        new_token  = resp.json().get("access_token", "")
        expires_in = resp.json().get("expires_in", 0)
        if new_token:
            os.environ["INSTAGRAM_ACCESS_TOKEN"] = new_token
            _write_env_token("INSTAGRAM_ACCESS_TOKEN", new_token)
            logger.info(
                f"[learning_loop] Instagram token refreshed "
                f"(expires in {expires_in}s / ~{expires_in // 86400}d)"
            )
            return new_token
        err = resp.json().get("error", {})
        logger.warning(f"[learning_loop] Instagram token refresh failed: {err.get('message', resp.text[:200])}")
    except Exception as e:
        logger.warning(f"[learning_loop] Instagram token refresh error: {e}")
    return token


def _resolve_ig_media_ids(posts: list, access_token: str, user_id: str) -> list:
    """Replace Buffer post IDs with real IG media IDs.

    Paginates /{user_id}/media (following `next` cursors) until all Buffer IDs
    are resolved or posts older than 28 days are reached. The old single-page
    limit=50 fetch missed Buffer IDs from posts more than ~50 items ago.
    Posts that cannot be resolved are returned unchanged — the caller skips them.
    """
    from datetime import timedelta

    buffer_posts = [p for p in posts if _is_buffer_id(p.get("post_id", ""))]
    if not buffer_posts:
        return posts

    cutoff = datetime.now() - timedelta(days=28)
    media_by_ts = []  # list of (datetime, media_id)

    url = f"{INSTAGRAM_GRAPH_BASE}/{user_id}/media"
    params = {"fields": "id,timestamp", "limit": 50, "access_token": access_token}
    pages_fetched = 0

    while url:
        try:
            resp = requests.get(url, params=params, timeout=15)
            params = {}  # next_url has all params baked in
            if resp.status_code != 200:
                err = resp.json().get("error", {}).get("message", resp.text[:200])
                logger.warning(f"[learning_loop] IG media list page {pages_fetched}: {resp.status_code} — {err}")
                break
            data = resp.json()
            items = data.get("data", [])
            stop = False
            for m in items:
                try:
                    ts = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
                    if ts.replace(tzinfo=None) < cutoff.replace(tzinfo=None):
                        stop = True
                        break
                    media_by_ts.append((ts, m["id"]))
                except Exception:
                    pass
            if stop:
                break
            pages_fetched += 1
            url = data.get("paging", {}).get("next", "")
            if pages_fetched >= 10:  # safety cap: 10 × 50 = 500 items
                break
        except Exception as e:
            logger.warning(f"[learning_loop] IG media list error: {e}")
            break

    if not media_by_ts:
        return posts

    # For each Buffer post, find the IG media item closest in time
    resolved = {p["post_id"]: p["post_id"] for p in posts}
    used_media_ids: set = set()
    for p in sorted(buffer_posts, key=lambda x: x.get("posted_at", "")):
        posted_at_str = p.get("posted_at", "")
        if not posted_at_str:
            continue
        try:
            posted_dt = datetime.fromisoformat(posted_at_str.replace("Z", "+00:00"))
        except Exception:
            continue
        best_id, best_delta = None, None
        for ts, mid in media_by_ts:
            if mid in used_media_ids:
                continue
            delta = abs((ts - posted_dt).total_seconds())
            if delta <= 7200 and (best_delta is None or delta < best_delta):
                best_id, best_delta = mid, delta
        if best_id:
            resolved[p["post_id"]] = best_id
            used_media_ids.add(best_id)
            logger.info(f"[learning_loop] Resolved Buffer ID {p['post_id']} → IG media {best_id} (Δ{best_delta:.0f}s)")

    result = []
    for p in posts:
        orig_id = p.get("post_id", "")
        new_id = resolved.get(orig_id, orig_id)
        if new_id != orig_id:
            p = dict(p)
            p["post_id"] = new_id
        result.append(p)
    return result


def _resolve_ig_story_ids(posts: list, access_token: str, user_id: str) -> list:
    """Replace Buffer story IDs with real IG story media IDs.

    Stories are only live for 24h but the /stories endpoint lists them.
    """
    buffer_posts = [p for p in posts if _is_buffer_id(p.get("post_id", ""))]
    if not buffer_posts:
        return posts

    try:
        resp = requests.get(
            f"{INSTAGRAM_GRAPH_BASE}/{user_id}/stories",
            params={"fields": "id,timestamp", "access_token": access_token},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"[learning_loop] IG stories list failed: {resp.status_code}")
            return posts
        stories = resp.json().get("data", [])
    except Exception as e:
        logger.warning(f"[learning_loop] IG stories list error: {e}")
        return posts

    story_by_ts = []
    for s in stories:
        try:
            ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00"))
            story_by_ts.append((ts, s["id"]))
        except Exception:
            pass

    resolved = {p["post_id"]: p["post_id"] for p in posts}
    used_ids: set = set()
    for p in sorted(buffer_posts, key=lambda x: x.get("posted_at", "")):
        posted_at_str = p.get("posted_at", "")
        if not posted_at_str:
            continue
        try:
            posted_dt = datetime.fromisoformat(posted_at_str.replace("Z", "+00:00"))
        except Exception:
            continue
        best_id, best_delta = None, None
        for ts, sid in story_by_ts:
            if sid in used_ids:
                continue
            delta = abs((ts - posted_dt).total_seconds())
            if delta <= 7200 and (best_delta is None or delta < best_delta):
                best_id, best_delta = sid, delta
        if best_id:
            resolved[p["post_id"]] = best_id
            used_ids.add(best_id)
            logger.info(f"[learning_loop] Resolved Buffer story ID {p['post_id']} → IG story {best_id} (Δ{best_delta:.0f}s)")

    result = []
    for p in posts:
        orig_id = p.get("post_id", "")
        new_id = resolved.get(orig_id, orig_id)
        if new_id != orig_id:
            p = dict(p)
            p["post_id"] = new_id
        result.append(p)
    return result


def fetch_instagram_metrics(post_ids: list, access_token: str) -> list:
    """
    Fetch per-post insights from Instagram Graph API.
    post_ids: list of {post_id, clip_index, variant, hook_mechanism, visual_type, clip_length}
    Returns list of PerformanceRecord.
    """
    from content_engine.types import PerformanceRecord
    records = []

    for meta in post_ids:
        post_id = meta.get("post_id", "")
        if not post_id:
            continue
        # Buffer post IDs (24-char hex) that couldn't be resolved to real IG media
        # IDs will always return 400 from the Graph API. Skip them cleanly rather
        # than making a doomed request.
        if _is_buffer_id(post_id):
            logger.warning(f"[learning_loop] IG insights skipped — unresolved Buffer ID {post_id} (post not yet live or not matched)")
            continue
        try:
            # `plays` was removed in Graph API v22+. Use `views` only — Facebook
            # serves v22+ metric validation even on v21 requests, so including
            # `plays` alongside `views` causes a 400 for the whole call.
            resp = requests.get(
                f"{INSTAGRAM_GRAPH_BASE}/{post_id}/insights",
                params={
                    "metric": "views,reach,saved,shares,total_interactions",
                    "period": "lifetime",
                    "access_token": access_token,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                err = resp.json().get("error", {}).get("message", resp.text[:200])
                logger.warning(f"[learning_loop] IG insights {post_id}: {resp.status_code} — {err}")
                continue

            raw = {d["name"]: d.get("values", [{}])[0].get("value", 0)
                   for d in resp.json().get("data", [])}

            plays  = raw.get("views", 0) or 0
            reach  = raw.get("reach", 1)
            saved  = raw.get("saved", 0)
            shares = raw.get("shares", 0)

            records.append(PerformanceRecord(
                post_id=post_id,
                platform="instagram",
                clip_index=meta.get("clip_index", 0),
                variant=meta.get("variant", "a"),
                hook_mechanism=meta.get("hook_mechanism", "tension"),
                visual_type=meta.get("visual_type", "b_roll"),
                clip_length=meta.get("clip_length", 15),
                views=plays,
                completion_rate=0.0,             # IG doesn't expose completion %
                scroll_stop_rate=round(plays / max(reach, 1), 4),
                share_rate=round(shares / max(plays, 1), 4),
                save_rate=round(saved / max(plays, 1), 4),
                recorded_at=datetime.now().isoformat(),
            ))
        except Exception as e:
            logger.warning(f"[learning_loop] IG metrics error for {post_id}: {e}")

    return records


def fetch_facebook_metrics(post_ids: list, page_token: str) -> list:
    """
    Fetch per-post metrics from Facebook Graph API (feed videos + reels).
    post_ids: list of {post_id, clip_index, variant, hook_mechanism, visual_type, clip_length}
    Returns list of PerformanceRecord.

    Uses GET /{video_id}?fields=id,views,length directly on the video node.
    The /{post_id}/insights endpoint does NOT work for Reels — it raises
    "(#100) Tried accessing nonexisting field (insights)" for that node type.
    The `views` field on the video node is the canonical Reel view count and
    works for both Reels and regular uploaded Page videos.
    """
    from content_engine.types import PerformanceRecord
    records = []

    for meta in post_ids:
        post_id = meta.get("post_id", "")
        if not post_id:
            continue
        try:
            resp = requests.get(
                f"{INSTAGRAM_GRAPH_BASE}/{post_id}",
                params={
                    "fields": "id,views,length",
                    "access_token": page_token,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                err = resp.json().get("error", {}).get("message", resp.text[:200])
                logger.warning(f"[learning_loop] FB video {post_id}: {resp.status_code} — {err}")
                continue

            data = resp.json()
            views = int(data.get("views", 0) or 0)
            clip_length_raw = float(data.get("length", 0) or 0)
            clip_length = meta.get("clip_length", 0) or int(clip_length_raw) or 15

            records.append(PerformanceRecord(
                post_id=post_id,
                platform="facebook",
                clip_index=meta.get("clip_index", 0),
                variant=meta.get("variant", "a"),
                hook_mechanism=meta.get("hook_mechanism", "tension"),
                visual_type=meta.get("visual_type", "b_roll"),
                clip_length=clip_length,
                views=views,
                completion_rate=0.0,
                scroll_stop_rate=0.0,
                share_rate=0.0,
                save_rate=0.0,
                recorded_at=datetime.now().isoformat(),
            ))
        except Exception as e:
            logger.warning(f"[learning_loop] FB metrics error for {post_id}: {e}")

    return records


def fetch_facebook_metrics_bulk(
    page_token: str,
    page_id: str,
    days_back: int = 28,
    registry_lookup: dict = None,
) -> list:
    """Fetch analytics for ALL Facebook Reels on the page in the past N days.

    Uses /{page_id}/video_reels?fields=id,views,created_time,length — the only
    endpoint that reliably returns view counts for Reels without requiring the
    `read_insights` permission that most apps do not have.
    (/{reel_id}/insights → #100 error; /video_insights → #200 permission error.)

    Paginates until all reels are fetched or the cutoff date is reached.
    Cross-references registry_lookup to attach hook/format metadata where known.
    """
    from content_engine.types import PerformanceRecord
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=days_back)
    registry_lookup = registry_lookup or {}
    records = []

    # /videos returns both Reels and regular uploaded videos; /video_reels only
    # returns Reels. Use /videos so non-reel uploads are not silently skipped.
    url = f"{INSTAGRAM_GRAPH_BASE}/{page_id}/videos"
    params = {
        "fields": "id,views,created_time,length",
        "limit": 25,
        "access_token": page_token,
    }

    pages_fetched = 0
    while url:
        try:
            resp = requests.get(url, params=params, timeout=15)
            params = {}  # next_url has all params baked in
            if resp.status_code != 200:
                err = resp.json().get("error", {}).get("message", resp.text[:200])
                logger.warning(f"[learning_loop] FB bulk reels page {pages_fetched}: {resp.status_code} — {err}")
                break

            data = resp.json()
            items = data.get("data", [])
            stop = False

            for item in items:
                try:
                    ts = datetime.fromisoformat(item["created_time"].replace("Z", "+00:00"))
                    if ts.replace(tzinfo=None) < cutoff.replace(tzinfo=None):
                        stop = True
                        break
                except Exception:
                    pass

                post_id = item.get("id", "")
                if not post_id:
                    continue
                views = int(item.get("views", 0) or 0)
                clip_length_raw = float(item.get("length", 0) or 0)
                meta = registry_lookup.get(post_id, {})
                clip_length = meta.get("clip_length", 0) or int(clip_length_raw) or 15

                records.append(PerformanceRecord(
                    post_id=post_id,
                    platform="facebook",
                    clip_index=meta.get("clip_index", 0),
                    variant=meta.get("variant", "a"),
                    hook_mechanism=meta.get("hook_mechanism", "tension"),
                    visual_type=meta.get("visual_type", "b_roll"),
                    clip_length=clip_length,
                    views=views,
                    completion_rate=0.0,
                    scroll_stop_rate=0.0,
                    share_rate=0.0,
                    save_rate=0.0,
                    recorded_at=datetime.now().isoformat(),
                ))

            if stop:
                break

            pages_fetched += 1
            url = data.get("paging", {}).get("next", "")
            if pages_fetched >= 10:  # safety cap: 10 × 25 = 250 reels
                break

        except Exception as e:
            logger.warning(f"[learning_loop] FB bulk reels error: {e}")
            break

    logger.info(f"[learning_loop] FB bulk reels: {len(records)} reels in last {days_back}d")
    return records


def fetch_instagram_story_metrics(post_ids: list, access_token: str) -> list:
    """
    Fetch per-story insights from Instagram Graph API.
    Stories only live 24h — this only works same-day.

    Supported metrics as of Graph API v22+:
      reach, replies, views, navigation
    (impressions, taps_forward, taps_back, exits were removed in v22+)
    navigation = total skip/exit actions (forward + back + exit + next_story).
    """
    from content_engine.types import PerformanceRecord
    records = []

    for meta in post_ids:
        post_id = meta.get("post_id", "")
        if not post_id:
            continue
        if _is_buffer_id(post_id):
            logger.warning(f"[learning_loop] IG Story insights skipped — unresolved Buffer ID {post_id}")
            continue
        try:
            resp = requests.get(
                f"{INSTAGRAM_GRAPH_BASE}/{post_id}/insights",
                params={
                    "metric": "reach,replies,views,navigation",
                    "access_token": access_token,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                err = resp.json().get("error", {}).get("message", resp.text[:200])
                logger.warning(f"[learning_loop] IG Story insights {post_id}: {resp.status_code} — {err}")
                continue

            raw = {d["name"]: d.get("values", [{}])[0].get("value", 0)
                   for d in resp.json().get("data", [])}

            reach      = raw.get("reach", 1)
            views      = raw.get("views", 1)
            replies    = raw.get("replies", 0)
            navigation = raw.get("navigation", 0)  # total skip/exit actions

            # Completion ≈ 1 - (navigation / views). navigation includes all
            # skip/exit interactions; dividing by views gives a skip rate, and
            # clamping keeps the signal in [0, 1].
            skip_rate       = min(1.0, navigation / max(views, 1))
            completion_rate = round(1.0 - skip_rate, 4)

            records.append(PerformanceRecord(
                post_id=post_id,
                platform="instagram_story",
                clip_index=meta.get("clip_index", 0),
                variant=meta.get("variant", "a"),
                hook_mechanism=meta.get("hook_mechanism", "tension"),
                visual_type=meta.get("visual_type", "b_roll"),
                clip_length=meta.get("clip_length", 15),
                views=int(views),
                completion_rate=completion_rate,
                scroll_stop_rate=round(views / max(reach, 1), 4),
                share_rate=round(replies / max(views, 1), 4),
                save_rate=0.0,
                recorded_at=datetime.now().isoformat(),
            ))
        except Exception as e:
            logger.warning(f"[learning_loop] IG Story metrics error for {post_id}: {e}")

    return records


def fetch_youtube_metrics(post_ids: list, oauth_token: str) -> list:
    """
    Fetch YouTube video analytics for a known list of video IDs.
    Uses a 28-day window so recently-posted videos (where today-only has no data yet)
    still return cumulative metrics. Falls back to the bulk channel approach when
    no specific IDs are provided.
    post_ids: list of {post_id (video_id), clip_index, variant, ...}
    """
    from content_engine.types import PerformanceRecord
    records = []

    # Build a lookup for per-video metadata from the registry
    meta_by_id = {m.get("post_id", ""): m for m in post_ids if m.get("post_id")}

    # Batch all video IDs into a single Analytics call with a 28-day window.
    # YouTube Analytics data takes 24-72h to propagate, so today-only always
    # returns empty rows for same-day posts. A 28-day window covers all cases.
    video_ids = [vid for vid in meta_by_id if vid]
    if not video_ids:
        return records

    start_date = (_date.today().replace(day=1)
                  if _date.today().day <= 28
                  else _date.today()).isoformat()
    # Always go back 28 days to capture full propagation window
    from datetime import timedelta
    start_date = (_date.today() - timedelta(days=28)).isoformat()
    end_date   = _date.today().isoformat()

    try:
        # Query all video IDs at once using a comma-joined filter
        video_filter = ",".join(video_ids)
        resp = requests.get(
            YOUTUBE_ANALYTICS,
            params={
                "ids":        "channel==MINE",
                "metrics":    "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
                "filters":    f"video=={video_filter}",
                "startDate":  start_date,
                "endDate":    end_date,
                "dimensions": "video",
                "maxResults": 200,
            },
            headers={"Authorization": f"Bearer {oauth_token}"},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"[learning_loop] YT analytics batch: {resp.status_code} — {resp.text[:200]}")
            return records

        rows = resp.json().get("rows", [])
        logger.info(f"[learning_loop] YT batch query: {len(video_ids)} IDs → {len(rows)} rows")

        for row in rows:
            video_id, views, _, avg_dur, avg_pct = row
            meta = meta_by_id.get(video_id, {})
            clip_length = meta.get("clip_length", int(avg_dur) if avg_dur else 15)
            records.append(PerformanceRecord(
                post_id=video_id,
                platform="youtube",
                clip_index=meta.get("clip_index", 0),
                variant=meta.get("variant", "a"),
                hook_mechanism=meta.get("hook_mechanism", "tension"),
                visual_type=meta.get("visual_type", "b_roll"),
                clip_length=clip_length,
                views=int(views),
                completion_rate=round(float(avg_pct) / 100, 4),
                scroll_stop_rate=0.0,
                share_rate=0.0,
                save_rate=0.0,
                recorded_at=datetime.now().isoformat(),
            ))
    except Exception as e:
        logger.warning(f"[learning_loop] YT metrics batch error: {e}")

    return records


def fetch_youtube_channel_metrics_bulk(
    oauth_token: str,
    days_back: int = 28,
    registry_lookup: dict = None,
) -> list:
    """
    Fetch YouTube analytics for ALL channel videos in the past N days.

    Uses dimensions=video with no filter — the API returns every video that had
    at least 1 view. Cross-references registry_lookup (post_id→registry entry)
    to attach hook/format/track metadata where available.

    This is the preferred function for the daily learning run because it does not
    require knowing video IDs in advance and uses a wide date window that works
    even when same-day data has not yet propagated.
    """
    from content_engine.types import PerformanceRecord
    from datetime import timedelta

    start_date = (_date.today() - timedelta(days=days_back)).isoformat()
    end_date   = _date.today().isoformat()
    registry_lookup = registry_lookup or {}
    records = []

    try:
        resp = requests.get(
            YOUTUBE_ANALYTICS,
            params={
                "ids":        "channel==MINE",
                "metrics":    "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
                "startDate":  start_date,
                "endDate":    end_date,
                "dimensions": "video",
                "sort":       "-views",
                "maxResults": 200,
            },
            headers={"Authorization": f"Bearer {oauth_token}"},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"[learning_loop] YT channel bulk: {resp.status_code} — {resp.text[:200]}")
            return records

        rows = resp.json().get("rows", [])
        logger.info(f"[learning_loop] YT channel bulk: {len(rows)} videos with data in last {days_back}d")

        for row in rows:
            video_id, views, _, avg_dur, avg_pct = row
            meta = registry_lookup.get(video_id, {})
            clip_length = meta.get("clip_length", int(avg_dur) if avg_dur else 15)
            records.append(PerformanceRecord(
                post_id=video_id,
                platform="youtube",
                clip_index=meta.get("clip_index", 0),
                variant=meta.get("variant", "a"),
                hook_mechanism=meta.get("hook_mechanism", "tension"),
                visual_type=meta.get("visual_type", "b_roll"),
                clip_length=clip_length,
                views=int(views),
                completion_rate=round(float(avg_pct) / 100, 4),
                scroll_stop_rate=0.0,
                share_rate=0.0,
                save_rate=0.0,
                recorded_at=datetime.now().isoformat(),
            ))
    except Exception as e:
        logger.warning(f"[learning_loop] YT channel bulk error: {e}")

    return records


def calculate_new_weights(records: list, old_weights) -> "PromptWeights":
    """
    Recalculate prompt weights based on per-record performance signal.
    Signal = completion_rate * 0.5 + save_rate * 0.3 + scroll_stop_rate * 0.2
    Uses EMA: new_weight = old * (1 - LR) + signal_normalized * LR
    """
    from content_engine.types import PromptWeights

    hook_scores:     defaultdict = defaultdict(list)
    visual_scores:   defaultdict = defaultdict(list)
    platform_scores: defaultdict = defaultdict(list)
    length_scores:   defaultdict = defaultdict(list)

    for r in records:
        signal = r.completion_rate * 0.5 + r.save_rate * 0.3 + r.scroll_stop_rate * 0.2
        hook_scores[r.hook_mechanism].append(signal)
        visual_scores[r.visual_type].append(signal)
        platform_scores[r.platform].append(signal)
        length_scores[r.clip_length].append(signal)

    def _avg(d: defaultdict, key) -> float:
        vals = d.get(key, [])
        return sum(vals) / len(vals) if vals else 0.0

    def _update(old_dict: dict, scores: defaultdict) -> dict:
        if not scores:
            return old_dict
        all_avgs = [sum(v) / len(v) for v in scores.values() if v]
        max_signal = max(all_avgs) if all_avgs else 1.0
        if max_signal == 0:
            return old_dict
        new = {}
        for k in old_dict:
            avg = _avg(scores, k)
            if avg > 0:
                normalized = avg / max_signal * 2.0  # 0-2 range
                new[k] = round(old_dict[k] * (1 - LEARNING_RATE) + normalized * LEARNING_RATE, 3)
            else:
                new[k] = old_dict[k]
        return new

    best_platform = (max(platform_scores, key=lambda k: _avg(platform_scores, k))
                     if platform_scores else old_weights.best_platform)
    best_length   = (max(length_scores,   key=lambda k: _avg(length_scores, k))
                     if length_scores   else old_weights.best_clip_length)

    return PromptWeights(
        hook_weights=_update(old_weights.hook_weights, hook_scores),
        visual_weights=_update(old_weights.visual_weights, visual_scores),
        best_clip_length=int(best_length),
        best_platform=best_platform,
        updated=datetime.now().isoformat(),
    )


def detect_outliers(records: list, min_views: int = 500, max_outliers: int = 5) -> list:
    """Return the top short-form outliers — videos exceeding 2× rolling average.

    min_views guards against low-sample noise: a 100-view video being "2× avg"
    on a channel with mostly 50-view videos is not a meaningful breakthrough.
    max_outliers caps Claude CLI subprocess calls per run so the loop stays fast.
    Only considers short-form content (clip_length ≤ 90s) to exclude long-form
    videos that skew the average and inflate outlier counts.
    """
    if not records:
        return []
    # Scope to short-form only so long-form videos don't dominate the average
    short_form = [r for r in records if getattr(r, "clip_length", 0) <= 90]
    pool = short_form if short_form else records
    if not pool:
        return []
    avg = sum(r.views for r in pool) / len(pool)
    threshold = avg * 2
    outliers = [r for r in pool if r.views >= min_views and r.views > threshold]
    # Return highest-view outliers up to max_outliers
    outliers.sort(key=lambda r: r.views, reverse=True)
    return outliers[:max_outliers]


def _write_breakthrough(outlier, date_str: str):
    """Claude CLI analysis of a viral breakthrough. Saves to learning/breakthroughs/."""
    from content_engine.trend_scanner import _find_claude
    claude = os.environ.get("CLAUDE_CLI_PATH", "") or _find_claude()
    prompt = (
        f"A short-form video went viral today (2x+ average views):\n"
        f"Platform: {outlier.platform}\n"
        f"Views: {outlier.views:,}\n"
        f"Completion rate: {outlier.completion_rate:.1%}\n"
        f"Save rate: {outlier.save_rate:.1%}\n"
        f"Scroll-stop rate: {outlier.scroll_stop_rate:.1%}\n"
        f"Hook mechanism: {outlier.hook_mechanism}\n"
        f"Visual type: {outlier.visual_type}\n"
        f"Clip length: {outlier.clip_length}s\n\n"
        "In 3-5 bullet points, explain what likely made this work and exactly what to repeat "
        "tomorrow. Be specific. Focus on the combination of hook + visual + length that drove "
        "completion and saves. No filler."
    )
    try:
        result = subprocess.run(
            [claude, "--print", "--model", "claude-haiku-4-5-20251001",
             "--no-session-persistence",
             "--system-prompt", "You are a music marketing analyst. Be specific and concise.",
             prompt],
            capture_output=True, text=True, timeout=60,
            cwd="/tmp",  # avoid loading project CLAUDE.md
        )
        analysis = result.stdout.strip()
    except Exception as e:
        analysis = f"[analysis failed: {e}]"

    out_dir = LEARNING_DIR / "breakthroughs"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{date_str}_{outlier.platform}_{outlier.post_id[:8]}.md"
    (out_dir / filename).write_text(
        f"# Breakthrough: {outlier.platform.title()} — {date_str}\n\n"
        f"**Views:** {outlier.views:,}  "
        f"**Completion:** {outlier.completion_rate:.1%}  "
        f"**Saves:** {outlier.save_rate:.1%}  "
        f"**Hook:** {outlier.hook_mechanism}  "
        f"**Visual:** {outlier.visual_type}  "
        f"**Length:** {outlier.clip_length}s\n\n"
        f"{analysis}\n"
    )
    logger.info(f"[learning_loop] Breakthrough saved: {filename}")


def _save_performance_log(records: list, date_str: str):
    PERFORMANCE_DIR.mkdir(parents=True, exist_ok=True)
    (PERFORMANCE_DIR / f"{date_str}.json").write_text(
        json.dumps([r.__dict__ for r in records], indent=2)
    )


def _build_registry_lookup(days_back: int = 28) -> dict:
    """Build a {post_id: registry_entry} lookup from all post registry files in the last N days.

    Used to attach hook/format/track metadata to bulk-fetched analytics rows
    where the analytics API returns a video ID but no associated metadata.
    """
    from datetime import timedelta
    cutoff = _date.today() - timedelta(days=days_back)
    lookup = {}
    for f in sorted(PERFORMANCE_DIR.glob("*_posts.json")):
        # Extract date from filename like "2026-04-16_posts.json"
        try:
            file_date = _date.fromisoformat(f.stem.split("_posts")[0])
            if file_date < cutoff:
                continue
        except Exception:
            pass
        try:
            posts = json.loads(f.read_text())
            for p in posts:
                pid = p.get("post_id", "")
                if pid and p.get("success"):
                    lookup.setdefault(pid, p)
        except Exception:
            pass
    return lookup


def backfill(days_back: int = 28) -> dict:
    """Pull full analytics history for all channel videos in the past N days.

    Fetches:
    - YouTube Analytics for ALL channel videos (no ID filter needed)
    - Instagram media list + per-post insights for all posts in registries
    - Facebook Reels via bulk page endpoint (/{page_id}/video_reels)
    - Cross-references all post registries for metadata enrichment

    Writes enriched performance records to data/performance/backfill_YYYY-MM-DD.json
    and recalculates unified weights from the full dataset.

    Returns summary dict.
    """
    from content_engine.types import UnifiedWeights

    date_str = _date.today().isoformat()
    logger.info(f"[learning_loop] Starting backfill for last {days_back}d…")

    registry_lookup = _build_registry_lookup(days_back=days_back)
    logger.info(f"[learning_loop] Registry lookup: {len(registry_lookup)} known posts")

    # Refresh tokens
    raw_ig_token  = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    ig_token      = _refresh_instagram_token(raw_ig_token)
    ig_user_id    = os.environ.get("INSTAGRAM_USER_ID", "").strip('"').strip("'")
    fb_page_token = os.environ.get("FACEBOOK_PAGE_TOKEN", "").strip('"').strip("'")
    fb_page_id    = os.environ.get("FACEBOOK_PAGE_ID", "").strip('"').strip("'")
    yt_token      = _refresh_youtube_token()

    all_records = []

    # --- YouTube: bulk channel-wide pull ---
    if yt_token:
        yt_records = fetch_youtube_channel_metrics_bulk(yt_token, days_back=days_back, registry_lookup=registry_lookup)
        all_records += yt_records
        logger.info(f"[learning_loop] Backfill YT: {len(yt_records)} records")

    # --- Instagram: fetch all posts from registries ---
    if ig_token and ig_user_id:
        ig_metas = [v for v in registry_lookup.values() if v.get("platform") == "instagram"]
        ig_story_metas = [v for v in registry_lookup.values() if v.get("platform") == "instagram_story"]

        if ig_metas:
            ig_metas = _resolve_ig_media_ids(ig_metas, ig_token, ig_user_id)
            ig_records = fetch_instagram_metrics(ig_metas, ig_token)
            all_records += ig_records
            logger.info(f"[learning_loop] Backfill IG: {len(ig_records)} records from {len(ig_metas)} posts")

        if ig_story_metas:
            ig_story_metas = _resolve_ig_story_ids(ig_story_metas, ig_token, ig_user_id)
            story_records = fetch_instagram_story_metrics(ig_story_metas, ig_token)
            all_records += story_records
            logger.info(f"[learning_loop] Backfill IG Stories: {len(story_records)} records")

    # --- Facebook: bulk reel pull ---
    if fb_page_token and fb_page_id:
        fb_records = fetch_facebook_metrics_bulk(fb_page_token, fb_page_id, days_back=days_back, registry_lookup=registry_lookup)
        all_records += fb_records
        logger.info(f"[learning_loop] Backfill FB: {len(fb_records)} records")

    if not all_records:
        logger.warning("[learning_loop] Backfill: no records collected")
        return {"records": 0, "status": "no_data"}

    # Save backfill snapshot
    PERFORMANCE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PERFORMANCE_DIR / f"backfill_{date_str}.json"
    out_path.write_text(json.dumps([r.__dict__ for r in all_records], indent=2))
    logger.info(f"[learning_loop] Backfill saved: {len(all_records)} records → {out_path.name}")

    # Recalculate weights from full dataset
    records_as_dicts = []
    for r in all_records:
        base = r.__dict__.copy()
        extra = registry_lookup.get(r.post_id, {})
        for k, v in extra.items():
            base.setdefault(k, v)
        records_as_dicts.append(base)

    old_weights = UnifiedWeights.load()
    new_weights = calculate_unified_weights(records_as_dicts, old_weights)
    new_weights.save()

    top_hook   = max(new_weights.hook_weights,   key=new_weights.hook_weights.get)   if new_weights.hook_weights   else "n/a"
    top_format = max(new_weights.format_weights, key=new_weights.format_weights.get) if new_weights.format_weights else "n/a"

    logger.info(
        f"[learning_loop] Backfill weights: platform={new_weights.best_platform}, "
        f"length={new_weights.best_clip_length}s, hook={top_hook}, format={top_format}"
    )

    return {
        "records":        len(all_records),
        "yt_videos":      len([r for r in all_records if r.platform == "youtube"]),
        "ig_posts":       len([r for r in all_records if r.platform == "instagram"]),
        "ig_stories":     len([r for r in all_records if r.platform == "instagram_story"]),
        "fb_reels":       len([r for r in all_records if r.platform == "facebook"]),
        "best_platform":  new_weights.best_platform,
        "best_clip_length": new_weights.best_clip_length,
        "top_hook":       top_hook,
        "top_format":     top_format,
        "saved_to":       str(out_path),
        "updated":        new_weights.updated,
    }


def run(date_str: str = None, post_registry: list = None) -> "UnifiedWeights":
    """
    Full learning loop run.
    Reads post_registry from data/performance/YYYY-MM-DD_posts.json if not provided.
    Returns updated UnifiedWeights.

    Pulls IG + YT metrics (the platforms with native insights APIs); FB/TikTok/Stories
    records flow in through other writers (buffer_poster feedback, manual imports).
    All platforms in the registry participate in the multi-dimensional weight update,
    as long as the registry entries carry completion/save/scroll-stop signal.
    """
    from content_engine.types import UnifiedWeights

    if date_str is None:
        date_str = _date.today().isoformat()

    registry_path = PERFORMANCE_DIR / f"{date_str}_posts.json"
    if post_registry is None:
        if registry_path.exists():
            post_registry = json.loads(registry_path.read_text())
        else:
            # Also check the dry-run subdirectory (pipeline may have been run in
            # --dry-run mode while still producing a valid registry for reference).
            dry_run_path = PERFORMANCE_DIR / "dry-run" / f"{date_str}_posts.json"
            if dry_run_path.exists():
                post_registry = json.loads(dry_run_path.read_text())
                logger.info(f"[learning_loop] Using dry-run registry: {dry_run_path.name}")
            else:
                logger.warning("[learning_loop] No post registry for today — continuing with bulk YT fetch only")
                post_registry = []

    yt_posts = [p for p in post_registry if p.get("platform") == "youtube"]

    # Refresh tokens first — everything below depends on them.
    raw_ig_token  = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    ig_token      = _refresh_instagram_token(raw_ig_token)
    ig_user_id    = os.environ.get("INSTAGRAM_USER_ID", "").strip('"').strip("'")
    fb_page_token = os.environ.get("FACEBOOK_PAGE_TOKEN", "").strip('"').strip("'")
    fb_page_id    = os.environ.get("FACEBOOK_PAGE_ID", "").strip('"').strip("'")
    yt_token      = _refresh_youtube_token()

    # Build a channel-wide registry lookup (all post registries, last 28 days)
    # so bulk fetches can be annotated with hook/format/track metadata.
    all_registry_lookup = _build_registry_lookup()
    for p in post_registry:
        pid = p.get("post_id", "")
        if pid:
            all_registry_lookup.setdefault(pid, p)

    # IG: pull all 28-day registry posts, not just today's — mirrors what the
    # YT bulk fetch does. This ensures historical reels are tracked daily.
    ig_posts = []
    seen_ig: set = set()
    for pid, p in all_registry_lookup.items():
        if p.get("platform") == "instagram" and pid and pid not in seen_ig:
            ig_posts.append(p)
            seen_ig.add(pid)

    ig_story_posts = [p for p in post_registry if p.get("platform") == "instagram_story"]

    # Resolve Buffer post IDs to real IG media IDs before querying insights.
    # Pagination now covers up to 28 days back so older Buffer IDs are resolved.
    if ig_token and ig_user_id:
        if ig_posts:
            ig_posts = _resolve_ig_media_ids(ig_posts, ig_token, ig_user_id)
        if ig_story_posts:
            ig_story_posts = _resolve_ig_story_ids(ig_story_posts, ig_token, ig_user_id)

    records = []
    if ig_posts and ig_token:
        ig_records = fetch_instagram_metrics(ig_posts, ig_token)
        records += ig_records
        logger.info(f"[learning_loop] IG: {len(ig_posts)} posts → {len(ig_records)} records")
    if yt_token:
        yt_records = fetch_youtube_channel_metrics_bulk(yt_token, days_back=28, registry_lookup=all_registry_lookup)
        if not yt_records and yt_posts:
            logger.info("[learning_loop] YT bulk returned 0 — falling back to per-ID query")
            yt_records = fetch_youtube_metrics(yt_posts, yt_token)
        records += yt_records
        logger.info(f"[learning_loop] YouTube: {len(yt_records)} records (channel-wide 28d)")
    if fb_page_token and fb_page_id:
        # Bulk reel fetch (like YT channel bulk) — gets all page reels in 28d.
        # Per-ID fallback handles any registry posts not returned by the bulk.
        fb_records = fetch_facebook_metrics_bulk(fb_page_token, fb_page_id, days_back=28, registry_lookup=all_registry_lookup)
        bulk_ids = {r.post_id for r in fb_records}
        fb_today = [p for p in post_registry if p.get("platform") == "facebook" and p.get("post_id") and p["post_id"] not in bulk_ids]
        if fb_today:
            fb_records += fetch_facebook_metrics(fb_today, fb_page_token)
        records += fb_records
        logger.info(f"[learning_loop] Facebook: {len(fb_records)} records (28d bulk + {len(fb_today)} per-ID)")
    elif fb_page_token:
        # page_id missing — fall back to per-ID only for today's posts
        fb_today = [p for p in post_registry if p.get("platform") == "facebook"]
        if fb_today:
            fb_records = fetch_facebook_metrics(fb_today, fb_page_token)
            records += fb_records
            logger.info(f"[learning_loop] Facebook: {len(fb_records)} records (per-ID fallback, no page_id)")
    if ig_story_posts and ig_token:
        story_records = fetch_instagram_story_metrics(ig_story_posts, ig_token)
        records += story_records
        logger.info(f"[learning_loop] IG Stories: {len(ig_story_posts)} posts → {len(story_records)} records")

    if not records:
        logger.warning("[learning_loop] No performance records collected — weights unchanged")
        return UnifiedWeights.load()

    _save_performance_log(records, date_str)

    # Hydrate records with full metadata (format, template, track, etc.) from both
    # today's registry and the channel-wide 28-day lookup so the multi-dimensional
    # EMA has something to group on even for historical YT videos.
    records_as_dicts = []
    for r in records:
        base = r.__dict__.copy()
        extra = all_registry_lookup.get(r.post_id, {})
        for k, v in extra.items():
            base.setdefault(k, v)
        records_as_dicts.append(base)

    old_weights = UnifiedWeights.load()
    new_weights = calculate_unified_weights(records_as_dicts, old_weights)
    new_weights.save()

    top_hook = max(new_weights.hook_weights, key=new_weights.hook_weights.get) if new_weights.hook_weights else "n/a"
    top_format = max(new_weights.format_weights, key=new_weights.format_weights.get) if new_weights.format_weights else "n/a"
    logger.info(f"[learning_loop] Weights updated — best_platform={new_weights.best_platform}, "
                f"length={new_weights.best_clip_length}s, "
                f"top hook={top_hook}, top format={top_format}")

    outliers = detect_outliers(records)
    for o in outliers:
        logger.info(f"[learning_loop] Breakthrough: {o.platform} {o.views:,} views — analysing")
        _write_breakthrough(o, date_str)

    return new_weights


# ---------------------------------------------------------------------------
# Multi-dimensional learning (unified pipeline)
# ---------------------------------------------------------------------------

def calculate_unified_weights(
    records: list,
    old_weights: "UnifiedWeights",
    learning_rate: float = 0.3,
) -> "UnifiedWeights":
    """Calculate new unified weights from today's performance records.

    Updates per: format, platform, template, visual, track, transitional category.
    Signal = completion_rate * 0.5 + save_rate * 0.3 + scroll_stop_rate * 0.2
    """
    from content_engine.types import UnifiedWeights

    new = UnifiedWeights(
        hook_weights=dict(old_weights.hook_weights),
        visual_weights=dict(old_weights.visual_weights),
        format_weights=dict(old_weights.format_weights),
        platform_weights=dict(old_weights.platform_weights),
        transitional_category_weights=dict(old_weights.transitional_category_weights),
        track_weights=dict(old_weights.track_weights),
        best_clip_length=old_weights.best_clip_length,
        best_platform=old_weights.best_platform,
        updated=datetime.now().isoformat(),
        sub_mode_weights=dict(getattr(old_weights, "sub_mode_weights", {}) or {}),
        time_of_day_weights=dict(getattr(old_weights, "time_of_day_weights", {}) or {}),
        best_time_of_day=getattr(old_weights, "best_time_of_day", "morning"),
    )

    if not records:
        return new

    # Compute signal per record + derive time-of-day bucket from posted_at
    signals = []
    for r in records:
        signal = (
            r.get("completion_rate", 0) * 0.5
            + r.get("save_rate", 0) * 0.3
            + r.get("scroll_stop_rate", 0) * 0.2
        )
        # Derive time-of-day bucket from posted_at or recorded_at.
        # morning: 05-11, midday: 11-15, evening: 15-21, late: 21-05
        bucket = _derive_time_of_day(r)
        if bucket:
            r["time_of_day"] = bucket
        signals.append((r, signal))

    # Group signals by dimension and update via EMA
    _ema_update(new.format_weights, signals, "format_type", learning_rate)
    _ema_update(new.platform_weights, signals, "platform", learning_rate)
    _ema_update(new.hook_weights, signals, "hook_template_id", learning_rate)
    _ema_update(new.visual_weights, signals, "visual_type", learning_rate)
    _ema_update(new.track_weights, signals, "track_title", learning_rate)
    _ema_update(new.transitional_category_weights, signals, "transitional_category", learning_rate)
    # New dimensions: sub_mode (emotional register) + time_of_day (posting slot)
    _ema_update(new.sub_mode_weights, signals, "hook_sub_mode", learning_rate)
    _ema_update(new.time_of_day_weights, signals, "time_of_day", learning_rate)

    # Update best_platform + best_time_of_day.
    # Only consider platforms observed in this run — platforms with no analytics
    # data (e.g. TikTok, which has no native API integration) keep their prior
    # EMA weight but must not override platforms we can actually measure.
    active_platforms = {r.get("platform") for r, _ in signals if r.get("platform")}
    if active_platforms:
        scored = {k: v for k, v in new.platform_weights.items() if k in active_platforms}
        if scored:
            new.best_platform = max(scored, key=scored.get)
    elif new.platform_weights:
        new.best_platform = max(new.platform_weights, key=new.platform_weights.get)
    if new.time_of_day_weights:
        new.best_time_of_day = max(new.time_of_day_weights, key=new.time_of_day_weights.get)

    return new


def _derive_time_of_day(record: dict) -> str:
    """Bucket the record's posting time into morning/midday/evening/late.

    Checks `posted_at` then `recorded_at`. Returns "" if neither parseable —
    which keeps the record out of the time_of_day EMA (correct behavior).
    """
    ts = record.get("posted_at") or record.get("recorded_at") or ""
    if not ts:
        return ""
    try:
        # Accept both 2026-04-16T09:30:00 and 2026-04-16T09:30:00+00:00 shapes
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        h = dt.hour
    except Exception:
        return ""
    if 5 <= h < 11:
        return "morning"
    if 11 <= h < 15:
        return "midday"
    if 15 <= h < 21:
        return "evening"
    return "late"


def _ema_update(weights: dict, signals: list, key: str, lr: float):
    """EMA update for a single dimension."""
    group_signals = defaultdict(list)
    for record, signal in signals:
        val = record.get(key, "")
        if val:
            group_signals[val].append(signal)

    for name, sigs in group_signals.items():
        avg_signal = sum(sigs) / len(sigs)
        # Normalize to 0-2 range (assuming signal is 0-1, multiply by 2)
        normalized = min(avg_signal * 2.0, 2.0)
        old = weights.get(name, 1.0)
        weights[name] = old * (1 - lr) + normalized * lr


def track_rotation_vote(
    pool: list,
    new_release: dict = None,
    min_days: int = 7,
) -> dict:
    """Vote on track rotation.

    Composite score: spotify_popularity * 0.004 + video_save_rate * 0.6
    (popularity is 0-100, scale 0.004 keeps it at most 0.4)
    """
    if not pool:
        return {"action": "keep", "reason": "pool empty"}

    scored = []
    for t in pool:
        score = t.get("spotify_popularity", 0) * 0.004 + t.get("video_save_rate", 0) * 0.6
        scored.append((t["title"], score))
    scored.sort(key=lambda x: x[1])
    bottom = scored[0]

    if new_release:
        new_score = new_release.get("spotify_popularity", 0) * 0.004 + new_release.get("video_save_rate", 0) * 0.6
        if new_score > bottom[1]:
            return {
                "action": "swap",
                "remove": bottom[0],
                "add": new_release["title"],
                "reason": f"{new_release['title']} ({new_score:.3f}) > {bottom[0]} ({bottom[1]:.3f})",
            }
        return {"action": "keep", "reason": f"{new_release['title']} score too low"}

    if len(pool) < 4:
        return {"action": "add", "reason": "pool below minimum"}

    return {"action": "keep", "reason": "no change needed"}


def update_template_lifecycle(
    template_scores: dict,
    days_active: int = 14,
) -> dict:
    """Update template priorities based on EMA scores.

    - <14d           -> status=learning,        priority=1.0
    - score > 1.5    -> status=boosted,         priority=2.0
    - score < 0.3 + 30d+ -> status=deprecated,  priority=0.0
    - score < 0.5    -> status=deprioritized,   priority=0.3
    - else           -> status=active,          priority=1.0
    """
    result = {}
    for template_id, score in template_scores.items():
        if days_active < 14:
            result[template_id] = {"priority": 1.0, "status": "learning"}
        elif score > 1.5:
            result[template_id] = {"priority": 2.0, "status": "boosted"}
        elif score < 0.3 and days_active >= 30:
            result[template_id] = {"priority": 0.0, "status": "deprecated"}
        elif score < 0.5:
            result[template_id] = {"priority": 0.3, "status": "deprioritized"}
        else:
            result[template_id] = {"priority": 1.0, "status": "active"}
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    w = run()
    print(json.dumps(w.__dict__, indent=2))
