"""End-to-end BTL (Boil-the-Lake) integration tests.

Exercises the FULL lifecycle across the BTL stack:

  experiment propose → veto window → start → log metrics → complete → analyze
  proposal create   → veto
  donation          → allocation → spend → balance check
  channel metrics   → reallocate → winner-vs-loser weight check
  self-assessment   → score + triggered fleet action
  brain status      → composite snapshot
  strategic insight → save + retrieve

These tests deliberately straddle module boundaries — every other BTL test
file is unit-scoped, this one is the integration safety net.

Test isolation
--------------
We use the same temp-file pattern as ``test_experiment_engine`` and
``test_growth_brain``: a real on-disk SQLite file (NOT ``:memory:``) because
``db.get_conn()`` opens a fresh connection per call and an in-memory DB does
not share state across connections.

We also isolate:

  * ``BTL_CHANNEL_REGISTRY_PATH`` → temp JSON, seeded with two active
    channels for the reallocation flow.
  * ``self_assessment.SCORE_PATH`` → temp JSON so we don't pollute
    ``data/growth_score.json``.
  * ``strategy_portfolio`` is re-imported lazily so any module-level
    registry path caching is re-evaluated against our env override.

In ``setup_function`` we re-pin ``db.DB_PATH`` / ``config.DB_PATH`` and
``self_assessment.SCORE_PATH`` because pytest collection imports sibling
test modules first, which mutate these globals when they pin their OWN temp
files.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# ── Isolate DB + registry + score-file per-module ────────────────────────────
_tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tf.close()
os.environ["RJM_DB_PATH"] = _tf.name

# Two active channels seeded — the reallocation test will record very
# different LEI values on them and assert the winner outweighs the loser.
_REGISTRY_PATH = Path(_tf.name + ".registry.json")
_SEED_REGISTRY = {
    "channels": [
        {
            "id": "ch_test",
            "name": "Test Channel",
            "tactic": "test tactic",
            "agent": "test_agent",
            "status": "active",
            "weight": 0.50,
            "cost_type": "free",
        },
        {
            "id": "ch_test2",
            "name": "Test Channel 2",
            "tactic": "test tactic 2",
            "agent": "test_agent2",
            "status": "active",
            "weight": 0.50,
            "cost_type": "free",
        },
    ],
    "last_reallocation": None,
}
_REGISTRY_PATH.write_text(json.dumps(_SEED_REGISTRY))
os.environ["BTL_CHANNEL_REGISTRY_PATH"] = str(_REGISTRY_PATH)

# Score JSON file lives next to the temp DB.
_SCORE_PATH = Path(_tf.name + ".growth_score.json")

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))

import config  # noqa: E402
config.DB_PATH = Path(os.environ["RJM_DB_PATH"])

import db  # noqa: E402
db.DB_PATH = Path(os.environ["RJM_DB_PATH"])

import btl_db  # noqa: E402

db.init_db()
btl_db.init_btl_tables()

import experiment_engine  # noqa: E402
import veto_system  # noqa: E402
import revenue_tracker  # noqa: E402
import strategy_portfolio  # noqa: E402
import self_assessment  # noqa: E402
import growth_brain  # noqa: E402


# Hold per-test mutable globals so we can restore them in teardown_function.
# Same pattern as test_growth_brain — keeps sibling test modules'
# SCORE_PATH / env-var bindings intact when control returns to them.
_PRIOR_SCORE_PATH = None
_PRIOR_REGISTRY_ENV = None
_PRIOR_DB_ENV = None


def _reset_registry():
    """Reset the on-disk channel registry to the seed before each test.

    Also re-pins ``BTL_CHANNEL_REGISTRY_PATH`` because sibling test modules
    (notably ``test_strategy_portfolio``) point that env var at THEIR own
    temp registry at module import time. ``strategy_portfolio._registry_path``
    re-reads the env var on every call, so re-pinning here is sufficient.
    """
    os.environ["BTL_CHANNEL_REGISTRY_PATH"] = str(_REGISTRY_PATH)
    _REGISTRY_PATH.write_text(json.dumps(_SEED_REGISTRY))


def setup_function():
    """Wipe state, re-pin DB + registry + score paths, restore seed registry.

    Captures the prior env-var values so ``teardown_function`` can put them
    back — this prevents poisoning sibling test modules whose
    ``setup_function`` does NOT re-pin these env vars (e.g.
    ``test_strategy_portfolio`` only re-writes its registry file but
    inherits whatever ``BTL_CHANNEL_REGISTRY_PATH`` is currently set to).
    """
    global _PRIOR_SCORE_PATH, _PRIOR_REGISTRY_ENV, _PRIOR_DB_ENV
    _PRIOR_DB_ENV = os.environ.get("RJM_DB_PATH")
    _PRIOR_REGISTRY_ENV = os.environ.get("BTL_CHANNEL_REGISTRY_PATH")
    os.environ["RJM_DB_PATH"] = _tf.name
    db.DB_PATH = Path(os.environ["RJM_DB_PATH"])
    config.DB_PATH = Path(os.environ["RJM_DB_PATH"])
    _PRIOR_SCORE_PATH = self_assessment.SCORE_PATH
    self_assessment.SCORE_PATH = _SCORE_PATH
    db.init_db()
    btl_db.init_btl_tables()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM experiments")
        conn.execute("DELETE FROM proposals")
        conn.execute("DELETE FROM growth_budget")
        conn.execute("DELETE FROM channel_metrics")
        conn.execute("DELETE FROM strategic_insights")
        conn.execute("DELETE FROM bandit_state")
    if _SCORE_PATH.exists():
        _SCORE_PATH.unlink()
    _reset_registry()


def teardown_function():
    """Restore SCORE_PATH + env vars after every test.

    Mirror image of ``setup_function`` — by putting back whatever was there
    before, sibling test modules don't see leaked state when control
    returns to them in the same pytest session.
    """
    global _PRIOR_SCORE_PATH, _PRIOR_REGISTRY_ENV, _PRIOR_DB_ENV
    if _PRIOR_SCORE_PATH is not None:
        self_assessment.SCORE_PATH = _PRIOR_SCORE_PATH
    if _PRIOR_REGISTRY_ENV is not None:
        os.environ["BTL_CHANNEL_REGISTRY_PATH"] = _PRIOR_REGISTRY_ENV
    elif "BTL_CHANNEL_REGISTRY_PATH" in os.environ:
        del os.environ["BTL_CHANNEL_REGISTRY_PATH"]
    if _PRIOR_DB_ENV is not None:
        os.environ["RJM_DB_PATH"] = _PRIOR_DB_ENV
    if _SCORE_PATH.exists():
        try:
            _SCORE_PATH.unlink()
        except OSError:
            pass


def teardown_module():
    """Remove temp DB + sidecar files when the module finishes."""
    for path in (_tf.name, str(_REGISTRY_PATH), str(_SCORE_PATH)):
        try:
            os.unlink(path)
        except OSError:
            pass


# ─── 1. Full experiment lifecycle ────────────────────────────────────────────


def test_full_experiment_lifecycle():
    """propose → start → log 2 metrics → complete → analyze(success).

    Verifies every status transition and that both metrics are persisted in
    the JSON metrics_log in insertion order.
    """
    exp_id = experiment_engine.propose_experiment(
        channel="reddit",
        hypothesis="Posts in r/melodictechno drive 50+ profile visits/post",
        tactic="Post 2x/week with production stories",
        duration_days=7,
        success_criteria="50+ profile visits per post",
        failure_criteria="<10 profile visits after 6 posts",
    )
    assert exp_id.startswith("exp_")

    exp = experiment_engine.get_experiment(exp_id)
    assert exp["status"] == "proposed"

    experiment_engine.start_experiment(exp_id)
    assert experiment_engine.get_experiment(exp_id)["status"] == "active"

    experiment_engine.log_metric(exp_id, {"day": 1, "profile_visits": 55})
    experiment_engine.log_metric(exp_id, {"day": 2, "profile_visits": 62})

    exp = experiment_engine.get_experiment(exp_id)
    metrics = json.loads(exp["metrics_log"])
    assert len(metrics) == 2
    assert metrics[0]["profile_visits"] == 55
    assert metrics[1]["profile_visits"] == 62

    experiment_engine.complete_experiment(exp_id)
    assert experiment_engine.get_experiment(exp_id)["status"] == "completed"

    experiment_engine.analyze_experiment(
        exp_id,
        result="success",
        learning="Reddit r/melodictechno is a strong fit",
    )
    final = experiment_engine.get_experiment(exp_id)
    assert final["status"] == "analyzed"
    assert final["result"] == "success"
    assert final["learning"] == "Reddit r/melodictechno is a strong fit"


# ─── 2. Veto proposal flow ───────────────────────────────────────────────────


def test_veto_proposal_flow():
    """create_proposal → veto_proposal → confirm in vetoed list."""
    pid = veto_system.create_proposal(
        proposal_type="new_experiment",
        title="Risky paid ad blast",
        description="Spend 200 EUR on Meta ads in 24h",
        hypothesis="Paid ads convert at 3%",
        risk_level="high",
        estimated_impact="200-500 listeners",
    )
    assert pid.startswith("prop_")

    veto_system.veto_proposal(pid, reason="Too risky for this stage")

    vetoed = veto_system.list_proposals(status="vetoed")
    assert len(vetoed) == 1
    assert vetoed[0]["id"] == pid
    assert "Too risky" in vetoed[0]["veto_reason"]

    # And it should NOT appear in the pending list anymore.
    pending = veto_system.get_pending_proposals()
    assert all(p["id"] != pid for p in pending)


# ─── 3. Budget flow (donation → spend → balance) ────────────────────────────


def test_budget_flow():
    """record_donation(100) → balance=50 → record_spend(3) → balance=47.

    With BTL_DONATION_ALLOCATION_PCT=0.50 a 100-EUR donation allocates 50.
    A subsequent 3-EUR spend should drop available balance to 47.
    """
    revenue_tracker.record_donation(100.0, source="ch_int_test", note="integration")
    summary = revenue_tracker.get_budget_summary()
    assert summary["total_donations"] == 100.0
    assert summary["total_allocated"] == 50.0
    assert summary["available_balance"] == 50.0

    revenue_tracker.record_spend(
        3.0, channel="reddit", experiment_id="exp_int", note="integration"
    )
    summary = revenue_tracker.get_budget_summary()
    assert summary["total_spent"] == 3.0
    assert summary["available_balance"] == 47.0


# ─── 4. Reallocation flow (LEI signal → weight shift) ───────────────────────


def test_reallocation_flow():
    """Channel with 20 listeners gained should outweigh the one with 2.

    We record listeners_gained metrics for both seeded active channels then
    invoke ``reallocate_weights``. Spirit: winner > loser in the resulting
    registry.
    """
    # ch_test gets 20 listeners gained, ch_test2 gets 2.
    strategy_portfolio.record_channel_metric("ch_test", "listeners_gained", 20.0)
    strategy_portfolio.record_channel_metric("ch_test2", "listeners_gained", 2.0)

    reg = strategy_portfolio.reallocate_weights()

    by_id = {ch["id"]: ch for ch in reg["channels"]}
    ch_test = by_id["ch_test"]
    ch_test2 = by_id["ch_test2"]

    assert ch_test["weight"] > ch_test2["weight"], (
        f"winner ch_test ({ch_test['weight']}) should outweigh "
        f"loser ch_test2 ({ch_test2['weight']})"
    )
    # And both should still be within the global floor/ceiling.
    floor = config.BTL_CHANNEL_WEIGHT_FLOOR
    breakthrough_ceiling = config.BTL_CHANNEL_BREAKTHROUGH_CEILING
    assert floor <= ch_test["weight"] <= breakthrough_ceiling
    assert floor <= ch_test2["weight"] <= breakthrough_ceiling


# ─── 5. Self-assessment flow ─────────────────────────────────────────────────


def test_self_assessment_flow():
    """Run a successful experiment → run_self_assess → score > 0 with a
    valid triggered_action level.

    We seed ONE analyzed-success experiment so the experiment_hit_rate
    component contributes a non-zero share, and a small listener delta
    (340 vs 325) so listener_velocity also fires.
    """
    exp_id = experiment_engine.propose_experiment(
        channel="reddit", hypothesis="t", tactic="t"
    )
    experiment_engine.start_experiment(exp_id)
    experiment_engine.complete_experiment(exp_id)
    experiment_engine.analyze_experiment(
        exp_id, result="success", learning="Worked great"
    )

    score = growth_brain.run_self_assess(
        listeners_current=340, listeners_previous=325
    )

    assert score["total_score"] > 0
    assert "components" in score
    assert "triggered_action" in score

    valid_levels = {
        "stay_course",
        "increase_discovery",
        "emergency",
        "red_alert",
        "system_pause",
    }
    assert score["triggered_action"]["level"] in valid_levels


# ─── 6. Brain status snapshot ────────────────────────────────────────────────


def test_brain_status():
    """get_brain_status returns a dict with the canonical BTL keys."""
    status = growth_brain.get_brain_status()
    assert isinstance(status, dict)
    assert "active_experiments" in status
    assert "pending_proposals" in status
    assert "active_channels" in status
    assert "budget" in status
    # active_channels should reflect the two seeded entries (status='active').
    assert status["active_channels"] == 2
    # Budget summary structure should be intact.
    assert "available_balance" in status["budget"]


# ─── 7. Strategic insight save + retrieve ────────────────────────────────────


def test_strategic_insight_save_and_retrieve():
    """save_strategic_insight then get_strategic_insights returns it."""
    growth_brain.save_strategic_insight(
        source="integration_test",
        insight="Reddit r/melodictechno outperforms r/edm by 4x on profile visits",
        confidence=0.85,
        applicable_channels=["reddit"],
    )

    insights = growth_brain.get_strategic_insights(limit=10)
    assert len(insights) == 1
    row = insights[0]
    assert row["source"] == "integration_test"
    assert "Reddit r/melodictechno" in row["insight"]
    assert abs(row["confidence"] - 0.85) < 1e-9
    assert json.loads(row["applicable_channels"]) == ["reddit"]
