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
    reference_urls: Optional[list[str]] = None,
) -> str:
    """
    Run one fal.ai generation. Returns the URL of the resulting image.

    Three paths, in priority order:

    1. Reference-conditioned (when reference_urls is non-empty):
       `fal-ai/flux-2-pro/edit` — accepts image_urls for style anchoring.
       Output cost same as flux-2-pro; inputs charge $0.015/MP.

    2. LoRA (when FAL_BRAND_LORA_URL is set):
       `fal-ai/flux-lora` — Flux 1 with LoRA, accepts loras array, negative
       prompt, and inference params.

    3. Baseline (default):
       `fal-ai/flux-2-pro` — text-only. Does NOT accept num_inference_steps,
       guidance_scale, negative_prompt, or loras. Negatives are merged into
       the positive prompt.

    Reference conditioning wins over LoRA when both are available — references
    are more flexible (per-generation style anchoring) than a baked-in LoRA.
    """
    client = _fal_client()

    if reference_urls:
        # Reference-conditioned path (fal-ai/flux-2-pro/edit).
        #
        # IMPORTANT #1: skip the "Avoid: X, Y, Z" negative-prompt merge on
        # this endpoint. The content checker scans the full prompt text and
        # flags religious/drug terms even when they appear inside an Avoid
        # clause (Catholic saints, ayahuasca imagery, DMT fractals, Buddha
        # statues, etc. are all negatives for us but trigger the filter).
        #
        # IMPORTANT #2: many reference thumbnails from proven-viral channels
        # (Astrix, Omiki, Indian Spirit, Ozora) have artist/festival LOGOS
        # and TEXT burned into them. Without explicit instruction, Flux 2
        # Pro Edit bleeds that text into our generated output. We append a
        # strong "ignore text in references, no text in output" clause to
        # every ref-conditioned prompt. This is observably effective.
        endpoint = cfg.FAL_FLUX_2_PRO_EDIT_EP
        anti_text_clause = (
            " Clean image with absolutely no text, no words, no typography, "
            "no logos, no watermarks, no festival names, no artist names, "
            "no captions, no signage. Ignore any text or logos that appear in "
            "the reference images — do not reproduce them. Output a pure "
            "photographic image of the subject and scene only."
        )
        arguments = {
            "prompt":           prompt + anti_text_clause,
            # Use only 1 reference per generation — multiple refs compound
            # text-bleed risk and muddy the composition. Single ref gives
            # cleaner style anchor.
            "image_urls":       reference_urls[:1],
            "image_size":       {"width": width, "height": height},
            "output_format":    "jpeg",
            "safety_tolerance": "4",
        }
    elif cfg.FAL_BRAND_LORA_URL:
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

def generate_hero(
    prompt: TrackPrompt,
    use_references: bool = True,
) -> ImageAsset:
    """
    Generate the 1920x1080 hero still used as the video background.

    When use_references=True (default) and the proven-viral pool has
    images available for the track's genre_family, this function:
      1. Picks N references via reference_pool.pick_references
      2. Uploads them to Cloudinary to get public URLs
      3. Routes to fal-ai/flux-2-pro/edit with image_urls
    Otherwise falls back to plain fal-ai/flux-2-pro text-to-image.

    Pass use_references=False for a baseline generation (useful for
    quality A/B — reference-conditioned vs not).
    """
    cfg.ensure_workspace()
    slug = _slug(prompt.track_title)

    # Reference resolution — if the pool is empty, we silently fall back
    reference_urls: list[str] = []
    if use_references:
        from content_engine.youtube_longform import reference_pool
        from content_engine.youtube_longform.render import upload_image_for_render

        refs = reference_pool.pick_references(prompt.genre_family)
        if refs:
            logger.info(
                "Reference-conditioning hero with %d refs from %s bucket",
                len(refs), prompt.genre_family,
            )
            # Upload each reference to Cloudinary for a public URL
            # (fal.ai requires URLs, not local paths)
            for ref_path in refs:
                try:
                    ref_url = upload_image_for_render(
                        ref_path,
                        public_id=f"ref_{ref_path.stem}",
                    )
                    reference_urls.append(ref_url)
                except Exception as e:
                    logger.warning(
                        "Skipping reference %s (Cloudinary upload failed: %s)",
                        ref_path.name, e,
                    )
        else:
            logger.info("No references available for %s — baseline path", prompt.genre_family)

    # Hash includes references so the cache key differs for ref vs no-ref
    digest = _hash(prompt.flux_prompt + "".join(reference_urls), prompt.seed)
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
        reference_urls=reference_urls or None,
    )
    _download(url, local_path)
    logger.info(
        "Hero generated in %.1fs (%s refs) → %s",
        time.time() - t0, len(reference_urls), local_path.name,
    )

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
    use_references: bool = True,
) -> list[ImageAsset]:
    """Generate 3 thumbnail variants (1280x720) for YouTube Test & Compare.

    When use_references=True (default), each variant samples fresh references
    from the proven-viral pool — this means the 3 thumbnails will not only
    use different seeds but also be anchored on different reference
    composition-archetypes, producing genuinely distinct A/B candidates.
    """
    cfg.ensure_workspace()
    target_count = count or cfg.THUMB_VARIANT_COUNT
    assets: list[ImageAsset] = []

    # Resolve per-variant references once per call to share Cloudinary uploads
    reference_urls_per_variant: list[list[str]] = []
    if use_references and variants:
        from content_engine.youtube_longform import reference_pool
        from content_engine.youtube_longform.render import upload_image_for_render
        for i, v in enumerate(variants[:target_count]):
            # Use variant index as seed into reference_pool so each variant
            # gets a deterministic-but-different reference set
            refs = reference_pool.pick_references(
                v.genre_family,
                seed=(v.seed or 0) + i * 101,
            )
            urls = []
            for ref_path in refs:
                try:
                    urls.append(upload_image_for_render(
                        ref_path, public_id=f"ref_{ref_path.stem}",
                    ))
                except Exception as e:
                    logger.warning("Skipping ref %s: %s", ref_path.name, e)
            reference_urls_per_variant.append(urls)
    else:
        reference_urls_per_variant = [[] for _ in range(len(variants[:target_count]))]

    for i, v in enumerate(variants[:target_count]):
        slug = _slug(v.track_title)
        ref_urls = reference_urls_per_variant[i] if i < len(reference_urls_per_variant) else []
        digest = _hash(v.flux_prompt + "".join(ref_urls), v.seed)
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
            reference_urls=ref_urls or None,
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
