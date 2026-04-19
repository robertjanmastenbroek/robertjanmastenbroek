"""
caption_engine.py — Beat-aligned auto-caption burn-in for muted autoplay.

85% of IG Reels and 40% of TikTok views start muted. This module generates
word-by-word kinetic captions timed to beat divisions of the track.

Implemented with PyAV (libav bindings) + Pillow — no ffmpeg subprocess.
Falls back to a no-op (returns input path) on any failure.
"""

import logging
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

CAPTION_FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/Library/Fonts/Impact.ttf",
    "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
CAPTION_FONT_SIZE = 80
CAPTION_OUTLINE  = 4
CAPTION_SHADOW   = 3
CAPTION_POS_Y    = 500   # pixels from top of 1920px frame (~26%)
# Kinetic pacing targets (2026 viral benchmark: 180-250ms/word).
# MIN=0.18 lets tight beats snap to fast pacing; MAX=0.30 prevents dead
# air when beats are sparse. At 128 BPM (~234ms/beat) this gives a
# one-beat buffer for readability before the next word hits.
CAPTION_MIN_WORD_DURATION = 0.18
CAPTION_MAX_WORD_DURATION = 0.30

# Per-word punch-in animation: oversized onset that settles into place.
# Reads as intentional motion rather than a hard cut.
PUNCH_DURATION = 0.10      # seconds to settle from START → END scale
PUNCH_SCALE_START = 1.12
PUNCH_SCALE_END = 1.0


def _load_font(size: int):
    from PIL import ImageFont
    for path in CAPTION_FONT_PATHS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _split_to_word_groups(text: str) -> List[str]:
    """Split hook text into 1-3 word groups for kinetic pacing."""
    words = text.strip().split()
    if not words:
        return []
    groups: List[str] = []
    i = 0
    while i < len(words):
        remaining = len(words) - i
        size = min(remaining, 2 if remaining >= 2 else remaining)
        if size == 2 and i + 2 < len(words) and len(words[i + 1]) <= 3:
            size = min(3, remaining)
        groups.append(" ".join(words[i:i + size]).upper())
        i += size
    return groups


def _beat_times(audio_path: str, audio_start: float, duration: float) -> List[float]:
    """Return beat timestamps (relative to clip start) inside [0, duration]."""
    try:
        import librosa
        y, sr = librosa.load(
            audio_path,
            sr=22050,
            offset=max(0.0, audio_start - 0.5),
            duration=duration + 1.0,
        )
        _, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        shifted = [float(bt) - 0.5 for bt in beat_times]
        return [t for t in shifted if 0.0 <= t <= duration]
    except Exception as e:
        logger.warning(f"Beat detection failed in caption engine: {e}")
        return []


def _compute_windows(
    groups: List[str],
    beats: List[float],
    total_duration: float,
    start_offset: float,
) -> List[Tuple[float, float]]:
    """Compute (start, end) time window for each caption group."""
    n = len(groups)
    windows: List[Tuple[float, float]] = []
    usable_beats = [b for b in beats if b >= start_offset]

    if len(usable_beats) >= n + 1:
        step = max(1, len(usable_beats) // (n + 1))
        picked = usable_beats[::step][: n + 1]
        if len(picked) < n + 1:
            picked = usable_beats[: n + 1]
        for i in range(n):
            s = picked[i]
            e = picked[i + 1] if i + 1 < len(picked) else total_duration
            dur = max(CAPTION_MIN_WORD_DURATION, min(e - s, CAPTION_MAX_WORD_DURATION * 3))
            e = min(total_duration - 0.1, s + dur)
            windows.append((s, e))
    else:
        avail = max(0.1, total_duration - start_offset - 0.3)
        per = max(CAPTION_MIN_WORD_DURATION, min(CAPTION_MAX_WORD_DURATION, avail / n))
        for i in range(n):
            s = start_offset + i * per
            e = min(total_duration - 0.1, s + per)
            windows.append((s, e))

    return windows


def _punch_in_scale(t_in_window: float) -> float:
    """Linear scale interpolation 1.12 → 1.0 across PUNCH_DURATION, then flat."""
    if t_in_window >= PUNCH_DURATION:
        return PUNCH_SCALE_END
    if t_in_window <= 0:
        return PUNCH_SCALE_START
    progress = t_in_window / PUNCH_DURATION
    return PUNCH_SCALE_START + (PUNCH_SCALE_END - PUNCH_SCALE_START) * progress


def _punch_in_alpha(t_in_window: float) -> int:
    """Linear alpha ramp 0 → 255 across PUNCH_DURATION, then flat at 255."""
    if t_in_window >= PUNCH_DURATION:
        return 255
    if t_in_window <= 0:
        return 0
    return int(255 * (t_in_window / PUNCH_DURATION))


def _draw_text_centered(
    draw, text: str, font, frame_width: int, y: int,
    scale: float = 1.0, alpha: int = 255,
) -> None:
    """Draw white text with black outline centered horizontally at y.

    Supports per-word punch-in via `scale` (1.0-1.12) and `alpha` (0-255).
    Fast path at scale=1.0, alpha=255 — no temp layer, draws directly.
    """
    if scale == 1.0 and alpha == 255:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        x = (frame_width - text_w) // 2
        draw.text(
            (x, y), text, font=font,
            fill=(255, 255, 255),
            stroke_width=CAPTION_OUTLINE,
            stroke_fill=(0, 0, 0),
        )
        return

    # Scaled / fading path: render into a temp RGBA layer and paste so we
    # can apply alpha uniformly and resize without deforming the stroke.
    from PIL import Image as _Image, ImageDraw as _ID
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad = CAPTION_OUTLINE * 2 + 4
    layer_w = text_w + pad * 2
    layer_h = text_h + pad * 2
    layer = _Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
    ld = _ID.Draw(layer)
    ld.text(
        (pad, pad), text, font=font,
        fill=(255, 255, 255, alpha),
        stroke_width=CAPTION_OUTLINE,
        stroke_fill=(0, 0, 0, alpha),
    )
    if scale != 1.0:
        new_w = max(1, int(layer_w * scale))
        new_h = max(1, int(layer_h * scale))
        layer = layer.resize((new_w, new_h), _Image.LANCZOS)
        layer_w, layer_h = new_w, new_h

    x = (frame_width - layer_w) // 2
    target_y = y - (layer_h - text_h) // 2
    draw._image.paste(layer, (x, target_y), layer)


def _get_caption_at(t: float, sorted_windows: list):
    """Return (text, window_start) for timestamp t, or (None, None) if not in any window."""
    for (s, e), text in sorted_windows:
        if s <= t < e:
            return text, s
        if s > t:
            break
    return None, None


def _encode_video_with_captions(
    input_path: str,
    video_only_path: str,
    sorted_windows: list,
) -> None:
    """Encode video frames with caption overlays to a video-only file.

    Separated from audio muxing so the MP4 muxer never has to interleave
    re-encoded video DTS with copied audio DTS — which causes AVERROR_EINVAL
    when PIL processing delays push audio far ahead of video.
    """
    import av
    from PIL import ImageDraw

    font = _load_font(CAPTION_FONT_SIZE)

    in_container = av.open(input_path)
    v_in      = in_container.streams.video[0]
    time_base = v_in.time_base
    fps       = v_in.average_rate

    out_container = av.open(video_only_path, "w")
    v_out = out_container.add_stream("libx264", rate=fps)
    v_out.width   = v_in.width
    v_out.height  = v_in.height
    v_out.pix_fmt = "yuv420p"
    v_out.options = {"crf": "22", "preset": "fast"}

    frame_num = 0
    for packet in in_container.demux(v_in):
        if packet.pts is None:
            continue
        for frame in packet.decode():
            if frame.pts is None:
                continue
            t = float(frame.pts * time_base)
            caption, window_start = _get_caption_at(t, sorted_windows)
            if caption:
                t_in = t - window_start
                scale = _punch_in_scale(t_in)
                alpha = _punch_in_alpha(t_in)
                img  = frame.to_image()
                draw = ImageDraw.Draw(img)
                _draw_text_centered(
                    draw, caption, font, v_in.width, CAPTION_POS_Y,
                    scale=scale, alpha=alpha,
                )
                new_frame = av.VideoFrame.from_image(img).reformat(format="yuv420p")
            else:
                # Fresh frame strips pict_type (I/P/B) that corrupts libx264 DTS
                # tracking when mixed with PIL-derived frames (pict_type=NONE).
                new_frame = av.VideoFrame.from_ndarray(
                    frame.to_ndarray(format="yuv420p"), format="yuv420p"
                )
            # Monotonic counter in encoder's time_base (1/fps) — avoids the
            # 512x scaling mismatch that occurs when input pts (1/15360 units)
            # are passed directly to a stream configured at 1/fps.
            new_frame.pts = frame_num
            frame_num += 1
            for pkt in v_out.encode(new_frame):
                out_container.mux(pkt)

    for pkt in v_out.encode(None):
        out_container.mux(pkt)

    out_container.close()
    in_container.close()


def _mux_with_pyav(video_only_path: str, original_path: str, output_path: str) -> None:
    """Mux captioned video-only file with original audio — pure PyAV packet copy, no subprocess.

    Both streams are copied without re-encoding. Packets are interleaved by DTS
    so the MP4 muxer doesn't reject out-of-order timestamps.
    """
    import av

    v_in = av.open(video_only_path)
    a_in = av.open(original_path)
    out  = av.open(output_path, "w")

    in_v = v_in.streams.video[0]
    in_a = a_in.streams.audio[0]

    # PyAV 17 uses add_stream_from_template (add_stream(template=) was removed)
    out_v = out.add_stream_from_template(in_v)
    out_a = out.add_stream_from_template(in_a)

    # Buffer all packets — interleave by DTS so the MP4 muxer stays happy
    v_pkts = []
    for pkt in v_in.demux(in_v):
        if pkt.dts is not None:
            pkt.stream = out_v
            v_pkts.append((float(pkt.dts * in_v.time_base), pkt))

    a_pkts = []
    for pkt in a_in.demux(in_a):
        if pkt.dts is not None:
            pkt.stream = out_a
            a_pkts.append((float(pkt.dts * in_a.time_base), pkt))

    vi = ai = 0
    while vi < len(v_pkts) or ai < len(a_pkts):
        if vi >= len(v_pkts):
            out.mux(a_pkts[ai][1]); ai += 1
        elif ai >= len(a_pkts):
            out.mux(v_pkts[vi][1]); vi += 1
        elif v_pkts[vi][0] <= a_pkts[ai][0]:
            out.mux(v_pkts[vi][1]); vi += 1
        else:
            out.mux(a_pkts[ai][1]); ai += 1

    out.close()
    v_in.close()
    a_in.close()


def _burn_with_pillow(
    input_path: str,
    output_path: str,
    sorted_windows: list,
) -> str:
    """Overlay captions on video frames using PyAV + Pillow, write to output_path.

    Two-pass approach:
    1. Encode video-with-captions to a temp file (PyAV/libx264, no audio).
    2. Mux captioned video + original audio via PyAV packet copy (no subprocess).
    """
    import tempfile
    import os

    tmp_video = tempfile.mktemp(suffix="_vid_only.mp4")
    try:
        _encode_video_with_captions(input_path, tmp_video, sorted_windows)
        _mux_with_pyav(tmp_video, input_path, output_path)
    finally:
        try:
            os.unlink(tmp_video)
        except OSError:
            pass
    return output_path


def burn_captions(
    input_path: str,
    output_path: str,
    hook_text: str,
    audio_path: str,
    audio_start: float,
    total_duration: float,
    start_offset: float = 0.3,
) -> str:
    """Burn beat-aligned captions into input_path, writing to output_path.

    Returns output_path on success, input_path on any failure.
    """
    groups = _split_to_word_groups(hook_text)
    if not groups:
        return input_path

    beats = _beat_times(audio_path, audio_start, total_duration)
    windows = _compute_windows(groups, beats, total_duration, start_offset)
    if not windows:
        return input_path

    sorted_windows = sorted(zip(windows, groups), key=lambda x: x[0][0])

    try:
        return _burn_with_pillow(input_path, output_path, sorted_windows)
    except Exception as e:
        logger.error(f"Caption burn error: {e}")
        return input_path
