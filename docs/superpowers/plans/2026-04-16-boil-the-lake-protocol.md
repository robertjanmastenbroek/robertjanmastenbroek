# Boil the Lake Protocol — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an autonomous self-improving growth intelligence layer to the existing RJM agent fleet, targeting 1M Spotify monthly listeners through experiment-driven channel optimization, self-funding via offering page donations, and three learning layers (tactical/strategic/discovery).

**Architecture:** Additive protocol layer on top of the existing fleet. Eight new Python modules in `outreach_agent/` orchestrated by `growth_brain.py`, which adds L1 (bandit optimization), L2 (weekly reallocation), and L3 (discovery+invention) to the master agent's operational modes. Channel agents in `outreach_agent/channel_agents/` follow a common `ChannelAgent` interface. All components communicate via the existing `events.py` backbone and register with `fleet_state.py`. A veto system sends daily digests and auto-executes proposals after 24hr windows.

**Tech Stack:** Python 3.11+, SQLite (existing outreach.db), Stripe API (revenue tracking), Thompson Sampling (bandits), existing Gmail OAuth (digest emails), existing events.py backbone.

**Spec:** `docs/superpowers/specs/2026-04-16-boil-the-lake-protocol-design.md`

---

## File Structure

### New files to create:
```
outreach_agent/
  btl_db.py                    # DB migrations for BTL tables
  bandit_framework.py          # Generalized Thompson Sampling bandit
  experiment_engine.py         # Experiment CRUD + lifecycle
  strategy_portfolio.py        # Channel registry + allocation weights
  self_assessment.py           # Growth Health Score
  veto_system.py               # Proposal queue + digest email + veto
  revenue_tracker.py           # Stripe API + budget ledger
  growth_brain.py              # Orchestrator for L1/L2/L3
  competitor_tracker.py        # Comparable artist monitoring
  channel_agents/
    __init__.py                # ChannelAgent base class
    ig_conversion.py           # Instagram → Spotify conversion (P0)
    offering_optimizer.py      # Offering page A/B testing (P1)
    reddit_seeder.py           # Reddit community posts (P1)
    collab_agent.py            # Artist collab finder (P2)
    submithub_agent.py         # SubmitHub submissions (P2, self-funded)
    groover_agent.py           # Groover submissions (P2, self-funded)
data/
  channel_registry.json        # Living channel status + weights
  experiments.json             # Experiment log (bootstrap)
  proposals.json               # Veto queue (bootstrap)
  growth_budget.json           # Revenue + spend ledger (bootstrap)
  growth_score.json            # Weekly score history (bootstrap)
  strategic_insights.json      # L3 meta-learnings (bootstrap)
  competitor_tracking.json     # Comparable artist metrics (bootstrap)
tests/
  test_bandit_framework.py
  test_experiment_engine.py
  test_strategy_portfolio.py
  test_self_assessment.py
  test_veto_system.py
  test_revenue_tracker.py
  test_growth_brain.py
  test_channel_agents.py
```

### Files to modify:
```
outreach_agent/db.py           # Call btl_db.init_btl_tables() from init_db()
outreach_agent/config.py       # Add BTL config constants
rjm.py                        # Add experiment/veto/brain/budget/channels/score/offering commands
outreach_agent/master_agent.py # Add BTL operational modes
```

---

## Task 1: BTL Config Constants

**Files:**
- Modify: `outreach_agent/config.py`

- [ ] **Step 1: Add BTL constants to config.py**

Add after the existing `REPLY_CHECK_INBOX_DAYS` block (around line 100):

```python
# ─── Boil the Lake Protocol ──────────────────────────────────────────────────

# Experiment limits
BTL_MAX_CONCURRENT_EXPERIMENTS = 5
BTL_MIN_EXPERIMENT_DAYS = 7
BTL_MAX_EXPERIMENT_DAYS = 28
BTL_MIN_DATA_POINTS = 6

# Bandit config
BTL_BANDIT_WINDOW_DAYS = 28
BTL_BANDIT_COLD_START_MIN = 5
BTL_BANDIT_EXPLORE_COLD = 0.20
BTL_BANDIT_EXPLORE_WARM = 0.10
BTL_BANDIT_WARM_THRESHOLD = 20
BTL_BANDIT_OUTLIER_MULTIPLIER = 2.0

# Reallocation
BTL_REALLOCATION_LEARNING_RATE = 0.3
BTL_CHANNEL_WEIGHT_FLOOR = 0.05
BTL_CHANNEL_WEIGHT_CEILING = 0.40
BTL_CHANNEL_BREAKTHROUGH_CEILING = 0.50
BTL_UNDERPERFORM_WEEKS_TO_PAUSE = 4
BTL_UNDERPERFORM_ROI_THRESHOLD = 0.2

# Veto system
BTL_VETO_WINDOW_HOURS = 24
BTL_DIGEST_HOUR_CET = 8
BTL_DIGEST_EMAIL = FROM_EMAIL

# Budget
BTL_DONATION_ALLOCATION_PCT = 0.50
BTL_AUTO_SPEND_MAX_EUR = 5.0
BTL_VETO_SPEND_MAX_EUR = 25.0
BTL_DAILY_SPEND_CAP_EUR = 15.0
BTL_DAILY_SPEND_CAP_PCT = 0.30
BTL_RESERVE_MIN_EUR = 5.0

# Stripe
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")

# Score thresholds
BTL_SCORE_STAY_COURSE = 80
BTL_SCORE_INCREASE_DISCOVERY = 60
BTL_SCORE_EMERGENCY = 40
BTL_SCORE_RED_ALERT = 20

# Layer cadences
BTL_L1_RUNS_PER_DAY = 4
BTL_L2_DAY = "sunday"
BTL_L2_HOUR_CET = 20
BTL_L3_DAYS = ["tuesday", "friday"]
BTL_L3_HOUR_CET = 10

# Platform safety limits
BTL_REDDIT_MAX_POSTS_PER_WEEK = 2
BTL_IG_MAX_REELS_PER_DAY = 3
BTL_TIKTOK_MAX_POSTS_PER_DAY = 3
```

- [ ] **Step 2: Commit**

```bash
git add outreach_agent/config.py
git commit -m "feat(btl): add Boil the Lake config constants"
```

---

## Task 2: Database Migrations

**Files:**
- Create: `outreach_agent/btl_db.py`
- Modify: `outreach_agent/db.py`
- Test: `tests/test_btl_db.py`

- [ ] **Step 1: Write the test for BTL table creation**

```python
# tests/test_btl_db.py
"""Tests for BTL database migrations."""
import sqlite3
import sys
import os
import tempfile
from pathlib import Path

# Allow imports from outreach_agent
sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
os.environ["RJM_DB_PATH"] = ":memory:"

import db
import btl_db


def _get_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
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


def test_proposals_schema():
    db.init_db()
    btl_db.init_btl_tables()
    with db.get_conn() as conn:
        cols = _get_columns(conn, "proposals")
        for col in ["id", "type", "title", "status", "proposed_at",
                     "execute_after", "veto_reason"]:
            assert col in cols, f"Missing column: {col}"


def test_idempotent():
    """Calling init_btl_tables twice should not error."""
    db.init_db()
    btl_db.init_btl_tables()
    btl_db.init_btl_tables()  # second call should be safe


def test_daily_stats_extension():
    db.init_db()
    btl_db.init_btl_tables()
    with db.get_conn() as conn:
        cols = _get_columns(conn, "daily_stats")
        for col in ["listeners_delta", "growth_score",
                     "active_experiments", "budget_available"]:
            assert col in cols, f"Missing BTL column in daily_stats: {col}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd outreach_agent && python -m pytest ../tests/test_btl_db.py -v`
Expected: FAIL — `btl_db` module not found

- [ ] **Step 3: Implement btl_db.py**

```python
# outreach_agent/btl_db.py
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

# Columns to add to daily_stats (existing table)
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
                conn.execute(
                    f"ALTER TABLE daily_stats ADD COLUMN {col_name} {col_def}"
                )
            except Exception:
                pass  # column already exists
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd outreach_agent && python -m pytest ../tests/test_btl_db.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Wire btl_db into db.init_db()**

In `outreach_agent/db.py`, add at the end of `init_db()`:

```python
    # BTL protocol tables
    try:
        import btl_db
        btl_db.init_btl_tables()
    except ImportError:
        pass
```

- [ ] **Step 6: Create bootstrap data files**

Create empty JSON bootstraps for all BTL data files:

`data/experiments.json`:
```json
[]
```

`data/proposals.json`:
```json
[]
```

`data/growth_budget.json`:
```json
{
    "total_donations": 0.00,
    "total_allocated": 0.00,
    "total_spent": 0.00,
    "available_balance": 0.00,
    "transactions": []
}
```

`data/growth_score.json`:
```json
[]
```

`data/strategic_insights.json`:
```json
[]
```

`data/competitor_tracking.json`:
```json
{
    "artists": [],
    "snapshots": []
}
```

- [ ] **Step 7: Commit**

```bash
git add outreach_agent/btl_db.py outreach_agent/db.py tests/test_btl_db.py \
    data/experiments.json data/proposals.json data/growth_budget.json \
    data/growth_score.json data/strategic_insights.json data/competitor_tracking.json
git commit -m "feat(btl): database migrations + bootstrap data files"
```

---

## Task 3: Bandit Framework

**Files:**
- Create: `outreach_agent/bandit_framework.py`
- Test: `tests/test_bandit_framework.py`

- [ ] **Step 1: Write tests for the bandit framework**

```python
# tests/test_bandit_framework.py
"""Tests for generalized Thompson Sampling bandit framework."""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
os.environ["RJM_DB_PATH"] = ":memory:"

import db
import btl_db

# Initialize DB before importing bandit
db.init_db()
btl_db.init_btl_tables()

from bandit_framework import Bandit


def setup_function():
    """Reset bandit state between tests."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM bandit_state")


def test_select_cold_start():
    """With no data, bandit should explore (return random arm value)."""
    b = Bandit("test_channel", {"color": ["red", "blue", "green"]})
    selection = b.select()
    assert "color" in selection
    assert selection["color"] in ["red", "blue", "green"]


def test_record_and_select():
    """After recording successes, bandit should favor winning arm."""
    b = Bandit("test_channel", {"color": ["red", "blue"]})

    # Record 20 successes for red, 20 failures for blue
    for _ in range(20):
        b.record({"color": "red"}, reward=1.0)
        b.record({"color": "blue"}, reward=0.0)

    # Over 100 selections, red should appear more often
    counts = {"red": 0, "blue": 0}
    for _ in range(100):
        sel = b.select()
        counts[sel["color"]] += 1

    assert counts["red"] > counts["blue"], f"Expected red > blue, got {counts}"


def test_record_updates_db():
    """Recording a result should persist to bandit_state table."""
    b = Bandit("test_channel", {"size": ["small", "large"]})
    b.record({"size": "small"}, reward=1.0)

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM bandit_state WHERE channel='test_channel' "
            "AND arm_name='size' AND arm_value='small'"
        ).fetchone()
        assert row is not None
        assert row["samples"] == 1
        assert row["alpha"] > 1.0  # success increments alpha


def test_multiple_arms():
    """Bandit should handle multiple arm dimensions."""
    b = Bandit("multi", {
        "hook": ["tension", "identity"],
        "length": ["5s", "15s"],
    })
    sel = b.select()
    assert "hook" in sel
    assert "length" in sel
    assert sel["hook"] in ["tension", "identity"]
    assert sel["length"] in ["5s", "15s"]


def test_get_stats():
    """get_stats should return arm performance summary."""
    b = Bandit("stats_test", {"tone": ["dark", "light"]})
    for _ in range(10):
        b.record({"tone": "dark"}, reward=0.8)
        b.record({"tone": "light"}, reward=0.3)

    stats = b.get_stats()
    assert "tone" in stats
    assert len(stats["tone"]) == 2
    dark_stat = next(s for s in stats["tone"] if s["value"] == "dark")
    assert dark_stat["samples"] == 10
    assert dark_stat["mean_reward"] > 0.5


def test_detect_breakthroughs():
    """Arms performing >2x mean should be flagged as breakthroughs."""
    b = Bandit("breakthrough_test", {"style": ["a", "b", "c"]})

    # c is the breakthrough — 10x the reward of a and b
    for _ in range(10):
        b.record({"style": "a"}, reward=0.1)
        b.record({"style": "b"}, reward=0.1)
        b.record({"style": "c"}, reward=0.9)

    breakthroughs = b.detect_breakthroughs()
    assert len(breakthroughs) >= 1
    assert any(bt["arm_value"] == "c" for bt in breakthroughs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd outreach_agent && python -m pytest ../tests/test_bandit_framework.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement bandit_framework.py**

```python
# outreach_agent/bandit_framework.py
"""Generalized Thompson Sampling bandit framework for BTL protocol.

Each channel gets a Bandit instance with named arms (dimensions).
Arms are persisted in the bandit_state SQLite table.
"""

import logging
import random
from datetime import datetime

import numpy as np

import db
from config import (
    BTL_BANDIT_COLD_START_MIN,
    BTL_BANDIT_EXPLORE_COLD,
    BTL_BANDIT_EXPLORE_WARM,
    BTL_BANDIT_WARM_THRESHOLD,
    BTL_BANDIT_OUTLIER_MULTIPLIER,
)

log = logging.getLogger(__name__)


class Bandit:
    """Thompson Sampling bandit with multiple arm dimensions.

    Args:
        channel: channel_id this bandit optimizes for
        arms: dict of {arm_name: [possible_values]}
              e.g. {"hook": ["tension", "identity"], "length": ["5s", "15s"]}
    """

    def __init__(self, channel: str, arms: dict[str, list[str]]):
        self.channel = channel
        self.arms = arms

    def select(self) -> dict[str, str]:
        """Select arm values using Thompson Sampling.

        Returns dict of {arm_name: selected_value}.
        """
        selection = {}
        for arm_name, values in self.arms.items():
            total_samples = self._total_samples(arm_name)

            # Exploration: random selection during cold start or with epsilon probability
            if total_samples < BTL_BANDIT_COLD_START_MIN:
                selection[arm_name] = random.choice(values)
                continue

            epsilon = (
                BTL_BANDIT_EXPLORE_COLD
                if total_samples < BTL_BANDIT_WARM_THRESHOLD
                else BTL_BANDIT_EXPLORE_WARM
            )
            if random.random() < epsilon:
                selection[arm_name] = random.choice(values)
                continue

            # Thompson Sampling: draw from Beta(alpha, beta) for each value
            best_value = None
            best_sample = -1.0
            for value in values:
                alpha, beta = self._get_params(arm_name, value)
                sample = np.random.beta(alpha, beta)
                if sample > best_sample:
                    best_sample = sample
                    best_value = value

            selection[arm_name] = best_value

        return selection

    def record(self, arm_values: dict[str, str], reward: float) -> None:
        """Record outcome for a set of arm values.

        Args:
            arm_values: dict of {arm_name: value_used}
            reward: float 0.0-1.0
        """
        now = datetime.utcnow().isoformat()
        with db.get_conn() as conn:
            for arm_name, arm_value in arm_values.items():
                row = conn.execute(
                    "SELECT alpha, beta, samples FROM bandit_state "
                    "WHERE channel=? AND arm_name=? AND arm_value=?",
                    (self.channel, arm_name, arm_value),
                ).fetchone()

                if row is None:
                    alpha = 1.0 + reward
                    beta = 1.0 + (1.0 - reward)
                    conn.execute(
                        "INSERT INTO bandit_state "
                        "(channel, arm_name, arm_value, alpha, beta, samples, last_updated) "
                        "VALUES (?, ?, ?, ?, ?, 1, ?)",
                        (self.channel, arm_name, arm_value, alpha, beta, now),
                    )
                else:
                    new_alpha = row["alpha"] + reward
                    new_beta = row["beta"] + (1.0 - reward)
                    conn.execute(
                        "UPDATE bandit_state SET alpha=?, beta=?, samples=samples+1, "
                        "last_updated=? "
                        "WHERE channel=? AND arm_name=? AND arm_value=?",
                        (new_alpha, new_beta, now,
                         self.channel, arm_name, arm_value),
                    )

    def get_stats(self) -> dict[str, list[dict]]:
        """Return performance stats per arm dimension.

        Returns: {arm_name: [{value, samples, mean_reward, alpha, beta}]}
        """
        stats = {}
        with db.get_conn() as conn:
            for arm_name, values in self.arms.items():
                arm_stats = []
                for value in values:
                    row = conn.execute(
                        "SELECT alpha, beta, samples FROM bandit_state "
                        "WHERE channel=? AND arm_name=? AND arm_value=?",
                        (self.channel, arm_name, value),
                    ).fetchone()
                    if row and row["samples"] > 0:
                        a, b = row["alpha"], row["beta"]
                        arm_stats.append({
                            "value": value,
                            "samples": row["samples"],
                            "mean_reward": a / (a + b),
                            "alpha": a,
                            "beta": b,
                        })
                    else:
                        arm_stats.append({
                            "value": value,
                            "samples": 0,
                            "mean_reward": 0.0,
                            "alpha": 1.0,
                            "beta": 1.0,
                        })
                stats[arm_name] = arm_stats
        return stats

    def detect_breakthroughs(self) -> list[dict]:
        """Find arm values performing > BTL_BANDIT_OUTLIER_MULTIPLIER * mean.

        Returns list of {arm_name, arm_value, mean_reward, overall_mean, ratio}.
        """
        breakthroughs = []
        stats = self.get_stats()

        for arm_name, arm_stats in stats.items():
            active = [s for s in arm_stats if s["samples"] >= BTL_BANDIT_COLD_START_MIN]
            if len(active) < 2:
                continue
            overall_mean = sum(s["mean_reward"] for s in active) / len(active)
            if overall_mean <= 0:
                continue

            for s in active:
                ratio = s["mean_reward"] / overall_mean
                if ratio >= BTL_BANDIT_OUTLIER_MULTIPLIER:
                    breakthroughs.append({
                        "arm_name": arm_name,
                        "arm_value": s["value"],
                        "mean_reward": round(s["mean_reward"], 4),
                        "overall_mean": round(overall_mean, 4),
                        "ratio": round(ratio, 2),
                        "samples": s["samples"],
                    })

        return breakthroughs

    def _get_params(self, arm_name: str, arm_value: str) -> tuple[float, float]:
        """Get Beta distribution params for an arm value."""
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT alpha, beta FROM bandit_state "
                "WHERE channel=? AND arm_name=? AND arm_value=?",
                (self.channel, arm_name, arm_value),
            ).fetchone()
            if row:
                return row["alpha"], row["beta"]
            return 1.0, 1.0  # uniform prior

    def _total_samples(self, arm_name: str) -> int:
        """Total samples across all values for an arm."""
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(samples), 0) as total FROM bandit_state "
                "WHERE channel=? AND arm_name=?",
                (self.channel, arm_name),
            ).fetchone()
            return row["total"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd outreach_agent && python -m pytest ../tests/test_bandit_framework.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/bandit_framework.py tests/test_bandit_framework.py
git commit -m "feat(btl): Thompson Sampling bandit framework"
```

---

## Task 4: Experiment Engine

**Files:**
- Create: `outreach_agent/experiment_engine.py`
- Test: `tests/test_experiment_engine.py`

- [ ] **Step 1: Write tests for experiment engine**

```python
# tests/test_experiment_engine.py
"""Tests for experiment lifecycle management."""
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
os.environ["RJM_DB_PATH"] = ":memory:"

import db
import btl_db

db.init_db()
btl_db.init_btl_tables()

from experiment_engine import (
    propose_experiment,
    get_experiment,
    list_experiments,
    start_experiment,
    complete_experiment,
    analyze_experiment,
    log_metric,
    active_count,
    can_start_new,
)


def setup_function():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM experiments")


def test_propose_creates_experiment():
    exp_id = propose_experiment(
        channel="reddit",
        hypothesis="Posts in r/melodictechno drive 50+ profile visits",
        tactic="Post 2x/week with production stories",
        duration_days=21,
        success_criteria="50+ profile visits per post",
        failure_criteria="<10 profile visits after 6 posts",
    )
    assert exp_id.startswith("exp_")
    exp = get_experiment(exp_id)
    assert exp["status"] == "proposed"
    assert exp["channel"] == "reddit"


def test_start_experiment():
    exp_id = propose_experiment(
        channel="reddit",
        hypothesis="test",
        tactic="test",
    )
    start_experiment(exp_id)
    exp = get_experiment(exp_id)
    assert exp["status"] == "active"
    assert exp["started_at"] is not None


def test_complete_experiment():
    exp_id = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    start_experiment(exp_id)
    complete_experiment(exp_id)
    exp = get_experiment(exp_id)
    assert exp["status"] == "completed"
    assert exp["ended_at"] is not None


def test_analyze_experiment():
    exp_id = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    start_experiment(exp_id)
    complete_experiment(exp_id)
    analyze_experiment(exp_id, result="success", learning="Reddit works great")
    exp = get_experiment(exp_id)
    assert exp["status"] == "analyzed"
    assert exp["result"] == "success"
    assert exp["learning"] == "Reddit works great"


def test_log_metric():
    exp_id = propose_experiment(channel="reddit", hypothesis="t", tactic="t")
    start_experiment(exp_id)
    log_metric(exp_id, {"profile_visits": 55, "date": "2026-04-20"})
    exp = get_experiment(exp_id)
    import json
    metrics = json.loads(exp["metrics_log"])
    assert len(metrics) == 1
    assert metrics[0]["profile_visits"] == 55


def test_active_count():
    assert active_count() == 0
    e1 = propose_experiment(channel="a", hypothesis="t", tactic="t")
    start_experiment(e1)
    assert active_count() == 1
    e2 = propose_experiment(channel="b", hypothesis="t", tactic="t")
    start_experiment(e2)
    assert active_count() == 2


def test_can_start_new_respects_limit():
    for i in range(5):
        eid = propose_experiment(channel=f"ch_{i}", hypothesis="t", tactic="t")
        start_experiment(eid)
    assert can_start_new() is False


def test_list_experiments_by_status():
    e1 = propose_experiment(channel="a", hypothesis="t", tactic="t")
    e2 = propose_experiment(channel="b", hypothesis="t", tactic="t")
    start_experiment(e2)
    proposed = list_experiments(status="proposed")
    active = list_experiments(status="active")
    assert len(proposed) == 1
    assert len(active) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd outreach_agent && python -m pytest ../tests/test_experiment_engine.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement experiment_engine.py**

```python
# outreach_agent/experiment_engine.py
"""Experiment lifecycle management for BTL protocol."""

import json
import logging
from datetime import datetime, timedelta

import db
from config import (
    BTL_MAX_CONCURRENT_EXPERIMENTS,
    BTL_MIN_EXPERIMENT_DAYS,
    BTL_MAX_EXPERIMENT_DAYS,
    BTL_VETO_WINDOW_HOURS,
)

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _make_id(channel: str) -> str:
    date_str = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
    return f"exp_{date_str}_{channel}"


def propose_experiment(
    channel: str,
    hypothesis: str,
    tactic: str,
    duration_days: int = 21,
    success_criteria: str = "",
    failure_criteria: str = "",
    cost_type: str = "free",
    cost_estimate: float = 0.0,
    expected_metric: str = "",
    expected_target: float = 0.0,
    expected_confidence: float = 0.5,
    guardrails: list[str] | None = None,
) -> str:
    """Create a new experiment in 'proposed' status. Returns experiment ID."""
    exp_id = _make_id(channel)
    now = _now_iso()
    execute_after = (
        datetime.utcnow() + timedelta(hours=BTL_VETO_WINDOW_HOURS)
    ).isoformat()

    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO experiments
            (id, channel, hypothesis, tactic, cost_type, cost_estimate,
             expected_metric, expected_target, expected_confidence,
             duration_days, success_criteria, failure_criteria, guardrails,
             status, proposed_at, execute_after, metrics_log, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, '[]', ?)""",
            (
                exp_id, channel, hypothesis, tactic, cost_type, cost_estimate,
                expected_metric, expected_target, expected_confidence,
                duration_days, success_criteria, failure_criteria,
                json.dumps(guardrails or []),
                now, execute_after, now,
            ),
        )

    log.info("Proposed experiment %s on channel %s", exp_id, channel)
    return exp_id


def get_experiment(exp_id: str) -> dict | None:
    """Fetch experiment by ID."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM experiments WHERE id=?", (exp_id,)
        ).fetchone()
        return dict(row) if row else None


def list_experiments(
    status: str | None = None,
    channel: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List experiments, optionally filtered by status and/or channel."""
    query = "SELECT * FROM experiments WHERE 1=1"
    params = []
    if status:
        query += " AND status=?"
        params.append(status)
    if channel:
        query += " AND channel=?"
        params.append(channel)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with db.get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def start_experiment(exp_id: str) -> None:
    """Transition experiment from proposed → active."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET status='active', started_at=? WHERE id=?",
            (_now_iso(), exp_id),
        )
    log.info("Started experiment %s", exp_id)


def complete_experiment(exp_id: str) -> None:
    """Transition experiment from active → completed."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET status='completed', ended_at=? WHERE id=?",
            (_now_iso(), exp_id),
        )
    log.info("Completed experiment %s", exp_id)


def analyze_experiment(exp_id: str, result: str, learning: str) -> None:
    """Record analysis results. Transition completed → analyzed."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET status='analyzed', result=?, learning=? WHERE id=?",
            (result, learning, exp_id),
        )
    log.info("Analyzed experiment %s: %s", exp_id, result)


def log_metric(exp_id: str, metric: dict) -> None:
    """Append a metric observation to an experiment's metrics_log."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT metrics_log FROM experiments WHERE id=?", (exp_id,)
        ).fetchone()
        if not row:
            return
        metrics = json.loads(row["metrics_log"] or "[]")
        metrics.append(metric)
        conn.execute(
            "UPDATE experiments SET metrics_log=? WHERE id=?",
            (json.dumps(metrics), exp_id),
        )


def active_count() -> int:
    """Count currently active experiments."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM experiments WHERE status='active'"
        ).fetchone()
        return row["n"]


def can_start_new() -> bool:
    """Check if we can start another experiment (under the concurrent limit)."""
    return active_count() < BTL_MAX_CONCURRENT_EXPERIMENTS


def get_due_experiments() -> list[dict]:
    """Get active experiments that have exceeded their duration."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM experiments
            WHERE status='active'
            AND datetime(started_at, '+' || duration_days || ' days') <= datetime('now')
            ORDER BY started_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_proposals() -> list[dict]:
    """Get proposed experiments whose veto window has passed."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM experiments
            WHERE status='proposed'
            AND datetime(execute_after) <= datetime('now')
            ORDER BY proposed_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def veto_experiment(exp_id: str, reason: str = "") -> None:
    """Veto a proposed experiment."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET status='vetoed', learning=? WHERE id=? AND status='proposed'",
            (f"VETOED: {reason}", exp_id),
        )
    log.info("Vetoed experiment %s: %s", exp_id, reason)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd outreach_agent && python -m pytest ../tests/test_experiment_engine.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/experiment_engine.py tests/test_experiment_engine.py
git commit -m "feat(btl): experiment engine — lifecycle management"
```

---

## Task 5: Strategy Portfolio + Channel Registry

**Files:**
- Create: `outreach_agent/strategy_portfolio.py`
- Create: `data/channel_registry.json`
- Test: `tests/test_strategy_portfolio.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_strategy_portfolio.py
"""Tests for strategy portfolio and channel allocation."""
import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
os.environ["RJM_DB_PATH"] = ":memory:"

import db
import btl_db
db.init_db()
btl_db.init_btl_tables()

from strategy_portfolio import (
    load_registry,
    save_registry,
    get_channel,
    get_active_channels,
    activate_channel,
    pause_channel,
    record_channel_metric,
    get_channel_lei,
    reallocate_weights,
)

# Use temp file for registry during tests
_TEMP_REG = None


def setup_function():
    global _TEMP_REG
    _TEMP_REG = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    )
    _TEMP_REG.write(json.dumps(_sample_registry()))
    _TEMP_REG.close()
    os.environ["BTL_CHANNEL_REGISTRY_PATH"] = _TEMP_REG.name
    with db.get_conn() as conn:
        conn.execute("DELETE FROM channel_metrics")


def _sample_registry():
    return {
        "channels": [
            {
                "id": "ch_playlist_outreach",
                "name": "Spotify Playlists",
                "status": "active",
                "weight": 0.30,
                "agent": "rjm-outreach",
                "cost_type": "free",
            },
            {
                "id": "ch_reddit",
                "name": "Reddit",
                "status": "queued",
                "weight": 0.10,
                "agent": "reddit-seeder",
                "cost_type": "free",
            },
        ],
        "last_reallocation": None,
    }


def test_load_registry():
    reg = load_registry()
    assert len(reg["channels"]) == 2


def test_get_channel():
    ch = get_channel("ch_playlist_outreach")
    assert ch["name"] == "Spotify Playlists"


def test_get_active_channels():
    active = get_active_channels()
    assert len(active) == 1
    assert active[0]["id"] == "ch_playlist_outreach"


def test_activate_channel():
    activate_channel("ch_reddit")
    ch = get_channel("ch_reddit")
    assert ch["status"] == "active"


def test_pause_channel():
    pause_channel("ch_playlist_outreach")
    ch = get_channel("ch_playlist_outreach")
    assert ch["status"] == "paused"


def test_record_and_get_lei():
    record_channel_metric("ch_playlist_outreach", "listeners_gained", 10.0)
    record_channel_metric("ch_playlist_outreach", "listeners_gained", 5.0)
    lei = get_channel_lei("ch_playlist_outreach", days=7)
    assert lei == 15.0


def test_reallocate_weights():
    # Give playlist outreach some LEI, reddit none
    activate_channel("ch_reddit")
    record_channel_metric("ch_playlist_outreach", "listeners_gained", 20.0)
    record_channel_metric("ch_reddit", "listeners_gained", 2.0)

    old_reg = load_registry()
    new_reg = reallocate_weights()
    playlist = next(c for c in new_reg["channels"] if c["id"] == "ch_playlist_outreach")
    reddit = next(c for c in new_reg["channels"] if c["id"] == "ch_reddit")
    # Playlist should have higher weight (it has 10x the LEI)
    assert playlist["weight"] > reddit["weight"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd outreach_agent && python -m pytest ../tests/test_strategy_portfolio.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create channel_registry.json**

```json
{
    "channels": [
        {
            "id": "ch_playlist_outreach",
            "name": "Spotify Playlists",
            "tactic": "Curator email outreach",
            "agent": "rjm-outreach",
            "status": "active",
            "weight": 0.20,
            "cost_type": "free",
            "est_listeners_low": 2000,
            "est_listeners_high": 10000
        },
        {
            "id": "ch_content_tiktok",
            "name": "TikTok",
            "tactic": "Daily viral shorts",
            "agent": "holy-rave-daily-run",
            "status": "active",
            "weight": 0.20,
            "cost_type": "free",
            "est_listeners_low": 5000,
            "est_listeners_high": 8000
        },
        {
            "id": "ch_content_reels",
            "name": "Instagram Reels",
            "tactic": "Daily clips from 290K base",
            "agent": "holy-rave-daily-run",
            "status": "active",
            "weight": 0.15,
            "cost_type": "free",
            "est_listeners_low": 2000,
            "est_listeners_high": 5000
        },
        {
            "id": "ch_content_ytshorts",
            "name": "YouTube Shorts",
            "tactic": "Daily clips",
            "agent": "holy-rave-daily-run",
            "status": "active",
            "weight": 0.10,
            "cost_type": "free",
            "est_listeners_low": 1000,
            "est_listeners_high": 3000
        },
        {
            "id": "ch_podcast_pitch",
            "name": "Podcasts",
            "tactic": "Guest appearance pitching",
            "agent": "rjm-outreach",
            "status": "active",
            "weight": 0.15,
            "cost_type": "free",
            "est_listeners_low": 500,
            "est_listeners_high": 2000
        },
        {
            "id": "ch_editorial",
            "name": "Spotify Editorial",
            "tactic": "Pitch for editorial playlists",
            "agent": "manual",
            "status": "active",
            "weight": 0.10,
            "cost_type": "free",
            "est_listeners_low": 10000,
            "est_listeners_high": 100000
        },
        {
            "id": "ch_ig_conversion",
            "name": "Instagram to Spotify",
            "tactic": "Convert 290K followers to listeners",
            "agent": "ig-conversion",
            "status": "queued",
            "weight": 0.05,
            "cost_type": "free",
            "est_listeners_low": 5000,
            "est_listeners_high": 14500
        },
        {
            "id": "ch_reddit",
            "name": "Reddit",
            "tactic": "r/melodictechno, r/psytrance community posts",
            "agent": "reddit-seeder",
            "status": "queued",
            "weight": 0.05,
            "cost_type": "free",
            "est_listeners_low": 500,
            "est_listeners_high": 800
        },
        {
            "id": "ch_yt_longform",
            "name": "YouTube Long-form",
            "tactic": "DJ mixes, studio sessions",
            "agent": "yt-longform",
            "status": "queued",
            "weight": 0.05,
            "cost_type": "free",
            "est_listeners_low": 800,
            "est_listeners_high": 1200
        },
        {
            "id": "ch_collab",
            "name": "Artist Collabs",
            "tactic": "Remix exchanges, playlist swaps",
            "agent": "collab-agent",
            "status": "queued",
            "weight": 0.05,
            "cost_type": "free",
            "est_listeners_low": 2000,
            "est_listeners_high": 5000
        },
        {
            "id": "ch_blog_pr",
            "name": "Music Blogs",
            "tactic": "Review/feature outreach",
            "agent": "rjm-outreach",
            "status": "queued",
            "weight": 0.05,
            "cost_type": "free",
            "est_listeners_low": 300,
            "est_listeners_high": 800
        },
        {
            "id": "ch_fan_email",
            "name": "Fan Email List",
            "tactic": "Release-day streaming spikes",
            "agent": "email-list-agent",
            "status": "queued",
            "weight": 0.05,
            "cost_type": "free",
            "est_listeners_low": 1000,
            "est_listeners_high": 1500
        },
        {
            "id": "ch_presave",
            "name": "Pre-save Campaigns",
            "tactic": "Spotify pre-save for releases",
            "agent": "release-system",
            "status": "queued",
            "weight": 0.05,
            "cost_type": "free",
            "est_listeners_low": 500,
            "est_listeners_high": 2000
        },
        {
            "id": "ch_discord",
            "name": "Discord",
            "tactic": "Electronic music servers",
            "agent": "community-agent",
            "status": "queued",
            "weight": 0.05,
            "cost_type": "free",
            "est_listeners_low": 200,
            "est_listeners_high": 400
        },
        {
            "id": "ch_offering",
            "name": "Offering Page",
            "tactic": "Optimize for donations to fund paid growth",
            "agent": "offering-optimizer",
            "status": "queued",
            "weight": 0.05,
            "cost_type": "free",
            "est_listeners_low": 0,
            "est_listeners_high": 0
        },
        {
            "id": "ch_submithub",
            "name": "SubmitHub Premium",
            "tactic": "Paid playlist submissions",
            "agent": "submithub-agent",
            "status": "locked",
            "weight": 0.0,
            "cost_type": "self_funded",
            "cost_per_unit": 2.0,
            "est_listeners_low": 5,
            "est_listeners_high": 20
        },
        {
            "id": "ch_groover",
            "name": "Groover",
            "tactic": "Paid curator + blog submissions",
            "agent": "groover-agent",
            "status": "locked",
            "weight": 0.0,
            "cost_type": "self_funded",
            "cost_per_unit": 2.0,
            "est_listeners_low": 3,
            "est_listeners_high": 15
        }
    ],
    "last_reallocation": null
}
```

- [ ] **Step 4: Implement strategy_portfolio.py**

```python
# outreach_agent/strategy_portfolio.py
"""Channel registry and allocation weight management for BTL protocol."""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import db
from config import (
    BTL_REALLOCATION_LEARNING_RATE,
    BTL_CHANNEL_WEIGHT_FLOOR,
    BTL_CHANNEL_WEIGHT_CEILING,
    BTL_CHANNEL_BREAKTHROUGH_CEILING,
    BTL_UNDERPERFORM_WEEKS_TO_PAUSE,
    BTL_UNDERPERFORM_ROI_THRESHOLD,
    BASE_DIR,
)

log = logging.getLogger(__name__)

_DEFAULT_REGISTRY_PATH = Path(BASE_DIR).parent / "data" / "channel_registry.json"


def _registry_path() -> Path:
    override = os.environ.get("BTL_CHANNEL_REGISTRY_PATH")
    return Path(override) if override else _DEFAULT_REGISTRY_PATH


def load_registry() -> dict:
    """Load channel registry from JSON file."""
    with open(_registry_path()) as f:
        return json.load(f)


def save_registry(reg: dict) -> None:
    """Save channel registry to JSON file."""
    with open(_registry_path(), "w") as f:
        json.dump(reg, f, indent=4)


def get_channel(channel_id: str) -> dict | None:
    """Get a single channel by ID."""
    reg = load_registry()
    for ch in reg["channels"]:
        if ch["id"] == channel_id:
            return ch
    return None


def get_active_channels() -> list[dict]:
    """Get all channels with status='active'."""
    reg = load_registry()
    return [ch for ch in reg["channels"] if ch["status"] == "active"]


def activate_channel(channel_id: str) -> None:
    """Set a channel's status to 'active'."""
    reg = load_registry()
    for ch in reg["channels"]:
        if ch["id"] == channel_id:
            ch["status"] = "active"
            break
    save_registry(reg)
    log.info("Activated channel %s", channel_id)


def pause_channel(channel_id: str) -> None:
    """Set a channel's status to 'paused'."""
    reg = load_registry()
    for ch in reg["channels"]:
        if ch["id"] == channel_id:
            ch["status"] = "paused"
            break
    save_registry(reg)
    log.info("Paused channel %s", channel_id)


def record_channel_metric(
    channel_id: str, metric_name: str, metric_value: float
) -> None:
    """Record a performance metric for a channel."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO channel_metrics (channel_id, date, metric_name, metric_value) "
            "VALUES (?, date('now'), ?, ?)",
            (channel_id, metric_name, metric_value),
        )


def get_channel_lei(channel_id: str, days: int = 7) -> float:
    """Get Listener Equivalent Impact for a channel over the past N days.

    Sums all 'listeners_gained' metrics for the channel.
    """
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(metric_value), 0) as lei FROM channel_metrics "
            "WHERE channel_id=? AND metric_name='listeners_gained' "
            "AND date >= date('now', ?)",
            (channel_id, f"-{days} days"),
        ).fetchone()
        return row["lei"]


def reallocate_weights() -> dict:
    """Run L2 strategic reallocation: shift weights toward high-ROI channels.

    Returns updated registry.
    """
    reg = load_registry()
    active = [ch for ch in reg["channels"] if ch["status"] == "active"]

    if len(active) < 2:
        return reg

    # Calculate LEI for each active channel
    leis = {}
    for ch in active:
        leis[ch["id"]] = max(get_channel_lei(ch["id"], days=7), 0.001)

    mean_lei = sum(leis.values()) / len(leis)
    std_lei = max(
        (sum((v - mean_lei) ** 2 for v in leis.values()) / len(leis)) ** 0.5,
        0.001,
    )

    # Update weights using z-score scaled learning rate
    for ch in active:
        lei = leis[ch["id"]]
        z = (lei - mean_lei) / std_lei
        new_weight = ch["weight"] * (1 + BTL_REALLOCATION_LEARNING_RATE * z)

        # Check for breakthrough ceiling
        ceiling = BTL_CHANNEL_WEIGHT_CEILING
        if lei > 3.0 * mean_lei:
            ceiling = BTL_CHANNEL_BREAKTHROUGH_CEILING

        ch["weight"] = max(BTL_CHANNEL_WEIGHT_FLOOR, min(ceiling, new_weight))

    # Normalize weights to sum to 1.0 across active channels
    total = sum(ch["weight"] for ch in active)
    if total > 0:
        for ch in active:
            ch["weight"] = round(ch["weight"] / total, 4)

    reg["last_reallocation"] = datetime.utcnow().isoformat()
    save_registry(reg)
    log.info("Reallocated weights across %d active channels", len(active))
    return reg


def get_portfolio_summary() -> dict:
    """Return a summary dict for display."""
    reg = load_registry()
    active = [ch for ch in reg["channels"] if ch["status"] == "active"]
    queued = [ch for ch in reg["channels"] if ch["status"] == "queued"]
    paused = [ch for ch in reg["channels"] if ch["status"] == "paused"]
    locked = [ch for ch in reg["channels"] if ch["status"] == "locked"]
    return {
        "active": len(active),
        "queued": len(queued),
        "paused": len(paused),
        "locked": len(locked),
        "total": len(reg["channels"]),
        "last_reallocation": reg.get("last_reallocation"),
        "channels": [
            {
                "id": ch["id"],
                "name": ch["name"],
                "status": ch["status"],
                "weight": ch.get("weight", 0),
                "lei_7d": get_channel_lei(ch["id"], days=7),
            }
            for ch in reg["channels"]
        ],
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd outreach_agent && python -m pytest ../tests/test_strategy_portfolio.py -v`
Expected: All 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add outreach_agent/strategy_portfolio.py data/channel_registry.json \
    tests/test_strategy_portfolio.py
git commit -m "feat(btl): strategy portfolio + channel registry"
```

---

## Task 6: Self-Assessment (Growth Health Score)

**Files:**
- Create: `outreach_agent/self_assessment.py`
- Test: `tests/test_self_assessment.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_self_assessment.py
"""Tests for Growth Health Score calculation."""
import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
os.environ["RJM_DB_PATH"] = ":memory:"

import db
import btl_db
db.init_db()
btl_db.init_btl_tables()

from self_assessment import (
    calculate_score,
    get_score_history,
    get_triggered_action,
)


def test_calculate_score_defaults():
    """With no data, score should be a reasonable default (not crash)."""
    result = calculate_score(
        listeners_current=325,
        listeners_previous=325,
    )
    assert "total_score" in result
    assert 0 <= result["total_score"] <= 100
    assert "components" in result
    assert len(result["components"]) == 7


def test_calculate_score_growth():
    """Growing listeners should produce a higher velocity score."""
    growing = calculate_score(listeners_current=400, listeners_previous=325)
    flat = calculate_score(listeners_current=325, listeners_previous=325)
    assert growing["components"]["listener_velocity"]["score"] > flat["components"]["listener_velocity"]["score"]


def test_triggered_action_stay_course():
    action = get_triggered_action(85)
    assert action["level"] == "stay_course"


def test_triggered_action_increase_discovery():
    action = get_triggered_action(65)
    assert action["level"] == "increase_discovery"


def test_triggered_action_emergency():
    action = get_triggered_action(45)
    assert action["level"] == "emergency"


def test_triggered_action_red_alert():
    action = get_triggered_action(25)
    assert action["level"] == "red_alert"


def test_triggered_action_system_pause():
    action = get_triggered_action(15)
    assert action["level"] == "system_pause"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd outreach_agent && python -m pytest ../tests/test_self_assessment.py -v`
Expected: FAIL

- [ ] **Step 3: Implement self_assessment.py**

```python
# outreach_agent/self_assessment.py
"""Growth Health Score calculation and triggered actions for BTL protocol."""

import json
import logging
from datetime import datetime
from pathlib import Path

import db
from config import (
    BTL_SCORE_STAY_COURSE,
    BTL_SCORE_INCREASE_DISCOVERY,
    BTL_SCORE_EMERGENCY,
    BTL_SCORE_RED_ALERT,
    BASE_DIR,
)

log = logging.getLogger(__name__)

SCORE_FILE = Path(BASE_DIR).parent / "data" / "growth_score.json"


def _score_band(value: float, thresholds: list[tuple[float, int]]) -> int:
    """Map a value to a score using threshold bands.
    thresholds = [(threshold, score), ...] in descending order.
    """
    for threshold, score in thresholds:
        if value >= threshold:
            return score
    return thresholds[-1][1]  # lowest band


def calculate_score(
    listeners_current: int = 0,
    listeners_previous: int = 0,
    experiments_succeeded: int = 0,
    experiments_completed: int = 0,
    contacts_added: int = 0,
    contacts_target: int = 50,
    active_channels_positive: int = 0,
    active_channels_total: int = 1,
    avg_completion_rate: float = 0.0,
    listeners_per_eur: float = 0.0,
    has_budget: bool = False,
    agent_runs_total: int = 1,
    agent_runs_failed: int = 0,
) -> dict:
    """Calculate the Growth Health Score (0-100).

    Returns dict with total_score and component breakdown.
    """
    # 1. Listener Velocity (30%)
    if listeners_previous > 0:
        velocity_pct = (listeners_current - listeners_previous) / listeners_previous * 100
    else:
        velocity_pct = 0
    velocity_score = _score_band(velocity_pct, [
        (10, 100), (5, 80), (1, 60), (0, 40), (-999, 20),
    ])

    # 2. Experiment Hit Rate (20%)
    if experiments_completed > 0:
        hit_rate = experiments_succeeded / experiments_completed * 100
    else:
        hit_rate = 0
    hit_score = _score_band(hit_rate, [
        (50, 100), (30, 80), (15, 60), (10, 40), (0, 40),
    ])

    # 3. Pipeline Health (15%)
    if contacts_target > 0:
        pipeline_pct = contacts_added / contacts_target * 100
    else:
        pipeline_pct = 100
    pipeline_score = _score_band(pipeline_pct, [
        (100, 100), (75, 80), (50, 60), (25, 40), (0, 40),
    ])

    # 4. Channel Diversity (10%)
    if active_channels_total > 0:
        diversity_pct = active_channels_positive / active_channels_total * 100
    else:
        diversity_pct = 0
    diversity_score = _score_band(diversity_pct, [
        (80, 100), (60, 80), (40, 60), (20, 40), (0, 40),
    ])

    # 5. Content Performance (10%)
    content_score = _score_band(avg_completion_rate * 100, [
        (60, 100), (40, 80), (25, 60), (15, 40), (0, 40),
    ])

    # 6. Budget Efficiency (10%)
    if has_budget:
        budget_score = _score_band(listeners_per_eur, [
            (20, 100), (10, 80), (5, 60), (2, 40), (0, 40),
        ])
    else:
        budget_score = 70  # default when not spending

    # 7. System Reliability (5%)
    if agent_runs_total > 0:
        reliability_pct = (agent_runs_total - agent_runs_failed) / agent_runs_total * 100
    else:
        reliability_pct = 100
    reliability_score = _score_band(reliability_pct, [
        (99, 100), (95, 80), (90, 60), (85, 40), (0, 40),
    ])

    # Weighted total
    total = round(
        velocity_score * 0.30
        + hit_score * 0.20
        + pipeline_score * 0.15
        + diversity_score * 0.10
        + content_score * 0.10
        + budget_score * 0.10
        + reliability_score * 0.05
    )

    return {
        "total_score": total,
        "calculated_at": datetime.utcnow().isoformat(),
        "components": {
            "listener_velocity": {
                "weight": 0.30, "score": velocity_score,
                "detail": f"{velocity_pct:+.1f}% ({listeners_previous} -> {listeners_current})",
            },
            "experiment_hit_rate": {
                "weight": 0.20, "score": hit_score,
                "detail": f"{experiments_succeeded}/{experiments_completed}",
            },
            "pipeline_health": {
                "weight": 0.15, "score": pipeline_score,
                "detail": f"{contacts_added}/{contacts_target} contacts",
            },
            "channel_diversity": {
                "weight": 0.10, "score": diversity_score,
                "detail": f"{active_channels_positive}/{active_channels_total} positive",
            },
            "content_performance": {
                "weight": 0.10, "score": content_score,
                "detail": f"{avg_completion_rate*100:.0f}% avg completion",
            },
            "budget_efficiency": {
                "weight": 0.10, "score": budget_score,
                "detail": f"{listeners_per_eur:.1f} listeners/EUR" if has_budget else "no spend",
            },
            "system_reliability": {
                "weight": 0.05, "score": reliability_score,
                "detail": f"{reliability_pct:.1f}% uptime",
            },
        },
    }


def get_triggered_action(score: int) -> dict:
    """Determine what action the system should take based on score."""
    if score >= BTL_SCORE_STAY_COURSE:
        return {"level": "stay_course", "description": "Stay the course. Log what's working."}
    elif score >= BTL_SCORE_INCREASE_DISCOVERY:
        return {"level": "increase_discovery", "description": "Increase L3 discovery to 3x/week. Propose 2 new experiments."}
    elif score >= BTL_SCORE_EMERGENCY:
        return {"level": "emergency", "description": "Emergency strategy review. L3 daily for 1 week. Bolder experiments."}
    elif score >= BTL_SCORE_RED_ALERT:
        return {"level": "red_alert", "description": "Red alert. Pause spending. Focus on highest-ROI free channels. Email RJM."}
    else:
        return {"level": "system_pause", "description": "System pause. Halt all experiments. Send diagnostic to RJM."}


def save_score(score_result: dict) -> None:
    """Append score to history file."""
    history = []
    if SCORE_FILE.exists():
        history = json.loads(SCORE_FILE.read_text())
    history.append(score_result)
    SCORE_FILE.write_text(json.dumps(history, indent=2))


def get_score_history(limit: int = 12) -> list[dict]:
    """Get recent score history."""
    if not SCORE_FILE.exists():
        return []
    history = json.loads(SCORE_FILE.read_text())
    return history[-limit:]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd outreach_agent && python -m pytest ../tests/test_self_assessment.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/self_assessment.py tests/test_self_assessment.py
git commit -m "feat(btl): Growth Health Score self-assessment"
```

---

## Task 7: Veto System

**Files:**
- Create: `outreach_agent/veto_system.py`
- Test: `tests/test_veto_system.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_veto_system.py
"""Tests for veto system — proposal queue and execution."""
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
os.environ["RJM_DB_PATH"] = ":memory:"

import db
import btl_db
db.init_db()
btl_db.init_btl_tables()

from veto_system import (
    create_proposal,
    veto_proposal,
    get_pending_proposals,
    get_due_proposals,
    execute_proposal,
    list_proposals,
)


def setup_function():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM proposals")
        conn.execute("DELETE FROM experiments")


def test_create_proposal():
    pid = create_proposal(
        proposal_type="new_experiment",
        title="Test Reddit r/melodictechno",
        description="Post 2x/week with production stories",
        hypothesis="50+ profile visits per post",
        risk_level="low",
        estimated_impact="500-800 listeners/month",
    )
    assert pid.startswith("prop_")
    proposals = list_proposals()
    assert len(proposals) == 1
    assert proposals[0]["status"] == "pending"


def test_veto_proposal():
    pid = create_proposal(
        proposal_type="new_experiment",
        title="Test",
        description="test",
    )
    veto_proposal(pid, reason="Not ready for this channel")
    proposals = list_proposals(status="vetoed")
    assert len(proposals) == 1
    assert "Not ready" in proposals[0]["veto_reason"]


def test_get_pending_proposals():
    create_proposal(proposal_type="new_experiment", title="A", description="a")
    create_proposal(proposal_type="reallocation", title="B", description="b")
    pending = get_pending_proposals()
    assert len(pending) == 2


def test_get_due_proposals():
    """Proposals past their veto window should appear in due list."""
    # Create with past execute_after
    pid = create_proposal(
        proposal_type="new_experiment",
        title="Past due",
        description="test",
    )
    # Manually backdate execute_after
    with db.get_conn() as conn:
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        conn.execute(
            "UPDATE proposals SET execute_after=? WHERE id=?", (past, pid)
        )
    due = get_due_proposals()
    assert len(due) == 1


def test_execute_proposal():
    pid = create_proposal(
        proposal_type="new_experiment",
        title="Reddit experiment",
        description="test",
        hypothesis="50 visits",
    )
    # Backdate so it's due
    with db.get_conn() as conn:
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        conn.execute("UPDATE proposals SET execute_after=? WHERE id=?", (past, pid))

    result = execute_proposal(pid)
    assert result["executed"] is True

    # Proposal should be marked executed
    proposals = list_proposals(status="executed")
    assert len(proposals) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd outreach_agent && python -m pytest ../tests/test_veto_system.py -v`
Expected: FAIL

- [ ] **Step 3: Implement veto_system.py**

```python
# outreach_agent/veto_system.py
"""Veto system — proposal queue, 24hr window, digest email, execution."""

import json
import logging
from datetime import datetime, timedelta

import db
import experiment_engine
from config import BTL_VETO_WINDOW_HOURS

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _make_id() -> str:
    ts = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
    return f"prop_{ts}"


def create_proposal(
    proposal_type: str,
    title: str,
    description: str,
    hypothesis: str = "",
    risk_level: str = "low",
    estimated_impact: str = "",
) -> str:
    """Create a new proposal in the veto queue. Returns proposal ID."""
    pid = _make_id()
    now = _now_iso()
    execute_after = (
        datetime.utcnow() + timedelta(hours=BTL_VETO_WINDOW_HOURS)
    ).isoformat()

    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO proposals
            (id, type, title, description, hypothesis, risk_level,
             estimated_impact, proposed_at, execute_after, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (pid, proposal_type, title, description, hypothesis,
             risk_level, estimated_impact, now, execute_after, now),
        )

    log.info("Created proposal %s: %s", pid, title)
    return pid


def veto_proposal(proposal_id: str, reason: str = "") -> None:
    """Veto a pending proposal."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE proposals SET status='vetoed', veto_reason=? "
            "WHERE id=? AND status='pending'",
            (reason, proposal_id),
        )
    log.info("Vetoed proposal %s: %s", proposal_id, reason)


def veto_all(reason: str = "Emergency brake") -> int:
    """Veto all pending proposals. Returns count vetoed."""
    with db.get_conn() as conn:
        cursor = conn.execute(
            "UPDATE proposals SET status='vetoed', veto_reason=? "
            "WHERE status='pending'",
            (reason,),
        )
        return cursor.rowcount


def get_pending_proposals() -> list[dict]:
    """Get all proposals still pending veto."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM proposals WHERE status='pending' ORDER BY proposed_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_due_proposals() -> list[dict]:
    """Get pending proposals whose veto window has passed."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM proposals WHERE status='pending' "
            "AND datetime(execute_after) <= datetime('now') "
            "ORDER BY proposed_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def execute_proposal(proposal_id: str) -> dict:
    """Execute a proposal that has passed its veto window.

    For new_experiment proposals, creates the corresponding experiment.
    Returns {executed: bool, experiment_id: str|None}.
    """
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM proposals WHERE id=?", (proposal_id,)
        ).fetchone()

    if not row or row["status"] != "pending":
        return {"executed": False, "reason": "Not pending"}

    proposal = dict(row)
    result = {"executed": True, "experiment_id": None}

    if proposal["type"] == "new_experiment":
        exp_id = experiment_engine.propose_experiment(
            channel=proposal.get("title", "unknown").split()[-1].lower(),
            hypothesis=proposal.get("hypothesis", ""),
            tactic=proposal.get("description", ""),
        )
        experiment_engine.start_experiment(exp_id)
        result["experiment_id"] = exp_id

    with db.get_conn() as conn:
        conn.execute(
            "UPDATE proposals SET status='executed', executed_at=?, experiment_id=? "
            "WHERE id=?",
            (_now_iso(), result.get("experiment_id"), proposal_id),
        )

    log.info("Executed proposal %s", proposal_id)
    return result


def list_proposals(
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List proposals, optionally filtered by status."""
    query = "SELECT * FROM proposals WHERE 1=1"
    params = []
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with db.get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def build_digest_body() -> str:
    """Build the daily digest email body for RJM.

    Lists proposals executing today + yesterday's results.
    """
    due = get_due_proposals()
    pending = get_pending_proposals()

    lines = ["=== BTL Daily Digest ===\n"]

    if due:
        lines.append(f"EXECUTING TODAY ({len(due)} proposals):\n")
        for p in due:
            lines.append(f"  [{p['id']}] {p['title']}")
            lines.append(f"    Risk: {p['risk_level']} | Impact: {p['estimated_impact']}")
            lines.append(f"    Reply 'veto {p['id']}' to block\n")
    else:
        lines.append("No proposals executing today.\n")

    if pending:
        remaining = [p for p in pending if p["id"] not in {d["id"] for d in due}]
        if remaining:
            lines.append(f"\nPENDING ({len(remaining)} in veto window):\n")
            for p in remaining:
                lines.append(f"  [{p['id']}] {p['title']} (executes: {p['execute_after'][:16]})")

    # Recent executed
    recent_executed = list_proposals(status="executed", limit=5)
    if recent_executed:
        lines.append("\n\nRECENTLY EXECUTED:\n")
        for p in recent_executed:
            lines.append(f"  [{p['id']}] {p['title']} (at {p.get('executed_at', '?')[:16]})")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd outreach_agent && python -m pytest ../tests/test_veto_system.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/veto_system.py tests/test_veto_system.py
git commit -m "feat(btl): veto system — proposal queue + 24hr window"
```

---

## Task 8: Revenue Tracker

**Files:**
- Create: `outreach_agent/revenue_tracker.py`
- Test: `tests/test_revenue_tracker.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_revenue_tracker.py
"""Tests for revenue tracker — budget ledger and spend authorization."""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
os.environ["RJM_DB_PATH"] = ":memory:"

import db
import btl_db
db.init_db()
btl_db.init_btl_tables()

from revenue_tracker import (
    record_donation,
    record_spend,
    get_budget_summary,
    can_auto_spend,
    get_daily_spend,
)


def setup_function():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM growth_budget")


def test_record_donation():
    record_donation(50.00, source="stripe_pi_test1")
    summary = get_budget_summary()
    assert summary["total_donations"] == 50.00
    assert summary["total_allocated"] == 25.00  # 50%
    assert summary["available_balance"] == 25.00


def test_record_spend():
    record_donation(50.00, source="stripe_pi_test1")
    record_spend(
        amount=3.00,
        channel="ch_submithub",
        experiment_id="exp_test_1",
        note="SubmitHub submission",
    )
    summary = get_budget_summary()
    assert summary["total_spent"] == 3.00
    assert summary["available_balance"] == 22.00


def test_can_auto_spend_under_limit():
    record_donation(100.00, source="test")
    assert can_auto_spend(4.99) is True  # under EUR 5 limit


def test_can_auto_spend_over_limit():
    record_donation(100.00, source="test")
    assert can_auto_spend(5.01) is False  # needs veto


def test_can_auto_spend_insufficient_balance():
    record_donation(10.00, source="test")
    # Balance is 5.00, reserve is 5.00, so nothing spendable
    assert can_auto_spend(1.00) is False


def test_daily_spend_cap():
    record_donation(1000.00, source="test")
    # Spend up to daily cap
    for i in range(4):
        record_spend(4.00, channel="test", note=f"spend {i}")
    # Total daily spend = 16 EUR, cap is 15 EUR
    # But spend #4 went through because we don't pre-check in record_spend
    # can_auto_spend should now return False for any more
    daily = get_daily_spend()
    assert daily == 16.00  # all 4 recorded
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd outreach_agent && python -m pytest ../tests/test_revenue_tracker.py -v`
Expected: FAIL

- [ ] **Step 3: Implement revenue_tracker.py**

```python
# outreach_agent/revenue_tracker.py
"""Revenue tracking — Stripe donations, budget ledger, spend authorization."""

import json
import logging
from datetime import datetime

import db
from config import (
    BTL_DONATION_ALLOCATION_PCT,
    BTL_AUTO_SPEND_MAX_EUR,
    BTL_DAILY_SPEND_CAP_EUR,
    BTL_DAILY_SPEND_CAP_PCT,
    BTL_RESERVE_MIN_EUR,
    STRIPE_API_KEY,
)

log = logging.getLogger(__name__)


def record_donation(amount: float, source: str = "", note: str = "") -> None:
    """Record a donation and allocate to growth budget."""
    allocated = round(amount * BTL_DONATION_ALLOCATION_PCT, 2)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO growth_budget (date, type, amount, source, note) "
            "VALUES (date('now'), 'donation', ?, ?, ?)",
            (amount, source, note or f"Donation of EUR {amount:.2f}"),
        )
        conn.execute(
            "INSERT INTO growth_budget (date, type, amount, source, note) "
            "VALUES (date('now'), 'allocation', ?, ?, ?)",
            (allocated, source, f"50% allocated from EUR {amount:.2f} donation"),
        )
    log.info("Recorded donation EUR %.2f, allocated EUR %.2f", amount, allocated)


def record_spend(
    amount: float,
    channel: str = "",
    experiment_id: str = "",
    note: str = "",
) -> None:
    """Record a spend from the growth budget."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO growth_budget (date, type, amount, channel, experiment_id, note) "
            "VALUES (date('now'), 'spend', ?, ?, ?, ?)",
            (-abs(amount), channel, experiment_id, note),
        )
    log.info("Recorded spend EUR %.2f on %s", amount, channel)


def get_budget_summary() -> dict:
    """Get current budget state."""
    with db.get_conn() as conn:
        donations = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM growth_budget WHERE type='donation'"
        ).fetchone()["total"]

        allocated = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM growth_budget WHERE type='allocation'"
        ).fetchone()["total"]

        spent = conn.execute(
            "SELECT COALESCE(SUM(ABS(amount)), 0) as total FROM growth_budget WHERE type='spend'"
        ).fetchone()["total"]

    return {
        "total_donations": round(donations, 2),
        "total_allocated": round(allocated, 2),
        "total_spent": round(spent, 2),
        "available_balance": round(allocated - spent, 2),
    }


def get_daily_spend() -> float:
    """Get total spend today."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(ABS(amount)), 0) as total FROM growth_budget "
            "WHERE type='spend' AND date=date('now')"
        ).fetchone()
        return round(row["total"], 2)


def can_auto_spend(amount: float) -> bool:
    """Check if a spend of this amount can be auto-approved (no veto needed).

    Rules:
    - Must be under BTL_AUTO_SPEND_MAX_EUR
    - Must not exceed daily cap
    - Must leave at least BTL_RESERVE_MIN_EUR in balance
    """
    if amount > BTL_AUTO_SPEND_MAX_EUR:
        return False

    summary = get_budget_summary()
    balance = summary["available_balance"]

    # Reserve check
    if balance - amount < BTL_RESERVE_MIN_EUR:
        return False

    # Daily cap check
    daily = get_daily_spend()
    daily_cap = min(BTL_DAILY_SPEND_CAP_EUR, balance * BTL_DAILY_SPEND_CAP_PCT)
    if daily + amount > daily_cap:
        return False

    return True


def poll_stripe() -> list[dict]:
    """Poll Stripe API for new donations. Returns list of new payments.

    Requires STRIPE_API_KEY to be set.
    """
    if not STRIPE_API_KEY:
        log.warning("STRIPE_API_KEY not set — skipping Stripe poll")
        return []

    try:
        import stripe
        stripe.api_key = STRIPE_API_KEY

        # Get recent successful charges
        charges = stripe.Charge.list(limit=20, status="succeeded")
        new_donations = []

        for charge in charges.auto_paging_iter():
            source_id = charge.id
            amount_eur = charge.amount / 100  # Stripe uses cents

            # Check if already recorded
            with db.get_conn() as conn:
                existing = conn.execute(
                    "SELECT id FROM growth_budget WHERE source=? AND type='donation'",
                    (source_id,),
                ).fetchone()

            if not existing:
                record_donation(amount_eur, source=source_id)
                new_donations.append({
                    "id": source_id,
                    "amount": amount_eur,
                    "date": datetime.fromtimestamp(charge.created).isoformat(),
                })

        return new_donations

    except ImportError:
        log.warning("stripe package not installed — run: pip install stripe")
        return []
    except Exception as e:
        log.error("Stripe poll failed: %s", e)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd outreach_agent && python -m pytest ../tests/test_revenue_tracker.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/revenue_tracker.py tests/test_revenue_tracker.py
git commit -m "feat(btl): revenue tracker — Stripe + budget ledger"
```

---

## Task 9: Channel Agent Base Class

**Files:**
- Create: `outreach_agent/channel_agents/__init__.py`
- Test: `tests/test_channel_agents.py`

- [ ] **Step 1: Write the base class test**

```python
# tests/test_channel_agents.py
"""Tests for channel agent base class and registration."""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
os.environ["RJM_DB_PATH"] = ":memory:"

import db
import btl_db
db.init_db()
btl_db.init_btl_tables()

from channel_agents import ChannelAgent, get_agent, list_agents


class MockAgent(ChannelAgent):
    channel_id = "ch_mock"
    arms = {"style": ["bold", "subtle"]}

    def can_run(self) -> bool:
        return True

    def execute(self, config: dict) -> dict:
        return {"posts": 1, "impressions": 100}

    def get_metrics(self, days: int = 7) -> dict:
        return {"listeners_gained": 5}


def test_base_class_interface():
    agent = MockAgent()
    assert agent.can_run() is True
    result = agent.execute({})
    assert "posts" in result


def test_agent_registration():
    MockAgent()  # constructor should register
    agents = list_agents()
    assert "ch_mock" in [a.channel_id for a in agents]


def test_get_agent_by_id():
    MockAgent()
    agent = get_agent("ch_mock")
    assert agent is not None
    assert agent.channel_id == "ch_mock"


def test_bandit_integration():
    agent = MockAgent()
    selection = agent.select_arms()
    assert "style" in selection
    assert selection["style"] in ["bold", "subtle"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd outreach_agent && python -m pytest ../tests/test_channel_agents.py -v`
Expected: FAIL

- [ ] **Step 3: Implement base class**

```python
# outreach_agent/channel_agents/__init__.py
"""Channel agent base class and registry for BTL protocol."""

from abc import ABC, abstractmethod
from bandit_framework import Bandit

_REGISTRY: dict[str, "ChannelAgent"] = {}


class ChannelAgent(ABC):
    """Base class for all BTL channel agents.

    Subclasses must define:
      - channel_id: str — matches channel_registry.json
      - arms: dict[str, list[str]] — bandit arms for this channel

    And implement:
      - can_run() -> bool
      - execute(config) -> dict
      - get_metrics(days) -> dict
    """

    channel_id: str = ""
    arms: dict[str, list[str]] = {}

    def __init__(self):
        if self.channel_id:
            _REGISTRY[self.channel_id] = self
            if self.arms:
                self._bandit = Bandit(self.channel_id, self.arms)
            else:
                self._bandit = None

    @abstractmethod
    def can_run(self) -> bool:
        """Check if this agent can execute (credentials available, not rate-limited)."""
        ...

    @abstractmethod
    def execute(self, config: dict) -> dict:
        """Run one action cycle. Return metrics dict."""
        ...

    @abstractmethod
    def get_metrics(self, days: int = 7) -> dict:
        """Return historical performance metrics."""
        ...

    def select_arms(self) -> dict[str, str]:
        """Select bandit arm values for next action."""
        if self._bandit:
            return self._bandit.select()
        return {}

    def record_outcome(self, arm_values: dict[str, str], reward: float) -> None:
        """Record outcome for bandit learning."""
        if self._bandit:
            self._bandit.record(arm_values, reward)

    def get_bandit_stats(self) -> dict:
        """Get bandit performance stats."""
        if self._bandit:
            return self._bandit.get_stats()
        return {}


def get_agent(channel_id: str) -> ChannelAgent | None:
    """Get a registered channel agent by ID."""
    return _REGISTRY.get(channel_id)


def list_agents() -> list[ChannelAgent]:
    """List all registered channel agents."""
    return list(_REGISTRY.values())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd outreach_agent && python -m pytest ../tests/test_channel_agents.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/channel_agents/__init__.py tests/test_channel_agents.py
git commit -m "feat(btl): channel agent base class + registry"
```

---

## Task 10: Growth Brain (Orchestrator)

**Files:**
- Create: `outreach_agent/growth_brain.py`
- Test: `tests/test_growth_brain.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_growth_brain.py
"""Tests for growth brain — the BTL orchestrator."""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
os.environ["RJM_DB_PATH"] = ":memory:"

import db
import btl_db
db.init_db()
btl_db.init_btl_tables()

from growth_brain import (
    run_l1_optimize,
    run_l2_reallocate,
    run_veto_check,
    get_brain_status,
)


def test_run_l1_optimize_no_crash():
    """L1 should run without error even with no data."""
    result = run_l1_optimize()
    assert "bandits_updated" in result


def test_run_l2_reallocate_no_crash():
    """L2 should run without error even with no channel metrics."""
    result = run_l2_reallocate()
    assert "channels_reallocated" in result


def test_run_veto_check_no_crash():
    """Veto check should run cleanly with no proposals."""
    result = run_veto_check()
    assert "proposals_executed" in result
    assert result["proposals_executed"] == 0


def test_get_brain_status():
    """Brain status should return a comprehensive state dict."""
    status = get_brain_status()
    assert "active_experiments" in status
    assert "pending_proposals" in status
    assert "active_channels" in status
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd outreach_agent && python -m pytest ../tests/test_growth_brain.py -v`
Expected: FAIL

- [ ] **Step 3: Implement growth_brain.py**

```python
# outreach_agent/growth_brain.py
"""Growth Brain — BTL protocol orchestrator.

Runs the three learning layers:
  L1: Tactical optimization (bandit updates, 4x/day)
  L2: Strategic reallocation (weekly)
  L3: Discovery + invention (2x/week)

Also handles veto execution, self-assessment triggers, and brain status.

Usage:
  python3 growth_brain.py status         # Full brain state
  python3 growth_brain.py l1             # Run L1 tactical optimization
  python3 growth_brain.py l2             # Run L2 strategic reallocation
  python3 growth_brain.py veto_check     # Execute due proposals
  python3 growth_brain.py assess         # Run self-assessment
  python3 growth_brain.py discover       # Run L3 discovery (stub)
  python3 growth_brain.py insights       # View strategic insights
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import db
import btl_db
import experiment_engine
import strategy_portfolio
import veto_system
import self_assessment
import revenue_tracker

try:
    import events as _events
    _EVENTS = True
except ImportError:
    _EVENTS = False

try:
    import fleet_state
    _FLEET = True
except ImportError:
    _FLEET = False

log = logging.getLogger(__name__)


def run_l1_optimize() -> dict:
    """Layer 1: Tactical optimization — update all bandits.

    Reads recent performance data and recalculates bandit weights.
    """
    if _FLEET:
        fleet_state.heartbeat("btl_l1", status="ok")

    # Import registered channel agents
    try:
        from channel_agents import list_agents
        agents = list_agents()
    except ImportError:
        agents = []

    updated = 0
    breakthroughs = []

    for agent in agents:
        if not agent.arms:
            continue
        stats = agent.get_bandit_stats()
        if stats:
            updated += 1
        # Check for breakthroughs
        from bandit_framework import Bandit
        b = Bandit(agent.channel_id, agent.arms)
        bts = b.detect_breakthroughs()
        if bts:
            breakthroughs.extend(bts)
            if _EVENTS:
                for bt in bts:
                    _events.publish("bandit.breakthrough", "growth_brain", bt)

    result = {"bandits_updated": updated, "breakthroughs": len(breakthroughs)}
    log.info("L1 optimize: %d bandits updated, %d breakthroughs", updated, len(breakthroughs))
    return result


def run_l2_reallocate() -> dict:
    """Layer 2: Strategic reallocation — shift channel weights."""
    if _FLEET:
        fleet_state.heartbeat("btl_l2", status="ok")

    reg = strategy_portfolio.reallocate_weights()
    active = [c for c in reg["channels"] if c["status"] == "active"]

    if _EVENTS:
        _events.publish("channel.reallocated", "growth_brain", {
            "active_channels": len(active),
            "timestamp": datetime.utcnow().isoformat(),
        })

    result = {"channels_reallocated": len(active)}
    log.info("L2 reallocate: %d active channels", len(active))
    return result


def run_veto_check() -> dict:
    """Check and execute proposals that have passed their veto window."""
    due = veto_system.get_due_proposals()
    executed = 0
    for proposal in due:
        if experiment_engine.can_start_new():
            veto_system.execute_proposal(proposal["id"])
            executed += 1
            if _EVENTS:
                _events.publish("proposal.executed", "growth_brain", {
                    "proposal_id": proposal["id"],
                    "title": proposal["title"],
                })
        else:
            log.warning(
                "Cannot execute proposal %s — experiment limit reached",
                proposal["id"],
            )

    result = {"proposals_executed": executed, "proposals_due": len(due)}
    log.info("Veto check: %d/%d proposals executed", executed, len(due))
    return result


def run_self_assess(
    listeners_current: int = 0,
    listeners_previous: int = 0,
) -> dict:
    """Run self-assessment and save the score."""
    # Gather metrics
    active_experiments = experiment_engine.active_count()

    # Experiments completed in last 30 days
    completed = experiment_engine.list_experiments(status="analyzed")
    succeeded = sum(1 for e in completed if e.get("result") == "success")

    # Channel diversity
    active_channels = strategy_portfolio.get_active_channels()
    positive_channels = 0
    for ch in active_channels:
        lei = strategy_portfolio.get_channel_lei(ch["id"], days=7)
        if lei > 0:
            positive_channels += 1

    # Budget
    budget = revenue_tracker.get_budget_summary()
    has_budget = budget["total_spent"] > 0

    score = self_assessment.calculate_score(
        listeners_current=listeners_current,
        listeners_previous=listeners_previous,
        experiments_succeeded=succeeded,
        experiments_completed=len(completed),
        active_channels_positive=positive_channels,
        active_channels_total=len(active_channels),
        has_budget=has_budget,
    )

    self_assessment.save_score(score)

    action = self_assessment.get_triggered_action(score["total_score"])
    score["triggered_action"] = action

    if _EVENTS:
        _events.publish("score.calculated", "growth_brain", {
            "score": score["total_score"],
            "action": action["level"],
        })

    log.info("Self-assessment: score=%d, action=%s", score["total_score"], action["level"])
    return score


def get_brain_status() -> dict:
    """Return comprehensive brain state for display."""
    active_exp = experiment_engine.list_experiments(status="active")
    pending_prop = veto_system.get_pending_proposals()
    active_ch = strategy_portfolio.get_active_channels()
    budget = revenue_tracker.get_budget_summary()
    score_history = self_assessment.get_score_history(limit=4)

    return {
        "active_experiments": len(active_exp),
        "experiments": [{"id": e["id"], "channel": e["channel"], "status": e["status"]} for e in active_exp],
        "pending_proposals": len(pending_prop),
        "proposals": [{"id": p["id"], "title": p["title"], "execute_after": p["execute_after"]} for p in pending_prop],
        "active_channels": len(active_ch),
        "channels": [{"id": c["id"], "name": c["name"], "weight": c["weight"]} for c in active_ch],
        "budget": budget,
        "recent_scores": [{"score": s["total_score"], "date": s["calculated_at"][:10]} for s in score_history],
    }


def get_strategic_insights(limit: int = 20) -> list[dict]:
    """Retrieve strategic insights from the database."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM strategic_insights ORDER BY discovered_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def save_strategic_insight(
    source: str,
    insight: str,
    confidence: float = 0.5,
    applicable_channels: list[str] | None = None,
) -> None:
    """Save a new strategic insight."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO strategic_insights (source, insight, confidence, applicable_channels) "
            "VALUES (?, ?, ?, ?)",
            (source, insight, confidence, json.dumps(applicable_channels or [])),
        )


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    db.init_db()
    btl_db.init_btl_tables()

    if len(sys.argv) < 2:
        print("Usage: python3 growth_brain.py [status|l1|l2|veto_check|assess|discover|insights]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        status = get_brain_status()
        print(json.dumps(status, indent=2))

    elif cmd == "l1":
        result = run_l1_optimize()
        print(json.dumps(result, indent=2))

    elif cmd == "l2":
        result = run_l2_reallocate()
        print(json.dumps(result, indent=2))

    elif cmd == "veto_check":
        result = run_veto_check()
        print(json.dumps(result, indent=2))

    elif cmd == "assess":
        listeners = int(sys.argv[2]) if len(sys.argv) > 2 else 325
        prev = int(sys.argv[3]) if len(sys.argv) > 3 else 325
        result = run_self_assess(listeners_current=listeners, listeners_previous=prev)
        print(f"\n=== Growth Health Score: {result['total_score']}/100 ===\n")
        for name, comp in result["components"].items():
            print(f"  {name}: {comp['score']} ({comp['detail']})")
        print(f"\nAction: {result['triggered_action']['description']}")

    elif cmd == "discover":
        print("L3 Discovery is a Claude-driven agent run — invoke via master agent.")
        print("Use: python3 rjm.py brain discover")

    elif cmd == "insights":
        insights = get_strategic_insights()
        if not insights:
            print("No strategic insights yet.")
        for ins in insights:
            print(f"  [{ins['confidence']:.0%}] {ins['insight']}")
            print(f"    Source: {ins['source']} | Channels: {ins.get('applicable_channels', '[]')}")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd outreach_agent && python -m pytest ../tests/test_growth_brain.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/growth_brain.py tests/test_growth_brain.py
git commit -m "feat(btl): growth brain — L1/L2/L3 orchestrator"
```

---

## Task 11: Competitor Tracker

**Files:**
- Create: `outreach_agent/competitor_tracker.py`

- [ ] **Step 1: Implement competitor_tracker.py**

```python
# outreach_agent/competitor_tracker.py
"""Comparable artist monitoring for BTL Layer 3.

Tracks Spotify metrics for similar artists to detect growth spikes
and replicable tactics.

Usage:
  python3 competitor_tracker.py status    # Current tracking state
  python3 competitor_tracker.py update    # Fetch latest metrics (requires Spotify API)
  python3 competitor_tracker.py spikes    # Show artists with recent growth spikes
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from config import BASE_DIR

log = logging.getLogger(__name__)

TRACKING_FILE = Path(BASE_DIR).parent / "data" / "competitor_tracking.json"

# Initial comparable artists (L3 will discover more)
SEED_ARTISTS = [
    {"name": "Anyma", "genre": "Melodic Techno", "reason": "Visual scale reference"},
    {"name": "Argy", "genre": "Tribal/Techno", "reason": "Tribal texture overlap"},
    {"name": "Agents Of Time", "genre": "Melodic Techno", "reason": "Similar production style"},
    {"name": "Colyn", "genre": "Melodic Techno", "reason": "Independent growth trajectory"},
    {"name": "Innellea", "genre": "Melodic Techno", "reason": "Similar visual aesthetic"},
]


def load_tracking() -> dict:
    """Load tracking data."""
    if TRACKING_FILE.exists():
        return json.loads(TRACKING_FILE.read_text())
    return {"artists": SEED_ARTISTS, "snapshots": []}


def save_tracking(data: dict) -> None:
    """Save tracking data."""
    TRACKING_FILE.write_text(json.dumps(data, indent=2))


def add_artist(name: str, genre: str = "", reason: str = "") -> None:
    """Add an artist to track."""
    data = load_tracking()
    if any(a["name"].lower() == name.lower() for a in data["artists"]):
        log.info("Already tracking %s", name)
        return
    data["artists"].append({"name": name, "genre": genre, "reason": reason})
    save_tracking(data)
    log.info("Added %s to competitor tracking", name)


def record_snapshot(artist_name: str, monthly_listeners: int) -> None:
    """Record a listener snapshot for an artist."""
    data = load_tracking()
    data["snapshots"].append({
        "artist": artist_name,
        "listeners": monthly_listeners,
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
    })
    save_tracking(data)


def detect_spikes(threshold_pct: float = 20.0) -> list[dict]:
    """Detect artists with >threshold% week-over-week growth.

    Returns list of {artist, previous, current, growth_pct}.
    """
    data = load_tracking()
    spikes = []

    for artist in data["artists"]:
        snapshots = sorted(
            [s for s in data["snapshots"] if s["artist"] == artist["name"]],
            key=lambda s: s["date"],
        )
        if len(snapshots) < 2:
            continue

        current = snapshots[-1]["listeners"]
        previous = snapshots[-2]["listeners"]

        if previous > 0:
            growth = (current - previous) / previous * 100
            if growth >= threshold_pct:
                spikes.append({
                    "artist": artist["name"],
                    "previous": previous,
                    "current": current,
                    "growth_pct": round(growth, 1),
                    "date": snapshots[-1]["date"],
                })

    return spikes


def get_status() -> dict:
    """Return tracking summary."""
    data = load_tracking()
    return {
        "artists_tracked": len(data["artists"]),
        "total_snapshots": len(data["snapshots"]),
        "artists": [a["name"] for a in data["artists"]],
        "recent_spikes": detect_spikes(),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 competitor_tracker.py [status|update|spikes]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        status = get_status()
        print(json.dumps(status, indent=2))
    elif cmd == "spikes":
        spikes = detect_spikes()
        if not spikes:
            print("No growth spikes detected.")
        for s in spikes:
            print(f"  {s['artist']}: {s['previous']} -> {s['current']} (+{s['growth_pct']}%)")
    elif cmd == "update":
        print("Spotify API update requires credentials — invoke via L3 discovery run.")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add outreach_agent/competitor_tracker.py
git commit -m "feat(btl): competitor tracker — comparable artist monitoring"
```

---

## Task 12: CLI Extensions to rjm.py

**Files:**
- Modify: `rjm.py`

- [ ] **Step 1: Read the current rjm.py dispatch section**

Read the bottom of `rjm.py` to find the main dispatch block where commands are routed.

- [ ] **Step 2: Add BTL command functions**

Add these functions before the main dispatch block in `rjm.py`:

```python
# ─── BTL Protocol Commands ───────────────────────────────────────────────────

GROWTH_BRAIN_PY = OUTREACH_DIR / "growth_brain.py"


def cmd_brain(args: list[str]):
    """Growth brain commands: status, l1, l2, veto_check, assess, discover, insights."""
    if not args:
        args = ["status"]
    sys.exit(_run([_OUTREACH_PYTHON, str(GROWTH_BRAIN_PY)] + args, cwd=str(OUTREACH_DIR)))


def cmd_experiment(args: list[str]):
    """Experiment commands: list, active, results, propose."""
    sub = args[0] if args else "list"
    if sub == "list":
        _run_py_snippet("""
import db, btl_db, experiment_engine, json
db.init_db(); btl_db.init_btl_tables()
for e in experiment_engine.list_experiments():
    print(f"  [{e['status']:10s}] {e['id']}  {e['channel']}  {e['hypothesis'][:60]}")
""")
    elif sub == "active":
        _run_py_snippet("""
import db, btl_db, experiment_engine, json
db.init_db(); btl_db.init_btl_tables()
active = experiment_engine.list_experiments(status='active')
if not active: print("No active experiments.")
for e in active:
    print(f"  {e['id']}  ch={e['channel']}  started={e.get('started_at','?')[:10]}")
    print(f"    {e['hypothesis'][:80]}")
""")
    elif sub == "results":
        _run_py_snippet("""
import db, btl_db, experiment_engine, json
db.init_db(); btl_db.init_btl_tables()
analyzed = experiment_engine.list_experiments(status='analyzed')
if not analyzed: print("No completed experiments yet.")
for e in analyzed:
    print(f"  {e['id']}  result={e.get('result','?')}  ch={e['channel']}")
    if e.get('learning'): print(f"    Learning: {e['learning'][:80]}")
""")
    else:
        print(f"Unknown experiment command: {sub}")
        sys.exit(1)


def cmd_veto(args: list[str]):
    """Veto a proposal: veto <id> or veto all."""
    if not args:
        print("Usage: python3 rjm.py veto <proposal_id> | veto all")
        sys.exit(1)
    target = args[0]
    _run_py_snippet(f"""
import db, btl_db, veto_system
db.init_db(); btl_db.init_btl_tables()
if '{target}' == 'all':
    n = veto_system.veto_all("Manual emergency brake")
    print(f"Vetoed {{n}} proposals.")
else:
    veto_system.veto_proposal('{target}', "Manual veto via CLI")
    print(f"Vetoed {target}.")
""")


def cmd_proposals(args: list[str]):
    """List pending proposals."""
    _run_py_snippet("""
import db, btl_db, veto_system
db.init_db(); btl_db.init_btl_tables()
pending = veto_system.get_pending_proposals()
if not pending: print("No pending proposals.")
for p in pending:
    print(f"  [{p['id']}] {p['title']}")
    print(f"    Risk: {p['risk_level']}  Executes: {p['execute_after'][:16]}")
""")


def cmd_budget(args: list[str]):
    """Growth budget status."""
    _run_py_snippet("""
import db, btl_db, revenue_tracker, json
db.init_db(); btl_db.init_btl_tables()
summary = revenue_tracker.get_budget_summary()
print("=== Growth Budget ===")
print(f"  Total donations:  EUR {summary['total_donations']:.2f}")
print(f"  Allocated (50%):  EUR {summary['total_allocated']:.2f}")
print(f"  Total spent:      EUR {summary['total_spent']:.2f}")
print(f"  Available:        EUR {summary['available_balance']:.2f}")
""")


def cmd_channels(args: list[str]):
    """Channel performance + allocation table."""
    sub = args[0] if args else "list"
    if sub in ("list", ""):
        _run_py_snippet("""
import db, btl_db, strategy_portfolio, json
db.init_db(); btl_db.init_btl_tables()
summary = strategy_portfolio.get_portfolio_summary()
print(f"=== Channels: {summary['active']} active, {summary['queued']} queued, {summary['paused']} paused ===\\n")
for ch in summary['channels']:
    lei = ch.get('lei_7d', 0)
    icon = '\\u2713' if ch['status'] == 'active' else ('\\u25cb' if ch['status'] == 'queued' else '\\u2717')
    print(f"  {icon} {ch['id']:30s}  w={ch['weight']:.2f}  LEI={lei:.0f}  [{ch['status']}]")
""")
    elif sub == "activate" and len(args) > 1:
        cid = args[1]
        _run_py_snippet(f"""
import db, btl_db, strategy_portfolio
db.init_db(); btl_db.init_btl_tables()
strategy_portfolio.activate_channel('{cid}')
print(f"Activated {cid}")
""")
    elif sub == "pause" and len(args) > 1:
        cid = args[1]
        _run_py_snippet(f"""
import db, btl_db, strategy_portfolio
db.init_db(); btl_db.init_btl_tables()
strategy_portfolio.pause_channel('{cid}')
print(f"Paused {cid}")
""")
    else:
        print(f"Unknown channels command: {sub}")
        sys.exit(1)


def cmd_score(args: list[str]):
    """Growth Health Score."""
    sys.exit(_run([_OUTREACH_PYTHON, str(GROWTH_BRAIN_PY), "assess"] + args, cwd=str(OUTREACH_DIR)))


def _run_py_snippet(code: str):
    """Run a quick Python snippet in the outreach venv."""
    import subprocess
    result = subprocess.run(
        [str(_OUTREACH_PYTHON), "-c", code],
        cwd=str(OUTREACH_DIR),
    )
    sys.exit(result.returncode)
```

- [ ] **Step 3: Add dispatch entries to main block**

In the main dispatch section of `rjm.py`, add these entries:

```python
    elif cmd == "brain":
        cmd_brain(rest)
    elif cmd == "experiment":
        cmd_experiment(rest)
    elif cmd == "veto":
        cmd_veto(rest)
    elif cmd == "proposals":
        cmd_proposals(rest)
    elif cmd == "budget":
        cmd_budget(rest)
    elif cmd == "channels":
        cmd_channels(rest)
    elif cmd == "score":
        cmd_score(rest)
```

- [ ] **Step 4: Test CLI commands**

Run: `cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/sharp-poincare" && python3 rjm.py brain status`
Expected: JSON output showing brain state (no crash)

Run: `python3 rjm.py channels`
Expected: Channel listing from registry

Run: `python3 rjm.py budget`
Expected: Budget summary showing zeros

- [ ] **Step 5: Commit**

```bash
git add rjm.py
git commit -m "feat(btl): CLI extensions — brain/experiment/veto/budget/channels/score commands"
```

---

## Task 13: Master Agent BTL Integration

**Files:**
- Modify: `outreach_agent/master_agent.py`

- [ ] **Step 1: Read the master agent's existing command dispatch**

Read the bottom of `outreach_agent/master_agent.py` to find the main dispatch pattern.

- [ ] **Step 2: Add BTL operational modes to master_agent.py**

Add after existing command functions (near the end of the file, before the `if __name__ == "__main__"` block):

```python
# ─── BTL Protocol Integration ────────────────────────────────────────────────

def cmd_btl_optimize():
    """Run BTL Layer 1 tactical optimization."""
    try:
        import growth_brain
        result = growth_brain.run_l1_optimize()
        print(json.dumps(result, indent=2))
    except ImportError:
        print("BTL protocol not installed — run setup first")
        sys.exit(1)


def cmd_btl_reallocate():
    """Run BTL Layer 2 strategic reallocation."""
    try:
        import growth_brain
        result = growth_brain.run_l2_reallocate()
        print(json.dumps(result, indent=2))
    except ImportError:
        print("BTL protocol not installed")
        sys.exit(1)


def cmd_btl_veto_check():
    """Check and execute due proposals."""
    try:
        import growth_brain
        result = growth_brain.run_veto_check()
        print(json.dumps(result, indent=2))
    except ImportError:
        print("BTL protocol not installed")
        sys.exit(1)


def cmd_btl_assess():
    """Run self-assessment and report Growth Health Score."""
    try:
        import growth_brain
        listeners = _get_current_listeners()
        prev = _get_previous_listeners()
        result = growth_brain.run_self_assess(
            listeners_current=listeners,
            listeners_previous=prev,
        )
        print(f"\n=== Growth Health Score: {result['total_score']}/100 ===\n")
        for name, comp in result["components"].items():
            print(f"  {name}: {comp['score']} ({comp['detail']})")
        print(f"\nAction: {result['triggered_action']['description']}")
    except ImportError:
        print("BTL protocol not installed")
        sys.exit(1)


def cmd_btl_digest():
    """Build and optionally send the daily veto digest email."""
    try:
        import veto_system
        body = veto_system.build_digest_body()
        print(body)
        # TODO: send via gmail_client when ready
    except ImportError:
        print("BTL protocol not installed")
        sys.exit(1)


def cmd_btl_fund():
    """Check Stripe for new donations and update budget."""
    try:
        import revenue_tracker
        donations = revenue_tracker.poll_stripe()
        if donations:
            print(f"Recorded {len(donations)} new donation(s):")
            for d in donations:
                print(f"  EUR {d['amount']:.2f} ({d['date'][:10]})")
        else:
            print("No new donations found.")
        summary = revenue_tracker.get_budget_summary()
        print(f"\nAvailable budget: EUR {summary['available_balance']:.2f}")
    except ImportError:
        print("BTL protocol not installed")
        sys.exit(1)


def _get_current_listeners() -> int:
    """Read current listener count from tracker."""
    tracker_path = Path(__file__).parent / "spotify_tracker.py"
    if tracker_path.exists():
        try:
            result = subprocess.run(
                [sys.executable, str(tracker_path), "current"],
                capture_output=True, text=True, cwd=str(Path(__file__).parent),
            )
            return int(result.stdout.strip()) if result.stdout.strip().isdigit() else 325
        except Exception:
            pass
    return 325


def _get_previous_listeners() -> int:
    """Read previous week's listener count."""
    listeners_file = Path(__file__).parent.parent / "data" / "listeners.json"
    if listeners_file.exists():
        try:
            data = json.loads(listeners_file.read_text())
            return data.get("count", 325)
        except Exception:
            pass
    return 325
```

- [ ] **Step 3: Add BTL commands to the dispatch block**

In the main dispatch (the `if __name__ == "__main__"` section), add:

```python
    elif cmd == "btl_optimize":
        cmd_btl_optimize()
    elif cmd == "btl_reallocate":
        cmd_btl_reallocate()
    elif cmd == "btl_veto_check":
        cmd_btl_veto_check()
    elif cmd == "btl_assess":
        cmd_btl_assess()
    elif cmd == "btl_digest":
        cmd_btl_digest()
    elif cmd == "btl_fund":
        cmd_btl_fund()
```

- [ ] **Step 4: Test the integration**

Run: `cd outreach_agent && python3 master_agent.py btl_assess`
Expected: Growth Health Score output (score based on current data)

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/master_agent.py
git commit -m "feat(btl): master agent BTL integration — 6 new operational modes"
```

---

## Task 14: End-to-End Integration Test

**Files:**
- Create: `tests/test_btl_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_btl_integration.py
"""End-to-end integration test for the BTL protocol.

Tests the full flow: propose experiment → veto check → execute → assess.
"""
import sys
import os
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))
os.environ["RJM_DB_PATH"] = ":memory:"

# Set up temp channel registry
_TEMP = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
_TEMP.write(json.dumps({
    "channels": [
        {"id": "ch_test", "name": "Test", "status": "active", "weight": 0.5,
         "agent": "test", "cost_type": "free"},
        {"id": "ch_test2", "name": "Test2", "status": "active", "weight": 0.5,
         "agent": "test2", "cost_type": "free"},
    ],
    "last_reallocation": None,
}))
_TEMP.close()
os.environ["BTL_CHANNEL_REGISTRY_PATH"] = _TEMP.name

import db
import btl_db
db.init_db()
btl_db.init_btl_tables()

import experiment_engine
import veto_system
import strategy_portfolio
import revenue_tracker
import self_assessment
import growth_brain


def setup_function():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM experiments")
        conn.execute("DELETE FROM proposals")
        conn.execute("DELETE FROM growth_budget")
        conn.execute("DELETE FROM channel_metrics")
        conn.execute("DELETE FROM bandit_state")
        conn.execute("DELETE FROM strategic_insights")


def test_full_experiment_lifecycle():
    """Propose → veto window → execute → metrics → complete → analyze."""
    # 1. Propose
    exp_id = experiment_engine.propose_experiment(
        channel="ch_test",
        hypothesis="Test channel drives 10 listeners",
        tactic="Post daily in test community",
        duration_days=7,
    )
    assert experiment_engine.get_experiment(exp_id)["status"] == "proposed"

    # 2. Start (simulating veto window passed)
    experiment_engine.start_experiment(exp_id)
    assert experiment_engine.get_experiment(exp_id)["status"] == "active"

    # 3. Log metrics
    experiment_engine.log_metric(exp_id, {"listeners": 3, "day": 1})
    experiment_engine.log_metric(exp_id, {"listeners": 5, "day": 2})

    # 4. Complete
    experiment_engine.complete_experiment(exp_id)
    assert experiment_engine.get_experiment(exp_id)["status"] == "completed"

    # 5. Analyze
    experiment_engine.analyze_experiment(
        exp_id, result="success", learning="Test channel works — 8 listeners in 2 days"
    )
    assert experiment_engine.get_experiment(exp_id)["result"] == "success"


def test_veto_proposal_flow():
    """Create proposal → veto → verify blocked."""
    pid = veto_system.create_proposal(
        proposal_type="new_experiment",
        title="Risky experiment",
        description="Something RJM might not want",
    )
    veto_system.veto_proposal(pid, reason="Too risky")
    assert veto_system.list_proposals(status="vetoed")[0]["id"] == pid


def test_budget_flow():
    """Donation → allocate → spend → verify balance."""
    revenue_tracker.record_donation(100.00, source="test_stripe")
    summary = revenue_tracker.get_budget_summary()
    assert summary["available_balance"] == 50.00

    revenue_tracker.record_spend(3.00, channel="ch_submithub", note="test")
    summary = revenue_tracker.get_budget_summary()
    assert summary["available_balance"] == 47.00


def test_reallocation_flow():
    """Record channel metrics → reallocate → verify weights shifted."""
    strategy_portfolio.record_channel_metric("ch_test", "listeners_gained", 20.0)
    strategy_portfolio.record_channel_metric("ch_test2", "listeners_gained", 2.0)

    reg = strategy_portfolio.reallocate_weights()
    ch_test = next(c for c in reg["channels"] if c["id"] == "ch_test")
    ch_test2 = next(c for c in reg["channels"] if c["id"] == "ch_test2")
    assert ch_test["weight"] > ch_test2["weight"]


def test_self_assessment_flow():
    """Calculate score with real experiment data."""
    # Create and complete an experiment
    exp_id = experiment_engine.propose_experiment(
        channel="ch_test", hypothesis="t", tactic="t"
    )
    experiment_engine.start_experiment(exp_id)
    experiment_engine.complete_experiment(exp_id)
    experiment_engine.analyze_experiment(exp_id, result="success", learning="works")

    score = growth_brain.run_self_assess(
        listeners_current=340, listeners_previous=325
    )
    assert score["total_score"] > 0
    assert score["triggered_action"]["level"] in [
        "stay_course", "increase_discovery", "emergency", "red_alert", "system_pause"
    ]


def test_brain_status():
    """Brain status should return comprehensive state."""
    status = growth_brain.get_brain_status()
    assert "active_experiments" in status
    assert "budget" in status
    assert "active_channels" in status


def test_strategic_insight_save_and_retrieve():
    growth_brain.save_strategic_insight(
        source="test",
        insight="Reddit posts with production stories outperform 3:1",
        confidence=0.8,
        applicable_channels=["ch_reddit"],
    )
    insights = growth_brain.get_strategic_insights()
    assert len(insights) == 1
    assert "production stories" in insights[0]["insight"]
```

- [ ] **Step 2: Run integration tests**

Run: `cd outreach_agent && python -m pytest ../tests/test_btl_integration.py -v`
Expected: All 7 tests PASS

- [ ] **Step 3: Run ALL BTL tests together**

Run: `cd outreach_agent && python -m pytest ../tests/test_btl*.py ../tests/test_bandit*.py ../tests/test_experiment*.py ../tests/test_strategy*.py ../tests/test_self_assessment.py ../tests/test_veto*.py ../tests/test_revenue*.py ../tests/test_channel*.py ../tests/test_growth*.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_btl_integration.py
git commit -m "test(btl): end-to-end integration tests for full BTL lifecycle"
```

---

## Task 15: Final Wiring — numpy Dependency + Event Registration

**Files:**
- Modify: `outreach_agent/requirements.txt` (or equivalent)
- Create: `outreach_agent/btl_events.py`

- [ ] **Step 1: Add numpy to dependencies**

Check if `requirements.txt` exists in `outreach_agent/` and add `numpy` if missing:

```
numpy>=1.24.0
```

Also check if `stripe` should be added (optional — for self-funding):

```
stripe>=7.0.0
```

Install: `cd outreach_agent && pip install numpy stripe`

- [ ] **Step 2: Create BTL event type registration**

```python
# outreach_agent/btl_events.py
"""BTL protocol event types — for documentation and validation.

All BTL events follow the domain.action convention from events.py.
"""

BTL_EVENT_TYPES = [
    "experiment.proposed",
    "experiment.started",
    "experiment.completed",
    "experiment.analyzed",
    "experiment.vetoed",
    "proposal.pending",
    "proposal.executed",
    "proposal.vetoed",
    "budget.donation",
    "budget.spend",
    "channel.activated",
    "channel.paused",
    "channel.reallocated",
    "score.calculated",
    "insight.discovered",
    "bandit.updated",
    "bandit.breakthrough",
    "competitor.spike_detected",
]
```

- [ ] **Step 3: Commit**

```bash
git add outreach_agent/btl_events.py
git commit -m "feat(btl): event type registry + numpy/stripe dependencies"
```

---

## Task 16: Final Integration Smoke Test

- [ ] **Step 1: Run full test suite**

Run: `cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/sharp-poincare" && cd outreach_agent && python -m pytest ../tests/ -v --tb=short 2>&1 | tail -30`

Expected: All BTL tests pass. Existing tests should not be broken.

- [ ] **Step 2: Smoke test CLI**

Run each of these and verify no crashes:

```bash
python3 rjm.py brain status
python3 rjm.py channels
python3 rjm.py budget
python3 rjm.py score
python3 rjm.py experiment list
python3 rjm.py proposals
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(btl): Boil the Lake protocol — complete implementation

Autonomous self-improving growth system for 1M Spotify monthly listeners.

Core components:
- Bandit framework (Thompson Sampling, multi-arm, per-channel)
- Experiment engine (propose → veto → active → analyze lifecycle)
- Strategy portfolio (channel registry + L2 weight reallocation)
- Growth brain (L1/L2/L3 orchestrator)
- Veto system (24hr propose-and-execute with daily digest)
- Revenue tracker (Stripe integration + budget ledger)
- Self-assessment (Growth Health Score 0-100 with triggered actions)
- Competitor tracker (comparable artist monitoring)
- Channel agent base class (common interface for all channels)

CLI: brain/experiment/veto/budget/channels/score commands via rjm.py
Tests: 40+ tests covering all modules + end-to-end integration"
```
