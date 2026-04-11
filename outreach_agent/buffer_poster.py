#!/usr/bin/env python3.13
"""
buffer_poster.py — Post to Instagram, TikTok, YouTube via Buffer GraphQL API

Usage:
  python3.13 buffer_poster.py instagram story s.jpg
  python3.13 buffer_poster.py instagram post s.jpg --caption "text"
  python3.13 buffer_poster.py tiktok post video.mp4 --caption "text"
  python3.13 buffer_poster.py --list-channels

Credentials:
  Set BUFFER_API_KEY env var, or edit BUFFER_API_KEY below.

Image hosting:
  Images are uploaded to Imgur (anonymous, free) to get public URLs.
  Buffer requires public HTTPS image URLs — local paths won't work.
"""

import argparse
import os
import subprocess
import sys
import json
from pathlib import Path

import requests

# ─── Config ───────────────────────────────────────────────────────────────────

BUFFER_API_KEY = os.environ.get("BUFFER_API_KEY", "131Alg-sjqKP6XuUZj3KOvMvoI8UmWGV8v2th47JQRf")
BUFFER_ENDPOINT = "https://api.buffer.com/graphql"

# Channel IDs (fetched via --list-channels)
CHANNELS = {
    "instagram": "69d6376c031bfa423ce00756",
    "tiktok":    "69d63784031bfa423ce007e4",
    "youtube":   "69d63798031bfa423ce0084e",
}

# Imgur anonymous client ID (public, rate-limited to 1250 uploads/day)
IMGUR_CLIENT_ID = "546c25a59c58ad7"

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _gql(query: str, variables: dict = None) -> dict:
    """Execute a Buffer GraphQL request. Retries once on 429."""
    import time
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BUFFER_API_KEY}",
    }
    for attempt in range(2):
        resp = requests.post(BUFFER_ENDPOINT, json=payload, headers=headers, timeout=30)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            print(f"    [Buffer] rate limited — waiting {retry_after}s…")
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        break
    data = resp.json()
    if "errors" in data:
        sys.exit(f"Buffer API error: {data['errors']}")
    return data["data"]


def upload_image(filepath: str) -> str:
    """Upload a local image to Imgur and return its public HTTPS URL."""
    p = Path(filepath)
    if not p.exists():
        sys.exit(f"ERROR: file not found: {filepath}")

    print(f"  Uploading {p.name} to Imgur…")
    resp = requests.post(
        "https://api.imgur.com/3/upload",
        headers={"Authorization": f"Client-ID {IMGUR_CLIENT_ID}"},
        files={"image": p.open("rb")},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        sys.exit(f"ERROR: Imgur upload failed: {data}")
    url = data["data"]["link"]
    print(f"    → {url}")
    return url


def _create_post(channel: str, post_type: str, image_urls: list[str], caption: str, dry_run: bool) -> str:
    """
    Core post creation. Returns the Buffer post ID or '[dry-run]'.
    post_type: 'post', 'story', 'reel', 'carousel'
    """
    channel_id = CHANNELS.get(channel)
    if not channel_id:
        sys.exit(f"ERROR: unknown channel '{channel}'. Valid: {list(CHANNELS.keys())}")

    if dry_run:
        print(f"\n[DRY RUN] Would post to {channel} ({post_type}):")
        for i, url in enumerate(image_urls, 1):
            print(f"  Image {i}: {url}")
        print(f"  Caption: {caption[:80]}{'…' if len(caption) > 80 else ''}")
        return "[dry-run]"

    # Build metadata per channel
    if channel == "instagram":
        metadata = {
            "instagram": {
                "type": post_type,
                "shouldShareToFeed": True,
            }
        }
    elif channel == "tiktok":
        metadata = {}  # TikTok uses video assets, handled separately
    else:
        metadata = {}

    mutation = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess { post { id status } }
        ... on MutationError { message }
      }
    }
    """

    variables = {
        "input": {
            "channelId": channel_id,
            "text": caption,
            "schedulingType": "automatic",
            "mode": "addToQueue",
            "metadata": metadata,
            "assets": {
                "images": [{"url": u} for u in image_urls]
            },
        }
    }

    result = _gql(mutation, variables)
    payload = result["createPost"]

    if "message" in payload:
        sys.exit(f"ERROR: Buffer rejected post: {payload['message']}")

    post_id = payload["post"]["id"]
    status = payload["post"]["status"]
    print(f"  → Post queued: {post_id} ({status})")
    return post_id


# ─── Video upload ─────────────────────────────────────────────────────────────

def _upload_video_for_buffer(filepath: str) -> str:
    """Upload a local video file to uguu.se and return the public URL.
    uguu.se is free, anonymous, no signup, 48-hour expiry (fine — Buffer fetches immediately).
    Buffer's GraphQL API requires a public URL with proper Content-Length — it has no file upload.
    """
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"Video file not found: {filepath}")

    file_size = p.stat().st_size
    print(f"    Uploading {p.name} ({file_size / 1_000_000:.1f} MB) to uguu.se…")
    with p.open("rb") as fh:
        resp = requests.post(
            "https://uguu.se/upload.php",
            files={"files[]": (p.name, fh, "video/mp4")},
            timeout=300,
        )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"uguu.se upload failed: {data}")
    url = data["files"][0]["url"]
    print(f"    → {url}")
    return url


def _create_video_post(
    channel: str,
    video_url: str,
    caption: str,
    title: str = "",
    description: str = "",
) -> str:
    """Queue a video post to one Buffer channel. Returns the Buffer post ID.
    Buffer's GraphQL API takes a public video URL in assets.videos[].url.
    """
    channel_id = CHANNELS.get(channel)
    if not channel_id:
        raise ValueError(f"Unknown channel '{channel}'. Valid: {list(CHANNELS.keys())}")

    if channel == "instagram":
        metadata = {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    elif channel == "youtube":
        metadata = {
            "youtube": {
                "title": (title or caption)[:100],
                "privacy": "public",
                "categoryId": "10",  # Music
            }
        }
    else:
        metadata = {}

    mutation = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess { post { id status } }
        ... on MutationError { message }
      }
    }
    """
    variables = {
        "input": {
            "channelId": channel_id,
            "text": caption,
            "schedulingType": "automatic",
            "mode": "addToQueue",
            "metadata": metadata,
            "assets": {"videos": [{"url": video_url}]},
        }
    }

    result = _gql(mutation, variables)
    payload = result["createPost"]
    if "message" in payload:
        raise RuntimeError(f"Buffer rejected {channel} post: {payload['message']}")

    post_id = payload["post"]["id"]
    status  = payload["post"]["status"]
    print(f"    → {channel} queued: {post_id} ({status})")
    return post_id


def upload_video_and_queue(
    clip_path: str,
    tiktok_caption: str,
    instagram_caption: str,
    youtube_title: str,
    youtube_desc: str,
) -> None:
    """Upload a video and queue it to TikTok, Instagram Reels, and YouTube Shorts via Buffer."""
    import time
    video_url = _upload_video_for_buffer(clip_path)

    _create_video_post("tiktok",    video_url, tiktok_caption)
    time.sleep(2)
    _create_video_post("instagram", video_url, instagram_caption)
    time.sleep(2)
    _create_video_post("youtube",   video_url, youtube_desc, title=youtube_title, description=youtube_desc)


# ─── Public API ───────────────────────────────────────────────────────────────

def post_instagram_story(image_path: str, dry_run: bool = False) -> str:
    print(f"\n[instagram:story] Uploading {Path(image_path).name}…")
    url = upload_image(image_path) if not dry_run else image_path
    return _create_post("instagram", "story", [url], "", dry_run)


def post_instagram_single(image_path: str, caption: str, dry_run: bool = False) -> str:
    print(f"\n[instagram:post] Uploading {Path(image_path).name}…")
    url = upload_image(image_path) if not dry_run else image_path
    return _create_post("instagram", "post", [url], caption, dry_run)


def list_channels() -> None:
    data = _gql("query { account { organizations { id name } } }")
    org_id = data["account"]["organizations"][0]["id"]
    channels = _gql(
        'query GetChannels($orgId: String!) { channels(input: { organizationId: $orgId }) { id name service } }',
        {"orgId": org_id}
    )["channels"]
    print("\nConnected Buffer channels:")
    for ch in channels:
        print(f"  {ch['service']:12} {ch['name']:30} id={ch['id']}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Post to social media via Buffer API.")
    parser.add_argument("--list-channels", action="store_true", help="List connected channels and exit")
    parser.add_argument("--dry-run", action="store_true", help="Preview without posting")
    parser.add_argument("channel", nargs="?", choices=["instagram", "tiktok", "youtube"], help="Target channel")
    parser.add_argument("post_type", nargs="?", choices=["story", "post", "reel"])
    parser.add_argument("images", nargs="*", help="Image/video file path(s)")
    parser.add_argument("--caption", default="", help="Post caption")
    args = parser.parse_args()

    if args.list_channels:
        list_channels()
        return

    if not args.channel:
        parser.print_help()
        return

    if args.channel == "instagram":
        if args.post_type == "story":
            if len(args.images) != 1:
                sys.exit("ERROR: story requires exactly 1 image.")
            post_instagram_story(args.images[0], args.dry_run)
        elif args.post_type == "post":
            if len(args.images) != 1:
                sys.exit("ERROR: post requires exactly 1 image.")
            post_instagram_single(args.images[0], args.caption, args.dry_run)
        else:
            sys.exit(f"ERROR: unsupported post_type '{args.post_type}' for instagram. Use: story, post, reel")
    else:
        sys.exit(f"ERROR: channel '{args.channel}' posting not yet implemented. Instagram is working.")


if __name__ == "__main__":
    main()
