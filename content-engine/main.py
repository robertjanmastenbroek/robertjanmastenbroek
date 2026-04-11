"""
Holy Rave Content Engine — main orchestrator.

Loop:
1. Check Google Drive input folder for new videos
2. Download new files
3. Process into 15s / 30s / 60s vertical clips
4. Generate hooks + captions for each platform via Claude
5. Upload clips + caption file to Google Drive output folder
6. Mark as processed
7. Sleep, then repeat

Environment variables required:
  GOOGLE_SERVICE_ACCOUNT_JSON  — full service account JSON as a string
  GOOGLE_DRIVE_INPUT_FOLDER_ID — Drive folder ID where you drop raw videos
  GOOGLE_DRIVE_OUTPUT_FOLDER_ID — Drive folder ID for processed content
  ANTHROPIC_API_KEY             — Claude API key
"""

import os
import json
import time
import shutil
import logging
import tempfile
import schedule

import drive
import processor
import generator

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
def process_file(svc, file_meta: dict, processed_ids: set):
    """Full pipeline for one video file."""
    file_id   = file_meta['id']
    file_name = file_meta['name']
    base_name = os.path.splitext(file_name)[0]

    logger.info(f"─── Processing: {file_name} ───")

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

        # 3a. Generate hooks — Call 1 (temperature 1.0, no JSON pressure, 5 ranked candidates)
        logger.info("Generating hooks with Claude (Call 1)...")
        hooks_meta = generator.generate_hooks(file_name, possible_lengths)
        hooks = hooks_meta.get('hooks', {})
        angle = hooks_meta.get('angle')

        if angle:
            logger.info(f"Angle: {angle} | Track: {hooks_meta.get('track_name')} | Seed: {hooks_meta.get('seed_hint')}")

        # 3b. Generate captions — Call 2 (temperature 0.4, structured JSON)
        logger.info("Generating captions with Claude (Call 2)...")
        generated = generator.generate_content(file_name, possible_lengths, hooks_meta)

        # 4. Process video into clips
        clips_dir = os.path.join(work_dir, 'clips')
        output_files = processor.process_video(local_path, clips_dir, hooks, angle)

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

        logger.info(f"✅ Done: {file_name} → {len(output_files)} clips + captions uploaded")

        # 8. Mark as processed
        processed_ids.add(file_id)
        save_processed_ids(processed_ids)

    except Exception as e:
        logger.error(f"Pipeline failed for {file_name}: {e}", exc_info=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


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

    except Exception as e:
        logger.error(f"Check failed: {e}", exc_info=True)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("  Holy Rave Content Engine — starting up")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Validate env
    missing = []
    for var in ['GOOGLE_SERVICE_ACCOUNT_JSON', 'GOOGLE_DRIVE_INPUT_FOLDER_ID',
                'GOOGLE_DRIVE_OUTPUT_FOLDER_ID', 'ANTHROPIC_API_KEY']:
        if not os.environ.get(var):
            missing.append(var)

    if missing:
        logger.warning(f"Missing environment variables: {', '.join(missing)}")
        logger.warning("Engine will start but won't process until all vars are set.")

    # Run immediately on startup, then on schedule
    run_check()

    schedule.every(CHECK_INTERVAL).minutes.do(run_check)
    logger.info(f"Scheduled: checking Drive every {CHECK_INTERVAL} minutes")

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == '__main__':
    main()
