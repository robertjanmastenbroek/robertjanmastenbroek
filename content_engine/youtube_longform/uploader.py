"""
uploader.py — YouTube Data API v3 long-form uploader.

Builds on the existing YouTube OAuth setup (scripts/setup_youtube_oauth.py
+ content_engine/distributor.py:521 _refresh_youtube_token). Which channel
the upload targets is determined by which channel was selected during the
OAuth consent flow — one refresh token authorizes one channel. To upload
to a different channel, rerun setup_youtube_oauth.py while that channel
is the active one in the Google account switcher.

Responsibilities:
  1. Refresh access token.
  2. Resumable upload of the MP4 via videos.insert.
  3. thumbnails.set with the primary variant.
  4. Optional: submit the 3 variants to YouTube Test & Compare.
  5. Optional: playlistItems.insert to add to a BPM-tier playlist.

Quota accounting (default 10k/day):
  videos.insert           = 1600 units
  thumbnails.set          =   50 units × up to 3 variants
  playlistItems.insert    =   50 units
  --------------------------
  Typical full upload     ~ 1800 units  (≈ 5.5 uploads/day with default quota)
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Optional

import requests

from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform.types import UploadSpec

logger = logging.getLogger(__name__)


class UploadError(Exception):
    """Raised when the YouTube upload fails irrecoverably."""


# ─── OAuth token refresh ─────────────────────────────────────────────────────

def _refresh_access_token() -> str:
    """
    Refresh the short-lived access token using the long-lived refresh token.
    Mirrors content_engine/distributor.py:521 so the same creds work
    across Shorts and long-form pipelines.
    """
    if not cfg.YT_REFRESH_TOKEN:
        raise UploadError(
            "YOUTUBE_REFRESH_TOKEN not set. Run scripts/setup_youtube_oauth.py "
            "and ensure the new channel is authorized."
        )
    if not (cfg.YT_CLIENT_ID and cfg.YT_CLIENT_SECRET):
        raise UploadError(
            "YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET missing in .env."
        )

    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id":     cfg.YT_CLIENT_ID,
            "client_secret": cfg.YT_CLIENT_SECRET,
            "refresh_token": cfg.YT_REFRESH_TOKEN,
            "grant_type":    "refresh_token",
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise UploadError(f"Token refresh failed: {r.status_code} {r.text[:400]}")
    token = r.json().get("access_token", "")
    if not token:
        raise UploadError(f"Token response missing access_token: {r.text[:400]}")
    return token


# ─── Resumable upload (videos.insert) ────────────────────────────────────────

_RETRIABLE = {500, 502, 503, 504}


def _resumable_upload(
    access_token: str,
    file_path: Path,
    metadata: dict[str, Any],
) -> str:
    """
    Perform a resumable upload per YouTube's protocol. Returns video ID.
    """
    # 1) Initiate
    init_url = (
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status"
    )
    init_headers = {
        "Authorization":          f"Bearer {access_token}",
        "Content-Type":           "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(file_path.stat().st_size),
        "X-Upload-Content-Type":  "video/mp4",
    }
    r = requests.post(init_url, headers=init_headers, data=json.dumps(metadata), timeout=30)
    if r.status_code != 200:
        raise UploadError(f"videos.insert initiate failed: {r.status_code} {r.text[:500]}")
    upload_session_url = r.headers.get("Location")
    if not upload_session_url:
        raise UploadError("videos.insert missing Location header — cannot resume")

    # 2) Upload in chunks
    total = file_path.stat().st_size
    chunk_size = cfg.YT_CHUNK_SIZE_BYTES
    offset = 0
    retries = 0

    with open(file_path, "rb") as fh:
        while offset < total:
            fh.seek(offset)
            chunk = fh.read(chunk_size)
            end = offset + len(chunk) - 1
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {offset}-{end}/{total}",
                "Content-Type":   "video/mp4",
            }
            try:
                resp = requests.put(upload_session_url, headers=headers, data=chunk, timeout=600)
            except requests.exceptions.RequestException as e:
                if retries >= cfg.YT_MAX_RETRIES:
                    raise UploadError(f"Upload aborted after {retries} retries: {e}")
                wait = min(2 ** retries + random.random(), 64)
                logger.warning("Network hiccup; retry %d in %.1fs: %s", retries, wait, e)
                time.sleep(wait)
                retries += 1
                continue

            if resp.status_code in (200, 201):
                body = resp.json()
                return body["id"]
            if resp.status_code == 308:
                # Continue; server reports range accepted in Range header
                rng = resp.headers.get("Range", "")
                # "bytes=0-N" → N+1 is next offset
                try:
                    last = int(rng.split("-")[-1])
                    offset = last + 1
                except Exception:
                    offset = end + 1
                logger.info("Chunk accepted; progress %.1f%%", 100 * offset / total)
                retries = 0
                continue
            if resp.status_code in _RETRIABLE:
                if retries >= cfg.YT_MAX_RETRIES:
                    raise UploadError(f"Retries exhausted: {resp.status_code} {resp.text[:400]}")
                wait = min(2 ** retries + random.random(), 64)
                logger.warning("5xx %d; retry %d in %.1fs", resp.status_code, retries, wait)
                time.sleep(wait)
                retries += 1
                continue
            raise UploadError(f"Upload failed: {resp.status_code} {resp.text[:500]}")

    raise UploadError("Unreachable — upload loop exited without success response")


# ─── Thumbnail ──────────────────────────────────────────────────────────────

def _set_thumbnail(access_token: str, video_id: str, thumb_path: Path) -> None:
    url = f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "image/jpeg",
    }
    with open(thumb_path, "rb") as f:
        data = f.read()
    r = requests.post(url, headers=headers, data=data, timeout=60)
    if r.status_code not in (200, 201):
        raise UploadError(f"thumbnails.set failed: {r.status_code} {r.text[:400]}")
    logger.info("Thumbnail set: %s", thumb_path.name)


# ─── Playlist add ────────────────────────────────────────────────────────────

def _add_to_playlist(access_token: str, video_id: str, playlist_id: str) -> None:
    url = "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
    }
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }
    r = requests.post(url, headers=headers, data=json.dumps(body), timeout=30)
    if r.status_code not in (200, 201):
        logger.warning("playlistItems.insert failed %d: %s", r.status_code, r.text[:300])
    else:
        logger.info("Added to playlist: %s", playlist_id)


# ─── Public API ──────────────────────────────────────────────────────────────

def upload(spec: UploadSpec) -> str:
    """
    Execute the full upload flow: insert → thumbnail → playlist.
    Returns the YouTube video ID.
    """
    token = _refresh_access_token()

    # Build videos.insert metadata
    snippet: dict[str, Any] = {
        "title":                spec.title[:100],         # Hard YouTube limit
        "description":          spec.description[:5000],  # Hard YouTube limit
        "tags":                 spec.tags[:30],           # ~500 char total practical cap
        "categoryId":           spec.category_id,
        "defaultLanguage":      spec.language,
        "defaultAudioLanguage": spec.audio_language,
    }
    status: dict[str, Any] = {
        "privacyStatus":              spec.privacy_status,
        "selfDeclaredMadeForKids":    spec.made_for_kids,
        "license":                    spec.license,
        "embeddable":                 spec.embeddable,
        "publicStatsViewable":        spec.public_stats,
    }
    if spec.publish_at_iso and spec.privacy_status == "private":
        status["publishAt"] = spec.publish_at_iso

    metadata = {"snippet": snippet, "status": status}
    # NOTE: YouTube's `onBehalfOfContentOwner` field is reserved for Content
    # ID partner accounts (labels, MCNs). For regular user uploads it returns
    # 403 Forbidden. The target channel is determined by the OAuth refresh
    # token's channel scope, not by any insert-time parameter. spec.channel_id
    # is kept on the spec for logging/dedup but NOT sent in the insert body.

    logger.info("YouTube upload starting: %s (%s)", spec.title, spec.video_path.name)
    video_id = _resumable_upload(token, spec.video_path, metadata)
    logger.info("YouTube upload complete. Video ID: %s", video_id)

    # Thumbnail (primary variant first)
    if spec.thumbnail_paths:
        _set_thumbnail(token, video_id, spec.thumbnail_paths[0])
        # Note: YouTube Test & Compare (A/B) requires videos.thumbnails.test
        # endpoint which is only available to channels with specific quota
        # tier. For now, submit primary only; we'll rotate manually in
        # Studio UI for variants 2 + 3.

    # Playlist
    if spec.playlist_id:
        _add_to_playlist(token, video_id, spec.playlist_id)

    return video_id


def estimate_quota_cost(upload_count: int = 1, thumb_per_upload: int = 1, add_to_playlist: bool = True) -> int:
    """Rough daily quota estimate — for rjm.py content youtube budget."""
    per = cfg.YT_UNITS_INSERT + (cfg.YT_UNITS_THUMBNAIL * thumb_per_upload)
    if add_to_playlist:
        per += cfg.YT_UNITS_PLAYLIST_ADD
    return per * upload_count
