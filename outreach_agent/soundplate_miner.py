"""
soundplate_miner.py — Mine Soundplate.com for curator submission leads

Soundplate publishes public submission pages for playlists where curators
explicitly want music submissions. This makes them higher-intent leads than
cold web search — they're pre-qualified to receive pitches.

Strategy:
  1. Scrape Soundplate genre/search pages to collect submission page URLs
  2. For each submission page: extract curator name
  3. Use DuckDuckGo to find the curator's contact email
  4. Add to contacts table as type='curator'

Usage:
  python3 soundplate_miner.py              # full run
  python3 soundplate_miner.py --dry-run    # preview without writes
  python3 soundplate_miner.py --limit 10   # process first 10 pages
  python3 soundplate_miner.py --genres "melodic-techno,psytrance"

Called by rjm-discover SKILL.md Part C, or manually:
  python3 rjm.py discover soundplate
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import re
import sys
import time
import urllib.request
import urllib.parse
from typing import Any

sys.path.insert(0, os.path.dirname(__file__))

import db
import playlist_db

log = logging.getLogger("outreach.soundplate_miner")

# ─── Config ───────────────────────────────────────────────────────────────────
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HTTP_TIMEOUT = 15
_RATE_LIMIT_S = 2.0

_TARGET_GENRES = [
    "melodic-techno",
    "psytrance",
    "tribal",
    "ethnic-electronic",
    "progressive-trance",
    "organic-house",
    "melodic-house",
    "progressive-house",
    "afro-house",
    "downtempo",
]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


# ─── HTTP ─────────────────────────────────────────────────────────────────────

def _fetch(url: str) -> str:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.debug("fetch failed %s: %s", url, e)
        return ""


# ─── DDG search ───────────────────────────────────────────────────────────────

def _ddg_search(query: str, max_results: int = 5) -> list[dict]:
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        return list(DDGS().text(query, max_results=max_results))
    except Exception as e:
        log.debug("DDG failed for %r: %s", query, e)
        return []


# ─── Email extraction ─────────────────────────────────────────────────────────

def _extract_emails(text: str) -> list[str]:
    BAD_DOMAINS = {"soundplate.com", "spotify.com", "example.com"}
    BAD_PREFIXES = {"noreply", "no-reply", "privacy", "abuse", "support"}
    found = _EMAIL_RE.findall(text)
    result = []
    for e in found:
        parts = e.lower().split("@")
        if len(parts) != 2:
            continue
        local, domain = parts
        if any(d in domain for d in BAD_DOMAINS):
            continue
        if local in BAD_PREFIXES:
            continue
        if "." not in domain:
            continue
        result.append(e.lower())
    return list(dict.fromkeys(result))


def _find_email_for_curator(curator_name: str, genre: str) -> str:
    """
    Search DDG for the curator's contact email using targeted queries.
    Returns the best email found or '' if none.
    """
    queries = [
        f'"{curator_name}" spotify curator contact email',
        f'"{curator_name}" spotify playlist booking OR submission',
        f'{curator_name} {genre} playlist email contact',
    ]
    all_text = ""
    for q in queries:
        results = _ddg_search(q, max_results=4)
        for r in results:
            all_text += f" {r.get('title','')} {r.get('body','')} {r.get('href','')}"
        time.sleep(random.uniform(0.8, 1.5))

    emails = _extract_emails(all_text)
    if emails:
        # Prefer professional emails over gmail
        for e in emails:
            local = e.split("@")[0]
            if local in ("booking", "contact", "info", "submissions", "submit", "music", "hello", "promo"):
                return e
        return emails[0]
    return ""


# ─── Soundplate page scraping ─────────────────────────────────────────────────

def _get_submission_urls_for_genre(genre_slug: str) -> list[str]:
    """Collect Soundplate submission page URLs for a genre slug."""
    urls: list[str] = []
    pattern = re.compile(
        r'href="(https://soundplate\.com/[^"]*(?:submit-music-here|submit-your-music)[^"]*)"',
        re.IGNORECASE,
    )

    # Genre page
    html = _fetch(f"https://soundplate.com/genre/{genre_slug}/")
    if html:
        urls.extend(pattern.findall(html))
    time.sleep(_RATE_LIMIT_S)

    # Search page
    q = urllib.parse.quote(genre_slug.replace("-", " "))
    html2 = _fetch(f"https://soundplate.com/?s={q}")
    if html2:
        urls.extend(pattern.findall(html2))
    time.sleep(_RATE_LIMIT_S)

    return list(dict.fromkeys(urls))


def _extract_curator_from_page(html: str, page_url: str) -> str:
    """Extract curator name from a Soundplate submission page."""
    # "curated by <Name>" pattern
    m = re.search(r'[Cc]urated by\s+([A-Za-z0-9][A-Za-z0-9 \-\.]{3,60})', html)
    if m:
        name = m.group(1).strip().rstrip(".,")
        if len(name) > 4 and "soundplate" not in name.lower():
            return name

    # title tag
    m = re.search(r'<title>([^<]{5,100})</title>', html, re.IGNORECASE)
    if m:
        title = m.group(1)
        # Strip " - Soundplate.com" suffix
        title = re.sub(r'\s*[–—\-]\s*Soundplate.*$', '', title, flags=re.IGNORECASE)
        # Strip "[Submit Music Here]" parts
        title = re.sub(r'\s*[\[\(]Submit.*?[\]\)]', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\s*Spotify\s*Playlist.*$', '', title, flags=re.IGNORECASE)
        title = title.strip()
        if len(title) > 4:
            return title

    # URL slug fallback
    slug = page_url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r'-spotify-playlist-submit.*$', '', slug)
    slug = slug.replace("-", " ").title()
    return slug[:60]


def _extract_genre_from_page(html: str, page_url: str, fallback_genre: str) -> str:
    """Extract or infer genre from page content."""
    for gk in ["psytrance", "tribal", "melodic techno", "progressive", "organic house",
               "afro house", "downtempo", "ethnic"]:
        if gk in html.lower():
            return gk
    return fallback_genre.replace("-", " ")


# ─── Main discovery ───────────────────────────────────────────────────────────

def run_discovery(
    genres: list[str] | None = None,
    limit: int = 0,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Collect Soundplate submission pages, extract curator names,
    research their emails via DDG, add to contacts table.
    """
    db.init_db()
    playlist_db.init_playlist_db()

    genres = genres or _TARGET_GENRES
    summary = {
        "pages_found":    0,
        "pages_processed": 0,
        "curator_names_found": 0,
        "emails_found":   0,
        "contacts_added": 0,
        "duplicates":     0,
        "errors":         0,
    }

    # Collect all submission page URLs
    all_urls: list[str] = []
    for genre in genres:
        log.info("Collecting submission pages for: %s", genre)
        urls = _get_submission_urls_for_genre(genre)
        log.info("  → %d pages found", len(urls))
        all_urls.extend(urls)

    all_urls = list(dict.fromkeys(all_urls))
    summary["pages_found"] = len(all_urls)
    log.info("Total unique submission pages: %d", len(all_urls))

    if limit:
        all_urls = all_urls[:limit]

    for url in all_urls:
        summary["pages_processed"] += 1
        try:
            html = _fetch(url)
            if not html:
                continue
            time.sleep(_RATE_LIMIT_S)

            # Extract genre from URL path (before fetch)
            genre_slug = url.rstrip("/").rsplit("/", 1)[-1]
            genre_slug = re.sub(r'-spotify-playlist-submit.*$', '', genre_slug)
            genre_inferred = genre_slug.replace("-", " ")

            genre = _extract_genre_from_page(html, url, genre_inferred)
            curator_name = _extract_curator_from_page(html, url)

            if not curator_name or len(curator_name) < 4:
                log.debug("No curator name at %s — skipping", url)
                continue

            summary["curator_names_found"] += 1
            log.info("Curator: %s | Genre: %s", curator_name, genre)

            email = _find_email_for_curator(curator_name, genre)

            if not email:
                log.debug("No email found for %s", curator_name)
                continue

            summary["emails_found"] += 1
            log.info("Email: %s <%s>", curator_name, email)

            if dry_run:
                summary["contacts_added"] += 1
                continue

            notes = f"Soundplate submission curator | page: {url} | genre: {genre}"
            success, reason = db.add_contact(
                email=email,
                name=curator_name,
                ctype="curator",
                genre=genre,
                notes=notes,
                source="soundplate",
            )

            if success:
                summary["contacts_added"] += 1
                log.info("Added: %s <%s>", curator_name, email)
            elif "duplicate" in str(reason).lower():
                summary["duplicates"] += 1
            else:
                log.warning("Could not add %s: %s", email, reason)

        except Exception as e:
            log.warning("Error processing %s: %s", url, e)
            summary["errors"] += 1

    return summary


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Mine Soundplate for curator leads")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--genres", type=str, default="")
    args = parser.parse_args()

    genres = [g.strip() for g in args.genres.split(",") if g.strip()] or None
    summary = run_discovery(genres=genres, limit=args.limit, dry_run=args.dry_run)

    print(f"\n{'='*50}")
    for k, v in summary.items():
        print(f"  {k:<24} {v}")
    if args.dry_run:
        print(f"  [DRY RUN — no writes]")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
