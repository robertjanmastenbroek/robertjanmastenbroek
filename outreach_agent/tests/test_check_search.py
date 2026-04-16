"""Regression: run_cycle.cmd_check_search blocks duplicate discovery queries.

The rjm-discover skill calls this before running each genre-cluster search.
If the same query ran in the last 48h, we skip it to avoid wasting API calls
on the same snippet set. The CLI exits 0 (=proceed) or 1 (=skip-duplicate),
and prints a one-line human hint.
"""
import pytest


def test_check_search_clean_query_exits_zero(temp_db, capsys):
    import run_cycle

    rc = run_cycle.cmd_check_search("tribal techno curator email")
    assert rc == 0
    out = capsys.readouterr().out
    assert "proceed" in out.lower()


def test_check_search_duplicate_exits_one(temp_db, capsys):
    db = temp_db
    import run_cycle

    # Simulate a prior search for this query
    db.log_discovery("tribal techno curator email", "curator", 3)

    rc = run_cycle.cmd_check_search("tribal techno curator email")
    assert rc == 1
    out = capsys.readouterr().out
    assert "skip" in out.lower() or "duplicate" in out.lower()


def test_check_search_stale_query_exits_zero(temp_db, capsys, monkeypatch):
    """A query older than the 48h window is no longer a duplicate."""
    db = temp_db
    import run_cycle
    from datetime import datetime, timedelta

    # Insert an aged row directly — recently_searched checks searched_at
    old = (datetime.now() - timedelta(hours=60)).isoformat()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO discovery_log (search_query, contact_type, results_found, searched_at)"
            " VALUES (?, ?, ?, ?)",
            ("psytrance podcast submit", "podcast", 2, old),
        )

    rc = run_cycle.cmd_check_search("psytrance podcast submit")
    assert rc == 0
