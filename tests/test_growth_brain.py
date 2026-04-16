"""Tests for growth brain — the BTL orchestrator.

Smoke tests covering the four public layer entry points:

  * run_l1_optimize  — bandit refresh + breakthrough scan
  * run_l2_reallocate — channel weight reallocation
  * run_veto_check   — execute proposals past their veto window
  * get_brain_status — assemble a full state snapshot

We use a real temp-file DB (not `:memory:`) because db.get_conn() opens a
fresh sqlite connection on every call, and an in-memory database does not
persist across connections — writes from one connection would be invisible
to the next.

Test isolation note: ``setup_function`` re-pins ``db.DB_PATH`` /
``config.DB_PATH`` every test (matches the pattern in
test_experiment_engine / test_veto_system) so a sibling test module that
mutates these globals between cases can't poison this one.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# ── Isolate DB + registry per-module ─────────────────────────────────────────
# Use a real temp-file DB (not ``:memory:``) so connections opened in
# different ``with db.get_conn()`` blocks see the same data.
_tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tf.close()
os.environ["RJM_DB_PATH"] = _tf.name

# Provide a temp channel registry so reallocate_weights() doesn't touch the
# real one (and so the L2 test sees a deterministic empty portfolio).
_REGISTRY_PATH = Path(_tf.name + ".registry.json")
_REGISTRY_PATH.write_text(
    json.dumps({"channels": [], "last_reallocation": None})
)
os.environ["BTL_CHANNEL_REGISTRY_PATH"] = str(_REGISTRY_PATH)

# Re-route the score JSON file too so save_score() doesn't write into the
# real data dir during get_brain_status().
_SCORE_PATH = Path(_tf.name + ".growth_score.json")

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))

import config  # noqa: E402
config.DB_PATH = Path(os.environ["RJM_DB_PATH"])

import db  # noqa: E402

# Force ``db.DB_PATH`` (imported from config at module load time) to point
# at our temp file even if another test module already mutated it during
# pytest collection.
db.DB_PATH = Path(os.environ["RJM_DB_PATH"])

import btl_db  # noqa: E402

db.init_db()
btl_db.init_btl_tables()

import self_assessment  # noqa: E402
# Do NOT mutate self_assessment.SCORE_PATH at module load — sibling test
# modules (e.g. test_self_assessment.py, imported during pytest collection)
# pin SCORE_PATH at THEIR import time and would lose it if we clobbered the
# module-level binding here. We swap it in/out per-test instead via
# setup_function / teardown_function below.

from growth_brain import (  # noqa: E402
    run_l1_optimize,
    run_l2_reallocate,
    run_veto_check,
    get_brain_status,
)


# Hold the SCORE_PATH each test temporarily clobbers so we can restore it
# in teardown_function — keeps sibling test modules' own SCORE_PATH intact.
_PRIOR_SCORE_PATH = None


def setup_function():
    """Wipe BTL state between tests, re-pin DB path, swap SCORE_PATH.

    Re-pinning is required because sibling test modules (imported during
    pytest collection) may have mutated ``db.DB_PATH`` / ``config.DB_PATH``
    pointing at their own temp DBs. We capture whatever ``SCORE_PATH`` is
    currently set to and restore it in ``teardown_function``.
    """
    global _PRIOR_SCORE_PATH
    db.DB_PATH = Path(os.environ["RJM_DB_PATH"])
    config.DB_PATH = Path(os.environ["RJM_DB_PATH"])
    _PRIOR_SCORE_PATH = self_assessment.SCORE_PATH
    self_assessment.SCORE_PATH = _SCORE_PATH
    db.init_db()
    btl_db.init_btl_tables()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM proposals")
        conn.execute("DELETE FROM experiments")
        conn.execute("DELETE FROM channel_metrics")
        conn.execute("DELETE FROM bandit_state")
        conn.execute("DELETE FROM strategic_insights")
    if _SCORE_PATH.exists():
        _SCORE_PATH.unlink()


def teardown_function():
    """Restore ``self_assessment.SCORE_PATH`` after every test."""
    global _PRIOR_SCORE_PATH
    if _PRIOR_SCORE_PATH is not None:
        self_assessment.SCORE_PATH = _PRIOR_SCORE_PATH
    if _SCORE_PATH.exists():
        try:
            _SCORE_PATH.unlink()
        except OSError:
            pass


def teardown_module():
    """Remove the temp DB + sidecar files when the module finishes.

    Mirrors test_experiment_engine.teardown_module — only the files we
    created are removed; we do NOT shutil.rmtree a directory because
    sibling modules may have inherited our ``RJM_DB_PATH`` env var via
    module-level rebinding of ``db.DB_PATH`` and would crash trying to
    write to a deleted directory.
    """
    for path in (_tf.name, str(_REGISTRY_PATH), str(_SCORE_PATH)):
        try:
            os.unlink(path)
        except OSError:
            pass


# ─── L1 / L2 / veto / status smoke tests ────────────────────────────────────


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
