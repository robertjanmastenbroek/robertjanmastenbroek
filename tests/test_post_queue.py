import json
import os
import sys
import pytest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'outreach_agent'))


@pytest.fixture
def queue_file(tmp_path, monkeypatch):
    """Redirect QUEUE_PATH to a temp file."""
    import post_queue
    fake_path = tmp_path / "failed_posts.json"
    monkeypatch.setattr(post_queue, "QUEUE_PATH", fake_path)
    return fake_path


def test_save_and_load_failed_post(queue_file):
    from post_queue import save_failed_post, load_failed_posts

    save_failed_post(
        clip_path="/content/output/clip.mp4",
        tiktok_caption="Hook\n#techno",
        instagram_caption="Hook\n#holymusicmovement",
        youtube_title="Title",
        youtube_desc="Desc",
        scheduled_at="2026-04-11T10:00:00Z",
        error="uguu.se 503",
    )

    posts = load_failed_posts()
    assert len(posts) == 1
    assert posts[0]["clip_path"] == "/content/output/clip.mp4"
    assert posts[0]["error"] == "uguu.se 503"
    assert "failed_at" in posts[0]


def test_load_returns_empty_when_no_file(queue_file):
    from post_queue import load_failed_posts
    posts = load_failed_posts()
    assert posts == []


def test_clear_failed_post_removes_entry(queue_file):
    from post_queue import save_failed_post, clear_failed_post, load_failed_posts

    save_failed_post("/a.mp4", "tt", "ig", "yt_t", "yt_d", "2026-04-11T10:00:00Z", "err1")
    save_failed_post("/b.mp4", "tt", "ig", "yt_t", "yt_d", "2026-04-11T13:00:00Z", "err2")

    clear_failed_post(0)  # remove first entry
    posts = load_failed_posts()

    assert len(posts) == 1
    assert posts[0]["clip_path"] == "/b.mp4"


def test_multiple_saves_accumulate(queue_file):
    from post_queue import save_failed_post, load_failed_posts

    for i in range(3):
        save_failed_post(f"/clip{i}.mp4", "tt", "ig", "yt_t", "yt_d", "2026-04-11T10:00:00Z", f"err{i}")

    posts = load_failed_posts()
    assert len(posts) == 3
