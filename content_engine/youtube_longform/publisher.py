"""
publisher.py — End-to-end orchestrator.

Single public entry point: publish_track(PublishRequest) → PublishResult

Pipeline:
  1.  Build TrackPrompt (prompt_builder).
  2.  Generate hero image + 3 thumbnail variants (image_gen).
  3.  Composite audio + hero → MP4 (render).
  4.  Build smart link + UTM (registry).
  5.  Compose title/description (template in this module).
  6.  Upload to YouTube (uploader).
  7.  Append registry row.

Dry-run short-circuits after step 4: builds prompts, generates images,
renders video, builds smart link — but skips the YouTube upload.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from content_engine.audio_engine import TrackPool
from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform import (
    image_gen,
    prompt_builder,
    registry,
    render,
    uploader,
)
from content_engine.youtube_longform.types import (
    ImageAsset,
    PublishRequest,
    PublishResult,
    RenderSpec,
    UploadSpec,
)

logger = logging.getLogger(__name__)


class PublishError(Exception):
    """Raised when publish fails at any stage."""


# ─── Description template ────────────────────────────────────────────────────

def _compose_description(
    track_title: str,
    smart_link: str,
    scripture_anchor: str,
    scripture_hook: str,
    genre_tag: str,
) -> str:
    """
    Mirrors the @osso-so description template — three top hashtags
    (which YouTube hoists above the title) + link stack + scripture
    flavoring + bottom hashtag stack.
    """
    top_hashtags = " ".join(cfg.HASHTAGS_TOP.get(genre_tag, cfg.HASHTAGS_TOP["organic_tribal"]))

    scripture_line = ""
    if scripture_anchor:
        scripture_line = f"\n— {scripture_anchor}\n"

    bottom_hashtags = " ".join(cfg.HASHTAGS_BOTTOM)

    return (
        f"{top_hashtags}\n\n"
        f"Enjoy it.\n\n"
        f"Listen everywhere: {smart_link}\n"
        f"Spotify: {cfg.SPOTIFY_ARTIST_URL}\n"
        f"Apple Music: {cfg.APPLE_MUSIC_URL}\n\n"
        f"— {track_title}\n"
        f"{scripture_line}\n"
        f"{cfg.ARTIST_FULL_NAME}\n"
        f"{cfg.CHANNEL_BRAND_NAME} — Ancient Truth. Future Sound.\n"
        f"Instagram: {cfg.ARTIST_INSTAGRAM}\n"
        f"TikTok: {cfg.ARTIST_TIKTOK}\n"
        f"Web: {cfg.ARTIST_WEBSITE}\n\n"
        f"{bottom_hashtags}\n"
    )


def _compose_title(track_title: str) -> str:
    return f"{cfg.ARTIST_FULL_NAME} - {track_title}"


def _compose_tags(genre: str, mood_tier: str, scripture_anchor: str) -> list[str]:
    base = [
        "Robert-Jan Mastenbroek", "RJM", "Holy Rave",
        "nomadic electronic", "organic house", "tribal psytrance",
        "ethnic electronic", "Middle Eastern electronic",
        "Cafe de Anatolia", "Sol Selectas", "handpan", "oud",
        "tribal drums", "sacred geometry", "desert rave",
        genre, mood_tier,
    ]
    if scripture_anchor:
        base.append(scripture_anchor)
    return base


def _genre_tag_key(mood_tier: str, scripture_anchor: str) -> str:
    """Map mood/genre to a hashtag bucket key."""
    if mood_tier == "ecstatic":
        return "psytrance"
    if scripture_anchor == "Psalm 46":
        return "middle_eastern"
    if mood_tier == "meditative":
        return "organic_house"
    return "organic_tribal"


# ─── Track audio lookup ──────────────────────────────────────────────────────

def _resolve_audio_path(track_title: str, override: Optional[Path]) -> Path:
    if override and override.exists():
        return override
    # Reuse audio_engine's TrackPool to find the file on disk
    pool = TrackPool()
    for t in pool.tracks:
        if t.title.lower() == track_title.lower() and t.file_path:
            p = Path(t.file_path)
            if p.exists():
                return p
    raise PublishError(
        f"Audio master not found for '{track_title}'. Place the WAV/FLAC/MP3 "
        f"under {cfg.AUDIO_MASTERS} or pass audio_path in PublishRequest."
    )


def _audio_duration_seconds(audio_path: Path) -> int:
    """
    Probe audio duration. Prefers mutagen (pure-Python, no ffmpeg) so the
    ffmpeg ban is respected. Falls back to 360s (6 min) if mutagen is
    missing — not ideal, but keeps the pipeline moving.
    """
    try:
        from mutagen import File as MutagenFile  # type: ignore
    except ImportError:
        logger.warning(
            "mutagen not installed — defaulting track length to 360s. "
            "Add `pip install mutagen` for accurate timing."
        )
        return 360

    mf = MutagenFile(str(audio_path))
    if mf is None or not getattr(mf, "info", None):
        logger.warning("mutagen could not parse %s — defaulting to 360s", audio_path)
        return 360
    return int(mf.info.length)


# ─── Public API ──────────────────────────────────────────────────────────────

def publish_track(req: PublishRequest) -> PublishResult:
    """Execute the full publish pipeline."""
    t_start = time.time()
    result = PublishResult(request=req)

    # Dedup guard
    existing = registry.already_published(req.track_title)
    if existing and not existing.get("dry_run") and not existing.get("error"):
        result.error = f"Already published: {existing.get('youtube_url')}"
        result.youtube_id = existing.get("youtube_id")
        result.youtube_url = existing.get("youtube_url")
        result.smart_link = existing.get("smart_link")
        return result

    try:
        cfg.ensure_workspace()

        # 1. Prompt
        prompt = prompt_builder.build_prompt(req.track_title)
        result.prompt = prompt
        logger.info(
            "Prompt built: %s | %s | %s BPM | anchor=%s",
            prompt.track_title, prompt.genre, prompt.bpm, prompt.scripture_anchor or "(none)",
        )

        # 2. Images
        if not req.skip_image_gen:
            result.hero_image = image_gen.generate_hero(prompt)
            thumb_variants = prompt_builder.build_thumbnail_variants(
                prompt, count=cfg.THUMB_VARIANT_COUNT,
            )
            result.thumbnails = image_gen.generate_thumbnails(thumb_variants)

        # 3. Render (requires publicly reachable URLs for audio + image)
        if not req.dry_run:
            if result.hero_image is None:
                raise PublishError("Hero image required for render")
            audio_path = _resolve_audio_path(req.track_title, req.audio_path)
            audio_public = render.upload_audio_for_render(
                audio_path,
                public_id=f"audio_{prompt.track_title.lower().replace(' ', '_')}",
            )
            hero_public = render.upload_image_for_render(
                result.hero_image.local_path,
                public_id=f"hero_{prompt.track_title.lower().replace(' ', '_')}",
            )
            duration = _audio_duration_seconds(audio_path)
            spec = RenderSpec(
                audio_url=audio_public,
                hero_image_url=hero_public,
                duration_seconds=duration,
                output_label=prompt.track_title.lower().replace(" ", "_"),
            )
            result.video = render.composite(spec)

        # 4. Smart link
        result.smart_link = registry.build_smart_link(req.track_title)

        # 5. Title / description / tags
        genre_key = _genre_tag_key(prompt.mood_tier, prompt.scripture_anchor)
        title = _compose_title(req.track_title)
        description = _compose_description(
            track_title=req.track_title,
            smart_link=result.smart_link,
            scripture_anchor=prompt.scripture_anchor,
            scripture_hook=prompt.scripture_hook,
            genre_tag=genre_key,
        )
        tags = _compose_tags(prompt.genre, prompt.mood_tier, prompt.scripture_anchor)

        # 6. Upload
        if req.dry_run:
            logger.info("DRY RUN — skipping YouTube upload. Title: %s", title)
            result.youtube_id = None
            result.youtube_url = None
        else:
            if result.video is None:
                raise PublishError("Rendered video required for upload")
            upload_spec = UploadSpec(
                video_path=result.video.local_path,
                thumbnail_paths=[t.local_path for t in result.thumbnails],
                title=title,
                description=description,
                tags=tags,
                publish_at_iso=req.publish_at_iso,
                privacy_status="private" if req.publish_at_iso else "public",
                channel_id=req.channel_id or cfg.YT_HOLY_RAVE_CHANNEL_ID or None,
                playlist_id=_select_playlist(genre_key),
            )
            video_id = uploader.upload(upload_spec)
            result.youtube_id = video_id
            result.youtube_url = f"https://youtube.com/watch?v={video_id}"

        # 7. Cost estimate + registry
        cost = image_gen.estimate_cost_usd(hero_count=1, thumb_count=len(result.thumbnails))
        result.cost_usd = cost
        result.elapsed_seconds = round(time.time() - t_start, 1)

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        result.elapsed_seconds = round(time.time() - t_start, 1)
        logger.exception("Publish failed for %s: %s", req.track_title, e)

    registry.append(result)
    return result


def _select_playlist(genre_key: str) -> Optional[str]:
    """Pick a playlist to append the video to based on genre bucket."""
    if genre_key == "psytrance":
        return cfg.YT_PLAYLIST_TRIBAL_PSY or None
    if genre_key == "middle_eastern":
        return cfg.YT_PLAYLIST_MIDDLE_EASTERN or None
    return cfg.YT_PLAYLIST_ORGANIC_HOUSE or None
