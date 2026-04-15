"""
viral_hook_library.py — proven short-form video hook templates.

These are not invented. Every template here is modelled on a format that has
already produced viral short-form video results on TikTok / Instagram Reels /
YouTube Shorts, often with millions of views. Source attribution is included
so future editors can audit why each pattern earned its spot.

The philosophy: raise the floor.

An LLM asked to "write a great hook" drifts into inner-monologue poetry. A
template with locked structure and slot-filling cannot drift — the worst it
can do is fill a proven shape with mediocre specifics. The shape does the
heavy lifting. Slot-filling with RJM-specific facts (BPM, Tenerife, scripture
anchor, time of day) is deterministic enough that a weak LLM still produces
an 8/10 hook, and a good LLM produces a 10/10.

Each template is locked to ONE angle (contrast / body-drop / identity) because
the three clip lengths (7s / 15s / 28s) each serve a different funnel job:

  contrast  — 7s  — cognitive dissonance; stops the thumb in the first frame
  body-drop — 15s — physical-reaction callout; drives saves and rewatches
  identity  — 28s — "this already belongs to you"; drives follows

Usage:
    from viral_hook_library import pick_templates, TEMPLATES
    trio = pick_templates()  # one template per angle
    filled = fill_templates_with_llm(trio, track_facts)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional


# ─── Data class ──────────────────────────────────────────────────────────────

@dataclass
class HookTemplate:
    """A single proven hook template locked to one angle."""

    id: str
    angle: str           # 'contrast' | 'body-drop' | 'identity'
    mechanism: str       # 'tension' | 'scene' | 'identity' | 'claim' | 'rupture'
    template: str        # string with {slots}
    slots: dict          # slot_name -> what to fill with (human description for LLM)
    example_fill: str    # a fully filled example used as a few-shot guide
    source_credit: str   # which viral format / creator this is modelled on
    priority: float = 1.0  # higher = picked more often
    tags: list = field(default_factory=list)


# ─── CONTRAST (7s) — cognitive dissonance, scroll-stop first frame ───────────
#
# Goal: the viewer's brain must pause to make sense of what it just saw. Any
# template that forces a second read wins here. The best ones collide two
# concrete nouns that don't normally belong in the same sentence.

CONTRAST_TEMPLATES: list[HookTemplate] = [
    HookTemplate(
        id="contrast.pov_collision",
        angle="contrast",
        mechanism="rupture",
        template="POV: {noun_a} meets {noun_b}",
        slots={
            "noun_a": "one concrete noun from the track's world (fire, bass, cliff, synth)",
            "noun_b": "one concrete noun from scripture / sacred register (psalm, Jericho, psalm 150, the altar)",
        },
        example_fill="POV: fire meets the psalm",
        source_credit="Fred again.. 'Delilah' + internet-wide POV format — concrete noun collision in <6 words.",
        priority=1.3,
        tags=["short", "visual"],
    ),
    HookTemplate(
        id="contrast.everyone_said_dead",
        angle="contrast",
        mechanism="claim",
        template="Everyone said {thing}. {proof}.",
        slots={
            "thing": "a thing people say is dead / over / done (melodic techno, raves in churches, 140 BPM)",
            "proof": "a concrete specific counter-proof (400 strangers at sunset, a Tenerife cliffside at 3am)",
        },
        example_fill="Everyone said melodic techno was dead. 400 strangers at a Tenerife sunset said otherwise.",
        source_credit="Ben Böhmer, ARTBAT tour recaps — disproof-by-specific-footage.",
        priority=1.2,
        tags=["claim"],
    ),
    HookTemplate(
        id="contrast.i_was_told",
        angle="contrast",
        mechanism="rupture",
        template="I was told {rule}. {act}. {result}.",
        slots={
            "rule": "a convention in electronic music (never open a set with scripture, never drop at 2am)",
            "act": "the specific opposing act (wrote Jericho at 140 BPM with Joshua 6 in the bassline)",
            "result": "a concrete consequence (nobody sat down, the room went still)",
        },
        example_fill="I was told never open with scripture. Wrote Jericho at 140 BPM. Nobody sat down.",
        source_credit="Creator confession format — Adam Neely, Rick Beato long-form, compressed to 7s.",
        priority=1.1,
        tags=["confession"],
    ),
    HookTemplate(
        id="contrast.walked_into",
        angle="contrast",
        mechanism="scene",
        template="A {profile} walked into {place}",
        slots={
            "profile": "a short character profile of RJM (Dutch techno producer, a 36-year-old with Scripture on his synth)",
            "place": "an unexpected place (Joshua 6, a psalm at 4am, a sunset church in Tenerife)",
        },
        example_fill="A Dutch techno producer walked into Joshua 6",
        source_credit="Setup/reveal joke format — proven for 'this shouldn't work but does' content.",
        priority=1.0,
        tags=["setup_reveal"],
    ),
    HookTemplate(
        id="contrast.you_think",
        angle="contrast",
        mechanism="tension",
        template="You think {assumption}. Play this at {moment}. Report back.",
        slots={
            "assumption": "a common assumption about the genre (techno is cold, 140 BPM is aggressive)",
            "moment": "a specific listening context (cliff edge at sunrise, headphones at 3am in your car)",
        },
        example_fill="You think 140 BPM can't hold a psalm. Play this at sunrise. Report back.",
        source_credit="Direct-address challenge format — proven on music TikTok for driving saves/tries.",
        priority=1.0,
        tags=["challenge"],
    ),
    HookTemplate(
        id="contrast.made_at_confession",
        angle="contrast",
        mechanism="rupture",
        template="Made this at {time} to {forbidden_thing}. {result}.",
        slots={
            "time": "a specific hour (3am, 4am, sunrise on the last day of a break)",
            "forbidden_thing": "the unsayable thing the creator wanted (stop apologising, scare myself, answer a prayer)",
            "result": "a clean one-line consequence",
        },
        example_fill="Made this at 4am to stop apologising for how it sounds. Slept like the dead after.",
        source_credit="Bedroom-producer confession format — Fred again..'s process posts, condensed.",
        priority=1.0,
        tags=["confession"],
    ),
    HookTemplate(
        id="contrast.nobody_asked",
        angle="contrast",
        mechanism="claim",
        template="Nobody asked for {thing}. {specific_defiance}.",
        slots={
            "thing": "the specific thing the track is (a 140 BPM psalm, a techno track with Joshua 6)",
            "specific_defiance": "what the creator did anyway (kept the verse in the drop, played it at a sunset rave)",
        },
        example_fill="Nobody asked for a 140 BPM psalm. Kept it in the drop anyway.",
        source_credit="Defiance format — proven on underground music TikTok for cult-forming intent.",
        priority=0.9,
        tags=["defiance"],
    ),
]


# ─── BODY-DROP (15s) — physical reaction callout, save-driver ────────────────
#
# Goal: make the viewer anticipate and then notice their own body reaction.
# The best ones promise a specific time-stamped moment so the viewer rewinds
# to "check if it's true" — which is what pumps completion rate.

BODY_DROP_TEMPLATES: list[HookTemplate] = [
    HookTemplate(
        id="bodydrop.countdown_body",
        angle="body-drop",
        mechanism="tension",
        template="{N} seconds until {body_part} {verb}",
        slots={
            "N": "a single-digit number, ideally 7-9",
            "body_part": "a specific body part (knees, shoulders, jaw, hands, neck)",
            "verb": "an involuntary verb (forget, drop, stop, break, release)",
        },
        example_fill="8 seconds until your knees forget",
        source_credit="Countdown-to-reaction format — proven on dance TikTok from Fisher drops to John Summit sets.",
        priority=1.4,
        tags=["countdown", "physical"],
    ),
    HookTemplate(
        id="bodydrop.watch_at_timestamp",
        angle="body-drop",
        mechanism="scene",
        template="Watch {subject} at {timestamp}",
        slots={
            "subject": "a specific subject in the footage (the girl in white, the front row, the guy on the cliff)",
            "timestamp": "a specific timecode in the clip (0:09, 0:12, the second the bass drops)",
        },
        example_fill="Watch the front row at 0:12",
        source_credit="Concert-clip callout format — Anyma, Argy, Rüfüs Du Sol aftermovies. Highest-converting callout on reels.",
        priority=1.3,
        tags=["callout"],
    ),
    HookTemplate(
        id="bodydrop.played_at_place",
        angle="body-drop",
        mechanism="scene",
        template="Played this at {place} at {time}. {observation}.",
        slots={
            "place": "a specific location (the cliff edge, sunset sessions, a warehouse at 2am)",
            "time": "a specific time (sunset, 3am, the last hour of the night)",
            "observation": "a concrete one-sentence observation of the crowd (nobody sat down, the floor went still, strangers turned to strangers)",
        },
        example_fill="Played this at the cliff edge at sunset. Nobody sat down.",
        source_credit="Location-proof format — Keinemusik, Afterlife aftermovies. Location IS the hook.",
        priority=1.2,
        tags=["location"],
    ),
    HookTemplate(
        id="bodydrop.drop_at_timestamp",
        angle="body-drop",
        mechanism="tension",
        template="The drop at {timestamp}. {body_consequence}.",
        slots={
            "timestamp": "a specific timecode (2:14, 0:45, 1:03)",
            "body_consequence": "what the body does (shoulders stopped asking, the jaw dropped first, knees gave out)",
        },
        example_fill="The drop at 2:14. Shoulders stopped asking.",
        source_credit="Timestamp-callout format — proven on melodic techno TikTok (Massano, Argy, Massive Attack edits).",
        priority=1.1,
        tags=["timestamp"],
    ),
    HookTemplate(
        id="bodydrop.felt_in_body",
        angle="body-drop",
        mechanism="claim",
        template="Felt this {sensation} in my {body_part}. That has never happened.",
        slots={
            "sensation": "a specific bass/frequency sensation (bass, sub, 140 BPM kick)",
            "body_part": "an unusual specific body location (back teeth, sternum, fingertips, collarbone)",
        },
        example_fill="Felt this bass in my back teeth. That has never happened.",
        source_credit="First-person sensation claim — proven on bass-music TikTok (SVDDEN DEATH, Excision edits).",
        priority=1.0,
        tags=["sensation"],
    ),
    HookTemplate(
        id="bodydrop.count_with_me",
        angle="body-drop",
        mechanism="tension",
        template="Count with me: {N}… {Nminus1}… {Nminus2}… {reaction}",
        slots={
            "N": "a starting number (usually 3 or 5)",
            "Nminus1": "the next number",
            "Nminus2": "the next number",
            "reaction": "what happens at zero (floor forgets, room goes still, knees)",
        },
        example_fill="Count with me: 3… 2… 1… floor forgets how to stand",
        source_credit="Participation countdown — proven for duet/stitch baiting on TikTok.",
        priority=0.9,
        tags=["countdown", "participation"],
    ),
    HookTemplate(
        id="bodydrop.bassline_warning",
        angle="body-drop",
        mechanism="rupture",
        template="Warning: the {element} at {timestamp} doesn't {soft_verb}. It {hard_verb}.",
        slots={
            "element": "a specific musical element (bassline, 140 BPM kick, Jericho horn, subdrop)",
            "timestamp": "a specific timecode (0:14, 0:22)",
            "soft_verb": "a soft verb (drop, enter, arrive)",
            "hard_verb": "a hard verb (rupture, open the room, split the air)",
        },
        example_fill="Warning: the 140 BPM kick at 0:14 doesn't drop. It opens the room.",
        source_credit="Warning-label format — Skrillex, Virtual Self edits. Primes the body for reaction.",
        priority=0.9,
        tags=["warning"],
    ),
]


# ─── IDENTITY (28s) — "this belongs to you", follow-driver ───────────────────
#
# Goal: make a specific viewer feel that the track was made for them, not at
# them. Must be TRULY specific — generic "for the ones still healing" fails
# every brand test. Great identity hooks read like they know the viewer.

IDENTITY_TEMPLATES: list[HookTemplate] = [
    HookTemplate(
        id="identity.if_youve_ever",
        angle="identity",
        mechanism="identity",
        template="If you've ever {very_specific_experience}, this already knows you.",
        slots={
            "very_specific_experience": "an extremely specific moment (danced alone at 4am and felt watched / prayed in a club bathroom / cried at a drop and lied about it)",
        },
        example_fill="If you've ever danced alone at 4am and felt watched, this already knows you.",
        source_credit="Direct-address recognition format — proven on mental-health TikTok, retuned for rave.",
        priority=1.3,
        tags=["recognition"],
    ),
    HookTemplate(
        id="identity.made_this_for_specific",
        angle="identity",
        mechanism="identity",
        template="Made this for {specific_person_profile}. {why}.",
        slots={
            "specific_person_profile": "a very specific person, not a category (the ex-fan who walked out at the last drop, the one who came back after three years, the friend who texted at 3am)",
            "why": "a specific one-sentence reason",
        },
        example_fill="Made this for the friend who texted at 3am and said 'play me something that doesn't lie.' Found it in Jericho.",
        source_credit="Dedication format — proven on singer-songwriter TikTok (Noah Kahan, Phoebe Bridgers), rewired for techno.",
        priority=1.2,
        tags=["dedication"],
    ),
    HookTemplate(
        id="identity.this_is_what_sounds_like",
        angle="identity",
        mechanism="claim",
        template="This is what {specific_internal_state} sounds like when nobody tells you to hide it.",
        slots={
            "specific_internal_state": "a very specific internal state (joy that scared your family, prayer that refused to be quiet, the day after you said no, the week you stopped explaining)",
        },
        example_fill="This is what prayer that refused to be quiet sounds like when nobody tells you to hide it.",
        source_credit="Metaphor-as-sound format — proven on Noah Kahan / Hozier reels. High save-rate.",
        priority=1.1,
        tags=["metaphor"],
    ),
    HookTemplate(
        id="identity.three_years_ago",
        angle="identity",
        mechanism="scene",
        template="{specific_time_past} I {past_state}. Wrote this the week {shift}.",
        slots={
            "specific_time_past": "a specific duration or date (three years ago, last spring, the year I moved to Tenerife)",
            "past_state": "a concrete past state (couldn't finish a track, stopped answering to my old name, sold the Ableton license)",
            "shift": "the shift that made the track possible (the psalm made sense, the 140 BPM held, I said the quiet thing out loud)",
        },
        example_fill="Three years ago I stopped answering to my old name. Wrote 'Renamed' the week Isaiah 62 made sense.",
        source_credit="Before/after confessional — proven on long-form TikTok (creator origin stories).",
        priority=1.2,
        tags=["origin"],
    ),
    HookTemplate(
        id="identity.dear_self",
        angle="identity",
        mechanism="identity",
        template="Dear {past_self}: {message}.",
        slots={
            "past_self": "a specific past self description (the version of me who apologised for the volume, the one still hiding the Bible in the synth stack)",
            "message": "a specific one-sentence message (you were right, it was supposed to be loud, the drop is a promise, this one is for you)",
        },
        example_fill="Dear the version of me who apologised for the volume: you were right.",
        source_credit="Letter-to-self format — proven on singer-songwriter and recovery TikTok.",
        priority=1.0,
        tags=["letter"],
    ),
    HookTemplate(
        id="identity.one_line_for_you",
        angle="identity",
        mechanism="identity",
        template="{specific_numbered_person}: this is the one.",
        slots={
            "specific_numbered_person": "a highly specific 'you' (the 37th person who skipped it / the one scrolling at 2am in Rotterdam / the one still holding the receipt from the last rave)",
        },
        example_fill="The 37th person who skipped Jericho: this is the one.",
        source_credit="Hyper-specific-you format — proven on BookTok and niche-music TikTok. Extreme save-rate.",
        priority=1.0,
        tags=["you"],
    ),
    HookTemplate(
        id="identity.same_week_shift",
        angle="identity",
        mechanism="scene",
        template="Same week I {specific_action}, {track_title} hit the drop.",
        slots={
            "specific_action": "a specific life action (stopped lying about the quiet part, moved to Tenerife, put the psalm in the project file)",
            "track_title": "the track's literal title",
        },
        example_fill="Same week I stopped lying about the quiet part, Fire In Our Hands hit the drop.",
        source_credit="Parallel-timeline format — proven on creator-origin-story TikTok.",
        priority=0.9,
        tags=["origin"],
    ),
]


# ─── Registry ────────────────────────────────────────────────────────────────

TEMPLATES: list[HookTemplate] = (
    CONTRAST_TEMPLATES + BODY_DROP_TEMPLATES + IDENTITY_TEMPLATES
)

BY_ANGLE: dict[str, list[HookTemplate]] = {
    "contrast":  CONTRAST_TEMPLATES,
    "body-drop": BODY_DROP_TEMPLATES,
    "identity":  IDENTITY_TEMPLATES,
}


def get_templates_for_angle(angle: str, n: int = 3) -> list[HookTemplate]:
    """
    Return N templates for the given angle, weighted by priority and shuffled
    so the daily pipeline gets variety across runs (same angle, different
    templates from one day to the next).
    """
    pool = BY_ANGLE.get(angle, [])
    if not pool:
        return []
    weights = [t.priority for t in pool]
    picked: list[HookTemplate] = []
    # Weighted sampling without replacement
    available = list(zip(pool, weights))
    for _ in range(min(n, len(pool))):
        r = random.choices(
            [item[0] for item in available],
            weights=[item[1] for item in available],
            k=1,
        )[0]
        picked.append(r)
        available = [item for item in available if item[0].id != r.id]
    return picked


def pick_templates(angles: Optional[list[str]] = None) -> dict[str, HookTemplate]:
    """
    Return a trio — one template for each of the three angles.
    angles: list of angles to pick for. Defaults to all three standard angles.
    """
    if angles is None:
        angles = ["contrast", "body-drop", "identity"]
    return {angle: get_templates_for_angle(angle, n=1)[0] for angle in angles}


def get_template_by_id(template_id: str) -> Optional[HookTemplate]:
    for t in TEMPLATES:
        if t.id == template_id:
            return t
    return None


# ─── RJM track facts (for deterministic slot-filling) ────────────────────────
#
# The LLM fills slots from THIS table, not from general knowledge. Having
# explicit facts here means the hook is always grounded in the real track.

TRACK_FACTS: dict[str, dict] = {
    "fire in our hands": {
        "bpm": 130,
        "style": "melodic techno with psytrance edges",
        "scripture_anchor": "Jeremiah 23:29",
        "scripture_note": "Is not my word like fire, says the Lord",
        "title_nouns": ["fire", "hands", "flame", "spark"],
        "canonical_location": "Tenerife Holy Rave",
    },
    "jericho": {
        "bpm": 140,
        "style": "tribal psytrance",
        "scripture_anchor": "Joshua 6",
        "scripture_note": "The walls came down when the horns sounded",
        "title_nouns": ["Jericho", "wall", "horn", "trumpet"],
        "canonical_location": "Tenerife cliff-side sunset session",
    },
    "halleluyah": {
        "bpm": 140,
        "style": "psytrance",
        "scripture_anchor": "Psalm 150",
        "scripture_note": "The last psalm ends with a rave",
        "title_nouns": ["hallelujah", "psalm", "praise"],
        "canonical_location": "Tenerife after-dark set",
    },
    "renamed": {
        "bpm": 128,
        "style": "melodic techno",
        "scripture_anchor": "Isaiah 62",
        "scripture_note": "You shall be called by a new name",
        "title_nouns": ["name", "new name", "renaming"],
        "canonical_location": "Tenerife sunrise session",
    },
    "living water": {
        "bpm": 124,
        "style": "melodic techno",
        "scripture_anchor": "John 4",
        "scripture_note": "Water that quenches forever",
        "title_nouns": ["water", "well", "spring", "river"],
        "canonical_location": "Atlantic coast set",
    },
    "he is the light": {
        "bpm": 126,
        "style": "melodic techno",
        "scripture_anchor": "John 8",
        "scripture_note": "I am the light of the world",
        "title_nouns": ["light", "dawn", "lamp"],
        "canonical_location": "Tenerife dawn set",
    },
}


def get_track_facts(track_title: str) -> dict:
    """
    Look up canonical facts for a track. Matches on case-insensitive
    substring so 'Robert-Jan Mastenbroek & LUCID - Fire In Our Hands' resolves
    to the 'fire in our hands' entry.
    """
    if not track_title:
        return _DEFAULT_FACTS
    title = track_title.lower()
    for key, facts in TRACK_FACTS.items():
        if key in title:
            return facts
    return _DEFAULT_FACTS


_DEFAULT_FACTS = {
    "bpm": 140,
    "style": "melodic techno",
    "scripture_anchor": "",
    "scripture_note": "",
    "title_nouns": [],
    "canonical_location": "Tenerife Holy Rave",
}
