"""Growth Brain — BTL protocol orchestrator.

Runs the three learning layers and ties together every other BTL component:

  L1 — Tactical optimization (4x/day): refresh every channel agent's bandit,
       surface "breakthrough" arm values whose mean reward is an outlier
       above the channel-wide mean. Publishes ``bandit.breakthrough`` events
       so the daily digest / fleet can amplify what's working.

  L2 — Strategic reallocation (weekly): hand off to
       ``strategy_portfolio.reallocate_weights`` to shift channel budget
       weights toward the channels with positive Listener Efficiency Index
       (LEI) over the last 7 days. Publishes ``channel.reallocated``.

  L3 — Discovery + invention (2x/week): a Claude-driven agent run that
       proposes new tactics / channels. The brain itself only exposes a
       stub here — the real work is invoked by the master agent.

In addition to the three layers the brain handles:

  * Veto execution     — execute proposals past their veto window, gated
                         by the BTL concurrent-experiment cap.
  * Self-assessment    — gather metrics, compute the 0–100 growth score,
                         persist it, and surface the triggered fleet action.
  * Brain status       — assemble a single dict snapshot of every BTL
                         dimension for dashboards / digests / the CLI.
  * Strategic insights — read/write to the ``strategic_insights`` SQLite
                         table that L3 (and any other learning loop) feeds.

Soft imports (``events``, ``fleet_state``) so the brain still works in
contexts where the event backbone or fleet-state heartbeats aren't wired
in (e.g. test environments or partial deployments).

Usage:
  python3 growth_brain.py status         # Full brain state
  python3 growth_brain.py l1             # Run L1 tactical optimization
  python3 growth_brain.py l2             # Run L2 strategic reallocation
  python3 growth_brain.py veto_check     # Execute due proposals
  python3 growth_brain.py assess         # Run self-assessment
  python3 growth_brain.py discover       # Stub — points at the master agent
  python3 growth_brain.py insights       # View strategic insights
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import List, Optional

import db
import btl_db
import experiment_engine
import strategy_portfolio
import veto_system
import self_assessment
import revenue_tracker

# ── Soft imports ─────────────────────────────────────────────────────────────
# Both `events` and `fleet_state` are optional integrations — degrade
# gracefully when they aren't importable so the brain still runs in a
# stripped-down test environment.
try:
    import events as _events
    _EVENTS = True
except ImportError:  # pragma: no cover — exercised only when events is absent
    _EVENTS = False

try:
    import fleet_state
    _FLEET = True
except ImportError:  # pragma: no cover — exercised only when fleet_state is absent
    _FLEET = False

log = logging.getLogger(__name__)


# ─── L1: Tactical optimization ───────────────────────────────────────────────


def run_l1_optimize() -> dict:
    """Layer 1: Tactical optimization — refresh every channel agent's bandit.

    Iterates every registered channel agent, snapshots its current bandit
    posterior (so callers can see what was sampled), and runs
    ``Bandit.detect_breakthroughs`` to find outlier-good arm values that
    deserve amplification. Each breakthrough is published as a
    ``bandit.breakthrough`` event for downstream consumers (digest,
    fleet_state, content schedulers).

    Returns:
        ``{"bandits_updated": int, "breakthroughs": int}``.
        Empty when no channel agents are registered (the function still
        returns the keyed dict — never raises).
    """
    if _FLEET:
        try:
            fleet_state.heartbeat("btl_l1", status="ok")
        except Exception as exc:  # noqa: BLE001 — heartbeat must never crash L1
            log.warning("L1 heartbeat failed: %s", exc)

    # Soft import so the brain doesn't hard-crash if channel_agents (which
    # depends on numpy via Bandit) isn't importable in this context.
    try:
        from channel_agents import list_agents
        agents = list_agents()
    except ImportError:
        agents = []

    updated = 0
    breakthroughs: List[dict] = []

    # Soft import Bandit — same defensive stance as channel_agents.
    try:
        from bandit_framework import Bandit
    except ImportError:
        Bandit = None  # type: ignore[assignment]

    for agent in agents:
        if not getattr(agent, "arms", None):
            continue

        stats = agent.get_bandit_stats()
        if stats:
            updated += 1

        if Bandit is None:
            continue

        b = Bandit(agent.channel_id, agent.arms)
        bts = b.detect_breakthroughs()
        if not bts:
            continue

        breakthroughs.extend(bts)
        if _EVENTS:
            for bt in bts:
                try:
                    _events.publish("bandit.breakthrough", "growth_brain", bt)
                except Exception as exc:  # noqa: BLE001 — never let events crash L1
                    log.warning("publish(bandit.breakthrough) failed: %s", exc)

    result = {"bandits_updated": updated, "breakthroughs": len(breakthroughs)}
    log.info(
        "L1 optimize: %d bandits updated, %d breakthroughs",
        updated, len(breakthroughs),
    )
    return result


# ─── L2: Strategic reallocation ──────────────────────────────────────────────


def run_l2_reallocate() -> dict:
    """Layer 2: Strategic reallocation — shift channel weights via LEI.

    Delegates to ``strategy_portfolio.reallocate_weights`` (which loads the
    registry, computes z-scores from each active channel's 7-day LEI, nudges
    weights toward the signal, clamps + renormalises, and persists the
    registry). Publishes a ``channel.reallocated`` event.

    Returns:
        ``{"channels_reallocated": int}`` — count of currently-active
        channels in the registry after reallocation. Zero is a valid
        result (e.g. fresh install with an empty registry).
    """
    if _FLEET:
        try:
            fleet_state.heartbeat("btl_l2", status="ok")
        except Exception as exc:  # noqa: BLE001
            log.warning("L2 heartbeat failed: %s", exc)

    reg = strategy_portfolio.reallocate_weights()
    active = [
        c for c in reg.get("channels", [])
        if c.get("status") == "active"
    ]

    if _EVENTS:
        try:
            _events.publish("channel.reallocated", "growth_brain", {
                "active_channels": len(active),
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("publish(channel.reallocated) failed: %s", exc)

    result = {"channels_reallocated": len(active)}
    log.info("L2 reallocate: %d active channels", len(active))
    return result


# ─── Veto execution ──────────────────────────────────────────────────────────


def run_veto_check() -> dict:
    """Execute proposals that have passed their veto window.

    Each due proposal is gated by ``experiment_engine.can_start_new()`` —
    if we're already at the BTL concurrent-experiment cap the proposal
    stays pending (it will surface again on the next veto check). When a
    proposal does fire, a ``proposal.executed`` event is published.

    Returns:
        ``{"proposals_executed": int, "proposals_due": int}``.
    """
    due = veto_system.get_due_proposals()
    executed = 0
    for proposal in due:
        if not experiment_engine.can_start_new():
            log.warning(
                "Cannot execute proposal %s — experiment limit reached",
                proposal["id"],
            )
            continue

        veto_system.execute_proposal(proposal["id"])
        executed += 1
        if _EVENTS:
            try:
                _events.publish("proposal.executed", "growth_brain", {
                    "proposal_id": proposal["id"],
                    "title": proposal.get("title", ""),
                })
            except Exception as exc:  # noqa: BLE001
                log.warning("publish(proposal.executed) failed: %s", exc)

    result = {"proposals_executed": executed, "proposals_due": len(due)}
    log.info("Veto check: %d/%d proposals executed", executed, len(due))
    return result


# ─── Self-assessment ─────────────────────────────────────────────────────────


def run_self_assess(
    listeners_current: int = 0,
    listeners_previous: int = 0,
) -> dict:
    """Compute the 0–100 growth score, persist it, return with action.

    Gathers the inputs the score needs from the rest of the BTL stack:

      * active experiment count + analyzed-success rate (experiment_engine)
      * positive-LEI active channels (strategy_portfolio)
      * paid-spend signal (revenue_tracker)

    Listener velocity is the strongest weight in the score (0.30) but the
    brain doesn't know how to count Spotify listeners on its own — the
    caller passes ``listeners_current`` / ``listeners_previous`` (typically
    sourced by the master agent from ``data/listeners.json``).

    Returns the score dict from ``self_assessment.calculate_score``, with
    a ``triggered_action`` key appended.
    """
    # Gather metrics ────────────────────────────────────────────────────────
    completed = experiment_engine.list_experiments(status="analyzed")
    succeeded = sum(1 for e in completed if e.get("result") == "success")

    active_channels = strategy_portfolio.get_active_channels()
    positive_channels = 0
    for ch in active_channels:
        try:
            lei = strategy_portfolio.get_channel_lei(ch["id"], days=7)
        except Exception as exc:  # noqa: BLE001
            log.warning("get_channel_lei(%s) failed: %s", ch.get("id"), exc)
            lei = 0.0
        if lei > 0:
            positive_channels += 1

    budget = revenue_tracker.get_budget_summary()
    has_budget = budget.get("total_spent", 0) > 0

    score = self_assessment.calculate_score(
        listeners_current=listeners_current,
        listeners_previous=listeners_previous,
        experiments_succeeded=succeeded,
        experiments_completed=len(completed),
        active_channels_positive=positive_channels,
        active_channels_total=max(len(active_channels), 1),
        has_budget=has_budget,
    )

    self_assessment.save_score(score)

    action = self_assessment.get_triggered_action(score["total_score"])
    score["triggered_action"] = action

    if _EVENTS:
        try:
            _events.publish("score.calculated", "growth_brain", {
                "score": score["total_score"],
                "action": action["level"],
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("publish(score.calculated) failed: %s", exc)

    log.info(
        "Self-assessment: score=%s, action=%s",
        score["total_score"], action["level"],
    )
    return score


# ─── Brain status ────────────────────────────────────────────────────────────


def get_brain_status() -> dict:
    """Return a single snapshot of every BTL dimension.

    Used by the CLI (``growth_brain.py status``), the daily digest, and
    Task 12's CLI integration. Designed to be JSON-serialisable so it can
    be dumped straight to stdout or piped into ``rjm.py``.
    """
    active_exp = experiment_engine.list_experiments(status="active")
    pending_prop = veto_system.get_pending_proposals()
    active_ch = strategy_portfolio.get_active_channels()
    budget = revenue_tracker.get_budget_summary()
    score_history = self_assessment.get_score_history(limit=4)

    return {
        "active_experiments": len(active_exp),
        "experiments": [
            {
                "id": e["id"],
                "channel": e.get("channel"),
                "status": e.get("status"),
            }
            for e in active_exp
        ],
        "pending_proposals": len(pending_prop),
        "proposals": [
            {
                "id": p["id"],
                "title": p.get("title"),
                "execute_after": p.get("execute_after"),
            }
            for p in pending_prop
        ],
        "active_channels": len(active_ch),
        "channels": [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "weight": c.get("weight"),
            }
            for c in active_ch
        ],
        "budget": budget,
        "recent_scores": [
            {
                "score": s.get("total_score"),
                "date": (s.get("calculated_at") or "")[:10],
            }
            for s in score_history
        ],
    }


# ─── Strategic insights (L3 sink) ────────────────────────────────────────────


def get_strategic_insights(limit: int = 20) -> List[dict]:
    """Read recent rows from the ``strategic_insights`` table, newest first."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM strategic_insights "
            "ORDER BY discovered_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def save_strategic_insight(
    source: str,
    insight: str,
    confidence: float = 0.5,
    applicable_channels: Optional[List[str]] = None,
) -> None:
    """Insert one strategic insight row.

    ``applicable_channels`` is stored as a JSON-encoded list so callers can
    round-trip it cleanly via ``json.loads``.
    """
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO strategic_insights "
            "(source, insight, confidence, applicable_channels) "
            "VALUES (?, ?, ?, ?)",
            (
                source,
                insight,
                float(confidence),
                json.dumps(applicable_channels or []),
            ),
        )


# ─── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:  # pragma: no cover — thin CLI shim
    db.init_db()
    btl_db.init_btl_tables()

    if len(sys.argv) < 2:
        print(
            "Usage: python3 growth_brain.py "
            "[status|l1|l2|veto_check|assess|discover|insights]"
        )
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        print(json.dumps(get_brain_status(), indent=2, default=str))

    elif cmd == "l1":
        print(json.dumps(run_l1_optimize(), indent=2))

    elif cmd == "l2":
        print(json.dumps(run_l2_reallocate(), indent=2))

    elif cmd == "veto_check":
        print(json.dumps(run_veto_check(), indent=2))

    elif cmd == "assess":
        listeners = int(sys.argv[2]) if len(sys.argv) > 2 else 325
        prev = int(sys.argv[3]) if len(sys.argv) > 3 else 325
        result = run_self_assess(
            listeners_current=listeners, listeners_previous=prev,
        )
        print(f"\n=== Growth Health Score: {result['total_score']}/100 ===\n")
        for name, comp in result["components"].items():
            print(f"  {name}: {comp['score']} ({comp['detail']})")
        print(f"\nAction: {result['triggered_action']['description']}")

    elif cmd == "discover":
        print(
            "L3 Discovery is a Claude-driven agent run — "
            "invoke via master agent."
        )
        print("Use: python3 rjm.py brain discover")

    elif cmd == "insights":
        insights = get_strategic_insights()
        if not insights:
            print("No strategic insights yet.")
            return
        for ins in insights:
            confidence = ins.get("confidence", 0.0) or 0.0
            print(f"  [{confidence:.0%}] {ins['insight']}")
            print(
                f"    Source: {ins['source']} | "
                f"Channels: {ins.get('applicable_channels', '[]')}"
            )

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()


__all__ = [
    "run_l1_optimize",
    "run_l2_reallocate",
    "run_veto_check",
    "run_self_assess",
    "get_brain_status",
    "get_strategic_insights",
    "save_strategic_insight",
    "main",
]
