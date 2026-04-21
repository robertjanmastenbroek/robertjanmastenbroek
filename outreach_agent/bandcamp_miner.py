"""
bandcamp_miner.py — Mine Bandcamp tag pages for music community contacts.

Bandcamp hosts tens of thousands of artists releasing nomadic electronic,
organic-tribal house, tribal psytrance, ethnic electronic, and world
electronic music. These are pre-qualified
music peers who would genuinely appreciate RJM's sound — and many
publish contact emails directly on their artist page.

No search API needed — fetches Bandcamp tag pages directly via HTTP.
DDG rate-limiting does not affect this miner.

Strategy:
  1. Fetch Bandcamp genre tag discovery pages (paginated)
  2. Extract individual artist subdomain URLs from embedded JSON data-blob
  3. Visit each artist page for contact email (mailto: + text regex)
  4. Add: persona='genre_fan', outreach_goal='music_share', warmth_score=7

Target: 20 per run × 6 runs = 120/day from Bandcamp alone

Usage:
  python3 bandcamp_miner.py              # auto-tag rotation
  python3 bandcamp_miner.py --tag tribal-psytrance
  python3 bandcamp_miner.py --limit 20
  python3 bandcamp_miner.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))

import db

log = logging.getLogger("outreach.bandcamp_miner")

# ── Config ────────────────────────────────────────────────────────────────────

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HTTP_TIMEOUT = 15
_RATE_LIMIT_S = 1.5

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_MAILTO_RE = re.compile(
    r"mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
    re.IGNORECASE,
)

# Tags to cycle through — each run picks 2-3 based on time of day + day ordinal
_TARGET_TAGS = [
    # Primary — nomadic electronic core
    "tribal-psytrance",
    "psytrance",
    "organic-house",
    "tribal-house",
    "desert-house",
    "middle-eastern-electronic",
    "ethnic-electronic",
    "handpan",
    "oud",
    "world-music",
    "progressive-psytrance",
    "goa-trance",
    "progressive-trance",
    "downtempo",
    "afrobeat",
    "progressive-house",
    "psychedelic",
    "sacred-geometry",
    # Fallback — adjacent scenes for 128 BPM tracks
    "melodic-techno",
    "melodic-house",
    "tribal",
]

_BAD_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "squarespace.com",
    "wordpress.com", "googletagmanager.com", "facebook.com",
    "instagram.com", "twitter.com", "tiktok.com", "spotify.com",
    "w3.org", "schema.org", "apple.com", "google.com", "youtube.com",
    "bandcamp.com",
}
_BAD_PREFIXES = {
    "noreply", "no-reply", "privacy", "abuse", "support",
    "admin", "postmaster", "webmaster", "donotreply",
}

_UNSAFE_SIGNALS = re.compile(
    r"\b(ayahuasca ceremony|psilocybin ceremony|drug ritual|"
    r"satanic|occult ritual|baphomet|wicca|witchcraft)\b",
    re.IGNORECASE,
)

# Bandcamp subdomain names that are platform-owned, not artist pages
_PLATFORM_SUBDOMAINS = {"store", "daily", "help", "pro", "merch", "support"}


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _fetch(url: str) -> str:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        log.debug("fetch failed %s: %s", url, exc)
        return ""


# ── Bandcamp tag page parsing ─────────────────────────────────────────────────

def _get_artist_items_from_tag_page(
    tag: str, page: int = 1
) -> list[dict]:
    """
    Return artist items from Bandcamp's hub/2/dig_deeper JSON API.

    Each item is a dict with keys: band_url, band_id, band_name.
    Bandcamp migrated tag pages to a Vite/Vue SPA in 2025 — the HTML no
    longer contains artist data. We call the underlying API directly instead.
    """
    api_url = "https://bandcamp.com/api/hub/2/dig_deeper"
    payload = json.dumps({
        "tag": tag,
        "page": page,
        "size": 20,
        "filters": {
            "sort": "date",
            "format": "mp3-128",
            "location": 0,
            "tags": [],
        },
    }).encode()
    headers = {
        "User-Agent": _USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://bandcamp.com",
        "Referer": f"https://bandcamp.com/tag/{urllib.parse.quote(tag)}",
    }
    try:
        req = urllib.request.Request(
            api_url, data=payload, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            data = json.loads(r.read())
    except Exception as exc:
        log.debug("Bandcamp API failed tag=%s page=%d: %s", tag, page, exc)
        return []

    if data.get("error"):
        log.debug("Bandcamp API error: %s", data.get("error_message"))
        return []

    seen: set[str] = set()
    result: list[dict] = []
    for item in data.get("items", []):
        band_url = item.get("band_url", "")
        if not band_url:
            subdomain = item.get("subdomain", "")
            custom = item.get("custom_domain", "")
            if custom and item.get("custom_domain_verified"):
                band_url = f"https://{custom}"
            elif subdomain:
                band_url = f"https://{subdomain}.bandcamp.com"
        if not band_url or band_url in seen:
            continue
        seen.add(band_url)
        m = re.match(r"https://([a-z0-9\-]+)\.bandcamp\.com", band_url)
        if m and m.group(1) in _PLATFORM_SUBDOMAINS:
            continue
        result.append({
            "band_url": band_url.rstrip("/") + "/",
            "band_id": item.get("band_id"),
            "band_name": item.get("band_name") or item.get("artist") or "",
        })
    return result


# Domains to skip when looking for contact emails via external sites
_SKIP_CONTACT_DOMAINS = {
    "instagram.com", "facebook.com", "twitter.com", "x.com",
    "soundcloud.com", "youtube.com", "tiktok.com", "spotify.com",
    "bandcamp.com", "bcbits.com", "mixcloud.com", "beatport.com",
    "apple.com", "deezer.com", "ra.co", "residentadvisor.net",
}


def _get_band_details(band_id: int) -> dict:
    """Call Bandcamp band_details API to get bio, sites, and location."""
    url = f"https://bandcamp.com/api/mobile/25/band_details?band_id={band_id}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def _useful_external_urls(sites: list) -> list[str]:
    """Return external site URLs worth visiting for email discovery.

    Filters out social/streaming platforms — only keeps personal websites,
    Linktrees, booking pages, and label sites that likely have email addresses.
    """
    result = []
    for site in sites:
        url = site.get("url", "")
        if not url or not url.startswith("http"):
            continue
        try:
            domain = url.split("/")[2].lstrip("www.")
        except IndexError:
            continue
        if not any(skip in domain for skip in _SKIP_CONTACT_DOMAINS):
            result.append(url)
    return result


def _get_artist_name(html: str, artist_url: str) -> str:
    """Extract artist/band name from a Bandcamp artist page."""
    m = re.search(r"<title>([^<|]{3,60})\s*[\|]", html)
    if m:
        return m.group(1).strip()
    m = re.search(r'"name"\s*:\s*"([^"]{3,60})"', html)
    if m:
        return m.group(1).strip()
    # Fallback: prettify subdomain
    sub_m = re.match(r"https://([a-z0-9\-]+)\.bandcamp\.com", artist_url)
    if sub_m:
        return sub_m.group(1).replace("-", " ").title()
    return "Bandcamp Artist"


# ── Email extraction ──────────────────────────────────────────────────────────

def _extract_emails(text: str) -> list[str]:
    # mailto: links are a stronger signal — check first
    mailto_hits = [e.lower() for e in _MAILTO_RE.findall(text)]
    text_hits = [e.lower() for e in _EMAIL_RE.findall(text)]
    all_emails = list(dict.fromkeys(mailto_hits + text_hits))

    result = []
    for e in all_emails:
        parts = e.split("@")
        if len(parts) != 2:
            continue
        local, domain = parts
        if any(d in domain for d in _BAD_DOMAINS):
            continue
        if local in _BAD_PREFIXES:
            continue
        if "." not in domain:
            continue
        result.append(e)
    return list(dict.fromkeys(result))


# ── Safety + DB helpers ───────────────────────────────────────────────────────

def _is_brand_safe(text: str) -> bool:
    return not bool(_UNSAFE_SIGNALS.search(text))


def _already_processed(band_url: str) -> bool:
    key = f"bandcamp:artist:{band_url}"
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM discovery_log WHERE search_query = ?", (key,)
        ).fetchone()
    return row is not None


def _mark_processed(band_url: str) -> None:
    key = f"bandcamp:artist:{band_url}"
    with db.get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO discovery_log "
            "(search_query, contact_type, results_found, searched_at) "
            "VALUES (?, 'genre_fan', 0, datetime('now'))",
            (key,),
        )


def _already_in_db(email: str) -> bool:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM contacts WHERE email = ?", (email.lower(),)
        ).fetchone()
    return row is not None


# ── Main mining loop ──────────────────────────────────────────────────────────

def mine(
    tag: Optional[str] = None,
    limit: int = 20,
    page: int = 1,
    dry_run: bool = False,
) -> dict:
    """
    Mine Bandcamp tag pages for artist contact emails.

    Cycles through genre tags automatically unless `tag` is specified.
    Returns: {added, skipped_dup, skipped_no_email, skipped_unsafe, skipped_processed}
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    added = 0
    skipped_dup = 0
    skipped_no_email = 0
    skipped_unsafe = 0
    skipped_processed = 0

    # Tag rotation: each of 6 daily runs hits 2 different tags
    if tag:
        tags_to_try = [tag]
    else:
        now = datetime.datetime.now()
        hour_bucket = now.hour // 4  # 6 buckets: 0=00-04, 1=04-08, ..., 5=20-24
        day_offset = now.toordinal() % len(_TARGET_TAGS)
        start_idx = (day_offset + hour_bucket * 2) % len(_TARGET_TAGS)
        tags_to_try = [
            _TARGET_TAGS[(start_idx + i) % len(_TARGET_TAGS)]
            for i in range(2)
        ]

    log.info("Bandcamp miner starting — tags=%s limit=%d", tags_to_try, limit)

    for current_tag in tags_to_try:
        if added >= limit:
            break

        for pg in range(page, page + 4):  # Up to 4 pages per tag
            if added >= limit:
                break

            log.info("Fetching bandcamp.com/tag/%s page=%d", current_tag, pg)
            artist_items = _get_artist_items_from_tag_page(current_tag, pg)
            log.info("  → %d artist URLs found", len(artist_items))
            time.sleep(_RATE_LIMIT_S)

            if not artist_items:
                break  # No more pages for this tag

            for artist_item in artist_items:
                if added >= limit:
                    break

                artist_url = artist_item["band_url"]
                band_id = artist_item.get("band_id")
                artist_name = artist_item.get("band_name") or ""

                if _already_processed(artist_url):
                    skipped_processed += 1
                    continue

                # Always mark as processed so we don't revisit across runs
                if not dry_run:
                    _mark_processed(artist_url)

                emails: list[str] = []

                # Step 1 — band_details API: check bio + external sites first
                if band_id:
                    details = _get_band_details(band_id)
                    time.sleep(0.5)

                    bio = details.get("bio", "")
                    if bio and _is_brand_safe(bio):
                        emails = _extract_emails(bio)

                    if not emails:
                        # Check email in external site URLs (linktree, personal sites)
                        external = _useful_external_urls(details.get("sites", []))
                        for ext_url in external[:3]:
                            ext_html = _fetch(ext_url)
                            if ext_html:
                                emails = _extract_emails(ext_html)
                                if emails:
                                    break
                            time.sleep(0.5)

                    if not artist_name:
                        artist_name = details.get("name", "")

                # Step 2 — fall back to Bandcamp artist page
                if not emails:
                    html = _fetch(artist_url)
                    time.sleep(_RATE_LIMIT_S)

                    if not html:
                        skipped_no_email += 1
                        continue

                    if not _is_brand_safe(html[:8000]):
                        log.debug("Skipped unsafe: %s", artist_url)
                        skipped_unsafe += 1
                        continue

                    if not artist_name:
                        artist_name = _get_artist_name(html, artist_url)

                    emails = _extract_emails(html)

                    if not emails:
                        base = artist_url.rstrip("/")
                        for path in ["/contact", "/about"]:
                            sub_html = _fetch(base + path)
                            if sub_html:
                                emails = _extract_emails(sub_html)
                                if emails:
                                    break
                            time.sleep(0.5)

                if not emails:
                    skipped_no_email += 1
                    continue

                email = emails[0]

                if _already_in_db(email):
                    skipped_dup += 1
                    continue

                notes = (
                    f"Bandcamp artist in {current_tag.replace('-', ' ')} genre. "
                    f"Music peer — pre-qualified by genre alignment. "
                    f"Source: bandcamp.com/tag/{current_tag}. URL: {artist_url}"
                )

                if dry_run:
                    log.info("DRY-RUN: %s <%s> [%s]", artist_name, email, current_tag)
                    added += 1
                    continue

                try:
                    with db.get_conn() as conn:
                        conn.execute(
                            """INSERT OR IGNORE INTO contacts
                               (email, name, type, genre, notes, status, source,
                                persona, outreach_goal, warmth_score,
                                faith_signals, date_added, website)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,date('now'),?)""",
                            (
                                email, artist_name, "curator",
                                current_tag.replace("-", " "),
                                notes, "verified", "bandcamp_miner",
                                "genre_fan", "music_share",
                                7, 0,
                                artist_url,
                            ),
                        )
                    log.info("✅ Added: %s <%s> [%s]", artist_name, email, current_tag)
                    added += 1
                except Exception as exc:
                    log.warning("DB insert failed for %s: %s", email, exc)

    return {
        "added": added,
        "skipped_dup": skipped_dup,
        "skipped_no_email": skipped_no_email,
        "skipped_unsafe": skipped_unsafe,
        "skipped_processed": skipped_processed,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mine Bandcamp tag pages for music community contacts"
    )
    parser.add_argument("--tag", type=str, default=None, help="Bandcamp tag slug (e.g. tribal-psytrance, organic-house, melodic-techno)")
    parser.add_argument("--limit", type=int, default=20, help="Max contacts to add per run")
    parser.add_argument("--page", type=int, default=1, help="Starting page number")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    result = mine(tag=args.tag, limit=args.limit, page=args.page, dry_run=args.dry_run)
    print(f"\n{'DRY-RUN ' if args.dry_run else ''}Bandcamp Miner complete:")
    print(f"  Added:                {result['added']}")
    print(f"  Skipped (duplicate):  {result['skipped_dup']}")
    print(f"  Skipped (no email):   {result['skipped_no_email']}")
    print(f"  Skipped (unsafe):     {result['skipped_unsafe']}")
    print(f"  Skipped (processed):  {result['skipped_processed']}")


if __name__ == "__main__":
    main()
