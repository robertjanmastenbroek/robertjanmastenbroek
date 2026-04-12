"""
Module 2b: Visual Engine
Selects or generates the opening frame for each clip.
Decision tree: footage_score >= SCORE_THRESHOLD → use footage; else → Runway ML Gen-4 Turbo.
"""
import logging
import os
import time
from pathlib import Path

import requests

from content_engine import footage_scorer
from content_engine.footage_scorer import SCORE_THRESHOLD
from content_engine.types import TrendBrief, OpeningFrame

logger = logging.getLogger(__name__)

RUNWAY_API_BASE = "https://api.runwayml.com/v1"
RUNWAY_VERSION  = "2024-11-06"
PROJECT_DIR     = Path(__file__).parent.parent

# Brand formula baked into every AI generation prompt
BRAND_FORMULA = (
    "ancient visual language fused with electronic energy. "
    "Sacred geometry, tribal ritual, dark atmospheric lighting. "
    "High contrast. Cinematic. No text overlays. Vertical 9:16 framing."
)

# Per-clip visual concepts — rotate by clip_index
CLIP_CONCEPTS = [
    "crowd of dancers in ecstatic movement, aerial perspective, bass-wave ripple through bodies",
    "sacred geometry patterns dissolving into a rave floor, particle light trails, slow motion",
    "aerial coastal cliffs at golden hour, synthesizer light trails rising from the ocean, fog",
]


def build_prompt(brief: TrendBrief, clip_index: int) -> str:
    """Build a Runway text-to-video prompt from today's brief."""
    concept       = CLIP_CONCEPTS[clip_index % len(CLIP_CONCEPTS)]
    visual_format = brief.top_visual_formats[clip_index % len(brief.top_visual_formats)]
    return (
        f"{brief.dominant_emotion} atmosphere. "
        f"{visual_format}. "
        f"{concept}. "
        f"{BRAND_FORMULA} "
        f"Avoid: {brief.oversaturated}."
    )


def generate_clip(prompt_text: str, date_str: str, clip_index: int) -> str:
    """
    Submit text-to-video job to Runway ML Gen-4 Turbo, poll until complete, download.
    Returns local file path.
    Raises RuntimeError if RUNWAY_API_KEY not set or generation fails after 5 min.
    """
    api_key = os.environ.get("RUNWAY_API_KEY", "")
    if not api_key:
        raise RuntimeError("RUNWAY_API_KEY not set — cannot generate AI clip")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Runway-Version": RUNWAY_VERSION,
        "Content-Type": "application/json",
    }

    resp = requests.post(f"{RUNWAY_API_BASE}/text_to_video", headers=headers, json={
        "model": "gen4_turbo",
        "promptText": prompt_text,
        "ratio": "768:1344",   # 9:16 vertical
        "duration": 5,
    }, timeout=30)

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Runway submission failed {resp.status_code}: {resp.text[:300]}")

    task_id = resp.json()["id"]
    logger.info(f"[visual_engine] Runway task {task_id} submitted")

    # Poll — max 5 minutes (60 × 5s)
    for attempt in range(60):
        time.sleep(5)
        poll = requests.get(f"{RUNWAY_API_BASE}/tasks/{task_id}", headers=headers, timeout=15)
        status = poll.json().get("status", "")
        if status == "SUCCEEDED":
            video_url = poll.json()["output"][0]
            break
        if status in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Runway task {task_id} {status}: {poll.json()}")
        logger.debug(f"[visual_engine] {task_id}: {status} (attempt {attempt + 1})")
    else:
        raise RuntimeError(f"Runway task {task_id} timed out after 5 minutes")

    # Download
    out_dir = PROJECT_DIR / "data" / "opening_frames" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / f"ai_clip_{clip_index}.mp4")

    video_resp = requests.get(video_url, stream=True, timeout=120)
    with open(out_path, "wb") as f:
        for chunk in video_resp.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info(f"[visual_engine] AI clip saved: {out_path}")
    return out_path


def _infer_category(path: str) -> str:
    p = path.lower()
    if "perf" in p:  return "performance"
    if "b-roll" in p or "broll" in p: return "b_roll"
    if "phone" in p: return "phone"
    return "b_roll"


def pick_opening_frame(brief: TrendBrief, clip_index: int, video_dirs: list) -> OpeningFrame:
    """
    Decide: use existing footage (score >= threshold) OR generate with Runway.
    Returns OpeningFrame. Never fails silently — raises if both paths fail.
    """
    candidates = footage_scorer.build_candidate_list(video_dirs)
    best_path, best_score = footage_scorer.pick_best_opening_frame(candidates, brief.dominant_emotion)

    logger.info(f"[visual_engine] clip {clip_index}: footage score={best_score:.2f} "
                f"(threshold={SCORE_THRESHOLD}) — {'USE FOOTAGE' if best_score >= SCORE_THRESHOLD and best_path else 'GENERATE AI'}")

    if best_score >= SCORE_THRESHOLD and best_path:
        return OpeningFrame(
            clip_index=clip_index,
            source="footage",
            source_file=best_path,
            emotion_tag=brief.dominant_emotion,
            visual_category=_infer_category(best_path),
            footage_score=best_score,
        )

    # Generate with Runway
    prompt = build_prompt(brief, clip_index)
    generated_path = generate_clip(prompt, brief.date, clip_index)
    return OpeningFrame(
        clip_index=clip_index,
        source="ai_generated",
        source_file=generated_path,
        emotion_tag=brief.dominant_emotion,
        visual_category="ai_generated",
        footage_score=0.0,
    )
