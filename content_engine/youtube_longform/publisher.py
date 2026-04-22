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
    thumbnail_compositor,
    thumbnail_learning,
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


# ─── CTR quality gate for thumbnail generation ───────────────────────────────

# Max regenerations per publish when the pre-publish check hard-fails.
# 1 is the sweet spot: a thumbnail that fails after ONE regen with
# prompt-suggestion feedback is almost certainly an inherent-composition
# issue in the story's still_prompt, which needs human intervention rather
# than another random Flux roll. Raise to 2 only after we have 20+ publishes
# of feedback data showing regens 2+ meaningfully beat regens 1.
MAX_THUMBNAIL_REGENS = 1

# Number of thumbnail variants to generate per publish. The publisher picks
# the best-scoring one and ships it; the losers are kept on disk for YouTube
# Test & Compare rotation (manual in Studio) and for weekly-learning analysis.
# Cost: N × $0.075 Flux call per publish. 3 hits the quality/cost sweet spot
# — 1 is flaky, 2 gives the gate real choice, 3+ diminishes in returns fast
# because the anchor prompt stays the same (all 3 variants are sampling the
# same prompt region, just with different refs and random seeds).
THUMBNAIL_VARIANTS_PER_PUBLISH = 3

# Short prompt suffixes used to differentiate the 3 variants. Each is a
# compositional nudge (not a brand rewrite) so the gate scores them
# against the same viral baseline. Think "same story, different angle"
# — every thumbnail is still 1200 BCE Hebrew, just with different
# cinematic framing / lighting / energy.
_VARIANT_SUFFIXES = (
    "",  # v0 — unchanged baseline
    " Alternative cinematic angle, camera slightly lower and to the left, "
    "richer warm amber light, more atmospheric haze in the background.",
    " Alternative lighting setup, cooler blue-hour sky with warmer key light "
    "on the subject, tighter depth of field, more bokeh bloom on background highlights.",
)


def _variant_score(check) -> tuple:
    """
    Composite ranking used to pick the best variant. Lexicographic tuple:
      1. Severity rank (pass=3 > soft_fail=2 > hard_fail=1)
      2. Corpus similarity (higher = more on-theme)
      3. Center-bias (higher = stronger dominant subject)
      4. Brightness normalized to the viral zone
      5. Palette on-brand flag

    Returning a tuple lets Python's natural ordering pick the winner
    via `max(candidates, key=_variant_score)`.
    """
    sev_rank = {"pass": 3, "soft_fail": 2, "hard_fail": 1}.get(check.severity, 0)
    # Brightness: ideal around 90-120; penalize both far from that window.
    b = check.composition.brightness_mean
    brightness_score = -abs(b - 105) / 105      # 0 at 105, negative further
    return (
        sev_rank,
        check.corpus_sim.mean_similarity,
        check.composition.center_bias,
        brightness_score,
        1 if check.composition.palette_on_brand else 0,
    )


def _title_thumbnail_for_youtube(
    clean_base_path: Path,
    track_title: str,
    scripture_anchor: str = "",
) -> Path:
    """
    Composite track title + artist + Holy Rave logo onto the clean Flux
    base. Returns the path to the titled JPEG that YouTube sees as the
    thumbnail. The clean base path stays untouched — Kling uses it as
    the morph chain's start frame so the interpolation stays stable
    (no text bleed-through as the morph runs).

    Non-fatal on failure: if compositing fails (missing font, bad base,
    etc.) we log a warning and return the clean base path as-is. Better
    to ship an untitled thumbnail than to fail the whole publish.
    """
    try:
        subtitle = scripture_anchor or None
        titled = thumbnail_compositor.composite_thumbnail(
            base_image_path=clean_base_path,
            track_title=track_title,
            subtitle=subtitle,
        )
        logger.info(
            "Titled thumbnail composed | %s → %s",
            track_title, titled.name,
        )
        return titled
    except Exception as e:
        logger.warning(
            "Thumbnail compositor failed (shipping untitled base): %s", e,
        )
        return clean_base_path


def _gated_motion_thumbnail(
    thumb_kf,                              # motion.Keyframe (frozen dataclass)
    prompt,                                # prompt_builder.TrackPrompt
    track_title: str,
    bpm: int,
    *,
    variants: int = THUMBNAIL_VARIANTS_PER_PUBLISH,
):
    """
    Generate N thumbnail variants, score each, ship the best.

    Returns a tuple (rendered_keyframe, effective_keyframe) where:
      - rendered_keyframe: motion._RenderedKeyframe for the winning variant
      - effective_keyframe: the Keyframe dataclass actually used (may be a
        variant with a modified still_prompt). Returned so motion.generate_
        morph_loop can use the SAME first-frame definition and the preroll
        morphs into the chain's rjm_warrior/priestess/etc from this winner.

    Behavior:
      1. Build N variant Keyframes by appending short compositional suffix
         prompts to the original (same subject, different framing/light).
      2. Generate each via Flux 2 Pro /edit (reference-conditioned on the
         528-thumbnail viral pool; view-count-weighted picks per variant).
      3. Score each via thumbnail_learning.pre_publish_check.
      4. If the best variant is a hard_fail AND MAX_THUMBNAIL_REGENS > 0
         AND the gate has prompt suggestions, run ONE more regen against
         the best variant's prompt + suggestions.
      5. Pick the final winner by composite score and return it.

    Net cost per publish: variants × $0.075 (+ optional 1 regen). With
    variants=3 this is $0.225-$0.30 — still cheap vs. the ~$7 full
    Jericho-class publish. spend_guard enforces the daily cap anyway.

    All variant scores are logged to thumbnail_learning_events.jsonl so
    the weekly analyzer can spot patterns (e.g. "variant suffix 2 tends
    to win on psytrance tracks").
    """
    from content_engine.youtube_longform.motion import Keyframe as MKf

    n = max(1, min(variants, len(_VARIANT_SUFFIXES)))
    logger.info(
        "Thumbnail variant sweep | %s | %d variants planned", track_title, n,
    )

    candidates: list[tuple] = []
    for idx in range(n):
        suffix = _VARIANT_SUFFIXES[idx]
        # Variant 0 uses the original still_prompt verbatim (same cache
        # digest as direct-call behavior — benefits from previous caches).
        # Variants 1+ get a suffix, producing a different digest → fresh gen.
        vkf = thumb_kf if idx == 0 else MKf(
            keyframe_id=f"{thumb_kf.keyframe_id}_v{idx}",
            still_prompt=thumb_kf.still_prompt + suffix,
        )
        rendered = motion_mod._generate_keyframe(vkf, prompt)
        check = thumbnail_learning.pre_publish_check(
            image_path=rendered.local_path,
            bpm=bpm,
            track_title=f"{track_title} / {vkf.keyframe_id} / variant={idx}",
        )
        logger.info(
            "Thumbnail variant %d/%d | %s | severity=%s | sim=%.2f | bright=%.0f | center=%.2f | palette=%s",
            idx + 1, n, vkf.keyframe_id, check.severity,
            check.corpus_sim.mean_similarity, check.composition.brightness_mean,
            check.composition.center_bias, check.composition.palette_on_brand,
        )
        candidates.append((rendered, vkf, check))

    # Pick the best variant
    winner = max(candidates, key=lambda t: _variant_score(t[2]))
    win_rendered, win_kf, win_check = winner
    win_idx = candidates.index(winner)
    logger.info(
        "Thumbnail variant sweep | %s | WINNER=variant_%d (%s) sim=%.2f severity=%s",
        track_title, win_idx, win_kf.keyframe_id,
        win_check.corpus_sim.mean_similarity, win_check.severity,
    )

    # Late-stage regen: if even the best variant hard-fails, try one more
    # pass with the gate's diagnosis suggestions appended. This catches
    # the case where all 3 variants are too dark (common when the ambient
    # scene is night/ruins). Costs one more $0.075 Flux call.
    if (win_check.severity == "hard_fail"
            and MAX_THUMBNAIL_REGENS >= 1
            and win_check.suggested_prompt_additions):
        addition = ". ".join(win_check.suggested_prompt_additions)
        regen_kf = MKf(
            keyframe_id=f"{win_kf.keyframe_id}_regen",
            still_prompt=f"{win_kf.still_prompt} {addition}",
        )
        logger.info(
            "Thumbnail regen after variant sweep | %s | suggestions: %s",
            track_title, addition[:160],
        )
        regen_rendered = motion_mod._generate_keyframe(regen_kf, prompt)
        regen_check = thumbnail_learning.pre_publish_check(
            image_path=regen_rendered.local_path,
            bpm=bpm,
            track_title=f"{track_title} / {regen_kf.keyframe_id} / regen-after-sweep",
        )
        logger.info(
            "Thumbnail regen | %s | severity=%s | sim=%.2f | bright=%.0f",
            track_title, regen_check.severity,
            regen_check.corpus_sim.mean_similarity,
            regen_check.composition.brightness_mean,
        )
        if _variant_score(regen_check) > _variant_score(win_check):
            return regen_rendered, regen_kf

    return win_rendered, win_kf


def _gated_stills_hero(prompt, track_title: str, bpm: int) -> ImageAsset:
    """
    Generate a stills-path hero image through the CTR gate.

    Analogous to _gated_motion_thumbnail but for the stills-only path,
    where the hero image IS the whole video background (no morph chain to
    worry about). Regenerates with suggestions appended to
    TrackPrompt.flux_prompt when hard-failing.
    """
    from dataclasses import replace

    hero = image_gen.generate_hero(prompt)
    check = thumbnail_learning.pre_publish_check(
        image_path=hero.local_path,
        bpm=bpm,
        track_title=f"{track_title} / stills-hero / attempt=1",
    )
    logger.info(
        "Hero CTR gate | %s | attempt=1 | severity=%s | sim=%.2f | bright=%.0f",
        track_title, check.severity, check.corpus_sim.mean_similarity,
        check.composition.brightness_mean,
    )
    if check.severity != "hard_fail" or MAX_THUMBNAIL_REGENS < 1 or not check.suggested_prompt_additions:
        return hero

    addition = ". ".join(check.suggested_prompt_additions)
    new_prompt = replace(prompt, flux_prompt=f"{prompt.flux_prompt} {addition}")
    logger.info(
        "Hero CTR gate | %s | regen attempt=2 with additions: %s",
        track_title, addition[:160],
    )
    hero2 = image_gen.generate_hero(new_prompt)
    check2 = thumbnail_learning.pre_publish_check(
        image_path=hero2.local_path,
        bpm=bpm,
        track_title=f"{track_title} / stills-hero / attempt=2",
    )
    logger.info(
        "Hero CTR gate | %s | attempt=2 | severity=%s | sim=%.2f | bright=%.0f",
        track_title, check2.severity, check2.corpus_sim.mean_similarity,
        check2.composition.brightness_mean,
    )

    def _score(c):
        sev_rank = {"pass": 3, "soft_fail": 2, "hard_fail": 1}[c.severity]
        return (sev_rank, c.corpus_sim.mean_similarity, c.composition.brightness_mean)

    if _score(check2) > _score(check):
        logger.info("Hero CTR gate | %s | regen WINS (shipped)", track_title)
        return hero2
    logger.info("Hero CTR gate | %s | original WINS (regen worse — shipped original)", track_title)
    return hero


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
    the title) + Spotify-first link stack + scripture-as-lyrics block +
    18 bottom hashtags.

    Link stack ordering is Spotify-first by explicit design (2026-04-22):
    North Star is 1M Spotify monthly listeners, so every click should
    route to Spotify. Apple Music is surfaced only as a secondary
    option when a track-specific Apple URL exists. Odesli-style
    aggregator landing pages are NOT used here because their DSP
    picker splits conversion away from Spotify.

    Every outbound Spotify/Apple/Web link gets a UTM tag so Spotify
    for Artists and Apple Music for Artists can attribute YouTube
    inbound traffic per-track.
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

    # Link stack — Spotify-first (2026-04-22). Fall back to the artist
    # URL when a track-specific Spotify URL isn't known yet (e.g. for
    # unreleased tracks like Kadosh / Side by Side where we still want
    # *some* Spotify discovery target).
    #
    # Apple Music appears ONLY when a track-specific URL exists —
    # artist-page fallbacks confuse the user ("where's the track I
    # just clicked on?"). No Odesli/smart-link line in the primary
    # stack because the smart_link is now already Spotify (see
    # registry.build_smart_link).
    #
    # UTM params go on every per-track DSP URL for analytics.
    # Instagram/TikTok/Web get UTMs via their own paths; IG/TikTok
    # are artist-level so UTMs are less useful there.
    from content_engine.audio_engine import (
        TRACK_APPLE_MUSIC_URLS, TRACK_SPOTIFY_URLS,
    )
    key = track_title.lower().strip()
    raw_spotify_track = TRACK_SPOTIFY_URLS.get(key, "")
    raw_apple_track   = TRACK_APPLE_MUSIC_URLS.get(key, "")

    if raw_spotify_track:
        primary_spotify_url = _tagged(raw_spotify_track, track_title)
        primary_label = "\U0001f3a7 Spotify"
    else:
        # No per-track URL — the smart_link already resolves to the
        # artist Spotify URL (see registry.build_smart_link default
        # "spotify" mode), so use that verbatim rather than
        # double-UTM-ing it.
        primary_spotify_url = smart_link
        primary_label = "\U0001f3a7 Spotify (artist page)"

    link_lines: list[str] = [f"{primary_label}: {primary_spotify_url}"]
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


def _compose_pinned_cta(
    track_title: str,
    smart_link: str,
    scripture_anchor: str,
) -> str:
    """
    First top-level comment posted by the channel owner after upload.

    YouTube has no Data API endpoint for pinning comments (confirmed
    2026-04-22), but the creator's own first comment ranks top of
    thread by default in most viewing contexts, and manual pinning
    from mobile YouTube Studio is 2 taps. So this function composes
    the copy; the uploader posts it; RJM pins it later if desired.

    Spotify-first CTA (2026-04-22): the first line is a Spotify link,
    nothing else. North Star is 1M Spotify monthly listeners —
    every touchpoint from long-form video funnels directly to
    Spotify so the algorithm picks up real listener intent signals
    (save, follow, playlist-add) on the platform that matters.

    Kept short (2–4 lines) — long CTA comments get collapsed behind
    "Read more." Scripture anchor is optional; we only surface it
    when the track has one.
    """
    # `smart_link` is Spotify-direct (see registry.build_smart_link
    # default mode as of 2026-04-22). Use it verbatim as the Spotify
    # click target.
    lines = [
        f"\U0001f3a7 Listen on Spotify: {smart_link}",
        f"New visualizers every Tue/Thu/Sun 21:00 UTC \u2014 subscribe so the algorithm feeds you the next one.",
    ]
    if scripture_anchor:
        lines.append(f"Anchor for this one: {scripture_anchor}.")
    lines.append("Which track hits hardest? Drop a timestamp below.")
    return "\n".join(lines)


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

    # Captured by the image-gen phase's CTR gate when it regenerates the
    # thumbnail keyframe. The render phase then applies this override to
    # the story it re-fetches from the global catalog so the pre-roll
    # morphs from the ACTUAL shipped thumbnail (and the morph chain's
    # start frame stays untouched). None = no override, use story as-is.
    effective_thumbnail_keyframe = None

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
                #
                # Story resolution (story_generator.resolve_or_generate_story):
                #   1. motion.TRACK_STORIES (hand-written, highest quality)
                #   2. JSON cache from a prior autonomous generation
                #   3. Fresh Claude-CLI generation from the scripture anchor
                #   4. motion.DEFAULT_STORY (last-resort fallback)
                from content_engine.youtube_longform import story_generator
                from content_engine.audio_engine import (
                    SCRIPTURE_ANCHORS as _SC, TRACK_BPMS as _BPM,
                    TRACK_LANGUAGES as _LANG,
                )
                _key = req.track_title.lower().strip()
                story = story_generator.resolve_or_generate_story(
                    track_title=req.track_title,
                    scripture_anchor=_SC.get(_key, ""),
                    bpm=_BPM.get(_key, 130),
                    language=_LANG.get(_key, "en"),
                )
                thumb_kf = story.thumbnail_keyframe or story.keyframes[0]
                logger.info(
                    "Motion path: thumbnail keyframe = '%s' (%s)",
                    thumb_kf.keyframe_id,
                    "dedicated" if story.thumbnail_keyframe else "first in chain",
                )
                # CTR gate — generate, score vs 528-thumbnail viral corpus +
                # composition baselines, regenerate once on hard-fail with
                # diagnosis-suggested prompt additions. The effective_kf we
                # get back is used downstream by generate_morph_loop so the
                # morph chain starts from the ACTUAL approved first frame
                # (preserves the thumbnail promise).
                rendered, effective_thumb_kf = _gated_motion_thumbnail(
                    thumb_kf=thumb_kf,
                    prompt=prompt,
                    track_title=req.track_title,
                    bpm=prompt.bpm,
                )
                # Capture the effective thumbnail keyframe — when the gate
                # regenerated, this is the new (better-CTR) version. The
                # render phase below will inject it into the fresh story
                # fetch so the preroll morphs from the SHIPPED thumbnail.
                # When gate didn't regen (effective == original), this
                # still gets set — downstream code treats "already matches"
                # as a no-op merge.
                effective_thumbnail_keyframe = effective_thumb_kf
                # Title the clean base for YouTube. Kling keeps using the
                # clean base (via the keyframe cache keyed on still_prompt),
                # so morph interpolation stays stable — no text bleed-thru.
                titled_path = _title_thumbnail_for_youtube(
                    clean_base_path=rendered.local_path,
                    track_title=req.track_title,
                    scripture_anchor=prompt.scripture_anchor,
                )
                result.hero_image = ImageAsset(
                    role="hero",
                    local_path=titled_path,
                    remote_url=rendered.remote_url,   # remote_url still points at the clean base (ok — YT uses local file for thumbnails.set)
                    width=cfg.HERO_WIDTH,
                    height=cfg.HERO_HEIGHT,
                    prompt_used=effective_thumb_kf.still_prompt,
                    variant_index=0,
                )
            else:
                # Stills-only publish: use prompt_builder's hero slot,
                # gated by the same pre-publish CTR check.
                clean_hero = _gated_stills_hero(
                    prompt=prompt,
                    track_title=req.track_title,
                    bpm=prompt.bpm,
                )
                # Title for YouTube. The stills path uses this SAME image
                # as the full-track video background in render.composite(),
                # so we have two choices:
                #   (a) ship the titled one everywhere (background shows
                #       track title for the whole video — fine for stills
                #       since there's no morphing to worry about)
                #   (b) keep the video clean, title only the thumbnail
                # We pick (a) because stills-only publishes tend to be
                # lower-stakes single-image visualizers where a burnt-in
                # title aids the passive listener.
                titled_path = _title_thumbnail_for_youtube(
                    clean_base_path=clean_hero.local_path,
                    track_title=req.track_title,
                    scripture_anchor=prompt.scripture_anchor,
                )
                result.hero_image = ImageAsset(
                    role="hero",
                    local_path=titled_path,
                    remote_url=clean_hero.remote_url,
                    width=clean_hero.width,
                    height=clean_hero.height,
                    prompt_used=clean_hero.prompt_used,
                    variant_index=0,
                )

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

                # If the pre-publish CTR gate regenerated the thumbnail
                # keyframe upstream, promote it into the story now so the
                # pre-roll shot morphs from the SHIPPED thumbnail, not the
                # canonical-story thumbnail that was rejected by the gate.
                # The morph chain keyframes[] are untouched — that's by
                # design: the chain loops independently of the thumbnail.
                if effective_thumbnail_keyframe is not None:
                    from dataclasses import replace as _replace
                    story = _replace(story, thumbnail_keyframe=effective_thumbnail_keyframe)
                    logger.info(
                        "Motion render: applied gated thumbnail override "
                        "'%s' → story.thumbnail_keyframe",
                        effective_thumbnail_keyframe.keyframe_id,
                    )

                logger.info(
                    "Motion path: story '%s' with %d keyframes / %d morphs",
                    story.story_id, len(story.keyframes), len(story.morphs),
                )
                keyframes, morph_clips = motion_mod.generate_morph_loop(
                    story_id=story.story_id,
                    track_prompt=prompt,
                )

                # Pre-roll hook (2026-04-22 retention-lift feature) —
                # 5s kinetic Kling clip from thumbnail keyframe into the
                # first chain keyframe. Prepended to the Shotstack timeline
                # so the audio plays under an arresting opening shot. Big
                # expected APV lift (Jericho was at 12%). +$0.42/publish.
                preroll_clip = motion_mod.generate_preroll_clip(
                    story=story,
                    track_prompt=prompt,
                    rendered_kfs=keyframes,
                )

                result.video = motion_mod.stitch_full_track(
                    clips=morph_clips,
                    audio_url=audio_public,
                    target_duration_s=duration,
                    output_label=f"{output_label}_motion",
                    shotstack_env="v1",   # PAYG, no watermark
                    preroll_clip=preroll_clip,
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
            pinned_cta = _compose_pinned_cta(
                track_title=req.track_title,
                smart_link=result.smart_link or "",
                scripture_anchor=prompt.scripture_anchor,
            )
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
                pinned_comment=pinned_cta,
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
    # Collect all morph_*.mp4 (Kling O3 keyframe-morph format, current pipeline)
    # and motion_*.mp4 (Kling 2.1 ambient-motion format, legacy clips) so the
    # Shorts pool includes every generated asset regardless of naming convention.
    candidate_globs = ["morph_*.mp4", "motion_*.mp4"]
    for glob_pattern in candidate_globs:
        for src in src_dir.glob(glob_pattern):
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
