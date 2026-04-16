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


def _escape_drawtext(text: str) -> str:
    """Escape a string for safe inclusion in ffmpeg drawtext filter.

    ffmpeg drawtext treats these as syntax: backslash, colon, single quote,
    percent, and the curly braces used in expression evaluation. Without full
    escaping, a hook containing a colon (common — "Ever felt like: ...") or
    a percent sign silently breaks the entire filter graph, returning the
    un-overlaid video at best or crashing ffmpeg at worst.
    """
    # Order matters: escape backslash FIRST so later escapes don't get
    # double-escaped.
    out = text.replace("\\", "\\\\")
    out = out.replace("'", "\\'")
    out = out.replace(":", "\\:")
    out = out.replace("%", "\\%")
    return out


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
    "emotional": {"font_size": 68, "y_pct": 0.78, "wrap": 16},
    "performance": {"font_size": 52, "y_pct": 0.85, "wrap": 20},
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


def _crop_to_vertical(input_path: str, output_path: str) -> str:
    """Crop any video to 9:16 vertical (1080x1920)."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,crop={OUTPUT_W}:{OUTPUT_H}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
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
        # Time-windowed eq: boost brightness+saturation for the first
        # SCROLL_STOP_DURATION seconds, then settle to the platform grade.
        # ``enable`` applies the first filter only when t < threshold.
        b_boost = grade["brightness"] + SCROLL_STOP_BRIGHTNESS
        s_boost = grade["saturation"] * SCROLL_STOP_SATURATION
        # Use a single eq filter whose params are ffmpeg expressions so
        # brightness/saturation ramp down from the boost to the normal grade
        # across SCROLL_STOP_DURATION. Smooth ramp avoids a visible "pop".
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
        # Fall back to simple grade without scroll-stop ramp
        if scroll_stop:
            return _apply_color_grade(input_path, output_path, platform, scroll_stop=False)
        # Copy input to output so the pipeline can continue without color grade.
        # Previously returned `input_path`, which meant downstream stages read from
        # a file that didn't match `output_path` — the next stage then tried to
        # read the (non-existent) output and failed the whole clip.
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
    """Burn text overlay onto video using ffmpeg drawtext.

    Uses Bebas Neue font with white text + black shadow.
    """
    s = HOOK_STYLES.get(style, HOOK_STYLES["emotional"])
    font_size = s["font_size"]
    y_pos = int(OUTPUT_H * s["y_pct"])

    # Escape text for ffmpeg drawtext (handles : % \ ' safely)
    escaped = _escape_drawtext(text)

    info = _get_video_info(input_path)
    if end_time is None:
        end_time = info["duration"] - 1.0 if info["duration"] > 1 else info["duration"]

    # Fade in at start_time, fade out at end_time
    alpha_expr = (
        f"if(lt(t,{start_time}),0,"
        f"if(lt(t,{start_time + 0.3}),(t-{start_time})/0.3,"
        f"if(lt(t,{end_time}),1,"
        f"if(lt(t,{end_time + 0.5}),1-(t-{end_time})/0.5,0))))"
    )

    # Try Bebas Neue, fall back to Helvetica, then default
    font = "/System/Library/Fonts/Helvetica.ttc"
    for candidate in [
        os.path.expanduser("~/.fonts/BebasNeue-Regular.ttf"),
        "/Library/Fonts/BebasNeue-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        if os.path.exists(candidate):
            font = candidate
            break

    escaped_font = _escape_drawtext(font)
    drawtext = (
        f"drawtext=text='{escaped}'"
        f":fontfile='{escaped_font}'"
        f":fontsize={font_size}"
        f":fontcolor=white"
        f":shadowcolor=black@0.6:shadowx=3:shadowy=3"
        f":x=(w-text_w)/2:y={y_pos}"
        f":alpha='{alpha_expr}'"
    )

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"Text overlay failed: {e.stderr.decode()[:300]}")
        return input_path  # return without overlay rather than crash


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
    bait_vert = str(work_dir / "_bait_vert.mp4")
    _crop_to_vertical(bait_clip, bait_vert)

    # 2. Prepare content segments -- concat and trim to content_duration
    if len(content_segments) == 1:
        content_vert = str(work_dir / "_content_vert.mp4")
        _crop_to_vertical(content_segments[0], content_vert)
    else:
        # Concat multiple segments
        seg_files = []
        for i, seg in enumerate(content_segments):
            seg_path = str(work_dir / f"_seg_{i}.mp4")
            _crop_to_vertical(seg, seg_path)
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
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-an", raw_video,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 4. Overlay track audio from 0:00
    from content_engine.audio_engine import mix_audio_onto_video
    with_audio = str(work_dir / "_with_audio.mp4")
    mix_audio_onto_video(raw_video, audio_path, audio_start, target_duration, with_audio)

    # 5. Burn hook text on bait portion (0s to bait_duration)
    with_hook = str(work_dir / "_with_hook.mp4")
    _burn_text_overlay(with_audio, with_hook, hook_text, "transitional", 0.0, bait_duration - 0.3)

    # 6. Burn track label on content portion
    with_label = str(work_dir / "_with_label.mp4")
    _burn_text_overlay(with_hook, with_label, track_label, "performance", bait_duration + 0.5)

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
    if len(content_segments) == 1:
        content_vert = str(work_dir / "_emo_vert.mp4")
        _crop_to_vertical(content_segments[0], content_vert)
    else:
        seg_files = []
        for i, seg in enumerate(content_segments):
            sp = str(work_dir / f"_emo_seg_{i}.mp4")
            _crop_to_vertical(seg, sp)
            seg_files.append(sp)
        concat_list = str(work_dir / "_emo_concat.txt")
        with open(concat_list, "w") as f:
            for sf in seg_files:
                f.write(f"file '{_escape_concat(sf)}'\n")
        content_vert = str(work_dir / "_emo_vert.mp4")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list, "-t", str(target_duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-an", content_vert,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 2. Trim to target duration
    trimmed = str(work_dir / "_emo_trimmed.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", content_vert, "-t", str(target_duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-an", trimmed,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 3. Add audio
    from content_engine.audio_engine import mix_audio_onto_video
    with_audio = str(work_dir / "_emo_audio.mp4")
    mix_audio_onto_video(trimmed, audio_path, audio_start, target_duration, with_audio)

    # 4. Burn prominent hook text
    with_hook = str(work_dir / "_emo_hook.mp4")
    _burn_text_overlay(with_audio, with_hook, hook_text, "emotional")

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
    seg_files = []
    seg_duration = target_duration / max(len(content_segments), 1)
    for i, seg in enumerate(content_segments):
        sp = str(work_dir / f"_perf_seg_{i}.mp4")
        # Crop to vertical and trim to segment duration
        cmd = [
            "ffmpeg", "-y", "-i", seg,
            "-t", str(seg_duration),
            "-vf", f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,crop={OUTPUT_W}:{OUTPUT_H}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-an", sp,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
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
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-an", concat_out,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 2. Add audio (peak energy section)
    from content_engine.audio_engine import mix_audio_onto_video
    with_audio = str(work_dir / "_perf_audio.mp4")
    mix_audio_onto_video(concat_out, audio_path, audio_start, target_duration, with_audio)

    # 3. Minimal text overlay
    with_text = str(work_dir / "_perf_text.mp4")
    _burn_text_overlay(with_audio, with_text, hook_text, "performance")

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
    in the bottom 15%.
    """
    cta_text = f"Listen on Spotify: {track_title}"
    y_pos = int(OUTPUT_H * 0.88)  # bottom 12%

    escaped = _escape_drawtext(cta_text)

    font = "/System/Library/Fonts/Helvetica.ttc"
    for candidate in [
        os.path.expanduser("~/.fonts/BebasNeue-Regular.ttf"),
        "/Library/Fonts/BebasNeue-Regular.ttf",
    ]:
        if os.path.exists(candidate):
            font = candidate
            break

    escaped_font = _escape_drawtext(font)
    drawtext = (
        f"drawtext=text='{escaped}'"
        f":fontfile='{escaped_font}':fontsize=42"
        f":fontcolor=white:shadowcolor=black@0.8:shadowx=2:shadowy=2"
        f":x=(w-text_w)/2:y={y_pos}"
    )

    cmd = [
        "ffmpeg", "-y", "-i", source_clip,
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return output_path
    except subprocess.CalledProcessError:
        logger.warning("Story CTA overlay failed, using original clip")
        import shutil
        shutil.copy2(source_clip, output_path)
        return output_path
