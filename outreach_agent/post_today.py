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
8. (Live run) Queues to Buffer: TikTok + Instagram Reels + Instagram Story + YouTube Shorts

Usage:
  python3 post_today.py           # live run
  python3 post_today.py --dry-run # generate videos + captions, skip Buffer
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
# Angles renamed to match TikTok algorithm logic (contrast/body-drop/identity).
# Clip lengths updated for 2026 watch-event and completion-rate optimisation:
#   7s  = contrast  (cognitive dissonance — crowd doing something unexpected)
#   15s = body-drop (audio kicks in hard, crowd reacts physically — drives saves)
#   28s = identity  ("if you've felt this, you already belong" — drives follows)

CLIP_ANGLE_MAP = {7: "contrast", 15: "body-drop", 28: "identity"}

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


AUDIO_ROTATION = Path(__file__).parent / "audio_rotation.json"


def _find_top_sections(audio_path: Path, clip_duration: int, n: int = 5) -> list:
    """
    Scan the track and return up to N non-overlapping high-energy sections,
    sorted by time (natural listening order). Sections are spaced at least
    clip_duration seconds apart so they don't overlap.
    """
    try:
        import librosa
        import numpy as np

        total      = _get_duration(audio_path)
        scan_start = 60
        scan_end   = max(scan_start + clip_duration, total - 30)
        load_dur   = scan_end - scan_start

        print(f"  Scanning {audio_path.name} ({total//60}:{total%60:02d}) "
              f"for top sections (t={scan_start}s–{int(scan_end)}s)…")

        y, sr = librosa.load(str(audio_path), sr=22050, mono=True,
                             offset=scan_start, duration=load_dur)

        hop  = 512
        fps  = sr / hop
        onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        rms   = librosa.feature.rms(y=y, hop_length=hop)[0]
        n_f   = min(len(onset), len(rms))
        score = onset[:n_f] * rms[:n_f]

        win_frames = max(1, int(clip_duration * fps))
        step       = max(1, int(fps))

        windows = []
        for i in range(0, n_f - win_frames, step):
            s = float(np.mean(score[i: i + win_frames]))
            t = int(min(scan_start + i / fps, total - clip_duration))
            windows.append((s, t))

        windows.sort(reverse=True)

        # Pick top N non-overlapping (min gap = clip_duration seconds)
        selected = []
        for s, t in windows:
            if all(abs(t - prev) >= clip_duration for prev in selected):
                selected.append(t)
                if len(selected) >= n:
                    break

        selected.sort()  # chronological order
        return selected if selected else [scan_start]

    except Exception as e:
        print(f"  WARN: librosa scan failed ({e}) — using t=60s")
        total = _get_duration(audio_path)
        return [min(60, max(0, total - clip_duration))]


def find_peak_section(audio_path: Path, clip_duration: int = 30, n_results: int = 1) -> list:
    """
    Return n_results audio section start times for this track, rotating through
    the top-N scoring sections so each clip in a run uses a different part of the song.
    Sections are cached in audio_rotation.json; the index advances by n_results each call.
    """
    track_key = audio_path.stem.lower()

    try:
        state = json.loads(AUDIO_ROTATION.read_text()) if AUDIO_ROTATION.exists() else {}
    except Exception:
        state = {}

    entry    = state.get(track_key, {})
    sections = entry.get("sections", [])

    if not sections:
        sections = _find_top_sections(audio_path, clip_duration, n=5)
        print(f"  Candidate sections: {sections}")
    else:
        print(f"  Cached sections: {sections}")

    next_idx = entry.get("next_idx", 0) % len(sections)

    # Return n_results consecutive sections starting at next_idx, advance by n_results
    result = [sections[(next_idx + i) % len(sections)] for i in range(n_results)]

    state[track_key] = {"sections": sections, "next_idx": (next_idx + n_results) % len(sections)}
    try:
        AUDIO_ROTATION.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"  WARN: Could not save audio rotation: {e}")

    for i, t in enumerate(result):
        print(f"  Audio section {next_idx + i + 1}/{len(sections)} at {t}s")
    return result


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


def pick_source_videos(count: int, exclude: set = None, lead_cat: str = 'perf') -> list[Path]:
    """
    Pick `count` unique source videos, starting with `lead_cat` category.
    lead_cat controls the visual mood of the clip:
      'perf'  → performances-led (epic, crowd energy)
      'broll' → b-roll-led (atmospheric, landscape)
      'phone' → phone-footage-led (raw, intimate)
    Within each category, least-recently-used videos are picked first.
    Excludes any paths in `exclude`.
    """
    if exclude is None:
        exclude = set()

    rotation = json.loads(VIDEO_ROTATION.read_text()) if VIDEO_ROTATION.exists() else {}

    def _sort_with_jitter(videos: list) -> list:
        """Sort by LRU, but shuffle clips with the same day-level timestamp so
        re-runs through a fully-used library always produce fresh combinations."""
        DAY = 86400
        # Group by day bucket, shuffle within each bucket, then sort buckets ascending
        from collections import defaultdict
        buckets: dict = defaultdict(list)
        for p in videos:
            ts = rotation.get(str(p), 0)
            buckets[ts // DAY].append(p)
        result = []
        for day_key in sorted(buckets):
            group = buckets[day_key]
            random.shuffle(group)
            result.extend(group)
        return result

    broll = _sort_with_jitter(_find_videos_in("b-roll"))
    perf  = _sort_with_jitter(_find_videos_in("performances"))
    phone = _sort_with_jitter(_find_videos_in("phone-footage"))

    if not broll and not perf and not phone:
        sys.exit("ERROR: no source videos found in content/videos/")

    # Target mix: phone-footage 45%, performances 40%, b-roll 15%.
    # Raw/authentic footage beats produced b-roll for algorithm reach in 2026 —
    # phone footage reads participatory (viewer feels IN the room), b-roll reads
    # like an ad and triggers skip behaviour in non-fans.
    n_phone = max(0, round(count * 0.45))
    n_perf  = max(0, round(count * 0.40))
    n_broll = max(1, count - n_perf - n_phone)

    # Clamp to what's actually available
    n_broll = min(n_broll, len(broll))
    n_perf  = min(n_perf,  len(perf))
    n_phone = min(n_phone, len(phone))

    # Draw from each pool in LRU order, no overlaps
    excl = set(exclude or [])

    def _take(pool, n):
        out = []
        for v in pool:
            if str(v) not in excl and len(out) < n:
                out.append(v)
                excl.add(str(v))
        return out

    br = _take(broll, n_broll)
    pf = _take(perf,  n_perf)
    ph = _take(phone, n_phone)

    # Split broll into two halves so each contributes distinct clips to the interleave
    br_a, br_b = br[::2], br[1::2]

    # Interleave: lead_cat opens, broll is doubled (50% share), others fill remaining
    if lead_cat == 'phone':
        cycle_lists = [ph, br_a, br_b, pf]
    elif lead_cat == 'broll':
        cycle_lists = [br_a, br_b, pf, ph]
    else:  # perf
        cycle_lists = [pf, br_a, br_b, ph]

    categories = [lst for lst in cycle_lists if lst]
    iters      = [iter(lst) for lst in categories]
    interleaved: list[Path] = []
    while iters:
        for it in list(iters):
            try:
                interleaved.append(next(it))
            except StopIteration:
                iters.remove(it)

    picked = interleaved[:count]

    # Fallback: fill remaining from any pool if we're short
    if len(picked) < count:
        all_remaining = [v for v in broll + phone + perf if str(v) not in excl]
        for v in all_remaining:
            if len(picked) >= count:
                break
            picked.append(v)

    return picked


def _get_video_category(vpath: Path) -> str:
    """Determine category from directory structure: performances / phone-footage / b-roll."""
    parts = str(vpath).lower()
    if 'phone-footage' in parts:
        return 'phone-footage'
    if 'performances' in parts:
        return 'performances'
    return 'b-roll'


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
    parser.add_argument("--force", action="store_true", help="Bypass active window check and post immediately.")
    args = parser.parse_args()

    mode = "[DRY RUN] " if args.dry_run else ""

    # ── Active window gate (live runs only) ───────────────────────────────────
    if not args.dry_run and not args.force:
        import scheduler
        if not scheduler.is_within_active_window():
            window = scheduler.SendWindow()
            print(f"  ⏸  Outside active send window — {window.status()}")
            print("  Use --dry-run to render clips without posting.")
            print("  Use --force to bypass the window check.")
            sys.exit(0)

    # ── 1. RJM track + peak section + BPM ────────────────────────────────────
    _sep("STEP 1 / 4 — RJM track + BPM")
    audio_path, track_title = pick_next_track(args.track)
    print(f"  Track: {track_title}")
    print(f"  File:  {audio_path.name}")

    bpm     = processor.get_bpm(str(audio_path))
    bar_dur = 4 * 60.0 / bpm   # one 4/4 bar in seconds
    print(f"  BPM:   {bpm:.1f}  (bar = {bar_dur:.2f}s)")

    raw_sections = find_peak_section(audio_path, max(processor.CLIP_LENGTHS), n_results=3)
    # Apply 1-bar lead-in to each section, one per clip
    clip_lengths_tmp = list(processor.CLIP_LENGTHS)  # [5, 9, 15]
    audio_starts = {
        cl: max(0, int(raw_sections[i] - bar_dur))
        for i, cl in enumerate(clip_lengths_tmp)
    }
    for cl, t in audio_starts.items():
        print(f"  Audio start {cl}s clip → {t}s (1 bar lead-in)")

    # ── 2. Source videos — one pool per clip, beat-synced start points ────────
    _sep("STEP 2 / 4 — Source videos + beat-sync")
    clip_lengths = list(processor.CLIP_LENGTHS)   # [5, 9, 15]

    per_clip = {}   # clip_len → {video_sources, angle, n_segs, lead_cat, lead_exploration}
    all_used = set()

    # Load learned lead_category weights once per run. When the loop is cold
    # (zero data), weights are uniform and we fall back to the hard-coded
    # angle → lead_cat map. Once we have 5+ posts per category, the weights
    # start biasing toward whatever actually drives completion + saves.
    #
    # Prefer content_engine.learning_loop (canonical bandit since 2026-04);
    # fall back to the legacy outreach_agent weights_learner for safety.
    _wl = None
    _learned_weights: dict = {}
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _proj = _Path(__file__).parent.parent
        if str(_proj) not in _sys.path:
            _sys.path.insert(0, str(_proj))
        from content_engine import learning_loop as _wl  # type: ignore
        _learned_weights = _wl.load_latest_weights()
    except Exception:
        try:
            import weights_learner as _wl  # type: ignore
            _learned_weights = _wl.load_latest_weights()
        except Exception:
            _wl = None
            _learned_weights = {}

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

        # Each angle gets a different visual lead:
        #   emotional (5s) → phone-footage first (raw/intimate)
        #   signal (9s)    → b-roll first (atmospheric/landscape)
        #   energy (15s)   → performances first (crowd energy)
        default_lead = {
            'emotional': 'phone', 'signal': 'broll', 'energy': 'perf',
            'contrast':  'phone', 'body-drop': 'perf', 'identity': 'phone',
        }.get(angle, 'perf')

        # Learning-loop override — epsilon-greedy over [phone, broll, perf]
        lead_exploring = False
        if _wl is not None and _learned_weights.get("lead_category"):
            lead_cat, lead_exploring = _wl.sample_arm(
                "lead_category",
                ["phone", "broll", "perf"],
                _learned_weights,
            )
            if lead_exploring:
                print(f"    ↯ exploring lead_cat={lead_cat} (ε-greedy)")
            else:
                print(f"    ✨ learned lead_cat={lead_cat} (was default {default_lead})")
        else:
            lead_cat = default_lead

        sources_raw = pick_source_videos(n_segs, exclude=all_used, lead_cat=lead_cat)
        sources     = []
        categories  = []

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

            cat = _get_video_category(vpath)
            sources.append((str(vpath), beat_start))
            categories.append(cat)
            all_used.add(str(vpath))
            print(f"    [{cat}] {vpath.parent.name}/{vpath.name}  start={beat_start:.1f}s  ({vid_dur:.1f}s total)")

        if not sources:
            # Fallback: pick any video, start at 0
            fallback = pick_source_videos(1)
            if fallback:
                sources    = [(str(fallback[0]), 0.0)]
                categories = [_get_video_category(fallback[0])]
                all_used.add(str(fallback[0]))
                print(f"    FALLBACK: {fallback[0].name}")

        per_clip[clip_len] = {
            "video_sources":     sources,
            "source_categories": categories,
            "angle":             angle,
            "n_segs":            n_segs,
            "lead_cat":          lead_cat,
            "lead_exploration":  lead_exploring,
        }

    # ── 3. Hooks + captions — single Claude call each ─────────────────────────
    _sep("STEP 3 / 4 — Hooks + captions (2 Claude calls)")

    clips_config = [
        {"length": cl, "angle": per_clip[cl]["angle"]}
        for cl in clip_lengths
    ]

    print("  Generating hooks (all 3 angles — 1 call)…")
    run_hooks = generator.generate_run_hooks(track_title, clips_config)
    # run_hooks is {length: {'hook': str, 'mechanism': str}} — helper for safe access
    def _hook_text(cl: int) -> str:
        meta = run_hooks.get(cl)
        if isinstance(meta, dict):
            return meta.get("hook", "")
        return meta or ""
    def _hook_mech(cl: int) -> str:
        meta = run_hooks.get(cl)
        if isinstance(meta, dict):
            return meta.get("mechanism", "other")
        return "other"

    for cl in clip_lengths:
        print(f"    {cl}s [{per_clip[cl]['angle']}] ({_hook_mech(cl)})  \"{_hook_text(cl)}\"")

    print("\n  Generating captions (all 3 clips — 1 call)…")
    clips_data = [
        {
            "length": cl,
            "angle":  per_clip[cl]["angle"],
            "hook":   _hook_text(cl),
        }
        for cl in clip_lengths
    ]
    run_captions = generator.generate_run_captions(track_title, clips_data)

    print("\n  Running caption quality gate (5 brand tests)…")
    run_captions = generator.validate_run_captions(run_captions, clips_data)

    # ── 4. Cut clips + mix audio ──────────────────────────────────────────────
    _sep("STEP 4 / 4 — Cut clips + mix RJM audio")
    timestamp  = datetime.now().strftime("%Y-%m-%d_%H%M")
    safe_track = _safe_name(track_title)
    run_dir    = OUTPUT_DIR / f"{timestamp}_{safe_track}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Output: {run_dir}\n")

    output_files = []

    for clip_len in clip_lengths:
        clip_data  = per_clip[clip_len]
        angle      = clip_data["angle"]
        sources    = clip_data["video_sources"]
        cat_list   = clip_data.get("source_categories", [])
        hook       = _hook_text(clip_len)

        print(f"  → {clip_len}s [{angle}]  {len(sources)} segments")
        out_file = run_dir / f"{safe_track}_{clip_len}s.mp4"

        processor.format_to_vertical_multiclip(
            video_sources=sources,
            output_path=str(out_file),
            clip_duration=float(clip_len),
            hook_text=hook,
            angle=angle,
            source_categories=cat_list,
        )
        mix_in_track(out_file, audio_path, audio_starts[clip_len], clip_len)

        size_mb = out_file.stat().st_size / 1_000_000
        print(f"    ✓ {out_file.name}  ({size_mb:.1f} MB)")
        output_files.append(out_file)

    # ── Captions file ─────────────────────────────────────────────────────────
    caption_lines = []
    for cl in clip_lengths:
        hook  = _hook_text(cl)
        caps  = run_captions.get(cl, {})
        angle = per_clip[cl]["angle"]

        caption_lines += [
            f"┌─────────────────────────────────────────────",
            f"│  {cl}-SECOND CLIP  [{angle.upper()}]",
            f"│  File: {safe_track}_{cl}s.mp4",
            f"└─────────────────────────────────────────────",
            "",
            f'HOOK: "{hook}"',
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

    # ── Cleanup old Cloudinary uploads (live only) ────────────────────────────
    if not args.dry_run:
        try:
            from video_host import cleanup_old_cloudinary_uploads
            cleanup_old_cloudinary_uploads(max_age_days=7)
        except Exception as _clean_exc:
            print(f"  ⚠ Upload cleanup skipped (non-fatal): {_clean_exc}")

    # ── Buffer (live only) ────────────────────────────────────────────────────
    if not args.dry_run:
        _sep("BUFFER — Queuing to TikTok / Instagram Reels / Instagram Story / YouTube")
        from buffer_poster import upload_video_and_queue
        from datetime import timezone
        import uuid as _uuid

        # Single batch ID for the whole run — lets the learning loop group
        # the 3 clips produced together and compare them as a cohort.
        batch_id = datetime.now().strftime("%Y%m%d_%H%M") + "_" + _uuid.uuid4().hex[:6]

        # Fixed CET posting slots: 08:30 / 13:00 / 21:30 (Tenerife time)
        # 08:30 beats 09:00 — catches EU pre-commute before feed congestion peaks.
        # 21:30 outperforms 19:00 for the nocturnal Psytrance audience + West Coast US.
        _POST_SLOTS_CET = ["08:30", "13:00", "21:30"]
        try:
            from zoneinfo import ZoneInfo
            _tz_cet = ZoneInfo("Europe/Madrid")
        except Exception:
            from datetime import timedelta as _td
            _tz_cet = timezone(_td(hours=1))

        from datetime import timedelta as _td
        _now_cet = datetime.now(_tz_cet)
        schedule_times = []
        _next_future = _now_cet + _td(minutes=15)  # earliest allowed slot
        for _i in range(len(output_files)):
            _slot = _POST_SLOTS_CET[_i % len(_POST_SLOTS_CET)]
            _h, _m = int(_slot.split(":")[0]), int(_slot.split(":")[1])
            _candidate = _now_cet.replace(hour=_h, minute=_m, second=0, microsecond=0)
            # If the fixed slot is in the past, use _next_future instead
            if _candidate <= _next_future:
                _candidate = _next_future
            _next_future = _candidate + _td(minutes=30)  # keep slots 30 min apart
            schedule_times.append(_candidate.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

        for i, (clip_len, clip_path) in enumerate(zip(clip_lengths, output_files)):
            caps  = run_captions.get(clip_len, {})
            sched = schedule_times[i]
            print(f"\n  Queuing {clip_len}s [{per_clip[clip_len]['angle']}] → {sched}…")

            # ── Quality gate ──────────────────────────────────────────────
            from quality_gate import check_clip
            angle = per_clip[clip_len]["angle"]
            print(f"    Checking quality…")
            gate_passed, gate_reason = check_clip(str(clip_path), expected_duration=clip_len, angle=angle)
            if not gate_passed:
                print(f"    ✗ Quality gate failed — skipping post: {gate_reason}")
                continue
            print(f"    ✓ Quality gate passed")
            # ─────────────────────────────────────────────────────────────

            tt_caption = caps.get('tiktok', {}).get('caption', '') + "\n" + caps.get('tiktok', {}).get('hashtags', '')
            ig_caption = caps.get('instagram', {}).get('caption', '') + "\n" + caps.get('instagram', {}).get('hashtags', '')
            yt_title   = caps.get('youtube', {}).get('title', '')
            yt_desc    = caps.get('youtube', {}).get('description', '') + "\n\n" + caps.get('youtube', {}).get('hashtags', '')

            try:
                results = upload_video_and_queue(
                    clip_path         = str(clip_path),
                    tiktok_caption    = tt_caption,
                    instagram_caption = ig_caption,
                    youtube_title     = yt_title,
                    youtube_desc      = yt_desc,
                    scheduled_at      = sched,
                )
                succeeded = [p for p, r in results.items() if r["success"]]
                failed_platforms = [p for p, r in results.items() if not r["success"]]
                print(f"    ✓ Queued: {', '.join(succeeded)}")
                if failed_platforms:
                    print(f"    ⚠ Failed platforms (will retry next run): {', '.join(failed_platforms)}")

                # ── Cross-platform content state log ─────────────────────────
                # Persist FULL creative metadata so the learning loop can
                # correlate "what we did" with "what worked" (metrics_fetcher
                # populates content_metrics; weights_learner reads both).
                try:
                    from content_signal import log_content_post as _log_content_post
                    hook_text   = _hook_text(clip_len)
                    hook_mech   = _hook_mech(clip_len)
                    _hook_meta  = run_hooks.get(clip_len) or {}
                    hook_explore = bool(
                        _hook_meta.get("exploration", False)
                        if isinstance(_hook_meta, dict) else False
                    )
                    clip_data   = per_clip[clip_len]
                    source_vids = [
                        {"path": str(_p), "category": _cat, "start_s": _start}
                        for (_p, _start), _cat in zip(
                            clip_data.get("video_sources", []),
                            clip_data.get("source_categories", []),
                        )
                    ]
                    # lead_cat chosen by the learning loop (or angle default
                    # when the loop is still cold). lead_exploration is True
                    # when the learner picked via ε-greedy exploration rather
                    # than exploitation — we log BOTH flags so we can filter
                    # later: was this a learner-driven win or a lucky explore?
                    lead_cat        = clip_data.get("lead_cat", "perf")
                    lead_explore    = bool(clip_data.get("lead_exploration", False))
                    exploration_row = hook_explore or lead_explore

                    # Per-platform caption text (what the learner will
                    # correlate against completion / saves / shares)
                    _tt_cap = (caps.get("tiktok", {}) or {}).get("caption", "")
                    _ig_cap = (caps.get("instagram", {}) or {}).get("caption", "")
                    _yt_title = (caps.get("youtube", {}) or {}).get("title", "")
                    _yt_desc  = (caps.get("youtube", {}) or {}).get("description", "")

                    # Cloudinary URL for post-hoc inspection (if available in
                    # the Buffer result payload; currently unknown → None)
                    _cloud_url = None

                    for _platform, _result in results.items():
                        if not _result.get("success"):
                            continue
                        _fmt = "short" if _platform == "youtube" else "reels"
                        _log_content_post(
                            platform=_platform,
                            format=_fmt,
                            track=track_title,
                            angle=angle,
                            hook=hook_text,
                            buffer_id=_result.get("id"),
                            filename=str(clip_path),
                            # ─── Learning loop fields ──────────────────
                            hook_mechanism=hook_mech,
                            bpm=float(bpm),
                            bar_duration=float(bar_dur),
                            clip_length=int(clip_len),
                            segment_count=int(clip_data.get("n_segs", 0)),
                            source_videos=source_vids,
                            lead_category=lead_cat,
                            cloudinary_url=_cloud_url,
                            scheduled_at=sched,
                            tiktok_caption=_tt_cap,
                            instagram_caption=_ig_cap,
                            youtube_title=_yt_title,
                            youtube_desc=_yt_desc,
                            exploration=exploration_row,
                            batch_id=batch_id,
                        )
                except ImportError:
                    pass
                except Exception as _log_exc:
                    print(f"    ⚠ content_signal log failed (non-fatal): {_log_exc}")
            except Exception as e:
                # Total failure (video upload failed on all hosts) — save for retry
                # IMPORTANT: use continue, not raise — remaining clips must still post.
                # Previously a raise here silently dropped all subsequent clips.
                print(f"    ✗ Total posting failure — saving to retry queue: {e}")
                from post_queue import save_failed_post
                save_failed_post(
                    clip_path         = str(clip_path),
                    tiktok_caption    = tt_caption,
                    instagram_caption = ig_caption,
                    youtube_title     = yt_title,
                    youtube_desc      = yt_desc,
                    scheduled_at      = sched,
                    error             = str(e),
                )
                continue  # do NOT break — keep posting remaining clips

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

    if not args.dry_run:
        print(f"\n{mode}Total posts queued: {len(output_files) * 4}  "
              f"(TikTok×{len(output_files)} + Reels×{len(output_files)} + Stories×{len(output_files)} + YouTube×{len(output_files)})")

        # Auto-log Spotify listeners once daily (non-fatal if Playwright not installed)
        try:
            import spotify_auto_logger
            count = spotify_auto_logger.run()
            if count:
                print(f"\n  📊 Spotify: {count:,} monthly listeners logged automatically")
        except Exception as _e:
            print(f"\n  ℹ️  Spotify auto-log skipped: {_e}")

    if args.dry_run:
        print("\n[DRY RUN] Buffer posting skipped.\n")
        for cl in clip_lengths:
            angle = per_clip[cl]["angle"]
            caps  = run_captions.get(cl, {})
            print(f"── {cl}s [{angle}] ──────────────────────────────")
            print(f"  Hook:      {_hook_text(cl)}  (mech: {_hook_mech(cl)})")
            print(f"  TikTok:    {caps.get('tiktok', {}).get('caption', '')}")
            print(f"  Instagram: {caps.get('instagram', {}).get('caption', '')}")
            print(f"  YT title:  {caps.get('youtube', {}).get('title', '')}")
            print()

    # ── Log to master audit trail ────────────────────────────────────────────
    try:
        subprocess.run(
            [sys.executable,
             str(Path(__file__).parent / "master_agent.py"),
             "log_run",
             f"content: 3 clips produced for {track_title}",
             "0",
             "tiktok_reels_content"],
            cwd=str(Path(__file__).parent),
            capture_output=True,
            timeout=10
        )
    except Exception:
        pass  # logging is non-fatal — never crash the content run

    # ── Fleet heartbeat (hive-mind state) ────────────────────────────────────
    try:
        from fleet_state import heartbeat as _heartbeat
        _heartbeat("post_today", status="ok", result={"clips": len(output_files)})
    except ImportError:
        pass
    except Exception:
        pass  # non-fatal


if __name__ == "__main__":
    main()
