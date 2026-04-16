"""
Veto system — proposal queue, 24hr veto window, digest body, execution.

The Boil-the-Lake (BTL) protocol uses a propose-and-execute-with-veto-window
control loop for human oversight of the autonomous fleet:

    create_proposal → status='pending' (execute_after = now + N hours)
        ↓
    BTL_VETO_WINDOW_HOURS pass without veto
        ↓
    get_due_proposals() surfaces it; scheduler calls execute_proposal()
        ↓
    For 'new_experiment' proposals → spawns an Experiment via experiment_engine

A vetoed proposal is terminal (status='vetoed', veto_reason recorded).
The daily digest (build_digest_body) lists today's executions, the pending
queue, and recently executed proposals so the operator has one place to
intervene each morning.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import db
import experiment_engine
from config import BTL_VETO_WINDOW_HOURS

log = logging.getLogger(__name__)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """UTC timestamp in ISO-8601, second precision (matches sqlite datetime())."""
    return datetime.utcnow().isoformat()


def _make_id() -> str:
    """Generate a human-readable, sortable proposal ID.

    Format: ``prop_YYYY-MM-DD_HHMMSS`` with a 3-digit millisecond suffix
    appended only when needed to disambiguate IDs created in the same second
    (mirrors the pattern used for experiment IDs).
    """
    now = datetime.utcnow()
    base = f"prop_{now.strftime('%Y-%m-%d_%H%M%S')}"

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM proposals WHERE id=?", (base,)
        ).fetchone()
        if row is None:
            return base

        ms = f"{now.microsecond // 1000:03d}"
        candidate = f"prop_{now.strftime('%Y-%m-%d_%H%M%S')}_{ms}"
        for i in range(1000):
            tag = candidate if i == 0 else f"{candidate}_{i}"
            row = conn.execute(
                "SELECT 1 FROM proposals WHERE id=?", (tag,)
            ).fetchone()
            if row is None:
                return tag
        # Astronomically unlikely.
        raise RuntimeError("Could not generate unique proposal ID")


# ─── create / veto ───────────────────────────────────────────────────────────

def create_proposal(
    proposal_type: str,
    title: str,
    description: str,
    hypothesis: str = "",
    risk_level: str = "low",
    estimated_impact: str = "",
) -> str:
    """Create a new proposal in the veto queue. Returns proposal ID.

    The proposal sits in 'pending' until ``execute_after`` (now +
    BTL_VETO_WINDOW_HOURS) elapses, at which point ``get_due_proposals``
    will surface it for ``execute_proposal``.
    """
    pid = _make_id()
    now = _now_iso()
    execute_after = (
        datetime.utcnow() + timedelta(hours=BTL_VETO_WINDOW_HOURS)
    ).isoformat()

    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO proposals
            (id, type, title, description, hypothesis, risk_level,
             estimated_impact, proposed_at, execute_after, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                pid, proposal_type, title, description, hypothesis,
                risk_level, estimated_impact, now, execute_after, now,
            ),
        )

    log.info("Created proposal %s: %s", pid, title)
    return pid


def veto_proposal(proposal_id: str, reason: str = "") -> None:
    """Veto a pending proposal. Idempotent — no-op if not 'pending'."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE proposals SET status='vetoed', veto_reason=? "
            "WHERE id=? AND status='pending'",
            (reason, proposal_id),
        )
    log.info("Vetoed proposal %s: %s", proposal_id, reason)


def veto_all(reason: str = "Emergency brake") -> int:
    """Veto every pending proposal. Returns the count vetoed.

    Useful as a kill-switch when the operator wants to pause the autonomous
    loop wholesale (e.g. before a launch, during an outage, or while
    debugging the fleet).
    """
    with db.get_conn() as conn:
        cursor = conn.execute(
            "UPDATE proposals SET status='vetoed', veto_reason=? "
            "WHERE status='pending'",
            (reason,),
        )
        return cursor.rowcount


# ─── read / scheduling ───────────────────────────────────────────────────────

def get_pending_proposals() -> list:
    """All proposals still in the veto window, oldest-first."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM proposals WHERE status='pending' "
            "ORDER BY proposed_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_due_proposals() -> list:
    """Pending proposals whose veto window has elapsed.

    These are ready for the scheduler to pass to ``execute_proposal``.
    """
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM proposals WHERE status='pending' "
            "AND datetime(execute_after) <= datetime('now') "
            "ORDER BY proposed_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def list_proposals(
    status: Optional[str] = None,
    limit: int = 50,
) -> list:
    """List proposals, optionally filtered by status. Newest first."""
    query = "SELECT * FROM proposals WHERE 1=1"
    params: list = []
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with db.get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# ─── execute ─────────────────────────────────────────────────────────────────

def execute_proposal(proposal_id: str) -> dict:
    """Execute a proposal that has passed its veto window.

    For ``type='new_experiment'`` proposals, this spawns the corresponding
    experiment via ``experiment_engine.propose_experiment`` +
    ``start_experiment``. The proposal row is updated to status='executed'
    with ``executed_at`` and (when applicable) ``experiment_id`` recorded.

    Returns ``{"executed": bool, "experiment_id": str|None}``. Returns
    ``{"executed": False, "reason": ...}`` if the proposal is missing or no
    longer pending.

    Concurrency: claims the row atomically via
    ``UPDATE ... WHERE status='pending'`` and guards on ``rowcount`` so two
    concurrent callers (e.g. scheduler + manual CLI run) cannot double-spawn
    the same experiment. The intermediate ``status='executing'`` state means
    a crash during experiment spawn leaves the row visible as stuck
    executing rather than silently rolling back to pending.
    """
    # Atomic claim: only the caller whose UPDATE affects a row proceeds.
    # The second caller sees rowcount==0 and exits cleanly.
    with db.get_conn() as conn:
        cursor = conn.execute(
            "UPDATE proposals SET status='executing' "
            "WHERE id=? AND status='pending'",
            (proposal_id,),
        )
        claimed = cursor.rowcount == 1

    if not claimed:
        # Disambiguate: missing row vs. already-claimed row.
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT status FROM proposals WHERE id=?", (proposal_id,)
            ).fetchone()
        if not row:
            return {"executed": False, "reason": "Not found"}
        return {"executed": False, "reason": f"Status is {row['status']}"}

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM proposals WHERE id=?", (proposal_id,)
        ).fetchone()
    proposal = dict(row)
    result: dict = {"executed": True, "experiment_id": None}

    if proposal["type"] == "new_experiment":
        # Best-effort channel guess from the title; real callers will normally
        # pass a structured proposal where the title ends with the channel
        # name (e.g. "Test Reddit r/melodictechno" → "r/melodictechno", or
        # "Reddit experiment" → "experiment"). The experiment engine doesn't
        # validate the channel string — it's just a tag — so this is safe.
        title_tail = (proposal.get("title") or "unknown").split()[-1].lower()
        exp_id = experiment_engine.propose_experiment(
            channel=title_tail,
            hypothesis=proposal.get("hypothesis") or "",
            tactic=proposal.get("description") or "",
        )
        experiment_engine.start_experiment(exp_id)
        result["experiment_id"] = exp_id

    with db.get_conn() as conn:
        conn.execute(
            "UPDATE proposals SET status='executed', executed_at=?, "
            "experiment_id=? WHERE id=?",
            (_now_iso(), result.get("experiment_id"), proposal_id),
        )

    log.info("Executed proposal %s (experiment_id=%s)",
             proposal_id, result.get("experiment_id"))
    return result


# ─── digest ──────────────────────────────────────────────────────────────────

def build_digest_body() -> str:
    """Build the daily BTL digest body for RJM.

    Sections:
      * EXECUTING TODAY — proposals past their veto window, about to fire.
      * PENDING — still in the veto window (with execute_after timestamp).
      * RECENTLY EXECUTED — last 5 proposals that have already run.

    Each due proposal includes the veto command the operator can reply with
    (``veto <id>``) so the digest is one-click actionable from email.
    """
    due = get_due_proposals()
    pending = get_pending_proposals()

    lines = ["=== BTL Daily Digest ===\n"]

    if due:
        lines.append(f"EXECUTING TODAY ({len(due)} proposals):\n")
        for p in due:
            lines.append(f"  [{p['id']}] {p['title']}")
            lines.append(
                f"    Risk: {p['risk_level']} | "
                f"Impact: {p['estimated_impact']}"
            )
            lines.append(f"    Reply 'veto {p['id']}' to block\n")
    else:
        lines.append("No proposals executing today.\n")

    if pending:
        # Filter out the ones already shown under EXECUTING TODAY.
        due_ids = {d["id"] for d in due}
        remaining = [p for p in pending if p["id"] not in due_ids]
        if remaining:
            lines.append(f"\nPENDING ({len(remaining)} in veto window):\n")
            for p in remaining:
                exec_at = (p["execute_after"] or "")[:16]
                lines.append(
                    f"  [{p['id']}] {p['title']} (executes: {exec_at})"
                )

    recent_executed = list_proposals(status="executed", limit=5)
    if recent_executed:
        lines.append("\n\nRECENTLY EXECUTED:\n")
        for p in recent_executed:
            ran_at = (p.get("executed_at") or "?")[:16]
            lines.append(f"  [{p['id']}] {p['title']} (at {ran_at})")

    return "\n".join(lines)
