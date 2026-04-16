"""
hook_library.py — Unified hook bank for all 3 clip formats.

Combines:
- 21 original templates (contrast/body-drop/identity) from viral_hook_library.py
- 21 save-driver templates (conditional emotional, POV, social proof, challenge, conversion)
- 4 performance templates (minimal text)
- Transitional hook bank management (visual bait clips)
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from content_engine.types import ClipFormat


@dataclass
class HookTemplate:
    """A single proven hook template."""
    id: str
    angle: str           # 'contrast' | 'body-drop' | 'identity' | 'save-driver' | 'performance'
    mechanism: str        # 'tension' | 'scene' | 'identity' | 'claim' | 'rupture' | 'save' | 'dare'
    template: str         # string with {slots}
    slots: dict           # slot_name -> description for LLM
    example_fill: str
    source_credit: str
    priority: float = 1.0
    tags: list = field(default_factory=list)


# ─── CONTRAST (7s) — 7 templates (from original library) ────────────────────

CONTRAST_TEMPLATES: list[HookTemplate] = [
    HookTemplate(
        id="contrast.pov_collision",
        angle="contrast", mechanism="rupture",
        template="POV: {noun_a} meets {noun_b}",
        slots={"noun_a": "concrete noun from track world", "noun_b": "concrete noun from scripture"},
        example_fill="POV: fire meets the psalm",
        source_credit="Fred again.. Delilah + POV format",
        priority=1.3, tags=["short", "visual"],
    ),
    HookTemplate(
        id="contrast.everyone_said_dead",
        angle="contrast", mechanism="claim",
        template="Everyone said {thing}. {proof}.",
        slots={"thing": "a genre or practice declared dead", "proof": "concrete counter-evidence"},
        example_fill="Everyone said melodic techno was dead. 400 strangers at a Tenerife sunset said otherwise.",
        source_credit="Contrarian proof format",
        priority=1.0,
    ),
    HookTemplate(
        id="contrast.i_was_told",
        angle="contrast", mechanism="rupture",
        template="I was told {rule}. {act}. {result}.",
        slots={"rule": "conventional wisdom", "act": "what RJM did instead", "result": "outcome"},
        example_fill="I was told never open with scripture. Wrote Jericho at 140 BPM. Nobody sat down.",
        source_credit="Rule-breaking narrative",
        priority=1.2,
    ),
    HookTemplate(
        id="contrast.walked_into",
        angle="contrast", mechanism="scene",
        template="A {profile} walked into {place}",
        slots={"profile": "RJM descriptor", "place": "unexpected scripture/sacred location"},
        example_fill="A Dutch techno producer walked into Joshua 6",
        source_credit="Fish-out-of-water setup",
        priority=1.0,
    ),
    HookTemplate(
        id="contrast.you_think",
        angle="contrast", mechanism="tension",
        template="You think {assumption}. Play this at {moment}. Report back.",
        slots={"assumption": "wrong belief about the music", "moment": "specific time/place"},
        example_fill="You think 140 BPM can't hold a psalm. Play this at sunrise. Report back.",
        source_credit="Challenge + specificity format",
        priority=1.1,
    ),
    HookTemplate(
        id="contrast.made_at_confession",
        angle="contrast", mechanism="rupture",
        template="Made this at {time} to {forbidden_thing}. {result}.",
        slots={"time": "vulnerable time", "forbidden_thing": "raw motivation", "result": "outcome"},
        example_fill="Made this at 4am to stop apologising for how it sounds. Slept like the dead after.",
        source_credit="Vulnerable confession format",
        priority=1.0,
    ),
    HookTemplate(
        id="contrast.nobody_asked",
        angle="contrast", mechanism="claim",
        template="Nobody asked for {thing}. {specific_defiance}.",
        slots={"thing": "genre mashup", "specific_defiance": "what RJM did anyway"},
        example_fill="Nobody asked for a 140 BPM psalm. Kept it in the drop anyway.",
        source_credit="Defiance format",
        priority=1.0,
    ),
]

# ─── BODY-DROP (15s) — 7 templates (from original library) ──────────────────

BODY_DROP_TEMPLATES: list[HookTemplate] = [
    HookTemplate(
        id="bodydrop.countdown_body",
        angle="body-drop", mechanism="tension",
        template="{N} seconds until {body_part} {verb}",
        slots={"N": "countdown number", "body_part": "physical body part", "verb": "involuntary reaction"},
        example_fill="8 seconds until your knees forget",
        source_credit="Countdown + body response",
        priority=1.3,
    ),
    HookTemplate(
        id="bodydrop.watch_at_timestamp",
        angle="body-drop", mechanism="scene",
        template="Watch {subject} at {timestamp}",
        slots={"subject": "who/what to watch", "timestamp": "exact timestamp"},
        example_fill="Watch the front row at 0:12",
        source_credit="Timestamp callout format",
        priority=1.1,
    ),
    HookTemplate(
        id="bodydrop.played_at_place",
        angle="body-drop", mechanism="scene",
        template="Played this at {place} at {time}. {observation}.",
        slots={"place": "specific location", "time": "time of day", "observation": "crowd reaction"},
        example_fill="Played this at the cliff edge at sunset. Nobody sat down.",
        source_credit="Live reaction testimonial",
        priority=1.0,
    ),
    HookTemplate(
        id="bodydrop.drop_at_timestamp",
        angle="body-drop", mechanism="tension",
        template="The drop at {timestamp}. {body_consequence}.",
        slots={"timestamp": "exact time", "body_consequence": "physical reaction"},
        example_fill="The drop at 2:14. Shoulders stopped asking.",
        source_credit="Drop reveal + body",
        priority=1.2,
    ),
    HookTemplate(
        id="bodydrop.felt_in_body",
        angle="body-drop", mechanism="claim",
        template="Felt this {sensation} in my {body_part}. That has never happened.",
        slots={"sensation": "physical sensation", "body_part": "body part"},
        example_fill="Felt this bass in my back teeth. That has never happened.",
        source_credit="First-person body claim",
        priority=1.0,
    ),
    HookTemplate(
        id="bodydrop.count_with_me",
        angle="body-drop", mechanism="tension",
        template="Count with me: {N}... {Nminus1}... {Nminus2}... {reaction}",
        slots={"N": "start number", "Nminus1": "second", "Nminus2": "third", "reaction": "what happens"},
        example_fill="Count with me: 3... 2... 1... floor forgets how to stand",
        source_credit="Countdown ritual",
        priority=1.1,
    ),
    HookTemplate(
        id="bodydrop.bassline_warning",
        angle="body-drop", mechanism="rupture",
        template="Warning: the {element} at {timestamp} doesn't {soft_verb}. It {hard_verb}.",
        slots={"element": "musical element", "timestamp": "time", "soft_verb": "gentle action", "hard_verb": "violent action"},
        example_fill="Warning: the 140 BPM kick at 0:14 doesn't drop. It opens the room.",
        source_credit="Warning label format",
        priority=1.0,
    ),
]

# ─── IDENTITY (28s) — 7 templates (from original library) ───────────────────

IDENTITY_TEMPLATES: list[HookTemplate] = [
    HookTemplate(
        id="identity.if_youve_ever",
        angle="identity", mechanism="identity",
        template="If you've ever {very_specific_experience}, this already knows you.",
        slots={"very_specific_experience": "a niche relatable moment"},
        example_fill="If you've ever danced alone at 4am and felt watched, this already knows you.",
        source_credit="Recognition hook",
        priority=1.3,
    ),
    HookTemplate(
        id="identity.made_this_for_specific",
        angle="identity", mechanism="identity",
        template="Made this for {specific_person_profile}. {why}.",
        slots={"specific_person_profile": "very specific person description", "why": "reason/story"},
        example_fill="Made this for the friend who texted at 3am and said 'play me something that doesn't lie.' Found it in Jericho.",
        source_credit="Dedication format",
        priority=1.0,
    ),
    HookTemplate(
        id="identity.this_is_what_sounds_like",
        angle="identity", mechanism="claim",
        template="This is what {specific_internal_state} sounds like when nobody tells you to hide it.",
        slots={"specific_internal_state": "internal emotional/spiritual state"},
        example_fill="This is what prayer that refused to be quiet sounds like when nobody tells you to hide it.",
        source_credit="Internal state externalization",
        priority=1.1,
    ),
    HookTemplate(
        id="identity.three_years_ago",
        angle="identity", mechanism="scene",
        template="{specific_time_past} I {past_state}. Wrote this the week {shift}.",
        slots={"specific_time_past": "exact time reference", "past_state": "past condition", "shift": "what changed"},
        example_fill="Three years ago I stopped answering to my old name. Wrote 'Renamed' the week Isaiah 62 made sense.",
        source_credit="Origin story micro-arc",
        priority=1.0,
    ),
    HookTemplate(
        id="identity.dear_self",
        angle="identity", mechanism="identity",
        template="Dear {past_self}: {message}.",
        slots={"past_self": "specific past version", "message": "letter content"},
        example_fill="Dear the version of me who apologised for the volume: you were right.",
        source_credit="Letter to self",
        priority=1.0,
    ),
    HookTemplate(
        id="identity.one_line_for_you",
        angle="identity", mechanism="identity",
        template="{specific_numbered_person}: this is the one.",
        slots={"specific_numbered_person": "numbered person description"},
        example_fill="The 37th person who skipped Jericho: this is the one.",
        source_credit="Direct address with specificity",
        priority=1.0,
    ),
    HookTemplate(
        id="identity.same_week_shift",
        angle="identity", mechanism="scene",
        template="Same week I {specific_action}, {track_title} hit the drop.",
        slots={"specific_action": "personal life event", "track_title": "track name"},
        example_fill="Same week I stopped lying about the quiet part, Fire In Our Hands hit the drop.",
        source_credit="Synchronicity format",
        priority=1.0,
    ),
]

# ─── SAVE-DRIVER TEMPLATES (Clip 1 text + Clip 2) — NEW ─────────────────────

SAVE_DRIVER_TEMPLATES: list[HookTemplate] = [
    # --- Conditional Emotional Triggers ---
    HookTemplate(
        id="save.if_heartbroken",
        angle="save-driver", mechanism="save",
        template="If you've had your heart broken, don't listen to this song...",
        slots={},
        example_fill="If you've had your heart broken, don't listen to this song...",
        source_credit="Pitch-US top-performing conditional hook format",
        priority=1.3, tags=["emotional", "save"],
    ),
    HookTemplate(
        id="save.for_you_if",
        angle="save-driver", mechanism="save",
        template="This song is for you if you {specific_emotional_state}",
        slots={"specific_emotional_state": "a relatable emotional condition (love late-night drives, been up at 3am overthinking, healing from something)"},
        example_fill="This song is for you if you've been up at 3am overthinking everything",
        source_credit="SocialSound.io conditional emotional trigger",
        priority=1.3, tags=["emotional", "save"],
    ),
    HookTemplate(
        id="save.vibes_were_song",
        angle="save-driver", mechanism="save",
        template="If {season_mood} vibes were a song, it would be this one",
        slots={"season_mood": "a season, time of day, or mood (summer midnight, golden hour, 4am solitude)"},
        example_fill="If golden hour vibes were a song, it would be this one",
        source_credit="Seasonal identification format",
        priority=1.2, tags=["emotional", "save"],
    ),
    HookTemplate(
        id="save.feeling_recently",
        angle="save-driver", mechanism="save",
        template="If you've been feeling {emotion} recently, this song might help",
        slots={"emotion": "a specific emotion (lost, restless, quietly hopeful, free for the first time)"},
        example_fill="If you've been feeling quietly hopeful recently, this song might help",
        source_credit="Damian Keyes top-5 save-driver",
        priority=1.2, tags=["emotional", "save"],
    ),
    HookTemplate(
        id="save.anyone_ever_felt",
        angle="save-driver", mechanism="save",
        template="For anyone who's ever felt {state}, this song's for you",
        slots={"state": "a universal emotional state (lost, alive, free, found, alone in a crowd)"},
        example_fill="For anyone who's ever felt alive and terrified at the same time, this song's for you",
        source_credit="Universal identification format",
        priority=1.1, tags=["emotional", "save"],
    ),
    # --- POV Scene-Setters ---
    HookTemplate(
        id="save.pov_listening",
        angle="save-driver", mechanism="save",
        template="POV: you're listening to a new track from a {age}-year-old {location} musician",
        slots={"age": "artist age", "location": "specific place"},
        example_fill="POV: you're listening to a new track from a 36-year-old Tenerife musician",
        source_credit="Jamie Lee viral format (1.2M views)",
        priority=1.3, tags=["pov", "save"],
    ),
    HookTemplate(
        id="save.imagine_moment",
        angle="save-driver", mechanism="save",
        template="Ok but imagine it's {season}, you're {activity}, and this song starts playing...",
        slots={"season": "season or time", "activity": "relatable activity (driving with windows down, walking alone at night)"},
        example_fill="Ok but imagine it's summer, you're driving with the windows down, and this song starts playing...",
        source_credit="Transportive visualization — highest share rate in music niche",
        priority=1.4, tags=["pov", "save"],
    ),
    HookTemplate(
        id="save.pov_discovered",
        angle="save-driver", mechanism="save",
        template="POV: you just discovered your new favourite artist",
        slots={},
        example_fill="POV: you just discovered your new favourite artist",
        source_credit="Discovery moment format",
        priority=1.1, tags=["pov", "save"],
    ),
    HookTemplate(
        id="save.pov_last_song",
        angle="save-driver", mechanism="save",
        template="POV: this is the last song of the set and nobody wants to leave",
        slots={},
        example_fill="POV: this is the last song of the set and nobody wants to leave",
        source_credit="FOMO/nostalgia format",
        priority=1.2, tags=["pov", "save"],
    ),
    HookTemplate(
        id="save.pov_driving",
        angle="save-driver", mechanism="save",
        template="POV: {time_of_day}, windows down, this on repeat",
        slots={"time_of_day": "golden hour, midnight, 5am, sunset"},
        example_fill="POV: golden hour, windows down, this on repeat",
        source_credit="Driving scene format — top TikTok music hook",
        priority=1.2, tags=["pov", "save"],
    ),
    # --- Social Proof ---
    HookTemplate(
        id="save.about_to_blow",
        angle="save-driver", mechanism="save",
        template="This artist is about to blow up and you heard it here first",
        slots={},
        example_fill="This artist is about to blow up and you heard it here first",
        source_credit="Early-adopter identity format",
        priority=1.1, tags=["proof", "save"],
    ),
    HookTemplate(
        id="save.imagine_opening",
        angle="save-driver", mechanism="save",
        template="Imagine {reference_artist} opening their set with this",
        slots={"reference_artist": "well-known DJ/artist in similar genre (Anyma, Satori, Ben Bohmer)"},
        example_fill="Imagine Anyma opening their set with this",
        source_credit="Reference artist association",
        priority=1.0, tags=["proof", "save"],
    ),
    HookTemplate(
        id="save.friend_said",
        angle="save-driver", mechanism="save",
        template="My friend said this sounds like {artist_a} meets {artist_b}",
        slots={"artist_a": "reference artist A", "artist_b": "reference artist B"},
        example_fill="My friend said this sounds like Satori meets Rufus Du Sol",
        source_credit="Third-person endorsement format",
        priority=1.0, tags=["proof", "save"],
    ),
    # --- Direct Meaning ---
    HookTemplate(
        id="save.your_sign",
        angle="save-driver", mechanism="save",
        template="This song is your sign to {action}",
        slots={"action": "an action the listener needs courage for (let go, start over, tell them how you feel)"},
        example_fill="This song is your sign to let go of what's been holding you back",
        source_credit="Directive action format — highest DM rate",
        priority=1.2, tags=["meaning", "save"],
    ),
    HookTemplate(
        id="save.what_it_felt",
        angle="save-driver", mechanism="save",
        template="This song is exactly what it felt like {experience}",
        slots={"experience": "a specific emotional experience (loving them, leaving home, standing in the rain, finding peace)"},
        example_fill="This song is exactly what it felt like finding peace after years of noise",
        source_credit="Emotional mapping format",
        priority=1.1, tags=["meaning", "save"],
    ),
    HookTemplate(
        id="save.hidden_message",
        angle="save-driver", mechanism="save",
        template="Did you catch the hidden message in this?",
        slots={},
        example_fill="Did you catch the hidden message in this?",
        source_credit="Curiosity + rewatch format",
        priority=1.0, tags=["meaning", "save"],
    ),
    # --- Challenge/Dare ---
    HookTemplate(
        id="save.bet_you_cant",
        angle="save-driver", mechanism="dare",
        template="Bet you can't get through this drop without {reaction}",
        slots={"reaction": "involuntary physical response (dancing, nodding, closing your eyes, turning it up)"},
        example_fill="Bet you can't get through this drop without nodding your head",
        source_credit="Competitive dare — highest completion rate in music",
        priority=1.3, tags=["dare", "completion"],
    ),
    HookTemplate(
        id="save.dare_listen",
        angle="save-driver", mechanism="dare",
        template="I dare you to listen without {physical_response}",
        slots={"physical_response": "physical reaction (bobbing your head, closing your eyes, turning the volume up)"},
        example_fill="I dare you to listen without closing your eyes",
        source_credit="Dare format variant",
        priority=1.2, tags=["dare", "completion"],
    ),
    HookTemplate(
        id="save.wait_for_drop",
        angle="save-driver", mechanism="dare",
        template="Wait for the drop. Just wait.",
        slots={},
        example_fill="Wait for the drop. Just wait.",
        source_credit="Single most-used hook on @melodictechno, @techno pages",
        priority=1.4, tags=["dare", "completion"],
    ),
    # --- Conversion (Spotify pipeline) ---
    HookTemplate(
        id="save.song_name_go",
        angle="save-driver", mechanism="save",
        template="Song: {title}. Now go find it.",
        slots={"title": "track title"},
        example_fill="Song: Jericho. Now go find it.",
        source_credit="Minimalist CTA — end-card format",
        priority=1.0, tags=["cta", "conversion"],
    ),
    HookTemplate(
        id="save.turn_up_11",
        angle="save-driver", mechanism="save",
        template="This is the song you'll want to turn up to 11...",
        slots={},
        example_fill="This is the song you'll want to turn up to 11...",
        source_credit="Volume metaphor CTA",
        priority=1.0, tags=["cta", "conversion"],
    ),
]

# ─── PERFORMANCE TEMPLATES (Clip 3) — Minimal text ──────────────────────────

PERFORMANCE_TEMPLATES: list[HookTemplate] = [
    HookTemplate(
        id="perf.wait_drop",
        angle="performance", mechanism="dare",
        template="Wait for the drop.",
        slots={},
        example_fill="Wait for the drop.",
        source_credit="Universal electronic music hook",
        priority=1.4,
    ),
    HookTemplate(
        id="perf.track_artist",
        angle="performance", mechanism="scene",
        template="{track_title} — {artist_name}",
        slots={"track_title": "track name", "artist_name": "Robert-Jan Mastenbroek"},
        example_fill="Jericho — Robert-Jan Mastenbroek",
        source_credit="Standard artist tag",
        priority=1.0,
    ),
    HookTemplate(
        id="perf.turn_volume",
        angle="performance", mechanism="dare",
        template="Turn your volume up for this one.",
        slots={},
        example_fill="Turn your volume up for this one.",
        source_credit="Volume CTA",
        priority=1.1,
    ),
    HookTemplate(
        id="perf.front_row",
        angle="performance", mechanism="scene",
        template="Watch the front row at {timestamp}",
        slots={"timestamp": "exact timestamp of reaction"},
        example_fill="Watch the front row at 0:08",
        source_credit="Crowd reaction callout",
        priority=1.2,
    ),
]


# ─── Selection Functions ─────────────────────────────────────────────────────

def get_all_templates() -> list[HookTemplate]:
    """Return every template in the library."""
    return (
        CONTRAST_TEMPLATES + BODY_DROP_TEMPLATES + IDENTITY_TEMPLATES
        + SAVE_DRIVER_TEMPLATES + PERFORMANCE_TEMPLATES
    )


def pick_templates_for_format(
    fmt: ClipFormat,
    weights: dict | None = None,
    exclude_ids: set | None = None,
) -> list[HookTemplate]:
    """Pick one hook template appropriate for the given clip format.

    Format → pool mapping (all 46 templates reachable):
    - TRANSITIONAL (22s bait + content): SAVE_DRIVER + BODY_DROP — the text
      overlay rides alongside a visual bait cut, so we favour save-drivers but
      let proven 15s body-drop hooks run too.
    - EMOTIONAL (7s): CONTRAST + SAVE_DRIVER — contrast hooks are the native
      7-second shape from the original library; save-drivers keep variety.
    - PERFORMANCE (28s): BODY_DROP + IDENTITY + PERFORMANCE — identity hooks
      are the native 28s shape; body-drop + performance templates round it out.

    Weighted random by priority × (weight from learning loop if provided).
    """
    exclude_ids = exclude_ids or set()

    if fmt == ClipFormat.TRANSITIONAL:
        pool = SAVE_DRIVER_TEMPLATES + BODY_DROP_TEMPLATES
    elif fmt == ClipFormat.EMOTIONAL:
        pool = CONTRAST_TEMPLATES + SAVE_DRIVER_TEMPLATES
    elif fmt == ClipFormat.PERFORMANCE:
        pool = IDENTITY_TEMPLATES + BODY_DROP_TEMPLATES + PERFORMANCE_TEMPLATES
    else:
        pool = SAVE_DRIVER_TEMPLATES

    candidates = [t for t in pool if t.id not in exclude_ids]
    if not candidates:
        candidates = pool  # fallback: ignore exclusions

    # Weighted random by priority x learned weight
    w = weights or {}
    scored = [(t, t.priority * w.get(t.id, 1.0)) for t in candidates]
    total = sum(s for _, s in scored)
    if total == 0:
        return [random.choice(candidates)]

    r = random.random() * total
    cumulative = 0.0
    for t, s in scored:
        cumulative += s
        if r <= cumulative:
            return [t]
    return [scored[-1][0]]


def pick_transitional_hook(
    bank: list[dict],
    yesterday_category: str | None = None,
) -> dict | None:
    """Pick a transitional visual hook clip from the bank.

    Rules:
    - 7-day cooldown: skip if last_used within 7 days
    - Category diversity: skip yesterday's category
    - Weighted random by performance_score
    """
    today = date.today()
    cooldown = today - timedelta(days=7)

    eligible = []
    for hook in bank:
        if hook["last_used"]:
            last = date.fromisoformat(hook["last_used"])
            if last >= cooldown:
                continue
        if yesterday_category and hook["category"] == yesterday_category:
            continue
        eligible.append(hook)

    if not eligible:
        # Relax: allow same category
        eligible = [
            h for h in bank
            if not h["last_used"] or date.fromisoformat(h["last_used"]) < cooldown
        ]
    if not eligible:
        # Relax: allow any
        eligible = bank

    if not eligible:
        return None

    # Weighted random by performance_score
    total = sum(h["performance_score"] for h in eligible)
    if total == 0:
        return random.choice(eligible)

    r = random.random() * total
    cumulative = 0.0
    for h in eligible:
        cumulative += h["performance_score"]
        if r <= cumulative:
            return h
    return eligible[-1]
