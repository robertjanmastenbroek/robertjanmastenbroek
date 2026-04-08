"""
Beat editor — creates beat-synced montage clips from multiple source videos.

Pipeline:
1. Extract audio from all input clips
2. Detect beats using librosa (tempo + beat timestamps)
3. Slice each source clip at beat boundaries (1, 2, or 4 beats per segment)
4. Assemble slices into a single montage, cycling through source clips
5. Add visual punch on each cut: brief brightness flash + subtle zoom
6. Output: 15s, 30s, 60s versions — all beat-synced, cinematic quality

Usage:
  from beat_editor import build_beat_montage
  output_files = build_beat_montage(
      source_clips=['clip1.mp4', 'clip2.mp4', ...],
      output_dir='/path/to/out',
      base_name='sunset_montage',
      hook_text='Sacred music for every dancefloor',
      target_lengths=[15, 30, 60],
  )
"""

import os
import json
import math
import shutil
import logging
import tempfile
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Optional

import numpy as np
try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

logger = logging.getLogger(__name__)


# ── Retry helper ──────────────────────────────────────────────────────────────

def _run_ffmpeg(cmd: list, timeout: int = 600, max_attempts: int = 3) -> subprocess.CompletedProcess:
    """
    Run an ffmpeg command with exponential-backoff retry on transient failures.
    Raises RuntimeError with stderr tail on final failure.
    """
    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if result.returncode == 0:
            return result
        err = result.stderr.decode(errors='replace')[-500:]
        if attempt < max_attempts:
            wait = 2 ** attempt
            logger.warning(f"ffmpeg attempt {attempt}/{max_attempts} failed — retrying in {wait}s\n{err[-200:]}")
            time.sleep(wait)
        else:
            raise RuntimeError(f"ffmpeg failed after {max_attempts} attempts:\n{err}")

# Output dimensions
OUTPUT_W = 1080
OUTPUT_H = 1920

# Beats per segment — always 4 (one full bar in 4/4 time).
# Cuts on bar downbeats only — this is what feels musically intentional.
# At 136 BPM: 4 beats = 1.76s → 7s clip = 4 cuts, 15s = 8 cuts, 30s = 17 cuts.
BEATS_PER_SEGMENT_SHORT = 4
BEATS_PER_SEGMENT_LONG  = 4
MIN_SEGMENT_S = 1.0
MAX_SEGMENT_S = 3.0

# Default target lengths — 7s is primary (TikTok/Reels loop rate optimised)
DEFAULT_TARGET_LENGTHS = [7, 15, 30]


# ── Audio extraction ──────────────────────────────────────────────────────────

def extract_audio(video_path: str, out_wav: str):
    """Extract audio track from video to a WAV file for beat analysis."""
    cmd = [
        'ffmpeg', '-y', '-i', video_path,
        '-vn', '-acodec', 'pcm_s16le',
        '-ar', '22050', '-ac', '1',
        out_wav
    ]
    _run_ffmpeg(cmd, timeout=120)


# ── Beat detection + energy analysis ─────────────────────────────────────────

def analyse_track(wav_path: str) -> dict:
    """
    Full analysis of a music track:
    - Detect BPM and precise beat timestamps
    - Find highest-energy sustained section (skip intros/outros)
    - Return analysis dict for use in montage planning

    Returns:
      {
        'tempo': float,
        'beat_times': np.ndarray,        # all beat timestamps (seconds)
        'energy_start': float,           # best section start (seconds)
        'phrase_boundaries': np.ndarray, # beat_times starting from energy_start
      }
    """
    import librosa

    logger.info(f"Analysing track: {os.path.basename(wav_path)}")
    y, sr = librosa.load(wav_path, sr=22050, mono=True)
    total_dur = len(y) / sr

    # ── 1. Beat tracking ────────────────────────────────────────────────────
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units='frames')
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    tempo = float(tempo[0]) if hasattr(tempo, '__len__') else float(tempo)
    logger.info(f"  BPM: {tempo:.1f}  |  Beats: {len(beat_times)}")

    # ── 2. Find high-energy section ─────────────────────────────────────────
    # Use RMS energy with a 10-second rolling window to find peak sustained energy
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    window_frames = int(10 * sr / hop)  # 10s window

    if len(rms) > window_frames:
        # Rolling average energy
        kernel = np.ones(window_frames) / window_frames
        smoothed = np.convolve(rms, kernel, 'valid')
        peak_frame = int(np.argmax(smoothed))
        energy_start_raw = librosa.frames_to_time(peak_frame, sr=sr, hop_length=hop)
    else:
        energy_start_raw = 0.0

    # Snap energy_start to the nearest beat (so first cut is perfectly on-beat)
    if len(beat_times) > 0:
        nearest_beat_idx = int(np.argmin(np.abs(beat_times - energy_start_raw)))
        # Don't start too close to the end — leave at least 30s of track
        max_start_idx = max(0, len(beat_times) - int(30 * tempo / 60))
        nearest_beat_idx = min(nearest_beat_idx, max_start_idx)
        energy_start = float(beat_times[nearest_beat_idx])
        phrase_boundaries = beat_times[nearest_beat_idx:]
    else:
        energy_start = 0.0
        phrase_boundaries = np.array([])

    logger.info(f"  High-energy section starts at: {energy_start:.1f}s "
                f"({energy_start/total_dur*100:.0f}% into track)")

    return {
        'tempo': tempo,
        'beat_times': beat_times,
        'energy_start': energy_start,
        'phrase_boundaries': phrase_boundaries,
    }


def get_phrase_starts(analysis: dict, beats_per_phrase: int) -> np.ndarray:
    """
    Return timestamps of every Nth beat (phrase boundaries) starting from
    the high-energy section. Each returned timestamp is a cut point.
    """
    pb = analysis['phrase_boundaries']
    if len(pb) == 0:
        return np.array([0.0])
    return pb[::beats_per_phrase]


# ── Video info ────────────────────────────────────────────────────────────────

def get_video_info(path: str) -> dict:
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams', '-show_format', path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    data = json.loads(result.stdout)
    duration = float(data['format'].get('duration', 0))
    width = height = 0
    for s in data.get('streams', []):
        if s.get('codec_type') == 'video':
            width = s.get('width', 0)
            height = s.get('height', 0)
            break
    return {'duration': duration, 'width': width, 'height': height}


# ── Segment planning ──────────────────────────────────────────────────────────

def plan_segments(source_clips: list[str], phrase_starts: np.ndarray,
                  target_duration: float) -> list[dict]:
    """
    Plan which video clip to show at each beat-phrase boundary.

    phrase_starts: array of timestamps (seconds) marking each cut point in the MUSIC.
    Each segment runs from phrase_starts[i] to phrase_starts[i+1] (variable length,
    perfectly aligned to actual beats).

    Returns list of dicts:
      {'clip': path, 'start': float, 'duration': float, 'music_offset': float}
    """
    clip_infos = []
    for clip in source_clips:
        try:
            info = get_video_info(clip)
            clip_infos.append({'path': clip, 'duration': info['duration']})
        except Exception as e:
            logger.warning(f"Skipping {clip}: {e}")

    if not clip_infos:
        raise RuntimeError("No usable source clips")

    # Build pairs: (phrase_start, phrase_duration) trimmed to target_duration
    pairs = []
    total = 0.0
    for i in range(len(phrase_starts) - 1):
        seg_dur = float(phrase_starts[i + 1] - phrase_starts[i])
        if total + seg_dur > target_duration + 0.1:
            # Last segment: trim to exact target
            seg_dur = max(0.1, target_duration - total)
            pairs.append((float(phrase_starts[i]), seg_dur))
            total += seg_dur
            break
        pairs.append((float(phrase_starts[i]), seg_dur))
        total += seg_dur
        if total >= target_duration:
            break

    if not pairs:
        raise RuntimeError("Not enough beat phrases for target duration")

    # Assign clips, spreading evenly and avoiding immediate repeats
    n_clips = len(clip_infos)
    used_starts = {c['path']: [] for c in clip_infos}
    segments = []

    for idx, (music_offset, seg_dur) in enumerate(pairs):
        info = clip_infos[idx % n_clips]
        clip_path = info['path']
        clip_dur = info['duration']

        # Find a source start position not recently used
        used = used_starts[clip_path]
        max_start = max(0.0, clip_dur - seg_dur - 0.1)
        candidates = np.linspace(0.05 * clip_dur, 0.90 * clip_dur, 30)
        candidates = [float(c) for c in candidates if float(c) <= max_start]

        if not candidates:
            start = 0.0
        elif used:
            start = max(candidates, key=lambda c: min(abs(c - u) for u in used[-4:]))
        else:
            # First use: start at energy-rich middle section
            start = candidates[len(candidates) // 2]

        used_starts[clip_path].append(start)
        segments.append({
            'clip': clip_path,
            'start': start,
            'duration': seg_dur,
            'music_offset': music_offset,
        })

    avg_dur = sum(s['duration'] for s in segments) / max(len(segments), 1)
    logger.info(f"Planned {len(segments)} beat-synced segments "
                f"(avg {avg_dur:.2f}s) from {n_clips} clips")
    return segments


# ── Single segment processing ─────────────────────────────────────────────────

def _escape(text: str) -> str:
    return (text
            .replace('\\', '\\\\')
            .replace("'", "\\'")
            .replace(':', '\\:')
            .replace('%', '\\%'))


def process_segment(clip_path: str, start: float, duration: float,
                    out_path: str):
    """
    Cut a single segment and format to 9:16 vertical.

    For landscape (16:9) footage — the right approach is CENTER-CROP ZOOM:
      Scale up so the height fills 1920px, then crop the center 1080px wide.
      At 1920×1080 source this is a 1.78x zoom — looks cinematic, no blur pillars.

    For vertical footage — scale/pad to fit.

    Camera audio is always muted — studio track is added at assembly stage.
    """
    info = get_video_info(clip_path)
    w, h = info['width'], info['height']
    already_vertical = h > w

    # ── Adaptive grade per footage type ──────────────────────────────────────
    # Phone footage: warmer, higher saturation to compensate compressed codecs.
    # Event/performance: cooler shadow tint (blue-black), boosted contrast for crowd energy.
    # Music-video: neutral, clean — trust the DP's grade.
    #
    # Saturation 1.25 (vs old 1.10): survives H.264 platform encode without going flat.
    # Vignette PI/5.5 (vs old PI/4): softer, doesn't darken edges on 7s loops.
    _clip_parts = Path(clip_path).parts
    if 'phone-footage' in _clip_parts:
        grade = (
            "eq=contrast=1.08:brightness=0.03:saturation=1.25,"
            "colorbalance=bs=-0.03:gs=-0.01:rs=0.06,"   # warm push
            "vignette=PI/5.5"
        )
    elif 'performances' in _clip_parts or any('LUC' in p for p in _clip_parts):
        grade = (
            "eq=contrast=1.12:brightness=0.01:saturation=1.25,"
            "colorbalance=bs=-0.08:gs=-0.03:rs=0.02,"   # cool/blue for crowd energy
            "vignette=PI/5.5"
        )
    else:
        # music-videos, other — neutral, preserve DP grade
        grade = (
            "eq=contrast=1.04:brightness=0.01:saturation=1.18,"
            "vignette=PI/6"
        )

    if already_vertical:
        vf = (
            f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=decrease,"
            f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:black,"
            f"{grade}"
        )
    else:
        # Landscape → center-crop zoom to 9:16 (1.78x zoom, no blur pillars)
        vf = (
            f"scale=-2:{OUTPUT_H},"
            f"crop={OUTPUT_W}:{OUTPUT_H},"
            f"{grade}"
        )

    cmd = [
        'ffmpeg', '-y',
        '-ss', str(start),
        '-i', clip_path,
        '-t', str(duration),
        '-vf', vf,
        '-map', '0:v',
        '-an',
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '18',
        '-pix_fmt', 'yuv420p',
        '-r', '30',
        out_path
    ]

    _run_ffmpeg(cmd, timeout=120)


# ── Text overlay rendering ────────────────────────────────────────────────────

def _render_hook_overlay(hook_text: str, out_png: str,
                         w: int = OUTPUT_W, h: int = OUTPUT_H):
    """
    Render hook text overlay — pure text, no background bars or pills.

    Design: bold uppercase white text with heavy multi-layer shadow.
    No background at all — text floats clean over the video.
    Thin gold accent line below (brand signature).
    Positioned at 38% from top — eye-level on portrait mobile.

    Shown for first 3 seconds only (controlled in assemble_montage via ffmpeg enable=).
    """
    if not PILLOW_AVAILABLE:
        return None

    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Font
    font_paths = [
        '/Library/Fonts/Arial Bold.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        '/Library/Fonts/Arial.ttf',
        '/System/Library/Fonts/Arial.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    ]
    font_size = 96
    font = None
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, font_size)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()

    # Uppercase, tight wrap for large font
    text = hook_text.upper()
    lines = textwrap.wrap(text, width=16)
    if not lines:
        lines = [text]

    line_h = int(font_size * 1.28)
    total_text_h = len(lines) * line_h

    # Eye-level on portrait mobile ≈ 38% from top
    y_start = int(h * 0.38) - total_text_h // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = (w - tw) // 2
        y = y_start + i * line_h

        # ── Heavy multi-layer shadow (makes text readable on any background) ──
        for sx, sy, alpha in [(5, 5, 180), (3, 3, 140), (7, 7, 100), (-2, 2, 80)]:
            draw.text((x + sx, y + sy), line, font=font, fill=(0, 0, 0, alpha))

        # ── White text ───────────────────────────────────────────────────────
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

    # ── Gold accent line below last line ─────────────────────────────────────
    gold = (212, 175, 55, 230)
    accent_y = y_start + total_text_h + 18
    accent_w = min(280, w - 120)
    ax0 = (w - accent_w) // 2
    draw.rectangle([(ax0, accent_y), (ax0 + accent_w, accent_y + 4)], fill=gold)

    # ── Small gold dots flanking the accent line ─────────────────────────────
    dot_r = 5
    draw.ellipse([(ax0 - 18 - dot_r, accent_y - dot_r + 2),
                  (ax0 - 18 + dot_r, accent_y + dot_r + 2)], fill=gold)
    draw.ellipse([(ax0 + accent_w + 18 - dot_r, accent_y - dot_r + 2),
                  (ax0 + accent_w + 18 + dot_r, accent_y + dot_r + 2)], fill=gold)

    img.save(out_png, 'PNG')
    return out_png


# ── Montage assembly ──────────────────────────────────────────────────────────

def assemble_montage(segment_files: list[str], out_path: str,
                     target_duration: float, hook_text: str = None,
                     source_audio: str = None,
                     platform: str = 'tiktok'):
    """
    Concatenate segment files into a single montage video.
    Optionally replace audio with source_audio (original music track).
    Optionally burn hook text overlay.
    """
    work_dir = tempfile.mkdtemp(prefix='assemble_')
    try:
        # Write concat list
        concat_list = os.path.join(work_dir, 'concat.txt')
        with open(concat_list, 'w') as f:
            for seg in segment_files:
                f.write(f"file '{seg}'\n")

        # Step 1: Concat segments
        concat_out = os.path.join(work_dir, 'concat.mp4')
        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat', '-safe', '0',
            '-i', concat_list,
            '-t', str(target_duration),
            '-c', 'copy',
            concat_out
        ]
        _run_ffmpeg(cmd, timeout=300)

        # Step 2: Apply hook text + audio normalization
        audio_input = []
        audio_map = ['-map', '0:a?']
        if source_audio:
            audio_input = ['-i', source_audio]
            audio_map = ['-map', '1:a']

        # Platform-specific loudness targets (research-backed):
        # TikTok/Instagram re-normalise to -16 LUFS — match to avoid double compression
        # YouTube targets -12 to -14 LUFS for Shorts discovery
        # Spotify/master: -14 LUFS (streaming standard)
        _lufs = {'tiktok': '-16', 'instagram': '-16', 'youtube': '-12'}.get(platform, '-14')
        _tp   = {'tiktok': '-1.5', 'instagram': '-1.5', 'youtube': '-1.0'}.get(platform, '-2.0')
        af = f'loudnorm=I={_lufs}:TP={_tp}:LRA=7'

        # Render hook text as PNG overlay (avoids drawtext dependency)
        overlay_png = None
        if hook_text and PILLOW_AVAILABLE:
            overlay_png = os.path.join(work_dir, 'hook_overlay.png')
            _render_hook_overlay(hook_text, overlay_png)
        has_overlay = bool(overlay_png and os.path.exists(overlay_png))

        # Build input list and track which index the audio ends up at
        # Order: [0] concat video, [1] overlay PNG (optional), [N] audio (optional)
        inputs = ['-i', concat_out]
        if has_overlay:
            inputs += ['-loop', '1', '-i', overlay_png]
        # Count number of -i flags already present → that's the next input index
        audio_idx = inputs.count('-i')
        if source_audio:
            inputs += ['-i', source_audio]
            audio_map = ['-map', f'{audio_idx}:a']
        else:
            audio_map = ['-map', '0:a?']  # fallback (usually silent)

        if has_overlay:
            png_idx = 1  # always at position 1
            cmd2 = [
                'ffmpeg', '-y',
            ] + inputs + [
                '-t', str(target_duration),
                '-filter_complex', f"[0:v][{png_idx}:v]overlay=0:0:enable='lte(t,3)'[vout]",
                '-map', '[vout]',
            ] + audio_map + [
                '-c:v', 'libx264', '-preset', 'slow', '-crf', '18',
                '-b:v', '5M', '-maxrate', '6M', '-bufsize', '10M',
                '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '192k',
                '-af', af,
                '-movflags', '+faststart',
                out_path
            ]
        else:
            cmd2 = [
                'ffmpeg', '-y',
            ] + inputs + [
                '-t', str(target_duration),
                '-map', '0:v',
            ] + audio_map + [
                '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', '192k',
                '-af', af,
                '-movflags', '+faststart',
                out_path
            ]

        _run_ffmpeg(cmd2, timeout=600)

        size_mb = os.path.getsize(out_path) / 1_000_000
        logger.info(f"Assembled: {os.path.basename(out_path)} ({size_mb:.1f} MB)")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ── Main entry point ──────────────────────────────────────────────────────────

def build_beat_montage(
    source_clips: list[str],
    output_dir: str,
    base_name: str,
    hook_text=None,
    target_lengths: list[int] = None,
    music_track: str = None,
) -> list[str]:
    """
    Build beat-synced montage clips from multiple source videos.

    Args:
        source_clips:    List of input video paths
        output_dir:      Where to write output files
        base_name:       Output filename prefix
        hook_text:       Hook text — either a single str (same on all clips) or a
                         dict mapping target_length (int) → str for per-clip hooks.
                         e.g. {7: "reach hook", 15: "reach hook 2", 30: "follow hook", 60: "spotify hook"}
        target_lengths:  List of desired clip lengths in seconds [7, 15, 30, 60]
        music_track:     Path to master WAV — used as audio + for beat detection.
                         Camera audio is always muted regardless.

    Returns:
        List of output file paths
    """
    if target_lengths is None:
        target_lengths = DEFAULT_TARGET_LENGTHS

    os.makedirs(output_dir, exist_ok=True)

    # ── Checkpoint: skip already-completed lengths on re-run ──────────────────
    checkpoint_path = os.path.join(output_dir, '.pipeline_checkpoint.json')
    completed_lengths: set = set()
    if os.path.exists(checkpoint_path):
        try:
            cp = json.load(open(checkpoint_path))
            completed_lengths = set(cp.get('completed_lengths', []))
            if completed_lengths:
                logger.info(f"Resuming — already done: {sorted(completed_lengths)}s clips")
        except Exception:
            pass

    work_dir = tempfile.mkdtemp(prefix='beat_montage_')
    output_files = []

    # Re-collect already-built outputs from previous partial run
    for tl in completed_lengths:
        existing = os.path.join(output_dir, f'{base_name}_{tl}s.mp4')
        if os.path.exists(existing):
            output_files.append(existing)

    try:
        # 1. Prepare analysis WAV
        if music_track and os.path.exists(music_track):
            ref_wav = os.path.join(work_dir, 'reference.wav')
            _run_ffmpeg([
                'ffmpeg', '-y', '-i', music_track,
                '-vn', '-acodec', 'pcm_s16le', '-ar', '22050', '-ac', '1',
                ref_wav
            ], timeout=120)
        else:
            clips_by_dur = sorted(source_clips,
                                  key=lambda p: get_video_info(p)['duration'], reverse=True)
            ref_wav = os.path.join(work_dir, 'reference.wav')
            extract_audio(clips_by_dur[0], ref_wav)
            logger.info("No music track — using video audio for beat detection")

        # 2. Full track analysis: BPM + energy-peak section
        analysis = analyse_track(ref_wav)
        tempo = analysis['tempo']

        # 3. Build each target length
        # Pre-compute per-length clip orders so each video uses a different
        # shuffled pool — visually distinct despite sharing the same audio.
        import random as _rnd
        from datetime import date
        day_int = int(date.today().strftime('%Y%j'))  # unique per calendar day

        length_clip_orders: dict[int, list[str]] = {}
        for i, tl in enumerate(sorted(target_lengths)):
            shuffled = list(source_clips)
            _rnd.Random(day_int + tl + i * 1000).shuffle(shuffled)
            length_clip_orders[tl] = shuffled

        for target_len in sorted(target_lengths):
            # Skip if already built in a previous partial run
            if target_len in completed_lengths:
                logger.info(f"Skipping {target_len}s — already complete (checkpoint)")
                continue

            logger.info(f"")
            logger.info(f"── Building {target_len}s montage ──")

            # Use faster cuts for short clips (7s), slower for longer
            bpp = BEATS_PER_SEGMENT_SHORT if target_len <= 10 else BEATS_PER_SEGMENT_LONG
            phrase_starts = get_phrase_starts(analysis, beats_per_phrase=bpp)
            avg_seg = 60.0 / tempo * bpp
            logger.info(f"  {bpp} beats/cut @ {tempo:.0f} BPM = {avg_seg:.2f}s per segment")

            # Each length gets its own shuffled clip order
            clips_for_length = length_clip_orders[target_len]
            segments = plan_segments(clips_for_length, phrase_starts, float(target_len))

            # Process each segment
            seg_dir = os.path.join(work_dir, f'segments_{target_len}')
            os.makedirs(seg_dir, exist_ok=True)
            seg_files = []

            for i, seg in enumerate(segments):
                seg_out = os.path.join(seg_dir, f'seg_{i:04d}.mp4')
                try:
                    process_segment(
                        clip_path=seg['clip'],
                        start=seg['start'],
                        duration=seg['duration'],
                        out_path=seg_out,
                    )
                    seg_files.append(seg_out)
                except Exception as e:
                    logger.error(f"Segment {i} failed: {e}")

            if not seg_files:
                logger.error(f"No segments produced for {target_len}s")
                continue

            # Assemble — studio track from energy section, camera audio muted
            out_path = os.path.join(output_dir, f'{base_name}_{target_len}s.mp4')

            # Trim music to start at high-energy section
            music_for_clip = None
            if music_track and os.path.exists(music_track):
                music_for_clip = os.path.join(work_dir, f'music_{target_len}s.wav')
                _run_ffmpeg([
                    'ffmpeg', '-y',
                    '-ss', str(analysis['energy_start']),
                    '-i', music_track,
                    '-t', str(target_len + 2),
                    '-acodec', 'pcm_s16le',
                    music_for_clip
                ], timeout=60)

            # Resolve per-clip hook — dict wins over single string
            clip_hook = (
                hook_text.get(target_len) if isinstance(hook_text, dict)
                else hook_text
            )

            try:
                assemble_montage(
                    segment_files=seg_files,
                    out_path=out_path,
                    target_duration=float(target_len),
                    hook_text=clip_hook,
                    source_audio=music_for_clip or music_track,
                )
                output_files.append(out_path)
                # Write checkpoint so a re-run can skip this length
                completed_lengths.add(target_len)
                with open(checkpoint_path, 'w') as _cp:
                    json.dump({'completed_lengths': sorted(completed_lengths)}, _cp)
                logger.info(f"✅ {target_len}s complete → {out_path}")
            except Exception as e:
                logger.error(f"Assembly failed for {target_len}s: {e} — skipping, other lengths continue")

    except Exception as e:
        logger.error(f"Beat montage failed: {e}", exc_info=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        # Clean up checkpoint only when all lengths completed successfully
        if checkpoint_path and os.path.exists(checkpoint_path):
            done = json.load(open(checkpoint_path)).get('completed_lengths', [])
            if set(done) >= set(target_lengths):
                os.remove(checkpoint_path)

    return output_files
