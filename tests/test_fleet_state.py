"""Tests for fleet state heartbeat system."""
import sys, os, tempfile, shutil
from pathlib import Path
import pytest

tmpdir = tempfile.mkdtemp()
os.environ["RJM_DB_PATH"] = str(Path(tmpdir) / "test.db")

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
import config
config.DB_PATH = Path(os.environ["RJM_DB_PATH"])

import db
db.init_db()

import fleet_state


@pytest.fixture(autouse=True)
def clean_fleet():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM fleet_state")
    yield


def teardown_module():
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_heartbeat_creates_record():
    fleet_state.heartbeat("run_cycle", status="ok", result={"sent": 3})
    state = fleet_state.get_all()
    names = [s["agent_name"] for s in state]
    assert "run_cycle" in names


def test_heartbeat_updates_run_count():
    fleet_state.heartbeat("spotify_tracker", status="ok")
    fleet_state.heartbeat("spotify_tracker", status="ok")
    state = fleet_state.get_all()
    tracker = next(s for s in state if s["agent_name"] == "spotify_tracker")
    assert tracker["run_count"] >= 2


def test_get_stale_returns_old_agents():
    from datetime import datetime, timedelta
    old_ts = (datetime.utcnow() - timedelta(hours=5)).isoformat()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fleet_state (agent_name, last_heartbeat, run_count) VALUES (?,?,0)",
            ("stale_agent", old_ts)
        )
    stale = fleet_state.get_stale(threshold_minutes=60)
    names = [s["agent_name"] for s in stale]
    assert "stale_agent" in names


def test_heartbeat_error_increments_error_count():
    fleet_state.heartbeat("bad_agent", status="error", result={"error": "timeout"})
    fleet_state.heartbeat("bad_agent", status="error")
    state = fleet_state.get_all()
    bad = next(s for s in state if s["agent_name"] == "bad_agent")
    assert bad["error_count"] >= 2
