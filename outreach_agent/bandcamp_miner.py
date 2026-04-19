"""
bandcamp_miner.py — Mine Bandcamp tag pages for music community contacts.

Bandcamp hosts tens of thousands of artists releasing melodic techno,
psytrance, tribal, and world electronic music. These are pre-qualified
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
  python3 bandcamp_miner.py --tag melodic-techno
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
    "melodic-techno",
    "psytrance",
    "tribal",
    "organic-house",
    "world-music",
    "ethnic-electronic",
    "progressive-trance",
    "downtempo",
    "afrobeat",
    "progressive-house",
    "melodic-house",
    "psychedelic",
    "sacred-geometry",
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

def _get_artist_urls_from_tag_page(tag: str, page: int = 1) -> list[str]:
    """
    Fetch a Bandcamp tag page and return unique artist band URLs.

    Tries three extraction strategies in order:
    1. JSON in id="pagedata" data-blob attribute (primary — has band_url field)
    2. Raw regex for "band_url" JSON keys anywhere in HTML
    3. Regex for .bandcamp.com/album or /track href paths (fallback)
    """
    url = (
        f"https://bandcamp.com/tag/{urllib.parse.quote(tag)}"
        f"?sort_field=tag_date&page={page}"
    )
    html = _fetch(url)
    if not html:
        return []

    artist_urls: list[str] = []

    # Strategy 1: data-blob JSON on #pagedata div
    blob_match = re.search(r'id=["\']pagedata["\'][^>]+data-blob=["\']([^"\']+)["\']', html)
    if not blob_match:
        blob_match = re.search(r'data-blob=["\']([^"\']+)["\'][^>]*id=["\']pagedata["\']', html)
    if blob_match:
        try:
            raw = blob_match.group(1)
            # Unescape HTML attribute encoding
            raw = (
                raw.replace("&quot;", '"')
                   .replace("&#39;", "'")
                   .replace("&amp;", "&")
                   .replace("&#x27;", "'")
            )
            blob = json.loads(raw)
            items: list[dict] = []
            if isinstance(blob.get("hub"), dict):
                items = blob["hub"].get("items", [])
            elif isinstance(blob.get("items"), list):
                items = blob["items"]
            for item in items:
                band_url = item.get("band_url") or item.get("item_url", "")
                if band_url and ".bandcamp.com" in band_url:
                    m = re.match(r"(https://[a-z0-9\-]+\.bandcamp\.com)", band_url)
                    if m:
                        sub = m.group(1).split("//")[1].split(".")[0]
                        if sub not in _PLATFORM_SUBDOMAINS:
                            artist_urls.append(m.group(1) + "/")
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass

    # Strategy 2: raw "band_url" pattern in HTML (works when JSON is unquoted/inline)
    if not artist_urls:
        for band_url in re.findall(
            r'"band_url"\s*:\s*"(https://[a-z0-9\-]+\.bandcamp\.com[^"]*)"', html
        ):
            m = re.match(r"(https://[a-z0-9\-]+\.bandcamp\.com)", band_url)
            if m:
                sub = m.group(1).split("//")[1].split(".")[0]
                if sub not in _PLATFORM_SUBDOMAINS:
                    artist_urls.append(m.group(1) + "/")

    # Strategy 3: href links to album/track pages (any subdomain with /album or /track)
    if not artist_urls:
        for sub in re.findall(
            r'https://([a-z0-9\-]+)\.bandcamp\.com/(?:album|track|music)', html
        ):
            if sub not in _PLATFORM_SUBDOMAINS:
                artist_urls.append(f"https://{sub}.bandcamp.com/")

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for u in artist_urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
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
            artist_urls = _get_artist_urls_from_tag_page(current_tag, pg)
            log.info("  → %d artist URLs found", len(artist_urls))
            time.sleep(_RATE_LIMIT_S)

            if not artist_urls:
                break  # No more pages for this tag

            for artist_url in artist_urls:
                if added >= limit:
                    break

                if _already_processed(artist_url):
                    skipped_processed += 1
                    continue

                html = _fetch(artist_url)
                time.sleep(_RATE_LIMIT_S)

                # Always mark as processed so we don't revisit across runs
                if not dry_run:
                    _mark_processed(artist_url)

                if not html:
                    continue

                # Brand safety on first 8KB (header/bio area)
                if not _is_brand_safe(html[:8000]):
                    log.debug("Skipped unsafe: %s", artist_url)
                    skipped_unsafe += 1
                    continue

                artist_name = _get_artist_name(html, artist_url)
                emails = _extract_emails(html)

                # Check /contact and /about if homepage has no email
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
    parser.add_argument("--tag", type=str, default=None, help="Bandcamp tag slug (e.g. melodic-techno)")
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
