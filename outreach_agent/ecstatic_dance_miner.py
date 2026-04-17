"""
ecstatic_dance_miner.py — Mine ecstatic dance organizer contacts globally.

Ecstatic Dance is a worldwide sober, spiritually-open movement where
independent organizers run events in their city. Every organizer is a
pre-qualified booking + relationship target for RJM:
  - Explicit sober / no-drugs policy (brand-safe)
  - Spiritually open (welcomes tribal, melodic, sacred sound)
  - Independently run (not a corporate promoter)
  - Always looking for music and guest DJs
  - Global network (hundreds of cities)

Strategy:
  1. Web-search for ecstatic dance organizers by region
  2. Fetch organizer websites / Linktrees for contact emails
  3. Add qualifying contacts with persona='ecstatic_dance', outreach_goal='booking'

Usage:
  python3 ecstatic_dance_miner.py              # full run (Europe priority)
  python3 ecstatic_dance_miner.py --limit 10   # max 10 contacts
  python3 ecstatic_dance_miner.py --region "Germany"
  python3 ecstatic_dance_miner.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))

import db
from geo_intelligence import classify_zone, BOOKING_REGIONS

log = logging.getLogger("outreach.ecstatic_dance_miner")

# ── Config ────────────────────────────────────────────────────────────────────

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HTTP_TIMEOUT = 15
_RATE_LIMIT_S = 2.0
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Domains that are never valid contact emails
_BAD_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "squarespace.com",
    "wordpress.com", "googletagmanager.com", "facebook.com",
    "instagram.com", "twitter.com", "tiktok.com", "spotify.com",
    "ecstaticdance.org",
}
_BAD_PREFIXES = {
    "noreply", "no-reply", "privacy", "abuse", "support", "info",
    "hello", "contact", "admin",
}

# Search query templates — filled with region at runtime
_QUERY_TEMPLATES = [
    '"ecstatic dance" organizer {region} contact email booking DJ 2026',
    '"ecstatic dance" {region} event organizer website contact',
    '"conscious dance" OR "authentic movement" {region} organizer contact email',
    '"ecstatic dance" facilitator {region} "contact" OR "booking" email',
    'ecstaticdance.org {region} organizer contact',
]

# Regions to cycle through — Europe first, then worldwide
_REGIONS = [r.replace('"', '').replace("'", "") for r in BOOKING_REGIONS] + [
    "Australia", "USA", "Canada", "Brazil", "South Africa",
    "Israel", "New Zealand", "Japan", "Mexico",
]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _fetch(url: str) -> str:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        log.debug("fetch failed %s: %s", url, exc)
        return ""


def _extract_emails(html: str) -> list[str]:
    found = _EMAIL_RE.findall(html)
    result = []
    for e in found:
        parts = e.lower().split("@")
        if len(parts) != 2:
            continue
        local, domain = parts
        if any(d in domain for d in _BAD_DOMAINS):
            continue
        if local in _BAD_PREFIXES:
            continue
        if "." not in domain:
            continue
        result.append(e.lower())
    return list(dict.fromkeys(result))


# ── DDG search ────────────────────────────────────────────────────────────────

def _ddg_search(query: str, max_results: int = 5) -> list[dict]:
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        return list(DDGS().text(query, max_results=max_results))
    except Exception as exc:
        log.debug("DDG failed %r: %s", query, exc)
        return []


# ── Email finding ─────────────────────────────────────────────────────────────

def _find_email(name: str, website: str, region: str) -> Optional[str]:
    """Try website first, then DDG search for contact email."""
    # 1. Try website directly
    if website and not any(
        s in website for s in ["instagram.com", "facebook.com", "twitter.com"]
    ):
        html = _fetch(website)
        emails = _extract_emails(html)
        if emails:
            return emails[0]

        # Try /contact subpage
        base = website.rstrip("/")
        for path in ["/contact", "/about", "/booking"]:
            html = _fetch(base + path)
            emails = _extract_emails(html)
            if emails:
                return emails[0]
            time.sleep(0.5)

    # 2. DDG search fallback
    query = f'"ecstatic dance" {name} {region} contact email'
    results = _ddg_search(query, max_results=3)
    for r in results:
        emails = _extract_emails(r.get("body", "") + " " + r.get("href", ""))
        if emails:
            return emails[0]

    return None


# ── Pagan / hard-exclude gate ─────────────────────────────────────────────────

_PAGAN_SIGNALS = re.compile(
    r"\b(pagan|wicca|witchcraft|occult|satan|lucifer|baphomet|"
    r"ayahuasca|psilocybin|plant medicine ceremony|drug ceremony|"
    r"psychedelic ceremony)\b",
    re.IGNORECASE,
)


def _is_brand_safe(text: str) -> bool:
    """Return False if the content has hard-exclude signals."""
    return not bool(_PAGAN_SIGNALS.search(text))


# ── Already in DB ─────────────────────────────────────────────────────────────

def _already_in_db(email: str) -> bool:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM contacts WHERE email = ?", (email.lower(),)
        ).fetchone()
    return row is not None


# ── Main mining loop ──────────────────────────────────────────────────────────

def mine(
    limit: int = 5,
    region: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    Mine ecstatic dance organizer contacts.

    Returns summary dict: {added, skipped_dup, skipped_no_email, skipped_unsafe}
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    added = 0
    skipped_dup = 0
    skipped_no_email = 0
    skipped_unsafe = 0

    regions_to_search = [region] if region else _REGIONS

    for reg in regions_to_search:
        if added >= limit:
            break

        # Pick a query template
        query = _QUERY_TEMPLATES[0].format(region=reg)
        log.info("Searching: %s", query)
        results = _ddg_search(query, max_results=8)
        time.sleep(_RATE_LIMIT_S)

        for r in results:
            if added >= limit:
                break

            title = r.get("title", "")
            body = r.get("body", "")
            url = r.get("href", "")

            # Skip social media profiles
            if any(s in url for s in ["instagram.com", "facebook.com", "twitter.com", "tiktok.com"]):
                continue

            combined_text = f"{title} {body} {url}"

            # Brand safety check
            if not _is_brand_safe(combined_text):
                log.info("Skipped unsafe: %s", title)
                skipped_unsafe += 1
                continue

            # Extract organizer name (best effort from title)
            name = title.split("|")[0].split("—")[0].split("-")[0].strip()
            if not name or len(name) > 80:
                name = f"Ecstatic Dance {reg}"

            # Check for email in snippet first
            quick_emails = _extract_emails(combined_text)
            email = quick_emails[0] if quick_emails else None

            # WebFetch if no email in snippet
            if not email and url:
                email = _find_email(name, url, reg)
                time.sleep(_RATE_LIMIT_S)

            if not email:
                skipped_no_email += 1
                continue

            if _already_in_db(email):
                skipped_dup += 1
                continue

            # Infer location from region string (strip Spotify-style OR syntax)
            location = reg.replace(" OR ", "/").replace('"', "").replace("'", "")

            notes = (
                f"Ecstatic Dance organizer in {location}. Sober, spiritually-open "
                f"movement. Pre-qualified booking target — always needs music/DJs. "
                f"Source: web search. URL: {url}"
            )

            if dry_run:
                log.info("DRY-RUN would add: %s <%s> in %s", name, email, location)
                added += 1
                continue

            try:
                with db.get_conn() as conn:
                    conn.execute(
                        """INSERT OR IGNORE INTO contacts
                           (email, name, type, genre, notes, status, source,
                            persona, outreach_goal, their_location, warmth_score,
                            faith_signals, date_added)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,date('now'))""",
                        (
                            email.lower(), name, "curator",
                            "ecstatic dance / conscious", notes, "verified",
                            "ecstatic_dance_miner",
                            "ecstatic_dance", "booking", location,
                            10,
                            1,
                        ),
                    )
                log.info("✅ Added: %s <%s> [%s]", name, email, location)
                added += 1
            except Exception as exc:
                log.warning("DB insert failed for %s: %s", email, exc)

    return {
        "added": added,
        "skipped_dup": skipped_dup,
        "skipped_no_email": skipped_no_email,
        "skipped_unsafe": skipped_unsafe,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Mine ecstatic dance organizer contacts")
    parser.add_argument("--limit", type=int, default=5, help="Max contacts to add")
    parser.add_argument("--region", type=str, default=None, help="Specific region to target")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    result = mine(limit=args.limit, region=args.region, dry_run=args.dry_run)
    print(f"\n{'DRY-RUN ' if args.dry_run else ''}Ecstatic Dance Miner complete:")
    print(f"  Added:               {result['added']}")
    print(f"  Skipped (duplicate): {result['skipped_dup']}")
    print(f"  Skipped (no email):  {result['skipped_no_email']}")
    print(f"  Skipped (unsafe):    {result['skipped_unsafe']}")


if __name__ == "__main__":
    main()
