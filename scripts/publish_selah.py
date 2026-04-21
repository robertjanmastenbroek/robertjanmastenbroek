#!/usr/bin/env python3
"""
publish_selah.py — Launch Selah with motion, 9-keyframe 90s no-repeat loop.

Selah is the contemplative counterpart to Jericho's ecstatic storm:
  · 130 BPM organic house (meditative, Psalm 46)
  · 9 unique keyframes + 9 drone-camera morphs = 90s unique chain
  · Dedicated CTR-optimized thumbnail (elder contemplative split-lit close-up)
  · Contemplative drone pace (slower than Jericho's 140 BPM)
  · Selah is on BOTH Spotify and Apple Music → both links appear in description

Default: schedule for the next @osso-so slot (Tue/Thu/Sun 21:00 UTC).
Override with --publish-now for immediate public upload.

Cost: ~$10.80 per publish
  · 10 × $0.075 Flux keyframes  (9 chain + 1 thumbnail) = $0.75
  · 9 × $0.84  Kling O3 10s morphs                      = $7.56
  · 1 × ~$2.50 Shotstack v1 for 6:14 full-track render  = $2.50
  · YouTube upload                                       = free

Usage:
  python3 scripts/publish_selah.py                     # schedule for next slot
  python3 scripts/publish_selah.py --publish-now       # live now, public
  python3 scripts/publish_selah.py --dry-run           # plan only
  python3 scripts/publish_selah.py --schedule 2026-04-23T21:00:00Z
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform import motion, publisher
from content_engine.youtube_longform import scheduler as sch
from content_engine.youtube_longform.types import PublishRequest


logger = logging.getLogger("publish_selah")


def _next_osso_slot_iso() -> str:
    """Return the next @osso-so slot (Tue/Thu/Sun 21:00 UTC) as ISO-8601 Z."""
    now = datetime.now(timezone.utc)
    slots = sch._next_osso_so_slots(now, 1, sch.OSSO_SO_HOUR_UTC)
    if not slots:
        raise RuntimeError("Scheduler returned no upcoming slots")
    return slots[0].isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview plan + cost, skip fal.ai + YouTube spend",
    )
    parser.add_argument(
        "--publish-now", action="store_true",
        help="Immediate public upload instead of scheduled private",
    )
    parser.add_argument(
        "--schedule", default=None,
        help='Explicit ISO-8601 UTC publish time (e.g. "2026-04-23T21:00:00Z"). '
             'Overrides the auto-calculated next slot.',
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
                "⚠  HOLYRAVE_REFRESH_TOKEN not set — publish would use the "
                "main channel's refresh token. Aborting."
            )
            print("   Run: python3 scripts/setup_youtube_oauth.py --channel holyrave")
            return 1
    if missing:
        print("✗ Missing env: " + ", ".join(missing), file=sys.stderr)
        return 1

    # Resolve publish schedule
    if args.publish_now:
        publish_at = None    # publisher treats None as "immediate public"
    elif args.schedule:
        publish_at = args.schedule
    else:
        publish_at = _next_osso_slot_iso()

    # ── Summary ──────────────────────────────────────────────────────────
    story = motion.story_for_track("Selah")
    kf_count = len(story.keyframes)
    morph_count = len(story.morphs)
    thumb_cost = 0.075 if story.thumbnail_keyframe else 0.0
    max_cost = round(
        (kf_count * 0.075)                # chain keyframes
        + thumb_cost                        # dedicated thumbnail keyframe
        + (morph_count * 0.84)              # Kling O3 10s morphs
        + 2.50,                             # Shotstack ~6:14 @ $0.40/min
        2,
    )

    print("═══════════════════════════════════════════════════════════════")
    print("  Selah v1 — full motion publish (90s no-repeat chain)")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Story:            {story.story_id}")
    print(f"  Keyframes:        {kf_count}  ({[k.keyframe_id for k in story.keyframes]})")
    print(f"  Morphs:           {morph_count}  (Kling O3 drone-camera chain)")
    print(f"  Thumbnail:        {story.thumbnail_keyframe.keyframe_id if story.thumbnail_keyframe else '(first in chain)'}")
    print(f"  Audio:            Selah 6:14 (374s), 130 BPM, Psalm 46")
    print(f"  Unique loop:      {morph_count * 10}s (no repeats within loop)")
    print(f"  Max cost:         ~${max_cost:.2f}")
    print(f"  Target channel:   Holy Rave  (holyrave refresh token={'✓' if cfg.youtube_oauth_is_holyrave() else '✗'})")
    print(f"  Playlist routing: Ethnic / Tribal Organic House  ({cfg.YT_PLAYLIST_ETHNIC_TRIBAL or '(none)'})")
    if publish_at:
        print(f"  Schedule:         {publish_at}  (private → publishes automatically)")
    else:
        print(f"  Schedule:         immediate public upload")
    print(f"  Mode:             {'DRY RUN (no upload)' if args.dry_run else 'LIVE PUBLISH'}")
    print("═══════════════════════════════════════════════════════════════\n")

    # ── Go ───────────────────────────────────────────────────────────────
    req = PublishRequest(
        track_title="Selah",
        motion=True,
        force=True,
        dry_run=args.dry_run,
        publish_at_iso=publish_at,
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
    if publish_at:
        print(f"  Goes live:    {publish_at}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
