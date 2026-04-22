"""
render.py — Still image + audio → MP4 via Shotstack.

CRITICAL CONSTRAINT: No ffmpeg, PyAV, OpenCV, or MoviePy anywhere. Any
local encoder pins CPU. All composition happens off-machine via
cloud-render API.

The composite is trivial: hold one image for the duration of the audio
track, at 1920×1080 H.264 + AAC 48kHz.

Architecture:
  - Cloudinary is used ONLY for hosting/public-URL access of the inputs
    (audio file + hero image). It cannot natively composite image+audio
    into video — verified against Cloudinary docs 2026-04-21.
  - Shotstack does the actual render from those URLs.
    PAYG cost: $0.40/min → ~$2.40 per 6-minute render.
    Cheaper alternatives (JSON2Video at ~$0.025/min) are a TODO and
    would slot in as an additional backend without changing the
    _upload_*_for_render helpers.

Selection logic:
  - If SHOTSTACK_API_KEY is set → Shotstack (the only working render path)
  - Else                        → RenderError with actionable message

Cloudinary is still required as input-hosting layer. Set either
CLOUDINARY_URL or the three split vars.
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
    """
    The only working render backend is Shotstack. Cloudinary is required
    for hosting inputs but cannot produce the composite MP4 (verified
    against Cloudinary docs 2026-04-21 — no image+audio→video transform).
    """
    if cfg.SHOTSTACK_API_KEY and cfg.cloudinary_configured():
        return "shotstack"
    missing = []
    if not cfg.SHOTSTACK_API_KEY:
        missing.append("SHOTSTACK_API_KEY (sign up at shotstack.io/pricing)")
    if not cfg.cloudinary_configured():
        missing.append("CLOUDINARY_URL (or CLOUDINARY_CLOUD_NAME/API_KEY/API_SECRET)")
    raise RenderError(
        "Render backend not fully configured. Missing: "
        + ", ".join(missing)
        + ". Cloudinary hosts the inputs (audio + hero); Shotstack composites the MP4."
    )


def _configure_cloudinary() -> None:
    """Call cloudinary.config with whichever credential format is present."""
    import cloudinary  # type: ignore
    if cfg.CLOUDINARY_URL:
        # SDK auto-reads CLOUDINARY_URL from env; explicit call to be safe
        cloudinary.config(secure=True)
    else:
        cloudinary.config(
            cloud_name=cfg.CLOUDINARY_CLOUD_NAME,
            api_key=cfg.CLOUDINARY_API_KEY,
            api_secret=cfg.CLOUDINARY_API_SECRET,
            secure=True,
        )


# ─── Cloudinary backend ──────────────────────────────────────────────────────

def _upload_to_cloudinary(
    file_path: Path,
    resource_type: str = "image",
    public_id: Optional[str] = None,
) -> str:
    """
    Upload a local file to Cloudinary and return its secure_url.

    Automatically switches to chunked `upload_large` when the file exceeds
    Cloudinary's single-shot upload limit (100MB free tier, 300MB+ paid).
    This is mandatory for full-track WAV uploads — a 10-minute 44.1kHz
    stereo 16-bit file is ~100MB and trips the single-shot limit with a
    413 Request Entity Too Large. upload_large streams in 20MB chunks and
    has no practical size ceiling.

    Confirmed 2026-04-22 Halleluyah publish: 10:23 WAV = 104MB failed
    via `upload()` with 413, succeeded via `upload_large()`.
    """
    try:
        import cloudinary  # type: ignore
        import cloudinary.uploader  # type: ignore
    except ImportError as e:
        raise RenderError(
            "cloudinary package not installed. Add to requirements: "
            "`pip install cloudinary>=1.40.0`"
        ) from e

    _configure_cloudinary()

    size_bytes = file_path.stat().st_size
    # 90MB cutoff — stays well under Cloudinary's 100MB single-shot limit
    # with margin for multipart overhead. Anything above this goes chunked.
    LARGE_FILE_THRESHOLD = 90 * 1024 * 1024

    logger.info(
        "Cloudinary upload | %s | %s | %.1f MB | %s",
        resource_type, file_path.name, size_bytes / (1024 * 1024),
        "chunked (upload_large)" if size_bytes > LARGE_FILE_THRESHOLD else "single-shot",
    )
    upload_kwargs = {
        "resource_type": resource_type,
        "folder":        "holy_rave/youtube_longform",
    }
    if public_id:
        upload_kwargs["public_id"] = public_id
        upload_kwargs["overwrite"] = True

    if size_bytes > LARGE_FILE_THRESHOLD:
        # Chunked upload — Cloudinary streams the file in parts. chunk_size
        # defaults to 20MB; we explicitly set it for clarity + logging.
        upload_kwargs["chunk_size"] = 20 * 1024 * 1024
        result = cloudinary.uploader.upload_large(str(file_path), **upload_kwargs)
    else:
        result = cloudinary.uploader.upload(str(file_path), **upload_kwargs)
    return result["secure_url"]


def _render_cloudinary(spec: RenderSpec) -> RenderedVideo:
    """
    NOT IMPLEMENTED. Cloudinary does not natively support image+audio→video
    compositing as a delivery transformation (verified against Cloudinary
    video_manipulation_and_delivery docs, 2026-04-21).

    Cloudinary DOES work for:
      - Hosting inputs (audio as resource_type=video, image as image)
      - Generating the public URLs that Shotstack consumes

    This function is preserved as a stub to document the attempt and to
    avoid reintroducing the broken path. Use composite() which routes to
    _render_shotstack.
    """
    raise RenderError(
        "Cloudinary does not support image+audio→video compositing. "
        "Use Shotstack (SHOTSTACK_API_KEY) or JSON2Video (not yet wired)."
    )
    # -- Archived former implementation (broken; do not resurrect as-is) --
    try:
        import cloudinary  # type: ignore
        import cloudinary.utils  # type: ignore
    except ImportError as e:
        raise RenderError("cloudinary package missing") from e

    _configure_cloudinary()

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
    # Shotstack URL pattern (verified from dashboard 2026-04-21):
    #   https://api.shotstack.io/edit/stage/render   (SANDBOX / free tier)
    #   https://api.shotstack.io/edit/v1/render      (PRODUCTION / PAYG)
    base_url = f"https://api.shotstack.io/edit/{cfg.SHOTSTACK_ENV}"
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
            # Shotstack resolution enum (verified 2026-04-21):
            #   "preview" "mobile" "sd" "hd" "1080" "4k"
            # "1080" maps to 1920x1080. Do NOT use "fhd" — rejected by API.
            "resolution": "1080",
            "fps":        cfg.VIDEO_FPS,
        },
    }

    logger.info("Shotstack queue render | %s", spec.output_label)
    logger.debug("Shotstack payload: %s", json.dumps(timeline)[:500])
    r = requests.post(f"{base_url}/render", headers=headers, json=timeline, timeout=60)
    if not r.ok:
        raise RenderError(
            f"Shotstack {r.status_code} {r.reason}: {r.text[:800]}\n"
            f"Payload sent: {json.dumps(timeline)[:400]}"
        )
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
    """Route to the first available backend. Currently only Shotstack works."""
    backend = _backend()
    logger.info("Render backend selected: %s", backend)
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
