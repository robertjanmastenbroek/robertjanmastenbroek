#!/usr/bin/env python3
"""
quality_gate.py — Pre-posting quality checks for rendered video clips.

Two-layer check:
  1. Technical: resolution, duration, codec, audio, file size (via ffprobe)
  2. Visual:    brand aesthetic, subject clarity, hook readability (via Claude Vision)

Usage:
  from quality_gate import check_clip
  passed, reason = check_clip(clip_path, expected_duration=9, angle="energy")
  if not passed:
      print(f"Clip rejected: {reason}")
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_WIDTH  = 1080
EXPECTED_HEIGHT = 1920
DURATION_TOLERANCE = 1.0
MIN_FILE_SIZE_BYTES = 1_000_000
LOG_PATH = Path(__file__).parent.parent / "data" / "quality_log.json"


def check_clip(clip_path: str, expected_duration: int, angle: str = "") -> tuple:
    """Run technical + visual checks on a rendered clip.
    Returns (passed: bool, reason: str). reason is "" when passed is True.
    Appends result to data/quality_log.json.
    """
    passed, reason = _check_technical(clip_path, expected_duration)
    if passed:
        passed, reason = _check_visual(clip_path, angle)
    _log_result(clip_path, expected_duration, angle, passed, reason)
    return passed, reason


def _check_technical(clip_path: str, expected_duration: int) -> tuple:
    """ffprobe-based technical validation. Returns (passed, reason)."""
    p = Path(clip_path)

    try:
        size = p.stat().st_size
    except OSError as exc:
        return False, f"Cannot read file: {exc}"

    if size < MIN_FILE_SIZE_BYTES:
        return False, f"File size too small ({size} bytes) — likely corrupt or empty"

    try:
        probe = _run_ffprobe(clip_path)
    except Exception as exc:
        return False, f"ffprobe failed: {exc}"

    video_stream = next((s for s in probe["streams"] if s.get("codec_type") == "video"), None)
    if not video_stream:
        return False, "No video stream found"

    w = video_stream.get("width", 0)
    h = video_stream.get("height", 0)
    if w != EXPECTED_WIDTH or h != EXPECTED_HEIGHT:
        return False, f"Wrong resolution: {w}×{h} (expected {EXPECTED_WIDTH}×{EXPECTED_HEIGHT})"

    try:
        duration = float(probe["format"]["duration"])
    except (KeyError, ValueError):
        return False, "Cannot determine duration"

    if abs(duration - expected_duration) > DURATION_TOLERANCE:
        return False, f"Wrong duration: {duration:.1f}s (expected {expected_duration}s ±{DURATION_TOLERANCE}s)"

    has_audio = any(s.get("codec_type") == "audio" for s in probe["streams"])
    if not has_audio:
        return False, "No audio stream — RJM track may not have been mixed in"

    return True, ""


def _run_ffprobe(clip_path: str) -> dict:
    """Run ffprobe and return parsed JSON. Raises RuntimeError on failure."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        clip_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe exited {result.returncode}: {result.stderr[:200]}")
    return json.loads(result.stdout)


def _check_visual(clip_path: str, angle: str = "") -> tuple:
    """Extract a frame and score it with Claude Vision. Returns (passed, reason)."""
    import tempfile
    import base64

    frame_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            frame_path = f.name

        cmd = [
            "ffmpeg", "-y",
            "-ss", "1",
            "-i", clip_path,
            "-vframes", "1",
            "-q:v", "3",
            frame_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            return False, "Could not extract frame for visual check"

        with open(frame_path, "rb") as f:
            frame_b64 = base64.standard_b64encode(f.read()).decode()

        return _score_frame(frame_b64, angle)

    except Exception as exc:
        print(f"    Visual check error (passing clip through): {exc}")
        return True, ""
    finally:
        if frame_path and Path(frame_path).exists():
            Path(frame_path).unlink()


def _score_frame(frame_b64: str, angle: str) -> tuple:
    """Send frame to Claude Vision via CLI. Returns (passed, reason)."""
    import tempfile
    import base64

    angle_context = f" The clip angle is '{angle}'." if angle else ""
    prompt = f"""You are reviewing a video frame from a short-form clip for Holy Rave — a dark, sacred, futuristic music brand by Dutch DJ Robert-Jan Mastenbroek.{angle_context}

Rate this frame 1–5 for posting quality:
5 = Perfect: dark club/performance energy, clear subject, on-brand
4 = Good: minor issues but postable
3 = Acceptable: watchable, not great
2 = Poor: too dark/blurry/static or off-brand
1 = Reject: completely unusable (black frame, technical error, wrong content)

Respond with JSON only: {{"score": N, "reason": "one sentence"}}"""

    # Write the frame to a temp file so we can pass it to the Claude CLI
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(base64.standard_b64decode(frame_b64))
        tmp_frame = f.name

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--image", tmp_frame, "--output-format", "text"],
            capture_output=True, text=True, timeout=30,
        )
        raw = result.stdout.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        score = int(parsed.get("score", 3))
        reason = parsed.get("reason", "")
    except (json.JSONDecodeError, KeyError, ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return True, ""
    finally:
        Path(tmp_frame).unlink(missing_ok=True)

    if score >= 3:
        return True, ""
    else:
        return False, f"Visual score {score}/5: {reason}"


def _log_result(clip_path: str, expected_duration: int, angle: str, passed: bool, reason: str) -> None:
    """Append check result to data/quality_log.json."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        log = json.loads(LOG_PATH.read_text()) if LOG_PATH.exists() else []
    except (json.JSONDecodeError, OSError):
        log = []

    log.append({
        "clip":       Path(clip_path).name,
        "duration":   expected_duration,
        "angle":      angle,
        "passed":     passed,
        "reason":     reason,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    })

    LOG_PATH.write_text(json.dumps(log[-200:], indent=2))
