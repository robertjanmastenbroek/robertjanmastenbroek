"""Tests for experiment lifecycle management (BTL protocol)."""
import json
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

from experiment_engine import (
    propose_experiment,
    get_experiment,
    list_experiments,
    start_experiment,
    complete_experiment,
    analyze_experiment,
    log_metric,
    active_count,
    can_start_new,
    get_due_experiments,
    get_pending_proposals,
    veto_experiment,
)


def setup_function():
    """Wipe the experiments table before every test.

    Re-pin `db.DB_PATH` in case another test module ran first and mutated it
    (test files are imported in collection order, but a sibling module's
    teardown may have deleted its own temp DB by then).
    """
    db.DB_PATH = Path(os.environ["RJM_DB_PATH"])
    config.DB_PATH = Path(os.environ["RJM_DB_PATH"])
    # Re-init the schema in case the file was wiped.
    db.init_db()
    btl_db.init_btl_tables()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM experiments")


def teardown_module():
    """Remove the temp database when the module finishes."""
    try:
        os.unlink(_tf.name)
    except OSError:
        pass


# ─── propose / get / list ────────────────────────────────────────────────────

def test_propose_creates_experiment():
    exp_id = propose_experiment(
        channel="reddit",
        hypothesis="Posts in r/melodictechno drive 50+ profile visits",
        tactic="Post 2x/week with production stories",
        duration_days=21,
        success_criteria="50+ profile visits per post",
        failure_criteria="<10 profile visits after 6 posts",
    )
    assert exp_id.startswith("exp_")
    exp = get_experiment(exp_id)
    assert exp is not None
    assert exp["status"] == "proposed"
    assert exp["channel"] == "reddit"
    assert exp["hypothesis"].startswith("Posts in")
    assert exp["duration_days"] == 21
    assert exp["proposed_at"] is not None
    assert exp["execute_after"] is not None
    # metrics_log initialized to '[]'
    assert json.loads(exp["metrics_log"]) == []


def test_propose_id_includes_channel():
    exp_id = propose_experiment(
        channel="tiktok", hypothesis="t", tactic="t"
    )
    # ID format: exp_YYYY-MM-DD_HHMMSS_channel
    assert exp_id.endswith("_tiktok")
    assert exp_id.startswith("exp_")


def test_propose_sets_execute_after_to_veto_window():
    before = datetime.utcnow()
    exp_id = propose_experiment(
        channel="reddit", hypothesis="t", tactic="t"
    )
    exp = get_experiment(exp_id)
    execute_after = datetime.fromisoformat(exp["execute_after"])
    delta = execute_after - before
    # Should be ~24 hours (BTL_VETO_WINDOW_HOURS) — allow ±1 minute
    expected = timedelta(hours=config.BTL_VETO_WINDOW_HOURS)
    assert abs((delta - expected).total_seconds()) < 60


def test_get_experiment_missing_returns_none():
    assert get_experiment("nonexistent_id") is None


def test_propose_persists_optional_fields():
    exp_id = propose_experiment(
        channel="reddit",
        hypothesis="t",
        tactic="t",
        cost_type="paid",
        cost_estimate=12.5,
        expected_metric="profile_visits",
        expected_target=50.0,
        expected_confidence=0.7,
        guardrails=["no_paid_ads", "no_spam"],
    )
    exp = get_experiment(exp_id)
    assert exp["cost_type"] == "paid"
    assert exp["cost_estimate"] == 12.5
    assert exp["expected_metric"] == "profile_visits"
    assert exp["expected_target"] == 50.0
    assert exp["expected_confidence"] == 0.7
    assert json.loads(exp["guardrails"]) == ["no_paid_ads", "no_spam"]


# ─── lifecycle: start / complete / analyze ───────────────────────────────────

def test_start_experiment():
    exp_id = propose_experiment(
        channel="reddit", hypothesis="test", tactic="test"
    )
    start_experiment(exp_id)
    exp = get_experiment(exp_id)
    assert exp["status"] == "active"
    assert exp["started_at"] is not None


def test_complete_experiment():
    exp_id = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    start_experiment(exp_id)
    complete_experiment(exp_id)
    exp = get_experiment(exp_id)
    assert exp["status"] == "completed"
    assert exp["ended_at"] is not None


def test_analyze_experiment():
    exp_id = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    start_experiment(exp_id)
    complete_experiment(exp_id)
    analyze_experiment(exp_id, result="success", learning="Reddit works great")
    exp = get_experiment(exp_id)
    assert exp["status"] == "analyzed"
    assert exp["result"] == "success"
    assert exp["learning"] == "Reddit works great"


# ─── metric logging ──────────────────────────────────────────────────────────

def test_log_metric_appends():
    exp_id = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    start_experiment(exp_id)
    log_metric(exp_id, {"profile_visits": 55, "date": "2026-04-20"})
    exp = get_experiment(exp_id)
    metrics = json.loads(exp["metrics_log"])
    assert len(metrics) == 1
    assert metrics[0]["profile_visits"] == 55
    assert metrics[0]["date"] == "2026-04-20"


def test_log_metric_multiple_appends_in_order():
    exp_id = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    start_experiment(exp_id)
    log_metric(exp_id, {"day": 1, "v": 10})
    log_metric(exp_id, {"day": 2, "v": 20})
    log_metric(exp_id, {"day": 3, "v": 30})
    exp = get_experiment(exp_id)
    metrics = json.loads(exp["metrics_log"])
    assert [m["day"] for m in metrics] == [1, 2, 3]
    assert [m["v"] for m in metrics] == [10, 20, 30]


def test_log_metric_unknown_id_is_noop():
    # Should not raise — silently no-op.
    log_metric("does_not_exist", {"x": 1})


# ─── concurrency: active_count / can_start_new ───────────────────────────────

def test_active_count():
    assert active_count() == 0
    e1 = propose_experiment(channel="a", hypothesis="t", tactic="t")
    start_experiment(e1)
    assert active_count() == 1
    e2 = propose_experiment(channel="b", hypothesis="t", tactic="t")
    start_experiment(e2)
    assert active_count() == 2
    # Completing one drops the count.
    complete_experiment(e1)
    assert active_count() == 1


def test_can_start_new_true_when_under_limit():
    assert can_start_new() is True
    e1 = propose_experiment(channel="a", hypothesis="t", tactic="t")
    start_experiment(e1)
    assert can_start_new() is True


def test_can_start_new_respects_limit():
    # MAX_CONCURRENT_EXPERIMENTS = 5
    for i in range(config.BTL_MAX_CONCURRENT_EXPERIMENTS):
        eid = propose_experiment(channel=f"ch_{i}", hypothesis="t", tactic="t")
        start_experiment(eid)
    assert active_count() == config.BTL_MAX_CONCURRENT_EXPERIMENTS
    assert can_start_new() is False


# ─── list_experiments filtering ──────────────────────────────────────────────

def test_list_experiments_by_status():
    e1 = propose_experiment(channel="a", hypothesis="t", tactic="t")
    e2 = propose_experiment(channel="b", hypothesis="t", tactic="t")
    start_experiment(e2)
    proposed = list_experiments(status="proposed")
    active = list_experiments(status="active")
    assert len(proposed) == 1
    assert proposed[0]["id"] == e1
    assert len(active) == 1
    assert active[0]["id"] == e2


def test_list_experiments_by_channel():
    e1 = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    e2 = propose_experiment(channel="tiktok", hypothesis="t", tactic="t")
    e3 = propose_experiment(channel="reddit", hypothesis="t2", tactic="t")
    reddit = list_experiments(channel="reddit")
    tiktok = list_experiments(channel="tiktok")
    assert {r["id"] for r in reddit} == {e1, e3}
    assert {r["id"] for r in tiktok} == {e2}


def test_list_experiments_combined_filters():
    e1 = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    e2 = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    start_experiment(e2)
    rows = list_experiments(status="proposed", channel="reddit")
    assert len(rows) == 1
    assert rows[0]["id"] == e1


def test_list_experiments_respects_limit():
    for i in range(10):
        propose_experiment(channel=f"ch_{i}", hypothesis="t", tactic="t")
    rows = list_experiments(limit=3)
    assert len(rows) == 3


# ─── due experiments / pending proposals ─────────────────────────────────────

def test_get_due_experiments_returns_overdue_active():
    # Insert an active experiment whose started_at is older than duration_days.
    exp_id = propose_experiment(
        channel="reddit", hypothesis="t", tactic="t", duration_days=7
    )
    start_experiment(exp_id)
    # Backdate started_at to 10 days ago.
    past = (datetime.utcnow() - timedelta(days=10)).isoformat()
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET started_at=? WHERE id=?", (past, exp_id)
        )
    due = get_due_experiments()
    assert any(e["id"] == exp_id for e in due)


def test_get_due_experiments_excludes_recent_active():
    exp_id = propose_experiment(
        channel="reddit", hypothesis="t", tactic="t", duration_days=21
    )
    start_experiment(exp_id)
    due = get_due_experiments()
    assert all(e["id"] != exp_id for e in due)


def test_get_pending_proposals_returns_after_veto_window():
    exp_id = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    # Backdate execute_after to 1 second ago so it's "due".
    past = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET execute_after=? WHERE id=?", (past, exp_id)
        )
    pending = get_pending_proposals()
    assert any(e["id"] == exp_id for e in pending)


def test_get_pending_proposals_excludes_within_veto_window():
    exp_id = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    pending = get_pending_proposals()
    # execute_after is 24h in the future → should NOT be returned
    assert all(e["id"] != exp_id for e in pending)


# ─── veto ────────────────────────────────────────────────────────────────────

def test_veto_experiment_marks_status_vetoed():
    exp_id = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    veto_experiment(exp_id, reason="too risky")
    exp = get_experiment(exp_id)
    assert exp["status"] == "vetoed"
    assert "too risky" in (exp["learning"] or "")


def test_veto_only_affects_proposed():
    # An already-active experiment should not flip to vetoed.
    exp_id = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    start_experiment(exp_id)
    veto_experiment(exp_id, reason="should not apply")
    exp = get_experiment(exp_id)
    assert exp["status"] == "active"
