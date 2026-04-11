#!/usr/bin/env python3
"""
spotify_auto_logger.py — Daily automatic Spotify listener count scraper.

Uses Playwright (headless Chromium) to load the public Spotify artist page
and extract monthly listeners, then logs the result to the spotify_stats table.

Runs at most ONCE per day — silently exits if data is already logged today.

Usage:
  python3 spotify_auto_logger.py          # auto-detect, log if not already done today
  python3 spotify_auto_logger.py --force  # overwrite today's reading
  python3 spotify_auto_logger.py --dry-run # scrape and print, no DB write

Setup (one-time):
  pip install playwright
  playwright install chromium
"""

import argparse
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import db
import spotify_tracker

log = logging.getLogger("spotify.auto_logger")
logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)

ARTIST_ID  = "2Seaafm5k1hAuCkpdq7yds"
ARTIST_URL = f"https://open.spotify.com/artist/{ARTIST_ID}"

# Regex that matches "1,234,567 monthly listeners" (any locale formatting)
_LISTENERS_RE = re.compile(
    r'([\d][,.\s\d]*\d)\s+monthly\s+listener',
    re.IGNORECASE,
)


def _parse_listeners(text: str) -> int | None:
    """Extract monthly listener count from page text. Returns None if not found."""
    match = _LISTENERS_RE.search(text)
    if not match:
        return None
    raw = match.group(1)
    # Remove thousands separators (commas, dots, spaces) and parse
    cleaned = re.sub(r'[,.\s]', '', raw)
    try:
        return int(cleaned)
    except ValueError:
        return None


def scrape_listeners() -> int | None:
    """
    Launch headless Chromium, load Spotify artist page, extract monthly listeners.
    Returns the count or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    except ImportError:
        log.error(
            "Playwright not installed. Run:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )
        return None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx  = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = ctx.new_page()
            page.goto(ARTIST_URL, wait_until="domcontentloaded", timeout=30_000)

            # Wait for the listeners text to appear; try specific selector first
            listeners = None
            try:
                page.wait_for_selector(
                    'span:has-text("monthly listener")',
                    timeout=15_000,
                )
                text = page.inner_text('span:has-text("monthly listener")')
                listeners = _parse_listeners(text)
            except PlaywrightTimeout:
                pass

            # Fallback: full page text search
            if listeners is None:
                full_text = page.inner_text("body")
                listeners = _parse_listeners(full_text)

            if listeners is None:
                log.warning("Could not find monthly listeners on page — page may have changed")
                # Dump a snippet for debugging
                snippet = page.inner_text("body")[:500]
                log.debug("Page snippet: %s", snippet)

            return listeners
        finally:
            browser.close()


def _already_logged_today() -> bool:
    """Return True if spotify_stats already has a row for today."""
    today = str(date.today())
    spotify_tracker.ensure_table()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM spotify_stats WHERE date = ? LIMIT 1", (today,)
        ).fetchone()
    return row is not None


def run(force: bool = False, dry_run: bool = False) -> int | None:
    """
    Main entry: scrape listeners and log to DB.
    Returns listener count on success, None on failure.
    """
    if not force and not dry_run and _already_logged_today():
        log.info("Spotify listeners already logged today — skipping (use --force to overwrite)")
        return None

    log.info("Scraping Spotify artist page for monthly listeners…")
    listeners = scrape_listeners()

    if listeners is None:
        log.error("Failed to scrape Spotify listeners — manual logging required: rjm.py spotify log <n>")
        return None

    log.info("  → %s monthly listeners", f"{listeners:,}")

    if dry_run:
        log.info("[DRY RUN] Would log %d listeners to DB", listeners)
        return listeners

    spotify_tracker.cmd_log(listeners, source="auto_browser")
    return listeners


def main():
    parser = argparse.ArgumentParser(description="Auto-log Spotify monthly listeners via browser.")
    parser.add_argument("--force",   action="store_true", help="Overwrite today's reading if one exists")
    parser.add_argument("--dry-run", action="store_true", help="Scrape and print without writing to DB")
    args = parser.parse_args()

    result = run(force=args.force, dry_run=args.dry_run)
    if result is None and not (_already_logged_today() and not args.force):
        sys.exit(1)


if __name__ == "__main__":
    main()
