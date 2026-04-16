"""Boil the Lake protocol — database migrations."""

import logging
import db

log = logging.getLogger(__name__)

BTL_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    tactic TEXT,
    cost_type TEXT DEFAULT 'free',
    cost_estimate REAL DEFAULT 0,
    expected_metric TEXT,
    expected_target REAL,
    expected_confidence REAL,
    duration_days INTEGER DEFAULT 21,
    success_criteria TEXT,
    failure_criteria TEXT,
    guardrails TEXT,
    status TEXT DEFAULT 'proposed',
    proposed_at TEXT,
    execute_after TEXT,
    started_at TEXT,
    ended_at TEXT,
    result TEXT,
    learning TEXT,
    metrics_log TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    hypothesis TEXT,
    risk_level TEXT DEFAULT 'low',
    estimated_impact TEXT,
    proposed_at TEXT NOT NULL,
    execute_after TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    veto_reason TEXT,
    executed_at TEXT,
    experiment_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS growth_budget (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    type TEXT NOT NULL,
    amount REAL NOT NULL,
    source TEXT,
    channel TEXT,
    experiment_id TEXT,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS channel_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    date TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bandit_state (
    channel TEXT NOT NULL,
    arm_name TEXT NOT NULL,
    arm_value TEXT NOT NULL,
    alpha REAL DEFAULT 1.0,
    beta REAL DEFAULT 1.0,
    samples INTEGER DEFAULT 0,
    last_updated TEXT,
    PRIMARY KEY (channel, arm_name, arm_value)
);

CREATE TABLE IF NOT EXISTS strategic_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    insight TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    applicable_channels TEXT,
    discovered_at TEXT DEFAULT (datetime('now')),
    validated INTEGER DEFAULT 0,
    applied_count INTEGER DEFAULT 0
);
"""

_DAILY_STATS_EXTENSIONS = [
    ("listeners_delta", "INTEGER DEFAULT 0"),
    ("growth_score", "INTEGER DEFAULT 0"),
    ("active_experiments", "INTEGER DEFAULT 0"),
    ("budget_available", "REAL DEFAULT 0"),
]


def init_btl_tables():
    """Create BTL tables and extend daily_stats. Safe to call repeatedly."""
    with db.get_conn() as conn:
        conn.executescript(BTL_SCHEMA)
        for col_name, col_def in _DAILY_STATS_EXTENSIONS:
            try:
                conn.execute(f"ALTER TABLE daily_stats ADD COLUMN {col_name} {col_def}")
            except Exception:
                pass
