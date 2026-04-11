import pytest
from unittest.mock import patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'outreach_agent'))

class TestUploadToCatbox:
    def test_returns_url_on_success(self, tmp_path):
        from video_host import _upload_to_catbox
        clip = tmp_path / "test.mp4"
        clip.write_bytes(b"fake video data")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "https://files.catbox.moe/abc123.mp4"

        with patch("video_host.requests.post", return_value=mock_resp):
            url = _upload_to_catbox(str(clip))

        assert url == "https://files.catbox.moe/abc123.mp4"

    def test_raises_on_non_https_response(self, tmp_path):
        from video_host import _upload_to_catbox
        clip = tmp_path / "test.mp4"
        clip.write_bytes(b"fake video data")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "Something went wrong"

        with patch("video_host.requests.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="catbox.moe"):
                _upload_to_catbox(str(clip))

    def test_raises_on_http_error(self, tmp_path):
        from video_host import _upload_to_catbox
        clip = tmp_path / "test.mp4"
        clip.write_bytes(b"fake video data")

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.raise_for_status.side_effect = Exception("503 Service Unavailable")

        with patch("video_host.requests.post", return_value=mock_resp):
            with pytest.raises(Exception):
                _upload_to_catbox(str(clip))


class TestUploadVideo:
    def test_uses_catbox_when_no_cloudinary(self, tmp_path):
        from video_host import upload_video
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLOUDINARY_URL", None)
            with patch("video_host._upload_to_catbox", return_value="https://files.catbox.moe/x.mp4") as mock_cat:
                url = upload_video(str(clip))

        assert url == "https://files.catbox.moe/x.mp4"
        mock_cat.assert_called_once()

    def test_falls_back_to_uguu_when_catbox_fails(self, tmp_path):
        from video_host import upload_video
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLOUDINARY_URL", None)
            with patch("video_host._upload_to_catbox", side_effect=RuntimeError("catbox down")):
                with patch("video_host._upload_to_uguu", return_value="https://uguu.se/abc.mp4") as mock_uguu:
                    url = upload_video(str(clip))

        assert url == "https://uguu.se/abc.mp4"
        mock_uguu.assert_called_once()

    def test_raises_when_all_hosts_fail(self, tmp_path):
        from video_host import upload_video
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLOUDINARY_URL", None)
            with patch("video_host._upload_to_catbox", side_effect=RuntimeError("catbox down")):
                with patch("video_host._upload_to_uguu", side_effect=RuntimeError("uguu down")):
                    with pytest.raises(RuntimeError, match="All video hosts failed"):
                        upload_video(str(clip))

    def test_raises_on_missing_file(self, tmp_path):
        from video_host import upload_video
        with pytest.raises(FileNotFoundError):
            upload_video("/nonexistent/path/clip.mp4")
