"""
caption_engine.py — Beat-aligned auto-caption burn-in for muted autoplay.

85% of IG Reels and 40% of TikTok views start muted. If the hook text is the
only on-screen content, we lose comprehension instantly. This module generates
word-by-word kinetic captions from the spoken hook (or the hook template text)
and burns them as an ASS subtitle track — displayed in short bursts timed to
beat divisions of the track so they pop ON the drum hit.

Design choices:
  * ASS format (not SRT) so we get per-word styling: big Bebas Neue, white
    with 8px black stroke + subtle drop shadow, uppercase. Same visual DNA
    as the hook overlay.
  * Captions position ABOVE the hook overlay block (y=0.35 vs 0.68) so they
    don't collide on transitional / emotional clips.
  * Burn-in via ffmpeg `subtitles=` filter — no second text-overlay pass.
  * Falls back to a no-op (return input) on any failure — captions are
    an enhancement, never a blocker.
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple

from content_engine.video_codec import video_codec_args

logger = logging.getLogger(__name__)

# Pillow-equivalent visual tokens, translated to ASS tag values.
# ASS uses &HBBGGRR& color order (not RGB) and alpha-inverted (00=opaque).
CAPTION_FONT = "Bebas Neue"
CAPTION_FONT_FALLBACK = "Impact"
CAPTION_FONT_SIZE = 80        # ~80pt at 1080p reads ~56pt perceived
CAPTION_OUTLINE = 4           # px black stroke
CAPTION_SHADOW = 2            # px drop shadow
CAPTION_POS_Y = 500           # from top (of 1920); gives ~26% from top
CAPTION_MIN_WORD_DURATION = 0.25  # seconds — readable floor per word
CAPTION_MAX_WORD_DURATION = 0.9   # seconds — any longer feels lazy


def _hr_time(seconds: float) -> str:
    """ASS timestamp format: H:MM:SS.cc (centiseconds)."""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _split_to_word_groups(text: str, max_per_group: int = 3) -> List[str]:
    """Split hook text into 1-3 word groups for kinetic pacing.

    Short groups pop harder on the beat than full lines. 3 is the upper
    bound — 4+ words stops being kinetic and becomes a subtitle.
    """
    words = text.strip().split()
    if not words:
        return []
    groups: List[str] = []
    i = 0
    while i < len(words):
        # Variable group size 1-3 biased toward 2 (most rhythmic)
        remaining = len(words) - i
        size = min(remaining, 2 if remaining >= 2 else remaining)
        # If the 2-word group would end with a stop-word, try 3
        if size == 2 and i + 2 < len(words) and len(words[i + 1]) <= 3:
            size = min(3, remaining)
        groups.append(" ".join(words[i:i + size]).upper())
        i += size
    return groups


def _beat_times(audio_path: str, audio_start: float, duration: float) -> List[float]:
    """Return beat timestamps (relative to clip start) falling inside [0, duration]."""
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
        # Shift back into clip time
        shifted = [float(bt) - 0.5 for bt in beat_times]
        return [t for t in shifted if 0.0 <= t <= duration]
    except Exception as e:
        logger.warning(f"Beat detection failed in caption engine: {e}")
        return []


def _generate_ass(
    groups: List[str],
    beats: List[float],
    total_duration: float,
    start_offset: float = 0.3,
) -> str:
    """Build an ASS file where each group is shown from beat[k] to beat[k+N].

    If we have fewer beats than groups, fall back to even pacing.
    """
    # Header — define one style, bold+uppercase+thick outline for legibility on
    # any background. Alignment=8 → top-center. MarginV is the distance in px
    # from the TOP of the frame when Alignment is top-*.
    header = (
        "[Script Info]\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: RJM,{CAPTION_FONT},{CAPTION_FONT_SIZE},"
        "&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,2,0,1,"
        f"{CAPTION_OUTLINE},{CAPTION_SHADOW},"
        f"8,40,40,{CAPTION_POS_Y},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    events: List[str] = []
    n = len(groups)
    if n == 0:
        return header  # No events; the filter is a no-op

    # Compute per-group (start, end) windows
    windows: List[Tuple[float, float]] = []
    usable_beats = [b for b in beats if b >= start_offset]
    if len(usable_beats) >= n + 1:
        # Use every other beat (half-note pacing → feels deliberate not frantic)
        step = max(1, len(usable_beats) // (n + 1))
        picked = usable_beats[::step][: n + 1]
        if len(picked) < n + 1:
            picked = usable_beats[: n + 1]
        for i in range(n):
            s = picked[i]
            e = picked[i + 1] if i + 1 < len(picked) else total_duration
            # Clamp to sane word-duration bounds
            dur = max(CAPTION_MIN_WORD_DURATION, min(e - s, CAPTION_MAX_WORD_DURATION * 3))
            e = min(total_duration - 0.1, s + dur)
            windows.append((s, e))
    else:
        # Even pacing fallback — evenly divide the [start_offset, total-0.3] window
        avail = max(0.1, total_duration - start_offset - 0.3)
        per = max(CAPTION_MIN_WORD_DURATION, min(CAPTION_MAX_WORD_DURATION, avail / n))
        for i in range(n):
            s = start_offset + i * per
            e = min(total_duration - 0.1, s + per)
            windows.append((s, e))

    for (s, e), text in zip(windows, groups):
        # Pop-in animation: scale from 85% → 100% over first 120ms via \t tag
        # ASS `\fscx` controls horizontal scale; mirror for vertical.
        effect = (
            "{\\fad(60,80)\\fscx85\\fscy85"
            "\\t(0,120,\\fscx100\\fscy100)"
            "}"
        )
        # Escape commas or curly braces in text
        safe = text.replace("{", "\\{").replace("}", "\\}")
        events.append(
            f"Dialogue: 0,{_hr_time(s)},{_hr_time(e)},RJM,,0,0,0,,{effect}{safe}"
        )

    return header + "\n".join(events) + "\n"


def burn_captions(
    input_path: str,
    output_path: str,
    hook_text: str,
    audio_path: str,
    audio_start: float,
    total_duration: float,
    start_offset: float = 0.3,
) -> str:
    """Burn beat-aligned captions into `input_path`, writing to `output_path`.

    Returns `output_path` on success, `input_path` on any failure so callers
    can chain: ``next = burn_captions(next, ...)``.
    """
    groups = _split_to_word_groups(hook_text)
    if not groups:
        return input_path

    beats = _beat_times(audio_path, audio_start, total_duration)
    ass_text = _generate_ass(groups, beats, total_duration, start_offset)

    ass_path = None
    try:
        # Write ASS to a temp file; ffmpeg subtitles filter reads from disk.
        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False, mode="w", encoding="utf-8") as tmp:
            tmp.write(ass_text)
            ass_path = tmp.name

        # ffmpeg subtitles filter requires escaping `:` and `'` in the path.
        # Safest approach: cd into the directory and pass the basename.
        ass_dir = os.path.dirname(ass_path)
        ass_base = os.path.basename(ass_path).replace(":", r"\:")

        # force_style lets us override font if the system doesn't have Bebas Neue
        # (the ASS style is only used if the font is found; otherwise libass
        # falls back to a default — force_style makes the fallback explicit).
        filter_expr = (
            f"subtitles=filename='{ass_base}'"
            f":force_style='FontName={CAPTION_FONT_FALLBACK}"
            f",Fontsize={CAPTION_FONT_SIZE}"
            f",PrimaryColour=&H00FFFFFF"
            f",OutlineColour=&H00000000"
            f",BorderStyle=1,Outline={CAPTION_OUTLINE},Shadow={CAPTION_SHADOW}'"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", filter_expr,
            *video_codec_args(22, "fast"),
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=180, cwd=ass_dir)
        return output_path

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode()[:300] if e.stderr else ""
        logger.error(f"Caption burn failed: {err}")
        return input_path
    except Exception as e:
        logger.error(f"Caption burn error: {e}")
        return input_path
    finally:
        if ass_path:
            try:
                os.unlink(ass_path)
            except Exception:
                pass
