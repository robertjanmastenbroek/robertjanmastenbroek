import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'outreach_agent'))


class TestGql:
    def test_returns_data_on_success(self):
        from buffer_poster import _gql
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"createPost": {"post": {"id": "p1", "status": "buffer"}}}}

        with patch("buffer_poster.requests.post", return_value=mock_resp):
            result = _gql("query { test }")

        assert result == {"createPost": {"post": {"id": "p1", "status": "buffer"}}}

    def test_retries_on_429_with_backoff(self):
        from buffer_poster import _gql
        rate_resp = MagicMock()
        rate_resp.status_code = 429
        rate_resp.headers = {"Retry-After": "1"}

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"data": {"result": "ok"}}

        with patch("buffer_poster.requests.post", side_effect=[rate_resp, ok_resp]):
            with patch("buffer_poster.time.sleep") as mock_sleep:
                result = _gql("query { test }")

        mock_sleep.assert_called_once_with(1)
        assert result == {"result": "ok"}

    def test_retries_on_500_up_to_3_times(self):
        from buffer_poster import _gql
        error_resp = MagicMock()
        error_resp.status_code = 500
        error_resp.raise_for_status.side_effect = Exception("500 Internal Server Error")

        with patch("buffer_poster.requests.post", return_value=error_resp):
            with patch("buffer_poster.time.sleep"):
                with pytest.raises(Exception, match="500"):
                    _gql("query { test }")

        # Should have attempted exactly 3 times
        assert error_resp.raise_for_status.call_count == 3

    def test_raises_runtime_error_on_graphql_errors(self):
        from buffer_poster import _gql
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"errors": [{"message": "Unauthorized"}]}

        with patch("buffer_poster.requests.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="Buffer API error"):
                _gql("query { test }")

    def test_does_not_call_sys_exit(self):
        """_gql must never call sys.exit() — it would kill the whole process."""
        from buffer_poster import _gql
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"errors": [{"message": "bad"}]}

        with patch("buffer_poster.requests.post", return_value=mock_resp):
            with patch("buffer_poster.sys.exit") as mock_exit:
                try:
                    _gql("query { test }")
                except RuntimeError:
                    pass
                mock_exit.assert_not_called()


class TestUploadVideoAndQueue:
    def test_continues_when_one_platform_fails(self, tmp_path):
        """If TikTok fails, Instagram and YouTube should still be attempted."""
        from buffer_poster import upload_video_and_queue
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")

        with patch("buffer_poster.upload_video", return_value="https://example.com/v.mp4"):
            with patch("buffer_poster._create_video_post") as mock_post:
                with patch("buffer_poster._create_video_story_post"):
                    with patch("buffer_poster.time.sleep"):
                        # TikTok fails, Instagram succeeds, YouTube succeeds
                        mock_post.side_effect = [
                            RuntimeError("TikTok API error"),
                            "ig_post_id",
                            "yt_post_id",
                        ]
                        results = upload_video_and_queue(
                            clip_path=str(clip),
                            tiktok_caption="tt",
                            instagram_caption="ig",
                            youtube_title="YT Title",
                            youtube_desc="YT Desc",
                        )

        assert results["tiktok"]["success"] is False
        assert results["instagram_reel"]["success"] is True
        assert results["youtube"]["success"] is True

    def test_returns_all_success_when_no_failures(self, tmp_path):
        from buffer_poster import upload_video_and_queue
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")

        with patch("buffer_poster.upload_video", return_value="https://example.com/v.mp4"):
            with patch("buffer_poster._create_video_post", return_value="post_id"):
                with patch("buffer_poster._create_video_story_post", return_value="story_id"):
                    with patch("buffer_poster.time.sleep"):
                        results = upload_video_and_queue(
                            clip_path=str(clip),
                            tiktok_caption="tt",
                            instagram_caption="ig",
                            youtube_title="YT Title",
                            youtube_desc="YT Desc",
                        )

        assert all(v["success"] for v in results.values())
