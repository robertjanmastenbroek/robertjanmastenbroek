"""Regression: _send_batch must track send_attempts and dead_letter at threshold."""
from unittest.mock import patch, MagicMock
import pytest


def _seed_verified(db, email="curator@example.com"):
    db.add_contact(email, "Test Curator", "curator", "melodic techno", "", source="manual")
    db.mark_verified(email)


def _neutralize_scheduler(monkeypatch):
    """Stub scheduler so tests don't actually sleep or block on windows."""
    import scheduler
    monkeypatch.setattr(scheduler, "wait_for_interval", lambda: None)
    monkeypatch.setattr(scheduler, "random_interval", lambda: 0)

    class _OpenWindow:
        can_send = True
        def status(self): return "open"
        def record_send(self): pass

    monkeypatch.setattr(scheduler, "SendWindow", _OpenWindow)


def test_send_batch_increments_attempts_on_send_failure(temp_db, mock_gmail, fake_claude, monkeypatch):
    db = temp_db
    _neutralize_scheduler(monkeypatch)
    _seed_verified(db)

    mock_gmail["send"].side_effect = RuntimeError("SMTP boom")

    import agent
    result = agent._send_batch(batch_size=1)

    assert result["failed"] == 1
    row = db.get_contact("curator@example.com")
    # mark_queued bumps send_attempts to 1 on the first attempt
    assert row["send_attempts"] == 1
    # Still retryable — back to 'verified' until threshold reached
    assert row["status"] == "verified"


def test_send_batch_dead_letters_after_max_attempts(temp_db, mock_gmail, fake_claude, monkeypatch):
    db = temp_db
    _neutralize_scheduler(monkeypatch)
    _seed_verified(db)

    from config import MAX_SEND_ATTEMPTS
    mock_gmail["send"].side_effect = RuntimeError("SMTP boom")

    import agent
    # Simulate N-1 prior failed attempts so this cycle hits the threshold
    db.update_contact("curator@example.com", send_attempts=MAX_SEND_ATTEMPTS - 1)

    result = agent._send_batch(batch_size=1)

    assert result["failed"] == 1
    row = db.get_contact("curator@example.com")
    assert row["status"] == "dead_letter"
    assert row["send_attempts"] >= MAX_SEND_ATTEMPTS
    assert "DEAD_LETTER" in (row["notes"] or "")


def test_send_batch_dead_letters_on_template_crash(temp_db, mock_gmail, fake_claude, monkeypatch):
    db = temp_db
    _neutralize_scheduler(monkeypatch)
    _seed_verified(db)

    from config import MAX_SEND_ATTEMPTS
    import template_engine

    def _boom(*args, **kwargs):
        raise RuntimeError("template crashed")

    monkeypatch.setattr(template_engine, "generate_email", _boom)

    import agent
    db.update_contact("curator@example.com", send_attempts=MAX_SEND_ATTEMPTS - 1)

    result = agent._send_batch(batch_size=1)

    assert result["failed"] == 1
    row = db.get_contact("curator@example.com")
    assert row["status"] == "dead_letter"
