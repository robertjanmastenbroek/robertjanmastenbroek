"""Shared pytest fixtures for outreach_agent tests."""
import sys
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# Make outreach_agent importable as a flat module namespace
OUTREACH_DIR = Path(__file__).resolve().parents[1]
if str(OUTREACH_DIR) not in sys.path:
    sys.path.insert(0, str(OUTREACH_DIR))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point every module's DB_PATH at an isolated temp file and init schema.

    Several modules (`reply_responder`, `gmail_client`, etc.) cache `DB_PATH`
    at import time, so patching `config.DB_PATH` alone is not enough. We patch
    every module that exposes a `DB_PATH` attribute.
    """
    db_file = tmp_path / "test_outreach.db"
    import importlib
    import config
    monkeypatch.setattr(config, "DB_PATH", db_file)
    import db as _db
    importlib.reload(_db)
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()

    # Patch any other module that has cached its own DB_PATH
    for mod_name in ("reply_responder", "reply_detector", "reply_classifier",
                     "followup_engine", "learning", "contact_scorer",
                     "bounce", "master_agent"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, "DB_PATH"):
            monkeypatch.setattr(mod, "DB_PATH", db_file)
    yield _db


@pytest.fixture
def mock_gmail(monkeypatch):
    """Replace gmail_client module functions with MagicMocks."""
    import gmail_client
    mock_send = MagicMock(return_value={"id": "msg-test", "threadId": "thr-test"})
    mock_thread = MagicMock(return_value=[])
    mock_replied = MagicMock(return_value=False)
    mock_draft = MagicMock(return_value={"message": {"id": "draft-1", "threadId": "thr-1"}})
    monkeypatch.setattr(gmail_client, "send_email", mock_send)
    monkeypatch.setattr(gmail_client, "get_thread_messages", mock_thread)
    monkeypatch.setattr(gmail_client, "already_replied_in_thread", mock_replied)
    monkeypatch.setattr(gmail_client, "create_draft", mock_draft)
    return {"send": mock_send, "thread": mock_thread, "replied": mock_replied, "draft": mock_draft}


@pytest.fixture
def fake_claude(monkeypatch):
    """Replace _call_claude in every module that imported it with a canned responder.

    Several modules use `from template_engine import _call_claude`, which binds
    the function in their own namespace. Patching only `template_engine._call_claude`
    leaves those local references pointing at the real function, so we also patch
    the consumer modules directly.
    """
    import importlib
    import template_engine

    def _call(prompt, model=None, timeout=None):
        return (
            '{"subject":"Halleluyah 140 BPM Psytrance for your playlist",'
            '"body":"Your psytrance rotation sits at 138-142 BPM. Halleluyah is 140 BPM '
            'tribal psytrance recorded in Tenerife — Joshua 6 reference in the drop where '
            'the walls come down. https://open.spotify.com/artist/2Seaafm5k1hAuCkpdq7yds",'
            '"hooks_used":["bpm_match","genre_fallback"]}'
        )

    monkeypatch.setattr(template_engine, "_call_claude", _call)
    for mod_name in ("reply_responder", "reply_classifier", "learning", "followup_engine"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, "_call_claude"):
            monkeypatch.setattr(mod, "_call_claude", _call)
    return _call
