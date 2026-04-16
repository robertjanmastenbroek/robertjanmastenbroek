#!/usr/bin/env python3
"""
playlist_to_contacts.py — Bridge: playlists table → contacts table

The playlists table has curator emails from find_contacts.py research.
Those contacts NEVER get emailed until they appear in the contacts table.
This script promotes them.

Usage:
  python3 playlist_to_contacts.py           # promote all contact_found playlists
  python3 playlist_to_contacts.py --dry-run # preview without DB writes
  python3 playlist_to_contacts.py --status  # show summary counts
  python3 playlist_to_contacts.py --limit N # cap promotions at N

Called automatically by:
  - rjm-discover (Part C) after each run
  - run_cycle.py when the send queue runs low
  - rjm.py discover playlists
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import db
import playlist_db

log = logging.getLogger("outreach.playlist_to_contacts")

# ─── Size tier mapping ────────────────────────────────────────────────────────
# Map playlist follower count → playlist_size label used by the batch allocator.
def _size_tier(follower_count: int | None) -> str:
    if not follower_count:
        return "small"
    if follower_count < 10_000:
        return "small"
    if follower_count < 50_000:
        return "medium"
    return "large"


# ─── Genre normalization ──────────────────────────────────────────────────────
def _normalize_genre(genre_tags: str | None) -> str:
    """
    Playlist genre_tags are comma-separated (e.g. 'tribal,psytrance,ethnic').
    Contacts use a free-text genre field. Just clean and return as-is — the
    playlist_enricher and template_engine both handle comma-separated genre strings.
    """
    if not genre_tags:
        return ""
    return genre_tags.strip().rstrip(",")


# ─── Notes builder ────────────────────────────────────────────────────────────
def _build_notes(playlist: dict) -> str:
    """
    Build the notes field for the contact from playlist metadata.
    This is what the template engine uses for personalization context.
    """
    parts: list[str] = []

    name = playlist.get("name", "") or ""
    if name:
        parts.append(f"Playlist: {name}")

    followers = playlist.get("follower_count")
    if followers:
        parts.append(f"{followers:,} followers")

    contact_notes = (playlist.get("contact_notes") or "").strip()
    if contact_notes:
        parts.append(contact_notes)

    website = (playlist.get("curator_website") or "").strip()
    if website:
        parts.append(f"web: {website}")

    instagram = (playlist.get("curator_instagram") or "").strip()
    if instagram:
        parts.append(f"ig: {instagram}")

    best_track = (playlist.get("best_track_match") or "").strip()
    if best_track:
        parts.append(f"best track fit: {best_track}")

    playlist_url = (playlist.get("playlist_url") or "").strip()
    if playlist_url:
        parts.append(f"spotify: {playlist_url}")

    return " | ".join(parts)


# ─── Core promotion logic ─────────────────────────────────────────────────────

def promote_playlist_contacts(
    limit: int = 0,
    dry_run: bool = False,
    min_followers: int = 0,
) -> dict[str, int]:
    """
    Fetch all playlists with status='contact_found' that have a real email,
    then add each to the contacts table as type='curator'.

    Returns:
        {
          'candidates': <total playlists with email>,
          'promoted':   <successfully added to contacts>,
          'skipped_dup': <already in contacts>,
          'skipped_no_email': <no usable email>,
          'skipped_followers': <below min_followers>,
        }
    """
    db.init_db()
    playlist_db.init_playlist_db()

    # Query all contact_found playlists with a real email
    with playlist_db.get_conn() as conn:
        rows = conn.execute("""
            SELECT spotify_id, name, curator_name, curator_email,
                   curator_website, curator_instagram, curator_contact_url,
                   contact_notes, genre_tags, follower_count, best_track_match,
                   playlist_url
            FROM playlists
            WHERE status = 'contact_found'
              AND curator_email IS NOT NULL
              AND TRIM(curator_email) != ''
            ORDER BY follower_count DESC
        """).fetchall()
    playlists = [dict(r) for r in rows]

    summary = {
        "candidates": len(playlists),
        "promoted": 0,
        "skipped_dup": 0,
        "skipped_no_email": 0,
        "skipped_followers": 0,
        "skipped_bad_email": 0,
    }

    promoted_count = 0
    for p in playlists:
        # Hard limit
        if limit and promoted_count >= limit:
            break

        email = (p.get("curator_email") or "").strip().lower()
        if not email or "@" not in email or "." not in email.split("@")[-1]:
            summary["skipped_bad_email"] += 1
            continue

        # Filter out obviously bad emails
        if any(bad in email for bad in ["noreply", "no-reply", "privacy@", "abuse@", "support@spotify"]):
            summary["skipped_bad_email"] += 1
            continue

        followers = p.get("follower_count") or 0
        if min_followers and followers < min_followers:
            summary["skipped_followers"] += 1
            continue

        curator_name = (p.get("curator_name") or "").strip()
        playlist_name = (p.get("name") or "").strip()
        # Use curator name if meaningful, else playlist name
        contact_name = curator_name if len(curator_name) > 2 else playlist_name

        genre = _normalize_genre(p.get("genre_tags"))
        notes = _build_notes(p)
        size = _size_tier(followers)

        if dry_run:
            log.info("[DRY] Would promote: %s <%s> | genre=%s | followers=%s",
                     contact_name, email, genre, followers)
            promoted_count += 1
            summary["promoted"] += 1
            continue

        success, reason = db.add_contact(
            email=email,
            name=contact_name,
            ctype="curator",
            genre=genre,
            notes=notes,
            source="playlist_db",
        )

        if success:
            # Also set playlist_size on the new contact
            try:
                with db.get_conn() as conn:
                    conn.execute(
                        "UPDATE contacts SET playlist_size = ? WHERE email = ?",
                        (size, email),
                    )
            except Exception:
                pass  # non-fatal

            # Mark the playlist as 'contacted_queued' so we don't re-promote
            try:
                with playlist_db.get_conn() as conn:
                    conn.execute(
                        "UPDATE playlists SET status = 'outreach_queued' WHERE spotify_id = ?",
                        (p["spotify_id"],),
                    )
            except Exception:
                pass  # non-fatal

            log.info("Promoted: %s <%s> [%s followers, %s]",
                     contact_name[:40], email, followers, size)
            promoted_count += 1
            summary["promoted"] += 1
        else:
            if "duplicate" in str(reason).lower():
                summary["skipped_dup"] += 1
                # Still mark as outreach_queued so we don't keep re-checking
                try:
                    with playlist_db.get_conn() as conn:
                        conn.execute(
                            "UPDATE playlists SET status = 'outreach_queued' WHERE spotify_id = ?",
                            (p["spotify_id"],),
                        )
                except Exception:
                    pass
            else:
                log.warning("Could not promote %s: %s", email, reason)

    return summary


# ─── Status report ────────────────────────────────────────────────────────────

def print_status():
    """Print a bridge status summary."""
    db.init_db()
    playlist_db.init_playlist_db()

    with playlist_db.get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
        contact_found = conn.execute(
            "SELECT COUNT(*) FROM playlists WHERE status = 'contact_found'"
        ).fetchone()[0]
        contact_found_with_email = conn.execute(
            "SELECT COUNT(*) FROM playlists WHERE status = 'contact_found' "
            "AND curator_email IS NOT NULL AND TRIM(curator_email) != ''"
        ).fetchone()[0]
        outreach_queued = conn.execute(
            "SELECT COUNT(*) FROM playlists WHERE status = 'outreach_queued'"
        ).fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM playlists WHERE status = 'verified'"
        ).fetchone()[0]

    with db.get_conn() as conn:
        curators_from_playlists = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE source = 'playlist_db'"
        ).fetchone()[0]

    print(f"\n=== Playlist → Contacts Bridge ===")
    print(f"  Playlists total:          {total}")
    print(f"  contact_found:            {contact_found}")
    print(f"    → with email:           {contact_found_with_email}  ← READY TO PROMOTE")
    print(f"  outreach_queued:          {outreach_queued}  ← already promoted")
    print(f"  verified (no contact):    {verified}  ← need find_contacts.py")
    print(f"  Contacts from playlists:  {curators_from_playlists}  ← in outreach queue")
    print()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Promote playlist contacts → contacts table"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing")
    parser.add_argument("--status", action="store_true",
                        help="Show bridge status and exit")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max contacts to promote this run (0 = all)")
    parser.add_argument("--min-followers", type=int, default=0,
                        help="Minimum playlist followers to include")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    summary = promote_playlist_contacts(
        limit=args.limit,
        dry_run=args.dry_run,
        min_followers=args.min_followers,
    )

    print(f"\n{'='*50}")
    print(f"  Candidates (with email):  {summary['candidates']}")
    print(f"  Promoted to contacts:     {summary['promoted']}")
    print(f"  Skipped (duplicates):     {summary['skipped_dup']}")
    print(f"  Skipped (bad email):      {summary['skipped_bad_email']}")
    print(f"  Skipped (low followers):  {summary['skipped_followers']}")
    if args.dry_run:
        print(f"  [DRY RUN — no writes]")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
