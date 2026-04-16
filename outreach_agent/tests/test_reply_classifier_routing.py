"""Regression: reply_classifier routes each intent to the correct DB state.

The classifier is the junction where replies become actions, so every intent
must land in the right status:
  - positive, question, booking_intent → stays 'responded'
  - playlist_added                      → 'won'
  - negative_fit, negative_hard         → 'closed'
  - unsubscribe                         → 'closed' + added to dead_addresses
  - auto_reply                          → reverted to 'sent'

We stub Claude + gmail_client so the test is hermetic and parametric.
"""
import json
import pytest


@pytest.fixture
def seeded_contact(temp_db):
    """Seed a single 'responded' contact ready for classification."""
    db = temp_db
    db.add_contact("replier@example.com", "Replier", "curator")
    db.mark_verified("replier@example.com")
    db.update_contact(
        "replier@example.com",
        status="responded",
        sent_subject="Halleluyah for your psytrance playlist",
        response_snippet="thanks for the track",
        date_response_received="2026-04-01",
    )
    return db


@pytest.mark.parametrize("intent,expected_status", [
    ("positive",        "responded"),
    ("playlist_added",  "won"),
    ("booking_intent",  "responded"),
    ("question",        "responded"),
    ("negative_fit",    "closed"),
    ("negative_hard",   "closed"),
    ("unsubscribe",     "closed"),
    ("auto_reply",      "sent"),
])
def test_intent_routes_to_correct_status(seeded_contact, monkeypatch, intent, expected_status):
    db = seeded_contact
    import reply_classifier
    import gmail_client

    # Stub gmail body fetch so we don't touch the network
    monkeypatch.setattr(gmail_client, "get_full_message_body", lambda mid: "thanks for the track")

    # Stub Claude with the intent we're testing
    def _fake_call(prompt, model=None, timeout=None):
        return json.dumps({
            "intent": intent,
            "confidence": 0.95,
            "summary": f"testing {intent}",
            "suggested_action": "noop for test",
        })
    monkeypatch.setattr(reply_classifier, "_call_claude", _fake_call)

    contact = db.get_contact("replier@example.com")
    classification = reply_classifier.classify_one(contact)
    assert classification is not None
    assert classification["intent"] == intent

    reply_classifier._apply_routing(contact, classification)

    row = db.get_contact("replier@example.com")
    assert row["status"] == expected_status, (
        f"intent={intent!r}: expected status {expected_status!r}, got {row['status']!r}"
    )


def test_unsubscribe_adds_dead_address(seeded_contact, monkeypatch):
    db = seeded_contact
    import reply_classifier
    import gmail_client
    import bounce

    monkeypatch.setattr(gmail_client, "get_full_message_body", lambda mid: "please remove me")
    monkeypatch.setattr(
        reply_classifier,
        "_call_claude",
        lambda p, model=None, timeout=None: json.dumps({
            "intent": "unsubscribe",
            "confidence": 0.99,
            "summary": "remove me",
            "suggested_action": "remove",
        }),
    )

    contact = db.get_contact("replier@example.com")
    classification = reply_classifier.classify_one(contact)
    reply_classifier._apply_routing(contact, classification)

    # Address must now be in the bounce dead list
    bounce._load_dead_lists()
    assert "replier@example.com" in bounce._dead_addresses


def test_unknown_intent_defaults_to_negative_fit(seeded_contact, monkeypatch):
    db = seeded_contact
    import reply_classifier
    import gmail_client

    monkeypatch.setattr(gmail_client, "get_full_message_body", lambda mid: "¯\\_(ツ)_/¯")
    monkeypatch.setattr(
        reply_classifier,
        "_call_claude",
        lambda p, model=None, timeout=None: json.dumps({
            "intent": "sparkly_maybe",
            "confidence": 0.7,
            "summary": "weird",
            "suggested_action": "?",
        }),
    )

    contact = db.get_contact("replier@example.com")
    classification = reply_classifier.classify_one(contact)
    assert classification["intent"] == "negative_fit"
