#!/usr/bin/env python3
"""
RJM Playlist Contact Finder

For each verified playlist in the DB (no contact yet), searches the web
for the curator's contact info: email, Instagram, website.

Usage:
  python3 find_contacts.py              # process all verified playlists
  python3 find_contacts.py --limit 20   # process first 20
  python3 find_contacts.py --dry-run    # show what would be searched, no DB writes
  python3 find_contacts.py --status     # show summary only

Strategy per playlist:
  1. Search DuckDuckGo: "<curator name> spotify contact"
  2. Search DuckDuckGo: "<playlist name> spotify submit music"
  3. Fetch the Spotify embed page and scan description for contact info
  4. Extract emails, Instagram handles, websites from all results
  5. Mark contact_found in DB if anything useful is found
"""

import sys
import os
import re
import time
import json
import random
import logging
import argparse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import db
import playlist_db
from config import MAX_CONTACTS_FOUND_PER_DAY

logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO
)
log = logging.getLogger("find_contacts")


# ─── Regex patterns ──────────────────────────────────────────────────────────

EMAIL_RE = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'
)
INSTAGRAM_RE = re.compile(
    r'(?:instagram\.com/|@)([\w.]+)',
    re.IGNORECASE
)
WEBSITE_RE = re.compile(
    r'https?://(?!open\.spotify|spotify\.com|instagram\.com|facebook\.com|twitter\.com|linktr\.ee|bit\.ly)[^\s\'"<>]+',
    re.IGNORECASE
)
LINKTREE_RE = re.compile(
    r'linktr\.ee/([\w.]+)',
    re.IGNORECASE
)

# Domains to ignore in results (not useful contact info)
IGNORE_DOMAINS = {
    'spotify.com', 'open.spotify.com', 'google.com', 'youtube.com',
    'facebook.com', 'twitter.com', 'soundcloud.com', 'beatport.com',
    'apple.com', 'amazon.com', 'tidal.com', 'deezer.com', 'wikipedia.org',
    'reddit.com', 'discogs.com', 'resident-advisor.net', 'ra.co',
    'mixcloud.com', 'bandcamp.com',
}


# ─── Spotify embed description fetcher ───────────────────────────────────────

def fetch_spotify_description(spotify_id: str) -> str:
    """
    Fetch the Spotify embed page and extract the playlist description.
    Spotify embeds render some data in a <script id="initial-state"> tag.
    """
    url = f"https://open.spotify.com/embed/playlist/{spotify_id}"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        # Look for description in initial state JSON
        state_match = re.search(
            r'<script id="initial-state" type="text/plain">([^<]+)</script>',
            html
        )
        if state_match:
            try:
                import base64
                data = json.loads(base64.b64decode(state_match.group(1)).decode('utf-8'))
                # Navigate the nested structure to find description
                desc = _dig(data, 'data', 'playlistV2', 'description')
                if desc:
                    return desc
            except Exception:
                pass

        # Fallback: look for description in any JSON blob
        json_matches = re.findall(r'"description"\s*:\s*"([^"]{10,})"', html)
        for m in json_matches:
            if any(c in m for c in ['@', '.com', 'mail', 'contact', 'email', 'ig:', 'instagram']):
                return m

        return ""
    except Exception as e:
        log.debug(f"  embed fetch failed for {spotify_id}: {e}")
        return ""


def _dig(obj, *keys):
    for k in keys:
        if isinstance(obj, dict):
            obj = obj.get(k)
        else:
            return None
        if obj is None:
            return None
    return obj


# ─── DuckDuckGo search ────────────────────────────────────────────────────────

def ddg_search(query: str, max_results: int = 5) -> list[dict]:
    """Run a DuckDuckGo/Bing search and return results."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        results = list(DDGS().text(query, max_results=max_results))
        return results
    except Exception as e:
        log.debug(f"  DDG search failed: {e}")
        return []


# ─── Contact extraction ───────────────────────────────────────────────────────

def extract_contacts(text: str) -> dict:
    """Extract email, instagram, linktree, website from text."""
    contacts = {
        'email': None,
        'instagram': None,
        'linktree': None,
        'website': None,
    }

    # Email — filter out generic/platform addresses
    emails = EMAIL_RE.findall(text)
    BAD_EMAIL_PREFIXES = {'noreply', 'no-reply', 'support', 'press@spotify', 'privacy', 'abuse'}
    BAD_EMAIL_DOMAINS = {'spotify.com', 'example.com', 'w3.org', 'schema.org', 'sentry.io'}
    good_emails = [
        e for e in emails
        if not any(e.lower().startswith(p) for p in BAD_EMAIL_PREFIXES)
        and not any(e.lower().endswith('@' + d) for d in BAD_EMAIL_DOMAINS)
        and '.' in e.split('@')[-1]
    ]
    if good_emails:
        contacts['email'] = good_emails[0]

    # Instagram
    ig = INSTAGRAM_RE.findall(text)
    ig_filtered = [h for h in ig if len(h) > 2 and h.lower() not in {
        'spotify', 'instagram', 'facebook', 'twitter', 'youtube', 'reels',
        'stories', 'explore', 'p', 'reel', 'tv'
    }]
    if ig_filtered:
        contacts['instagram'] = '@' + ig_filtered[0]

    # Linktree
    lt = LINKTREE_RE.findall(text)
    if lt:
        contacts['linktree'] = f"https://linktr.ee/{lt[0]}"

    # Website (non-social, non-spotify)
    websites = WEBSITE_RE.findall(text)
    for w in websites:
        # Check it's not a social or big platform
        clean = w.rstrip('.,;)')
        domain = re.sub(r'^https?://', '', clean).split('/')[0].lower()
        if not any(ig_d in domain for ig_d in IGNORE_DOMAINS):
            contacts['website'] = clean
            break

    return contacts


def has_contact(c: dict) -> bool:
    # Only count real actionable contacts — email or Instagram (not just a website URL)
    return bool(c.get('email') or c.get('instagram') or c.get('linktree'))


def contacts_summary(c: dict) -> str:
    parts = []
    if c.get('email'):    parts.append(f"email: {c['email']}")
    if c.get('instagram'): parts.append(f"ig: {c['instagram']}")
    if c.get('linktree'):  parts.append(f"linktree: {c['linktree']}")
    if c.get('website'):   parts.append(f"web: {c['website']}")
    return ' | '.join(parts) if parts else 'none found'


# ─── Main processing ──────────────────────────────────────────────────────────

def process_playlist(p: dict, dry_run: bool = False) -> bool:
    """
    Find contact info for a single playlist.
    Returns True if contact info was found and saved.
    """
    spotify_id   = p['spotify_id']
    name         = p['name'] or spotify_id
    curator      = p['curator_name'] or ''

    log.info(f"▶ {name[:60]} (curator: {curator or '?'})")

    all_text = ""
    found = {}

    # ── Step 1: Spotify embed description ─────────────────────────────────
    desc = fetch_spotify_description(spotify_id)
    if desc:
        log.debug(f"  description: {desc[:120]}")
        all_text += " " + desc
        found = extract_contacts(desc)
        if has_contact(found):
            log.info(f"  ✅ Found in description: {contacts_summary(found)}")

    # ── Step 2: DDG search — curator name ─────────────────────────────────
    if not has_contact(found) and curator and curator.lower() not in ('unknown', ''):
        # Try multiple queries for better coverage
        queries = [
            f'{curator} spotify curator contact email',
            f'{curator} spotify playlist booking OR submissions',
            f'site:instagram.com {curator}',
        ]
        for query in queries:
            if has_contact(found):
                break
            log.debug(f"  searching: {query}")
            results = ddg_search(query, max_results=5)
            for r in results:
                blob = f"{r.get('title','')} {r.get('body','')} {r.get('href','')}"
                all_text += " " + blob
            found = extract_contacts(all_text)
            time.sleep(random.uniform(1.0, 2.0))
        if has_contact(found):
            log.info(f"  ✅ Found via curator search: {contacts_summary(found)}")

    # ── Step 3: DDG search — playlist name ────────────────────────────────
    if not has_contact(found):
        query = f'{name[:50]} spotify playlist submit OR curator OR contact'
        log.debug(f"  searching: {query}")
        results = ddg_search(query, max_results=5)
        for r in results:
            blob = f"{r.get('title','')} {r.get('body','')} {r.get('href','')}"
            all_text += " " + blob
        found = extract_contacts(all_text)
        if has_contact(found):
            log.info(f"  ✅ Found via playlist search: {contacts_summary(found)}")
        time.sleep(random.uniform(1.0, 2.0))

    # ── Save to DB ─────────────────────────────────────────────────────────
    if has_contact(found):
        if not dry_run:
            playlist_db.mark_contact_found(
                spotify_id,
                email=found.get('email'),
                instagram=found.get('instagram'),
                website=found.get('linktree') or found.get('website'),
                contact_notes=contacts_summary(found),
            )
        return True
    else:
        log.info(f"  ⚠️  No contact found")
        # Still mark progress so we don't re-search (store in notes)
        if not dry_run:
            playlist_db.update_playlist(
                spotify_id,
                notes=f"{p.get('notes','')}\n[contact_search: no results {time.strftime('%Y-%m-%d')}]"
            )
        return False


def build_curator_contact_map() -> dict:
    """
    Build a map of curator_name → contact info from playlists that already have contacts.
    Used to cross-reference and fill in contacts for verified playlists by the same curator.
    """
    import sqlite3
    from config import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT curator_name, curator_email, curator_instagram, curator_website, contact_notes
        FROM playlists
        WHERE status = 'contact_found'
          AND curator_name IS NOT NULL AND curator_name != ''
          AND curator_name != 'Unknown' AND curator_name != 'unknown'
    """).fetchall()
    conn.close()
    result = {}
    for r in rows:
        name = r['curator_name'].lower().strip()
        if name and (r['curator_email'] or r['curator_instagram']):
            result[name] = {
                'email': r['curator_email'],
                'instagram': r['curator_instagram'],
                'website': r['curator_website'],
                'contact_notes': r['contact_notes'],
            }
    return result


def apply_cross_reference(playlists: list, curator_map: dict, dry_run: bool) -> tuple[list, int]:
    """
    For playlists whose curator already has known contact info, fill it in immediately.
    Returns (remaining_playlists, filled_count).
    """
    remaining = []
    filled = 0
    for p in playlists:
        name = (p.get('curator_name') or '').lower().strip()
        if name and name in curator_map:
            c = curator_map[name]
            log.info(f"↩ Cross-ref: {p['name'][:50]} ← {p['curator_name']}")
            if not dry_run:
                playlist_db.mark_contact_found(
                    p['spotify_id'],
                    email=c.get('email'),
                    instagram=c.get('instagram'),
                    website=c.get('website'),
                    contact_notes=f"cross-ref from same curator | {c.get('contact_notes','')}",
                )
            filled += 1
        else:
            remaining.append(p)
    return remaining, filled


def main():
    parser = argparse.ArgumentParser(description='Find contact info for verified playlists')
    parser.add_argument('--limit', type=int, default=0, help='Max playlists to process via web search (0=all)')
    parser.add_argument('--dry-run', action='store_true', help='Search but do not write to DB')
    parser.add_argument('--status', action='store_true', help='Show DB summary only')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')
    parser.add_argument('--no-search', action='store_true', help='Only apply cross-references, skip web search')
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    playlist_db.init_playlist_db()

    if args.status:
        s = playlist_db.get_summary()
        print(f"\n=== Playlist Contact Status ===")
        print(f"  Total:         {s.get('_total', 0)}")
        print(f"  contact_found: {s.get('contact_found', 0)}")
        print(f"  verified:      {s.get('verified', 0)}  (need contacts)")
        print(f"  contacted:     {s.get('contacted', 0)}")
        print(f"  responded:     {s.get('responded', 0)}")
        print()
        return

    playlists = playlist_db.get_playlists_by_status('verified', limit=200)

    # Skip playlists already searched
    playlists = [p for p in playlists if '[contact_search:' not in (p.get('notes') or '')]

    log.info(f"Found {len(playlists)} verified playlists needing contacts")
    if args.dry_run:
        log.info("DRY RUN — no DB writes")

    # ── Step 1: Cross-reference known curators ────────────────────────────
    log.info("\n── Phase 1: Cross-referencing known curators ──")
    curator_map = build_curator_contact_map()
    log.info(f"  {len(curator_map)} curators with known contact info")
    playlists, cross_ref_count = apply_cross_reference(playlists, curator_map, dry_run=args.dry_run)
    log.info(f"  Filled {cross_ref_count} playlists via cross-reference")
    log.info(f"  {len(playlists)} playlists still need contact research")

    if args.no_search:
        print(f"\n{'='*50}")
        print(f"  Cross-ref: {cross_ref_count} filled")
        print(f"  Remaining: {len(playlists)} still need contacts")
        print(f"{'='*50}\n")
        return

    # ── Step 2: Web search for remaining playlists ────────────────────────
    log.info("\n── Phase 2: Web search for remaining playlists ──")
    to_search = playlists[:args.limit] if args.limit else playlists

    if not args.dry_run:
        db.init_db()
        already_found = db.today_contacts_found()
        if already_found >= MAX_CONTACTS_FOUND_PER_DAY:
            log.info(
                "Daily discovery cap reached (%d/%d) — skipping web search",
                already_found, MAX_CONTACTS_FOUND_PER_DAY,
            )
            to_search = []
        else:
            remaining_cap = MAX_CONTACTS_FOUND_PER_DAY - already_found
            if args.limit:
                to_search = to_search[:min(args.limit, remaining_cap)]
            else:
                to_search = to_search[:remaining_cap]
            log.info("Discovery quota: %d found today, %d slots remaining",
                     already_found, remaining_cap)

    found_count = 0
    for i, p in enumerate(to_search, 1):
        log.info(f"\n[{i}/{len(to_search)}] ─────────────────────────────")
        found = process_playlist(p, dry_run=args.dry_run)
        if found:
            found_count += 1
            if not args.dry_run:
                db.increment_contacts_found()
        time.sleep(random.uniform(0.5, 1.5))

    print(f"\n{'='*50}")
    print(f"  Cross-ref fills: {cross_ref_count}")
    print(f"  Web search finds: {found_count}/{len(to_search)}")
    print(f"  Total new contacts: {cross_ref_count + found_count}")
    print(f"{'='*50}\n")


if __name__ == '__main__':
    main()
