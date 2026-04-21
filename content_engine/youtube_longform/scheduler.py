"""
scheduler.py — Weekly auto-scheduler for Holy Rave uploads.

Picks the next track from RJM's catalogue based on:
  - Rotation lock (don't re-publish the same track within N weeks)
  - BPM tier spread (alternate high-BPM ecstatic with mid-BPM processional)
  - Unreleased-tracks-first preference (Kadosh, Side By Side → YT exclusive)
  - Last-published timestamp

Schedules 2–3 uploads per week at the Thursday 17:00 UTC slot (default).
Returns a list of PublishRequest objects ready to hand to publisher.publish_track.

Invoked by:
  - `python3 rjm.py content youtube schedule --dry-run`
  - launchd job that fires every Monday 08:00 CET to queue the week
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from content_engine.audio_engine import SCRIPTURE_ANCHORS, TRACK_BPMS
from content_engine.youtube_longform import config as cfg, registry
from content_engine.youtube_longform.types import PublishRequest

logger = logging.getLogger(__name__)

# ─── Scheduling policy ───────────────────────────────────────────────────────

WEEKLY_SLOTS = 3                                  # Mon, Thu, Sat by default
MIN_DAYS_BETWEEN_SAME_TRACK = 45                  # 6+ weeks before a track can repeat
PUBLISH_HOUR_UTC_DEFAULT = cfg.DEFAULT_PUBLISH_HOUR_UTC

# Track priority multipliers (higher = more likely to be picked this week)
TRACK_PRIORITY = {
    # Unreleased YouTube-first exclusives — push these first
    "kadosh":             3.0,
    "side by side":       3.0,
    # Strongest catalogue + highest scripture visual potency
    "jericho":            2.5,   # Joshua 6 — strongest Subtle Salt
    "selah":              2.2,   # Psalm 46 + Middle Eastern instrumentation
    "halleluyah":         2.0,
    "renamed":            1.8,   # Isaiah 62 — new name
    "fire in our hands":  1.6,
    "living water":       1.5,   # John 4
    "he is the light":    1.3,   # John 8
}

BPM_TIER_SPREAD_REWARD = 0.5   # Bonus for alternating ecstatic / processional / meditative


@dataclass
class ScheduledUpload:
    track_title:      str
    publish_at_utc:   datetime
    bpm:              int
    scripture_anchor: str
    reason:           str

    def as_publish_request(self) -> PublishRequest:
        return PublishRequest(
            track_title=self.track_title,
            publish_at_iso=self.publish_at_utc.isoformat().replace("+00:00", "Z"),
            dry_run=False,
        )


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _last_published_map() -> dict[str, datetime]:
    """Read the longform registry and return track_title -> most recent publish datetime."""
    path = registry.REGISTRY_FILE
    if not path.exists():
        return {}
    out: dict[str, datetime] = {}
    with open(path, "r") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("dry_run") or row.get("error"):
                continue
            title = row.get("track_title", "").lower().strip()
            ts_raw = row.get("timestamp", "")
            if not title or not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            prev = out.get(title)
            if prev is None or ts > prev:
                out[title] = ts
    return out


def _tier_for_bpm(bpm: int) -> str:
    if bpm <= 126:
        return "meditative"
    if bpm <= 132:
        return "processional"
    if bpm <= 138:
        return "gathering"
    return "ecstatic"


def _next_weekday(weekday: int, hour_utc: int, after: datetime) -> datetime:
    """Return next datetime on target weekday/hour strictly after `after`."""
    days_ahead = (weekday - after.weekday()) % 7
    candidate = (after + timedelta(days=days_ahead)).replace(
        hour=hour_utc, minute=0, second=0, microsecond=0,
    )
    if candidate <= after:
        candidate += timedelta(days=7)
    return candidate


# ─── Scoring ─────────────────────────────────────────────────────────────────

def _score_track(
    title: str,
    now: datetime,
    last_published: dict[str, datetime],
    already_selected_tiers: list[str],
) -> tuple[float, str]:
    """Return (score, reason) for a candidate track. Score ≤ 0 = ineligible."""
    key = title.lower()
    bpm = TRACK_BPMS.get(key, 0)
    if bpm == 0:
        return -1, f"no BPM in TRACK_BPMS for '{title}'"

    # Hard rotation lock
    last = last_published.get(key)
    if last is not None:
        days_since = (now - last).days
        if days_since < MIN_DAYS_BETWEEN_SAME_TRACK:
            return -1, f"published {days_since}d ago (min {MIN_DAYS_BETWEEN_SAME_TRACK})"

    score = TRACK_PRIORITY.get(key, 1.0)

    # Reward BPM tier spread across the week's schedule
    tier = _tier_for_bpm(bpm)
    if tier not in already_selected_tiers:
        score += BPM_TIER_SPREAD_REWARD

    # Decay for recency (even outside the hard lock, prefer tracks that haven't
    # been published in months)
    if last is not None:
        days_since = (now - last).days
        score += min(days_since / 90.0, 1.0)

    reason_parts = [f"priority={TRACK_PRIORITY.get(key, 1.0):.1f}"]
    if tier not in already_selected_tiers:
        reason_parts.append(f"+spread({tier})")
    if last is not None:
        reason_parts.append(f"last={(now - last).days}d")
    else:
        reason_parts.append("never published")
    return score, " ".join(reason_parts)


# ─── Public API ──────────────────────────────────────────────────────────────

def plan_week(
    now: Optional[datetime] = None,
    slots: int = WEEKLY_SLOTS,
    weekday: int = cfg.DEFAULT_PUBLISH_WEEKDAY,   # 3 = Thursday
    hour_utc: int = PUBLISH_HOUR_UTC_DEFAULT,
) -> list[ScheduledUpload]:
    """
    Produce a schedule for the coming week.

    Default cadence: Mon, Thu, Sat — three slots each at `hour_utc`. The
    `weekday`/`hour_utc` params adjust the anchor slot (Thu 17:00 UTC
    by default); Mon and Sat are derived by ±3 days.
    """
    now = now or datetime.now(timezone.utc)
    last_pub = _last_published_map()

    # Anchor slot (Thu) + offsets for the other two slots
    thu = _next_weekday(weekday, hour_utc, now)
    slot_datetimes = sorted({
        thu - timedelta(days=3),  # Mon 17:00
        thu,
        thu + timedelta(days=2),  # Sat 17:00
    })[:slots]

    catalogue = list(TRACK_BPMS.keys())
    selected: list[ScheduledUpload] = []
    selected_tiers: list[str] = []

    for slot_dt in slot_datetimes:
        candidates = []
        for title in catalogue:
            # Skip tracks already selected this week
            if any(s.track_title.lower() == title for s in selected):
                continue
            score, reason = _score_track(
                title=title,
                now=now,
                last_published=last_pub,
                already_selected_tiers=selected_tiers,
            )
            if score > 0:
                candidates.append((score, title, reason))

        if not candidates:
            logger.warning("No eligible tracks for slot %s", slot_dt)
            continue

        candidates.sort(reverse=True)
        best_score, best_title, best_reason = candidates[0]
        bpm = TRACK_BPMS[best_title]
        selected.append(ScheduledUpload(
            track_title=best_title,
            publish_at_utc=slot_dt,
            bpm=bpm,
            scripture_anchor=SCRIPTURE_ANCHORS.get(best_title, ""),
            reason=f"score={best_score:.2f} ({best_reason})",
        ))
        selected_tiers.append(_tier_for_bpm(bpm))

    return selected


def plan_to_requests(plans: list[ScheduledUpload]) -> list[PublishRequest]:
    """Convert a schedule to a list of PublishRequest objects."""
    return [s.as_publish_request() for s in plans]


def format_schedule(plans: list[ScheduledUpload]) -> str:
    """Pretty-print a schedule for CLI display."""
    if not plans:
        return "(no tracks eligible this week)"
    lines = ["Next-week schedule:"]
    for s in plans:
        tier = _tier_for_bpm(s.bpm)
        lines.append(
            f"  {s.publish_at_utc.strftime('%a %Y-%m-%d %H:%M UTC')}  "
            f"{s.track_title:<20} ({s.bpm} BPM, {tier:<12})  "
            f"anchor={s.scripture_anchor or '(none)':<10} "
            f"→ {s.reason}"
        )
    return "\n".join(lines)
