import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch, MagicMock
import content_engine.distributor as _distributor
from content_engine.distributor import (
    post_instagram_reel, post_youtube_short,
    distribute_clip, distribute_all, POST_SCHEDULE,
)


@pytest.fixture(autouse=True)
def _reset_distributor_module_state():
    """Reset the one-way ``_INSTAGRAM_TOKEN_DEAD`` latch between tests.

    ``post_instagram_reel`` calls ``refresh_instagram_token`` which makes a
    real HTTP call to graph.facebook.com unless patched. The 400-path test
    mocks ``requests.post`` but leaves ``requests.get`` un-patched, so the
    refresh hits the real API with a fake token, gets back an ``OAuthException
    190``/"Cannot parse access token", and flips the module-level dead flag.
    Every subsequent test that relied on native IG routing then falls through
    to Buffer and its ``mock_ig.assert_called_once()`` fails.

    Rather than rewire every test to patch ``refresh_instagram_token``, we
    simply reset the flag so each test starts with a clean distributor state.
    """
    _distributor._INSTAGRAM_TOKEN_DEAD = False
    yield
    _distributor._INSTAGRAM_TOKEN_DEAD = False

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


# ─── _buffer_fallback ───────────────────────────────────────────────────────
# Regression guard: Buffer must post to EXACTLY ONE channel matching the clip's
# target platform. The old upload_video_and_queue() fanned out to TikTok + IG
# Reel + IG Story + YouTube on every call, causing duplicate posts whenever any
# single native API failed (or whenever TikTok ran as the primary Buffer path).

def test_buffer_fallback_posts_to_single_tiktok_channel():
    import content_engine.distributor as d
    clip = {**CLIP_IG, "platform": "tiktok", "caption": "drop it"}
    with patch("buffer_poster.upload_video", return_value="https://cdn/v.mp4"), \
         patch("buffer_poster._create_video_post", return_value="buf_tt") as mock_post, \
         patch("db.init_db"), patch("db.increment_content_count"):
        result = d._buffer_fallback(clip)

    assert result["success"] is True
    assert result["platform"] == "tiktok"
    assert result["post_id"]  == "buf_tt"
    # Must target ONLY the tiktok channel
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "tiktok"


def test_buffer_fallback_posts_to_single_instagram_channel():
    import content_engine.distributor as d
    clip = {**CLIP_IG, "platform": "instagram"}
    with patch("buffer_poster.upload_video", return_value="https://cdn/v.mp4"), \
         patch("buffer_poster._create_video_post", return_value="buf_ig") as mock_post, \
         patch("db.init_db"), patch("db.increment_content_count"):
        result = d._buffer_fallback(clip)

    assert result["success"] is True
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "instagram"


def test_buffer_fallback_posts_to_single_youtube_channel():
    import content_engine.distributor as d
    clip = {**CLIP_IG, "platform": "youtube", "track_title": "Jericho"}
    with patch("buffer_poster.upload_video", return_value="https://cdn/v.mp4"), \
         patch("buffer_poster._create_video_post", return_value="buf_yt") as mock_post, \
         patch("db.init_db"), patch("db.increment_content_count"):
        result = d._buffer_fallback(clip)

    assert result["success"] is True
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "youtube"
    # YouTube post must receive a title kwarg so Buffer doesn't reject it
    assert "title" in mock_post.call_args.kwargs
    assert "Jericho" in mock_post.call_args.kwargs["title"]


def test_buffer_fallback_rejects_unsupported_platform():
    # Platforms outside _BUFFER_CHANNELS (e.g. linkedin, x_twitter) have no
    # Buffer channel connected — falling back would raise. Distributor must
    # short-circuit with a clean error instead of calling into buffer_poster.
    # Facebook used to be in this list; it's now a valid Buffer channel so we
    # test an actually-unsupported platform here.
    import content_engine.distributor as d
    clip = {**CLIP_IG, "platform": "linkedin"}
    with patch("buffer_poster._create_video_post") as mock_post:
        result = d._buffer_fallback(clip)
    assert result["success"] is False
    assert "no Buffer channel" in result["error"]
    mock_post.assert_not_called()


def test_buffer_fallback_rejects_stories():
    import content_engine.distributor as d
    for story_platform in ("instagram_story", "facebook_story"):
        clip = {**CLIP_IG, "platform": story_platform}
        with patch("buffer_poster._create_video_post") as mock_post:
            result = d._buffer_fallback(clip)
        assert result["success"] is False, f"{story_platform} should not go to Buffer"
        mock_post.assert_not_called()


def test_tiktok_dispatch_goes_through_single_channel_buffer():
    """End-to-end: a TikTok clip must reach Buffer's TikTok channel and
    NOT also post to Instagram or YouTube. Guards against the historic
    upload_video_and_queue() fan-out bug.
    """
    import content_engine.distributor as d
    clip = {**CLIP_IG, "platform": "tiktok"}
    with patch("buffer_poster.upload_video", return_value="https://cdn/v.mp4"), \
         patch("buffer_poster._create_video_post", return_value="buf_tt") as mock_post, \
         patch("db.init_db"), patch("db.increment_content_count"):
        result = d.distribute_clip(clip)

    assert result["success"] is True
    assert result["platform"] == "tiktok"
    # Exactly one Buffer call, exactly to tiktok
    assert mock_post.call_count == 1
    assert mock_post.call_args.args[0] == "tiktok"


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
