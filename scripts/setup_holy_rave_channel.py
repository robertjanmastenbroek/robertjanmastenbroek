#!/usr/bin/env python3
"""
setup_holy_rave_channel.py — Automate YouTube channel settings for the Holy Rave rebrand.

Uses the existing RJM OAuth credentials (YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN)
to apply the full channel-settings checklist in one pass:

  • Update channel description, keywords, and default language
  • Create three empty playlists:
        Holy Rave — Tribal Psytrance
        Holy Rave — Organic House
        Holy Rave — Middle Eastern
  • Print the playlist IDs for pasting into .env

This is NOT destructive. It:
  - Does not change the display name (that's a manual Studio step + handle lookup)
  - Does not upload profile picture / banner (use generate_brand_assets.py)
  - Does not delete existing content (that's a manual confirmation step)

Prerequisites:
    YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN in .env
    The refresh token must authorize the Holy Rave channel. If you've only
    ever authorized your main channel, run scripts/setup_youtube_oauth.py
    again while logged into the Holy Rave channel.

Usage:
    python3 scripts/setup_holy_rave_channel.py             # Apply all settings
    python3 scripts/setup_holy_rave_channel.py --dry-run   # Preview without calling API
    python3 scripts/setup_holy_rave_channel.py --playlists-only
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from content_engine.youtube_longform import config as cfg


logger = logging.getLogger("setup_hr_channel")

CHANNEL_DESCRIPTION = (
    "Nomadic electronic — organic house through tribal psytrance. "
    "128–145 BPM of oud, handpan, darbuka, tribal drums, and Middle Eastern "
    "modes. Ancient melodies given modern electronic production.\n\n"

    "If you love Café de Anatolia, Sol Selectas, Sabo, Bedouin, Acid Arab, "
    "Keinemusik, All Day I Dream, Anjunadeep, Monolink, Be Svendsen, "
    "Innellea — or Astrix, Ace Ventura, Vini Vici, Symphonix, Vertex, "
    "Ranji, Tristan, Infected Mushroom, Iboga Records — this is the same "
    "sonic family, rooted in Abrahamic-nomadic imagery and Hebrew "
    "scripture.\n\n"

    "140 BPM where the walls come down. 130 BPM where the dust settles.\n\n"

    "Ancient truth. Future sound.\n\n"

    "— Robert-Jan Mastenbroek\n"
    f"Website: {cfg.ARTIST_WEBSITE}\n"
    f"Spotify: {cfg.SPOTIFY_ARTIST_URL}\n"
    f"Apple Music: {cfg.APPLE_MUSIC_URL}\n"
    f"Instagram: {cfg.ARTIST_INSTAGRAM}\n"
    f"TikTok: {cfg.ARTIST_TIKTOK}"
)

CHANNEL_KEYWORDS = (
    '"nomadic electronic" "organic house" "tribal psytrance" '
    '"ethnic electronic" "Middle Eastern electronic" "Cafe de Anatolia" '
    '"Sol Selectas" "Sabo" "Bedouin" "Acid Arab" "Keinemusik" '
    '"All Day I Dream" "Anjunadeep" "Monolink" "Astrix" "Ace Ventura" '
    '"Vini Vici" "Symphonix" "Vertex" "Ranji" "Infected Mushroom" '
    '"Iboga Records" "Holy Rave" handpan oud "tribal drums" darbuka '
    '"sacred geometry" "desert rave" "Abrahamic nomadic" '
    '"Robert-Jan Mastenbroek" RJM'
)

# Consolidated 2-playlist structure (2026-04-21).
# Rich descriptions with reference-artist names for SEO — YouTube's search
# algorithm weighs playlist descriptions for recommendation clustering.
PLAYLISTS_TO_CREATE = [
    {
        "key":   "YOUTUBE_PLAYLIST_ETHNIC_TRIBAL",
        "title": "Holy Rave — Ethnic / Tribal Organic House",
        "description": (
            "128–136 BPM ethnic-tribal organic house by Robert-Jan Mastenbroek. "
            "Oud, handpan, darbuka, tribal drums, Middle Eastern modes — ancient "
            "melodies given modern electronic production.\n\n"
            "If you love Café de Anatolia, Sol Selectas, Sabo, Bedouin, Acid "
            "Arab, Keinemusik, All Day I Dream, Anjunadeep, Monolink, Be "
            "Svendsen, Innellea — you'll recognize the sonic family here.\n\n"
            "Rooted in Hebrew scripture, Aramaic prayer, and Abrahamic-nomadic "
            "imagery. Ancient truth. Future sound."
        ),
        "privacy": "public",
    },
    {
        "key":   "YOUTUBE_PLAYLIST_TRIBAL_PSY",
        "title": "Holy Rave — Tribal Psytrance",
        "description": (
            "140–145 BPM tribal psytrance by Robert-Jan Mastenbroek. "
            "Hebrew vocals, shofar rhythms, temple-courtyard geometry, "
            "desert-ritual intensity.\n\n"
            "If you love Astrix, Ace Ventura, Vini Vici, Ranji, Symphonix, "
            "Vertex, Atmos, Blisargon Demogorgon, Tristan, Infected Mushroom, "
            "Berg, Avalon, Iboga Records — this is the same frequency with "
            "an Abrahamic spine.\n\n"
            "140 BPM where the walls come down. Ancient truth. Future sound."
        ),
        "privacy": "public",
    },
]


# ─── API helpers ─────────────────────────────────────────────────────────────

def _refresh_access_token() -> str:
    if not cfg.YT_REFRESH_TOKEN:
        raise RuntimeError("YOUTUBE_REFRESH_TOKEN not set")
    if not (cfg.YT_CLIENT_ID and cfg.YT_CLIENT_SECRET):
        raise RuntimeError("YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET missing")
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id":     cfg.YT_CLIENT_ID,
            "client_secret": cfg.YT_CLIENT_SECRET,
            "refresh_token": cfg.YT_REFRESH_TOKEN,
            "grant_type":    "refresh_token",
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Token refresh failed: {r.status_code} {r.text[:400]}")
    return r.json()["access_token"]


def _fetch_current_channel(access_token: str) -> dict:
    """Fetch the authorized channel's current branding + settings."""
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet,brandingSettings,contentDetails", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"channels.list mine failed: {r.status_code} {r.text[:400]}")
    items = r.json().get("items", [])
    if not items:
        raise RuntimeError("No channel returned for authorized user")
    return items[0]


def _update_channel_branding(access_token: str, channel_id: str, dry_run: bool) -> None:
    body = {
        "id": channel_id,
        "brandingSettings": {
            "channel": {
                "description":      CHANNEL_DESCRIPTION,
                "keywords":         CHANNEL_KEYWORDS,
                "defaultLanguage":  "en",
                "country":          "ES",
            }
        },
    }
    logger.info("Prepared brandingSettings patch:\n%s", json.dumps(body, indent=2))
    if dry_run:
        print("  (dry-run — skipping API call)")
        return
    r = requests.put(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "brandingSettings"},
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"channels.update failed: {r.status_code} {r.text[:500]}")
    logger.info("✓ brandingSettings updated")


def _create_playlist(
    access_token: str,
    title: str,
    description: str,
    privacy: str,
    dry_run: bool,
) -> str:
    body = {
        "snippet": {
            "title":           title,
            "description":     description,
            "defaultLanguage": "en",
        },
        "status": {"privacyStatus": privacy},
    }
    if dry_run:
        print(f"  (dry-run — would create playlist '{title}')")
        return "DRYRUN_ID"
    r = requests.post(
        "https://www.googleapis.com/youtube/v3/playlists",
        params={"part": "snippet,status"},
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"playlists.insert failed: {r.status_code} {r.text[:400]}")
    return r.json()["id"]


def _list_existing_playlists(access_token: str) -> list[dict]:
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/playlists",
        params={"part": "snippet", "mine": "true", "maxResults": 50},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if r.status_code != 200:
        return []
    return r.json().get("items", [])


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without calling the API")
    parser.add_argument("--playlists-only", action="store_true", help="Skip brandingSettings patch; only create playlists")
    args = parser.parse_args()

    try:
        access_token = _refresh_access_token()
    except Exception as e:
        print(f"✗ OAuth failed: {e}", file=sys.stderr)
        print("  Ensure YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN are set", file=sys.stderr)
        print("  If the refresh token authorizes the wrong channel, rerun scripts/setup_youtube_oauth.py", file=sys.stderr)
        return 1

    # Confirm which channel we're authenticated against
    channel = _fetch_current_channel(access_token)
    channel_id = channel["id"]
    channel_title = channel.get("snippet", {}).get("title", "?")
    print(f"\nAuthenticated against channel:\n  {channel_title!r}  (id: {channel_id})\n")

    if not args.playlists_only:
        reply = "y" if args.dry_run else input(
            f"Proceed with brandingSettings update for '{channel_title}'? [y/N]: "
        ).strip().lower()
        if reply != "y":
            print("Aborted.")
            return 0
        print("\n— Updating channel description, keywords, language, country…")
        _update_channel_branding(access_token, channel_id, dry_run=args.dry_run)

    print("\n— Checking existing playlists…")
    existing = _list_existing_playlists(access_token)
    existing_titles = {p["snippet"]["title"] for p in existing}
    for p in PLAYLISTS_TO_CREATE:
        if p["title"] in existing_titles:
            print(f"  ✓ '{p['title']}' already exists — skipping")

    print("\n— Creating missing playlists…")
    created: dict[str, str] = {}
    for p in PLAYLISTS_TO_CREATE:
        if p["title"] in existing_titles:
            continue
        print(f"  Creating: {p['title']}")
        playlist_id = _create_playlist(
            access_token=access_token,
            title=p["title"],
            description=p["description"],
            privacy=p["privacy"],
            dry_run=args.dry_run,
        )
        created[p["key"]] = playlist_id

    if created and not args.dry_run:
        print("\n" + "=" * 70)
        print("✓ Playlists created. Paste into your .env:\n")
        for key, pid in created.items():
            print(f"  {key}={pid}")

    print("\n✓ Setup complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
