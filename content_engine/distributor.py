"""
Module 4: Distributor
Native API uploads: Instagram Graph API + Facebook Page + YouTube Data API v3.
Falls back to existing buffer_poster.py if native API unavailable or credentials missing.
Posts 3 clips × 3 platforms = 9 posts/day on staggered schedule.
"""
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR / "outreach_agent"))

logger = logging.getLogger(__name__)

INSTAGRAM_GRAPH_BASE = "https://graph.instagram.com/v21.0"
FACEBOOK_GRAPH_BASE  = "https://graph.facebook.com/v21.0"
TIKTOK_API_BASE      = "https://open.tiktokapis.com/v2"
YOUTUBE_UPLOAD_BASE  = "https://www.googleapis.com/upload/youtube/v3"

# All 6 distribution targets the unified daily content pipeline fans out to.
# Reels go to instagram/youtube/facebook/tiktok; ephemeral cuts go to
# instagram_story/facebook_story (24h, link-stickered to the Spotify URL).
DISTRIBUTION_TARGETS = [
    "instagram", "youtube", "facebook", "tiktok",
    "instagram_story", "facebook_story",
]

# Peak posting times in CET/CEST (Europe/Madrid = Tenerife)
# clip_index 0 → first slot, 1 → second, 2 → third
# Stories run alongside Reels but offset slightly so they don't compete for
# the same audience attention window.
POST_SCHEDULE = {
    "instagram":       ["08:30", "13:00", "19:30"],
    "youtube":         ["09:00", "13:30", "20:00"],
    "facebook":        ["09:15", "13:45", "20:15"],
    "tiktok":          ["09:30", "14:00", "20:30"],
    "instagram_story": ["08:45", "13:15", "19:45"],
    "facebook_story":  ["09:15", "13:45", "20:15"],
}
# CET offset: UTC+1 winter, UTC+2 summer (CEST). Use fixed +1 as conservative default;
# zoneinfo adjusts automatically when available.
_CET_OFFSET = timedelta(hours=1)


class CircuitBreaker:
    """Track consecutive failures per platform. Trip after threshold.

    Once tripped, the breaker stays open until ``reset()`` or ``record_success()``
    is called for that platform. Each successful post resets the failure counter.
    """

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._failures: dict[str, int] = {}
        self._tripped: set[str] = set()

    def record_failure(self, platform: str) -> None:
        self._failures[platform] = self._failures.get(platform, 0) + 1
        if self._failures[platform] >= self.threshold:
            self._tripped.add(platform)
            logger.warning(
                f"[distributor] Circuit breaker TRIPPED for {platform} after "
                f"{self.threshold} consecutive failures"
            )

    def record_success(self, platform: str) -> None:
        self._failures[platform] = 0
        self._tripped.discard(platform)

    def is_open(self, platform: str) -> bool:
        return platform in self._tripped

    def reset(self, platform: str) -> None:
        self._failures[platform] = 0
        self._tripped.discard(platform)


def _scheduled_at_utc(platform: str, clip_index: int) -> str:
    """
    Return ISO-8601 UTC datetime string for when clip_index should go live.
    Uses POST_SCHEDULE times in CET. If the slot is already past for today,
    still returns today's time (Buffer/YouTube will publish immediately).
    """
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Madrid")  # Tenerife (same as CET/CEST)
    except Exception:
        tz = timezone(_CET_OFFSET)

    slots  = POST_SCHEDULE.get(platform, POST_SCHEDULE["instagram"])
    slot   = slots[clip_index % len(slots)]
    h, m   = int(slot.split(":")[0]), int(slot.split(":")[1])
    today  = datetime.now(tz).replace(hour=h, minute=m, second=0, microsecond=0)
    utc_dt = today.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _upload_video_for_instagram(video_path: str) -> str:
    """Upload video to get a stable public URL for Instagram/TikTok Graph API.

    Delegates to video_host.upload_video(): Cloudinary (primary) → uguu.se (last resort).
    Configure Cloudinary by setting CLOUDINARY_URL in Railway environment variables.
    """
    from video_host import upload_video
    return upload_video(video_path)


def _atomic_env_update(updates: dict[str, str]) -> bool:
    """
    Atomically update or append one or more KEY=VALUE pairs in .env.

    Uses a temp file + os.replace() so readers never see a partial file,
    and concurrent refreshes (e.g. two cron jobs racing) can't corrupt state.
    The last writer wins — which is exactly what we want for rotated tokens.

    Returns True on success, False on any failure (logged).
    """
    import os as _os
    import re
    import tempfile

    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return False

    try:
        text = env_path.read_text()
        for key, value in updates.items():
            # Escape backslashes so re.sub's replacement string treats them literally
            safe_value = value.replace("\\", "\\\\")
            pattern = rf"^{re.escape(key)}=.*$"
            if re.search(pattern, text, re.MULTILINE):
                text = re.sub(pattern, f"{key}={safe_value}", text, flags=re.MULTILINE)
            else:
                if text and not text.endswith("\n"):
                    text += "\n"
                text += f"{key}={value}\n"

        # Write to temp file in same directory (so os.replace is atomic on same FS),
        # then atomically swap into place. NamedTemporaryFile with delete=False so
        # we can close then rename without Python nuking the file first.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".env.", suffix=".tmp", dir=str(PROJECT_DIR)
        )
        try:
            with _os.fdopen(fd, "w") as f:
                f.write(text)
            # Preserve original permissions if readable
            try:
                mode = env_path.stat().st_mode & 0o777
                _os.chmod(tmp_path, mode)
            except Exception:
                pass
            _os.replace(tmp_path, env_path)
        except Exception:
            # Clean up temp file if replace failed
            try:
                _os.unlink(tmp_path)
            except Exception:
                pass
            raise
        return True
    except Exception as e:
        logger.warning(f"[distributor] atomic .env update failed: {e}")
        return False


def refresh_instagram_token(access_token: str = "") -> str:
    """
    Refresh the Instagram long-lived token (valid 60 days, resets on each refresh).
    Writes the new token back to .env so it persists across runs.
    Safe to call weekly — Meta only refreshes if token is still valid.
    """
    token = access_token or os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    if not token:
        return token

    try:
        resp = requests.get(
            "https://graph.instagram.com/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
            timeout=15,
        )
        data = resp.json()
        new_token = data.get("access_token", "")
        expires_in = data.get("expires_in", 0)
        if new_token:
            logger.info(f"[distributor] Instagram token refreshed (expires in {expires_in}s / ~{expires_in//86400}d)")
            _atomic_env_update({"INSTAGRAM_ACCESS_TOKEN": new_token})
            os.environ["INSTAGRAM_ACCESS_TOKEN"] = new_token
            return new_token
        error = data.get("error", {})
        logger.warning(f"[distributor] Instagram token refresh failed: {error.get('message', resp.text[:200])}")
    except Exception as e:
        logger.warning(f"[distributor] Instagram token refresh error: {e}")

    return token


def get_facebook_page_token(user_access_token: str, page_id: str) -> str:
    """
    Exchange a User Access Token for a Page Access Token.
    Page tokens never expire (unlike User tokens) when generated from a long-lived User token.
    Saves to .env as FACEBOOK_PAGE_TOKEN on first call.
    """
    try:
        resp = requests.get(
            f"{FACEBOOK_GRAPH_BASE}/me/accounts",
            params={"access_token": user_access_token},
            timeout=15,
        )
        pages = resp.json().get("data", [])
        for page in pages:
            if page.get("id") == page_id or page.get("name", "").lower().replace(" ", "") in ("holyraveofficial", "robertjanmastenbroek"):
                token = page.get("access_token", "")
                if token:
                    # Persist atomically so concurrent refreshes can't corrupt .env
                    _atomic_env_update({
                        "FACEBOOK_PAGE_TOKEN": token,
                        "FACEBOOK_PAGE_ID": page["id"],
                    })
                    os.environ["FACEBOOK_PAGE_TOKEN"] = token
                    os.environ["FACEBOOK_PAGE_ID"]    = page["id"]
                    logger.info(f"[distributor] Facebook page token obtained for: {page.get('name')}")
                return token
        logger.warning(f"[distributor] Facebook page not found among {[p.get('name') for p in pages]}")
    except Exception as e:
        logger.warning(f"[distributor] Facebook page token error: {e}")
    return ""


def post_facebook_reel(video_path: str, description: str, page_id: str, page_token: str) -> dict:
    """
    Upload a video as a Facebook Reel to a Page via the Graph API resumable upload.
    Returns {success: bool, post_id: str|None, platform: 'facebook', error: str|None}
    """
    try:
        video_size = Path(video_path).stat().st_size

        # 1. Start resumable upload session
        start_resp = requests.post(
            f"{FACEBOOK_GRAPH_BASE}/{page_id}/videos",
            params={"access_token": page_token},
            data={
                "upload_phase": "start",
                "file_size":    video_size,
            },
            timeout=30,
        )
        if start_resp.status_code != 200:
            return {"success": False, "platform": "facebook",
                    "error": start_resp.json().get("error", {}).get("message", start_resp.text[:200])}

        start_data  = start_resp.json()
        upload_id   = start_data.get("upload_session_id", "")
        video_id    = start_data.get("video_id", "")
        start_offset = int(start_data.get("start_offset", 0))
        end_offset   = int(start_data.get("end_offset", video_size))

        # 2. Upload the file in one chunk (≤ 1 GB; shorts are well under this)
        with open(video_path, "rb") as f:
            f.seek(start_offset)
            chunk = f.read(end_offset - start_offset)

        transfer_resp = requests.post(
            f"{FACEBOOK_GRAPH_BASE}/{page_id}/videos",
            params={"access_token": page_token},
            data={
                "upload_phase":   "transfer",
                "upload_session_id": upload_id,
                "start_offset":   start_offset,
            },
            files={"video_file_chunk": (Path(video_path).name, chunk, "video/mp4")},
            timeout=300,
        )
        if transfer_resp.status_code != 200:
            return {"success": False, "platform": "facebook",
                    "error": transfer_resp.json().get("error", {}).get("message", transfer_resp.text[:200])}

        # 3. Finish — set description and publish. Meta defaults uploaded videos
        # to draft state; explicitly setting published=true ensures the Reel goes
        # live instead of sitting in the Page's "Your Posts" drafts.
        finish_resp = requests.post(
            f"{FACEBOOK_GRAPH_BASE}/{page_id}/videos",
            params={"access_token": page_token},
            data={
                "upload_phase":      "finish",
                "upload_session_id": upload_id,
                "description":       description,
                "content_tags":      "",
                "published":         "true",
                "video_state":       "PUBLISHED",
            },
            timeout=30,
        )
        if finish_resp.status_code != 200:
            return {"success": False, "platform": "facebook",
                    "error": finish_resp.json().get("error", {}).get("message", finish_resp.text[:200])}

        logger.info(f"[distributor] Facebook Reel published: video_id={video_id}")
        return {"success": True, "post_id": video_id, "platform": "facebook"}

    except Exception as e:
        return {"success": False, "platform": "facebook", "error": str(e)}


def _refresh_youtube_token() -> str:
    """Refresh YouTube OAuth token using client_secret.json + YOUTUBE_REFRESH_TOKEN."""
    import json
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
    if not refresh_token:
        return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")

    secret_path = PROJECT_DIR / "client_secret.json"
    if not secret_path.exists():
        return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")

    try:
        data = json.loads(secret_path.read_text())
        creds = data.get("installed") or data.get("web") or {}
        resp = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id":     creds.get("client_id", ""),
            "client_secret": creds.get("client_secret", ""),
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token",
        }, timeout=15)
        token = resp.json().get("access_token", "")
        if token:
            logger.info("[distributor] YouTube token refreshed")
            return token
    except Exception as e:
        logger.warning(f"[distributor] YouTube token refresh failed: {e}")

    return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")


def post_instagram_reel(video_path: str, caption: str, ig_user_id: str, access_token: str) -> dict:
    """
    Upload Reel via Instagram Graph API.
    Returns {success: bool, post_id: str|None, platform: 'instagram', error: str|None}
    """
    try:
        # Refresh token before use — safe to call every time, resets 60-day clock
        access_token = refresh_instagram_token(access_token)
        video_url = _upload_video_for_instagram(video_path)

        # 1. Create media container
        resp = requests.post(
            f"{INSTAGRAM_GRAPH_BASE}/{ig_user_id}/media",
            params={
                "access_token": access_token,
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return {"success": False, "platform": "instagram",
                    "error": resp.json().get("error", {}).get("message", resp.text[:200])}

        creation_id = resp.json()["id"]

        # 2. Wait for FINISHED (up to 2 minutes)
        for _ in range(24):
            time.sleep(5)
            status_resp = requests.get(
                f"{INSTAGRAM_GRAPH_BASE}/{creation_id}",
                params={"fields": "status_code", "access_token": access_token},
                timeout=15,
            )
            if status_resp.json().get("status_code") == "FINISHED":
                break

        # 3. Publish
        pub_resp = requests.post(
            f"{INSTAGRAM_GRAPH_BASE}/{ig_user_id}/media_publish",
            params={"creation_id": creation_id, "access_token": access_token},
            timeout=30,
        )
        if pub_resp.status_code != 200:
            return {"success": False, "platform": "instagram",
                    "error": pub_resp.json().get("error", {}).get("message", pub_resp.text[:200])}

        return {"success": True, "post_id": pub_resp.json()["id"], "platform": "instagram"}

    except Exception as e:
        return {"success": False, "platform": "instagram", "error": str(e)}


def post_tiktok(video_path: str, caption: str, access_token: str) -> dict:
    """Upload video via TikTok Content Posting API v2 (PULL_FROM_URL method)."""
    try:
        video_url = _upload_video_for_instagram(video_path)
        headers   = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

        init_resp = requests.post(
            f"{TIKTOK_API_BASE}/post/publish/video/init/",
            headers=headers,
            json={
                "post_info": {
                    "title": caption[:150],
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                },
                "source_info": {
                    "source": "PULL_FROM_URL",
                    "video_url": video_url,
                },
            },
            timeout=30,
        )

        if init_resp.status_code not in (200, 201):
            return {"success": False, "platform": "tiktok", "error": init_resp.text[:200]}

        publish_id = init_resp.json().get("data", {}).get("publish_id", "")

        for _ in range(24):
            time.sleep(5)
            status_resp = requests.post(
                f"{TIKTOK_API_BASE}/post/publish/status/fetch/",
                headers=headers,
                json={"publish_id": publish_id},
                timeout=15,
            )
            status = status_resp.json().get("data", {}).get("status", "")
            if status == "PUBLISH_COMPLETE":
                return {"success": True, "post_id": publish_id, "platform": "tiktok"}
            if "FAIL" in status:
                return {"success": False, "platform": "tiktok", "error": f"TikTok: {status}"}

        return {"success": False, "platform": "tiktok", "error": "Publish timed out"}

    except Exception as e:
        return {"success": False, "platform": "tiktok", "error": str(e)}


def post_youtube_short(video_path: str, title: str, description: str,
                       api_key: str, oauth_token: str,
                       publish_at: str = "") -> dict:
    """
    Upload YouTube Short via YouTube Data API v3 resumable upload.
    publish_at: ISO-8601 UTC string (e.g. "2026-04-14T13:00:00Z"). When set,
    the video is uploaded as private and scheduled to go public at that time.
    """
    try:
        # Always refresh token to avoid 401
        oauth_token = _refresh_youtube_token() or oauth_token

        video_size = Path(video_path).stat().st_size

        status_obj: dict = {"selfDeclaredMadeForKids": False}
        if publish_at:
            # YouTube requires privacyStatus=private to use publishAt
            status_obj["privacyStatus"] = "private"
            status_obj["publishAt"]     = publish_at
            logger.info(f"[distributor] YouTube scheduled for {publish_at}")
        else:
            status_obj["privacyStatus"] = "public"

        init_resp = requests.post(
            f"{YOUTUBE_UPLOAD_BASE}/videos",
            params={"uploadType": "resumable", "part": "snippet,status", "key": api_key},
            headers={
                "Authorization": f"Bearer {oauth_token}",
                "Content-Type": "application/json",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(video_size),
            },
            json={
                "snippet": {
                    "title": title[:100],
                    "description": description,
                    "tags": ["techno", "psytrance", "holyrave", "RJM", "#shorts"],
                    "categoryId": "10",
                },
                "status": status_obj,
            },
            timeout=30,
        )

        if init_resp.status_code not in (200, 201):
            return {"success": False, "platform": "youtube", "error": init_resp.text[:200]}

        upload_url = init_resp.headers.get("Location", "")
        if not upload_url:
            return {"success": False, "platform": "youtube", "error": "No upload URL returned"}

        with open(video_path, "rb") as f:
            upload_resp = requests.put(
                upload_url,
                data=f,
                headers={"Content-Type": "video/mp4", "Content-Length": str(video_size)},
                timeout=300,
            )

        if upload_resp.status_code not in (200, 201):
            return {"success": False, "platform": "youtube", "error": upload_resp.text[:200]}

        return {"success": True, "post_id": upload_resp.json().get("id", ""), "platform": "youtube"}

    except Exception as e:
        return {"success": False, "platform": "youtube", "error": str(e)}


def _buffer_fallback(clip: dict, scheduled_at: str = "") -> dict:
    """Fall back to existing buffer_poster.py if native API unavailable."""
    try:
        import buffer_poster
        result = buffer_poster.upload_video_and_queue(
            clip_path=clip["path"],
            tiktok_caption=clip.get("caption", ""),
            instagram_caption=clip.get("caption", ""),
            youtube_title=clip.get("track_title", "RJM") + " | Holy Rave #shorts",
            youtube_desc=clip.get("caption", ""),
            scheduled_at=scheduled_at or None,
        )
        success = any(v.get("success") for v in (result or {}).values())
        return {"success": success, "post_id": "buffer", "platform": clip["platform"],
                "via": "buffer_fallback"}
    except Exception as e:
        return {"success": False, "platform": clip["platform"],
                "error": f"Buffer fallback failed: {e}"}


def distribute_clip(clip: dict) -> dict:
    """
    Distribute a single clip to its target platform on its scheduled time slot.
    Tries native API first, falls back to Buffer on failure or missing credentials.
    clip dict keys: {platform, path, caption, hook_text, track_title, clip_index, variant, ...}
    """
    platform    = clip["platform"]
    path        = clip["path"]
    caption     = clip.get("caption", "")
    clip_index  = clip.get("clip_index", 0)
    scheduled_at = _scheduled_at_utc(platform, clip_index)

    logger.info(f"[distributor] {platform} clip {clip_index} → scheduled {scheduled_at}")

    if platform == "instagram":
        ig_user_id   = os.environ.get("INSTAGRAM_USER_ID", "")
        access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        if ig_user_id and access_token:
            # Refresh token, then post via native API; Buffer used for scheduling
            result = post_instagram_reel(path, caption, ig_user_id, access_token)
            if not result.get("success"):
                logger.warning(f"[distributor] Instagram native failed: {result.get('error')} — Buffer fallback")
                result = _buffer_fallback(clip, scheduled_at)
        else:
            logger.info("[distributor] Instagram credentials missing — using Buffer")
            result = _buffer_fallback(clip, scheduled_at)

    elif platform == "facebook":
        page_token = os.environ.get("FACEBOOK_PAGE_TOKEN", "")
        page_id    = os.environ.get("FACEBOOK_PAGE_ID", "")
        # Auto-fetch page token from user token if not yet saved
        if not page_token:
            user_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
            if user_token:
                page_token = get_facebook_page_token(user_token, page_id)
        if page_token and page_id:
            result = post_facebook_reel(path, caption, page_id, page_token)
        else:
            logger.info("[distributor] Facebook credentials missing — skipping Facebook")
            result = {"success": False, "platform": "facebook", "error": "credentials missing"}

    elif platform == "youtube":
        api_key     = os.environ.get("YOUTUBE_API_KEY", "")
        oauth_token = os.environ.get("YOUTUBE_OAUTH_TOKEN", "")
        title       = f"{clip.get('track_title', 'RJM')} | Holy Rave #shorts"
        if api_key and oauth_token:
            result = post_youtube_short(path, title, caption, api_key, oauth_token,
                                        publish_at=scheduled_at)
        else:
            logger.info("[distributor] YouTube credentials missing — using Buffer")
            result = _buffer_fallback(clip, scheduled_at)

    elif platform == "tiktok":
        # TikTok posting routes through Buffer (no native OAuth flow in this environment).
        result = _buffer_fallback(clip, scheduled_at)

    elif platform == "instagram_story":
        story_path   = clip.get("story_path", clip.get("path", ""))
        ig_user_id   = os.environ.get("INSTAGRAM_USER_ID", "")
        access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        spotify_url  = clip.get("spotify_url", "")
        if ig_user_id and access_token:
            result = post_instagram_story(
                story_path, caption, ig_user_id, access_token, spotify_url,
            )
        else:
            logger.info("[distributor] Instagram Story credentials missing — skipping")
            result = {"success": False, "platform": "instagram_story", "error": "credentials missing"}

    elif platform == "facebook_story":
        story_path = clip.get("story_path", clip.get("path", ""))
        page_id    = os.environ.get("FACEBOOK_PAGE_ID", "")
        page_token = os.environ.get("FACEBOOK_PAGE_TOKEN", "")
        if page_id and page_token:
            result = post_facebook_story(story_path, page_id, page_token)
        else:
            logger.info("[distributor] Facebook Story credentials missing — skipping")
            result = {"success": False, "platform": "facebook_story", "error": "credentials missing"}

    else:
        result = {"success": False, "platform": platform, "error": f"Unknown platform: {platform}"}

    # If native API failed, try Buffer fallback with schedule preserved — but only
    # for Reel targets. Stories and TikTok-via-Buffer already attempted what they
    # could; looping back to Buffer for a Story would post it as a Reel.
    if (
        not result.get("success")
        and result.get("via") != "buffer_fallback"
        and platform in ("instagram", "youtube", "facebook")
    ):
        logger.warning(f"[distributor] {platform} native failed: {result.get('error')} — Buffer fallback")
        result = _buffer_fallback(clip, scheduled_at)

    result["clip_index"] = clip.get("clip_index")
    result["variant"]    = clip.get("variant")
    status = "✓" if result.get("success") else "✗"
    logger.info(f"[distributor] {platform} clip {clip.get('clip_index')}{clip.get('variant')}: {status}")
    return result


def post_instagram_story(video_path: str, caption: str, ig_user_id: str,
                         access_token: str, spotify_url: str = "") -> dict:
    """Post an Instagram Story via Graph API. Same flow as Reel but media_type=STORIES.

    Optional ``spotify_url`` becomes a Story link sticker — the only direct
    Spotify-driver Stories provide.

    Note: ``caption`` is accepted for API symmetry but NOT sent to the Graph API —
    STORIES media_type rejects the caption parameter. Any copy must be baked
    into the video via drawtext in the renderer's story variant.
    """
    del caption  # intentionally unused — STORIES has no caption field
    try:
        # Refresh token before use — keeps the 60-day clock alive
        access_token = refresh_instagram_token(access_token)
        video_url = _upload_video_for_instagram(video_path)
        if not video_url:
            return {"success": False, "platform": "instagram_story",
                    "error": "video upload failed"}

        # 1. Create media container (STORIES type) — no caption param
        params = {
            "video_url":    video_url,
            "media_type":   "STORIES",
            "access_token": access_token,
        }
        if spotify_url:
            params["link"] = spotify_url  # Link sticker

        resp = requests.post(
            f"{INSTAGRAM_GRAPH_BASE}/{ig_user_id}/media",
            data=params,
            timeout=30,
        )
        data = resp.json()
        container_id = data.get("id")
        if not container_id:
            return {"success": False, "platform": "instagram_story",
                    "error": f"container creation failed: {data}"}

        # 2. Poll for FINISHED (up to 2 minutes)
        for _ in range(24):
            time.sleep(5)
            status_resp = requests.get(
                f"{INSTAGRAM_GRAPH_BASE}/{container_id}",
                params={"fields": "status_code", "access_token": access_token},
                timeout=15,
            )
            status = status_resp.json().get("status_code", "")
            if status == "FINISHED":
                break
            if status == "ERROR":
                return {"success": False, "platform": "instagram_story",
                        "error": "container processing error"}

        # 3. Publish
        pub_resp = requests.post(
            f"{INSTAGRAM_GRAPH_BASE}/{ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": access_token},
            timeout=30,
        )
        pub_data = pub_resp.json()
        return {"success": True, "platform": "instagram_story",
                "post_id": pub_data.get("id", container_id)}

    except Exception as e:
        return {"success": False, "platform": "instagram_story", "error": str(e)}


def post_facebook_story(video_path: str, page_id: str, page_token: str) -> dict:
    """Post a Facebook Story via Graph API ``video_stories`` endpoint.

    Uses Meta's 3-phase resumable upload: start → transfer (to /{video_id}) →
    finish (with post_status=PUBLISHED so the Story goes live).
    """
    try:
        url = f"{FACEBOOK_GRAPH_BASE}/{page_id}/video_stories"

        # 1. Initialize upload — Meta returns upload_url + video_id
        init_resp = requests.post(
            url,
            data={"upload_phase": "start", "access_token": page_token},
            timeout=30,
        )
        init_data = init_resp.json()
        video_id = init_data.get("video_id")
        upload_url = init_data.get("upload_url", "")
        if not video_id:
            return {"success": False, "platform": "facebook_story",
                    "error": f"story init failed: {init_data}"}

        # 2. Upload video binary. Meta's resumable upload for video_stories uses
        # the returned upload_url (from the start phase). Fall back to
        # /{video_id} with multipart source if no upload_url was provided.
        transfer_ok = False
        if upload_url:
            with open(video_path, "rb") as f:
                file_size = Path(video_path).stat().st_size
                transfer_resp = requests.post(
                    upload_url,
                    data=f.read(),
                    headers={
                        "Authorization": f"OAuth {page_token}",
                        "offset": "0",
                        "file_size": str(file_size),
                    },
                    timeout=300,
                )
                transfer_ok = transfer_resp.status_code in (200, 201)
        if not transfer_ok:
            # Legacy path — some FB Page SDKs still accept this
            with open(video_path, "rb") as f:
                transfer_resp = requests.post(
                    f"{FACEBOOK_GRAPH_BASE}/{video_id}",
                    files={"source": f},
                    data={"access_token": page_token, "upload_phase": "transfer"},
                    timeout=300,
                )
                transfer_ok = transfer_resp.status_code in (200, 201)
        if not transfer_ok:
            return {"success": False, "platform": "facebook_story",
                    "error": f"transfer failed: {transfer_resp.text[:200]}"}

        # 3. Finish — publish explicitly. Without post_status the Story never
        # shows up on the Page Story tray.
        finish_resp = requests.post(
            url,
            data={
                "upload_phase": "finish",
                "video_id":     video_id,
                "post_status":  "PUBLISHED",
                "access_token": page_token,
            },
            timeout=30,
        )
        finish_data = finish_resp.json()
        success = bool(finish_data.get("success", False)) or bool(finish_data.get("post_id"))
        return {
            "success":  success,
            "platform": "facebook_story",
            "post_id":  str(finish_data.get("post_id", video_id)),
            "error":    None if success else finish_data.get("error", {}).get("message", str(finish_data)),
        }

    except Exception as e:
        return {"success": False, "platform": "facebook_story", "error": str(e)}


def _distribute_single(clip: dict, target: str) -> dict:
    """Route a clip to the correct posting function for the given target.

    Single dispatch surface: all targets flow through ``distribute_clip``, which
    knows how to handle every entry in ``DISTRIBUTION_TARGETS``.
    """
    if target not in DISTRIBUTION_TARGETS:
        return {"success": False, "platform": target, "error": f"unknown target: {target}"}
    return distribute_clip({**clip, "platform": target})


def _distribute_with_retry(clip: dict, target: str, max_retries: int = 3) -> dict:
    """Distribute to a single target with exponential backoff retry.

    Delays between attempts: 2s, 8s, 32s. After exhausting native retries for
    a Reel target (instagram/youtube/facebook), falls back to Buffer with the
    schedule preserved. Story targets do NOT fall back to Buffer (Buffer can't
    post Stories).
    """
    delays = [2, 8, 32]
    result: dict = {}

    for attempt in range(max_retries):
        result = _distribute_single(clip, target)
        if result.get("success"):
            return result
        if attempt < max_retries - 1:
            logger.warning(
                f"[distributor] Retry {attempt + 1}/{max_retries} for {target}: "
                f"{result.get('error', '')}"
            )
            time.sleep(delays[attempt])

    # Final fallback to Buffer (only for Reel targets — Stories aren't supported by Buffer)
    if target in ("instagram", "youtube", "facebook"):
        logger.info(f"[distributor] Falling back to Buffer for {target}")
        return _buffer_fallback(
            {**clip, "platform": target},
            _scheduled_at_utc(target, clip.get("clip_index", 0)),
        )

    return result


def distribute_all(clips: list, circuit_breaker: "CircuitBreaker | None" = None) -> list:
    """Distribute clips to all 6 targets with retry + circuit breaker.

    For each clip, fans out to every target in ``DISTRIBUTION_TARGETS``. A target
    whose circuit breaker is open is skipped (and the failure recorded in the
    result list so the caller can see what was suppressed).
    """
    cb = circuit_breaker or CircuitBreaker()
    results: list = []

    for clip in clips:
        for target in DISTRIBUTION_TARGETS:
            if cb.is_open(target):
                logger.warning(f"[distributor] Skipping {target} — circuit breaker open")
                results.append({
                    "platform":   target,
                    "success":    False,
                    "error":      "circuit breaker open",
                    "clip_index": clip.get("clip_index"),
                    "variant":    clip.get("variant"),
                })
                continue

            result = _distribute_with_retry(clip, target, max_retries=3)
            if result.get("success"):
                cb.record_success(target)
            else:
                cb.record_failure(target)

            # Preserve clip metadata on every result row
            result.setdefault("platform", target)
            result["clip_index"] = clip.get("clip_index")
            result["variant"]    = clip.get("variant")
            results.append(result)

    return results
