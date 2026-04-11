"""
Video processor — cuts clips and formats to 9:16 vertical.
Uses center-crop to fill (no blur bars).
Burns hook text using Impact font with thick stroke — matches Holy Rave visual style.
Supports multi-clip beat-sync editing: stitches equal-length segments from different sources.
"""

import os
import json
import logging
import subprocess
import tempfile
import textwrap

logger = logging.getLogger(__name__)

OUTPUT_W     = 1080
OUTPUT_H     = 1920
CLIP_LENGTHS = [5, 9, 15]

# Impact is the primary font — bold, condensed, readable at any size, matches reference style.
# Fall back down the list if not found.
FONT_CANDIDATES = [
    '/Library/Fonts/Impact.ttf',
    '/Library/Fonts/Arial Black.ttf',
    '/Library/Fonts/DIN Condensed Bold.ttf',
    '/Library/Fonts/DIN Alternate Bold.ttf',
    '/System/Library/Fonts/HelveticaNeue.ttc',
]

# Per-angle hook style — all use Impact, all uppercase.
# Differentiated only by size and vertical position.
HOOK_STYLES = {
    'emotional': {
        'fontsize': 78,
        'uppercase': True,
        'y_pct':    0.50,   # always center
        'stroke_w': 9,
        'wrap_at':  18,
    },
    'signal': {
        'fontsize': 78,
        'uppercase': True,
        'y_pct':    0.50,   # always center
        'stroke_w': 9,
        'wrap_at':  18,
    },
    'energy': {
        'fontsize': 84,
        'uppercase': True,
        'y_pct':    0.50,   # always center
        'stroke_w': 10,
        'wrap_at':  16,
    },
    'default': {
        'fontsize': 78,
        'uppercase': True,
        'y_pct':    0.50,   # always center
        'stroke_w': 9,
        'wrap_at':  18,
    },
}

PAD_X = 60   # horizontal padding from frame edge


def _load_font(size: int):
    from PIL import ImageFont
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _render_hook_overlay(hook_text: str, angle: str) -> str:
    """
    Render hook text onto a transparent 1080×1920 PNG.
    Uses Impact font + thick black stroke to match reference style.
    Returns path to temp PNG (caller must delete).
    """
    from PIL import Image, ImageDraw

    style = HOOK_STYLES.get(angle, HOOK_STYLES['default'])
    text  = hook_text.upper()
    lines = textwrap.wrap(text, width=style['wrap_at'])
    if not lines:
        lines = [text]

    font     = _load_font(style['fontsize'])
    stroke_w = style['stroke_w']
    max_text_w = OUTPUT_W - PAD_X * 2  # noqa: F841

    # Measure each line
    probe      = Image.new('RGBA', (1, 1))
    probe_draw = ImageDraw.Draw(probe)
    line_bboxes = []
    for line in lines:
        bb = probe_draw.textbbox((0, 0), line, font=font, stroke_width=stroke_w)
        line_bboxes.append(bb)

    line_h  = max(bb[3] - bb[1] for bb in line_bboxes) if line_bboxes else style['fontsize']
    spacing = int(style['fontsize'] * 0.18)
    total_h = line_h * len(lines) + spacing * (len(lines) - 1)

    # Y anchor: y_pct of canvas height, centred on the block
    y_anchor = int(OUTPUT_H * style['y_pct']) - total_h // 2
    y_anchor = max(stroke_w + 10, min(y_anchor, OUTPUT_H - total_h - stroke_w - 10))

    canvas = Image.new('RGBA', (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(canvas)

    for i, line in enumerate(lines):
        bb = line_bboxes[i]
        lw = bb[2] - bb[0]
        x  = max(PAD_X, (OUTPUT_W - lw) // 2)
        y  = y_anchor + i * (line_h + spacing)

        # White fill + thick black stroke — Impact reference look
        draw.text(
            (x, y), line, font=font,
            fill=(255, 255, 255, 255),
            stroke_width=stroke_w,
            stroke_fill=(0, 0, 0, 255),
        )

    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    canvas.save(tmp.name, 'PNG')
    tmp.close()
    return tmp.name


def get_video_info(video_path: str) -> dict:
    """Return duration, width, height of a video file."""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams', '-show_format',
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    data     = json.loads(result.stdout)
    duration = float(data['format'].get('duration', 0))
    width = height = 0
    for stream in data.get('streams', []):
        if stream.get('codec_type') == 'video':
            width  = stream.get('width', 0)
            height = stream.get('height', 0)
            break

    return {'duration': duration, 'width': width, 'height': height}


def detect_best_segments(video_path: str, duration: float) -> list:
    """
    Find high-energy segments using audio volume peaks.
    Returns list of (start_time, energy_score) sorted by energy descending.
    """
    if duration < 15:
        return [(0, 1.0)]

    cmd = [
        'ffprobe', '-v', 'quiet',
        '-f', 'lavfi',
        '-i', f'amovie={video_path},astats=metadata=1:reset=1',
        '-show_entries', 'frame_tags=lavfi.astats.Overall.RMS_level',
        '-of', 'csv=p=0',
    ]
    result   = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    segments = []

    if result.returncode == 0 and result.stdout.strip():
        window = 5
        for i, line in enumerate(result.stdout.strip().split('\n')):
            try:
                rms = float(line.strip())
                if rms > -100:
                    start = i * window
                    if start + min(CLIP_LENGTHS) <= duration:
                        segments.append((start, rms))
            except ValueError:
                continue

    if not segments:
        # Prefer middle section — skip first/last 20% to avoid intros/outros
        for pct in [0.3, 0.45, 0.55, 0.4, 0.6]:
            start = duration * pct
            if start + min(CLIP_LENGTHS) <= duration:
                segments.append((start, 1.0))

    segments.sort(key=lambda x: x[1], reverse=True)
    return segments if segments else [(0, 1.0)]


def get_bpm(audio_path: str) -> float:
    """
    Detect BPM of an audio file using librosa.
    Samples from the 30s mark (skips intro) for accuracy.
    Falls back to 130.0 BPM if detection fails.
    """
    try:
        import librosa
        y, sr = librosa.load(audio_path, sr=22050, mono=True, offset=30, duration=60)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        # tempo may be a numpy scalar or 0-d array depending on librosa version
        import numpy as np
        bpm = float(np.asarray(tempo).flat[0])
        # librosa commonly detects half-tempo for electronic music (e.g. 70 instead of 140).
        # Double it if it falls below 100 BPM — psytrance/techno lives at 125–145.
        if 60 <= bpm < 100:
            bpm *= 2
        # Reject obviously wrong values
        if bpm < 60 or bpm > 240:
            logger.warning(f"Suspicious BPM {bpm:.1f} — using 130.0")
            return 130.0
        logger.info(f"Detected BPM: {bpm:.1f}")
        return bpm
    except Exception as e:
        logger.warning(f"BPM detection failed: {e} — using 130.0")
        return 130.0


def format_to_vertical(video_path: str, output_path: str,
                        start: float, duration: float,
                        hook_text: str = None, angle: str = None):
    """
    Cut a clip starting at `start` for `duration` seconds.
    Formats to 9:16 (1080×1920) using center-crop fill — no blur bars.
    Optionally burns in hook text with Impact stroke overlay.
    """
    crop_filter = (
        f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_W}:{OUTPUT_H}"
    )

    overlay_path = None
    try:
        if hook_text and angle:
            try:
                overlay_path = _render_hook_overlay(hook_text, angle)
            except Exception as e:
                logger.warning(f"Hook overlay failed: {e} — producing clean clip")
                overlay_path = None

        if overlay_path:
            vf = (
                f"[0:v]{crop_filter}[cropped];"
                f"[cropped][1:v]overlay=0:0[out]"
            )
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(start),
                '-i', video_path,
                '-i', overlay_path,
                '-t', str(duration),
                '-filter_complex', vf,
                '-map', '[out]',
                '-map', '0:a?',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                output_path,
            ]
        else:
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(start),
                '-i', video_path,
                '-t', str(duration),
                '-vf', crop_filter,
                '-map', '0:v:0',
                '-map', '0:a?',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                output_path,
            ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"ffmpeg error: {result.stderr[-500:]}")
            raise RuntimeError(f"ffmpeg failed for {output_path}")

        logger.info(f"Created clip: {output_path} ({duration}s from {start:.1f}s)")

    finally:
        if overlay_path and os.path.isfile(overlay_path):
            os.unlink(overlay_path)


def format_to_vertical_multiclip(
    video_sources: list,
    output_path: str,
    clip_duration: float,
    hook_text: str = None,
    angle: str = None,
):
    """
    Build a beat-sync multi-clip vertical video from multiple source videos.

    video_sources: list of (video_path: str, start_time: float) tuples.
                   Each entry contributes an equal-length segment.
    clip_duration: total output duration in seconds.
    hook_text / angle: optional hook overlay burned on the final output.

    Strategy:
      1. Encode each segment separately (crop + 30fps, no audio).
      2. Concat with -f concat (fast stream copy).
      3. Apply hook overlay on concatenated clip in a single final pass.
    """
    n       = len(video_sources)
    seg_dur = clip_duration / n

    crop_filter = (
        f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_W}:{OUTPUT_H},fps=30"
    )

    tmp_segs     = []
    concat_path  = None
    list_path    = None
    overlay_path = None

    try:
        # ── Step 1: encode each segment (video only) ──────────────────────────
        for i, (src_path, src_start) in enumerate(video_sources):
            tmp = tempfile.NamedTemporaryFile(suffix=f'_seg{i}.mp4', delete=False)
            tmp.close()
            tmp_segs.append(tmp.name)

            cmd = [
                'ffmpeg', '-y',
                '-ss', str(src_start),
                '-i', src_path,
                '-t', str(seg_dur),
                '-vf', crop_filter,
                '-an',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
                tmp.name,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"Segment {i} encode failed: {r.stderr[-300:]}")

        # ── Step 2: concat all segments ───────────────────────────────────────
        lf = tempfile.NamedTemporaryFile(mode='w', suffix='_list.txt', delete=False)
        for seg in tmp_segs:
            lf.write(f"file '{seg}'\n")
        lf.close()
        list_path = lf.name

        ct = tempfile.NamedTemporaryFile(suffix='_concat.mp4', delete=False)
        ct.close()
        concat_path = ct.name

        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat', '-safe', '0',
            '-i', list_path,
            '-c', 'copy',
            concat_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"Concat failed: {r.stderr[-300:]}")

        # ── Step 3: apply hook overlay + final encode ─────────────────────────
        if hook_text and angle:
            try:
                overlay_path = _render_hook_overlay(hook_text, angle)
            except Exception as e:
                logger.warning(f"Hook overlay failed: {e} — producing clean clip")
                overlay_path = None

        if overlay_path:
            cmd = [
                'ffmpeg', '-y',
                '-i', concat_path,
                '-i', overlay_path,
                '-filter_complex', '[0:v][1:v]overlay=0:0[out]',
                '-map', '[out]',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
                '-movflags', '+faststart',
                output_path,
            ]
        else:
            cmd = [
                'ffmpeg', '-y',
                '-i', concat_path,
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
                '-movflags', '+faststart',
                output_path,
            ]

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"Final encode failed: {r.stderr[-300:]}")

        logger.info(f"Created multiclip: {output_path} ({n} segs × {seg_dur:.2f}s)")

    finally:
        for seg in tmp_segs:
            if os.path.isfile(seg):
                os.unlink(seg)
        if concat_path and os.path.isfile(concat_path):
            os.unlink(concat_path)
        if list_path and os.path.isfile(list_path):
            os.unlink(list_path)
        if overlay_path and os.path.isfile(overlay_path):
            os.unlink(overlay_path)
