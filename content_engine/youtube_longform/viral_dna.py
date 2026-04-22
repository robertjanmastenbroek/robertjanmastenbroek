"""
viral_dna.py — Loader for the distilled viral-thumbnail style guides.

Reads the JSON artifacts produced by scripts/extract_viral_dna.py and
surfaces the `prompt_preamble` field — a 120-180 word distillation of
the compositional DNA extracted from the top-N highest-viewed
thumbnails in each genre bucket.

Usage (from motion._generate_keyframe or similar):

    from content_engine.youtube_longform import viral_dna

    preamble = viral_dna.preamble_for(track_prompt.genre_family)
    if preamble:
        full_prompt = f"{preamble}\n\n{keyframe.still_prompt}"

Design notes:
  - Zero-cost at runtime. Preamble is text — no API calls.
  - Cached per process. Load once on first access, reuse everywhere.
  - Graceful degradation: if the DNA file is missing (extraction never
    run, file got lost, etc.), preamble_for() returns "" and callers
    fall back to their original prompts. No crashes.
  - The DNA content feeds the Flux cache digest (via caller), so
    updating a DNA file invalidates all thumbnails using it — exactly
    what we want when the viral vocabulary shifts and we re-extract.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from content_engine.youtube_longform.types import GenreFamily

logger = logging.getLogger(__name__)

_DNA_DIR = Path(__file__).parent / "viral_dna"

# Process-local cache — loaded lazily on first access.
_DNA_CACHE: dict[str, dict] = {}


def _dna_path(genre_family: GenreFamily) -> Path:
    return _DNA_DIR / f"viral_dna_{genre_family}.json"


def load_dna(genre_family: GenreFamily) -> Optional[dict]:
    """
    Return the parsed viral DNA dict for a genre family, or None if
    the extraction artifact doesn't exist yet.
    """
    if genre_family in _DNA_CACHE:
        return _DNA_CACHE[genre_family]

    path = _dna_path(genre_family)
    if not path.exists():
        logger.debug(
            "No viral DNA artifact for %s at %s — run scripts/extract_viral_dna.py",
            genre_family, path,
        )
        return None

    try:
        data = json.loads(path.read_text())
    except Exception as e:
        logger.warning("Could not parse viral DNA at %s: %s", path, e)
        return None

    _DNA_CACHE[genre_family] = data
    logger.info(
        "Loaded viral DNA for %s (%d images analyzed)",
        genre_family, data.get("_meta", {}).get("n_images", 0),
    )
    return data


def preamble_for(genre_family: GenreFamily) -> str:
    """
    Return the prompt_preamble string for a genre family. Empty string
    if no DNA artifact exists — callers should treat "" as "skip
    preamble, use original prompt verbatim".
    """
    data = load_dna(genre_family)
    if not data:
        return ""
    preamble = data.get("prompt_preamble", "").strip()
    if not preamble:
        logger.warning(
            "Viral DNA for %s has no prompt_preamble field — skipping",
            genre_family,
        )
        return ""
    return preamble


def summary() -> dict:
    """Status snapshot for /rjm content youtube status and tests."""
    status = {}
    for gf in ("organic_house", "tribal_psytrance"):
        path = _dna_path(gf)
        if path.exists():
            data = load_dna(gf)
            if data:
                status[gf] = {
                    "loaded":    True,
                    "n_images":  data.get("_meta", {}).get("n_images", 0),
                    "preamble_chars": len(data.get("prompt_preamble", "")),
                }
                continue
        status[gf] = {"loaded": False, "path": str(path)}
    return status


def clear_cache() -> None:
    """Used by tests and by re-extraction flows to force a reload."""
    global _DNA_CACHE
    _DNA_CACHE = {}
