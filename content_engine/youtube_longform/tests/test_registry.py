"""
Tests for registry.py primary-link behavior + dedup.

Spotify-first default (2026-04-22 North-Star mandate):
  build_smart_link returns the per-track Spotify URL + UTM by default.
  Odesli / Feature.fm aggregators are only consulted when
  HOLYRAVE_PRIMARY_LINK=smart is set explicitly.

Opt-in "smart" mode priority:
  1. Feature.fm (if API key set and request succeeds)
  2. Odesli/Songlink (if request succeeds)
  3. Raw Spotify URL + UTM suffix (always works — final fallback)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from content_engine.youtube_longform import registry


def test_utm_suffix_formats_correctly():
    """UTM suffix uses consistent snake_case and track-slug campaign."""
    suffix = registry._utm_suffix("Fire In Our Hands")
    assert "utm_source=youtube" in suffix
    assert "utm_medium=holyrave_longform" in suffix
    assert "utm_campaign=hr_fire_in_our_hands" in suffix


def test_build_smart_link_default_is_spotify_direct(monkeypatch):
    """
    Default (no HOLYRAVE_PRIMARY_LINK env var): return the per-track
    Spotify URL + UTM with ZERO aggregator network calls. This is the
    2026-04-22 North-Star default — every click funnels to Spotify.
    """
    monkeypatch.delenv("HOLYRAVE_PRIMARY_LINK", raising=False)

    with patch("content_engine.youtube_longform.registry.requests.get") as mock_get, \
         patch("content_engine.youtube_longform.registry.requests.post") as mock_post:
        url = registry.build_smart_link("Jericho")

    # No network — we skip Odesli and Feature.fm entirely in Spotify mode.
    mock_get.assert_not_called()
    mock_post.assert_not_called()

    assert "open.spotify.com/track/2M7cL3KynPGzE1DonuldrN" in url
    assert "utm_source=youtube" in url
    assert "utm_campaign=hr_jericho" in url


def test_build_smart_link_falls_back_to_spotify_utm_when_no_services(monkeypatch):
    """
    Opt-in smart mode with no Feature.fm key + Odesli failing → final
    fallback is the UTM-suffixed per-track Spotify URL.
    """
    monkeypatch.setenv("HOLYRAVE_PRIMARY_LINK", "smart")
    monkeypatch.setattr("content_engine.youtube_longform.config.FEATUREFM_API_KEY", "")

    # Stub Odesli to fail
    def fail(*a, **kw):
        raise Exception("network")

    with patch("content_engine.youtube_longform.registry.requests.get", side_effect=fail):
        url = registry.build_smart_link("Jericho")

    # Post-2026-04-21: we emit the per-TRACK Spotify URL, not the artist
    # page, whenever a track URL is on file in audio_engine.TRACK_SPOTIFY_URLS.
    # Jericho is known (2M7cL3KynPGzE1DonuldrN) so the fallback is the track.
    assert "open.spotify.com/track/2M7cL3KynPGzE1DonuldrN" in url
    assert "utm_source=youtube" in url
    assert "utm_campaign=hr_jericho" in url


def test_build_smart_link_uses_odesli_when_available(monkeypatch):
    """Opt-in smart mode: Odesli returns pageUrl → returns that + UTM."""
    monkeypatch.setenv("HOLYRAVE_PRIMARY_LINK", "smart")
    monkeypatch.setattr("content_engine.youtube_longform.config.FEATUREFM_API_KEY", "")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"pageUrl": "https://song.link/s/fake123"}

    with patch("content_engine.youtube_longform.registry.requests.get", return_value=fake_response):
        url = registry.build_smart_link("Jericho")

    assert url.startswith("https://song.link/s/fake123")
    assert "utm_source=youtube" in url


def test_build_smart_link_prefers_featurefm(monkeypatch):
    """Opt-in smart mode: Feature.fm succeeds → returns its url without hitting Odesli."""
    monkeypatch.setenv("HOLYRAVE_PRIMARY_LINK", "smart")
    monkeypatch.setattr("content_engine.youtube_longform.config.FEATUREFM_API_KEY", "test-key")
    monkeypatch.setattr("content_engine.youtube_longform.config.FEATUREFM_ACCOUNT_ID", "")

    fake_ff = MagicMock()
    fake_ff.status_code = 201
    fake_ff.json.return_value = {"url": "https://ffm.to/holyrave-jericho"}

    with patch("content_engine.youtube_longform.registry.requests.post", return_value=fake_ff), \
         patch("content_engine.youtube_longform.registry.requests.get") as mock_get:
        url = registry.build_smart_link("Jericho")

    assert url == "https://ffm.to/holyrave-jericho"
    mock_get.assert_not_called()


def test_already_published_handles_missing_file(tmp_path, monkeypatch):
    """No registry file → None."""
    monkeypatch.setattr(
        "content_engine.youtube_longform.registry.REGISTRY_FILE",
        tmp_path / "nonexistent.jsonl",
    )
    assert registry.already_published("Jericho") is None


def test_already_published_matches_case_insensitive(tmp_path, monkeypatch):
    """Title lookups normalize case + whitespace."""
    path = tmp_path / "reg.jsonl"
    path.write_text(
        '{"track_title": "Jericho", "youtube_id": "abc123", "dry_run": false, "error": null}\n'
    )
    monkeypatch.setattr("content_engine.youtube_longform.registry.REGISTRY_FILE", path)
    assert registry.already_published("JERICHO") is not None
    assert registry.already_published("jericho ") is not None


def test_count_today_excludes_dry_runs_and_errors(tmp_path, monkeypatch):
    """Only successful non-dry-run publishes are counted."""
    from datetime import datetime, timezone
    path = tmp_path / "reg.jsonl"
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        f'{{"timestamp": "{today}T12:00:00+00:00", "track_title": "A", "dry_run": false, "error": null, "youtube_id": "a"}}',
        f'{{"timestamp": "{today}T13:00:00+00:00", "track_title": "B", "dry_run": true,  "error": null, "youtube_id": null}}',
        f'{{"timestamp": "{today}T14:00:00+00:00", "track_title": "C", "dry_run": false, "error": "boom", "youtube_id": null}}',
        f'{{"timestamp": "{today}T15:00:00+00:00", "track_title": "D", "dry_run": false, "error": null, "youtube_id": "d"}}',
    ]
    path.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr("content_engine.youtube_longform.registry.REGISTRY_FILE", path)
    assert registry.count_today() == 2
