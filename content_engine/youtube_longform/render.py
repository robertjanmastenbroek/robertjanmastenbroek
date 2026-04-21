"""
render.py — Still image + audio → MP4 via Cloudinary (Shotstack fallback).

CRITICAL CONSTRAINT: No ffmpeg, PyAV, OpenCV, or MoviePy anywhere. Any
local encoder pins CPU. See memory: feedback_no_ffmpeg.md.

The composite is trivial: hold one image for the duration of the audio
track, at 1920x1080 H.264 + AAC 48kHz. Both providers do this natively.

Primary: Cloudinary (free tier, 25 credits/month ≈ 60+ 6-min renders).
Fallback: Shotstack PAYG ($0.40/min ≈ $2.40 per 6-min render).

Selection logic:
  - If CLOUDINARY_* creds configured → Cloudinary
  - Else if SHOTSTACK_API_KEY set   → Shotstack
  - Else                             → RenderError
"""
from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform.types import RenderSpec, RenderedVideo

logger = logging.getLogger(__name__)


class RenderError(Exception):
    """Raised when no render backend can satisfy a request."""


# ─── Backend selection ───────────────────────────────────────────────────────

def _backend() -> str:
    if all((cfg.CLOUDINARY_CLOUD_NAME, cfg.CLOUDINARY_API_KEY, cfg.CLOUDINARY_API_SECRET)):
        return "cloudinary"
    if cfg.SHOTSTACK_API_KEY:
        return "shotstack"
    raise RenderError(
        "No render backend configured. Set either CLOUDINARY_* "
        "or SHOTSTACK_API_KEY in .env. See "
        "content_engine/youtube_longform/config.py."
    )


# ─── Cloudinary backend ──────────────────────────────────────────────────────

def _upload_to_cloudinary(
    file_path: Path,
    resource_type: str = "image",
    public_id: Optional[str] = None,
) -> str:
    """Upload a local file to Cloudinary and return its secure_url."""
    try:
        import cloudinary  # type: ignore
        import cloudinary.uploader  # type: ignore
    except ImportError as e:
        raise RenderError(
            "cloudinary package not installed. Add to requirements: "
            "`pip install cloudinary>=1.40.0`"
        ) from e

    cloudinary.config(
        cloud_name=cfg.CLOUDINARY_CLOUD_NAME,
        api_key=cfg.CLOUDINARY_API_KEY,
        api_secret=cfg.CLOUDINARY_API_SECRET,
        secure=True,
    )

    logger.info("Cloudinary upload | %s | %s", resource_type, file_path.name)
    upload_kwargs = {
        "resource_type": resource_type,
        "folder":        "holy_rave/youtube_longform",
    }
    if public_id:
        upload_kwargs["public_id"] = public_id
        upload_kwargs["overwrite"] = True

    result = cloudinary.uploader.upload(str(file_path), **upload_kwargs)
    return result["secure_url"]


def _render_cloudinary(spec: RenderSpec) -> RenderedVideo:
    """
    Cloudinary image+audio → video via URL transformation.

    Flow:
      1. Upload audio as `video` resource_type (Cloudinary's "video"
         category accepts audio-only files too).
      2. Upload image as `image` resource_type.
      3. Build a transformation URL that overlays the audio on a
         duration-stretched image.
      4. Download the rendered MP4 and return.
    """
    try:
        import cloudinary  # type: ignore
        import cloudinary.utils  # type: ignore
    except ImportError as e:
        raise RenderError("cloudinary package missing") from e

    cloudinary.config(
        cloud_name=cfg.CLOUDINARY_CLOUD_NAME,
        api_key=cfg.CLOUDINARY_API_KEY,
        api_secret=cfg.CLOUDINARY_API_SECRET,
        secure=True,
    )

    # Generate a composite video URL: image held for audio.duration,
    # with audio overlaid. Cloudinary's transformation chain:
    #   l_video:<audio_public_id>    → overlay the audio
    #   du_<seconds>                 → set duration to track length
    #   c_fill,w_1920,h_1080         → ensure 1080p canvas
    #   q_auto:good                  → quality
    #   f_mp4                        → output format
    #
    # Because Cloudinary assumes raw-URL transformations work best with
    # uploaded assets, this function uploads both inputs first (if given
    # as local paths) and then builds the URL. If inputs are already
    # publicly reachable URLs, it uploads them remotely (Cloudinary
    # supports fetch-by-URL).
    public_audio_id = f"{spec.output_label}_audio"
    public_image_id = f"{spec.output_label}_hero"

    # Upload (either remote-fetch via secure_url, or from-URL upload)
    cloudinary.uploader.upload(  # type: ignore[attr-defined]
        spec.audio_url,
        resource_type="video",
        public_id=public_audio_id,
        folder="holy_rave/youtube_longform",
        overwrite=True,
    )
    cloudinary.uploader.upload(  # type: ignore[attr-defined]
        spec.hero_image_url,
        resource_type="image",
        public_id=public_image_id,
        folder="holy_rave/youtube_longform",
        overwrite=True,
    )

    audio_full_id = f"holy_rave:youtube_longform:{public_audio_id}"
    image_full_id = f"holy_rave/youtube_longform/{public_image_id}"

    transformation = [
        {"width": cfg.VIDEO_WIDTH, "height": cfg.VIDEO_HEIGHT, "crop": "fill"},
        {"duration": spec.duration_seconds},
        {"overlay": f"video:{audio_full_id.replace(':', ':')}"},
        {"flags": "layer_apply"},
        {"quality": "auto:good"},
        {"fetch_format": "mp4"},
    ]
    video_url, _opts = cloudinary.utils.cloudinary_url(  # type: ignore[attr-defined]
        image_full_id,
        resource_type="image",
        format="mp4",
        transformation=transformation,
    )

    # Download rendered MP4
    local_path = cfg.VIDEO_DIR / f"{spec.output_label}.mp4"
    local_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Cloudinary render URL: %s", video_url)
    t0 = time.time()
    with requests.get(video_url, stream=True, timeout=cfg.SHOTSTACK_TIMEOUT) as r:
        r.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    logger.info("Cloudinary render downloaded in %.1fs: %s", time.time() - t0, local_path)

    return RenderedVideo(
        local_path=local_path,
        remote_url=video_url,
        width=cfg.VIDEO_WIDTH,
        height=cfg.VIDEO_HEIGHT,
        duration=spec.duration_seconds,
        codec=cfg.VIDEO_CODEC,
        audio_codec=cfg.AUDIO_CODEC,
    )


# ─── Shotstack backend ───────────────────────────────────────────────────────

def _render_shotstack(spec: RenderSpec) -> RenderedVideo:
    """
    Shotstack JSON timeline render. Uses PAYG endpoint; polls for completion.
    """
    base_url = f"https://api.shotstack.io/{cfg.SHOTSTACK_ENV}"
    headers = {
        "x-api-key":    cfg.SHOTSTACK_API_KEY,
        "Content-Type": "application/json",
    }

    timeline = {
        "timeline": {
            "soundtrack": {"src": spec.audio_url, "effect": "fadeInFadeOut"},
            "tracks": [{
                "clips": [{
                    "asset":   {"type": "image", "src": spec.hero_image_url},
                    "start":   0,
                    "length":  spec.duration_seconds,
                    "fit":     "cover",
                }],
            }],
        },
        "output": {
            "format":     "mp4",
            "resolution": "fhd",   # 1920x1080
            "fps":        cfg.VIDEO_FPS,
        },
    }

    logger.info("Shotstack queue render | %s", spec.output_label)
    r = requests.post(f"{base_url}/render", headers=headers, json=timeline, timeout=60)
    r.raise_for_status()
    job_id = r.json()["response"]["id"]
    logger.info("Shotstack job id: %s", job_id)

    # Poll until done
    deadline = time.time() + cfg.SHOTSTACK_TIMEOUT
    poll_interval = 5
    rendered_url = None
    while time.time() < deadline:
        time.sleep(poll_interval)
        s = requests.get(f"{base_url}/render/{job_id}", headers=headers, timeout=30)
        s.raise_for_status()
        status = s.json()["response"]["status"]
        logger.info("Shotstack status: %s", status)
        if status == "done":
            rendered_url = s.json()["response"]["url"]
            break
        if status == "failed":
            raise RenderError(f"Shotstack render failed: {s.json()!r}")
    if not rendered_url:
        raise RenderError(f"Shotstack render timed out after {cfg.SHOTSTACK_TIMEOUT}s")

    local_path = cfg.VIDEO_DIR / f"{spec.output_label}.mp4"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(rendered_url, stream=True, timeout=cfg.SHOTSTACK_TIMEOUT) as resp:
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    return RenderedVideo(
        local_path=local_path,
        remote_url=rendered_url,
        width=cfg.VIDEO_WIDTH,
        height=cfg.VIDEO_HEIGHT,
        duration=spec.duration_seconds,
        codec=cfg.VIDEO_CODEC,
        audio_codec=cfg.AUDIO_CODEC,
    )


# ─── Public API ──────────────────────────────────────────────────────────────

def composite(spec: RenderSpec) -> RenderedVideo:
    """Route to the first available backend."""
    backend = _backend()
    logger.info("Render backend selected: %s", backend)
    if backend == "cloudinary":
        return _render_cloudinary(spec)
    return _render_shotstack(spec)


def upload_audio_for_render(local_audio: Path, public_id: str) -> str:
    """
    Cloudinary-only helper: upload a local audio file and return a
    public URL suitable for use as RenderSpec.audio_url.
    """
    return _upload_to_cloudinary(local_audio, resource_type="video", public_id=public_id)


def upload_image_for_render(local_image: Path, public_id: str) -> str:
    """Cloudinary-only helper: upload a local image and return a public URL."""
    return _upload_to_cloudinary(local_image, resource_type="image", public_id=public_id)
