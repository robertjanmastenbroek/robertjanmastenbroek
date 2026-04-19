"""Tests for bandcamp_miner.py — no network, no DDG."""
import json
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Unit tests for parsing helpers ───────────────────────────────────────────

def test_extract_emails_finds_mailto():
    import bandcamp_miner
    html = '<a href="mailto:artist@example.org">Contact me</a>'
    emails = bandcamp_miner._extract_emails(html)
    assert "artist@example.org" in emails


def test_extract_emails_filters_bad_domain():
    import bandcamp_miner
    html = "reach me at noreply@bandcamp.com or book@myband.com"
    emails = bandcamp_miner._extract_emails(html)
    assert "noreply@bandcamp.com" not in emails
    assert "book@myband.com" in emails


def test_extract_emails_filters_noreply_prefix():
    import bandcamp_miner
    html = "noreply@myband.com or hello@myband.com"
    emails = bandcamp_miner._extract_emails(html)
    assert "noreply@myband.com" not in emails
    assert "hello@myband.com" in emails


def test_is_brand_safe_blocks_unsafe():
    import bandcamp_miner
    assert bandcamp_miner._is_brand_safe("ayahuasca ceremony music") is False
    assert bandcamp_miner._is_brand_safe("wicca ritual DJ set") is False


def test_is_brand_safe_passes_music():
    import bandcamp_miner
    assert bandcamp_miner._is_brand_safe("melodic techno artist Tenerife") is True
    assert bandcamp_miner._is_brand_safe("psytrance producer organic sound") is True


def test_get_artist_urls_from_tag_page_json_blob(monkeypatch):
    import bandcamp_miner

    # Simulate data-blob JSON with two artist items
    blob_data = {
        "hub": {
            "items": [
                {"band_url": "https://deepecho.bandcamp.com", "artist": "Deep Echo"},
                {"band_url": "https://tribalvibe.bandcamp.com", "artist": "Tribal Vibe"},
                {"band_url": "https://store.bandcamp.com", "artist": "Store"},  # should be skipped
            ]
        }
    }
    blob_json = json.dumps(blob_data).replace('"', "&quot;")
    fake_html = f'<div id="pagedata" data-blob="{blob_json}"></div>'

    monkeypatch.setattr(bandcamp_miner, "_fetch", lambda url: fake_html)

    urls = bandcamp_miner._get_artist_urls_from_tag_page("melodic-techno", page=1)
    assert "https://deepecho.bandcamp.com/" in urls
    assert "https://tribalvibe.bandcamp.com/" in urls
    # Platform subdomain must be filtered out
    assert "https://store.bandcamp.com/" not in urls


def test_get_artist_urls_from_tag_page_raw_json_fallback(monkeypatch):
    import bandcamp_miner

    # No data-blob, but raw JSON key present in HTML
    fake_html = '''
    <script>
    var data = {"band_url":"https://sacredgroove.bandcamp.com","artist":"Sacred Groove"};
    </script>
    '''
    monkeypatch.setattr(bandcamp_miner, "_fetch", lambda url: fake_html)

    urls = bandcamp_miner._get_artist_urls_from_tag_page("psytrance", page=1)
    assert "https://sacredgroove.bandcamp.com/" in urls


def test_get_artist_urls_from_tag_page_href_fallback(monkeypatch):
    import bandcamp_miner

    # No JSON at all — artist URLs only in href attributes
    fake_html = '''
    <a href="https://desertecho.bandcamp.com/album/fire-sessions">Fire Sessions</a>
    <a href="https://daily.bandcamp.com/news">Daily</a>
    '''
    monkeypatch.setattr(bandcamp_miner, "_fetch", lambda url: fake_html)

    urls = bandcamp_miner._get_artist_urls_from_tag_page("tribal", page=1)
    assert "https://desertecho.bandcamp.com/" in urls
    assert "https://daily.bandcamp.com/" not in urls


def test_get_artist_urls_deduplicates(monkeypatch):
    import bandcamp_miner

    blob_data = {
        "hub": {
            "items": [
                {"band_url": "https://dupeartist.bandcamp.com"},
                {"band_url": "https://dupeartist.bandcamp.com"},  # duplicate
            ]
        }
    }
    blob_json = json.dumps(blob_data).replace('"', "&quot;")
    fake_html = f'<div id="pagedata" data-blob="{blob_json}"></div>'
    monkeypatch.setattr(bandcamp_miner, "_fetch", lambda url: fake_html)

    urls = bandcamp_miner._get_artist_urls_from_tag_page("melodic-techno")
    assert urls.count("https://dupeartist.bandcamp.com/") == 1


def test_get_artist_name_from_title(monkeypatch):
    import bandcamp_miner
    html = "<title>Ancient Desert | Free Music Download on Bandcamp</title>"
    name = bandcamp_miner._get_artist_name(html, "https://ancientdesert.bandcamp.com/")
    assert name == "Ancient Desert"


def test_get_artist_name_fallback_to_subdomain(monkeypatch):
    import bandcamp_miner
    html = "<title>something weird without pipe</title>"
    name = bandcamp_miner._get_artist_name(html, "https://my-artist.bandcamp.com/")
    assert "My Artist" in name or "my-artist" in name.lower()


# ── Integration-style tests using temp_db ────────────────────────────────────

def test_mine_adds_contact(temp_db, monkeypatch):
    import bandcamp_miner

    blob_data = {
        "hub": {
            "items": [{"band_url": "https://newartist.bandcamp.com", "artist": "New Artist"}]
        }
    }
    blob_json = json.dumps(blob_data).replace('"', "&quot;")
    tag_html = f'<div id="pagedata" data-blob="{blob_json}"></div>'
    artist_html = '<title>New Artist | Bandcamp</title><a href="mailto:new@artist.com">Contact</a>'

    def fake_fetch(url):
        if "bandcamp.com/tag" in url:
            return tag_html
        if "newartist.bandcamp.com" in url:
            return artist_html
        return ""

    monkeypatch.setattr(bandcamp_miner, "_fetch", fake_fetch)
    monkeypatch.setattr(bandcamp_miner, "time", type("t", (), {"sleep": staticmethod(lambda s: None)})())

    result = bandcamp_miner.mine(tag="melodic-techno", limit=5, dry_run=False)
    assert result["added"] >= 1


def test_mine_skips_unsafe_artist(temp_db, monkeypatch):
    import bandcamp_miner

    blob_data = {
        "hub": {
            "items": [{"band_url": "https://ritualartist.bandcamp.com"}]
        }
    }
    blob_json = json.dumps(blob_data).replace('"', "&quot;")
    tag_html = f'<div id="pagedata" data-blob="{blob_json}"></div>'
    artist_html = "<title>Ritual | Bandcamp</title>ayahuasca ceremony music for healing"

    def fake_fetch(url):
        if "bandcamp.com/tag" in url:
            return tag_html
        return artist_html

    monkeypatch.setattr(bandcamp_miner, "_fetch", fake_fetch)
    monkeypatch.setattr(bandcamp_miner, "time", type("t", (), {"sleep": staticmethod(lambda s: None)})())

    result = bandcamp_miner.mine(tag="melodic-techno", limit=5, dry_run=False)
    assert result["skipped_unsafe"] >= 1
    assert result["added"] == 0


def test_mine_skips_duplicate_email(temp_db, monkeypatch):
    import bandcamp_miner
    import db

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("existing@artist.com", "Existing Artist", "curator", "verified"),
        )

    blob_data = {
        "hub": {"items": [{"band_url": "https://existingartist.bandcamp.com"}]}
    }
    blob_json = json.dumps(blob_data).replace('"', "&quot;")
    tag_html = f'<div id="pagedata" data-blob="{blob_json}"></div>'
    artist_html = '<title>Existing Artist | Bandcamp</title><a href="mailto:existing@artist.com">email</a>'

    def fake_fetch(url):
        if "bandcamp.com/tag" in url:
            return tag_html
        return artist_html

    monkeypatch.setattr(bandcamp_miner, "_fetch", fake_fetch)
    monkeypatch.setattr(bandcamp_miner, "time", type("t", (), {"sleep": staticmethod(lambda s: None)})())

    result = bandcamp_miner.mine(tag="melodic-techno", limit=5, dry_run=False)
    assert result["skipped_dup"] >= 1
    assert result["added"] == 0


def test_mine_skips_no_email(temp_db, monkeypatch):
    import bandcamp_miner

    blob_data = {
        "hub": {"items": [{"band_url": "https://noemailartist.bandcamp.com"}]}
    }
    blob_json = json.dumps(blob_data).replace('"', "&quot;")
    tag_html = f'<div id="pagedata" data-blob="{blob_json}"></div>'
    artist_html = "<title>Silent Artist | Bandcamp</title><p>No contact info here.</p>"

    def fake_fetch(url):
        if "bandcamp.com/tag" in url:
            return tag_html
        return artist_html

    monkeypatch.setattr(bandcamp_miner, "_fetch", fake_fetch)
    monkeypatch.setattr(bandcamp_miner, "time", type("t", (), {"sleep": staticmethod(lambda s: None)})())

    result = bandcamp_miner.mine(tag="melodic-techno", limit=5, dry_run=False)
    assert result["skipped_no_email"] >= 1
    assert result["added"] == 0


def test_mine_dry_run_does_not_write(temp_db, monkeypatch):
    import bandcamp_miner
    import db

    blob_data = {
        "hub": {"items": [{"band_url": "https://dryrunartist.bandcamp.com"}]}
    }
    blob_json = json.dumps(blob_data).replace('"', "&quot;")
    tag_html = f'<div id="pagedata" data-blob="{blob_json}"></div>'
    artist_html = '<title>Dry Run Artist | Bandcamp</title><a href="mailto:dry@run.com">email</a>'

    def fake_fetch(url):
        if "bandcamp.com/tag" in url:
            return tag_html
        return artist_html

    monkeypatch.setattr(bandcamp_miner, "_fetch", fake_fetch)
    monkeypatch.setattr(bandcamp_miner, "time", type("t", (), {"sleep": staticmethod(lambda s: None)})())

    result = bandcamp_miner.mine(tag="melodic-techno", limit=5, dry_run=True)
    assert result["added"] >= 1

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM contacts WHERE email=?", ("dry@run.com",)
        ).fetchone()
    assert row is None  # dry_run: nothing written


def test_mine_skips_already_processed(temp_db, monkeypatch):
    import bandcamp_miner

    # Pre-mark the artist as processed
    with bandcamp_miner.db.get_conn() as conn:
        conn.execute(
            "INSERT INTO discovery_log (search_query, contact_type, results_found, searched_at) "
            "VALUES ('bandcamp:artist:https://oldartist.bandcamp.com/', 'genre_fan', 0, datetime('now'))"
        )

    blob_data = {
        "hub": {"items": [{"band_url": "https://oldartist.bandcamp.com"}]}
    }
    blob_json = json.dumps(blob_data).replace('"', "&quot;")
    tag_html = f'<div id="pagedata" data-blob="{blob_json}"></div>'

    call_count = []

    def fake_fetch(url):
        call_count.append(url)
        if "bandcamp.com/tag" in url:
            return tag_html
        return ""

    monkeypatch.setattr(bandcamp_miner, "_fetch", fake_fetch)
    monkeypatch.setattr(bandcamp_miner, "time", type("t", (), {"sleep": staticmethod(lambda s: None)})())

    result = bandcamp_miner.mine(tag="melodic-techno", limit=5, dry_run=False)
    assert result["skipped_processed"] >= 1
    # The artist page should NOT have been fetched
    assert not any("oldartist.bandcamp.com" in u for u in call_count if "tag" not in u)
