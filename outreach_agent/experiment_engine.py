"""
Experiment lifecycle management for the Boil-the-Lake (BTL) protocol.

An experiment moves through these states:

    proposed → active → completed → analyzed
        ↘ vetoed

Each experiment carries a hypothesis, tactic, expected metric/target,
duration, success/failure criteria, optional cost, and a JSON metrics_log
that the daily analytics loop appends to.

Two safety properties enforced here:

  * BTL_MAX_CONCURRENT_EXPERIMENTS — prevents running too many bets at once
    (cognitive load + signal interference). `can_start_new()` is the gate.
  * BTL_VETO_WINDOW_HOURS — every proposal sits for N hours before
    `get_pending_proposals()` will surface it for execution, giving the
    operator a chance to veto via the morning digest.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import db
from config import (
    BTL_MAX_CONCURRENT_EXPERIMENTS,
    BTL_VETO_WINDOW_HOURS,
)

log = logging.getLogger(__name__)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """UTC timestamp in ISO-8601, second precision (matches sqlite datetime())."""
    return datetime.utcnow().isoformat()


def _make_id(channel: str) -> str:
    """Generate a human-readable, sortable experiment ID.

    Format: ``exp_YYYY-MM-DD_HHMMSS_channel`` with a 3-digit millisecond
    suffix appended only when needed to disambiguate IDs created in the same
    second (e.g. a batch loop inside a test or a scheduler tick proposing
    multiple experiments at once).
    """
    now = datetime.utcnow()
    base = f"exp_{now.strftime('%Y-%m-%d_%H%M%S')}_{channel}"

    # Fast path: if the base ID is unused, return it. Otherwise tack on
    # millisecond precision (and then a counter as final fallback).
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM experiments WHERE id=?", (base,)
        ).fetchone()
        if row is None:
            return base

        ms = f"{now.microsecond // 1000:03d}"
        candidate = f"exp_{now.strftime('%Y-%m-%d_%H%M%S')}_{ms}_{channel}"
        for i in range(1000):
            tag = candidate if i == 0 else f"{candidate}_{i}"
            row = conn.execute(
                "SELECT 1 FROM experiments WHERE id=?", (tag,)
            ).fetchone()
            if row is None:
                return tag
        # Astronomically unlikely.
        raise RuntimeError("Could not generate unique experiment ID")


# ─── propose / read ──────────────────────────────────────────────────────────

def propose_experiment(
    channel: str,
    hypothesis: str,
    tactic: str,
    duration_days: int = 21,
    success_criteria: str = "",
    failure_criteria: str = "",
    cost_type: str = "free",
    cost_estimate: float = 0.0,
    expected_metric: str = "",
    expected_target: float = 0.0,
    expected_confidence: float = 0.5,
    guardrails: Optional[list] = None,
) -> str:
    """Create a new experiment in 'proposed' status. Returns experiment ID.

    The experiment will sit in 'proposed' for BTL_VETO_WINDOW_HOURS before
    becoming eligible for execution (see ``get_pending_proposals``).
    """
    exp_id = _make_id(channel)
    now = _now_iso()
    execute_after = (
        datetime.utcnow() + timedelta(hours=BTL_VETO_WINDOW_HOURS)
    ).isoformat()

    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO experiments
            (id, channel, hypothesis, tactic, cost_type, cost_estimate,
             expected_metric, expected_target, expected_confidence,
             duration_days, success_criteria, failure_criteria, guardrails,
             status, proposed_at, execute_after, metrics_log, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'proposed', ?, ?, '[]', ?)""",
            (
                exp_id, channel, hypothesis, tactic, cost_type, cost_estimate,
                expected_metric, expected_target, expected_confidence,
                duration_days, success_criteria, failure_criteria,
                json.dumps(guardrails or []),
                now, execute_after, now,
            ),
        )

    log.info("Proposed experiment %s on channel %s", exp_id, channel)
    return exp_id


def get_experiment(exp_id: str) -> Optional[dict]:
    """Fetch a single experiment by ID, or None if not found."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM experiments WHERE id=?", (exp_id,)
        ).fetchone()
        return dict(row) if row else None


def list_experiments(
    status: Optional[str] = None,
    channel: Optional[str] = None,
    limit: int = 50,
) -> list:
    """List experiments, optionally filtered by status and/or channel.

    Results are returned newest-first (by ``created_at`` DESC), capped at
    ``limit`` rows.
    """
    query = "SELECT * FROM experiments WHERE 1=1"
    params: list = []
    if status:
        query += " AND status=?"
        params.append(status)
    if channel:
        query += " AND channel=?"
        params.append(channel)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with db.get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# ─── lifecycle transitions ───────────────────────────────────────────────────

def start_experiment(exp_id: str) -> None:
    """Transition experiment proposed → active. Stamps ``started_at``."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET status='active', started_at=? WHERE id=?",
            (_now_iso(), exp_id),
        )
    log.info("Started experiment %s", exp_id)


def complete_experiment(exp_id: str) -> None:
    """Transition experiment active → completed. Stamps ``ended_at``."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET status='completed', ended_at=? WHERE id=?",
            (_now_iso(), exp_id),
        )
    log.info("Completed experiment %s", exp_id)


def analyze_experiment(exp_id: str, result: str, learning: str) -> None:
    """Record analysis results. Transitions completed → analyzed.

    ``result`` is a short verdict (e.g. "success", "failure", "inconclusive").
    ``learning`` is the prose takeaway fed back into the strategic insights
    loop.
    """
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET status='analyzed', result=?, learning=? "
            "WHERE id=?",
            (result, learning, exp_id),
        )
    log.info("Analyzed experiment %s: %s", exp_id, result)


def veto_experiment(exp_id: str, reason: str = "") -> None:
    """Veto a proposed experiment. No-op if the experiment is not 'proposed'.

    The reason is preserved in ``learning`` (prefixed with 'VETOED: ') so the
    decision is auditable later.
    """
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET status='vetoed', learning=? "
            "WHERE id=? AND status='proposed'",
            (f"VETOED: {reason}", exp_id),
        )
    log.info("Vetoed experiment %s: %s", exp_id, reason)


# ─── metric logging ──────────────────────────────────────────────────────────

def log_metric(exp_id: str, metric: dict) -> None:
    """Append a metric observation (dict) to an experiment's metrics_log.

    Silently no-ops if the experiment ID is unknown.

    Concurrency: the metrics_log column is a JSON blob we read, mutate,
    and write back. Without a write lock, two concurrent callers can
    both SELECT the same blob and one UPDATE silently overwrites the
    other's append. We issue ``BEGIN IMMEDIATE`` so the write lock is
    held across the SELECT+UPDATE — the second caller waits on the lock
    rather than racing.
    """
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT metrics_log FROM experiments WHERE id=?", (exp_id,)
        ).fetchone()
        if not row:
            return
        metrics = json.loads(row["metrics_log"] or "[]")
        metrics.append(metric)
        conn.execute(
            "UPDATE experiments SET metrics_log=? WHERE id=?",
            (json.dumps(metrics), exp_id),
        )


# ─── concurrency / scheduling ────────────────────────────────────────────────

def active_count() -> int:
    """Count experiments currently in 'active' status."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM experiments WHERE status='active'"
        ).fetchone()
        return int(row["n"])


def can_start_new() -> bool:
    """True if we're under the BTL concurrent-experiment cap."""
    return active_count() < BTL_MAX_CONCURRENT_EXPERIMENTS


def get_due_experiments() -> list:
    """Active experiments whose ``started_at + duration_days`` is in the past.

    Used by the analytics loop to know which experiments are ready to be
    completed and analyzed.
    """
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM experiments
            WHERE status='active'
              AND started_at IS NOT NULL
              AND datetime(started_at, '+' || duration_days || ' days')
                  <= datetime('now')
            ORDER BY started_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_proposals() -> list:
    """Proposed experiments whose veto window has elapsed.

    These are ready to be auto-started by the scheduler.
    """
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM experiments
            WHERE status='proposed'
              AND datetime(execute_after) <= datetime('now')
            ORDER BY proposed_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]
