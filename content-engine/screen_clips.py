"""
Clip screening tool — two jobs:

1. Generate a thumbnail contact sheet of all source clips so you can
   visually review them and spot anything that shouldn't be posted.

2. Detect clips with burnt-in text overlays (captions, watermarks, etc.)
   and write them to content/videos/.blacklist so they're excluded.

Usage:
  python3 screen_clips.py              # full scan: thumbs + text detection
  python3 screen_clips.py --thumbs     # contact sheet only
  python3 screen_clips.py --detect     # text overlay detection only
  python3 screen_clips.py --blacklist FILENAME1 FILENAME2 ...  # manually blacklist

The contact sheet is written to:
  content/output/clip_review/contact_sheet.jpg

Blacklist is at:
  content/videos/.blacklist
(one filename stem per line — content_library.py reads this automatically)
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CC_ROOT    = Path("~/Documents/Robert-Jan Mastenbroek Command Centre").expanduser()
VIDEOS_DIR = CC_ROOT / "content" / "videos"
OUTPUT_DIR = CC_ROOT / "content" / "output" / "clip_review"
BLACKLIST  = VIDEOS_DIR / ".blacklist"

VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.m4v', '.MP4', '.MOV', '.MKV'}

THUMB_W = 320
THUMB_H = 180
COLS    = 8


def find_clips() -> list[Path]:
    clips = []
    for p in sorted(VIDEOS_DIR.rglob('*')):
        if p.suffix in VIDEO_EXTS and p.is_file():
            # Skip already-processed output
            if 'output' not in p.parts and '_7s' not in p.name and '_15s' not in p.name:
                clips.append(p)
    return clips


def extract_frame(clip: Path, out: Path, time_pct: float = 0.3) -> bool:
    """Extract a single frame at `time_pct` into the clip."""
    # Get duration first
    dur_cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        str(clip)
    ]
    try:
        dur = float(subprocess.check_output(dur_cmd, stderr=subprocess.DEVNULL).strip())
        t = max(0.5, dur * time_pct)
    except Exception:
        t = 1.0

    cmd = [
        'ffmpeg', '-y', '-ss', str(t), '-i', str(clip),
        '-vframes', '1',
        '-vf', f'scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease,pad={THUMB_W}:{THUMB_H}:(ow-iw)/2:(oh-ih)/2:black',
        '-q:v', '3',
        str(out)
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and out.exists()


def detect_text_overlay(clip: Path) -> bool:
    """
    Heuristic: sample 3 frames, check for high-contrast horizontal edge
    density in the lower-third (where captions/watermarks typically live).
    Returns True if a text overlay is likely present.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # Get duration
        try:
            dur_cmd = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                str(clip)
            ]
            dur = float(subprocess.check_output(dur_cmd, stderr=subprocess.DEVNULL).strip())
        except Exception:
            dur = 10.0

        scores = []
        for t_pct in [0.2, 0.5, 0.75]:
            t = max(0.3, dur * t_pct)
            frame = tmp / f'frame_{t_pct}.png'

            # Extract frame, crop to lower third, apply edge detection
            cmd = [
                'ffmpeg', '-y', '-ss', str(t), '-i', str(clip),
                '-vframes', '1',
                # Resize to 640x360, crop bottom third, then edge detect
                '-vf', (
                    'scale=640:360:force_original_aspect_ratio=decrease,'
                    'pad=640:360:(ow-iw)/2:(oh-ih)/2:black,'
                    'crop=640:120:0:240,'       # bottom third
                    'edgedetect=low=0.05:high=0.15'
                ),
                '-q:v', '2',
                str(frame)
            ]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0 or not frame.exists():
                continue

            # Measure mean pixel brightness of edge image
            # High brightness = lots of edges = likely text
            measure_cmd = [
                'ffprobe', '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'frame_tags=lavfi.signalstats.YAVG',
                '-f', 'lavfi',
                f'movie={frame},signalstats'
            ]
            r2 = subprocess.run(measure_cmd, capture_output=True, text=True)
            for line in r2.stdout.splitlines():
                if 'YAVG' in line:
                    try:
                        score = float(line.split('=')[1])
                        scores.append(score)
                    except (ValueError, IndexError):
                        pass

        if not scores:
            return False

        avg = sum(scores) / len(scores)
        # Threshold tuned empirically: clean footage ~1–4, text overlays ~8+
        return avg > 7.0


def read_blacklist() -> set[str]:
    if not BLACKLIST.exists():
        return set()
    return {
        line.strip().lower()
        for line in BLACKLIST.read_text().splitlines()
        if line.strip() and not line.startswith('#')
    }


def write_blacklist(entries: set[str]):
    BLACKLIST.parent.mkdir(parents=True, exist_ok=True)
    header = "# Clips excluded from daily rotation\n# One filename stem per line (case-insensitive)\n"
    BLACKLIST.write_text(header + "\n".join(sorted(entries)) + "\n")


def make_contact_sheet(clips: list[Path], thumbs_dir: Path) -> Path:
    """Stitch all thumbnails into a contact sheet with filenames labelled."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nGenerating thumbnails for {len(clips)} clips...")
    thumb_paths = []
    blacklisted = read_blacklist()

    for i, clip in enumerate(clips, 1):
        thumb = thumbs_dir / f"{i:03d}_{clip.stem[:30]}.jpg"
        ok = extract_frame(clip, thumb)
        is_bl = clip.stem.lower() in blacklisted
        status = " [BLACKLISTED]" if is_bl else ""
        if ok:
            # Draw filename label on thumbnail
            label_thumb = thumbs_dir / f"labeled_{i:03d}.jpg"
            label = f"{i}. {clip.stem[:25]}{status}"
            cmd = [
                'ffmpeg', '-y', '-i', str(thumb),
                '-vf', (
                    f"drawtext=text='{label}':"
                    "fontsize=14:fontcolor=white:x=4:y=4:"
                    "box=1:boxcolor=black@0.7:boxborderw=3"
                ),
                str(label_thumb)
            ]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode == 0:
                thumb_paths.append(label_thumb)
            else:
                thumb_paths.append(thumb)
        else:
            print(f"  ⚠ Could not extract frame from {clip.name}")

        if i % 10 == 0:
            print(f"  {i}/{len(clips)} done")

    if not thumb_paths:
        print("No thumbnails generated.")
        return None

    # Build ffmpeg tile mosaic
    rows = (len(thumb_paths) + COLS - 1) // COLS
    sheet_path = OUTPUT_DIR / "contact_sheet.jpg"

    # Pad to full grid
    while len(thumb_paths) % COLS != 0:
        thumb_paths.append(thumb_paths[-1])  # repeat last to fill

    inputs = []
    for t in thumb_paths:
        inputs += ['-i', str(t)]

    # Build xstack layout
    positions = []
    for row in range(rows):
        for col in range(COLS):
            positions.append(f"{col*THUMB_W}_{row*THUMB_H}")
    layout = "|".join(positions[:len(thumb_paths)])

    filter_str = (
        f"xstack=inputs={len(thumb_paths)}:layout={layout}"
        f":shortest=1,scale=iw:ih"
    )

    cmd = inputs + [
        '-filter_complex', filter_str,
        '-q:v', '4',
        '-y', str(sheet_path)
    ]
    cmd = ['ffmpeg'] + cmd

    print(f"\nStitching contact sheet ({COLS} cols × {rows} rows)...")
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode == 0:
        size_mb = sheet_path.stat().st_size / 1_000_000
        print(f"✅ Contact sheet: {sheet_path}  ({size_mb:.1f} MB)")
        print(f"   Open it to review all {len(clips)} clips.")
        return sheet_path
    else:
        print(f"xstack failed, trying simpler tile...")
        # Fallback: just list the individual thumbnails
        print(f"Individual thumbnails in: {thumbs_dir}")
        return thumbs_dir


def run_text_detection(clips: list[Path]) -> list[Path]:
    """Scan all clips for burnt-in text overlays. Returns list of flagged clips."""
    print(f"\nScanning {len(clips)} clips for text overlays...")
    flagged = []
    for i, clip in enumerate(clips, 1):
        has_text = detect_text_overlay(clip)
        marker = "⚠ TEXT OVERLAY" if has_text else "✓"
        print(f"  [{i:2d}/{len(clips)}] {clip.name:<40} {marker}")
        if has_text:
            flagged.append(clip)
    return flagged


def main():
    parser = argparse.ArgumentParser(description='Holy Rave clip screening tool')
    parser.add_argument('--thumbs', action='store_true', help='Generate contact sheet only')
    parser.add_argument('--detect', action='store_true', help='Text overlay detection only')
    parser.add_argument('--blacklist', nargs='+', metavar='FILENAME',
                        help='Manually add filenames to blacklist (stems, no extension)')
    parser.add_argument('--unblacklist', nargs='+', metavar='FILENAME',
                        help='Remove filenames from blacklist')
    parser.add_argument('--show-blacklist', action='store_true', help='Print current blacklist')
    args = parser.parse_args()

    # Show / edit blacklist
    if args.show_blacklist:
        bl = read_blacklist()
        if bl:
            print("Current blacklist:")
            for entry in sorted(bl):
                print(f"  {entry}")
        else:
            print("Blacklist is empty.")
        return

    if args.blacklist:
        bl = read_blacklist()
        for name in args.blacklist:
            stem = Path(name).stem.lower()
            bl.add(stem)
            print(f"  Blacklisted: {stem}")
        write_blacklist(bl)
        print(f"Saved to {BLACKLIST}")
        return

    if args.unblacklist:
        bl = read_blacklist()
        for name in args.unblacklist:
            stem = Path(name).stem.lower()
            bl.discard(stem)
            print(f"  Removed: {stem}")
        write_blacklist(bl)
        print(f"Saved to {BLACKLIST}")
        return

    clips = find_clips()
    print(f"Found {len(clips)} source clips")

    thumbs_dir = OUTPUT_DIR / "thumbs"
    do_thumbs = args.thumbs or not args.detect
    do_detect = args.detect or not args.thumbs

    if do_thumbs:
        make_contact_sheet(clips, thumbs_dir)

    if do_detect:
        flagged = run_text_detection(clips)
        if flagged:
            print(f"\n⚠  {len(flagged)} clip(s) have likely text overlays:")
            for c in flagged:
                print(f"   {c.name}")

            ans = input("\nAdd these to the blacklist? [y/N] ").strip().lower()
            if ans == 'y':
                bl = read_blacklist()
                for c in flagged:
                    bl.add(c.stem.lower())
                write_blacklist(bl)
                print(f"✅ {len(flagged)} clips blacklisted.")
        else:
            print("\n✅ No text overlays detected.")

    print()
    print("To manually blacklist a clip:")
    print("  python3 screen_clips.py --blacklist FILENAME_STEM")
    print()
    print("To review all clips visually:")
    print(f"  open '{OUTPUT_DIR}/contact_sheet.jpg'")


if __name__ == '__main__':
    main()
