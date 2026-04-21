#!/usr/bin/env python3
"""
publish_jericho_v2.py — Launch Jericho with motion, positive-prompt keyframes.

Invokes the full publisher pipeline with req.motion=True:
  1. Flux 2 Pro /edit  — 5 keyframes (positive-only Iron Age Levant prompts)
  2. Kling O3          — 6 drone-camera morph clips chained seamlessly
  3. Shotstack v1      — full 5:12 render with Jericho audio
  4. YouTube Data API  — uploads public to Holy Rave, auto-adds to Tribal Psy playlist
  5. registry.json     — logged for future dedup + analytics

Total spend (v2 from scratch, nothing cached): ~$7.50
  · 5 × $0.075 keyframes      = $0.375
  · 6 × $0.84  Kling O3 10s   = $5.04
  · 1 × $2.08  Shotstack 5:12 = $2.08
  · YouTube upload            = free

Flags:
  --dry-run         build assets + render, skip YouTube upload
  --schedule <ISO>  schedule for later (default: publish immediately/public)
  --scenes <list>   advanced: override story (keyframe IDs, comma-sep)

Usage:
  python3 scripts/publish_jericho_v2.py                  # go live now
  python3 scripts/publish_jericho_v2.py --dry-run        # smoke test
  python3 scripts/publish_jericho_v2.py --schedule 2026-04-24T21:00:00Z
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform import motion, publisher
from content_engine.youtube_longform.types import PublishRequest


logger = logging.getLogger("publish_jericho_v2")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build assets + render, skip YouTube upload",
    )
    parser.add_argument(
        "--schedule", default=None,
        help='ISO-8601 UTC publish time, e.g. "2026-04-24T21:00:00Z". '
             'Defaults to immediate public upload.',
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # ── Preflight ────────────────────────────────────────────────────────
    missing = []
    if not cfg.FAL_KEY:
        missing.append("FAL_KEY")
    if not cfg.cloudinary_configured():
        missing.append("CLOUDINARY_URL")
    if not cfg.SHOTSTACK_API_KEY:
        missing.append("SHOTSTACK_API_KEY")
    if not args.dry_run:
        if not all([cfg.YT_CLIENT_ID, cfg.YT_CLIENT_SECRET, cfg.YT_REFRESH_TOKEN]):
            missing.append("YOUTUBE_CLIENT_ID / SECRET / REFRESH_TOKEN")
        if not cfg.youtube_oauth_is_holyrave():
            print(
                "⚠  HOLYRAVE_REFRESH_TOKEN not set — publish will use the main "
                "channel's refresh token. This will upload to the WRONG channel."
            )
            print("   Run: python3 scripts/setup_youtube_oauth.py --channel holyrave")
            return 1
    if missing:
        print("✗ Missing env: " + ", ".join(missing), file=sys.stderr)
        return 1

    # ── Summary ──────────────────────────────────────────────────────────
    story = motion.story_for_track("Jericho")
    kf_count = len(story.keyframes)
    morph_count = len(story.morphs)
    # Rough cost (actual will be lower if any clips are cached)
    max_cost = round(
        motion.estimate_cost_usd(kf_count, 10)   # keyframes + Kling
        + 2.08                                   # Shotstack 5:12 @ $0.40/min
        + 0.075,                                 # publisher's fallback hero gen
        2,
    )

    print("═══════════════════════════════════════════════════════════════")
    print("  Jericho v2 — full motion publish")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Story:          {story.story_id}")
    print(f"  Keyframes:      {kf_count}  ({[k.keyframe_id for k in story.keyframes]})")
    print(f"  Morphs:         {morph_count}  (Kling O3 drone-camera chain)")
    print(f"  Audio:          Jericho 5:12 (312s)")
    print(f"  Max cost:       ~${max_cost:.2f}  (caching may reduce)")
    print(f"  Target channel: Holy Rave  (holyrave refresh token={'✓' if cfg.youtube_oauth_is_holyrave() else '✗'})")
    print(f"  Playlist:       {cfg.YT_PLAYLIST_TRIBAL_PSY or '(none — will skip playlist add)'}")
    print(f"  Mode:           {'DRY RUN (no upload)' if args.dry_run else 'LIVE PUBLISH'}")
    print(f"  Schedule:       {args.schedule or '(immediate public upload)'}")
    print("═══════════════════════════════════════════════════════════════\n")

    # ── Go ───────────────────────────────────────────────────────────────
    req = PublishRequest(
        track_title="Jericho",
        motion=True,
        force=True,                   # supersede any prior Jericho publish in registry
        dry_run=args.dry_run,
        publish_at_iso=args.schedule,
    )
    result = publisher.publish_track(req)

    # ── Report ───────────────────────────────────────────────────────────
    print("\n" + "═" * 63)
    if result.error:
        print(f"✗ PUBLISH FAILED: {result.error}")
        return 1

    print("✓ PUBLISH COMPLETE")
    if result.youtube_url:
        print(f"  YouTube URL:  {result.youtube_url}")
    if result.smart_link:
        print(f"  Smart link:   {result.smart_link}")
    if result.video:
        print(f"  Video MP4:    {result.video.local_path}")
        print(f"  Duration:     {result.video.duration}s")
    if result.cost_usd is not None:
        print(f"  Actual spend: ${result.cost_usd:.2f}")
    if result.elapsed_seconds is not None:
        print(f"  Elapsed:      {result.elapsed_seconds:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
