"""
Tests for watcher.py — new-track detection + safety rails.

These tests monkey-patch the audio resolver and registry so we can
verify the scan + report + promote flow without hitting disk or fal.ai.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from content_engine.youtube_longform import watcher


def _make_fake_audio_file(tmp_path: Path, title: str, mtime_offset: int = -120) -> Path:
    """Write a tiny fake WAV and mtime it `mtime_offset` seconds in the past."""
    file_name = title.upper().replace(" ", "_") + "_MASTER.wav"
    audio = tmp_path / file_name
    audio.write_bytes(b"RIFFfake-wav-content")
    # Backdate so it passes the FILE_STABILITY_SECONDS gate
    now = time.time()
    os.utime(audio, (now + mtime_offset, now + mtime_offset))
    return audio


def test_scan_skips_too_recent_file(tmp_path, monkeypatch):
    """A file newer than FILE_STABILITY_SECONDS must be deferred."""
    audio = _make_fake_audio_file(tmp_path, "Jericho", mtime_offset=-5)
    monkeypatch.setattr(watcher, "_resolve_audio_path", lambda title, _: audio)
    monkeypatch.setattr(watcher, "_audio_duration_seconds", lambda _: 300)
    monkeypatch.setattr(
        "content_engine.youtube_longform.registry.REGISTRY_FILE",
        tmp_path / "registry.jsonl",
    )
    cands = watcher.scan_new_tracks()
    # Jericho is in TRACK_BPMS but the file is too new → no candidate
    assert all(c.track_title.lower() != "jericho" for c in cands)


def test_scan_skips_already_published(tmp_path, monkeypatch):
    """Tracks with a non-errored registry row must be excluded."""
    audio = _make_fake_audio_file(tmp_path, "Jericho")
    monkeypatch.setattr(watcher, "_resolve_audio_path", lambda title, _: audio if title.lower() == "jericho" else (_ for _ in ()).throw(Exception("no file")))
    monkeypatch.setattr(watcher, "_audio_duration_seconds", lambda _: 300)

    # Seed a successful registry row for jericho
    reg = tmp_path / "registry.jsonl"
    reg.write_text(json.dumps({
        "track_title": "Jericho",
        "youtube_id":  "abc123",
        "youtube_url": "https://youtube.com/watch?v=abc123",
        "dry_run":     False,
        "error":       None,
    }) + "\n")
    monkeypatch.setattr("content_engine.youtube_longform.registry.REGISTRY_FILE", reg)

    # Watcher now calls already_published(..., validate=True). In this
    # test we have no YouTube OAuth — stub out the token to None so the
    # validation short-circuits and preserves the original "first-successful
    # row wins" behavior. The live validation path is covered by
    # test_registry.test_already_published_validation_skips_stale_returns_next_success.
    monkeypatch.setattr(
        "content_engine.youtube_longform.registry._dedup_token",
        lambda: None,
    )

    cands = watcher.scan_new_tracks()
    assert all(c.track_title.lower() != "jericho" for c in cands)


def test_scan_retries_after_error(tmp_path, monkeypatch):
    """A track with an errored registry row should come back as a candidate."""
    audio = _make_fake_audio_file(tmp_path, "Jericho")
    monkeypatch.setattr(
        watcher,
        "_resolve_audio_path",
        lambda title, _: audio if title.lower() == "jericho" else (_ for _ in ()).throw(Exception("no file")),
    )
    monkeypatch.setattr(watcher, "_audio_duration_seconds", lambda _: 300)

    reg = tmp_path / "registry.jsonl"
    reg.write_text(json.dumps({
        "track_title": "Jericho",
        "youtube_id":  None,
        "youtube_url": None,
        "dry_run":     False,
        "error":       "OAuth failed",
    }) + "\n")
    monkeypatch.setattr("content_engine.youtube_longform.registry.REGISTRY_FILE", reg)

    cands = watcher.scan_new_tracks()
    # Filter for Jericho specifically
    jericho = [c for c in cands if c.track_title.lower() == "jericho"]
    assert len(jericho) == 1
    assert jericho[0].reason == "retry_after_error"


def test_whitelist_gated_never_publishes_outside_TRACK_BPMS(tmp_path, monkeypatch):
    """
    A random WAV in the audio folder must NOT appear as a candidate
    unless the corresponding title is in audio_engine.TRACK_BPMS.
    """
    audio = _make_fake_audio_file(tmp_path, "Chaos Bends")  # NOT in TRACK_BPMS

    def resolver(title, _):
        if title.lower() == "chaos bends":
            return audio
        raise FileNotFoundError

    monkeypatch.setattr(watcher, "_resolve_audio_path", resolver)
    monkeypatch.setattr(watcher, "_audio_duration_seconds", lambda _: 300)
    monkeypatch.setattr(
        "content_engine.youtube_longform.registry.REGISTRY_FILE",
        tmp_path / "registry.jsonl",
    )

    cands = watcher.scan_new_tracks()
    # Must not appear — scan_new_tracks iterates TRACK_BPMS keys only
    assert all(c.track_title.lower() != "chaos bends" for c in cands)


def test_write_pending_report_emits_json(tmp_path, monkeypatch):
    """write_pending_report produces a parseable JSON file on disk."""
    audio = _make_fake_audio_file(tmp_path, "Jericho")
    monkeypatch.setattr(watcher, "_resolve_audio_path", lambda title, _: audio)
    monkeypatch.setattr(watcher, "_audio_duration_seconds", lambda _: 300)
    monkeypatch.setattr(
        "content_engine.youtube_longform.registry.REGISTRY_FILE",
        tmp_path / "registry.jsonl",
    )
    monkeypatch.setattr("content_engine.youtube_longform.watcher.PENDING_PUBLISH_FILE", tmp_path / "pending.json")
    monkeypatch.setattr("content_engine.youtube_longform.watcher.SCAN_REPORT_FILE", tmp_path / "scan_report.json")

    cands = watcher.scan_new_tracks()
    path = watcher.write_pending_report(cands)
    assert path.exists()
    payload = json.loads(path.read_text())
    assert "generated_at" in payload
    assert "candidates" in payload
    assert "weekly_plan" in payload


def test_promote_dry_run_does_not_upload(tmp_path, monkeypatch):
    """Dry-run mode must not hit fal.ai or YouTube."""
    audio = _make_fake_audio_file(tmp_path, "Jericho")
    monkeypatch.setattr(watcher, "_resolve_audio_path", lambda title, _: audio)
    monkeypatch.setattr(watcher, "_audio_duration_seconds", lambda _: 300)
    monkeypatch.setattr(
        "content_engine.youtube_longform.registry.REGISTRY_FILE",
        tmp_path / "registry.jsonl",
    )
    # Stub publish_track to capture what the watcher would call
    captured = []
    def fake_publish(req):
        captured.append(req)
        from content_engine.youtube_longform.types import PublishResult
        return PublishResult(request=req)
    monkeypatch.setattr("content_engine.youtube_longform.watcher.publish_track", fake_publish)

    results = watcher.promote_candidates(dry_run=True, limit=2)
    assert all(r.request.dry_run for r in results)
    assert len(captured) <= 2
