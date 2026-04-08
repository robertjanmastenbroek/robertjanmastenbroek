"""
Holy Rave Content Engine — main orchestrator.

Loop:
1. Check Google Drive input folder for new videos/images
2. Download new files
3. Videos → process into 15s/30s/60s vertical clips, generate hooks + captions
   Images  → generate branded carousel slides
4. All output tagged with growth bucket (reach / follow / spotify)
5. Captions include 3 hook variants for A/B/C testing + best posting time
6. Upload clips/images + caption file to Google Drive output folder
7. Log to performance learner — feeds strategy notes back into next generation cycle
8. Sleep, then repeat

Growth strategy: 3 posts per day across TikTok, IG Reels, YT Shorts.
  Bucket 1 — REACH:   max views, algorithm push
  Bucket 2 — FOLLOW:  grow audience, personality content
  Bucket 3 — SPOTIFY: drive streams, direct CTA

Environment variables required:
  GOOGLE_SERVICE_ACCOUNT_JSON  — full service account JSON as a string
  GOOGLE_DRIVE_INPUT_FOLDER_ID — Drive folder ID where you drop raw content
  GOOGLE_DRIVE_OUTPUT_FOLDER_ID — Drive folder ID for processed output
  ANTHROPIC_API_KEY             — Claude API key
"""

import os
import json
import time
import shutil
import logging
import tempfile
import schedule
from datetime import datetime, timezone

import drive
import processor
import generator

try:
    import learner as learner_module
    LEARNER_AVAILABLE = True
except ImportError:
    LEARNER_AVAILABLE = False

try:
    import carousel as carousel_module
    CAROUSEL_AVAILABLE = True
except ImportError:
    CAROUSEL_AVAILABLE = False

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
INPUT_FOLDER_ID  = os.environ.get('GOOGLE_DRIVE_INPUT_FOLDER_ID')
OUTPUT_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_OUTPUT_FOLDER_ID')
PROCESSED_LOG    = '/tmp/processed_ids.json'
CHECK_INTERVAL   = 30  # minutes between Drive checks

# Global learner instance — initialized in main()
_learner = None

# ── Processed file tracking ───────────────────────────────────────────────────
def load_processed_ids() -> set:
    try:
        with open(PROCESSED_LOG, 'r') as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_processed_ids(ids: set):
    with open(PROCESSED_LOG, 'w') as f:
        json.dump(list(ids), f)


# ── Core pipeline ─────────────────────────────────────────────────────────────
def _get_strategy_notes() -> str:
    """Get performance learnings to inject into content generation."""
    if _learner is None:
        return ''
    try:
        return _learner.get_strategy_notes()
    except Exception as e:
        logger.warning(f"Could not load strategy notes: {e}")
        return ''


def process_video_file(svc, file_meta: dict, processed_ids: set):
    """Full pipeline for one video file."""
    file_id   = file_meta['id']
    file_name = file_meta['name']
    base_name = os.path.splitext(file_name)[0]

    logger.info(f"─── Processing video: {file_name} ───")

    work_dir = tempfile.mkdtemp(prefix='holyrave_')
    try:
        # 1. Download
        local_path = drive.download_file(svc, file_id, file_name, work_dir)

        # 2. Get video info to know which clip lengths are possible
        try:
            info = processor.get_video_info(local_path)
            duration = info['duration']
        except Exception as e:
            logger.error(f"Can't read video: {e}")
            return

        possible_lengths = [l for l in processor.CLIP_LENGTHS if duration >= l]
        if not possible_lengths:
            logger.warning(f"Video too short ({duration:.1f}s) for any clip length, skipping")
            processed_ids.add(file_id)
            save_processed_ids(processed_ids)
            return

        # 3. Generate hooks + captions with performance learnings injected
        logger.info("Generating captions with Claude...")
        strategy_notes = _get_strategy_notes()
        if strategy_notes:
            logger.info("Injecting performance learnings into generation prompt")
        generated = generator.generate_content(file_name, possible_lengths, strategy_notes)
        clips_data = generated.get('clips', {})

        # Build hooks dict for processor — use hook_a (primary) for burned-in text
        hooks = {}
        for length in possible_lengths:
            clip_data = clips_data.get(str(length)) or clips_data.get(length, {})
            hooks[length] = clip_data.get('hook_a') or clip_data.get('hook', '')

        # 4. Process video into high-quality clips
        clips_dir = os.path.join(work_dir, 'clips')
        output_files = processor.process_video(local_path, clips_dir, hooks)

        if not output_files:
            logger.error(f"No clips produced for {file_name}")
            return

        # 5. Create output folder in Drive named after the source file
        output_subfolder_id = drive.get_or_create_subfolder(
            svc, OUTPUT_FOLDER_ID, base_name
        )

        # 6. Upload clips
        for clip_path in output_files:
            drive.upload_file(svc, clip_path, output_subfolder_id)

        # 7. Format and upload caption file
        caption_text = generator.format_caption_file(file_name, generated)
        drive.upload_text(svc, caption_text, f"{base_name}_captions.txt", output_subfolder_id)

        # 8. Log to performance learner for future improvement
        if _learner is not None:
            try:
                hooks_for_log = {
                    length: (clips_data.get(str(length)) or {}).get('hook_a', '')
                    for length in possible_lengths
                }
                _learner.log_batch(
                    filename=file_name,
                    bucket=generated.get('bucket', 'reach'),
                    content_type=generated.get('content_type', 'event'),
                    hooks=hooks_for_log,
                    clip_lengths=possible_lengths,
                )
            except Exception as e:
                logger.warning(f"Performance log failed (non-fatal): {e}")

        bucket = generated.get('bucket', 'reach')
        logger.info(f"✅ Done: {file_name} → {len(output_files)} clips [{bucket.upper()} bucket] + captions uploaded")

        # 9. Mark as processed
        processed_ids.add(file_id)
        save_processed_ids(processed_ids)

    except Exception as e:
        logger.error(f"Pipeline failed for {file_name}: {e}", exc_info=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def process_image_file(svc, file_meta: dict, processed_ids: set):
    """Pipeline for image files — generate branded carousel."""
    file_id   = file_meta['id']
    file_name = file_meta['name']
    base_name = os.path.splitext(file_name)[0]

    logger.info(f"─── Processing image: {file_name} ───")

    if not CAROUSEL_AVAILABLE:
        logger.warning("carousel.py not available — skipping image processing")
        processed_ids.add(file_id)
        save_processed_ids(processed_ids)
        return

    work_dir = tempfile.mkdtemp(prefix='holyrave_img_')
    try:
        # 1. Download image
        local_path = drive.download_file(svc, file_id, file_name, work_dir)

        # 2. Detect carousel type from filename prefix
        name_lower = file_name.lower()
        if name_lower.startswith('quote_'):
            carousel_type = 'quote_card'
            title = base_name.replace('quote_', '').replace('_', ' ').title()
        elif name_lower.startswith('track_') or name_lower.startswith('spotify_'):
            carousel_type = 'track_announcement'
            title = base_name.replace('track_', '').replace('spotify_', '').replace('_', ' ').title()
        else:
            carousel_type = 'event_recap'
            title = base_name.replace('_', ' ').title()

        # 3. Generate carousel slides
        slides_dir = os.path.join(work_dir, 'slides')
        os.makedirs(slides_dir, exist_ok=True)
        slide_paths = carousel_module.generate_carousel(
            carousel_type=carousel_type,
            output_dir=slides_dir,
            title=title,
            photo_paths=[local_path],
            base_name=base_name,
        )

        if not slide_paths:
            logger.error(f"No carousel slides produced for {file_name}")
            return

        # 4. Upload slides to Drive
        output_subfolder_id = drive.get_or_create_subfolder(
            svc, OUTPUT_FOLDER_ID, base_name
        )
        for slide_path in slide_paths:
            drive.upload_file(svc, slide_path, output_subfolder_id)

        logger.info(f"✅ Done: {file_name} → {len(slide_paths)} carousel slides uploaded [{carousel_type}]")

        processed_ids.add(file_id)
        save_processed_ids(processed_ids)

    except Exception as e:
        logger.error(f"Carousel pipeline failed for {file_name}: {e}", exc_info=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def process_file(svc, file_meta: dict, processed_ids: set):
    """Route file to correct pipeline based on type."""
    name = file_meta['name'].lower()
    ext = os.path.splitext(name)[1]
    if ext in drive.IMAGE_EXTENSIONS:
        process_image_file(svc, file_meta, processed_ids)
    else:
        process_video_file(svc, file_meta, processed_ids)


def generate_weekly_report(svc):
    """Generate and upload weekly performance report to Drive."""
    if _learner is None:
        return
    try:
        report = _learner.generate_report()
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        report_name = f"weekly_report_{date_str}.txt"
        drive.upload_text(svc, report, report_name, OUTPUT_FOLDER_ID)
        logger.info(f"Weekly report uploaded: {report_name}")
    except Exception as e:
        logger.error(f"Weekly report failed: {e}")


def run_check():
    """Check for new files and process them."""
    if not INPUT_FOLDER_ID or not OUTPUT_FOLDER_ID:
        logger.error("Drive folder IDs not set — check environment variables")
        return

    logger.info("Checking Google Drive for new files...")
    try:
        svc = drive.get_service()
        processed_ids = load_processed_ids()
        new_files = drive.list_new_files(svc, INPUT_FOLDER_ID, processed_ids)

        if not new_files:
            logger.info("No new files found.")
            return

        logger.info(f"Found {len(new_files)} new file(s) to process")
        for file_meta in new_files:
            process_file(svc, file_meta, processed_ids)

        # Sync learner to Drive after processing batch
        if _learner is not None:
            try:
                _learner.save_to_drive()
            except Exception as e:
                logger.warning(f"Learner Drive sync failed (non-fatal): {e}")

    except Exception as e:
        logger.error(f"Check failed: {e}", exc_info=True)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    global _learner

    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("  Holy Rave Content Engine — starting up")
    logger.info("  3-bucket strategy: REACH / FOLLOW / SPOTIFY")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Validate env
    missing = []
    for var in ['GOOGLE_SERVICE_ACCOUNT_JSON', 'GOOGLE_DRIVE_INPUT_FOLDER_ID',
                'GOOGLE_DRIVE_OUTPUT_FOLDER_ID', 'ANTHROPIC_API_KEY']:
        if not os.environ.get(var):
            missing.append(var)

    if missing:
        logger.warning(f"Missing environment variables: {', '.join(missing)}")
        logger.warning("Engine will start but won't process until all vars are set.")

    # Initialize performance learner
    if LEARNER_AVAILABLE and OUTPUT_FOLDER_ID:
        try:
            svc = drive.get_service()
            _learner = learner_module.PerformanceLearner(
                drive_service=svc,
                output_folder_id=OUTPUT_FOLDER_ID
            )
            _learner.load_from_drive()
            logger.info("Performance learner initialized — strategy notes will improve over time")
        except Exception as e:
            logger.warning(f"Learner init failed (non-fatal): {e}")
    else:
        logger.info("Performance learner not available — running without learning loop")

    if CAROUSEL_AVAILABLE:
        logger.info("Carousel generator ready — drop images prefixed with quote_/track_/event_")
    else:
        logger.info("Carousel generator not available (install Pillow to enable)")

    # Run immediately on startup, then on schedule
    run_check()

    schedule.every(CHECK_INTERVAL).minutes.do(run_check)
    logger.info(f"Scheduled: checking Drive every {CHECK_INTERVAL} minutes")

    # Weekly performance report — every Sunday at 9am
    schedule.every().sunday.at("09:00").do(
        lambda: generate_weekly_report(drive.get_service())
    )
    logger.info("Scheduled: weekly performance report every Sunday 9am")

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == '__main__':
    main()
