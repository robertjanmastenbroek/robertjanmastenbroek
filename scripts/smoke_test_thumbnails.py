#!/usr/bin/env python3
"""
smoke_test_thumbnails.py — Generate 3 thumbnail variants for the same track.

Spends ~$0.09 ($0.03 × 3) on fal.ai Flux 2 Pro. Produces 3 different-seed
1280×720 thumbnails of Jericho. Opens all 3 + the original hero in Preview
so you can tile them for side-by-side comparison.

These 3 + the hero form the asset bundle for the first real publish.
Once the YouTube video is live, upload all 3 thumbnails via YouTube's
Test & Compare feature — YouTube picks the winner automatically based
on watch-time share.

Usage:
    python3 scripts/smoke_test_thumbnails.py [--track TRACK]

Default track: Jericho.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
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

    base = prompt_builder.build_prompt(args.track)
    variants = prompt_builder.build_thumbnail_variants(base, count=cfg.THUMB_VARIANT_COUNT)

    print("─" * 70)
    print(f"Track:           {args.track}")
    print(f"Variants:        {len(variants)} (seeds: {[v.seed for v in variants]})")
    print(f"Size:            {cfg.THUMB_WIDTH}x{cfg.THUMB_HEIGHT}")
    print(f"Cost estimate:   ${0.03 * len(variants):.2f}")
    print(f"LoRA:            {'YES' if cfg.FAL_BRAND_LORA_URL else 'NO — baseline Flux 2 Pro'}")
    print("─" * 70)

    thumbs = image_gen.generate_thumbnails(variants)

    # Find the hero if it exists — open it alongside the 3 variants
    from content_engine.youtube_longform.image_gen import _slug, _hash
    hero_glob = list(cfg.IMAGE_DIR.glob(f"{_slug(args.track)}_hero_*.jpg"))

    print("─" * 70)
    print(f"✓ Generated {len(thumbs)} thumbnails:")
    for t in thumbs:
        print(f"  Variant {t.variant_index}: {t.local_path.name}")
    if hero_glob:
        print(f"  Hero:      {hero_glob[0].name}")
    print("─" * 70)

    if sys.platform == "darwin":
        all_paths = [str(t.local_path) for t in thumbs]
        if hero_glob:
            all_paths.insert(0, str(hero_glob[0]))
        subprocess.Popen(["open", "-a", "Preview", *all_paths])
        print("✓ Opening hero + 3 thumbnail variants in Preview for side-by-side compare")

    return 0


if __name__ == "__main__":
    sys.exit(main())
