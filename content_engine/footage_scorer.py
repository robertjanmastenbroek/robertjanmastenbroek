"""
Module 2a: Footage Scorer
Semantic scoring of existing video library against today's TrendBrief.
Replaces simple timestamp rotation with composite score: motion + contrast + freshness + emotion match.
Returns best opening frame candidate, or signals AI generation is needed (score < SCORE_THRESHOLD).
"""
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

SCORE_THRESHOLD = 7.0   # Below this → trigger AI generation in visual_engine

PROJECT_DIR        = Path(__file__).parent.parent
VIDEO_ROTATION_FILE = PROJECT_DIR / "video_rotation.json"

# Composite score weights (sum = 1.0)
W_MOTION    = 0.30
W_CONTRAST  = 0.25
W_FRESHNESS = 0.25
W_EMOTION   = 0.20


def _get_motion_score(video_path: str) -> float:
    """Score 0-10: movement velocity in first 3 seconds via ffprobe frame types."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "frame=pict_type",
            "-read_intervals", "%+3",
            "-of", "json",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return 5.0
        data = json.loads(result.stdout)
        frames = data.get("frames", [])
        if not frames:
            return 5.0
        # P/B frames indicate motion; I-only = static
        motion_frames = sum(1 for f in frames if f.get("pict_type") in ("P", "B"))
        ratio = motion_frames / len(frames)
        return min(10.0, ratio * 12)
    except Exception:
        return 5.0


def _get_contrast_score(video_path: str) -> float:
    """Score 0-10: visual contrast of first frame (stddev of luma channel)."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        subprocess.run([
            "ffmpeg", "-y", "-ss", "0", "-i", video_path,
            "-vframes", "1", "-q:v", "2", tmp,
        ], capture_output=True, timeout=15)
        from PIL import Image, ImageStat
        img = Image.open(tmp).convert("L")
        stat = ImageStat.Stat(img)
        stddev = stat.stddev[0]  # 0-127
        os.unlink(tmp)
        return min(10.0, (stddev / 60) * 10)
    except Exception:
        return 5.0


def _get_freshness_score(last_used_days: float) -> float:
    """Score 0-10: higher = not used recently. ≥14 days = full score."""
    if last_used_days >= 14:
        return 10.0
    return (last_used_days / 14) * 10.0


def _get_emotion_match(video_path: str, dominant_emotion: str) -> float:
    """Score 0-10: Claude CLI scoring of visual match to today's dominant emotion."""
    try:
        claude = os.environ.get("CLAUDE_CLI_PATH", "") or "claude"
        fname = Path(video_path).name
        prompt = (
            f"Video filename: {fname}\n"
            f"Target emotion: {dominant_emotion}\n"
            "Rate 0-10 how likely this video's visual content (judged by filename/path cues) "
            "evokes the target emotion. Higher = better match. Reply with ONLY a single number."
        )
        result = subprocess.run(
            [claude, "--print", "--model", "claude-haiku-4-5-20251001", prompt],
            capture_output=True, text=True, timeout=30,
        )
        text = result.stdout.strip()
        # Extract first number found
        import re
        m = re.search(r"\d+(?:\.\d+)?", text)
        return min(10.0, float(m.group())) if m else 5.0
    except Exception:
        return 5.0


def score_clip(video_path: str, dominant_emotion: str, last_used_days: float = 7.0) -> float:
    """Composite score 0-10 for a video clip as opening frame candidate."""
    motion    = _get_motion_score(video_path)
    contrast  = _get_contrast_score(video_path)
    freshness = _get_freshness_score(last_used_days)
    emotion   = _get_emotion_match(video_path, dominant_emotion)

    score = (
        motion    * W_MOTION +
        contrast  * W_CONTRAST +
        freshness * W_FRESHNESS +
        emotion   * W_EMOTION
    )
    logger.debug(f"  {Path(video_path).name}: motion={motion:.1f} contrast={contrast:.1f} "
                 f"fresh={freshness:.1f} emotion={emotion:.1f} → {score:.2f}")
    return score


def _load_rotation() -> dict:
    if VIDEO_ROTATION_FILE.exists():
        return json.loads(VIDEO_ROTATION_FILE.read_text())
    return {}


def build_candidate_list(video_dirs: list) -> list:
    """Scan video dirs, return list of {path, last_used_days, category}."""
    from datetime import datetime
    rotation = _load_rotation()
    candidates = []
    exts = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}

    for vdir in video_dirs:
        p = Path(vdir)
        if not p.exists():
            continue
        category = p.name  # 'performances', 'b-roll', 'phone-footage'
        for f in p.rglob("*"):
            if f.suffix.lower() in exts:
                last_used_ts = rotation.get(str(f), 0)
                days = (datetime.now().timestamp() - last_used_ts) / 86400 if last_used_ts else 30.0
                candidates.append({
                    "path": str(f),
                    "last_used_days": days,
                    "category": category,
                })

    return candidates


def pick_best_opening_frame(candidates: list, dominant_emotion: str, exclude: set = None) -> tuple:
    """Score all candidates, return (best_path, best_score). Skips paths in exclude."""
    if not candidates:
        return ("", 0.0)

    exclude = exclude or set()
    best_path  = ""
    best_score = -1.0

    for c in candidates:
        if c["path"] in exclude:
            continue
        s = score_clip(c["path"], dominant_emotion, last_used_days=float(c.get("last_used_days", 7)))
        if s > best_score:
            best_score = s
            best_path  = c["path"]

    return (best_path, best_score)
