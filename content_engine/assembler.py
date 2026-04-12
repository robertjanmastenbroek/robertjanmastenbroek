"""
Module 3: Assembler — visual-first pipeline.
Opening frame selected first (via visual_engine), audio matched to its energy,
rendered platform-specifically into 9 clips (3 clips × 3 platforms).

Key flip from legacy: opening frame is locked first, then audio is matched to it.
Text overlay appears at 0.5s (viewer sees visual first, text amplifies it).
"""
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from content_engine import visual_engine
from content_engine.types import TrendBrief, OpeningFrame, PromptWeights

PROJECT_DIR  = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR / "outreach_agent"))

logger = logging.getLogger(__name__)

CLIP_LENGTHS = [5, 9, 15]
PLATFORMS    = ["instagram", "youtube"]
ANGLES       = ["emotional", "signal", "energy"]

# Variant A/B per clip per platform — alternates across the week so each platform
# sees both variants. Balances A/B signal collection without double-posting.
VARIANT_MAP = {
    0: {"instagram": "a", "youtube": "b"},
    1: {"instagram": "b", "youtube": "a"},
    2: {"instagram": "a", "youtube": "b"},
}

# Platform-specific rendering settings
PLATFORM_SETTINGS = {
    "tiktok":    {"font_size": 72, "color_grade": "punchy",  "text_y_pct": 0.22, "safe_bottom_pct": 0.15},
    "instagram": {"font_size": 58, "color_grade": "warm",    "text_y_pct": 0.50, "safe_bottom_pct": 0.20},
    "youtube":   {"font_size": 64, "color_grade": "neutral", "text_y_pct": 0.22, "safe_bottom_pct": 0.15},
}

FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")


def _pick_audio():
    """Pick next track via existing rotation logic in post_today.py."""
    import post_today as pt
    audio_path, title = pt.pick_next_track()
    return str(audio_path), title


def _find_best_audio_segment(audio_path: str, clip_duration: int) -> float:
    """
    Find the audio start time whose energy arc best matches clip_duration seconds.
    Uses librosa onset strength envelope. Falls back to 30.0 on any error.
    """
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(audio_path, offset=30, duration=120, mono=True)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        hop_secs  = librosa.get_duration(y=y, sr=sr) / len(onset_env)
        window    = int(clip_duration / hop_secs)
        if window >= len(onset_env):
            return 30.0
        means = [onset_env[i:i + window].mean() for i in range(len(onset_env) - window)]
        best_frame = int(np.argmax(means))
        return 30.0 + best_frame * hop_secs
    except Exception as e:
        logger.warning(f"[assembler] librosa segment detection failed: {e} — using 30s")
        return 30.0


def _pick_supplementary_videos(opening_frame_path: str, n: int = 2) -> list:
    """Pick n supplementary videos for acts 2+3, excluding the opening frame."""
    try:
        import post_today as pt
        videos = pt.pick_source_videos(count=n + 1, exclude={opening_frame_path})
        return [(str(v), 0.0) for v in videos[:n]]
    except Exception:
        return []


def _apply_platform_color_grade(input_path: str, output_path: str, color_grade: str):
    """Apply ffmpeg color grading filter for platform-specific look."""
    filters = {
        "punchy":  "eq=contrast=1.15:saturation=1.2:brightness=0.02",
        "warm":    "eq=contrast=1.05:saturation=1.1:gamma_r=1.05:gamma_b=0.95",
        "neutral": "eq=contrast=1.0:saturation=1.0",
    }
    vf = filters.get(color_grade, filters["neutral"])
    cmd = [
        FFMPEG, "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "22", "-preset", "fast",
        "-c:a", "copy",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=120)


def build_clip(
    opening_frame: OpeningFrame,
    audio_path: str,
    clip_length: int,
    platform: str,
    hook_text: str,
    output_path: str,
) -> str:
    """
    Build one platform-specific clip.
    Structure: SHOCK(0-3s opening frame) → ESCALATION+PAYOFF(supplementary footage)
    Hook text overlay appears at 0.5s.
    Returns output_path.
    """
    from processor import format_to_vertical_multiclip

    # Stub path — no source footage or API key; write empty file for dry-run validation
    if not opening_frame.source_file:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).touch()
        logger.warning(f"[assembler] No source file for {platform} clip — writing stub: {output_path}")
        return output_path

    audio_start = _find_best_audio_segment(audio_path, clip_length)
    settings    = PLATFORM_SETTINGS[platform]

    # Build video source list: opening frame first, supplementary for remainder
    opening_duration = min(3.0, clip_length * 0.3)
    remainder        = clip_length - opening_duration
    supp             = _pick_supplementary_videos(opening_frame.source_file, n=2)

    video_sources = [(opening_frame.source_file, 0.0)]
    categories    = [_category_for_platform(opening_frame.visual_category)]

    if supp:
        video_sources += supp[:2]
        categories    += ["b-roll", "b-roll"]

    # Render base clip (multi-source, 9:16, with hook overlay at 0.5s)
    format_to_vertical_multiclip(
        video_sources=video_sources,
        output_path=output_path,
        clip_duration=float(clip_length),
        hook_text=hook_text,
        angle=ANGLES[opening_frame.clip_index % len(ANGLES)],
        source_categories=categories,
    )

    # Mix in RJM audio
    from post_today import mix_in_track
    mix_in_track(
        clip_path=Path(output_path),
        audio_path=Path(audio_path),
        audio_start=int(audio_start),
        clip_duration=clip_length,
    )

    # Apply platform color grade (in-place via temp file)
    tmp = output_path + ".grade.mp4"
    try:
        _apply_platform_color_grade(output_path, tmp, settings["color_grade"])
        os.replace(tmp, output_path)
    except Exception as e:
        logger.warning(f"[assembler] Color grade failed for {platform}: {e} — using ungraded")
        if os.path.exists(tmp):
            os.unlink(tmp)

    return output_path


def _category_for_platform(visual_category: str) -> str:
    mapping = {
        "performance": "performances",
        "b_roll":      "b-roll",
        "phone":       "phone-footage",
        "ai_generated":"b-roll",
    }
    return mapping.get(visual_category, "b-roll")


def _generate_hooks(track_title: str) -> dict:
    """Generate A/B/C hooks for all 3 clip lengths via existing generator."""
    import generator as gen
    clips_config = [
        {"length": 5,  "angle": "emotional"},
        {"length": 9,  "angle": "signal"},
        {"length": 15, "angle": "energy"},
    ]
    return gen.generate_run_hooks(track_title, clips_config)


def _generate_captions(track_title: str, hooks_by_length: dict, brief: TrendBrief) -> dict:
    """Generate platform captions for all 3 clips via existing generator."""
    import generator as gen
    def _hook_str(val) -> str:
        if isinstance(val, dict):
            return val.get("a", val.get("text", ""))
        return val or ""

    clips_data = [
        {"length": 5,  "angle": "emotional", "hook": _hook_str(hooks_by_length.get(5))},
        {"length": 9,  "angle": "signal",    "hook": _hook_str(hooks_by_length.get(9))},
        {"length": 15, "angle": "energy",    "hook": _hook_str(hooks_by_length.get(15))},
    ]
    return gen.generate_run_captions(track_title, clips_data)


def _extract_caption(captions_by_length: dict, clip_length: int, platform: str) -> str:
    """Extract platform caption string from nested captions dict."""
    clip_caps = captions_by_length.get(clip_length, {})
    platform_data = clip_caps.get(platform, {})
    if isinstance(platform_data, dict):
        return platform_data.get("caption", platform_data.get("title", ""))
    return str(platform_data)


def run_assembly(
    brief: TrendBrief,
    weights: PromptWeights,
    video_dirs: list,
    output_dir: str,
) -> list:
    """
    Full assembly run. Returns list of 9 dicts:
    {clip_index, platform, variant, path, hook_text, caption,
     hook_mechanism, visual_type, clip_length, track_title}
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    audio_path, track_title = _pick_audio()
    logger.info(f"[assembler] Track: {track_title} | {audio_path}")

    hooks_by_length    = _generate_hooks(track_title)
    captions_by_length = _generate_captions(track_title, hooks_by_length, brief)

    results = []
    ts = datetime.now().strftime("%H%M")

    for clip_idx in range(3):
        clip_length   = CLIP_LENGTHS[clip_idx]
        opening_frame = visual_engine.pick_opening_frame(brief, clip_idx, video_dirs)
        opening_frame.clip_index = clip_idx
        logger.info(f"[assembler] Clip {clip_idx} ({clip_length}s): "
                    f"{opening_frame.source} — {opening_frame.source_file}")

        _raw_hooks = hooks_by_length.get(clip_length, {})
        # generate_run_hooks returns plain strings; normalise to dict for variant lookup
        if isinstance(_raw_hooks, str):
            clip_hooks = {"a": _raw_hooks, "b": _raw_hooks}
        else:
            clip_hooks = _raw_hooks

        for platform in PLATFORMS:
            variant   = VARIANT_MAP[clip_idx][platform]
            hook_text = clip_hooks.get(variant, clip_hooks.get("a", ""))
            caption   = _extract_caption(captions_by_length, clip_length, platform)

            fname       = f"clip{clip_idx}_{platform}_{variant}_{ts}.mp4"
            output_path = str(Path(output_dir) / fname)

            logger.info(f"[assembler]   → {platform} variant={variant}")
            build_clip(
                opening_frame=opening_frame,
                audio_path=audio_path,
                clip_length=clip_length,
                platform=platform,
                hook_text=hook_text,
                output_path=output_path,
            )

            results.append({
                "clip_index":     clip_idx,
                "platform":       platform,
                "variant":        variant,
                "path":           output_path,
                "hook_text":      hook_text,
                "caption":        caption,
                "hook_mechanism": "tension",  # updated by learning loop after analysis
                "visual_type":    opening_frame.visual_category,
                "clip_length":    clip_length,
                "track_title":    track_title,
            })

    logger.info(f"[assembler] Assembly complete: {len(results)} clips")
    return results
