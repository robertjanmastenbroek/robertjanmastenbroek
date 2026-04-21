#!/usr/bin/env python3
"""
smoke_test_jericho.py — One-shot smoke test for the image gen pipeline.

Spends $0.045 on fal.ai Flux 2 Pro. Generates a single 1920x1080 hero
image for any track using the calibrated prompt. Opens it in Preview.
No upload, no render, no thumbnails, no LoRA. Just the image.

Script name is historical (originally Jericho-only). Now accepts any
track title via --track.
"""
from __future__ import annotations

import argparse
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from content_engine.youtube_longform import config as cfg, image_gen, prompt_builder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", default="Jericho", help="Track title (default Jericho)")
    args = parser.parse_args()

    if not cfg.FAL_KEY:
        print("✗ FAL_KEY not set", file=sys.stderr)
        return 1

    prompt = prompt_builder.build_prompt(args.track)
    print("─" * 70)
    print(f"Track:           {prompt.track_title}")
    print(f"BPM:             {prompt.bpm}")
    print(f"Genre family:    {prompt.genre_family}")
    print(f"Mood tier:       {prompt.mood_tier}")
    print(f"Scripture:       {prompt.scripture_anchor}")
    print(f"LoRA applied:    {'YES (' + cfg.FAL_BRAND_LORA_URL[:40] + '...)' if cfg.FAL_BRAND_LORA_URL else 'NO — baseline Flux 2 Pro'}")
    print("─" * 70)
    print(f"Estimated cost:  $0.045 (single 1920x1080)")
    print(f"Endpoint:        {cfg.FAL_FLUX_2_PRO_EP}")
    print("─" * 70)
    print("Prompt:")
    print(prompt.flux_prompt[:500] + ("…" if len(prompt.flux_prompt) > 500 else ""))
    print()
    print("Generating… (~15-30 seconds)")
    print()

    asset = image_gen.generate_hero(prompt)

    print("─" * 70)
    print(f"✓ Generated:  {asset.local_path}")
    print(f"  Dimensions: {asset.width}x{asset.height}")
    print(f"  Remote URL: {asset.remote_url}")
    print("─" * 70)

    # Open in Preview
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Preview", str(asset.local_path)])
        print("✓ Opening in Preview.app")
    else:
        print(f"  (open manually: {asset.local_path})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
