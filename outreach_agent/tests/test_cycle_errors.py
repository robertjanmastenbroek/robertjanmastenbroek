"""Regression: cycle steps publish events on failure instead of failing silently."""
import pytest


def test_run_step_publishes_event_on_failure(temp_db, monkeypatch):
    db = temp_db
    import agent
    import events

    def _boom():
        raise RuntimeError("oauth expired")

    result = agent._run_step("inbox_check", _boom)
    assert result is None

    rows = events.recent(event_type="agent.step_failed", limit=5)
    assert len(rows) == 1
    import json
    payload = json.loads(rows[0]["payload"])
    assert payload["step"] == "inbox_check"
    assert "oauth expired" in payload["error"]
    assert payload["error_type"] == "RuntimeError"


def test_run_step_returns_value_on_success(temp_db):
    import agent
    import events

    def _ok():
        return {"classified": 3}

    result = agent._run_step("reply_classify", _ok)
    assert result == {"classified": 3}

    # Success path must not publish a failure event
    assert events.recent(event_type="agent.step_failed", limit=5) == []
