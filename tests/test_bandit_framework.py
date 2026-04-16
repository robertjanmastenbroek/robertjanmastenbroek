"""Tests for the Thompson Sampling bandit framework (BTL protocol)."""
import os
import sys
import shutil
import tempfile
from collections import Counter
from pathlib import Path

# Set up isolated DB BEFORE importing the project's modules.
_tmpdir = tempfile.mkdtemp()
_db_path = str(Path(_tmpdir) / "test_bandit.db")
os.environ["RJM_DB_PATH"] = _db_path

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))

import config  # noqa: E402
config.DB_PATH = Path(_db_path)

import db  # noqa: E402
import btl_db  # noqa: E402
from bandit_framework import Bandit  # noqa: E402


def setup_module(_module):
    db.init_db()
    btl_db.init_btl_tables()


def teardown_module(_module):
    shutil.rmtree(_tmpdir, ignore_errors=True)


def _wipe_state(channel: str):
    """Reset the bandit_state table for a single channel between tests."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM bandit_state WHERE channel = ?", (channel,))


# ─── 1. Cold start ────────────────────────────────────────────────────────────

def test_select_cold_start():
    """With no prior data, select() returns one valid value per arm dimension."""
    _wipe_state("test_cold")
    arms = {"hook": ["A", "B", "C"], "tone": ["x", "y"]}
    bandit = Bandit("test_cold", arms)

    selection = bandit.select()
    assert isinstance(selection, dict)
    assert set(selection.keys()) == {"hook", "tone"}
    assert selection["hook"] in arms["hook"]
    assert selection["tone"] in arms["tone"]


# ─── 2. Record + select bias ──────────────────────────────────────────────────

def test_record_and_select():
    """After recording many successes for one arm value, it dominates draws."""
    _wipe_state("test_bias")
    arms = {"hook": ["winner", "loser_1", "loser_2"]}
    bandit = Bandit("test_bias", arms)

    # Inject lots of evidence past the cold-start threshold.
    for _ in range(40):
        bandit.record({"hook": "winner"}, reward=1.0)
        bandit.record({"hook": "loser_1"}, reward=0.0)
        bandit.record({"hook": "loser_2"}, reward=0.0)

    counts = Counter()
    for _ in range(200):
        sel = bandit.select()
        counts[sel["hook"]] += 1

    # Winner should be the modal pick by a wide margin.
    assert counts["winner"] > counts["loser_1"]
    assert counts["winner"] > counts["loser_2"]
    # And dominate decisively (>= 60% of draws even with epsilon exploration).
    assert counts["winner"] >= 120, f"winner only picked {counts['winner']}/200"


# ─── 3. DB persistence ────────────────────────────────────────────────────────

def test_record_updates_db():
    """record() upserts the right alpha/beta/samples values into bandit_state."""
    _wipe_state("test_persist")
    bandit = Bandit("test_persist", {"hook": ["alpha_arm", "beta_arm"]})

    bandit.record({"hook": "alpha_arm"}, reward=1.0)
    bandit.record({"hook": "alpha_arm"}, reward=1.0)
    bandit.record({"hook": "alpha_arm"}, reward=0.0)
    bandit.record({"hook": "beta_arm"}, reward=0.0)

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT arm_name, arm_value, alpha, beta, samples "
            "FROM bandit_state WHERE channel = ?",
            ("test_persist",),
        ).fetchall()
    state = {(r["arm_name"], r["arm_value"]): r for r in rows}

    alpha_row = state[("hook", "alpha_arm")]
    # Two successes (+2 alpha) + one failure (+1 beta), starting from 1.0/1.0 priors.
    assert alpha_row["samples"] == 3
    assert alpha_row["alpha"] == 3.0   # 1 prior + 2 reward
    assert alpha_row["beta"] == 2.0    # 1 prior + 1 (1-reward)

    beta_row = state[("hook", "beta_arm")]
    assert beta_row["samples"] == 1
    assert beta_row["alpha"] == 1.0    # prior unchanged
    assert beta_row["beta"] == 2.0     # prior + 1 failure


# ─── 4. Multiple arm dimensions ───────────────────────────────────────────────

def test_multiple_arms():
    """Bandit handles N arm dimensions independently."""
    _wipe_state("test_multi")
    arms = {
        "hook": ["A", "B"],
        "subject": ["short", "long"],
        "send_hour": ["09", "21"],
    }
    bandit = Bandit("test_multi", arms)

    sel = bandit.select()
    assert set(sel.keys()) == {"hook", "subject", "send_hour"}

    bandit.record(
        {"hook": "A", "subject": "short", "send_hour": "21"}, reward=1.0
    )

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT arm_name, arm_value FROM bandit_state WHERE channel = ?",
            ("test_multi",),
        ).fetchall()
    pairs = {(r["arm_name"], r["arm_value"]) for r in rows}
    assert ("hook", "A") in pairs
    assert ("subject", "short") in pairs
    assert ("send_hour", "21") in pairs
    # Untouched values shouldn't have rows yet.
    assert ("hook", "B") not in pairs


# ─── 5. get_stats ─────────────────────────────────────────────────────────────

def test_get_stats():
    """get_stats() returns mean reward + samples per arm value."""
    _wipe_state("test_stats")
    bandit = Bandit("test_stats", {"hook": ["good", "bad"]})

    for _ in range(8):
        bandit.record({"hook": "good"}, reward=1.0)
    for _ in range(2):
        bandit.record({"hook": "good"}, reward=0.0)
    for _ in range(10):
        bandit.record({"hook": "bad"}, reward=0.0)

    stats = bandit.get_stats()
    assert "hook" in stats
    by_value = {row["value"]: row for row in stats["hook"]}

    good = by_value["good"]
    assert good["samples"] == 10
    # 8 wins / 10 = 0.8
    assert abs(good["mean_reward"] - 0.8) < 1e-9
    assert good["alpha"] == 9.0   # 1 prior + 8
    assert good["beta"] == 3.0    # 1 prior + 2

    bad = by_value["bad"]
    assert bad["samples"] == 10
    assert abs(bad["mean_reward"] - 0.0) < 1e-9


# ─── 6. Breakthrough detection ────────────────────────────────────────────────

def test_detect_breakthroughs():
    """Arm values with mean_reward > MULTIPLIER * overall_mean are flagged."""
    _wipe_state("test_break")
    bandit = Bandit("test_break", {"hook": ["star", "mid", "dud"]})

    # Star: 9/10 = 0.90
    for _ in range(9):
        bandit.record({"hook": "star"}, reward=1.0)
    bandit.record({"hook": "star"}, reward=0.0)

    # Mid: 2/10 = 0.20
    for _ in range(2):
        bandit.record({"hook": "mid"}, reward=1.0)
    for _ in range(8):
        bandit.record({"hook": "mid"}, reward=0.0)

    # Dud: 1/10 = 0.10
    bandit.record({"hook": "dud"}, reward=1.0)
    for _ in range(9):
        bandit.record({"hook": "dud"}, reward=0.0)

    # Overall mean = (9 + 2 + 1) / 30 = 0.40 → 2x = 0.80
    breakthroughs = bandit.detect_breakthroughs()
    assert isinstance(breakthroughs, list)
    flagged = {b["value"] for b in breakthroughs}
    assert "star" in flagged
    assert "mid" not in flagged
    assert "dud" not in flagged


def test_detect_breakthroughs_ignores_undersampled():
    """Arm values with < COLD_START_MIN samples are excluded from breakthroughs."""
    _wipe_state("test_break_cold")
    bandit = Bandit("test_break_cold", {"hook": ["unproven", "tested"]})

    # Unproven: 2 samples, both wins (looks great but too few)
    bandit.record({"hook": "unproven"}, reward=1.0)
    bandit.record({"hook": "unproven"}, reward=1.0)

    # Tested: 10 samples, mostly losses — defines a low overall mean
    bandit.record({"hook": "tested"}, reward=1.0)
    for _ in range(9):
        bandit.record({"hook": "tested"}, reward=0.0)

    breakthroughs = bandit.detect_breakthroughs()
    flagged = {b["value"] for b in breakthroughs}
    assert "unproven" not in flagged   # too few samples to trust
