#!/usr/bin/env python3.13
"""
⚠️  DEPRECATED — 2026-04-16 ⚠️
========================================================================
This module has been SUPERSEDED by content_engine/learning_loop.py.

The new loop unifies fetch + recompute into a single daily pass, reads
directly from data/performance/*_posts.json (the canonical post registry
written by content_engine/pipeline.py), attributes Spotify follower
deltas per batch, and writes data/weights_snapshot.json which every
downstream reader (generator.py, assembler.py, visual_engine.py) uses
via content_engine.learning_loop.load_latest_weights().

Entry points that used to invoke THIS file now route to the new loop:
  - rjm.py learning {fetch,recompute,show,report}
  - scripts/run_agent.sh metrics-fetch|weights-learn|learning
  - launchd: com.rjm.viral-learning.plist (daily 18:00 CET)

The old launchd plists (com.rjm.metrics-fetch, com.rjm.weights-learn)
have been unloaded and renamed *.disabled. Do not re-enable them.

This file is kept only to preserve the SQLite schema helpers referenced
by outreach_agent/db.py migrations. It is NOT part of the active
learning pipeline and should not be imported by new code.
========================================================================

weights_learner.py — Compute arm weights from observed performance.

Reads:
  content_log       (what we did)
  content_metrics   (how it performed)

Writes:
  content_weights_history (timestamped weight snapshots for generator.py to read)

Arms (dimensions we learn over):
  - hook_mechanism    : tension / identity / scene / claim / rupture / other
  - lead_category     : phone / broll / perf
  - clip_length       : 5 / 9 / 15 / 7 / 15 / 28   (whatever's been posted)
  - angle             : emotional / signal / energy / contrast / body-drop / identity

Reward formula (composite, per row):
    reward = 0.35·completion_rate
           + 0.25·saves_per_reach
           + 0.20·shares_per_reach
           + 0.10·follows_per_reach
           + 0.10·profile_to_spotify_ctr

  - completion_rate is the primary signal (drives TikTok + Reels ranking).
  - Rates are per-reach so small-audience posts aren't penalised for raw counts.
  - follows and spotify_ctr default to 0 when not yet wired — the reward still
    works, the loop just weighs less heavily on those signals.

Rolling window: 28 days by default (matches TikTok's ranking half-life).

Cold-start rule: if an arm has fewer than MIN_SAMPLES posts in the window,
it gets the uniform baseline weight, NOT the observed mean. Prevents one
lucky early post from locking us into a bad arm.

Exploration: epsilon-greedy with ε = 0.20 when total_samples < 30 (cold),
                                ε = 0.10 otherwise (warm). Reported alongside
the weights so generator.py can flip the `exploration` flag on ~10% of picks.

Usage:
  python3.13 weights_learner.py              # compute + persist
  python3.13 weights_learner.py --dry-run    # compute + print, no write
  python3.13 weights_learner.py --window 14  # override window to 14 days
  python3.13 weights_learner.py --show       # print latest stored weights
"""

import argparse
import json
import logging
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

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

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  weights_learner: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("weights_learner")

# ─── Tunables ────────────────────────────────────────────────────────────────

DEFAULT_WINDOW_DAYS = 28
MIN_SAMPLES_PER_ARM = 5     # cold-start guard
COLD_TOTAL_THRESHOLD = 30   # total_samples below → cold regime
EPSILON_COLD = 0.20
EPSILON_WARM = 0.10

# Arms we learn over. Each entry: (content_log column, nice label)
ARMS = [
    ("hook_mechanism", "hook_mechanism"),
    ("lead_category",  "lead_category"),
    ("clip_length",    "clip_length"),
    ("angle",          "angle"),
]

# Uniform baselines — used when cold-start applies OR an arm has zero samples.
# The values are *defaults only*. The learner overwrites each one based on
# observed reward means as sample sizes grow past the threshold.
UNIFORM_BASELINE = {
    "hook_mechanism": {
        "tension":  1.0, "identity": 1.0, "scene": 1.0,
        "claim":    1.0, "rupture":  1.0, "other": 1.0,
    },
    "lead_category": {"phone": 1.0, "broll": 1.0, "perf": 1.0},
    "clip_length":   {},  # populated dynamically from what we've actually posted
    "angle":         {},
}


# ─── Reward formula ──────────────────────────────────────────────────────────

def _composite_reward(row: dict) -> float:
    """
    Turn one joined (content_log × content_metrics) row into a single reward
    score in roughly [0, 1]. Missing metrics → 0 contribution (not penalty).

    Weights (per design):
        0.35 completion_rate
        0.25 saves_per_reach
        0.20 shares_per_reach
        0.10 follows_per_reach  (placeholder — not tracked yet, always 0)
        0.10 profile_to_spotify (placeholder — needs UTM/analytics pipeline)
    """
    completion = row.get("completion_rate") or 0.0
    reach      = row.get("reach") or 0
    saves      = row.get("saves") or 0
    shares     = row.get("shares") or 0
    follows    = row.get("follows_from") or 0

    # Rates with safe divide
    saves_rate   = (saves / reach)   if reach else 0.0
    shares_rate  = (shares / reach)  if reach else 0.0
    follows_rate = (follows / reach) if reach else 0.0
    spotify_ctr  = 0.0  # placeholder — rewired when UTM + S4A pipeline exists

    # Clamp each component to [0, 1] so one runaway ratio can't dominate
    completion   = max(0.0, min(1.0, float(completion)))
    saves_rate   = max(0.0, min(1.0, saves_rate * 10))    # ×10 so 10% saves = full score
    shares_rate  = max(0.0, min(1.0, shares_rate * 20))   # ×20 so 5% shares = full score
    follows_rate = max(0.0, min(1.0, follows_rate * 50))  # ×50 so 2% follows = full score

    return (
        0.35 * completion
        + 0.25 * saves_rate
        + 0.20 * shares_rate
        + 0.10 * follows_rate
        + 0.10 * spotify_ctr
    )


# ─── Data assembly ───────────────────────────────────────────────────────────

def _load_rows(window_days: int) -> list[dict]:
    """
    Join content_log × content_metrics inside the rolling window and return
    one row per (post, platform) with the fields needed for reward + grouping.
    We join on buffer_id because Buffer is the common key.
    """
    cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()
    sql = """
        SELECT
            cl.id                AS log_id,
            cl.posted_at         AS posted_at,
            cl.platform          AS platform,
            cl.hook_mechanism    AS hook_mechanism,
            cl.lead_category     AS lead_category,
            cl.clip_length       AS clip_length,
            cl.angle             AS angle,
            cl.track             AS track,
            cl.batch_id          AS batch_id,
            cl.exploration       AS exploration,
            cm.views             AS views,
            cm.likes             AS likes,
            cm.comments          AS comments,
            cm.shares            AS shares,
            cm.saves             AS saves,
            cm.reach             AS reach,
            cm.completion_rate   AS completion_rate,
            cm.avg_watch_s       AS avg_watch_s,
            cm.follows_from      AS follows_from
        FROM content_log cl
        INNER JOIN content_metrics cm
                ON cm.buffer_id = cl.buffer_id
               AND cm.platform  = cl.platform
        WHERE cl.posted_at >= ?
    """
    with db.get_conn() as conn:
        rows = conn.execute(sql, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


# ─── Weight computation ──────────────────────────────────────────────────────

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _normalise(weights: dict) -> dict:
    """Scale a weight dict so the max value is 1.0 (preserve ratios)."""
    if not weights:
        return {}
    m = max(weights.values())
    if m <= 0:
        # Fall back to uniform if everything is zero
        return {k: 1.0 for k in weights}
    return {k: v / m for k, v in weights.items()}


def compute_weights(rows: list[dict]) -> dict:
    """
    For each arm, compute per-value mean reward, apply cold-start guard,
    and normalise to [0, 1]. Returns:

    {
        'hook_mechanism': {'tension': 0.8, 'identity': 1.0, ...},
        'lead_category':  {'phone': 0.4, 'broll': 1.0, 'perf': 0.7},
        'clip_length':    {'5': 0.9, '15': 1.0, '28': 0.6},
        'angle':          {'emotional': 1.0, 'signal': 0.6, 'energy': 0.8},
        '_sample_size':   42,
        '_by_arm_samples': {'hook_mechanism': 42, 'lead_category': 40, ...},
    }
    """
    total_samples = len(rows)
    result: dict = {
        "_sample_size":      total_samples,
        "_by_arm_samples":   {},
        "_by_arm_raw_means": {},
    }

    # Precompute rewards once
    reward_by_row = [_composite_reward(r) for r in rows]

    for column, label in ARMS:
        # Group rewards by arm value
        buckets: dict[str, list[float]] = defaultdict(list)
        for r, reward in zip(rows, reward_by_row):
            val = r.get(column)
            if val is None:
                continue
            buckets[str(val)].append(reward)

        # Compute raw means
        raw_means = {k: _mean(v) for k, v in buckets.items()}
        sample_sizes = {k: len(v) for k, v in buckets.items()}

        # Apply cold-start guard: arms under MIN_SAMPLES → pooled mean
        pooled_mean = _mean(reward_by_row) if reward_by_row else 0.0
        guarded = {
            k: (raw_means[k] if sample_sizes[k] >= MIN_SAMPLES_PER_ARM else pooled_mean)
            for k in buckets
        }

        # Blend in baseline values so arms we haven't tried yet still have a prior
        baseline = UNIFORM_BASELINE.get(label, {})
        for k, v in baseline.items():
            guarded.setdefault(k, pooled_mean if pooled_mean > 0 else 0.5)

        result[label]                   = _normalise(guarded)
        result["_by_arm_samples"][label] = sample_sizes
        result["_by_arm_raw_means"][label] = raw_means

    return result


def pick_epsilon(total_samples: int) -> float:
    """ε = 0.20 cold (under 30 labelled posts), 0.10 warm."""
    return EPSILON_COLD if total_samples < COLD_TOTAL_THRESHOLD else EPSILON_WARM


# ─── Persistence ─────────────────────────────────────────────────────────────

def save_snapshot(weights: dict, window_days: int, notes: str = "") -> int:
    """Write one row into content_weights_history."""
    with db.get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO content_weights_history
               (computed_at, window_days, sample_size, weights_json, exploration_eps, notes)
               VALUES (?,?,?,?,?,?)""",
            (
                datetime.utcnow().isoformat(),
                window_days,
                int(weights.get("_sample_size", 0)),
                json.dumps(weights),
                pick_epsilon(int(weights.get("_sample_size", 0))),
                notes,
            ),
        )
        return cursor.lastrowid


def sample_arm(
    arm_name: str,
    candidates: list[str],
    weights: dict | None = None,
) -> tuple[str, bool]:
    """
    Epsilon-greedy selection from a list of candidate values on one arm.

    - With probability ε, pick uniformly at random (exploration).
    - Otherwise, pick the candidate with the highest learned weight.
    - Ties at the top are broken uniformly at random (important during
      cold-start when every arm reads 1.0 on the uniform baseline — without
      this, Python's stable sort would lock the exploit branch to whichever
      candidate is listed first).
    - Missing-arm or zero-weight → uniform over candidates.

    Returns (picked_value, is_exploration).

    Usage from generator.py / post_today.py:
        picked, exploring = weights_learner.sample_arm(
            "hook_mechanism",
            ["tension", "identity", "scene", "claim", "rupture"],
        )
    """
    import random
    if not candidates:
        return ("", False)

    if weights is None:
        weights = load_latest_weights()

    eps = float(weights.get("_exploration_eps", EPSILON_COLD)) if weights else EPSILON_COLD

    # Explore
    if random.random() < eps:
        return (random.choice(candidates), True)

    # Exploit — pick highest-weight candidate (random tie-break)
    arm_w = (weights or {}).get(arm_name, {}) or {}
    scored = [(c, float(arm_w.get(c, 0.0))) for c in candidates]
    top_score = max(s for _, s in scored)
    if top_score <= 0:
        # No learned signal yet on this arm → uniform pick (not "first")
        return (random.choice(candidates), False)
    top_candidates = [c for c, s in scored if s >= top_score - 1e-9]
    return (random.choice(top_candidates), False)


def load_latest_weights() -> dict:
    """
    Return the most recent snapshot as a dict, or an empty dict if none exist.
    generator.py / post_today.py call this to bias selection.
    """
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT weights_json, exploration_eps, sample_size, computed_at, window_days
               FROM content_weights_history
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    if not row:
        return {}
    d = dict(row)
    try:
        weights = json.loads(d["weights_json"])
    except Exception:
        weights = {}
    weights["_exploration_eps"] = d["exploration_eps"]
    weights["_computed_at"]     = d["computed_at"]
    weights["_window_days"]     = d["window_days"]
    return weights


# ─── CLI helpers ─────────────────────────────────────────────────────────────

def _fmt_weights(w: dict) -> str:
    """Human-friendly dump of a weight dict for --show / --dry-run."""
    lines = []
    n      = w.get("_sample_size", 0)
    eps    = pick_epsilon(n)
    lines.append(f"Sample size: {n}  (ε = {eps:.2f})")
    for column, label in ARMS:
        arm = w.get(label, {})
        samples = (w.get("_by_arm_samples", {}) or {}).get(label, {})
        if not arm:
            continue
        ranked = sorted(arm.items(), key=lambda kv: kv[1], reverse=True)
        lines.append(f"\n  {label}:")
        for k, v in ranked:
            bar = "█" * int(round(v * 20))
            s   = samples.get(k, 0)
            lines.append(f"    {k:14}  {v:.2f}  [{s:3}]  {bar}")
    return "\n".join(lines)


# ─── Main ────────────────────────────────────────────────────────────────────

def run(window_days: int = DEFAULT_WINDOW_DAYS, dry_run: bool = False) -> dict:
    db.init_db()
    rows = _load_rows(window_days)
    log.info(f"Loaded {len(rows)} joined rows from last {window_days} days")
    weights = compute_weights(rows)

    if not dry_run and rows:
        row_id = save_snapshot(weights, window_days, notes=f"window={window_days}d")
        log.info(f"Persisted weights snapshot id={row_id}")
    elif not rows:
        log.info("No rows with metrics — nothing to learn yet. Skipping write.")
    return weights


def main():
    parser = argparse.ArgumentParser(description="Compute learning-loop arm weights.")
    parser.add_argument("--dry-run", action="store_true", help="Print but don't write.")
    parser.add_argument("--window",  type=int, default=DEFAULT_WINDOW_DAYS,
                        help="Rolling window in days (default 28)")
    parser.add_argument("--show",    action="store_true",
                        help="Print latest stored weights and exit")
    args = parser.parse_args()

    if args.show:
        w = load_latest_weights()
        if not w:
            print("No stored weights yet.")
            sys.exit(0)
        print(f"computed_at: {w.get('_computed_at')}")
        print(f"window:      {w.get('_window_days')} days")
        print(f"eps:         {w.get('_exploration_eps')}")
        print(_fmt_weights(w))
        return

    weights = run(window_days=args.window, dry_run=args.dry_run)
    print(_fmt_weights(weights))


if __name__ == "__main__":
    main()
