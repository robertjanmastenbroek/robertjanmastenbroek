"""
Module 5: Learning Loop — closed-loop multi-armed bandit for the content engine.

Daily flow (18:00 CET, 9 hours after morning posts):
  1. Load post registries from data/performance/*_posts.json for last 28 days.
  2. Fetch IG Graph API insights, YT Data API v3 stats, YT Analytics v2 retention.
  3. Read spotify_stats for per-batch follower deltas (attribution).
  4. Join registry × metrics → per-clip performance rows.
  5. Composite reward per row (completion 0.35 + saves 0.25 + shares 0.15
     + comments 0.05 + listeners 0.20).
  6. Group by arm value → cold-start guarded mean → normalised [0,1] weight.
  7. Persist bandit snapshot (data/weights_snapshot.json) and update the
     legacy prompt_weights.json so existing assembler/generator readers work.
  8. Write breakthrough analyses for any clip >2× rolling mean views.

Arms the bandit learns over:
  hook_mechanism / visual_type / clip_length / platform

Public API (used by generator.py / assembler.py):
  load_latest_weights() -> dict
  sample_arm(arm_name, candidates, weights=None) -> (value, is_exploring)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date as _date
from pathlib import Path

_UTC = timezone.utc


def _utcnow() -> datetime:
    """Timezone-naive UTC 'now' — matches the ISO format we've been writing."""
    return datetime.now(_UTC).replace(tzinfo=None)

import requests

# ─── Paths / env ─────────────────────────────────────────────────────────────

PROJECT_DIR     = Path(__file__).parent.parent
PERFORMANCE_DIR = PROJECT_DIR / "data" / "performance"
LEARNING_DIR    = PROJECT_DIR / "learning"
SNAPSHOT_PATH   = PROJECT_DIR / "data" / "weights_snapshot.json"


def _load_env():
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_env()

# db bridge (for spotify_stats). Optional — the loop still runs without it.
sys.path.insert(0, str(PROJECT_DIR / "outreach_agent"))
try:
    import db  # noqa: E402
except Exception:   # pragma: no cover
    db = None

logger = logging.getLogger("content_engine.learning_loop")

# ─── Tunables ────────────────────────────────────────────────────────────────

WINDOW_DAYS           = 28
MIN_SAMPLES_PER_ARM   = 5        # cold-start guard
COLD_TOTAL_THRESHOLD  = 30       # total samples below → high exploration
EPSILON_COLD          = 0.20
EPSILON_WARM          = 0.10
LISTENER_ATTRIB_HOURS = 48       # window from batch post_at → follower read
OUTLIER_MULTIPLE      = 2.0      # views > 2× rolling avg → breakthrough note

REWARD_WEIGHTS = {
    "completion":   0.35,
    "saves":        0.25,
    "shares":       0.15,
    "comments":     0.05,
    "listeners":    0.20,
}

# Saturation multipliers — a rate of 1/multiplier gives full score.
SAVES_CLAMP_MULT     = 10        # 10% saves   = full
SHARES_CLAMP_MULT    = 20        #  5% shares  = full
COMMENTS_CLAMP_MULT  = 50        #  2% comments= full
LISTENER_HALF_POINT  = 50        # follower gain where score = 0.5 (logistic)

ARMS = ["hook_mechanism", "visual_type", "clip_length", "platform"]

# ─── API endpoints ───────────────────────────────────────────────────────────

IG_GRAPH_BASE      = "https://graph.facebook.com/v21.0"
YT_DATA_BASE       = "https://www.googleapis.com/youtube/v3"
YT_ANALYTICS_BASE  = "https://youtubeanalytics.googleapis.com/v2/reports"


# ─── Registry loading ────────────────────────────────────────────────────────

def load_registries(window_days: int = WINDOW_DAYS) -> list[dict]:
    """
    Read every post registry JSON in the rolling window and return a flat list
    of clip entries. Injects `registry_date` so downstream code can reconstruct
    the batch-to-delta mapping even for legacy entries that lacked batch_id.
    """
    if not PERFORMANCE_DIR.exists():
        return []
    cutoff = _date.today() - timedelta(days=window_days)
    clips: list[dict] = []
    for f in sorted(PERFORMANCE_DIR.glob("*_posts.json")):
        stem = f.stem  # e.g. "2026-04-14_posts"
        try:
            d = _date.fromisoformat(stem.split("_")[0])
        except Exception:
            continue
        if d < cutoff:
            continue
        try:
            entries = json.loads(f.read_text() or "[]")
        except Exception as exc:
            logger.warning(f"[learning_loop] Could not parse {f.name}: {exc}")
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            e.setdefault("registry_date", d.isoformat())
            clips.append(e)
    return clips


# ─── Instagram Graph API ─────────────────────────────────────────────────────

def fetch_ig_media_in_window(
    user_id: str,
    access_token: str,
    window_days: int = WINDOW_DAYS,
) -> list[dict]:
    """
    List the business account's recent media. We can't look insights up directly
    from a Buffer update ID, so we fetch the account's media list and match to
    registry entries by caption / timestamp proximity.
    """
    if not user_id or not access_token:
        return []
    if not user_id.isdigit():
        logger.warning(
            f"[learning_loop] INSTAGRAM_USER_ID '{user_id}' is not numeric — "
            "IG insights require the Business Account numeric ID. Skipping IG."
        )
        return []
    url    = f"{IG_GRAPH_BASE}/{user_id}/media"
    since  = int((_utcnow() - timedelta(days=window_days)).timestamp())
    params = {
        "fields":       "id,caption,media_type,timestamp,permalink",
        "since":        since,
        "limit":        100,
        "access_token": access_token,
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code != 200:
            logger.warning(
                f"[learning_loop] IG media list failed: "
                f"{resp.status_code} {resp.text[:200]}"
            )
            return []
        return resp.json().get("data", []) or []
    except Exception as exc:
        logger.warning(f"[learning_loop] IG media list error: {exc}")
        return []


def fetch_ig_insights(media_id: str, access_token: str) -> dict:
    """
    Return raw insights dict for one IG media item.

    Graph API v22+ removed the `plays` metric and replaced it with `views`
    for Reels/video. A single failing metric aborts the whole call, so we
    fetch `views` separately and merge it: if the item is a feed post that
    doesn't support `views`, we just drop it and keep the rest.
    """
    if not media_id or not access_token:
        return {}
    raw: dict = {}
    # Core metrics that work for all media types (v22+)
    core_metrics = "reach,saved,shares,total_interactions,comments,likes"
    try:
        resp = requests.get(
            f"{IG_GRAPH_BASE}/{media_id}/insights",
            params={"metric": core_metrics, "access_token": access_token},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(
                f"[learning_loop] IG insights core failed for {media_id}: "
                f"{resp.status_code} {resp.text[:200]}"
            )
            return {}
        for d in resp.json().get("data", []):
            name   = d.get("name")
            values = d.get("values", [{}])
            if name:
                raw[name] = values[0].get("value", 0) if values else 0
    except Exception as exc:
        logger.warning(f"[learning_loop] IG insights error for {media_id}: {exc}")
        return {}

    # Reels / video: fetch `views` separately so one unsupported metric
    # doesn't wipe out the whole response.
    try:
        resp = requests.get(
            f"{IG_GRAPH_BASE}/{media_id}/insights",
            params={"metric": "views", "access_token": access_token},
            timeout=15,
        )
        if resp.status_code == 200:
            for d in resp.json().get("data", []):
                name   = d.get("name")
                values = d.get("values", [{}])
                if name:
                    raw[name] = values[0].get("value", 0) if values else 0
    except Exception:
        pass  # feed posts legitimately don't have `views` — not an error

    return raw


def _parse_ig_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # IG returns "2026-04-14T09:30:00+0000" — normalise +0000 → +00:00
        s2 = s.replace("+0000", "+00:00")
        return datetime.fromisoformat(s2).replace(tzinfo=None)
    except Exception:
        return None


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.rstrip("Z"))
    except Exception:
        return None


def _match_ig_media_to_clip(clip: dict, media_list: list[dict]) -> dict | None:
    """
    Match a registry clip to one IG media entry by posted_at proximity.
    Tolerance: ±90 minutes. Requires the registry entry to carry posted_at
    (new schema) — legacy entries without posted_at are skipped.
    """
    if not media_list:
        return None
    target = _parse_iso(clip.get("posted_at"))
    if target is None:
        return None

    best      = None
    best_diff = timedelta(minutes=90)
    for m in media_list:
        ts = _parse_ig_ts(m.get("timestamp"))
        if ts is None:
            continue
        diff = abs(ts - target)
        if diff < best_diff:
            best      = m
            best_diff = diff
    return best


# ─── YouTube Data API v3 (basic stats, API key) ──────────────────────────────

def _parse_iso_duration(iso: str | None) -> int:
    """PT1M30S → 90 (seconds)."""
    if not iso:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    h, mn, s = (int(v) if v else 0 for v in m.groups())
    return h * 3600 + mn * 60 + s


def fetch_yt_basic_stats(video_ids: list[str], api_key: str) -> dict[str, dict]:
    """
    Views / likes / comments / duration for each video. API key only — no OAuth.
    This path is always available for public videos.
    """
    if not video_ids or not api_key:
        return {}
    out: dict[str, dict] = {}
    unique_ids = list({v for v in video_ids if v})
    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i:i + 50]
        try:
            resp = requests.get(
                f"{YT_DATA_BASE}/videos",
                params={
                    "part": "statistics,contentDetails",
                    "id":   ",".join(batch),
                    "key":  api_key,
                },
                timeout=20,
            )
            if resp.status_code != 200:
                logger.warning(
                    f"[learning_loop] YT Data API: "
                    f"{resp.status_code} {resp.text[:200]}"
                )
                continue
            for item in resp.json().get("items", []):
                stats = item.get("statistics", {}) or {}
                dur   = _parse_iso_duration(
                    item.get("contentDetails", {}).get("duration", "PT0S")
                )
                out[item["id"]] = {
                    "views":      int(stats.get("viewCount", 0) or 0),
                    "likes":      int(stats.get("likeCount", 0) or 0),
                    "comments":   int(stats.get("commentCount", 0) or 0),
                    "duration_s": dur,
                }
        except Exception as exc:
            logger.warning(f"[learning_loop] YT Data API error: {exc}")
    return out


# ─── YouTube Analytics API v2 (retention, OAuth scope required) ──────────────

def _persist_env_updates(updates: dict[str, str]) -> None:
    """Merge {KEY: val} into .env, preserving ordering and comments."""
    env_file = PROJECT_DIR / ".env"
    text = env_file.read_text() if env_file.exists() else ""
    lines = text.splitlines()
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in remaining:
                out.append(f"{k}={remaining.pop(k)}")
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    env_file.write_text("\n".join(out).rstrip() + "\n")


def _ensure_fresh_yt_token() -> str:
    """
    Mint a fresh YouTube access token from YOUTUBE_REFRESH_TOKEN +
    YOUTUBE_CLIENT_ID + YOUTUBE_CLIENT_SECRET, persist it to .env and
    os.environ, and return it. Falls back to the stored YOUTUBE_OAUTH_TOKEN
    (stale-but-something) if refresh is not possible.

    Google access tokens expire after ~1 hour, so the daily learning run
    MUST refresh before calling the Analytics API — otherwise we'd hit 401
    on every cron invocation.
    """
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip()
    client_id     = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    stored_token  = os.environ.get("YOUTUBE_OAUTH_TOKEN", "").strip()

    if not (refresh_token and client_id and client_secret):
        if not stored_token:
            logger.warning(
                "[learning_loop] YT refresh credentials missing — "
                "set YOUTUBE_REFRESH_TOKEN + YOUTUBE_CLIENT_ID + "
                "YOUTUBE_CLIENT_SECRET in .env (run scripts/setup_youtube_oauth.py)"
            )
        return stored_token

    try:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            },
            timeout=15,
        )
    except Exception as exc:
        logger.warning(f"[learning_loop] YT token refresh failed: {exc}")
        return stored_token

    if resp.status_code != 200:
        logger.warning(
            f"[learning_loop] YT token refresh {resp.status_code}: {resp.text[:200]}"
        )
        return stored_token

    new_token = resp.json().get("access_token", "").strip()
    if not new_token:
        return stored_token

    os.environ["YOUTUBE_OAUTH_TOKEN"] = new_token
    try:
        _persist_env_updates({"YOUTUBE_OAUTH_TOKEN": new_token})
        logger.info("[learning_loop] YT access token refreshed and persisted")
    except Exception as exc:
        logger.warning(f"[learning_loop] YT token persist failed: {exc}")
    return new_token


def fetch_yt_retention(video_ids: list[str], oauth_token: str) -> dict[str, dict]:
    """
    Per-video averageViewPercentage + averageViewDuration.

    Uses ONE YouTube Analytics API call with dimensions=video (no filter).
    The `filters=video==X` + `dimensions=video` combo is rejected as
    "query not supported" by the API; grouping by video and filtering
    in-memory is the supported shape.

    Requires an OAuth access token carrying `yt-analytics.readonly`.
    Returns {} and logs a clear warning if the token lacks the scope.
    """
    if not video_ids or not oauth_token:
        return {}

    wanted = {v for v in video_ids if v}
    if not wanted:
        return {}

    start = (_utcnow() - timedelta(days=WINDOW_DAYS)).date().isoformat()
    end   = _utcnow().date().isoformat()

    try:
        resp = requests.get(
            YT_ANALYTICS_BASE,
            params={
                "ids":        "channel==MINE",
                "metrics":    "views,averageViewDuration,averageViewPercentage",
                "dimensions": "video",
                "startDate":  start,
                "endDate":    end,
                "maxResults": 200,
                "sort":       "-views",
            },
            headers={"Authorization": f"Bearer {oauth_token}"},
            timeout=25,
        )
    except Exception as exc:
        logger.warning(f"[learning_loop] YT Analytics request failed: {exc}")
        return {}

    if resp.status_code == 403:
        logger.warning(
            "[learning_loop] YT Analytics 403 — token lacks "
            "yt-analytics.readonly. Retention unavailable."
        )
        return {}
    if resp.status_code != 200:
        logger.warning(
            f"[learning_loop] YT Analytics {resp.status_code}: {resp.text[:200]}"
        )
        return {}

    rows = resp.json().get("rows", []) or []
    out: dict[str, dict] = {}
    for row in rows:
        # row shape: [video_id, views, avgViewDuration, avgViewPercentage]
        try:
            vid, _views, avg_dur, avg_pct = row[0], row[1], row[2], row[3]
        except (IndexError, TypeError):
            continue
        if vid not in wanted:
            continue
        # YouTube Shorts can report averageViewPercentage > 100 because
        # the loop-rewatch counts toward total watch time. Clamp to [0, 1]
        # so downstream dashboards and breakthrough reports stay honest.
        cr = float(avg_pct) / 100
        out[vid] = {
            "completion_rate": round(max(0.0, min(1.0, cr)), 4),
            "completion_raw":  round(cr, 4),   # kept for diagnostics
            "avg_view_s":      float(avg_dur),
        }

    logger.info(
        f"[learning_loop] YT retention: {len(out)}/{len(wanted)} videos "
        f"resolved from {len(rows)} channel rows"
    )
    return out


# ─── Spotify listener-delta attribution ──────────────────────────────────────

def fetch_spotify_series(window_days: int = WINDOW_DAYS) -> list[dict]:
    """Return spotify_stats rows ordered by date, from the last N+2 days."""
    if db is None:
        return []
    cutoff = (_utcnow() - timedelta(days=window_days + 2)).date().isoformat()
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT date, followers, monthly_listeners, source
                   FROM spotify_stats
                   WHERE date >= ?
                   ORDER BY date ASC, id ASC""",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning(f"[learning_loop] spotify_stats load failed: {exc}")
        return []


def _listener_delta_for_batch(
    batch_posted_at: str | None,
    series: list[dict],
    hours: int = LISTENER_ATTRIB_HOURS,
) -> int:
    """
    Delta = (followers at first read ≥ posted_at + `hours`)
          − (followers at most-recent read ≤ posted_at).
    Returns 0 if the series can't cover the window.
    """
    if not series or not batch_posted_at:
        return 0
    t0 = _parse_iso(batch_posted_at)
    if t0 is None:
        return 0

    def _row_dt(row: dict) -> datetime | None:
        try:
            return datetime.fromisoformat(row["date"])
        except Exception:
            return None

    # Most recent reading at or before t0 with a valid follower count
    before = None
    for row in series:
        d = _row_dt(row)
        if d is None or d > t0:
            continue
        if int(row.get("followers") or 0) > 0:
            if before is None or d > _row_dt(before):
                before = row

    target = t0 + timedelta(hours=hours)
    after  = None
    for row in series:
        d = _row_dt(row)
        if d is None or d < target:
            continue
        if int(row.get("followers") or 0) > 0:
            after = row
            break

    if not before or not after:
        return 0
    return max(0, int(after["followers"]) - int(before["followers"]))


# ─── Reward formula ──────────────────────────────────────────────────────────

def composite_reward(row: dict) -> float:
    """
    Turn one joined registry × metrics row into a single reward in roughly [0,1].
    Missing metrics contribute 0 — never a penalty.
    """
    completion = max(0.0, min(1.0, float(row.get("completion_rate") or 0.0)))
    reach      = int(row.get("reach") or row.get("views") or 0)

    saves      = int(row.get("saves")    or 0)
    shares     = int(row.get("shares")   or 0)
    comments   = int(row.get("comments") or 0)

    saves_rate    = (saves    / reach) if reach else 0.0
    shares_rate   = (shares   / reach) if reach else 0.0
    comments_rate = (comments / reach) if reach else 0.0

    saves_score    = max(0.0, min(1.0, saves_rate    * SAVES_CLAMP_MULT))
    shares_score   = max(0.0, min(1.0, shares_rate   * SHARES_CLAMP_MULT))
    comments_score = max(0.0, min(1.0, comments_rate * COMMENTS_CLAMP_MULT))

    # Logistic saturation on listener delta — 50 followers ≈ 0.5 score, 200 ≈ 0.8.
    ld = float(row.get("listener_delta_share") or 0.0)
    listeners_score = ld / (ld + LISTENER_HALF_POINT) if ld > 0 else 0.0

    return (
        REWARD_WEIGHTS["completion"] * completion
        + REWARD_WEIGHTS["saves"]    * saves_score
        + REWARD_WEIGHTS["shares"]   * shares_score
        + REWARD_WEIGHTS["comments"] * comments_score
        + REWARD_WEIGHTS["listeners"]* listeners_score
    )


# ─── Bandit math ─────────────────────────────────────────────────────────────

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    if not weights:
        return {}
    m = max(weights.values())
    if m <= 0:
        return {k: 1.0 for k in weights}
    return {k: round(v / m, 4) for k, v in weights.items()}


def compute_arm_weights(rows: list[dict]) -> dict:
    """
    For each arm, compute per-value mean reward, apply cold-start guard
    (arms below MIN_SAMPLES_PER_ARM fall back to pooled mean), and
    normalise to [0, 1].
    """
    total   = len(rows)
    rewards = [composite_reward(r) for r in rows]
    pooled  = _mean(rewards) if rewards else 0.0

    result: dict = {
        "_sample_size":       total,
        "_pooled_reward":     round(pooled, 4),
        "_by_arm_samples":    {},
        "_by_arm_raw_means":  {},
        "_exploration_eps":   EPSILON_COLD if total < COLD_TOTAL_THRESHOLD else EPSILON_WARM,
        "_updated":           _utcnow().isoformat(),
        "_window_days":       WINDOW_DAYS,
    }

    for arm in ARMS:
        buckets: dict[str, list[float]] = defaultdict(list)
        for r, reward in zip(rows, rewards):
            val = r.get(arm)
            if val is None or val == "":
                continue
            buckets[str(val)].append(reward)

        raw_means    = {k: _mean(v) for k, v in buckets.items()}
        sample_sizes = {k: len(v)  for k, v in buckets.items()}
        guarded = {
            k: (raw_means[k] if sample_sizes[k] >= MIN_SAMPLES_PER_ARM else pooled)
            for k in buckets
        }

        result[arm]                      = _normalise(guarded)
        result["_by_arm_samples"][arm]   = sample_sizes
        result["_by_arm_raw_means"][arm] = {k: round(v, 4) for k, v in raw_means.items()}

    return result


# ─── Public sampling API (consumed by generator.py / assembler.py) ────────────

def load_latest_weights() -> dict:
    """
    Return the most recent bandit snapshot as a dict, or {} if none exist.
    generator.py / assembler.py call this once per generation cycle.
    """
    if not SNAPSHOT_PATH.exists():
        return {}
    try:
        return json.loads(SNAPSHOT_PATH.read_text())
    except Exception:
        return {}


def sample_arm(
    arm_name: str,
    candidates: list,
    weights: dict | None = None,
) -> tuple:
    """
    Epsilon-greedy pick from a list of candidate values on one arm.

    * With probability ε → pick uniformly at random (exploration).
    * Otherwise → highest-weight candidate, random tie-break.
    * Missing arm or all-zero → uniform over candidates.

    Returns (picked_value, is_exploration).
    """
    if not candidates:
        return ("", False)

    if weights is None:
        weights = load_latest_weights()

    eps = float(weights.get("_exploration_eps", EPSILON_COLD)) if weights else EPSILON_COLD

    # Explore
    if random.random() < eps:
        return (random.choice(candidates), True)

    # Exploit — highest-weight candidate with random tie-break
    arm_w  = (weights or {}).get(arm_name, {}) or {}
    scored = [(c, float(arm_w.get(str(c), 0.0))) for c in candidates]
    top    = max(s for _, s in scored)
    if top <= 0:
        return (random.choice(candidates), False)
    top_cands = [c for c, s in scored if s >= top - 1e-9]
    return (random.choice(top_cands), False)


# ─── Persistence ─────────────────────────────────────────────────────────────

def save_weights_snapshot(arm_weights: dict) -> Path:
    """Write the full bandit snapshot to data/weights_snapshot.json."""
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(arm_weights, indent=2, default=str))
    return SNAPSHOT_PATH


def _update_legacy_prompt_weights(arm_weights: dict) -> None:
    """
    Keep prompt_weights.json roughly in sync so any older code path that still
    reads PromptWeights.load() gets recent numbers.
    """
    try:
        from content_engine.types import PromptWeights
    except Exception:
        return

    try:
        old = PromptWeights.load()
    except Exception:
        old = PromptWeights.defaults()

    hook_arm     = arm_weights.get("hook_mechanism", {}) or {}
    visual_arm   = arm_weights.get("visual_type", {}) or {}
    length_arm   = arm_weights.get("clip_length", {}) or {}
    platform_arm = arm_weights.get("platform", {}) or {}

    legacy_hooks: dict[str, float] = {}
    for k in ("tension", "identity", "scene", "claim", "rupture"):
        legacy_hooks[k] = float(hook_arm.get(k, old.hook_weights.get(k, 1.0)))

    legacy_visuals = dict(old.visual_weights)
    for k in legacy_visuals:
        if k in visual_arm:
            legacy_visuals[k] = float(visual_arm[k])

    best_len = old.best_clip_length
    if length_arm:
        try:
            best_len = int(max(length_arm, key=lambda k: length_arm[k]))
        except Exception:
            pass

    best_plat = old.best_platform
    if platform_arm:
        best_plat = max(platform_arm, key=lambda k: platform_arm[k])

    try:
        PromptWeights(
            hook_weights=legacy_hooks,
            visual_weights=legacy_visuals,
            best_clip_length=best_len,
            best_platform=best_plat,
            updated=_utcnow().isoformat(),
        ).save()
    except Exception as exc:
        logger.warning(f"[learning_loop] Legacy prompt_weights write failed: {exc}")


# ─── Breakthrough detection ──────────────────────────────────────────────────

def detect_outliers(rows: list[dict]) -> list[dict]:
    """Return rows whose views exceed OUTLIER_MULTIPLE × rolling mean."""
    if not rows:
        return []
    views = [int(r.get("views") or 0) for r in rows]
    avg   = sum(views) / max(1, len(views))
    if avg <= 0:
        return []
    return [r for r in rows if int(r.get("views") or 0) > avg * OUTLIER_MULTIPLE]


def _write_breakthrough(row: dict, date_str: str) -> None:
    """Write a short Claude-generated breakthrough note for one outlier."""
    try:
        from content_engine.trend_scanner import _find_claude
        claude = os.environ.get("CLAUDE_CLI_PATH", "") or _find_claude()
    except Exception:
        claude = os.environ.get("CLAUDE_CLI_PATH", "")

    prompt = (
        f"A short-form video went viral today (>{OUTLIER_MULTIPLE}× average views):\n"
        f"Platform: {row.get('platform')}\n"
        f"Views: {int(row.get('views') or 0):,}\n"
        f"Completion: {float(row.get('completion_rate') or 0):.1%}\n"
        f"Saves: {row.get('saves', 0)}  Shares: {row.get('shares', 0)}\n"
        f"Hook mechanism: {row.get('hook_mechanism', 'other')}\n"
        f"Hook text: \"{row.get('hook_text', '')}\"\n"
        f"Visual type: {row.get('visual_type', '')}\n"
        f"Clip length: {row.get('clip_length', 0)}s\n"
        f"Track: {row.get('track_title', '')}\n\n"
        "In 3-5 bullet points, explain what likely made this work and exactly "
        "what to repeat tomorrow. Be specific. No filler."
    )

    analysis = ""
    if claude:
        try:
            result = subprocess.run(
                [claude, "--print", "--model", "claude-haiku-4-5-20251001",
                 "--no-session-persistence",
                 "--system-prompt",
                 "You are a music marketing analyst. Be specific and concise.",
                 prompt],
                capture_output=True, text=True, timeout=60,
                cwd="/tmp",  # avoid project CLAUDE.md
            )
            analysis = (result.stdout or "").strip()
        except Exception as exc:
            analysis = f"[analysis failed: {exc}]"

    out_dir = LEARNING_DIR / "breakthroughs"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{date_str}_{row.get('platform', 'x')}_{str(row.get('post_id', 'x'))[:8]}.md"
    (out_dir / fname).write_text(
        f"# Breakthrough: {str(row.get('platform', '')).title()} — {date_str}\n\n"
        f"**Views:** {int(row.get('views') or 0):,}  "
        f"**Completion:** {float(row.get('completion_rate') or 0):.1%}  "
        f"**Hook:** {row.get('hook_mechanism', '')}  "
        f"**Visual:** {row.get('visual_type', '')}  "
        f"**Length:** {row.get('clip_length', 0)}s\n\n"
        f"{analysis or '_(no analysis — claude CLI unavailable)_'}\n"
    )
    logger.info(f"[learning_loop] Breakthrough saved: {fname}")


# ─── Main pipeline ───────────────────────────────────────────────────────────

def _join_metrics(
    clips: list[dict],
    yt_basic: dict[str, dict],
    yt_retent: dict[str, dict],
    ig_media: list[dict],
    ig_token: str,
    batch_deltas: dict[str, int],
    batch_sizes: dict[str, int],
) -> list[dict]:
    """Produce one enriched row per registry clip, ready for reward scoring."""
    rows: list[dict] = []
    for c in clips:
        platform = (c.get("platform") or "").lower()
        post_id  = c.get("post_id", "")
        row      = dict(c)

        if platform == "youtube" and post_id and post_id not in ("", "buffer"):
            yt = yt_basic.get(post_id)
            if yt:
                row["views"]    = yt["views"]
                row["likes"]    = yt["likes"]
                row["comments"] = yt["comments"]
                row["reach"]    = yt["views"]  # YT has no dedicated reach metric
            ret = yt_retent.get(post_id)
            if ret:
                row["completion_rate"] = ret["completion_rate"]
                row["avg_view_s"]      = ret["avg_view_s"]

        elif platform == "instagram" and ig_token:
            media = _match_ig_media_to_clip(c, ig_media)
            if media:
                raw = fetch_ig_insights(media["id"], ig_token)
                # Graph API v22+ uses `views`; older videos may still return
                # `plays`/`video_views`. Accept any of them.
                row["views"]              = (raw.get("views", 0)
                                             or raw.get("plays", 0)
                                             or raw.get("video_views", 0))
                row["reach"]              = raw.get("reach", 0)
                row["saves"]              = raw.get("saved", 0)
                row["shares"]             = raw.get("shares", 0)
                row["comments"]           = raw.get("comments", 0)
                row["likes"]              = raw.get("likes", 0)
                row["total_interactions"] = raw.get("total_interactions", 0)
                row["media_id"]           = media["id"]

        # Listener attribution share
        bid   = c.get("batch_id") or c.get("registry_date") or ""
        bsize = max(1, batch_sizes.get(bid, 1))
        row["listener_delta_share"] = batch_deltas.get(bid, 0) / bsize

        rows.append(row)
    return rows


def run(
    date_str: str | None = None,
    window_days: int = WINDOW_DAYS,
    dry_run: bool = False,
) -> dict:
    """
    Full learning-loop pass. Returns the computed arm_weights dict (empty on
    early exit). Side effects: writes weights_snapshot.json, updates
    prompt_weights.json, saves per-clip metrics, writes breakthroughs.
    """
    date_str = date_str or _date.today().isoformat()
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    PERFORMANCE_DIR.mkdir(parents=True, exist_ok=True)

    clips = load_registries(window_days)
    if not clips:
        logger.warning("[learning_loop] No registries in window — nothing to learn.")
        return {}
    logger.info(
        f"[learning_loop] Loaded {len(clips)} clips from last {window_days}d registries"
    )

    # ── YouTube fetch
    yt_api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    # Mint a fresh access token from the refresh token every run — Google
    # access tokens expire after ~1 hour, so the daily cron would otherwise
    # always hit 401 on the Analytics endpoint.
    yt_oauth   = _ensure_fresh_yt_token()
    yt_ids = [
        c["post_id"] for c in clips
        if (c.get("platform") or "").lower() == "youtube"
        and c.get("post_id")
        and c["post_id"] not in ("", "buffer")
    ]
    yt_basic  = fetch_yt_basic_stats(yt_ids, yt_api_key) if yt_api_key else {}
    yt_retent = fetch_yt_retention(yt_ids, yt_oauth) if yt_oauth else {}
    logger.info(
        f"[learning_loop] YT: {len(yt_basic)}/{len(set(yt_ids))} basic, "
        f"{len(yt_retent)}/{len(set(yt_ids))} retention"
    )

    # ── Instagram fetch
    ig_token   = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
    ig_user_id = os.environ.get("INSTAGRAM_USER_ID", "").strip()
    ig_media   = fetch_ig_media_in_window(ig_user_id, ig_token, window_days) \
        if (ig_token and ig_user_id) else []
    logger.info(f"[learning_loop] IG media in window: {len(ig_media)}")

    # ── Spotify follower attribution
    spot_series = fetch_spotify_series(window_days)
    logger.info(f"[learning_loop] Spotify rows loaded: {len(spot_series)}")

    batches: dict[str, list[dict]] = defaultdict(list)
    for c in clips:
        bid = c.get("batch_id") or c.get("registry_date") or ""
        batches[bid].append(c)

    batch_deltas: dict[str, int] = {}
    batch_sizes:  dict[str, int] = {}
    for bid, bclips in batches.items():
        posted = next((c.get("posted_at") for c in bclips if c.get("posted_at")), None)
        batch_deltas[bid] = _listener_delta_for_batch(posted, spot_series)
        batch_sizes[bid]  = len(bclips)
    gained_batches = sum(1 for v in batch_deltas.values() if v > 0)
    logger.info(
        f"[learning_loop] Batch deltas: {gained_batches}/{len(batch_deltas)} "
        f"with positive follower gain"
    )

    # ── Join registry × metrics
    rows = _join_metrics(
        clips, yt_basic, yt_retent, ig_media, ig_token, batch_deltas, batch_sizes
    )

    # ── Save joined metrics
    metrics_path = PERFORMANCE_DIR / f"{date_str}_metrics.json"
    metrics_path.write_text(json.dumps(rows, indent=2, default=str))
    logger.info(f"[learning_loop] Per-clip metrics written: {metrics_path.name}")

    # ── Compute arm weights
    arm_weights = compute_arm_weights(rows)
    logger.info(
        f"[learning_loop] Computed weights: n={arm_weights['_sample_size']}, "
        f"ε={arm_weights['_exploration_eps']}, "
        f"pooled={arm_weights['_pooled_reward']}"
    )
    for arm in ARMS:
        top = arm_weights.get(arm, {})
        if top:
            leader = max(top, key=lambda k: top[k])
            logger.info(f"[learning_loop]   {arm}: leader={leader} ({top[leader]:.2f})")

    # ── Persist
    if not dry_run:
        snap = save_weights_snapshot(arm_weights)
        _update_legacy_prompt_weights(arm_weights)
        logger.info(f"[learning_loop] Snapshot → {snap}")
    else:
        logger.info("[learning_loop] DRY RUN — skipped weight write")

    # ── Breakthroughs
    for o in detect_outliers(rows):
        logger.info(
            f"[learning_loop] Breakthrough: "
            f"{o.get('platform')} {int(o.get('views') or 0):,} views"
        )
        _write_breakthrough(o, date_str)

    return arm_weights


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Content engine learning loop")
    parser.add_argument("--date",    default=None,  help="Override date (YYYY-MM-DD)")
    parser.add_argument("--window",  type=int, default=WINDOW_DAYS, help="Rolling window days")
    parser.add_argument("--dry-run", action="store_true", help="Print but don't write")
    parser.add_argument("--show",    action="store_true", help="Print latest weights and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  learning_loop: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.show:
        w = load_latest_weights()
        if not w:
            print("No stored weights yet.")
            return
        print(json.dumps(w, indent=2, default=str))
        return

    result = run(date_str=args.date, window_days=args.window, dry_run=args.dry_run)
    print(json.dumps({
        "sample_size":   result.get("_sample_size", 0),
        "exploration":   result.get("_exploration_eps"),
        "pooled_reward": result.get("_pooled_reward"),
        "updated":       result.get("_updated"),
    }, indent=2))


if __name__ == "__main__":
    main()
