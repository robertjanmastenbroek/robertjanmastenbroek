"""
Daily automation — one command to produce and schedule 5 posts per day.

What it does:
  1. Picks today's song from the masters library (rotates, never repeats until all used)
  2. Picks 12 video clips spread across phone footage + event footage + music videos
  3. Produces 4 beat-synced vertical clips (7s / 15s / 30s / 60s)
  4. Generates platform captions with Bible verse hooks from the database
  5. Schedules 5 posts via Buffer (4 videos + 1 carousel across TikTok + IG + YouTube)
  6. Posts 7s and 60s clips as Instagram Stories
  7. Logs everything to the content library for future rotation

Output goes to: [Command Centre]/content/output/rjm_content_<date>/

Usage:
  python3 daily_run.py                          # today's automated run
  python3 daily_run.py --dry-run                # preview without posting
  python3 daily_run.py --song JERICHO_FINAL     # force a specific song
  python3 daily_run.py --videos-only            # produce clips, don't schedule
  python3 daily_run.py --schedule-only <dir>    # schedule existing output dir
  python3 daily_run.py --stats                  # show library stats + run history

Environment:
  BUFFER_ACCESS_TOKEN — Buffer OAuth token (required unless --dry-run or --videos-only)
"""

import os
import sys
import logging
import argparse
from datetime import datetime
from pathlib import Path

# Ensure content-engine modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import content_library
import beat_editor
import run_local
import social_master

try:
    from learner import PerformanceLearner
    _LEARNER_AVAILABLE = True
except ImportError:
    _LEARNER_AVAILABLE = False

logger = logging.getLogger(__name__)

# Allow overriding the Command Centre root via env var (useful on other machines)
CC_ROOT     = Path(os.environ.get('RJM_ROOT',
                   '~/Documents/Robert-Jan Mastenbroek Command Centre')).expanduser()
OUTPUT_BASE = CC_ROOT / "content" / "output"


def _setup_logging():
    """File + console logging. Log file rotates daily in CC_ROOT/logs/."""
    log_dir = CC_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"daily_run_{datetime.now().strftime('%Y-%m-%d')}.log"

    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not root.handlers:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)

    # Add file handler if not already logging to this file
    if not any(isinstance(h, logging.FileHandler) and
               getattr(h, 'baseFilename', '') == str(log_file)
               for h in root.handlers):
        fh = logging.FileHandler(log_file, encoding='utf-8')
        fh.setFormatter(fmt)
        root.addHandler(fh)
        logger.info(f"Logging to {log_file}")


_setup_logging()


def _load_env():
    """Load .env from Command Centre root into os.environ if not already set."""
    env_path = CC_ROOT / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and not os.environ.get(key):  # don't override already-set vars
                os.environ[key] = val


_load_env()

# ── Performance learner (local disk, no Google Drive required) ────────────────
_PERF_LOG = CC_ROOT / "performance.json"
_learner = None
if _LEARNER_AVAILABLE:
    try:
        _learner = PerformanceLearner()
        _learner.load_from_disk(_PERF_LOG)
    except Exception as _e:
        logger.warning(f"Learner init failed ({_e}) — running without performance tracking")
        _learner = None


def _banner(song_name: str, n_videos: int, output_dir: Path, dry_run: bool):
    mode = "DRY RUN — " if dry_run else ""
    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info(f"  🎧 HOLY RAVE DAILY RUN  {mode}")
    logger.info(f"  Song:    {song_name}")
    logger.info(f"  Clips:   {n_videos} source videos")
    logger.info(f"  Output:  {output_dir}")
    logger.info(f"  Goal:    5 posts → TikTok + Instagram + YouTube")
    logger.info("╚══════════════════════════════════════════════════════════╝")
    logger.info("")


def run_daily(song_path: str, video_paths: list[str],
              output_dir: Path, dry_run: bool = False) -> bool:
    """
    Full pipeline: produce clips → generate captions → schedule via Buffer.
    Returns True if Buffer scheduling succeeded (or dry_run).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    song_name = Path(song_path).stem
    base_name = f"rjm_{song_name.lower().replace(' ', '_')[:30]}"

    _banner(song_name, len(video_paths), output_dir, dry_run)

    # ── 1. Build per-clip hooks — each length gets a unique hook + bucket goal ──
    # 7s / 15s → REACH (loop bait, max views)
    # 30s      → FOLLOW (introduce artist, CTA)
    # 60s      → SPOTIFY (full preview, drive to stream)
    from datetime import datetime
    import random as _random
    day_seed = datetime.now().timetuple().tm_yday

    FALLBACKS = {
        'reach':   ["Sacred music for every dancefloor",
                    "Nobody expected this at a rave.",
                    "126 BPM. In the name of Jesus.",
                    "Ancient truth. Future sound."],
        'follow':  ["Every week in Tenerife — free.",
                    "Follow for weekly Sunset Sessions.",
                    "This is Holy Rave.",
                    "Come find us in Tenerife."],
        'spotify': ["Now on Spotify — link in bio.",
                    "Stream it. Share it. All glory.",
                    "Full track on Spotify.",
                    "Save this track."],
    }

    CLIP_BUCKETS = {7: 'reach', 15: 'reach', 30: 'follow', 60: 'spotify'}

    hook_texts = {}
    try:
        import hook_generator
        bible_info = hook_generator.get_bible_info(song_path) or {}
        ref = bible_info.get('verse_reference', '')

        # Collect hooks per bucket — each clip gets a different one
        bucket_pools: dict[str, list] = {}
        for length, bucket in CLIP_BUCKETS.items():
            if bucket not in bucket_pools:
                raw = hook_generator.get_hooks_for_file(song_path, bucket=bucket, limit=20)
                rng = _random.Random(day_seed + hash(bucket))
                rng.shuffle(raw)
                bucket_pools[bucket] = raw if raw else FALLBACKS[bucket]

        # Assign unique hook per clip — cycle within bucket pool
        used: dict[str, int] = {}
        for length, bucket in CLIP_BUCKETS.items():
            pool = bucket_pools[bucket]
            idx = used.get(bucket, 0)
            hook_texts[length] = pool[idx % len(pool)]
            used[bucket] = idx + 1

        for length, hook in hook_texts.items():
            logger.info(f"  {length}s hook [{CLIP_BUCKETS[length]}]: \"{hook}\""
                        + (f"  [{ref}]" if ref else ""))
    except Exception as exc:
        logger.warning(f"Hook DB unavailable ({exc}) — using fallback hooks")
        for length, bucket in CLIP_BUCKETS.items():
            rng = _random.Random(day_seed + length)
            hook_texts[length] = rng.choice(FALLBACKS[bucket])

    # ── 2. Beat-synced video production ────────────────────────────────────
    logger.info(f"Producing clips from {len(video_paths)} source videos...")
    try:
        output_files = beat_editor.build_beat_montage(
            source_clips=video_paths,
            output_dir=str(output_dir),
            base_name=base_name,
            hook_text=hook_texts,        # dict: {7: ..., 15: ..., 30: ..., 60: ...}
            target_lengths=[7, 15, 30, 60],
            music_track=song_path,
        )
    except Exception as exc:
        logger.error(f"Beat montage failed: {exc}", exc_info=True)
        return False

    if not output_files:
        logger.error("No clips produced — aborting")
        return False

    logger.info(f"Produced {len(output_files)} clips")

    # ── 3. Generate captions — unique seed per day ────────────────────────
    try:
        captions_path = run_local.generate_captions_local(
            music_file=song_path,
            output_dir=str(output_dir),
            base_name=base_name,
            target_lengths=[7, 15, 30, 60],
            seed=day_seed,
        )
        logger.info(f"Captions: {Path(captions_path).name}")
    except Exception as exc:
        logger.warning(f"Caption generation failed ({exc}) — will post without captions")

    # ── 4. Schedule via Buffer ─────────────────────────────────────────────
    if dry_run:
        logger.info("Dry run — calling social_master in dry-run mode")

    # Patch sys.argv for social_master.main() call
    original_argv = sys.argv
    sys.argv = ['social_master.py', str(output_dir)]
    if dry_run:
        sys.argv.append('--dry-run')
    try:
        social_master.main()
        posted = not dry_run
    except SystemExit as e:
        if e.code != 0:
            logger.error(f"social_master exited with code {e.code}")
            posted = False
        else:
            posted = not dry_run
    except Exception as exc:
        logger.error(f"Buffer scheduling failed: {exc}")
        posted = False
    finally:
        sys.argv = original_argv

    return posted


def cmd_stats():
    """Print library stats and recent run history."""
    stats = content_library.library_stats()
    history = content_library.get_run_history(limit=10)

    print()
    print("━━━ CONTENT LIBRARY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Active rotation ({len(stats['active_songs'])} songs):")
    for s in stats['active_songs']:
        print(f"    {s['name']:<45}  used {s['use_count']}x  last: {s['last_used'][:10] if s['last_used'] != 'never' else 'never'}")
    print()
    print(f"  Videos: {stats['videos']} total  ({stats['videos_unused']} never used)")
    print(f"  Runs:   {stats['runs_total']} total")
    print()
    if history:
        print("  Recent runs:")
        print(f"  {'Date':<25} {'Song':<40} {'Clips':>5}  {'Posted'}")
        print(f"  {'─'*70}")
        for r in history:
            posted = "✅" if r['posted'] else "⬜"
            print(f"  {r['date'][:19]:<25} {r['song']:<40} {r['n_videos']:>5}  {posted}")
    else:
        print("  No runs yet.")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Holy Rave Daily Content Machine — 5 posts/day across TikTok + IG + YouTube",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview without posting to Buffer')
    parser.add_argument('--song', default=None, metavar='NAME',
                        help='Force a specific song (filename stem, e.g. JERICHO_FINAL)')
    parser.add_argument('--videos-only', action='store_true',
                        help='Produce clips and captions, skip Buffer scheduling')
    parser.add_argument('--schedule-only', default=None, metavar='OUTPUT_DIR',
                        help='Skip production, just schedule an existing output folder')
    parser.add_argument('--stats', action='store_true',
                        help='Show library stats and run history')
    parser.add_argument('--n-videos', type=int, default=12,
                        help='Number of source clips to pull (default: 12)')
    args = parser.parse_args()

    if args.stats:
        cmd_stats()
        return

    if args.schedule_only:
        # Just schedule an existing output folder
        output_dir = Path(os.path.expanduser(args.schedule_only))
        original_argv = sys.argv
        sys.argv = ['social_master.py', str(output_dir)]
        if args.dry_run:
            sys.argv.append('--dry-run')
        try:
            social_master.main()
        finally:
            sys.argv = original_argv
        return

    # ── Pick today's content ───────────────────────────────────────────────
    try:
        song_path, video_paths = content_library.pick_today(
            n_videos=args.n_videos,
            force_song=args.song,
        )
    except Exception as exc:
        logger.error(f"Content library error: {exc}")
        sys.exit(1)

    # ── Output dir in Command Centre ───────────────────────────────────────
    date_str = datetime.now().strftime('%Y-%m-%d_%H%M')
    song_stem = Path(song_path).stem.replace(' ', '_')[:25]
    output_dir = OUTPUT_BASE / f"rjm_{song_stem}_{date_str}"

    # ── Run pipeline ───────────────────────────────────────────────────────
    posted = run_daily(
        song_path=song_path,
        video_paths=video_paths,
        output_dir=output_dir,
        dry_run=args.dry_run or args.videos_only,
    )

    # ── Record to library ──────────────────────────────────────────────────
    if not args.dry_run:
        content_library.record_run(
            song=song_path,
            videos=video_paths,
            output_dir=str(output_dir),
            posted=posted and not args.videos_only,
        )

    # ── Log to performance learner ─────────────────────────────────────────
    if _learner and not args.dry_run:
        try:
            from pathlib import Path as _P
            _song_name = _P(song_path).stem
            # hook_texts is set inside run_daily — retrieve from output dir if exists
            _hooks_used = {}
            _captions = list(output_dir.glob('*_captions.txt'))
            _learner.log_batch(
                filename=_song_name,
                bucket='reach',
                content_type='event',
                hooks=_hooks_used,
                clip_lengths=[7, 15, 30, 60],
            )
            _learner.save_to_disk(_PERF_LOG)
        except Exception as _e:
            logger.warning(f"Learner log failed ({_e}) — continuing")

    logger.info("")
    logger.info(f"Output → {output_dir}")
    if not posted and not args.dry_run and not args.videos_only:
        logger.warning("Buffer scheduling failed — clips are ready, run --schedule-only to retry")
    logger.info("All glory to Jesus.")


if __name__ == '__main__':
    main()
