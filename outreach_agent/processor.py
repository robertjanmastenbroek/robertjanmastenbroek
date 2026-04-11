"""
Video processor — cuts clips, formats to 9:16, burns in hook text.
Uses ffmpeg under the hood.
"""

import os
import json
import logging
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# Output dimensions for short-form vertical video
OUTPUT_W = 1080
OUTPUT_H = 1920

# Clip lengths in seconds
CLIP_LENGTHS = [5, 9, 15]


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


def detect_best_segments(video_path: str, duration: float) -> list:
    """
    Find high-energy segments using audio volume peaks.
    Returns a list of (start_time, energy_score) tuples, sorted by energy descending.
    For event footage, loud = crowd energy = good clip.
    """
    if duration < 15:
        return [(0, 1.0)]

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
    return segments if segments else [(0, 1.0)]


# Visual style per angle — controls how hook text is rendered on the clip
HOOK_STYLES = {
    # Emotional: intimate, cinematic — smaller text, centered vertically, softer border
    'emotional': {
        'fontsize':    44,
        'fontcolor':   'white',
        'borderw':     2,
        'bordercolor': 'black',
        'y':           '(h-text_h)/2',   # vertical center
        'uppercase':   False,
        'wrap_at':     32,
    },
    # Signal: intimate, bottom-third — small text, caption-like, reads as personal address
    'signal': {
        'fontsize':    40,
        'fontcolor':   'white',
        'borderw':     2,
        'bordercolor': 'black',
        'y':           'h*0.72',          # bottom third
        'uppercase':   False,
        'wrap_at':     36,
    },
    # Energy: punchy, loud — biggest text, all-caps, top position, thick border
    'energy': {
        'fontsize':    64,
        'fontcolor':   'white',
        'borderw':     4,
        'bordercolor': 'black',
        'y':           '100',             # top
        'uppercase':   True,
        'wrap_at':     28,
    },
    # Default fallback (original behaviour)
    'default': {
        'fontsize':    52,
        'fontcolor':   'white',
        'borderw':     3,
        'bordercolor': 'black',
        'y':           '120',
        'uppercase':   False,
        'wrap_at':     38,
    },
}


def _render_hook_overlay(hook_text: str, style: dict) -> str:
    """
    Render hook text as a transparent PNG using Pillow.
    Returns path to a temp PNG file (caller must delete after use).
    """
    from PIL import Image, ImageDraw, ImageFont

    display_text = hook_text.upper() if style['uppercase'] else hook_text

    # Wrap at style-defined char width
    words = display_text.split()
    lines, line = [], []
    for w in words:
        line.append(w)
        if len(' '.join(line)) > style['wrap_at']:
            lines.append(' '.join(line[:-1]))
            line = [w]
    if line:
        lines.append(' '.join(line))
    wrapped = '\n'.join(lines)

    # Load a system font — try several locations
    font_size = style['fontsize']
    font = None
    for font_path in [
        '/System/Library/Fonts/HelveticaNeue.ttc',
        '/System/Library/Fonts/Helvetica.ttc',
        '/System/Library/Fonts/SFNSDisplay.ttf',
        '/Library/Fonts/Arial.ttf',
        '/System/Library/Fonts/Supplemental/Arial.ttf',
    ]:
        try:
            font = ImageFont.truetype(font_path, font_size)
            break
        except (IOError, OSError):
            continue
    if font is None:
        font = ImageFont.load_default()

    # Measure text on a scratch image
    scratch = Image.new('RGBA', (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(scratch)
    bbox = d.multiline_textbbox((0, 0), wrapped, font=font, spacing=12, align='center')
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Resolve y position
    y_expr = style['y']
    if y_expr == '(h-text_h)/2':
        y = (OUTPUT_H - text_h) // 2
    elif y_expr.startswith('h*'):
        y = int(OUTPUT_H * float(y_expr[2:]))
    else:
        try:
            y = int(y_expr)
        except ValueError:
            y = 120

    x = (OUTPUT_W - text_w) // 2

    # Draw on transparent canvas
    img = Image.new('RGBA', (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    border = style['borderw']
    bc = (0, 0, 0, 220) if style['bordercolor'] == 'black' else (255, 255, 255, 220)
    tc = (255, 255, 255, 255) if style['fontcolor'] == 'white' else (0, 0, 0, 255)

    # Draw border by offsetting in all directions
    for dx in range(-border, border + 1):
        for dy in range(-border, border + 1):
            if dx != 0 or dy != 0:
                draw.multiline_text((x + dx, y + dy), wrapped, font=font,
                                    fill=bc, spacing=12, align='center')
    draw.multiline_text((x, y), wrapped, font=font, fill=tc, spacing=12, align='center')

    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    img.save(tmp.name, 'PNG')
    tmp.close()
    return tmp.name


def format_to_vertical(video_path: str, output_path: str,
                        start: float, duration: float,
                        hook_text: str = None, angle: str = None):
    """
    Cut a clip starting at `start` for `duration` seconds,
    format to 9:16 (1080x1920) with blurred background fill,
    and optionally burn in hook text styled for the given angle.
    Text is rendered via Pillow and composited with ffmpeg overlay.
    """
    blur_bg = (
        f"[0:v]scale={OUTPUT_W}:-1,setsar=1[scaled];"
        f"[0:v]scale={OUTPUT_W}:{OUTPUT_H},boxblur=20:1[blurred];"
        f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2[composited]"
    )

    overlay_png = None
    try:
        if hook_text:
            style = HOOK_STYLES.get(angle, HOOK_STYLES['default'])
            overlay_png = _render_hook_overlay(hook_text, style)

            vf = blur_bg + f";[composited][1:v]overlay=0:0[out]"
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(start),
                '-i', video_path,
                '-i', overlay_png,
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
            vf = blur_bg + ';[composited]copy[out]'
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
        if overlay_png:
            try:
                os.unlink(overlay_png)
            except OSError:
                pass


def process_video(video_path: str, output_dir: str, hooks: dict = None, angle: str = None) -> list:
    """
    Process a video into short-form clips.
    - hooks: dict mapping clip_length (int) to hook text string
    - angle: "emotional" | "bts" | "energy" | None — controls hook overlay style
    Returns list of output file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    output_files = []

    try:
        info = get_video_info(video_path)
        duration = info['duration']
        logger.info(f"Video info: {duration:.1f}s, {info['width']}x{info['height']}")
    except Exception as e:
        logger.error(f"Could not read video info: {e}")
        return []

    if duration < 10:
        logger.warning(f"Video too short ({duration:.1f}s), skipping")
        return []

    segments = detect_best_segments(video_path, duration)
    best_start = segments[0][0] if segments else 0

    for clip_len in CLIP_LENGTHS:
        if duration < clip_len:
            logger.info(f"Video too short for {clip_len}s clip, skipping")
            continue

        # Use the best high-energy segment, but make sure it fits
        start = min(best_start, max(0, duration - clip_len))

        hook_text = (hooks or {}).get(clip_len)
        out_file = os.path.join(output_dir, f"{base_name}_{clip_len}s.mp4")

        try:
            format_to_vertical(video_path, out_file, start, clip_len, hook_text, angle)
            output_files.append(out_file)
        except Exception as e:
            logger.error(f"Failed to create {clip_len}s clip: {e}")

    return output_files


def is_already_vertical(width: int, height: int) -> bool:
    """Check if a video is already in 9:16 format."""
    if width == 0 or height == 0:
        return False
    return height / width >= 1.6
