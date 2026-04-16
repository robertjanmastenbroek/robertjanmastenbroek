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

# How aggressively weights shift each day. 0 = no change, 1 = full replacement.
LEARNING_RATE = 0.3


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
            # `plays` was removed in Graph API v22+. Request `views` alongside
            # it so the call still works on both v21 and v22, and fall back to
            # whichever the API actually returns. A single unsupported metric
            # in the comma-separated list will 400 the whole response, so we
            # include both rather than branching on API version.
            resp = requests.get(
                f"{INSTAGRAM_GRAPH_BASE}/{post_id}/insights",
                params={
                    "metric": "views,plays,reach,saved,shares,total_interactions",
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

            # views is the v22+ name; plays is the v21 fallback.
            plays  = raw.get("views", 0) or raw.get("plays", 0)
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

    ig_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    yt_token = os.environ.get("YOUTUBE_OAUTH_TOKEN", "")

    records = []
    if ig_posts and ig_token:
        records += fetch_instagram_metrics(ig_posts, ig_token)
        logger.info(f"[learning_loop] IG: {len(ig_posts)} posts → {len([r for r in records if r.platform == 'instagram'])} records")
    if yt_posts and yt_token:
        yt_records = fetch_youtube_metrics(yt_posts, yt_token)
        records += yt_records
        logger.info(f"[learning_loop] YouTube: {len(yt_posts)} posts → {len(yt_records)} records")

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
    )

    if not records:
        return new

    # Compute signal per record
    signals = []
    for r in records:
        signal = (
            r.get("completion_rate", 0) * 0.5
            + r.get("save_rate", 0) * 0.3
            + r.get("scroll_stop_rate", 0) * 0.2
        )
        signals.append((r, signal))

    # Group signals by dimension and update via EMA
    _ema_update(new.format_weights, signals, "format_type", learning_rate)
    _ema_update(new.platform_weights, signals, "platform", learning_rate)
    _ema_update(new.hook_weights, signals, "hook_template_id", learning_rate)
    _ema_update(new.visual_weights, signals, "visual_type", learning_rate)
    _ema_update(new.track_weights, signals, "track_title", learning_rate)
    _ema_update(new.transitional_category_weights, signals, "transitional_category", learning_rate)

    # Update best_platform
    if new.platform_weights:
        new.best_platform = max(new.platform_weights, key=new.platform_weights.get)

    return new


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
