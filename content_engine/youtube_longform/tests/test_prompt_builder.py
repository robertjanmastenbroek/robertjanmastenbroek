"""
Tests for prompt_builder.build_prompt — the core visual-language contract.

These tests guard against visual drift: every assertion here represents a
brand decision that must survive code changes. If a test fails because a
string changed, verify the new string still carries the Biblically-Nomadic
intent before updating the expectation.
"""
from __future__ import annotations

import pytest

from content_engine.youtube_longform import prompt_builder
from content_engine.youtube_longform.types import TrackPrompt


def test_build_prompt_jericho_ecstatic_tier():
    """Jericho (140 BPM) → ecstatic tier + Joshua 6 trumpet imagery."""
    p = prompt_builder.build_prompt("Jericho")
    assert p.track_title == "Jericho"
    assert p.bpm == 140
    assert p.mood_tier == "ecstatic"
    assert p.genre == "tribal psytrance"
    assert p.scripture_anchor == "Joshua 6"
    # Visual contract
    assert "ram's-horn trumpet" in p.flux_prompt
    assert "crumbling sandstone wall" in p.flux_prompt
    # Locked palette
    assert "#0a0a0a" in p.flux_prompt
    assert "#d4af37" in p.flux_prompt


def test_build_prompt_selah_processional_tier():
    """Selah (130 BPM) → processional + Psalm 46 handpan-stillness imagery."""
    p = prompt_builder.build_prompt("Selah")
    assert p.bpm == 130
    assert p.mood_tier == "processional"
    assert "handpan/oud/Middle Eastern".lower() in p.genre.lower() or "Middle Eastern" in p.genre
    assert p.scripture_anchor == "Psalm 46"
    assert "handpan" in p.flux_prompt
    assert "oud" in p.flux_prompt
    assert "stillness" in p.flux_prompt


def test_build_prompt_renamed_organic_tribal():
    """Renamed (128 BPM) → processional + Isaiah 62 new-name imagery."""
    p = prompt_builder.build_prompt("Renamed")
    assert p.bpm == 128
    assert p.scripture_anchor == "Isaiah 62"
    assert "Hebrew characters" in p.flux_prompt


def test_negative_prompt_blocks_wrong_scene():
    """Negative prompt must exclude Latin-Christian, neo-pagan, Balenciaga-editorial references."""
    p = prompt_builder.build_prompt("Jericho")
    forbidden = [
        "Latin crucifixes",
        "Byzantine",
        "stained glass",
        "OM mandalas",
        "yantras",
        "Buddha statues",
        "Himalayan monks",
        "Amazon jungle",
        "Balenciaga-editorial",
        "purple gradients",
        "teal",
        "plastic skin",
        "cyberpunk",
    ]
    for token in forbidden:
        assert token in p.flux_negative, f"Negative prompt missing: {token}"


def test_mood_tier_derivation_boundaries():
    """BPM boundaries for mood tiers."""
    # Using the internal helper via a known track; create synthetic cases
    # through build_prompt's bpm= override.
    assert prompt_builder.build_prompt("Test", bpm=120).mood_tier == "meditative"
    assert prompt_builder.build_prompt("Test", bpm=126).mood_tier == "meditative"
    assert prompt_builder.build_prompt("Test", bpm=127).mood_tier == "processional"
    assert prompt_builder.build_prompt("Test", bpm=132).mood_tier == "processional"
    assert prompt_builder.build_prompt("Test", bpm=133).mood_tier == "gathering"
    assert prompt_builder.build_prompt("Test", bpm=138).mood_tier == "gathering"
    assert prompt_builder.build_prompt("Test", bpm=139).mood_tier == "ecstatic"
    assert prompt_builder.build_prompt("Test", bpm=145).mood_tier == "ecstatic"


def test_environment_is_stable_per_track():
    """Same track name → same environment across regenerations (consistency)."""
    a = prompt_builder.build_prompt("Jericho")
    b = prompt_builder.build_prompt("Jericho")
    assert a.flux_prompt == b.flux_prompt


def test_thumbnail_variants_generate_unique_seeds():
    """3 thumbnail variants must have distinct seeds for real A/B variation."""
    base = prompt_builder.build_prompt("Jericho", seed=42)
    variants = prompt_builder.build_thumbnail_variants(base, count=3)
    seeds = [v.seed for v in variants]
    assert len(set(seeds)) == 3, f"Variant seeds not unique: {seeds}"


def test_explain_human_readable():
    """explain() returns a usable debug string."""
    text = prompt_builder.explain("Jericho")
    assert "Track:" in text
    assert "Jericho" in text
    assert "Joshua 6" in text
    assert "Positive prompt:" in text
    assert "Negative prompt:" in text


def test_no_scripture_anchor_falls_back_gracefully():
    """Tracks with empty scripture anchor (Halleluyah, Fire In Our Hands) get default imagery."""
    p = prompt_builder.build_prompt("Halleluyah")
    assert p.scripture_anchor == ""     # SCRIPTURE_ANCHORS has "" for halleluyah
    # Should fall back to the "" hook (nomadic encampment)
    assert "Bedouin" in p.flux_prompt or "nomadic" in p.flux_prompt.lower()


def test_genre_family_routing():
    """BPM cleanly bucketed into one of two visual DNAs (organic_house / tribal_psytrance)."""
    # 139+ = tribal psytrance family
    assert prompt_builder.build_prompt("Jericho").genre_family == "tribal_psytrance"
    assert prompt_builder.build_prompt("Halleluyah").genre_family == "tribal_psytrance"
    assert prompt_builder.build_prompt("Test", bpm=145).genre_family == "tribal_psytrance"
    assert prompt_builder.build_prompt("Test", bpm=139).genre_family == "tribal_psytrance"
    # 138 and below = organic house family
    assert prompt_builder.build_prompt("Test", bpm=138).genre_family == "organic_house"
    assert prompt_builder.build_prompt("Renamed").genre_family == "organic_house"
    assert prompt_builder.build_prompt("Selah").genre_family == "organic_house"
    assert prompt_builder.build_prompt("Living Water").genre_family == "organic_house"
    assert prompt_builder.build_prompt("Test", bpm=124).genre_family == "organic_house"


def test_thumbnail_variants_preserve_genre_family():
    """Seed-varied variants must keep the same BPM-derived family."""
    base = prompt_builder.build_prompt("Jericho")
    for v in prompt_builder.build_thumbnail_variants(base, count=3):
        assert v.genre_family == base.genre_family == "tribal_psytrance"


def test_selah_defaults_to_instrument_forward():
    """
    Selah's sonic identity IS the handpan + oud. Per the instrument-research
    evidence, performer-niche tracks should default to instrument-visible.
    """
    p = prompt_builder.build_prompt("Selah")
    # handpan + oud must appear in the hero phrase (from HERO_BY_FAMILY_WITH_INSTRUMENT)
    assert "handpan" in p.flux_prompt.lower() or "oud" in p.flux_prompt.lower(), (
        "Selah should default to instrument-forward hero phrase. "
        f"Got prompt: {p.flux_prompt[:200]}"
    )


def test_non_performer_tracks_default_to_no_instrument():
    """
    Jericho/Halleluyah/Renamed sit in DJ-mix niche — no-instrument matches
    proven-viral majority. Explicitly verify those prompts don't mention
    handpan in the hero phrase by default.
    """
    for title in ["Jericho", "Halleluyah", "Renamed", "Fire In Our Hands"]:
        p = prompt_builder.build_prompt(title)
        # Check the hero slot specifically — the scripture hook may still
        # reference instruments (e.g. shofars in Joshua 6) and that's fine.
        # We look at the first 200 chars of the prompt which is the hero slot.
        hero_section = p.flux_prompt[:250].lower()
        assert "handpan" not in hero_section, (
            f"{title} default prompt hero mentions handpan — should be no-instrument by default"
        )
        # Only the LoRA/WITH_INSTRUMENT variant mentions frame-drum; baseline should not
        assert "cradling" not in hero_section and "held" not in hero_section, (
            f"{title} default prompt hero mentions an instrument — should match proven-viral no-instrument majority"
        )


def test_explicit_with_instrument_override_wins():
    """Explicit with_instrument=True or False overrides the track-specific default."""
    # Jericho default (False) → explicit True switches to instrument variant
    p_true = prompt_builder.build_prompt("Jericho", with_instrument=True)
    assert "shofar" in p_true.flux_prompt.lower() or "drum" in p_true.flux_prompt.lower()

    # Selah default (True) → explicit False switches to no-instrument variant
    p_false = prompt_builder.build_prompt("Selah", with_instrument=False)
    # Scripture hook for Psalm 46 mentions handpan/oud on basalt — that stays.
    # But the HERO slot should be the no-instrument variant (single veiled
    # nomad walking through dune ridge, etc.)
    hero_section = p_false.flux_prompt[:250].lower()
    assert "cradling" not in hero_section and "held" not in hero_section, (
        "Explicit with_instrument=False should pick the no-instrument hero variant"
    )
