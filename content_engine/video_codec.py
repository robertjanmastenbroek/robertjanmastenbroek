"""
video_codec.py — Hardware-first ffmpeg video codec args.

On Apple Silicon Macs, ``h264_videotoolbox`` offloads H.264 encoding to the
dedicated Media Engine ASIC. The CPU goes near-idle, the laptop stays cool,
and encoding runs 3-5× faster than libx264. We detect the encoder once at
import time; if it's missing (Linux CI, non-Apple hardware, weird ffmpeg
build) we fall back to ``libx264`` capped at 4 threads so a single render
can never burn every core.

Call sites keep passing the old libx264 knobs (``crf`` 20-30, ``preset``
``fast``/``ultrafast``) so the call sites read the same as before; the
helper maps those to the equivalent ``-q:v`` for VideoToolbox.
"""
from __future__ import annotations

import subprocess


def _detect_videotoolbox() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        return "h264_videotoolbox" in result.stdout
    except Exception:
        return False


USE_VIDEOTOOLBOX = _detect_videotoolbox()

# Software-fallback thread cap. Keeps libx264 from grabbing every core on
# machines that lack VideoToolbox (or any future build where we disable it).
SOFTWARE_THREAD_CAP = "4"


def video_codec_args(crf: int | str, preset: str = "fast") -> list[str]:
    """Return ffmpeg video codec args, hardware-accelerated when possible.

    crf: 20-30, libx264-style. Lower = higher quality. Translated to
        ``-q:v`` (0-100, higher = higher quality) for VideoToolbox via
        the inverse mapping ``q = 110 - 2*crf`` (clamped to 30-85).
    preset: libx264 preset. Ignored by VideoToolbox (hardware has no preset
        knob); retained so call sites don't need conditional logic.
    """
    crf_int = int(crf)
    if USE_VIDEOTOOLBOX:
        q = max(30, min(85, 110 - 2 * crf_int))
        return ["-c:v", "h264_videotoolbox", "-q:v", str(q)]
    return [
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf_int),
        "-threads", SOFTWARE_THREAD_CAP,
    ]
