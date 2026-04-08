"""
Buffer publisher — schedules posts across TikTok, Instagram, YouTube via Buffer API.

Environment:
  BUFFER_ACCESS_TOKEN  — Buffer OAuth access token

Buffer API base: https://api.bufferapp.com/1/
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BUFFER_BASE = "https://api.bufferapp.com/1"
MAX_RETRIES = 3

# Cache last validation result to avoid repeated /profiles.json calls
_token_validated: bool = False


def _token() -> str:
    """Return the Buffer access token or raise a clear error."""
    token = os.environ.get("BUFFER_ACCESS_TOKEN", "").strip()
    if not token:
        raise EnvironmentError(
            "BUFFER_ACCESS_TOKEN is not set.\n"
            "Export it before running:\n"
            "  export BUFFER_ACCESS_TOKEN=<your_token>\n"
            "Get it from: https://buffer.com/developers/api\n"
            "To get a proper OAuth token: python3 buffer_auth.py"
        )
    return token


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


def validate_token() -> bool:
    """
    Validate the Buffer token with a lightweight /profiles.json call.
    Returns True if valid, False + logs clear error if invalid.
    Caches the result so subsequent calls are free.
    """
    global _token_validated
    if _token_validated:
        return True
    try:
        resp = requests.get(
            f"{BUFFER_BASE}/profiles.json",
            headers=_headers(),
            timeout=10,
        )
        if resp.status_code == 200:
            _token_validated = True
            logger.info("Buffer token validated OK")
            return True
        elif resp.status_code == 401:
            body = resp.text[:200]
            if 'OIDC' in body or 'oidc' in body:
                logger.error(
                    "Buffer token rejected: OIDC tokens are not accepted by the v1 API.\n"
                    "Run: python3 buffer_auth.py  (to get a proper OAuth token)"
                )
            else:
                logger.error(f"Buffer token invalid (401): {body}")
            return False
        else:
            logger.warning(f"Buffer token check returned {resp.status_code} — proceeding anyway")
            return True
    except Exception as e:
        logger.warning(f"Buffer token validation failed ({e}) — proceeding")
        return True  # Don't block on network errors


def _request_with_backoff(method: str, url: str, **kwargs) -> requests.Response:
    """
    Make an HTTP request with exponential back-off on 429 / 5xx.
    Raises on final failure.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        resp = getattr(requests, method)(url, **kwargs)
        if resp.status_code == 429:
            wait = 2 ** attempt
            logger.warning(f"Rate limited by Buffer (429) — waiting {wait}s before retry {attempt}/{MAX_RETRIES}")
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            wait = 2 ** attempt
            logger.warning(f"Buffer server error {resp.status_code} — waiting {wait}s before retry {attempt}/{MAX_RETRIES}")
            time.sleep(wait)
            continue
        return resp
    # Final attempt — let it raise naturally if still bad
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_profiles() -> list[dict]:
    """
    GET /profiles.json
    Returns list of connected profiles with id, service, service_username, timezone.
    """
    url = f"{BUFFER_BASE}/profiles.json"
    resp = _request_with_backoff("get", url, headers=_headers())
    resp.raise_for_status()
    profiles = resp.json()
    logger.info(f"Buffer: found {len(profiles)} connected profile(s)")
    for p in profiles:
        logger.debug(
            f"  [{p.get('service', '?')}] @{p.get('service_username', '?')}  id={p.get('id', '?')}"
        )
    return profiles


def _try_media_upload(video_path: str) -> Optional[str]:
    """
    Attempt to pre-upload a video file via Buffer's /media/upload.json endpoint.
    Returns the media key/URL on success, None if the endpoint is unavailable.

    Buffer's media upload (some API versions) accepts multipart upload and returns
    a media key that can be referenced in /updates/create.json.
    """
    url = f"{BUFFER_BASE}/media/upload.json"
    try:
        with open(video_path, "rb") as fh:
            resp = _request_with_backoff(
                "post", url,
                headers=_headers(),
                files={"file": (Path(video_path).name, fh, "video/mp4")},
            )
        if resp.status_code == 200:
            data = resp.json()
            media_key = data.get("media_key") or data.get("id") or data.get("url")
            if media_key:
                logger.debug(f"Buffer media pre-upload succeeded: {media_key}")
                return str(media_key)
        logger.debug(f"Buffer media pre-upload not available (status {resp.status_code})")
    except Exception as exc:
        logger.debug(f"Buffer media pre-upload skipped: {exc}")
    return None


def upload_video(
    video_path: str,
    caption: str,
    profile_ids: list[str],
    scheduled_at: str = None,
    youtube_profile_ids: list[str] = None,
) -> dict:
    """
    Create a Buffer update with video attachment.

    Buffer video upload flow:
    1. POST /updates/create.json with media[video] as multipart
       OR pre-upload via /media/upload.json then reference by key.
    2. Returns {profile_id: update_id, ...}

    scheduled_at: ISO 8601 string e.g. "2026-04-11T19:00:00+01:00"
                  If None, adds to queue (next available slot).
    youtube_profile_ids: if provided, these IDs get youtube_category_id=10 (Music)
                         and youtube_privacy=public appended.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    url = f"{BUFFER_BASE}/updates/create.json"
    yt_ids = set(youtube_profile_ids or [])

    # Try pre-upload path first (works on newer Buffer API tiers)
    media_key = _try_media_upload(video_path)

    if media_key:
        form_fields = [("text", caption), ("shorten", "false")]
        for pid in profile_ids:
            form_fields.append(("profile_ids[]", pid))
        form_fields.append(("media[video]", media_key))
        if scheduled_at:
            form_fields.append(("scheduled_at", scheduled_at))
        if yt_ids:
            form_fields += [("youtube_category_id", "10"), ("youtube_privacy", "public")]

        resp = _request_with_backoff("post", url, headers=_headers(), data=form_fields)
    else:
        # Direct multipart binary upload
        form_fields = [("text", caption), ("shorten", "false")]
        for pid in profile_ids:
            form_fields.append(("profile_ids[]", pid))
        if scheduled_at:
            form_fields.append(("scheduled_at", scheduled_at))
        if yt_ids:
            form_fields += [("youtube_category_id", "10"), ("youtube_privacy", "public")]

        with open(video_path, "rb") as fh:
            resp = _request_with_backoff(
                "post", url,
                headers=_headers(),
                data=form_fields,
                files={"media[video]": (Path(video_path).name, fh, "video/mp4")},
            )

    resp.raise_for_status()
    result = resp.json()

    # Buffer returns {"updates": [...]} or a single update dict
    updates = result.get("updates", [result] if "id" in result else [])
    update_map: dict[str, str] = {}
    for update in updates:
        pid = update.get("profile_id", "unknown")
        uid = update.get("id", "unknown")
        update_map[pid] = uid

    # Log successes with profile names (best-effort — profile name may not be in response)
    for pid, uid in update_map.items():
        logger.info(f"Buffer video queued — profile_id={pid}  update_id={uid}  file={Path(video_path).name}")

    return update_map


def upload_image(
    image_path: str,
    caption: str,
    profile_ids: list[str],
    scheduled_at: str = None,
) -> dict:
    """
    Create a Buffer update with an image attachment (carousel slide or cover).
    Returns {profile_id: update_id, ...}.
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    url = f"{BUFFER_BASE}/updates/create.json"
    ext = Path(image_path).suffix.lower()
    mime = "image/jpeg" if ext in {".jpg", ".jpeg"} else "image/png"

    form_fields = [("text", caption), ("shorten", "false")]
    for pid in profile_ids:
        form_fields.append(("profile_ids[]", pid))
    if scheduled_at:
        form_fields.append(("scheduled_at", scheduled_at))

    with open(image_path, "rb") as fh:
        resp = _request_with_backoff(
            "post", url,
            headers=_headers(),
            data=form_fields,
            files={"media[photo]": (Path(image_path).name, fh, mime)},
        )

    resp.raise_for_status()
    result = resp.json()

    updates = result.get("updates", [result] if "id" in result else [])
    update_map: dict[str, str] = {}
    for update in updates:
        pid = update.get("profile_id", "unknown")
        uid = update.get("id", "unknown")
        update_map[pid] = uid

    for pid, uid in update_map.items():
        logger.info(f"Buffer image queued — profile_id={pid}  update_id={uid}  file={Path(image_path).name}")

    return update_map


def upload_story(
    video_path: str,
    profile_ids: list[str],
    scheduled_at: str = None,
) -> dict:
    """
    Post a video as an Instagram Story via Buffer.

    Buffer supports IG Stories via the same /updates/create.json endpoint
    with instagram_story=true. Requires Instagram profile with Stories enabled.
    Returns {profile_id: update_id, ...}.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    url = f"{BUFFER_BASE}/updates/create.json"
    form_fields = [
        ("text", ""),           # Stories have no caption
        ("shorten", "false"),
        ("instagram_story", "true"),
    ]
    for pid in profile_ids:
        form_fields.append(("profile_ids[]", pid))
    if scheduled_at:
        form_fields.append(("scheduled_at", scheduled_at))

    with open(video_path, "rb") as fh:
        resp = _request_with_backoff(
            "post", url,
            headers=_headers(),
            data=form_fields,
            files={"media[video]": (Path(video_path).name, fh, "video/mp4")},
        )

    resp.raise_for_status()
    result = resp.json()
    updates = result.get("updates", [result] if "id" in result else [])
    update_map: dict[str, str] = {}
    for update in updates:
        pid = update.get("profile_id", "unknown")
        uid = update.get("id", "unknown")
        update_map[pid] = uid
        logger.info(f"Buffer story queued — profile_id={pid}  update_id={uid}  file={Path(video_path).name}")
    return update_map


def upload_carousel(
    image_paths: list[str],
    caption: str,
    profile_ids: list[str],
    scheduled_at: str = None,
) -> dict:
    """
    Post a multi-image carousel to Instagram/TikTok via Buffer.

    Sends multiple media[photo][] fields in a single update.
    Returns {profile_id: update_id, ...}.
    """
    if not image_paths:
        raise ValueError("image_paths must not be empty")

    url = f"{BUFFER_BASE}/updates/create.json"
    form_fields = [("text", caption), ("shorten", "false")]
    for pid in profile_ids:
        form_fields.append(("profile_ids[]", pid))
    if scheduled_at:
        form_fields.append(("scheduled_at", scheduled_at))

    files = []
    file_handles = []
    try:
        for img_path in image_paths[:10]:  # Buffer cap: 10 images per carousel
            if not os.path.isfile(img_path):
                logger.warning(f"Carousel image missing, skipping: {img_path}")
                continue
            ext = Path(img_path).suffix.lower()
            mime = "image/jpeg" if ext in {".jpg", ".jpeg"} else "image/png"
            fh = open(img_path, "rb")
            file_handles.append(fh)
            files.append(("media[photo][]", (Path(img_path).name, fh, mime)))

        if not files:
            raise ValueError("No valid image files for carousel")

        resp = _request_with_backoff(
            "post", url,
            headers=_headers(),
            data=form_fields,
            files=files,
        )
    finally:
        for fh in file_handles:
            fh.close()

    resp.raise_for_status()
    result = resp.json()
    updates = result.get("updates", [result] if "id" in result else [])
    update_map: dict[str, str] = {}
    for update in updates:
        pid = update.get("profile_id", "unknown")
        uid = update.get("id", "unknown")
        update_map[pid] = uid
        logger.info(f"Buffer carousel queued — profile_id={pid}  update_id={uid}  slides={len(files)}")
    return update_map


def get_pending_updates(profile_id: str) -> list[dict]:
    """
    GET /profiles/{id}/updates/pending.json
    Returns list of pending/scheduled updates for logging and verification.
    """
    url = f"{BUFFER_BASE}/profiles/{profile_id}/updates/pending.json"
    resp = _request_with_backoff("get", url, headers=_headers())
    resp.raise_for_status()
    data = resp.json()
    updates = data.get("updates", [])
    logger.info(f"Buffer pending updates for profile {profile_id}: {len(updates)}")
    return updates


def fetch_analytics(profile_id: str, date_preset: str = "week") -> list[dict]:
    """
    Fetch sent post analytics for a profile.

    GET /profiles/{id}/updates/sent.json?count=100
    Returns list of sent updates with statistics.

    Each update includes:
      text, statistics.clicks, statistics.reach, statistics.likes,
      statistics.comments, statistics.shares, created_at

    date_preset: 'week' | 'month' | 'year' (used to filter client-side since
                 Buffer v1 doesn't filter by date on the sent endpoint).
    """
    url = f"{BUFFER_BASE}/profiles/{profile_id}/updates/sent.json"
    try:
        resp = _request_with_backoff("get", url, headers=_headers(),
                                     params={"count": 100, "page": 1})
        resp.raise_for_status()
        data = resp.json()
        updates = data.get("updates", [])
        logger.info(f"Buffer analytics: {len(updates)} sent posts for profile {profile_id}")
        return updates
    except Exception as exc:
        logger.warning(f"Analytics fetch failed for profile {profile_id}: {exc}")
        return []


def get_analytics_summary(profiles: list[dict]) -> list[dict]:
    """
    Fetch analytics for all connected profiles and return a flat list of
    post records with normalised fields:
    {
      profile_id, service, text, views, likes, shares, comments,
      engagement_score, created_at, update_id
    }
    Sorted by engagement_score descending so best performers are first.
    """
    records = []
    for p in profiles:
        service = (p.get("service") or "").lower()
        pid = p.get("id", "")
        if service not in {"tiktok", "instagram", "youtube"} or not pid:
            continue
        updates = fetch_analytics(pid)
        for u in updates:
            stats = u.get("statistics") or u.get("statistics_subset") or {}
            views    = int(stats.get("reach", 0) or stats.get("impressions", 0) or 0)
            likes    = int(stats.get("likes", 0) or stats.get("favorites", 0) or 0)
            shares   = int(stats.get("shares", 0) or stats.get("retweets", 0) or 0)
            comments = int(stats.get("comments", 0) or 0)
            # Engagement score: weighted formula (same as hook_database.record_performance)
            score = (likes * 2 + shares * 5 + comments * 3) / max(views, 1) * 1000
            records.append({
                "profile_id":       pid,
                "service":          service,
                "text":             u.get("text", ""),
                "views":            views,
                "likes":            likes,
                "shares":           shares,
                "comments":         comments,
                "engagement_score": round(score, 4),
                "created_at":       u.get("created_at", ""),
                "update_id":        u.get("id", ""),
            })
    records.sort(key=lambda r: r["engagement_score"], reverse=True)
    return records
