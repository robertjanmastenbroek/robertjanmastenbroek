"""Regression: stale-queued + stale-research rescue behavior."""
import json
from datetime import datetime, timedelta
import pytest


def test_stale_queued_rescued_under_threshold(temp_db):
    db = temp_db
    import run_cycle

    db.add_contact("slow@example.com", "Slow", "curator")
    db.mark_verified("slow@example.com")
    # Force into queued state with send_attempts=1 and queued 3h ago
    old_ts = (datetime.now() - timedelta(hours=3)).isoformat()
    db.update_contact("slow@example.com", status="queued", date_queued=old_ts, send_attempts=1)

    run_cycle._rescue_stale_queued()

    row = db.get_contact("slow@example.com")
    assert row["status"] == "verified"


def test_stale_queued_dead_letters_at_threshold(temp_db):
    db = temp_db
    import run_cycle
    from config import MAX_SEND_ATTEMPTS

    db.add_contact("dead@example.com", "Dead", "curator")
    db.mark_verified("dead@example.com")
    old_ts = (datetime.now() - timedelta(hours=3)).isoformat()
    db.update_contact(
        "dead@example.com", status="queued", date_queued=old_ts,
        send_attempts=MAX_SEND_ATTEMPTS,
    )

    run_cycle._rescue_stale_queued()

    row = db.get_contact("dead@example.com")
    assert row["status"] == "dead_letter"
    assert "DEAD_LETTER" in (row["notes"] or "")


def test_stale_queued_publishes_event(temp_db):
    db = temp_db
    import run_cycle
    import events

    db.add_contact("e1@example.com", "E1", "curator")
    db.mark_verified("e1@example.com")
    old_ts = (datetime.now() - timedelta(hours=3)).isoformat()
    db.update_contact("e1@example.com", status="queued", date_queued=old_ts)

    run_cycle._rescue_stale_queued()

    rows = events.recent(event_type="pipeline.stale_queued", limit=5)
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["rescued"] == 1
    assert payload["dead_lettered"] == 0


def test_stale_research_publishes_event(temp_db):
    db = temp_db
    import run_cycle
    import events

    db.add_contact("stale@example.com", "Stale", "curator")
    db.mark_verified("stale@example.com")
    # Force date_verified to 5 days ago
    old = (datetime.now() - timedelta(days=5)).date().isoformat()
    db.update_contact("stale@example.com", date_verified=old, research_done=0)

    count = run_cycle._detect_stale_research(max_age_days=3)
    assert count == 1

    rows = events.recent(event_type="pipeline.stale_research", limit=5)
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["count"] == 1


def test_stale_research_silent_when_fresh(temp_db):
    db = temp_db
    import run_cycle
    import events

    db.add_contact("fresh@example.com", "Fresh", "curator")
    db.mark_verified("fresh@example.com")

    count = run_cycle._detect_stale_research(max_age_days=3)
    assert count == 0
    assert events.recent(event_type="pipeline.stale_research", limit=5) == []
