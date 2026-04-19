"""Tests for church_miner.py — deterministic paths only, no network."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_is_brand_safe_blocks_occult():
    import church_miner
    assert church_miner._is_brand_safe("wicca ritual ceremony") is False
    assert church_miner._is_brand_safe("ayahuasca ceremony DJ booking") is False


def test_is_brand_safe_passes_christian():
    import church_miner
    assert church_miner._is_brand_safe("youth church electronic worship Berlin") is True
    assert church_miner._is_brand_safe("Jugendkirche techno Gottesdienst") is True


def test_extract_emails_filters_bad_domains():
    import church_miner
    html = "contact us at spam@sentry.io or book@realchurch.de"
    emails = church_miner._extract_emails(html)
    assert "spam@sentry.io" not in emails
    assert "book@realchurch.de" in emails


def test_extract_emails_filters_bad_prefixes():
    import church_miner
    html = "Write noreply@church.de or pastor@church.de"
    emails = church_miner._extract_emails(html)
    assert "noreply@church.de" not in emails
    assert "pastor@church.de" in emails


def test_mine_dry_run_returns_dict(temp_db, monkeypatch):
    import church_miner
    monkeypatch.setattr(church_miner, "_ddg_search", lambda *a, **k: [
        {
            "title": "Jugendkirche Köln",
            "body": "book@jugendkirche-koeln.de",
            "href": "https://jugendkirche-koeln.de",
        }
    ])
    result = church_miner.mine(limit=1, dry_run=True)
    assert "added" in result
    assert result["added"] == 1


def test_mine_skips_unsafe(temp_db, monkeypatch):
    import church_miner
    monkeypatch.setattr(church_miner, "_ddg_search", lambda *a, **k: [
        {
            "title": "Wicca ceremony DJ",
            "body": "pagan@witchcircle.org",
            "href": "https://witchcircle.org",
        }
    ])
    result = church_miner.mine(limit=5, dry_run=True)
    assert result["skipped_unsafe"] >= 1
    assert result["added"] == 0


def test_mine_skips_social_media_urls(temp_db, monkeypatch):
    import church_miner
    monkeypatch.setattr(church_miner, "_ddg_search", lambda *a, **k: [
        {
            "title": "Youth Church",
            "body": "follow us",
            "href": "https://instagram.com/youthchurch",
        }
    ])
    result = church_miner.mine(limit=5, dry_run=True)
    assert result["added"] == 0


def test_mine_skips_duplicate(temp_db, monkeypatch):
    import church_miner
    import db

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("existing@church.de", "Existing Church", "curator", "verified"),
        )

    monkeypatch.setattr(church_miner, "_ddg_search", lambda *a, **k: [
        {
            "title": "Some Church",
            "body": "existing@church.de",
            "href": "https://somechurch.de",
        }
    ])
    result = church_miner.mine(limit=5, dry_run=False)
    assert result["skipped_dup"] >= 1
    assert result["added"] == 0
