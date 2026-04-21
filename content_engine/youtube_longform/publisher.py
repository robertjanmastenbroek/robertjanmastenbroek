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

from content_engine.audio_engine import (
    TRACK_LANGUAGES,
    TRACK_LYRICS,
    TrackPool,
)
from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform import (
    image_gen,
    motion as motion_mod,
    prompt_builder,
    registry,
    render,
    scripture as scripture_mod,
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

def _utm_suffix(track_title: str, medium: str = "holyrave_longform") -> str:
    """UTM params for every outbound link — free measurement across all destinations."""
    slug = "".join(c if c.isalnum() else "_" for c in track_title.lower()).strip("_")
    return f"?utm_source=youtube&utm_medium={medium}&utm_campaign=hr_{slug}"


def _tagged(url: str, track_title: str) -> str:
    """Append UTM to any URL that doesn't already have query params."""
    if not url:
        return url
    # Rough check: if URL already has ?, append with &; otherwise append ?
    separator = "&" if "?" in url else "?"
    # Skip the "?" at the start of our suffix since we add our own separator
    params = _utm_suffix(track_title).lstrip("?")
    return f"{url}{separator}{params}"


def _compose_description(
    track_title: str,
    smart_link: str,
    scripture_anchor: str,
    scripture_hook: str,
    genre_tag: str,
) -> str:
    """
    @osso-so-template description — 3 top hashtags (YouTube hoists above
    the title) + link stack + scripture-as-lyrics block + 18 bottom
    hashtags. Every outbound link gets a UTM tag so we can measure
    per-track YouTube→Spotify/Apple/website conversion in existing
    analytics (Spotify for Artists, Apple Music for Artists, Google
    Analytics on robertjanmastenbroek.com).
    """
    top_hashtags = " ".join(cfg.HASHTAGS_TOP.get(genre_tag, cfg.HASHTAGS_TOP["organic_tribal"]))
    bottom_hashtags = " ".join(cfg.HASHTAGS_BOTTOM)

    # Scripture / lyrics block — priority: explicit lyrics > scripture verse > skip.
    key = track_title.lower().strip()
    explicit_lyrics = TRACK_LYRICS.get(key, "") if key in TRACK_LYRICS else ""
    scripture_verse = scripture_mod.verse_for(scripture_anchor)

    lyrics_block = ""
    if explicit_lyrics:
        lyrics_block = f"\nLyrics:\n{explicit_lyrics}\n"
    elif scripture_verse:
        lyrics_block = f"\nScripture:\n{scripture_verse}\n"

    # Link stack — show a per-DSP line ONLY when we have a track-specific
    # URL for that platform. Artist-page fallbacks (e.g. Apple Music artist
    # URL for a track not yet on Apple) are MORE CONFUSING than no link —
    # users click expecting the track and land on a page that doesn't show
    # it. The smart link (Odesli) already covers everything via aggregation.
    #
    # UTM params are added only for per-track URLs so downstream analytics
    # can attribute clicks. Artist-page links (Instagram/TikTok/Web) are
    # left untagged because they're too generic to attribute to this specific
    # video reliably — Linktree in bio handles those separately.
    from content_engine.audio_engine import (
        TRACK_APPLE_MUSIC_URLS, TRACK_SPOTIFY_URLS,
    )
    key = track_title.lower().strip()
    raw_spotify_track = TRACK_SPOTIFY_URLS.get(key, "")
    raw_apple_track   = TRACK_APPLE_MUSIC_URLS.get(key, "")

    link_lines: list[str] = [f"Listen everywhere: {smart_link}"]
    if raw_spotify_track:
        link_lines.append(f"Spotify: {_tagged(raw_spotify_track, track_title)}")
    if raw_apple_track:
        link_lines.append(f"Apple Music: {_tagged(raw_apple_track, track_title)}")
    link_stack = "\n".join(link_lines)

    web_url = _tagged(cfg.ARTIST_WEBSITE, track_title)
    ig_url  = cfg.ARTIST_INSTAGRAM   # IG + TikTok tracked separately via Linktree in bio
    tt_url  = cfg.ARTIST_TIKTOK

    anchor_line = f"\n— {scripture_anchor}" if scripture_anchor else ""

    return (
        f"{top_hashtags}\n\n"
        f"Enjoy it.\n\n"
        f"{link_stack}\n"
        f"{lyrics_block}"
        f"— {track_title}{anchor_line}\n\n"
        f"{cfg.ARTIST_FULL_NAME}\n"
        f"{cfg.CHANNEL_BRAND_NAME} — Ancient Truth. Future Sound.\n"
        f"Instagram: {ig_url}\n"
        f"TikTok: {tt_url}\n"
        f"Web: {web_url}\n\n"
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

    # Dedup guard — registry prevents accidental double-publish. Bypass with
    # req.force=True for legitimate re-publishes (e.g., after deleting the
    # previous YouTube upload because thumbnails/links needed fixing).
    existing = registry.already_published(req.track_title)
    if existing and not existing.get("dry_run") and not existing.get("error"):
        if req.force:
            logger.warning(
                "force=True: bypassing dedup guard. Prior publish at %s will be superseded.",
                existing.get("youtube_url"),
            )
        else:
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
        # @osso-so pattern: thumbnail IS the hero — same image, same promise.
        # YouTube auto-downsamples 1920x1080 → thumbnail sizes it needs. Keeps
        # the viewer's "what I clicked on is what plays" promise intact and
        # cuts 3 extra image-gen calls worth of fal.ai spend (~\$0.13/publish).
        #
        # Two paths:
        #   Motion publish: the FIRST motion keyframe IS the thumbnail — no
        #   separate hero gen. This guarantees the thumbnail exactly matches
        #   the video's opening frame (the viewer promise) and avoids using
        #   the still-hero prompt_builder vocabulary which is tuned to the
        #   older festival-psytrance slot (can trigger fal.ai content filter
        #   on some families; also off-brand for the Hebrew/Bedouin motion
        #   keyframes).
        #
        #   Stills-only publish: prompt_builder → image_gen.generate_hero
        #   produces the single image held for the full track duration.
        #
        # If YouTube Test & Compare A/B variants are desired later, call
        # image_gen.generate_thumbnails() explicitly — it still exists and
        # samples fresh references per variant.
        if not req.skip_image_gen:
            from content_engine.youtube_longform.types import ImageAsset

            if req.motion:
                # Motion publish: prefer the dedicated thumbnail keyframe
                # from the story (CTR-optimized, tight crop, strong face).
                # Fall back to the first in-chain keyframe if no dedicated
                # thumbnail is defined. Either way, we generate it HERE so
                # it's ready before the Kling morph pass.
                story = motion_mod.story_for_track(req.track_title)
                thumb_kf = story.thumbnail_keyframe or story.keyframes[0]
                logger.info(
                    "Motion path: thumbnail keyframe = '%s' (%s)",
                    thumb_kf.keyframe_id,
                    "dedicated" if story.thumbnail_keyframe else "first in chain",
                )
                rendered = motion_mod._generate_keyframe(thumb_kf, prompt)
                result.hero_image = ImageAsset(
                    role="hero",
                    local_path=rendered.local_path,
                    remote_url=rendered.remote_url,
                    width=cfg.HERO_WIDTH,
                    height=cfg.HERO_HEIGHT,
                    prompt_used=thumb_kf.still_prompt,
                    variant_index=0,
                )
            else:
                # Stills-only publish: use prompt_builder's hero slot.
                result.hero_image = image_gen.generate_hero(prompt)

            result.thumbnails = [
                ImageAsset(
                    role="thumbnail",
                    local_path=result.hero_image.local_path,
                    remote_url=result.hero_image.remote_url,
                    width=result.hero_image.width,
                    height=result.hero_image.height,
                    prompt_used=result.hero_image.prompt_used,
                    variant_index=0,
                )
            ]

        # 3. Render (requires publicly reachable URLs for audio + image)
        if not req.dry_run:
            if result.hero_image is None:
                raise PublishError("Hero image required for render")
            audio_path = _resolve_audio_path(req.track_title, req.audio_path)
            audio_public = render.upload_audio_for_render(
                audio_path,
                public_id=f"audio_{prompt.track_title.lower().replace(' ', '_')}",
            )
            duration = _audio_duration_seconds(audio_path)
            output_label = prompt.track_title.lower().replace(" ", "_")

            if req.motion:
                # Motion path: Kling O3 keyframe-chain morph loop rendered
                # across the full track duration on Shotstack v1 (PAYG).
                story = motion_mod.story_for_track(req.track_title)
                logger.info(
                    "Motion path: story '%s' with %d keyframes / %d morphs",
                    story.story_id, len(story.keyframes), len(story.morphs),
                )
                keyframes, morph_clips = motion_mod.generate_morph_loop(
                    story_id=story.story_id,
                    track_prompt=prompt,
                )
                result.video = motion_mod.stitch_full_track(
                    clips=morph_clips,
                    audio_url=audio_public,
                    target_duration_s=duration,
                    output_label=f"{output_label}_motion",
                    shotstack_env="v1",   # PAYG, no watermark
                )
            else:
                # Stills-only path: single hero image held for full duration.
                hero_public = render.upload_image_for_render(
                    result.hero_image.local_path,
                    public_id=f"hero_{output_label}",
                )
                spec = RenderSpec(
                    audio_url=audio_public,
                    hero_image_url=hero_public,
                    duration_seconds=duration,
                    output_label=output_label,
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
            # Per-track audio language — "he" for Hebrew vocals, "en" otherwise.
            # Falls back to "en" for unknown tracks.
            audio_lang = TRACK_LANGUAGES.get(req.track_title.lower().strip(), "en")
            upload_spec = UploadSpec(
                video_path=result.video.local_path,
                thumbnail_paths=[t.local_path for t in result.thumbnails],
                title=title,
                description=description,
                tags=tags,
                language="en",            # metadata language (title/description)
                audio_language=audio_lang, # per-track language of vocal content
                publish_at_iso=req.publish_at_iso,
                privacy_status="private" if req.publish_at_iso else "public",
                channel_id=req.channel_id or cfg.YT_HOLY_RAVE_CHANNEL_ID or None,
                playlist_id=_select_playlist(genre_key),
            )
            video_id = uploader.upload(upload_spec)
            result.youtube_id = video_id
            result.youtube_url = f"https://youtube.com/watch?v={video_id}"

        # 6b. Shorts pool amortization — on motion publishes (dry-run or
        # live), copy every Kling O3 clip generated for this track into
        # content/videos/holy-rave-motion/ so the Shorts pipeline can
        # reuse them as source footage. We pay for the clips anyway;
        # this doubles their utility across long-form + Shorts.
        if req.motion and not req.dry_run:
            try:
                n = _add_motion_clips_to_shorts_pool(req.track_title)
                if n:
                    logger.info(
                        "Shorts pool amortized: +%d clips from %s now reusable "
                        "in Shorts/Reels/TikTok pipeline",
                        n, req.track_title,
                    )
            except Exception as e:
                logger.warning("Shorts pool copy failed (non-fatal): %s", e)

        # 7. Cost estimate + registry
        cost = image_gen.estimate_cost_usd(hero_count=1, thumb_count=len(result.thumbnails))
        if req.motion:
            story = motion_mod.story_for_track(req.track_title)
            cost += motion_mod.estimate_cost_usd(
                keyframe_count=len(story.keyframes),
                duration_s=10,
            )
            # Shotstack PAYG for full-track render: $0.40/min
            if result.video:
                cost += round(0.40 * result.video.duration / 60, 4)
        result.cost_usd = round(cost, 4)
        result.elapsed_seconds = round(time.time() - t_start, 1)

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        result.elapsed_seconds = round(time.time() - t_start, 1)
        logger.exception("Publish failed for %s: %s", req.track_title, e)

    registry.append(result)
    return result


# ─── Shorts pool amortization ────────────────────────────────────────────────

def _add_motion_clips_to_shorts_pool(track_title: str) -> int:
    """
    After a successful motion publish, copy every motion_*.mp4 that belongs
    to this track into content/videos/holy-rave-motion/ so the existing
    Shorts pipeline (content_engine.pipeline) can use them as source footage.

    Rationale: we pay for each 10s Kling O3 clip (~$0.84 each). A single
    Selah publish spends $7.56 on 9 motion clips. Re-using those clips
    as Shorts source footage amortizes the spend across BOTH long-form
    YouTube AND the daily Shorts pipeline that feeds IG/TikTok/Shorts.

    Returns the number of clips copied. Idempotent — skips files that
    already exist in the pool (by filename).

    Files are COPIED (not symlinked) so that cleanup of the long-form
    output directory doesn't break the Shorts pool.
    """
    import shutil
    slug = track_title.lower().strip().replace(" ", "_")
    src_dir = cfg.VIDEO_DIR  # content/output/youtube_longform/videos
    dst_dir = cfg.PROJECT_DIR / "content" / "videos" / "holy-rave-motion"
    dst_dir.mkdir(parents=True, exist_ok=True)

    # Match motion_<track-related-id>_<hash>.mp4 — include any clip whose
    # filename contains a keyframe ID starting with rjm_<slug> or rjm_<track
    # keyword>. To keep it simple we match by the track's keyframe-id prefix.
    # For Jericho: rjm_warrior, rjm_priestess, rjm_temple, rjm_jericho_*
    # For Selah:   rjm_selah_*
    # We collect any motion_*.mp4 whose name contains the track slug OR
    # matches the RJM hero keyframes (warrior/priestess/temple) which are
    # shared across stories.
    patterns = [
        f"rjm_{slug}_",            # rjm_selah_… / rjm_jericho_…
        "rjm_warrior",             # universal hero keyframes shared across stories
        "rjm_priestess",
        "rjm_temple",
    ]

    copied = 0
    # motion.py writes these files as morph_<clip_id>_<hash>.mp4
    for src in src_dir.glob("morph_*.mp4"):
        if not any(p in src.name for p in patterns):
            continue
        dst = dst_dir / src.name
        if dst.exists():
            continue
        try:
            shutil.copy2(src, dst)
            copied += 1
            logger.info("Shorts pool: copied %s", src.name)
        except Exception as e:
            logger.warning("Could not copy %s to shorts pool: %s", src.name, e)

    if copied:
        logger.info(
            "Shorts pool: +%d clips from %s publish now available to "
            "the Shorts pipeline (content/videos/holy-rave-motion/)",
            copied, track_title,
        )
    return copied


def _select_playlist(genre_key: str) -> Optional[str]:
    """
    Pick which Holy Rave playlist to append the new video to.

    Two playlists, per the 2026-04-21 consolidation:
      - Tribal Psytrance (140+ BPM)   → psytrance bucket
      - Ethnic / Tribal / Organic (<140 BPM) → everything else

    Legacy 3-playlist fallback kept for back-compat in case older env vars
    are still set (ORGANIC_HOUSE / MIDDLE_EASTERN).
    """
    if genre_key == "psytrance":
        return cfg.YT_PLAYLIST_TRIBAL_PSY or None
    # Everything else routes to Ethnic / Tribal
    primary = cfg.YT_PLAYLIST_ETHNIC_TRIBAL
    if primary:
        return primary
    # Legacy fallback
    if genre_key == "middle_eastern":
        return cfg.YT_PLAYLIST_MIDDLE_EASTERN or None
    return cfg.YT_PLAYLIST_ORGANIC_HOUSE or None
