"""
church_miner.py — Mine European church and Christian ministry contacts
open to house/techno worship.

Target organisations:
  - Jugendkirche / Jugendgottesdienste (Germany, Austria, Switzerland)
  - Jongerendienst / jongeren events (Netherlands, Belgium)
  - ICF network (~50 European locations)
  - Hillsong Young & Free chapters (NL, UK, DE, FR, ES, IT, DK, SE)
  - Charismatic/Vineyard churches with modern worship
  - Christian festivals: Freakstock, Christival, Opwekking, EO-Jongerendag
  - Rave-church movements: Church Rave Berlin, R.O.W Nights UK
  - Faith-based retreat centres with DJ/music programming

Every contact: persona='church', outreach_goal='booking', faith_signals=1

Usage:
  python3 church_miner.py              # full run (Europe priority)
  python3 church_miner.py --limit 10   # max 10 contacts
  python3 church_miner.py --region "Germany"
  python3 church_miner.py --dry-run
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

log = logging.getLogger("outreach.church_miner")

# ── Config ────────────────────────────────────────────────────────────────────

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HTTP_TIMEOUT = 15
_RATE_LIMIT_S = 2.0
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

_BAD_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "squarespace.com",
    "wordpress.com", "googletagmanager.com", "facebook.com",
    "instagram.com", "twitter.com", "tiktok.com", "spotify.com",
    "w3.org", "schema.org", "apple.com", "google.com",
}
_BAD_PREFIXES = {
    "noreply", "no-reply", "privacy", "abuse", "support",
    "admin", "postmaster", "webmaster", "donotreply",
}

# Europe-priority region list for church bookings
_CHURCH_REGIONS = [
    "Netherlands",
    "Germany",
    "UK",
    "Belgium",
    "France",
    "Spain",
    "Italy",
    "Switzerland",
    "Denmark",
    "Norway",
    "Sweden",
    "Poland",
    "Portugal",
    "Austria",
    "Czech Republic",
    "Hungary",
    "Ireland",
    "Finland",
    "Greece",
    "Romania",
]

# Query templates — filled with {region} at runtime
_QUERY_TEMPLATES = [
    '"youth church" OR "youth ministry" electronic music DJ {region} contact email 2026',
    '"Jugendkirche" OR "Jugendgottesdienst" elektronisch DJ {region} Kontakt email 2026',
    '"Jongerendienst" OR "jongeren" elektronische muziek DJ {region} contact email 2026',
    '"church rave" OR "worship rave" OR "rave church" {region} contact booking email 2026',
    '"charismatic church" OR "vineyard church" OR "ICF church" modern music DJ {region} contact email',
    '"Christian festival" OR "faith festival" electronic music DJ booking {region} contact email 2026',
    '"techno worship" OR "house worship" OR "electronic worship" church {region} contact email 2026',
    '"Hillsong" youth OR "young and free" {region} music program booking contact email',
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

def _ddg_search(query: str, max_results: int = 8) -> list[dict]:
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
    """Try website first, then subpages, then DDG search."""
    if website and not any(
        s in website for s in ["instagram.com", "facebook.com", "twitter.com"]
    ):
        html = _fetch(website)
        emails = _extract_emails(html)
        if emails:
            return emails[0]

        base = website.rstrip("/")
        for path in ["/contact", "/about", "/booking", "/kontakt", "/team"]:
            html = _fetch(base + path)
            emails = _extract_emails(html)
            if emails:
                return emails[0]
            time.sleep(0.5)

    query = f'"church" {name} {region} contact email booking'
    results = _ddg_search(query, max_results=3)
    for r in results:
        emails = _extract_emails(r.get("body", "") + " " + r.get("href", ""))
        if emails:
            return emails[0]

    return None


# ── Brand safety ──────────────────────────────────────────────────────────────

_UNSAFE_SIGNALS = re.compile(
    r"\b(pagan|wicca|witchcraft|occult|satan|lucifer|baphomet|"
    r"ayahuasca|psilocybin|plant medicine ceremony|drug ceremony|"
    r"psychedelic ceremony|new age ritual|antichrist)\b",
    re.IGNORECASE,
)


def _is_brand_safe(text: str) -> bool:
    return not bool(_UNSAFE_SIGNALS.search(text))


# ── DB helpers ────────────────────────────────────────────────────────────────

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
    Mine church contacts open to house/techno worship.

    Returns summary dict: {added, skipped_dup, skipped_no_email, skipped_unsafe}
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    added = 0
    skipped_dup = 0
    skipped_no_email = 0
    skipped_unsafe = 0

    regions_to_search = [region] if region else _CHURCH_REGIONS

    for reg in regions_to_search:
        if added >= limit:
            break

        # Rotate through query templates to avoid repetition
        query_idx = hash(reg) % len(_QUERY_TEMPLATES)
        query = _QUERY_TEMPLATES[query_idx].format(region=reg)
        log.info("Searching: %s", query)
        results = _ddg_search(query, max_results=8)
        time.sleep(_RATE_LIMIT_S)

        for r in results:
            if added >= limit:
                break

            title = r.get("title", "")
            body = r.get("body", "")
            url = r.get("href", "")

            if any(s in url for s in [
                "instagram.com", "facebook.com", "twitter.com", "tiktok.com",
                "youtube.com", "eventbrite.com", "meetup.com",
            ]):
                continue

            combined_text = f"{title} {body} {url}"

            if not _is_brand_safe(combined_text):
                log.info("Skipped unsafe: %s", title)
                skipped_unsafe += 1
                continue

            name = title.split("|")[0].split("—")[0].split("-")[0].strip()
            if not name or len(name) > 80:
                name = f"Church/Ministry {reg}"

            quick_emails = _extract_emails(combined_text)
            email = quick_emails[0] if quick_emails else None

            if not email and url:
                email = _find_email(name, url, reg)
                time.sleep(_RATE_LIMIT_S)

            if not email:
                skipped_no_email += 1
                continue

            if _already_in_db(email):
                skipped_dup += 1
                continue

            location = reg.replace(" OR ", "/")

            notes = (
                f"Church/ministry in {location} open to electronic/modern worship. "
                f"Target for DJ booking — Holy Rave format (ancient truth, future sound). "
                f"Source: church_miner web search. URL: {url}"
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
                            "christian / worship / church", notes,
                            "verified", "church_miner",
                            "church", "booking", location,
                            8, 1,
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
    parser = argparse.ArgumentParser(description="Mine church/ministry contacts for techno worship bookings")
    parser.add_argument("--limit", type=int, default=5, help="Max contacts to add")
    parser.add_argument("--region", type=str, default=None, help="Specific region")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    result = mine(limit=args.limit, region=args.region, dry_run=args.dry_run)
    print(f"\n{'DRY-RUN ' if args.dry_run else ''}Church Miner complete:")
    print(f"  Added:               {result['added']}")
    print(f"  Skipped (duplicate): {result['skipped_dup']}")
    print(f"  Skipped (no email):  {result['skipped_no_email']}")
    print(f"  Skipped (unsafe):    {result['skipped_unsafe']}")


if __name__ == "__main__":
    main()
