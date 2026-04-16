"""Regression: add_contact enforces org-domain dedup for custom domains only."""
import pytest


def test_exact_email_duplicate_rejected(temp_db):
    db = temp_db
    ok, _ = db.add_contact("curator@label.com", "Curator", "curator")
    assert ok
    ok2, reason = db.add_contact("curator@label.com", "Curator", "curator")
    assert not ok2
    assert "duplicate" in reason


def test_org_duplicate_rejected_for_custom_domain(temp_db):
    db = temp_db
    ok, _ = db.add_contact("alice@indielabel.com", "Alice", "curator")
    assert ok
    db.mark_verified("alice@indielabel.com")
    # Simulate prior send
    db.update_contact("alice@indielabel.com", status="sent", date_sent="2026-04-01")

    # Now a second contact at the same custom domain should be blocked
    ok2, reason = db.add_contact("bob@indielabel.com", "Bob", "curator")
    assert not ok2
    assert "org duplicate" in reason
    assert "alice@indielabel.com" in reason


def test_org_duplicate_not_enforced_for_shared_provider(temp_db):
    db = temp_db
    # Shared providers (gmail, hotmail, etc.) MUST NOT trigger org-level dedup
    ok, _ = db.add_contact("alice@gmail.com", "Alice", "curator")
    assert ok
    db.update_contact("alice@gmail.com", status="sent", date_sent="2026-04-01")

    ok2, _ = db.add_contact("bob@gmail.com", "Bob", "podcast")
    assert ok2, "shared email provider should allow multiple contacts"


def test_org_dedup_ignores_unsent_statuses(temp_db):
    db = temp_db
    ok, _ = db.add_contact("alice@indielabel.com", "Alice", "curator")
    assert ok
    # 'verified' alone shouldn't block a second contact at the same domain —
    # only after we've actually sent mail does org-level dedup kick in.
    db.mark_verified("alice@indielabel.com")

    ok2, _ = db.add_contact("bob@indielabel.com", "Bob", "curator")
    assert ok2
