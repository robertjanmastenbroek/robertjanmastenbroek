"""Regression: reply_responder must release the claim on any failure path."""
from unittest.mock import patch, MagicMock
import pytest


def _seed_positive_reply(db):
    db.add_contact("curator@example.com", "Test Curator", "curator", "melodic techno", "", source="manual")
    db.mark_verified("curator@example.com")
    db.update_contact(
        "curator@example.com",
        reply_intent="positive",
        reply_action="send_track",
        reply_classified_at="2026-04-16T10:00:00",
        gmail_thread_id="thr-123",
        reply_message_id="msg-123",
        sent_subject="Living Water for your playlist",
    )


def test_reply_responder_releases_claim_on_send_failure(temp_db, mock_gmail, fake_claude):
    db = temp_db
    import reply_responder
    _seed_positive_reply(db)

    mock_gmail["send"].side_effect = RuntimeError("boom")

    result = reply_responder.run(dry_run=False)

    assert result["failed"] == 1
    row = db.get_contact("curator@example.com")
    assert row["date_replied"] is None, "claim must be released after failure"


def test_reply_responder_marks_replied_on_success(temp_db, mock_gmail, fake_claude):
    db = temp_db
    import reply_responder
    _seed_positive_reply(db)

    result = reply_responder.run(dry_run=False)

    assert result["sent"] == 1
    row = db.get_contact("curator@example.com")
    assert row["date_replied"] is not None
    assert row["date_replied"] != "processing"
