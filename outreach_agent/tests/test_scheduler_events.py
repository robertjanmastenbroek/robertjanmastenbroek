"""Regression: SendWindow publishes rate_limit.hit when gates block sending."""
import json
import pytest


def test_sendwindow_publishes_outside_window(temp_db, monkeypatch):
    import scheduler
    import events

    monkeypatch.setattr(scheduler, "is_within_active_window", lambda: False)
    monkeypatch.setattr(scheduler, "remaining_quota_today", lambda: 100)
    monkeypatch.setattr(scheduler, "minimum_interval_satisfied", lambda: True)
    monkeypatch.setattr(scheduler, "bounce_rate_safe", lambda: (True, "ok"))
    monkeypatch.setattr(scheduler, "seconds_until_window_opens", lambda: 3600)

    w = scheduler.SendWindow()
    assert w.can_send is False

    rows = events.recent(event_type="rate_limit.hit", limit=5)
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["reason"] == "outside_window"
    assert payload["seconds_until_open"] == 3600


def test_sendwindow_publishes_bounce_rate(temp_db, monkeypatch):
    import scheduler
    import events

    monkeypatch.setattr(scheduler, "is_within_active_window", lambda: True)
    monkeypatch.setattr(scheduler, "remaining_quota_today", lambda: 100)
    monkeypatch.setattr(scheduler, "minimum_interval_satisfied", lambda: True)
    monkeypatch.setattr(scheduler, "bounce_rate_safe", lambda: (False, "BOUNCE RATE TOO HIGH: 5/20"))

    scheduler.SendWindow()

    rows = events.recent(event_type="rate_limit.hit", limit=5)
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["reason"] == "bounce_rate"
    assert "BOUNCE" in payload["status"]


def test_sendwindow_publishes_daily_quota(temp_db, monkeypatch):
    import scheduler
    import events

    monkeypatch.setattr(scheduler, "is_within_active_window", lambda: True)
    monkeypatch.setattr(scheduler, "remaining_quota_today", lambda: 0)
    monkeypatch.setattr(scheduler, "minimum_interval_satisfied", lambda: True)
    monkeypatch.setattr(scheduler, "bounce_rate_safe", lambda: (True, "ok"))

    scheduler.SendWindow()

    rows = events.recent(event_type="rate_limit.hit", limit=5)
    assert any(json.loads(r["payload"])["reason"] == "daily_quota" for r in rows)


def test_sendwindow_silent_when_open(temp_db, monkeypatch):
    import scheduler
    import events

    monkeypatch.setattr(scheduler, "is_within_active_window", lambda: True)
    monkeypatch.setattr(scheduler, "remaining_quota_today", lambda: 100)
    monkeypatch.setattr(scheduler, "minimum_interval_satisfied", lambda: True)
    monkeypatch.setattr(scheduler, "bounce_rate_safe", lambda: (True, "ok"))

    w = scheduler.SendWindow()
    assert w.can_send is True
    assert events.recent(event_type="rate_limit.hit", limit=5) == []
