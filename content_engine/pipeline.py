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
import os
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


def _load_project_env() -> None:
    """Load KEY=VALUE pairs from the project .env file into os.environ.

    Dependency-free implementation (no python-dotenv required). Parses
    simple KEY=VALUE lines, strips surrounding quotes, skips comments and
    blanks, and DOES NOT clobber variables already present in os.environ
    (so explicit exports keep winning over the file).

    This is the fix for the silent 401 Unauthorized storm in distribution:
    buffer_poster / meta_native previously fell back to stale hardcoded
    tokens whenever the pipeline was spawned from a context that didn't
    already export BUFFER_API_KEY / INSTAGRAM_ACCESS_TOKEN / etc.
    """
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as exc:
        logger.warning(f"[pipeline] .env load failed: {exc}")


# Load .env as soon as the module is imported so downstream modules that
# read os.environ at import time (e.g. buffer_poster's CHANNELS dict) see
# the right values.
_load_project_env()


@dataclass
class DailyPipelineConfig:
    # Format mix weighted heavily toward TRANSITIONAL — the "viral hook
    # starter" format (pre-cleared bait clip → count-down / tension → track).
    # Data from 2026-04-16: the transitional variant outperformed emotional
    # and performance on every platform we can measure, so we give it 2
    # of 3 daily slots and keep one PERFORMANCE slot for b-roll variety.
    # Revert to a balanced mix by passing an explicit formats list.
    formats: list = field(default_factory=lambda: [
        ClipFormat.TRANSITIONAL,
        ClipFormat.TRANSITIONAL,
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


def _already_distributed_today(date_str: str) -> tuple[bool, int]:
    """Idempotency guard: return (already_ran, success_count) for today's registry.

    A day counts as 'already distributed' if today's registry file exists AND
    contains at least one entry with success=True OR a non-empty post_id. This
    prevents multiple pipeline runs on the same day from double-posting to
    IG/TikTok/YouTube — which is what happened on 2026-04-16 when 4 separate
    runs landed 17 uploads instead of 3.

    The guard can be bypassed by deleting the registry file for the day or by
    passing force=True to run_full_day (emergency re-run path).
    """
    registry_path = PERFORMANCE_DIR / f"{date_str}_posts.json"
    if not registry_path.exists():
        return False, 0
    try:
        data = json.loads(registry_path.read_text())
    except Exception:
        return False, 0
    successes = [
        r for r in data
        if r.get("success") is True or (r.get("post_id") or "").strip()
    ]
    return (len(successes) > 0), len(successes)


def run_full_day(
    dry_run: bool = False,
    config: Optional[DailyPipelineConfig] = None,
    force: bool = False,
) -> dict:
    """Full daily pipeline run.

    Args:
        dry_run: render + build registry but skip distribution.
        config: override defaults (formats/durations/platforms).
        force: bypass the per-day idempotency guard. Use ONLY when you've
            confirmed the previous run's posts are deleted or you actively
            want a second batch on the same day.

    Returns {date, clips_rendered, valid_clips, distributed, failures, dry_run, registry}.
    """
    config = config or DailyPipelineConfig()
    date_str = _date.today().isoformat()
    logger.info(f"[pipeline] Starting unified daily run for {date_str} (dry_run={dry_run})")

    # Per-day idempotency guard — bail BEFORE any FFmpeg work or network calls
    # if today's registry already has successful posts. Covers the full cost
    # path (rendering ≈ 2 min/clip, captions ≈ 80s each, distribution ≈ 30s
    # per platform) so re-runs are genuinely cheap no-ops.
    if not dry_run and not force:
        ran, succ = _already_distributed_today(date_str)
        if ran:
            logger.warning(
                f"[pipeline] IDEMPOTENCY GUARD: {date_str} already has {succ} "
                f"successful posts in registry — aborting to prevent duplicates. "
                f"Pass force=True or delete {PERFORMANCE_DIR}/{date_str}_posts.json "
                f"to re-run intentionally."
            )
            return {
                "date": date_str,
                "clips_rendered": 0,
                "valid_clips": 0,
                "distributed": 0,
                "failures": 0,
                "dry_run": dry_run,
                "registry": str(PERFORMANCE_DIR / f"{date_str}_posts.json"),
                "skipped_reason": "already_distributed_today",
                "existing_successes": succ,
            }

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
            # Build a lookup so each failure record carries the clip metadata the
            # retry command needs: clip_path, story_path, caption, track_title, etc.
            clip_by_index = {c["clip_index"]: c for c in valid_clips}
            enriched_failures = []
            for f in failures:
                src = clip_by_index.get(f.get("clip_index"), {})
                enriched_failures.append({
                    **f,
                    "clip_path":   src.get("path", ""),
                    "story_path":  src.get("story_path", ""),
                    "caption":     src.get("caption", ""),
                    "hook_text":   src.get("hook_text", ""),
                    "track_title": src.get("track_title", ""),
                    "spotify_url": src.get("spotify_url", ""),
                })
            failed_posts_path = PROJECT_DIR / "data" / "failed_posts.json"
            existing: list = []
            if failed_posts_path.exists():
                try:
                    existing = json.loads(failed_posts_path.read_text())
                except Exception:
                    existing = []
            existing.extend(enriched_failures)
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
            from datetime import datetime, timezone
            _now_iso = datetime.now(timezone.utc).isoformat()
            for r in clip_results:
                entry_copy = dict(entry)
                entry_copy["platform"] = r["platform"]
                entry_copy["post_id"] = r.get("post_id", "")
                # Stamp posted_at at write time — the distributor doesn't
                # always populate it (buffer_poster returns only post_id),
                # but we know the post just completed so 'now UTC' is
                # accurate enough for the learning loop to order events.
                entry_copy["posted_at"] = r.get("posted_at") or (
                    _now_iso if r.get("success") else ""
                )
                entry_copy["success"] = bool(r.get("success"))
                entry_copy["via"] = r.get("via", "native")
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
    used_segments: set[str] = set()
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

        # Generate caption per platform so each surface gets the right voice
        # (Instagram vs YouTube SEO vs TikTok casual vs Stories short-form).
        # The distributor consumes ``caption_by_platform`` when available and
        # falls back to the ``caption`` field for older callers.
        caption_platforms = [
            "instagram", "youtube", "tiktok", "facebook",
            "instagram_story", "facebook_story",
        ]
        caption_by_platform = {
            p: generate_caption(track.title, hook_data["hook"], p, track_facts)
            for p in caption_platforms
        }
        caption = caption_by_platform.get("instagram", "")

        # Pick content segments. More cuts = more visual energy; the old
        # values (2/1/4) left long static shots mid-clip that read as 'stuck'
        # on small screens. Bump transitional content to 3 cuts, emotional
        # to 2 (so there's always motion even in a 7s beat), performance to 5.
        n_segments = {"transitional": 3, "emotional": 2, "performance": 5}.get(fmt.value, 3)
        # Avoid repeating segments across the day's three clips — use the
        # video exclusion set from prior iterations.
        available = [v for v in all_videos if v not in used_segments]
        if len(available) < n_segments:
            available = list(all_videos)  # reset if we've used too many
        segments = random.sample(available, min(n_segments, len(available)))
        used_segments.update(segments)

        output_path = str(Path(output_dir) / f"{fmt.value}_{track.title.lower().replace(' ', '_')}.mp4")
        story_path = str(Path(output_dir) / f"{fmt.value}_{track.title.lower().replace(' ', '_')}_story.mp4")

        clip_meta = {
            "clip_index": clip_idx,
            "format_type": fmt.value,
            "hook_mechanism": hook_data["mechanism"],
            "hook_template_id": hook_data["template_id"],
            "hook_sub_mode": hook_data["sub_mode"],
            "hook_text": hook_data["hook"],
            "caption": caption,                           # default IG-tuned caption
            "caption_by_platform": caption_by_platform,   # distributor picks per-target
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the per-day idempotency guard (use only if today's posts were deleted)",
    )
    args = parser.parse_args()
    result = run_full_day(dry_run=args.dry_run, force=args.force)
    print(json.dumps(result, indent=2))
