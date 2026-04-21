#!/usr/bin/env python3
"""
test_jericho_motion.py — Option A: 5×5s Jericho motion loops ($1.40 budget).

Sequence:
  1. Build TrackPrompt for Jericho
  2. Generate 5 scene stills (Flux 2 Pro /edit with references)
  3. Animate each via Kling 2.1 Standard 5s
  4. Stitch a 22-25s preview via Shotstack (no audio — we're evaluating motion)
  5. Print paths, URLs, total cost

This is a "show me before we publish" checkpoint. Outputs live in:
  content/output/youtube_longform/videos/motion_jericho_*.mp4
  content/output/youtube_longform/videos/preview_jericho_motion_*.mp4

Abort with Ctrl-C at any point; cache means re-runs won't re-spend on
scenes already rendered.

Usage:
  python3 scripts/test_jericho_motion.py              # run full 5-scene test
  python3 scripts/test_jericho_motion.py --dry-run    # plan only, no spend
  python3 scripts/test_jericho_motion.py --scenes 3   # cheap 3-scene subset
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform import motion
from content_engine.youtube_longform.prompt_builder import build_prompt


logger = logging.getLogger("test_jericho_motion")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Plan only, no fal.ai spend")
    parser.add_argument("--scenes",  type=int, default=5, help="How many scenes to render (1-5)")
    parser.add_argument("--seconds", type=int, default=5, help="Clip length per scene (5 or 10)")
    parser.add_argument("--no-stitch", action="store_true", help="Render loops but skip the preview stitch")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.seconds not in (5, 10):
        print("✗ --seconds must be 5 or 10 (Kling 2.1 Standard constraint)", file=sys.stderr)
        return 1
    if not (1 <= args.scenes <= len(motion.JERICHO_STORYBOARD)):
        print(f"✗ --scenes must be 1..{len(motion.JERICHO_STORYBOARD)}", file=sys.stderr)
        return 1

    # ── Preflight ─────────────────────────────────────────────────────────
    missing = []
    if not cfg.FAL_KEY:
        missing.append("FAL_KEY")
    if not cfg.cloudinary_configured():
        missing.append("CLOUDINARY_URL")
    if not args.no_stitch and not cfg.SHOTSTACK_API_KEY:
        missing.append("SHOTSTACK_API_KEY (for stitch; or pass --no-stitch)")
    if missing:
        print("✗ Missing env: " + ", ".join(missing), file=sys.stderr)
        return 1

    cost = motion.estimate_cost_usd(args.scenes, args.seconds)
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Jericho motion test — {args.scenes} scenes × {args.seconds}s")
    print(f"  Kling 2.1 Standard @ ${'0.28' if args.seconds == 5 else '0.56'}/clip")
    print(f"  + Flux 2 Pro /edit stills @ $0.075/scene")
    print(f"  Estimated total: ${cost:.2f}")
    print(f"  Shotstack stitch: {'skipped' if args.no_stitch else 'yes (free on stage env)'}")
    print("═══════════════════════════════════════════════════════════════\n")

    if args.dry_run:
        print("── Storyboard preview (dry-run) ──")
        for i, s in enumerate(motion.JERICHO_STORYBOARD[:args.scenes], 1):
            print(f"\n  Scene {i}: {s.scene_id}")
            print(f"    Still  : {s.still_prompt[:140]}…")
            print(f"    Motion : {s.motion_prompt}")
        print(f"\n  Dry-run complete. Re-run without --dry-run to spend ${cost:.2f}.")
        return 0

    # ── Build track prompt (pulls BPM + genre_family + negative) ──────────
    track_prompt = build_prompt("Jericho")
    print(f"Track:        {track_prompt.track_title}")
    print(f"BPM:          {track_prompt.bpm} ({track_prompt.mood_tier})")
    print(f"Genre family: {track_prompt.genre_family}")
    print(f"Scripture:    {track_prompt.scripture_anchor}\n")

    # ── Generate motion loops ─────────────────────────────────────────────
    print("── Generating motion loops ──")
    clips = motion.generate_motion_loops(
        track_title="jericho",
        track_prompt=track_prompt,
        clip_seconds=args.seconds,
        aspect_ratio=cfg.KLING_ASPECT_16_9,
        max_scenes=args.scenes,
    )
    print(f"\n✓ {len(clips)} motion clips rendered:")
    for c in clips:
        print(f"  {c.scene_id}: {c.local_path}  ({c.duration_s}s)")
        if c.remote_url:
            print(f"    remote: {c.remote_url}")

    # ── Stitch preview ────────────────────────────────────────────────────
    if args.no_stitch:
        print("\n(--no-stitch specified; done.)")
        return 0

    # Preview stitch needs fresh remote URLs; warn if any clip was cached
    if any(not c.remote_url for c in clips):
        print(
            "\n⚠  Some clips were served from cache (no remote_url). "
            "Re-uploading them to Cloudinary so we can stitch…"
        )
        # Upload each cached clip to Cloudinary and replace the remote_url
        from content_engine.youtube_longform.render import _upload_to_cloudinary
        rehydrated = []
        for c in clips:
            if c.remote_url:
                rehydrated.append(c)
                continue
            url = _upload_to_cloudinary(
                c.local_path,
                resource_type="video",
                public_id=f"motion_{c.scene_id}",
            )
            rehydrated.append(motion.MotionClip(
                scene_id=c.scene_id,
                local_path=c.local_path,
                remote_url=url,
                still_path=c.still_path,
                duration_s=c.duration_s,
                width=c.width,
                height=c.height,
            ))
        clips = rehydrated

    print("\n── Stitching preview ──")
    preview_path = motion.stitch_preview(
        clips=clips,
        output_label=f"preview_jericho_motion_{args.scenes}x{args.seconds}s",
        crossfade_s=0.5,
        audio_url=None,      # Silent preview — we're grading motion, not vibe-check
    )
    print(f"\n✓ Preview stitched: {preview_path}")
    print("\n── Done ──")
    print(f"Total spend:  ~${cost:.2f}")
    print(f"Files:        {cfg.VIDEO_DIR}")
    print(f"Preview:      open '{preview_path}'")
    print("\nIf the motion quality reads well, run again with --scenes 7 --seconds 10")
    print("or wire motion=True into publisher.py for the full Jericho re-publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
