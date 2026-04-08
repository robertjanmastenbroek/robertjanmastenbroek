"""
Buffer publisher — schedules posts via Buffer GraphQL API.

Docs: https://developers.buffer.com/guides/getting-started.html

Environment:
  BUFFER_API_KEY  — API key from https://publish.buffer.com/settings/api

Flow:
  1. validate_token()   → verify key works
  2. get_profiles()     → list channels (TikTok, Instagram, YouTube)
  3. upload_media()     → upload local file to transfer.sh → get public URL
  4. schedule_video()   → createPost mutation with video URL + scheduled time
  5. schedule_carousel()→ createPost mutation with image URLs
"""

import os
import time
import logging
import subprocess
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BUFFER_ENDPOINT = "https://api.buffer.com"
MAX_RETRIES = 3

_token_validated: bool = False
_channels_cache: Optional[list] = None


# ── Auth ──────────────────────────────────────────────────────────────────────

def _api_key() -> str:
    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if not key:
        raise EnvironmentError(
            "BUFFER_API_KEY is not set.\n"
            "Get your API key from: https://publish.buffer.com/settings/api\n"
            "Then add to your .env:  BUFFER_API_KEY=your_key_here"
        )
    return key


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


def _gql(query: str, variables: dict = None) -> dict:
    """Execute a GraphQL query/mutation against the Buffer API."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                BUFFER_ENDPOINT,
                json=payload,
                headers=_headers(),
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                if "errors" in data:
                    logger.error(f"GraphQL errors: {data['errors']}")
                    return {}
                return data.get("data", {})
            elif resp.status_code == 401:
                logger.error(
                    "Buffer API key rejected (401).\n"
                    "Get your key from: https://publish.buffer.com/settings/api\n"
                    "Set in .env: BUFFER_API_KEY=your_key_here"
                )
                return {}
            else:
                logger.warning(f"Buffer API {resp.status_code}: {resp.text[:200]}")
        except requests.RequestException as e:
            logger.warning(f"Buffer request error (attempt {attempt}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    return {}


# ── Token validation ──────────────────────────────────────────────────────────

def validate_token() -> bool:
    """Verify API key with a lightweight account query. Caches result."""
    global _token_validated
    if _token_validated:
        return True

    try:
        _api_key()  # raises if not set
    except EnvironmentError as e:
        logger.error(str(e))
        return False

    data = _gql("query { account { id email } }")
    if data.get("account", {}).get("id"):
        _token_validated = True
        logger.info(f"Buffer auth OK — {data['account'].get('email', '')}")
        return True

    logger.error(
        "Buffer API key invalid.\n"
        "Get your key from: https://publish.buffer.com/settings/api\n"
        "Set in .env: BUFFER_API_KEY=your_key_here"
    )
    return False


# ── Channels ──────────────────────────────────────────────────────────────────

def get_profiles() -> list[dict]:
    """
    Return all connected Buffer channels.
    Each dict has: id, service, name, organizationId
    """
    global _channels_cache
    if _channels_cache is not None:
        return _channels_cache

    # First get org ID
    org_data = _gql("query { account { organizations { id name } } }")
    orgs = org_data.get("account", {}).get("organizations", [])
    if not orgs:
        logger.error("No Buffer organizations found.")
        return []

    org_id = orgs[0]["id"]

    # Inline org_id to avoid scalar type mismatch (OrganizationId! vs String!)
    data = _gql(
        f"""
        query GetChannels {{
          channels(input: {{ organizationId: "{org_id}" }}) {{
            id
            name
            displayName
            service
            avatar
          }}
        }}
        """
    )

    channels = data.get("channels", [])
    result = []
    for ch in channels:
        result.append({
            "id": ch["id"],
            "service": ch.get("service", "").lower(),
            "name": ch.get("displayName") or ch.get("name", ""),
            "formatted_username": ch.get("displayName") or ch.get("name", ""),
            "organizationId": org_id,
        })

    logger.info(f"Found {len(result)} Buffer channels: {[c['service'] for c in result]}")
    _channels_cache = result
    return result




# ── Media upload ──────────────────────────────────────────────────────────────

def upload_media(local_path: str) -> Optional[str]:
    """
    Upload a local video/image to litterbox.catbox.moe and return the public URL.
    Buffer requires a publicly accessible URL for media assets.
    URL is valid for 72 hours — Buffer fetches it well within that window.
    """
    path = Path(local_path)
    if not path.exists():
        logger.error(f"Media file not found: {local_path}")
        return None

    logger.info(f"Uploading {path.name} ({path.stat().st_size // 1024 // 1024}MB)...")
    try:
        result = subprocess.run(
            [
                "curl", "-s", "--max-time", "600",
                "-F", "reqtype=fileupload",
                "-F", "time=72h",
                "-F", f"fileToUpload=@{path}",
                "https://litterbox.catbox.moe/resources/internals/api.php",
            ],
            capture_output=True, text=True, timeout=620
        )
        url = result.stdout.strip()
        if result.returncode == 0 and url.startswith("https://"):
            logger.info(f"Uploaded → {url}")
            return url
        else:
            logger.error(f"Upload failed (rc={result.returncode}): {url[:200]}")
            return None
    except subprocess.TimeoutExpired:
        logger.error("Media upload timed out")
        return None
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return None


# ── Post scheduling ───────────────────────────────────────────────────────────

_CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess {
      post {
        id
        status
        dueAt
      }
    }
  }
}
"""


def schedule_video(
    channel_id: str,
    video_path: str,
    caption: str,
    scheduled_time: str,
    title: str = None,
) -> Optional[str]:
    """
    Upload a video and schedule it to a Buffer channel.

    Args:
        channel_id:     Buffer channel ID
        video_path:     Local path to the video file
        caption:        Post caption/description
        scheduled_time: ISO 8601 string, e.g. "2026-04-08T18:45:00+02:00"
        title:          Optional title (YouTube uses this)

    Returns post ID on success, None on failure.
    """
    video_url = upload_media(video_path)
    if not video_url:
        return None

    variables = {
        "input": {
            "channelId": channel_id,
            "text": caption,
            "shareMode": "customScheduled",
            "dueAt": scheduled_time,
            "assets": {
                "videos": [{"url": video_url}]
            },
        }
    }

    if title:
        variables["input"]["metadata"] = {"title": title}

    data = _gql(_CREATE_POST, variables)
    post = data.get("createPost", {}).get("post", {})
    if post.get("id"):
        logger.info(f"Scheduled post {post['id']} for {scheduled_time}")
        return post["id"]

    logger.error(f"createPost failed for channel {channel_id}")
    return None


def schedule_carousel(
    channel_ids: list[str],
    image_paths: list[str],
    caption: str,
    scheduled_time: str,
) -> list[str]:
    """
    Upload images and schedule a carousel post to multiple channels.

    Returns list of created post IDs.
    """
    image_urls = []
    for p in image_paths:
        url = upload_media(p)
        if url:
            image_urls.append(url)

    if not image_urls:
        logger.error("No images uploaded for carousel")
        return []

    post_ids = []
    for channel_id in channel_ids:
        variables = {
            "input": {
                "channelId": channel_id,
                "text": caption,
                "shareMode": "customScheduled",
                "dueAt": scheduled_time,
                "assets": {
                    "images": [{"url": u} for u in image_urls]
                },
            }
        }
        data = _gql(_CREATE_POST, variables)
        post = data.get("createPost", {}).get("post", {})
        if post.get("id"):
            post_ids.append(post["id"])
            logger.info(f"Carousel scheduled {post['id']} → {channel_id}")
        else:
            logger.error(f"Carousel createPost failed for {channel_id}")

    return post_ids


# ── Analytics ─────────────────────────────────────────────────────────────────

def get_analytics_summary(profiles: list[dict]) -> list[dict]:
    """
    Fetch sent post analytics for the weekly report.
    Returns list of dicts with: text, views, likes, shares.
    """
    if not profiles:
        return []

    org_id = profiles[0].get("organizationId", "")
    if not org_id:
        return []

    results = []
    for profile in profiles:
        data = _gql(
            """
            query GetPosts($channelId: String!) {
              posts(input: { channelId: $channelId, status: "sent" }, first: 50) {
                edges {
                  node {
                    id
                    text
                    statistics {
                      clicks
                      impressions
                      likes
                      shares
                    }
                  }
                }
              }
            }
            """,
            {"channelId": profile["id"]},
        )
        edges = data.get("posts", {}).get("edges", [])
        for edge in edges:
            node = edge.get("node", {})
            stats = node.get("statistics") or {}
            results.append({
                "text": node.get("text", ""),
                "views": stats.get("impressions", 0) or 0,
                "likes": stats.get("likes", 0) or 0,
                "shares": stats.get("shares", 0) or 0,
            })

    return results


# ── Compatibility shims (social_master.py uses these names) ───────────────────

def _token() -> str:
    """Alias for _api_key() — kept for backwards compatibility."""
    return _api_key()


def upload_video(
    video_path: str,
    caption: str,
    channel_ids: list,
    scheduled_time: str,
    youtube_profile_ids: list = None,
    title: str = None,
) -> set:
    """Schedule a video to multiple channels. Returns set of post IDs."""
    post_ids = set()
    for channel_id in channel_ids:
        post_id = schedule_video(channel_id, video_path, caption, scheduled_time, title=title)
        if post_id:
            post_ids.add(post_id)
    return post_ids


def upload_story(video_path: str, channel_ids: list, scheduled_time: str) -> set:
    """Schedule an IG Story (treated as a regular video post)."""
    return upload_video(video_path, "", channel_ids, scheduled_time)


def upload_carousel(
    slide_paths: list,
    caption: str,
    channel_ids: list,
    scheduled_time: str,
) -> list:
    """Schedule a carousel to multiple channels. Returns list of post IDs."""
    return schedule_carousel(channel_ids, slide_paths, caption, scheduled_time)
