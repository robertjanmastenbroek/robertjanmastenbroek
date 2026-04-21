"""
prompt_builder.py — Track metadata → Flux 2 prompt.

The heart of the visual system. Takes a track title, reads BPM + scripture
anchor from content_engine.audio_engine, and emits a Biblically-Nomadic
Flux prompt that (a) fits the @osso-so / Café de Anatolia format and
(b) carries the Subtle Salt that distinguishes RJM from the pan-spiritual
scene.

The template is intentionally opinionated. Every slot fills from track
metadata — no free-form human input required per upload. This is how we
scale to daily cadence without losing visual cohesion.

Philosophy:
  - Abrahamic cradle, not neo-pagan syncretism.
  - Object-over-concept (dust on the oud > "spiritual").
  - Locked palette tokens.
  - Negative prompt forbids purple gradients, teal, plastic skin, Latin
    crucifixes, mandalas/yantras, Balenciaga-editorial masked figures.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from content_engine.audio_engine import SCRIPTURE_ANCHORS, TRACK_BPMS
from content_engine.youtube_longform.types import GenreFamily, MoodTier, TrackPrompt

logger = logging.getLogger(__name__)


# ─── BPM → mood tier ─────────────────────────────────────────────────────────

def _derive_mood_tier(bpm: int) -> MoodTier:
    if bpm <= 126:
        return "meditative"
    if bpm <= 132:
        return "processional"
    if bpm <= 138:
        return "gathering"
    return "ecstatic"


def _derive_genre_family(bpm: int) -> GenreFamily:
    """
    Bigger-picture visual culture split. 139+ BPM routes to the tribal
    psytrance family (Goa/tribal — rejecting New Age cosmic imagery);
    below that routes to the organic house / Cafe de Anatolia family.
    """
    return "tribal_psytrance" if bpm >= 139 else "organic_house"


# ─── Track genre inferred from BPM + title ───────────────────────────────────

def _derive_genre(bpm: int, title_lower: str) -> str:
    """Return a canonical genre string that matches brand vocabulary."""
    if "selah" in title_lower:
        return "handpan / oud / Middle Eastern"
    if bpm >= 139:
        return "tribal psytrance"
    if bpm >= 128:
        return "organic tribal house"
    return "organic house"


# ─── Mood-tier → hero/subject slot ───────────────────────────────────────────

MOOD_HERO = {
    "meditative":
        "a solitary robed figure seated cross-legged on basalt stone, "
        "face obscured by head covering, hands resting open on the knees",
    "processional":
        "a small caravan of cloaked nomads moving slowly across a dune ridge, "
        "staffs in hand, robes trailing dust",
    "gathering":
        "a circle of cloaked figures around a rising fire, arms beginning to lift, "
        "the moment before movement becomes ecstatic",
    "ecstatic":
        "a vast crowd of silhouetted nomads with arms raised toward a split sky, "
        "ram's-horn trumpets aloft, dust plume catching the gold light",
}


# ─── Scripture anchor → specific visual hook ─────────────────────────────────
# Each anchor maps to an OBJECT/SCENE visual phrase that encodes the scripture
# subtly. Never quote the verse. Show the thing the verse describes.

SCRIPTURE_HOOKS = {
    # Joshua 6 — walls of Jericho fall at the seventh day, seven trumpets
    "Joshua 6":
        "a crumbling sandstone wall at the moment of collapse, seven bronze "
        "ram's-horn trumpets raised toward the fissure, ancient Canaanite "
        "city receding into dusk behind it",
    # Isaiah 62 — "you shall be called by a new name"
    "Isaiah 62":
        "a single carved stone slab under gold dawn light, Hebrew characters "
        "freshly chiseled, chisel and hammer laid aside in the dust",
    # Psalm 46 — "Be still, and know that I am God"
    "Psalm 46":
        "a handpan resting on black basalt at the center of a moonlit desert "
        "canyon, oud leaning against the stone, incense smoke rising in a "
        "single vertical line of perfect stillness",
    # John 4 — woman at the well, living water
    "John 4":
        "a clay water jar resting on the rim of an ancient stone well at "
        "noon, a single thread of water catching the sun, figure's shadow "
        "just entering the frame",
    # John 8 — "I am the light of the world"
    "John 8":
        "a single oil lamp on a carved stone shelf cutting through deep "
        "temple darkness, stone corridor receding, the flame the only point "
        "of brightness in the frame",
    # Exodus 14 — parting of the Red Sea
    "Exodus 14":
        "a towering wall of still water rising on each side of a dry "
        "seabed path at dawn, a lone pillar of cloud at the horizon",
    # Romans 8:15 — "Abba, Father"
    "Romans 8:15":
        "a child's small hand reaching up toward a weathered adult hand "
        "extended from above, both dust-covered, warm desert light",
    # Default — no anchor; use nomadic archetype
    "":
        "ancient nomadic encampment at dusk, firelight against Bedouin "
        "tents, the single first star appearing in the indigo sky",
}


# ─── Environment slot — rotates to prevent visual fatigue ─────────────────────
# Indexed by a stable hash of the track title so each track has a consistent
# environment across regenerations, but the channel grid shows variety.

ENVIRONMENTS = [
    "Wadi Rum at dusk, terracotta sandstone monoliths, stars just appearing",
    "Petra's carved Treasury at blue hour, deep shadow interior, gold light on the facade",
    "Negev desert plateau at dawn, single Bedouin tent, smoke rising vertically",
    "Mount Sinai rocky slopes at the hour before sunrise, scattered acacia trees",
    "ancient Nabataean canyon interior, smooth stone walls, light from a slot above",
    "Sinai peninsula at new-moon night, Milky Way visible, sand undisturbed",
    "dry riverbed of the Jordan valley at golden hour, olive grove in distance",
    "ancient stone temple interior at night, oil lamps mounted in carved niches",
    "desert oasis at evening, date palms silhouetted against red-orange horizon",
    "Sahara edge under storm light, terracotta dust in the air, single caravan silhouette",
]


# ─── Lighting and sacred-geometry slots (mood-coupled) ───────────────────────

LIGHTING_BY_MOOD = {
    "meditative":  "single vertical shaft of cold moonlight from directly above, no wind, perfect stillness",
    "processional": "low warm dawn light from the horizon, long shadows, dust suspended in the beams",
    "gathering":   "firelight uplighting the figures, gold flickering on robes, indigo sky closing above",
    "ecstatic":    "multiple shafts of liturgical gold light piercing clouds of dust, high-contrast edge rim on every figure",
}

SACRED_GEOMETRY_BY_MOOD = {
    "meditative":  "a single faint Flower-of-Life pattern etched into the stone behind the figure, almost invisible",
    "processional": "the cloaks fold in slow hexagonal symmetry echoing Tabernacle courtyard geometry",
    "gathering":   "hexagonal sacred geometry faintly glowing in the embers and fire-pit stones",
    "ecstatic":    "a vast Metatron-cube of light suspended above the crowd, liturgical gold, semi-transparent, partially obscured by dust and smoke",
}


# ─── Locked palette + cinematic specs (every prompt gets this) ───────────────

CINEMATOGRAPHY = (
    "centered composition, cinematic ultra-wide 24mm, "
    "shot on ARRI Alexa 65, 35mm film grain, subtle chromatic aberration, "
    "editorial photography, cathedral-scale awe, "
    "volumetric atmosphere of incense smoke and fine desert dust"
)

PALETTE = (
    "color palette of obsidian black #0a0a0a, liturgical gold #d4af37, "
    "cold moonlight white #ffffff, terracotta #b8532a, indigo night #1a2a4a, "
    "ochre #c8883a"
)


# ─── Negative prompt — the AI-slop + wrong-scene blacklist ───────────────────

NEGATIVE_PROMPT = (
    "purple gradients, teal, neon signage, fantasy armor, Renaissance fair, "
    "generic AI sheen, plastic skin, CGI-glossy people, Cinzel typography, "
    "stock photo lighting, happy smiling faces, selfies, logos, text, "
    "watermarks, Latin crucifixes, European Gothic cathedrals, Byzantine mosaics, "
    "stained glass, Catholic saints iconography, OM mandalas, yantras, "
    "Buddha statues, Himalayan monks, Amazon jungle, ayahuasca imagery, "
    "DMT fractals, Balenciaga-editorial masked figures on flat red background, "
    "mushroom forest, unicorn, dragon, cyberpunk city, neon Tokyo, "
    "modern cars, smartphones, baseball caps, graphic tees, Coachella, "
    "EDM festival LED walls"
)


# ─── Public API ──────────────────────────────────────────────────────────────

def build_prompt(
    track_title: str,
    bpm: Optional[int] = None,
    scripture_anchor: Optional[str] = None,
    seed: Optional[int] = None,
) -> TrackPrompt:
    """
    Build a Flux 2 prompt for a single track.

    Arguments:
        track_title: case-insensitive key into audio_engine.TRACK_BPMS
        bpm: override BPM (defaults to audio_engine.TRACK_BPMS lookup)
        scripture_anchor: override anchor (defaults to SCRIPTURE_ANCHORS lookup)
        seed: optional fixed seed for reproducible generation

    Returns:
        TrackPrompt dataclass ready for image_gen.generate()
    """
    key = track_title.lower().strip()

    resolved_bpm = bpm if bpm is not None else TRACK_BPMS.get(key, 130)
    resolved_anchor = scripture_anchor if scripture_anchor is not None \
        else SCRIPTURE_ANCHORS.get(key, "")
    mood_tier: MoodTier = _derive_mood_tier(resolved_bpm)
    genre_family: GenreFamily = _derive_genre_family(resolved_bpm)
    genre = _derive_genre(resolved_bpm, key)

    # Stable environment selection per track title (consistent across regenerations)
    env_index = int(hashlib.md5(key.encode()).hexdigest(), 16) % len(ENVIRONMENTS)
    environment = ENVIRONMENTS[env_index]

    # Assemble the prompt
    hero          = MOOD_HERO[mood_tier]
    scripture_viz = SCRIPTURE_HOOKS.get(resolved_anchor, SCRIPTURE_HOOKS[""])
    lighting      = LIGHTING_BY_MOOD[mood_tier]
    geometry      = SACRED_GEOMETRY_BY_MOOD[mood_tier]

    positive_prompt = (
        f"{hero}, with {scripture_viz}, set in {environment}, "
        f"{lighting}, {geometry}, "
        f"{CINEMATOGRAPHY}, {PALETTE}, "
        f"--ar 16:9 --style raw"
    )

    return TrackPrompt(
        track_title=track_title,
        bpm=resolved_bpm,
        genre=genre,
        mood_tier=mood_tier,
        genre_family=genre_family,
        scripture_anchor=resolved_anchor,
        scripture_hook=scripture_viz,
        flux_prompt=positive_prompt,
        flux_negative=NEGATIVE_PROMPT,
        seed=seed,
    )


def build_thumbnail_variants(
    base: TrackPrompt,
    count: int = 3,
) -> list[TrackPrompt]:
    """
    Produce N prompt variants for thumbnail A/B testing.

    YouTube Test & Compare (native since 2024) requires 3 thumbnails and
    picks a winner by watch-time share. We give Flux three different seeds
    (or slight composition tweaks) derived from the base prompt.
    """
    variants: list[TrackPrompt] = []
    for i in range(count):
        # Seed variation — same scene, different framing/crop
        seed = base.seed if base.seed is not None else 0
        variant_seed = seed + (i * 1_000_003)   # Prime offset for randomness spread
        variants.append(TrackPrompt(
            track_title=base.track_title,
            bpm=base.bpm,
            genre=base.genre,
            mood_tier=base.mood_tier,
            genre_family=base.genre_family,
            scripture_anchor=base.scripture_anchor,
            scripture_hook=base.scripture_hook,
            flux_prompt=base.flux_prompt,
            flux_negative=base.flux_negative,
            seed=variant_seed,
        ))
    return variants


# ─── Debug/introspection ─────────────────────────────────────────────────────

def explain(track_title: str) -> str:
    """Human-readable breakdown for `rjm.py content youtube explain <track>`."""
    p = build_prompt(track_title)
    return "\n".join([
        f"Track:           {p.track_title}",
        f"BPM:             {p.bpm}",
        f"Genre:           {p.genre}",
        f"Mood tier:       {p.mood_tier}",
        f"Scripture:       {p.scripture_anchor or '(none)'}",
        f"Scripture hook:  {p.scripture_hook}",
        "",
        "Positive prompt:",
        p.flux_prompt,
        "",
        "Negative prompt:",
        p.flux_negative,
    ])
