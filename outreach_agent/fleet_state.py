"""
Fleet state registry — shared awareness across all agents.

Each agent calls heartbeat() at start and end of its run.
master_agent reads get_all() and get_stale() for health checks.
"""

import json
import logging
from datetime import datetime, timedelta

import db

log = logging.getLogger("outreach.fleet_state")

# Expected cadence in minutes per agent — used to determine staleness (2× = stale)
EXPECTED_CADENCE = {
    "run_cycle":        30,
    "master_agent":     60,
    "discover_agent":   60,
    "research_agent":   60,
    "playlist_run":     60,
    "post_today":       1440,
    "spotify_tracker":  1440,
    "reply_detector":   30,
}
DEFAULT_CADENCE = 120


def heartbeat(agent_name: str, status: str = "ok", result: dict | None = None) -> None:
    """
    Record that an agent just ran.

    Args:
        agent_name: short name matching EXPECTED_CADENCE keys
        status: "ok" | "error"
        result: optional JSON-serialisable summary dict
    """
    now = datetime.utcnow().isoformat()
    try:
        result_json = json.dumps(result) if result else None
    except (TypeError, ValueError):
        result_json = None

    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT run_count, error_count FROM fleet_state WHERE agent_name=?",
            (agent_name,)
        ).fetchone()
        if existing:
            run_count   = (existing["run_count"] or 0) + 1
            error_count = (existing["error_count"] or 0) + (1 if status == "error" else 0)
            conn.execute(
                """UPDATE fleet_state
                   SET last_heartbeat=?, status=?, last_result=?, run_count=?, error_count=?
                   WHERE agent_name=?""",
                (now, status, result_json, run_count, error_count, agent_name)
            )
        else:
            conn.execute(
                """INSERT INTO fleet_state
                   (agent_name, last_heartbeat, status, last_result, run_count, error_count)
                   VALUES (?,?,?,?,1,?)""",
                (agent_name, now, status, result_json, 1 if status == "error" else 0)
            )
    log.debug("Heartbeat: %s status=%s", agent_name, status)


def get_all() -> list[dict]:
    """Return all agent records, most recently active first."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM fleet_state ORDER BY last_heartbeat DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_stale(threshold_minutes: int | None = None) -> list[dict]:
    """
    Return agents that haven't reported in longer than 2× their expected cadence.
    Pass threshold_minutes to override cadence-based calculation.
    """
    all_agents = get_all()
    now = datetime.utcnow()
    stale = []
    for agent in all_agents:
        cadence = threshold_minutes or EXPECTED_CADENCE.get(agent["agent_name"], DEFAULT_CADENCE)
        cutoff = now - timedelta(minutes=cadence * 2)
        try:
            last = datetime.fromisoformat(agent["last_heartbeat"])
        except (TypeError, ValueError):
            stale.append(agent)
            continue
        if last < cutoff:
            stale.append(agent)
    return stale


def summary_line(agent: dict) -> str:
    """One-line human-readable status for a single agent record."""
    status_icon = "\u2713" if agent["status"] == "ok" else "\u2717"
    return (
        f"  {status_icon} {agent['agent_name']:<20} "
        f"last={agent['last_heartbeat'][:16]}  "
        f"runs={agent['run_count']}  errors={agent['error_count']}"
    )
