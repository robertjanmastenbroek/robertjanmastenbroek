# Contact Volume Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Increase daily contact discovery from ~3/run to 15–20+/run by adding church_miner.py, fan_miner.py, an email scrub, and wiring both miners into the rjm-discover SKILL.md.

**Architecture:** Three new scripts (church_miner.py, fan_miner.py, email_scrub.py) all follow the pattern established by ecstatic_dance_miner.py — DDG web search → WebFetch for email extraction → INSERT OR IGNORE into outreach.db. The discover SKILL.md gains two new parts (E + F) so both miners run automatically each discover cycle. Email scrub uses the existing bounce.verify_email() to pre-clean the queue and lower the bounce rate below the 15% threshold that is currently pausing all sends.

**Tech Stack:** Python 3.13, SQLite (via db.py + db.get_conn()), DuckDuckGo search (ddgs/duckduckgo_search), urllib (fetch), bounce.py (verify_email), existing geo_intelligence.py (BOOKING_REGIONS).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `outreach_agent/church_miner.py` | Create | Mine European church/ministry contacts open to techno/house worship |
| `outreach_agent/fan_miner.py` | Create | Mine music fan contacts — genre bloggers, community newsletter editors, YouTube channel owners covering melodic techno/psytrance |
| `outreach_agent/email_scrub.py` | Create | Pre-verify 'new'/'verified' contacts using bounce.verify_email(); mark invalid as skip to lower bounce rate |
| `~/.claude/scheduled-tasks/rjm-discover/SKILL.md` | Modify | Add Part E (church miner), Part F (fan miner), church query bank in Part A, update header counts |

---

## Task 1: church_miner.py

**Files:**
- Create: `outreach_agent/church_miner.py`
- Test: `outreach_agent/tests/test_church_miner.py`

### Church miner logic

Targets: European churches, youth ministries, Christian festivals, and charismatic networks that are already open to or running house/techno worship nights. Evidence from research: St. Thomas Berlin Church Rave, Jugendkirche network across Germany, Hillsong Young & Free, ICF network (50 locations), Freakstock, Christival, EO-Jongerendag.

Query templates rotate through regions and persona angles — Jugendkirche/Jongerendienst (local-language), charismatic/Vineyard/ICF networks, Christian festival bookers, and rave-church movements.

- [ ] **Step 1: Write the failing tests**

Create `outreach_agent/tests/test_church_miner.py`:

```python
"""Tests for church_miner.py — deterministic paths only, no network."""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_is_brand_safe_blocks_occult():
    import church_miner
    assert church_miner._is_brand_safe("wicca ritual ceremony") is False
    assert church_miner._is_brand_safe("ayahuasca ceremony DJ booking") is False


def test_is_brand_safe_passes_christian():
    import church_miner
    assert church_miner._is_brand_safe("youth church electronic worship Berlin") is True
    assert church_miner._is_brand_safe("Jugendkirche techno Gottesdienst") is True


def test_extract_emails_filters_bad_domains():
    import church_miner
    html = "contact us at spam@sentry.io or book@realchurch.de"
    emails = church_miner._extract_emails(html)
    assert "spam@sentry.io" not in emails
    assert "book@realchurch.de" in emails


def test_extract_emails_filters_bad_prefixes():
    import church_miner
    html = "Write noreply@church.de or pastor@church.de"
    emails = church_miner._extract_emails(html)
    assert "noreply@church.de" not in emails
    assert "pastor@church.de" in emails


def test_mine_dry_run_returns_dict(temp_db, monkeypatch):
    import church_miner
    monkeypatch.setattr(church_miner, "_ddg_search", lambda *a, **k: [
        {"title": "Jugendkirche Köln", "body": "book@jugendkirche-koeln.de", "href": "https://jugendkirche-koeln.de"}
    ])
    result = church_miner.mine(limit=1, dry_run=True)
    assert "added" in result
    assert result["added"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
source venv/bin/activate
pytest tests/test_church_miner.py -v 2>&1 | head -20
```

Expected: `ImportError: No module named 'church_miner'`

- [ ] **Step 3: Write church_miner.py**

Create `outreach_agent/church_miner.py`:

```python
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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
source venv/bin/activate
pytest tests/test_church_miner.py -v 2>&1
```

Expected output:
```
tests/test_church_miner.py::test_is_brand_safe_blocks_occult PASSED
tests/test_church_miner.py::test_is_brand_safe_passes_christian PASSED
tests/test_church_miner.py::test_extract_emails_filters_bad_domains PASSED
tests/test_church_miner.py::test_extract_emails_filters_bad_prefixes PASSED
tests/test_church_miner.py::test_mine_dry_run_returns_dict PASSED
5 passed
```

- [ ] **Step 5: Smoke test dry-run (real network)**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
source venv/bin/activate
python3 church_miner.py --limit 3 --dry-run 2>&1
```

Expected: logs 3 DRY-RUN would add lines, no DB writes.

- [ ] **Step 6: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add outreach_agent/church_miner.py outreach_agent/tests/test_church_miner.py
git commit -m "feat(discover): add church_miner.py — mine European church/ministry contacts for techno worship bookings"
```

---

## Task 2: fan_miner.py

**Files:**
- Create: `outreach_agent/fan_miner.py`
- Test: `outreach_agent/tests/test_fan_miner.py`

### Fan miner logic

Targets people who already love melodic techno / psytrance / tribal / world electronic music and have contact emails — genre bloggers, community newsletter editors, genre-specific YouTube channel owners, music journalists covering the scene, festival community managers. Persona: `genre_fan`, outreach_goal: `music_share`.

Search angles: peers of Anyma/KSHMR/Tale of Us (their fans are RJM's fans), Burning Man / Ozora / Boom music community, conscious rave scene writers, "weekly techno picks" newsletter editors.

- [ ] **Step 1: Write the failing tests**

Create `outreach_agent/tests/test_fan_miner.py`:

```python
"""Tests for fan_miner.py — deterministic paths only, no network."""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_is_brand_safe_blocks_drug_ceremony():
    import fan_miner
    assert fan_miner._is_brand_safe("ayahuasca ceremony playlist") is False


def test_is_brand_safe_passes_music_fan():
    import fan_miner
    assert fan_miner._is_brand_safe("melodic techno blog newsletter weekly picks") is True
    assert fan_miner._is_brand_safe("psytrance community Burning Man contact") is True


def test_extract_emails_returns_valid():
    import fan_miner
    html = "subscribe: editor@technonewsletter.com or spam@sentry.io"
    emails = fan_miner._extract_emails(html)
    assert "editor@technonewsletter.com" in emails
    assert "spam@sentry.io" not in emails


def test_mine_dry_run_returns_dict(temp_db, monkeypatch):
    import fan_miner
    monkeypatch.setattr(fan_miner, "_ddg_search", lambda *a, **k: [
        {
            "title": "Melodic Techno Weekly Newsletter",
            "body": "Get weekly picks — contact editor@melodicweekly.com",
            "href": "https://melodicweekly.com",
        }
    ])
    result = fan_miner.mine(limit=1, dry_run=True)
    assert result["added"] == 1


def test_mine_skips_social_media_urls(temp_db, monkeypatch):
    import fan_miner
    monkeypatch.setattr(fan_miner, "_ddg_search", lambda *a, **k: [
        {"title": "Fan page", "body": "follow us", "href": "https://instagram.com/melodicfans"},
    ])
    result = fan_miner.mine(limit=5, dry_run=True)
    assert result["added"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
source venv/bin/activate
pytest tests/test_fan_miner.py -v 2>&1 | head -10
```

Expected: `ImportError: No module named 'fan_miner'`

- [ ] **Step 3: Write fan_miner.py**

Create `outreach_agent/fan_miner.py`:

```python
"""
fan_miner.py — Mine music fan contacts: genre bloggers, newsletter editors,
YouTube channel owners, and community managers who cover melodic techno,
psytrance, tribal, and world electronic music.

These people already love RJM's genre — outreach is music sharing,
not a cold pitch. They can amplify, review, or simply stream the track.

Target: 50+ contacts per day across all discover runs combined.
Persona: genre_fan | outreach_goal: music_share

Usage:
  python3 fan_miner.py              # full run
  python3 fan_miner.py --limit 10   # max 10 contacts
  python3 fan_miner.py --dry-run    # preview without writing
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

log = logging.getLogger("outreach.fan_miner")

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
    "w3.org", "schema.org", "apple.com", "google.com", "youtube.com",
}
_BAD_PREFIXES = {
    "noreply", "no-reply", "privacy", "abuse", "support",
    "admin", "postmaster", "webmaster", "donotreply",
}

# Each query targets a different genre-fan angle — cycle through them
_QUERIES = [
    '"melodic techno" blog OR newsletter writer contact email 2026',
    '"psytrance" OR "psy trance" community blog newsletter contact email 2026',
    '"tribal techno" OR "world electronic" music blog contact email 2026',
    '"conscious rave" OR "sacred dance" community newsletter OR blog contact email 2026',
    '"Burning Man" music blog OR newsletter "melodic techno" OR "organic house" contact email 2026',
    '"Anyma" OR "Tale of Us" OR "KSHMR" fan blog review contact email 2026',
    '"Ozora" OR "Boom festival" music blog community newsletter contact email 2026',
    '"melodic techno" YouTube channel creator business email contact 2026',
    '"psytrance" YouTube channel OR newsletter creator contact email 2026',
    '"ethnic electronic" OR "world bass" music blog reviewer contact email 2026',
    '"flow state" OR "consciousness music" blog newsletter curator contact email 2026',
    '"desert hearts" OR "All Day I Dream" fan blog music community contact email 2026',
    '"organic house" OR "melodic house" music blog newsletter contact email 2026',
    '"festival culture" OR "rave culture" blog newsletter creator contact email 2026',
    '"underground techno" blog writer journalist contact email 2026',
    'psytrance festival community organizer newsletter contact email Europe 2026',
    '"melodic techno" podcast OR mix series contact booking email 2026',
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

    # Rotate through query list so each run hits different angles
    import datetime
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
                f"Genre fan / music community contact. Covers melodic techno / "
                f"psytrance / world electronic. Outreach: share RJM track + story. "
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
                            "melodic techno / psytrance / world electronic",
                            notes, "verified", "fan_miner",
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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
source venv/bin/activate
pytest tests/test_fan_miner.py -v 2>&1
```

Expected:
```
tests/test_fan_miner.py::test_is_brand_safe_blocks_drug_ceremony PASSED
tests/test_fan_miner.py::test_is_brand_safe_passes_music_fan PASSED
tests/test_fan_miner.py::test_extract_emails_returns_valid PASSED
tests/test_fan_miner.py::test_mine_dry_run_returns_dict PASSED
tests/test_fan_miner.py::test_mine_skips_social_media_urls PASSED
5 passed
```

- [ ] **Step 5: Smoke test dry-run**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
source venv/bin/activate
python3 fan_miner.py --limit 3 --dry-run 2>&1
```

Expected: 3 DRY-RUN lines with blog/newsletter names and emails. No DB writes.

- [ ] **Step 6: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add outreach_agent/fan_miner.py outreach_agent/tests/test_fan_miner.py
git commit -m "feat(discover): add fan_miner.py — mine genre fan contacts (blogs, newsletters, community editors) for music sharing"
```

---

## Task 3: email_scrub.py

**Files:**
- Create: `outreach_agent/email_scrub.py`
- Test: `outreach_agent/tests/test_email_scrub.py`

### Scrub logic

The bounce rate is currently 18.3% (15 bounces / 82 sends over 7 days) — above the 15% limit that pauses all sends. The scrub runs `bounce.verify_email()` on all contacts with `status IN ('new', 'verified')` that haven't been sent yet. Confirmed-invalid contacts are marked `status='skip', bounce='pre-check'`. This reduces the bounce rate by cleaning the queue before sends resume.

- [ ] **Step 1: Write the failing tests**

Create `outreach_agent/tests/test_email_scrub.py`:

```python
"""Tests for email_scrub.py."""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_scrub_marks_invalid_as_skip(temp_db, monkeypatch):
    import db, email_scrub

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("dead@deadomain.xyz", "Dead Contact", "curator", "verified"),
        )

    monkeypatch.setattr(
        "bounce.verify_email",
        lambda e: ("invalid", "Disify: domain has no MX record"),
    )

    result = email_scrub.scrub(limit=10, dry_run=False)
    assert result["marked_skip"] == 1

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status, bounce FROM contacts WHERE email=?",
            ("dead@deadomain.xyz",),
        ).fetchone()
    assert row[0] == "skip"
    assert row[1] == "pre-check"


def test_scrub_keeps_valid(temp_db, monkeypatch):
    import db, email_scrub

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("good@gmail.com", "Good Contact", "curator", "verified"),
        )

    monkeypatch.setattr(
        "bounce.verify_email",
        lambda e: ("valid", "major provider fast-path"),
    )

    result = email_scrub.scrub(limit=10, dry_run=False)
    assert result["confirmed_valid"] >= 1

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM contacts WHERE email=?", ("good@gmail.com",)
        ).fetchone()
    assert row[0] == "verified"


def test_scrub_dry_run_does_not_write(temp_db, monkeypatch):
    import db, email_scrub

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO contacts (email, name, type, status) VALUES (?,?,?,?)",
            ("dead2@deadomain.xyz", "Dead2", "curator", "new"),
        )

    monkeypatch.setattr(
        "bounce.verify_email",
        lambda e: ("invalid", "no MX"),
    )

    email_scrub.scrub(limit=10, dry_run=True)

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM contacts WHERE email=?",
            ("dead2@deadomain.xyz",),
        ).fetchone()
    assert row[0] == "new"  # unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
source venv/bin/activate
pytest tests/test_email_scrub.py -v 2>&1 | head -10
```

Expected: `ImportError: No module named 'email_scrub'`

- [ ] **Step 3: Write email_scrub.py**

Create `outreach_agent/email_scrub.py`:

```python
"""
email_scrub.py — Pre-verify pending contacts to lower bounce rate.

Runs bounce.verify_email() on all 'new'/'verified' contacts not yet sent.
Marks confirmed-invalid as status='skip', bounce='pre-check'.

The send system pauses when bounce rate > 15%. This scrub clears bad
emails from the queue so the rate recovers and sends resume.

Usage:
  python3 email_scrub.py              # scrub all pending contacts
  python3 email_scrub.py --limit 50   # max 50 checks (default 100)
  python3 email_scrub.py --dry-run    # report without writing
"""

from __future__ import annotations

import argparse
import logging
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

import db
import bounce

log = logging.getLogger("outreach.email_scrub")

_RATE_LIMIT_S = 0.5  # Disify allows ~2 req/s


def scrub(limit: int = 100, dry_run: bool = False) -> dict:
    """
    Verify pending contacts, mark invalid ones as skip.

    Returns: {checked, marked_skip, confirmed_valid, inconclusive}
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT email, name FROM contacts
               WHERE status IN ('new', 'verified')
               AND (bounce IS NULL OR bounce = 'no')
               LIMIT ?""",
            (limit,),
        ).fetchall()

    log.info("Scrubbing %d pending contacts...", len(rows))

    checked = 0
    marked_skip = 0
    confirmed_valid = 0
    inconclusive = 0

    for email, name in rows:
        result, reason = bounce.verify_email(email)
        checked += 1

        if result == "invalid":
            log.info("❌ Invalid: %s (%s) — %s", email, name, reason)
            marked_skip += 1
            if not dry_run:
                with db.get_conn() as conn:
                    conn.execute(
                        "UPDATE contacts SET status='skip', bounce='pre-check' WHERE email=?",
                        (email,),
                    )
        elif result == "valid":
            log.debug("✅ Valid: %s", email)
            confirmed_valid += 1
        else:
            log.debug("❓ Inconclusive: %s — %s", email, reason)
            inconclusive += 1

        time.sleep(_RATE_LIMIT_S)

    log.info(
        "Scrub complete: %d checked, %d skip, %d valid, %d inconclusive",
        checked, marked_skip, confirmed_valid, inconclusive,
    )

    return {
        "checked": checked,
        "marked_skip": marked_skip,
        "confirmed_valid": confirmed_valid,
        "inconclusive": inconclusive,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-verify pending contacts to cut bounce rate")
    parser.add_argument("--limit", type=int, default=100, help="Max contacts to check")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    result = scrub(limit=args.limit, dry_run=args.dry_run)
    print(f"\n{'DRY-RUN ' if args.dry_run else ''}Email Scrub complete:")
    print(f"  Checked:            {result['checked']}")
    print(f"  Marked skip:        {result['marked_skip']}")
    print(f"  Confirmed valid:    {result['confirmed_valid']}")
    print(f"  Inconclusive:       {result['inconclusive']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
source venv/bin/activate
pytest tests/test_email_scrub.py -v 2>&1
```

Expected:
```
tests/test_email_scrub.py::test_scrub_marks_invalid_as_skip PASSED
tests/test_email_scrub.py::test_scrub_keeps_valid PASSED
tests/test_email_scrub.py::test_scrub_dry_run_does_not_write PASSED
3 passed
```

- [ ] **Step 5: Smoke test dry-run against real DB**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
source venv/bin/activate
python3 email_scrub.py --limit 20 --dry-run 2>&1
```

Expected: summary showing checked/skip/valid counts, no DB changes.

- [ ] **Step 6: Run real scrub on first 50 pending contacts**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
source venv/bin/activate
python3 email_scrub.py --limit 50 2>&1
```

Check how many were marked skip:
```bash
sqlite3 outreach.db "SELECT COUNT(*) FROM contacts WHERE bounce='pre-check';"
```

- [ ] **Step 7: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add outreach_agent/email_scrub.py outreach_agent/tests/test_email_scrub.py
git commit -m "feat(outreach): add email_scrub.py — pre-verify pending contacts to cut bounce rate below 15% threshold"
```

---

## Task 4: Update rjm-discover SKILL.md

**Files:**
- Modify: `~/.claude/scheduled-tasks/rjm-discover/SKILL.md`

Add Part E (church miner) and Part F (fan miner) to the discover sequence. Update the header, hard limits, and Part A church query bank. Add `genre_fan` persona to the persona table.

- [ ] **Step 1: Read the full SKILL.md to identify exact insertion points**

```bash
cat "/Users/motomoto/.claude/scheduled-tasks/rjm-discover/SKILL.md" | head -60
```

Locate: (a) the "Four pipelines per run" bullet list, (b) the Personas table, (c) the Hard Execution Limits block, (d) the QUERY BANKS section, (e) the FINISH section.

- [ ] **Step 2: Update header — pipelines count and persona table**

Change "Four pipelines per run" to "Six pipelines per run" and add two bullets:
```
- **Part E:** Church miner (runs when church count < 100)
- **Part F:** Fan miner (runs always — target 10 contacts/run)
```

Add to the Personas table:
```
| `genre_fan` | music_share | worldwide |
```

- [ ] **Step 3: Update Hard Execution Limits — add Parts E and F**

Add after the Part D line:
```
- **Part E — Run when church count < 100** — 5 contacts target
- **Part F — Always run** — 10 contacts target
```

Update total wall time: `~7 minutes`

- [ ] **Step 4: Add CHURCH query bank to Part A QUERY BANKS**

After the existing CHURCH / RETREAT block, replace/expand with:

```
**CHURCH — TECHNO WORSHIP (2 slots — Europe priority, rotate regions):**
"youth church" OR "youth ministry" electronic music DJ [REGION] contact email 2026
"Jugendkirche" OR "Jugendgottesdienst" elektronisch DJ [REGION] Kontakt email 2026
"Jongerendienst" OR "jongeren" elektronische muziek DJ [REGION] contact email 2026
"church rave" OR "rave church" OR "worship rave" [REGION] contact booking email 2026
"charismatic church" OR "ICF church" OR "vineyard church" modern music DJ [REGION] contact
"Christian festival" electronic music DJ booking [REGION] contact email 2026
"techno worship" OR "house worship" OR "electronic worship" church [REGION] contact email
Hillsong OR "young and free" youth [REGION] music program booking contact email

[REGION] rotation: Netherlands · Germany · UK · Belgium · France · Spain · Italy ·
Switzerland · Denmark · Norway · Sweden · Poland · Portugal · Austria · Czech Republic ·
Hungary · Ireland · Finland · Greece
Do NOT repeat a region queried in last 48h.
```

- [ ] **Step 5: Add PART E and PART F sections before FINISH**

Add before the `## FINISH` section:

```markdown
## PART E — Church Miner

Run when church count < 100 (from Step 0 intelligence brief).

**Step E1 — Check church count:**
```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent" && sqlite3 outreach.db "SELECT COUNT(*) FROM contacts WHERE persona='church';"
```

If count < 100:
```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent" && python3 church_miner.py --limit 5 2>&1
```

Log:
```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent" && sqlite3 outreach.db "INSERT INTO discovery_log VALUES (NULL, 'church_miner:run', 'church', 0, datetime('now'));"
```

---

## PART F — Fan Miner

Run every discover cycle — always.

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent" && python3 fan_miner.py --limit 10 2>&1
```

Log:
```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent" && sqlite3 outreach.db "INSERT INTO discovery_log VALUES (NULL, 'fan_miner:run', 'genre_fan', 0, datetime('now'));"
```
```

- [ ] **Step 6: Update FINISH summary template**

Change the final log line to:
```
python3 master_agent.py log_run "discover: X contacts added (A faith_creator, B ecstatic_dance, C church, D photographer, E nomad, F fan, G other) + Y playlists + Z promoted" X+Y+Z rjm-discover
```

Change the final 4-line output to 6 lines:
```
Part A: ...
Part B: ...
Part C: ...
Part D: N ecstatic dance organizers added. [or: skipped — quota met (N≥50)]
Part E: N church contacts added. [or: skipped — quota met (N≥100)]
Part F: N fan contacts added.
```

- [ ] **Step 7: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add ~/.claude/scheduled-tasks/rjm-discover/SKILL.md
git commit -m "feat(discover): add Part E (church_miner) + Part F (fan_miner) to rjm-discover pipeline; add genre_fan persona + church query bank"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] church_miner.py — Task 1
- [x] fan_miner.py — Task 2 (50-100 fans/day across all runs: 10/run × 6 runs = 60/day)
- [x] email_scrub.py — Task 3
- [x] SKILL.md update with church query bank + Parts E & F — Task 4
- [x] Peer artist outreach — Jesus Loves Electro emails already added in this session (ole@jlemusic.com, ak@kkv.no). Padre Guilherme and REDEMPTIØN require Instagram DM — flag for manual action.

**Placeholder scan:** No TBDs, all code blocks complete, all commands have expected outputs.

**Type consistency:** All miners use same DB schema fields (persona, outreach_goal, warmth_score, faith_signals, date_added). `genre_fan` persona consistent across fan_miner.py, SKILL.md, and discover log.

**Flag for manual action (not automatable):**
- Instagram DM to Padre Guilherme (@padreguilhermeofficial) — suggest sharing a 30s clip of Halleluyah/Jericho + "love what you're doing with faith + club music, this is my angle too"
- Instagram DM to REDEMPTIØN — suggest collaboration on a UNDR CTRL event in Netherlands
