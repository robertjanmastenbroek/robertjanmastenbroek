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


# ─── Family + mood-tier → hero slot ──────────────────────────────────────────
# Calibrated against proven-viral YouTube thumbnails (2026-04-21 research).
#
# 130 BPM organic-house winners (Café de Anatolia, Sol Selectas): still
# contemplative veiled or jewelry-clad human subject, direct eye contact,
# telephoto ultra-close crop, instrument sometimes held in hand.
#
# 140 BPM tribal-psytrance winners (Astrix Ozora, Boom, Universo Paralello):
# warrior face-paint dancer mid-ecstasy, eyes closed or piercing gaze,
# dreadlocks or feather headpiece, shoulder tattoo, sunset/festival bokeh.
# (Rejecting the Hindu/cosmic/neon 60% of the scene that is off-brand.)

# Default hero phrases match proven-viral competitor thumbnails (no
# instruments — that's an A/B test we'll run later, not a default).
HERO_BY_FAMILY: dict[GenreFamily, dict[MoodTier, str]] = {
    "organic_house": {
        "meditative":
            "a single Bedouin woman in a hand-woven patterned niqab with "
            "gold-thread embroidery, silver tribal diadem across her forehead, "
            "nose ring and stacked silver cuff bracelets, piercing "
            "contemplative eye contact directly with the camera",
        "processional":
            "a single veiled nomad walking through a dune ridge at golden "
            "hour, silver-embroidered robes trailing sand, eyes downcast in "
            "quiet procession",
        "gathering":
            "a single robed figure seated cross-legged on basalt stone at "
            "dusk, warm amber firelight on her face, eyes closed in "
            "ceremonial stillness, silver jewelry catching the fire's glow",
        "ecstatic":
            "a solitary veiled figure at the edge of a desert dance circle, "
            "dust-gold light pouring from behind, silver jewelry catching the "
            "sunset, hand raised in slow measured praise",
    },
    "tribal_psytrance": {
        "meditative":
            "a serene dancer with eyes closed, tribal-stone earrings and "
            "loose dreadlocks, soft sunset bokeh sparkle behind, shoulder "
            "tattoo of a geometric Tabernacle motif",
        "processional":
            "a cloaked figure approaching an ancient stone temple ruin at "
            "dusk, single shaft of cool blue-gold light through an arched "
            "window, weathered sandstone columns receding",
        "gathering":
            "a dancer with blue-ochre warrior stripes painted across her "
            "cheekbones and temples, feather headpiece catching golden-hour "
            "light, arms beginning to lift, dust and ember suspended in the air",
        "ecstatic":
            "an ecstatic warrior-painted dancer mid-leap, eyes closed and "
            "face tilted toward the sky, blue ochre stripes across the "
            "cheekbones, dreadlocks and feather headpiece airborne, crowd "
            "silhouettes behind at sunset, arms overhead in V-shape",
    },
}

# Optional A/B variant: hero WITH a visible instrument in hand. Uncontested
# gap in competitor thumbnails (only Café de Anatolia's ~10M "Best of 2020"
# carved-flute shot shows one). Could be a differentiator OR could flop.
# Not used by default; accessed when build_prompt(with_instrument=True).
HERO_BY_FAMILY_WITH_INSTRUMENT: dict[GenreFamily, dict[MoodTier, str]] = {
    "organic_house": {
        "meditative":
            "a single Bedouin woman in a hand-woven patterned niqab with "
            "gold-thread embroidery, silver tribal diadem, a weathered "
            "ney flute held to her lips, eyes closed",
        "processional":
            "a single veiled nomad walking through a dune ridge at golden "
            "hour, one hand resting on a handpan carried at her side, "
            "silver-embroidered robes trailing sand",
        "gathering":
            "a single robed figure seated cross-legged on basalt stone, "
            "cradling a handpan in her lap, warm amber firelight on her "
            "hands, eyes closed in ceremonial stillness",
        "ecstatic":
            "a robed figure silhouetted against a sunset desert circle, "
            "an oud held aloft in one hand, silver jewelry catching the light",
    },
    "tribal_psytrance": {
        "meditative":
            "a serene dancer with eyes closed, cradling a tribal "
            "frame-drum, tribal-stone earrings and dreadlocks",
        "processional":
            "a cloaked figure with a ram's-horn shofar slung across the "
            "back, approaching an ancient stone temple ruin at dusk",
        "gathering":
            "a warrior-painted dancer cradling a tribal frame-drum at her "
            "chest, blue ochre stripes on her cheekbones, feather headpiece",
        "ecstatic":
            "an ecstatic warrior-painted dancer mid-leap, a ram's-horn "
            "shofar raised overhead, blue ochre stripes on her cheekbones, "
            "crowd silhouettes behind at sunset",
    },
}


def _hero_slot(family: GenreFamily, mood: MoodTier, with_instrument: bool = False) -> str:
    """
    Pick the hero phrase for a given family + mood.

    By default returns the no-instrument variant (matches proven-viral
    competitor thumbnails). Pass with_instrument=True to return the
    handpan/oud/shofar variant — this is an unproven hypothesis and
    should only be used for explicit A/B variants, not as the default.
    """
    dct = HERO_BY_FAMILY_WITH_INSTRUMENT if with_instrument else HERO_BY_FAMILY
    return dct[family].get(mood) or HERO_BY_FAMILY["organic_house"][mood]


# ─── Per-track instrument override ───────────────────────────────────────────
# Based on 2026-04-21 instrument-in-thumbnail evidence research:
#   - DJ-mix niche (Cercle, Cafe de Anatolia, Anjunadeep) → no-instrument wins
#   - Performer niche (Hang Massive 60M, Estas Tonne 116M) → instrument helps
#
# Most of RJM's catalog sits in the DJ-mix niche — default is no-instrument.
# Tracks where the instrument IS the sonic identity cross into the performer
# niche and default to instrument-forward. Only Selah qualifies today
# (handpan + oud + Middle-Eastern modes + Psalm 46 "be still" anchor).
#
# Opt out per-call by passing with_instrument=False explicitly.
# Opt in for any other track by passing with_instrument=True.
TRACKS_WITH_INSTRUMENT_DEFAULT: set[str] = {
    "selah",
}


def _default_with_instrument(track_title_lower: str, explicit: Optional[bool]) -> bool:
    """Resolve whether to include an instrument in the hero phrase."""
    if explicit is not None:
        return explicit
    return track_title_lower in TRACKS_WITH_INSTRUMENT_DEFAULT


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

SACRED_OBJECT_BY_MOOD = {
    # Research finding: winners show PHYSICAL sacred objects, not abstract
    # Platonic-solid overlays. Flower-of-Life / Metatron cube / OM mandala
    # all pull the image toward New Age aesthetic. Physical objects pull
    # it toward the Abrahamic-nomadic aesthetic that wins.
    "meditative":   "silver tribal diadem across the forehead, hand-woven patterned scarf with gold-thread embroidery, stacked silver cuff bracelets",
    "processional": "weathered leather-bound prayer scroll held in one hand, oil-polished ram's-horn shofar slung across the back, worn sandal leather",
    "gathering":    "carved stone libation bowl at the circle's center, copper oil lamp in the firelight, frankincense smoke rising",
    "ecstatic":     "ram's-horn shofar raised overhead, feather headpiece catching the last gold light, stone amulet on a leather cord against the chest",
}


# ─── Cinematography by family ────────────────────────────────────────────────
# Research finding: 130 BPM winners are 85-135mm shallow-DOF portraits.
# 140 BPM winners split between close dancer portraits and wide festival
# drone shots. Both anchor on telephoto compression rather than ultra-wide.

CINEMATOGRAPHY_BY_FAMILY: dict[GenreFamily, str] = {
    "organic_house":
        "shot on ARRI Alexa 65 with 85mm lens, shallow depth of field, "
        "background softly blurred dunes or stone, cinematic telephoto "
        "portrait compression, 35mm film grain, subtle chromatic aberration, "
        "editorial Café de Anatolia aesthetic, centered subject composition",
    "tribal_psytrance":
        "shot on ARRI Alexa 65 with 50mm lens, medium-tight crop on the "
        "dancer, soft sunset bokeh behind, dust and ember particles suspended "
        "in the air, 35mm film grain, editorial Ozora / Universo Paralello "
        "festival-documentary aesthetic",
}


# ─── Palette by family — ONE accent per image, not five stacked ──────────────
# Research finding: winners anchor on a single accent + earth base. Stacking
# all 5 brand colors in one prompt muddies the output.

PALETTE_BY_FAMILY: dict[GenreFamily, str] = {
    "organic_house":
        "dominant warm earth palette of ochre #c8883a and terracotta #b8532a, "
        "deep obsidian black #0a0a0a shadows, single liturgical gold #d4af37 "
        "highlight on jewelry or fabric embroidery, peach sunset sky accent",
    "tribal_psytrance":
        "dominant obsidian black #0a0a0a and indigo night #1a2a4a, single "
        "gold #d4af37 highlight on skin or jewelry, a single stripe of cool "
        "blue-ochre warrior paint as the only saturated hue",
}


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
    "EDM festival LED walls, "
    # Mesoamerican drift — added 2026-04-21 after smoke test thumb 2 went Aztec
    "Mayan temple, Aztec carvings, Aztec warrior, Incan ruins, Mesoamerican "
    "architecture, Chichen Itza, Tenochtitlan, stepped pyramids, jaguar god, "
    "feathered serpent Quetzalcoatl, Native American plains-tribe feather "
    "war bonnet, multicolored parrot feathers"
)


# ─── Public API ──────────────────────────────────────────────────────────────

def build_prompt(
    track_title: str,
    bpm: Optional[int] = None,
    scripture_anchor: Optional[str] = None,
    seed: Optional[int] = None,
    with_instrument: Optional[bool] = None,
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
    resolved_with_instrument = _default_with_instrument(key, with_instrument)

    # Stable environment selection per track title (consistent across regenerations)
    env_index = int(hashlib.md5(key.encode()).hexdigest(), 16) % len(ENVIRONMENTS)
    environment = ENVIRONMENTS[env_index]

    # Assemble the prompt — family-routed slots replace the old mood-only
    # slots after 2026-04-21 viral research (see proven_viral/ bucket analysis).
    hero            = _hero_slot(genre_family, mood_tier, with_instrument=resolved_with_instrument)
    scripture_viz   = SCRIPTURE_HOOKS.get(resolved_anchor, SCRIPTURE_HOOKS[""])
    lighting        = LIGHTING_BY_MOOD[mood_tier]
    sacred_object   = SACRED_OBJECT_BY_MOOD[mood_tier]
    cinematography  = CINEMATOGRAPHY_BY_FAMILY[genre_family]
    palette         = PALETTE_BY_FAMILY[genre_family]

    positive_prompt = (
        f"{hero}, with {scripture_viz}, "
        f"adorned with {sacred_object}, "
        f"set in {environment}, {lighting}, "
        f"{cinematography}, {palette}, "
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
