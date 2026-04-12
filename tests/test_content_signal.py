"""Tests for cross-platform content state log."""
import sys, os, tempfile, shutil
from pathlib import Path
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

import content_signal


@pytest.fixture(autouse=True)
def clean_content_log():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM content_log")
    yield


def teardown_module():
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_log_content_post_creates_record():
    content_signal.log_content_post(
        platform="tiktok",
        format="reels",
        track="Jericho",
        angle="energy",
        hook="140 BPM and the crowd hasn't moved yet",
        buffer_id="buf_abc123",
        filename="jericho_energy_test.mp4"
    )
    state = content_signal.get_cross_platform_state()
    assert len(state) == 1
    assert state[0]["platform"] == "tiktok"
    assert state[0]["track"] == "Jericho"


def test_weekly_summary_counts_platforms():
    content_signal.log_content_post(platform="tiktok", format="reels", track="Jericho")
    content_signal.log_content_post(platform="instagram_reels", format="reels", track="Jericho")
    content_signal.log_content_post(platform="youtube", format="short", track="Living Water")
    summary = content_signal.get_weekly_summary()
    assert summary["total_posts"] == 3
    assert summary["by_platform"].get("tiktok", 0) == 1
    assert summary["by_platform"].get("instagram_reels", 0) == 1


def test_get_cross_platform_state_returns_recent():
    content_signal.log_content_post(platform="tiktok", format="reels", track="Jericho")
    state = content_signal.get_cross_platform_state(days=7)
    assert isinstance(state, list)
    assert len(state) >= 1


def test_log_publishes_event():
    # events table should receive a content.post_published event
    import events
    with db.get_conn() as conn:
        conn.execute("DELETE FROM events")
    content_signal.log_content_post(platform="youtube", format="short", track="Halleluyah")
    published = events.subscribe(["content.post_published"], limit=5)
    assert len(published) >= 1
    import json
    payload = json.loads(published[0]["payload"])
    assert payload["platform"] == "youtube"
