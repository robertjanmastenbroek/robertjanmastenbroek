#!/usr/bin/env python3
"""
test_morph_loop.py — Seamless psychedelic morph-loop test (Kling O3 chain).

Target aesthetic: Omiki & Vegas — "Wana" (1M views, Jan 2026). Every
transition is a cinematic morph, not a cut — bird zooms out to temple,
temple zooms in to dance scene, dance morphs into warrior, camera enters
the warrior's mouth and comes back out to the overview. That's the
playbook. We translate the vocabulary to Hebrew / Bedouin / Abrahamic-
nomadic (no Mesoamerican, no Islamic, no Hindu) while keeping the
single-subject hero-portrait + continuous-morph structure.

Sequence:
  1. Build TrackPrompt for a target track (default Jericho — for BPM +
     genre_family + negative prompt routing)
  2. Generate N keyframes (default 3 via RJM_HERO_STORY) via Flux 2 Pro /edit
     with the proven-viral reference corpus
  3. Generate N Kling O3 morph clips, each with start+end frame =
     keyframe_i → keyframe_{i+1 mod N}
  4. Stitch into one seamless MP4 via Shotstack (optionally looped M× for
     a longer preview)
  5. Copy to Desktop, open it

Typical cost:
  3-keyframe / 10s morphs / single loop:  ~$2.75
  3-keyframe / 10s morphs / 3× loop:      ~$2.75 (Shotstack stage is free)
  6-keyframe / 10s morphs / single loop:  ~$5.50

Usage:
  python3 scripts/test_morph_loop.py                     # default: RJM_HERO, single 30s loop
  python3 scripts/test_morph_loop.py --loops 3           # 90s preview
  python3 scripts/test_morph_loop.py --story <id>        # other stories
  python3 scripts/test_morph_loop.py --seconds 5         # cheap 15s test ($1.46)
  python3 scripts/test_morph_loop.py --dry-run
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


logger = logging.getLogger("test_morph_loop")


def _truncate_story(story: motion.MorphStory, n: int) -> motion.MorphStory:
    """
    Truncate a MorphStory to the first N keyframes and synthesize a closing
    wrap morph (kf_N → kf_1) using the canonical story's final morph as a
    structural template. The canonical wrap morph was already written to
    close the full ring (kf_last → kf_1), so its motion language is a
    reasonable starting point even when N < len(keyframes).

    Used to keep test runs cheap on long-chain stories like Halleluyah
    (9 keyframes, ~$7.80 at 10s Kling O3) without mutating motion.STORIES.
    A 3-keyframe subset gives the same visual vocabulary at ~$2.75.
    """
    if n < 2:
        raise ValueError(f"--keyframes must be >= 2 (got {n})")
    if n >= len(story.keyframes):
        return story  # no-op: full story

    kept_kfs     = story.keyframes[:n]
    kept_morphs  = list(story.morphs[:n - 1])   # chains 1→2 … N-1→N
    canonical_wrap = story.morphs[-1]           # kf_last → kf_1 in original

    last_kf_id  = kept_kfs[-1].keyframe_id
    first_kf_id = kept_kfs[0].keyframe_id
    synth_wrap = motion.MorphClip(
        clip_id=f"{last_kf_id}__to__{first_kf_id}__synth_wrap",
        from_kf_id=last_kf_id,
        to_kf_id=first_kf_id,
        motion_prompt=canonical_wrap.motion_prompt,
        duration_s=canonical_wrap.duration_s,
    )
    kept_morphs.append(synth_wrap)

    return motion.MorphStory(
        story_id=f"{story.story_id}__first{n}",
        keyframes=kept_kfs,
        morphs=kept_morphs,
        thumbnail_keyframe=story.thumbnail_keyframe,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--track",    default="Jericho",
                        help="Target track for BPM + genre routing (default Jericho)")
    parser.add_argument("--story",    default="rjm_hero_hebrew_bedouin",
                        help="Story ID from motion.STORIES")
    parser.add_argument("--seconds",  type=int, default=cfg.KLING_O3_CLIP_SECONDS,
                        help="Kling O3 clip length per morph (5 or 10)")
    parser.add_argument("--keyframes", type=int, default=0,
                        help="Truncate the story to the first N keyframes + synthesize a "
                             "wrap morph from kf_N back to kf_1. 0 = full story. Use this "
                             "to keep tests cheap on long-chain stories — e.g. Halleluyah "
                             "has 9 keyframes; --keyframes 3 gives a 30s @ 10s Kling test "
                             "at ~$2.75 instead of ~$7.80 for the full chain.")
    parser.add_argument("--loops",    type=int, default=1,
                        help="How many full chain-loops to stitch. Ignored when --preview-seconds is set.")
    parser.add_argument("--preview-seconds", type=int, default=0,
                        help="Explicit preview length in seconds. Stitches the chain in "
                             "order, reusing clips from the start as needed to reach the "
                             "target length. E.g. 90 with a 60s chain → full chain + first "
                             "3 clips repeated = 90s. Overrides --loops.")
    parser.add_argument("--dry-run",  action="store_true", help="Plan + cost only")
    parser.add_argument("--no-stitch", action="store_true", help="Render clips but skip stitch")
    args = parser.parse_args()

    if args.seconds not in (5, 10):
        print("✗ --seconds must be 5 or 10 (Kling O3 constraint)", file=sys.stderr)
        return 1

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Validate story + optional keyframe truncation.
    # When --keyframes is set, register the truncated story in motion.STORIES
    # under a new id so generate_morph_loop() can look it up normally. This
    # keeps the production API (story_id lookup) untouched — truncation is a
    # pure test-harness concern.
    if args.story not in motion.STORIES:
        print(f"✗ Unknown story '{args.story}'. Available: {list(motion.STORIES)}", file=sys.stderr)
        return 1
    story = motion.STORIES[args.story]

    original_keyframe_count = len(story.keyframes)
    if args.keyframes > 0:
        story = _truncate_story(story, args.keyframes)
        motion.STORIES[story.story_id] = story  # runtime-only registration
        args.story = story.story_id             # redirect generator to the subset

    if args.loops < 1:
        print("✗ --loops must be >= 1", file=sys.stderr)
        return 1

    # Preflight env
    missing = []
    if not cfg.FAL_KEY:
        missing.append("FAL_KEY")
    if not cfg.cloudinary_configured():
        missing.append("CLOUDINARY_URL (host keyframes + clips)")
    if not args.no_stitch and not cfg.SHOTSTACK_API_KEY:
        missing.append("SHOTSTACK_API_KEY (for stitch; or pass --no-stitch)")
    if missing:
        print("✗ Missing env: " + ", ".join(missing), file=sys.stderr)
        return 1

    kf_count    = len(story.keyframes)
    morph_count = len(story.morphs)
    # Cost is per-morph, not per-keyframe, since a chain can revisit keyframes
    cost = round(0.075 * kf_count + 0.084 * args.seconds * morph_count, 4)
    loop_seconds = morph_count * args.seconds
    if args.preview_seconds > 0:
        preview_seconds = args.preview_seconds
    else:
        preview_seconds = loop_seconds * args.loops

    truncated_suffix = (
        f"  [truncated from {original_keyframe_count} keyframes]"
        if args.keyframes > 0 else ""
    )

    print("═══════════════════════════════════════════════════════════════")
    print(f"  Morph-loop test — story '{args.story}'{truncated_suffix}")
    print(f"  Target track:   {args.track}")
    print(f"  Keyframes:      {kf_count}  ({[kf.keyframe_id for kf in story.keyframes]})")
    print(f"  Morphs:         {morph_count}  (Kling O3 @ ${0.084*args.seconds:.2f}/{args.seconds}s each)")
    print(f"  Unique loop:    {loop_seconds}s ({morph_count} × {args.seconds}s)")
    print(f"  Preview length: {preview_seconds}s")
    print(f"  Max cost:       ${cost:.2f}  (caching may reduce; Shotstack {'skipped' if args.no_stitch else 'stage=free'})")
    print("═══════════════════════════════════════════════════════════════\n")

    if args.dry_run:
        print("── Story preview (dry-run) ──\n")
        for i, kf in enumerate(story.keyframes, 1):
            print(f"  Keyframe {i}: {kf.keyframe_id}")
            print(f"    Still : {kf.still_prompt[:160]}…\n")
        for i, m in enumerate(story.morphs, 1):
            print(f"  Morph {i}: {m.from_kf_id} → {m.to_kf_id}")
            print(f"    Motion: {m.motion_prompt}\n")
        print(f"\n  Dry-run complete. Re-run without --dry-run to spend ${cost:.2f}.")
        return 0

    # ── Build track prompt (routes references + negative prompt) ──────────
    track_prompt = build_prompt(args.track)
    print(f"Track context: {args.track}  {track_prompt.bpm} BPM  ({track_prompt.mood_tier})")
    print(f"Genre family:  {track_prompt.genre_family}")
    print(f"Scripture:     {track_prompt.scripture_anchor or '(none)'}\n")

    # ── Run keyframes + morphs ────────────────────────────────────────────
    keyframes, clips = motion.generate_morph_loop(
        story_id=args.story,
        track_prompt=track_prompt,
        duration_s=args.seconds,
        aspect_ratio=cfg.KLING_ASPECT_16_9,
    )
    print(f"\n✓ {len(keyframes)} keyframes + {len(clips)} morph clips rendered.\n")

    for kf in keyframes:
        print(f"  keyframe:  {kf.keyframe_id:25s}  {kf.local_path}")
    print()
    for c in clips:
        print(f"  morph:     {c.from_kf_id:15s} → {c.to_kf_id:15s}  {c.local_path}")

    if args.no_stitch:
        print("\n(--no-stitch specified; done.)")
        return 0

    # If any clip came from cache without a remote_url, re-upload so stitch can use it
    if any(not c.remote_url for c in clips):
        print("\n⚠  Re-uploading cached clips to Cloudinary so Shotstack can consume them…")
        from content_engine.youtube_longform.render import _upload_to_cloudinary
        rehydrated = []
        for c in clips:
            if c.remote_url:
                rehydrated.append(c)
                continue
            url = _upload_to_cloudinary(
                c.local_path,
                resource_type="video",
                public_id=f"morph_{c.clip_id}",
            )
            rehydrated.append(motion.RenderedMorphClip(
                clip_id=c.clip_id,
                from_kf_id=c.from_kf_id,
                to_kf_id=c.to_kf_id,
                local_path=c.local_path,
                remote_url=url,
                duration_s=c.duration_s,
                width=c.width,
                height=c.height,
            ))
        clips = rehydrated

    print("\n── Stitching seamless loop ──")

    # Build the final clip sequence. If --preview-seconds was set, we
    # append clips from the start of the chain (in order) until we hit
    # the target length. Kling O3 clip durations are all args.seconds,
    # so preview_seconds / args.seconds must yield a clean integer.
    if args.preview_seconds > 0:
        target_clip_count = args.preview_seconds // args.seconds
        if args.preview_seconds % args.seconds != 0:
            print(
                f"⚠  --preview-seconds {args.preview_seconds} is not a clean "
                f"multiple of clip length {args.seconds}s; truncating to "
                f"{target_clip_count * args.seconds}s."
            )
        # Repeat the chain cyclically until we've accumulated enough clips
        seq: list[motion.RenderedMorphClip] = []
        while len(seq) < target_clip_count:
            seq.extend(clips)
        seq = seq[:target_clip_count]
        stitch_clips = seq
        loop_count = 1   # The sequence already contains the repeats
        label = (
            f"preview_{args.story}_{kf_count}kf_{args.seconds}s_"
            f"{args.preview_seconds}s"
        )
    else:
        stitch_clips = clips
        loop_count = args.loops
        label = (
            f"preview_{args.story}_{kf_count}kf_{args.seconds}s_"
            f"{args.loops}x"
        )

    preview_path = motion.stitch_loop(
        clips=stitch_clips,
        output_label=label,
        audio_url=None,
        loop_count=loop_count,
    )
    print(f"\n✓ Preview stitched: {preview_path}")

    # Copy to Desktop and open
    import shutil, subprocess
    desktop = Path.home() / "Desktop" / preview_path.name
    shutil.copy(str(preview_path), str(desktop))
    try:
        subprocess.run(["open", str(desktop)], check=False)
    except Exception:
        pass

    print("\n── Done ──")
    print(f"Spend:      ~${cost:.2f}")
    print(f"Preview:    {desktop}")
    print(f"Source MP4: {preview_path}")
    print(f"\nIf the morph chain reads well, the loop becomes the track's publish")
    print(f"background (looped to fill the full track length).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
