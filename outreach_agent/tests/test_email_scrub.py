"""Tests for email_scrub.py."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_scrub_marks_invalid_as_skip(temp_db, monkeypatch):
    import db
    import email_scrub

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("dead@deadomain.xyz", "Dead Contact", "curator", "verified"),
        )

    monkeypatch.setattr(
        "bounce.verify_email",
        lambda e: ("invalid", "Disify: domain has no MX record"),
    )

    result = email_scrub.scrub(limit=10, dry_run=False)
    assert result["marked_skip"] == 1

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status, bounce FROM contacts WHERE email=?",
            ("dead@deadomain.xyz",),
        ).fetchone()
    assert row[0] == "skip"
    assert row[1] == "pre-check"


def test_scrub_keeps_valid(temp_db, monkeypatch):
    import db
    import email_scrub

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("good@gmail.com", "Good Contact", "curator", "verified"),
        )

    monkeypatch.setattr(
        "bounce.verify_email",
        lambda e: ("valid", "major provider fast-path"),
    )

    result = email_scrub.scrub(limit=10, dry_run=False)
    assert result["confirmed_valid"] >= 1

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM contacts WHERE email=?", ("good@gmail.com",)
        ).fetchone()
    assert row[0] == "verified"


def test_scrub_dry_run_does_not_write(temp_db, monkeypatch):
    import db
    import email_scrub

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("dead2@deadomain.xyz", "Dead2", "curator", "new"),
        )

    monkeypatch.setattr(
        "bounce.verify_email",
        lambda e: ("invalid", "no MX"),
    )

    email_scrub.scrub(limit=10, dry_run=True)

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM contacts WHERE email=?",
            ("dead2@deadomain.xyz",),
        ).fetchone()
    assert row[0] == "new"  # unchanged in dry-run


def test_scrub_ignores_already_sent(temp_db, monkeypatch):
    import db
    import email_scrub

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("sent@example.com", "Already Sent", "curator", "sent"),
        )

    called = []
    monkeypatch.setattr(
        "bounce.verify_email",
        lambda e: called.append(e) or ("invalid", "no MX"),
    )

    email_scrub.scrub(limit=10, dry_run=False)
    assert "sent@example.com" not in called


def test_scrub_returns_correct_counts(temp_db, monkeypatch):
    import db
    import email_scrub

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("a@dead.xyz", "A", "curator", "new"),
        )
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("b@gmail.com", "B", "curator", "verified"),
        )
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("c@unknown.tld", "C", "curator", "new"),
        )

    def fake_verify(email):
        if "dead" in email:
            return ("invalid", "no MX")
        if "gmail" in email:
            return ("valid", "major provider")
        return ("unknown", "inconclusive")

    monkeypatch.setattr("bounce.verify_email", fake_verify)

    result = email_scrub.scrub(limit=10, dry_run=False)
    assert result["checked"] == 3
    assert result["marked_skip"] == 1
    assert result["confirmed_valid"] == 1
    assert result["inconclusive"] == 1
