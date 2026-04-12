# tests/test_auth_monitor.py
import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'outreach_agent'))

import auth_monitor


def test_check_returns_dict():
    result = auth_monitor.run_check()
    assert isinstance(result, dict)
    assert "gmail" in result
    assert "instagram" in result


def test_check_gmail_key_has_status():
    result = auth_monitor.run_check()
    assert "status" in result["gmail"]
    assert result["gmail"]["status"] in ("ok", "missing", "expired", "error")


def test_check_instagram_key_has_status():
    result = auth_monitor.run_check()
    assert "status" in result["instagram"]
    assert result["instagram"]["status"] in ("ok", "missing", "error")


def test_check_instagram_missing_when_no_env(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    result = auth_monitor.run_check()
    assert result["instagram"]["status"] == "missing"


def test_format_summary_returns_string():
    result = auth_monitor.run_check()
    summary = auth_monitor.format_summary(result)
    assert isinstance(summary, str)
    assert "gmail" in summary.lower()
