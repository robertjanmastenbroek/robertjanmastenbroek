"""
Video processor — cuts clips, formats to 9:16, burns in hook text.
Uses ffmpeg under the hood.

Upgrades over v1:
- CRF 18, slow preset for higher quality encode
- loudnorm audio filter to -14 LUFS / -2 TP (platform standard)
- Cinematic color grade: eq + colorbalance (cool shadows / warm highlights)
- Vignette: PI/4.5 for subtle focus pull
- Hook text: dark translucent drawbox behind drawtext (fontsize 56, y=90)
- Bitrate caps: -b:v 5M -maxrate 6M -bufsize 10M
- Multiple best segments: 15s uses rank-0, 30s uses rank-1, 60s uses rank-2
- Smart crop: skips blur-fill for already-vertical input
- pix_fmt yuv420p for maximum platform compatibility
"""

import os
import json
import logging
import subprocess
import textwrap

logger = logging.getLogger(__name__)

# Output dimensions for short-form vertical video
OUTPUT_W = 1080
OUTPUT_H = 1920

# Clip lengths in seconds
CLIP_LENGTHS = [15, 30, 60]

# Map clip length → segment rank (0 = best, 1 = 2nd best, 2 = 3rd best)
CLIP_SEGMENT_RANK = {15: 0, 30: 1, 60: 2}


def get_video_info(video_path: str) -> dict:
    """Return duration, width, height of a video file using ffprobe."""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams', '-show_format',
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    data = json.loads(result.stdout)
    duration = float(data['format'].get('duration', 0))
    width, height = 0, 0

    for stream in data.get('streams', []):
        if stream.get('codec_type') == 'video':
            width = stream.get('width', 0)
            height = stream.get('height', 0)
            break

    return {'duration': duration, 'width': width, 'height': height}


def is_already_vertical(width: int, height: int) -> bool:
    """Check if a video is already in 9:16 (or close to vertical) format."""
    if width == 0 or height == 0:
        return False
    return height > width


def detect_best_segments(video_path: str, duration: float) -> list:
    """
    Find high-energy segments using audio volume peaks.
    Returns a list of (start_time, energy_score) tuples, sorted by energy descending.
    Returns at least 3 entries (with fallback values) so each clip length gets
    a distinct segment rank to start from.
    For event footage, loud = crowd energy = good clip.
    """
    if duration < 15:
        return [(0, 1.0), (0, 0.9), (0, 0.8)]

    # Sample audio volume every 5 seconds across the video
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-f', 'lavfi',
        '-i', f'amovie={video_path},astats=metadata=1:reset=1',
        '-show_entries', 'frame_tags=lavfi.astats.Overall.RMS_level',
        '-of', 'csv=p=0'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    segments = []

    if result.returncode == 0 and result.stdout.strip():
        lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
        window = 5  # seconds per chunk
        for i, line in enumerate(lines):
            try:
                rms = float(line)
                if rms > -100:  # Filter out silence markers
                    start = i * window
                    if start + 15 <= duration:  # Must be long enough for shortest clip
                        segments.append((start, rms))
            except ValueError:
                continue

    # Fallback: sample key moments if audio analysis didn't work
    if not segments:
        checkpoints = [0.1, 0.25, 0.4, 0.55, 0.7]
        for pct in checkpoints:
            start = duration * pct
            if start + 60 <= duration:
                segments.append((start, 1.0))
            elif start + 30 <= duration:
                segments.append((start, 0.8))
            elif start + 15 <= duration:
                segments.append((start, 0.6))

    # Sort by energy score descending
    segments.sort(key=lambda x: x[1], reverse=True)

    # Pad to at least 3 entries so rank indexing never fails
    while len(segments) < 3:
        segments.append((0, 0.5))

    return segments


def _wrap_hook_text(text: str, max_chars: int = 32) -> str:
    """Wrap hook text at max_chars, return newline-joined lines."""
    lines = textwrap.wrap(text, width=max_chars)
    return '\n'.join(lines) if lines else text


def _escape_ffmpeg_text(text: str) -> str:
    """Escape special characters for ffmpeg drawtext."""
    return (
        text
        .replace('\\', '\\\\')
        .replace("'", "\\'")
        .replace(':', '\\:')
        .replace('%', '\\%')
    )


def format_to_vertical(video_path: str, output_path: str,
                        start: float, duration: float,
                        hook_text: str = None,
                        already_vertical: bool = False):
    """
    Cut a clip starting at `start` for `duration` seconds, format to 9:16
    (1080x1920), apply cinematic grade + vignette, and optionally burn in
    hook text with a dark translucent bar behind it.

    If already_vertical is True, skips the blur-fill trick and scales directly.
    """

    # ── 1. Build base scaling / blur-fill filter ─────────────────────────────
    if already_vertical:
        # Input is already portrait — just scale to exact output dimensions
        blur_bg = (
            f"[0:v]scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=decrease,"
            f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2,setsar=1[composited]"
        )
    else:
        # Landscape or square input — scale to width, pad height with blurred fill
        blur_bg = (
            f"[0:v]scale={OUTPUT_W}:-2,setsar=1[scaled];"
            f"[0:v]scale={OUTPUT_W}:{OUTPUT_H},"
            f"boxblur=luma_radius=25:luma_power=2,"
            f"eq=brightness=-0.1[blurred];"
            f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2[composited]"
        )

    # ── 2. Cinematic color grade + vignette (always applied) ─────────────────
    grade_vignette = (
        ";[composited]"
        "eq=contrast=1.06:brightness=0.01:saturation=1.08,"
        "colorbalance=bs=-0.06:gs=-0.02:rs=0.05,"
        "vignette=PI/4.5"
        "[graded]"
    )

    # ── 3. Optional hook text overlay ────────────────────────────────────────
    if hook_text:
        wrapped = _wrap_hook_text(hook_text, max_chars=32)
        safe_text = _escape_ffmpeg_text(wrapped)

        # Dark translucent bar behind text: drawbox covers a strip at y=90,
        # height sized to fit the text lines (approx 80px per line + padding)
        line_count = wrapped.count('\n') + 1
        bar_height = line_count * 80 + 40  # 80px per line + 20px top/bottom padding
        bar_y = 90

        text_filter = (
            f";[graded]"
            f"drawbox=x=0:y={bar_y}:w=iw:h={bar_height}:"
            f"color=black@0.75:t=fill,"
            f"drawtext="
            f"text='{safe_text}':"
            f"fontsize=56:"
            f"fontcolor=white:"
            f"x=(w-text_w)/2:"
            f"y={bar_y + 20}:"
            f"line_spacing=14"
            f"[out]"
        )
        vf = blur_bg + grade_vignette + text_filter
    else:
        # No text — just rename [graded] to [out]
        vf = blur_bg + grade_vignette + ";[graded]copy[out]"

    map_str = '[out]'

    # ── 4. Audio: loudnorm to -14 LUFS / -2 TP ───────────────────────────────
    af = "loudnorm=I=-14:TP=-2:LRA=11"

    # ── 5. Build ffmpeg command ───────────────────────────────────────────────
    cmd = [
        'ffmpeg', '-y',
        '-ss', str(start),
        '-i', video_path,
        '-t', str(duration),
        '-vf', vf,
        '-map', map_str,
        '-map', '0:a?',
        '-c:v', 'libx264',
        '-preset', 'slow',
        '-crf', '18',
        '-b:v', '5M',
        '-maxrate', '6M',
        '-bufsize', '10M',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-af', af,
        '-movflags', '+faststart',
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.error(f"ffmpeg error: {result.stderr[-1000:]}")
        raise RuntimeError(f"ffmpeg failed for {output_path}")

    logger.info(f"Created clip: {output_path} ({duration}s from {start:.1f}s)")


def process_video(video_path: str, output_dir: str, hooks: dict = None) -> list:
    """
    Process a video into 15s / 30s / 60s short-form clips.

    Each clip length draws from a different ranked segment:
      15s → rank 0 (best energy peak)
      30s → rank 1 (2nd best)
      60s → rank 2 (3rd best)
    This gives variety — each clip starts at a distinct moment.

    hooks: dict mapping clip_length (int) → hook text string (optional)
    Returns list of output file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    output_files = []

    try:
        info = get_video_info(video_path)
        duration = info['duration']
        width = info['width']
        height = info['height']
        logger.info(f"Video info: {duration:.1f}s, {width}x{height}")
    except Exception as e:
        logger.error(f"Could not read video info: {e}")
        return []

    if duration < 10:
        logger.warning(f"Video too short ({duration:.1f}s), skipping")
        return []

    vertical = is_already_vertical(width, height)
    if vertical:
        logger.info("Input is already vertical — skipping blur-fill")

    segments = detect_best_segments(video_path, duration)

    for clip_len in CLIP_LENGTHS:
        if duration < clip_len:
            logger.info(f"Video too short for {clip_len}s clip, skipping")
            continue

        # Pick segment rank for this clip length (0, 1, or 2)
        rank = CLIP_SEGMENT_RANK.get(clip_len, 0)
        # Clamp rank to available segments
        rank = min(rank, len(segments) - 1)
        best_start = segments[rank][0]

        # Ensure the clip fits within the video
        start = min(best_start, max(0, duration - clip_len))

        hook_text = (hooks or {}).get(clip_len)
        out_file = os.path.join(output_dir, f"{base_name}_{clip_len}s.mp4")

        try:
            format_to_vertical(
                video_path, out_file, start, clip_len,
                hook_text=hook_text,
                already_vertical=vertical
            )
            output_files.append(out_file)
        except Exception as e:
            logger.error(f"Failed to create {clip_len}s clip: {e}")

    return output_files
