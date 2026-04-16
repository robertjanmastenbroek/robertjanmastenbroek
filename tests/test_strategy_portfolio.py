"""Tests for the BTL strategy portfolio + channel registry."""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# ── Isolate DB + registry per-module ────────────────────────────────────────────
tmpdir = tempfile.mkdtemp(prefix="rjm_strategy_portfolio_")
os.environ["RJM_DB_PATH"] = str(Path(tmpdir) / "test_portfolio.db")

# Build a temp registry so save/load don't touch the real one
_REGISTRY_PATH = Path(tmpdir) / "channel_registry.json"
_SEED_REGISTRY = {
    "channels": [
        {
            "id": "ch_alpha",
            "name": "Alpha",
            "tactic": "alpha tactic",
            "agent": "agent_a",
            "status": "active",
            "weight": 0.40,
            "cost_type": "free",
            "est_listeners_low": 1000,
            "est_listeners_high": 5000,
        },
        {
            "id": "ch_beta",
            "name": "Beta",
            "tactic": "beta tactic",
            "agent": "agent_b",
            "status": "active",
            "weight": 0.30,
            "cost_type": "free",
            "est_listeners_low": 500,
            "est_listeners_high": 2000,
        },
        {
            "id": "ch_gamma",
            "name": "Gamma",
            "tactic": "gamma tactic",
            "agent": "agent_c",
            "status": "active",
            "weight": 0.20,
            "cost_type": "free",
            "est_listeners_low": 100,
            "est_listeners_high": 500,
        },
        {
            "id": "ch_delta",
            "name": "Delta",
            "tactic": "delta tactic",
            "agent": "agent_d",
            "status": "queued",
            "weight": 0.10,
            "cost_type": "free",
            "est_listeners_low": 0,
            "est_listeners_high": 0,
        },
        {
            "id": "ch_locked",
            "name": "Locked",
            "tactic": "paid",
            "agent": "paid_agent",
            "status": "locked",
            "weight": 0.0,
            "cost_type": "self_funded",
            "cost_per_unit": 2.0,
            "est_listeners_low": 5,
            "est_listeners_high": 20,
        },
    ],
    "last_reallocation": None,
}
_REGISTRY_PATH.write_text(json.dumps(_SEED_REGISTRY))
os.environ["BTL_CHANNEL_REGISTRY_PATH"] = str(_REGISTRY_PATH)

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
import config  # noqa: E402
config.DB_PATH = Path(os.environ["RJM_DB_PATH"])

import db  # noqa: E402
import btl_db  # noqa: E402
import strategy_portfolio  # noqa: E402


def setup_module():
    db.init_db()
    btl_db.init_btl_tables()


def teardown_module():
    shutil.rmtree(tmpdir, ignore_errors=True)


def _reset_registry():
    """Reset the on-disk registry to the seed before each test."""
    _REGISTRY_PATH.write_text(json.dumps(_SEED_REGISTRY))


def _wipe_metrics():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM channel_metrics")


# ── load / save ────────────────────────────────────────────────────────────────


def test_load_registry_returns_dict():
    _reset_registry()
    reg = strategy_portfolio.load_registry()
    assert isinstance(reg, dict)
    assert "channels" in reg
    assert isinstance(reg["channels"], list)
    assert len(reg["channels"]) == 5


def test_load_registry_uses_env_override():
    _reset_registry()
    reg = strategy_portfolio.load_registry()
    ids = {c["id"] for c in reg["channels"]}
    assert "ch_alpha" in ids


def test_save_registry_roundtrip():
    _reset_registry()
    reg = strategy_portfolio.load_registry()
    reg["last_reallocation"] = "2026-04-16T00:00:00"
    strategy_portfolio.save_registry(reg)

    reloaded = strategy_portfolio.load_registry()
    assert reloaded["last_reallocation"] == "2026-04-16T00:00:00"


# ── get_channel ────────────────────────────────────────────────────────────────


def test_get_channel_known():
    _reset_registry()
    ch = strategy_portfolio.get_channel("ch_alpha")
    assert ch is not None
    assert ch["name"] == "Alpha"


def test_get_channel_unknown():
    _reset_registry()
    assert strategy_portfolio.get_channel("ch_nope") is None


# ── activation lifecycle ───────────────────────────────────────────────────────


def test_get_active_channels():
    _reset_registry()
    actives = strategy_portfolio.get_active_channels()
    ids = {c["id"] for c in actives}
    assert ids == {"ch_alpha", "ch_beta", "ch_gamma"}


def test_activate_channel():
    _reset_registry()
    strategy_portfolio.activate_channel("ch_delta")
    assert strategy_portfolio.get_channel("ch_delta")["status"] == "active"


def test_pause_channel():
    _reset_registry()
    strategy_portfolio.pause_channel("ch_alpha")
    assert strategy_portfolio.get_channel("ch_alpha")["status"] == "paused"


# ── metrics + LEI ──────────────────────────────────────────────────────────────


def test_record_channel_metric_inserts_row():
    _reset_registry()
    _wipe_metrics()
    strategy_portfolio.record_channel_metric("ch_alpha", "listeners_gained", 42.0)
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM channel_metrics WHERE channel_id=?", ("ch_alpha",)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["metric_name"] == "listeners_gained"
    assert rows[0]["metric_value"] == 42.0


def test_get_channel_lei_sums_recent_metrics():
    _reset_registry()
    _wipe_metrics()
    # Record three metrics inside the 7-day window.
    strategy_portfolio.record_channel_metric("ch_alpha", "listeners_gained", 10.0)
    strategy_portfolio.record_channel_metric("ch_alpha", "listeners_gained", 25.0)
    strategy_portfolio.record_channel_metric("ch_alpha", "listeners_gained", 5.0)
    # Unrelated metric — should NOT be counted.
    strategy_portfolio.record_channel_metric("ch_alpha", "click_rate", 0.5)
    lei = strategy_portfolio.get_channel_lei("ch_alpha", days=7)
    assert lei == 40.0


def test_get_channel_lei_ignores_old_metrics():
    _reset_registry()
    _wipe_metrics()
    # Manually insert an old row (10 days ago).
    old_date = (datetime.utcnow() - timedelta(days=10)).date().isoformat()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO channel_metrics (channel_id, date, metric_name, metric_value) "
            "VALUES (?,?,?,?)",
            ("ch_alpha", old_date, "listeners_gained", 9999.0),
        )
    strategy_portfolio.record_channel_metric("ch_alpha", "listeners_gained", 7.0)
    lei = strategy_portfolio.get_channel_lei("ch_alpha", days=7)
    assert lei == 7.0


def test_get_channel_lei_zero_when_no_data():
    _reset_registry()
    _wipe_metrics()
    assert strategy_portfolio.get_channel_lei("ch_alpha", days=7) == 0.0


# ── reallocate_weights ─────────────────────────────────────────────────────────


def test_reallocate_weights_normalizes_to_one():
    _reset_registry()
    _wipe_metrics()
    # Strong winner: alpha gets 100, beta 50, gamma 5
    for v in [40, 30, 30]:
        strategy_portfolio.record_channel_metric("ch_alpha", "listeners_gained", v)
    for v in [20, 15, 15]:
        strategy_portfolio.record_channel_metric("ch_beta", "listeners_gained", v)
    strategy_portfolio.record_channel_metric("ch_gamma", "listeners_gained", 5)

    reg = strategy_portfolio.reallocate_weights()
    actives = [c for c in reg["channels"] if c["status"] == "active"]
    total = sum(c["weight"] for c in actives)
    assert abs(total - 1.0) < 1e-6


def test_reallocate_weights_respects_floor_and_ceiling():
    _reset_registry()
    _wipe_metrics()
    # Make alpha a runaway winner.
    for v in [200, 200, 200]:
        strategy_portfolio.record_channel_metric("ch_alpha", "listeners_gained", v)
    # Beta + gamma stay at 0 → should still hit floor.
    reg = strategy_portfolio.reallocate_weights()
    weights = {c["id"]: c["weight"] for c in reg["channels"] if c["status"] == "active"}
    floor = config.BTL_CHANNEL_WEIGHT_FLOOR
    ceiling = config.BTL_CHANNEL_BREAKTHROUGH_CEILING  # winner uses breakthrough cap
    assert weights["ch_alpha"] <= ceiling + 1e-6
    for cid in ("ch_beta", "ch_gamma"):
        assert weights[cid] >= floor - 1e-6


def test_reallocate_weights_writes_last_reallocation():
    _reset_registry()
    _wipe_metrics()
    strategy_portfolio.record_channel_metric("ch_alpha", "listeners_gained", 10)
    reg = strategy_portfolio.reallocate_weights()
    assert reg["last_reallocation"] is not None
    # Also persisted to disk.
    reloaded = strategy_portfolio.load_registry()
    assert reloaded["last_reallocation"] == reg["last_reallocation"]


# ── summary ────────────────────────────────────────────────────────────────────


def test_get_portfolio_summary_shape():
    _reset_registry()
    _wipe_metrics()
    summary = strategy_portfolio.get_portfolio_summary()
    assert "active_count" in summary
    assert "channels" in summary
    assert summary["active_count"] == 3
