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
    "Nomadic Electronic — organic powerful house through tribal psytrance. "
    "Oud, handpan, tribal drums, Middle Eastern modes. 130–145 BPM rooted in scripture.\n\n"
    "Ancient Truth. Future Sound.\n\n"
    "— Robert-Jan Mastenbroek\n"
    f"Web: {cfg.ARTIST_WEBSITE}\n"
    f"Spotify: {cfg.SPOTIFY_ARTIST_URL}\n"
    f"Apple Music: {cfg.APPLE_MUSIC_URL}\n"
    f"Instagram: {cfg.ARTIST_INSTAGRAM}\n"
    f"TikTok: {cfg.ARTIST_TIKTOK}"
)

CHANNEL_KEYWORDS = (
    '"nomadic electronic" "organic house" "tribal psytrance" '
    '"ethnic electronic" "Middle Eastern electronic" "Cafe de Anatolia" '
    '"Sol Selectas" "Holy Rave" handpan oud "tribal drums" '
    '"sacred geometry" "desert rave" "Robert-Jan Mastenbroek" RJM'
)

PLAYLISTS_TO_CREATE = [
    {
        "key":   "YOUTUBE_PLAYLIST_TRIBAL_PSY",
        "title": "Holy Rave — Tribal Psytrance",
        "description": (
            "140–145 BPM tribal psytrance from Robert-Jan Mastenbroek. "
            "Ancient rhythm, Hebrew lyrics, sacred geometry in motion."
        ),
        "privacy": "public",
    },
    {
        "key":   "YOUTUBE_PLAYLIST_ORGANIC_HOUSE",
        "title": "Holy Rave — Organic House",
        "description": (
            "128–132 BPM organic-tribal house. Oud, handpan, tribal drums, "
            "Middle Eastern modes woven through modern electronic production."
        ),
        "privacy": "public",
    },
    {
        "key":   "YOUTUBE_PLAYLIST_MIDDLE_EASTERN",
        "title": "Holy Rave — Middle Eastern",
        "description": (
            "Middle Eastern instrumentation at the heart of every track. "
            "Handpan, oud, Nabataean modes, Bedouin rhythms — the sound of Holy Rave's deepest roots."
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
