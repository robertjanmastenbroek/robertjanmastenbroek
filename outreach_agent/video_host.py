#!/usr/bin/env python3.13
"""
video_host.py — Upload a local video to a stable public URL for Buffer/Meta/TikTok.

Host priority:
  1. Cloudinary (primary) — free tier, CDN-delivered, URLs never expire
  2. uguu.se              — last resort only (48h expiry — unreliable for Buffer)

Setup (one-time, free):
  1. Create account at https://cloudinary.com (free tier: 25 GB/month — plenty)
  2. Go to Dashboard → copy "API Environment variable"
  3. Add to your Railway project env vars:
       CLOUDINARY_URL=cloudinary://API_KEY:API_SECRET@CLOUD_NAME

Usage:
  from video_host import upload_video, cleanup_old_cloudinary_uploads
  url = upload_video("/path/to/clip.mp4")
  cleanup_old_cloudinary_uploads(max_age_days=7)  # call once per daily run
"""

import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests


# ─── Public API ──────────────────────────────────────────────────────────────

def upload_video(filepath: str) -> str:
    """Upload a local video file and return a stable public HTTPS URL.

    Priority: Cloudinary (stable, CDN) → uguu.se (48h — last resort only).
    Raises FileNotFoundError if the file doesn't exist.
    Raises RuntimeError if all hosts fail.
    """
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"Video file not found: {filepath}")

    errors = []

    # 1. Cloudinary — primary, stable, CDN-delivered, free tier 25GB/month
    if os.environ.get("CLOUDINARY_URL"):
        try:
            url = _upload_to_cloudinary(str(p))
            print(f"    → [Cloudinary] {url}")
            return url
        except Exception as exc:
            errors.append(f"Cloudinary: {exc}")
            print(f"    ⚠ Cloudinary failed: {exc} — falling back to uguu.se…")
    else:
        print("    ⚠ CLOUDINARY_URL not set — add it to Railway env vars (see video_host.py header)")

    # 2. uguu.se — last resort (48h expiry — Buffer may fail if post is scheduled far out)
    try:
        url = _upload_to_uguu(str(p))
        print(f"    → [uguu.se — 48h ONLY, unreliable] {url}")
        return url
    except Exception as exc:
        errors.append(f"uguu.se: {exc}")

    raise RuntimeError(
        f"All video hosts failed: {'; '.join(errors)}\n"
        "Fix: add CLOUDINARY_URL to your Railway environment variables."
    )


def cleanup_old_cloudinary_uploads(max_age_days: int = 7) -> int:
    """Delete Cloudinary videos in the holy-rave/ folder older than max_age_days.

    Safe to call on every daily run — silently skips if Cloudinary not configured.
    Returns the number of files deleted.
    """
    if not os.environ.get("CLOUDINARY_URL"):
        return 0

    try:
        import cloudinary
        import cloudinary.api

        cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp())

        # List resources in the holy-rave folder
        result   = cloudinary.api.resources(
            resource_type="video",
            type="upload",
            prefix="holy-rave/",
            max_results=500,
        )
        resources = result.get("resources", [])

        deleted = 0
        for r in resources:
            created = r.get("created_at", "")
            try:
                created_ts = int(datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp())
            except Exception:
                continue
            if created_ts < cutoff_ts:
                try:
                    cloudinary.api.delete_resources([r["public_id"]], resource_type="video")
                    deleted += 1
                    print(f"    [cleanup] Deleted: {r['public_id']}")
                except Exception as exc:
                    print(f"    [cleanup] Could not delete {r['public_id']}: {exc}")

        if deleted:
            print(f"  [Cloudinary] Cleaned up {deleted} video(s) older than {max_age_days} days")
        return deleted

    except Exception as exc:
        print(f"  ⚠ Cloudinary cleanup failed (non-fatal): {exc}")
        return 0


# ─── Hosts ───────────────────────────────────────────────────────────────────

def _upload_to_cloudinary(filepath: str) -> str:
    """Upload via Cloudinary SDK. Requires CLOUDINARY_URL env var."""
    import cloudinary
    import cloudinary.uploader

    result = cloudinary.uploader.upload(
        filepath,
        resource_type="video",
        folder="holy-rave",
    )
    url = result.get("secure_url")
    if not url:
        raise RuntimeError(f"Cloudinary returned no URL: {result}")
    return url


def _upload_to_uguu(filepath: str) -> str:
    """Upload to uguu.se (anonymous, 48h expiry — last resort only)."""
    p = Path(filepath)
    file_size_mb = p.stat().st_size / 1_000_000
    print(f"    Uploading {p.name} ({file_size_mb:.1f} MB) to uguu.se…")

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
    return data["files"][0]["url"]
