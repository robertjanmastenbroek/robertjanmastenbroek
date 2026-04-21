"""
fan_miner.py — Mine music fan contacts: genre bloggers, newsletter editors,
YouTube channel owners, and community managers who cover nomadic electronic,
organic-tribal house, tribal psytrance, ethnic electronic, desert house,
and Middle Eastern / world electronic music.

These people already love RJM's genre — outreach is music sharing,
not a cold pitch. They can amplify, review, or simply stream the track.

Target: 10 contacts per discover run (60/day across 6 runs).
Persona: genre_fan | outreach_goal: music_share

Usage:
  python3 fan_miner.py              # full run
  python3 fan_miner.py --limit 10   # max 10 contacts
  python3 fan_miner.py --dry-run    # preview without writing
"""

from __future__ import annotations

import argparse
import datetime
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

log = logging.getLogger("outreach.fan_miner")

# ── Config ────────────────────────────────────────────────────────────────────

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HTTP_TIMEOUT = 15
_RATE_LIMIT_S = 2.0
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_HEX_RE   = re.compile(r"^[0-9a-f]{16,}$")  # catches hash-style local parts

_BAD_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "squarespace.com",
    "wordpress.com", "googletagmanager.com", "facebook.com",
    "instagram.com", "twitter.com", "tiktok.com", "spotify.com",
    "w3.org", "schema.org", "apple.com", "google.com", "youtube.com",
}
_BAD_PREFIXES = {
    "noreply", "no-reply", "privacy", "abuse", "support",
    "admin", "postmaster", "webmaster", "donotreply",
}

# Each query targets a different genre-fan angle — cycle through them per day
_QUERIES = [
    '"nomadic electronic" OR "organic house" blog OR newsletter writer contact email 2026',
    '"psytrance" OR "psy trance" community blog newsletter contact email 2026',
    '"tribal house" OR "world electronic" music blog contact email 2026',
    '"conscious rave" OR "sacred dance" community newsletter OR blog contact email 2026',
    '"Burning Man" music blog OR newsletter "organic house" OR "desert house" contact email 2026',
    '"Café de Anatolia" OR "Sol Selectas" OR "Bedouin" fan blog review contact email 2026',
    '"Ozora" OR "Boom festival" music blog community newsletter contact email 2026',
    '"handpan" OR "oud" electronic music YouTube channel creator business email contact 2026',
    '"psytrance" YouTube channel OR newsletter creator contact email 2026',
    '"ethnic electronic" OR "world bass" music blog reviewer contact email 2026',
    '"flow state" OR "consciousness music" blog newsletter curator contact email 2026',
    '"desert hearts" OR "All Day I Dream" fan blog music community contact email 2026',
    '"organic house" OR "desert house" OR "tribal house" music blog newsletter contact email 2026',
    '"festival culture" OR "rave culture" blog newsletter creator contact email 2026',
    '"underground techno" blog writer journalist contact email 2026',
    'psytrance festival community organizer newsletter contact email Europe 2026',
    '"Middle Eastern electronic" OR "nomadic electronic" podcast OR mix series contact booking email 2026',
    '"ecstatic dance music" playlist curator blog newsletter contact email 2026',
]

_UNSAFE_SIGNALS = re.compile(
    r"\b(ayahuasca ceremony|psilocybin ceremony|drug ritual|"
    r"satanic|occult ritual|baphomet)\b",
    re.IGNORECASE,
)


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
        # Reject hex-hash local parts (e.g. 04c118fab6a345f7b4009aafe33d8b52)
        if _HEX_RE.match(local):
            continue
        segments = domain.split(".")
        tld = segments[-1]
        # TLD must be pure alpha, 2–10 chars (rejects .ru.js, .d58f…)
        if not tld.isalpha() or not (2 <= len(tld) <= 10):
            continue
        # Reject any domain segment that looks like a hex hash
        if any(_HEX_RE.match(seg) for seg in segments):
            continue
        # Reject test. subdomains (e.g. test.rutube.ru)
        if segments[0] == "test":
            continue
        result.append(e.lower())
    return list(dict.fromkeys(result))


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


def _find_email(name: str, website: str) -> Optional[str]:
    if website and not any(
        s in website for s in [
            "instagram.com", "facebook.com", "twitter.com", "youtube.com",
        ]
    ):
        html = _fetch(website)
        emails = _extract_emails(html)
        if emails:
            return emails[0]

        base = website.rstrip("/")
        for path in ["/contact", "/about", "/write-for-us", "/submit"]:
            html = _fetch(base + path)
            emails = _extract_emails(html)
            if emails:
                return emails[0]
            time.sleep(0.5)

    return None


def _is_brand_safe(text: str) -> bool:
    return not bool(_UNSAFE_SIGNALS.search(text))


def _already_in_db(email: str) -> bool:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM contacts WHERE email = ?", (email.lower(),)
        ).fetchone()
    return row is not None


# ── Main mining loop ──────────────────────────────────────────────────────────

def mine(limit: int = 10, dry_run: bool = False) -> dict:
    """
    Mine genre fan contacts — bloggers, newsletter editors, community managers.

    Returns summary dict: {added, skipped_dup, skipped_no_email, skipped_unsafe}
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    added = 0
    skipped_dup = 0
    skipped_no_email = 0
    skipped_unsafe = 0

    # Rotate starting query based on day so each run covers different angles
    day_offset = datetime.date.today().toordinal() % len(_QUERIES)
    ordered_queries = _QUERIES[day_offset:] + _QUERIES[:day_offset]

    for query in ordered_queries:
        if added >= limit:
            break

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
                "youtube.com", "reddit.com", "soundcloud.com", "bandcamp.com",
            ]):
                continue

            combined_text = f"{title} {body} {url}"

            if not _is_brand_safe(combined_text):
                skipped_unsafe += 1
                continue

            name = title.split("|")[0].split("—")[0].split("-")[0].strip()
            if not name or len(name) > 80:
                name = "Music Blog/Newsletter"

            quick_emails = _extract_emails(combined_text)
            email = quick_emails[0] if quick_emails else None

            if not email and url:
                email = _find_email(name, url)
                time.sleep(_RATE_LIMIT_S)

            if not email:
                skipped_no_email += 1
                continue

            if _already_in_db(email):
                skipped_dup += 1
                continue

            notes = (
                f"Genre fan / music community contact. Covers nomadic electronic / "
                f"organic-tribal house / tribal psytrance / world electronic. "
                f"Outreach: share RJM track + story. "
                f"Source query: {query[:100]}. URL: {url}"
            )

            if dry_run:
                log.info("DRY-RUN would add: %s <%s>", name, email)
                added += 1
                continue

            try:
                with db.get_conn() as conn:
                    conn.execute(
                        """INSERT OR IGNORE INTO contacts
                           (email, name, type, genre, notes, status, source,
                            persona, outreach_goal, warmth_score,
                            faith_signals, date_added)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,date('now'))""",
                        (
                            email.lower(), name, "curator",
                            "nomadic electronic / organic-tribal house / tribal psytrance / world electronic",
                            notes, "new", "fan_miner",
                            "genre_fan", "music_share",
                            7, 0,
                        ),
                    )
                log.info("✅ Added: %s <%s>", name, email)
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
    parser = argparse.ArgumentParser(description="Mine genre fan contacts for music sharing")
    parser.add_argument("--limit", type=int, default=10, help="Max contacts to add")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    result = mine(limit=args.limit, dry_run=args.dry_run)
    print(f"\n{'DRY-RUN ' if args.dry_run else ''}Fan Miner complete:")
    print(f"  Added:               {result['added']}")
    print(f"  Skipped (duplicate): {result['skipped_dup']}")
    print(f"  Skipped (no email):  {result['skipped_no_email']}")
    print(f"  Skipped (unsafe):    {result['skipped_unsafe']}")


if __name__ == "__main__":
    main()
