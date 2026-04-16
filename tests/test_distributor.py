import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from content_engine.distributor import (
    post_instagram_reel, post_youtube_short,
    distribute_clip, distribute_all, POST_SCHEDULE,
)

CLIP_IG = {
    "clip_index": 0, "platform": "instagram", "variant": "a",
    "path": "/tmp/clip.mp4", "caption": "Listen.", "hook_text": "The drop",
    "hook_mechanism": "tension", "visual_type": "ai_generated",
    "clip_length": 15, "track_title": "Jericho",
}

CLIP_YT = {**CLIP_IG, "platform": "youtube", "variant": "b"}


def test_post_schedule_has_three_times_per_platform():
    # All 6 distribution targets (4 Reel destinations + 2 Story destinations)
    # run on a 3-slot daily schedule.
    for platform in ("instagram", "youtube", "facebook", "tiktok",
                     "instagram_story", "facebook_story"):
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
         patch("content_engine.distributor._upload_video_for_instagram", return_value="https://cdn/v.mp4"), \
         patch("content_engine.distributor.time.sleep"):
        result = post_instagram_reel("/tmp/clip.mp4", "Caption", "123", "tok")

    assert result["success"] is True
    assert result["post_id"] == "media_456"
    assert result["platform"] == "instagram"


def test_post_instagram_reel_returns_error_on_400():
    mock_fail = MagicMock(status_code=400)
    mock_fail.json.return_value = {"error": {"message": "Invalid token"}}
    with patch("content_engine.distributor.requests.post", return_value=mock_fail), \
         patch("content_engine.distributor._upload_video_for_instagram", return_value="https://cdn/v.mp4"):
        result = post_instagram_reel("/tmp/clip.mp4", "Cap", "123", "tok")
    assert result["success"] is False
    assert "error" in result


def test_distribute_clip_routes_instagram():
    with patch("content_engine.distributor.post_instagram_reel",
               return_value={"success": True, "post_id": "ig1", "platform": "instagram"}) as mock_ig, \
         patch.dict(os.environ, {"INSTAGRAM_USER_ID": "123", "INSTAGRAM_ACCESS_TOKEN": "tok"}):
        result = distribute_clip(CLIP_IG)
    mock_ig.assert_called_once()
    assert result["success"] is True


def test_distribute_clip_routes_youtube():
    with patch("content_engine.distributor.post_youtube_short",
               return_value={"success": True, "post_id": "yt1", "platform": "youtube"}) as mock_yt, \
         patch.dict(os.environ, {"YOUTUBE_API_KEY": "key", "YOUTUBE_OAUTH_TOKEN": "tok"}):
        result = distribute_clip(CLIP_YT)
    mock_yt.assert_called_once()
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


def test_distribute_all_fans_out_to_all_six_targets():
    # The unified pipeline takes 3 clip specs (one per clip_index) and fans
    # each one out to all 6 distribution targets. 3 × 6 = 18 results.
    clips = [{**CLIP_IG, "clip_index": i} for i in range(3)]
    with patch("content_engine.distributor.distribute_clip",
               return_value={"success": True, "post_id": "x", "platform": "test"}):
        results = distribute_all(clips)
    assert len(results) == 18


def test_distribute_unknown_platform():
    clip = {**CLIP_IG, "platform": "snapchat"}
    with patch("content_engine.distributor._buffer_fallback",
               return_value={"success": False, "platform": "snapchat", "error": "Buffer fallback failed: x", "via": "buffer_fallback"}):
        result = distribute_clip(clip)
    assert result["success"] is False


# ─── _atomic_env_update ──────────────────────────────────────────────────────
# Token refresh writes back to .env. If two refreshes race, a naive read→edit→
# write pipeline can truncate the file. The atomic helper writes to a temp
# file in the same dir and swaps it in with os.replace().

def test_atomic_env_update_replaces_existing_key(tmp_path, monkeypatch):
    import content_engine.distributor as d
    env = tmp_path / ".env"
    env.write_text("INSTAGRAM_ACCESS_TOKEN=old\nOTHER=keep\n")
    monkeypatch.setattr(d, "PROJECT_DIR", tmp_path)

    assert d._atomic_env_update({"INSTAGRAM_ACCESS_TOKEN": "new_token"}) is True
    text = env.read_text()
    assert "INSTAGRAM_ACCESS_TOKEN=new_token" in text
    assert "OTHER=keep" in text
    assert "old" not in text


def test_atomic_env_update_appends_missing_key(tmp_path, monkeypatch):
    import content_engine.distributor as d
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n")
    monkeypatch.setattr(d, "PROJECT_DIR", tmp_path)

    d._atomic_env_update({"NEW_KEY": "hello"})
    assert "EXISTING=1" in env.read_text()
    assert "NEW_KEY=hello" in env.read_text()


def test_atomic_env_update_handles_multiple_keys(tmp_path, monkeypatch):
    import content_engine.distributor as d
    env = tmp_path / ".env"
    env.write_text("FACEBOOK_PAGE_TOKEN=old\n")
    monkeypatch.setattr(d, "PROJECT_DIR", tmp_path)

    d._atomic_env_update({
        "FACEBOOK_PAGE_TOKEN": "fresh",
        "FACEBOOK_PAGE_ID":    "12345",
    })
    text = env.read_text()
    assert "FACEBOOK_PAGE_TOKEN=fresh" in text
    assert "FACEBOOK_PAGE_ID=12345" in text


def test_atomic_env_update_returns_false_when_missing(tmp_path, monkeypatch):
    import content_engine.distributor as d
    monkeypatch.setattr(d, "PROJECT_DIR", tmp_path)
    # .env doesn't exist in tmp_path
    assert d._atomic_env_update({"KEY": "value"}) is False


def test_atomic_env_update_preserves_file_on_failure(tmp_path, monkeypatch):
    """If the write fails mid-flight, the original .env must be intact.

    This is the whole point of the temp-file + os.replace() pattern: readers
    (other cron jobs, live processes) never see a truncated file.
    """
    import content_engine.distributor as d
    env = tmp_path / ".env"
    env.write_text("IMPORTANT=do_not_lose\n")
    monkeypatch.setattr(d, "PROJECT_DIR", tmp_path)

    # Force os.replace to raise after the temp file is written.
    with patch("content_engine.distributor.os.replace", side_effect=OSError("boom")):
        result = d._atomic_env_update({"KEY": "value"})

    assert result is False
    # Original content untouched
    assert env.read_text() == "IMPORTANT=do_not_lose\n"
