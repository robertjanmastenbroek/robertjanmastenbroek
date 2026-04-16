"""Tests for the BTL self-assessment / growth-score module."""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Isolate per module — the module writes growth scores to disk.
tmpdir = tempfile.mkdtemp(prefix="rjm_self_assessment_")
os.environ["RJM_DB_PATH"] = str(Path(tmpdir) / "test_self_assessment.db")

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
import config  # noqa: E402
config.DB_PATH = Path(os.environ["RJM_DB_PATH"])

import self_assessment  # noqa: E402


# Re-route the score JSON file to tmpdir so we don't overwrite real data.
_SCORE_PATH = Path(tmpdir) / "growth_score.json"
self_assessment.SCORE_PATH = _SCORE_PATH


def setup_function(_):
    if _SCORE_PATH.exists():
        _SCORE_PATH.unlink()


def teardown_module():
    shutil.rmtree(tmpdir, ignore_errors=True)


# ── calculate_score shape ──────────────────────────────────────────────────────


def test_calculate_score_returns_expected_keys():
    result = self_assessment.calculate_score()
    assert "total_score" in result
    assert "calculated_at" in result
    assert "components" in result
    assert isinstance(result["components"], dict)


def test_calculate_score_has_seven_components_with_weights():
    result = self_assessment.calculate_score()
    expected = {
        "listener_velocity": 0.30,
        "experiment_hit_rate": 0.20,
        "pipeline_health": 0.15,
        "channel_diversity": 0.10,
        "content_performance": 0.10,
        "budget_efficiency": 0.10,
        "system_reliability": 0.05,
    }
    assert set(result["components"].keys()) == set(expected.keys())
    for name, weight in expected.items():
        assert abs(result["components"][name]["weight"] - weight) < 1e-9, (
            f"{name} weight mismatch"
        )
    # Weights must sum to 1.0
    assert abs(sum(c["weight"] for c in result["components"].values()) - 1.0) < 1e-9


def test_calculate_score_components_have_score_and_detail():
    result = self_assessment.calculate_score()
    for name, comp in result["components"].items():
        assert "score" in comp, f"{name} missing score"
        assert "detail" in comp, f"{name} missing detail"
        assert 0 <= comp["score"] <= 100, f"{name} score out of range"


# ── calculate_score values ─────────────────────────────────────────────────────


def test_calculate_score_zero_when_everything_is_zero():
    result = self_assessment.calculate_score(
        listeners_current=0,
        listeners_previous=0,
        experiments_succeeded=0,
        experiments_completed=0,
        contacts_added=0,
        contacts_target=50,
        active_channels_positive=0,
        active_channels_total=1,
        avg_completion_rate=0.0,
        listeners_per_eur=0.0,
        has_budget=False,
        agent_runs_total=1,
        agent_runs_failed=0,
    )
    # Only system_reliability should contribute (no failures, 1 run).
    assert result["total_score"] >= 0
    assert result["total_score"] < 50


def test_calculate_score_high_when_everything_strong():
    result = self_assessment.calculate_score(
        listeners_current=120,
        listeners_previous=100,   # +20% growth
        experiments_succeeded=4,
        experiments_completed=5,  # 80% hit rate
        contacts_added=60,
        contacts_target=50,        # over target
        active_channels_positive=5,
        active_channels_total=5,   # full diversity
        avg_completion_rate=0.95,
        listeners_per_eur=10.0,
        has_budget=True,
        agent_runs_total=100,
        agent_runs_failed=2,       # 98% reliability
    )
    assert result["total_score"] >= 70


def test_calculate_score_total_is_weighted_sum():
    result = self_assessment.calculate_score(
        listeners_current=110,
        listeners_previous=100,
        experiments_succeeded=2,
        experiments_completed=4,
        contacts_added=25,
        contacts_target=50,
        active_channels_positive=3,
        active_channels_total=5,
        avg_completion_rate=0.8,
        listeners_per_eur=2.0,
        has_budget=True,
        agent_runs_total=10,
        agent_runs_failed=1,
    )
    expected_total = sum(
        c["weight"] * c["score"] for c in result["components"].values()
    )
    assert abs(result["total_score"] - expected_total) < 1e-6


# ── get_triggered_action ───────────────────────────────────────────────────────


def test_triggered_action_stay_course():
    a = self_assessment.get_triggered_action(85)
    assert a["level"] == "stay_course"
    assert "description" in a


def test_triggered_action_increase_discovery():
    a = self_assessment.get_triggered_action(65)
    assert a["level"] == "increase_discovery"


def test_triggered_action_emergency():
    a = self_assessment.get_triggered_action(45)
    assert a["level"] == "emergency"


def test_triggered_action_red_alert():
    a = self_assessment.get_triggered_action(25)
    assert a["level"] == "red_alert"


def test_triggered_action_system_pause():
    a = self_assessment.get_triggered_action(10)
    assert a["level"] == "system_pause"


def test_triggered_action_boundaries():
    # Boundaries — score == threshold should trigger that level.
    assert self_assessment.get_triggered_action(80)["level"] == "stay_course"
    assert self_assessment.get_triggered_action(60)["level"] == "increase_discovery"
    assert self_assessment.get_triggered_action(40)["level"] == "emergency"
    assert self_assessment.get_triggered_action(20)["level"] == "red_alert"


# ── persistence ────────────────────────────────────────────────────────────────


def test_save_score_appends_to_history():
    s1 = self_assessment.calculate_score(listeners_current=10, listeners_previous=5)
    s2 = self_assessment.calculate_score(listeners_current=20, listeners_previous=10)
    self_assessment.save_score(s1)
    self_assessment.save_score(s2)

    history = self_assessment.get_score_history()
    assert len(history) == 2


def test_save_score_handles_missing_file():
    assert not _SCORE_PATH.exists()
    s = self_assessment.calculate_score()
    self_assessment.save_score(s)
    assert _SCORE_PATH.exists()


def test_get_score_history_respects_limit():
    for i in range(15):
        s = self_assessment.calculate_score(listeners_current=i, listeners_previous=0)
        self_assessment.save_score(s)
    history = self_assessment.get_score_history(limit=5)
    assert len(history) == 5


def test_get_score_history_empty_when_no_file():
    assert self_assessment.get_score_history() == []
