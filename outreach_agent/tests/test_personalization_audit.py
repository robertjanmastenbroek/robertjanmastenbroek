"""Regression: every generate_email call records a personalization_audit row."""
import json
import pytest


def test_generate_email_logs_audit_row(temp_db, fake_claude, monkeypatch):
    db = temp_db
    import template_engine

    contact = {
        "email": "curator@example.com",
        "name": "Test Curator",
        "type": "curator",
        "genre": "psytrance",
        "notes": "Christian melodic techno playlist, 138 BPM rotation",
        "research_notes": "Runs the 'Ancient Future' playlist, 8K followers",
    }

    subject, body = template_engine.generate_email(contact, learning_context="avg reply 12%")

    rows = db.get_personalization_audit("curator@example.com", limit=5)
    assert len(rows) == 1
    row = rows[0]
    assert row["contact_type"] == "curator"
    assert row["subject"] == subject
    assert row["body_snippet"]
    assert row["research_used"] == 1
    assert row["learning_applied"] == 1

    # The fake_claude fixture returns hooks_used=["bpm_match","genre_fallback"]
    # verbatim. With model-reported hooks now trusted, those are what we record.
    hooks = json.loads(row["hooks_used"])
    assert "bpm_match" in hooks
    assert "genre_fallback" in hooks


def test_generate_email_records_brand_gate_pass_state(temp_db, fake_claude, monkeypatch):
    db = temp_db
    import template_engine

    # Fake_claude fixture returns a body that mentions Halleluyah/psytrance/140 BPM,
    # so the brand gate should pass cleanly.
    contact = {
        "email": "noresearch@example.com",
        "name": "Bare Curator",
        "type": "curator",
        "genre": "melodic techno",
        "notes": "",
    }

    template_engine.generate_email(contact)

    rows = db.get_personalization_audit("noresearch@example.com")
    assert len(rows) == 1
    assert rows[0]["research_used"] == 0
    assert rows[0]["brand_gate_passed"] in (0, 1)  # field populated
    # body_snippet is truncated to 400 chars
    assert len(rows[0]["body_snippet"]) <= 400
