"""
Top-level orchestrator for the Viral Shorts Mindhive pipeline.
Called by: rjm.py content viral, cron at 08:00 CET.

Daily flow:
1. Load today's trend brief (generated at 06:00 by trend_scanner)
2. Load current prompt weights
3. Assemble 9 clips (3 clips × 3 platforms)
4. Distribute to all platforms
5. Save post registry for the 18:00 learning loop
"""
import json
import logging
import uuid
from datetime import date as _date, datetime as _datetime
from pathlib import Path

PROJECT_DIR     = Path(__file__).parent.parent
PERFORMANCE_DIR = PROJECT_DIR / "data" / "performance"

logger = logging.getLogger(__name__)


def run_full_day(dry_run: bool = False) -> dict:
    """
    Full daily pipeline run.
    Returns {clips, distributed, dry_run, date}.
    """
    from content_engine.types import TrendBrief, PromptWeights
    from content_engine import assembler, distributor

    date_str = _date.today().isoformat()
    logger.info(f"[pipeline] Starting daily run for {date_str} (dry_run={dry_run})")

    # Load trend brief — run trend scanner now if missing (06:00 might not have fired yet)
    try:
        brief = TrendBrief.load_today()
        logger.info(f"[pipeline] Trend brief loaded: {brief.dominant_emotion}")
    except FileNotFoundError:
        logger.warning("[pipeline] Trend brief missing — running trend_scanner now")
        try:
            from content_engine import trend_scanner
            brief = trend_scanner.run(date_str)
        except Exception as exc:
            logger.warning(f"[pipeline] Trend scanner failed ({exc}) — using default brief")
            brief = TrendBrief(
                date=date_str,
                top_visual_formats=["performance", "b_roll"],
                dominant_emotion="euphoric",
                oversaturated=["generic_dance"],
                hook_pattern_of_day="tension",
                contrarian_gap="raw authentic moments",
                trend_confidence=0.5,
            )

    weights = PromptWeights.load()
    logger.info(f"[pipeline] Weights loaded — best platform: {weights.best_platform}")

    video_dirs = [
        str(PROJECT_DIR / "content" / "videos" / "performances"),
        str(PROJECT_DIR / "content" / "videos" / "b-roll"),
        str(PROJECT_DIR / "content" / "videos" / "phone-footage"),
    ]
    output_dir = str(PROJECT_DIR / "content" / "output" / date_str)

    clips = assembler.run_assembly(
        brief=brief,
        weights=weights,
        video_dirs=video_dirs,
        output_dir=output_dir,
    )
    logger.info(f"[pipeline] Assembly complete: {len(clips)} clips in {output_dir}")

    if dry_run:
        logger.info("[pipeline] DRY RUN — skipping distribution")
        return {"date": date_str, "clips": len(clips), "distributed": 0, "dry_run": True}

    results  = distributor.distribute_all(clips)
    success  = [r for r in results if r.get("success")]
    failures = [r for r in results if not r.get("success")]

    if failures:
        logger.warning(f"[pipeline] {len(failures)} distribution failures: "
                       f"{[r.get('error', '') for r in failures]}")

    # Save post registry for learning loop at 18:00.
    # Schema includes every creative decision needed for the bandit to learn
    # over (mechanism, lead, length, track, variant, exploration flag).
    batch_id  = f"{date_str}-{uuid.uuid4().hex[:8]}"
    posted_at = _datetime.utcnow().isoformat()

    registry = []
    for clip, result in zip(clips, results):
        if not result.get("success"):
            continue
        registry.append({
            "batch_id":       batch_id,
            "post_id":        result.get("post_id", ""),
            "platform":       clip["platform"],
            "via":            result.get("via", "native"),
            "clip_index":     clip["clip_index"],
            "variant":        clip["variant"],
            "hook_text":      clip.get("hook_text", ""),
            "hook_mechanism": clip.get("hook_mechanism", "other"),
            "exploration":    bool(clip.get("exploration", False)),
            "visual_type":    clip.get("visual_type", ""),
            "clip_length":    clip["clip_length"],
            "track_title":    clip.get("track_title", ""),
            "posted_at":      posted_at,
        })

    PERFORMANCE_DIR.mkdir(parents=True, exist_ok=True)
    registry_path = PERFORMANCE_DIR / f"{date_str}_posts.json"
    registry_path.write_text(json.dumps(registry, indent=2))
    logger.info(f"[pipeline] Post registry saved: {registry_path} "
                f"({len(registry)} entries, batch={batch_id})")

    summary = {
        "date":         date_str,
        "clips":        len(clips),
        "distributed":  len(success),
        "failures":     len(failures),
        "dry_run":      False,
        "registry":     str(registry_path),
    }
    logger.info(f"[pipeline] Done — {len(success)}/{len(results)} posted successfully")
    return summary


if __name__ == "__main__":
    import argparse
    import sys
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_full_day(dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
