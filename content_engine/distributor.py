"""
Module 4: Distributor
Native API uploads: Instagram Graph API + YouTube Data API v3.
Falls back to existing buffer_poster.py if native API unavailable or credentials missing.
Posts 3 clips × 2 platforms = 6 posts/day on staggered schedule.
"""
import logging
import os
import sys
import time
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR / "outreach_agent"))

logger = logging.getLogger(__name__)

INSTAGRAM_GRAPH_BASE = "https://graph.instagram.com/v21.0"
TIKTOK_API_BASE      = "https://open.tiktokapis.com/v2"
YOUTUBE_UPLOAD_BASE  = "https://www.googleapis.com/upload/youtube/v3"

# Peak posting times CET (clip_index → UTC offset applied externally by cron)
POST_SCHEDULE = {
    "instagram": ["09:00", "11:00", "19:00"],
    "youtube":   ["10:00", "13:00", "20:00"],
}


def _upload_video_for_instagram(video_path: str) -> str:
    """Upload video to get a public URL for Instagram Graph API. Tries multiple hosts."""
    # 1. Cloudinary (preferred)
    cloudinary_url = os.environ.get("CLOUDINARY_URL", "")
    if cloudinary_url:
        try:
            from video_host import upload_video
            return upload_video(video_path)
        except Exception as e:
            logger.warning(f"[distributor] Cloudinary failed: {e}")

    # 2. uguu.se (free, 48h — enough for IG to fetch)
    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                "https://uguu.se/upload",
                files={"files[]": (Path(video_path).name, f, "video/mp4")},
                timeout=120,
            )
        if resp.status_code == 200:
            data = resp.json()
            url = data.get("files", [{}])[0].get("url", "")
            if url:
                logger.info(f"[distributor] Uploaded to uguu.se: {url}")
                return url
    except Exception as e:
        logger.warning(f"[distributor] uguu.se failed: {e}")

    # 3. Catbox.moe
    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload", "userhash": ""},
                files={"fileToUpload": f},
                timeout=120,
            )
        if resp.status_code == 200 and resp.text.startswith("https://"):
            return resp.text.strip()
    except Exception as e:
        logger.warning(f"[distributor] Catbox failed: {e}")

    raise RuntimeError(f"All video upload methods failed for {video_path}")


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
                       api_key: str, oauth_token: str) -> dict:
    """Upload YouTube Short via YouTube Data API v3 resumable upload."""
    try:
        # Always refresh token to avoid 401
        oauth_token = _refresh_youtube_token() or oauth_token

        video_size = Path(video_path).stat().st_size

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
                "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
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


def _buffer_fallback(clip: dict) -> dict:
    """Fall back to existing buffer_poster.py if native API unavailable."""
    try:
        import buffer_poster
        result = buffer_poster.upload_video_and_queue(
            clip_path=clip["path"],
            tiktok_caption=clip.get("caption", ""),
            instagram_caption=clip.get("caption", ""),
            youtube_title=clip.get("track_title", "RJM") + " | Holy Rave #shorts",
            youtube_desc=clip.get("caption", ""),
        )
        success = any(v.get("success") for v in (result or {}).values())
        return {"success": success, "post_id": "buffer", "platform": clip["platform"],
                "via": "buffer_fallback"}
    except Exception as e:
        return {"success": False, "platform": clip["platform"],
                "error": f"Buffer fallback failed: {e}"}


def distribute_clip(clip: dict) -> dict:
    """
    Distribute a single clip to its target platform.
    Tries native API first, falls back to Buffer on failure or missing credentials.
    clip dict keys: {platform, path, caption, hook_text, track_title, clip_index, variant, ...}
    """
    platform = clip["platform"]
    path     = clip["path"]
    caption  = clip.get("caption", "")

    if platform == "instagram":
        ig_user_id   = os.environ.get("INSTAGRAM_USER_ID", "")
        access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        if ig_user_id and access_token:
            result = post_instagram_reel(path, caption, ig_user_id, access_token)
        else:
            logger.info("[distributor] Instagram credentials missing — using Buffer")
            result = _buffer_fallback(clip)

    elif platform == "youtube":
        api_key     = os.environ.get("YOUTUBE_API_KEY", "")
        oauth_token = os.environ.get("YOUTUBE_OAUTH_TOKEN", "")
        title       = f"{clip.get('track_title', 'RJM')} | Holy Rave #shorts"
        if api_key and oauth_token:
            result = post_youtube_short(path, title, caption, api_key, oauth_token)
        else:
            logger.info("[distributor] YouTube credentials missing — using Buffer")
            result = _buffer_fallback(clip)

    else:
        result = {"success": False, "platform": platform, "error": f"Unknown platform: {platform}"}

    # If native API failed, try Buffer fallback
    if not result.get("success") and result.get("via") != "buffer_fallback":
        logger.warning(f"[distributor] {platform} native failed: {result.get('error')} — Buffer fallback")
        result = _buffer_fallback(clip)

    result["clip_index"] = clip.get("clip_index")
    result["variant"]    = clip.get("variant")
    status = "✓" if result.get("success") else "✗"
    logger.info(f"[distributor] {platform} clip {clip.get('clip_index')}{clip.get('variant')}: {status}")
    return result


def distribute_all(clips: list) -> list:
    """Distribute all clips. Returns list of result dicts."""
    return [distribute_clip(c) for c in clips]
