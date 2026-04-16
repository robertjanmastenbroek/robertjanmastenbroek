"""
BTL Self-Assessment / Growth Score
==================================

A single 0–100 number that summarises whether the autonomous fleet is
making progress toward the 1M-listener North Star.

Components (weights sum to 1.0):
  listener_velocity     0.30  — week-over-week listener delta
  experiment_hit_rate   0.20  — successful / completed experiments
  pipeline_health       0.15  — new contacts vs target
  channel_diversity     0.10  — share of active channels with positive LEI
  content_performance   0.10  — average completion / engagement rate
  budget_efficiency     0.10  — listeners gained per € spent
  system_reliability    0.05  — agent-run success rate

Triggered actions (read top-down — first match wins):
  >= 80   stay_course
  >= 60   increase_discovery
  >= 40   emergency
  >= 20   red_alert
  <  20   system_pause
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import config

log = logging.getLogger("outreach.self_assessment")

# Keep this attribute mutable so tests can re-route the file.
SCORE_PATH: Path = Path(config.BASE_DIR).parent / "data" / "growth_score.json"


# ── component weights (must sum to 1.0) ────────────────────────────────────────

_WEIGHTS = {
    "listener_velocity":   0.30,
    "experiment_hit_rate": 0.20,
    "pipeline_health":     0.15,
    "channel_diversity":   0.10,
    "content_performance": 0.10,
    "budget_efficiency":   0.10,
    "system_reliability":  0.05,
}
assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, "self_assessment weights drifted"


# ── component scorers ──────────────────────────────────────────────────────────


def _score_listener_velocity(current: float, previous: float) -> tuple[float, str]:
    """+10% week-over-week → 100. Flat → 50. -10% or worse → 0."""
    if previous <= 0:
        # Cold-start: any positive listener count counts as neutral progress.
        score = 50.0 if current > 0 else 0.0
        return score, f"current={current}, previous={previous} (cold start)"
    delta_pct = (current - previous) / previous * 100.0
    score = max(0.0, min(100.0, 50.0 + delta_pct * 5.0))
    return score, f"WoW delta = {delta_pct:+.1f}%"


def _score_experiment_hit_rate(succeeded: int, completed: int) -> tuple[float, str]:
    if completed <= 0:
        return 0.0, "no completed experiments yet"
    rate = succeeded / completed
    return rate * 100.0, f"{succeeded}/{completed} experiments succeeded ({rate:.0%})"


def _score_pipeline_health(added: int, target: int) -> tuple[float, str]:
    if target <= 0:
        return 0.0, "no contact target set"
    rate = added / target
    score = max(0.0, min(100.0, rate * 100.0))
    return score, f"{added}/{target} contacts added ({rate:.0%})"


def _score_channel_diversity(positive: int, total: int) -> tuple[float, str]:
    if total <= 0:
        return 0.0, "no active channels"
    rate = positive / total
    return rate * 100.0, f"{positive}/{total} channels with positive LEI"


def _score_content_performance(completion: float) -> tuple[float, str]:
    """Treat 1.0 (=100% completion) as a perfect score."""
    score = max(0.0, min(100.0, completion * 100.0))
    return score, f"avg completion rate = {completion:.0%}"


def _score_budget_efficiency(
    listeners_per_eur: float,
    has_budget: bool,
) -> tuple[float, str]:
    if not has_budget:
        return 50.0, "no paid spend (free tier neutral)"
    # 5 listeners per € is "good" → 100. Cap there.
    score = max(0.0, min(100.0, listeners_per_eur * 20.0))
    return score, f"{listeners_per_eur:.2f} listeners per €"


def _score_system_reliability(total: int, failed: int) -> tuple[float, str]:
    if total <= 0:
        return 0.0, "no agent runs recorded"
    rate = max(0.0, (total - failed) / total)
    return rate * 100.0, f"{total - failed}/{total} runs successful ({rate:.0%})"


# ── public API ─────────────────────────────────────────────────────────────────


def calculate_score(
    listeners_current: float = 0,
    listeners_previous: float = 0,
    experiments_succeeded: int = 0,
    experiments_completed: int = 0,
    contacts_added: int = 0,
    contacts_target: int = 50,
    active_channels_positive: int = 0,
    active_channels_total: int = 1,
    avg_completion_rate: float = 0.0,
    listeners_per_eur: float = 0.0,
    has_budget: bool = False,
    agent_runs_total: int = 1,
    agent_runs_failed: int = 0,
) -> dict:
    """
    Compute the growth-score snapshot.

    Returns:
        {
          "total_score": float (0–100),
          "calculated_at": ISO-8601 UTC,
          "components": {
              <name>: {"weight": float, "score": float, "detail": str},
              ...
          }
        }
    """
    components: dict[str, dict] = {}

    score, detail = _score_listener_velocity(listeners_current, listeners_previous)
    components["listener_velocity"] = {
        "weight": _WEIGHTS["listener_velocity"], "score": score, "detail": detail
    }

    score, detail = _score_experiment_hit_rate(experiments_succeeded, experiments_completed)
    components["experiment_hit_rate"] = {
        "weight": _WEIGHTS["experiment_hit_rate"], "score": score, "detail": detail
    }

    score, detail = _score_pipeline_health(contacts_added, contacts_target)
    components["pipeline_health"] = {
        "weight": _WEIGHTS["pipeline_health"], "score": score, "detail": detail
    }

    score, detail = _score_channel_diversity(active_channels_positive, active_channels_total)
    components["channel_diversity"] = {
        "weight": _WEIGHTS["channel_diversity"], "score": score, "detail": detail
    }

    score, detail = _score_content_performance(avg_completion_rate)
    components["content_performance"] = {
        "weight": _WEIGHTS["content_performance"], "score": score, "detail": detail
    }

    score, detail = _score_budget_efficiency(listeners_per_eur, has_budget)
    components["budget_efficiency"] = {
        "weight": _WEIGHTS["budget_efficiency"], "score": score, "detail": detail
    }

    score, detail = _score_system_reliability(agent_runs_total, agent_runs_failed)
    components["system_reliability"] = {
        "weight": _WEIGHTS["system_reliability"], "score": score, "detail": detail
    }

    total = sum(c["weight"] * c["score"] for c in components.values())
    return {
        "total_score": round(total, 2),
        "calculated_at": datetime.utcnow().isoformat(),
        "components": components,
    }


def get_triggered_action(score: float) -> dict:
    """Map a score onto a fleet action level."""
    if score >= getattr(config, "BTL_SCORE_STAY_COURSE", 80):
        return {
            "level": "stay_course",
            "description": "Healthy growth — keep current allocations and cadence.",
        }
    if score >= getattr(config, "BTL_SCORE_INCREASE_DISCOVERY", 60):
        return {
            "level": "increase_discovery",
            "description": "Growth slowing — increase discovery + experiment cadence.",
        }
    if score >= getattr(config, "BTL_SCORE_EMERGENCY", 40):
        return {
            "level": "emergency",
            "description": "Underperforming — reallocate weight to top-LEI channels and "
                           "drop bottom-quartile experiments.",
        }
    if score >= getattr(config, "BTL_SCORE_RED_ALERT", 20):
        return {
            "level": "red_alert",
            "description": "Critical — request veto-level human review and pause "
                           "spend on low-confidence channels.",
        }
    return {
        "level": "system_pause",
        "description": "System-wide pause — halt non-essential agents and wait for "
                       "operator input.",
    }


def save_score(score_result: dict) -> None:
    """Append a score snapshot to the on-disk history file."""
    SCORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    history: list[dict] = []
    if SCORE_PATH.exists():
        try:
            with SCORE_PATH.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, list):
                    history = loaded
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("growth_score.json unreadable (%s) — starting fresh", exc)
            history = []
    history.append(score_result)
    with SCORE_PATH.open("w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=2)


def get_score_history(limit: int = 12) -> list[dict]:
    """Return the most recent `limit` score snapshots, newest last."""
    if not SCORE_PATH.exists():
        return []
    try:
        with SCORE_PATH.open("r", encoding="utf-8") as fh:
            history = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(history, list):
        return []
    return history[-limit:]


__all__ = [
    "calculate_score",
    "get_triggered_action",
    "save_score",
    "get_score_history",
    "SCORE_PATH",
]
