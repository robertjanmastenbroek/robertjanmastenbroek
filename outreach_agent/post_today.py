#!/usr/bin/env python3
"""
post_today.py — Holy Rave daily content run

1. Picks source videos from content/videos/ (rotation, 40/40/20 weighting)
2. Picks one of 4 active RJM tracks via rotation
3. Detects BPM — calculates beat-sync cut points (4/4 bar length)
4. Single Claude call: hooks for all 3 angles (emotional/signal/energy)
5. Single Claude call: unique captions for all 3 clips
6. Cuts 3 vertical multi-clip reels with burned-in hooks + RJM audio
7. Saves to content/output/YYYY-MM-DD_HHMM_trackname/
8. (Live run) Queues to Buffer: TikTok + Instagram Reels + YouTube Shorts

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

def _find_base() -> Path:
    """
    Find the project root that contains content/.
    Handles both normal layout (outreach_agent/../) and git worktrees
    where the code lives inside .claude/worktrees/<name>/.
    """
    candidate = Path(__file__).parent.parent
    if (candidate / "content").exists():
        return candidate
    # Worktree case: walk up until we find a directory with content/
    for parent in candidate.parents:
        if (parent / "content").exists():
            return parent
        # Stop at filesystem root
        if parent == parent.parent:
            break
    return candidate  # fall back to original

BASE           = _find_base()
VIDEOS_DIR     = BASE / "content" / "videos"
OUTPUT_DIR     = BASE / "content" / "output"
TRACK_ROTATION = Path(__file__).parent / "track_rotation.json"
VIDEO_ROTATION = Path(__file__).parent / "video_rotation.json"

import generator   # outreach_agent/generator.py
import processor   # outreach_agent/processor.py

FFMPEG  = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

# ─── Angle-per-clip mapping ───────────────────────────────────────────────────
# Each clip length gets a fixed angle every run.
# 5s  = emotional (artist interior — why this exists, what it cost)
# 9s  = signal    (who this track is for — specific person/moment)
# 15s = energy    (what happens to a body in that room)

CLIP_ANGLE_MAP = {5: "emotional", 9: "signal", 15: "energy"}

# ─── Active tracks (only these 4 are currently being promoted) ───────────────

ACTIVE_TRACKS = {"halleluyah", "renamed", "jericho", "fire in our hands"}

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

                # Only promote the 4 active tracks
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
        sys.exit("ERROR: no active RJM tracks found in audio directories.")

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
    """
    Find the start time (seconds) of the best beat-dense section to use as audio.

    Uses librosa onset-strength × RMS to score every 1-second window, then picks
    the window with the highest combined score.  Onset strength rewards sections
    where beats are landing consistently — so a flat intro or quiet breakdown will
    score low even if it's loud.

    Skip window:
      - First 60 s (intro ramp-up — beats usually not fully in yet)
      - Last  30 s (outro fade)
    """
    try:
        import librosa
        import numpy as np

        total      = _get_duration(audio_path)
        scan_start = 60                                  # skip intro
        scan_end   = max(scan_start + clip_duration, total - 30)
        load_dur   = scan_end - scan_start

        print(f"  Scanning {audio_path.name} ({total//60}:{total%60:02d}) "
              f"for best beat section (t={scan_start}s–{scan_end}s)…")

        y, sr = librosa.load(str(audio_path), sr=22050, mono=True,
                             offset=scan_start, duration=load_dur)

        hop      = 512
        fps      = sr / hop                              # frames per second

        onset    = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        rms      = librosa.feature.rms(y=y, hop_length=hop)[0]

        n        = min(len(onset), len(rms))
        score    = onset[:n] * rms[:n]

        win_frames = max(1, int(clip_duration * fps))
        step       = max(1, int(fps))                    # step 1 s at a time

        best_t     = scan_start
        best_score = -1.0
        for i in range(0, n - win_frames, step):
            s = float(np.mean(score[i : i + win_frames]))
            if s > best_score:
                best_score = s
                best_t     = scan_start + i / fps

        best_t = int(min(best_t, total - clip_duration))
        print(f"  Peak beat section at {best_t}s  (score={best_score:.5f})")
        return best_t

    except Exception as e:
        print(f"  WARN: librosa beat-scan failed ({e}) — falling back to t=60s")
        total = _get_duration(audio_path)
        return min(60, max(0, total - clip_duration))


def _snap_to_beat(audio_path: Path, target_start: int) -> int:
    """
    Snap target_start to the nearest beat in the audio track.
    Loads a 12-second window around target to find beat positions.
    """
    try:
        import librosa
        offset = max(0, target_start - 3)
        y, sr  = librosa.load(str(audio_path), offset=offset, duration=12,
                               sr=22050, mono=True)
        _, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times     = librosa.frames_to_time(beat_frames, sr=sr) + offset
        if len(beat_times) == 0:
            return target_start
        snapped = int(round(min(beat_times, key=lambda t: abs(t - target_start))))
        return snapped
    except Exception:
        return target_start


# ─── Source video rotation ────────────────────────────────────────────────────
# b-roll/ and performances/ each get 40% weight; phone-footage/ gets 20%.
# Within each pool, least-recently-used videos are picked first.

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


def pick_source_videos(count: int, exclude: set = None) -> list[Path]:
    """
    Pick `count` unique source videos.
    Alternates between categories: performances → b-roll → phone → performances → …
    This ensures visual variety within each clip's segments.
    Within each category, least-recently-used videos are picked first.
    Excludes any paths in `exclude`.
    """
    if exclude is None:
        exclude = set()

    rotation = json.loads(VIDEO_ROTATION.read_text()) if VIDEO_ROTATION.exists() else {}

    broll = sorted(_find_videos_in("b-roll"),        key=lambda p: rotation.get(str(p), 0))
    perf  = sorted(_find_videos_in("performances"),  key=lambda p: rotation.get(str(p), 0))
    phone = sorted(_find_videos_in("phone-footage"), key=lambda p: rotation.get(str(p), 0))

    if not broll and not perf and not phone:
        sys.exit("ERROR: no source videos found in content/videos/")

    # Interleave: perf → broll → phone → perf → broll → phone → …
    # This guarantees alternating visual variety rather than a run of the same type.
    categories = [lst for lst in [perf, broll, phone] if lst]
    iters      = [iter(lst) for lst in categories]
    interleaved: list[Path] = []
    while iters:
        for it in list(iters):
            try:
                interleaved.append(next(it))
            except StopIteration:
                iters.remove(it)

    picked, seen = [], set(exclude)
    for v in interleaved:
        if str(v) not in seen:
            picked.append(v)
            seen.add(str(v))
        if len(picked) == count:
            break

    # Fallback: cycle without exclude constraint
    if len(picked) < count:
        for v in interleaved:
            if str(v) not in seen:
                picked.append(v)
                seen.add(str(v))
            if len(picked) == count:
                break

    return picked


def mark_video_used(video_path: Path):
    rotation = json.loads(VIDEO_ROTATION.read_text()) if VIDEO_ROTATION.exists() else {}
    rotation[str(video_path)] = int(time.time())
    VIDEO_ROTATION.write_text(json.dumps(rotation, indent=2))


# ─── Audio mixing ─────────────────────────────────────────────────────────────

def mix_in_track(clip_path: Path, audio_path: Path, audio_start: int, clip_duration: int):
    """Replace clip audio with a peak-energy segment from the RJM track."""
    tmp = clip_path.with_suffix(".tmp.mp4")
    clip_path.rename(tmp)
    fade_start   = max(0, clip_duration - 0.8)
    audio_filter = f"afade=t=out:st={fade_start:.3f}:d=0.8"
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

    # ── 1. RJM track + peak section + BPM ────────────────────────────────────
    _sep("STEP 1 / 4 — RJM track + BPM")
    audio_path, track_title = pick_next_track(args.track)
    print(f"  Track: {track_title}")
    print(f"  File:  {audio_path.name}")
    audio_start = find_peak_section(audio_path, max(processor.CLIP_LENGTHS))

    bpm     = processor.get_bpm(str(audio_path))
    bar_dur = 4 * 60.0 / bpm   # one 4/4 bar in seconds
    print(f"  BPM:   {bpm:.1f}  (bar = {bar_dur:.2f}s)")

    # ── 2. Source videos — one pool per clip, beat-synced start points ────────
    _sep("STEP 2 / 4 — Source videos + beat-sync")
    clip_lengths = list(processor.CLIP_LENGTHS)   # [5, 9, 15]

    per_clip = {}   # clip_len → {video_sources, angle, n_segs}
    all_used = set()

    for clip_len in clip_lengths:
        angle  = CLIP_ANGLE_MAP.get(clip_len, "emotional")
        # n_segs: 5s clips use 2-beat (half-bar) cuts for faster pacing;
        # longer clips use full 4/4 bars.
        if clip_len <= 5:
            n_segs = max(4, round(clip_len / (bar_dur / 2)))
        else:
            n_segs = max(2, round(clip_len / bar_dur))
        seg_dur = clip_len / n_segs

        print(f"\n  {clip_len}s [{angle}] — {n_segs} segments × {seg_dur:.2f}s  (4/4 at {bpm:.0f} BPM)")

        sources_raw = pick_source_videos(n_segs, exclude=all_used)
        sources     = []

        for vpath in sources_raw:
            try:
                info     = processor.get_video_info(str(vpath))
                vid_dur  = info["duration"]
            except Exception as e:
                print(f"    WARN: cannot read {vpath.name} — {e}, skipping")
                continue

            if vid_dur < seg_dur:
                print(f"    WARN: {vpath.name} too short ({vid_dur:.1f}s) for {seg_dur:.1f}s segment, skipping")
                continue

            segs       = processor.detect_best_segments(str(vpath), vid_dur)
            raw_start  = segs[0][0] if segs else 0.0
            raw_start  = min(raw_start, max(0.0, vid_dur - seg_dur))
            beat_start = _snap_to_beat(audio_path, int(raw_start))
            beat_start = min(float(beat_start), max(0.0, vid_dur - seg_dur))

            sources.append((str(vpath), beat_start))
            all_used.add(str(vpath))
            print(f"    {vpath.parent.name}/{vpath.name}  start={beat_start:.1f}s  ({vid_dur:.1f}s total)")

        if not sources:
            # Fallback: pick any video, start at 0
            fallback = pick_source_videos(1)
            if fallback:
                sources = [(str(fallback[0]), 0.0)]
                all_used.add(str(fallback[0]))
                print(f"    FALLBACK: {fallback[0].name}")

        per_clip[clip_len] = {
            "video_sources": sources,
            "angle":  angle,
            "n_segs": n_segs,
        }

    # ── 3. Hooks + captions — single Claude call each ─────────────────────────
    _sep("STEP 3 / 4 — Hooks + captions (2 Claude calls)")

    clips_config = [
        {"length": cl, "angle": per_clip[cl]["angle"]}
        for cl in clip_lengths
    ]

    print("  Generating hooks (all 3 angles — 1 call)…")
    run_hooks = generator.generate_run_hooks(track_title, clips_config)
    for cl in clip_lengths:
        abc = run_hooks.get(cl, {})
        print(f"    {cl}s [{per_clip[cl]['angle']}]")
        print(f"      A: \"{abc.get('a', '')}\"")
        print(f"      B: \"{abc.get('b', '')}\"")
        print(f"      C: \"{abc.get('c', '')}\"")

    print("\n  Generating captions (all 3 clips — 1 call)…")
    clips_data = [
        {
            "length":  cl,
            "angle":   per_clip[cl]["angle"],
            "hook_a":  run_hooks.get(cl, {}).get("a", ""),
            "hook_b":  run_hooks.get(cl, {}).get("b", ""),
            "hook_c":  run_hooks.get(cl, {}).get("c", ""),
        }
        for cl in clip_lengths
    ]
    run_captions = generator.generate_run_captions(track_title, clips_data)

    # ── 4. Cut clips + mix audio ──────────────────────────────────────────────
    _sep("STEP 4 / 4 — Cut clips + mix RJM audio")
    timestamp  = datetime.now().strftime("%Y-%m-%d_%H%M")
    safe_track = _safe_name(track_title)
    run_dir    = OUTPUT_DIR / f"{timestamp}_{safe_track}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Output: {run_dir}\n")

    output_files = []

    for clip_len in clip_lengths:
        clip_data = per_clip[clip_len]
        angle     = clip_data["angle"]
        sources   = clip_data["video_sources"]
        hook_a    = run_hooks.get(clip_len, {}).get("a", "")

        print(f"  → {clip_len}s [{angle}]  {len(sources)} segments")
        out_file = run_dir / f"{safe_track}_{clip_len}s.mp4"

        processor.format_to_vertical_multiclip(
            video_sources=sources,
            output_path=str(out_file),
            clip_duration=float(clip_len),
            hook_text=hook_a,
            angle=angle,
        )
        mix_in_track(out_file, audio_path, audio_start, clip_len)

        size_mb = out_file.stat().st_size / 1_000_000
        print(f"    ✓ {out_file.name}  ({size_mb:.1f} MB)")
        output_files.append(out_file)

    # ── Captions file ─────────────────────────────────────────────────────────
    caption_lines = []
    for cl in clip_lengths:
        abc   = run_hooks.get(cl, {})
        caps  = run_captions.get(cl, {})
        angle = per_clip[cl]["angle"]

        caption_lines += [
            f"┌─────────────────────────────────────────────",
            f"│  {cl}-SECOND CLIP  [{angle.upper()}]",
            f"│  File: {safe_track}_{cl}s.mp4",
            f"└─────────────────────────────────────────────",
            "",
            "HOOKS (A/B/C — post same clip on different days with each variant):",
            f'   A: "{abc.get("a", "")}"',
            f'   B: "{abc.get("b", "")}"',
            f'   C: "{abc.get("c", "")}"',
            "",
            "━━━ TIKTOK ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"   {caps.get('tiktok', {}).get('caption', '')}",
            f"   {caps.get('tiktok', {}).get('hashtags', '')}",
            "",
            "━━━ INSTAGRAM REELS ━━━━━━━━━━━━━━━━━━━━━━━━",
            f"   {caps.get('instagram', {}).get('caption', '')}",
            f"   {caps.get('instagram', {}).get('hashtags', '')}",
            "",
            "━━━ YOUTUBE SHORTS ━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"   {caps.get('youtube', {}).get('title', '')}",
            f"   {caps.get('youtube', {}).get('description', '')}",
            "",
            "",
        ]

    caption_lines += [
        "═══════════════════════════════════════════════",
        "  All glory to Jesus.",
        "═══════════════════════════════════════════════",
    ]

    caption_file = run_dir / f"{safe_track}_captions.txt"
    caption_file.write_text('\n'.join(caption_lines))
    print(f"\n  ✓ {caption_file.name}")

    mark_track_used(track_title)
    for cl in clip_lengths:
        for src_path, _ in per_clip[cl]["video_sources"]:
            mark_video_used(Path(src_path))

    # ── Buffer (live only) ────────────────────────────────────────────────────
    if not args.dry_run:
        _sep("BUFFER — Queuing to TikTok / Instagram / YouTube")
        from buffer_poster import upload_video_and_queue
        for clip_len, clip_path in zip(clip_lengths, output_files):
            caps = run_captions.get(clip_len, {})
            print(f"\n  Queuing {clip_len}s [{per_clip[clip_len]['angle']}]…")
            try:
                upload_video_and_queue(
                    clip_path         = str(clip_path),
                    tiktok_caption    = caps.get('tiktok', {}).get('caption', '') + "\n" + caps.get('tiktok', {}).get('hashtags', ''),
                    instagram_caption = caps.get('instagram', {}).get('caption', '') + "\n" + caps.get('instagram', {}).get('hashtags', ''),
                    youtube_title     = caps.get('youtube', {}).get('title', ''),
                    youtube_desc      = caps.get('youtube', {}).get('description', ''),
                )
                print(f"    ✓ Queued")
            except Exception as e:
                print(f"    ✗ Buffer error: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    _sep("SUMMARY")
    print(f"\n{mode}Track:  {track_title}  ({bpm:.1f} BPM)")
    print(f"{mode}Clips produced ({len(output_files)}):")
    for cl, f in zip(clip_lengths, output_files):
        n  = per_clip[cl]["n_segs"]
        ag = per_clip[cl]["angle"]
        print(f"  {f.name}  [{ag}]  {n} beat-synced segments  ({f.stat().st_size / 1_000_000:.1f} MB)")
    print(f"\n{mode}Captions: {caption_file.name}")
    print(f"{mode}Output:   {run_dir}")

    if args.dry_run:
        print("\n[DRY RUN] Buffer posting skipped.\n")
        for cl in clip_lengths:
            angle = per_clip[cl]["angle"]
            abc   = run_hooks.get(cl, {})
            caps  = run_captions.get(cl, {})
            print(f"── {cl}s [{angle}] ──────────────────────────────")
            print(f"  Hook A:    {abc.get('a', '')}")
            print(f"  Hook B:    {abc.get('b', '')}")
            print(f"  Hook C:    {abc.get('c', '')}")
            print(f"  TikTok:    {caps.get('tiktok', {}).get('caption', '')}")
            print(f"  Instagram: {caps.get('instagram', {}).get('caption', '')}")
            print(f"  YT title:  {caps.get('youtube', {}).get('title', '')}")
            print()


if __name__ == "__main__":
    main()
