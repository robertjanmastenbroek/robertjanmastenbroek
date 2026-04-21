"""
reference_pool.py — Pick proven-viral thumbnails to use as Flux 2 Pro
reference images, bucketed by genre family.

Flow:
  1. Background research agent populates content/images/proven_viral/
     with 500 thumbnails split into bucket_130_organic/ and
     bucket_140_psytrance/, plus a manifest.json describing each.
  2. When image_gen generates a hero for a track, it asks this module
     for N references from the correct bucket for that track's
     genre_family.
  3. image_gen uploads those references to Cloudinary (which returns
     public URLs) and passes them to fal-ai/flux-2-pro/edit as
     image_urls. Flux conditions its output on those references.

Selection strategy (kept simple intentionally):
  - Weight higher-view thumbnails higher (weighted random sample)
  - Never pick the same reference twice in consecutive generations
    (stateful across calls within one process; resets at restart)
  - Fall back to uniform random if view-count metadata is missing

No CLIP / no ML — this is pure random sampling from a curated pool.
The CLIP scorer lives separately in quality_score.py (future work).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
from pathlib import Path
from typing import Optional

from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform.types import GenreFamily

logger = logging.getLogger(__name__)


# Per-process memory of recently-used references — avoid picking the same
# two for back-to-back generations. Keyed by genre family.
_recent_refs: dict[GenreFamily, list[str]] = {
    "organic_house":    [],
    "tribal_psytrance": [],
}
_RECENT_WINDOW = 6     # Avoid re-picking within the last N calls per bucket


def _bucket_dir(family: GenreFamily) -> Path:
    """Map genre family → reference bucket directory."""
    if family == "tribal_psytrance":
        return cfg.PROVEN_VIRAL_DIR / "bucket_140_psytrance"
    return cfg.PROVEN_VIRAL_DIR / "bucket_130_organic"


def _load_manifest() -> list[dict]:
    """
    Load content/images/proven_viral/manifest.json as a list of entries.
    Accepts either a raw JSON list OR a dict with "images"/"entries"/"items" key.
    Returns [] if missing or malformed.
    """
    manifest_path = cfg.PROVEN_VIRAL_DIR / "manifest.json"
    if not manifest_path.exists():
        return []
    try:
        data = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Couldn't parse reference manifest: %s", e)
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("images", "entries", "items", "thumbnails"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _view_count_for(filename: str, manifest: list[dict]) -> int:
    """Lookup view count from manifest; fallback to 1 for uniform weighting."""
    for entry in manifest:
        if not isinstance(entry, dict):
            continue
        if entry.get("filename") == filename:
            try:
                return int(entry.get("view_count_estimate", 1))
            except (TypeError, ValueError):
                return 1
    return 1


def available_reference_count(family: GenreFamily) -> int:
    """How many references are on disk for this bucket?"""
    bucket = _bucket_dir(family)
    if not bucket.exists():
        return 0
    return sum(1 for p in bucket.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))


def pick_references(
    family: GenreFamily,
    count: Optional[int] = None,
    seed: Optional[int] = None,
) -> list[Path]:
    """
    Return N reference image paths from the bucket for the given family.

    Uses view-count-weighted random sampling when manifest is present,
    uniform random otherwise. Excludes recently-used refs within the
    process lifetime (to prevent back-to-back repetition).

    Returns an empty list if the bucket is empty or missing — caller
    should then fall back to a non-reference (baseline) generation path.
    """
    target_count = count if count is not None else cfg.REFERENCE_COUNT_PER_GEN
    bucket = _bucket_dir(family)
    if not bucket.exists():
        logger.info("Reference bucket missing: %s (baseline path instead)", bucket)
        return []

    candidates = [
        p for p in bucket.iterdir()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png")
        and not p.name.startswith(".")
    ]
    if not candidates:
        logger.info("Reference bucket empty: %s", bucket)
        return []

    # Exclude recently-used
    recent = set(_recent_refs[family])
    eligible = [p for p in candidates if p.name not in recent]
    if len(eligible) < target_count:
        # Everyone's been used recently → reset memory and use full list
        _recent_refs[family].clear()
        eligible = candidates

    manifest = _load_manifest()
    weights = [_view_count_for(p.name, manifest) for p in eligible]
    # Log-scale weights so a 40M-view outlier doesn't dominate the 1M-view cluster
    import math
    log_weights = [max(1.0, math.log10(max(1, w))) for w in weights]

    rng = random.Random(seed)
    picks: list[Path] = []
    pool = list(zip(eligible, log_weights))
    while len(picks) < target_count and pool:
        # Weighted choice without replacement
        total = sum(w for _, w in pool)
        r = rng.uniform(0, total)
        acc = 0.0
        for i, (p, w) in enumerate(pool):
            acc += w
            if r <= acc:
                picks.append(p)
                pool.pop(i)
                break

    # Record picks in recent buffer
    for p in picks:
        _recent_refs[family].append(p.name)
    while len(_recent_refs[family]) > _RECENT_WINDOW:
        _recent_refs[family].pop(0)

    logger.info(
        "Reference pool | family=%s | picked %d/%d | recent=%d",
        family, len(picks), target_count, len(_recent_refs[family]),
    )
    return picks


def summary() -> dict:
    """Status snapshot for `rjm.py content youtube status`."""
    return {
        "pool_130_organic_count":  available_reference_count("organic_house"),
        "pool_140_psytrance_count": available_reference_count("tribal_psytrance"),
        "references_per_generation": cfg.REFERENCE_COUNT_PER_GEN,
        "manifest_present":        (cfg.PROVEN_VIRAL_DIR / "manifest.json").exists(),
    }
