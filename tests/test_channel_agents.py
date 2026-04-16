"""Tests for the BTL channel agent base class + registry."""
import os
import sys
import shutil
import tempfile
from pathlib import Path

# Set up isolated DB BEFORE importing the project's modules.
# (`:memory:` doesn't work — db.get_conn() opens a fresh connection each time.)
_tmpdir = tempfile.mkdtemp()
_db_path = str(Path(_tmpdir) / "test_channel_agents.db")
os.environ["RJM_DB_PATH"] = _db_path

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))

import config  # noqa: E402
config.DB_PATH = Path(_db_path)

import db  # noqa: E402
db.DB_PATH = Path(_db_path)  # rebind: db.py captured DB_PATH at import time
import btl_db  # noqa: E402
from channel_agents import (  # noqa: E402
    ChannelAgent,
    get_agent,
    list_agents,
    _REGISTRY,
)


def setup_module(_module):
    db.init_db()
    btl_db.init_btl_tables()


def teardown_module(_module):
    shutil.rmtree(_tmpdir, ignore_errors=True)


def setup_function(_function):
    """Clear registry between tests so registration assertions stay clean."""
    _REGISTRY.clear()


# ─── Mock agent fixture ───────────────────────────────────────────────────────

class MockAgent(ChannelAgent):
    channel_id = "ch_mock"
    arms = {"style": ["bold", "subtle"]}

    def can_run(self) -> bool:
        return True

    def execute(self, config: dict) -> dict:
        return {"posts": 1, "impressions": 100}

    def get_metrics(self, days: int = 7) -> dict:
        return {"listeners_gained": 5}


# ─── 1. Base-class interface ──────────────────────────────────────────────────

def test_base_class_interface():
    """Subclass implements the three abstract methods correctly."""
    agent = MockAgent()
    assert agent.can_run() is True
    result = agent.execute({})
    assert isinstance(result, dict)
    assert "posts" in result
    assert result["posts"] == 1


# ─── 2. Auto-registration on construction ─────────────────────────────────────

def test_agent_registration():
    """Constructing a ChannelAgent subclass registers it under its channel_id."""
    assert _REGISTRY == {}  # cleared by setup_function
    agent = MockAgent()
    agents = list_agents()
    assert agent in agents
    assert any(a.channel_id == "ch_mock" for a in agents)


# ─── 3. Lookup by channel_id ──────────────────────────────────────────────────

def test_get_agent_by_id():
    """get_agent() returns the registered instance for a known channel_id."""
    agent = MockAgent()
    fetched = get_agent("ch_mock")
    assert fetched is agent
    # Unknown id → None.
    assert get_agent("ch_does_not_exist") is None


# ─── 4. Bandit integration ────────────────────────────────────────────────────

def test_bandit_integration():
    """select_arms() returns one valid value per declared arm dimension."""
    agent = MockAgent()
    selection = agent.select_arms()
    assert isinstance(selection, dict)
    assert "style" in selection
    assert selection["style"] in ["bold", "subtle"]
