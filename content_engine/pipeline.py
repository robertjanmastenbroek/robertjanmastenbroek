"""
pipeline.py — Unified daily content pipeline orchestrator.

Daily flow:
1. Load trend brief + weights
2. Select track from pool
3. For each of 3 formats: pick visual hook / text hook, render clip, render story variant
4. Distribute all clips to 6 targets
5. Save post registry for learning loop
"""
import fcntl
import json
import logging
import os
import random
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

# Viral-only bias for bait-clip picks. Pexels-sourced clips in the other
# six categories have zero evidence of viral performance, while the 15
# clips in viral/ match the proven 20k+ IG post format. Suppress the
# rest until we have CTR evidence otherwise.
VIRAL_ONLY_CATEGORY_WEIGHTS = {
    "viral": 1.0,
    "nature": 0.0,
    "satisfying": 0.0,
    "elemental": 0.0,
    "sports": 0.0,
    "craftsmanship": 0.0,
    "illusion": 0.0,
}

# Fast-cut format constants — derived from the Short Video Coach research
# (Anyma / ISOxo / Knock2 / RL Grime cut rates). Beat lengths fall naturally
# into this range at 130-140 BPM (kick = 60 / BPM = 0.43-0.46s), so cuts on
# the kick land here automatically.
FAST_CUT_MIN_S = 0.4
FAST_CUT_MAX_S = 0.7
# Final "hold" window — last segment locks on one shot so the drop breathes.
FAST_CUT_HOLD_MIN_S = 3.0
FAST_CUT_HOLD_MAX_S = 5.0


def _segment_weight(seg_path: str, segment_weights: dict) -> float:
    """Look up a segment's learned weight by basename. Default 1.0 for unseen."""
    return float(segment_weights.get(Path(seg_path).name, 1.0))


def _weighted_choice(items: list, weights: list) -> "str | None":
    """Random pick proportional to weights. Returns None on empty list."""
    if not items:
        return None
    total = sum(max(w, 0.0) for w in weights)
    if total <= 0:
        return random.choice(items)
    r = random.random() * total
    cumulative = 0.0
    for item, w in zip(items, weights):
        cumulative += max(w, 0.0)
        if r <= cumulative:
            return item
    return items[-1]


def compute_fast_cut_slices(
    source_pool: list,
    total_duration: float,
    bpm: float,
    segment_weights: dict | None = None,
) -> list:
    """Return a list of (source_path, start_s, end_s) tuples for a fast-cut clip.

    Each slice lands in the [FAST_CUT_MIN_S, FAST_CUT_MAX_S] window, which at
    130-140 BPM aligns with the kick (60 / BPM = 0.43-0.46s). The final slice
    is held FAST_CUT_HOLD_MIN_S to FAST_CUT_HOLD_MAX_S to let the drop breathe.

    Source picks are biased by segment_weights (basename → weight). Slices
    pull from random in-source positions so we don't always see the same
    opening frame; positions are skewed toward the middle (avoids dead intro/
    outro frames).

    Returns empty list if source_pool is empty.
    """
    if not source_pool:
        return []

    segment_weights = segment_weights or {}
    weights = [_segment_weight(p, segment_weights) for p in source_pool]

    # Reserve the final hold slice
    hold_dur = min(FAST_CUT_HOLD_MAX_S, max(FAST_CUT_HOLD_MIN_S, total_duration * 0.18))
    hold_dur = min(hold_dur, total_duration - FAST_CUT_MIN_S * 4)  # leave room for ≥4 fast cuts
    cut_budget = total_duration - hold_dur

    # Beat-aligned cut length: 60/BPM if it falls in the window, else midpoint
    beat_s = 60.0 / max(bpm, 1.0) if bpm else (FAST_CUT_MIN_S + FAST_CUT_MAX_S) / 2
    cut_len = max(FAST_CUT_MIN_S, min(FAST_CUT_MAX_S, beat_s))

    slices = []
    elapsed = 0.0
    while elapsed + cut_len <= cut_budget:
        src = _weighted_choice(source_pool, weights)
        if src is None:
            break
        src_dur = _safe_probe_duration(src)
        # Skew toward middle 60% of source so we avoid dead intro/outro
        margin = src_dur * 0.2
        max_start = max(0.0, src_dur - cut_len - margin)
        start_s = random.uniform(margin, max_start) if max_start > margin else 0.0
        slices.append((src, start_s, start_s + cut_len))
        elapsed += cut_len

    # Append the final hold slice — pick a high-weight source if possible
    hold_src = _weighted_choice(source_pool, weights)
    if hold_src:
        hold_src_dur = _safe_probe_duration(hold_src)
        margin = hold_src_dur * 0.15
        max_start = max(0.0, hold_src_dur - hold_dur - margin)
        start_s = random.uniform(margin, max_start) if max_start > margin else 0.0
        slices.append((hold_src, start_s, start_s + hold_dur))

    return slices


def _safe_probe_duration(path: str) -> float:
    """ffprobe wrapper — returns 0.0 on any failure so callers fall through cleanly."""
    try:
        from content_engine.renderer import _get_video_info
        info = _get_video_info(path)
        return float(info.get("duration", 0.0))
    except Exception:
        return 0.0


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


def _get_latest_longform_url(track_title: str) -> str | None:
    """Return the YouTube URL for the most recent successful longform publish of this track.

    Correctness contract: the JSONL is append-only (registry.py appends, never rewrites),
    so the last matching line is always the most recent publish. If that invariant is ever
    broken, the returned URL may be stale — add a published_at timestamp comparison then.
    """
    jsonl = PROJECT_DIR / "data" / "youtube_longform" / "youtube_longform.jsonl"
    if not jsonl.exists():
        return None
    slug = track_title.lower().strip()
    best = None
    try:
        for line in jsonl.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw_url = (entry.get("youtube_url") or "").strip()
            if (
                entry.get("track_title", "").lower() == slug
                and raw_url.startswith("https://")
                and not entry.get("dry_run")
            ):
                best = raw_url
    except Exception:
        pass
    return best


def _get_motion_clips_for_track(track_title: str) -> list[str]:
    """Return holy-rave-motion clips for this track plus universal RJM archetype clips.

    Priority order:
    1. Track-specific clips whose filename contains the track slug
       (e.g. morph_rjm_selah_*, morph_rjm_jericho_*)
    2. Universal RJM character clips — rjm_warrior / rjm_priestess / rjm_temple
       appear in every track's Kling story and are visually appropriate for any
       track, including tracks that have no dedicated morph clips yet.

    Returns [] only when holy-rave-motion/ does not exist. Never returns
    another track's named clips (selah clips will not appear under halleluyah).
    """
    motion_dir = PROJECT_DIR / "content" / "videos" / "holy-rave-motion"
    if not motion_dir.exists():
        return []
    slug = track_title.lower().strip().replace(" ", "_")
    # Archetypes shared across all track Kling stories
    UNIVERSAL = ("rjm_warrior", "rjm_priestess", "rjm_temple")
    track_clips: list[str] = []
    universal_clips: list[str] = []
    for f in motion_dir.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in (".mp4", ".mov"):
            continue
        name = f.name.lower()
        if slug in name:
            track_clips.append(str(f))
        elif any(pat in name for pat in UNIVERSAL):
            universal_clips.append(str(f))
    # Track-specific first so the most relevant visuals lead
    return track_clips + universal_clips


def emotional_duration_from_weights(best_clip_length: int, min_s: int = 5, max_s: int = 15) -> int:
    """Clamp emotional clip duration to [5, 15] seconds.

    The viral learning loop computes best_clip_length from real viewer retention
    data. This function prevents extreme values while respecting the learned
    preference when it falls within the valid range.
    """
    return max(min_s, min(max_s, best_clip_length))


def derive_format_mix(format_weights: dict, n_clips: int = 3) -> list:
    """Derive a clip format mix from learned weights.

    Each format can occupy at most n_clips-1 slots so the mix always has
    at least two distinct formats. Zero-weight formats are never picked.
    """
    formats = [ClipFormat.TRANSITIONAL, ClipFormat.EMOTIONAL, ClipFormat.PERFORMANCE]
    base_weights = [max(format_weights.get(f.value, 1.0), 0.0) for f in formats]
    result = []
    counts = {f: 0 for f in formats}
    max_per_format = n_clips - 1

    while len(result) < n_clips:
        available = [
            (f, w) for f, w in zip(formats, base_weights)
            if counts[f] < max_per_format
        ]
        if not available:
            available = list(zip(formats, base_weights))
        total = sum(w for _, w in available)
        if total == 0:
            f = random.choice([fmt for fmt, _ in available])
        else:
            r = random.random() * total
            cumulative = 0.0
            f = available[-1][0]
            for fmt, w in available:
                cumulative += w
                if r <= cumulative:
                    f = fmt
                    break
        result.append(f)
        counts[f] += 1

    return result


@dataclass
class DailyPipelineConfig:
    # Format mix weighted heavily toward TRANSITIONAL — the "viral hook
    # starter" format (pre-cleared bait clip → count-down / tension → track).
    # Data from 2026-04-16: the transitional variant outperformed emotional
    # and performance on every platform we can measure, so we give it 2
    # of 3 daily slots and keep one PERFORMANCE slot for b-roll variety.
    # Revert to a balanced mix by passing an explicit formats list.
    formats: list = field(default_factory=lambda: [
        ClipFormat.SACRED_ARC,
        ClipFormat.LONGFORM_TRAILER,
        ClipFormat.PERFORMANCE_FAST_CUT,
    ])
    durations: dict = field(default_factory=lambda: {
        ClipFormat.SACRED_ARC: 22,
        ClipFormat.LONGFORM_TRAILER: 22,
        ClipFormat.TRANSITIONAL: 22,
        ClipFormat.PERFORMANCE_FAST_CUT: 22,
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
    date_str = _date.today().isoformat()
    logger.info(f"[pipeline] Starting unified daily run for {date_str} (dry_run={dry_run})")

    # Process-level exclusive lock — prevents concurrent launchd + manual runs
    # from both passing the idempotency guard and double-posting. Lock is held
    # for the duration of this call; released automatically when fd is GC'd.
    if not dry_run:
        _lock_path = PERFORMANCE_DIR.parent / ".pipeline.lock"
        _lock_path.parent.mkdir(parents=True, exist_ok=True)
        _lock_fd = _lock_path.open("w")
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _lock_fd.close()
            logger.error("[pipeline] LOCK: another run is in progress — aborting to prevent double-post")
            return {
                "date": date_str, "clips_rendered": 0, "valid_clips": 0,
                "distributed": 0, "failures": 0, "dry_run": dry_run,
                "skipped_reason": "concurrent_run_blocked_by_lock",
            }

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

    # Locked slot allocation:
    #   Slot 0 → SACRED_ARC (proven viral: bait hook + slow performance arc)
    #   Slot 1 → LONGFORM_TRAILER (short cut of day's longform + YouTube CTA)
    #   Slot 2 → PERFORMANCE_FAST_CUT (Anyma/ISOxo: 0.4-0.7s cuts on the kick)
    # LONGFORM_TRAILER replaces the second SACRED_ARC slot. It uses the Kling
    # morph clips from holy-rave-motion/ as visuals and appends a YouTube link
    # to every caption. Falls back to SACRED_ARC behaviour when no longform
    # exists for the selected track.
    if config is None:
        config = DailyPipelineConfig(
            formats=[
                ClipFormat.SACRED_ARC,
                ClipFormat.LONGFORM_TRAILER,
                ClipFormat.PERFORMANCE_FAST_CUT,
            ],
            durations={
                ClipFormat.SACRED_ARC: 22,
                ClipFormat.LONGFORM_TRAILER: 22,
                ClipFormat.TRANSITIONAL: 22,
                ClipFormat.PERFORMANCE_FAST_CUT: 22,
                ClipFormat.EMOTIONAL: emotional_duration_from_weights(weights.best_clip_length),
                ClipFormat.PERFORMANCE: 28,
            },
        )

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
            # Per-segment usage so the learning loop can score footage,
            # not just the full clip. List of {file, path, start, end}.
            "segments_used": clip.get("segments_used", []),
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
    _reg_tmp = registry_path.with_suffix(".tmp")
    _reg_tmp.write_text(json.dumps(registry, indent=2))
    _reg_tmp.replace(registry_path)  # atomic rename — no partial-write corruption
    logger.info(f"[pipeline] Post registry saved: {registry_path} ({len(registry)} entries)")

    _cleanup_output_dir(output_dir)

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


def _cleanup_output_dir(output_dir: str) -> None:
    """Delete _ prefixed temp files and update the 'latest' symlink."""
    out = Path(output_dir)
    removed = []
    for f in out.iterdir():
        if f.name.startswith("_"):
            try:
                f.unlink()
                removed.append(f.name)
            except OSError:
                pass
    if removed:
        logger.info(f"[pipeline] Cleaned {len(removed)} temp files from {out.name}")

    latest = out.parent / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(out.name)
        logger.info(f"[pipeline] latest → {out.name}")
    except OSError as e:
        logger.warning(f"[pipeline] Could not update latest symlink: {e}")


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
        render_transitional, render_emotional, render_performance,
        render_performance_fast_cut,
    )
    from content_engine.generator import generate_hooks_for_format, generate_caption
    from content_engine.transitional_manager import TransitionalManager
    import random

    clips = []
    video_dirs = [
        str(PROJECT_DIR / "content" / "videos" / "b-roll"),
        str(PROJECT_DIR / "content" / "videos" / "phone-footage"),
        str(PROJECT_DIR / "content" / "videos" / "performances"),
        # Kling O3 motion clips from Holy Rave long-form publishes. Every
        # 10s morph clip we pay for on fal.ai gets copied here after the
        # long-form publish completes (see publisher._add_motion_clips_to_shorts_pool).
        # Re-using these as Shorts source footage amortizes the ~$8/track
        # Kling spend across both long-form AND Shorts output.
        str(PROJECT_DIR / "content" / "videos" / "holy-rave-motion"),
    ]
    # Performance-anchored pool = performances/ + phone-footage/. Used by
    # SACRED_ARC and PERFORMANCE_FAST_CUT so the music-source signal stays
    # strong (per Short Video Coach research, 2026-04-19).
    perf_dir = str(PROJECT_DIR / "content" / "videos" / "performances")
    phone_dir = str(PROJECT_DIR / "content" / "videos" / "phone-footage")

    # Collect available videos — rglob so nested subdirectories are included
    all_videos = []
    for vd in video_dirs:
        vd_path = Path(vd)
        if vd_path.exists():
            for f in vd_path.rglob("*"):
                if f.is_file() and f.suffix.lower() in (".mp4", ".mov"):
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

        # ── Step 1: Determine visual context BEFORE generating hook text ──────
        # Hook and caption quality depend on knowing what's on screen. Picking
        # the bait clip here means Claude can write for nature footage vs
        # satisfying footage vs performance footage specifically.
        visual_context: dict = {}
        bait = None
        bait_path = None

        if fmt in (ClipFormat.TRANSITIONAL, ClipFormat.SACRED_ARC,
                   ClipFormat.LONGFORM_TRAILER, ClipFormat.PERFORMANCE_FAST_CUT):
            tm = TransitionalManager()
            bait = tm.pick(category_weights=VIRAL_ONLY_CATEGORY_WEIGHTS)
            if bait:
                bait_path = str(tm.full_path(bait["file"]))
                tm.mark_used(bait["file"])
                visual_context = {"category": bait["category"], "file": bait["file"]}
        elif fmt == ClipFormat.PERFORMANCE:
            visual_context = {"category": "performance"}
        elif fmt == ClipFormat.EMOTIONAL:
            visual_context = {"category": "emotional"}

        # ── Step 2: Generate hook with visual context ─────────────────────────
        # LONGFORM_TRAILER reuses SACRED_ARC hook templates (proven viral pool)
        # rather than a separate untested set.
        hook_fmt = ClipFormat.SACRED_ARC if fmt == ClipFormat.LONGFORM_TRAILER else fmt
        hook_data = generate_hooks_for_format(
            hook_fmt, track.title, track_facts, weights.hook_weights, used_ids,
            visual_context=visual_context,
            sub_mode_weights=weights.sub_mode_weights,
        )
        used_ids.add(hook_data["template_id"])

        # ── Step 3: Generate captions with visual context ─────────────────────
        # Each platform gets its own voice; distributor picks per-target.
        caption_platforms = [
            "instagram", "youtube", "tiktok", "facebook",
            "instagram_story", "facebook_story",
        ]
        caption_by_platform = {
            p: generate_caption(
                track.title, hook_data["hook"], p, track_facts,
                visual_context=visual_context,
            )
            for p in caption_platforms
        }
        caption = caption_by_platform.get("instagram", "")

        # LONGFORM_TRAILER: append YouTube CTA to every platform caption.
        if fmt == ClipFormat.LONGFORM_TRAILER:
            yt_url = _get_latest_longform_url(track.title)
            if yt_url:
                cta_map = {
                    "instagram":       f"\n\n▶ Full track on YouTube → {yt_url}",
                    "youtube":         f"\n\n▶ Stream the full version: {yt_url}",
                    "tiktok":          f"\n▶ Full track → YouTube: {yt_url}",
                    "facebook":        f"\n\n▶ Watch the full track on YouTube: {yt_url}",
                    "instagram_story": f"\n▶ YouTube: {yt_url}",
                    "facebook_story":  f"\n▶ YouTube: {yt_url}",
                }
                caption_by_platform = {
                    p: caption_by_platform.get(p, "") + cta_map.get(p, f"\n▶ {yt_url}")
                    for p in caption_platforms
                }
                caption = caption_by_platform.get("instagram", caption)
                logger.info(f"[pipeline] LONGFORM_TRAILER: YouTube CTA appended ({yt_url})")
            else:
                logger.info(f"[pipeline] LONGFORM_TRAILER: no longform URL for {track.title!r}, CTA skipped")

        n_segments = {
            "transitional": 3, "emotional": 2, "performance": 5, "sacred_arc": 3,
            "performance_fast_cut": 1,  # slice list computed by helper, not sampled here
            "longform_trailer": 3,
        }.get(fmt.value, 3)

        # Performance-anchored pool: performances/ + phone-footage/ combined.
        # SACRED_ARC and PERFORMANCE_FAST_CUT both pull from here; phone-footage/
        # was empty as of 2026-04-19 (user has stage video only) but the path is
        # wired so dropping clips in starts the diversification immediately.
        perf_pool_all = [v for v in all_videos if perf_dir in v or phone_dir in v]
        segment_slices: list = []  # only populated for PERFORMANCE_FAST_CUT

        if fmt == ClipFormat.LONGFORM_TRAILER:
            # Prefer Kling morph clips for this track only.
            # _get_motion_clips_for_track returns [] when no track-specific clips
            # exist, preventing another track's visuals from appearing here.
            motion_clips = _get_motion_clips_for_track(track.title)
            avail_motion = [v for v in motion_clips if v not in used_segments]
            if not avail_motion:
                avail_motion = motion_clips  # re-open pool if dedup exhausted it
            if avail_motion:
                segments = random.sample(avail_motion, min(n_segments, len(avail_motion)))
            else:
                # No track-specific motion clips — fall back to perf pool.
                # Explicitly exclude holy-rave-motion/ to avoid mismatched visuals.
                non_motion_videos = [
                    v for v in all_videos
                    if "holy-rave-motion" not in v
                ]
                avail_perf = [v for v in perf_pool_all if v not in used_segments] or perf_pool_all
                fallback = avail_perf if avail_perf else non_motion_videos or list(all_videos)
                segments = random.sample(fallback, min(n_segments, len(fallback)))
                logger.info("[pipeline] LONGFORM_TRAILER: no motion clips for %r, using perf pool", track.title)

        elif fmt == ClipFormat.SACRED_ARC:
            avail_perf = [v for v in perf_pool_all if v not in used_segments]
            if len(avail_perf) < n_segments:
                avail_perf = perf_pool_all  # ignore dedup if pool too small
            available = avail_perf if avail_perf else [v for v in all_videos if v not in used_segments]
            segments = random.sample(available, min(n_segments, len(available)))
        elif fmt == ClipFormat.PERFORMANCE_FAST_CUT:
            # The slice computer handles weighted picking + beat alignment.
            # Pool is performance-anchored only — no atmospheric b-roll.
            fc_pool = perf_pool_all if perf_pool_all else list(all_videos)
            segment_slices = compute_fast_cut_slices(
                source_pool=fc_pool,
                total_duration=duration - 4.0,  # reserve ~4s for bait
                bpm=track.bpm,
                segment_weights=getattr(weights, "segment_weights", {}) or {},
            )
            # Deduplicate the underlying source paths into `segments` so the
            # dedup set + tracking logic below works the same as other formats.
            segments = list({s[0] for s in segment_slices}) or [random.choice(fc_pool)]
        else:
            available = [v for v in all_videos if v not in used_segments]
            if len(available) < n_segments:
                available = list(all_videos)
            segments = random.sample(available, min(n_segments, len(available)))

        used_segments.update(segments)

        output_path = str(Path(output_dir) / f"{fmt.value}_{clip_idx}_{track.title.lower().replace(' ', '_')}.mp4")

        # Visual type tag for the registry / learning loop.
        if fmt == ClipFormat.LONGFORM_TRAILER:
            visual_type = "longform_motion"
        elif fmt in (ClipFormat.SACRED_ARC, ClipFormat.PERFORMANCE_FAST_CUT):
            visual_type = "performance"
        else:
            visual_type = "b_roll"

        # segments_used = list of {file, start, end} dicts. For fast-cut this
        # carries the actual slice timings; for other formats start=0 and
        # end=clip's pro-rata share. Drives segment-level virality learning.
        if fmt == ClipFormat.PERFORMANCE_FAST_CUT and segment_slices:
            segments_used = [
                {
                    "file": Path(src).name,
                    "path": src,
                    "start": float(s_start),
                    "end": float(s_end),
                }
                for src, s_start, s_end in segment_slices
            ]
        else:
            seg_share = duration / max(len(segments), 1)
            segments_used = [
                {
                    "file": Path(s).name,
                    "path": s,
                    "start": 0.0,
                    "end": float(seg_share),
                }
                for s in segments
            ]

        clip_meta = {
            "clip_index": clip_idx,
            "format_type": fmt.value,
            "hook_mechanism": hook_data["mechanism"],
            "hook_template_id": hook_data["template_id"],
            "hook_sub_mode": hook_data["sub_mode"],
            "hook_text": hook_data["hook"],
            "caption": caption,
            "caption_by_platform": caption_by_platform,
            "track_title": track.title,
            "bpm": track.bpm,
            "clip_length": duration,
            "visual_type": visual_type,
            "transitional_category": visual_context.get("category", ""),
            "transitional_file": visual_context.get("file", ""),
            "segments_used": segments_used,
            "spotify_url": "https://open.spotify.com/artist/2Seaafm5k1hAuCkpdq7yds",
        }

        try:
            if fmt in (ClipFormat.TRANSITIONAL, ClipFormat.SACRED_ARC, ClipFormat.LONGFORM_TRAILER):
                if bait_path:
                    render_transitional(
                        bait_clip=bait_path,
                        content_segments=segments,
                        audio_path=track.file_path,
                        audio_start=audio_start,
                        hook_text=hook_data["hook"],
                        track_label=f"{track.title} — Robert-Jan Mastenbroek",
                        platform="youtube",
                        output_path=output_path,
                        target_duration=duration,
                    )
                else:
                    logger.warning(f"[pipeline] No transitional bait available for {fmt.value}, falling back to emotional")
                    render_emotional(segments, track.file_path, audio_start, hook_data["hook"],
                                     "youtube", output_path, duration)

            elif fmt == ClipFormat.PERFORMANCE_FAST_CUT:
                if bait_path and segment_slices:
                    render_performance_fast_cut(
                        bait_clip=bait_path,
                        segment_slices=segment_slices,
                        audio_path=track.file_path,
                        audio_start=audio_start,
                        hook_text=hook_data["hook"],
                        track_label=f"{track.title} — Robert-Jan Mastenbroek",
                        platform="youtube",
                        output_path=output_path,
                        target_duration=duration,
                    )
                else:
                    logger.warning(
                        f"[pipeline] Fast-cut needs bait + slices "
                        f"(bait={bool(bait_path)}, slices={len(segment_slices)}); "
                        f"falling back to performance render"
                    )
                    render_performance(segments, track.file_path, audio_start, hook_data["hook"],
                                       "youtube", output_path, duration)

            elif fmt == ClipFormat.EMOTIONAL:
                render_emotional(segments, track.file_path, audio_start, hook_data["hook"],
                                 "youtube", output_path, duration)

            elif fmt == ClipFormat.PERFORMANCE:
                render_performance(segments, track.file_path, audio_start, hook_data["hook"],
                                   "youtube", output_path, duration)

            clip_meta["path"] = output_path
            clip_meta["story_path"] = output_path   # stories reuse main clip — no separate re-render
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
