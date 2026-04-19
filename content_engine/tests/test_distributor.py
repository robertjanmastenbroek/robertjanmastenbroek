# content_engine/tests/test_distributor.py
import os
import pytest
from unittest.mock import patch, MagicMock
from content_engine.distributor import (
    DISTRIBUTION_TARGETS,
    POST_SCHEDULE,
    _scheduled_at_utc,
    _is_auth_error,
    _log_native_failure,
    CircuitBreaker,
)


def test_distribution_targets():
    expected = {"instagram", "youtube", "facebook", "tiktok", "instagram_story", "facebook_story"}
    assert set(DISTRIBUTION_TARGETS) == expected


def test_post_schedule_has_6_targets():
    for target in DISTRIBUTION_TARGETS:
        assert target in POST_SCHEDULE


def test_scheduled_at_utc():
    result = _scheduled_at_utc("instagram", 0)
    assert "T" in result  # ISO format


def test_circuit_breaker_init():
    cb = CircuitBreaker()
    assert not cb.is_open("instagram")


def test_circuit_breaker_trips_after_3():
    cb = CircuitBreaker(threshold=3)
    cb.record_failure("instagram")
    cb.record_failure("instagram")
    assert not cb.is_open("instagram")
    cb.record_failure("instagram")
    assert cb.is_open("instagram")


def test_circuit_breaker_reset():
    cb = CircuitBreaker(threshold=3)
    for _ in range(3):
        cb.record_failure("instagram")
    assert cb.is_open("instagram")
    cb.reset("instagram")
    assert not cb.is_open("instagram")


def test_circuit_breaker_success_resets():
    cb = CircuitBreaker(threshold=3)
    cb.record_failure("instagram")
    cb.record_failure("instagram")
    cb.record_success("instagram")
    assert not cb.is_open("instagram")


# ─── Token & auth error detection ──────────────────────────────────────────────

class TestIsAuthError:
    """_is_auth_error should distinguish OAuth failures from platform errors."""

    def test_oauth_phrase_detected(self):
        assert _is_auth_error({"error": "Invalid OAuth access token"})

    def test_access_token_phrase_detected(self):
        assert _is_auth_error({"error": "Cannot parse access token — bad format"})

    def test_code_190_detected(self):
        assert _is_auth_error({"error": "OAuthException code 190: token expired"})

    def test_missing_permission_detected(self):
        assert _is_auth_error({"error": "missing permission instagram_content_publish"})

    def test_platform_5xx_not_auth(self):
        assert not _is_auth_error({"error": "upstream connect error or disconnect/reset"})

    def test_upload_error_not_auth(self):
        assert not _is_auth_error({"error": "video upload failed: connection reset"})

    def test_empty_error_not_auth(self):
        assert not _is_auth_error({})

    def test_rate_limit_not_auth(self):
        assert not _is_auth_error({"error": "rate limit exceeded, retry after 60s"})


# ─── YouTube token refresh persists to env ─────────────────────────────────────

class TestRefreshYouTubeToken:
    """_refresh_youtube_token should update os.environ when it gets a new token."""

    def test_new_token_written_to_environ(self):
        from content_engine.distributor import _refresh_youtube_token
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "ya29.FRESH_TOKEN"}
        with patch("content_engine.distributor.requests.post", return_value=mock_resp), \
             patch("content_engine.distributor._atomic_env_update") as mock_env, \
             patch.dict(os.environ, {
                 "YOUTUBE_REFRESH_TOKEN": "1//refresh",
                 "YOUTUBE_CLIENT_ID": "client-id",
                 "YOUTUBE_CLIENT_SECRET": "client-secret",
             }, clear=False):
            token = _refresh_youtube_token()
            # Assertions inside with-block so patch.dict hasn't restored env yet
            assert token == "ya29.FRESH_TOKEN"
            assert os.environ.get("YOUTUBE_OAUTH_TOKEN") == "ya29.FRESH_TOKEN"
            mock_env.assert_called_once_with({"YOUTUBE_OAUTH_TOKEN": "ya29.FRESH_TOKEN"})

    def test_falls_back_to_existing_on_network_error(self):
        from content_engine.distributor import _refresh_youtube_token
        with patch("content_engine.distributor.requests.post", side_effect=ConnectionError("timeout")), \
             patch.dict(os.environ, {
                 "YOUTUBE_REFRESH_TOKEN": "1//refresh",
                 "YOUTUBE_CLIENT_ID": "client-id",
                 "YOUTUBE_CLIENT_SECRET": "client-secret",
                 "YOUTUBE_OAUTH_TOKEN": "ya29.OLD_TOKEN",
             }, clear=False):
            token = _refresh_youtube_token()
        assert token == "ya29.OLD_TOKEN"

    def test_no_refresh_token_returns_existing(self):
        from content_engine.distributor import _refresh_youtube_token
        with patch.dict(os.environ, {"YOUTUBE_REFRESH_TOKEN": "", "YOUTUBE_OAUTH_TOKEN": "ya29.CURRENT"}, clear=False):
            token = _refresh_youtube_token()
        assert token == "ya29.CURRENT"


# ─── Instagram token refresh ───────────────────────────────────────────────────

class TestRefreshInstagramToken:
    """refresh_instagram_token should update os.environ and .env on success."""

    def test_new_token_written_to_environ(self):
        from content_engine.distributor import refresh_instagram_token
        import content_engine.distributor as dist_mod
        # Reset dead flag for clean test
        dist_mod._INSTAGRAM_TOKEN_DEAD = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "EAAnewtoken", "expires_in": 5184000}
        with patch("content_engine.distributor.requests.get", return_value=mock_resp), \
             patch("content_engine.distributor._atomic_env_update") as mock_env, \
             patch.dict(os.environ, {
                 "INSTAGRAM_ACCESS_TOKEN": "EAAoldtoken",
                 "META_APP_ID": "12345",
                 "META_APP_SECRET": "secret",
             }, clear=False):
            token = refresh_instagram_token("EAAoldtoken")
            # Assertions inside with-block so patch.dict hasn't restored env yet
            assert token == "EAAnewtoken"
            assert os.environ.get("INSTAGRAM_ACCESS_TOKEN") == "EAAnewtoken"
            mock_env.assert_called_once_with({"INSTAGRAM_ACCESS_TOKEN": "EAAnewtoken"})

    def test_dead_flag_prevents_refresh(self):
        from content_engine.distributor import refresh_instagram_token
        import content_engine.distributor as dist_mod
        dist_mod._INSTAGRAM_TOKEN_DEAD = True
        with patch("content_engine.distributor.requests.get") as mock_get:
            token = refresh_instagram_token("EAAoldtoken")
        mock_get.assert_not_called()
        dist_mod._INSTAGRAM_TOKEN_DEAD = False  # reset


# ─── Native-first routing via distribute_clip ──────────────────────────────────

class TestDistributeClipNativeFirst:
    """IG, FB, YT should use native API; Buffer only for TikTok or platform 5xx."""

    def _clip(self, platform):
        return {
            "platform": platform,
            "path": "/tmp/test.mp4",
            "caption": "test caption",
            "track_title": "Jericho",
            "clip_index": 0,
            "variant": "a",
        }

    def test_tiktok_always_uses_buffer(self):
        from content_engine.distributor import distribute_clip
        with patch("content_engine.distributor._ensure_tokens_fresh"), \
             patch("content_engine.distributor._buffer_fallback") as mock_buf:
            mock_buf.return_value = {"success": True, "post_id": "tt1", "platform": "tiktok", "via": "buffer_fallback"}
            result = distribute_clip(self._clip("tiktok"))
        mock_buf.assert_called_once()
        assert result.get("via") == "buffer_fallback"

    def test_instagram_uses_native_when_available(self):
        from content_engine.distributor import distribute_clip
        import content_engine.distributor as dist_mod
        dist_mod._INSTAGRAM_TOKEN_DEAD = False
        with patch("content_engine.distributor._ensure_tokens_fresh"), \
             patch("content_engine.distributor._load_native_registry", return_value=set()), \
             patch("content_engine.distributor._instagram_native_available", return_value=True), \
             patch("content_engine.distributor.post_instagram_reel") as mock_native, \
             patch("content_engine.distributor._buffer_fallback") as mock_buf, \
             patch.dict(os.environ, {"INSTAGRAM_USER_ID": "17841443472097088", "INSTAGRAM_ACCESS_TOKEN": "EAAtoken"}):
            mock_native.return_value = {"success": True, "post_id": "ig1", "platform": "instagram"}
            result = distribute_clip(self._clip("instagram"))
        mock_native.assert_called_once()
        mock_buf.assert_not_called()
        assert result["post_id"] == "ig1"

    def test_instagram_no_buffer_on_auth_error(self):
        """Auth errors must NOT fall back to Buffer — they must surface loudly."""
        from content_engine.distributor import distribute_clip
        import content_engine.distributor as dist_mod
        dist_mod._INSTAGRAM_TOKEN_DEAD = False
        with patch("content_engine.distributor._ensure_tokens_fresh"), \
             patch("content_engine.distributor._load_native_registry", return_value=set()), \
             patch("content_engine.distributor._instagram_native_available", return_value=True), \
             patch("content_engine.distributor.post_instagram_reel") as mock_native, \
             patch("content_engine.distributor._buffer_fallback") as mock_buf, \
             patch.dict(os.environ, {"INSTAGRAM_USER_ID": "17841443472097088", "INSTAGRAM_ACCESS_TOKEN": "EAAtoken"}):
            mock_native.return_value = {"success": False, "platform": "instagram",
                                        "error": "Invalid OAuth access token - Cannot parse access token"}
            result = distribute_clip(self._clip("instagram"))
        mock_native.assert_called_once()
        mock_buf.assert_not_called()
        assert not result["success"]

    def test_instagram_does_buffer_on_platform_error(self):
        """5xx / network errors should still fall back to Buffer."""
        from content_engine.distributor import distribute_clip
        import content_engine.distributor as dist_mod
        dist_mod._INSTAGRAM_TOKEN_DEAD = False
        with patch("content_engine.distributor._ensure_tokens_fresh"), \
             patch("content_engine.distributor._load_native_registry", return_value=set()), \
             patch("content_engine.distributor._instagram_native_available", return_value=True), \
             patch("content_engine.distributor.post_instagram_reel") as mock_native, \
             patch("content_engine.distributor._buffer_fallback") as mock_buf, \
             patch.dict(os.environ, {"INSTAGRAM_USER_ID": "17841443472097088", "INSTAGRAM_ACCESS_TOKEN": "EAAtoken"}):
            mock_native.return_value = {"success": False, "platform": "instagram",
                                        "error": "upstream 503 service unavailable"}
            mock_buf.return_value = {"success": True, "post_id": "buf1", "platform": "instagram", "via": "buffer_fallback"}
            result = distribute_clip(self._clip("instagram"))
        mock_buf.assert_called_once()
        assert result["via"] == "buffer_fallback"

    def test_youtube_uses_native(self):
        from content_engine.distributor import distribute_clip
        with patch("content_engine.distributor._ensure_tokens_fresh"), \
             patch("content_engine.distributor._load_native_registry", return_value=set()), \
             patch("content_engine.distributor.post_youtube_short") as mock_native, \
             patch("content_engine.distributor._buffer_fallback") as mock_buf, \
             patch.dict(os.environ, {"YOUTUBE_API_KEY": "key", "YOUTUBE_OAUTH_TOKEN": "ya29.token"}):
            mock_native.return_value = {"success": True, "post_id": "yt1", "platform": "youtube"}
            result = distribute_clip(self._clip("youtube"))
        mock_native.assert_called_once()
        mock_buf.assert_not_called()

    def test_facebook_uses_native(self):
        from content_engine.distributor import distribute_clip
        with patch("content_engine.distributor._ensure_tokens_fresh"), \
             patch("content_engine.distributor._load_native_registry", return_value=set()), \
             patch("content_engine.distributor.post_facebook_reel") as mock_native, \
             patch("content_engine.distributor._buffer_fallback") as mock_buf, \
             patch.dict(os.environ, {"FACEBOOK_PAGE_TOKEN": "EAApage", "FACEBOOK_PAGE_ID": "982928391581585"}):
            mock_native.return_value = {"success": True, "post_id": "fb1", "platform": "facebook"}
            result = distribute_clip(self._clip("facebook"))
        mock_native.assert_called_once()
        mock_buf.assert_not_called()


# ─── .env loader quote-stripping (rjm.py behaviour) ───────────────────────────

class TestEnvQuoteStripping:
    """The rjm.py env loader must strip surrounding quotes from values."""

    def test_double_quoted_value_stripped(self):
        """Simulate what rjm.py does: INSTAGRAM_USER_ID="17841443472097088" → no quotes."""
        line = 'INSTAGRAM_USER_ID="17841443472097088"'
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1]
        assert v == "17841443472097088"
        assert '"' not in v

    def test_single_quoted_value_stripped(self):
        line = "FACEBOOK_PAGE_ID='982928391581585'"
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
            v = v[1:-1]
        assert v == "982928391581585"

    def test_unquoted_token_unchanged(self):
        line = "INSTAGRAM_ACCESS_TOKEN=EAAcmZAfzeQ4sBRO"
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and (v[0] == '"' and v[-1] == '"' or v[0] == "'" and v[-1] == "'"):
            v = v[1:-1]
        assert v == "EAAcmZAfzeQ4sBRO"
