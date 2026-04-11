#!/usr/bin/env python3
"""
post_today.py — Holy Rave daily content run

1. Picks a source video from content/videos/ (rotation)
2. Picks an RJM track via rotation (peak-energy audio overlay)
3. Generates hooks + captions via Claude (15s / 30s / 60s clips)
4. Cuts 3 vertical clips with burned-in hooks + RJM audio
5. Saves to content/output/YYYY-MM-DD_HHMM_trackname/
6. (Live run) Queues to Buffer: TikTok + Instagram Reels + YouTube Shorts

Usage:
  python3 post_today.py           # live run
  python3 post_today.py --dry-run # generate videos + captions, skip Buffer
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE           = Path(__file__).parent.parent
VIDEOS_DIR     = BASE / "content" / "videos"
OUTPUT_DIR     = BASE / "content" / "output"
TRACK_ROTATION = Path(__file__).parent / "track_rotation.json"
VIDEO_ROTATION = Path(__file__).parent / "video_rotation.json"

import generator   # outreach_agent/generator.py
import processor   # outreach_agent/processor.py

FFMPEG  = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

# ─── Angle auto-cycle ─────────────────────────────────────────────────────────
# Every run cycles through emotional → signal → energy → emotional …
# State persists in /tmp so it survives between runs but resets on reboot.

ANGLE_CYCLE     = ["emotional", "signal", "energy"]
ANGLE_CYCLE_LOG = Path("/tmp/holyrave_angle_cycle.json")


def get_next_cycle_angle() -> str:
    """Returns the next angle in the rotation and advances the counter."""
    try:
        idx = json.loads(ANGLE_CYCLE_LOG.read_text()).get("index", 0)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        idx = 0
    angle = ANGLE_CYCLE[idx % len(ANGLE_CYCLE)]
    ANGLE_CYCLE_LOG.write_text(json.dumps({"index": (idx + 1) % len(ANGLE_CYCLE)}))
    return angle

# ─── Track discovery ──────────────────────────────────────────────────────────

AUDIO_DIRS = [
    BASE / "content" / "audio" / "masters",
    BASE / "content" / "audio" / "tracks",
    BASE / "content" / "audio",
    Path.home() / "Downloads/Music/Tracks",
    Path.home() / "Downloads",
]

SKIP_PATTERNS = [
    r"JERICHO__MASTER", r"STRONG TOWER  MASTER", r"YOU SEE IT ALL MASTER",
    r"Unmastered", r"YT2mp3", r"DJ Moto Moto", r"King Topher", r"Gamemaster",
    r"\(Edit\)", r"\(MP3\)", r"DJ Lucid", r"RENAMED IN THE LIGHT",
    r"You're At The Door", r"He Has Been Good To Me",
]

SPOTIFY_LIVE = {
    "fire in our hands", "he is the light", "my hope is in you", "shema",
    "under your wings", "thunder", "not by might", "living water",
    "halala king jesus", "good to me", "good_to_me", "better is one day",
    "better_is_one_day", "at the door", "renamed", "jericho", "halleluyah",
    "you see it all",
}


def _should_skip(path: Path) -> bool:
    return any(re.search(pat, str(path), re.IGNORECASE) for pat in SKIP_PATTERNS)


def find_tracks() -> list[tuple[Path, str]]:
    seen = {}
    for audio_dir in AUDIO_DIRS:
        if not audio_dir.exists():
            continue
        for ext in ("*.wav", "*.mp3", "*.flac"):
            for f in sorted(audio_dir.glob(ext)):
                if _should_skip(f):
                    continue
                name  = f.stem
                title = re.sub(r"Robert-Jan Mastenbroek\s*[-–]\s*", "", name, flags=re.IGNORECASE)
                title = re.sub(r"Electronic Worship\s*[-–]\s*", "", title, flags=re.IGNORECASE)
                title = re.sub(r"_MASTER(_)?$", "", title, flags=re.IGNORECASE)
                title = re.sub(r"\s*MASTER$", "", title, flags=re.IGNORECASE)
                title = title.replace("_", " ").strip()
                dedup = re.sub(r"\s*(final|master)\s*$", "", title.lower()).strip()
                dedup = re.sub(r"\s*\(\d+\)\s*$", "", dedup).strip()
                dedup = re.sub(r"\s*-\s*psalm\s*\d+.*$", "", dedup).strip()
                dedup = re.sub(r"^title\s+", "", dedup).strip()

                if not any(live in dedup for live in SPOTIFY_LIVE):
                    continue

                def _prio(p: Path) -> int:
                    n = p.stem.upper()
                    s = 0
                    if p.suffix.lower() == ".wav": s += 10
                    if "MASTER" in n: s += 6
                    if "MASTENBROEK" in n: s += 4
                    if "FINAL" in n: s += 2
                    return s

                if dedup not in seen or _prio(f) > _prio(seen[dedup][0]):
                    seen[dedup] = (f, title)

    return [(p, t) for _, (p, t) in sorted(seen.items())]


def pick_next_track(override: str = None) -> tuple[Path, str]:
    tracks = find_tracks()
    if not tracks:
        sys.exit("ERROR: no RJM Spotify tracks found in audio directories.")

    if override:
        matches = [(p, t) for p, t in tracks if override.lower() in t.lower()]
        if not matches:
            sys.exit(f"ERROR: no track matching '{override}'")
        return matches[0]

    rotation = json.loads(TRACK_ROTATION.read_text()) if TRACK_ROTATION.exists() else {}
    tracks.sort(key=lambda item: rotation.get(item[1].lower(), 0))
    return tracks[0]


def mark_track_used(title: str):
    rotation = json.loads(TRACK_ROTATION.read_text()) if TRACK_ROTATION.exists() else {}
    rotation[title.lower()] = int(time.time())
    TRACK_ROTATION.write_text(json.dumps(rotation, indent=2))


# ─── Audio analysis ───────────────────────────────────────────────────────────

def _get_duration(path: Path) -> int:
    result = subprocess.run(
        [FFPROBE, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    return int(float(result.stdout.strip() or "0"))


def find_peak_section(audio_path: Path, clip_duration: int = 30) -> int:
    """Return start time (seconds) of peak-energy section. Skips intro/outro (30s each)."""
    total      = _get_duration(audio_path)
    scan_start = 30
    scan_end   = max(scan_start + clip_duration, total - 30)
    window     = 5
    best_start = scan_start
    best_rms   = -999.0

    print(f"  Scanning {audio_path.name} ({total//60}:{total%60:02d}) for peak energy…")
    t = scan_start
    while t + clip_duration <= scan_end:
        result = subprocess.run(
            [FFMPEG, "-v", "quiet", "-ss", str(t), "-t", str(window),
             "-i", str(audio_path), "-af", "astats=metadata=1:reset=1",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=15,
        )
        for line in result.stderr.splitlines():
            if "RMS level dB" in line:
                try:
                    rms = float(line.split()[-1])
                    if rms > best_rms:
                        best_rms   = rms
                        best_start = t
                except ValueError:
                    pass
        t += window

    print(f"  Peak at {best_start}s (RMS: {best_rms:.1f} dB)")
    return best_start


# ─── Source video rotation ────────────────────────────────────────────────────

VIDEO_SUBDIRS = ["phone-footage", "b-roll", "performances", "music-videos"]
VIDEO_EXTS    = {".mp4", ".mov", ".MP4", ".MOV"}
MIN_SIZE_MB   = 2


def _find_all_videos() -> list[Path]:
    videos = []
    for subdir in VIDEO_SUBDIRS:
        d = VIDEOS_DIR / subdir
        if not d.exists():
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix in VIDEO_EXTS and f.stat().st_size > MIN_SIZE_MB * 1_000_000:
                videos.append(f)
    return videos


def pick_source_video() -> Path:
    videos = _find_all_videos()
    if not videos:
        sys.exit("ERROR: no source videos found in content/videos/")
    rotation = json.loads(VIDEO_ROTATION.read_text()) if VIDEO_ROTATION.exists() else {}
    videos.sort(key=lambda p: rotation.get(str(p), 0))
    return videos[0]


def mark_video_used(video_path: Path):
    rotation = json.loads(VIDEO_ROTATION.read_text()) if VIDEO_ROTATION.exists() else {}
    rotation[str(video_path)] = int(time.time())
    VIDEO_ROTATION.write_text(json.dumps(rotation, indent=2))


# ─── Audio mixing ─────────────────────────────────────────────────────────────

def mix_in_track(clip_path: Path, audio_path: Path, audio_start: int, clip_duration: int):
    """Replace clip audio with a peak-energy segment from the RJM track."""
    tmp = clip_path.with_suffix(".tmp.mp4")
    clip_path.rename(tmp)
    fade_start   = max(0, clip_duration - 2)
    audio_filter = f"afade=t=out:st={fade_start}:d=2"
    cmd = [
        FFMPEG, "-y",
        "-i", str(tmp),
        "-ss", str(audio_start), "-t", str(clip_duration), "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-af", audio_filter,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(clip_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    tmp.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"Audio mix failed:\n{result.stderr[-500:]}")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _sep(title):
    print(f"\n{'─' * 60}\n  {title}\n{'─' * 60}")


def _safe_name(s: str) -> str:
    return re.sub(r"[^\w\s-]", "", s).strip().replace(" ", "-").lower()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Holy Rave daily content run.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate videos + captions locally. Skip Buffer posting."
    )
    parser.add_argument("--track", help="Force a specific track (partial name match).")
    args = parser.parse_args()

    mode = "[DRY RUN] " if args.dry_run else ""

    # ── 1. Source video ───────────────────────────────────────────────────────
    _sep("STEP 1 / 4 — Source video")
    video_path = pick_source_video()
    print(f"  File:   {video_path.name}")
    print(f"  Folder: {video_path.parent.name}/")

    try:
        info = processor.get_video_info(str(video_path))
    except Exception as e:
        sys.exit(f"ERROR: cannot read video — {e}")

    duration = info["duration"]
    print(f"  Duration: {duration:.1f}s  ({info['width']}×{info['height']})")

    clip_lengths = [l for l in processor.CLIP_LENGTHS if duration >= l]
    if not clip_lengths:
        sys.exit(f"ERROR: video too short ({duration:.1f}s) — need at least {min(processor.CLIP_LENGTHS)}s")
    print(f"  Clip lengths: {clip_lengths}s")

    # ── 2. RJM track + peak section ──────────────────────────────────────────
    _sep("STEP 2 / 4 — RJM track")
    audio_path, track_title = pick_next_track(args.track)
    print(f"  Track: {track_title}")
    print(f"  File:  {audio_path.name}")
    audio_start = find_peak_section(audio_path, max(clip_lengths))

    # ── 3. Hooks + captions ───────────────────────────────────────────────────
    _sep("STEP 3 / 4 — Hooks + captions (Claude)")
    angle_override = get_next_cycle_angle()
    print(f"  Angle: {angle_override} (auto-cycle)")
    hooks_meta = generator.generate_hooks(video_path.name, clip_lengths, angle_override=angle_override)
    content    = generator.generate_content(video_path.name, clip_lengths, hooks_meta)
    angle      = content.get("angle") or hooks_meta.get("angle")

    print()
    for length in clip_lengths:
        hook = hooks_meta["hooks"].get(length, "")
        print(f"  {length}s → \"{hook}\"")

    # ── 4. Cut clips + mix audio ──────────────────────────────────────────────
    _sep("STEP 4 / 4 — Cut clips + mix RJM audio")
    timestamp   = datetime.now().strftime("%Y-%m-%d_%H%M")
    safe_track  = _safe_name(track_title)
    run_dir     = OUTPUT_DIR / f"{timestamp}_{safe_track}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Output: {run_dir}\n")

    segments    = processor.detect_best_segments(str(video_path), duration)
    best_start  = segments[0][0] if segments else 0.0
    hooks_dict  = {int(k): v for k, v in hooks_meta["hooks"].items()}
    output_files = []

    for clip_len in clip_lengths:
        print(f"  → {clip_len}s clip…")
        out_file  = run_dir / f"{safe_track}_{clip_len}s.mp4"
        vid_start = min(best_start, max(0.0, duration - clip_len))

        processor.format_to_vertical(
            str(video_path), str(out_file),
            vid_start, clip_len,
            hooks_dict.get(clip_len, ""),
            angle,
        )
        mix_in_track(out_file, audio_path, audio_start, clip_len)

        size_mb = out_file.stat().st_size / 1_000_000
        print(f"    ✓ {out_file.name}  ({size_mb:.1f} MB)")
        output_files.append(out_file)

    # Captions file
    caption_text = generator.format_caption_file(video_path.name, content)
    caption_file = run_dir / f"{safe_track}_captions.txt"
    caption_file.write_text(caption_text)
    print(f"\n  ✓ {caption_file.name}")

    mark_track_used(track_title)
    mark_video_used(video_path)

    # ── Buffer (live only) ────────────────────────────────────────────────────
    if not args.dry_run:
        _sep("BUFFER — Queuing to TikTok / Instagram / YouTube")
        from buffer_poster import upload_video_and_queue
        for clip_len, clip_path in zip(clip_lengths, output_files):
            clips_data  = content.get("clips", {}).get(str(clip_len), {})
            tiktok      = clips_data.get("tiktok", {})
            instagram   = clips_data.get("instagram", {})
            youtube     = clips_data.get("youtube", {})
            print(f"\n  Queuing {clip_len}s…")
            try:
                upload_video_and_queue(
                    clip_path         = str(clip_path),
                    tiktok_caption    = tiktok.get("caption", "") + "\n" + tiktok.get("hashtags", ""),
                    instagram_caption = instagram.get("caption", "") + "\n" + instagram.get("hashtags", ""),
                    youtube_title     = youtube.get("title", ""),
                    youtube_desc      = youtube.get("description", ""),
                )
                print(f"    ✓ Queued")
            except Exception as e:
                print(f"    ✗ Buffer error: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    _sep("SUMMARY")
    print(f"\n{mode}Track:  {track_title}")
    print(f"{mode}Angle:  {angle or 'default'}")
    print(f"{mode}Source: {video_path.name} ({video_path.parent.name}/)")
    print(f"\n{mode}Clips produced ({len(output_files)}):")
    for f in output_files:
        print(f"  {f.name}  ({f.stat().st_size / 1_000_000:.1f} MB)")
    print(f"\n{mode}Captions: {caption_file.name}")
    print(f"{mode}Output:   {run_dir}")

    if args.dry_run:
        print("\n[DRY RUN] Buffer posting skipped.\n")
        print("── Caption preview (15s) ──────────────────────────")
        clip_15 = content.get("clips", {}).get("15", {})
        if clip_15:
            print(f"\nTikTok:\n  {clip_15.get('tiktok', {}).get('caption', '')}")
            print(f"  {clip_15.get('tiktok', {}).get('hashtags', '')}")
            print(f"\nInstagram:\n  {clip_15.get('instagram', {}).get('caption', '')}")
            yt = clip_15.get("youtube", {})
            print(f"\nYouTube title:\n  {yt.get('title', '')}")


if __name__ == "__main__":
    main()
