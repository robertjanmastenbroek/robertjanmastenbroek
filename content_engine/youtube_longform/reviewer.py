"""
reviewer.py — Pre-publish visual approval gate.

For the first 10–20 publishes the LoRA is still bedding in and you don't
want to publish a broken image to a public channel. This gate:

  1. Runs prompt_builder → image_gen (hero + 3 thumb variants).
  2. Opens the generated images in macOS Preview / cross-platform default.
  3. Writes a summary JSON to data/youtube_longform/review/<track>.json
     so you can re-open the review later.
  4. Prompts the user: approve / regenerate / edit-prompt / abort.
  5. On approve: returns `approved=True` so publisher.publish_track()
     continues past the image step.

Safe to wire into publisher.py once we trust the LoRA — set
SKIP_REVIEW_GATE=1 in .env to bypass.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from content_engine.youtube_longform import config as cfg, image_gen, prompt_builder
from content_engine.youtube_longform.types import ImageAsset, TrackPrompt

logger = logging.getLogger(__name__)

REVIEW_DIR = cfg.REGISTRY_DIR / "review"


@dataclass
class ReviewResult:
    approved:         bool
    track_title:      str
    prompt:           TrackPrompt
    hero_image:       ImageAsset
    thumbnails:       list[ImageAsset]
    notes:            str = ""
    regenerations:    int = 0


# ─── Image viewer (cross-platform) ──────────────────────────────────────────

def _open_image(path: Path) -> None:
    """Open an image in the OS default viewer (macOS: Preview, Linux: xdg-open)."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", str(path)])
    elif sys.platform == "win32":
        subprocess.Popen(["start", str(path)], shell=True)
    else:
        print(f"  (unsupported platform; open manually: {path})")


# ─── Interactive loop ────────────────────────────────────────────────────────

def _show_and_ask(
    track_title: str,
    hero: ImageAsset,
    thumbs: list[ImageAsset],
    iteration: int,
) -> str:
    """Display the generated assets and return the user's decision."""
    print("\n" + "=" * 70)
    print(f"Review iteration {iteration} — {track_title}")
    print("=" * 70)
    print(f"  Hero (1920×1080):  {hero.local_path}")
    for i, t in enumerate(thumbs):
        print(f"  Thumb variant {i}:   {t.local_path}")
    print()

    # Open hero + all thumbnails
    _open_image(hero.local_path)
    for t in thumbs:
        _open_image(t.local_path)

    print("\nOptions:")
    print("  [a] approve — proceed to render + upload")
    print("  [r] regenerate — new Flux seed, same prompt")
    print("  [e] edit — print the prompt so you can tweak and rerun")
    print("  [x] abort — cancel this publish entirely")
    while True:
        reply = input("\nDecision [a/r/e/x]: ").strip().lower()
        if reply in ("a", "r", "e", "x"):
            return reply
        print("  Invalid option. Type a, r, e, or x.")


# ─── Public API ──────────────────────────────────────────────────────────────

def review_track(track_title: str, max_iterations: int = 3) -> ReviewResult:
    """
    Generate hero + thumbnails for a track and prompt the user for approval.
    Iterates up to max_iterations times on regeneration.
    """
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    prompt = prompt_builder.build_prompt(track_title)
    print("\n" + "=" * 70)
    print(f"Pre-publish review: {track_title}")
    print("=" * 70)
    print(prompt_builder.explain(track_title))
    print("\nGenerating assets…")

    hero: Optional[ImageAsset] = None
    thumbs: list[ImageAsset] = []
    notes = ""

    for iteration in range(1, max_iterations + 1):
        # Every retry bumps the seed so we get genuinely different images
        if iteration > 1:
            prompt = prompt_builder.build_prompt(
                track_title,
                seed=(prompt.seed or 0) + iteration * 7919,   # prime offset
            )

        hero = image_gen.generate_hero(prompt)
        thumb_variants = prompt_builder.build_thumbnail_variants(prompt, count=cfg.THUMB_VARIANT_COUNT)
        thumbs = image_gen.generate_thumbnails(thumb_variants)

        decision = _show_and_ask(track_title, hero, thumbs, iteration)

        if decision == "a":
            notes = f"approved on iteration {iteration}"
            result = ReviewResult(
                approved=True,
                track_title=track_title,
                prompt=prompt,
                hero_image=hero,
                thumbnails=thumbs,
                notes=notes,
                regenerations=iteration - 1,
            )
            _write_review_record(result)
            return result

        if decision == "x":
            notes = f"aborted on iteration {iteration}"
            result = ReviewResult(
                approved=False,
                track_title=track_title,
                prompt=prompt,
                hero_image=hero,
                thumbnails=thumbs,
                notes=notes,
                regenerations=iteration - 1,
            )
            _write_review_record(result)
            return result

        if decision == "e":
            print("\nCurrent Flux prompt (edit this in prompt_builder.py for permanent changes):\n")
            print(prompt.flux_prompt)
            print("\nNegative prompt:")
            print(prompt.flux_negative)
            print("\nAborting this run — rerun after editing.")
            result = ReviewResult(
                approved=False,
                track_title=track_title,
                prompt=prompt,
                hero_image=hero,
                thumbnails=thumbs,
                notes=f"prompt-edit requested on iteration {iteration}",
                regenerations=iteration - 1,
            )
            _write_review_record(result)
            return result

        # decision == "r" — loop with new seed
        print(f"  Regenerating (iteration {iteration + 1}/{max_iterations})…")

    # Exhausted iterations
    print(f"\n⚠ Max iterations ({max_iterations}) reached without approval.")
    result = ReviewResult(
        approved=False,
        track_title=track_title,
        prompt=prompt,
        hero_image=hero,            # type: ignore[arg-type]
        thumbnails=thumbs,
        notes=f"max iterations ({max_iterations}) without approval",
        regenerations=max_iterations - 1,
    )
    _write_review_record(result)
    return result


def _write_review_record(result: ReviewResult) -> None:
    """Persist a review-session record for future reference."""
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = REVIEW_DIR / f"{result.track_title.lower().replace(' ', '_')}.json"
    path.write_text(json.dumps({
        "track_title":    result.track_title,
        "approved":       result.approved,
        "regenerations":  result.regenerations,
        "notes":          result.notes,
        "hero_image":     str(result.hero_image.local_path) if result.hero_image else None,
        "thumbnails":     [str(t.local_path) for t in result.thumbnails],
        "flux_prompt":    result.prompt.flux_prompt,
    }, indent=2))
    logger.info("Review record: %s", path)
