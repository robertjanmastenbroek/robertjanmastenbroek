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

# Peak posting times in CET/CEST (Europe/Madrid = Tenerife)
# clip_index 0 → first slot, 1 → second, 2 → third
# 08:30 beats 09:00 — catches EU pre-commute before feed congestion peaks.
# 21:30 outperforms 19:00 for the nocturnal Psytrance/Techno audience + West Coast US (12:30 PST).
POST_SCHEDULE = {
    "instagram": ["08:30", "13:00", "21:30"],
    "facebook":  ["09:00", "13:30", "22:00"],  # 30 min after Instagram
    "youtube":   ["09:30", "14:00", "22:30"],
}
# CET offset: UTC+1 winter, UTC+2 summer (CEST). Use fixed +1 as conservative default;
# zoneinfo adjusts automatically when available.
_CET_OFFSET = timedelta(hours=1)


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
            # Persist to .env
            env_path = PROJECT_DIR / ".env"
            if env_path.exists():
                text = env_path.read_text()
                import re
                if re.search(r"^INSTAGRAM_ACCESS_TOKEN=", text, re.MULTILINE):
                    text = re.sub(
                        r"^(INSTAGRAM_ACCESS_TOKEN=).*$",
                        f"\\g<1>{new_token}",
                        text, flags=re.MULTILINE,
                    )
                else:
                    text += f"\nINSTAGRAM_ACCESS_TOKEN={new_token}\n"
                env_path.write_text(text)
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
                    # Persist so we don't need to re-fetch every run
                    env_path = PROJECT_DIR / ".env"
                    if env_path.exists():
                        import re
                        text = env_path.read_text()
                        if re.search(r"^FACEBOOK_PAGE_TOKEN=", text, re.MULTILINE):
                            text = re.sub(r"^(FACEBOOK_PAGE_TOKEN=).*$", f"\\g<1>{token}", text, flags=re.MULTILINE)
                        else:
                            text += f"\nFACEBOOK_PAGE_TOKEN={token}\n"
                        if re.search(r"^FACEBOOK_PAGE_ID=", text, re.MULTILINE):
                            text = re.sub(r"^(FACEBOOK_PAGE_ID=).*$", f"\\g<1>{page['id']}", text, flags=re.MULTILINE)
                        else:
                            text += f"FACEBOOK_PAGE_ID={page['id']}\n"
                        env_path.write_text(text)
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

        # 3. Finish — set description and publish
        finish_resp = requests.post(
            f"{FACEBOOK_GRAPH_BASE}/{page_id}/videos",
            params={"access_token": page_token},
            data={
                "upload_phase":      "finish",
                "upload_session_id": upload_id,
                "description":       description,
                "content_tags":      "",
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

    else:
        result = {"success": False, "platform": platform, "error": f"Unknown platform: {platform}"}

    # If native API failed, try Buffer fallback with schedule preserved
    if not result.get("success") and result.get("via") != "buffer_fallback":
        logger.warning(f"[distributor] {platform} native failed: {result.get('error')} — Buffer fallback")
        result = _buffer_fallback(clip, scheduled_at)

    result["clip_index"] = clip.get("clip_index")
    result["variant"]    = clip.get("variant")
    status = "✓" if result.get("success") else "✗"
    logger.info(f"[distributor] {platform} clip {clip.get('clip_index')}{clip.get('variant')}: {status}")
    return result


def distribute_all(clips: list) -> list:
    """Distribute all clips. Returns list of result dicts."""
    return [distribute_clip(c) for c in clips]
