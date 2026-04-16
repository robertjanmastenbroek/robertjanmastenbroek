"""Regression: run_cycle.cmd_plan publishes pipeline-gap telemetry events.

The goal is operational: when `rjm outreach plan` runs and finds the queue
empty (or nothing sendable), it must emit a `pipeline.gap_detected` event so
the master/health view can surface the gap. A healthy plan with actions must
NOT emit a gap event — silence is the steady state.
"""
import json
import pytest


def test_plan_emits_gap_event_when_queue_empty(temp_db, monkeypatch, capsys):
    import run_cycle
    import events
    import scheduler

    # Force window open + quota available so the only reason for zero actions
    # is an empty verified queue.
    monkeypatch.setattr(scheduler, "is_within_active_window", lambda: True)
    monkeypatch.setattr(scheduler, "remaining_quota_today", lambda: 150)
    monkeypatch.setattr(scheduler, "compute_batch_size", lambda: 10)

    run_cycle.cmd_plan()
    capsys.readouterr()  # discard JSON stdout

    rows = events.recent(event_type="pipeline.gap_detected", limit=5)
    assert len(rows) == 1, f"expected 1 gap event, got {len(rows)}"
    payload = json.loads(rows[0]["payload"])
    assert payload["verified_count"] == 0
    assert payload["action_count"] == 0
    assert "no_sendable_contacts" in payload.get("reasons", [])


def test_plan_silent_when_actions_exist(temp_db, monkeypatch, capsys):
    db = temp_db
    import run_cycle
    import events
    import scheduler
    import template_engine

    # Seed a verified, researched contact that will generate an action
    db.add_contact("ok@example.com", "OK", "curator")
    db.mark_verified("ok@example.com")
    db.update_contact("ok@example.com", research_done=1)

    monkeypatch.setattr(scheduler, "is_within_active_window", lambda: True)
    monkeypatch.setattr(scheduler, "remaining_quota_today", lambda: 150)
    monkeypatch.setattr(scheduler, "compute_batch_size", lambda: 5)
    monkeypatch.setattr(
        template_engine,
        "generate_emails_batch",
        lambda batch, cache: {c["email"]: ("Subject", "Body") for c in batch},
    )

    run_cycle.cmd_plan()
    capsys.readouterr()

    rows = events.recent(event_type="pipeline.gap_detected", limit=5)
    assert rows == [], f"expected silent plan, got {rows}"


def test_plan_gap_event_reports_window_closed(temp_db, monkeypatch, capsys):
    import run_cycle
    import events
    import scheduler

    monkeypatch.setattr(scheduler, "is_within_active_window", lambda: False)
    monkeypatch.setattr(scheduler, "remaining_quota_today", lambda: 150)
    monkeypatch.setattr(scheduler, "compute_batch_size", lambda: 0)

    run_cycle.cmd_plan()
    capsys.readouterr()

    rows = events.recent(event_type="pipeline.gap_detected", limit=5)
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert "window_closed" in payload.get("reasons", [])
