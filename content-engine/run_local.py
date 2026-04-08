"""
Local runner — process video files directly without Google Drive.

Usage:
  # Beat montage from multiple clips (recommended):
  python3 run_local.py --montage file1.mp4 file2.mp4 ... --music JERICHO_FINAL.wav

  # Hook auto-selected from database based on --music track (Bible verse based).
  # Override with --hook "custom text" if needed.

  # Single-clip mode (fallback):
  python3 run_local.py file.mp4 --bucket reach

Output: ~/Desktop/rjm_content_<date>/
"""

import os
import sys
import shutil
import logging
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import processor
import beat_editor
import generator as gen_module

try:
    import hook_generator
    HOOK_DB_AVAILABLE = True
except ImportError:
    HOOK_DB_AVAILABLE = False

try:
    import caption_bank
    CAPTION_BANK_AVAILABLE = True
except ImportError:
    CAPTION_BANK_AVAILABLE = False


def generate_captions_local(music_file: str, output_dir: str, base_name: str,
                             target_lengths: list, bucket: str = 'reach',
                             seed: int = None) -> str:
    """
    Generate a unique captions .txt file — no API call.
    Each run gets different captions + hook variants via caption_bank + hook_database.
    seed: pass an int (e.g. day-of-year + run count) for deterministic but daily-rotating output.
    """
    import random
    from datetime import datetime

    if seed is None:
        seed = datetime.now().timetuple().tm_yday

    hooks_list = []
    bible_info = {}
    if HOOK_DB_AVAILABLE and music_file:
        hooks_list = hook_generator.get_hooks_for_file(music_file, bucket=bucket, limit=20)
        bible_info = hook_generator.get_bible_info(music_file) or {}

    if not hooks_list:
        hooks_list = [
            "Sacred music for every dancefloor",
            "126 BPM. In the name of Jesus.",
            "Nobody expected this at a rave.",
            "Ancient truth. Future sound.",
            "The dancefloor is sacred tonight.",
            "This track carries scripture.",
        ]

    verse_ref  = bible_info.get('verse_reference', '')
    verse_text = bible_info.get('verse_text', '')

    clips = {}
    for i, length in enumerate(sorted(target_lengths)):
        ls = str(length)
        # Each clip length gets a different hook, cycling with seed for daily rotation
        rng = random.Random(seed + i)
        shuffled = hooks_list[:]
        rng.shuffle(shuffled)
        hook_a = shuffled[0]
        hook_b = shuffled[1] if len(shuffled) > 1 else hook_a
        hook_c = shuffled[2] if len(shuffled) > 2 else hook_b

        # Pull unique platform captions from the bank (different seed per length)
        if CAPTION_BANK_AVAILABLE and music_file:
            caps = caption_bank.get_unique_captions(music_file, seed=seed + i * 7)
            tiktok_cap   = caps["tiktok"]["caption"]
            tiktok_tags  = caps["tiktok"]["hashtags"]
            ig_cap       = caps["instagram"]["caption"]
            ig_tags      = caps["instagram"]["hashtags"]
            yt_title     = caps["youtube"]["title"]
            yt_desc      = caps["youtube"]["description"]
        else:
            # Minimal fallback — still unique via verse ref
            verse_line = f" ({verse_ref})" if verse_ref else ""
            tiktok_cap  = f"nobody told me a rave could feel like this{verse_line} 🌊"
            tiktok_tags = "#holyrave #melodictechno #rave #undergroundtechno #electronicmusic #sunsetsessions #tenerife #techno"
            ig_cap      = f"The dust on the floor. The bassline in the chest.{' ' + verse_ref + '.' if verse_ref else ''} Every week in Tenerife."
            ig_tags     = "#holyrave #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #dancefloor"
            yt_title    = hook_a[:70] if len(hook_a) <= 70 else "Holy Rave Tenerife — Sacred Melodic Techno"
            yt_desc     = (
                f"Robert-Jan Mastenbroek — Ancient Truth. Future Sound.\n"
                f"Free weekly Sunset Sessions in Tenerife.\n"
                f"{verse_ref + ' — ' + verse_text[:80] if verse_ref else 'Link in bio.'}"
            )

        clips[ls] = {
            "hook_a": hook_a,
            "hook_b": hook_b,
            "hook_c": hook_c,
            "best_posting_time": "Friday 7pm CET",
            "tiktok":    {"caption": tiktok_cap, "hashtags": tiktok_tags},
            "instagram": {"caption": ig_cap,     "hashtags": ig_tags},
            "youtube":   {"title": yt_title,      "description": yt_desc},
        }

    generated = {
        "content_type": "event",
        "bucket": bucket,
        "bucket_label": "REACH — Max Views",
        "story_repurpose": False,
        "clips": clips,
    }

    caption_text = gen_module.format_caption_file(base_name, generated)
    out_path = os.path.join(output_dir, f'{base_name}_captions.txt')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(caption_text)

    if verse_ref:
        logger.info(f"Captions generated — {verse_ref}: {len(clips)} clip lengths")
    else:
        logger.info(f"Captions generated (generic hooks): {len(clips)} clip lengths")

    return out_path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.m4v', '.webm',
                    '.MP4', '.MOV', '.MKV', '.M4V'}

# Phone footage lives in the Command Centre — auto-included in every --montage run
PHONE_FOOTAGE_DIR = os.path.expanduser(
    '~/Documents/Robert-Jan Mastenbroek Command Centre/content/videos/phone-footage'
)


def collect_videos(inputs: list) -> list:
    files = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            for ext in VIDEO_EXTENSIONS:
                files.extend(sorted(p.glob(f'*{ext}')))
        elif p.is_file() and p.suffix in VIDEO_EXTENSIONS:
            files.append(p)
    return [str(f) for f in files]


def run_montage(source_clips: list, output_dir: str, base_name: str,
                hook_text: str, captions_src: str = None, music_track: str = None):
    """Build beat-synced montage from multiple clips."""
    logger.info(f"")
    logger.info(f"╔══════════════════════════════════════════════════╗")
    logger.info(f"  Beat Montage — {len(source_clips)} source clips")
    logger.info(f"  Output: {output_dir}/{base_name}_[15|30|60]s.mp4")
    logger.info(f"╚══════════════════════════════════════════════════╝")
    logger.info(f"  Hook:  \"{hook_text}\"")
    if music_track:
        logger.info(f"  Music: {os.path.basename(music_track)}")
    logger.info(f"")

    output_files = beat_editor.build_beat_montage(
        source_clips=source_clips,
        output_dir=output_dir,
        base_name=base_name,
        hook_text=hook_text,
        target_lengths=[7, 15, 30, 60],
        music_track=music_track,
    )

    # Copy captions file if provided
    if captions_src and os.path.exists(captions_src):
        dest = os.path.join(output_dir, f'{base_name}_captions.txt')
        shutil.copy2(captions_src, dest)
        logger.info(f"Captions saved: {dest}")
    elif captions_src:
        logger.warning(f"Captions file not found: {captions_src}")

    return output_files


def run_single(input_path: str, output_dir: str, bucket: str,
               captions_src: str = None):
    """Single-clip pipeline (fallback for short clips)."""
    file_name = os.path.basename(input_path)
    base_name = os.path.splitext(file_name)[0]

    logger.info(f"")
    logger.info(f"━━━ Processing: {file_name} [{bucket.upper()}] ━━━")

    info = processor.get_video_info(input_path)
    duration = info['duration']
    possible_lengths = [l for l in processor.CLIP_LENGTHS if duration >= l]

    if not possible_lengths:
        logger.warning(f"Too short ({duration:.1f}s) — skipping")
        return

    clips_dir = os.path.join(output_dir, base_name)
    os.makedirs(clips_dir, exist_ok=True)

    output_files = processor.process_video(input_path, clips_dir, hooks={})
    logger.info(f"✅ {len(output_files)} clips → {clips_dir}")

    if captions_src and os.path.exists(captions_src):
        dest = os.path.join(clips_dir, f'{base_name}_captions.txt')
        shutil.copy2(captions_src, dest)


def main():
    parser = argparse.ArgumentParser(description='RJM local content processor')
    parser.add_argument('inputs', nargs='*', help='Video files (single-clip mode)')
    parser.add_argument('--montage', nargs='+', metavar='FILE',
                        help='Build beat montage from these clips')
    parser.add_argument('--hook', default=None,
                        help='Hook text to burn into video (auto-selected from DB if omitted)')
    parser.add_argument('--music', default=None,
                        help='Master WAV track to use as audio (camera audio muted)')
    parser.add_argument('--captions', default=None,
                        help='Path to pre-written captions .txt file')
    parser.add_argument('--name', default='rjm_event',
                        help='Base name for output files')
    parser.add_argument('--bucket', choices=['reach', 'follow', 'spotify'],
                        default='reach', help='Growth bucket (single-clip mode)')
    parser.add_argument('--output-dir', default=None,
                        help='Output directory (default: ~/Desktop/rjm_content_<date>)')
    args = parser.parse_args()

    date_str = datetime.now().strftime('%Y-%m-%d_%H%M')
    cc_output = os.path.expanduser(
        f'~/Documents/Robert-Jan Mastenbroek Command Centre/content/output/rjm_content_{date_str}'
    )
    output_dir = args.output_dir or cc_output
    os.makedirs(output_dir, exist_ok=True)

    if args.montage:
        sources = list(args.montage)
        # Auto-include phone-footage folder if it exists and isn't already listed
        if os.path.isdir(PHONE_FOOTAGE_DIR) and PHONE_FOOTAGE_DIR not in sources:
            sources.append(PHONE_FOOTAGE_DIR)
            logger.info(f"Auto-including phone footage: {PHONE_FOOTAGE_DIR}")
        clips = collect_videos(sources)
        if not clips:
            logger.error("No video files found in --montage list")
            sys.exit(1)

        # Auto-select hook from database based on music track
        hook_text = args.hook
        if not hook_text and HOOK_DB_AVAILABLE and args.music:
            hooks = hook_generator.get_hooks_for_file(args.music, bucket=args.bucket, limit=1)
            if hooks:
                hook_text = hooks[0]
                bible_info = hook_generator.get_bible_info(args.music)
                if bible_info:
                    logger.info(f"Auto-hook from {bible_info.get('verse_reference','DB')}: \"{hook_text}\"")
                else:
                    logger.info(f"Auto-hook (generic): \"{hook_text}\"")
        if not hook_text:
            hook_text = 'Sacred music for every dancefloor'

        run_montage(
            source_clips=clips,
            output_dir=output_dir,
            base_name=args.name,
            hook_text=hook_text,
            captions_src=args.captions,
            music_track=args.music,
        )

        # Auto-generate captions file if none was provided
        if not args.captions:
            generate_captions_local(
                music_file=args.music,
                output_dir=output_dir,
                base_name=args.name,
                target_lengths=[7, 15, 30, 60],
                bucket=args.bucket,
            )
    elif args.inputs:
        clips = collect_videos(args.inputs)
        for clip in clips:
            run_single(clip, output_dir, args.bucket, args.captions)
    else:
        parser.print_help()
        sys.exit(1)

    logger.info(f"")
    logger.info(f"All done → {output_dir}")


if __name__ == '__main__':
    main()
