"""
watcher.py — Detect new audio masters and auto-schedule publish.

Scans content/audio/masters/ for tracks that:
  1. Are in the whitelist (audio_engine.TRACK_BPMS — artist-verified BPMs)
  2. Have a file on disk matching the title
  3. Have NOT been successfully published (per registry)

For each new candidate, plans the next Mon/Thu/Sat 17:00 UTC slot via
scheduler.plan_week() and either returns the plan (scan mode) or
executes publisher.publish_track() with the scheduled time.

Safety rails:
  - Whitelist-based. A track NOT in TRACK_BPMS is never auto-published.
  - Dedup through the existing JSONL registry.
  - File-stability check: a file must be >60 s old (unchanged mtime)
    before we consider it complete on disk. Prevents racing a still-
    copying file.
  - Never publishes live from a single scan — writes a candidate list to
    data/youtube_longform/pending_publish.json. A separate explicit
    command (`promote_candidates`) commits to publishing.

Entry points:
  scan_new_tracks()        → list[TrackCandidate]     (read-only)
  write_pending_report()   → Path                      (snapshots candidates to JSON)
  promote_candidates()     → list[PublishResult]       (actually publishes)

CLI wiring is in rjm.py content youtube watch / publish-pending.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from content_engine.audio_engine import (
    HOLY_RAVE_TRACKS,
    SCRIPTURE_ANCHORS,
    TRACK_BPMS,
)
from content_engine.youtube_longform import config as cfg, registry, scheduler
from content_engine.youtube_longform.publisher import (
    _audio_duration_seconds,
    _resolve_audio_path,
    publish_track,
)
from content_engine.youtube_longform.types import PublishRequest, PublishResult

logger = logging.getLogger(__name__)

# File must be unchanged for this many seconds before we consider it complete
FILE_STABILITY_SECONDS = 60

PENDING_PUBLISH_FILE = cfg.REGISTRY_DIR / "pending_publish.json"
SCAN_REPORT_FILE     = cfg.REGISTRY_DIR / "scan_report.json"


@dataclass
class TrackCandidate:
    """A track detected as eligible for auto-publish."""
    track_title:      str
    audio_path:       str
    bpm:              int
    scripture_anchor: str
    duration_seconds: Optional[int]
    file_mtime:       float               # For stability checks
    reason:           str                 # "new_file" / "never_published" / etc.


# ─── Scan ────────────────────────────────────────────────────────────────────

def scan_new_tracks() -> list[TrackCandidate]:
    """
    Walk audio_engine.TRACK_BPMS whitelist. For each track:
      - find the audio file on disk (via TrackPool-style lookup)
      - check not already successfully published
      - check file has been stable for FILE_STABILITY_SECONDS
    Return the eligible candidates.
    """
    now_ts = time.time()
    candidates: list[TrackCandidate] = []

    # ONLY iterate HOLY_RAVE_TRACKS — this is the explicit whitelist of
    # tracks approved for the Holy Rave channel. The broader TRACK_BPMS
    # dict contains BPM metadata for the full catalogue, but including
    # off-brand tracks (<130 BPM, old worship material) on Holy Rave
    # would dilute the genre-focus that YouTube's recommender uses to
    # cluster our audience.
    for title in HOLY_RAVE_TRACKS:
        if title not in TRACK_BPMS:
            logger.warning(
                "%r listed in HOLY_RAVE_TRACKS but missing from TRACK_BPMS — skipping",
                title,
            )
            continue
        try:
            audio_path = _resolve_audio_path(title, None)
        except Exception as e:
            logger.debug("skip %r: no audio file (%s)", title, e)
            continue

        # File-stability gate
        mtime = audio_path.stat().st_mtime
        age = now_ts - mtime
        if age < FILE_STABILITY_SECONDS:
            logger.info("skip %r: file only %.0fs old; waiting for stability", title, age)
            continue

        # Dedup via registry (successfully published OR errored-but-has-youtube-id)
        existing = registry.already_published(title)
        if existing and existing.get("youtube_id") and not existing.get("error"):
            logger.debug("skip %r: already published %s", title, existing.get("youtube_url"))
            continue

        # Duration (best-effort; None if mutagen can't parse)
        duration: Optional[int] = None
        try:
            duration = _audio_duration_seconds(audio_path)
        except Exception:
            duration = None

        candidates.append(TrackCandidate(
            track_title=title,
            audio_path=str(audio_path),
            bpm=TRACK_BPMS[title],
            scripture_anchor=SCRIPTURE_ANCHORS.get(title, ""),
            duration_seconds=duration,
            file_mtime=mtime,
            reason="never_published" if not existing else "retry_after_error",
        ))
        logger.info(
            "candidate: %s (BPM %d, %s s, %s)",
            title, TRACK_BPMS[title], duration, candidates[-1].reason,
        )

    return candidates


# ─── Report writing ──────────────────────────────────────────────────────────

def write_pending_report(candidates: list[TrackCandidate]) -> Path:
    """Snapshot the candidate list + next-week schedule to a JSON file."""
    cfg.ensure_workspace()
    PENDING_PUBLISH_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Plan the week so the report shows where each would land
    planned = scheduler.plan_week()
    plan_map = {p.track_title.lower(): p for p in planned}

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates": [
            {
                **asdict(c),
                "scheduled_slot": (
                    plan_map[c.track_title.lower()].publish_at_utc.isoformat()
                    if c.track_title.lower() in plan_map
                    else None
                ),
            }
            for c in candidates
        ],
        "weekly_plan": [
            {
                "track": p.track_title,
                "slot":  p.publish_at_utc.isoformat(),
                "bpm":   p.bpm,
                "reason": p.reason,
            }
            for p in planned
        ],
    }
    PENDING_PUBLISH_FILE.write_text(json.dumps(payload, indent=2, default=str))
    SCAN_REPORT_FILE.write_text(json.dumps(payload, indent=2, default=str))
    logger.info("Pending report written: %s", PENDING_PUBLISH_FILE)
    return PENDING_PUBLISH_FILE


# ─── Promote (explicit publish) ──────────────────────────────────────────────

def promote_candidates(
    candidates: Optional[list[TrackCandidate]] = None,
    dry_run:    bool = True,
    limit:      Optional[int] = None,
    motion:     bool = True,
) -> list[PublishResult]:
    """
    Run the full publish pipeline for each candidate, using the weekly
    schedule to pick a publishAt slot.

    By default dry_run=True — no fal.ai spend, no YouTube upload.
    Pass dry_run=False to actually commit the spend.

    Pass limit=N to cap the number of promoted tracks per call
    (prevents a surprise batch of 9 uploads consuming your whole quota).

    motion=True (default) routes through the Kling O3 keyframe-chain
    morph pipeline — ~$10.80/publish, premium Omiki-style visuals.
    Set motion=False for the legacy stills-only path (~$0.15/publish)
    — cheaper but just a single held still image.
    """
    candidates = candidates or scan_new_tracks()
    if not candidates:
        logger.info("No new candidates")
        return []

    # Plan the week and map track → slot
    planned = scheduler.plan_week()
    plan_map = {p.track_title.lower(): p for p in planned}

    results: list[PublishResult] = []
    for i, cand in enumerate(candidates):
        if limit is not None and i >= limit:
            logger.info("Hit limit (%d); skipping remaining %d", limit, len(candidates) - i)
            break

        slot = plan_map.get(cand.track_title.lower())
        if slot is None:
            logger.warning(
                "No schedule slot planned for %r — skipping (would need a future slot)",
                cand.track_title,
            )
            continue

        req = PublishRequest(
            track_title=cand.track_title,
            publish_at_iso=slot.publish_at_utc.isoformat().replace("+00:00", "Z"),
            dry_run=dry_run,
            motion=motion,
        )
        logger.info(
            "%s %s → scheduled for %s (motion=%s)",
            "DRY-RUN" if dry_run else "PUBLISHING",
            cand.track_title, req.publish_at_iso, motion,
        )
        result = publish_track(req)
        results.append(result)

    return results


# ─── Daily autonomous entry point ────────────────────────────────────────────

def daily_auto_publish(
    dry_run:     bool = False,
    max_per_day: int  = 1,
) -> list[PublishResult]:
    """
    Autonomous daily runner. Scans for new candidate tracks, picks the top
    `max_per_day` (by order of HOLY_RAVE_TRACKS), and publishes them with
    motion=True to the next available @osso-so schedule slots.

    Called by ~/bin/rjm-youtube-longform-daily.sh via launchd
    (com.rjm.youtube-longform).

    Safety rails baked in:
      · max_per_day=1 caps daily spend at ~$10.80
      · motion=True (the premium path — this is the Omiki-style system
        RJM committed to as the production default)
      · file-stability gate in scan_new_tracks() (60s unchanged mtime)
      · dedup via registry (won't re-publish an existing track)
      · whitelist-only (HOLY_RAVE_TRACKS) — non-brand tracks never fire
      · slot-aware — if no upcoming @osso-so slot for a track, skips it

    Writes a pending_publish.json snapshot before acting so the decision
    is always auditable post-hoc.
    """
    logger.info("Daily auto-publish scan starting (dry_run=%s, max_per_day=%d)",
                dry_run, max_per_day)

    # 1. Scan
    candidates = scan_new_tracks()
    if not candidates:
        logger.info("No eligible candidates today. Nothing to do.")
        return []

    # 2. Snapshot the decision surface BEFORE acting
    try:
        write_pending_report(candidates)
    except Exception as e:
        logger.warning("Could not write pending report: %s", e)

    # 3. Promote with safety caps
    logger.info(
        "%d candidate(s). Promoting up to %d with motion=True.",
        len(candidates), max_per_day,
    )
    results = promote_candidates(
        candidates=candidates,
        dry_run=dry_run,
        limit=max_per_day,
        motion=True,
    )

    # 4. Summary log
    for r in results:
        if r.error:
            logger.error("%s FAILED: %s", r.request.track_title, r.error)
        else:
            logger.info(
                "%s scheduled → %s (spend $%.2f)",
                r.request.track_title,
                r.youtube_url or "(dry-run)",
                r.cost_usd or 0.0,
            )
    return results
