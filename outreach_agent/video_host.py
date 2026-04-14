#!/usr/bin/env python3.13
"""
video_host.py — Upload a local video to a stable public URL for Buffer/Meta/TikTok.

Host priority:
  1. robertjanmastenbroek.com (SFTP) — primary, 7-day TTL, own domain
  2. Cloudinary                       — secondary (set CLOUDINARY_URL env var)
  3. uguu.se                          — last resort (48h expiry, unreliable)

Required env vars for RJM website upload:
  RJM_SSH_HOST        — hostname  (default: robertjanmastenbroek.com)
  RJM_SSH_USER        — SFTP/SSH username (your cPanel or hosting user)
  RJM_SSH_PASSWORD    — SFTP password (set this OR RJM_SSH_KEY_PATH)
  RJM_SSH_KEY_PATH    — path to private key file (default: ~/.ssh/id_rsa)
  RJM_SSH_UPLOAD_PATH — remote directory  (default: /public_html/uploads/)
  RJM_UPLOAD_BASE_URL — public base URL   (default: https://robertjanmastenbroek.com/uploads/)

Cloudinary:
  export CLOUDINARY_URL="cloudinary://API_KEY:API_SECRET@CLOUD_NAME"

Usage:
  from video_host import upload_video, cleanup_old_uploads
  url = upload_video("/path/to/clip.mp4")
  cleanup_old_uploads(max_age_days=7)   # call once per daily run
"""

import os
import time
from pathlib import Path

import requests


# ─── Public API ──────────────────────────────────────────────────────────────

def upload_video(filepath: str) -> str:
    """Upload a local video file and return a stable public HTTPS URL.

    Priority: RJM website (SFTP) → Cloudinary → uguu.se (last resort).
    Raises FileNotFoundError if the file doesn't exist.
    Raises RuntimeError if all hosts fail.
    """
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"Video file not found: {filepath}")

    errors = []

    # 1. robertjanmastenbroek.com via SFTP (primary — stable, own domain, 7-day TTL)
    if _rjm_website_configured():
        try:
            url = _upload_to_rjm_website(str(p))
            print(f"    → [RJM website] {url}")
            return url
        except Exception as exc:
            errors.append(f"RJM website: {exc}")
            print(f"    ⚠ RJM website upload failed: {exc} — trying Cloudinary…")

    # 2. Cloudinary (secondary — requires CLOUDINARY_URL env var)
    if os.environ.get("CLOUDINARY_URL"):
        try:
            url = _upload_to_cloudinary(str(p))
            print(f"    → [Cloudinary] {url}")
            return url
        except Exception as exc:
            errors.append(f"Cloudinary: {exc}")
            print(f"    ⚠ Cloudinary failed: {exc} — trying uguu.se…")
    else:
        print("    ⚠ CLOUDINARY_URL not set — skipping Cloudinary")

    # 3. uguu.se (last resort — 48h expiry, only use if all else fails)
    try:
        url = _upload_to_uguu(str(p))
        print(f"    → [uguu.se — 48h ONLY] {url}")
        return url
    except Exception as exc:
        errors.append(f"uguu.se: {exc}")

    raise RuntimeError(f"All video hosts failed: {'; '.join(errors)}")


def cleanup_old_uploads(max_age_days: int = 7) -> int:
    """Remove videos older than max_age_days from robertjanmastenbroek.com/uploads/.

    Runs silently if RJM SSH credentials are not configured.
    Returns the number of files removed.
    """
    if not _rjm_website_configured():
        return 0

    host        = os.environ.get("RJM_SSH_HOST", "robertjanmastenbroek.com")
    user        = os.environ.get("RJM_SSH_USER", "")
    password    = os.environ.get("RJM_SSH_PASSWORD", "")
    key_path    = os.environ.get("RJM_SSH_KEY_PATH", "~/.ssh/id_rsa")
    upload_path = os.environ.get("RJM_SSH_UPLOAD_PATH", "/public_html/uploads/")

    try:
        import paramiko
    except ImportError:
        print("    ⚠ paramiko not installed — run: pip install paramiko")
        return 0

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {"username": user, "timeout": 30}
        if password:
            connect_kwargs["password"] = password
        else:
            connect_kwargs["key_filename"] = os.path.expanduser(key_path)

        ssh.connect(host, **connect_kwargs)

        removed = 0
        cutoff  = time.time() - (max_age_days * 86_400)

        with ssh.open_sftp() as sftp:
            try:
                entries = sftp.listdir_attr(upload_path)
            except FileNotFoundError:
                ssh.close()
                return 0

            for entry in entries:
                if entry.st_mtime and entry.st_mtime < cutoff:
                    remote = f"{upload_path.rstrip('/')}/{entry.filename}"
                    try:
                        sftp.remove(remote)
                        removed += 1
                        print(f"    [cleanup] Removed: {entry.filename}")
                    except Exception as exc:
                        print(f"    [cleanup] Could not remove {entry.filename}: {exc}")

        ssh.close()
        if removed:
            print(f"  [RJM website] Cleaned up {removed} video(s) older than {max_age_days} days")
        return removed

    except Exception as exc:
        print(f"  ⚠ Cleanup failed (non-fatal): {exc}")
        return 0


# ─── Hosts ───────────────────────────────────────────────────────────────────

def _rjm_website_configured() -> bool:
    """Return True if the minimum RJM SSH config is available."""
    user = os.environ.get("RJM_SSH_USER", "")
    has_key = os.path.exists(os.path.expanduser(
        os.environ.get("RJM_SSH_KEY_PATH", "~/.ssh/id_rsa")
    ))
    has_pass = bool(os.environ.get("RJM_SSH_PASSWORD", ""))
    return bool(user) and (has_key or has_pass)


def _upload_to_rjm_website(filepath: str) -> str:
    """Upload via SFTP to robertjanmastenbroek.com and return a public HTTPS URL.

    The remote file is prefixed with a Unix timestamp to avoid name collisions
    and to allow age-based cleanup.
    """
    try:
        import paramiko
    except ImportError:
        raise RuntimeError("paramiko not installed — run: pip install paramiko")

    host        = os.environ.get("RJM_SSH_HOST", "robertjanmastenbroek.com")
    user        = os.environ.get("RJM_SSH_USER", "")
    password    = os.environ.get("RJM_SSH_PASSWORD", "")
    key_path    = os.environ.get("RJM_SSH_KEY_PATH", "~/.ssh/id_rsa")
    upload_path = os.environ.get("RJM_SSH_UPLOAD_PATH", "/public_html/uploads/")
    base_url    = os.environ.get("RJM_UPLOAD_BASE_URL",
                                 "https://robertjanmastenbroek.com/uploads/")

    if not user:
        raise RuntimeError("RJM_SSH_USER not set")

    p               = Path(filepath)
    remote_filename = f"{int(time.time())}_{p.name}"
    remote_path     = f"{upload_path.rstrip('/')}/{remote_filename}"

    file_size_mb = p.stat().st_size / 1_000_000
    print(f"    Uploading {p.name} ({file_size_mb:.1f} MB) → {host}{remote_path}…")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {"username": user, "timeout": 30}
    if password:
        connect_kwargs["password"] = password
    else:
        connect_kwargs["key_filename"] = os.path.expanduser(key_path)

    ssh.connect(host, **connect_kwargs)

    with ssh.open_sftp() as sftp:
        # Ensure the uploads directory exists
        try:
            sftp.stat(upload_path)
        except FileNotFoundError:
            sftp.mkdir(upload_path)

        sftp.put(str(p), remote_path)

    ssh.close()
    return f"{base_url.rstrip('/')}/{remote_filename}"


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
