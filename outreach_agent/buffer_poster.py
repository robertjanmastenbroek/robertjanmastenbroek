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
import time
from pathlib import Path
from typing import Optional

import requests
from video_host import upload_video
import db
from config import MAX_CONTENT_POSTS_PER_DAY

# ─── Hive connectivity (defensive import) ────────────────────────────────────
try:
    import events as _events
    import fleet_state as _fleet_state
    _HIVE_AVAILABLE = True
except ImportError:
    _HIVE_AVAILABLE = False

# ─── Config ───────────────────────────────────────────────────────────────────

BUFFER_ENDPOINT = "https://api.buffer.com/graphql"

# Fallback Buffer API key used only when BUFFER_API_KEY env var is unset.
# The project's real key lives in .env — do NOT change this constant.
_BUFFER_API_KEY_FALLBACK = "131Alg-sjqKP6XuUZj3KOvMvoI8UmWGV8v2th47JQRf"


def _get_buffer_api_key() -> str:
    """Read BUFFER_API_KEY lazily on every request.

    The pipeline loads .env AFTER this module is imported (the pipeline is
    the import entry point). Reading via a function call means each request
    gets the latest env value, not the one present at import time.
    """
    return os.environ.get("BUFFER_API_KEY") or _BUFFER_API_KEY_FALLBACK


# Back-compat shim: modules that imported BUFFER_API_KEY as a module-level
# constant still get a value, but it may be the pre-.env-load value. New
# code should call _get_buffer_api_key() instead.
BUFFER_API_KEY = _get_buffer_api_key()


def _channel_id(platform: str, default: str) -> str:
    """Read BUFFER_CHANNEL_<PLATFORM> lazily — same rationale as the API key."""
    return os.environ.get(f"BUFFER_CHANNEL_{platform.upper()}", default)


# Channel IDs (fetched via --list-channels). Override via BUFFER_CHANNEL_<PLATFORM>
# environment variables — useful if a channel is rotated without redeploying.
# These are the import-time defaults; _resolve_channel() picks up env overrides
# at request time.
_CHANNEL_DEFAULTS = {
    "instagram": "69d6376c031bfa423ce00756",
    "tiktok":    "69d63784031bfa423ce007e4",
    "youtube":   "69d63798031bfa423ce0084e",
    "facebook":  "69deaee9031bfa423c0403f9",
}
CHANNELS = {p: _channel_id(p, d) for p, d in _CHANNEL_DEFAULTS.items()}


def _resolve_channel(platform: str) -> Optional[str]:  # noqa: F821 - defined below if needed
    """Return the current channel id for a platform, with env override."""
    default = _CHANNEL_DEFAULTS.get(platform, "")
    if not default:
        return None
    return _channel_id(platform, default)


# Cache of locked channel services (Buffer's free plan locks channels after
# trial; `createPost` rejects locked channels with a generic "Channel not
# found" message which would otherwise burn three retry cycles + 65s of
# backoff per platform). Populated lazily on first access.
_LOCKED_CHANNELS: set[str] = set()
_LOCK_PROBE_DONE: bool = False


def _probe_locked_channels() -> set[str]:
    """Query Buffer once per run to learn which channels are locked.

    Buffer's `isLocked: true` flag indicates the channel exists on the
    account but can't be posted to (plan tier / trial expiration). Returns
    a set of service strings (e.g. {"facebook"}).
    """
    global _LOCK_PROBE_DONE
    if _LOCK_PROBE_DONE:
        return _LOCKED_CHANNELS
    try:
        data = _gql("query { account { organizations { id } } }")
        org_id = data["account"]["organizations"][0]["id"]
        channels = _gql(
            "query($o: OrganizationId!) { channels(input:{organizationId:$o}) "
            "{ id service isLocked isDisconnected } }",
            {"o": org_id},
        )["channels"]
        for ch in channels:
            if ch.get("isLocked") or ch.get("isDisconnected"):
                _LOCKED_CHANNELS.add(ch["service"])
    except Exception as exc:
        # If the probe fails we don't mark anything locked — better to try
        # posting and surface the real error than to silently skip.
        print(f"    [Buffer] channel-lock probe failed (non-fatal): {exc}")
    finally:
        _LOCK_PROBE_DONE = True
    return _LOCKED_CHANNELS


def _channel_service(channel: str) -> str:
    """Map our internal channel name to Buffer's `service` string.

    Stories share the parent service (facebook_story → facebook, etc.).
    """
    return channel.split("_")[0] if "_" in channel else channel


class BufferChannelLocked(RuntimeError):
    """Raised when a target Buffer channel is locked (plan-tier restriction)."""


# Imgur anonymous client ID (public, rate-limited to 1250 uploads/day)
IMGUR_CLIENT_ID = "546c25a59c58ad7"

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _gql(query: str, variables: dict = None) -> dict:
    """Execute a Buffer GraphQL request.

    Retries up to 3 times on network errors, 5xx, and 429.
    Uses exponential backoff: 5s, 15s, 45s.
    Raises RuntimeError on GraphQL-level errors (never calls sys.exit).
    """
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_get_buffer_api_key()}",
    }

    max_attempts = 3
    backoff = [5, 15, 45]

    for attempt in range(max_attempts):
        try:
            resp = requests.post(BUFFER_ENDPOINT, json=payload, headers=headers, timeout=30)
        except requests.exceptions.RequestException as exc:
            if attempt < max_attempts - 1:
                wait = backoff[attempt]
                print(f"    [Buffer] network error (attempt {attempt + 1}/{max_attempts}): {exc} — retrying in {wait}s…")
                time.sleep(wait)
                continue
            raise

        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", backoff[attempt]))
            print(f"    [Buffer] rate limited — waiting {wait}s…")
            time.sleep(wait)
            continue

        if resp.status_code == 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:300]
            raise RuntimeError(f"Buffer 400 Bad Request: {body}")

        if resp.status_code >= 500:
            wait = backoff[attempt]
            if attempt < max_attempts - 1:
                print(f"    [Buffer] server error {resp.status_code} (attempt {attempt + 1}/{max_attempts}) — retrying in {wait}s…")
                time.sleep(wait)
                try:
                    resp.raise_for_status()
                except Exception:
                    pass
                continue

        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Buffer API error: {data['errors']}")
        return data["data"]

    raise RuntimeError("Buffer API: max retries exceeded")


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

    try:
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
    except BufferChannelLocked as e:
        # Known-state: account plan tier doesn't allow this channel (e.g. free
        # tier blocks Facebook). Not a poster failure — heartbeat "ok" with a
        # descriptive skip result so the fleet error count stays clean.
        if _HIVE_AVAILABLE:
            _fleet_state.heartbeat("buffer_poster", status="ok", result=f"skipped:locked:{channel}")
        raise
    except Exception as e:
        if _HIVE_AVAILABLE:
            _fleet_state.heartbeat("buffer_poster", status="error", result=str(e))
        raise

    if _HIVE_AVAILABLE:
        platform = channel
        _events.publish(
            "content.scheduled",
            "buffer_poster",
            {
                "platform": platform,
                "post_type": post_type,
            },
        )
        _fleet_state.heartbeat("buffer_poster", status="ok", result=f"scheduled:{platform}")
    return post_id


# ─── Video upload ─────────────────────────────────────────────────────────────

# Video upload is handled by video_host.upload_video (imported at top of file)

_VIDEO_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id status } }
    ... on MutationError { message }
  }
}
"""


def _create_video_post(
    channel: str,
    video_url: str,
    caption: str,
    title: str = "",
    description: str = "",
    scheduled_at: str = None,
) -> str:
    """Queue a video post to one Buffer channel. Returns the Buffer post ID.
    Buffer's GraphQL API takes a public video URL in assets.videos[].url.
    scheduled_at is accepted for API compatibility but ignored — Buffer's queue
    handles timing automatically ("automatic" mode).
    """
    channel_id = CHANNELS.get(channel)
    if not channel_id:
        raise ValueError(f"Unknown channel '{channel}'. Valid: {list(CHANNELS.keys())}")

    service = _channel_service(channel)
    if service in _probe_locked_channels():
        raise BufferChannelLocked(
            f"Buffer channel '{channel}' is locked (plan tier / disconnected) — "
            f"upgrade plan or reconnect at buffer.com to enable posting"
        )

    try:
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
        elif channel == "facebook":
            # Facebook Reels via Buffer — "reel" type publishes to the Page Reel tray,
            # "post" falls back to a regular video post. We use "post" for maximum
            # compatibility since Buffer's Facebook Reel surface is newer.
            metadata = {"facebook": {"type": "post"}}
        else:
            metadata = {}

        inp = {
            "channelId": channel_id,
            "text": caption,
            "schedulingType": "automatic",
            "mode": "customScheduled" if scheduled_at else "addToQueue",
            "metadata": metadata,
            "assets": {"videos": [{"url": video_url}]},
        }
        if scheduled_at:
            inp["dueAt"] = scheduled_at

        result = _gql(_VIDEO_POST_MUTATION, {"input": inp})
        payload = result["createPost"]
        if "message" in payload:
            raise RuntimeError(f"Buffer rejected {channel} post: {payload['message']}")

        post_id = payload["post"]["id"]
        status  = payload["post"]["status"]
        print(f"    → {channel} queued: {post_id} ({status})")
    except BufferChannelLocked as e:
        # Known-state: account plan tier doesn't allow this channel (e.g. free
        # tier blocks Facebook). Not a poster failure — heartbeat "ok" with a
        # descriptive skip result so the fleet error count stays clean.
        if _HIVE_AVAILABLE:
            _fleet_state.heartbeat("buffer_poster", status="ok", result=f"skipped:locked:{channel}")
        raise
    except Exception as e:
        if _HIVE_AVAILABLE:
            _fleet_state.heartbeat("buffer_poster", status="error", result=str(e))
        raise

    if _HIVE_AVAILABLE:
        platform = channel
        _events.publish(
            "content.scheduled",
            "buffer_poster",
            {
                "platform": platform,
                "media_kind": "video",
            },
        )
        _fleet_state.heartbeat("buffer_poster", status="ok", result=f"scheduled:{platform}")
    return post_id


def _create_video_story_post(channel: str, video_url: str, scheduled_at: str = None) -> str:
    """Queue a video as a Story via Buffer (Instagram or Facebook)."""
    channel_id = CHANNELS.get(channel)
    if not channel_id:
        raise ValueError(f"Unknown channel '{channel}'. Valid: {list(CHANNELS.keys())}")

    service = _channel_service(channel)
    if service in _probe_locked_channels():
        raise BufferChannelLocked(
            f"Buffer channel '{channel}' is locked (plan tier / disconnected) — "
            f"upgrade plan or reconnect at buffer.com to enable posting"
        )

    if channel == "instagram":
        story_meta = {"instagram": {"type": "story", "shouldShareToFeed": False}}
    elif channel == "facebook":
        story_meta = {"facebook": {"type": "story"}}
    else:
        raise ValueError(f"Stories not supported for channel '{channel}'")

    try:
        inp = {
            "channelId": channel_id,
            "text": "",
            "schedulingType": "automatic",
            "mode": "customScheduled" if scheduled_at else "addToQueue",
            "metadata": story_meta,
            "assets": {"videos": [{"url": video_url}]},
        }
        if scheduled_at:
            inp["dueAt"] = scheduled_at

        result = _gql(_VIDEO_POST_MUTATION, {"input": inp})
        payload = result["createPost"]
        if "message" in payload:
            raise RuntimeError(f"Buffer rejected {channel} story: {payload['message']}")

        post_id = payload["post"]["id"]
        status  = payload["post"]["status"]
        print(f"    → {channel} story queued: {post_id} ({status})")
    except BufferChannelLocked as e:
        # Known-state: account plan tier doesn't allow this channel (e.g. free
        # tier blocks Facebook). Not a poster failure — heartbeat "ok" with a
        # descriptive skip result so the fleet error count stays clean.
        if _HIVE_AVAILABLE:
            _fleet_state.heartbeat("buffer_poster", status="ok", result=f"skipped:locked:{channel}")
        raise
    except Exception as e:
        if _HIVE_AVAILABLE:
            _fleet_state.heartbeat("buffer_poster", status="error", result=str(e))
        raise

    if _HIVE_AVAILABLE:
        platform = channel
        _events.publish(
            "content.scheduled",
            "buffer_poster",
            {
                "platform": platform,
                "media_kind": "video_story",
            },
        )
        _fleet_state.heartbeat("buffer_poster", status="ok", result=f"scheduled:{platform}_story")
    return post_id


def upload_video_and_queue(
    clip_path: str,
    tiktok_caption: str,
    instagram_caption: str,
    youtube_title: str,
    youtube_desc: str,
    scheduled_at: str = None,
) -> dict:
    """Upload a video and queue it to TikTok, Instagram Reels, Instagram Story,
    and YouTube Shorts via Buffer.

    Each platform is attempted independently — one failure does not cancel others.

    Returns a dict of {platform: {"success": bool, "id": str|None, "error": str|None}}.
    """
    db.init_db()
    posts_today = db.today_content_count()
    if posts_today >= MAX_CONTENT_POSTS_PER_DAY:
        print(f"  [Buffer] Daily content cap reached ({posts_today}/{MAX_CONTENT_POSTS_PER_DAY}) — skipping upload")
        return {p: {"success": False, "id": None, "error": "daily_cap_reached"}
                for p in ("tiktok", "instagram_reel", "instagram_story", "youtube")}

    video_url = upload_video(clip_path)  # raises on total failure

    platforms = {
        "tiktok":           lambda: _create_video_post("tiktok",    video_url, tiktok_caption,    scheduled_at=scheduled_at),
        "instagram_reel":   lambda: _create_video_post("instagram", video_url, instagram_caption, scheduled_at=scheduled_at),
        "instagram_story":  lambda: _create_video_story_post("instagram", video_url, scheduled_at=scheduled_at),
        "youtube":          lambda: _create_video_post("youtube",   video_url, youtube_desc, title=youtube_title, description=youtube_desc, scheduled_at=scheduled_at),
    }

    results = {}
    for platform, post_fn in platforms.items():
        try:
            post_id = post_fn()
            results[platform] = {"success": True, "id": post_id, "error": None}
        except Exception as exc:
            print(f"    ✗ {platform} failed: {exc}")
            results[platform] = {"success": False, "id": None, "error": str(exc)}
        time.sleep(2)

    if any(r["success"] for r in results.values()):
        db.increment_content_count()

    return results


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
    # Buffer's GraphQL schema types $orgId as OrganizationId!, not String!
    channels = _gql(
        'query GetChannels($orgId: OrganizationId!) { channels(input: { organizationId: $orgId }) { id name service } }',
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
