"""
Tests for uploader.py metadata shape.

Critical regression guards:
  - metadata MUST NOT contain onBehalfOfContentOwner (reserved for Content
    ID partners — returns 403 for regular user uploads)
  - categoryId must be "10" (Music) by default
  - madeForKids must be explicitly False (default-True silently kills
    comments, notifications, embeddability)
  - publishAt requires privacyStatus=private (YouTube rejects otherwise)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from content_engine.youtube_longform import uploader
from content_engine.youtube_longform.types import UploadSpec


def _make_spec(**overrides) -> UploadSpec:
    base = {
        "video_path":        Path("/tmp/fake.mp4"),
        "thumbnail_paths":   [Path("/tmp/fake_thumb.jpg")],
        "title":             "Robert-Jan Mastenbroek - Jericho",
        "description":       "Joshua 6. Ancient Truth. Future Sound.",
        "tags":              ["tribal psytrance", "holy rave"],
        "privacy_status":    "public",
    }
    base.update(overrides)
    return UploadSpec(**base)


def test_upload_metadata_excludes_onbehalf_of_content_owner():
    """This field would 403 for regular user uploads."""
    spec = _make_spec(channel_id="UC_HOLY_RAVE")
    captured: dict = {}

    def fake_resumable(token, path, metadata):
        captured.update(metadata)
        return "fake_video_id"

    def fake_refresh():
        return "fake_token"

    def fake_set_thumb(token, video_id, thumb_path):
        pass

    with patch.object(uploader, "_resumable_upload", side_effect=fake_resumable), \
         patch.object(uploader, "_refresh_access_token", side_effect=fake_refresh), \
         patch.object(uploader, "_set_thumbnail", side_effect=fake_set_thumb):
        uploader.upload(spec)

    assert "onBehalfOfContentOwner" not in captured, (
        "metadata contains onBehalfOfContentOwner — this field is ONLY for "
        "Content ID partner accounts. Regular user uploads must NOT send it."
    )


def test_upload_metadata_category_is_music():
    """categoryId must default to '10' (Music) for music shelf placement."""
    spec = _make_spec()
    captured: dict = {}

    with patch.object(uploader, "_resumable_upload", side_effect=lambda t, p, m: captured.update(m) or "id"), \
         patch.object(uploader, "_refresh_access_token", return_value="tok"), \
         patch.object(uploader, "_set_thumbnail"):
        uploader.upload(spec)

    assert captured["snippet"]["categoryId"] == "10"


def test_upload_metadata_made_for_kids_explicit_false():
    """Missing or True breaks comments/notifications — must explicitly be False."""
    spec = _make_spec()
    captured: dict = {}

    with patch.object(uploader, "_resumable_upload", side_effect=lambda t, p, m: captured.update(m) or "id"), \
         patch.object(uploader, "_refresh_access_token", return_value="tok"), \
         patch.object(uploader, "_set_thumbnail"):
        uploader.upload(spec)

    status = captured["status"]
    assert "selfDeclaredMadeForKids" in status
    assert status["selfDeclaredMadeForKids"] is False


def test_upload_metadata_publish_at_requires_private():
    """publishAt only works with privacyStatus=private — verify."""
    spec = _make_spec(
        privacy_status="private",
        publish_at_iso="2026-04-24T17:00:00.000Z",
    )
    captured: dict = {}

    with patch.object(uploader, "_resumable_upload", side_effect=lambda t, p, m: captured.update(m) or "id"), \
         patch.object(uploader, "_refresh_access_token", return_value="tok"), \
         patch.object(uploader, "_set_thumbnail"):
        uploader.upload(spec)

    assert captured["status"]["privacyStatus"] == "private"
    assert captured["status"]["publishAt"] == "2026-04-24T17:00:00.000Z"


def test_upload_metadata_title_description_truncated_to_youtube_limits():
    """YouTube hard caps: title=100, description=5000."""
    spec = _make_spec(
        title="x" * 200,
        description="y" * 10000,
    )
    captured: dict = {}

    with patch.object(uploader, "_resumable_upload", side_effect=lambda t, p, m: captured.update(m) or "id"), \
         patch.object(uploader, "_refresh_access_token", return_value="tok"), \
         patch.object(uploader, "_set_thumbnail"):
        uploader.upload(spec)

    assert len(captured["snippet"]["title"]) == 100
    assert len(captured["snippet"]["description"]) == 5000


def test_estimate_quota_cost_matches_research():
    """1 upload + 1 thumb + 1 playlist add = 1700 quota units."""
    q = uploader.estimate_quota_cost(upload_count=1, thumb_per_upload=1, add_to_playlist=True)
    assert q == 1700


def test_estimate_quota_cost_without_playlist():
    """No playlist = 1650 units (1600 insert + 50 thumb)."""
    q = uploader.estimate_quota_cost(upload_count=1, thumb_per_upload=1, add_to_playlist=False)
    assert q == 1650
