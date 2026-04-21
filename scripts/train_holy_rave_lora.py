#!/usr/bin/env python3
"""
train_holy_rave_lora.py — Train the Holy Rave brand LoRA on fal.ai.

Uploads the curated reference set from content/images/lora_training/holy_rave_v1/
to fal.ai's Flux 2 LoRA trainer, monitors the job, prints the .safetensors
URL that you paste into .env as FAL_BRAND_LORA_URL.

Prerequisites:
    FAL_KEY set in .env
    25+ curated images under content/images/lora_training/holy_rave_v1/
    pip install fal-client requests

Usage:
    python3 scripts/train_holy_rave_lora.py             # Full run — trains from v1 folder
    python3 scripts/train_holy_rave_lora.py --dry-run   # Validate inputs only, no API call
    python3 scripts/train_holy_rave_lora.py --steps 2000  # Override training steps

Cost reference (fal.ai Flux 2 Trainer, April 2026):
    ~$0.008 per step; 1,500–2,500 steps = $12–$20 one-time.
    Total wall-clock: ~1 hour.

The trainer creates a LoRA (Low-Rank Adaptation) that teaches Flux 2 to
produce images matching the Holy Rave visual universe 95% of the time,
up from ~70% with prompt engineering alone.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from content_engine.youtube_longform import config as cfg


TRAINER_ENDPOINT = "fal-ai/flux-2-trainer"
DEFAULT_TRAIN_STEPS = 2000
DEFAULT_TRIGGER_WORD = "hr_brand"

logger = logging.getLogger("train_lora")


# ─── Input validation ────────────────────────────────────────────────────────

MIN_IMAGES = 15
RECOMMENDED_IMAGES = 25
MAX_IMAGES = 40     # Diminishing returns beyond this point for style LoRAs
MAX_FILE_SIZE_MB = 20


def collect_training_images(training_dir: Path) -> list[Path]:
    """Find all .jpg/.jpeg/.png files in the training directory."""
    if not training_dir.exists():
        raise FileNotFoundError(
            f"Training directory does not exist: {training_dir}\n"
            f"Run the image gathering agent first, or manually curate 25 images."
        )

    images = sorted(
        p for p in training_dir.iterdir()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png")
        and not p.name.startswith(".")
        and not p.name.startswith("_")   # Convention: _foo.jpg = staged, not yet approved
    )
    return images


def validate_training_set(images: list[Path]) -> None:
    """Raise RuntimeError if the set is unsuitable for LoRA training."""
    n = len(images)
    if n < MIN_IMAGES:
        raise RuntimeError(
            f"Only {n} images found; need at least {MIN_IMAGES}. "
            f"LoRAs trained on too few images produce bland, unspecific output."
        )
    if n > MAX_IMAGES:
        logger.warning(
            "Training set has %d images; optimal is 20–30. "
            "Extra images beyond ~30 add noise without improving specificity. "
            "Consider pruning to your 25 strongest references.",
            n,
        )

    # File-size sanity
    too_big = [p for p in images if p.stat().st_size > MAX_FILE_SIZE_MB * 1024 * 1024]
    if too_big:
        logger.warning(
            "%d images exceed %d MB and may be slow to upload: %s",
            len(too_big), MAX_FILE_SIZE_MB, [p.name for p in too_big[:3]],
        )


# ─── Upload + training ───────────────────────────────────────────────────────

def build_zip(images: list[Path]) -> bytes:
    """Bundle the training set into a single zip for fal upload."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for img in images:
            zf.write(img, arcname=img.name)
    buf.seek(0)
    return buf.getvalue()


def submit_training_job(
    images: list[Path],
    steps: int,
    trigger_word: str,
    dry_run: bool,
) -> str:
    """Submit the LoRA training job. Returns the safetensors URL on success."""
    try:
        import fal_client  # type: ignore
    except ImportError:
        print("✗ fal-client not installed. Run:\n  pip install fal-client>=0.5.0", file=sys.stderr)
        sys.exit(1)

    os.environ.setdefault("FAL_KEY", cfg.FAL_KEY)

    zip_bytes = build_zip(images)
    zip_size_mb = len(zip_bytes) / 1024 / 1024
    logger.info("Training zip: %d images, %.1f MB", len(images), zip_size_mb)

    if dry_run:
        print("\n✓ Dry run complete. Training set passed validation.")
        print(f"  Images:         {len(images)}")
        print(f"  Zip size:       {zip_size_mb:.1f} MB")
        print(f"  Steps:          {steps}")
        print(f"  Trigger word:   {trigger_word}")
        print(f"  Endpoint:       {TRAINER_ENDPOINT}")
        print(f"  Estimated cost: ~${steps * 0.008:.2f}")
        return ""

    # 1) Upload the zip to fal storage
    logger.info("Uploading training zip to fal storage…")
    # fal_client's upload helper accepts bytes since 0.5+
    zip_url = fal_client.upload(zip_bytes, content_type="application/zip")
    logger.info("Zip uploaded: %s", zip_url)

    # 2) Kick off the training job
    logger.info("Submitting training job (%d steps)…", steps)
    arguments = {
        "images_data_url":  zip_url,
        "trigger_word":     trigger_word,
        "steps":            steps,
        "learning_rate":    0.0004,
    }
    handler = fal_client.submit(TRAINER_ENDPOINT, arguments=arguments)
    request_id = handler.request_id
    logger.info("Training job submitted. request_id=%s", request_id)

    # 3) Poll until complete. fal.ai's subscribe() abstracts this — but
    # since we want progress reporting, we poll manually.
    deadline = time.time() + 3 * 3600  # 3h hard ceiling; typical is <1h
    poll_interval = 30
    while time.time() < deadline:
        time.sleep(poll_interval)
        status = fal_client.status(TRAINER_ENDPOINT, request_id, with_logs=False)
        # fal's status object: queue_position, status string, logs
        state = getattr(status, "status", None) or (status if isinstance(status, str) else "UNKNOWN")
        logger.info("Training status: %s", state)
        if str(state).upper() in ("COMPLETED", "SUCCESS", "DONE"):
            break
        if str(state).upper() in ("FAILED", "ERROR", "CANCELLED"):
            raise RuntimeError(f"Training failed with status: {state}")
    else:
        raise RuntimeError("Training did not complete within 3 hours. Check fal dashboard.")

    # 4) Fetch result
    result = fal_client.result(TRAINER_ENDPOINT, request_id)
    lora_url = None
    if isinstance(result, dict):
        lora_url = (
            result.get("diffusers_lora_file", {}).get("url")
            or result.get("lora_url")
            or result.get("safetensors_url")
        )
    if not lora_url:
        raise RuntimeError(f"Training complete but no LoRA URL in result: {result!r}")

    logger.info("✓ LoRA training complete: %s", lora_url)
    return lora_url


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs only; do not call fal.ai")
    parser.add_argument("--steps", type=int, default=DEFAULT_TRAIN_STEPS, help=f"Training steps (default {DEFAULT_TRAIN_STEPS})")
    parser.add_argument("--trigger", type=str, default=DEFAULT_TRIGGER_WORD, help=f"Trigger word baked into the LoRA (default {DEFAULT_TRIGGER_WORD})")
    parser.add_argument("--training-dir", type=Path, default=cfg.LORA_TRAINING_DIR, help="Override training directory")
    args = parser.parse_args()

    if not cfg.FAL_KEY and not args.dry_run:
        print("✗ FAL_KEY not set in environment. See .env.example.youtube_longform", file=sys.stderr)
        return 1

    training_dir = args.training_dir
    print(f"Training directory: {training_dir}")
    images = collect_training_images(training_dir)
    print(f"Found {len(images)} training images:")
    for img in images[:10]:
        print(f"  {img.name}")
    if len(images) > 10:
        print(f"  … and {len(images) - 10} more")

    validate_training_set(images)

    print(f"\nEstimated cost: ~${args.steps * 0.008:.2f} ({args.steps} steps at $0.008/step)")
    if not args.dry_run:
        reply = input("\nProceed with training? [y/N]: ").strip().lower()
        if reply != "y":
            print("Aborted.")
            return 0

    lora_url = submit_training_job(
        images=images,
        steps=args.steps,
        trigger_word=args.trigger,
        dry_run=args.dry_run,
    )

    if lora_url:
        print("\n" + "=" * 70)
        print("✓ LoRA training complete!")
        print(f"\n  LoRA URL: {lora_url}")
        print("\nNext step: paste into your .env file:")
        print(f"  FAL_BRAND_LORA_URL={lora_url}")
        print(f"  FAL_BRAND_LORA_SCALE=0.80")
        print("\nThen rerun: python3 rjm.py content youtube explain Jericho")
        print("(should now use the LoRA automatically)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
