"""
Module 4: Distributor
Native API uploads: Instagram Graph API + Facebook Page + YouTube Data API v3.
Falls back to existing buffer_poster.py if native API unavailable or credentials missing.
Posts 3 clips × 3 platforms = 9 posts/day on staggered schedule.
"""
import json
import logging
import os
import sys
import time
from datetime import date as _date, datetime, timezone, timedelta
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).parent.parent
PERFORMANCE_DIR = PROJECT_DIR / "data" / "performance"
sys.path.insert(0, str(PROJECT_DIR / "outreach_agent"))

logger = logging.getLogger(__name__)


# ─── Native-post dedup ────────────────────────────────────────────────────────
# Separate from _posts.json so it survives manual registry deletion.
# Prevents native double-posts when a user clears the idempotency guard
# to force a re-run after deleting Buffer scheduled videos.

def _native_dedup_path(date_str: str = "") -> Path:
    return PERFORMANCE_DIR / f"{date_str or _date.today().isoformat()}_native.json"


def _load_native_registry(date_str: str = "") -> set:
    """Return set of (platform, clip_index) already posted natively today."""
    path = _native_dedup_path(date_str)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        return {(r["platform"], r["clip_index"]) for r in data if r.get("success")}
    except Exception:
        return set()


def _record_native_post(platform: str, clip_index: int, post_id: str, date_str: str = "") -> None:
    """Append a successful native post to the dedup registry."""
    path = _native_dedup_path(date_str)
    PERFORMANCE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text()) if path.exists() else []
        data.append({
            "platform": platform,
            "clip_index": clip_index,
            "post_id": post_id,
            "success": True,
            "posted_at": datetime.now(timezone.utc).isoformat(),
        })
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning(f"[distributor] native dedup write failed: {e}")

# IG publishing is now routed through graph.facebook.com — that's the
# endpoint that accepts the Facebook User/Page access token we get via the
# "Instagram Business Login through Facebook" flow. graph.instagram.com
# v21 only accepts IG-native tokens (Basic Display / IG Login), not FB
# User tokens, and that mismatch was what 400'd the refresh calls.
INSTAGRAM_GRAPH_BASE = "https://graph.facebook.com/v21.0"
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
    Uses POST_SCHEDULE times in CET. If the slot has already passed, returns
    now + 15 minutes so Buffer/TikTok always receive a future timestamp.
    """
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Madrid")  # Tenerife (same as CET/CEST)
    except Exception:
        tz = timezone(_CET_OFFSET)

    slots  = POST_SCHEDULE.get(platform, POST_SCHEDULE["instagram"])
    slot   = slots[clip_index % len(slots)]
    h, m   = int(slot.split(":")[0]), int(slot.split(":")[1])
    now    = datetime.now(timezone.utc)
    today  = datetime.now(tz).replace(hour=h, minute=m, second=0, microsecond=0)
    utc_dt = today.astimezone(timezone.utc)

    # If the scheduled time is in the past (or within the next 5 minutes, which
    # is too tight for Buffer to accept), push to now + 15 minutes so the post
    # is always accepted as a future-scheduled item.
    if utc_dt <= now + timedelta(minutes=5):
        utc_dt = now + timedelta(minutes=15)

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
                text += f"{key}={safe_value}\n"

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


# Module-level flag so a single dead token short-circuits ALL subsequent native
# Instagram calls for this run. Without this, every Reel / Story / clip would
# re-attempt the refresh → 3× retry → fail → fall to Buffer loop, wasting 30+
# seconds per clip and cluttering logs.
_INSTAGRAM_TOKEN_DEAD: bool = False


def refresh_instagram_token(access_token: str = "") -> str:
    """
    Refresh the long-lived token used for Instagram Business publishing.

    Our tokens come from the "Instagram Business Login through Facebook"
    flow, so they're Facebook User tokens (EAA*) with instagram_content_publish
    scope — NOT Instagram Basic Display tokens. The correct refresh endpoint
    for these is graph.facebook.com/oauth/access_token?grant_type=fb_exchange_token,
    which resets the 60-day window.

    graph.instagram.com/refresh_access_token is only valid for IG-native tokens
    (the old IG Basic Display / IG Login flows) and will 400 on FB tokens with
    "Invalid OAuth access token" — that mismatch is why every run before
    2026-04-16 was silently falling back to Buffer.

    Safe to call once per day / per run. If Meta responds with code 190
    (OAuthException), the token is genuinely expired — we flip the dead
    flag and let the fallback chain take over.
    """
    global _INSTAGRAM_TOKEN_DEAD
    token = access_token or os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    if not token or _INSTAGRAM_TOKEN_DEAD:
        return token

    app_id     = os.environ.get("META_APP_ID", "")
    app_secret = os.environ.get("META_APP_SECRET", "")
    if not (app_id and app_secret):
        # Can't refresh without app creds, but the token may still be valid.
        return token

    try:
        resp = requests.get(
            f"{FACEBOOK_GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type":        "fb_exchange_token",
                "client_id":         app_id,
                "client_secret":     app_secret,
                "fb_exchange_token": token,
            },
            timeout=15,
        )
        data = resp.json()
        new_token  = data.get("access_token", "")
        expires_in = data.get("expires_in", 0)
        if new_token:
            logger.info(
                f"[distributor] Instagram/FB User token refreshed "
                f"(expires in {expires_in}s / ~{expires_in // 86400}d)"
            )
            _atomic_env_update({"INSTAGRAM_ACCESS_TOKEN": new_token})
            os.environ["INSTAGRAM_ACCESS_TOKEN"] = new_token
            return new_token
        error = data.get("error", {})
        code  = error.get("code", 0)
        msg   = error.get("message", resp.text[:200])
        logger.warning(f"[distributor] Instagram token refresh failed: {msg}")
        if code == 190 or "Cannot parse access token" in msg or "expired" in msg.lower():
            _INSTAGRAM_TOKEN_DEAD = True
            logger.warning(
                "[distributor] Instagram token permanently dead — "
                "routing all Instagram traffic through Buffer for this run. "
                "Re-auth at https://developers.facebook.com/tools/explorer/ and re-run "
                "the Meta OAuth flow to refresh INSTAGRAM_ACCESS_TOKEN."
            )
    except Exception as e:
        logger.warning(f"[distributor] Instagram token refresh error: {e}")

    return token


def _instagram_native_available() -> bool:
    """Whether native Instagram Graph API is usable for this run.

    Returns False if the token is missing, the module-level dead flag has been
    set by refresh_instagram_token(), or the user id is missing.
    """
    if _INSTAGRAM_TOKEN_DEAD:
        return False
    return bool(
        os.environ.get("INSTAGRAM_USER_ID", "")
        and os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    )


def get_facebook_page_token(user_access_token: str, page_id: str) -> str:
    """
    Exchange a User Access Token for a Page Access Token.
    Page tokens never expire (unlike User tokens) when generated from a long-lived User token.
    Saves to .env as FACEBOOK_PAGE_TOKEN on first call.
    """
    import re as _re

    def _norm(name: str) -> str:
        # Strip every non-alphanumeric so "Robert-Jan Mastenbroek" → "robertjanmastenbroek"
        return _re.sub(r"[^a-z0-9]", "", (name or "").lower())

    expected_names = {"holyraveofficial", "robertjanmastenbroek"}
    try:
        resp = requests.get(
            f"{FACEBOOK_GRAPH_BASE}/me/accounts",
            params={"access_token": user_access_token},
            timeout=15,
        )
        pages = resp.json().get("data", [])
        for page in pages:
            if page.get("id") == page_id or _norm(page.get("name", "")) in expected_names:
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
        # Fall-through: if exactly one page, use it regardless of name — many
        # accounts only manage one Page and we'd rather succeed than be picky.
        if len(pages) == 1:
            page = pages[0]
            token = page.get("access_token", "")
            if token:
                _atomic_env_update({
                    "FACEBOOK_PAGE_TOKEN": token,
                    "FACEBOOK_PAGE_ID": page["id"],
                })
                os.environ["FACEBOOK_PAGE_TOKEN"] = token
                os.environ["FACEBOOK_PAGE_ID"]    = page["id"]
                logger.info(
                    f"[distributor] Facebook page token obtained (only page): {page.get('name')}"
                )
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
    """Refresh YouTube OAuth token using YOUTUBE_CLIENT_ID/SECRET + YOUTUBE_REFRESH_TOKEN.

    Falls back to client_secret.json if the env vars are not set.
    Persists the new token to os.environ and .env so every subsequent call
    in this process (and the next run) uses the fresh token automatically.
    """
    import json
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
    if not refresh_token:
        return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")

    # Prefer env vars; fall back to client_secret.json for legacy compat
    client_id     = os.environ.get("YOUTUBE_CLIENT_ID", "")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        secret_path = PROJECT_DIR / "client_secret.json"
        if secret_path.exists():
            try:
                data   = json.loads(secret_path.read_text())
                creds  = data.get("installed") or data.get("web") or {}
                client_id     = creds.get("client_id", "")
                client_secret = creds.get("client_secret", "")
            except Exception:
                pass

    if not (client_id and client_secret):
        logger.warning("[distributor] YouTube refresh: missing client credentials")
        return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")

    try:
        resp = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id":     client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token",
        }, timeout=15)
        token = resp.json().get("access_token", "")
        if token:
            logger.info("[distributor] YouTube token refreshed")
            os.environ["YOUTUBE_OAUTH_TOKEN"] = token
            _atomic_env_update({"YOUTUBE_OAUTH_TOKEN": token})
            return token
        logger.warning(f"[distributor] YouTube token refresh got no token: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"[distributor] YouTube token refresh failed: {e}")

    return os.environ.get("YOUTUBE_OAUTH_TOKEN", "")


def _ensure_tokens_fresh() -> None:
    """Pre-flight: refresh all platform tokens before a distribution run.

    Called once at the top of distribute_clip() — idempotent and fast because
    refresh_instagram_token() caches the result in os.environ and skips the
    network call if the token is still valid (and because the FB page token
    derived from a long-lived user token does not expire).

    This ensures that even if the process has been running for hours, the
    tokens used for every clip are fresh rather than relying on lazy refresh
    inside each native posting function.
    """
    # Instagram / Facebook — one refresh covers both (FB page token is derived
    # from the same long-lived user token)
    ig_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    if ig_token and not _INSTAGRAM_TOKEN_DEAD:
        new_ig = refresh_instagram_token(ig_token)
        if new_ig and new_ig != ig_token:
            # Re-derive FB page token from the refreshed user token
            page_id = os.environ.get("FACEBOOK_PAGE_ID", "")
            if page_id and not _INSTAGRAM_TOKEN_DEAD:
                get_facebook_page_token(new_ig, page_id)

    # YouTube — always refresh since access tokens expire in ~1h
    _refresh_youtube_token()


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

        # 2. Wait for FINISHED (up to 3 minutes — Reels can take > 2 min to transcode)
        finished = False
        for _ in range(36):
            time.sleep(5)
            status_resp = requests.get(
                f"{INSTAGRAM_GRAPH_BASE}/{creation_id}",
                params={"fields": "status_code,error_message", "access_token": access_token},
                timeout=15,
            )
            status_data = status_resp.json()
            ig_status = status_data.get("status_code", "")
            if ig_status == "FINISHED":
                finished = True
                break
            if ig_status == "ERROR":
                err = status_data.get("error_message", "unknown processing error")
                logger.error(f"Instagram Reel container ERROR: {err}")
                return {"success": False, "platform": "instagram", "error": f"container ERROR: {err}"}

        if not finished:
            logger.error(
                f"[distributor] Instagram Reel container {creation_id} timed out before FINISHED — "
                "not publishing to avoid storing an invalid media ID"
            )
            return {"success": False, "platform": "instagram", "error": "container timed out before FINISHED"}

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


# Platforms that have a Buffer channel connected. Every one of these is a
# viable fallback target when the native Graph/YouTube/TikTok API fails or
# credentials are missing. Facebook was added once a FB Buffer channel was
# wired up; Stories are supported via Buffer's story metadata surface.
_BUFFER_CHANNELS = {
    "tiktok", "instagram", "youtube", "facebook",
    "instagram_story", "facebook_story",
}

# Auth-related HTTP status codes and Meta/Google error codes that indicate an
# expired or revoked token — as opposed to a transient platform error.
# Auth errors should NOT silently fall back to Buffer; they should surface
# loudly so the operator knows re-auth is needed.
_AUTH_ERROR_CODES = {190, 102, 200, 463, 467}  # Meta OAuth codes
_AUTH_HTTP_STATUS = {401, 403}


def _is_auth_error(result: dict) -> bool:
    """Return True if the native API failure is an authentication error.

    Auth errors (expired/revoked token, wrong credentials) should NOT route
    to Buffer — they must be fixed at the source.  Transient platform errors
    (5xx, network timeouts, rate limits) are legitimate fallback triggers.
    """
    error_str = str(result.get("error", "")).lower()
    auth_phrases = (
        "oauth", "access token", "invalid token", "token expired",
        "cannot parse access token", "missing permission",
    )
    if any(p in error_str for p in auth_phrases):
        return True
    # Check numeric error code embedded in error string (e.g. "code 190")
    for code in _AUTH_ERROR_CODES:
        if str(code) in error_str:
            return True
    return False


def _log_native_failure(platform: str, result: dict) -> None:
    """Log a native API failure with the correct severity."""
    error = result.get("error", "unknown error")
    if _is_auth_error(result):
        logger.critical(
            f"[distributor] {platform} AUTH ERROR — re-auth needed: {error}\n"
            f"  Run: python3 rjm.py auth {platform.split('_')[0]}  to refresh tokens."
        )
    else:
        logger.warning(f"[distributor] {platform} native failed (platform error) — Buffer fallback: {error}")


def _buffer_fallback(clip: dict, scheduled_at: str = "") -> dict:
    """Post a clip to exactly ONE Buffer channel.

    Historically this called ``buffer_poster.upload_video_and_queue()``, which
    fanned out to TikTok + Instagram Reel + Instagram Story + YouTube on every
    call — so any single native API failure caused three duplicate posts on
    the other platforms. Now we target only the platform the caller specified.

    Stories use Buffer's story metadata (instagram.type=story /
    facebook.type=story) via ``_create_video_story_post``. Reels/feed posts go
    through ``_create_video_post``.
    """
    platform = clip["platform"]
    if platform not in _BUFFER_CHANNELS:
        return {
            "success": False,
            "platform": platform,
            "error": f"no Buffer channel for {platform}",
            "via": "buffer_fallback",
        }

    try:
        import buffer_poster
        # Stories should upload the vertical-story variant if the caller
        # provided one; Reels use the main path.
        if platform.endswith("_story"):
            source = clip.get("story_path") or clip.get("path")
        else:
            source = clip.get("path")
        if not source:
            return {
                "success": False,
                "platform": platform,
                "error": "no video path",
                "via": "buffer_fallback",
            }

        video_url = buffer_poster.upload_video(source)
        caption   = clip.get("caption", "")

        if platform == "instagram_story":
            post_id = buffer_poster._create_video_story_post(
                "instagram", video_url, scheduled_at=scheduled_at or None,
            )
        elif platform == "facebook_story":
            post_id = buffer_poster._create_video_story_post(
                "facebook", video_url, scheduled_at=scheduled_at or None,
            )
        elif platform == "youtube":
            title = clip.get("track_title", "RJM") + " | Holy Rave #shorts"
            post_id = buffer_poster._create_video_post(
                "youtube", video_url, caption,
                title=title, description=caption,
                scheduled_at=scheduled_at or None,
            )
        else:
            # tiktok, instagram, facebook — feed post with caption
            post_id = buffer_poster._create_video_post(
                platform, video_url, caption,
                scheduled_at=scheduled_at or None,
            )

        # Count against Buffer's daily cap (parity with upload_video_and_queue).
        try:
            import db as _db
            _db.init_db()
            _db.increment_content_count()
        except Exception:
            pass

        return {
            "success":  True,
            "post_id":  post_id,
            "platform": platform,
            "via":      "buffer_fallback",
        }
    except Exception as e:
        return {
            "success":  False,
            "platform": platform,
            "error":    f"Buffer fallback failed: {e}",
            "via":      "buffer_fallback",
        }


def distribute_clip(clip: dict) -> dict:
    """
    Distribute a single clip to its target platform on its scheduled time slot.
    Tries native API first, falls back to Buffer on failure or missing credentials.
    clip dict keys: {platform, path, caption, caption_by_platform, hook_text, track_title, clip_index, variant, ...}

    If ``caption_by_platform`` is present, the per-platform caption replaces the
    generic ``caption`` for native + Buffer calls. This lets TikTok go casual,
    YouTube go SEO-friendly, and Stories stay short without one caption string
    compromising across six surfaces.
    """
    platform    = clip["platform"]
    path        = clip["path"]
    caption_map = clip.get("caption_by_platform") or {}
    caption     = caption_map.get(platform) or clip.get("caption", "")
    clip = {**clip, "caption": caption}  # ensure downstream Buffer call sees it
    clip_index  = clip.get("clip_index", 0)
    scheduled_at = _scheduled_at_utc(platform, clip_index)

    # Pre-flight: refresh all platform tokens before attempting native APIs.
    # This runs once per clip call but is idempotent — refresh_instagram_token()
    # and _refresh_youtube_token() both cache in os.environ so subsequent clips
    # in the same process pay only the cost of an env lookup.
    # TikTok is excluded — it routes through Buffer only (no native OAuth).
    if platform not in ("tiktok",):
        _ensure_tokens_fresh()

    logger.info(f"[distributor] {platform} clip {clip_index} → scheduled {scheduled_at}")

    # Native-post dedup: skip platforms already posted natively today, even if
    # the main _posts.json registry was deleted to force a re-run. This is what
    # prevents double-posts when the user clears Buffer and re-runs the pipeline.
    _native_already_posted = _load_native_registry()
    if (platform, clip_index) in _native_already_posted:
        logger.warning(
            f"[distributor] DEDUP: {platform} clip {clip_index} was already posted "
            f"natively today — skipping to prevent double-post"
        )
        return {
            "success": True,
            "post_id": "dedup_skipped",
            "platform": platform,
            "clip_index": clip_index,
            "variant": clip.get("variant"),
            "via": "native_dedup",
        }

    if platform == "instagram":
        if _instagram_native_available():
            ig_user_id   = os.environ.get("INSTAGRAM_USER_ID", "")
            access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
            result = post_instagram_reel(path, caption, ig_user_id, access_token)
            if result.get("success"):
                _record_native_post(platform, clip_index, result.get("post_id", ""))
            else:
                _log_native_failure("instagram", result)
                if not _is_auth_error(result):
                    result = _buffer_fallback(clip, scheduled_at)
        else:
            logger.critical(
                "[distributor] Instagram native unavailable after pre-flight — "
                "check INSTAGRAM_USER_ID / INSTAGRAM_ACCESS_TOKEN in .env"
            )
            result = {"success": False, "platform": "instagram",
                      "error": "native unavailable after pre-flight", "via": "native"}

    elif platform == "facebook":
        page_token = os.environ.get("FACEBOOK_PAGE_TOKEN", "")
        page_id    = os.environ.get("FACEBOOK_PAGE_ID", "")
        if page_token and page_id:
            result = post_facebook_reel(path, caption, page_id, page_token)
            if result.get("success"):
                _record_native_post(platform, clip_index, result.get("post_id", ""))
            else:
                _log_native_failure("facebook", result)
                if not _is_auth_error(result):
                    result = _buffer_fallback(clip, scheduled_at)
        else:
            logger.critical(
                "[distributor] Facebook native unavailable after pre-flight — "
                "check FACEBOOK_PAGE_TOKEN / FACEBOOK_PAGE_ID in .env"
            )
            result = {"success": False, "platform": "facebook",
                      "error": "native unavailable after pre-fight", "via": "native"}

    elif platform == "youtube":
        api_key     = os.environ.get("YOUTUBE_API_KEY", "")
        oauth_token = os.environ.get("YOUTUBE_OAUTH_TOKEN", "")
        title       = f"{clip.get('track_title', 'RJM')} | Holy Rave #shorts"
        if api_key and oauth_token:
            result = post_youtube_short(path, title, caption, api_key, oauth_token,
                                        publish_at=scheduled_at)
            if result.get("success"):
                _record_native_post(platform, clip_index, result.get("post_id", ""))
            else:
                _log_native_failure("youtube", result)
                if not _is_auth_error(result):
                    result = _buffer_fallback(clip, scheduled_at)
        else:
            logger.critical(
                "[distributor] YouTube native unavailable after pre-flight — "
                "check YOUTUBE_API_KEY / YOUTUBE_OAUTH_TOKEN / YOUTUBE_REFRESH_TOKEN in .env"
            )
            result = {"success": False, "platform": "youtube",
                      "error": "native unavailable after pre-flight", "via": "native"}

    elif platform == "tiktok":
        # TikTok routes through Buffer only — no native OAuth flow configured.
        result = _buffer_fallback(clip, scheduled_at)

    elif platform == "instagram_story":
        spotify_url  = clip.get("spotify_url", "")
        if _instagram_native_available():
            story_path   = clip.get("story_path", clip.get("path", ""))
            ig_user_id   = os.environ.get("INSTAGRAM_USER_ID", "")
            access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
            result = post_instagram_story(
                story_path, caption, ig_user_id, access_token, spotify_url,
            )
            if result.get("success"):
                _record_native_post(platform, clip_index, result.get("post_id", ""))
            else:
                _log_native_failure("instagram_story", result)
                if not _is_auth_error(result):
                    result = _buffer_fallback(clip, scheduled_at)
        else:
            logger.critical(
                "[distributor] Instagram Story native unavailable after pre-flight — "
                "check INSTAGRAM_USER_ID / INSTAGRAM_ACCESS_TOKEN in .env"
            )
            result = {"success": False, "platform": "instagram_story",
                      "error": "native unavailable after pre-flight", "via": "native"}

    elif platform == "facebook_story":
        page_id    = os.environ.get("FACEBOOK_PAGE_ID", "")
        page_token = os.environ.get("FACEBOOK_PAGE_TOKEN", "")
        if page_id and page_token:
            story_path = clip.get("story_path", clip.get("path", ""))
            result = post_facebook_story(story_path, page_id, page_token)
            if result.get("success"):
                _record_native_post(platform, clip_index, result.get("post_id", ""))
            else:
                _log_native_failure("facebook_story", result)
                if not _is_auth_error(result):
                    result = _buffer_fallback(clip, scheduled_at)
        else:
            logger.critical(
                "[distributor] Facebook Story native unavailable after pre-flight — "
                "check FACEBOOK_PAGE_TOKEN / FACEBOOK_PAGE_ID in .env"
            )
            result = {"success": False, "platform": "facebook_story",
                      "error": "native unavailable after pre-flight", "via": "native"}

    else:
        result = {"success": False, "platform": platform, "error": f"Unknown platform: {platform}"}

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

        # 2. Poll for FINISHED (up to 3 minutes)
        finished = False
        for _ in range(36):
            time.sleep(5)
            status_resp = requests.get(
                f"{INSTAGRAM_GRAPH_BASE}/{container_id}",
                params={"fields": "status_code", "access_token": access_token},
                timeout=15,
            )
            status = status_resp.json().get("status_code", "")
            if status == "FINISHED":
                finished = True
                break
            if status == "ERROR":
                return {"success": False, "platform": "instagram_story",
                        "error": "container processing error"}

        if not finished:
            logger.error(
                f"[distributor] Instagram Story container {container_id} timed out before FINISHED — "
                "not publishing to avoid storing an invalid media ID"
            )
            return {"success": False, "platform": "instagram_story", "error": "container timed out before FINISHED"}

        # 3. Publish
        pub_resp = requests.post(
            f"{INSTAGRAM_GRAPH_BASE}/{ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": access_token},
            timeout=30,
        )
        if pub_resp.status_code != 200:
            return {"success": False, "platform": "instagram_story",
                    "error": pub_resp.json().get("error", {}).get("message", pub_resp.text[:200])}
        pub_data = pub_resp.json()
        media_id = pub_data.get("id", "")
        if not media_id or media_id == container_id:
            logger.error(
                f"[distributor] Instagram Story media_publish returned container_id as media_id — "
                "post ID is not queryable; treating as failure"
            )
            return {"success": False, "platform": "instagram_story",
                    "error": "media_publish did not return a distinct media ID"}
        return {"success": True, "platform": "instagram_story", "post_id": media_id}

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

    Delays between attempts: 2s, 8s, 32s. ``distribute_clip`` already routes
    failing native calls to Buffer inline, so most transient failures resolve
    on the first attempt. The retry here covers genuine network blips and
    Cloudinary upload hiccups — NOT misconfiguration, which the inline Buffer
    fallback has already absorbed.
    """
    delays = [2, 8, 32]
    result: dict = {}

    for attempt in range(max_retries):
        result = _distribute_single(clip, target)
        if result.get("success"):
            return result
        # Don't retry platform-misconfig errors — they won't change on retry.
        err = str(result.get("error", "")).lower()
        permanent = any(s in err for s in (
            "no buffer channel", "unknown platform", "credentials missing",
            "no video path", "is locked", "channel not found",
        ))
        if permanent:
            break
        if attempt < max_retries - 1:
            logger.warning(
                f"[distributor] Retry {attempt + 1}/{max_retries} for {target}: "
                f"{result.get('error', '')}"
            )
            time.sleep(delays[attempt])

    # Final fallback to Buffer for ANY target that still failed and has a
    # Buffer channel. Stories are now Buffer-compatible, so this safely covers
    # every configured distribution target.
    if (
        not result.get("success")
        and result.get("via") != "buffer_fallback"
        and target in _BUFFER_CHANNELS
    ):
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
