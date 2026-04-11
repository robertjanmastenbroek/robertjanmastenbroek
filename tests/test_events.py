"""Tests for the event backbone."""
import sys, os, json, tempfile, shutil
from pathlib import Path
import pytest

tmpdir = tempfile.mkdtemp()
os.environ["RJM_DB_PATH"] = str(Path(tmpdir) / "test.db")

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
import config
config.DB_PATH = Path(os.environ["RJM_DB_PATH"])

import db
db.init_db()

import events


@pytest.fixture(autouse=True)
def clean_events():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM events")
    yield


def teardown_module():
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_publish_creates_event():
    events.publish("email.sent", "test_agent", {"email": "x@y.com", "template": "curator"})
    rows = events.subscribe(["email.sent"], limit=10)
    assert len(rows) >= 1
    assert rows[0]["event_type"] == "email.sent"
    payload = json.loads(rows[0]["payload"])
    assert payload["email"] == "x@y.com"


def test_subscribe_filters_by_type():
    events.publish("email.sent", "test_agent", {"email": "a@a.com"})
    events.publish("bounce.detected", "test_agent", {"email": "z@z.com"})
    rows = events.subscribe(["email.sent"], limit=10)
    for r in rows:
        assert r["event_type"] == "email.sent"


def test_subscribe_returns_empty_on_no_event_types():
    events.publish("email.sent", "test_agent", {"email": "b@b.com"})
    rows = events.subscribe([], limit=10)
    assert rows == []


def test_mark_consumed():
    events.publish("reply.detected", "test_agent", {"email": "a@b.com"})
    rows = events.subscribe(["reply.detected"], limit=10)
    event_id = rows[0]["id"]
    events.mark_consumed(event_id, "master_agent")
    unconsumed = events.subscribe(["reply.detected"], exclude_consumed_by="master_agent", limit=10)
    ids = [r["id"] for r in unconsumed]
    assert event_id not in ids


def test_recent_returns_results():
    for i in range(3):
        events.publish("test.event", "agent", {"i": i})
    rows = events.recent(event_type="test.event", limit=3)
    assert len(rows) >= 3


def test_publish_raises_on_non_serialisable_payload():
    import datetime
    with pytest.raises(ValueError, match="not JSON-serialisable"):
        events.publish("bad.event", "test_agent", {"ts": datetime.datetime.now()})
