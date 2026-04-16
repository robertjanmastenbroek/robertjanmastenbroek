"""
Thompson Sampling bandit framework — BTL protocol Lake 2.

A multi-armed bandit per growth channel. Each "arm dimension" (hook, subject
line, send hour, etc.) is sampled independently via a Beta(alpha, beta)
posterior, then epsilon-greedy noise is layered on top to keep exploring
underdog options. State is persisted in the `bandit_state` table so the bandit
survives process restarts and shares learning across agent runs.

Why this shape:
- Reward is a 0..1 binary outcome (reply / no reply, click / no click), which
  matches the Beta-Bernoulli conjugate prior exactly. alpha = 1 + successes,
  beta = 1 + failures.
- Cold start: until every arm value has at least BTL_BANDIT_COLD_START_MIN
  samples, draws are uniformly random. Otherwise, any early lucky run on a
  brand-new arm would lock the bandit in (premature exploitation).
- Epsilon-greedy on top of TS: even with strong evidence, EXPLORE_WARM=10% of
  draws stay random so we keep noticing taste shifts in our audience over time.
- Breakthroughs: the BTL protocol's "outlier hunting" job — surface arm values
  whose mean reward is OUTLIER_MULTIPLIER (=2.0) times the channel-wide mean,
  with enough samples to trust them. These bubble up to the weekly review.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any

import numpy as np

import db
import config

log = logging.getLogger("bandit")


class Bandit:
    """Thompson Sampling bandit with per-channel state persistence."""

    def __init__(self, channel: str, arms: dict[str, list[str]]) -> None:
        """
        Args:
            channel: stable identifier — typically a growth channel id
                ("outreach_email", "tiktok_clip", "ig_reel"). State is
                partitioned by this key.
            arms: mapping of arm dimension → list of candidate values, e.g.
                {"hook": ["scarcity", "curiosity"], "send_hour": ["09", "21"]}.
                Each dimension is sampled independently.
        """
        if not channel:
            raise ValueError("channel must be a non-empty string")
        if not arms:
            raise ValueError("arms must contain at least one dimension")
        for name, values in arms.items():
            if not values:
                raise ValueError(f"arm dimension '{name}' has no values")

        self.channel = channel
        self.arms = {name: list(values) for name, values in arms.items()}

    # ─── Selection ────────────────────────────────────────────────────────────

    def select(self) -> dict[str, str]:
        """Choose one value per arm dimension via Thompson Sampling.

        Returns:
            dict mapping arm_name → chosen value. Always returns a value for
            every dimension declared in __init__.
        """
        state = self._load_state()
        choice: dict[str, str] = {}

        for arm_name, values in self.arms.items():
            choice[arm_name] = self._select_one(arm_name, values, state)

        return choice

    def _select_one(
        self,
        arm_name: str,
        values: list[str],
        state: dict[tuple[str, str], dict[str, Any]],
    ) -> str:
        """Pick one value for a single arm dimension."""
        # Sample counts per value (default 0 if no row yet).
        sample_counts = [
            state.get((arm_name, v), {}).get("samples", 0) for v in values
        ]
        min_samples = min(sample_counts)

        # Cold start: any value below the threshold → uniform random across all
        # values. This guarantees every arm gets a baseline before we trust TS.
        if min_samples < config.BTL_BANDIT_COLD_START_MIN:
            return random.choice(values)

        # Epsilon-greedy exploration. Once warm, drop to a smaller epsilon so
        # we still occasionally pick a non-best arm to track drift.
        warm = min_samples >= config.BTL_BANDIT_WARM_THRESHOLD
        epsilon = (
            config.BTL_BANDIT_EXPLORE_WARM if warm else config.BTL_BANDIT_EXPLORE_COLD
        )
        if random.random() < epsilon:
            return random.choice(values)

        # Thompson Sampling: draw one sample from each value's Beta posterior,
        # pick the value whose draw is highest.
        draws = []
        for v in values:
            row = state.get((arm_name, v))
            alpha = float(row["alpha"]) if row else 1.0
            beta = float(row["beta"]) if row else 1.0
            draws.append(np.random.beta(alpha, beta))

        best_idx = int(np.argmax(draws))
        return values[best_idx]

    # ─── Recording outcomes ───────────────────────────────────────────────────

    def record(self, arm_values: dict[str, str], reward: float) -> None:
        """Record a single play and its observed reward.

        Args:
            arm_values: the (arm_name → value) dict that was actually used.
                Typically the dict returned by a previous `select()` call.
            reward: outcome in [0.0, 1.0]. For binary outcomes use 0 or 1;
                fractional rewards are supported (alpha += reward,
                beta += 1 - reward) for things like normalised CTR.
        """
        if not arm_values:
            return
        # Clamp to [0, 1] — defensive; callers should already do this.
        reward = max(0.0, min(1.0, float(reward)))
        now = datetime.now().isoformat()

        with db.get_conn() as conn:
            for arm_name, arm_value in arm_values.items():
                if arm_name not in self.arms:
                    log.warning(
                        "bandit %s: ignoring unknown arm '%s'",
                        self.channel, arm_name,
                    )
                    continue

                conn.execute(
                    """
                    INSERT INTO bandit_state
                        (channel, arm_name, arm_value, alpha, beta,
                         samples, last_updated)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(channel, arm_name, arm_value) DO UPDATE SET
                        alpha = alpha + excluded.alpha - 1.0,
                        beta = beta + excluded.beta - 1.0,
                        samples = samples + 1,
                        last_updated = excluded.last_updated
                    """,
                    (
                        self.channel,
                        arm_name,
                        arm_value,
                        1.0 + reward,            # prior + new evidence
                        1.0 + (1.0 - reward),    # prior + new evidence
                        now,
                    ),
                )

    # ─── Inspection ───────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, list[dict]]:
        """Return current posterior summary per arm dimension.

        Output shape:
            {
              arm_name: [
                {"value": str, "samples": int, "mean_reward": float,
                 "alpha": float, "beta": float},
                ...
              ],
              ...
            }
        Only arm values that have at least one row in `bandit_state` are
        included; cold values are omitted (their effective stats are the
        Beta(1,1) prior, which is mean=0.5 with samples=0).
        """
        state = self._load_state()
        result: dict[str, list[dict]] = {arm: [] for arm in self.arms}

        for arm_name, values in self.arms.items():
            for v in values:
                row = state.get((arm_name, v))
                if not row:
                    continue
                samples = int(row["samples"])
                alpha = float(row["alpha"])
                beta = float(row["beta"])
                # Mean reward = empirical success rate.
                # alpha = 1 (prior) + successes, so successes = alpha - 1.
                successes = max(0.0, alpha - 1.0)
                mean_reward = (successes / samples) if samples else 0.0
                result[arm_name].append({
                    "value": v,
                    "samples": samples,
                    "mean_reward": mean_reward,
                    "alpha": alpha,
                    "beta": beta,
                })

        return result

    def detect_breakthroughs(self) -> list[dict]:
        """Identify outlier-good arm values worth amplifying.

        An arm value is a "breakthrough" if:
          1. It has at least BTL_BANDIT_COLD_START_MIN samples (so the mean
             is not a fluke), AND
          2. Its mean reward exceeds BTL_BANDIT_OUTLIER_MULTIPLIER × the
             overall mean reward across all sampled values for that channel.

        Returns:
            list of dicts, each with keys:
              arm_name, value, samples, mean_reward, overall_mean, ratio
            Sorted by ratio descending so the strongest outliers come first.
        """
        stats = self.get_stats()

        # Compute overall mean across every sampled (arm, value) — i.e. total
        # successes / total samples, channel-wide.
        total_samples = 0
        total_successes = 0.0
        for arm_rows in stats.values():
            for row in arm_rows:
                total_samples += row["samples"]
                total_successes += row["mean_reward"] * row["samples"]

        if total_samples == 0:
            return []

        overall_mean = total_successes / total_samples
        threshold = config.BTL_BANDIT_OUTLIER_MULTIPLIER * overall_mean
        cold_min = config.BTL_BANDIT_COLD_START_MIN

        breakthroughs: list[dict] = []
        for arm_name, arm_rows in stats.items():
            for row in arm_rows:
                if row["samples"] < cold_min:
                    continue
                if row["mean_reward"] > threshold:
                    breakthroughs.append({
                        "arm_name": arm_name,
                        "value": row["value"],
                        "samples": row["samples"],
                        "mean_reward": row["mean_reward"],
                        "overall_mean": overall_mean,
                        "ratio": (
                            row["mean_reward"] / overall_mean
                            if overall_mean > 0 else float("inf")
                        ),
                    })

        breakthroughs.sort(key=lambda x: x["ratio"], reverse=True)
        return breakthroughs

    # ─── Internals ────────────────────────────────────────────────────────────

    def _load_state(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Snapshot all rows for this channel keyed by (arm_name, arm_value)."""
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT arm_name, arm_value, alpha, beta, samples, last_updated "
                "FROM bandit_state WHERE channel = ?",
                (self.channel,),
            ).fetchall()
        return {(r["arm_name"], r["arm_value"]): dict(r) for r in rows}
