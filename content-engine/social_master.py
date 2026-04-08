"""
Social master — reads content engine output and schedules posts via Buffer.

Usage:
  # Schedule posts from a completed content run:
  python3 social_master.py ~/Desktop/rjm_content_2026-04-08_1200/

  # Dry run (show what would be posted, don't actually post):
  python3 social_master.py ~/Desktop/rjm_content_2026-04-08_1200/ --dry-run

  # Override schedule time:
  python3 social_master.py ~/Desktop/rjm_content_2026-04-08_1200/ --schedule "2026-04-11T19:00:00+01:00"

Environment:
  BUFFER_ACCESS_TOKEN — Buffer OAuth access token
"""

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import publisher_buffer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# All clips go to all 3 platforms, every time.
ALL_PLATFORMS = ["tiktok", "instagram", "youtube"]

# ── Platform-aware posting schedule (CET) ────────────────────────────────────
# Research-backed peaks for music/electronic content creators:
#   TikTok:    Tue/Thu/Fri 18:00-21:00 (peak scroll + algorithm push)
#   Instagram: Wed/Thu/Fri 18:00-20:00 (Reels peak)
#   YouTube:   Thu/Fri 18:00, Sat 10:00 (Shorts discovery window)
#
# Slot assignment per clip length:
#   7s   → 19:00  (prime loop-bait slot — highest TikTok/IG traffic)
#   carousel → 12:30  (lunch scroll, lower competition than evening)
#   15s  → 15:00  (afternoon warm-up)
#   30s  → 18:30  (early prime — catches commuters)
#   60s  → 20:30  (peak engagement window — audience settled in)
#
# Day-of-week multiplier applied in _compute_daily_slots():
#   Mon 0.7 × → push slots 30 min later (lower organic reach day)
#   Tue 1.0 × → standard
#   Wed 1.1 × → pull slots 15 min earlier (rising traffic)
#   Thu 1.2 × → pull 30 min earlier (strongest weekday)
#   Fri 1.3 × → pull 30 min earlier + flag as priority day
#   Sat 0.6 × → push 45 min later (weekend morning lag)
#   Sun 0.5 × → push 60 min later (lowest organic reach)

DAILY_POST_SLOTS_CET = ["19:00", "12:30", "15:00", "18:30", "20:30"]

# Per-day offset in minutes: negative = earlier, positive = later
_DAY_OFFSETS = {0: 30, 1: 0, 2: -15, 3: -30, 4: -30, 5: 45, 6: 60}

# Clips that also get posted as Instagram Stories (in addition to feed)
STORIES_CLIP_LENGTHS = {"7", "60"}

# Caption hard limits
MAX_CAPTION_CHARS = 2200

# Days of week used when parsing best_posting_time
_DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Timezone aliases that appear in the captions file
_TZ_ALIASES = {
    "cet":  "Europe/Paris",
    "cest": "Europe/Paris",
    "utc":  "UTC",
    "gmt":  "UTC",
    "est":  "America/New_York",
    "pst":  "America/Los_Angeles",
}


# ---------------------------------------------------------------------------
# Captions parser
# ---------------------------------------------------------------------------

def parse_captions_file(captions_path: str) -> dict:
    """
    Parse a Holy Rave content-engine captions .txt file.

    Returns:
    {
      '7':  {'bucket': 'reach', 'platforms': {'tiktok': {...}, ...}, 'posting_time': 'Friday 7pm CET'},
      '15': {...},
      '30': {...},
    }

    The file has one global BEST POSTING TIME header and then per-clip sections.
    When a clip section overrides posting time via its own "BEST POSTING TIME:" line,
    that takes precedence.  Otherwise the global value is used.
    """
    text = Path(captions_path).read_text(encoding="utf-8")
    result: dict = {}

    # ── Global best posting time ─────────────────────────────────────────────
    global_posting_time = ""
    m = re.search(r"BEST POSTING TIME:\s*(.+)", text)
    if m:
        global_posting_time = m.group(1).strip()

    # ── Split on clip-length headers ─────────────────────────────────────────
    # Matches lines like:
    #   7-SECOND CLIP — File: rjm_event_7s.mp4
    #   15-SECOND CLIP — ...
    #   30-SECOND CLIP — ...
    clip_header_re = re.compile(
        r"[│┌└─]*\s*(\d+)-SECOND CLIP[^\n]*",
        re.IGNORECASE,
    )
    bucket_re = re.compile(r"BUCKET:\s*(\w+)", re.IGNORECASE)

    # Find all clip sections
    headers = list(clip_header_re.finditer(text))
    for i, header in enumerate(headers):
        length_str = header.group(1)
        start = header.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        section = text[start:end]

        # Bucket
        bm = bucket_re.search(section)
        bucket = bm.group(1).lower() if bm else "reach"

        # Per-clip posting time override
        local_pt_m = re.search(r"BEST POSTING TIME:\s*(.+)", section)
        posting_time = local_pt_m.group(1).strip() if local_pt_m else global_posting_time

        # Platform-specific blocks
        tiktok_data = _parse_tiktok_block(section)
        instagram_data = _parse_instagram_block(section)
        youtube_data = _parse_youtube_block(section)

        result[length_str] = {
            "bucket": bucket,
            "platforms": {
                "tiktok": tiktok_data,
                "instagram": instagram_data,
                "youtube": youtube_data,
            },
            "posting_time": posting_time,
        }

    return result


def _extract_block(section: str, header_pattern: str) -> str:
    """
    Extract text between a ━━━ HEADER ━━━ line and the next ━━━ or end of section.
    Returns the raw text of the block (may be empty string).
    """
    pat = re.compile(
        r"━+\s*" + re.escape(header_pattern) + r"\s*━*\n(.*?)(?=━+|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    m = pat.search(section)
    return m.group(1).strip() if m else ""


def _field(block: str, label: str) -> str:
    """Extract the first indented/non-empty line following a 'Label:' line."""
    pat = re.compile(r"^" + re.escape(label) + r":\s*\n((?:[ \t]+[^\n]+\n?)+)", re.MULTILINE)
    m = pat.search(block)
    if not m:
        return ""
    lines = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
    return " ".join(lines)


def _parse_tiktok_block(section: str) -> dict:
    block = _extract_block(section, "TIKTOK")
    return {
        "caption": _field(block, "Caption"),
        "hashtags": _field(block, "Hashtags"),
    }


def _parse_instagram_block(section: str) -> dict:
    block = _extract_block(section, "INSTAGRAM REELS")
    return {
        "caption": _field(block, "Caption"),
        "hashtags": _field(block, "Hashtags (paste in first comment)") or _field(block, "Hashtags"),
    }


def _parse_youtube_block(section: str) -> dict:
    block = _extract_block(section, "YOUTUBE SHORTS")
    return {
        "title": _field(block, "Title"),
        "description": _field(block, "Description"),
    }


# ---------------------------------------------------------------------------
# Profile resolver
# ---------------------------------------------------------------------------

def resolve_profiles(profiles: list[dict]) -> dict:
    """
    Build a service→[profile_id, ...] map from the Buffer profiles list.
    Buffer service names: 'tiktok', 'instagram', 'youtube', 'twitter', etc.

    Returns:
    {
      'tiktok':    ['<id>', ...],
      'instagram': ['<id>', ...],
      'youtube':   ['<id>', ...],
    }
    """
    mapping: dict[str, list[str]] = {"tiktok": [], "instagram": [], "youtube": []}
    for p in profiles:
        service = (p.get("service") or "").lower()
        pid = p.get("id", "")
        if not pid:
            continue
        if service in mapping:
            mapping[service].append(pid)
            logger.debug(
                f"Mapped {service} → @{p.get('service_username', '?')} ({pid})"
            )
    return mapping


# ---------------------------------------------------------------------------
# Scheduling helper
# ---------------------------------------------------------------------------

def _compute_daily_slots(base_date: Optional[str] = None) -> list[str]:
    """
    Return 5 ISO 8601 datetimes mapped to DAILY_POST_SLOTS_CET, adjusted for
    day-of-week algorithm patterns.

    Thu/Fri slots run 30 min earlier (peak algorithm days for music content).
    Mon/Sat/Sun slots run later (lower organic reach days).
    """
    try:
        tz = ZoneInfo("Europe/Paris")
    except ZoneInfoNotFoundError:
        tz = timezone.utc

    if base_date:
        day = datetime.strptime(base_date, "%Y-%m-%d").replace(tzinfo=tz)
    else:
        day = datetime.now(tz=tz)

    weekday = day.weekday()  # 0=Mon … 6=Sun
    offset_min = _DAY_OFFSETS.get(weekday, 0)

    if weekday == 4:  # Friday — log as priority posting day
        logger.info("Friday detected — priority posting day. Slots shifted 30 min earlier.")

    results = []
    for slot in DAILY_POST_SLOTS_CET:
        h, m = map(int, slot.split(":"))
        dt = day.replace(hour=h, minute=m, second=0, microsecond=0)
        dt += timedelta(minutes=offset_min)
        # If the slot is already past, move to tomorrow (same weekday offset won't apply)
        if dt <= datetime.now(tz=tz):
            dt += timedelta(days=1)
        results.append(dt.isoformat())

    logger.info(f"Posting slots ({['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][weekday]}, "
                f"offset {offset_min:+d}min): "
                + " | ".join(dt[11:16] for dt in results))
    return results


def _parse_posting_time(posting_time_str: str) -> Optional[str]:
    """
    Convert a human-readable posting time string like "Friday 7pm CET"
    into an ISO 8601 datetime string for the *next* occurrence from today.

    Returns None if the string cannot be parsed (falls back to Buffer queue).
    """
    if not posting_time_str:
        return None

    s = posting_time_str.strip().lower()

    # Extract day of week
    day_num = None
    for day_name, num in _DAY_MAP.items():
        if day_name in s:
            day_num = num
            break

    # Extract hour and am/pm
    time_m = re.search(r"(\d{1,2})\s*(am|pm)", s)
    if not time_m:
        logger.warning(f"Cannot parse hour from posting time: '{posting_time_str}'")
        return None

    hour = int(time_m.group(1))
    meridiem = time_m.group(2)
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    # Extract timezone
    tz_obj = timezone.utc  # default
    tz_m = re.search(r"\b(cet|cest|utc|gmt|est|pst)\b", s)
    if tz_m:
        tz_key = tz_m.group(1)
        tz_name = _TZ_ALIASES.get(tz_key, "UTC")
        try:
            tz_obj = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, KeyError):
            logger.warning(f"Unknown timezone '{tz_key}', defaulting to UTC")

    # Compute next occurrence from today
    now = datetime.now(tz=tz_obj)
    if day_num is None:
        # No day specified — use today at the given hour (or tomorrow if already past)
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
    else:
        days_ahead = (day_num - now.weekday()) % 7
        if days_ahead == 0:
            # Same day — check if time has already passed
            candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate <= now:
                days_ahead = 7
                candidate = now + timedelta(days=days_ahead)
                candidate = candidate.replace(hour=hour, minute=0, second=0, microsecond=0)
        else:
            candidate = now + timedelta(days=days_ahead)
            candidate = candidate.replace(hour=hour, minute=0, second=0, microsecond=0)

    iso = candidate.isoformat()
    logger.debug(f"Posting time '{posting_time_str}' → {iso}")
    return iso


def _truncate_caption(text: str, max_len: int = MAX_CAPTION_CHARS) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[: max_len - 3].rstrip() + "..."
    logger.warning(f"Caption truncated to {max_len} chars")
    return truncated


# ---------------------------------------------------------------------------
# Per-clip scheduler
# ---------------------------------------------------------------------------

def _extract_video_still(video_path: str, out_jpg: str, offset_ratio: float = 0.4) -> bool:
    """Extract a single frame from a video at offset_ratio of its duration."""
    import subprocess, json
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
            capture_output=True, text=True, timeout=30
        )
        duration = float(json.loads(probe.stdout).get("format", {}).get("duration", 5))
        t = duration * offset_ratio
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", video_path,
             "-vframes", "1", "-q:v", "2", out_jpg],
            capture_output=True, timeout=30
        )
        return result.returncode == 0 and os.path.exists(out_jpg)
    except Exception as exc:
        logger.warning(f"Could not extract still from {Path(video_path).name}: {exc}")
        return False


def schedule_clip(
    video_path: str,
    clip_length: str,
    captions_data: dict,
    profile_map: dict,
    scheduled_at: Optional[str],
    dry_run: bool,
) -> dict:
    """
    Schedule one clip across its target platforms.

    Returns a dict describing what was (or would be) posted:
    {
      'video': str,
      'length': str,
      'bucket': str,
      'platforms_posted': [...],
      'update_ids': {...},   # empty in dry-run
      'scheduled_at': str or None,
      'dry_run': bool,
    }
    """
    clip_data = captions_data.get(clip_length, {})
    bucket = clip_data.get("bucket", "reach")
    platforms = clip_data.get("platforms", {})

    # resolved_time comes from caller (pre-computed daily slot) or captions file fallback
    resolved_time = scheduled_at
    if resolved_time is None:
        posting_time_str = clip_data.get("posting_time", "")
        resolved_time = _parse_posting_time(posting_time_str)

    # Everything goes to all 3 platforms
    target_platforms: list[str] = ALL_PLATFORMS

    # Captions per platform
    tiktok_p = platforms.get("tiktok", {})
    ig_p = platforms.get("instagram", {})
    yt_p = platforms.get("youtube", {})

    tiktok_caption = _truncate_caption(
        f"{tiktok_p.get('caption', '')}\n\n{tiktok_p.get('hashtags', '')}".strip()
    )
    ig_caption = _truncate_caption(
        f"{ig_p.get('caption', '')}\n\n{ig_p.get('hashtags', '')}".strip()
    )
    yt_text = _truncate_caption(
        f"{yt_p.get('title', '')}\n\n{yt_p.get('description', '')}".strip()
    )

    summary = {
        "video": video_path,
        "length": clip_length,
        "bucket": bucket,
        "platforms_posted": [],
        "update_ids": {},
        "scheduled_at": resolved_time,
        "dry_run": dry_run,
    }

    if not os.path.isfile(video_path):
        logger.warning(f"Video file missing — skipping: {video_path}")
        summary["error"] = "file_not_found"
        return summary

    # ── TikTok + Instagram (posted together to the same Buffer call if both targeted) ──
    social_platforms = [p for p in ["tiktok", "instagram"] if p in target_platforms]
    social_ids: list[str] = []
    for platform in social_platforms:
        social_ids.extend(profile_map.get(platform, []))

    if social_ids:
        # Use TikTok caption for TikTok profiles; IG caption for IG profiles.
        # Buffer doesn't support per-profile captions in a single call, so we
        # send two separate calls when captions differ, or one combined call
        # when both platforms are in the same bucket and have the same caption.
        tiktok_ids = [pid for p in ["tiktok"] if p in social_platforms
                      for pid in profile_map.get(p, [])]
        ig_ids = [pid for p in ["instagram"] if p in social_platforms
                  for pid in profile_map.get(p, [])]

        # TikTok post
        if tiktok_ids:
            if dry_run:
                logger.info(
                    f"[DRY RUN] TikTok — {Path(video_path).name}\n"
                    f"  Caption: {tiktok_caption[:120]}{'…' if len(tiktok_caption) > 120 else ''}\n"
                    f"  Scheduled: {resolved_time or 'queue'}"
                )
                summary["platforms_posted"].append("tiktok")
            else:
                try:
                    ids = publisher_buffer.upload_video(
                        video_path, tiktok_caption, tiktok_ids, resolved_time
                    )
                    summary["update_ids"].update(ids)
                    summary["platforms_posted"].append("tiktok")
                except Exception as exc:
                    logger.error(f"TikTok upload failed for {video_path}: {exc}")
                    summary["errors"] = summary.get("errors", []) + [f"tiktok: {exc}"]

        # Instagram post
        if ig_ids:
            if dry_run:
                logger.info(
                    f"[DRY RUN] Instagram — {Path(video_path).name}\n"
                    f"  Caption: {ig_caption[:120]}{'…' if len(ig_caption) > 120 else ''}\n"
                    f"  Scheduled: {resolved_time or 'queue'}"
                )
                summary["platforms_posted"].append("instagram")
            else:
                try:
                    ids = publisher_buffer.upload_video(
                        video_path, ig_caption, ig_ids, resolved_time
                    )
                    summary["update_ids"].update(ids)
                    summary["platforms_posted"].append("instagram")
                except Exception as exc:
                    logger.error(f"Instagram upload failed for {video_path}: {exc}")
                    summary["errors"] = summary.get("errors", []) + [f"instagram: {exc}"]

    # ── YouTube (separate post — category 10 = Music) ──────────────────────
    if "youtube" in target_platforms:
        yt_ids = profile_map.get("youtube", [])
        if yt_ids:
            if dry_run:
                logger.info(
                    f"[DRY RUN] YouTube — {Path(video_path).name}\n"
                    f"  Title+desc: {yt_text[:120]}{'…' if len(yt_text) > 120 else ''}\n"
                    f"  Category: Music (10)  |  Scheduled: {resolved_time or 'queue'}"
                )
                summary["platforms_posted"].append("youtube")
            else:
                try:
                    ids = publisher_buffer.upload_video(
                        video_path, yt_text, yt_ids, resolved_time,
                        youtube_profile_ids=yt_ids,
                    )
                    summary["update_ids"].update(ids)
                    summary["platforms_posted"].append("youtube")
                except Exception as exc:
                    logger.error(f"YouTube upload failed for {video_path}: {exc}")
                    summary["errors"] = summary.get("errors", []) + [f"youtube: {exc}"]
        else:
            logger.debug("No YouTube profile connected in Buffer — skipping YouTube post")

    # ── 60s clip: remind about Spotify sticker on Stories ───────────────────
    if clip_length == "60" and not dry_run:
        logger.info(
            f"  NOTE: {Path(video_path).name} also posting as IG Story — "
            f"add Spotify sticker manually after it goes live."
        )

    return summary


# ---------------------------------------------------------------------------
# Output directory scanner
# ---------------------------------------------------------------------------

def find_content_files(output_dir: str) -> dict:
    """
    Scan output_dir for clip files and matching captions file.

    Returns:
    {
      'clips': {'7': '/path/to/rjm_event_7s.mp4', '15': ..., '30': ..., '60': ...},
      'captions': '/path/to/rjm_event_captions.txt' or None,
    }
    """
    d = Path(output_dir)
    if not d.is_dir():
        raise NotADirectoryError(f"Output directory not found: {output_dir}")

    clips: dict[str, str] = {}
    for length in ["7", "15", "30", "60"]:
        matches = list(d.glob(f"*_{length}s.mp4"))
        if matches:
            clips[length] = str(matches[0])
            if len(matches) > 1:
                logger.warning(f"Multiple {length}s clips found — using {matches[0].name}")

    captions_matches = list(d.glob("*_captions.txt"))
    captions_path = str(captions_matches[0]) if captions_matches else None
    if not captions_path:
        logger.warning("No *_captions.txt found in output directory")

    return {"clips": clips, "captions": captions_path}


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def _print_summary(results: list[dict], dry_run: bool) -> None:
    label = "DRY-RUN PREVIEW" if dry_run else "POSTING SUMMARY"
    print()
    print(f"{'═' * 60}")
    print(f"  HOLY RAVE BUFFER {label}")
    print(f"{'═' * 60}")
    print(f"  {'CLIP':<12} {'BUCKET':<10} {'PLATFORMS':<30} {'SCHEDULED'}")
    print(f"  {'─' * 56}")
    for r in results:
        clip_name = Path(r["video"]).name if r.get("video") else "?"
        bucket = r.get("bucket", "?")
        platforms = ", ".join(r.get("platforms_posted", [])) or "—"
        sched = r.get("scheduled_at") or "queue"
        error = r.get("error") or ""
        if error:
            platforms = f"ERROR: {error}"
        print(f"  {clip_name:<12} {bucket:<10} {platforms:<30} {sched}")
    print(f"{'═' * 60}")
    if dry_run:
        print("  Nothing was posted. Remove --dry-run to publish.")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _schedule_stories(video_path: str, ig_ids: list[str],
                      scheduled_at: Optional[str], dry_run: bool) -> None:
    """Post a clip as an Instagram Story via Buffer."""
    if not ig_ids:
        return
    clip_name = Path(video_path).name
    if dry_run:
        logger.info(f"[DRY RUN] IG Story — {clip_name}  scheduled: {scheduled_at or 'queue'}")
        return
    try:
        publisher_buffer.upload_story(video_path, ig_ids, scheduled_at)
        logger.info(f"IG Story queued — {clip_name}")
    except Exception as exc:
        logger.warning(f"IG Story failed for {clip_name} (non-fatal): {exc}")


def _schedule_carousel(output_dir: str, clips: dict, profile_map: dict,
                       scheduled_at: Optional[str], dry_run: bool) -> Optional[dict]:
    """
    Generate a carousel/image post from video stills and schedule it via Buffer.

    Strategy:
    1. Check output_dir for existing carousel images (*.png or *.jpg not ending in s.mp4)
    2. If none found, extract one still from each clip and run carousel.py
    3. Post to TikTok + Instagram (carousels don't work on YouTube Shorts)
    """
    import tempfile, subprocess

    all_ids = (profile_map.get("tiktok", []) + profile_map.get("instagram", []))
    if not all_ids:
        return None

    d = Path(output_dir)
    caption = (
        "Every week. Sacred music on the dancefloor.\n"
        "Sunset Sessions — free, weekly, open to everyone.\n\n"
        "#holyrave #sunsetsessions #melodictechno #tenerife "
        "#electronicworship #sacredmusic #ancienttruthfuturesound"
    )

    # 1. Check for existing carousel slides
    existing_slides = sorted(d.glob("*_slide_*.png")) + sorted(d.glob("*_slide_*.jpg"))
    if existing_slides:
        slide_paths = [str(p) for p in existing_slides[:10]]
        logger.info(f"Using {len(slide_paths)} existing carousel slides")
    else:
        # 2. Extract stills from each clip
        work_dir = tempfile.mkdtemp(prefix="carousel_stills_")
        still_paths = []
        for length, vpath in sorted(clips.items(), key=lambda x: int(x[0])):
            out_jpg = os.path.join(work_dir, f"still_{length}s.jpg")
            if _extract_video_still(vpath, out_jpg):
                still_paths.append(out_jpg)

        if not still_paths:
            logger.warning("Could not extract any stills for carousel — skipping")
            return None

        # 3. Try carousel.py if available
        slide_paths = still_paths  # fallback: use raw stills
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            import carousel as carousel_module
            slides_dir = os.path.join(work_dir, "slides")
            os.makedirs(slides_dir, exist_ok=True)
            generated = carousel_module.generate_carousel(
                carousel_type="event_recap",
                output_dir=slides_dir,
                title="Holy Rave — Sunset Sessions",
                photo_paths=still_paths,
                base_name="rjm_carousel",
            )
            if generated:
                slide_paths = generated
                logger.info(f"Generated {len(slide_paths)} branded carousel slides")
        except Exception as exc:
            logger.warning(f"carousel.py failed ({exc}) — using raw stills instead")

    if not slide_paths:
        return None

    result = {
        "type": "carousel",
        "slides": len(slide_paths),
        "platforms_posted": [],
        "scheduled_at": scheduled_at,
        "dry_run": dry_run,
    }

    if dry_run:
        logger.info(
            f"[DRY RUN] Carousel — {len(slide_paths)} slides → TikTok + Instagram\n"
            f"  Scheduled: {scheduled_at or 'queue'}"
        )
        result["platforms_posted"] = ["tiktok", "instagram"]
        return result

    try:
        ids = publisher_buffer.upload_carousel(slide_paths, caption, all_ids, scheduled_at)
        result["update_ids"] = ids
        result["platforms_posted"] = ["tiktok", "instagram"]
        logger.info(f"Carousel queued — {len(slide_paths)} slides to TikTok + Instagram")
    except Exception as exc:
        logger.error(f"Carousel upload failed: {exc}")
        result["error"] = str(exc)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Schedule RJM content engine output via Buffer — 5 posts/day",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Daily schedule (CET):\n"
            "  08:00 — 7s  REACH clip      → TikTok + IG + YT + IG Story\n"
            "  12:00 — Carousel/image      → TikTok + IG\n"
            "  15:00 — 15s REACH clip      → TikTok + IG + YT\n"
            "  18:00 — 30s clip            → TikTok + IG + YT\n"
            "  20:00 — 60s SPOTIFY clip    → TikTok + IG + YT + IG Story\n"
        ),
    )
    parser.add_argument(
        "output_dir",
        help="Path to content engine output folder (e.g. ~/Desktop/rjm_content_2026-04-08_1200/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be posted without making any API calls",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Post date for the daily schedule (default: today/tomorrow if slots already passed)",
    )
    args = parser.parse_args()

    output_dir = os.path.expanduser(args.output_dir)
    dry_run: bool = args.dry_run

    if not dry_run:
        try:
            publisher_buffer._token()
        except EnvironmentError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        # Validate token is accepted before encoding/uploading anything
        if not publisher_buffer.validate_token():
            print(
                "\nERROR: Buffer token invalid. Run:  python3 buffer_auth.py\n"
                "to get a proper OAuth token, then re-run.",
                file=sys.stderr,
            )
            sys.exit(1)

    # ── Scan output folder ──────────────────────────────────────────────────
    try:
        content = find_content_files(output_dir)
    except NotADirectoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    clips = content["clips"]
    captions_path = content["captions"]

    if not clips:
        print(f"ERROR: No clip files found in {output_dir}", file=sys.stderr)
        sys.exit(1)

    logger.info(f"Found {len(clips)} clip(s): {', '.join(sorted(clips.keys(), key=int))}s")
    if captions_path:
        logger.info(f"Captions: {Path(captions_path).name}")

    # ── Parse captions ──────────────────────────────────────────────────────
    captions_data: dict = {}
    if captions_path:
        try:
            captions_data = parse_captions_file(captions_path)
        except Exception as exc:
            logger.warning(f"Failed to parse captions: {exc} — posting without captions")

    # ── Load Buffer profiles ────────────────────────────────────────────────
    profile_map: dict[str, list[str]] = {"tiktok": [], "instagram": [], "youtube": []}
    if not dry_run:
        try:
            profiles = publisher_buffer.get_profiles()
            profile_map = resolve_profiles(profiles)
        except Exception as exc:
            print(f"ERROR loading Buffer profiles: {exc}", file=sys.stderr)
            sys.exit(1)
        connected = [p for p, ids in profile_map.items() if ids]
        if not connected:
            print("ERROR: No TikTok, Instagram, or YouTube profiles in Buffer.", file=sys.stderr)
            sys.exit(1)
        logger.info(f"Buffer connected: {connected}")
    else:
        profile_map = {
            "tiktok":    ["dry-run-tiktok-id"],
            "instagram": ["dry-run-instagram-id"],
            "youtube":   ["dry-run-youtube-id"],
        }

    # ── Compute daily time slots ────────────────────────────────────────────
    # Slot assignment (index):  0=7s  1=carousel  2=15s  3=30s  4=60s
    slots = _compute_daily_slots(args.date)
    slot_map = {"7": slots[0], "carousel": slots[1], "15": slots[2],
                "30": slots[3], "60": slots[4]}

    logger.info("Daily schedule:")
    labels = {"7": "7s  (REACH)", "carousel": "Carousel  ", "15": "15s (REACH)",
              "30": "30s (ALL)  ", "60": "60s (SPOTIFY)"}
    for key in ["7", "carousel", "15", "30", "60"]:
        logger.info(f"  {labels[key]} → {slot_map[key]}")

    # ── Schedule video clips ────────────────────────────────────────────────
    results: list[dict] = []
    ig_ids = profile_map.get("instagram", [])

    for length in ["7", "15", "30", "60"]:
        if length not in clips:
            logger.warning(f"No {length}s clip found — skipping slot")
            continue

        result = schedule_clip(
            video_path=clips[length],
            clip_length=length,
            captions_data=captions_data,
            profile_map=profile_map,
            scheduled_at=slot_map[length],
            dry_run=dry_run,
        )
        results.append(result)

        # Also post 7s and 60s as IG Stories (same time slot)
        if length in STORIES_CLIP_LENGTHS:
            _schedule_stories(clips[length], ig_ids, slot_map[length], dry_run)

    # ── Schedule carousel (slot 1 = 12:00) ─────────────────────────────────
    carousel_result = _schedule_carousel(
        output_dir=output_dir,
        clips=clips,
        profile_map=profile_map,
        scheduled_at=slot_map["carousel"],
        dry_run=dry_run,
    )
    if carousel_result:
        results.append({**carousel_result, "video": "carousel", "length": "img",
                        "bucket": "reach"})

    _print_summary(results, dry_run)


def sync_analytics() -> None:
    """
    Pull Buffer post analytics, match posts to hook database entries by text,
    and update performance scores so the best hooks surface first next run.

    Run this periodically (e.g. weekly) after posts have accumulated views.

    Usage:  python3 social_master.py --sync-analytics
    """
    try:
        publisher_buffer._token()
    except EnvironmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        import hook_database
        hook_db_available = True
    except ImportError:
        hook_db_available = False
        logger.warning("hook_database.py not found — analytics will be shown but not saved")

    print("\n━━━ BUFFER ANALYTICS SYNC ━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    profiles = publisher_buffer.get_profiles()
    records = publisher_buffer.get_analytics_summary(profiles)

    if not records:
        print("No analytics data found yet — posts may not have enough history.")
        return

    # Print top performers
    print(f"\nTop performing posts (by engagement score):\n")
    print(f"  {'Score':>8}  {'Platform':<12} {'Views':>7} {'Likes':>6} {'Shares':>7}  Text")
    print(f"  {'─'*70}")
    for r in records[:20]:
        text_preview = r["text"][:55].replace("\n", " ")
        print(
            f"  {r['engagement_score']:>8.2f}  {r['service']:<12} "
            f"{r['views']:>7,} {r['likes']:>6,} {r['shares']:>7,}  {text_preview}"
        )

    # Sync scores back to hook database
    if hook_db_available:
        synced = 0
        for r in records:
            if r["views"] < 10:
                continue  # not enough data
            # Try to match the post text to a hook in the database
            # The hook text appears in the first ~100 chars of TikTok/IG posts
            text_upper = r["text"].upper()
            try:
                # Search all hooks to find a match
                conn = hook_database.sqlite3.connect(str(hook_database.DB_PATH))
                cursor = conn.execute(
                    "SELECT hook_text FROM hooks WHERE UPPER(?) LIKE '%' || UPPER(hook_text) || '%' LIMIT 1",
                    (r["text"][:300],)
                )
                row = cursor.fetchone()
                conn.close()
                if row:
                    hook_database.record_performance(
                        hook_text=row[0],
                        views=r["views"],
                        likes=r["likes"],
                        shares=r["shares"],
                    )
                    synced += 1
            except Exception as exc:
                logger.debug(f"Hook match failed for post: {exc}")

        print(f"\n✅ Synced {synced} post scores → hook database")
        print("   Best-performing hooks will now surface first in future runs.\n")
    else:
        print("\n(Install hook_database.py to save these scores for future hook selection.)\n")

    # Show what's working at a pattern level
    if hook_db_available:
        try:
            best = hook_database.get_best_hooks(limit=5)
            if best:
                print("Current top hooks by performance score:")
                for h in best:
                    score = h.get("performance_score") or 0
                    print(f"  [{score:.1f}] {h['hook_text'][:80]}")
            print()
        except Exception:
            pass


if __name__ == "__main__":
    import sys as _sys
    if "--sync-analytics" in _sys.argv:
        sync_analytics()
    else:
        main()
