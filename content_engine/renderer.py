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

from content_engine.video_codec import video_codec_args

PROJECT_DIR = Path(__file__).parent.parent
logger = logging.getLogger(__name__)

OUTPUT_W, OUTPUT_H = 1080, 1920

# Scroll-stop engineering: first N seconds get a brightness + saturation boost
# so the thumbnail jumps out of the feed. Extended to 600ms per 2026-04-16 audit
# — analytics showed a full second of punch still reads as "intentional grade"
# not "glitch"; 300ms was under-delivering on the pause-on-scroll goal.
SCROLL_STOP_DURATION = 0.6
SCROLL_STOP_BRIGHTNESS = 0.08    # +0.08 (scale 0-1) for first 600ms
SCROLL_STOP_SATURATION = 1.25    # 25% saturation boost for first 600ms

# ─── Encode hardening constants ──────────────────────────────────────────────
# These flags eliminate the "stuck / frozen mid-playback" class of bugs:
#   -pix_fmt yuv420p   → max player compatibility (IG / TikTok strip yuv444p)
#   -fps_mode cfr      → constant frame rate, no VFR freezes at concat cuts
#   -g 30 -keyint_min  → GOP of exactly 1s so IG/TikTok transcoders don't
#                        produce stuck keyframe gaps
#   -movflags +faststart → moov atom at file head → instant playback, no
#                          buffer-stall on feed autoplay
# Applied to every intermediate AND the final output; final gets faststart.
COMMON_VIDEO_FLAGS = [
    "-pix_fmt", "yuv420p",
    "-fps_mode", "cfr",
    "-g", "30",
    "-keyint_min", "30",
    "-sc_threshold", "0",   # disable scene-change keyframes (cleaner GOP)
]
FINAL_MUX_FLAGS = ["-movflags", "+faststart"]


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

# Platform-specific CRF (lower = higher quality / bigger file).
# YouTube rewards visual fidelity → 20. IG/TikTok/FB re-transcode aggressively
# so uploading higher than their target only bloats upload time — tuned to
# each platform's re-encoder sweet spot (determined from 2026-04 audit).
PLATFORM_CRF = {
    "youtube": 20,
    "instagram": 22,
    "facebook": 23,
    "tiktok": 23,
}

# Hook overlay attention window. Research: ~2.5s before scroll risk.
# We cap at 2.2s so the viewer's eye lands on motion (bait clip action)
# before the hook cements, then snaps to the drop. Previous behavior let
# the hook linger for the full bait duration (up to 7s) — well past the
# attention window on TikTok/Reels.
_HOOK_DWELL_MAX_S = 2.2

# --- Hook text styling ------------------------------------------------------------
# y_pct is the CENTER of the text block as a fraction of video height.
# IG Reels UI chrome (like/comment/share buttons, caption) occupies roughly
# the bottom 25% of the 1080x1920 canvas — anchoring text above ~0.72 keeps
# it clear of the UI obstruction on the world's biggest short-form surface.
HOOK_STYLES = {
    "transitional": {"font_size": 68, "y_pct": 0.68, "wrap": 16},
    "emotional":    {"font_size": 68, "y_pct": 0.68, "wrap": 16},
    "performance":  {"font_size": 52, "y_pct": 0.72, "wrap": 20},
    "story":        {"font_size": 42, "y_pct": 0.72, "wrap": 24},
}


def get_platform_color_grade(platform: str) -> dict:
    """Get color grade settings for a platform, defaulting to youtube (neutral)."""
    return PLATFORM_GRADES.get(platform, PLATFORM_GRADES["youtube"])


def get_platform_crf(platform: str) -> int:
    """Get platform-tuned CRF, defaulting to 22 (the old universal setting)."""
    return PLATFORM_CRF.get(platform, 22)


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
        *video_codec_args(26, "ultrafast"),
        *COMMON_VIDEO_FLAGS,
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
    # NOTE: _apply_color_grade is ALWAYS the final render step — this is where
    # we stamp faststart + clean GOP + yuv420p for zero-stutter playback.
    # -force_key_frames 0 pins an I-frame at t=0 so the platform thumbnailer
    # NEVER picks a blurry P-frame reference as the cover; always samples the
    # high-saturation scroll-stop frame we engineered above.
    crf = str(get_platform_crf(platform))
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", eq_filter,
        *video_codec_args(crf, "fast"),
        "-profile:v", "high", "-level", "4.1",
        "-force_key_frames", "0",
        *COMMON_VIDEO_FLAGS,
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        *FINAL_MUX_FLAGS,
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.warning(f"Color grade failed for {platform}: {e.stderr.decode()[:200] if e.stderr else ''}")
        if scroll_stop:
            return _apply_color_grade(input_path, output_path, platform, scroll_stop=False)
        # Last-resort: re-mux with faststart + pix_fmt so playback still works
        try:
            fallback_cmd = [
                "ffmpeg", "-y", "-i", input_path,
                *video_codec_args(crf, "fast"),
                "-force_key_frames", "0",
                *COMMON_VIDEO_FLAGS,
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                *FINAL_MUX_FLAGS,
                output_path,
            ]
            subprocess.run(fallback_cmd, check=True, capture_output=True, timeout=180)
            return output_path
        except Exception:
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
    """Burn branded text overlay using Pillow + ffmpeg overlay filter.

    Design system (Dark / Holy / Futuristic):
    - Font: Bebas Neue (display, uppercase, wide tracking) → Impact fallback
    - Background: full-width semi-transparent dark bar behind each line of
      text (not a floating pill — the bar bleeds edge-to-edge so text is
      always readable regardless of background footage)
    - Text: pure white, 4px multi-directional shadow for depth
    - Accent line: 3px white line above the text block (visual anchor)
    - Position: lower third (y_pct) for feed hooks; upper third for story CTA

    Returns output_path on success, input_path on failure so callers can
    safely chain: ``next_input = _burn_text_overlay(...)``
    """
    from PIL import Image, ImageDraw, ImageFont

    s = HOOK_STYLES.get(style, HOOK_STYLES["emotional"])
    font_size = s["font_size"]
    y_pct = s["y_pct"]
    wrap_chars = s["wrap"]

    info = _get_video_info(input_path)
    if end_time is None:
        natural_end = max(start_time + 0.5, info["duration"] - 1.0) if info["duration"] > 1 else info["duration"]
        end_time = min(natural_end, start_time + _HOOK_DWELL_MAX_S)
    else:
        end_time = min(end_time, start_time + _HOOK_DWELL_MAX_S)

    # --- Load font: Bebas Neue → Impact → Helvetica ---
    font = None
    for candidate in [
        os.path.expanduser("~/.fonts/BebasNeue-Regular.ttf"),
        "/Library/Fonts/BebasNeue-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/Library/Fonts/Impact.ttf",
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

    # --- Word-wrap (pixel-aware when possible) ---
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    max_px = OUTPUT_W - 80  # 40px side padding each side

    def _line_width(words_: list[str]) -> int:
        try:
            tmp = Image.new("RGBA", (1, 1))
            d = ImageDraw.Draw(tmp)
            bb = d.textbbox((0, 0), " ".join(words_), font=font)
            return bb[2] - bb[0]
        except Exception:
            return sum(len(w) for w in words_) * (font_size // 2)

    for word in words:
        test = current + [word]
        if _line_width(test) <= max_px:
            current = test
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    if not lines:
        lines = [text]

    # --- Measure block ---
    line_height = font_size + 12
    pad_v = 18          # top/bottom padding inside each bar
    bar_h = line_height + pad_v * 2
    total_block_h = len(lines) * bar_h
    accent_h = 3        # accent line height
    gap = 8             # gap between accent line and text block

    y_anchor = int(OUTPUT_H * y_pct) - total_block_h // 2

    # --- Render overlay ---
    canvas = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # Accent line (white bar above text block)
    draw.rectangle(
        [0, y_anchor - gap - accent_h, OUTPUT_W, y_anchor - gap],
        fill=(255, 255, 255, 230),
    )

    for i, line in enumerate(lines):
        bar_y0 = y_anchor + i * bar_h
        bar_y1 = bar_y0 + bar_h

        # Full-width dark background bar (alternating opacity for depth)
        bar_alpha = 200 if i % 2 == 0 else 185
        draw.rectangle([0, bar_y0, OUTPUT_W, bar_y1], fill=(0, 0, 0, bar_alpha))

        # Measure text for centering
        try:
            bb = draw.textbbox((0, 0), line, font=font)
            text_w = bb[2] - bb[0]
            text_h = bb[3] - bb[1]
        except Exception:
            text_w = len(line) * (font_size // 2)
            text_h = font_size

        x = (OUTPUT_W - text_w) // 2
        y = bar_y0 + (bar_h - text_h) // 2

        # Multi-directional shadow for depth
        for dx, dy in [(4, 4), (-4, 4), (4, -4), (-4, -4), (0, 4)]:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 160))
        # White text
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

    # --- Composite with ffmpeg overlay ---
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
            *video_codec_args(22, "fast"),
            *COMMON_VIDEO_FLAGS,
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

    # Beat-sync: snap the bait→content hard cut to the nearest beat boundary
    # relative to audio_start. This makes the visual cut land ON the drum hit,
    # which is what the human brain reads as "professionally edited." The
    # audit measured 25-35% lift in completion on beat-aligned cuts vs arbitrary.
    try:
        from content_engine.audio_engine import snap_to_beat
        target_cut_in_track = audio_start + bait_duration
        snapped_cut_in_track = snap_to_beat(audio_path, target_cut_in_track)
        # Translate back to bait_duration space, but keep bait in a sane range
        snapped_bait = snapped_cut_in_track - audio_start
        if 2.0 <= snapped_bait <= 7.0 and abs(snapped_bait - bait_duration) <= 0.35:
            bait_duration = snapped_bait
    except Exception as e:
        logger.warning(f"Beat-snap failed in transitional, using raw cut: {e}")

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
            *video_codec_args(22, "fast"),
            *COMMON_VIDEO_FLAGS,
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
        *video_codec_args(22, "fast"),
        *COMMON_VIDEO_FLAGS,
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

    # 7. Beat-aligned kinetic captions (muted autoplay safety net — 85% of
    #    IG Reels views start muted). Falls back to no-op on failure.
    from content_engine.caption_engine import burn_captions
    with_caps = burn_captions(
        with_label, str(work_dir / "_with_captions.mp4"),
        hook_text, audio_path, audio_start, target_duration,
        start_offset=0.3,
    )

    # 8. Platform color grade (final step — stamps faststart + I-frame at 0)
    _apply_color_grade(with_caps, output_path, platform)

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
            *video_codec_args(22, "fast"),
            *COMMON_VIDEO_FLAGS,
            "-an", content_vert,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 2. Trim to target duration (-stream_loop loops if source is shorter)
    trimmed = str(work_dir / "_emo_trimmed.mp4")
    cmd = [
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", content_vert,
        "-t", str(target_duration),
        *video_codec_args(22, "fast"),
        *COMMON_VIDEO_FLAGS,
        "-an", trimmed,
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

    # 5. Emotional hook text is positioned at y=0.68; captions up at y=0.26
    #    so there's no collision. Start at 0.2s so first hook frame is clean.
    from content_engine.caption_engine import burn_captions
    with_caps = burn_captions(
        with_hook, str(work_dir / "_emo_caps.mp4"),
        hook_text, audio_path, audio_start, target_duration,
        start_offset=0.2,
    )

    # 6. Color grade (final step — stamps faststart + I-frame at 0)
    _apply_color_grade(with_caps, output_path, platform)

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
    #
    # Beat-sync: compute per-segment durations by snapping each cut boundary
    # to the nearest beat in the audio track. The total MUST sum to
    # target_duration, so we compute snapped boundaries within [0, target_duration]
    # relative to audio_start, then diff. Falls back to even split if snap fails.
    n_segs = max(len(content_segments), 1)
    even_duration = target_duration / n_segs
    seg_durations = [even_duration] * n_segs
    try:
        from content_engine.audio_engine import snap_to_beat
        # Snap cut points 1..n-1 (not 0 or end). Keep 0 as 0, end as target_duration.
        cut_points = [0.0]
        for k in range(1, n_segs):
            target_cut = audio_start + k * even_duration
            snapped = snap_to_beat(audio_path, target_cut) - audio_start
            # Clamp so segments stay reasonable (no zero-length or runaway cuts)
            snapped = max(cut_points[-1] + 0.8, min(snapped, target_duration - 0.8 * (n_segs - k)))
            cut_points.append(snapped)
        cut_points.append(target_duration)
        seg_durations = [cut_points[k + 1] - cut_points[k] for k in range(n_segs)]
        # Sanity: if any seg is absurd (<1s or >2x even), revert to even split
        if any(d < 1.0 or d > 2.2 * even_duration for d in seg_durations):
            seg_durations = [even_duration] * n_segs
    except Exception as e:
        logger.warning(f"Beat-snap failed in performance, using even split: {e}")

    seg_files = []
    for i, seg in enumerate(content_segments):
        seg_duration = seg_durations[i]
        sp = str(work_dir / f"_perf_seg_{i}.mp4")
        tmp_cropped = str(work_dir / f"_perf_src_{i}.mp4")

        # Step A: transcode + crop to vertical (trim to seg_duration to bound output size)
        cmd_a = [
            "ffmpeg", "-y", "-i", seg,
            "-t", str(seg_duration),
            "-r", "30",
            "-vf", f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,crop={OUTPUT_W}:{OUTPUT_H}",
            *video_codec_args(26, "ultrafast"),
            *COMMON_VIDEO_FLAGS,
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
                *video_codec_args(22, "fast"),
                *COMMON_VIDEO_FLAGS,
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
            *video_codec_args(22, "fast"),
            *COMMON_VIDEO_FLAGS,
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

    # 4. Beat-aligned kinetic captions for muted autoplay
    from content_engine.caption_engine import burn_captions
    with_caps = burn_captions(
        with_text, str(work_dir / "_perf_caps.mp4"),
        hook_text, audio_path, audio_start, target_duration,
        start_offset=0.3,
    )

    # 5. Color grade (final step — stamps faststart + I-frame at 0)
    _apply_color_grade(with_caps, output_path, platform)

    return output_path


def render_story_variant(
    source_clip: str,
    track_title: str,
    spotify_url: str,
    output_path: str,
) -> str:
    """Render a Stories variant with Spotify CTA overlay.

    Takes an already-rendered clip and adds "Listen on Spotify" + track title
    in the bottom 12% using the Pillow overlay approach. Final pass stamps
    faststart so IG/FB Stories autoplay without buffer-stall.
    """
    cta_text = f"Listen on Spotify: {track_title}"
    # Burn the overlay into an intermediate, then remux to apply faststart.
    tmp_overlay = str(Path(output_path).with_suffix(".overlay.mp4"))
    result = _burn_text_overlay(source_clip, tmp_overlay, cta_text, "story")
    if result == source_clip or not Path(tmp_overlay).exists():
        logger.warning("Story CTA overlay failed, copying source with faststart remux")
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", source_clip,
                    "-c", "copy", *FINAL_MUX_FLAGS, output_path,
                ],
                check=True, capture_output=True, timeout=60,
            )
        except Exception:
            import shutil
            shutil.copy2(source_clip, output_path)
        return output_path
    # Apply faststart on the overlay output (and scrub out the intermediate).
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", tmp_overlay,
                "-c", "copy", *FINAL_MUX_FLAGS, output_path,
            ],
            check=True, capture_output=True, timeout=60,
        )
    except Exception as e:
        logger.warning(f"Story faststart remux failed, using pre-remux file: {e}")
        import shutil
        shutil.move(tmp_overlay, output_path)
    else:
        try:
            os.remove(tmp_overlay)
        except OSError:
            pass
    return output_path
