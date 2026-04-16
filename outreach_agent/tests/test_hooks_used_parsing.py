"""Regression: template_engine parses hooks_used from Claude response.

When Claude returns `"hooks_used": [...]` in its JSON, we trust the model's
self-reported hooks over the heuristic fallback. The audit row should then
record those exact hooks, so personalization_audit reflects what the model
actually used.
"""
import json
import pytest


def test_parse_response_returns_hooks_when_present():
    import template_engine

    raw = json.dumps({
        "subject": "Hey Alice",
        "body": "Your Friday Techno playlist caught me — Halleluyah 140 BPM https://open.spotify.com/track/abc — RJM",
        "hooks_used": ["research", "bpm_match"],
    })
    subject, body, hooks = template_engine._parse_response_with_hooks(raw)
    assert subject == "Hey Alice"
    assert "Halleluyah" in body
    assert hooks == ["research", "bpm_match"]


def test_parse_response_returns_empty_hooks_when_absent():
    import template_engine

    raw = json.dumps({
        "subject": "Hey Bob",
        "body": "Tribal psytrance 140 BPM — Jericho https://open.spotify.com/track/xyz — RJM",
    })
    subject, body, hooks = template_engine._parse_response_with_hooks(raw)
    assert hooks == []


def test_generate_email_records_model_hooks_in_audit(temp_db, monkeypatch):
    db = temp_db
    import template_engine

    db.add_contact("alice@example.com", "Alice", "curator", "melodic techno", "")
    db.update_contact("alice@example.com", research_notes="Friday Techno playlist, 8.4k followers")

    def fake_call(prompt, model=None, timeout=None):
        return json.dumps({
            "subject": "Hey Alice",
            "body": "Your Friday Techno playlist — Halleluyah 140 BPM https://open.spotify.com/track/abc — RJM",
            "hooks_used": ["research", "playlist_mention", "bpm_match"],
        })
    monkeypatch.setattr(template_engine, "_call_claude", fake_call)

    contact = db.get_contact("alice@example.com")
    template_engine.generate_email(contact)

    rows = db.get_personalization_audit(email="alice@example.com")
    assert len(rows) == 1
    hooks = json.loads(rows[0]["hooks_used"])
    assert hooks == ["research", "playlist_mention", "bpm_match"]
