"""
renderer.py — Unified video rendering for all 3 clip formats.

Consolidates the legacy outreach_agent/processor.py and assembler logic into
one module with three render paths: transitional, emotional, performance.
Plus Stories variant and output validation.
"""
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).parent.parent
logger = logging.getLogger(__name__)

OUTPUT_W, OUTPUT_H = 1080, 1920

# Scroll-stop engineering: first N seconds get a brightness + saturation boost
# so the thumbnail jumps out of the feed. Tuned for 300ms — any longer and it
# looks artificial; any shorter and the algorithm misses it.
SCROLL_STOP_DURATION = 0.3
SCROLL_STOP_BRIGHTNESS = 0.08    # +0.08 (scale 0-1) for first 300ms
SCROLL_STOP_SATURATION = 1.25    # 25% saturation boost for first 300ms


def _escape_concat(path: str) -> str:
    """Escape a path for use inside an ffmpeg concat demuxer list.

    The concat demuxer reads lines like ``file 'PATH'`` and treats single
    quotes as terminators. A path with a single quote — or more dangerously,
    a path a caller didn't sanitize that contains ``'; rm -rf /`` — would
    break out of the quoted context. Double escape: first the backslash,
    then the quote.
    """
    return path.replace("\\", "\\\\").replace("'", "'\\''")

# --- Platform color grades -------------------------------------------------------

PLATFORM_GRADES = {
    "instagram": {"contrast": 1.1, "saturation": 1.15, "gamma": 1.0, "brightness": 0.02},
    "tiktok": {"contrast": 1.1, "saturation": 1.15, "gamma": 1.0, "brightness": 0.02},
    "youtube": {"contrast": 1.0, "saturation": 1.0, "gamma": 1.0, "brightness": 0.0},
    "facebook": {"contrast": 1.05, "saturation": 1.08, "gamma": 1.02, "brightness": 0.01},
}

# --- Hook text styling ------------------------------------------------------------

HOOK_STYLES = {
    "transitional": {"font_size": 68, "y_pct": 0.78, "wrap": 16},
    "emotional":    {"font_size": 68, "y_pct": 0.78, "wrap": 16},
    "performance":  {"font_size": 52, "y_pct": 0.85, "wrap": 20},
    "story":        {"font_size": 42, "y_pct": 0.88, "wrap": 24},
}


def get_platform_color_grade(platform: str) -> dict:
    """Get color grade settings for a platform, defaulting to youtube (neutral)."""
    return PLATFORM_GRADES.get(platform, PLATFORM_GRADES["youtube"])


def _get_video_info(path: str) -> dict:
    """Get video duration, width, height via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        video_stream = next(s for s in data["streams"] if s["codec_type"] == "video")
        return {
            "duration": duration,
            "width": int(video_stream["width"]),
            "height": int(video_stream["height"]),
        }
    except Exception as e:
        logger.error(f"ffprobe failed for {path}: {e}")
        return {"duration": 0, "width": 0, "height": 0}


def validate_output(path: str, target_duration: float) -> dict:
    """Validate rendered output. Returns {valid: bool, errors: list}."""
    errors = []
    p = Path(path)

    if not p.exists():
        return {"valid": False, "errors": ["file does not exist"]}

    if p.stat().st_size < 100_000:  # 100KB
        return {"valid": False, "errors": ["file too small (< 100KB), likely corrupt"]}

    info = _get_video_info(path)
    if info["duration"] == 0:
        errors.append("could not read duration (invalid container)")
    elif abs(info["duration"] - target_duration) > 1.5:
        errors.append(f"duration {info['duration']:.1f}s, expected {target_duration:.1f}s (+/-1.5s)")

    if info["width"] != OUTPUT_W or info["height"] != OUTPUT_H:
        if info["width"] > 0:  # only flag if we could read dimensions
            errors.append(f"resolution {info['width']}x{info['height']}, expected {OUTPUT_W}x{OUTPUT_H}")

    # Check audio stream
    try:
        cmd = ["ffprobe", "-v", "quiet", "-select_streams", "a",
               "-show_entries", "stream=codec_type", path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if "audio" not in result.stdout:
            errors.append("no audio stream")
    except Exception:
        errors.append("could not check audio stream")

    return {"valid": len(errors) == 0, "errors": errors}


def _crop_to_vertical(input_path: str, output_path: str, max_duration: float | None = None) -> str:
    """Crop any video to 9:16 vertical (1080x1920).

    ``max_duration`` limits how much of the input is decoded — pass it when
    working with large phone footage files (multi-GB MOV/MP4) to keep the
    operation well under the 120s timeout. The flag is placed before ``-i``
    so ffmpeg stops reading the input stream after that many seconds, which
    is far faster than an output-side ``-t``.

    Uses ``ultrafast`` preset for intermediate crops so even large sources
    complete quickly. Quality is slightly lower (crf 26) but this is an
    intermediate file that will be re-encoded by the color-grade step anyway.
    """
    cmd = ["ffmpeg", "-y"]
    if max_duration is not None:
        cmd += ["-t", str(max_duration)]  # input duration limit — must be before -i
    cmd += [
        "-i", input_path,
        "-r", "30",  # normalize to 30fps — without this, mixed-rate sources (24/30/60fps)
        # produce frame drops when concatenated via the concat demuxer
        "-vf", f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,crop={OUTPUT_W}:{OUTPUT_H}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        "-an", output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    return output_path


def _apply_color_grade(input_path: str, output_path: str, platform: str,
                       scroll_stop: bool = True) -> str:
    """Apply platform-specific color grading via ffmpeg eq filter.

    When ``scroll_stop`` is True, the first 300ms get an extra brightness +
    saturation boost. This is the single highest-leverage scroll-stop
    engineering trick: a thumbnail frame that's a notch brighter/punchier
    than what the feed algorithm expects. The effect is subtle visually but
    measurable in scroll-stop rate.
    """
    grade = get_platform_color_grade(platform)
    if scroll_stop:
        b_boost = grade["brightness"] + SCROLL_STOP_BRIGHTNESS
        s_boost = grade["saturation"] * SCROLL_STOP_SATURATION
        b_expr = (
            f"if(lt(t,{SCROLL_STOP_DURATION}),"
            f"{b_boost}-((t/{SCROLL_STOP_DURATION})*{SCROLL_STOP_BRIGHTNESS}),"
            f"{grade['brightness']})"
        )
        s_expr = (
            f"if(lt(t,{SCROLL_STOP_DURATION}),"
            f"{s_boost}-((t/{SCROLL_STOP_DURATION})*{grade['saturation']*(SCROLL_STOP_SATURATION-1)}),"
            f"{grade['saturation']})"
        )
        eq_filter = (
            f"eq=contrast={grade['contrast']}"
            f":saturation='{s_expr}'"
            f":gamma={grade['gamma']}"
            f":brightness='{b_expr}'"
        )
    else:
        eq_filter = (
            f"eq=contrast={grade['contrast']}:saturation={grade['saturation']}"
            f":gamma={grade['gamma']}:brightness={grade['brightness']}"
        )
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", eq_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.warning(f"Color grade failed for {platform}: {e.stderr.decode()[:200] if e.stderr else ''}")
        if scroll_stop:
            return _apply_color_grade(input_path, output_path, platform, scroll_stop=False)
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path


def _burn_text_overlay(
    input_path: str,
    output_path: str,
    text: str,
    style: str = "emotional",
    start_time: float = 0.0,
    end_time: float | None = None,
) -> str:
    """Burn text overlay using Pillow (PNG) + ffmpeg overlay filter.

    Replaces the drawtext approach which requires --enable-libfreetype in
    ffmpeg. Pillow renders the text to a transparent RGBA PNG sized to the
    full frame (1080×1920), positioned correctly, then ffmpeg composites it
    using the overlay filter with alpha fade in/out.

    Returns output_path on success, input_path on failure so that callers
    can safely chain: ``next_input = _burn_text_overlay(...)``
    """
    from PIL import Image, ImageDraw, ImageFont

    s = HOOK_STYLES.get(style, HOOK_STYLES["emotional"])
    font_size = s["font_size"]
    y_pct = s["y_pct"]
    wrap_chars = s["wrap"]

    info = _get_video_info(input_path)
    if end_time is None:
        end_time = max(start_time + 0.5, info["duration"] - 1.0) if info["duration"] > 1 else info["duration"]

    # --- Word-wrap ---
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        line_len = sum(len(w) for w in current) + len(current) + len(word)
        if line_len <= wrap_chars:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))

    # --- Load font ---
    font = None
    for candidate in [
        os.path.expanduser("~/.fonts/BebasNeue-Regular.ttf"),
        "/Library/Fonts/BebasNeue-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
    ]:
        if os.path.exists(candidate):
            try:
                font = ImageFont.truetype(candidate, font_size)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()

    # --- Create RGBA frame ---
    canvas = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    line_height = font_size + 8
    total_height = len(lines) * line_height
    y_start = int(OUTPUT_H * y_pct) - total_height // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (OUTPUT_W - text_w) // 2
        y = y_start + i * line_height
        draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 180))   # shadow
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))      # text

    # --- Write PNG to temp file, composite with ffmpeg overlay ---
    overlay_png = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            overlay_png = tmp.name
        canvas.save(overlay_png, "PNG")

        fade_out_start = max(start_time + 0.3, end_time - 0.5)
        fade_filter = (
            f"[1:v]format=rgba,"
            f"fade=t=in:st={start_time}:d=0.3:alpha=1,"
            f"fade=t=out:st={fade_out_start}:d=0.5:alpha=1[txt];"
            f"[0:v][txt]overlay=0:0"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", overlay_png,
            "-filter_complex", fade_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "copy",
            output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return output_path

    except subprocess.CalledProcessError as e:
        logger.error(f"Text overlay failed: {e.stderr.decode()[:300] if e.stderr else ''}")
        return input_path
    except Exception as e:
        logger.error(f"Text overlay error: {e}")
        return input_path
    finally:
        if overlay_png:
            try:
                os.unlink(overlay_png)
            except Exception:
                pass


def render_transitional(
    bait_clip: str,
    content_segments: list[str],
    audio_path: str,
    audio_start: float,
    hook_text: str,
    track_label: str,
    platform: str,
    output_path: str,
    target_duration: float = 22.0,
) -> str:
    """Render a transitional hook clip.

    1. Crop bait clip to vertical (muted)
    2. Crop + concat content segments
    3. Hard cut: bait + content
    4. Overlay track audio from 0:00
    5. Burn hook text on bait portion
    6. Burn track label on content portion
    7. Platform color grade
    """
    work_dir = Path(output_path).parent
    work_dir.mkdir(parents=True, exist_ok=True)

    bait_info = _get_video_info(bait_clip)
    bait_duration = min(bait_info["duration"], 7.0)  # cap at 7s
    content_duration = target_duration - bait_duration

    # 1. Crop bait to vertical, strip audio
    # Bait clips are short by design — add 1s headroom and cap source reading.
    bait_vert = str(work_dir / "_bait_vert.mp4")
    _crop_to_vertical(bait_clip, bait_vert, max_duration=bait_duration + 1.0)

    # 2. Prepare content segments -- concat and trim to content_duration
    # Limit each source read to its share of content_duration + 2s headroom so
    # large phone footage files complete within the 120s timeout.
    if len(content_segments) == 1:
        content_vert = str(work_dir / "_content_vert.mp4")
        _crop_to_vertical(content_segments[0], content_vert, max_duration=content_duration + 2.0)
    else:
        per_seg_max = content_duration / len(content_segments) + 2.0
        seg_files = []
        for i, seg in enumerate(content_segments):
            seg_path = str(work_dir / f"_seg_{i}.mp4")
            _crop_to_vertical(seg, seg_path, max_duration=per_seg_max)
            seg_files.append(seg_path)

        concat_list = str(work_dir / "_concat.txt")
        with open(concat_list, "w") as f:
            for sf in seg_files:
                f.write(f"file '{_escape_concat(sf)}'\n")

        content_vert = str(work_dir / "_content_vert.mp4")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-t", str(content_duration),
            "-r", "30",  # defensive: enforce 30fps across mixed-rate sources
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-an", content_vert,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 3. Concat bait + content (hard cut)
    concat_final = str(work_dir / "_concat_final.txt")
    with open(concat_final, "w") as f:
        f.write(f"file '{_escape_concat(bait_vert)}'\n")
        f.write(f"file '{_escape_concat(content_vert)}'\n")

    raw_video = str(work_dir / "_raw_concat.mp4")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_final,
        "-r", "30",  # enforce 30fps for bait→content hard-cut concat
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-an", raw_video,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 4. Overlay track audio from 0:00
    from content_engine.audio_engine import mix_audio_onto_video
    with_audio = str(work_dir / "_with_audio.mp4")
    mix_audio_onto_video(raw_video, audio_path, audio_start, target_duration, with_audio)

    # 5. Burn hook text on bait portion (0s to bait_duration)
    #    Use return value — if overlay fails, with_hook is with_audio (exists).
    with_hook = _burn_text_overlay(
        with_audio, str(work_dir / "_with_hook.mp4"),
        hook_text, "transitional", 0.0, bait_duration - 0.3,
    )

    # 6. Burn track label on content portion
    with_label = _burn_text_overlay(
        with_hook, str(work_dir / "_with_label.mp4"),
        track_label, "performance", bait_duration + 0.5,
    )

    # 7. Platform color grade
    _apply_color_grade(with_label, output_path, platform)

    return output_path


def render_emotional(
    content_segments: list[str],
    audio_path: str,
    audio_start: float,
    hook_text: str,
    platform: str,
    output_path: str,
    target_duration: float = 7.0,
) -> str:
    """Render an emotional/POV text hook clip (7s).

    Text hook is prominent. Video is background.
    """
    work_dir = Path(output_path).parent
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Prepare content (single segment or concat)
    # Limit each source read to its share of target_duration + 2s headroom.
    # The stream_loop -1 in the trim step handles any remaining gap if the
    # source is shorter than needed.
    if len(content_segments) == 1:
        content_vert = str(work_dir / "_emo_vert.mp4")
        _crop_to_vertical(content_segments[0], content_vert, max_duration=target_duration + 2.0)
    else:
        per_seg_max = target_duration / len(content_segments) + 2.0
        seg_files = []
        for i, seg in enumerate(content_segments):
            sp = str(work_dir / f"_emo_seg_{i}.mp4")
            _crop_to_vertical(seg, sp, max_duration=per_seg_max)
            seg_files.append(sp)
        concat_list = str(work_dir / "_emo_concat.txt")
        with open(concat_list, "w") as f:
            for sf in seg_files:
                f.write(f"file '{_escape_concat(sf)}'\n")
        content_vert = str(work_dir / "_emo_vert.mp4")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list, "-t", str(target_duration),
            "-r", "30",  # defensive: enforce 30fps across mixed-rate sources
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-an", content_vert,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 2. Trim to target duration (-stream_loop loops if source is shorter)
    trimmed = str(work_dir / "_emo_trimmed.mp4")
    cmd = [
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", content_vert,
        "-t", str(target_duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-an", trimmed,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 3. Add audio
    from content_engine.audio_engine import mix_audio_onto_video
    with_audio = str(work_dir / "_emo_audio.mp4")
    mix_audio_onto_video(trimmed, audio_path, audio_start, target_duration, with_audio)

    # 4. Burn prominent hook text
    #    Use return value — if overlay fails, with_hook is with_audio (exists).
    with_hook = _burn_text_overlay(
        with_audio, str(work_dir / "_emo_hook.mp4"), hook_text, "emotional",
    )

    # 5. Color grade
    _apply_color_grade(with_hook, output_path, platform)

    return output_path


def render_performance(
    content_segments: list[str],
    audio_path: str,
    audio_start: float,
    hook_text: str,
    platform: str,
    output_path: str,
    target_duration: float = 28.0,
) -> str:
    """Render a performance energy clip (28s).

    Music carries it. Minimal text. More segments, faster cuts.
    """
    work_dir = Path(output_path).parent
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Crop + concat all segments
    # Two-step: first transcode source (potentially large MOV/RAW) to a small MP4,
    # then loop that MP4 if it's shorter than seg_duration. Avoids the extreme
    # slowness of stream_loop -1 on multi-GB phone footage files.
    seg_files = []
    seg_duration = target_duration / max(len(content_segments), 1)
    for i, seg in enumerate(content_segments):
        sp = str(work_dir / f"_perf_seg_{i}.mp4")
        tmp_cropped = str(work_dir / f"_perf_src_{i}.mp4")

        # Step A: transcode + crop to vertical (trim to seg_duration to bound output size)
        cmd_a = [
            "ffmpeg", "-y", "-i", seg,
            "-t", str(seg_duration),
            "-vf", f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,crop={OUTPUT_W}:{OUTPUT_H}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
            "-an", tmp_cropped,
        ]
        subprocess.run(cmd_a, check=True, capture_output=True, timeout=120)

        # Step B: if the cropped clip is shorter than seg_duration, loop the small MP4
        src_info = _get_video_info(tmp_cropped)
        if src_info["duration"] >= seg_duration - 0.2:
            import shutil as _shutil
            _shutil.copy2(tmp_cropped, sp)
        else:
            cmd_b = [
                "ffmpeg", "-y", "-stream_loop", "-1", "-i", tmp_cropped,
                "-t", str(seg_duration),
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-an", sp,
            ]
            subprocess.run(cmd_b, check=True, capture_output=True, timeout=60)

        seg_files.append(sp)

    if len(seg_files) == 1:
        concat_out = seg_files[0]
    else:
        concat_list = str(work_dir / "_perf_concat.txt")
        with open(concat_list, "w") as f:
            for sf in seg_files:
                f.write(f"file '{_escape_concat(sf)}'\n")
        concat_out = str(work_dir / "_perf_concat.mp4")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list, "-t", str(target_duration),
            "-r", "30",  # defensive: enforce 30fps even if crops somehow differ
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-an", concat_out,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 2. Add audio (peak energy section)
    from content_engine.audio_engine import mix_audio_onto_video
    with_audio = str(work_dir / "_perf_audio.mp4")
    mix_audio_onto_video(concat_out, audio_path, audio_start, target_duration, with_audio)

    # 3. Minimal text overlay
    #    Use return value — if overlay fails, with_text is with_audio (exists).
    with_text = _burn_text_overlay(
        with_audio, str(work_dir / "_perf_text.mp4"), hook_text, "performance",
    )

    # 4. Color grade
    _apply_color_grade(with_text, output_path, platform)

    return output_path


def render_story_variant(
    source_clip: str,
    track_title: str,
    spotify_url: str,
    output_path: str,
) -> str:
    """Render a Stories variant with Spotify CTA overlay.

    Takes an already-rendered clip and adds "Listen on Spotify" + track title
    in the bottom 12% using the Pillow overlay approach.
    """
    cta_text = f"Listen on Spotify: {track_title}"
    result = _burn_text_overlay(source_clip, output_path, cta_text, "story")
    if result == source_clip:
        # Overlay failed — copy source as fallback
        import shutil
        logger.warning("Story CTA overlay failed, using original clip")
        shutil.copy2(source_clip, output_path)
    return output_path
