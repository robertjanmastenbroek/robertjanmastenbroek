# content_engine/tests/test_spotify_watcher.py
import pytest
from content_engine.spotify_watcher import (
    fetch_new_releases,
    fetch_track_popularity,
    fetch_audio_features,
    _get_user_token,
)


def test_fetch_new_releases_returns_list():
    """Should return a list (empty if no API key)."""
    result = fetch_new_releases()
    assert isinstance(result, list)


def test_fetch_track_popularity_returns_int():
    """Returns 0 if no API access."""
    result = fetch_track_popularity("fake_track_id")
    assert isinstance(result, int)
    assert 0 <= result <= 100


def test_fetch_audio_features_returns_dict():
    result = fetch_audio_features("fake_track_id")
    assert isinstance(result, dict)
    assert "bpm" in result
    assert "energy" in result
