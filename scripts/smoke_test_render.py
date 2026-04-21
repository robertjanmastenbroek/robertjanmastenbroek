#!/usr/bin/env python3
"""
smoke_test_render.py — Composite Jericho hero + audio → MP4 via Shotstack.

Free on Shotstack SANDBOX tier (watermarked). Validates the full render
pipeline end-to-end against the real APIs:

  1. Upload the already-generated hero JPG to Cloudinary → public URL
  2. Upload the Jericho master WAV to Cloudinary → public URL
  3. Probe audio duration via mutagen (no ffmpeg — per structural ban)
  4. POST to Shotstack with the JSON timeline (image held for full duration + audio)
  5. Poll until render completes
  6. Download the resulting MP4

Cost: $0. Cloudinary free tier covers the uploads. Shotstack sandbox is free
with a watermark. This test only validates plumbing — watermark is fine.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from content_engine.youtube_longform import config as cfg, render
from content_engine.youtube_longform.publisher import (
    _audio_duration_seconds,
    _resolve_audio_path,
)
from content_engine.youtube_longform.types import RenderSpec


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    track = "Jericho"

    # 1. Find the already-generated hero image
    hero_matches = sorted(cfg.IMAGE_DIR.glob(f"{track.lower()}_hero_*.jpg"))
    if not hero_matches:
        print(f"✗ No hero image found for {track}. Run smoke_test_jericho.py first.", file=sys.stderr)
        return 1
    hero_path = hero_matches[-1]     # most recent
    print(f"Hero image:   {hero_path.name}  ({hero_path.stat().st_size // 1024} KB)")

    # 2. Find the audio master
    audio_path = _resolve_audio_path(track, None)
    audio_mb = audio_path.stat().st_size / 1024 / 1024
    print(f"Audio master: {audio_path.name}  ({audio_mb:.1f} MB)")

    # 3. Probe duration (no ffmpeg)
    duration = _audio_duration_seconds(audio_path)
    print(f"Duration:     {duration} s  ({duration // 60}:{duration % 60:02d})")

    print("─" * 70)
    print(f"Render backend: Shotstack {cfg.SHOTSTACK_ENV} (SANDBOX = free, watermarked)")
    print(f"Cloudinary:     {'CLOUDINARY_URL set' if cfg.CLOUDINARY_URL else 'split vars set'}")
    print("─" * 70)

    # 4. Upload to Cloudinary (returns public URLs)
    print("Uploading hero image to Cloudinary…")
    hero_url = render.upload_image_for_render(hero_path, public_id=f"smoke_{track.lower()}_hero")
    print(f"  → {hero_url}")

    print("Uploading audio master to Cloudinary… (~15-30s for 53MB WAV)")
    t0 = time.time()
    audio_url = render.upload_audio_for_render(audio_path, public_id=f"smoke_{track.lower()}_audio")
    print(f"  → {audio_url}  ({time.time() - t0:.1f}s)")

    # 5. Composite via Shotstack
    spec = RenderSpec(
        audio_url=audio_url,
        hero_image_url=hero_url,
        duration_seconds=duration,
        output_label=f"smoke_{track.lower()}",
    )

    print(f"\nComposing MP4 via Shotstack… (render typically takes 1-2x realtime)")
    t0 = time.time()
    video = render.composite(spec)
    elapsed = time.time() - t0
    print(f"\n{'─' * 70}")
    print(f"✓ Rendered in {elapsed:.0f}s")
    print(f"  Local file:  {video.local_path}")
    print(f"  Dimensions:  {video.width}x{video.height}")
    print(f"  Duration:    {video.duration} s")
    print(f"  Size:        {video.local_path.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"  Codec:       {video.codec} / {video.audio_codec}")
    print(f"  Remote URL:  {video.remote_url}")
    print("─" * 70)

    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "QuickTime Player", str(video.local_path)])
        print("✓ Opening in QuickTime Player")

    return 0


if __name__ == "__main__":
    sys.exit(main())
