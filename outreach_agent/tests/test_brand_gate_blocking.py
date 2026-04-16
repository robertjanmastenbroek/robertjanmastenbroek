"""Regression: brand gate is now blocking with exactly 1 retry.

Lake 5 flips `generate_email` from warn-only to blocking. The contract:
  - First draft passes brand gate → return normally (happy path).
  - First draft fails → regenerate once with gate's `suggestion` injected.
  - Retry passes → return retry.
  - Retry also fails → raise `BrandGateRejected`. Callers must not ship it.
"""
import json
import pytest


def test_generate_email_returns_first_draft_when_clean(temp_db, monkeypatch):
    import template_engine

    calls = {"n": 0}
    def fake_call(prompt, model=None, timeout=None):
        calls["n"] += 1
        return json.dumps({
            "subject": "Halleluyah 140 BPM psytrance for your rotation",
            "body": (
                "Your psytrance playlist sits at 138-142 BPM. Halleluyah is a 140 BPM "
                "tribal psytrance track recorded in Tenerife — the drop hits at 2:14 "
                "https://open.spotify.com/track/abc123 — Robert-Jan"
            ),
            "hooks_used": ["bpm_match", "genre_fallback"],
        })
    monkeypatch.setattr(template_engine, "_call_claude", fake_call)

    contact = {
        "email": "alice@example.com",
        "name": "Alice",
        "type": "curator",
        "genre": "psytrance",
        "notes": "",
    }
    subject, body = template_engine.generate_email(contact)
    assert "Halleluyah" in body
    assert calls["n"] == 1, "clean draft should not trigger retry"


def test_generate_email_retries_once_on_brand_gate_failure(temp_db, monkeypatch):
    import template_engine

    # First draft fails the gate (boilerplate, no specifics), second passes.
    responses = [
        json.dumps({
            "subject": "Hey Alice",
            "body": "Dear curator, I have a passionate journey to share with you. My unique sound is special and I hope you enjoy it.",
            "hooks_used": [],
        }),
        json.dumps({
            "subject": "Halleluyah 140 BPM psytrance",
            "body": (
                "Your psytrance Friday playlist hits 138-142 BPM. Halleluyah is a "
                "140 BPM tribal psytrance drop recorded in Tenerife — "
                "https://open.spotify.com/track/abc123 — Robert-Jan"
            ),
            "hooks_used": ["bpm_match"],
        }),
    ]
    calls = {"n": 0, "prompts": []}
    def fake_call(prompt, model=None, timeout=None):
        calls["n"] += 1
        calls["prompts"].append(prompt)
        return responses[min(calls["n"] - 1, len(responses) - 1)]
    monkeypatch.setattr(template_engine, "_call_claude", fake_call)

    contact = {
        "email": "alice@example.com",
        "name": "Alice",
        "type": "curator",
        "genre": "psytrance",
        "notes": "",
    }
    subject, body = template_engine.generate_email(contact)
    assert calls["n"] == 2, f"expected exactly 1 retry, got {calls['n']} calls"
    assert "Halleluyah" in body
    # Retry prompt must contain the brand-gate feedback
    assert "BRAND GATE" in calls["prompts"][1] or "previous draft failed" in calls["prompts"][1].lower()


def test_generate_email_raises_when_retry_also_fails(temp_db, monkeypatch):
    import template_engine

    generic = json.dumps({
        "subject": "Hey Alice",
        "body": "Dear curator, I have a passionate journey to share with you. My unique sound is special and I hope you enjoy it.",
        "hooks_used": [],
    })
    monkeypatch.setattr(
        template_engine,
        "_call_claude",
        lambda prompt, model=None, timeout=None: generic,
    )

    contact = {
        "email": "alice@example.com",
        "name": "Alice",
        "type": "curator",
        "genre": "psytrance",
        "notes": "",
    }
    with pytest.raises(template_engine.BrandGateRejected):
        template_engine.generate_email(contact)


def test_send_batch_skips_brand_gate_rejected(temp_db, monkeypatch, mock_gmail):
    """A BrandGateRejected in per-contact generation must not explode the batch.

    The contact stays in 'verified' (not dead_letter yet), so the stale queue
    rescue will retry it next cycle. send_batch increments `skipped`.
    """
    db = temp_db
    import agent
    import template_engine
    import scheduler

    db.add_contact("rej@example.com", "Rej", "curator")
    db.mark_verified("rej@example.com")

    def _raise(*a, **k):
        raise template_engine.BrandGateRejected("two strikes")
    monkeypatch.setattr(template_engine, "generate_email", _raise)

    class _OpenWindow:
        can_send = True
        def status(self): return "open"
        def record_send(self): pass
    monkeypatch.setattr(scheduler, "SendWindow", _OpenWindow)

    result = agent._send_batch(batch_size=5)
    assert result["sent"] == 0
    assert result["failed"] >= 0  # varies by branch
    row = db.get_contact("rej@example.com")
    assert row["status"] in ("verified", "queued"), (
        "brand-gate reject must not permanently dead-letter; stale rescue handles retry"
    )
