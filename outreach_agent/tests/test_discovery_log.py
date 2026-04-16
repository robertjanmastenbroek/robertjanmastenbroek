"""Regression: run_cycle.cmd_add_contact writes a discovery_log row."""
import pytest


def test_add_contact_logs_discovery(temp_db, monkeypatch):
    db = temp_db
    import run_cycle
    import bounce

    # Neutralise DNS/MX probe
    monkeypatch.setattr(bounce, "verify_email", lambda e: ("valid", "ok"))

    run_cycle.cmd_add_contact(
        email="new@example.com",
        name="New Curator",
        ctype="curator",
        genre="melodic techno",
        notes="test",
        search_query="melodic techno spotify curator email",
    )

    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM discovery_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["search_query"] == "melodic techno spotify curator email"
    assert rows[0]["contact_type"] == "curator"
    assert rows[0]["results_found"] == 1


def test_add_contact_logs_discovery_on_duplicate(temp_db, monkeypatch):
    db = temp_db
    import run_cycle
    import bounce

    monkeypatch.setattr(bounce, "verify_email", lambda e: ("valid", "ok"))

    # Seed an existing contact
    db.add_contact("dup@example.com", "Dup", "curator", "", "", source="manual")

    run_cycle.cmd_add_contact("dup@example.com", "Dup", "curator", search_query="q1")

    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM discovery_log").fetchall()
    assert len(rows) == 1
    # Duplicate rejected → results_found=0
    assert rows[0]["results_found"] == 0


def test_add_contact_uses_manual_add_fallback_query(temp_db, monkeypatch):
    db = temp_db
    import run_cycle
    import bounce

    monkeypatch.setattr(bounce, "verify_email", lambda e: ("valid", "ok"))

    run_cycle.cmd_add_contact("noquery@example.com", "No Query", "podcast")

    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM discovery_log").fetchone()
    assert row["search_query"] == "manual_add:podcast"
