"""
Integration smoke test — dry-run of the publisher orchestrator.

This does NOT hit fal.ai, Cloudinary, Shotstack, or YouTube. It verifies
the prompt + metadata assembly path end-to-end with image generation
skipped via the `skip_image_gen` flag.
"""
from __future__ import annotations

import os

import pytest

from content_engine.youtube_longform import publisher
from content_engine.youtube_longform.types import PublishRequest


def test_publisher_dry_run_without_image_gen(tmp_path, monkeypatch):
    """Dry run with skip_image_gen — should build prompt + write registry row."""
    # Redirect registry to temp location
    monkeypatch.setattr(
        "content_engine.youtube_longform.config.REGISTRY_DIR",
        tmp_path / "registry",
    )
    monkeypatch.setattr(
        "content_engine.youtube_longform.registry.REGISTRY_FILE",
        tmp_path / "registry" / "youtube_longform.jsonl",
    )

    req = PublishRequest(
        track_title="Jericho",
        dry_run=True,
        skip_image_gen=True,
    )
    result = publisher.publish_track(req)

    # Prompt built
    assert result.prompt is not None
    assert result.prompt.track_title == "Jericho"
    assert result.prompt.scripture_anchor == "Joshua 6"

    # No upload artifacts in dry-run
    assert result.youtube_id is None
    assert result.youtube_url is None
    assert result.video is None

    # Smart link built (falls back to UTM-suffixed Spotify URL without Feature.fm key)
    assert result.smart_link
    assert "utm_source=youtube" in result.smart_link

    # Registry row written
    registry_file = tmp_path / "registry" / "youtube_longform.jsonl"
    assert registry_file.exists()
    content = registry_file.read_text()
    assert "Jericho" in content
    assert "dry_run" in content


def test_publisher_refuses_duplicate_upload(tmp_path, monkeypatch):
    """If a successful upload already exists in registry, second call short-circuits."""
    monkeypatch.setattr(
        "content_engine.youtube_longform.config.REGISTRY_DIR",
        tmp_path / "registry",
    )
    monkeypatch.setattr(
        "content_engine.youtube_longform.registry.REGISTRY_FILE",
        tmp_path / "registry" / "youtube_longform.jsonl",
    )
    # Manually seed a non-dry-run successful row
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry" / "youtube_longform.jsonl").write_text(
        '{"track_title": "Jericho", "youtube_id": "abc123", '
        '"youtube_url": "https://youtube.com/watch?v=abc123", '
        '"dry_run": false, "error": null}\n'
    )

    req = PublishRequest(track_title="Jericho", dry_run=False, skip_image_gen=True)
    result = publisher.publish_track(req)
    assert result.error is not None
    assert "Already published" in result.error
    assert result.youtube_id == "abc123"
