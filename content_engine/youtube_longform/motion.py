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

    thumbnail_keyframe is a SEPARATE keyframe used only as the YouTube
    thumbnail. Optional — if None, the first in-chain keyframe is used.
    Define a dedicated thumbnail when you want CTR-optimized composition
    (tighter crop, stronger contrast, ultra-close face) that doesn't need
    to morph into another frame. The thumbnail is rendered via the same
    Flux 2 Pro /edit path so it picks up the proven-viral reference pool
    but it's NEVER fed into Kling O3 — it lives outside the morph chain.
    """
    story_id:            str
    keyframes:           list[Keyframe]
    morphs:              list[MorphClip]
    thumbnail_keyframe:  Optional[Keyframe] = None

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


# ─── TWO VISUAL SYSTEMS (by BPM tier) ────────────────────────────────────────
# Observation (RJM post-Jericho-launch, 12h stats: 3.0% CTR / 80% Suggested
# Videos / 0:37 APV): Café de Anatolia 128-138 BPM audiences and Astrix 140+
# BPM audiences expect visibly different visualizers. Organic-house viewers
# want daytime / contemplative / solo-figure. Psytrance viewers want night /
# kinetic / crowd-ritual. Mixing the two signals tells YouTube the channel
# is unclustered — dilutes the clustering that's already working.
#
# ─── STRATEGIC CALL: morph-chain on BOTH systems (2026-04-22) ────────────────
# The 20-video viral visualizer research (docs/viral_visualizer_analysis.md)
# found that top organic-house visualizers (Keinemusik "Move", Ben Böhmer
# "Rust") are near-static single-anchor covers — the "video" is one
# hand-drawn graphic looped for 6 minutes. Typography does the virality work.
#
# WE ARE DELIBERATELY NOT FOLLOWING THAT FORMULA on System A.
#
# Rationale (RJM decision): Keinemusik-minimalism works because Keinemusik
# has the brand equity to make one flat image feel essential. Holy Rave has
# zero channel equity today. If we ship static covers in the organic bucket
# we disappear into the pile of producer-tier channels doing the same thing.
# Going full cinematic morph-chain on organic IS the differentiator — we're
# the psy-production-values organic-house channel, not the 57th lofi-aesthetic
# one.
#
# Trade-off accepted: ~7× higher per-publish cost on organic ($10.80 vs
# ~$1.50 static) in exchange for a distinct visual signature YouTube's
# algorithm and human viewers both recognize. At 3×/week osso-so cadence
# and ~12 organic publishes/year, the extra spend is ~$110/mo — the price
# of "stand out."
#
# What the two systems DIFFER on (even though both use the same morph-chain
# pipeline): subject vocabulary, palette, camera energy, setting, time-of-day,
# emotional register. Same pipeline, different DNA per keyframe. Keeps the
# Cafe-de-Anatolia vs Astrix audience signal distinct without collapsing
# into either one's own production convention.
#
# Every new MorphStory leans its keyframes into ONE of the two systems:
#
# ╔═══════════════════════════════════════════════════════════════════════════
# ║ SYSTEM A — ORGANIC HOUSE (128-138 BPM)
# ║   Selah, Renamed, Fire In Our Hands, Living Water, He Is The Light, Abba,
# ║   Side By Side, Step By Step, Rise Up My Love, How Good And Pleasant
# ╠═══════════════════════════════════════════════════════════════════════════
# ║  Time of day:   dawn / golden hour / dusk (daytime light dominant)
# ║  Palette:       warm amber + terracotta + soft gold + pale indigo + ochre
# ║  Subject:       solo contemplative figure(s); veiled portraits; handpan or
# ║                 oud players; elders in prayer; lone watchers on ridges
# ║  Setting:       oasis, cedar grove, cliffside vigil, dawn caravan, prayer
# ║                 tent interior lit by oil lamp
# ║  Camera:        floating drone, slow cinematic glide, gentle orbital arcs
# ║  Lighting:      soft warm key, gradient shadows, ambient softness
# ║  Texture:       linen, wool, hand-woven, still water, sand, silver, lapis
# ║  Register:      contemplation, stillness, reverence, ceremony
# ║  Reference DNA: Café de Anatolia / Sol Selectas / Sabo / Bedouin /
# ║                 Monolink / Be Svendsen / Anjunadeep / All Day I Dream
# ╚═══════════════════════════════════════════════════════════════════════════
#
# ╔═══════════════════════════════════════════════════════════════════════════
# ║ SYSTEM B — TRIBAL PSYTRANCE (140+ BPM)
# ║   Jericho (hybrid), Halleluyah, Kadosh, Shema, Not By Might, Kavod,
# ║   Ruach (intense variants)
# ╠═══════════════════════════════════════════════════════════════════════════
# ║  Time of day:   deep night — torchlight, firelight, moonlight (NO daytime)
# ║  Palette:       indigo-black + ember-orange + amber-crimson + silver accent
# ║                 (NO daytime warm gold; keep gold as fire/ember light only)
# ║  Subject:       warriors / priests mid-ritual; ecstatic crowds; fire-bearers;
# ║                 sound-wave moments; architectural macro (cracking stone)
# ║  Setting:       fire circle, night altar, temple at night, ziggurat under
# ║                 stars, cave refuge at midnight, macro sacred stone detail,
# ║                 spiral-dance aerial shots
# ║  Camera:        sweeping arcs, fast orbital zooms, kinetic aerial swoops,
# ║                 sharp tracking — matches BPM intensity
# ║  Lighting:      hard amber key from below (firelight), deep obsidian
# ║                 shadows on opposite side, rim-lit silhouettes, high contrast
# ║  Texture:       bronze, silver, flame, embers, cracked stone, sparks, ash
# ║  Register:      celebration, trance, ecstasy, confrontation, sacred violence
# ║  Reference DNA: Astrix / Ace Ventura / Vini Vici / Symphonix / Vertex /
# ║                 Ranji / Tristan / Infected Mushroom / Iboga Records
# ╚═══════════════════════════════════════════════════════════════════════════
#
# Per-story variety STILL matters WITHIN a system so that two System-B tracks
# don't both look like "ecstatic fire circle." Pick different vocabulary
# inside the system — e.g. Jericho = warrior + stone walls + golden dust at
# night, Halleluyah = fire circle + dancers + embers, Kadosh = sacred
# architecture + white-linen priests + altar flames, Shema = cloaked-figure
# silhouettes + Paleo-Hebrew-light + geometric pattern, etc.
#
# Convenience helper:

def system_for_bpm(bpm: int) -> str:
    """Return 'A' (organic 128-138) or 'B' (psytrance 140+) given a BPM."""
    return "B" if bpm >= 139 else "A"


# ─── SELAH — Psalm 46, 130 BPM meditative, 9-keyframe 90s no-repeat ──────────
# Selah is the contemplative counterpart to Jericho's ecstatic storm. Where
# Jericho revisits the warrior as its anchor (3x in the chain), Selah has
# NO repeats — all 9 keyframes are unique, giving 90s of fresh content per
# loop cycle. The camera moves slower (130 BPM vs 140) to match the track's
# meditative pace.
#
# Psalm 46:10 — "Be still, and know that I am God."
# Visual arc: handpan → water → oud → cave → ridge → prayer → caravan →
#             scroll → cedars → (loop back to handpan)
#
# Cost: 9 keyframes + 1 thumbnail = 10 × $0.075 = $0.75
#       9 morphs × $0.84 = $7.56
#       Shotstack 6:14 full-track render ≈ $2.50
#       Total per publish: ≈ $10.81

_SELAH_KEYFRAMES: list[Keyframe] = [
    Keyframe(
        keyframe_id="rjm_selah_handpan",
        still_prompt=(
            "Close-up heroic cinematic portrait of an Iron Age Hebrew "
            "contemplative in his forties seated cross-legged on a desert "
            "stone at golden hour, a hand-hammered steel handpan resting "
            "between his knees, his weathered olive-skinned hands poised "
            "just above the sound-dimples mid-strike, eyes softly closed "
            "in quiet meditation, warm terracotta dust drifting slowly "
            "through the air around him, he wears a simple rough-spun "
            "ivory linen tunic with a hand-woven indigo shoulder sash "
            "striped in gold thread, a braided leather rope belt, a "
            "single hand-wrought silver pendant on a leather cord at his "
            "neck, one small silver earring in the lit ear, dark curled "
            "hair with a single indigo ribbon woven through, the distant "
            "sandstone cliffs of ancient Canaan softly blurred behind, "
            "warm golden-hour side light carving his face from the right, "
            "shallow depth of field, heroic contemplative framing, "
            "terracotta and indigo-night and gold palette, Iron Age "
            "Levant c. 1000 BCE biblical contemplative, photographic "
            "realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_selah_still_water",
        still_prompt=(
            "Wide cinematic establishing shot of an ancient Iron Age "
            "desert oasis at pre-dawn, a perfectly still pool of dark "
            "water mirror-flat reflecting the surrounding weathered "
            "sandstone cliffs and the fading stars of a deep indigo sky, "
            "tall reeds along the pool edges catching the first warm "
            "light of the coming dawn, fine warm mist rising from the "
            "water surface, no human figures, no animals, deep "
            "atmospheric stillness, cinematic wide heroic framing from "
            "low angle, terracotta and indigo-night and gold palette, "
            "Iron Age Canaan c. 1000 BCE, photographic realism, 16:9, "
            "--style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_selah_oud_player",
        still_prompt=(
            "Close-up heroic cinematic portrait of an Iron Age Hebrew "
            "elder oud player in his sixties, a wooden oud with a deep "
            "bulbous bowl-shaped back held close against his chest, his "
            "weathered olive-skinned hands gently plucking the double "
            "courses of strings with a polished-wood plectrum, eyes "
            "softly lowered to the instrument in quiet focus, long "
            "gray-streaked beard, he wears a rough-spun ivory linen "
            "tunic beneath a hand-woven dark indigo woolen mantle with "
            "gold-thread trim at the edges, a hand-woven head wrap in "
            "indigo and gold patterned with ancient Levantine geometric "
            "motif, warm single-candle light from the right side of the "
            "frame catching the oud's varnish and the strings, deep "
            "black shadow on the left, shallow depth of field, heroic "
            "contemplative framing, terracotta and indigo-night and gold "
            "palette, Iron Age Levant c. 1000 BCE biblical elder, "
            "photographic realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_selah_refuge_cave",
        still_prompt=(
            "Wide cinematic establishing shot of an ancient Iron Age "
            "stone cliff refuge at night — a hand-carved cave entrance "
            "cut into a massive weathered sandstone face, a single warm "
            "oil-lamp flame burning from inside the cave glowing against "
            "the dark interior, a cloaked contemplative figure in dark "
            "indigo wool robe silhouetted at the cave mouth with their "
            "back to the camera facing the flame, a deep indigo starry "
            "desert night sky above, fine cool mist drifting across the "
            "cliff face, distant sandstone ridges in indigo-night shadow, "
            "deep atmospheric silence, cinematic ultra-wide heroic "
            "framing from below, terracotta and indigo-night and gold "
            "palette, Iron Age Canaan c. 1000 BCE Psalm 46 refuge "
            "imagery, photographic realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_selah_mountain_ridge",
        still_prompt=(
            "Wide cinematic heroic establishing shot of a lone hooded "
            "contemplative figure in a dark indigo wool robe standing in "
            "silhouette on a high ancient desert ridge at first dawn, "
            "cool blue-gold mist rising slowly from the valley floor far "
            "below, a distant ancient sandstone city visible in the "
            "valley, the sky warming from deep indigo-night at the top "
            "of frame to golden-amber at the horizon, the figure stands "
            "still facing the sunrise with arms relaxed at their sides, "
            "deep atmospheric silence, cinematic ultra-wide heroic "
            "framing from low angle, terracotta and indigo-night and "
            "gold palette, Iron Age Canaan c. 1000 BCE biblical vigil, "
            "photographic realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_selah_prayer_lamp",
        still_prompt=(
            "Close-up cinematic heroic portrait of an Iron Age Hebrew "
            "contemplative elder in his seventies kneeling in the hushed "
            "warm interior of a hand-woven goat-hair prayer tent at "
            "night, a small seven-branched bronze oil lampstand burning "
            "with seven warm flames directly in front of him casting "
            "long amber light across his weathered face, eyes softly "
            "closed in prayer, both hands lifted palms-upward toward the "
            "lamps, long gray beard, a rough-spun ivory linen tunic "
            "beneath a hand-woven indigo-and-gold striped tallit-style "
            "prayer shawl draped over his head and shoulders with tzitzit "
            "tassels knotted at each of the four corners, deep black "
            "shadows behind him, shallow depth of field, heroic "
            "contemplative framing, terracotta and indigo-night and gold "
            "palette, Iron Age Levant c. 1000 BCE biblical priest at "
            "prayer, photographic realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_selah_dawn_caravan",
        still_prompt=(
            "Wide cinematic heroic establishing shot of a small Iron Age "
            "nomadic caravan paused at dawn beside a tall weathered "
            "desert standing stone, three silhouetted hooded figures in "
            "dark indigo wool robes standing beside two resting camels "
            "laden with hand-woven saddle bags and tent-folds in "
            "terracotta and gold stripes, warm pink-gold dawn mist "
            "rolling in from the left across the desert floor, distant "
            "sandstone mountains in cool indigo-night shadow, the sky "
            "warming from indigo above to amber at the horizon, deep "
            "atmospheric quiet, cinematic ultra-wide heroic framing from "
            "low angle, terracotta and indigo-night and gold palette, "
            "Iron Age Canaan c. 1000 BCE Abrahamic-nomadic caravan, "
            "photographic realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_selah_psalms_scroll",
        still_prompt=(
            "Macro cinematic close-up from above of an unrolled ancient "
            "parchment scroll lying on a low dark wooden table, deeply "
            "inked Paleo-Hebrew calligraphic script flowing across the "
            "parchment in vertical lines of rich black iron-gall ink, "
            "subtle red accent marks at verse divisions, the parchment's "
            "weathered fiber texture and faint age-stains visible under "
            "a single warm oil-lamp flame from the top-right corner of "
            "the frame, two weathered olive-skinned hands resting at the "
            "lower edge of the scroll with fingertips barely touching "
            "the parchment, deep black shadow in the rest of the frame, "
            "terracotta and indigo-night and gold palette, Iron Age "
            "Levant c. 1000 BCE Psalm scribe, photographic realism, "
            "16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_selah_cedar_grove",
        still_prompt=(
            "Wide cinematic establishing shot of a grove of ancient tall "
            "Lebanon cedar trees at warm dusk, their dark trunks and "
            "deep-green spread branches silhouetted against a warming "
            "amber-gold dusk sky, shafts of last golden light passing "
            "between the trunks, fine cool mist drifting low along the "
            "forest floor, soft bed of pine needles and fallen cedar "
            "cones catching faint gold highlights, a single small "
            "hooded figure in a dark indigo wool robe walking slowly "
            "among the trees toward the camera left side of the frame, "
            "deep atmospheric hush, cinematic ultra-wide heroic framing "
            "from low angle, terracotta and indigo-night and gold "
            "palette, Iron Age Lebanon c. 1000 BCE cedar grove, "
            "photographic realism, 16:9, --style raw"
        ),
    ),
]

_SELAH_MORPHS: list[MorphClip] = [
    MorphClip(
        clip_id="rjm_selah_handpan__to__still_water",
        from_kf_id="rjm_selah_handpan",
        to_kf_id="rjm_selah_still_water",
        motion_prompt=(
            "Cinematic drone camera orbiting slowly around the seated "
            "handpan player as his hands meet the metal bowl in a gentle "
            "strike, soft warm golden resonance waves rippling outward "
            "from the handpan surface, the drone descends gracefully "
            "through the rippling waves into a perfectly still pre-dawn "
            "oasis pool, the widening ripples slowly settling back into "
            "a mirror-flat water surface reflecting distant sandstone "
            "cliffs. Continuous contemplative drone motion throughout, "
            "slow meditative pace at 130 BPM, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_selah_still_water__to__oud_player",
        from_kf_id="rjm_selah_still_water",
        to_kf_id="rjm_selah_oud_player",
        motion_prompt=(
            "Slow cinematic drone camera gliding across the mirror-still "
            "pre-dawn water surface, a single silver ripple begins to "
            "spread outward from the reflected center, the drone rises "
            "and arcs forward as the expanding ripple resolves into the "
            "deep curved bowl-shaped back of a wooden oud held close "
            "against a bearded Hebrew elder's chest, warm candlelight "
            "catching the oud's polished varnish. Continuous "
            "contemplative drone motion, slow meditative pace, never "
            "static."
        ),
    ),
    MorphClip(
        clip_id="rjm_selah_oud_player__to__refuge_cave",
        from_kf_id="rjm_selah_oud_player",
        to_kf_id="rjm_selah_refuge_cave",
        motion_prompt=(
            "Cinematic drone camera orbiting the oud player while "
            "pushing slowly toward the circular soundhole at the oud's "
            "face, the dark soundhole growing larger until it fills the "
            "frame in deep black, the drone continues gliding forward "
            "and the darkness resolves into the hand-carved stone mouth "
            "of a desert cliff refuge at night, a single warm oil-lamp "
            "flame glowing at the cave entrance. Continuous contemplative "
            "drone motion, slow meditative pace, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_selah_refuge_cave__to__mountain_ridge",
        from_kf_id="rjm_selah_refuge_cave",
        to_kf_id="rjm_selah_mountain_ridge",
        motion_prompt=(
            "Slow cinematic drone camera rising out of the cave entrance "
            "and arcing upward through the cool indigo night air, the "
            "single warm oil-lamp flame shrinking to become a distant "
            "warm dawn sun on the horizon, the drone continues climbing "
            "in a wide sweeping arc until a lone hooded contemplative "
            "figure emerges standing in silhouette on a high desert "
            "ridge far below, pink-gold mist rising from the valley. "
            "Continuous contemplative drone motion, slow meditative "
            "pace, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_selah_mountain_ridge__to__prayer_lamp",
        from_kf_id="rjm_selah_mountain_ridge",
        to_kf_id="rjm_selah_prayer_lamp",
        motion_prompt=(
            "Cinematic drone camera arcing slowly around the lone ridge "
            "figure, then pushing gently forward into the folds of their "
            "dark indigo woolen cloak, the fabric filling the frame in "
            "deep amber-shadowed weave, the drone continues forward "
            "passing through the weave and emerges inside the hushed "
            "warm interior of a goat-hair prayer tent where a weathered "
            "Hebrew elder kneels in prayer by a seven-branched bronze "
            "lampstand. Continuous contemplative drone motion, slow "
            "meditative pace, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_selah_prayer_lamp__to__dawn_caravan",
        from_kf_id="rjm_selah_prayer_lamp",
        to_kf_id="rjm_selah_dawn_caravan",
        motion_prompt=(
            "Slow cinematic drone camera pulling back from the seven "
            "flames of the bronze lampstand as the flames grow tall and "
            "blend into the warm pink-gold dawn sky outside, the drone "
            "continues backward passing through the open flap of the "
            "prayer tent and emerges high above a small nomadic caravan "
            "of three hooded figures paused beside a weathered standing "
            "stone with two resting camels, pink-gold mist rolling in. "
            "Continuous contemplative drone motion, slow meditative "
            "pace, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_selah_dawn_caravan__to__psalms_scroll",
        from_kf_id="rjm_selah_dawn_caravan",
        to_kf_id="rjm_selah_psalms_scroll",
        motion_prompt=(
            "Cinematic drone camera descending slowly toward one of the "
            "seated caravan figures who is holding a small hand-woven "
            "leather satchel at their side, the drone enters the "
            "satchel's dark opening and emerges looking straight down "
            "over an unrolled ancient parchment scroll lying flat on a "
            "low dark wooden table, warm oil-lamp light from the corner "
            "illuminating vertical lines of Paleo-Hebrew calligraphy. "
            "Continuous contemplative drone motion, slow meditative "
            "pace, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_selah_psalms_scroll__to__cedar_grove",
        from_kf_id="rjm_selah_psalms_scroll",
        to_kf_id="rjm_selah_cedar_grove",
        motion_prompt=(
            "Slow cinematic drone camera hovering directly above the "
            "unrolled scroll as the black iron-gall ink strokes begin to "
            "soften and rearrange themselves into vertical silhouettes, "
            "the ink lines lifting upward from the parchment and "
            "resolving into the tall dark trunks of ancient Lebanon "
            "cedar trees against a warm amber-gold dusk sky, the drone "
            "rises and glides forward through the misty cedar grove. "
            "Continuous contemplative drone motion, slow meditative "
            "pace, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_selah_cedar_grove__to__handpan",
        from_kf_id="rjm_selah_cedar_grove",
        to_kf_id="rjm_selah_handpan",
        motion_prompt=(
            "Cinematic drone camera emerging slowly from between the "
            "tall silhouetted cedar trunks into an open forest clearing, "
            "the warm dusk light gradually deepening back into golden "
            "hour, the drone descends in a gentle orbital arc and "
            "resolves around the seated handpan player beneath one of "
            "the cedars, his weathered hands resting on the hand-"
            "hammered steel handpan in quiet meditation as the drone "
            "completes its orbit. Continuous contemplative drone motion, "
            "seamlessly closing the hypnotic loop."
        ),
    ),
]

# Dedicated thumbnail keyframe — tighter crop, ultra-close, CTR-optimized.
# This is NOT in the morph chain; it lives outside, purely for YouTube.
# Small-format YouTube thumbnails need: (1) single clear subject, (2) face
# occupying the majority of the frame, (3) direct piercing eye contact,
# (4) high contrast, (5) saturated earth-palette tones against black.
_SELAH_THUMBNAIL_KEYFRAME = Keyframe(
    keyframe_id="rjm_selah_thumbnail",
    still_prompt=(
        "Ultra-close cinematic thumbnail portrait of an Iron Age Hebrew "
        "desert contemplative elder in his sixties, piercing direct eye "
        "contact with the camera, weathered bearded face with every line "
        "and pore visible, deep-set dark brown eyes reflecting warm "
        "candlelight from the right side of the frame, long "
        "gray-streaked beard, a hand-woven indigo-and-gold striped head "
        "wrap framing his brow patterned with ancient Levantine "
        "geometric motif, a single hand-wrought silver pendant on a "
        "dark leather cord visible at the collarbone, warm single-candle "
        "light from the right carving half his face in rich golden "
        "tones, deep obsidian black shadow on the opposite side "
        "creating dramatic high-contrast split lighting, the face "
        "occupies 70 percent of the frame, shallow depth of field, "
        "ultra-saturated earth-gold tones against deep indigo-night "
        "background, Iron Age Levant c. 1000 BCE biblical "
        "contemplative, photographic realism, 16:9, --style raw"
    ),
)

SELAH_STORY: MorphStory = MorphStory(
    story_id="selah_psalm46_contemplative",
    keyframes=_SELAH_KEYFRAMES,
    morphs=_SELAH_MORPHS,
    thumbnail_keyframe=_SELAH_THUMBNAIL_KEYFRAME,
)

# Register SELAH_STORY in the by-ID lookup. STORIES is defined earlier in the
# file (before this definition) so we add to it here. Any future track story
# defined after the initial STORIES block should do the same — add one line
# registering itself by story_id.
STORIES[SELAH_STORY.story_id] = SELAH_STORY


# ─── STYLE VARIETY DOCTRINE ──────────────────────────────────────────────────
# Every new track story MUST visually diverge from the previously-shipped ones
# to prevent channel monotony. Every story picks a distinct combination from
# the matrix below — think of them as "style DNA" dimensions:
#
#   PERIOD / ERA
#     · iron_age_levant_c1200      (Jericho: warrior-era Canaan)
#     · first_temple_c1000          (Kadosh: Solomon's temple, sacred)
#     · bronze_age_mesopotamia      (Selah: contemplative oasis culture)
#     · exodus_wilderness_c1450     (Exodus-family tracks: caravan/tent)
#     · second_temple_intertest.    (On All Flesh / Ruach: prophetic)
#
#   PALETTE-KEY
#     · golden_hour_warm            (Jericho)
#     · pre_dawn_mist               (Selah — cool + gold)
#     · fiery_ecstatic              (Halleluyah — amber/red/black-night)
#     · white_silver_sacred         (Kadosh — white linen, silver, indigo)
#     · sky_wind_breath             (Ruach — pale blue + cloud white)
#     · crimson_supplication        (Have Mercy On Me — blood red + gold)
#
#   SUBJECT-FOCUS
#     · character_heroic_portrait   (Jericho warrior/priestess close-ups)
#     · contemplative_solo          (Selah handpan/oud/elder meditative)
#     · ecstatic_crowd_ritual       (Halleluyah dancers, fire circle)
#     · sacred_architecture         (Kadosh temple interior, menorah, altar)
#     · prophetic_vision            (Ruach breath/wind/spirit imagery)
#     · single_lament               (Have Mercy On Me solo kneeling figure)
#
#   CAMERA-ENERGY (matched to BPM tier)
#     · floating_slow      — 126 and below (meditative)
#     · flowing_medium     — 127-138 (processional/gathering)
#     · sweeping_ecstatic  — 139+ (ecstatic psytrance; fast arcs, particle bursts)
#
# Two stories must not share more than 2 of the 4 dimensions. Jericho
# (iron_age + golden_hour + character_heroic + sweeping_ecstatic) vs Halleluyah
# (second_temple + fiery_ecstatic + ecstatic_crowd_ritual + sweeping_ecstatic)
# share only "sweeping_ecstatic" — good separation.
#
# When adding a new track, pick the combination that differs most from what's
# already published recently. The weekly thumbnail-learning report will flag
# when the corpus-similarity scores start clustering (channel drift warning).

# ─── HALLELUYAH — 140 BPM tribal psytrance, ecstatic crowd ritual ────────────
# Deliberate divergence from Jericho: celebration vs confrontation. No walls,
# no warrior/priestess duo, no cool indigo-dusk. Instead: ecstatic fire-circle,
# spinning drum ritual, the hot saturated amber-red of night ceremony, the
# aerial geometry of spiral dance. This is the "release" to Jericho's "siege."

_HALLELUYAH_KEYFRAMES: list[Keyframe] = [
    Keyframe(
        keyframe_id="rjm_halleluyah_fire_dancer",
        still_prompt=(
            "Close-up heroic cinematic portrait of an Iron Age Hebrew "
            "ecstatic female dancer in her late twenties mid-spin around a "
            "desert bonfire, face tilted upward toward the night sky with "
            "eyes softly closed in ecstatic praise, wild dark curled hair "
            "whipping outward from the spin, layered silver coin necklaces "
            "and silver bangle bracelets catching the firelight, a "
            "hand-woven crimson-and-gold shawl trailing through the air "
            "behind her, warm bronze sparks and glowing embers flying "
            "through the frame, a deep-red-and-black starry desert night "
            "behind her, harsh amber fire-lit side-lighting from below "
            "the frame, deep obsidian shadows, high contrast, shallow "
            "depth of field, saturated amber-crimson-black palette, Iron "
            "Age Levant ceremonial fire-dance c. 1000 BCE, photographic "
            "realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_halleluyah_drum_circle",
        still_prompt=(
            "Wide cinematic heroic establishing shot of an Iron Age Hebrew "
            "drum circle of seven hand-drummers seated around a roaring "
            "central desert bonfire at deep night, each drummer silhouetted "
            "in warm amber backlight striking hand-hewn goatskin drums and "
            "darbukas, flames reaching tall into the black sky with orange "
            "sparks spiraling upward, the ground a deep warm ochre with "
            "dark cast shadows, a ring of torches planted in the sand "
            "beyond the circle, cinematic ultra-wide heroic framing from "
            "slightly above, saturated amber-crimson-black palette, Iron "
            "Age Levant ceremonial drum circle c. 1000 BCE, photographic "
            "realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_halleluyah_torches",
        still_prompt=(
            "Wide cinematic establishing shot of three tall hand-hewn "
            "bronze-banded wooden torches planted vertically in the "
            "desert sand at deep night, each flame reaching over three "
            "meters skyward in an amber-orange column, thick black smoke "
            "trailing upward into a starry indigo-black sky, glowing "
            "embers and ash swirling in the warm updraft around the "
            "torches, the far desert horizon in deep indigo shadow, a "
            "distant glow of a larger bonfire in the background blurred, "
            "cinematic ultra-wide heroic framing from low angle, "
            "saturated amber-crimson-black palette, Iron Age Levant "
            "ceremonial night c. 1000 BCE, photographic realism, 16:9, "
            "--style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_halleluyah_chanting_elder",
        still_prompt=(
            "Close-up heroic cinematic portrait of an Iron Age Hebrew "
            "elder in his sixties standing with both hands raised above "
            "his head in ecstatic praise, his mouth open mid-shout "
            "(halleluyah on his lips), eyes blazing open looking toward "
            "the heavens, a long gray-streaked beard, weathered "
            "sun-darkened skin, wearing a rough-spun ivory linen tunic "
            "and a hand-woven crimson-and-gold striped tallit-style "
            "prayer shawl draped over his shoulders with tzitzit tassels, "
            "hand-wrought silver beaded necklaces, warm amber firelight "
            "up-lighting his face from below in dramatic Rembrandt "
            "lighting, obsidian-black shadows behind him, high contrast, "
            "shallow depth of field, saturated amber-crimson-black "
            "palette, Iron Age Levant c. 1000 BCE ecstatic praise, "
            "photographic realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_halleluyah_spiral_dance",
        still_prompt=(
            "Aerial cinematic establishing shot looking straight down at "
            "a large spiral formation of twenty Iron Age Hebrew ecstatic "
            "dancers moving counter-clockwise around a central desert "
            "bonfire at deep night, each figure silhouetted in the amber "
            "glow with arms lifted, crimson and ochre fabrics trailing, "
            "concentric spiral paths traced in the sand by their feet, "
            "the central fire throwing golden light radially outward, "
            "orange embers drifting in the updraft, the geometry forming "
            "a sacred-dance spiral, cinematic overhead heroic framing, "
            "saturated amber-crimson-black palette, Iron Age Levant "
            "spiral dance c. 1000 BCE, photographic realism, 16:9, "
            "--style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_halleluyah_raised_hand",
        still_prompt=(
            "Macro cinematic close-up of a single weathered Iron Age "
            "Hebrew hand raised high toward a star-filled indigo-black "
            "desert night sky, the hand strong and sun-darkened with "
            "visible knuckles and tendons, three hand-wrought silver "
            "bangles on the wrist, a single polished lapis-lazuli signet "
            "ring on the middle finger, warm amber sparks and bright "
            "embers rising past the fingers toward the stars, a bright "
            "firelight glow from below the frame, black shadow around "
            "the arm, saturated amber-crimson-black palette with "
            "star-silver accent, Iron Age Levant praise gesture c. 1000 "
            "BCE, photographic realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_halleluyah_embers",
        still_prompt=(
            "Macro cinematic abstract shot of a dense cloud of glowing "
            "bronze embers and golden ash rising in a swirling updraft "
            "against a pitch-black night sky, some embers bright white-"
            "hot at their cores trailing amber and crimson halos, "
            "individual embers in sharp focus with shallow depth blurring "
            "the rest into a warm bokeh, no humans or objects visible, "
            "pure elemental texture, saturated amber-crimson-black "
            "palette, ceremonial fire c. 1000 BCE ember cloud, "
            "photographic realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_halleluyah_tambourine",
        still_prompt=(
            "Close-up cinematic heroic shot of a large Iron Age Hebrew "
            "hand-hewn wooden tambourine lifted high overhead with both "
            "hands, struck mid-ring with a burst of sparks from its "
            "impact, eight polished bronze zills catching firelight, "
            "the tambourine frame painted with faded ancient Levantine "
            "geometric patterns in ochre and crimson, the wielder's "
            "silhouetted arms and upper body below the tambourine "
            "catching amber backlight, a blurred ecstatic crowd below, "
            "warm fire-glow from behind, obsidian shadows, saturated "
            "amber-crimson-black palette, Iron Age Levant celebration "
            "c. 1000 BCE, photographic realism, 16:9, --style raw"
        ),
    ),
    Keyframe(
        keyframe_id="rjm_halleluyah_crowd_chant",
        still_prompt=(
            "Wide cinematic heroic establishing shot of a crowd of "
            "thirty silhouetted Iron Age Hebrew celebrants facing a "
            "single torch-bearing priest at the front of the gathering, "
            "all of them with arms raised overhead in unified ecstatic "
            "praise, the torch a bright amber column of flame throwing "
            "golden light across the crowd from behind, a deep starry "
            "black desert night sky overhead, faces lost in silhouette "
            "except rim-lit edges in amber, hands and raised fabrics "
            "catching firelight, thick dust and embers swirling through "
            "the frame, cinematic ultra-wide heroic framing, saturated "
            "amber-crimson-black palette, Iron Age Levant ecstatic "
            "gathering c. 1000 BCE, photographic realism, 16:9, "
            "--style raw"
        ),
    ),
]

_HALLELUYAH_MORPHS: list[MorphClip] = [
    MorphClip(
        clip_id="rjm_halleluyah_fire_dancer__to__drum_circle",
        from_kf_id="rjm_halleluyah_fire_dancer",
        to_kf_id="rjm_halleluyah_drum_circle",
        motion_prompt=(
            "Sweeping cinematic drone camera spinning outward around the "
            "ecstatic fire dancer as her spin accelerates, her trailing "
            "crimson shawl arcing through the frame, the drone pulls "
            "backward in a wide aerial orbit revealing the full ring of "
            "seven hand-drummers seated around the central bonfire, the "
            "flames leaping tall in the deep night. Fast sweeping drone "
            "motion matching 140 BPM ecstatic energy, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_halleluyah_drum_circle__to__torches",
        from_kf_id="rjm_halleluyah_drum_circle",
        to_kf_id="rjm_halleluyah_torches",
        motion_prompt=(
            "Cinematic drone camera rising rapidly through the shower of "
            "rising embers and sparks from the central bonfire, ascending "
            "through swirling warm ash into the black star-filled sky, "
            "then sweeping laterally and descending past three tall "
            "torch flames planted in the sand, the camera arcing in a "
            "wide aerial curve around the torch column. Fast drone "
            "kinetic motion matching 140 BPM ecstatic energy, never "
            "static."
        ),
    ),
    MorphClip(
        clip_id="rjm_halleluyah_torches__to__chanting_elder",
        from_kf_id="rjm_halleluyah_torches",
        to_kf_id="rjm_halleluyah_chanting_elder",
        motion_prompt=(
            "Sweeping cinematic drone camera gliding through the warm "
            "flame of the central torch, the fire filling the frame in "
            "amber-red, then resolving through the flame to reveal the "
            "face of the chanting Hebrew elder directly ahead with his "
            "hands lifted skyward and mouth open mid-shout, the drone "
            "pulls back with orbital motion carving his face in dramatic "
            "firelight. Fast drone motion matching 140 BPM ecstatic "
            "energy, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_halleluyah_chanting_elder__to__spiral_dance",
        from_kf_id="rjm_halleluyah_chanting_elder",
        to_kf_id="rjm_halleluyah_spiral_dance",
        motion_prompt=(
            "Cinematic drone camera pulling back quickly from the "
            "chanting elder, the drone ascending vertically in a smooth "
            "straight rise while rotating ninety degrees to face "
            "straight down, revealing from above a sacred spiral "
            "formation of twenty ecstatic dancers circling a central "
            "bonfire, the aerial geometry clarifying as the drone "
            "climbs. Fast drone motion matching 140 BPM ecstatic "
            "energy, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_halleluyah_spiral_dance__to__raised_hand",
        from_kf_id="rjm_halleluyah_spiral_dance",
        to_kf_id="rjm_halleluyah_raised_hand",
        motion_prompt=(
            "Cinematic drone camera diving rapidly down from the aerial "
            "spiral view toward one specific dancer below, accelerating "
            "into a close-up of that dancer's raised hand, the hand "
            "filling the frame against the starry night sky above, "
            "embers streaming past the fingers. Fast drone dive motion "
            "matching 140 BPM ecstatic energy, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_halleluyah_raised_hand__to__embers",
        from_kf_id="rjm_halleluyah_raised_hand",
        to_kf_id="rjm_halleluyah_embers",
        motion_prompt=(
            "Cinematic drone camera tracking the bright embers rising "
            "past the raised hand upward into the black sky, the hand "
            "falling out of the frame below as the drone follows the "
            "embers higher and higher, the surrounding embers "
            "multiplying into a dense glowing swirling cloud of "
            "elemental fire-light. Fast drone ascent matching 140 BPM "
            "ecstatic energy, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_halleluyah_embers__to__tambourine",
        from_kf_id="rjm_halleluyah_embers",
        to_kf_id="rjm_halleluyah_tambourine",
        motion_prompt=(
            "Cinematic drone camera surging through the ember cloud as "
            "the embers compress and converge into a circular form "
            "directly ahead, the convergence resolving into the polished "
            "bronze zills of a tambourine being struck overhead, the "
            "drone orbits around the tambourine catching the impact "
            "burst of sparks. Fast drone motion matching 140 BPM "
            "ecstatic energy, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_halleluyah_tambourine__to__crowd_chant",
        from_kf_id="rjm_halleluyah_tambourine",
        to_kf_id="rjm_halleluyah_crowd_chant",
        motion_prompt=(
            "Cinematic drone camera pulling back rapidly from the "
            "tambourine while arcing laterally, the tambourine shrinking "
            "in the frame revealing it was held by one figure at the "
            "front of a massive crowd of ecstatic celebrants, the drone "
            "continues pulling back and rising until the whole crowd "
            "and the torch-bearing priest at the front are visible in "
            "a wide ecstatic tableau. Fast drone motion matching 140 "
            "BPM ecstatic energy, never static."
        ),
    ),
    MorphClip(
        clip_id="rjm_halleluyah_crowd_chant__to__fire_dancer",
        from_kf_id="rjm_halleluyah_crowd_chant",
        to_kf_id="rjm_halleluyah_fire_dancer",
        motion_prompt=(
            "Cinematic drone camera diving forward through the ecstatic "
            "crowd toward the torch at the front, then continuing past "
            "the torch flame and resolving into a close orbital shot "
            "around the fire dancer mid-spin, the crimson shawl trailing "
            "through the frame and embers swirling, the drone closes "
            "its orbit completing the hypnotic loop. Fast drone motion "
            "matching 140 BPM ecstatic energy, seamlessly closing the "
            "loop."
        ),
    ),
]

_HALLELUYAH_THUMBNAIL_KEYFRAME = Keyframe(
    keyframe_id="rjm_halleluyah_thumbnail",
    still_prompt=(
        "Ultra-close cinematic thumbnail portrait of an Iron Age Hebrew "
        "ecstatic female priestess-dancer in her late twenties, piercing "
        "direct eye contact with the camera, face tilted slightly "
        "upward with a joyful half-smile of ecstatic praise, deep brown "
        "eyes blazing with reflected firelight, strong cheekbones, warm "
        "olive skin, layered silver coin necklaces catching amber "
        "firelight, three silver bangles on her raised right wrist "
        "visible at the frame edge, wild dark curled hair crowned with "
        "a silver diadem set with crimson carnelian, warm glowing "
        "bronze embers and sparks swirling through the air around her "
        "face, harsh amber fire-lit key-lighting from the right, deep "
        "obsidian-black shadow on the opposite side, face occupies 70 "
        "percent of the frame, extremely high contrast, ultra-saturated "
        "amber-crimson tones against pitch-black background, Iron Age "
        "Levant ecstatic praise c. 1000 BCE, photographic realism, "
        "16:9, --style raw"
    ),
)

HALLELUYAH_STORY: MorphStory = MorphStory(
    story_id="halleluyah_ecstatic_fire_circle",
    keyframes=_HALLELUYAH_KEYFRAMES,
    morphs=_HALLELUYAH_MORPHS,
    thumbnail_keyframe=_HALLELUYAH_THUMBNAIL_KEYFRAME,
)

STORIES[HALLELUYAH_STORY.story_id] = HALLELUYAH_STORY


# Per-track narrative override. Lowercase keys. Scripture-anchored where
# applicable. Add new tracks as their stories are written — Kadosh,
# Not By Might, etc. each get their own dedicated chain with distinct
# style DNA (see "STYLE VARIETY DOCTRINE" above).
TRACK_STORIES: dict[str, MorphStory] = {
    "jericho":    JERICHO_EXTENDED_STORY,       # iron_age + golden_hour + character + ecstatic
    "selah":      SELAH_STORY,                  # bronze_age + pre_dawn_mist + contemplative + slow
    "halleluyah": HALLELUYAH_STORY,             # iron_age + fiery + ecstatic_crowd + ecstatic
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

    # Record the render_id in a persistent log so later cleanup retries
    # are possible even if this process dies. Format: one JSON per line
    # with timestamp, env, render_id, output_label, target_duration_s.
    _log_shotstack_render(job_id, shotstack_env, output_label, target_duration_s)

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

    # Shotstack cleanup protocol (per RJM 2026-04-22):
    #   render → download → VERIFY → delete
    # Only purge the Shotstack-side copy after we've confirmed the local
    # MP4 exists, is non-trivial size, and has a valid MP4 container
    # header. If any check fails we keep the Shotstack copy so re-download
    # is possible, and log the problem loudly.
    if _verify_local_mp4(local_path):
        _delete_shotstack_render_assets(
            render_id=job_id,
            env=shotstack_env,
            api_key=cfg.SHOTSTACK_API_KEY,
        )
    else:
        logger.error(
            "Local MP4 verification FAILED for %s — leaving Shotstack copy "
            "intact so you can re-download manually. Check disk space + "
            "network stability.",
            local_path,
        )

    return RenderedVideo(
        local_path=local_path,
        remote_url=final_url,
        width=cfg.VIDEO_WIDTH,
        height=cfg.VIDEO_HEIGHT,
        duration=target_duration_s,
        codec=cfg.VIDEO_CODEC,
        audio_codec=cfg.AUDIO_CODEC,
    )


SHOTSTACK_RENDER_LOG = cfg.REGISTRY_DIR / "shotstack_renders.jsonl"


def _log_shotstack_render(
    render_id:    str,
    env:          str,
    output_label: str,
    duration_s:   int,
) -> None:
    """
    Append a line to data/youtube_longform/shotstack_renders.jsonl so
    cleanup retries work even if this process dies mid-pipeline.
    Each row: {timestamp, env, render_id, output_label, duration_s, deleted}
    'deleted' starts false and is flipped to true when the asset-delete
    call returns 2xx/404 (scripts/cleanup_shotstack.py can also flip it).
    """
    cfg.ensure_workspace()
    SHOTSTACK_RENDER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(SHOTSTACK_RENDER_LOG, "a") as f:
        f.write(json.dumps({
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "env":          env,
            "render_id":    render_id,
            "output_label": output_label,
            "duration_s":   duration_s,
            "deleted":      False,
        }) + "\n")


def _mark_shotstack_render_deleted(render_id: str) -> None:
    """Mark a render as cleaned up by rewriting the JSONL line's `deleted` flag."""
    if not SHOTSTACK_RENDER_LOG.exists():
        return
    rows: list[dict] = []
    with open(SHOTSTACK_RENDER_LOG) as f:
        for line in f:
            try:
                row = json.loads(line)
                if row.get("render_id") == render_id:
                    row["deleted"] = True
                rows.append(row)
            except json.JSONDecodeError:
                continue
    with open(SHOTSTACK_RENDER_LOG, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _verify_local_mp4(path: Path, min_bytes: int = 1_000_000) -> bool:
    """
    Pre-delete integrity check. Confirms:
      1. file exists
      2. size >= min_bytes (default 1 MB — a completed Selah is ~260 MB,
         partial downloads would be much smaller)
      3. first 12 bytes contain the 'ftyp' MP4 box marker at offset 4
         (every valid ISO-BMFF/MP4 file starts with `<size><ftyp>...`)
    Non-destructive — only reads the first 12 bytes.
    """
    try:
        if not path.exists():
            logger.error("Verify: %s does not exist", path)
            return False
        size = path.stat().st_size
        if size < min_bytes:
            logger.error(
                "Verify: %s is only %d bytes (< %d min) — partial download?",
                path, size, min_bytes,
            )
            return False
        with open(path, "rb") as f:
            head = f.read(12)
        if len(head) < 12 or head[4:8] != b"ftyp":
            logger.error(
                "Verify: %s lacks 'ftyp' MP4 header (got %r) — corrupt?",
                path, head,
            )
            return False
        logger.info("Verify OK: %s (%.1f MB, valid MP4 container)", path, size / 1024 / 1024)
        return True
    except Exception as e:
        logger.error("Verify %s raised: %s", path, e)
        return False


def _delete_shotstack_render_assets(
    render_id: str,
    env: str,
    api_key: str,
) -> int:
    """
    Delete every asset (video + thumbnail + poster) associated with a
    render_id from Shotstack's storage to free the 500 MB free-tier cap.

    Correct API paths (verified 2026-04-22 from Shotstack docs):
      GET    https://api.shotstack.io/serve/{env}/assets/render/{render_id}
             → returns list of asset IDs generated by this render
      DELETE https://api.shotstack.io/serve/{env}/assets/{asset_id}
             → removes the asset

    Returns the number of assets successfully deleted (or already-gone).

    Non-fatal on failure; logs the issue. Called only after local MP4
    is verified complete, so a failed delete means the Shotstack copy
    lingers (auto-expires in 24-72h) but local is safe.
    """
    if not (render_id and api_key):
        return 0

    list_url = f"https://api.shotstack.io/serve/{env}/assets/render/{render_id}"
    try:
        r = requests.get(list_url, headers={"x-api-key": api_key}, timeout=20)
    except Exception as e:
        logger.warning("Shotstack asset-list for render %s failed: %s", render_id, e)
        return 0

    if r.status_code == 404:
        logger.debug("Shotstack assets for render %s already gone (404)", render_id)
        return 0
    if not r.ok:
        logger.warning(
            "Shotstack asset-list for render %s returned %d: %s",
            render_id, r.status_code, r.text[:200],
        )
        return 0

    # Response shape: {"data": [{"id": "...", ...}, ...]}
    # OR {"response": {"data": [...]}} — support both
    try:
        payload = r.json()
    except Exception:
        logger.warning("Shotstack asset-list for render %s returned non-JSON", render_id)
        return 0

    data = payload.get("data") or payload.get("response", {}).get("data") or []
    if isinstance(data, dict):
        data = [data]   # Single asset response, wrap in list

    deleted = 0
    for item in data:
        attrs = item.get("attributes") or item
        asset_id = attrs.get("id") or item.get("id")
        if not asset_id:
            continue
        delete_url = f"https://api.shotstack.io/serve/{env}/assets/{asset_id}"
        try:
            d = requests.delete(
                delete_url, headers={"x-api-key": api_key}, timeout=20,
            )
            if 200 <= d.status_code < 300 or d.status_code == 404:
                deleted += 1
                logger.info(
                    "Shotstack asset freed: %s (render %s, env %s)",
                    asset_id, render_id, env,
                )
            else:
                logger.warning(
                    "Shotstack asset delete %s returned %d: %s",
                    asset_id, d.status_code, d.text[:200],
                )
        except Exception as e:
            logger.warning("Shotstack asset delete %s raised: %s", asset_id, e)

    if deleted == 0 and data:
        logger.warning(
            "Shotstack: found %d assets for render %s but deleted 0 — "
            "check API key permissions or env. Assets will auto-expire.",
            len(data), render_id,
        )
    elif deleted > 0:
        # Persist the cleanup so retries know this render is done
        _mark_shotstack_render_deleted(render_id)

    return deleted


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
