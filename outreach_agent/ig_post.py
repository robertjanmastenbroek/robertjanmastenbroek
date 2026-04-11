#!/usr/bin/env python3.13
"""
ig_post.py — Post to Instagram via instagrapi (no Meta Developer App needed)

Usage:
  python3 ig_post.py story image.jpg
  python3 ig_post.py single image.jpg --caption "text"

Credentials read from environment or from config.py:
  IG_USERNAME  — Instagram username (without @)
  IG_PASSWORD  — Instagram password

Session is cached to ig_session.json to avoid re-login on every run.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from instagrapi import Client
from instagrapi.exceptions import LoginRequired, TwoFactorRequired

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
SESSION_FILE = BASE_DIR / "ig_session.json"
IMAGES_DIR = Path(__file__).parent.parent / "images" / "ig_posts"

# ─── Auth ─────────────────────────────────────────────────────────────────────

def _get_credentials():
    username = os.environ.get("IG_USERNAME", "").strip()
    password = os.environ.get("IG_PASSWORD", "").strip()

    if not username or not password:
        # Try loading from config.py
        try:
            sys.path.insert(0, str(BASE_DIR))
            import config
            username = getattr(config, "IG_USERNAME", "")
            password = getattr(config, "IG_PASSWORD", "")
        except Exception:
            pass

    if not username:
        sys.exit("ERROR: IG_USERNAME not set. Export it or add to config.py")
    if not password:
        sys.exit("ERROR: IG_PASSWORD not set. Export it or add to config.py")

    return username, password


def get_client() -> Client:
    """Return an authenticated instagrapi Client, reusing cached session."""
    username, password = _get_credentials()
    cl = Client()
    cl.delay_range = [2, 5]  # human-like delay between requests

    if SESSION_FILE.exists():
        try:
            cl.load_settings(SESSION_FILE)
            cl.login(username, password)
            cl.get_timeline_feed()  # verify session is still valid
            print(f"  Session restored for @{username}")
            return cl
        except (LoginRequired, Exception) as e:
            print(f"  Cached session expired ({e}), re-logging in…")
            SESSION_FILE.unlink(missing_ok=True)

    print(f"  Logging in as @{username}…")
    try:
        cl.login(username, password)
    except TwoFactorRequired:
        code = input("  2FA code: ").strip()
        cl.login(username, password, verification_code=code)

    cl.dump_settings(SESSION_FILE)
    print("  Session saved.")
    return cl


# ─── Post functions ───────────────────────────────────────────────────────────

def post_story(image_path: str) -> str:
    """Post a single image story. Returns the media ID."""
    p = Path(image_path)
    if not p.exists():
        sys.exit(f"ERROR: file not found: {p}")

    print(f"\n[story] Posting story: {p.name}…")
    cl = get_client()
    media = cl.photo_upload_to_story(p)
    print(f"[story] Published — media ID: {media.id}")
    return str(media.id)


def post_single(image_path: str, caption: str) -> str:
    """Post a single image feed post. Returns the media URL."""
    p = Path(image_path)
    if not p.exists():
        sys.exit(f"ERROR: file not found: {p}")

    print(f"\n[single] Posting: {p.name}…")
    cl = get_client()
    media = cl.photo_upload(p, caption=caption)
    url = f"https://www.instagram.com/p/{media.code}/"
    print(f"[single] Published: {url}")
    return url


# ─── Dry-run ──────────────────────────────────────────────────────────────────

def dry_run_story(path):
    print(f"\n[DRY RUN] Would post STORY: {path}")


def dry_run_single(path, caption):
    print(f"\n[DRY RUN] Would post SINGLE: {path}")
    print(f"  Caption:\n{caption}\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Post to Instagram via instagrapi.")
    parser.add_argument("command", choices=["story", "single"])
    parser.add_argument("images", nargs="+", help="Image file path(s)")
    parser.add_argument("--caption", default="", help="Caption text")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.command == "story":
        if len(args.images) != 1:
            sys.exit("ERROR: story takes exactly 1 image.")
        if args.dry_run:
            dry_run_story(args.images[0])
        else:
            post_story(args.images[0])

    elif args.command == "single":
        if len(args.images) != 1:
            sys.exit("ERROR: single takes exactly 1 image.")
        if args.dry_run:
            dry_run_single(args.images[0], args.caption)
        else:
            post_single(args.images[0], args.caption)


if __name__ == "__main__":
    main()
