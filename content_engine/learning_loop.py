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
    """Overwrite a single key=value line in .env (no quotes). Safe for tokens."""
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    new_lines = []
    replaced = False
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"{key}=\""):
            new_lines.append(f"{key}={value}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n")


def _resolve_ig_media_ids(posts: list, access_token: str, user_id: str) -> list:
    """Replace Buffer post IDs with real IG media IDs.

    Queries /me/media for the last 50 posts and matches by closest timestamp.
    Posts that cannot be resolved (too old, no match) are returned with their
    original ID — the caller will see a 400 and skip them, which is correct.
    """
    buffer_posts = [p for p in posts if _is_buffer_id(p.get("post_id", ""))]
    if not buffer_posts:
        return posts

    try:
        resp = requests.get(
            f"{INSTAGRAM_GRAPH_BASE}/{user_id}/media",
            params={"fields": "id,timestamp", "limit": 50, "access_token": access_token},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"[learning_loop] IG media list failed: {resp.status_code}")
            return posts
        media_items = resp.json().get("data", [])
    except Exception as e:
        logger.warning(f"[learning_loop] IG media list error: {e}")
        return posts

    # Build a list of (datetime, media_id) sorted newest-first
    media_by_ts = []
    for m in media_items:
        try:
            ts = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
            media_by_ts.append((ts, m["id"]))
        except Exception:
            pass
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
        # Find the closest IG media within ±2 hours that hasn't been used yet
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

    # Apply resolutions
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
                logger.warning(f"[learning_loop] IG insights {post_id}: {resp.status_code}")
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
    Fetch per-post insights from Facebook Graph API (feed videos + reels).
    post_ids: list of {post_id, clip_index, variant, hook_mechanism, visual_type, clip_length}
    Returns list of PerformanceRecord.
    """
    from content_engine.types import PerformanceRecord
    records = []

    for meta in post_ids:
        post_id = meta.get("post_id", "")
        if not post_id:
            continue
        try:
            # FB Page post insights. total_video_views is the view count,
            # post_video_view_time_by_region_id gives us completion signal,
            # post_reactions_by_type_total + post_clicks_by_type approximate
            # engagement. We keep it to views + reactions for now — FB's
            # completion_rate equivalent (video_avg_time_watched) is on the
            # video endpoint, not the post.
            resp = requests.get(
                f"{INSTAGRAM_GRAPH_BASE}/{post_id}/insights",
                params={
                    "metric": "post_video_views,post_reactions_by_type_total,"
                              "post_video_avg_time_watched,post_video_view_time",
                    "access_token": page_token,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"[learning_loop] FB insights {post_id}: {resp.status_code}")
                continue

            raw = {d["name"]: d.get("values", [{}])[0].get("value", 0)
                   for d in resp.json().get("data", [])}

            views = raw.get("post_video_views", 0) or 0
            avg_watched_ms = raw.get("post_video_avg_time_watched", 0) or 0
            clip_length = meta.get("clip_length", 15)
            # Completion rate = avg seconds watched / clip length
            completion = 0.0
            if clip_length > 0 and avg_watched_ms > 0:
                completion = min(1.0, (avg_watched_ms / 1000.0) / clip_length)
            reactions = raw.get("post_reactions_by_type_total", {}) or {}
            total_reactions = sum(reactions.values()) if isinstance(reactions, dict) else 0

            records.append(PerformanceRecord(
                post_id=post_id,
                platform="facebook",
                clip_index=meta.get("clip_index", 0),
                variant=meta.get("variant", "a"),
                hook_mechanism=meta.get("hook_mechanism", "tension"),
                visual_type=meta.get("visual_type", "b_roll"),
                clip_length=clip_length,
                views=int(views),
                completion_rate=round(completion, 4),
                scroll_stop_rate=0.0,  # FB doesn't expose reach→views ratio on post insights
                share_rate=0.0,
                save_rate=round(total_reactions / max(views, 1), 4) if views else 0.0,
                recorded_at=datetime.now().isoformat(),
            ))
        except Exception as e:
            logger.warning(f"[learning_loop] FB metrics error for {post_id}: {e}")

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
                logger.warning(f"[learning_loop] IG Story insights {post_id}: {resp.status_code}")
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
    Fetch YouTube video analytics.
    post_ids: list of {post_id (video_id), clip_index, variant, ...}
    """
    from content_engine.types import PerformanceRecord
    records = []

    for meta in post_ids:
        video_id = meta.get("post_id", "")
        if not video_id:
            continue
        try:
            resp = requests.get(
                YOUTUBE_ANALYTICS,
                params={
                    "ids": "channel==MINE",
                    "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
                    "filters": f"video=={video_id}",
                    "startDate": _date.today().isoformat(),
                    "endDate": _date.today().isoformat(),
                    "dimensions": "video",
                },
                headers={"Authorization": f"Bearer {oauth_token}"},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"[learning_loop] YT analytics {video_id}: {resp.status_code}")
                continue

            rows = resp.json().get("rows", [])
            if not rows:
                continue

            _, views, _, avg_dur, avg_pct = rows[0]
            clip_length = meta.get("clip_length", 15)

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
            logger.warning(f"[learning_loop] YT metrics error for {video_id}: {e}")

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


def detect_outliers(records: list) -> list:
    """Return records with views > 2× rolling average."""
    if not records:
        return []
    avg = sum(r.views for r in records) / len(records)
    return [r for r in records if r.views > avg * 2]


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
            logger.warning("[learning_loop] No post registry — weights unchanged")
            return UnifiedWeights.load()

    ig_posts = [p for p in post_registry if p.get("platform") == "instagram"]
    yt_posts = [p for p in post_registry if p.get("platform") == "youtube"]
    fb_posts = [p for p in post_registry if p.get("platform") == "facebook"]
    ig_story_posts = [p for p in post_registry if p.get("platform") == "instagram_story"]

    ig_token     = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    ig_user_id   = os.environ.get("INSTAGRAM_USER_ID", "").strip('"')
    fb_page_token = os.environ.get("FACEBOOK_PAGE_TOKEN", "") or ig_token

    # Always refresh YouTube token before use — access tokens expire in ~1h.
    yt_token = _refresh_youtube_token()

    # Resolve Buffer post IDs to real IG media IDs before querying insights.
    # Buffer returns its own 24-char hex IDs; IG Graph API only accepts native
    # numeric media IDs. Resolution runs against the /media and /stories
    # endpoints which list the last 50 items — more than enough for daily runs.
    if ig_token and ig_user_id:
        if ig_posts:
            ig_posts = _resolve_ig_media_ids(ig_posts, ig_token, ig_user_id)
        if ig_story_posts:
            ig_story_posts = _resolve_ig_story_ids(ig_story_posts, ig_token, ig_user_id)

    records = []
    if ig_posts and ig_token:
        records += fetch_instagram_metrics(ig_posts, ig_token)
        logger.info(f"[learning_loop] IG: {len(ig_posts)} posts → {len([r for r in records if r.platform == 'instagram'])} records")
    if yt_posts and yt_token:
        yt_records = fetch_youtube_metrics(yt_posts, yt_token)
        records += yt_records
        logger.info(f"[learning_loop] YouTube: {len(yt_posts)} posts → {len(yt_records)} records")
    if fb_posts and fb_page_token:
        fb_records = fetch_facebook_metrics(fb_posts, fb_page_token)
        records += fb_records
        logger.info(f"[learning_loop] Facebook: {len(fb_posts)} posts → {len(fb_records)} records")
    if ig_story_posts and ig_token:
        story_records = fetch_instagram_story_metrics(ig_story_posts, ig_token)
        records += story_records
        logger.info(f"[learning_loop] IG Stories: {len(ig_story_posts)} posts → {len(story_records)} records")

    if not records:
        logger.warning("[learning_loop] No performance records collected — weights unchanged")
        return UnifiedWeights.load()

    _save_performance_log(records, date_str)

    # Hydrate records with full metadata from the registry (format, template, track, etc.)
    # so the multi-dimensional EMA has something to group on.
    registry_by_post_id = {p.get("post_id", ""): p for p in post_registry if p.get("post_id")}
    records_as_dicts = []
    for r in records:
        base = r.__dict__.copy()
        extra = registry_by_post_id.get(r.post_id, {})
        # merge — don't overwrite real metric values
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

    # Update best_platform + best_time_of_day
    if new.platform_weights:
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
