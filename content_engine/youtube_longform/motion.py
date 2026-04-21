"""
motion.py — Seamless psychedelic morph loops via Kling O3.

The visual vocabulary we're cloning: Omiki & Vegas — "Wana" (1M views in
3 months, Israeli psytrance). Key observation from the reference video:
every transition is a CONTINUOUS MORPH, not a hard cut. Bird zooms out
to temple → temple zooms in to dance scene → dance morphs into warrior →
camera enters warrior's mouth → emerges back at the wide overview. The
7-minute video is one hypnotic chain that loops back on itself.

Architecture:
  1. Generate N "keyframes" — single-subject hero compositions via Flux
     2 Pro /edit with the proven-viral reference corpus.
  2. For each adjacent pair (k_i, k_{i+1}), generate a 10s Kling O3 morph
     clip with image_url=k_i and end_image_url=k_{i+1}.
  3. The FINAL clip wraps back: image_url=k_N, end_image_url=k_1.
  4. Stitch all clips linearly via Shotstack → one seamless MP4.

Result: an N×10s chain that plays forever without a visible seam. A
3-keyframe test produces 30 seconds of unique motion looped ~14× across
a 7-min track — the loop point is invisible because every frame is
mid-morph.

Cost:
  Kling O3 Standard 10s clip:  $0.84 (no audio)
  Flux 2 Pro /edit keyframe:   $0.075
  3-keyframe test:             3×$0.075 + 3×$0.84 = ~$2.75
  8-keyframe publish:          ~$7.20 (minus Shotstack full-track render)

CRITICAL CONSTRAINT: No ffmpeg/PyAV/OpenCV/MoviePy. Every bit of encoding
happens off-machine — Flux (keyframes), Kling (morphs), Shotstack (stitch).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import requests

from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform.image_gen import (
    ImageGenError,
    _download,
    _fal_client,
    _generate_one,
    _slug,
)
from content_engine.youtube_longform.types import TrackPrompt

logger = logging.getLogger(__name__)


class MotionError(Exception):
    """Raised when keyframe generation or Kling morph fails irrecoverably."""


# ─── Data types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Keyframe:
    """A single compositional anchor in the morph chain.

    Each keyframe becomes one still via Flux 2 Pro /edit. The still is
    then used as both the end-frame of the previous morph clip AND the
    start-frame of the next morph clip — which is why the morphs look
    continuous (they share a frame at the seam).
    """
    keyframe_id:  str                  # "rjm_warrior", "rjm_priestess", ...
    still_prompt: str                  # Full Flux 2 Pro /edit prompt


@dataclass(frozen=True)
class MorphClip:
    """A 10s Kling O3 morph between two adjacent keyframes."""
    clip_id:       str                 # "rjm_warrior__to__rjm_priestess"
    from_kf_id:    str
    to_kf_id:      str
    motion_prompt: str                 # What transforms, and how (camera move, dissolve)
    duration_s:    int = cfg.KLING_O3_CLIP_SECONDS


@dataclass(frozen=True)
class MorphStory:
    """A full keyframe-chain story.

    The chain is the source of truth — list of morphs in playback order.
    The LAST morph's to_kf_id must equal the FIRST morph's from_kf_id so
    that the chain loops back seamlessly (same frame).

    keyframes is the UNIQUE set of compositional anchors referenced by
    the chain. A keyframe can appear multiple times in the chain — useful
    for multi-chapter stories where a "home" character anchors each
    chapter (e.g. warrior → priestess → temple → warrior → wall → shofar
    → warrior, which loops back to warrior cleanly).
    """
    story_id:   str
    keyframes:  list[Keyframe]
    morphs:     list[MorphClip]

    def __post_init__(self):
        if not self.keyframes:
            raise ValueError("MorphStory must have at least one keyframe")
        if not self.morphs:
            raise ValueError("MorphStory must have at least one morph")
        # Every morph must reference valid keyframes
        kf_ids = {k.keyframe_id for k in self.keyframes}
        for m in self.morphs:
            if m.from_kf_id not in kf_ids or m.to_kf_id not in kf_ids:
                raise ValueError(
                    f"Morph '{m.clip_id}' references unknown keyframes: "
                    f"from='{m.from_kf_id}' to='{m.to_kf_id}'. "
                    f"Known: {sorted(kf_ids)}"
                )
        # Loop closure: last morph's to must equal first morph's from
        first_from = self.morphs[0].from_kf_id
        last_to    = self.morphs[-1].to_kf_id
        if first_from != last_to:
            raise ValueError(
                f"Chain does not close: first morph starts from "
                f"'{first_from}' but last morph ends at '{last_to}'. "
                f"For a seamless loop, the final morph's to_kf_id must "
                f"equal the first morph's from_kf_id."
            )


@dataclass(frozen=True)
class RenderedKeyframe:
    """A generated keyframe on disk with both local path and a public URL."""
    keyframe_id:   str
    local_path:    Path
    remote_url:    str


@dataclass(frozen=True)
class RenderedMorphClip:
    """A Kling O3 morph clip on disk."""
    clip_id:       str
    from_kf_id:    str
    to_kf_id:      str
    local_path:    Path
    remote_url:    str
    duration_s:    int
    width:         int
    height:        int


# ─── Hebrew/Bedouin universal story (the RJM brand translation) ──────────────
# 3 keyframes + 3 morph clips = 30s seamless loop. Fits all 140+ BPM psy
# tracks. Variants for 128-136 organic house will swap keyframe compositions
# for softer / more meditative subjects.
#
# Design rules baked into every keyframe (matches BRAND_VOICE.md + locked
# tokens + Omiki "Wana" formula after aesthetic translation):
#   · SINGLE centered subject — no action, no processions
#   · Direct piercing eye contact with camera  (where subject is human)
#   · Heroic cinematic framing (shallow DoF, moody rim lighting)
#   · Locked palette: terracotta + indigo night + gold + ochre
#   · Hebrew / Bedouin / Abrahamic-nomadic vocabulary — NOT Islamic,
#     NOT Mesoamerican, NOT Hindu, NOT New Age cosmic
#   · Character persistency within a scene — same face the whole clip

#
# ─── v2 prompts — positive-only discipline ───────────────────────────────────
# RJM critique (2026-04-21 post-first-test):
#   "focus on avoiding using negative prompts, but mainly on positive prompts
#    that should do the trick much better."
#
# Insight: diffusion models respond better to positive specificity than
# negatives. A "no modern clothing, no T-shirt" clause still puts T-shirt in
# the attention space. Better to describe the ancient period so fully that
# wrong things have no space to emerge.
#
# Every keyframe below is anchored to:
#   · Iron Age Levant c. 1400-1200 BCE (Joshua-era biblical period)
#   · Specific fabric language (rough-spun linen, hand-woven wool, bronze fibula)
#   · Specific jewelry language (hammered silver, lapis-lazuli, carnelian, copper)
#   · Specific motif anchor (Paleo-Canaanite, Iron Age Levantine, ancient Hebrew)
#   · Locked palette tokens (terracotta, indigo-night, gold, ochre)
#
# Every motion prompt below uses drone-camera language (orbiting, arcing,
# sweeping, tunneling, ascending) — continuous cinematic movement, never
# static. This is what RJM called out as the Omiki 'Wana' signature.

RJM_HERO_STORY: MorphStory = MorphStory(
    story_id="rjm_hero_hebrew_bedouin",
    keyframes=[
        Keyframe(
            keyframe_id="rjm_warrior",
            still_prompt=(
                "Close-up heroic cinematic portrait of an Iron Age Hebrew "
                "nomad warrior in his late twenties, piercing direct eye "
                "contact with the camera, deep brown eyes, strong bearded "
                "jaw with thick dark beard, weathered sun-darkened skin of a "
                "desert traveler, fine Paleo-Canaanite geometric face paint "
                "in gold and ochre painted in clean straight vertical lines "
                "beneath each eye, a hammered silver forehead diadem set "
                "with polished lapis-lazuli and turquoise in ancient "
                "Levantine geometric pattern, dark curled hair with small "
                "hand-wrought silver beads woven through the locks, layered "
                "necklaces of hand-wrought silver links interwoven with "
                "lapis-lazuli and carnelian and polished copper beads, a "
                "hand-woven dark indigo wool robe with gold-thread trim at "
                "the shoulder, a thick woolen cloak pinned at the collarbone "
                "with a bronze fibula brooch, the ancient desert at dusk "
                "behind him with warm gold dust sparks drifting through the "
                "air, shallow depth of field, heroic low-angle cinematic "
                "framing, terracotta and indigo-night and gold palette, "
                "Iron Age Levant c. 1200 BCE biblical period, photographic "
                "realism, 16:9, --style raw"
            ),
        ),
        Keyframe(
            keyframe_id="rjm_priestess",
            still_prompt=(
                "Close-up heroic cinematic portrait of an Iron Age Hebrew "
                "priestess in her early thirties, piercing direct eye "
                "contact with the camera, deep brown eyes rimmed with dark "
                "kohl, strong jawline, warm olive skin, fine gold face paint "
                "in clean straight horizontal lines beneath each eye "
                "(ancient Levantine ceremonial motif), an ornate hammered "
                "silver-and-turquoise headpiece cascading hand-wrought "
                "silver chains across her forehead and temples, a delicate "
                "silver nose-ring connected by a silver chain to her ear, "
                "multiple layered silver necklaces set with polished "
                "lapis-lazuli and carnelian and amethyst, a hand-woven "
                "black wool veil with gold-thread embroidery draped softly "
                "over her head and shoulders, an ornate ephod-style "
                "vestment of hand-woven gold and terracotta linen across "
                "her chest with braided linen shoulder straps, warm "
                "firelight side-lighting carving her features from the "
                "right, fine warm gold sparks drifting through the dark "
                "air behind her, shallow depth of field, heroic cinematic "
                "framing, terracotta and indigo-night and gold palette, "
                "Iron Age Levant c. 1200 BCE biblical period, photographic "
                "realism, 16:9, --style raw"
            ),
        ),
        Keyframe(
            keyframe_id="rjm_temple",
            still_prompt=(
                "Wide cinematic establishing shot of an ancient Iron Age "
                "Abrahamic desert temple at night — a stepped stone "
                "ziggurat of weathered sandstone reminiscent of the great "
                "temples of Ur-of-the-Chaldees, hand-carved ancient "
                "Levantine geometric patterns traced along each tier, the "
                "massive structure rising into a deep indigo starry desert "
                "sky with a distant crescent moon, a single tall bronze "
                "altar brazier burning at the base with warm golden flames "
                "licking upward, a small hooded figure in a dark indigo "
                "wool robe silhouetted beside the altar facing the flame, "
                "warm gold sparks drifting upward into the night sky, "
                "distant desert mountains in indigo-night shadow beyond, "
                "deep atmospheric haze, cinematic wide heroic framing from "
                "below, terracotta and indigo-night and gold palette, Iron "
                "Age Mesopotamia / Canaan c. 1400 BCE, photographic "
                "realism, 16:9, --style raw"
            ),
        ),
    ],
    morphs=[
        MorphClip(
            clip_id="rjm_warrior__to__rjm_priestess",
            from_kf_id="rjm_warrior",
            to_kf_id="rjm_priestess",
            motion_prompt=(
                "Sweeping cinematic drone camera orbiting slowly around the "
                "warrior's head as his eyes gently close, warm gold dust "
                "swirls outward from his silver diadem and fills the frame, "
                "the drone continues forward through the gold dust cloud in "
                "a smooth curving aerial arc, and as the dust clears the "
                "drone swings into a slow orbital arc around the priestess "
                "whose eyes slowly open to meet the camera. Continuous "
                "cinematic drone motion throughout, never static, always "
                "flying."
            ),
        ),
        MorphClip(
            clip_id="rjm_priestess__to__rjm_temple",
            from_kf_id="rjm_priestess",
            to_kf_id="rjm_temple",
            motion_prompt=(
                "Cinematic drone camera orbiting the priestess while slowly "
                "pushing into her right pupil, the drone tunneling through "
                "darkness with perpetual subtle arcing motion, a single "
                "distant warm flame appears in the black, the drone "
                "continues pulling backward in a wide arcing aerial sweep, "
                "the flame resolving into the bronze altar fire of an "
                "ancient Iron Age ziggurat at night, the full wide temple "
                "scene coming into view as stars emerge in the deep indigo "
                "sky. Continuous cinematic drone motion throughout, never "
                "static."
            ),
        ),
        MorphClip(
            clip_id="rjm_temple__to__rjm_warrior",
            from_kf_id="rjm_temple",
            to_kf_id="rjm_warrior",
            motion_prompt=(
                "Sweeping cinematic drone camera pushing in toward the "
                "bronze altar flame at the base of the ancient ziggurat "
                "while orbiting around the flame, the flame growing until "
                "it fills the frame and reshaping into the hammered "
                "silver-and-turquoise geometric ornament on the warrior's "
                "diadem, the drone pulls back with sweeping orbital motion "
                "revealing the warrior's forehead and then his full face, "
                "his eyes opening to meet the camera. Continuous cinematic "
                "drone motion throughout, seamlessly closing the hypnotic "
                "loop."
            ),
        ),
    ],
)


# ─── Jericho extended story — 5 keyframes, 6 morphs, two chapters ────────────
# Reuses the 3 cached keyframes + 3 cached morphs from RJM_HERO_STORY and
# extends the chain with Joshua-6-specific Jericho content using strong
# drone-camera motion prompts. Chain layout:
#
#   Chapter A (cached, existing):
#     warrior → priestess → temple → warrior
#   Chapter B (new drone motion):
#     warrior → jericho_wall → jericho_shofar → warrior   (closes loop)
#
# warrior appears three times in the chain (positions 0, 3, 6). Because
# every "warrior" in the chain is the SAME keyframe JPG passed to Kling O3
# as start/end frame, all three appearances are frame-identical — so the
# chain loops back on itself invisibly. This also means Chapter B starts
# and ends on the exact warrior frame that Chapter A starts and ends on —
# giving us a continuous hypnotic loop with two distinct story chapters.
#
# Cost delta over RJM_HERO_STORY:
#   + 2 new keyframes  × $0.075 = $0.15
#   + 3 new morphs     × $0.84  = $2.52
#   = $2.67 to upgrade a 30s loop to a 60s chain (existing clips re-used).

_JERICHO_WALL_KEYFRAME = Keyframe(
    keyframe_id="rjm_jericho_wall",
    still_prompt=(
        "Wide cinematic heroic establishing shot of the ancient Iron Age "
        "walls of Jericho at dawn — a massive weathered sandstone "
        "fortification towering into a warm golden-amber sky, hand-carved "
        "Paleo-Canaanite geometric patterns running along the upper "
        "courses, fine stonemason chisel marks on every block, the base "
        "meeting a sand-swept desert floor with warm gold dust drifting "
        "upward across the weathered stone face, distant desert mountains "
        "in indigo-night shadow beyond the wall, deep atmospheric warmth, "
        "cinematic ultra-wide heroic framing from below, terracotta and "
        "indigo-night and gold palette, Canaan period c. 1400 BCE, "
        "photographic realism, 16:9, --style raw"
    ),
)

_JERICHO_SHOFAR_KEYFRAME = Keyframe(
    keyframe_id="rjm_jericho_shofar",
    still_prompt=(
        "Close-up heroic cinematic portrait of an Iron Age Hebrew priest "
        "shofar-blower in his fifties, weathered Mediterranean face with "
        "a long gray-streaked beard, piercing direct eye contact with the "
        "camera lifted just above the horn's curve, a large curved ram's "
        "horn shofar raised firmly to his lips with both sun-aged hands "
        "gripping the horn, he wears a rough-spun ancient linen tunic "
        "ankle-length in natural undyed ivory, a thick woolen priestly "
        "shoulder cloak hand-woven in earth-tone stripes of indigo and "
        "terracotta and gold thread, tzitzit prayer tassels knotted at "
        "each of the four corners of his tallit-style outer mantle, a "
        "braided leather rope belt tied at the waist, bare weathered "
        "sun-darkened forearms, a hand-woven indigo-and-gold linen head "
        "wrap patterned with ancient Levantine geometric motif, "
        "hand-wrought silver beaded necklaces strung with lapis-lazuli "
        "and carnelian, a massive ancient sandstone wall rising in soft "
        "warm blur behind him, golden hour side-lighting carving his "
        "profile from the right, fine warm gold dust drifting through "
        "the air, shallow depth of field, heroic cinematic framing, "
        "terracotta and indigo-night and gold palette, Iron Age Levant "
        "c. 1400 BCE Canaan period biblical priesthood, photographic "
        "realism, 16:9, --style raw"
    ),
)

# Morphs with heavy drone-camera language — this is the primary fix from
# the first test. Every clip describes continuous orbital/arcing drone
# motion, never a static frame.
_JERICHO_MORPHS_DRONE: list[MorphClip] = [
    MorphClip(
        clip_id="rjm_warrior__to__rjm_jericho_wall",
        from_kf_id="rjm_warrior",
        to_kf_id="rjm_jericho_wall",
        motion_prompt=(
            "Sweeping cinematic drone camera orbiting around the warrior's "
            "head, pulling back and arcing upward, the silver-and-turquoise "
            "diadem ornament dissolves into drifting warm gold dust that "
            "fills the frame, the drone ascends rapidly through the gold "
            "dust cloud and emerges above a vast desert at dawn revealing "
            "a massive ancient Abrahamic stone wall towering into the "
            "golden sky. Continuous drone orbital motion throughout, "
            "aerial arc, never static, always flying."
        ),
    ),
    MorphClip(
        clip_id="rjm_jericho_wall__to__rjm_jericho_shofar",
        from_kf_id="rjm_jericho_wall",
        to_kf_id="rjm_jericho_shofar",
        motion_prompt=(
            "Slow cinematic drone camera orbiting the base of the massive "
            "ancient stone wall, arcing sideways across the sunlit sandstone "
            "surface and then descending in a curving swoop, coming around "
            "to reveal a weathered bearded shofar player standing at the "
            "wall's foot lifting a large ram's horn toward his lips, golden "
            "hour backlight streaming past him, warm dust drifting. "
            "Continuous drone orbital motion throughout, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_jericho_shofar__to__rjm_warrior",
        from_kf_id="rjm_jericho_shofar",
        to_kf_id="rjm_warrior",
        motion_prompt=(
            "Cinematic drone camera arcing around the shofar player's head "
            "as he blows the ram's horn, a warm pulse of golden light "
            "rippling outward from the horn's mouth, the drone follows "
            "the light wave backward through the desert air, the golden "
            "light concentrates and resolves into the silver-and-turquoise "
            "diadem ornament on a young Hebrew warrior's forehead, drone "
            "pulls back with sweeping orbital motion revealing the "
            "warrior's full face as his eyes meet the camera. Continuous "
            "cinematic drone motion throughout, closes the hypnotic loop."
        ),
    ),
]

JERICHO_EXTENDED_STORY: MorphStory = MorphStory(
    story_id="jericho_joshua6_extended",
    keyframes=[
        *RJM_HERO_STORY.keyframes,          # warrior, priestess, temple  (cached)
        _JERICHO_WALL_KEYFRAME,             # NEW
        _JERICHO_SHOFAR_KEYFRAME,           # NEW
    ],
    morphs=[
        # Chapter A — cached, from the first test ($0 additional)
        RJM_HERO_STORY.morphs[0],           # warrior → priestess
        RJM_HERO_STORY.morphs[1],           # priestess → temple
        RJM_HERO_STORY.morphs[2],           # temple → warrior
        # Chapter B — new, drone-camera ($2.52 additional)
        _JERICHO_MORPHS_DRONE[0],           # warrior → jericho_wall
        _JERICHO_MORPHS_DRONE[1],           # jericho_wall → jericho_shofar
        _JERICHO_MORPHS_DRONE[2],           # jericho_shofar → warrior (loop close)
    ],
)


# ─── Stories + per-track routing ─────────────────────────────────────────────
# STORIES is the named registry — any story can be referenced by ID from the
# CLI (`test_morph_loop.py --story <id>`) or from publisher.
#
# TRACK_STORIES maps lowercase track title → story. The publisher consults
# this when req.motion=True and falls back to DEFAULT_STORY if no per-track
# story exists. Add a new entry here every time a track gets its own
# scripture-anchored narrative.
#
# DEFAULT_STORY is the universal RJM hero-portrait chain — usable for any
# track while its dedicated story is still being written. Always safe as
# a fallback.

STORIES: dict[str, MorphStory] = {
    "rjm_hero_hebrew_bedouin":   RJM_HERO_STORY,          # 30s, 3 keyframes
    "jericho_joshua6_extended":  JERICHO_EXTENDED_STORY,  # 60s, 5 keyframes
}

DEFAULT_STORY: MorphStory = RJM_HERO_STORY

# Per-track narrative override. Lowercase keys. Scripture-anchored where
# applicable. Add new tracks as their stories are written — Selah, Halleluyah,
# Kadosh, Not By Might, etc. each get their own dedicated chain.
TRACK_STORIES: dict[str, MorphStory] = {
    "jericho":   JERICHO_EXTENDED_STORY,
    # Add future tracks here as their stories are written.
}


def story_for_track(track_title: str) -> MorphStory:
    """Return the MorphStory for a track, or the default fallback."""
    return TRACK_STORIES.get(track_title.lower().strip(), DEFAULT_STORY)


# ─── Kling O3 client (the morph engine) ──────────────────────────────────────

def _animate_morph(
    from_frame_url: str,
    to_frame_url:   str,
    motion_prompt:  str,
    duration:       int = cfg.KLING_O3_CLIP_SECONDS,
    aspect_ratio:   str = cfg.KLING_ASPECT_16_9,
) -> str:
    """
    Submit one Kling O3 morph job with start+end frame conditioning.
    Returns the rendered MP4 URL.
    """
    if duration not in (5, 10):
        raise MotionError(f"Kling O3 only supports 5 or 10 second clips; got {duration}")

    client = _fal_client()

    arguments = {
        "prompt":         motion_prompt,
        "image_url":      from_frame_url,
        "end_image_url":  to_frame_url,
        "duration":       str(duration),
        "aspect_ratio":   aspect_ratio,
    }

    logger.info(
        "Kling O3 morph | %ds | %s | '%s'",
        duration, aspect_ratio, motion_prompt[:90],
    )

    try:
        result = client.subscribe(
            cfg.FAL_KLING_O3_STANDARD_EP,
            arguments=arguments,
            with_logs=False,
        )
    except Exception as e:
        raise MotionError(f"Kling O3 subscribe failed: {e}") from e

    if not isinstance(result, dict):
        raise MotionError(f"Kling O3 returned non-dict: {result!r}")
    video = result.get("video")
    if not video or not isinstance(video, dict):
        raise MotionError(f"Kling O3 response missing video: {result!r}")
    url = video.get("url")
    if not url:
        raise MotionError(f"Kling O3 video entry missing url: {video!r}")
    return url


# ─── Keyframe generation (Flux 2 Pro /edit) ──────────────────────────────────

def _generate_keyframe(
    kf:              Keyframe,
    track_prompt:    TrackPrompt,
    use_references:  bool = True,
) -> RenderedKeyframe:
    """
    Produce one keyframe still (Flux 2 Pro /edit with reference corpus).
    Uploads to Cloudinary so Kling O3 can consume a public URL.
    """
    from content_engine.youtube_longform import reference_pool
    from content_engine.youtube_longform.render import upload_image_for_render

    slug = _slug(kf.keyframe_id)
    digest = hashlib.sha256(kf.still_prompt.encode()).hexdigest()[:8]
    local_path = cfg.IMAGE_DIR / f"kf_{slug}_{digest}.jpg"

    # Reference resolution from the proven-viral bucket (same family as track)
    reference_urls: list[str] = []
    if use_references:
        refs = reference_pool.pick_references(
            track_prompt.genre_family,
            seed=int(digest, 16),
        )
        for ref_path in refs:
            try:
                reference_urls.append(upload_image_for_render(
                    ref_path, public_id=f"ref_{ref_path.stem}",
                ))
            except Exception as e:
                logger.warning("Skipping ref %s: %s", ref_path.name, e)

    if local_path.exists():
        logger.info("Keyframe cached: %s", local_path.name)
        remote_url = upload_image_for_render(
            local_path, public_id=f"kf_{slug}_{digest}",
        )
        return RenderedKeyframe(
            keyframe_id=kf.keyframe_id,
            local_path=local_path,
            remote_url=remote_url,
        )

    t0 = time.time()
    url = _generate_one(
        prompt=kf.still_prompt,
        negative_prompt=track_prompt.flux_negative,
        width=cfg.HERO_WIDTH,
        height=cfg.HERO_HEIGHT,
        seed=None,
        reference_urls=reference_urls or None,
    )
    _download(url, local_path)
    logger.info(
        "Keyframe %s generated in %.1fs (%d refs) → %s",
        kf.keyframe_id, time.time() - t0, len(reference_urls), local_path.name,
    )

    remote_url = upload_image_for_render(
        local_path, public_id=f"kf_{slug}_{digest}",
    )
    return RenderedKeyframe(
        keyframe_id=kf.keyframe_id,
        local_path=local_path,
        remote_url=remote_url,
    )


# ─── Orchestration ───────────────────────────────────────────────────────────

def generate_morph_loop(
    story_id:      str,
    track_prompt:  TrackPrompt,
    duration_s:    int = cfg.KLING_O3_CLIP_SECONDS,
    aspect_ratio:  str = cfg.KLING_ASPECT_16_9,
) -> tuple[list[RenderedKeyframe], list[RenderedMorphClip]]:
    """
    Run a full MorphStory: keyframes → morph chain → downloadable clips.

    Returns (keyframes, morph_clips). Clips are in chain order; the last
    clip wraps keyframe_N → keyframe_1 so concatenating them plays as a
    seamless loop.
    """
    cfg.ensure_workspace()
    if story_id not in STORIES:
        raise MotionError(
            f"Unknown story '{story_id}'. Available: {list(STORIES)}"
        )
    story = STORIES[story_id]

    # 1. Generate keyframes
    logger.info("─" * 66)
    logger.info("Phase 1/2 — Keyframes  (%d × Flux 2 Pro /edit)", len(story.keyframes))
    logger.info("─" * 66)
    rendered_kfs: list[RenderedKeyframe] = []
    for kf in story.keyframes:
        rendered_kfs.append(_generate_keyframe(kf, track_prompt))

    kf_by_id = {rk.keyframe_id: rk for rk in rendered_kfs}

    # 2. Generate morph chain
    logger.info("─" * 66)
    logger.info("Phase 2/2 — Morph chain  (%d × Kling O3 @ %ds)", len(story.morphs), duration_s)
    logger.info("─" * 66)
    rendered_clips: list[RenderedMorphClip] = []
    for i, morph in enumerate(story.morphs, start=1):
        logger.info("Morph %d/%d: %s → %s", i, len(story.morphs), morph.from_kf_id, morph.to_kf_id)

        from_rk = kf_by_id.get(morph.from_kf_id)
        to_rk   = kf_by_id.get(morph.to_kf_id)
        if not (from_rk and to_rk):
            raise MotionError(
                f"Morph '{morph.clip_id}' references unknown keyframes: "
                f"from={morph.from_kf_id} to={morph.to_kf_id}"
            )

        clip_digest = hashlib.sha256(
            (
                f"{morph.motion_prompt}::"
                f"{from_rk.remote_url}::{to_rk.remote_url}::"
                f"{duration_s}::{aspect_ratio}"
            ).encode()
        ).hexdigest()[:8]
        clip_path = cfg.VIDEO_DIR / f"morph_{_slug(morph.clip_id)}_{clip_digest}.mp4"

        if clip_path.exists():
            logger.info("Morph clip cached: %s", clip_path.name)
            rendered_clips.append(RenderedMorphClip(
                clip_id=morph.clip_id,
                from_kf_id=morph.from_kf_id,
                to_kf_id=morph.to_kf_id,
                local_path=clip_path,
                remote_url="",
                duration_s=duration_s,
                width=cfg.HERO_WIDTH,
                height=cfg.HERO_HEIGHT,
            ))
            continue

        t0 = time.time()
        video_url = _animate_morph(
            from_frame_url=from_rk.remote_url,
            to_frame_url=to_rk.remote_url,
            motion_prompt=morph.motion_prompt,
            duration=duration_s,
            aspect_ratio=aspect_ratio,
        )
        _download(video_url, clip_path)
        logger.info(
            "Morph %s rendered in %.1fs → %s",
            morph.clip_id, time.time() - t0, clip_path.name,
        )

        rendered_clips.append(RenderedMorphClip(
            clip_id=morph.clip_id,
            from_kf_id=morph.from_kf_id,
            to_kf_id=morph.to_kf_id,
            local_path=clip_path,
            remote_url=video_url,
            duration_s=duration_s,
            width=cfg.HERO_WIDTH,
            height=cfg.HERO_HEIGHT,
        ))

    return rendered_kfs, rendered_clips


# ─── Shotstack stitch — concat the morph chain ───────────────────────────────

def stitch_loop(
    clips:         list[RenderedMorphClip],
    output_label:  str,
    audio_url:     Optional[str] = None,
    loop_count:    int = 1,
) -> Path:
    """
    Stitch the morph chain into one MP4. The chain already loops back
    seamlessly (last clip's end = first clip's start) so we concatenate
    head-to-tail with NO crossfade, and can optionally repeat the whole
    chain `loop_count` times for a longer preview.

    No crossfade because:
      · End frame of clip N literally equals start frame of clip N+1
        (both are the same keyframe image passed to Kling O3)
      · A crossfade would blur that seamless join
    """
    if not cfg.SHOTSTACK_API_KEY:
        raise MotionError("SHOTSTACK_API_KEY not set — cannot stitch preview.")
    if not clips:
        raise MotionError("Cannot stitch empty clip list.")

    base_url = f"https://api.shotstack.io/edit/{cfg.SHOTSTACK_ENV}"
    headers = {
        "x-api-key":    cfg.SHOTSTACK_API_KEY,
        "Content-Type": "application/json",
    }

    cursor = 0.0
    shotstack_clips = []
    for loop_i in range(loop_count):
        for c in clips:
            if not c.remote_url:
                raise MotionError(
                    f"Clip {c.clip_id} has no remote_url — re-upload cached "
                    f"clips to Cloudinary before stitching."
                )
            shotstack_clips.append({
                "asset":  {"type": "video", "src": c.remote_url},
                "start":  round(cursor, 3),
                "length": c.duration_s,
                "fit":    "cover",
            })
            cursor += c.duration_s

    timeline = {
        "timeline": {
            "tracks": [{"clips": shotstack_clips}],
        },
        "output": {
            "format":     "mp4",
            "resolution": "1080",
            "fps":        cfg.VIDEO_FPS,
        },
    }
    if audio_url:
        timeline["timeline"]["soundtrack"] = {
            "src": audio_url,
            "effect": "fadeInFadeOut",
        }

    logger.info(
        "Shotstack stitch %d clips × %d loops → %s (~%.1fs total)",
        len(clips), loop_count, output_label, cursor,
    )
    r = requests.post(f"{base_url}/render", headers=headers, json=timeline, timeout=60)
    if not r.ok:
        raise MotionError(
            f"Shotstack stitch {r.status_code}: {r.text[:500]}\n"
            f"Payload: {json.dumps(timeline)[:400]}"
        )
    job_id = r.json()["response"]["id"]
    logger.info("Shotstack job id: %s", job_id)

    deadline = time.time() + cfg.SHOTSTACK_TIMEOUT
    final_url = None
    while time.time() < deadline:
        time.sleep(5)
        s = requests.get(f"{base_url}/render/{job_id}", headers=headers, timeout=30)
        s.raise_for_status()
        status = s.json()["response"]["status"]
        logger.info("Shotstack status: %s", status)
        if status == "done":
            final_url = s.json()["response"]["url"]
            break
        if status == "failed":
            raise MotionError(f"Shotstack stitch failed: {s.json()!r}")
    if not final_url:
        raise MotionError(f"Shotstack stitch timed out after {cfg.SHOTSTACK_TIMEOUT}s")

    local = cfg.VIDEO_DIR / f"{output_label}.mp4"
    with requests.get(final_url, stream=True, timeout=cfg.SHOTSTACK_TIMEOUT) as resp:
        resp.raise_for_status()
        with open(local, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    logger.info("Preview written: %s", local)
    return local


# ─── Full-track render (the publisher-path stitch) ───────────────────────────

def stitch_full_track(
    clips:              list[RenderedMorphClip],
    audio_url:          str,
    target_duration_s:  int,
    output_label:       str,
    shotstack_env:      str = "v1",     # v1 = production PAYG, no watermark
) -> "RenderedVideo":
    """
    Render the final publish MP4: motion chain looped to cover `target_duration_s`
    with `audio_url` as soundtrack, at 1080p via Shotstack PAYG.

    Clip-cycling logic:
      For target_duration_s = 312 (Jericho 5:12) and a 6-clip 60s chain,
      we emit 31 clip slots cycling [c0..c5, c0..c5, …]. The first 30 slots
      are full 10s; the final slot is truncated to 2s so the video ends
      exactly at 312s matching the audio.

    Uses Shotstack v1 (production) by default — the stage env has
    watermarks + time caps and is unsuitable for real publishes. Stage
    stays free for our test_morph_loop.py runs.

    Returns a RenderedVideo dataclass so it slots into existing publisher
    flow (same shape as render.composite()).
    """
    # Defer import to keep render dep optional in test-only paths
    from content_engine.youtube_longform.types import RenderedVideo

    if not cfg.SHOTSTACK_API_KEY:
        raise MotionError("SHOTSTACK_API_KEY not set — cannot render.")
    if not clips:
        raise MotionError("Cannot render empty clip list.")
    if target_duration_s <= 0:
        raise MotionError(f"target_duration_s must be positive, got {target_duration_s}")

    # Rehydrate cached clips if they lack remote_url (happens when pulled
    # from Path cache with no fresh Kling URL). Upload each to Cloudinary.
    if any(not c.remote_url for c in clips):
        from content_engine.youtube_longform.render import _upload_to_cloudinary
        rehydrated: list[RenderedMorphClip] = []
        for c in clips:
            if c.remote_url:
                rehydrated.append(c)
                continue
            url = _upload_to_cloudinary(
                c.local_path,
                resource_type="video",
                public_id=f"motion_{c.clip_id}",
            )
            rehydrated.append(RenderedMorphClip(
                clip_id=c.clip_id,
                from_kf_id=c.from_kf_id,
                to_kf_id=c.to_kf_id,
                local_path=c.local_path,
                remote_url=url,
                duration_s=c.duration_s,
                width=c.width,
                height=c.height,
            ))
        clips = rehydrated

    base_url = f"https://api.shotstack.io/edit/{shotstack_env}"
    headers = {
        "x-api-key":    cfg.SHOTSTACK_API_KEY,
        "Content-Type": "application/json",
    }

    # Build the cycled clip sequence. Most clips are full-duration; the
    # LAST clip may be truncated so total = target_duration_s exactly.
    clip_len = clips[0].duration_s
    shotstack_clips = []
    cursor = 0.0
    i = 0
    while cursor < target_duration_s:
        src = clips[i % len(clips)].remote_url
        remaining = target_duration_s - cursor
        length = clip_len if remaining >= clip_len else remaining
        shotstack_clips.append({
            "asset":  {"type": "video", "src": src},
            "start":  round(cursor, 3),
            "length": round(length, 3),
            "fit":    "cover",
        })
        cursor += length
        i += 1

    timeline = {
        "timeline": {
            "soundtrack": {
                "src":    audio_url,
                "effect": "fadeInFadeOut",
            },
            "tracks": [{"clips": shotstack_clips}],
        },
        "output": {
            "format":     "mp4",
            "resolution": "1080",
            "fps":        cfg.VIDEO_FPS,
        },
    }

    logger.info(
        "Shotstack %s | full-track render %s | %d clips → %ds",
        shotstack_env, output_label, len(shotstack_clips), target_duration_s,
    )
    r = requests.post(f"{base_url}/render", headers=headers, json=timeline, timeout=60)
    if not r.ok:
        raise MotionError(
            f"Shotstack {shotstack_env} render {r.status_code}: "
            f"{r.text[:600]}\nPayload preview: {json.dumps(timeline)[:500]}"
        )
    job_id = r.json()["response"]["id"]
    logger.info("Shotstack job id: %s", job_id)

    deadline = time.time() + cfg.SHOTSTACK_TIMEOUT
    final_url = None
    while time.time() < deadline:
        time.sleep(5)
        s = requests.get(f"{base_url}/render/{job_id}", headers=headers, timeout=30)
        s.raise_for_status()
        status = s.json()["response"]["status"]
        logger.info("Shotstack status: %s", status)
        if status == "done":
            final_url = s.json()["response"]["url"]
            break
        if status == "failed":
            raise MotionError(f"Shotstack render failed: {s.json()!r}")
    if not final_url:
        raise MotionError(f"Shotstack render timed out after {cfg.SHOTSTACK_TIMEOUT}s")

    local_path = cfg.VIDEO_DIR / f"{output_label}.mp4"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(final_url, stream=True, timeout=cfg.SHOTSTACK_TIMEOUT) as resp:
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    logger.info("Full-track render written: %s", local_path)

    return RenderedVideo(
        local_path=local_path,
        remote_url=final_url,
        width=cfg.VIDEO_WIDTH,
        height=cfg.VIDEO_HEIGHT,
        duration=target_duration_s,
        codec=cfg.VIDEO_CODEC,
        audio_codec=cfg.AUDIO_CODEC,
    )


# ─── Cost helpers ────────────────────────────────────────────────────────────

def estimate_cost_usd(
    keyframe_count: int,
    duration_s:     int = cfg.KLING_O3_CLIP_SECONDS,
) -> float:
    """
    Cost for one full morph-loop generation:
      keyframes × $0.075 (Flux 2 Pro /edit) +
      morph_clips × $0.084/s (Kling O3 Standard, no audio)
    Number of morph clips equals number of keyframes (last wraps back).
    """
    flux_cost  = 0.075 * keyframe_count
    kling_cost = 0.084 * duration_s * keyframe_count
    return round(flux_cost + kling_cost, 4)
