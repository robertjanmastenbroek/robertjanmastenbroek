"""
Video processor — cuts clips and formats to 9:16 vertical.
Burns hook text into the clip using high-quality Pillow rendering with drop shadows.
Uses ffmpeg under the hood.
"""

import os
import io
import json
import logging
import subprocess
import tempfile
import textwrap

logger = logging.getLogger(__name__)

OUTPUT_W    = 1080
OUTPUT_H    = 1920
CLIP_LENGTHS = [5, 9, 15]

# ── Text overlay styles ────────────────────────────────────────────────────────
# Per-angle positioning, sizing, and shadow parameters.

HOOK_STYLES = {
    'emotional': {
        'fontsize':      44,
        'uppercase':     False,
        'y_pct':         0.50,   # vertically centered
        'shadow_offset': 3,
        'shadow_blur':   4,
        'wrap_at':       28,
        'align':         'center',
    },
    'signal': {
        'fontsize':      40,
        'uppercase':     False,
        'y_pct':         0.72,   # bottom third
        'shadow_offset': 3,
        'shadow_blur':   3,
        'wrap_at':       30,
        'align':         'center',
    },
    'energy': {
        'fontsize':      68,
        'uppercase':     True,
        'y_pct':         0.06,   # top
        'shadow_offset': 5,
        'shadow_blur':   6,
        'wrap_at':       20,
        'align':         'center',
    },
}

FONT_CANDIDATES = [
    '/Library/Fonts/DIN Condensed Bold.ttf',
    '/Library/Fonts/DIN Alternate Bold.ttf',
    '/Library/Fonts/Futura.ttc',
    '/System/Library/Fonts/HelveticaNeue.ttc',
    '/Library/Fonts/Arial Bold.ttf',
    '/Library/Fonts/Impact.ttf',
]


def _load_font(size: int):
    """Load the best available font at the given size."""
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
    Render hook text onto a transparent 1080×1920 PNG with drop shadow.
    Returns the path to a temp PNG file (caller must delete it).
    """
    from PIL import Image, ImageDraw, ImageFilter

    style = HOOK_STYLES.get(angle, HOOK_STYLES['emotional'])

    text = hook_text.upper() if style['uppercase'] else hook_text
    lines = textwrap.wrap(text, width=style['wrap_at'])

    font      = _load_font(style['fontsize'])
    shadow_px = style['shadow_offset']
    blur_r    = style['shadow_blur']

    # Measure total text block height
    from PIL import ImageDraw as _ID
    probe = Image.new('RGBA', (1, 1))
    probe_draw = _ID.Draw(probe)
    line_heights = []
    line_widths  = []
    for line in lines:
        bb = probe_draw.textbbox((0, 0), line, font=font)
        line_widths.append(bb[2] - bb[0])
        line_heights.append(bb[3] - bb[1])

    line_spacing = int(style['fontsize'] * 0.25)
    total_h = sum(line_heights) + line_spacing * (len(lines) - 1)
    max_w   = max(line_widths) if line_widths else OUTPUT_W

    # Canvas
    canvas = Image.new('RGBA', (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))

    # Y anchor
    y_anchor = int(OUTPUT_H * style['y_pct']) - total_h // 2
    y_anchor = max(20, min(y_anchor, OUTPUT_H - total_h - 20))

    for i, line in enumerate(lines):
        y = y_anchor + sum(line_heights[:i]) + line_spacing * i
        x = (OUTPUT_W - line_widths[i]) // 2

        # Shadow layer: draw text at offset onto its own RGBA canvas, then blur
        shadow_layer = Image.new('RGBA', (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow_layer)
        sd.text((x + shadow_px, y + shadow_px), line, font=font, fill=(0, 0, 0, 210))
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(blur_r))

        # Composite shadow then white text
        canvas = Image.alpha_composite(canvas, shadow_layer)
        draw   = ImageDraw.Draw(canvas)
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    canvas.save(tmp.name, 'PNG')
    tmp.close()
    return tmp.name


def get_video_info(video_path: str) -> dict:
    """Return duration, width, height of a video file using ffprobe."""
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
        for pct in [0.1, 0.25, 0.4, 0.55, 0.7]:
            start = duration * pct
            if start + min(CLIP_LENGTHS) <= duration:
                segments.append((start, 1.0))

    segments.sort(key=lambda x: x[1], reverse=True)
    return segments if segments else [(0, 1.0)]


def format_to_vertical(video_path: str, output_path: str,
                        start: float, duration: float,
                        hook_text: str = None, angle: str = None):
    """
    Cut a clip starting at `start` for `duration` seconds.
    Formats to 9:16 (1080x1920) with blurred background fill.
    If hook_text and angle are provided, burns text in with drop shadow.
    """
    overlay_path = None

    try:
        if hook_text and angle:
            try:
                overlay_path = _render_hook_overlay(hook_text, angle)
            except ImportError:
                logger.warning("Pillow not installed — skipping text overlay. Run: pip install Pillow")
                overlay_path = None
            except Exception as e:
                logger.warning(f"Text overlay render failed: {e} — producing clean clip")
                overlay_path = None

        if overlay_path:
            vf = (
                f"[0:v]scale={OUTPUT_W}:-1,setsar=1[scaled];"
                f"[0:v]scale={OUTPUT_W}:{OUTPUT_H},boxblur=20:1[blurred];"
                f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2[composited];"
                f"[composited][1:v]overlay=0:0[out]"
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
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                '-s', f'{OUTPUT_W}x{OUTPUT_H}',
                output_path,
            ]
        else:
            vf = (
                f"[0:v]scale={OUTPUT_W}:-1,setsar=1[scaled];"
                f"[0:v]scale={OUTPUT_W}:{OUTPUT_H},boxblur=20:1[blurred];"
                f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2[composited];"
                f"[composited]copy[out]"
            )
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(start),
                '-i', video_path,
                '-t', str(duration),
                '-filter_complex', vf,
                '-map', '[out]',
                '-map', '0:a?',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart',
                '-s', f'{OUTPUT_W}x{OUTPUT_H}',
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
