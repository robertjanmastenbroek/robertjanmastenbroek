"""
RJM Outreach Agent — YouTube Channel Discovery

Finds music-promo YouTube channels that match RJM's Progressive/Tribal Psytrance
aesthetic, extracts their business emails, and writes qualified channels into
the contacts DB with type='youtube' for the existing outreach agent to pick up.

Strategy (see docs/superpowers/specs/2026-04-14-youtube-outreach-branch-design.md):

  Pass 1 — search.list over YOUTUBE_DISCOVERY_QUERIES       (~1500 units)
  Pass 2 — channels.list(snippet,statistics,brandingSettings) (~15 units)
  Pass 3 — playlistItems.list on each uploads playlist       (~300 units)

  Email extraction: description regex → linked site scrape → Linktree fallback.

  Qualification: 10K–500K subs, last upload within 30 days, genre keyword match,
  reject artist-owned channels.

  All writes are idempotent on youtube_channel_id (see db.add_youtube_contact).

Read-only — uses YOUTUBE_API_KEY env var, no OAuth. Write ops (upload, comment)
require OAuth and belong to content_engine/distributor.py.

Entry point: run_discovery() — called by `python3 rjm.py youtube discover`.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

import db
from config import (
    YOUTUBE_API_DAILY_UNITS_CAP,
    YOUTUBE_ARTIST_CHANNEL_BLOCKLIST,
    YOUTUBE_ARTIST_CHANNEL_MARKERS,
    YOUTUBE_DISCOVERY_QUERIES,
    YOUTUBE_GENRE_KEYWORDS,
    YOUTUBE_MAX_SUBS,
    YOUTUBE_MAX_UPLOAD_AGE_DAYS,
    YOUTUBE_MIN_SUBS,
    YOUTUBE_MIN_TOTAL_VIEWS,
    YOUTUBE_MIN_VIDEO_COUNT,
)

log = logging.getLogger("outreach.youtube_discover")

# ─── Constants ────────────────────────────────────────────────────────────────
_YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
_HTTP_TIMEOUT = 15
_USER_AGENT = "RJM-Outreach/1.0 (+https://robertjanmastenbroek.com)"
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s\)<>\"']+")
_PREFERRED_EMAIL_PREFIXES = (
    "business", "promo", "submissions", "submission", "contact",
    "demo", "music", "hello", "info", "booking", "management",
)
_BLOCKED_HOSTS = (
    "instagram.com", "tiktok.com", "twitter.com", "x.com", "facebook.com",
    "threads.net", "soundcloud.com", "spotify.com", "apple.com", "youtube.com",
)
# API unit costs (YouTube Data API v3)
_UNITS_SEARCH = 100          # per search.list call
_UNITS_CHANNELS_LIST = 1     # per channels.list call (one call covers up to 50 ids)
_UNITS_PLAYLIST_ITEMS = 1    # per playlistItems.list call

# HTTP rate limit for website-scrape fallback: max 1 request per host per 12s
_host_last_hit: dict[str, float] = {}
_HOST_MIN_INTERVAL_S = 12.0


# ─── API key ──────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "YOUTUBE_API_KEY env var not set. Get one from "
            "https://console.cloud.google.com → APIs → YouTube Data API v3"
        )
    return key


def _budget_check(units_about_to_spend: int) -> None:
    """Raise if this spend would exceed the daily cap."""
    used = db.get_api_units_today("youtube")
    if used + units_about_to_spend > YOUTUBE_API_DAILY_UNITS_CAP:
        raise RuntimeError(
            f"YouTube API budget exceeded: {used} + {units_about_to_spend} "
            f"> {YOUTUBE_API_DAILY_UNITS_CAP} units/day"
        )


def _record_units(units: int) -> None:
    db.record_api_units("youtube", units)


# ─── API wrappers ─────────────────────────────────────────────────────────────

def _search_channels(query: str, *, max_results: int = 50, page_token: str = "") -> dict:
    """Run one search.list call and return the parsed JSON."""
    _budget_check(_UNITS_SEARCH)
    params = {
        "key": _get_api_key(),
        "part": "snippet",
        "type": "channel",
        "q": query,
        "maxResults": min(max_results, 50),
        "relevanceLanguage": "en",
    }
    if page_token:
        params["pageToken"] = page_token
    resp = requests.get(f"{_YOUTUBE_API_BASE}/search", params=params, timeout=_HTTP_TIMEOUT)
    _record_units(_UNITS_SEARCH)
    resp.raise_for_status()
    return resp.json()


def _fetch_channels_batch(channel_ids: list[str]) -> list[dict]:
    """Fetch channels.list(snippet,statistics,brandingSettings,contentDetails) for up to 50 IDs."""
    if not channel_ids:
        return []
    _budget_check(_UNITS_CHANNELS_LIST)
    params = {
        "key": _get_api_key(),
        "part": "snippet,statistics,brandingSettings,contentDetails",
        "id": ",".join(channel_ids[:50]),
        "maxResults": 50,
    }
    resp = requests.get(f"{_YOUTUBE_API_BASE}/channels", params=params, timeout=_HTTP_TIMEOUT)
    _record_units(_UNITS_CHANNELS_LIST)
    resp.raise_for_status()
    return resp.json().get("items", [])


def _fetch_latest_upload(uploads_playlist_id: str) -> dict | None:
    """Fetch the single most-recent upload from an uploads playlist."""
    if not uploads_playlist_id:
        return None
    _budget_check(_UNITS_PLAYLIST_ITEMS)
    params = {
        "key": _get_api_key(),
        "part": "snippet",
        "playlistId": uploads_playlist_id,
        "maxResults": 1,
    }
    resp = requests.get(
        f"{_YOUTUBE_API_BASE}/playlistItems", params=params, timeout=_HTTP_TIMEOUT
    )
    _record_units(_UNITS_PLAYLIST_ITEMS)
    if resp.status_code != 200:
        return None
    items = resp.json().get("items", [])
    return items[0] if items else None


# ─── Qualification ────────────────────────────────────────────────────────────

def _genre_score(text: str) -> float:
    """
    Return a 0.0–1.0 score based on how many genre keywords appear in the text.
    1.0 = all keywords present, 0.0 = none.
    """
    if not text:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for kw in YOUTUBE_GENRE_KEYWORDS if kw in text_lower)
    return min(1.0, hits / max(3, len(YOUTUBE_GENRE_KEYWORDS) / 3))


def _is_artist_channel(snippet: dict, branding: dict) -> bool:
    """Detect YouTube artist-owned channels we shouldn't pitch."""
    title = (snippet.get("title", "") or "").strip().lower()
    description = (snippet.get("description", "") or "")
    if title in YOUTUBE_ARTIST_CHANNEL_BLOCKLIST:
        return True
    if title.endswith(" - topic"):
        return True
    for marker in YOUTUBE_ARTIST_CHANNEL_MARKERS:
        if marker.lower() in description.lower():
            return True
    return False


def _parse_iso_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _qualify(channel: dict, latest_upload: dict | None) -> tuple[bool, str, float]:
    """
    Return (qualified, rejection_reason, genre_match_score).
    `qualified=True` means the channel enters the outreach pipeline.
    """
    snippet = channel.get("snippet", {}) or {}
    stats = channel.get("statistics", {}) or {}
    branding = channel.get("brandingSettings", {}) or {}

    try:
        subs = int(stats.get("subscriberCount", 0) or 0)
        video_count = int(stats.get("videoCount", 0) or 0)
        total_views = int(stats.get("viewCount", 0) or 0)
    except (TypeError, ValueError):
        return False, "stats not numeric", 0.0

    # Subs window
    if subs < YOUTUBE_MIN_SUBS:
        return False, f"subs<{YOUTUBE_MIN_SUBS}", 0.0
    if subs > YOUTUBE_MAX_SUBS:
        return False, f"subs>{YOUTUBE_MAX_SUBS}", 0.0

    # Catalog depth
    if video_count < YOUTUBE_MIN_VIDEO_COUNT:
        return False, f"video_count<{YOUTUBE_MIN_VIDEO_COUNT}", 0.0
    if total_views < YOUTUBE_MIN_TOTAL_VIEWS:
        return False, f"total_views<{YOUTUBE_MIN_TOTAL_VIEWS}", 0.0

    # Recency
    if latest_upload:
        published_at = (latest_upload.get("snippet", {}) or {}).get("publishedAt", "")
        dt = _parse_iso_dt(published_at)
        if dt is None:
            return False, "latest upload date unparseable", 0.0
        age_days = (datetime.now(timezone.utc) - dt).days
        if age_days > YOUTUBE_MAX_UPLOAD_AGE_DAYS:
            return False, f"last_upload_age={age_days}d", 0.0
    else:
        return False, "no latest upload fetched", 0.0

    # Genre fit — check title + description
    text = f"{snippet.get('title', '')} {snippet.get('description', '')}"
    score = _genre_score(text)
    if score <= 0.0:
        return False, "no genre keyword match", 0.0

    # Artist-owned rejection
    if _is_artist_channel(snippet, branding):
        return False, "artist-owned channel", 0.0

    return True, "qualified", score


# ─── Email extraction ─────────────────────────────────────────────────────────

def _rank_emails(emails: list[str]) -> list[str]:
    """Sort emails so preferred prefixes come first, then stable alphabetical."""
    def key(e: str) -> tuple[int, str]:
        prefix = e.split("@", 1)[0].lower()
        rank = 0 if prefix in _PREFERRED_EMAIL_PREFIXES else 1
        return (rank, e)
    return sorted(dict.fromkeys(emails), key=key)  # dedup + sort


def _extract_emails_from_text(text: str) -> list[str]:
    if not text:
        return []
    return _rank_emails(_EMAIL_RE.findall(text))


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _throttle_host(host: str) -> None:
    now = time.time()
    last = _host_last_hit.get(host, 0.0)
    delta = now - last
    if delta < _HOST_MIN_INTERVAL_S:
        time.sleep(_HOST_MIN_INTERVAL_S - delta)
    _host_last_hit[host] = time.time()


def _scrape_email_from_url(url: str) -> str:
    """Fetch a URL and look for an email. Returns '' on failure."""
    host = _host_of(url)
    if not host or any(b in host for b in _BLOCKED_HOSTS):
        return ""
    _throttle_host(host)
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_HTTP_TIMEOUT,
            allow_redirects=True,
        )
    except Exception:
        return ""
    if resp.status_code != 200:
        return ""
    # Linktree/Beacons serialize data inside window.__NEXT_DATA__ or similar JSON blobs
    # — regex over the raw HTML catches both mailto: links and plain-text emails.
    emails = _extract_emails_from_text(resp.text[:200_000])  # cap at 200KB
    return emails[0] if emails else ""


def _extract_channel_email(snippet: dict) -> str:
    """
    Four-pass email extraction for a single channel's snippet.
      1. Direct email regex on description
      2. URL regex on description → scrape each surviving URL
      3. Linktree / Beacons special cases (same scrape path)
      4. Give up — caller writes channel with empty email and status='skip'
    """
    description = snippet.get("description", "") or ""

    # Pass 1 — direct email in description
    found = _extract_emails_from_text(description)
    if found:
        return found[0]

    # Pass 2 — scrape linked sites
    urls = _URL_RE.findall(description)
    # Dedup by host, keep order
    seen_hosts: set[str] = set()
    candidates: list[str] = []
    for u in urls:
        u = u.rstrip(").,;")
        host = _host_of(u)
        if host in seen_hosts:
            continue
        if any(b in host for b in _BLOCKED_HOSTS):
            continue
        seen_hosts.add(host)
        candidates.append(u)

    # Try at most 3 distinct hosts per channel to bound HTTP cost
    for u in candidates[:3]:
        email = _scrape_email_from_url(u)
        if email:
            return email

    return ""


# ─── Main discovery flow ──────────────────────────────────────────────────────

def _seed_channel_ids(queries: list[str], per_query: int) -> set[str]:
    """Pass 1 — run the seed queries and collect unique channel IDs."""
    ids: set[str] = set()
    for q in queries:
        try:
            data = _search_channels(q, max_results=per_query)
        except RuntimeError as e:
            log.warning("search aborted for %r: %s", q, e)
            break
        except Exception as e:
            log.warning("search failed for %r: %s", q, e)
            continue

        items = data.get("items", []) or []
        for item in items:
            cid = ((item.get("id") or {}).get("channelId")) or item.get("snippet", {}).get("channelId")
            if cid:
                ids.add(cid)
        log.info("search %r → %d channels (running total %d)", q, len(items), len(ids))
    return ids


def _enrich_channels_batched(channel_ids: list[str]) -> list[dict]:
    """Pass 2 — fetch full channel metadata in batches of 50."""
    out: list[dict] = []
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i : i + 50]
        try:
            out.extend(_fetch_channels_batch(batch))
        except RuntimeError as e:
            log.warning("enrich aborted: %s", e)
            break
        except Exception as e:
            log.warning("enrich batch failed (%d): %s", i, e)
    return out


def run_discovery(
    *,
    queries: list[str] | None = None,
    per_query: int = 50,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Full discovery cycle. Returns a summary dict:
      {found, enriched, qualified, with_email, inserted}
    dry_run=True: runs all passes but skips DB writes. Safe for development.
    """
    db.init_db()
    queries = queries or YOUTUBE_DISCOVERY_QUERIES

    # Pass 1 — seed
    log.info("Pass 1: seeding with %d queries (max %d per query)", len(queries), per_query)
    seed_ids = _seed_channel_ids(queries, per_query)
    log.info("Pass 1 complete: %d unique channel IDs", len(seed_ids))

    # Pass 2 — enrich
    log.info("Pass 2: enriching %d channels (channels.list)", len(seed_ids))
    enriched = _enrich_channels_batched(sorted(seed_ids))
    log.info("Pass 2 complete: %d channels enriched", len(enriched))

    # Pass 3 — qualification + recency
    summary = {
        "found": len(seed_ids),
        "enriched": len(enriched),
        "qualified": 0,
        "with_email": 0,
        "inserted": 0,
        "skipped_no_email": 0,
        "rejected": 0,
    }
    rejections: dict[str, int] = {}

    for ch in enriched:
        snippet = ch.get("snippet", {}) or {}
        content_details = ch.get("contentDetails", {}) or {}
        related_playlists = (content_details.get("relatedPlaylists") or {})
        uploads_playlist_id = related_playlists.get("uploads", "")

        try:
            latest = _fetch_latest_upload(uploads_playlist_id)
        except RuntimeError as e:
            log.warning("playlistItems aborted: %s", e)
            break
        except Exception as e:
            log.warning("playlistItems failed for %s: %s", ch.get("id"), e)
            latest = None

        qualified, reason, score = _qualify(ch, latest)
        if not qualified:
            summary["rejected"] += 1
            rejections[reason] = rejections.get(reason, 0) + 1
            continue
        summary["qualified"] += 1

        email = _extract_channel_email(snippet)
        if email:
            summary["with_email"] += 1
        else:
            summary["skipped_no_email"] += 1

        # Extract fields for DB write
        latest_snippet = (latest or {}).get("snippet", {}) if latest else {}
        record = {
            "email": email,
            "name": snippet.get("title", "") or "",
            "channel_id": ch.get("id", "") or "",
            "channel_url": f"https://www.youtube.com/channel/{ch.get('id', '')}",
            "subs": int((ch.get("statistics", {}) or {}).get("subscriberCount", 0) or 0),
            "video_count": int((ch.get("statistics", {}) or {}).get("videoCount", 0) or 0),
            "last_upload_at": latest_snippet.get("publishedAt", "") if latest_snippet else "",
            "genre_match_score": score,
            "recent_upload_title": latest_snippet.get("title", "") if latest_snippet else "",
            "genre": "psytrance tribal progressive techno",  # fixed — youtube ctype bypasses this
            "notes": f"{snippet.get('title', '')}: {snippet.get('description', '')[:200]}",
        }

        if dry_run:
            continue

        ok, reason = db.add_youtube_contact(**record)
        if ok:
            summary["inserted"] += 1

    log.info("Discovery summary: %s", summary)
    if rejections:
        log.info("Rejection reasons: %s", dict(sorted(rejections.items(), key=lambda x: -x[1])))

    units_used_today = db.get_api_units_today("youtube")
    summary["api_units_used_today"] = units_used_today
    return summary


# ─── CLI entry point ──────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Discover YouTube promo channels for RJM outreach")
    parser.add_argument("--dry-run", action="store_true", help="Run full pipeline without DB writes")
    parser.add_argument(
        "--per-query", type=int, default=50,
        help="Max results per search query (1-50). Lower = cheaper, useful for dev.",
    )
    parser.add_argument(
        "--queries", nargs="*", default=None,
        help="Override YOUTUBE_DISCOVERY_QUERIES with custom terms.",
    )
    args = parser.parse_args()

    try:
        summary = run_discovery(
            queries=args.queries,
            per_query=max(1, min(50, args.per_query)),
            dry_run=args.dry_run,
        )
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        log.exception("discovery failed")
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print("\n=== YouTube Discovery Summary ===")
    for k, v in summary.items():
        print(f"  {k:<24} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
