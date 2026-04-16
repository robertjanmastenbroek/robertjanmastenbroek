"""
pipeline.py — Unified daily content pipeline orchestrator.

Daily flow:
1. Load trend brief + weights
2. Select track from pool
3. For each of 3 formats: pick visual hook / text hook, render clip, render story variant
4. Distribute all clips to 6 targets
5. Save post registry for learning loop
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Optional

from content_engine.types import (
    ClipFormat, TrendBrief, UnifiedWeights, TransitionalHook,
)

PROJECT_DIR = Path(__file__).parent.parent
PERFORMANCE_DIR = PROJECT_DIR / "data" / "performance"

logger = logging.getLogger(__name__)


@dataclass
class DailyPipelineConfig:
    formats: list = field(default_factory=lambda: [
        ClipFormat.TRANSITIONAL,
        ClipFormat.EMOTIONAL,
        ClipFormat.PERFORMANCE,
    ])
    durations: dict = field(default_factory=lambda: {
        ClipFormat.TRANSITIONAL: 22,
        ClipFormat.EMOTIONAL: 7,
        ClipFormat.PERFORMANCE: 28,
    })
    platforms: list = field(default_factory=lambda: [
        "instagram", "youtube", "facebook", "tiktok",
        "instagram_story", "facebook_story",
    ])


def run_full_day(dry_run: bool = False, config: Optional[DailyPipelineConfig] = None) -> dict:
    """Full daily pipeline run.

    Returns {date, clips_rendered, valid_clips, distributed, failures, dry_run, registry}.
    """
    config = config or DailyPipelineConfig()
    date_str = _date.today().isoformat()
    logger.info(f"[pipeline] Starting unified daily run for {date_str} (dry_run={dry_run})")

    # 1. Load trend brief
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
                oversaturated="generic_dance",
                hook_pattern_of_day="tension",
                contrarian_gap="raw authentic moments",
                trend_confidence=0.5,
            )

    # 2. Load weights
    weights = UnifiedWeights.load()
    logger.info(f"[pipeline] Weights loaded — best platform: {weights.best_platform}")

    # 3. Select track
    from content_engine.audio_engine import TrackPool, detect_bpm, find_peak_sections
    pool = TrackPool.load_pool()
    track = pool.select_track(weights.track_weights)
    pool.mark_used(track.title)

    # Detect BPM if not set
    if track.bpm == 0:
        track.bpm = detect_bpm(track.file_path)
    logger.info(f"[pipeline] Track selected: {track.title} ({track.bpm} BPM)")

    # 4. Find peak audio sections (one per format)
    peak_sections = find_peak_sections(track.file_path, max(config.durations.values()), n_sections=3)

    # 5. Build clips
    output_dir = str(PROJECT_DIR / "content" / "output" / date_str)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    clips = build_daily_clips(config, brief, weights, track, peak_sections, output_dir)
    logger.info(f"[pipeline] Rendered {len(clips)} clips")

    # 6. Validate renders
    from content_engine.renderer import validate_output
    valid_clips = []
    for clip in clips:
        result = validate_output(clip["path"], clip["clip_length"])
        if result["valid"]:
            valid_clips.append(clip)
        else:
            logger.error(f"[pipeline] Invalid render for {clip['format_type']}: {result['errors']}")

    if not valid_clips:
        logger.critical("[pipeline] No valid clips rendered — aborting distribution")
        return {
            "date": date_str,
            "clips_rendered": len(clips),
            "valid_clips": 0,
            "distributed": 0,
            "failures": len(clips),
            "dry_run": dry_run,
            "registry": "",
        }

    # 7. Distribute
    results = []
    if dry_run:
        logger.info("[pipeline] DRY RUN — skipping distribution")
        registry_dir = PERFORMANCE_DIR / "dry-run"
    else:
        from content_engine.distributor import distribute_all
        results = distribute_all(valid_clips)
        success = [r for r in results if r.get("success")]
        failures = [r for r in results if not r.get("success")]
        if failures:
            logger.warning(f"[pipeline] {len(failures)} distribution failures")
            failed_posts_path = PROJECT_DIR / "data" / "failed_posts.json"
            existing: list = []
            if failed_posts_path.exists():
                try:
                    existing = json.loads(failed_posts_path.read_text())
                except Exception:
                    existing = []
            existing.extend(failures)
            failed_posts_path.write_text(json.dumps(existing, indent=2))
            logger.info(f"[pipeline] Wrote {len(failures)} failures → {failed_posts_path}")
        registry_dir = PERFORMANCE_DIR

    # 8. Save post registry
    # One entry per (clip, platform) pair. Match results to their source clip via
    # clip_index — the cross-product bug turned 3 clips × 6 targets (18 expected)
    # into 3 × 18 = 54 rows.
    registry = []
    for clip in valid_clips:
        entry = {
            "platform": "all",
            "format_type": clip["format_type"],
            "clip_index": clip["clip_index"],
            "variant": "a",
            "hook_mechanism": clip.get("hook_mechanism", ""),
            "hook_template_id": clip.get("hook_template_id", ""),
            "hook_sub_mode": clip.get("hook_sub_mode", ""),
            "visual_type": clip.get("visual_type", ""),
            "transitional_category": clip.get("transitional_category", ""),
            "transitional_file": clip.get("transitional_file", ""),
            "track_title": track.title,
            "clip_length": clip["clip_length"],
        }
        # If we distributed, expand into per-platform entries for THIS clip only
        if not dry_run and results:
            clip_results = [
                r for r in results
                if r.get("clip_index") == clip["clip_index"]
                and r.get("platform", "") != ""
            ]
            for r in clip_results:
                entry_copy = dict(entry)
                entry_copy["platform"] = r["platform"]
                entry_copy["post_id"] = r.get("post_id", "")
                entry_copy["posted_at"] = r.get("posted_at", "")
                entry_copy["success"] = bool(r.get("success"))
                if not r.get("success"):
                    entry_copy["error"] = r.get("error", "")
                registry.append(entry_copy)
        else:
            registry.append(entry)

    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_path = registry_dir / f"{date_str}_posts.json"
    registry_path.write_text(json.dumps(registry, indent=2))
    logger.info(f"[pipeline] Post registry saved: {registry_path} ({len(registry)} entries)")

    distributed_count = len([r for r in results if r.get("success")]) if not dry_run else 0

    return {
        "date": date_str,
        "clips_rendered": len(clips),
        "valid_clips": len(valid_clips),
        "distributed": distributed_count,
        "failures": len(clips) - len(valid_clips),
        "dry_run": dry_run,
        "registry": str(registry_path),
    }


def build_daily_clips(
    config: DailyPipelineConfig,
    brief: TrendBrief,
    weights: UnifiedWeights,
    track,
    peak_sections: list,
    output_dir: str,
) -> list:
    """Build 3 clips (one per format)."""
    from content_engine.renderer import (
        render_transitional, render_emotional, render_performance, render_story_variant,
    )
    from content_engine.generator import generate_hooks_for_format, generate_caption
    from content_engine.transitional_manager import TransitionalManager
    import random

    clips = []
    video_dirs = [
        str(PROJECT_DIR / "content" / "videos" / "b-roll"),
        str(PROJECT_DIR / "content" / "videos" / "phone-footage"),
        str(PROJECT_DIR / "content" / "videos" / "performances"),
    ]

    # Collect available videos
    all_videos = []
    for vd in video_dirs:
        vd_path = Path(vd)
        if vd_path.exists():
            for f in vd_path.iterdir():
                if f.suffix.lower() in (".mp4", ".mov"):
                    all_videos.append(str(f))

    if not all_videos:
        logger.error("[pipeline] No source videos found!")
        return []

    used_ids = set()
    track_facts = {
        "bpm": track.bpm,
        "scripture_anchor": track.scripture_anchor,
        "energy": track.energy,
    }

    for clip_idx, fmt in enumerate(config.formats):
        duration = config.durations[fmt]
        audio_start = peak_sections[clip_idx] if clip_idx < len(peak_sections) else 30.0

        # Generate hook
        hook_data = generate_hooks_for_format(fmt, track.title, track_facts, weights.hook_weights, used_ids)
        used_ids.add(hook_data["template_id"])

        # Generate caption
        caption = generate_caption(track.title, hook_data["hook"], "instagram", track_facts)

        # Pick content segments
        n_segments = {"transitional": 2, "emotional": 1, "performance": 4}.get(fmt.value, 2)
        segments = random.sample(all_videos, min(n_segments, len(all_videos)))

        output_path = str(Path(output_dir) / f"{fmt.value}_{track.title.lower().replace(' ', '_')}.mp4")
        story_path = str(Path(output_dir) / f"{fmt.value}_{track.title.lower().replace(' ', '_')}_story.mp4")

        clip_meta = {
            "clip_index": clip_idx,
            "format_type": fmt.value,
            "hook_mechanism": hook_data["mechanism"],
            "hook_template_id": hook_data["template_id"],
            "hook_sub_mode": hook_data["sub_mode"],
            "hook_text": hook_data["hook"],
            "caption": caption,
            "track_title": track.title,
            "clip_length": duration,
            "visual_type": "b_roll",  # categorize based on actual segments
            "transitional_category": "",
            "transitional_file": "",
            "spotify_url": "https://open.spotify.com/artist/2Seaafm5k1hAuCkpdq7yds",
        }

        try:
            if fmt == ClipFormat.TRANSITIONAL:
                # Pick transitional bait clip
                tm = TransitionalManager()
                bait = tm.pick()
                if bait:
                    bait_path = str(tm.full_path(bait["file"]))
                    clip_meta["transitional_category"] = bait["category"]
                    clip_meta["transitional_file"] = bait["file"]
                    tm.mark_used(bait["file"])

                    render_transitional(
                        bait_clip=bait_path,
                        content_segments=segments,
                        audio_path=track.file_path,
                        audio_start=audio_start,
                        hook_text=hook_data["hook"],
                        track_label=f"{track.title} — Robert-Jan Mastenbroek",
                        platform="youtube",   # neutral grade; distributor applies per-platform
                        output_path=output_path,
                        target_duration=duration,
                    )
                else:
                    logger.warning("[pipeline] No transitional hooks available, falling back to emotional format")
                    render_emotional(segments, track.file_path, audio_start, hook_data["hook"],
                                     "youtube", output_path, duration)

            elif fmt == ClipFormat.EMOTIONAL:
                render_emotional(segments, track.file_path, audio_start, hook_data["hook"],
                                 "youtube", output_path, duration)

            elif fmt == ClipFormat.PERFORMANCE:
                render_performance(segments, track.file_path, audio_start, hook_data["hook"],
                                   "youtube", output_path, duration)

            # Render Story variant
            render_story_variant(output_path, track.title, clip_meta["spotify_url"], story_path)

            clip_meta["path"] = output_path
            clip_meta["story_path"] = story_path
            clips.append(clip_meta)

        except Exception as e:
            logger.error(f"[pipeline] Failed to render {fmt.value}: {e}")
            continue

    return clips


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_full_day(dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
