#!/usr/bin/env python3
"""
instagram_poster.py — Post to Instagram via the Graph API (v21.0)

Supports:
  story     image.jpg                 [--dry-run]
  single    image.jpg                 [--caption "text"] [--dry-run]

Required environment variables:
  INSTAGRAM_ACCESS_TOKEN  — long-lived IG user access token
  INSTAGRAM_USER_ID       — (optional) IG-scoped user ID; fetched from /me if absent
"""

import argparse
import os
import subprocess
import sys
import time
from typing import List, Optional

import requests

# ─── Configuration ────────────────────────────────────────────────────────────

BASE_URL = "https://graph.instagram.com/v21.0"
UPLOAD_HOST = "https://0x0.st"


def _access_token() -> str:
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
    if not token:
        sys.exit("ERROR: INSTAGRAM_ACCESS_TOKEN environment variable is not set.")
    return token


def _user_id() -> str:
    uid = os.environ.get("INSTAGRAM_USER_ID", "").strip()
    if uid:
        return uid
    # Auto-fetch from /me
    print("INSTAGRAM_USER_ID not set — fetching from /me …")
    resp = requests.get(
        f"{BASE_URL}/me",
        params={"fields": "id,username", "access_token": _access_token()},
        timeout=30,
    )
    _raise_for_status(resp, "fetch user ID")
    data = resp.json()
    uid = data["id"]
    print(f"  → user ID: {uid}  (username: {data.get('username', '?')})")
    return uid


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _raise_for_status(resp: requests.Response, context: str) -> None:
    """Raise a clean error with the Graph API error message if available."""
    if resp.ok:
        return
    try:
        err = resp.json().get("error", {})
        msg = err.get("message", resp.text)
        code = err.get("code", resp.status_code)
    except Exception:
        msg = resp.text
        code = resp.status_code
    sys.exit(f"ERROR [{context}] HTTP {code}: {msg}")


def _poll_container(container_id: str, timeout: int = 120) -> None:
    """Wait until a media container finishes processing (status = FINISHED)."""
    token = _access_token()
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(
            f"{BASE_URL}/{container_id}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=30,
        )
        _raise_for_status(resp, "poll container status")
        data = resp.json()
        status = data.get("status_code") or data.get("status", "")
        if status == "FINISHED":
            return
        if status == "ERROR":
            sys.exit(f"ERROR: media container {container_id} failed processing.")
        print(f"  … container status: {status} — waiting 5 s")
        time.sleep(5)
    sys.exit(f"ERROR: timed out waiting for container {container_id} to finish.")


# ─── Core functions ───────────────────────────────────────────────────────────

def upload_image_to_host(filepath: str) -> str:
    """Upload a local image to 0x0.st and return its public HTTPS URL."""
    if not os.path.isfile(filepath):
        sys.exit(f"ERROR: file not found: {filepath}")
    print(f"  Uploading {os.path.basename(filepath)} to 0x0.st …")
    result = subprocess.run(
        ["curl", "-sS", "-F", f"file=@{filepath}", UPLOAD_HOST],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        sys.exit(f"ERROR: 0x0.st upload failed:\n{result.stderr}")
    url = result.stdout.strip()
    if not url.startswith("https://"):
        sys.exit(f"ERROR: unexpected response from 0x0.st: {url}")
    print(f"    → {url}")
    return url


def create_media_container(
    image_url: str,
    caption: Optional[str] = None,
    is_carousel_item: bool = False,
    is_story: bool = False,
) -> str:
    """
    Create an IG media container and return its container ID.

    Parameters
    ----------
    image_url        : public HTTPS URL of the image
    caption          : post caption (ignored for carousel items and stories)
    is_carousel_item : True when this container is one slide of a carousel
    is_story         : True when posting to Stories
    """
    token = _access_token()
    uid = _user_id()

    params: dict = {
        "image_url": image_url,
        "access_token": token,
    }

    if is_story:
        params["media_type"] = "STORIES"
    elif is_carousel_item:
        params["is_carousel_item"] = "true"
    else:
        # Single feed post
        if caption:
            params["caption"] = caption

    resp = requests.post(
        f"{BASE_URL}/{uid}/media",
        data=params,
        timeout=30,
    )
    _raise_for_status(resp, "create media container")
    container_id = resp.json()["id"]
    print(f"    → container ID: {container_id}")
    return container_id


def post_story(image_path: str) -> str:
    """Upload image, post as a Story, and return the media ID."""
    print(f"\n[story] Posting story: {os.path.basename(image_path)} …")
    url = upload_image_to_host(image_path)

    print("  Creating story container …")
    container_id = create_media_container(url, is_story=True)
    _poll_container(container_id)

    token = _access_token()
    uid = _user_id()
    print("  Publishing story …")
    resp = requests.post(
        f"{BASE_URL}/{uid}/media_publish",
        data={"creation_id": container_id, "access_token": token},
        timeout=30,
    )
    _raise_for_status(resp, "publish story")
    media_id = resp.json()["id"]
    print(f"[story] Published — media ID: {media_id}")
    return media_id


def post_single(image_path: str, caption: str) -> str:
    """Upload image, post as a single feed post, and return the permalink."""
    print(f"\n[single] Posting: {os.path.basename(image_path)} …")
    url = upload_image_to_host(image_path)

    print("  Creating media container …")
    container_id = create_media_container(url, caption=caption)
    _poll_container(container_id)

    token = _access_token()
    uid = _user_id()
    print("  Publishing …")
    resp = requests.post(
        f"{BASE_URL}/{uid}/media_publish",
        data={"creation_id": container_id, "access_token": token},
        timeout=30,
    )
    _raise_for_status(resp, "publish single post")
    media_id = resp.json()["id"]

    permalink = _fetch_permalink(media_id)
    print(f"[single] Published: {permalink}")
    return permalink


def _fetch_permalink(media_id: str) -> str:
    """Return the permalink for a published media object."""
    resp = requests.get(
        f"{BASE_URL}/{media_id}",
        params={"fields": "permalink", "access_token": _access_token()},
        timeout=30,
    )
    _raise_for_status(resp, "fetch permalink")
    return resp.json().get("permalink", f"media ID {media_id}")


# ─── Dry-run wrappers ─────────────────────────────────────────────────────────

def dry_run_story(image_path: str) -> None:
    print(f"\n[DRY RUN] Would post STORY: {image_path}")


def dry_run_single(image_path: str, caption: str) -> None:
    print(f"\n[DRY RUN] Would post SINGLE: {image_path}")
    print(f"  Caption:\n{caption}\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post to Instagram via the Graph API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 instagram_poster.py story story.jpg
  python3 instagram_poster.py single post.jpg --caption "Hello"
""",
    )
    parser.add_argument(
        "command",
        choices=["story", "single"],
        help="Type of post to create",
    )
    parser.add_argument("images", nargs="+", help="Image file path(s)")
    parser.add_argument("--caption", default="", help="Caption text (not used for stories)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be posted without calling the API",
    )
    args = parser.parse_args()

    if args.command == "story":
        if len(args.images) != 1:
            sys.exit("ERROR: story accepts exactly 1 image.")
        if args.dry_run:
            dry_run_story(args.images[0])
        else:
            media_id = post_story(args.images[0])
            print(f"\nStory media ID: {media_id}")

    elif args.command == "single":
        if len(args.images) != 1:
            sys.exit("ERROR: single accepts exactly 1 image.")
        if args.dry_run:
            dry_run_single(args.images[0], args.caption)
        else:
            permalink = post_single(args.images[0], args.caption)
            print(f"\nPost URL: {permalink}")


if __name__ == "__main__":
    main()
