#!/usr/bin/env python3
"""
smoke_test_upload.py — Upload the rendered Jericho MP4 to YouTube as PRIVATE.

Validates the YouTube Data API v3 upload path end-to-end:
  1. Refreshes OAuth access token (HOLYRAVE_REFRESH_TOKEN)
  2. Resumable upload of the MP4 via videos.insert
  3. Sets the first thumbnail variant via thumbnails.set
  4. Verifies the video ID is returned

Safety:
  - privacyStatus = "private"
  - publishAt      = "2027-01-01" (9 months out — never auto-publishes by accident)
  - notifySubscribers = False (no ping even if it flips public)
  - title prefixed with [SMOKE TEST] so it's obvious in YouTube Studio

Cost: 1700 YouTube quota units (of 10k/day). $0.

Manual cleanup: you delete the test video in YouTube Studio when convenient.
(Auto-delete requires additional API scope we haven't requested.)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from content_engine.youtube_longform import config as cfg, uploader
from content_engine.youtube_longform.types import UploadSpec


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    # 1. Find the rendered MP4
    mp4_candidates = sorted(cfg.VIDEO_DIR.glob("smoke_jericho*.mp4"))
    if not mp4_candidates:
        print(
            "✗ No rendered MP4 found at "
            f"{cfg.VIDEO_DIR}/smoke_jericho*.mp4\n"
            "  Run smoke_test_render.py first.",
            file=sys.stderr,
        )
        return 1
    mp4_path = mp4_candidates[-1]
    mp4_mb = mp4_path.stat().st_size / 1024 / 1024
    print(f"Video:     {mp4_path.name} ({mp4_mb:.1f} MB)")

    # 2. Pick the first thumbnail variant
    thumb_candidates = sorted(cfg.IMAGE_DIR.glob("jericho_thumb_0_*.jpg"))
    if not thumb_candidates:
        print(
            f"⚠ No thumbnail found at {cfg.IMAGE_DIR}/jericho_thumb_0_*.jpg.\n"
            "  Continuing without a custom thumbnail (YouTube will use auto-generated).",
            file=sys.stderr,
        )
        thumb_path = None
    else:
        thumb_path = thumb_candidates[-1]
        print(f"Thumbnail: {thumb_path.name}")

    # 3. Verify OAuth is authorized against Holy Rave (not main)
    import requests
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id":     cfg.YT_CLIENT_ID,
            "client_secret": cfg.YT_CLIENT_SECRET,
            "refresh_token": cfg.YT_REFRESH_TOKEN,
            "grant_type":    "refresh_token",
        },
        timeout=15,
    )
    if r.status_code != 200:
        print(f"✗ OAuth refresh failed: {r.status_code} {r.text[:300]}", file=sys.stderr)
        return 1
    access_token = r.json()["access_token"]
    ch = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    ).json()
    channels = ch.get("items", [])
    if not channels:
        print("✗ OAuth did not return any channel", file=sys.stderr)
        return 1
    channel_name = channels[0]["snippet"]["title"]
    channel_id = channels[0]["id"]
    print(f"Channel:   {channel_name} ({channel_id})")
    if channel_name.lower() != "holy rave":
        print(
            f"✗ SAFETY ABORT — OAuth points at '{channel_name}', not Holy Rave.\n"
            "  Re-run scripts/setup_youtube_oauth.py --channel holyrave",
            file=sys.stderr,
        )
        return 1

    print("─" * 70)

    # 4. Build upload spec
    spec = UploadSpec(
        video_path=mp4_path,
        thumbnail_paths=[thumb_path] if thumb_path else [],
        title="[SMOKE TEST] Robert-Jan Mastenbroek - Jericho",
        description=(
            "This is a pipeline smoke-test upload. Scheduled private for 2027.\n\n"
            "If you see this on YouTube, it's safe to delete from Studio."
        ),
        tags=["smoketest", "pipeline", "holyrave"],
        category_id="10",
        privacy_status="private",
        publish_at_iso="2027-01-01T12:00:00.000Z",   # Far future — never publishes accidentally
        notify_subscribers=False,
        made_for_kids=False,
        license="youtube",
        embeddable=True,
        public_stats=True,
    )

    print(f"Uploading as PRIVATE, scheduled for {spec.publish_at_iso}…")
    print("(1700 YouTube quota units, ~10-30s for 50MB MP4)")
    video_id = uploader.upload(spec)
    print("─" * 70)
    print(f"✓ Uploaded!")
    print(f"  Video ID:    {video_id}")
    print(f"  Studio URL:  https://studio.youtube.com/video/{video_id}/edit")
    print(f"  Watch URL:   https://youtube.com/watch?v={video_id} (private — only you can see)")
    print("─" * 70)
    print()
    print("Manual cleanup: delete this video in YouTube Studio when done verifying.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
