import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from content_engine.distributor import (
    post_instagram_reel, post_tiktok, post_youtube_short,
    distribute_clip, distribute_all, POST_SCHEDULE,
)

CLIP_IG = {
    "clip_index": 0, "platform": "instagram", "variant": "b",
    "path": "/tmp/clip.mp4", "caption": "Listen.", "hook_text": "The drop",
    "hook_mechanism": "tension", "visual_type": "ai_generated",
    "clip_length": 15, "track_title": "Jericho",
}

CLIP_TT = {**CLIP_IG, "platform": "tiktok",  "variant": "a"}
CLIP_YT = {**CLIP_IG, "platform": "youtube", "variant": "a"}


def test_post_schedule_has_three_times_per_platform():
    for platform in ("tiktok", "instagram", "youtube"):
        assert len(POST_SCHEDULE[platform]) == 3


def test_post_instagram_reel_success():
    mock_container = MagicMock(status_code=200)
    mock_container.json.return_value = {"id": "container_123"}
    mock_status    = MagicMock(status_code=200)
    mock_status.json.return_value = {"status_code": "FINISHED"}
    mock_publish   = MagicMock(status_code=200)
    mock_publish.json.return_value = {"id": "media_456"}

    with patch("content_engine.distributor.requests.post", side_effect=[mock_container, mock_publish]), \
         patch("content_engine.distributor.requests.get",  return_value=mock_status), \
         patch("content_engine.distributor._upload_to_cloudinary", return_value="https://cdn/v.mp4"), \
         patch("content_engine.distributor.time.sleep"):
        result = post_instagram_reel("/tmp/clip.mp4", "Caption", "123", "tok")

    assert result["success"] is True
    assert result["post_id"] == "media_456"
    assert result["platform"] == "instagram"


def test_post_instagram_reel_returns_error_on_400():
    mock_fail = MagicMock(status_code=400)
    mock_fail.json.return_value = {"error": {"message": "Invalid token"}}
    with patch("content_engine.distributor.requests.post", return_value=mock_fail), \
         patch("content_engine.distributor._upload_to_cloudinary", return_value="https://cdn/v.mp4"):
        result = post_instagram_reel("/tmp/clip.mp4", "Cap", "123", "tok")
    assert result["success"] is False
    assert "error" in result


def test_post_tiktok_success():
    mock_init = MagicMock(status_code=200)
    mock_init.json.return_value = {"data": {"publish_id": "pub_789"}}
    mock_status = MagicMock(status_code=200)
    mock_status.json.return_value = {"data": {"status": "PUBLISH_COMPLETE"}}

    with patch("content_engine.distributor.requests.post", side_effect=[mock_init, mock_status]), \
         patch("content_engine.distributor._upload_to_cloudinary", return_value="https://cdn/v.mp4"), \
         patch("content_engine.distributor.time.sleep"):
        result = post_tiktok("/tmp/clip.mp4", "Caption #techno", "tiktok_token")

    assert result["success"] is True
    assert result["post_id"] == "pub_789"
    assert result["platform"] == "tiktok"


def test_post_tiktok_returns_error_on_api_fail():
    mock_fail = MagicMock(status_code=403)
    mock_fail.text = "Unauthorized"
    with patch("content_engine.distributor.requests.post", return_value=mock_fail), \
         patch("content_engine.distributor._upload_to_cloudinary", return_value="https://cdn/v.mp4"):
        result = post_tiktok("/tmp/clip.mp4", "Cap", "bad_token")
    assert result["success"] is False


def test_distribute_clip_routes_instagram():
    with patch("content_engine.distributor.post_instagram_reel",
               return_value={"success": True, "post_id": "ig1", "platform": "instagram"}) as mock_ig, \
         patch.dict(os.environ, {"INSTAGRAM_USER_ID": "123", "INSTAGRAM_ACCESS_TOKEN": "tok"}):
        result = distribute_clip(CLIP_IG)
    mock_ig.assert_called_once()
    assert result["success"] is True


def test_distribute_clip_falls_back_to_buffer_when_no_credentials(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_USER_ID", raising=False)
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    with patch("content_engine.distributor._buffer_fallback",
               return_value={"success": True, "post_id": "buf", "platform": "instagram", "via": "buffer_fallback"}) as mock_buf:
        result = distribute_clip(CLIP_IG)
    mock_buf.assert_called_once()
    assert result["success"] is True


def test_distribute_clip_falls_back_to_buffer_on_native_failure(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_USER_ID", "123")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    with patch("content_engine.distributor.post_instagram_reel",
               return_value={"success": False, "platform": "instagram", "error": "timeout"}), \
         patch("content_engine.distributor._buffer_fallback",
               return_value={"success": True, "post_id": "buf", "platform": "instagram", "via": "buffer_fallback"}) as mock_buf:
        result = distribute_clip(CLIP_IG)
    mock_buf.assert_called_once()
    assert result["success"] is True


def test_distribute_all_returns_nine_results():
    clips = [
        {**CLIP_IG, "clip_index": i, "platform": p}
        for i in range(3) for p in ("tiktok", "instagram", "youtube")
    ]
    with patch("content_engine.distributor.distribute_clip",
               return_value={"success": True, "post_id": "x", "platform": "test"}):
        results = distribute_all(clips)
    assert len(results) == 9


def test_distribute_unknown_platform():
    clip = {**CLIP_IG, "platform": "snapchat"}
    with patch("content_engine.distributor._buffer_fallback",
               return_value={"success": False, "platform": "snapchat", "error": "Buffer fallback failed: x", "via": "buffer_fallback"}):
        result = distribute_clip(clip)
    assert result["success"] is False
