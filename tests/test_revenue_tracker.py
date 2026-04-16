"""Tests for BTL revenue tracker — donations, allocations, spend, auto-spend gating.

Uses a temp-file SQLite DB so tests don't touch the real outreach.db.
Tests MUST NOT require the `stripe` package to be installed — poll_stripe()
gracefully degrades when stripe is absent.
"""
import os
import sys
import shutil
import tempfile
from datetime import date
from pathlib import Path

# ─── isolated temp DB before importing the modules under test ───────────────
_tmpdir = tempfile.mkdtemp()
_db_path = str(Path(_tmpdir) / "test_revenue.db")
os.environ["RJM_DB_PATH"] = _db_path

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))

import config  # noqa: E402
config.DB_PATH = Path(_db_path)

import db        # noqa: E402
import btl_db    # noqa: E402
import revenue_tracker  # noqa: E402


def setup_module():
    """Initialise schema once for the test module."""
    db.init_db()
    btl_db.init_btl_tables()


def teardown_module():
    shutil.rmtree(_tmpdir, ignore_errors=True)


def _wipe_budget():
    """Clear growth_budget between tests for isolation."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM growth_budget")


def setup_function(_fn):
    _wipe_budget()


# ─── record_donation ────────────────────────────────────────────────────────
def test_record_donation_inserts_donation_and_allocation():
    revenue_tracker.record_donation(100.0, source="ch_test123", note="test donation")
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT type, amount, source, note FROM growth_budget ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    types = [r["type"] for r in rows]
    assert "donation" in types
    assert "allocation" in types
    donation = next(r for r in rows if r["type"] == "donation")
    allocation = next(r for r in rows if r["type"] == "allocation")
    assert donation["amount"] == 100.0
    assert donation["source"] == "ch_test123"
    assert allocation["amount"] == 50.0  # 50% of 100


def test_record_donation_rounds_allocation_to_cents():
    revenue_tracker.record_donation(33.33)
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT amount FROM growth_budget WHERE type='allocation'"
        ).fetchone()
    # 33.33 * 0.50 = 16.665 → 16.67 (banker's rounding) or 16.66 — must be 2 dp
    assert round(row["amount"], 2) == row["amount"]
    assert abs(row["amount"] - 16.67) < 0.01 or abs(row["amount"] - 16.66) < 0.01


# ─── record_spend ───────────────────────────────────────────────────────────
def test_record_spend_stores_negative_amount():
    revenue_tracker.record_spend(3.50, channel="reddit", experiment_id="exp_001",
                                  note="boost")
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT type, amount, channel, experiment_id FROM growth_budget"
        ).fetchone()
    assert row["type"] == "spend"
    assert row["amount"] == -3.50
    assert row["channel"] == "reddit"
    assert row["experiment_id"] == "exp_001"


def test_record_spend_negates_already_negative():
    """If caller passes -5, we still store -5 (not +5)."""
    revenue_tracker.record_spend(-5.0, channel="ig")
    with db.get_conn() as conn:
        row = conn.execute("SELECT amount FROM growth_budget").fetchone()
    assert row["amount"] == -5.0


# ─── get_budget_summary ─────────────────────────────────────────────────────
def test_budget_summary_100_eur_donation():
    revenue_tracker.record_donation(100.0)
    summary = revenue_tracker.get_budget_summary()
    assert summary["total_donations"] == 100.0
    assert summary["total_allocated"] == 50.0
    assert summary["total_spent"] == 0.0
    assert summary["available_balance"] == 50.0


def test_budget_summary_after_spend():
    revenue_tracker.record_donation(100.0)  # +50 allocated
    revenue_tracker.record_spend(10.0)
    summary = revenue_tracker.get_budget_summary()
    assert summary["total_allocated"] == 50.0
    assert summary["total_spent"] == 10.0
    assert summary["available_balance"] == 40.0


def test_budget_summary_empty():
    summary = revenue_tracker.get_budget_summary()
    assert summary["total_donations"] == 0.0
    assert summary["total_allocated"] == 0.0
    assert summary["total_spent"] == 0.0
    assert summary["available_balance"] == 0.0


# ─── get_daily_spend ────────────────────────────────────────────────────────
def test_daily_spend_sums_today_only():
    revenue_tracker.record_spend(3.0)
    revenue_tracker.record_spend(2.5)
    # back-date a row to yesterday — must be excluded
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO growth_budget(date, type, amount) VALUES (?, ?, ?)",
            ("2020-01-01", "spend", -99.0),
        )
    assert revenue_tracker.get_daily_spend() == 5.5


def test_daily_spend_zero_when_empty():
    assert revenue_tracker.get_daily_spend() == 0.0


# ─── can_auto_spend ─────────────────────────────────────────────────────────
def test_can_auto_spend_under_limit_with_balance():
    revenue_tracker.record_donation(100.0)  # 50 EUR allocated
    assert revenue_tracker.can_auto_spend(3.0) is True


def test_can_auto_spend_blocks_over_per_action_max():
    revenue_tracker.record_donation(1000.0)  # plenty of budget
    # BTL_AUTO_SPEND_MAX_EUR = 5.0 → 6 EUR must be blocked
    assert revenue_tracker.can_auto_spend(6.0) is False


def test_can_auto_spend_blocks_when_below_reserve():
    # Drive balance down to exactly 6 EUR with a large donation so the
    # daily-cap gate is non-binding, then test the reserve floor in isolation.
    # 100 EUR donation → 50 EUR allocated.
    revenue_tracker.record_donation(100.0)
    # Back-date a 44 EUR spend to yesterday so it doesn't affect today's
    # daily-spend tally but DOES draw down the balance to 6 EUR.
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO growth_budget(date, type, amount, channel) "
            "VALUES (?, ?, ?, ?)",
            ("2020-01-01", "spend", -44.0, "historic"),
        )
    summary = revenue_tracker.get_budget_summary()
    assert summary["available_balance"] == 6.0
    # Spending 2 EUR → balance would drop to 4 EUR, below 5 EUR reserve → block
    assert revenue_tracker.can_auto_spend(2.0) is False
    # Spending 1 EUR → balance 5 EUR, equal to reserve → allowed
    assert revenue_tracker.can_auto_spend(1.0) is True


def test_can_auto_spend_blocks_when_balance_too_low():
    # No donations → balance 0, any spend blocked.
    assert revenue_tracker.can_auto_spend(1.0) is False


def test_can_auto_spend_respects_daily_cap_pct():
    # 30 EUR donation → 15 EUR allocated. 30% of 15 = 4.50 EUR daily cap.
    # First 4.50 spend OK (balance still 10.50, above 5 reserve).
    # Second 1 EUR same day → daily total 5.50 > 4.50 → block.
    revenue_tracker.record_donation(30.0)
    assert revenue_tracker.can_auto_spend(4.5) is True
    revenue_tracker.record_spend(4.5)
    assert revenue_tracker.can_auto_spend(1.0) is False


# ─── poll_stripe ────────────────────────────────────────────────────────────
def test_poll_stripe_no_api_key_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_API_KEY", "")
    result = revenue_tracker.poll_stripe()
    assert result == []


def test_poll_stripe_no_stripe_module_returns_empty(monkeypatch):
    """If stripe is not installed, poll_stripe must degrade gracefully."""
    monkeypatch.setattr(config, "STRIPE_API_KEY", "sk_test_dummy")
    # Force ImportError by hiding the stripe module from sys.modules and
    # making any import fail.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "stripe":
            raise ImportError("simulated missing stripe")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = revenue_tracker.poll_stripe()
    assert result == []
