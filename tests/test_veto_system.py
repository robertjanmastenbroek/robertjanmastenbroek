"""Tests for veto system — proposal queue and execution (BTL protocol)."""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Use a real temp-file DB (not :memory:) so connections opened in different
# context-managed `with db.get_conn()` blocks see the same data.
_tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tf.close()
os.environ["RJM_DB_PATH"] = _tf.name

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
import config
config.DB_PATH = Path(os.environ["RJM_DB_PATH"])

import db
import btl_db

# Force `db.DB_PATH` (imported from config at module load time) to point at
# our temp file even when other test modules have already mutated it.
db.DB_PATH = Path(os.environ["RJM_DB_PATH"])

db.init_db()
btl_db.init_btl_tables()

from veto_system import (
    create_proposal,
    veto_proposal,
    veto_all,
    get_pending_proposals,
    get_due_proposals,
    execute_proposal,
    list_proposals,
    build_digest_body,
)


def setup_function():
    """Wipe proposals + experiments before every test, re-pin DB path."""
    db.DB_PATH = Path(os.environ["RJM_DB_PATH"])
    config.DB_PATH = Path(os.environ["RJM_DB_PATH"])
    # Re-init schema in case the file was wiped by a sibling module.
    db.init_db()
    btl_db.init_btl_tables()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM proposals")
        conn.execute("DELETE FROM experiments")


def teardown_module():
    """Remove the temp database when the module finishes."""
    try:
        os.unlink(_tf.name)
    except OSError:
        pass


# ─── core proposal lifecycle ────────────────────────────────────────────────

def test_create_proposal():
    pid = create_proposal(
        proposal_type="new_experiment",
        title="Test Reddit r/melodictechno",
        description="Post 2x/week with production stories",
        hypothesis="50+ profile visits per post",
        risk_level="low",
        estimated_impact="500-800 listeners/month",
    )
    assert pid.startswith("prop_")
    proposals = list_proposals()
    assert len(proposals) == 1
    assert proposals[0]["status"] == "pending"


def test_veto_proposal():
    pid = create_proposal(
        proposal_type="new_experiment",
        title="Test",
        description="test",
    )
    veto_proposal(pid, reason="Not ready for this channel")
    proposals = list_proposals(status="vetoed")
    assert len(proposals) == 1
    assert "Not ready" in proposals[0]["veto_reason"]


def test_get_pending_proposals():
    create_proposal(
        proposal_type="new_experiment", title="A", description="a"
    )
    create_proposal(
        proposal_type="reallocation", title="B", description="b"
    )
    pending = get_pending_proposals()
    assert len(pending) == 2


def test_get_due_proposals():
    """Proposals past their veto window should appear in due list."""
    pid = create_proposal(
        proposal_type="new_experiment",
        title="Past due",
        description="test",
    )
    # Manually backdate execute_after.
    with db.get_conn() as conn:
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        conn.execute(
            "UPDATE proposals SET execute_after=? WHERE id=?", (past, pid)
        )
    due = get_due_proposals()
    assert len(due) == 1


def test_execute_proposal():
    pid = create_proposal(
        proposal_type="new_experiment",
        title="Reddit experiment",
        description="test",
        hypothesis="50 visits",
    )
    # Backdate so it's due.
    with db.get_conn() as conn:
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        conn.execute(
            "UPDATE proposals SET execute_after=? WHERE id=?", (past, pid)
        )

    result = execute_proposal(pid)
    assert result["executed"] is True

    # Proposal should be marked executed.
    proposals = list_proposals(status="executed")
    assert len(proposals) == 1
