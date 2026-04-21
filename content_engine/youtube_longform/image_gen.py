"""
image_gen.py — Flux 2 Pro on fal.ai with brand-LoRA support.

Produces:
  1x 1920x1080 hero still  (for video background)
  3x 1280x720 thumbnails   (for YouTube A/B Test & Compare)

Uses fal.ai's Flux 2 endpoints:
  - fal-ai/flux-2-pro         (default when no LoRA configured)
  - fal-ai/flux-2/lora        (when FAL_BRAND_LORA_URL is set)

Depends on the `fal-client` Python package. Installed via requirements.txt.

Fallback: if fal.ai returns an error, raises ImageGenError. Caller decides
whether to retry with a different endpoint (Ideogram for text overlays,
GPT Image 1 for full fallback).
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform.types import ImageAsset, TrackPrompt

logger = logging.getLogger(__name__)

# Lazy-imported so the module loads even if fal-client isn't installed yet.
# Actual import happens in _fal_client().


class ImageGenError(Exception):
    """Raised when fal.ai generation fails irrecoverably."""


def _fal_client():
    """Lazy-import fal_client; raise a helpful error if missing."""
    try:
        import fal_client  # type: ignore
    except ImportError as e:
        raise ImageGenError(
            "fal-client package not installed. Add to requirements: "
            "`pip install fal-client>=0.5.0`"
        ) from e
    if not cfg.FAL_KEY:
        raise ImageGenError(
            "FAL_KEY environment variable not set. Create a fal.ai account "
            "at https://fal.ai and put the key in .env as FAL_KEY=..."
        )
    # fal_client reads FAL_KEY from env automatically
    os.environ.setdefault("FAL_KEY", cfg.FAL_KEY)
    return fal_client


# ─── Slug helpers ────────────────────────────────────────────────────────────

def _slug(title: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in title.lower()).strip("_")


def _hash(prompt: str, seed: Optional[int]) -> str:
    """Short digest used in filenames to dedupe prompt variations."""
    h = hashlib.sha256(f"{prompt}::{seed or 0}".encode()).hexdigest()
    return h[:8]


# ─── fal.ai generation primitive ─────────────────────────────────────────────

def _merge_negative_into_prompt(prompt: str, negative: str) -> str:
    """
    Flux 2 Pro has no `negative_prompt` field. We fold negatives into the
    positive prompt as "--no" / "avoid:" clauses. Flux 2 respects these
    as soft guidance.
    """
    if not negative:
        return prompt
    # Trim noise whitespace
    neg = " ".join(w.strip() for w in negative.split() if w.strip())
    return f"{prompt}. Avoid: {neg}"


def _generate_one(
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    seed: Optional[int],
) -> str:
    """
    Run one fal.ai generation. Returns the URL of the resulting image.

    Primary path: `fal-ai/flux-2-pro` — supports ImageSize (width/height) but
    NOT num_inference_steps, guidance_scale, negative_prompt, or loras.
    Negatives are merged into the positive prompt.

    LoRA path (when FAL_BRAND_LORA_URL is set): `fal-ai/flux-lora` — accepts
    width/height, loras array, num_inference_steps, guidance_scale, and
    negative_prompt. Note: this is Flux 1 with LoRA (Flux 2 Dev LoRA
    inference requires a different endpoint + training pipeline; opt in
    only if you've trained specifically for that endpoint).
    """
    client = _fal_client()

    if cfg.FAL_BRAND_LORA_URL:
        endpoint = cfg.FAL_FLUX_LORA_EP
        arguments = {
            "prompt":              prompt,
            "negative_prompt":     negative_prompt,
            "image_size":          {"width": width, "height": height},
            "num_inference_steps": 28,
            "guidance_scale":      3.5,
            "loras": [
                {"path": cfg.FAL_BRAND_LORA_URL, "scale": cfg.FAL_BRAND_LORA_SCALE},
            ],
        }
    else:
        endpoint = cfg.FAL_FLUX_2_PRO_EP
        arguments = {
            "prompt":          _merge_negative_into_prompt(prompt, negative_prompt),
            "image_size":      {"width": width, "height": height},
            "output_format":   "jpeg",
        }

    if seed is not None:
        arguments["seed"] = seed

    logger.info(
        "fal.ai generate | endpoint=%s | %dx%d | seed=%s",
        endpoint, width, height, seed,
    )
    try:
        result = client.subscribe(endpoint, arguments=arguments, with_logs=False)
    except Exception as e:
        raise ImageGenError(f"fal.ai subscribe failed: {e}") from e

    # Both endpoints return { "images": [ { "url": ... } ], ... }
    images = result.get("images") if isinstance(result, dict) else None
    if not images:
        raise ImageGenError(f"fal.ai returned no images. Raw: {result!r}")
    url = images[0].get("url")
    if not url:
        raise ImageGenError(f"fal.ai image entry missing url: {images[0]!r}")
    return url


# ─── Download + persist ──────────────────────────────────────────────────────

def _download(url: str, dest: Path, timeout: int = 60) -> None:
    """Stream-download an image URL to disk."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)


# ─── Public API ──────────────────────────────────────────────────────────────

def generate_hero(prompt: TrackPrompt) -> ImageAsset:
    """Generate the 1920x1080 hero still used as the video background."""
    cfg.ensure_workspace()
    slug = _slug(prompt.track_title)
    digest = _hash(prompt.flux_prompt, prompt.seed)
    local_path = cfg.IMAGE_DIR / f"{slug}_hero_{digest}.jpg"

    if local_path.exists():
        logger.info("Hero image already cached: %s", local_path.name)
        return ImageAsset(
            role="hero",
            local_path=local_path,
            remote_url="",   # Cached — no remote URL tracked
            width=cfg.HERO_WIDTH,
            height=cfg.HERO_HEIGHT,
            prompt_used=prompt.flux_prompt,
            variant_index=0,
        )

    t0 = time.time()
    url = _generate_one(
        prompt=prompt.flux_prompt,
        negative_prompt=prompt.flux_negative,
        width=cfg.HERO_WIDTH,
        height=cfg.HERO_HEIGHT,
        seed=prompt.seed,
    )
    _download(url, local_path)
    logger.info("Hero generated in %.1fs → %s", time.time() - t0, local_path.name)

    return ImageAsset(
        role="hero",
        local_path=local_path,
        remote_url=url,
        width=cfg.HERO_WIDTH,
        height=cfg.HERO_HEIGHT,
        prompt_used=prompt.flux_prompt,
        variant_index=0,
    )


def generate_thumbnails(
    variants: list[TrackPrompt],
    count: Optional[int] = None,
) -> list[ImageAsset]:
    """Generate 3 thumbnail variants (1280x720) for YouTube Test & Compare."""
    cfg.ensure_workspace()
    target_count = count or cfg.THUMB_VARIANT_COUNT
    assets: list[ImageAsset] = []

    for i, v in enumerate(variants[:target_count]):
        slug = _slug(v.track_title)
        digest = _hash(v.flux_prompt, v.seed)
        local_path = cfg.IMAGE_DIR / f"{slug}_thumb_{i}_{digest}.jpg"

        if local_path.exists():
            logger.info("Thumb variant %d already cached: %s", i, local_path.name)
            assets.append(ImageAsset(
                role="thumbnail",
                local_path=local_path,
                remote_url="",
                width=cfg.THUMB_WIDTH,
                height=cfg.THUMB_HEIGHT,
                prompt_used=v.flux_prompt,
                variant_index=i,
            ))
            continue

        t0 = time.time()
        url = _generate_one(
            prompt=v.flux_prompt,
            negative_prompt=v.flux_negative,
            width=cfg.THUMB_WIDTH,
            height=cfg.THUMB_HEIGHT,
            seed=v.seed,
        )
        _download(url, local_path)
        logger.info(
            "Thumb variant %d generated in %.1fs → %s",
            i, time.time() - t0, local_path.name,
        )
        assets.append(ImageAsset(
            role="thumbnail",
            local_path=local_path,
            remote_url=url,
            width=cfg.THUMB_WIDTH,
            height=cfg.THUMB_HEIGHT,
            prompt_used=v.flux_prompt,
            variant_index=i,
        ))
    return assets


def estimate_cost_usd(
    hero_count: int = 1,
    thumb_count: int = 3,
) -> float:
    """Rough cost estimate (fal.ai Flux 2 Pro pricing as of 2026-04-21)."""
    hero_cost = 0.045 * hero_count       # 1920x1080
    thumb_cost = 0.03 * thumb_count      # 1280x720
    return round(hero_cost + thumb_cost, 4)
