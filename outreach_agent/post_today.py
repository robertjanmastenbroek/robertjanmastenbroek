#!/usr/bin/env python3
"""
post_today.py — Holy Rave daily content run

1. Picks 3 source videos (40% b-roll / 40% performances / 20% phone-footage)
2. Picks the next of 4 active tracks (Halleluyah / Renamed / Jericho / Fire In Our Hands)
3. Finds peak-energy section, beat-synced to track tempo (librosa)
4. Single Claude call generates hooks for all 3 angles simultaneously (ensures uniqueness)
5. Cuts 3 vertical 9:16 clips (center-crop, hook A burned in with Impact font)
6. Mixes in RJM audio, saves captions.txt with A/B/C hooks
7. (Live) Queues to Buffer: TikTok + Instagram Reels + YouTube Shorts

Usage:
  python3 post_today.py           # live run
  python3 post_today.py --dry-run # generate + skip Buffer
  python3 post_today.py --track "Jericho"
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ─── Load .env ────────────────────────────────────────────────────────────────

def _load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE           = Path(__file__).parent.parent
VIDEOS_DIR     = BASE / "content" / "videos"
OUTPUT_DIR     = BASE / "content" / "output"
TRACK_ROTATION = Path(__file__).parent / "track_rotation.json"
VIDEO_ROTATION = Path(__file__).parent / "video_rotation.json"

import generator
import processor

FFMPEG  = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

# ─── Clip → angle mapping ─────────────────────────────────────────────────────
CLIP_ANGLE_MAP = {5: "emotional", 9: "signal", 15: "energy"}

# ─── Active tracks (only these 4 are promoted) ───────────────────────────────
# All other tracks are excluded regardless of what's in the audio directories.
ACTIVE_TRACKS = {
    "halleluyah",
    "renamed",
    "jericho",
    "fire in our hands",
}

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
    r"\(Edit\)", r"\(MP3\)", r"RENAMED IN THE LIGHT",
    r"You're At The Door", r"He Has Been Good To Me",
]

# Note: "DJ Lucid" removed from skip list — Fire In Our Hands has a LUCID collab version
# that would otherwise be skipped.


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
                title = re.sub(r"& (DJ )?LUCID\s*[-–]\s*", "- ", title, flags=re.IGNORECASE)
                title = re.sub(r"_MASTER(_)?$", "", title, flags=re.IGNORECASE)
                title = re.sub(r"\s*MASTER$", "", title, flags=re.IGNORECASE)
                title = title.replace("_", " ").strip()
                dedup = re.sub(r"\s*(final|master)\s*$", "", title.lower()).strip()
                dedup = re.sub(r"\s*\(\d+\)\s*$", "", dedup).strip()
                dedup = re.sub(r"\s*-\s*psalm\s*\d+.*$", "", dedup).strip()
                dedup = re.sub(r"^title\s+", "", dedup).strip()

                # Only include the 4 active tracks
                if not any(active in dedup for active in ACTIVE_TRACKS):
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
        sys.exit("ERROR: none of the 4 active tracks found in audio directories.")

    if override:
        matches = [(p, t) for p, t in tracks if override.lower() in t.lower()]
        if not matches:
            sys.exit(f"ERROR: no track matching '{override}' among active tracks")
        return matches[0]

    rotation = json.loads(TRACK_ROTATION.read_text()) if TRACK_ROTATION.exists() else {}
    tracks.sort(key=lambda item: rotation.get(item[1].lower(), 0))
    return tracks[0]


def mark_track_used(title: str):
    rotation = json.loads(TRACK_ROTATION.read_text()) if TRACK_ROTATION.exists() else {}
    rotation[title.lower()] = int(time.time())
    TRACK_ROTATION.write_text(json.dumps(rotation, indent=2))


# ─── Audio analysis + beat sync ───────────────────────────────────────────────

def _get_duration(path: Path) -> int:
    result = subprocess.run(
        [FFPROBE, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    return int(float(result.stdout.strip() or "0"))


def _snap_to_beat(audio_path: Path, target_start: int) -> int:
    """
    Snap target_start to the nearest beat in the audio track using librosa.
    Falls back gracefully if librosa fails.
    """
    try:
        import librosa
        offset = max(0, target_start - 3)
        y, sr  = librosa.load(str(audio_path), offset=offset, duration=12,
                               sr=22050, mono=True)
        _, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        if len(beat_frames) == 0:
            return target_start
        beat_times = librosa.frames_to_time(beat_frames, sr=sr) + offset
        snapped = int(round(min(beat_times, key=lambda t: abs(t - target_start))))
        print(f"  Beat-sync: {target_start}s → {snapped}s")
        return snapped
    except Exception as e:
        print(f"  Beat-sync skipped ({e})")
        return target_start


def find_peak_section(audio_path: Path, clip_duration: int = 30) -> int:
    """Return beat-synced start time (seconds) of peak-energy section."""
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
    return _snap_to_beat(audio_path, best_start)


# ─── Source video selection (40% b-roll / 40% performances / 20% phone-footage) ──

VIDEO_EXTS  = {".mp4", ".mov", ".MP4", ".MOV"}
MIN_SIZE_MB = 2


def _find_videos_in(subdir: str) -> list[Path]:
    d = VIDEOS_DIR / subdir
    if not d.exists():
        return []
    return [
        f for f in sorted(d.rglob("*"))
        if f.suffix in VIDEO_EXTS and f.stat().st_size > MIN_SIZE_MB * 1_000_000
    ]


def pick_source_videos(count: int = 3) -> list[Path]:
    """
    Pick `count` different source videos with weighted random selection.
    Weights: 40% b-roll, 40% performances, 20% phone-footage.
    Within each pool, least-recently-used videos are prioritised.
    """
    rotation = json.loads(VIDEO_ROTATION.read_text()) if VIDEO_ROTATION.exists() else {}

    b_roll = sorted(_find_videos_in("b-roll"),        key=lambda p: rotation.get(str(p), 0))
    perf   = sorted(_find_videos_in("performances"),  key=lambda p: rotation.get(str(p), 0))
    phone  = sorted(_find_videos_in("phone-footage"), key=lambda p: rotation.get(str(p), 0))

    all_available = [v for pool in (b_roll, perf, phone) for v in pool]
    if not all_available:
        sys.exit("ERROR: no source videos found in content/videos/")

    # Build weighted pools, skip empty ones
    raw_pools   = [(b_roll, 0.40), (perf, 0.40), (phone, 0.20)]
    active      = [(pool, w) for pool, w in raw_pools if pool]
    pools       = [pool for pool, _ in active]
    total_w     = sum(w for _, w in active)
    weights     = [w / total_w for _, w in active]

    # Counters track next-to-pick index within each pool
    indices = [0] * len(pools)

    picked, seen = [], set()
    # Try weighted random picks up to count*15 attempts
    for _ in range(count * 15):
        if len(picked) == count:
            break
        pi = random.choices(range(len(pools)), weights=weights, k=1)[0]
        pool = pools[pi]
        # Find next unseen video from this pool
        while indices[pi] < len(pool):
            v = pool[indices[pi]]
            indices[pi] += 1
            if str(v) not in seen:
                picked.append(v)
                seen.add(str(v))
                break

    # Fallback: any remaining unseen video (LRU order)
    if len(picked) < count:
        remaining = sorted(
            (v for v in all_available if str(v) not in seen),
            key=lambda p: rotation.get(str(p), 0),
        )
        for v in remaining:
            if len(picked) >= count:
                break
            picked.append(v)
            seen.add(str(v))

    return picked[:count]


def mark_video_used(video_path: Path):
    rotation = json.loads(VIDEO_ROTATION.read_text()) if VIDEO_ROTATION.exists() else {}
    rotation[str(video_path)] = int(time.time())
    VIDEO_ROTATION.write_text(json.dumps(rotation, indent=2))


# ─── Audio mixing ─────────────────────────────────────────────────────────────

def mix_in_track(clip_path: Path, audio_path: Path, audio_start: int, clip_duration: int):
    """Replace clip audio with a beat-synced segment from the RJM track."""
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
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate locally. Skip Buffer posting.")
    parser.add_argument("--track", help="Force a specific track (partial name match).")
    args = parser.parse_args()

    mode = "[DRY RUN] " if args.dry_run else ""

    # ── 1. Source videos ─────────────────────────────────────────────────────
    _sep("STEP 1 / 4 — Source videos  (40% b-roll / 40% performances / 20% phone)")
    clip_lengths  = processor.CLIP_LENGTHS  # [5, 9, 15]
    source_videos = pick_source_videos(len(clip_lengths))

    valid_pairs = []
    for clip_len, vpath in zip(clip_lengths, source_videos):
        try:
            info = processor.get_video_info(str(vpath))
        except Exception as e:
            print(f"  WARN: cannot read {vpath.name} — {e}, skipping")
            continue
        if info["duration"] < clip_len:
            print(f"  WARN: {vpath.name} too short ({info['duration']:.1f}s), skipping")
            continue
        valid_pairs.append((clip_len, vpath, info))
        folder = vpath.parent.parent.name if vpath.parent.name.startswith("wetransfer") else vpath.parent.name
        print(f"  {clip_len}s [{CLIP_ANGLE_MAP.get(clip_len,'?')}] → {folder}/{vpath.name}  "
              f"({info['duration']:.1f}s  {info['width']}×{info['height']})")

    if not valid_pairs:
        sys.exit("ERROR: no valid video/clip-length pairs found")

    clip_lengths = [cl for cl, _, _ in valid_pairs]

    # ── 2. RJM track + beat-synced peak section ───────────────────────────────
    _sep("STEP 2 / 4 — RJM track (1 of 4 active tracks)")
    audio_path, track_title = pick_next_track(args.track)
    print(f"  Track: {track_title}")
    print(f"  File:  {audio_path.name}")
    audio_start = find_peak_section(audio_path, max(clip_lengths))

    # ── 3. Hooks — single call for all 3 angles (guarantees uniqueness) ───────
    _sep("STEP 3 / 4 — Hooks + captions (one Claude call for all 3 angles)")

    # Build the clip config for the multi-angle call
    clips_config = [
        {"length": cl, "angle": CLIP_ANGLE_MAP.get(cl, "emotional")}
        for cl, _, _ in valid_pairs
    ]
    run_hooks = generator.generate_run_hooks(track_title, clips_config)

    per_clip = {}
    for clip_len, vpath, info in valid_pairs:
        angle   = CLIP_ANGLE_MAP.get(clip_len, "emotional")
        abc     = run_hooks.get(clip_len, {})
        print(f"  {clip_len}s [{angle}]")
        print(f"    A: \"{abc.get('a', '')}\"")
        print(f"    B: \"{abc.get('b', '')}\"")
        print(f"    C: \"{abc.get('c', '')}\"")

        # Build hooks_meta for generate_content
        hooks_meta = {
            "track_name": track_title,
            "angle":      angle,
            "seed_hint":  None,
            "hooks":      {clip_len: abc},
        }
        content = generator.generate_content(vpath.name, [clip_len], hooks_meta)

        per_clip[clip_len] = {
            "video_path": vpath, "info": info,
            "angle":      angle,
            "hook":       abc.get("a", ""),
            "hooks_abc":  abc,
            "content":    content,
        }

    # ── 4. Cut clips + mix audio ──────────────────────────────────────────────
    _sep("STEP 4 / 4 — Cut clips (9:16 center-crop) + mix RJM audio")
    timestamp  = datetime.now().strftime("%Y-%m-%d_%H%M")
    safe_track = _safe_name(track_title)
    run_dir    = OUTPUT_DIR / f"{timestamp}_{safe_track}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Output: {run_dir}\n")

    output_files = []
    for clip_len in clip_lengths:
        clip_data = per_clip[clip_len]
        vpath     = clip_data["video_path"]
        duration  = clip_data["info"]["duration"]
        folder    = vpath.parent.parent.name if vpath.parent.name.startswith("wetransfer") else vpath.parent.name
        print(f"  → {clip_len}s [{clip_data['angle']}]  {folder}/{vpath.name}")
        out_file = run_dir / f"{safe_track}_{clip_len}s.mp4"

        segments  = processor.detect_best_segments(str(vpath), duration)
        vid_start = segments[0][0] if segments else 0.0
        vid_start = min(vid_start, max(0.0, duration - clip_len))

        processor.format_to_vertical(
            str(vpath), str(out_file),
            vid_start, clip_len,
            clip_data["hook"],    # hook A burned in with Impact font
            clip_data["angle"],
        )
        mix_in_track(out_file, audio_path, audio_start, clip_len)

        size_mb = out_file.stat().st_size / 1_000_000
        print(f"    ✓ {out_file.name}  ({size_mb:.1f} MB)")
        output_files.append(out_file)

    # Captions file
    caption_lines = []
    for clip_len in clip_lengths:
        caption_lines.append(generator.format_caption_file(
            per_clip[clip_len]["video_path"].name, per_clip[clip_len]["content"]
        ))
    caption_file = run_dir / f"{safe_track}_captions.txt"
    caption_file.write_text("\n\n".join(caption_lines))
    print(f"\n  ✓ {caption_file.name}")

    mark_track_used(track_title)
    for clip_len in clip_lengths:
        mark_video_used(per_clip[clip_len]["video_path"])

    # ── Buffer (live only) ────────────────────────────────────────────────────
    if not args.dry_run:
        _sep("BUFFER — Queuing to TikTok / Instagram / YouTube")
        from buffer_poster import upload_video_and_queue
        for clip_len, clip_path in zip(clip_lengths, output_files):
            clips_data = per_clip[clip_len]["content"].get("clips", {}).get(str(clip_len), {})
            tiktok     = clips_data.get("tiktok", {})
            instagram  = clips_data.get("instagram", {})
            youtube    = clips_data.get("youtube", {})
            print(f"\n  Queuing {clip_len}s [{per_clip[clip_len]['angle']}]…")
            try:
                upload_video_and_queue(
                    clip_path         = str(clip_path),
                    tiktok_caption    = tiktok.get("caption","") + "\n" + tiktok.get("hashtags",""),
                    instagram_caption = instagram.get("caption","") + "\n" + instagram.get("hashtags",""),
                    youtube_title     = youtube.get("title",""),
                    youtube_desc      = youtube.get("description",""),
                )
                print(f"    ✓ Queued")
            except Exception as e:
                print(f"    ✗ Buffer error: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    _sep("SUMMARY")
    print(f"\n{mode}Track:  {track_title}")
    print(f"\n{mode}Clips produced ({len(output_files)}):")
    for clip_len, f in zip(clip_lengths, output_files):
        vp    = per_clip[clip_len]["video_path"]
        angle = per_clip[clip_len]["angle"]
        print(f"  {f.name}  [{angle}]  ({f.stat().st_size/1_000_000:.1f} MB)")
    print(f"\n{mode}Captions: {caption_file.name}")
    print(f"{mode}Output:   {run_dir}")

    if args.dry_run:
        print("\n[DRY RUN] Buffer posting skipped.\n")
        for clip_len in clip_lengths:
            angle     = per_clip[clip_len]["angle"]
            abc       = per_clip[clip_len]["hooks_abc"]
            clip_data = per_clip[clip_len]["content"].get("clips", {}).get(str(clip_len), {})
            print(f"── {clip_len}s [{angle}] ──────────────────────────────")
            print(f"  Hook A:    {abc.get('a','')}")
            print(f"  Hook B:    {abc.get('b','')}")
            print(f"  Hook C:    {abc.get('c','')}")
            print(f"  TikTok:    {clip_data.get('tiktok',{}).get('caption','')}")
            print(f"  Instagram: {clip_data.get('instagram',{}).get('caption','')}")
            print(f"  YT title:  {clip_data.get('youtube',{}).get('title','')}")
            print()


if __name__ == "__main__":
    main()
