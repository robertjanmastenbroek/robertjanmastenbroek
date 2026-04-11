#!/usr/bin/env python3.13
"""
video_host.py — Upload a local video to a public URL for Buffer.

Tries hosts in priority order:
  1. Cloudinary  (if CLOUDINARY_URL env var is set)
  2. Catbox.moe  (free, permanent, no account needed)
  3. uguu.se     (free, 48h expiry, last resort)

Usage:
  from video_host import upload_video
  url = upload_video("/path/to/clip.mp4")
"""

import os
from pathlib import Path

import requests


def upload_video(filepath: str) -> str:
    """Upload a local video file and return a public HTTPS URL.

    Tries Cloudinary → Catbox.moe → uguu.se in order.
    Raises FileNotFoundError if the file doesn't exist.
    Raises RuntimeError("All video hosts failed: ...") if all hosts fail.
    """
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"Video file not found: {filepath}")

    errors = []

    # 1. Cloudinary (optional — only if credentials configured)
    if os.environ.get("CLOUDINARY_URL"):
        try:
            url = _upload_to_cloudinary(str(p))
            print(f"    → [Cloudinary] {url}")
            return url
        except Exception as exc:
            errors.append(f"Cloudinary: {exc}")
            print(f"    ⚠ Cloudinary failed: {exc} — trying Catbox.moe…")

    # 2. Catbox.moe (free, no account, permanent URLs)
    try:
        url = _upload_to_catbox(str(p))
        print(f"    → [Catbox.moe] {url}")
        return url
    except Exception as exc:
        errors.append(f"catbox.moe: {exc}")
        print(f"    ⚠ Catbox.moe failed: {exc} — trying uguu.se…")

    # 3. uguu.se (last resort, 48h expiry)
    try:
        url = _upload_to_uguu(str(p))
        print(f"    → [uguu.se] {url}")
        return url
    except Exception as exc:
        errors.append(f"uguu.se: {exc}")

    raise RuntimeError(f"All video hosts failed: {'; '.join(errors)}")


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


def _upload_to_catbox(filepath: str) -> str:
    """Upload to catbox.moe (anonymous, permanent, 200 MB limit)."""
    p = Path(filepath)
    file_size_mb = p.stat().st_size / 1_000_000
    print(f"    Uploading {p.name} ({file_size_mb:.1f} MB) to catbox.moe…")

    with p.open("rb") as fh:
        resp = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload", "userhash": ""},
            files={"fileToUpload": (p.name, fh, "video/mp4")},
            timeout=300,
        )
    resp.raise_for_status()
    url = resp.text.strip()
    if not url.startswith("https://"):
        raise RuntimeError(f"catbox.moe returned unexpected response: {url!r}")
    return url


def _upload_to_uguu(filepath: str) -> str:
    """Upload to uguu.se (anonymous, 48h expiry, fallback only)."""
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
