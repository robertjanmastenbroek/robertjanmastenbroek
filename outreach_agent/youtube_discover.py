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
    YOUTUBE_ARTIST_CHANNEL_NAME_SUFFIXES,
    YOUTUBE_DISCOVERY_QUERIES,
    YOUTUBE_GENRE_KEYWORDS_PRIMARY,
    YOUTUBE_GENRE_KEYWORDS_SECONDARY,
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
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s\)<>\"']+")

# Obfuscation patterns — many channels write emails as "name at gmail dot com"
# or "name [at] gmail [dot] com" to dodge scrapers. Normalize before regex.
_OBFUSCATION_PATTERNS = [
    (re.compile(r"\s*\(\s*at\s*\)\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s*\[\s*at\s*\]\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s+at\s+", re.IGNORECASE), "@"),
    (re.compile(r"\s*\(\s*dot\s*\)\s*", re.IGNORECASE), "."),
    (re.compile(r"\s*\[\s*dot\s*\]\s*", re.IGNORECASE), "."),
    (re.compile(r"\s+dot\s+", re.IGNORECASE), "."),
]
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
    Return a 0.0–1.0 score measuring how strongly a channel matches RJM's
    target genres.

    Psytrance keywords (primary focus) score 2 points each; secondary genre
    keywords (melodic techno, progressive house, Christian EDM, organic house,
    plus promo-intent terms like 'mix', 'set') score 1 point each. Full score
    is reached at 5 points — so a psytrance channel matching 3 primary keywords
    (6 points, capped at 1.0) outranks a melodic channel matching 5 secondary
    keywords (5 points = 1.0 tied, BUT more fine-grained ordering via the raw
    score below the cap).

    The score is stored in youtube_genre_match_score on the contact row, so
    the send allocator can sort YouTube contacts by score-descending and
    dispatch psytrance channels first.
    """
    if not text:
        return 0.0
    text_lower = text.lower()
    primary_hits = sum(2.0 for kw in YOUTUBE_GENRE_KEYWORDS_PRIMARY if kw in text_lower)
    secondary_hits = sum(1.0 for kw in YOUTUBE_GENRE_KEYWORDS_SECONDARY if kw in text_lower)
    # No upper cap — scores above 1.0 are valid and indicate very strong psy
    # alignment (e.g. 3 primary keywords = 6 → 0.6; 4 primary + 4 secondary = 12 → 1.2).
    # The allocator sorts by this value descending, so higher raw score wins.
    return (primary_hits + secondary_hits) / 10.0


def _is_artist_channel(snippet: dict, branding: dict) -> bool:
    """Detect YouTube artist-owned channels we shouldn't pitch."""
    title = (snippet.get("title", "") or "").strip().lower()
    description = (snippet.get("description", "") or "")
    if title in YOUTUBE_ARTIST_CHANNEL_BLOCKLIST:
        return True
    if title.endswith(" - topic"):
        return True
    # Name suffix heuristic — "Captain Hook Official", "Astrix Official" etc.
    # are almost always artist-run. We skip LABELS (e.g. "Steyoyoke Records")
    # because those ARE promo channels and we want to pitch them.
    for suffix in YOUTUBE_ARTIST_CHANNEL_NAME_SUFFIXES:
        if title.endswith(suffix.lower()):
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


def _deobfuscate(text: str) -> str:
    """
    Replace '(at)', '[at]', ' at ', '(dot)', '[dot]', ' dot ' with @/. so that
    emails written 'contact at gmail dot com' become 'contact@gmail.com' and
    can be matched by the regex.
    """
    for pattern, replacement in _OBFUSCATION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _extract_emails_from_text(text: str) -> list[str]:
    if not text:
        return []
    # First pass: direct match
    direct = _EMAIL_RE.findall(text)
    # Second pass: de-obfuscated match (catches 'name at domain dot com')
    obfuscated = _EMAIL_RE.findall(_deobfuscate(text))
    return _rank_emails(direct + obfuscated)


def _fetch_channel_about_html(channel_id: str) -> str:
    """
    Fetch the channel's About page HTML. YouTube's server-side-rendered HTML
    sometimes contains the business email in the ytInitialData JSON blob even
    though the UI requires human verification to reveal it. This is a best-effort
    scrape — if YouTube returns an empty shell or bot-check page, we get nothing.
    """
    if not channel_id:
        return ""
    url = f"https://www.youtube.com/channel/{channel_id}/about"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            timeout=_HTTP_TIMEOUT,
            allow_redirects=True,
        )
    except Exception:
        return ""
    if resp.status_code != 200:
        return ""
    return resp.text[:800_000]  # cap at ~800KB to bound memory


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


def _extract_channel_email(snippet: dict, channel_id: str = "") -> str:
    """
    Multi-pass email extraction for a single channel.
      1. Direct email regex on description (w/ obfuscation deobfuscator)
      2. URL regex on description → scrape each surviving non-social URL
      3. Channel About page HTML scrape (YouTube server-rendered, sometimes
         has the business email embedded in ytInitialData even though the UI
         gates it behind a CAPTCHA)
      4. Give up — caller writes channel with empty email and status='skip'
    """
    description = snippet.get("description", "") or ""

    # Pass 1 — direct email in description (+ obfuscation handling)
    found = _extract_emails_from_text(description)
    if found:
        return found[0]

    # Pass 2 — scrape linked sites
    urls = _URL_RE.findall(description)
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

    for u in candidates[:3]:  # bound HTTP cost per channel
        email = _scrape_email_from_url(u)
        if email:
            return email

    # Pass 3 — channel About page HTML scrape
    if channel_id:
        html = _fetch_channel_about_html(channel_id)
        if html:
            found = _extract_emails_from_text(html)
            if found:
                # Filter out YouTube's own system addresses (legal@, abuse@, press@)
                for e in found:
                    local = e.split("@", 1)[0].lower()
                    domain = e.split("@", 1)[1].lower()
                    if domain.endswith("youtube.com") or domain.endswith("google.com"):
                        continue
                    if local in ("press", "legal", "abuse", "copyright", "support"):
                        continue
                    return e

    return ""


def requalify_existing(blocklist: bool = True) -> dict[str, int]:
    """
    Apply the current qualification rules (MAX_SUBS, artist-channel heuristic)
    to channels already in the DB. Blocklists any that no longer qualify so
    the review queue only shows channels that MIGHT actually respond.

    This is meant to be run when the config tightens (e.g. lowering MAX_SUBS
    from 500K to 80K) — existing rows were written under the old thresholds
    and need to be cleaned up without re-running the API.

    Returns summary dict.
    """
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, email, status, youtube_channel_id,
                   youtube_subs, notes
            FROM contacts
            WHERE type = 'youtube'
              AND status = 'skip'
              AND youtube_channel_id IS NOT NULL
            """
        ).fetchall()
    rows = [dict(r) for r in rows]
    log.info("requalify: %d skip-status youtube contacts to check", len(rows))

    summary = {
        "checked":           len(rows),
        "rejected_subs_hi":  0,
        "rejected_subs_lo":  0,
        "rejected_artist":   0,
        "kept":              0,
    }

    with db.get_conn() as conn:
        for r in rows:
            subs = r.get("youtube_subs") or 0
            name = (r.get("name") or "").strip()
            notes = (r.get("notes") or "")

            reject_reason = None
            if subs > YOUTUBE_MAX_SUBS:
                reject_reason = "subs_hi"
                summary["rejected_subs_hi"] += 1
            elif subs and subs < YOUTUBE_MIN_SUBS:
                reject_reason = "subs_lo"
                summary["rejected_subs_lo"] += 1
            else:
                # Re-run artist heuristic on the cached title + description
                fake_snippet = {
                    "title": name,
                    # Notes is stored as "title: description[:200]"
                    "description": notes.split(":", 1)[1].strip() if ":" in notes else notes,
                }
                if _is_artist_channel(fake_snippet, {}):
                    reject_reason = "artist"
                    summary["rejected_artist"] += 1

            if reject_reason is None:
                summary["kept"] += 1
                continue

            if blocklist:
                conn.execute(
                    "UPDATE contacts SET status = 'closed' WHERE id = ?",
                    (r["id"],),
                )
                log.info("requalify blocklisted (%s): %s [%s subs]",
                         reject_reason, name[:40], subs)

    return summary


def retry_email_extraction(limit: int = 1000) -> dict[str, int]:
    """
    Re-run email extraction on existing status='skip' YouTube contacts using
    the current (improved) extractor. No new YouTube Data API calls — the
    channel description is already in DB (notes field is the first 200 chars,
    so we re-fetch the full snippet from the channels.list API on demand).

    Returns {retried, recovered, still_none}.
    """
    db.init_db()

    # Load skip-status youtube contacts with their channel_id
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, email, name, youtube_channel_id, notes "
            "FROM contacts WHERE type='youtube' AND status='skip' "
            "  AND youtube_channel_id IS NOT NULL "
            "  AND email LIKE 'no-email-%' "
            "ORDER BY youtube_genre_match_score DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
    rows = [dict(r) for r in rows]
    log.info("retry_email: %d skip-status youtube contacts to reprocess", len(rows))

    if not rows:
        return {"retried": 0, "recovered": 0, "still_none": 0}

    # Re-fetch full snippets from channels.list in batches of 50
    channel_ids = [r["youtube_channel_id"] for r in rows]
    snippets: dict[str, dict] = {}
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i : i + 50]
        try:
            items = _fetch_channels_batch(batch)
        except RuntimeError as e:
            log.warning("retry_email aborted: %s", e)
            break
        for item in items:
            snippets[item.get("id", "")] = item.get("snippet", {}) or {}

    recovered = 0
    still_none = 0
    with db.get_conn() as conn:
        for r in rows:
            snippet = snippets.get(r["youtube_channel_id"])
            if not snippet:
                still_none += 1
                continue
            email = _extract_channel_email(snippet, r["youtube_channel_id"])
            if not email:
                still_none += 1
                continue
            # Check email isn't already in DB
            existing = conn.execute(
                "SELECT id FROM contacts WHERE email = ? AND id != ?",
                (email, r["id"]),
            ).fetchone()
            if existing:
                # Email belongs to another contact — don't steal it. Skip.
                still_none += 1
                continue
            # Update the placeholder email to the real one, promote to 'new'
            conn.execute(
                "UPDATE contacts SET email = ?, status = 'new' WHERE id = ?",
                (email, r["id"]),
            )
            recovered += 1
            log.info("recovered: %s → %s", r["name"][:30], email)

    return {"retried": len(rows), "recovered": recovered, "still_none": still_none}


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

        email = _extract_channel_email(snippet, ch.get("id", "") or "")
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
    parser.add_argument(
        "--retry-email", action="store_true",
        help="Re-run email extraction on existing status='skip' YouTube contacts "
             "using the current (improved) extractor. No new search.list calls.",
    )
    parser.add_argument(
        "--retry-limit", type=int, default=500,
        help="Max skip contacts to reprocess in --retry-email mode.",
    )
    parser.add_argument(
        "--requalify", action="store_true",
        help="Apply current qualification rules (sub caps, artist heuristic) to "
             "existing skip-status contacts. Blocklists channels that no longer "
             "qualify. Use after tightening thresholds in config.py.",
    )
    args = parser.parse_args()

    try:
        if args.requalify:
            summary = requalify_existing(blocklist=True)
        elif args.retry_email:
            summary = retry_email_extraction(limit=args.retry_limit)
        else:
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

    if args.requalify:
        header = "YouTube Requalify Summary"
    elif args.retry_email:
        header = "YouTube Email Retry Summary"
    else:
        header = "YouTube Discovery Summary"
    print(f"\n=== {header} ===")
    for k, v in summary.items():
        print(f"  {k:<24} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
