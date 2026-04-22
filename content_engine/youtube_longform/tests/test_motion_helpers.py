"""
Tests for the 2026-04-22 post-Renamed reliability helpers in motion.py:
  - _content_digest  — content-based cache keys stable across re-uploads
  - _download_verified — size-verified streaming download with retry
  - _subscribe_with_timeout — client-side Kling timeout wrapper
  - _compute_timeline_digest — Shotstack render reuse lookup key
  - _find_reusable_shotstack_render — log scan + HEAD check
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from content_engine.youtube_longform import motion


# ─── _content_digest ─────────────────────────────────────────────────────────

def test_content_digest_same_bytes_same_digest(tmp_path):
    """Same file content → same digest, regardless of path."""
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    payload = b"hello holy rave" * 1000
    a.write_bytes(payload)
    b.write_bytes(payload)
    assert motion._content_digest(a) == motion._content_digest(b)


def test_content_digest_different_bytes_different_digest(tmp_path):
    """Different content → different digest."""
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    assert motion._content_digest(a) != motion._content_digest(b)


def test_content_digest_length_kwarg(tmp_path):
    """length= controls how many hex chars returned."""
    p = tmp_path / "x.bin"
    p.write_bytes(b"x")
    assert len(motion._content_digest(p, length=8)) == 8
    assert len(motion._content_digest(p, length=16)) == 16


# ─── _download_verified ──────────────────────────────────────────────────────

def test_download_verified_success_size_matches(tmp_path):
    """Happy path: HEAD reports size, GET returns exactly that many bytes."""
    payload = b"X" * 1024
    dest = tmp_path / "out.mp4"

    fake_head = MagicMock()
    fake_head.headers = {"Content-Length": str(len(payload))}

    fake_get = MagicMock()
    fake_get.raise_for_status = MagicMock()
    fake_get.iter_content = MagicMock(return_value=[payload])
    fake_get.__enter__ = MagicMock(return_value=fake_get)
    fake_get.__exit__ = MagicMock(return_value=False)

    with patch("content_engine.youtube_longform.motion.requests.head", return_value=fake_head), \
         patch("content_engine.youtube_longform.motion.requests.get", return_value=fake_get):
        size = motion._download_verified("https://x/y.mp4", dest, max_tries=1)
    assert size == len(payload)
    assert dest.read_bytes() == payload


def test_download_verified_retries_on_truncation(tmp_path):
    """
    Simulates the Renamed failure: first attempt truncates to half,
    second attempt succeeds. _download_verified must retry.
    """
    full = b"X" * 2000
    half = b"X" * 1000
    dest = tmp_path / "out.mp4"

    fake_head = MagicMock()
    fake_head.headers = {"Content-Length": str(len(full))}

    call_counter = {"n": 0}

    class _Resp:
        def __init__(self, body): self._body = body
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=None):
            yield self._body

    def fake_get_factory(*args, **kwargs):
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            return _Resp(half)     # truncated
        return _Resp(full)          # full

    with patch("content_engine.youtube_longform.motion.requests.head", return_value=fake_head), \
         patch("content_engine.youtube_longform.motion.requests.get", side_effect=fake_get_factory), \
         patch("content_engine.youtube_longform.motion.time.sleep"):  # skip backoff
        size = motion._download_verified("https://x/y.mp4", dest, max_tries=3)
    assert size == len(full)
    assert call_counter["n"] == 2


def test_download_verified_raises_after_max_tries(tmp_path):
    """Every attempt truncates → MotionError after max_tries."""
    dest = tmp_path / "out.mp4"

    fake_head = MagicMock()
    fake_head.headers = {"Content-Length": "1000000"}   # 1MB expected

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=None):
            yield b"X" * 100         # always truncate

    with patch("content_engine.youtube_longform.motion.requests.head", return_value=fake_head), \
         patch("content_engine.youtube_longform.motion.requests.get", return_value=_Resp()), \
         patch("content_engine.youtube_longform.motion.time.sleep"):
        with pytest.raises(motion.MotionError):
            motion._download_verified("https://x/y.mp4", dest, max_tries=2)


# ─── _subscribe_with_timeout ─────────────────────────────────────────────────

def test_subscribe_with_timeout_returns_result(monkeypatch):
    """Fast-returning subscribe passes through."""
    fake_client = MagicMock()
    fake_client.subscribe.return_value = {"video": {"url": "https://x/y.mp4"}}
    monkeypatch.setattr("content_engine.youtube_longform.motion._fal_client",
                        lambda: fake_client)

    result = motion._subscribe_with_timeout(
        "fal-ai/something", arguments={"prompt": "x"}, timeout_s=5,
    )
    assert result["video"]["url"] == "https://x/y.mp4"


def test_subscribe_with_timeout_raises_on_timeout(monkeypatch):
    """
    When subscribe hangs beyond timeout, raise TimeoutError instead of
    blocking indefinitely (the Renamed morph-8 23-min hang guard).
    """
    class _SlowClient:
        def subscribe(self, *a, **kw):
            time.sleep(5)
            return {"video": {"url": "too-slow"}}
    monkeypatch.setattr("content_engine.youtube_longform.motion._fal_client",
                        lambda: _SlowClient())
    with pytest.raises(TimeoutError):
        motion._subscribe_with_timeout("x", arguments={}, timeout_s=1)


def test_subscribe_with_timeout_propagates_inner_exception(monkeypatch):
    """An exception in subscribe propagates out (not swallowed)."""
    class _FailClient:
        def subscribe(self, *a, **kw):
            raise ValueError("fal content policy violation")
    monkeypatch.setattr("content_engine.youtube_longform.motion._fal_client",
                        lambda: _FailClient())
    with pytest.raises(ValueError, match="content policy"):
        motion._subscribe_with_timeout("x", arguments={}, timeout_s=5)


# ─── _compute_timeline_digest ────────────────────────────────────────────────

def test_timeline_digest_stable_for_same_inputs():
    a = motion._compute_timeline_digest(
        clip_urls=["a", "b", "c"], audio_url="au",
        preroll_url="pr", target_duration_s=300,
        output_label="test", env="v1",
    )
    b = motion._compute_timeline_digest(
        clip_urls=["a", "b", "c"], audio_url="au",
        preroll_url="pr", target_duration_s=300,
        output_label="test", env="v1",
    )
    assert a == b
    assert len(a) == 16


def test_timeline_digest_changes_when_inputs_change():
    base = motion._compute_timeline_digest(
        clip_urls=["a"], audio_url="au", preroll_url="pr",
        target_duration_s=300, output_label="test", env="v1",
    )
    changes = [
        motion._compute_timeline_digest(
            clip_urls=["a", "extra"], audio_url="au", preroll_url="pr",
            target_duration_s=300, output_label="test", env="v1",
        ),
        motion._compute_timeline_digest(
            clip_urls=["a"], audio_url="different", preroll_url="pr",
            target_duration_s=300, output_label="test", env="v1",
        ),
        motion._compute_timeline_digest(
            clip_urls=["a"], audio_url="au", preroll_url="pr",
            target_duration_s=400, output_label="test", env="v1",
        ),
        motion._compute_timeline_digest(
            clip_urls=["a"], audio_url="au", preroll_url="pr",
            target_duration_s=300, output_label="test", env="stage",
        ),
    ]
    for c in changes:
        assert c != base


# ─── _find_reusable_shotstack_render ─────────────────────────────────────────

def test_find_reusable_returns_none_when_no_log(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "content_engine.youtube_longform.motion.SHOTSTACK_RENDER_LOG",
        tmp_path / "does-not-exist.jsonl",
    )
    result = motion._find_reusable_shotstack_render(
        timeline_digest="abc", env="v1", api_key="x",
    )
    assert result is None


def test_find_reusable_ignores_old_renders(tmp_path, monkeypatch):
    """Renders older than max_age_hours are skipped."""
    from datetime import datetime, timedelta, timezone
    log = tmp_path / "renders.jsonl"
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    log.write_text(json.dumps({
        "timestamp":       old_ts,
        "env":             "v1",
        "render_id":       "stale-render",
        "timeline_digest": "abc",
        "deleted":         False,
    }) + "\n")
    monkeypatch.setattr("content_engine.youtube_longform.motion.SHOTSTACK_RENDER_LOG", log)

    result = motion._find_reusable_shotstack_render(
        timeline_digest="abc", env="v1", api_key="x", max_age_hours=12,
    )
    assert result is None


def test_find_reusable_returns_url_on_match(tmp_path, monkeypatch):
    """Fresh + matching + done → URL returned (simulated via mocks)."""
    from datetime import datetime, timezone
    log = tmp_path / "renders.jsonl"
    log.write_text(json.dumps({
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "env":             "v1",
        "render_id":       "fresh-render",
        "timeline_digest": "abc",
        "deleted":         False,
    }) + "\n")
    monkeypatch.setattr("content_engine.youtube_longform.motion.SHOTSTACK_RENDER_LOG", log)

    # Shotstack status API says done + URL
    status_resp = MagicMock(status_code=200)
    status_resp.json.return_value = {"response": {"status": "done", "url": "https://s3/cached.mp4"}}
    head_resp = MagicMock(status_code=200)

    with patch("content_engine.youtube_longform.motion.requests.get", return_value=status_resp), \
         patch("content_engine.youtube_longform.motion.requests.head", return_value=head_resp):
        url = motion._find_reusable_shotstack_render(
            timeline_digest="abc", env="v1", api_key="x", max_age_hours=12,
        )
    assert url == "https://s3/cached.mp4"


def test_find_reusable_skips_expired_url(tmp_path, monkeypatch):
    """If render status is done but S3 HEAD is 403 (expired), fall through to None."""
    from datetime import datetime, timezone
    log = tmp_path / "renders.jsonl"
    log.write_text(json.dumps({
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "env":             "v1",
        "render_id":       "r1",
        "timeline_digest": "abc",
        "deleted":         False,
    }) + "\n")
    monkeypatch.setattr("content_engine.youtube_longform.motion.SHOTSTACK_RENDER_LOG", log)

    status_resp = MagicMock(status_code=200)
    status_resp.json.return_value = {"response": {"status": "done", "url": "https://s3/expired.mp4"}}
    head_resp = MagicMock(status_code=403)     # expired S3 presigned link

    with patch("content_engine.youtube_longform.motion.requests.get", return_value=status_resp), \
         patch("content_engine.youtube_longform.motion.requests.head", return_value=head_resp):
        url = motion._find_reusable_shotstack_render(
            timeline_digest="abc", env="v1", api_key="x",
        )
    assert url is None
