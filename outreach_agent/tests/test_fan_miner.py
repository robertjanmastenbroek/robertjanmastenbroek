"""Tests for fan_miner.py — deterministic paths only, no network."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_is_brand_safe_blocks_drug_ceremony():
    import fan_miner
    assert fan_miner._is_brand_safe("ayahuasca ceremony playlist") is False


def test_is_brand_safe_passes_music_fan():
    import fan_miner
    assert fan_miner._is_brand_safe("melodic techno blog newsletter weekly picks") is True
    assert fan_miner._is_brand_safe("psytrance community Burning Man contact") is True


def test_extract_emails_returns_valid():
    import fan_miner
    html = "subscribe: editor@technonewsletter.com or spam@sentry.io"
    emails = fan_miner._extract_emails(html)
    assert "editor@technonewsletter.com" in emails
    assert "spam@sentry.io" not in emails


def test_extract_emails_filters_noreply():
    import fan_miner
    html = "noreply@music.com or editor@music.com"
    emails = fan_miner._extract_emails(html)
    assert "noreply@music.com" not in emails
    assert "editor@music.com" in emails


def test_mine_dry_run_returns_dict(temp_db, monkeypatch):
    import fan_miner
    monkeypatch.setattr(fan_miner, "_ddg_search", lambda *a, **k: [
        {
            "title": "Melodic Techno Weekly Newsletter",
            "body": "Get weekly picks — contact editor@melodicweekly.com",
            "href": "https://melodicweekly.com",
        }
    ])
    result = fan_miner.mine(limit=1, dry_run=True)
    assert result["added"] == 1


def test_mine_skips_social_media_urls(temp_db, monkeypatch):
    import fan_miner
    monkeypatch.setattr(fan_miner, "_ddg_search", lambda *a, **k: [
        {
            "title": "Fan page",
            "body": "follow us",
            "href": "https://instagram.com/melodicfans",
        },
    ])
    result = fan_miner.mine(limit=5, dry_run=True)
    assert result["added"] == 0


def test_mine_skips_unsafe(temp_db, monkeypatch):
    import fan_miner
    monkeypatch.setattr(fan_miner, "_ddg_search", lambda *a, **k: [
        {
            "title": "Ayahuasca ceremony music",
            "body": "ayahuasca ceremony playlist blog@ritual.com",
            "href": "https://ritual.com",
        }
    ])
    result = fan_miner.mine(limit=5, dry_run=True)
    assert result["skipped_unsafe"] >= 1
    assert result["added"] == 0


def test_mine_skips_duplicate(temp_db, monkeypatch):
    import fan_miner
    import db

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("existing@blog.com", "Existing Blog", "curator", "verified"),
        )

    monkeypatch.setattr(fan_miner, "_ddg_search", lambda *a, **k: [
        {
            "title": "Some Blog",
            "body": "existing@blog.com",
            "href": "https://someblog.com",
        }
    ])
    result = fan_miner.mine(limit=5, dry_run=False)
    assert result["skipped_dup"] >= 1
    assert result["added"] == 0
