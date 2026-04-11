#!/usr/bin/env python3
"""
RJM Curator Form Submitter

Finds and auto-submits to music submission forms (Google Forms, Typeform, Tally,
custom HTML forms) linked from curator websites stored in playlists.db.

Pipeline:
  1. Read playlists with curator_website / curator_contact_url (contact_found status)
  2. Fetch the page, scan for submission form links
  3. Also follow Linktree pages — many curators park their form there
  4. Open the form in Playwright, detect field labels, fill with RJM's standard data
  5. Submit and track in form_submissions table

Usage:
  python3 form_submitter.py run         # Find forms and submit
  python3 form_submitter.py scan        # Scan for forms only — no submit
  python3 form_submitter.py status      # Show submission stats
  python3 form_submitter.py preview     # Show next 5 forms without submitting
"""

import argparse
import asyncio
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from config import DB_PATH
from story import ARTIST, TRACKS

log = logging.getLogger("outreach.form_submitter")
logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)

# ─── Standard submission data ─────────────────────────────────────────────────
# One source of truth — edit here, propagates everywhere.

_PRIMARY   = TRACKS["tribal_techno"][0]   # Renamed — 130 BPM
_SECONDARY = TRACKS["psytrance"][0]       # Halleluyah — 140 BPM

SUBMISSION = {
    "artist_name":      ARTIST["full_name"],
    "email":            ARTIST["email"],
    "spotify_artist":   ARTIST["spotify_artist"],
    "track_title":      _PRIMARY["title"],
    "track_spotify":    _PRIMARY["spotify"],
    "track_bpm":        str(_PRIMARY["bpm"]),
    "track_title_2":    _SECONDARY["title"],
    "track_spotify_2":  _SECONDARY["spotify"],
    "genre":            "Tribal Psytrance / Melodic Techno",
    "location":         "Tenerife, Spain",
    "instagram":        "@robertjanmastenbroek",
    "website":          ARTIST["website"],
    "followers":        "290000",
    "bio_short": (
        "Dutch DJ/producer based in Tenerife. 30+ original tracks, fully independent. "
        "Tribal psytrance and melodic techno with biblical depth. 130–140 BPM. "
        "290K Instagram followers. Ancient truth. Future sound."
    ),
    "description": (
        "Renamed is a 130 BPM tribal techno track with ethnic percussion and English vocals. "
        "Part of a catalogue that blends ancient scripture with modern electronic production — "
        "sounds like Argy meets Vini Vici. All tracks independently owned."
    ),
    "press_kit": "https://robertjanmastenbroek.com",
}

# ─── Form detection patterns ──────────────────────────────────────────────────

# URLs that indicate a submission form
FORM_URL_PATTERNS = [
    re.compile(r"docs\.google\.com/forms", re.I),
    re.compile(r"forms\.gle/", re.I),
    re.compile(r"typeform\.com/to/", re.I),
    re.compile(r"tally\.so/r/", re.I),
    re.compile(r"jotform\.com/", re.I),
    re.compile(r"surveysparrow\.com/", re.I),
    re.compile(r"wufoo\.com/forms/", re.I),
    re.compile(r"airtable\.com/.*form", re.I),
]

# Link text that suggests a music submission form
FORM_LINK_TEXT = re.compile(
    r"(submit|submission|demo|music|track|add|pitch|playlist|send|apply)",
    re.I,
)

# ─── Field label → data mapping ───────────────────────────────────────────────
# Each entry: (regex to match label, key in SUBMISSION dict)
FIELD_MAP = [
    (re.compile(r"artist\s*name|your\s*name|name",           re.I), "artist_name"),
    (re.compile(r"email|e-mail",                              re.I), "email"),
    (re.compile(r"spotify.*artist|artist.*spotify|artist.*url|profile.*link", re.I), "spotify_artist"),
    (re.compile(r"track.*title|song.*title|track.*name|song.*name|title", re.I), "track_title"),
    (re.compile(r"spotify.*track|track.*link|song.*link|stream|music.*url", re.I), "track_spotify"),
    (re.compile(r"genre",                                     re.I), "genre"),
    (re.compile(r"bpm|tempo",                                 re.I), "track_bpm"),
    (re.compile(r"location|country|city|where.*based|based",  re.I), "location"),
    (re.compile(r"instagram|ig\b",                            re.I), "instagram"),
    (re.compile(r"website|web\b|homepage",                    re.I), "website"),
    (re.compile(r"follower|following",                        re.I), "followers"),
    (re.compile(r"bio|about|artist.*info|description|story",  re.I), "bio_short"),
    (re.compile(r"press.*kit|epk",                            re.I), "press_kit"),
]


def _value_for_label(label: str) -> str | None:
    """Return the submission value for a given form field label, or None."""
    for pattern, key in FIELD_MAP:
        if pattern.search(label):
            return SUBMISSION.get(key, "")
    return None


# ─── DB: form_submissions table ───────────────────────────────────────────────

_FORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS form_submissions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id     TEXT    NOT NULL,
    playlist_name   TEXT,
    form_url        TEXT    NOT NULL,
    form_type       TEXT,           -- google | typeform | tally | jotform | custom
    status          TEXT    DEFAULT 'pending',
    -- pending | submitted | failed | skipped
    date_submitted  TEXT,
    error_msg       TEXT,
    fields_filled   INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_form_playlist
    ON form_submissions(playlist_id, form_url);
"""


def _init_form_table():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(_FORM_SCHEMA)
    conn.commit()
    conn.close()


def _already_submitted(playlist_id: str, form_url: str) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT id FROM form_submissions WHERE playlist_id = ? AND form_url = ? AND status = 'submitted'",
        (playlist_id, form_url),
    ).fetchone()
    conn.close()
    return row is not None


def _mark_submitted(playlist_id: str, playlist_name: str, form_url: str,
                    form_type: str, fields_filled: int):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """
        INSERT OR REPLACE INTO form_submissions
            (playlist_id, playlist_name, form_url, form_type, status, date_submitted, fields_filled)
        VALUES (?, ?, ?, ?, 'submitted', ?, ?)
        """,
        (playlist_id, playlist_name, form_url, form_type, str(date.today()), fields_filled),
    )
    conn.commit()
    conn.close()


def _mark_form_failed(playlist_id: str, form_url: str, error: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """
        INSERT OR REPLACE INTO form_submissions
            (playlist_id, form_url, status, error_msg, date_submitted)
        VALUES (?, ?, 'failed', ?, ?)
        """,
        (playlist_id, form_url, error[:500], str(date.today())),
    )
    conn.commit()
    conn.close()


# ─── Candidate loading ────────────────────────────────────────────────────────

def _get_candidates(limit: int = 100) -> list[dict]:
    """Playlists with a website or contact URL that might have a submission form."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT spotify_id, name, curator_name, curator_website,
               curator_contact_url, curator_instagram, genre_tags, follower_count
        FROM playlists
        WHERE status IN ('contact_found', 'contacted')
          AND (
              (curator_website IS NOT NULL AND curator_website != '')
              OR (curator_contact_url IS NOT NULL AND curator_contact_url != '')
          )
        ORDER BY relevance_score DESC, follower_count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Web fetch helpers ────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch(url: str, timeout: int = 10) -> str:
    """Fetch a URL and return the HTML. Returns empty string on error."""
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.debug("Fetch failed %s: %s", url, e)
        return ""


def _find_form_links(html: str, base_url: str = "") -> list[str]:
    """
    Scan HTML for links to known submission form services.
    Returns a deduplicated list of form URLs.
    """
    forms = []

    # Direct form service URLs in href attributes
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        href = m.group(1).strip()
        if any(p.search(href) for p in FORM_URL_PATTERNS):
            forms.append(href)

    # Anchor text like "Submit music" pointing to any URL
    for m in re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>', html, re.I):
        href, text = m.group(1), m.group(2)
        if FORM_LINK_TEXT.search(text):
            # Check if href itself is a form or could lead to one
            if href.startswith("http") and not href.endswith((".mp3", ".pdf", ".jpg", ".png")):
                forms.append(href)

    # Deduplicate, preserving order
    seen = set()
    result = []
    for f in forms:
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result


def _follow_linktree(url: str) -> list[str]:
    """Fetch a Linktree page and extract all linked URLs."""
    html = _fetch(url)
    if not html:
        return []
    links = re.findall(r'"url"\s*:\s*"(https?://[^"]+)"', html)
    links += re.findall(r'href=["](https?://[^"]+)["]', html)
    return list(set(links))


def _classify_form(url: str) -> str:
    """Return a short string identifying the form service."""
    if "google.com/forms" in url or "forms.gle" in url:
        return "google"
    if "typeform.com" in url:
        return "typeform"
    if "tally.so" in url:
        return "tally"
    if "jotform.com" in url:
        return "jotform"
    if "airtable.com" in url:
        return "airtable"
    return "custom"


# ─── Playwright form filling ──────────────────────────────────────────────────

async def _fill_and_submit(page, form_url: str, dry_run: bool = False) -> int:
    """
    Navigate to form_url, detect fields, fill them.
    Returns number of fields filled. Submits unless dry_run=True.
    """
    await page.goto(form_url, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(2)

    fields_filled = 0
    form_type = _classify_form(form_url)

    # ── Collect all visible text inputs, textareas, and selects ─────────────
    inputs = await page.query_selector_all(
        'input[type="text"], input[type="email"], input[type="url"], '
        'input[type="number"], textarea'
    )

    for inp in inputs:
        # Try to get label from: aria-label, placeholder, nearby <label>, parent text
        label = ""
        aria  = await inp.get_attribute("aria-label") or ""
        placeholder = await inp.get_attribute("placeholder") or ""
        input_id = await inp.get_attribute("id") or ""

        # Try associated <label> element
        if input_id:
            label_el = await page.query_selector(f'label[for="{input_id}"]')
            if label_el:
                label = (await label_el.inner_text()).strip()

        # Google Forms wraps in divs with role="heading" — grab nearest heading
        if not label and form_type == "google":
            try:
                heading = await inp.evaluate(
                    """el => {
                        let p = el.closest('[role="listitem"]') || el.closest('.freebirdFormviewerViewItemsItemItem');
                        if (p) {
                            let h = p.querySelector('[role="heading"]') || p.querySelector('.freebirdFormviewerViewItemsItemItemTitle');
                            return h ? h.textContent : '';
                        }
                        return '';
                    }"""
                )
                label = (heading or "").strip()
            except Exception:
                pass

        # Typeform uses data-qa attributes
        if not label and form_type == "typeform":
            try:
                heading = await inp.evaluate(
                    """el => {
                        let p = el.closest('[data-qa="question-form-item"]');
                        if (p) {
                            let h = p.querySelector('h1,h2,[class*="question"]');
                            return h ? h.textContent : '';
                        }
                        return '';
                    }"""
                )
                label = (heading or "").strip()
            except Exception:
                pass

        # Fallback: use placeholder or aria-label
        if not label:
            label = aria or placeholder

        if not label:
            continue

        value = _value_for_label(label)
        if value is None:
            log.debug("  No mapping for field: %r", label[:60])
            continue

        try:
            is_readonly = await inp.get_attribute("readonly")
            is_disabled = await inp.get_attribute("disabled")
            if is_readonly or is_disabled:
                continue

            await inp.click()
            await inp.fill("")
            await inp.type(value, delay=30)
            fields_filled += 1
            log.debug("  ✓ Filled %r → %r", label[:40], value[:40])
        except Exception as e:
            log.debug("  Could not fill %r: %s", label[:40], e)

    # ── Handle radio / checkbox for genre selects ────────────────────────────
    for radio in await page.query_selector_all('div[role="radio"], div[role="checkbox"]'):
        try:
            text = (await radio.inner_text()).strip().lower()
            genre_lower = SUBMISSION["genre"].lower()
            if any(w in text for w in ["techno", "psy", "tribal", "trance", "electronic"]):
                await radio.click()
                fields_filled += 1
                log.debug("  ✓ Clicked option: %r", text[:40])
                break
        except Exception:
            pass

    log.info("  %d fields filled", fields_filled)

    if dry_run or fields_filled == 0:
        return fields_filled

    # ── Submit ────────────────────────────────────────────────────────────────
    submit_btn = None
    for selector in [
        'input[type="submit"]',
        'button[type="submit"]',
        'button:has-text("Submit")',
        'button:has-text("Send")',
        'button:has-text("submit")',
        '[aria-label="Submit"]',
        '.freebirdFormviewerViewNavigationSubmitButton',   # Google Forms
        '[data-qa="submit-button"]',                       # Typeform
    ]:
        try:
            btn = await page.query_selector(selector)
            if btn and await btn.is_visible():
                submit_btn = btn
                break
        except Exception:
            pass

    if submit_btn:
        await submit_btn.click()
        await asyncio.sleep(3)
        log.info("  ✅ Form submitted")
    else:
        log.warning("  ⚠️  Submit button not found — fields filled but not submitted")

    return fields_filled


# ─── Per-playlist processing ──────────────────────────────────────────────────

async def _process_playlist(browser, playlist: dict, dry_run: bool = False) -> int:
    """
    Find and submit forms for a single playlist.
    Returns number of forms submitted.
    """
    pid   = playlist["spotify_id"]
    pname = playlist.get("name", pid)
    submitted = 0

    # Collect candidate URLs to scan
    urls_to_scan = []
    for field in ("curator_website", "curator_contact_url"):
        val = (playlist.get(field) or "").strip()
        if val and val.startswith("http"):
            urls_to_scan.append(val)

    if not urls_to_scan:
        return 0

    form_urls_found = []

    for url in urls_to_scan:
        log.info("  Scanning %s", url)
        html = _fetch(url)
        if not html:
            continue

        # Direct form links on the page
        links = _find_form_links(html, base_url=url)
        form_urls_found.extend(links)

        # If it's a Linktree, follow all links and scan each
        if "linktr.ee" in url or "linktree" in url.lower():
            sub_links = _follow_linktree(url)
            for sub in sub_links:
                sub_html = _fetch(sub)
                if sub_html:
                    form_urls_found.extend(_find_form_links(sub_html, base_url=sub))
                # Also check if the sub_link itself is a form
                if any(p.search(sub) for p in FORM_URL_PATTERNS):
                    form_urls_found.append(sub)

        time.sleep(1)

    # Deduplicate
    seen = set()
    unique_forms = []
    for f in form_urls_found:
        if f not in seen and not _already_submitted(pid, f):
            seen.add(f)
            unique_forms.append(f)

    if not unique_forms:
        log.info("  No new forms found for %s", pname[:50])
        return 0

    log.info("  %d form(s) found for %s", len(unique_forms), pname[:50])

    for form_url in unique_forms:
        form_type = _classify_form(form_url)
        log.info("  → %s form: %s", form_type, form_url[:80])

        try:
            context = await browser.new_context()
            page    = await context.new_page()

            fields = await _fill_and_submit(page, form_url, dry_run=dry_run)

            await context.close()

            if fields > 0 and not dry_run:
                _mark_submitted(pid, pname, form_url, form_type, fields)
                submitted += 1
            elif fields == 0:
                log.info("  ⚠️  No fields mapped — skipping")
                _mark_form_failed(pid, form_url, "no_fields_mapped")

        except Exception as e:
            log.error("  Form error (%s): %s", form_url[:60], e)
            _mark_form_failed(pid, form_url, str(e)[:300])

        time.sleep(2)

    return submitted


# ─── Commands ─────────────────────────────────────────────────────────────────

async def _run_async(dry_run: bool = False, scan_only: bool = False, limit: int = 20):
    from playwright.async_api import async_playwright  # type: ignore

    _init_form_table()
    candidates = _get_candidates(limit=limit)

    if not candidates:
        log.info("No candidates with websites. Run find_contacts.py first.")
        return

    log.info("%d playlists to process", len(candidates))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        total_submitted = 0
        for i, playlist in enumerate(candidates, 1):
            log.info(
                "[%d/%d] %s",
                i, len(candidates), playlist.get("name", playlist["spotify_id"])[:55],
            )

            if scan_only:
                # Just log what we'd find
                for field in ("curator_website", "curator_contact_url"):
                    url = (playlist.get(field) or "").strip()
                    if url and url.startswith("http"):
                        html = _fetch(url)
                        links = _find_form_links(html) if html else []
                        if links:
                            print(f"  {playlist.get('name','')[:45]}")
                            for l in links:
                                print(f"    → {l}")
            else:
                n = await _process_playlist(browser, playlist, dry_run=dry_run)
                total_submitted += n

            time.sleep(1.5)

        await browser.close()

    if not scan_only:
        action = "would submit" if dry_run else "submitted"
        log.info("Done — %d form(s) %s", total_submitted, action)


def run(limit: int = 20):
    asyncio.run(_run_async(dry_run=False, scan_only=False, limit=limit))


def scan():
    asyncio.run(_run_async(dry_run=False, scan_only=True, limit=50))


def preview():
    asyncio.run(_run_async(dry_run=True, scan_only=False, limit=5))


def status():
    _init_form_table()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT status, COUNT(*) as n FROM form_submissions GROUP BY status"
    ).fetchall()
    today = conn.execute(
        "SELECT COUNT(*) FROM form_submissions WHERE date_submitted = ? AND status = 'submitted'",
        (str(date.today()),),
    ).fetchone()[0]
    candidates = conn.execute(
        """
        SELECT COUNT(*) FROM playlists
        WHERE status IN ('contact_found','contacted')
          AND (
              (curator_website IS NOT NULL AND curator_website != '')
              OR (curator_contact_url IS NOT NULL AND curator_contact_url != '')
          )
        """
    ).fetchone()[0]
    conn.close()

    print(f"\n{'='*42}")
    print(f"  Curator Form Submissions")
    print(f"{'='*42}")
    for r in rows:
        print(f"  {r['status']:<16} {r['n']}")
    print(f"{'─'*42}")
    print(f"  Submitted today:  {today}")
    print(f"  Eligible playlists: {candidates} (with websites)")
    print(f"{'='*42}\n")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Curator form submitter for RJM")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run",     help="Find and submit forms")
    sub.add_parser("scan",    help="Scan for forms only (no submit)")
    sub.add_parser("preview", help="Preview next 5 forms without submitting")
    sub.add_parser("status",  help="Show submission stats")
    args = parser.parse_args()

    if args.cmd == "run":
        run()
    elif args.cmd == "scan":
        scan()
    elif args.cmd == "preview":
        preview()
    elif args.cmd == "status":
        status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
