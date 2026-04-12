"""Tests for release trigger system."""
import sys, os, tempfile, shutil
from pathlib import Path
from datetime import date, timedelta
import pytest

tmpdir = tempfile.mkdtemp()
_test_db_path = Path(tmpdir) / "test.db"
os.environ["RJM_DB_PATH"] = str(_test_db_path)

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
import config
config.DB_PATH = _test_db_path

import db
db.DB_PATH = _test_db_path
db.init_db()

import release_trigger


@pytest.fixture(autouse=True)
def clean_releases():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM release_calendar")
        conn.execute("DELETE FROM events")
    yield


def teardown_module():
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_add_release_creates_record():
    release_date = (date.today() + timedelta(days=7)).isoformat()
    release_trigger.add_release("Jericho", release_date, notes="Psytrance single")
    pending = release_trigger.get_pending_releases()
    assert any(r["track_name"] == "Jericho" for r in pending)


def test_get_pending_returns_unfired_only():
    release_date = (date.today() - timedelta(days=1)).isoformat()
    release_trigger.add_release("Living Water", release_date)
    pending = release_trigger.get_pending_releases()
    tracks = [r["track_name"] for r in pending]
    assert "Living Water" in tracks


def test_mark_fired_removes_from_pending():
    release_date = date.today().isoformat()
    release_trigger.add_release("Fire In Our Hands", release_date)
    pending_before = release_trigger.get_pending_releases()
    target = next(r for r in pending_before if r["track_name"] == "Fire In Our Hands")
    release_trigger.mark_fired(target["id"])
    pending_after = release_trigger.get_pending_releases()
    ids_after = [r["id"] for r in pending_after]
    assert target["id"] not in ids_after


def test_check_due_returns_releases_within_window():
    release_date = date.today().isoformat()
    release_trigger.add_release("He Is The Light", release_date, notes="Drop day")
    due = release_trigger.check_due(days_window=0)
    tracks = [r["track_name"] for r in due]
    assert "He Is The Light" in tracks


def test_fire_due_campaigns_publishes_event():
    import events, json
    release_date = date.today().isoformat()
    release_trigger.add_release("Halleluyah", release_date)
    fired = release_trigger.fire_due_campaigns(days_window=0)
    assert len(fired) >= 1
    published = events.subscribe(["release.campaign_fired"], limit=5)
    assert len(published) >= 1
    payload = json.loads(published[0]["payload"])
    assert payload["track_name"] == "Halleluyah"
