"""Tests for BTL database migrations."""
import sqlite3
import sys
import os
import tempfile
import shutil
from pathlib import Path

tmpdir = tempfile.mkdtemp()
os.environ["RJM_DB_PATH"] = str(Path(tmpdir) / "test_btl.db")

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
import config
config.DB_PATH = Path(os.environ["RJM_DB_PATH"])

import db
import btl_db


def teardown_module():
    shutil.rmtree(tmpdir, ignore_errors=True)


def _get_tables(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def _get_columns(conn, table):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def test_btl_tables_created():
    db.init_db()
    btl_db.init_btl_tables()
    with db.get_conn() as conn:
        tables = _get_tables(conn)
        assert "experiments" in tables
        assert "proposals" in tables
        assert "growth_budget" in tables
        assert "channel_metrics" in tables
        assert "bandit_state" in tables
        assert "strategic_insights" in tables


def test_experiments_schema():
    db.init_db()
    btl_db.init_btl_tables()
    with db.get_conn() as conn:
        cols = _get_columns(conn, "experiments")
        for col in ["id", "channel", "hypothesis", "status", "proposed_at",
                     "execute_after", "started_at", "ended_at", "result",
                     "learning", "metrics_log"]:
            assert col in cols, f"Missing column: {col}"


def test_idempotent():
    db.init_db()
    btl_db.init_btl_tables()
    btl_db.init_btl_tables()  # should not error
