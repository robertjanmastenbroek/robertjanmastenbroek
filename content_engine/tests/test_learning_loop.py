# content_engine/tests/test_learning_loop.py
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from content_engine.learning_loop import (
    calculate_unified_weights,
    track_rotation_vote,
    update_template_lifecycle,
    _is_buffer_id,
    _refresh_instagram_token,
    _refresh_youtube_token,
)
from content_engine.types import UnifiedWeights


def test_calculate_unified_weights():
    """Weights should update via EMA."""
    old = UnifiedWeights.defaults()
    records = [
        {
            "format_type": "transitional", "hook_template_id": "save.wait_for_drop",
            "hook_mechanism": "dare", "visual_type": "b_roll", "platform": "instagram",
            "transitional_category": "nature", "track_title": "Jericho",
            "completion_rate": 0.8, "save_rate": 0.05, "scroll_stop_rate": 0.3,
        },
        {
            "format_type": "emotional", "hook_template_id": "save.for_you_if",
            "hook_mechanism": "save", "visual_type": "phone", "platform": "youtube",
            "transitional_category": "", "track_title": "Renamed",
            "completion_rate": 0.6, "save_rate": 0.02, "scroll_stop_rate": 0.2,
        },
    ]
    new_weights = calculate_unified_weights(records, old)
    assert isinstance(new_weights, UnifiedWeights)
    assert new_weights.updated != ""


def test_track_rotation_vote():
    pool = [
        {"title": "Jericho", "spotify_popularity": 60, "video_save_rate": 0.05},
        {"title": "Renamed", "spotify_popularity": 40, "video_save_rate": 0.02},
        {"title": "Halleluyah", "spotify_popularity": 50, "video_save_rate": 0.03},
        {"title": "Fire In Our Hands", "spotify_popularity": 45, "video_save_rate": 0.01},
    ]
    new_release = {"title": "New Track", "spotify_popularity": 55, "video_save_rate": 0.04}
    result = track_rotation_vote(pool, new_release, min_days=0)
    assert "action" in result
    assert result["action"] in ("swap", "keep", "add")


def test_update_template_lifecycle():
    template_scores = {
        "save.wait_for_drop": 2.0,   # top performer
        "save.for_you_if": 0.3,      # bottom performer
        "save.pov_driving": 1.0,     # neutral
    }
    result = update_template_lifecycle(template_scores, days_active=15)
    assert result["save.wait_for_drop"]["priority"] == 2.0
    assert result["save.for_you_if"]["priority"] == 0.3


# ─── Buffer ID detection ────────────────────────────────────────────────────────

class TestIsBufferId:
    """Buffer IDs are 24-char lowercase hex; real IG IDs are long numeric strings."""

    def test_buffer_id_detected(self):
        assert _is_buffer_id("69e1dc17bf79a8a2f2e4c743")

    def test_real_ig_id_not_buffer(self):
        assert not _is_buffer_id("18179953573388740")

    def test_empty_string_not_buffer(self):
        assert not _is_buffer_id("")

    def test_mixed_case_not_buffer(self):
        # Buffer IDs are lowercase only
        assert not _is_buffer_id("69E1DC17BF79A8A2F2E4C743")

    def test_short_hex_not_buffer(self):
        assert not _is_buffer_id("69e1dc17bf79a8a2")  # too short

    def test_youtube_video_id_not_buffer(self):
        assert not _is_buffer_id("Qgv-PYZ35iE")


# ─── Instagram token refresh in analytics path ─────────────────────────────────

class TestRefreshInstagramTokenLearningLoop:
    """_refresh_instagram_token (learning_loop) should update os.environ + .env."""

    def test_success_updates_environ(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "EAAnewtoken", "expires_in": 5184000}
        with patch("content_engine.learning_loop.requests.get", return_value=mock_resp), \
             patch("content_engine.learning_loop._write_env_token") as mock_write, \
             patch.dict(os.environ, {
                 "META_APP_ID": "12345",
                 "META_APP_SECRET": "secret",
                 "INSTAGRAM_ACCESS_TOKEN": "EAAoldtoken",
             }, clear=False):
            token = _refresh_instagram_token("EAAoldtoken")
            # Assertions inside with-block so patch.dict hasn't restored env yet
            assert token == "EAAnewtoken"
            assert os.environ["INSTAGRAM_ACCESS_TOKEN"] == "EAAnewtoken"
            mock_write.assert_called_once_with("INSTAGRAM_ACCESS_TOKEN", "EAAnewtoken")

    def test_network_error_returns_original(self):
        with patch("content_engine.learning_loop.requests.get", side_effect=ConnectionError("timeout")), \
             patch.dict(os.environ, {
                 "META_APP_ID": "12345",
                 "META_APP_SECRET": "secret",
             }, clear=False):
            token = _refresh_instagram_token("EAAoldtoken")
        assert token == "EAAoldtoken"

    def test_no_app_creds_returns_original(self):
        with patch.dict(os.environ, {"META_APP_ID": "", "META_APP_SECRET": ""}, clear=False):
            token = _refresh_instagram_token("EAAoldtoken")
        assert token == "EAAoldtoken"

    def test_empty_token_returns_empty(self):
        with patch.dict(os.environ, {"INSTAGRAM_ACCESS_TOKEN": ""}, clear=False):
            token = _refresh_instagram_token("")
        assert token == ""


# ─── YouTube token refresh in analytics path ───────────────────────────────────

class TestRefreshYouTubeTokenLearningLoop:
    """_refresh_youtube_token (learning_loop) should persist to os.environ + .env."""

    def test_success_updates_environ_and_env_file(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "ya29.FRESH"}
        with patch("content_engine.learning_loop.requests.post", return_value=mock_resp), \
             patch("content_engine.learning_loop._write_env_token") as mock_write, \
             patch.dict(os.environ, {
                 "YOUTUBE_REFRESH_TOKEN": "1//refresh",
                 "YOUTUBE_CLIENT_ID": "client-id",
                 "YOUTUBE_CLIENT_SECRET": "client-secret",
                 "YOUTUBE_OAUTH_TOKEN": "ya29.OLD",
             }, clear=False):
            token = _refresh_youtube_token()
            # Assertions inside with-block so patch.dict hasn't restored env yet
            assert token == "ya29.FRESH"
            assert os.environ["YOUTUBE_OAUTH_TOKEN"] == "ya29.FRESH"
            mock_write.assert_called_once_with("YOUTUBE_OAUTH_TOKEN", "ya29.FRESH")

    def test_no_refresh_token_returns_existing(self):
        with patch.dict(os.environ, {
            "YOUTUBE_REFRESH_TOKEN": "",
            "YOUTUBE_OAUTH_TOKEN": "ya29.CURRENT",
        }, clear=False):
            token = _refresh_youtube_token()
        assert token == "ya29.CURRENT"

    def test_failed_refresh_returns_existing(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "invalid_grant"}
        with patch("content_engine.learning_loop.requests.post", return_value=mock_resp), \
             patch.dict(os.environ, {
                 "YOUTUBE_REFRESH_TOKEN": "1//refresh",
                 "YOUTUBE_CLIENT_ID": "cid",
                 "YOUTUBE_CLIENT_SECRET": "csec",
                 "YOUTUBE_OAUTH_TOKEN": "ya29.CURRENT",
             }, clear=False):
            token = _refresh_youtube_token()
        assert token == "ya29.CURRENT"


# ─── IG metrics use views-only (no plays) ──────────────────────────────────────

class TestInstagramMetricsApiCall:
    """fetch_instagram_metrics must not include 'plays' in the metric param."""

    def test_plays_not_in_request(self):
        from content_engine.learning_loop import fetch_instagram_metrics
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [
            {"name": "views", "values": [{"value": 500}]},
            {"name": "reach", "values": [{"value": 400}]},
            {"name": "saved", "values": [{"value": 10}]},
            {"name": "shares", "values": [{"value": 5}]},
            {"name": "total_interactions", "values": [{"value": 20}]},
        ]}
        posts = [{"post_id": "18179953573388740", "clip_index": 0, "variant": "a",
                  "hook_mechanism": "tension", "visual_type": "b_roll", "clip_length": 22}]
        with patch("content_engine.learning_loop.requests.get", return_value=mock_resp) as mock_get:
            fetch_instagram_metrics(posts, "EAAtoken")
        call_kwargs = mock_get.call_args
        metric_str = call_kwargs.kwargs.get("params", {}).get("metric", "") or \
                     (call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}).get("metric", "")
        # Check via the actual call arguments
        all_args = str(mock_get.call_args)
        assert "plays" not in all_args or "views" in all_args
        assert "plays" not in (mock_get.call_args.kwargs.get("params") or {}).get("metric", "")


# ─── IG story metrics use new API surface ──────────────────────────────────────

class TestInstagramStoryMetricsApiCall:
    """fetch_instagram_story_metrics must use reach,replies,views,navigation."""

    def test_correct_metrics_requested(self):
        from content_engine.learning_loop import fetch_instagram_story_metrics
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [
            {"name": "reach", "values": [{"value": 22}]},
            {"name": "replies", "values": [{"value": 0}]},
            {"name": "views", "values": [{"value": 31}]},
            {"name": "navigation", "values": [{"value": 5}]},
        ]}
        posts = [{"post_id": "18043191509782285", "clip_index": 0, "variant": "a",
                  "hook_mechanism": "tension", "visual_type": "b_roll", "clip_length": 15}]
        with patch("content_engine.learning_loop.requests.get", return_value=mock_resp) as mock_get:
            fetch_instagram_story_metrics(posts, "EAAtoken")
        metric_str = (mock_get.call_args.kwargs.get("params") or {}).get("metric", "")
        assert "taps_forward" not in metric_str
        assert "exits" not in metric_str
        assert "impressions" not in metric_str
        assert "navigation" in metric_str
        assert "views" in metric_str


# ─── Unresolved Buffer IDs skipped cleanly ────────────────────────────────────

class TestBufferIdSkip:
    """Unresolved Buffer IDs must be skipped before making an insights API call."""

    def test_ig_metrics_skips_buffer_id(self):
        from content_engine.learning_loop import fetch_instagram_metrics
        posts = [{"post_id": "69e1dc88ae36470f58bac9d0", "clip_index": 0, "variant": "a",
                  "hook_mechanism": "tension", "visual_type": "b_roll", "clip_length": 22}]
        with patch("content_engine.learning_loop.requests.get") as mock_get:
            result = fetch_instagram_metrics(posts, "EAAtoken")
        mock_get.assert_not_called()
        assert result == []

    def test_ig_story_metrics_skips_buffer_id(self):
        from content_engine.learning_loop import fetch_instagram_story_metrics
        posts = [{"post_id": "69e1dccebf79a8a2f2e4ca0f", "clip_index": 0, "variant": "a",
                  "hook_mechanism": "tension", "visual_type": "b_roll", "clip_length": 15}]
        with patch("content_engine.learning_loop.requests.get") as mock_get:
            result = fetch_instagram_story_metrics(posts, "EAAtoken")
        mock_get.assert_not_called()
        assert result == []

    def test_ig_metrics_real_id_still_called(self):
        """Real IG numeric IDs should still trigger an API call."""
        from content_engine.learning_loop import fetch_instagram_metrics
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [
            {"name": "views", "values": [{"value": 500}]},
            {"name": "reach", "values": [{"value": 400}]},
            {"name": "saved", "values": [{"value": 10}]},
            {"name": "shares", "values": [{"value": 5}]},
            {"name": "total_interactions", "values": [{"value": 20}]},
        ]}
        posts = [{"post_id": "18179953573388740", "clip_index": 0, "variant": "a",
                  "hook_mechanism": "tension", "visual_type": "b_roll", "clip_length": 22}]
        with patch("content_engine.learning_loop.requests.get", return_value=mock_resp) as mock_get:
            result = fetch_instagram_metrics(posts, "EAAtoken")
        mock_get.assert_called_once()
        assert len(result) == 1


# ─── YouTube bulk channel fetch ───────────────────────────────────────────────

class TestYouTubeChannelBulkFetch:
    """fetch_youtube_channel_metrics_bulk should query all videos, 28-day window."""

    def test_bulk_returns_records_for_all_rows(self):
        from content_engine.learning_loop import fetch_youtube_channel_metrics_bulk
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "rows": [
                ["p8NtdfXEtNM", 1018, 85, 10, 66.6],
                ["EnOqG14A-9g", 946, 96, 13, 50.5],
            ]
        }
        with patch("content_engine.learning_loop.requests.get", return_value=mock_resp):
            records = fetch_youtube_channel_metrics_bulk("ya29.token", days_back=28)
        assert len(records) == 2
        assert records[0].post_id == "p8NtdfXEtNM"
        assert records[0].views == 1018
        assert records[0].platform == "youtube"
        assert records[0].completion_rate == round(66.6 / 100, 4)

    def test_bulk_uses_no_filter(self):
        """Must not include a 'filters' param so all channel videos are returned."""
        from content_engine.learning_loop import fetch_youtube_channel_metrics_bulk
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"rows": []}
        with patch("content_engine.learning_loop.requests.get", return_value=mock_resp) as mock_get:
            fetch_youtube_channel_metrics_bulk("ya29.token", days_back=28)
        params = mock_get.call_args.kwargs.get("params", {}) or {}
        assert "filters" not in params or params.get("filters", "") == ""

    def test_bulk_enriches_with_registry_metadata(self):
        """Videos in registry_lookup should get hook/format metadata attached."""
        from content_engine.learning_loop import fetch_youtube_channel_metrics_bulk
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "rows": [["Qgv-PYZ35iE", 50, 5, 22, 80.0]]
        }
        registry = {
            "Qgv-PYZ35iE": {
                "hook_mechanism": "tension", "visual_type": "b_roll",
                "clip_length": 22, "format_type": "transitional",
            }
        }
        with patch("content_engine.learning_loop.requests.get", return_value=mock_resp):
            records = fetch_youtube_channel_metrics_bulk("ya29.token", registry_lookup=registry)
        assert records[0].hook_mechanism == "tension"
        assert records[0].clip_length == 22

    def test_bulk_api_error_returns_empty(self):
        from content_engine.learning_loop import fetch_youtube_channel_metrics_bulk
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        with patch("content_engine.learning_loop.requests.get", return_value=mock_resp):
            records = fetch_youtube_channel_metrics_bulk("ya29.stale")
        assert records == []


# ─── YouTube per-ID batch uses 28-day window ─────────────────────────────────

class TestYouTubeMetricsBatchWindow:
    """fetch_youtube_metrics must use a 28-day window, not today-only."""

    def test_start_date_is_not_today(self):
        from content_engine.learning_loop import fetch_youtube_metrics
        from datetime import date, timedelta
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"rows": []}
        posts = [{"post_id": "Qgv-PYZ35iE", "clip_index": 0, "variant": "a",
                  "hook_mechanism": "tension", "visual_type": "b_roll", "clip_length": 22}]
        with patch("content_engine.learning_loop.requests.get", return_value=mock_resp) as mock_get:
            fetch_youtube_metrics(posts, "ya29.token")
        params = mock_get.call_args.kwargs.get("params", {}) or {}
        start = params.get("startDate", "")
        assert start != date.today().isoformat(), "startDate must not be today-only"
        # Should be roughly 28 days back
        start_dt = date.fromisoformat(start)
        delta = (date.today() - start_dt).days
        assert 25 <= delta <= 31, f"Expected ~28 day window, got {delta}d"
