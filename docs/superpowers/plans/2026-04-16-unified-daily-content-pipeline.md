# Unified Holy Rave Daily Content Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge two content creation systems into one unified pipeline that ships 18 posts/day (3 clip formats × 6 distribution targets), with save-optimized hooks, auto-rotating Spotify track pool, and per-dimension learning.

**Architecture:** Consolidate `outreach_agent/` content modules (processor.py, generator.py, viral_hook_library.py, brand_gate.py, post_today.py audio logic) into `content_engine/`. Delete legacy assembler.py and visual_engine.py. New modules: hook_library.py, audio_engine.py, renderer.py, generator.py, brand_gate.py. Expand distributor.py (6 targets), learning_loop.py (multi-dimensional), spotify_watcher.py (releases + popularity), pipeline.py (3 formats).

**Tech Stack:** Python 3.13, ffmpeg, librosa, PIL/Pillow, requests, Spotify Web API, Instagram Graph API v21, YouTube Data API v3, Facebook Graph API v21, Buffer GraphQL API, Claude CLI (haiku model)

**Spec:** `docs/superpowers/specs/2026-04-16-unified-daily-content-pipeline-design.md`

---

## Task 1: Expand types.py — New Dataclasses

**Files:**
- Modify: `content_engine/types.py`
- Test: `content_engine/tests/test_types.py`

This is the foundation — every other module imports from here.

- [ ] **Step 1: Write failing tests for new types**

```python
# content_engine/tests/test_types.py
import pytest
from content_engine.types import (
    TrendBrief, PromptWeights, PerformanceRecord, OpeningFrame,
    ClipFormat, TransitionalHook, TrackInfo, UnifiedWeights,
)


def test_clip_format_enum():
    assert ClipFormat.TRANSITIONAL.value == "transitional"
    assert ClipFormat.EMOTIONAL.value == "emotional"
    assert ClipFormat.PERFORMANCE.value == "performance"


def test_transitional_hook_dataclass():
    hook = TransitionalHook(
        file="nature/lightning_01.mp4",
        category="nature",
        duration_s=3.2,
        last_used=None,
        performance_score=1.0,
        times_used=0,
    )
    assert hook.category == "nature"
    assert hook.performance_score == 1.0


def test_track_info_dataclass():
    track = TrackInfo(
        title="Jericho",
        file_path="/content/audio/masters/JERICHO_FINAL.wav",
        bpm=140,
        energy=0.8,
        danceability=0.7,
        valence=0.5,
        scripture_anchor="Joshua 6",
        spotify_id="abc123",
        spotify_popularity=45,
        pool_weight=1.0,
        entered_pool="2026-04-01",
    )
    assert track.bpm == 140
    assert track.scripture_anchor == "Joshua 6"


def test_unified_weights_defaults():
    w = UnifiedWeights.defaults()
    assert "transitional" in w.format_weights
    assert "emotional" in w.format_weights
    assert "performance" in w.format_weights
    assert w.format_weights["transitional"] == 1.0
    assert "nature" in w.transitional_category_weights
    assert len(w.track_weights) == 0  # empty until populated


def test_unified_weights_save_load(tmp_path):
    w = UnifiedWeights.defaults()
    w.save(tmp_path / "weights.json")
    loaded = UnifiedWeights.load(tmp_path / "weights.json")
    assert loaded.format_weights == w.format_weights


def test_performance_record_expanded():
    rec = PerformanceRecord(
        post_id="123",
        platform="instagram",
        clip_index=0,
        variant="a",
        hook_mechanism="tension",
        visual_type="b_roll",
        clip_length=15,
        format_type="transitional",
        hook_template_id="save.if_heartbroken",
        hook_sub_mode="DEVOTION",
        transitional_category="nature",
        transitional_file="nature/lightning_01.mp4",
        track_title="Jericho",
    )
    assert rec.format_type == "transitional"
    assert rec.hook_template_id == "save.if_heartbroken"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/happy-goldwasser"
python3 -m pytest content_engine/tests/test_types.py -v
```
Expected: ImportError — `ClipFormat`, `TransitionalHook`, `TrackInfo`, `UnifiedWeights` don't exist yet.

- [ ] **Step 3: Implement new types**

Add to `content_engine/types.py` after existing classes:

```python
from enum import Enum


class ClipFormat(Enum):
    TRANSITIONAL = "transitional"
    EMOTIONAL = "emotional"
    PERFORMANCE = "performance"


@dataclass
class TransitionalHook:
    """A pre-cleared bait clip for transitional hook format."""
    file: str                   # relative path within content/hooks/transitional/
    category: str               # nature, satisfying, elemental, sports, craftsmanship, illusion
    duration_s: float           # clip duration in seconds
    last_used: str | None       # ISO date or None
    performance_score: float    # EMA-updated; starts at 1.0
    times_used: int             # total times used


@dataclass
class TrackInfo:
    """A track in the active pool with auto-populated facts."""
    title: str
    file_path: str
    bpm: int
    energy: float               # 0.0–1.0 from Spotify audio features
    danceability: float         # 0.0–1.0
    valence: float              # 0.0–1.0
    scripture_anchor: str       # manual, artist-curated
    spotify_id: str             # Spotify track ID
    spotify_popularity: int     # 0–100, updated daily
    pool_weight: float          # learning-loop-adjusted
    entered_pool: str           # ISO date


@dataclass
class UnifiedWeights:
    """Expanded weights covering all learning dimensions."""
    hook_weights: dict          # template_id → float
    visual_weights: dict        # visual_type → float
    format_weights: dict        # format_type → float
    platform_weights: dict      # platform → float
    transitional_category_weights: dict  # category → float
    track_weights: dict         # track_title → float
    best_clip_length: int
    best_platform: str
    updated: str

    @classmethod
    def defaults(cls) -> "UnifiedWeights":
        return cls(
            hook_weights={
                "tension": 1.0, "identity": 1.0, "scene": 1.0,
                "claim": 1.0, "rupture": 1.0,
            },
            visual_weights={
                "performance": 1.0, "b_roll": 1.0, "phone": 1.0,
            },
            format_weights={
                "transitional": 1.0, "emotional": 1.0, "performance": 1.0,
            },
            platform_weights={
                "instagram": 1.0, "youtube": 1.0, "facebook": 1.0,
                "tiktok": 1.0, "instagram_story": 1.0, "facebook_story": 1.0,
            },
            transitional_category_weights={
                "nature": 1.0, "satisfying": 1.0, "elemental": 1.0,
                "sports": 1.0, "craftsmanship": 1.0, "illusion": 1.0,
            },
            track_weights={},
            best_clip_length=15,
            best_platform="instagram",
            updated="",
        )

    def save(self, path=None):
        path = path or (PROJECT_DIR / "prompt_weights.json")
        path.write_text(json.dumps(self.__dict__, indent=2))

    @classmethod
    def load(cls, path=None) -> "UnifiedWeights":
        path = path or (PROJECT_DIR / "prompt_weights.json")
        if not path.exists():
            w = cls.defaults()
            w.save(path)
            return w
        data = json.loads(path.read_text())
        # Backwards compat: old PromptWeights may be on disk
        if "format_weights" not in data:
            w = cls.defaults()
            w.hook_weights = data.get("hook_weights", w.hook_weights)
            w.visual_weights = data.get("visual_weights", w.visual_weights)
            w.best_clip_length = data.get("best_clip_length", w.best_clip_length)
            w.best_platform = data.get("best_platform", w.best_platform)
            w.updated = data.get("updated", w.updated)
            return w
        return cls(**data)
```

Also expand `PerformanceRecord` with new optional fields:

```python
@dataclass
class PerformanceRecord:
    post_id: str
    platform: str
    clip_index: int
    variant: str
    hook_mechanism: str
    visual_type: str
    clip_length: int
    # New fields for expanded tracking
    format_type: str = ""           # transitional | emotional | performance
    hook_template_id: str = ""      # e.g. "save.if_heartbroken"
    hook_sub_mode: str = ""         # e.g. "DEVOTION"
    transitional_category: str = "" # e.g. "nature"
    transitional_file: str = ""     # e.g. "nature/lightning_01.mp4"
    track_title: str = ""
    # Metrics (populated by learning loop)
    views: int = 0
    completion_rate: float = 0.0
    scroll_stop_rate: float = 0.0
    share_rate: float = 0.0
    save_rate: float = 0.0
    recorded_at: str = ""
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python3 -m pytest content_engine/tests/test_types.py -v
```
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add content_engine/types.py content_engine/tests/test_types.py
git commit -m "feat(types): add ClipFormat, TransitionalHook, TrackInfo, UnifiedWeights for unified pipeline"
```

---

## Task 2: Migrate brand_gate.py

**Files:**
- Copy: `outreach_agent/brand_gate.py` → `content_engine/brand_gate.py`
- Test: `content_engine/tests/test_brand_gate.py`

Brand gate has no external dependencies — pure validation logic. Copy it verbatim, add tests, verify.

- [ ] **Step 1: Copy brand_gate.py to content_engine/**

```bash
cp outreach_agent/brand_gate.py content_engine/brand_gate.py
```

- [ ] **Step 2: Write tests for key behaviors**

```python
# content_engine/tests/test_brand_gate.py
import pytest
from content_engine.brand_gate import validate_content, gate_or_reject


def test_passing_hook():
    """A hook with visual marker, no generic adj, no boilerplate, under 280 chars, with contrast."""
    result = validate_content("140 BPM tribal rhythm at the Tenerife cliff — ancient meets future")
    assert result["passes"] is True
    assert result["score"] >= 3


def test_hard_ban_opener():
    result = validate_content("For the ones who dance alone at 4am")
    assert result["hard_fail"] is True
    assert result["passes"] is False


def test_hard_ban_phrase():
    result = validate_content("Joy became a weapon on the dancefloor at 140 BPM")
    assert result["hard_fail"] is True


def test_too_generic():
    result = validate_content("Amazing incredible beautiful music")
    assert result["passes"] is False


def test_gate_or_reject_passes():
    text = "Jericho at 140 BPM — the walls came down on the dancefloor"
    assert gate_or_reject(text) is not None


def test_gate_or_reject_blocks():
    text = "For the ones who feel the rhythm"
    assert gate_or_reject(text) is None
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest content_engine/tests/test_brand_gate.py -v
```
Expected: All PASS (brand_gate.py is self-contained).

- [ ] **Step 4: Commit**

```bash
git add content_engine/brand_gate.py content_engine/tests/test_brand_gate.py
git commit -m "feat(brand_gate): migrate brand gate validation to content_engine"
```

---

## Task 3: Build hook_library.py — Unified Hook Bank

**Files:**
- Create: `content_engine/hook_library.py`
- Test: `content_engine/tests/test_hook_library.py`
- Reference: `outreach_agent/viral_hook_library.py` (existing 21 templates)

- [ ] **Step 1: Write failing tests**

```python
# content_engine/tests/test_hook_library.py
import pytest
from content_engine.hook_library import (
    HookTemplate,
    SAVE_DRIVER_TEMPLATES,
    PERFORMANCE_TEMPLATES,
    CONTRAST_TEMPLATES,
    BODY_DROP_TEMPLATES,
    IDENTITY_TEMPLATES,
    TRANSITIONAL_BANK,
    pick_templates_for_format,
    pick_transitional_hook,
    get_all_templates,
)
from content_engine.types import ClipFormat


def test_save_driver_count():
    """At least 20 save-driver templates."""
    assert len(SAVE_DRIVER_TEMPLATES) >= 20


def test_performance_template_count():
    assert len(PERFORMANCE_TEMPLATES) >= 4


def test_existing_templates_preserved():
    """All 21 original templates still exist."""
    all_t = get_all_templates()
    original_ids = [
        "contrast.pov_collision", "contrast.everyone_said_dead", "contrast.i_was_told",
        "contrast.walked_into", "contrast.you_think", "contrast.made_at_confession",
        "contrast.nobody_asked",
        "bodydrop.countdown_body", "bodydrop.watch_at_timestamp", "bodydrop.played_at_place",
        "bodydrop.drop_at_timestamp", "bodydrop.felt_in_body", "bodydrop.count_with_me",
        "bodydrop.bassline_warning",
        "identity.if_youve_ever", "identity.made_this_for_specific",
        "identity.this_is_what_sounds_like", "identity.three_years_ago",
        "identity.dear_self", "identity.one_line_for_you", "identity.same_week_shift",
    ]
    all_ids = {t.id for t in all_t}
    for oid in original_ids:
        assert oid in all_ids, f"Missing original template: {oid}"


def test_pick_templates_transitional():
    """Transitional format picks a save-driver template."""
    templates = pick_templates_for_format(ClipFormat.TRANSITIONAL)
    assert len(templates) == 1
    assert templates[0].id.startswith("save.")


def test_pick_templates_emotional():
    """Emotional format picks a save-driver template."""
    templates = pick_templates_for_format(ClipFormat.EMOTIONAL)
    assert len(templates) == 1
    assert templates[0].id.startswith("save.")


def test_pick_templates_performance():
    """Performance format picks a performance template."""
    templates = pick_templates_for_format(ClipFormat.PERFORMANCE)
    assert len(templates) == 1
    assert templates[0].id.startswith("perf.")


def test_pick_transitional_hook_respects_cooldown():
    """Hooks used today should not be picked."""
    from datetime import date
    today = date.today().isoformat()
    bank = [
        {"file": "a.mp4", "category": "nature", "duration_s": 3.0,
         "last_used": today, "performance_score": 1.0, "times_used": 1},
        {"file": "b.mp4", "category": "satisfying", "duration_s": 4.0,
         "last_used": None, "performance_score": 1.0, "times_used": 0},
    ]
    hook = pick_transitional_hook(bank, yesterday_category="elemental")
    assert hook["file"] == "b.mp4"


def test_all_templates_have_required_fields():
    for t in get_all_templates():
        assert t.id, f"Template missing id"
        assert t.angle, f"Template {t.id} missing angle"
        assert t.mechanism, f"Template {t.id} missing mechanism"
        assert t.template, f"Template {t.id} missing template text"
        assert t.example_fill, f"Template {t.id} missing example_fill"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python3 -m pytest content_engine/tests/test_hook_library.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement hook_library.py**

Create `content_engine/hook_library.py`. This file:
1. Imports the `HookTemplate` dataclass from `outreach_agent/viral_hook_library.py` structure
2. Includes ALL 21 existing templates (copy verbatim from `outreach_agent/viral_hook_library.py`)
3. Adds 21 new save-driver templates (from spec Section 6.1 Tier 2)
4. Adds 4 performance templates (from spec Section 6.1 Tier 3)
5. Provides `pick_templates_for_format(format: ClipFormat)` selection function
6. Provides `pick_transitional_hook(bank, yesterday_category)` selection function

The full file is ~600 lines. Key sections:

```python
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

    - TRANSITIONAL and EMOTIONAL: pick from SAVE_DRIVER_TEMPLATES
    - PERFORMANCE: pick from PERFORMANCE_TEMPLATES

    Weighted random by priority × (weight from learning loop if provided).
    """
    exclude_ids = exclude_ids or set()

    if fmt in (ClipFormat.TRANSITIONAL, ClipFormat.EMOTIONAL):
        pool = SAVE_DRIVER_TEMPLATES
    elif fmt == ClipFormat.PERFORMANCE:
        pool = PERFORMANCE_TEMPLATES
    else:
        pool = SAVE_DRIVER_TEMPLATES

    candidates = [t for t in pool if t.id not in exclude_ids]
    if not candidates:
        candidates = pool  # fallback: ignore exclusions

    # Weighted random by priority × learned weight
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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python3 -m pytest content_engine/tests/test_hook_library.py -v
```
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add content_engine/hook_library.py content_engine/tests/test_hook_library.py
git commit -m "feat(hooks): unified hook library — 21 original + 21 save-drivers + 4 performance templates"
```

---

## Task 4: Build audio_engine.py — Track Pool + BPM + Beat-Sync

**Files:**
- Create: `content_engine/audio_engine.py`
- Test: `content_engine/tests/test_audio_engine.py`
- Reference: `outreach_agent/post_today.py` (L228–600 audio functions)

Extract audio logic from post_today.py into a clean module. This handles: track pool management, BPM detection, onset strength mapping, high-energy section finding, beat-sync segment extraction, and track audio mixing.

- [ ] **Step 1: Write failing tests**

```python
# content_engine/tests/test_audio_engine.py
import pytest
import json
from pathlib import Path
from content_engine.audio_engine import (
    TrackPool,
    detect_bpm,
    find_peak_sections,
    snap_to_beat,
    mix_audio_onto_video,
)
from content_engine.types import TrackInfo


def test_track_pool_init():
    pool = TrackPool()
    assert len(pool.tracks) >= 4  # seeded with current top tracks


def test_track_pool_select_weighted():
    pool = TrackPool()
    track = pool.select_track()
    assert isinstance(track, TrackInfo)
    assert track.title != ""


def test_track_pool_add_track():
    pool = TrackPool()
    new_track = TrackInfo(
        title="Test Track", file_path="/tmp/test.wav", bpm=128,
        energy=0.7, danceability=0.8, valence=0.5,
        scripture_anchor="", spotify_id="test123",
        spotify_popularity=50, pool_weight=1.0,
        entered_pool="2026-04-16",
    )
    initial_count = len(pool.tracks)
    pool.add_track(new_track)
    assert len(pool.tracks) == initial_count + 1


def test_track_pool_max_size():
    pool = TrackPool(max_size=4)
    for i in range(5):
        pool.add_track(TrackInfo(
            title=f"Track {i}", file_path=f"/tmp/{i}.wav", bpm=128,
            energy=0.7, danceability=0.8, valence=0.5,
            scripture_anchor="", spotify_id=f"id{i}",
            spotify_popularity=50 + i, pool_weight=1.0,
            entered_pool="2026-04-16",
        ))
    assert len(pool.tracks) <= 6  # max_size cap


def test_track_pool_rotation(tmp_path):
    """Track rotation persists to JSON."""
    pool = TrackPool()
    pool.rotation_path = tmp_path / "track_rotation.json"
    track = pool.select_track()
    pool.mark_used(track.title)
    data = json.loads(pool.rotation_path.read_text())
    assert track.title in data
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python3 -m pytest content_engine/tests/test_audio_engine.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement audio_engine.py**

```python
"""
audio_engine.py — Track pool management, BPM detection, beat-sync.

Extracted from outreach_agent/post_today.py audio logic.
"""
import json
import logging
import os
import random
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from content_engine.types import TrackInfo

PROJECT_DIR = Path(__file__).parent.parent
AUDIO_DIR = PROJECT_DIR / "content" / "audio" / "masters"
ROTATION_PATH = PROJECT_DIR / "data" / "track_rotation.json"

logger = logging.getLogger(__name__)

# ─── Scripture anchors (artist-curated, cannot be automated) ─────────────────

SCRIPTURE_ANCHORS = {
    "renamed": "Isaiah 62",
    "halleluyah": "",
    "jericho": "Joshua 6",
    "fire in our hands": "",
    "living water": "John 4",
    "he is the light": "John 8",
    "exodus": "Exodus 14",
    "abba": "Romans 8:15",
}

# ─── Active track seed (top 4 by save rate) ─────────────────────────────────

SEED_TRACKS = ["halleluyah", "renamed", "jericho", "fire in our hands"]


class TrackPool:
    """Manages the active track pool with weighted selection and rotation."""

    def __init__(self, max_size: int = 6):
        self.max_size = max_size
        self.rotation_path = ROTATION_PATH
        self.tracks: list[TrackInfo] = []
        self._load_seed_tracks()

    def _load_seed_tracks(self):
        """Seed pool from WAV files matching SEED_TRACKS."""
        for title in SEED_TRACKS:
            path = self._find_track_file(title)
            if path:
                self.tracks.append(TrackInfo(
                    title=title,
                    file_path=str(path),
                    bpm=0,  # detected on first use
                    energy=0.7,
                    danceability=0.7,
                    valence=0.5,
                    scripture_anchor=SCRIPTURE_ANCHORS.get(title, ""),
                    spotify_id="",
                    spotify_popularity=50,
                    pool_weight=1.0,
                    entered_pool=date.today().isoformat(),
                ))

    def _find_track_file(self, title: str) -> Optional[Path]:
        """Find WAV file matching track title (case-insensitive partial match)."""
        if not AUDIO_DIR.exists():
            return None
        title_lower = title.lower().replace(" ", "_")
        for f in AUDIO_DIR.iterdir():
            if f.suffix.lower() in (".wav", ".flac", ".mp3"):
                fname = f.stem.lower().replace(" ", "_").replace("-", "_")
                if title_lower in fname or fname in title_lower:
                    return f
        # Broader match
        for f in AUDIO_DIR.iterdir():
            if f.suffix.lower() in (".wav", ".flac", ".mp3"):
                if title.lower().split()[0] in f.stem.lower():
                    return f
        return None

    def select_track(self, weights: dict | None = None) -> TrackInfo:
        """Weighted random selection from pool. LRU bias via rotation."""
        if not self.tracks:
            raise ValueError("Track pool is empty")

        rotation = self._load_rotation()
        w = weights or {}

        scored = []
        for t in self.tracks:
            # Base weight from learning loop
            base = w.get(t.title, t.pool_weight)
            # LRU bonus: longer since last use = higher score
            last_used = rotation.get(t.title, "2020-01-01")
            days_since = (date.today() - date.fromisoformat(last_used[:10])).days
            lru_bonus = min(days_since / 7.0, 2.0)  # cap at 2x
            scored.append((t, base * (1.0 + lru_bonus)))

        total = sum(s for _, s in scored)
        if total == 0:
            return random.choice(self.tracks)

        r = random.random() * total
        cumulative = 0.0
        for t, s in scored:
            cumulative += s
            if r <= cumulative:
                return t
        return scored[-1][0]

    def mark_used(self, title: str):
        """Record that a track was used today."""
        rotation = self._load_rotation()
        rotation[title] = datetime.now().isoformat()
        self.rotation_path.parent.mkdir(parents=True, exist_ok=True)
        self.rotation_path.write_text(json.dumps(rotation, indent=2))

    def add_track(self, track: TrackInfo):
        """Add a track to the pool. If at max_size, don't add (learning loop handles eviction)."""
        if len(self.tracks) >= self.max_size:
            logger.warning(f"Pool at max size ({self.max_size}), not adding {track.title}")
            return
        if any(t.title == track.title for t in self.tracks):
            return  # already in pool
        self.tracks.append(track)

    def remove_lowest(self) -> Optional[TrackInfo]:
        """Remove the track with the lowest pool_weight. Returns removed track."""
        if len(self.tracks) <= 4:
            return None  # maintain minimum
        worst = min(self.tracks, key=lambda t: t.pool_weight)
        self.tracks.remove(worst)
        return worst

    def _load_rotation(self) -> dict:
        if self.rotation_path.exists():
            return json.loads(self.rotation_path.read_text())
        return {}

    def save_pool(self, path: Optional[Path] = None):
        """Persist pool state to JSON."""
        path = path or (PROJECT_DIR / "data" / "track_pool.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "title": t.title, "file_path": t.file_path, "bpm": t.bpm,
                "energy": t.energy, "danceability": t.danceability,
                "valence": t.valence, "scripture_anchor": t.scripture_anchor,
                "spotify_id": t.spotify_id, "spotify_popularity": t.spotify_popularity,
                "pool_weight": t.pool_weight, "entered_pool": t.entered_pool,
            }
            for t in self.tracks
        ]
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load_pool(cls, path: Optional[Path] = None) -> "TrackPool":
        """Load pool from JSON, falling back to seed tracks."""
        path = path or (PROJECT_DIR / "data" / "track_pool.json")
        pool = cls.__new__(cls)
        pool.max_size = 6
        pool.rotation_path = ROTATION_PATH
        pool.tracks = []
        if path.exists():
            data = json.loads(path.read_text())
            for d in data:
                pool.tracks.append(TrackInfo(**d))
        if not pool.tracks:
            pool._load_seed_tracks()
        return pool


# ─── BPM + Beat-Sync Functions ──────────────────────────────────────────────

def detect_bpm(audio_path: str) -> int:
    """Detect BPM via librosa. Returns integer BPM (doubles if < 100)."""
    try:
        import librosa
        y, sr = librosa.load(audio_path, sr=22050, duration=60)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo) if not hasattr(tempo, '__len__') else float(tempo[0])
        if bpm < 100:
            bpm *= 2
        return int(round(bpm))
    except Exception as e:
        logger.warning(f"BPM detection failed for {audio_path}: {e}")
        return 128  # safe default for melodic techno


def find_peak_sections(
    audio_path: str,
    section_duration: float,
    n_sections: int = 5,
) -> list[float]:
    """Find top N high-energy start times in the track.

    Uses librosa onset_strength to find energy peaks, then returns
    start times for the highest-energy windows of `section_duration` seconds.
    """
    try:
        import librosa
        import numpy as np

        y, sr = librosa.load(audio_path, sr=22050)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        hop_length = 512
        hop_secs = hop_length / sr
        total_duration = len(y) / sr

        # Sliding window: sum onset strength over section_duration
        window_frames = int(section_duration / hop_secs)
        if window_frames >= len(onset_env):
            return [0.0]

        energies = []
        for start_frame in range(len(onset_env) - window_frames):
            energy = float(np.sum(onset_env[start_frame:start_frame + window_frames]))
            start_time = start_frame * hop_secs
            # Don't start in first 15s (intro) or last 10s (outro)
            if start_time < 15.0 or start_time + section_duration > total_duration - 10.0:
                continue
            energies.append((start_time, energy))

        energies.sort(key=lambda x: x[1], reverse=True)

        # Deduplicate: no two sections within 5s of each other
        selected = []
        for start_time, energy in energies:
            if all(abs(start_time - s) > 5.0 for s in selected):
                selected.append(start_time)
            if len(selected) >= n_sections:
                break

        return selected if selected else [30.0]

    except Exception as e:
        logger.warning(f"Peak section detection failed: {e}")
        return [30.0]


def snap_to_beat(audio_path: str, target_time: float) -> float:
    """Snap a target time to the nearest beat boundary."""
    try:
        import librosa
        y, sr = librosa.load(audio_path, sr=22050, duration=min(target_time + 30, 300))
        _, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        if len(beat_times) == 0:
            return target_time
        diffs = [abs(bt - target_time) for bt in beat_times]
        nearest_idx = diffs.index(min(diffs))
        return float(beat_times[nearest_idx])
    except Exception as e:
        logger.warning(f"Beat snap failed: {e}")
        return target_time


def mix_audio_onto_video(
    video_path: str,
    audio_path: str,
    start_time: float,
    duration: float,
    output_path: str,
    fade_out_s: float = 1.5,
) -> str:
    """Replace video audio with a segment of the track. Returns output path.

    Uses ffmpeg to:
    1. Extract audio segment from track (start_time, duration)
    2. Apply fade-out in last fade_out_s seconds
    3. Merge with video (replacing original audio)
    """
    fade_start = max(0, duration - fade_out_s)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ss", str(start_time), "-t", str(duration), "-i", audio_path,
        "-filter_complex",
        f"[1:a]afade=t=out:st={fade_start}:d={fade_out_s}[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"Audio mix failed: {e.stderr.decode()[:500]}")
        raise
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python3 -m pytest content_engine/tests/test_audio_engine.py -v
```
Expected: All PASS (at least pool management tests; BPM tests may skip if librosa not installed).

- [ ] **Step 5: Commit**

```bash
git add content_engine/audio_engine.py content_engine/tests/test_audio_engine.py
git commit -m "feat(audio): audio engine — track pool, BPM detection, beat-sync, audio mixing"
```

---

## Task 5: Migrate generator.py — Hook Filling + Sub-Mode Diversity

**Files:**
- Create: `content_engine/generator.py`
- Test: `content_engine/tests/test_generator.py`
- Reference: `outreach_agent/generator.py` (L55–900+)

Migrate hook generation + caption generation. Wire sub-mode diversity.

- [ ] **Step 1: Write failing tests**

```python
# content_engine/tests/test_generator.py
import pytest
from content_engine.generator import (
    generate_hooks_for_format,
    generate_caption,
    pick_sub_mode,
    ANGLE_SUB_MODES,
)
from content_engine.types import ClipFormat
from content_engine.hook_library import HookTemplate


def test_sub_modes_exist():
    assert "emotional" in ANGLE_SUB_MODES
    assert "signal" in ANGLE_SUB_MODES
    assert "energy" in ANGLE_SUB_MODES
    assert len(ANGLE_SUB_MODES["emotional"]) == 5


def test_pick_sub_mode():
    mode = pick_sub_mode("emotional")
    assert mode in ANGLE_SUB_MODES["emotional"]


def test_generate_hooks_returns_dict():
    """Test with fallback (Claude unavailable = uses example_fill)."""
    result = generate_hooks_for_format(
        fmt=ClipFormat.EMOTIONAL,
        track_title="Jericho",
        track_facts={"bpm": 140, "scripture_anchor": "Joshua 6"},
    )
    assert "hook" in result
    assert "template_id" in result
    assert "mechanism" in result
    assert "sub_mode" in result
    assert len(result["hook"]) > 0


def test_generate_caption():
    caption = generate_caption(
        track_title="Jericho",
        hook_text="Wait for the drop. Just wait.",
        platform="instagram",
    )
    assert isinstance(caption, str)
    assert len(caption) > 0
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python3 -m pytest content_engine/tests/test_generator.py -v
```

- [ ] **Step 3: Implement generator.py**

```python
"""
generator.py — Claude-driven hook filling + caption generation with sub-mode diversity.

Migrated from outreach_agent/generator.py. Key changes:
- Works with ClipFormat enum (transitional/emotional/performance)
- Wires sub-mode diversity (COST, NAMING, DOUBT, etc.) that was coded but never executed
- Uses content_engine.hook_library for template selection
- Uses content_engine.brand_gate for validation
"""
import json
import logging
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from content_engine.types import ClipFormat
from content_engine.hook_library import (
    HookTemplate,
    pick_templates_for_format,
    get_all_templates,
)
from content_engine.brand_gate import gate_or_reject

PROJECT_DIR = Path(__file__).parent.parent
logger = logging.getLogger(__name__)

# ─── Sub-mode diversity (was documented but never executed) ──────────────────

ANGLE_SUB_MODES = {
    "emotional": ["COST", "NAMING", "DOUBT", "DEVOTION", "RUPTURE"],
    "signal": ["FINDER", "PERMISSION", "RECOGNITION", "SEASON", "UNSAID"],
    "energy": ["BODY", "TIME", "GEOGRAPHY", "THRESHOLD", "DISSOLUTION"],
}

# Map ClipFormat to angle
FORMAT_TO_ANGLE = {
    ClipFormat.TRANSITIONAL: "emotional",
    ClipFormat.EMOTIONAL: "emotional",
    ClipFormat.PERFORMANCE: "energy",
}


def pick_sub_mode(angle: str) -> str:
    """Pick a random sub-mode for the given angle."""
    modes = ANGLE_SUB_MODES.get(angle, ANGLE_SUB_MODES["emotional"])
    return random.choice(modes)


def _call_claude(prompt: str, system: str = "", timeout: int = 120) -> Optional[str]:
    """Call Claude CLI (haiku model). Returns response text or None on failure."""
    claude_path = "/usr/local/bin/claude"
    if not os.path.exists(claude_path):
        # Try common locations
        for p in ["/opt/homebrew/bin/claude", os.path.expanduser("~/.claude/local/claude")]:
            if os.path.exists(p):
                claude_path = p
                break

    cmd = [
        claude_path, "--print",
        "--model", "claude-haiku-4-5-20251001",
        "--no-session-persistence",
        "--max-turns", "1",
        prompt,
    ]
    if system:
        cmd.insert(3, "--system-prompt")
        cmd.insert(4, system)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd="/tmp",
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        logger.warning(f"Claude call failed: {result.stderr[:300]}")
        return None
    except Exception as e:
        logger.warning(f"Claude call exception: {e}")
        return None


def generate_hooks_for_format(
    fmt: ClipFormat,
    track_title: str,
    track_facts: dict,
    weights: dict | None = None,
    exclude_ids: set | None = None,
) -> dict:
    """Generate a hook for a specific clip format.

    Returns: {hook, template_id, mechanism, sub_mode, exploration}
    """
    # 1. Pick template
    templates = pick_templates_for_format(fmt, weights, exclude_ids)
    template = templates[0]

    # 2. Pick sub-mode
    angle = FORMAT_TO_ANGLE.get(fmt, "emotional")
    sub_mode = pick_sub_mode(angle)

    # 3. Fill template slots with Claude (or use example_fill as fallback)
    if template.slots:
        filled = _fill_template_with_claude(template, track_title, track_facts, sub_mode)
    else:
        filled = template.template  # no slots to fill

    # 4. Brand gate validation
    if filled:
        validated = gate_or_reject(filled)
        if validated:
            return {
                "hook": validated,
                "template_id": template.id,
                "mechanism": template.mechanism,
                "sub_mode": sub_mode,
                "exploration": False,
            }

    # 5. Fallback to example_fill
    logger.info(f"Using example_fill for {template.id}")
    return {
        "hook": template.example_fill,
        "template_id": template.id,
        "mechanism": template.mechanism,
        "sub_mode": sub_mode,
        "exploration": False,
    }


def _fill_template_with_claude(
    template: HookTemplate,
    track_title: str,
    track_facts: dict,
    sub_mode: str,
) -> Optional[str]:
    """Fill a single template's slots using Claude."""
    slots_desc = "\n".join(f"  {k}: {v}" for k, v in template.slots.items())
    prompt = f"""Fill the slots in this hook template for the track "{track_title}".

Template: {template.template}
Slots:
{slots_desc}

Example (quality bar): {template.example_fill}

Track facts:
- BPM: {track_facts.get('bpm', 'unknown')}
- Scripture anchor: {track_facts.get('scripture_anchor', 'none')}
- Style: melodic techno / tribal psytrance
- Artist: Robert-Jan Mastenbroek (Dutch, 36, Tenerife)
- Energy: {track_facts.get('energy', 'high')}

Register: {sub_mode} — fill the slots with this emotional register in mind.

Rules:
- Return ONLY the filled hook text, nothing else
- Keep under 280 characters
- Be concrete and specific (no generic adjectives)
- The hook must work as a text overlay on a short video
"""

    system = "You are a hook copywriter for a Dutch DJ/producer. Fill template slots with specific, concrete language. No generic adjectives. Under 280 chars. Return ONLY the filled text."

    response = _call_claude(prompt, system, timeout=60)
    if response:
        # Clean up: remove quotes, markdown, etc.
        cleaned = response.strip().strip('"').strip("'").strip("`")
        cleaned = re.sub(r'^#+\s*', '', cleaned)  # remove markdown headers
        cleaned = re.sub(r'\*+', '', cleaned)  # remove bold/italic markers
        if len(cleaned) <= 280 and len(cleaned) > 5:
            return cleaned

    return None


def generate_caption(
    track_title: str,
    hook_text: str,
    platform: str,
    track_facts: dict | None = None,
) -> str:
    """Generate a platform-specific caption for a clip.

    Falls back to a template caption if Claude is unavailable.
    """
    facts = track_facts or {}
    scripture = facts.get("scripture_anchor", "")

    prompt = f"""Write a short caption for a {platform} Reel/Short.

Track: {track_title} by Robert-Jan Mastenbroek
Hook used: {hook_text}
Scripture anchor: {scripture or 'none'}
Platform: {platform}

Rules:
- 1-3 short lines
- Include track name
- Include 3-5 relevant hashtags
- If scripture anchor exists, weave it in subtly (Matthew 5:13 — salt, not sermon)
- End with a call-to-action (save this, link in bio, full track on Spotify)
- For TikTok: more casual, emoji OK
- For Instagram: slightly more polished
- For YouTube: include "Subscribe" CTA
- For Facebook: conversational
"""

    response = _call_claude(prompt, timeout=60)
    if response and len(response) < 1000:
        return response.strip()

    # Fallback caption
    hashtags = "#melodictechno #holyrave #tenerife #newmusic #techno"
    if platform == "tiktok":
        return f"{track_title} — Robert-Jan Mastenbroek\n\nFull track on Spotify (link in bio)\n\n{hashtags}"
    elif platform == "youtube":
        return f"{track_title} — Robert-Jan Mastenbroek\n\nStream on Spotify: link in description\nSubscribe for more\n\n{hashtags}"
    else:
        return f"{track_title} — Robert-Jan Mastenbroek\n\nSave this. Full track on Spotify (link in bio)\n\n{hashtags}"
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python3 -m pytest content_engine/tests/test_generator.py -v
```

- [ ] **Step 5: Commit**

```bash
git add content_engine/generator.py content_engine/tests/test_generator.py
git commit -m "feat(generator): hook generation with sub-mode diversity + caption generation"
```

---

## Task 6: Build renderer.py — Unified Video Rendering

**Files:**
- Create: `content_engine/renderer.py`
- Test: `content_engine/tests/test_renderer.py`
- Reference: `outreach_agent/processor.py` (L262–555), `content_engine/assembler.py` (L104–250)

This is the largest single module. Three render paths: transitional, emotional, performance. Plus Stories variant and output validation.

- [ ] **Step 1: Write failing tests**

```python
# content_engine/tests/test_renderer.py
import pytest
from pathlib import Path
from content_engine.renderer import (
    validate_output,
    get_platform_color_grade,
    PLATFORM_GRADES,
    render_transitional,
    render_emotional,
    render_performance,
    render_story_variant,
)
from content_engine.types import ClipFormat


def test_platform_grades_exist():
    for p in ["instagram", "youtube", "facebook", "tiktok"]:
        assert p in PLATFORM_GRADES


def test_validate_output_rejects_missing(tmp_path):
    result = validate_output(str(tmp_path / "nonexistent.mp4"), target_duration=15)
    assert result["valid"] is False


def test_validate_output_rejects_tiny(tmp_path):
    f = tmp_path / "tiny.mp4"
    f.write_bytes(b"x" * 50)  # 50 bytes < 100KB threshold
    result = validate_output(str(f), target_duration=15)
    assert result["valid"] is False


def test_get_platform_color_grade():
    grade = get_platform_color_grade("instagram")
    assert "contrast" in grade
    assert "saturation" in grade
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python3 -m pytest content_engine/tests/test_renderer.py -v
```

- [ ] **Step 3: Implement renderer.py**

This is a large file (~500 lines). Key structure:

```python
"""
renderer.py — Unified video rendering for all 3 clip formats.

Consolidates outreach_agent/processor.py + content_engine/assembler.py into
one module with three render paths: transitional, emotional, performance.
Plus Stories variant and output validation.
"""
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).parent.parent
logger = logging.getLogger(__name__)

OUTPUT_W, OUTPUT_H = 1080, 1920

# ─── Platform color grades ───────────────────────────────────────────────────

PLATFORM_GRADES = {
    "instagram": {"contrast": 1.1, "saturation": 1.15, "gamma": 1.0, "brightness": 0.02},
    "tiktok": {"contrast": 1.1, "saturation": 1.15, "gamma": 1.0, "brightness": 0.02},
    "youtube": {"contrast": 1.0, "saturation": 1.0, "gamma": 1.0, "brightness": 0.0},
    "facebook": {"contrast": 1.05, "saturation": 1.08, "gamma": 1.02, "brightness": 0.01},
}

# ─── Hook text styling ──────────────────────────────────────────────────────

HOOK_STYLES = {
    "transitional": {"font_size": 68, "y_pct": 0.78, "wrap": 16},
    "emotional": {"font_size": 68, "y_pct": 0.78, "wrap": 16},
    "performance": {"font_size": 52, "y_pct": 0.85, "wrap": 20},
}


def get_platform_color_grade(platform: str) -> dict:
    return PLATFORM_GRADES.get(platform, PLATFORM_GRADES["youtube"])


def _get_video_info(path: str) -> dict:
    """Get video duration, width, height via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        video_stream = next(s for s in data["streams"] if s["codec_type"] == "video")
        return {
            "duration": duration,
            "width": int(video_stream["width"]),
            "height": int(video_stream["height"]),
        }
    except Exception as e:
        logger.error(f"ffprobe failed for {path}: {e}")
        return {"duration": 0, "width": 0, "height": 0}


def validate_output(path: str, target_duration: float) -> dict:
    """Validate rendered output. Returns {valid: bool, errors: list}."""
    errors = []
    p = Path(path)

    if not p.exists():
        return {"valid": False, "errors": ["file does not exist"]}

    if p.stat().st_size < 100_000:  # 100KB
        return {"valid": False, "errors": ["file too small (< 100KB), likely corrupt"]}

    info = _get_video_info(path)
    if info["duration"] == 0:
        errors.append("could not read duration (invalid container)")
    elif abs(info["duration"] - target_duration) > 1.5:
        errors.append(f"duration {info['duration']:.1f}s, expected {target_duration:.1f}s (±1.5s)")

    if info["width"] != OUTPUT_W or info["height"] != OUTPUT_H:
        if info["width"] > 0:  # only flag if we could read dimensions
            errors.append(f"resolution {info['width']}x{info['height']}, expected {OUTPUT_W}x{OUTPUT_H}")

    # Check audio stream
    try:
        cmd = ["ffprobe", "-v", "quiet", "-select_streams", "a", "-show_entries", "stream=codec_type", path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if "audio" not in result.stdout:
            errors.append("no audio stream")
    except Exception:
        errors.append("could not check audio stream")

    return {"valid": len(errors) == 0, "errors": errors}


def _crop_to_vertical(input_path: str, output_path: str) -> str:
    """Crop any video to 9:16 vertical (1080x1920)."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,crop={OUTPUT_W}:{OUTPUT_H}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-an", output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    return output_path


def _apply_color_grade(input_path: str, output_path: str, platform: str) -> str:
    """Apply platform-specific color grading via ffmpeg eq filter."""
    grade = get_platform_color_grade(platform)
    eq_filter = (
        f"eq=contrast={grade['contrast']}:saturation={grade['saturation']}"
        f":gamma={grade['gamma']}:brightness={grade['brightness']}"
    )
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", eq_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return output_path
    except subprocess.CalledProcessError:
        logger.warning(f"Color grade failed for {platform}, using ungraded")
        return input_path


def _burn_text_overlay(
    input_path: str,
    output_path: str,
    text: str,
    style: str = "emotional",
    start_time: float = 0.0,
    end_time: float | None = None,
) -> str:
    """Burn text overlay onto video using ffmpeg drawtext.

    Uses Bebas Neue font with white text + black shadow.
    """
    s = HOOK_STYLES.get(style, HOOK_STYLES["emotional"])
    font_size = s["font_size"]
    y_pos = int(OUTPUT_H * s["y_pct"])

    # Escape text for ffmpeg drawtext
    escaped = text.replace("'", "\\'").replace(":", "\\:")

    info = _get_video_info(input_path)
    if end_time is None:
        end_time = info["duration"] - 1.0 if info["duration"] > 1 else info["duration"]

    # Fade in at start_time, fade out at end_time
    alpha_expr = (
        f"if(lt(t,{start_time}),0,"
        f"if(lt(t,{start_time + 0.3}),(t-{start_time})/0.3,"
        f"if(lt(t,{end_time}),1,"
        f"if(lt(t,{end_time + 0.5}),1-(t-{end_time})/0.5,0))))"
    )

    # Try Bebas Neue, fall back to Helvetica, then default
    font = "/System/Library/Fonts/Helvetica.ttc"
    for candidate in [
        os.path.expanduser("~/.fonts/BebasNeue-Regular.ttf"),
        "/Library/Fonts/BebasNeue-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        if os.path.exists(candidate):
            font = candidate
            break

    drawtext = (
        f"drawtext=text='{escaped}'"
        f":fontfile='{font}'"
        f":fontsize={font_size}"
        f":fontcolor=white"
        f":shadowcolor=black@0.6:shadowx=3:shadowy=3"
        f":x=(w-text_w)/2:y={y_pos}"
        f":alpha='{alpha_expr}'"
    )

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"Text overlay failed: {e.stderr.decode()[:300]}")
        return input_path  # return without overlay rather than crash


def render_transitional(
    bait_clip: str,
    content_segments: list[str],
    audio_path: str,
    audio_start: float,
    hook_text: str,
    track_label: str,
    platform: str,
    output_path: str,
    target_duration: float = 22.0,
) -> str:
    """Render a transitional hook clip.

    1. Crop bait clip to vertical (muted)
    2. Crop + concat content segments
    3. Hard cut: bait + content
    4. Overlay track audio from 0:00
    5. Burn hook text on bait portion
    6. Burn track label on content portion
    7. Platform color grade
    """
    work_dir = Path(output_path).parent
    work_dir.mkdir(parents=True, exist_ok=True)

    bait_info = _get_video_info(bait_clip)
    bait_duration = min(bait_info["duration"], 7.0)  # cap at 7s
    content_duration = target_duration - bait_duration

    # 1. Crop bait to vertical, strip audio
    bait_vert = str(work_dir / "_bait_vert.mp4")
    _crop_to_vertical(bait_clip, bait_vert)

    # 2. Prepare content segments — concat and trim to content_duration
    if len(content_segments) == 1:
        content_vert = str(work_dir / "_content_vert.mp4")
        _crop_to_vertical(content_segments[0], content_vert)
    else:
        # Concat multiple segments
        seg_files = []
        for i, seg in enumerate(content_segments):
            seg_path = str(work_dir / f"_seg_{i}.mp4")
            _crop_to_vertical(seg, seg_path)
            seg_files.append(seg_path)

        concat_list = str(work_dir / "_concat.txt")
        with open(concat_list, "w") as f:
            for sf in seg_files:
                f.write(f"file '{sf}'\n")

        content_vert = str(work_dir / "_content_vert.mp4")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-t", str(content_duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-an", content_vert,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 3. Concat bait + content (hard cut)
    concat_final = str(work_dir / "_concat_final.txt")
    with open(concat_final, "w") as f:
        f.write(f"file '{bait_vert}'\n")
        f.write(f"file '{content_vert}'\n")

    raw_video = str(work_dir / "_raw_concat.mp4")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_final,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-an", raw_video,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 4. Overlay track audio from 0:00
    from content_engine.audio_engine import mix_audio_onto_video
    with_audio = str(work_dir / "_with_audio.mp4")
    mix_audio_onto_video(raw_video, audio_path, audio_start, target_duration, with_audio)

    # 5. Burn hook text on bait portion (0s to bait_duration)
    with_hook = str(work_dir / "_with_hook.mp4")
    _burn_text_overlay(with_audio, with_hook, hook_text, "transitional", 0.0, bait_duration - 0.3)

    # 6. Burn track label on content portion
    with_label = str(work_dir / "_with_label.mp4")
    _burn_text_overlay(with_hook, with_label, track_label, "performance", bait_duration + 0.5)

    # 7. Platform color grade
    _apply_color_grade(with_label, output_path, platform)

    return output_path


def render_emotional(
    content_segments: list[str],
    audio_path: str,
    audio_start: float,
    hook_text: str,
    platform: str,
    output_path: str,
    target_duration: float = 7.0,
) -> str:
    """Render an emotional/POV text hook clip (7s).

    Text hook is prominent. Video is background.
    """
    work_dir = Path(output_path).parent
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Prepare content (single segment or concat)
    if len(content_segments) == 1:
        content_vert = str(work_dir / "_emo_vert.mp4")
        _crop_to_vertical(content_segments[0], content_vert)
    else:
        seg_files = []
        for i, seg in enumerate(content_segments):
            sp = str(work_dir / f"_emo_seg_{i}.mp4")
            _crop_to_vertical(seg, sp)
            seg_files.append(sp)
        concat_list = str(work_dir / "_emo_concat.txt")
        with open(concat_list, "w") as f:
            for sf in seg_files:
                f.write(f"file '{sf}'\n")
        content_vert = str(work_dir / "_emo_vert.mp4")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list, "-t", str(target_duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-an", content_vert,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 2. Trim to target duration
    trimmed = str(work_dir / "_emo_trimmed.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", content_vert, "-t", str(target_duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-an", trimmed,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 3. Add audio
    from content_engine.audio_engine import mix_audio_onto_video
    with_audio = str(work_dir / "_emo_audio.mp4")
    mix_audio_onto_video(trimmed, audio_path, audio_start, target_duration, with_audio)

    # 4. Burn prominent hook text
    with_hook = str(work_dir / "_emo_hook.mp4")
    _burn_text_overlay(with_audio, with_hook, hook_text, "emotional")

    # 5. Color grade
    _apply_color_grade(with_hook, output_path, platform)

    return output_path


def render_performance(
    content_segments: list[str],
    audio_path: str,
    audio_start: float,
    hook_text: str,
    platform: str,
    output_path: str,
    target_duration: float = 28.0,
) -> str:
    """Render a performance energy clip (28s).

    Music carries it. Minimal text. More segments, faster cuts.
    """
    work_dir = Path(output_path).parent
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Crop + concat all segments
    seg_files = []
    seg_duration = target_duration / max(len(content_segments), 1)
    for i, seg in enumerate(content_segments):
        sp = str(work_dir / f"_perf_seg_{i}.mp4")
        # Crop to vertical and trim to segment duration
        cmd = [
            "ffmpeg", "-y", "-i", seg,
            "-t", str(seg_duration),
            "-vf", f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,crop={OUTPUT_W}:{OUTPUT_H}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-an", sp,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        seg_files.append(sp)

    if len(seg_files) == 1:
        concat_out = seg_files[0]
    else:
        concat_list = str(work_dir / "_perf_concat.txt")
        with open(concat_list, "w") as f:
            for sf in seg_files:
                f.write(f"file '{sf}'\n")
        concat_out = str(work_dir / "_perf_concat.mp4")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list, "-t", str(target_duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-an", concat_out,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    # 2. Add audio (peak energy section)
    from content_engine.audio_engine import mix_audio_onto_video
    with_audio = str(work_dir / "_perf_audio.mp4")
    mix_audio_onto_video(concat_out, audio_path, audio_start, target_duration, with_audio)

    # 3. Minimal text overlay
    with_text = str(work_dir / "_perf_text.mp4")
    _burn_text_overlay(with_audio, with_text, hook_text, "performance")

    # 4. Color grade
    _apply_color_grade(with_text, output_path, platform)

    return output_path


def render_story_variant(
    source_clip: str,
    track_title: str,
    spotify_url: str,
    output_path: str,
) -> str:
    """Render a Stories variant with Spotify CTA overlay.

    Takes an already-rendered clip and adds "Listen on Spotify" + track title
    in the bottom 15%.
    """
    cta_text = f"Listen on Spotify: {track_title}"
    y_pos = int(OUTPUT_H * 0.88)  # bottom 12%

    escaped = cta_text.replace("'", "\\'").replace(":", "\\:")

    font = "/System/Library/Fonts/Helvetica.ttc"
    for candidate in [
        os.path.expanduser("~/.fonts/BebasNeue-Regular.ttf"),
        "/Library/Fonts/BebasNeue-Regular.ttf",
    ]:
        if os.path.exists(candidate):
            font = candidate
            break

    drawtext = (
        f"drawtext=text='{escaped}'"
        f":fontfile='{font}':fontsize=42"
        f":fontcolor=white:shadowcolor=black@0.8:shadowx=2:shadowy=2"
        f":x=(w-text_w)/2:y={y_pos}"
    )

    cmd = [
        "ffmpeg", "-y", "-i", source_clip,
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return output_path
    except subprocess.CalledProcessError:
        logger.warning("Story CTA overlay failed, using original clip")
        import shutil
        shutil.copy2(source_clip, output_path)
        return output_path
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python3 -m pytest content_engine/tests/test_renderer.py -v
```

- [ ] **Step 5: Commit**

```bash
git add content_engine/renderer.py content_engine/tests/test_renderer.py
git commit -m "feat(renderer): unified 3-format video renderer with Stories variant + validation"
```

---

## Task 7: Transitional Hook Infrastructure

**Files:**
- Create: `content/hooks/transitional/index.json`
- Create: `content_engine/transitional_manager.py`
- Test: `content_engine/tests/test_transitional_manager.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p content/hooks/transitional/{nature,satisfying,elemental,sports,craftsmanship,illusion}
```

- [ ] **Step 2: Create index.json (empty, ready for manual clip drops)**

```json
[]
```

Note: Actual clip downloads are manual (from VideoHooks.app free tier). The manager handles selection and tracking once clips are present.

- [ ] **Step 3: Write tests for transitional manager**

```python
# content_engine/tests/test_transitional_manager.py
import json
import pytest
from pathlib import Path
from content_engine.transitional_manager import TransitionalManager


@pytest.fixture
def populated_index(tmp_path):
    hooks_dir = tmp_path / "hooks" / "transitional"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "nature").mkdir()
    (hooks_dir / "satisfying").mkdir()

    # Create dummy video files
    (hooks_dir / "nature" / "wave_01.mp4").write_bytes(b"x" * 1000)
    (hooks_dir / "nature" / "aurora_01.mp4").write_bytes(b"x" * 1000)
    (hooks_dir / "satisfying" / "soap_01.mp4").write_bytes(b"x" * 1000)

    index = [
        {"file": "nature/wave_01.mp4", "category": "nature", "duration_s": 3.5,
         "last_used": None, "performance_score": 1.0, "times_used": 0},
        {"file": "nature/aurora_01.mp4", "category": "nature", "duration_s": 4.0,
         "last_used": None, "performance_score": 1.5, "times_used": 0},
        {"file": "satisfying/soap_01.mp4", "category": "satisfying", "duration_s": 3.0,
         "last_used": None, "performance_score": 1.0, "times_used": 0},
    ]
    (hooks_dir / "index.json").write_text(json.dumps(index, indent=2))
    return hooks_dir


def test_load_bank(populated_index):
    mgr = TransitionalManager(populated_index)
    assert len(mgr.bank) == 3


def test_pick_hook(populated_index):
    mgr = TransitionalManager(populated_index)
    hook = mgr.pick()
    assert hook is not None
    assert "file" in hook


def test_mark_used_updates_index(populated_index):
    mgr = TransitionalManager(populated_index)
    hook = mgr.pick()
    mgr.mark_used(hook["file"])
    reloaded = json.loads((populated_index / "index.json").read_text())
    used_hook = next(h for h in reloaded if h["file"] == hook["file"])
    assert used_hook["last_used"] is not None
    assert used_hook["times_used"] == 1


def test_pick_respects_cooldown(populated_index):
    mgr = TransitionalManager(populated_index)
    from datetime import date
    today = date.today().isoformat()
    # Mark all nature hooks as used today
    for h in mgr.bank:
        if h["category"] == "nature":
            h["last_used"] = today
    hook = mgr.pick()
    # Should pick satisfying since nature is on cooldown
    assert hook["category"] == "satisfying"


def test_scan_for_new_clips(populated_index):
    # Add a new file not in index
    (populated_index / "nature" / "new_clip.mp4").write_bytes(b"x" * 1000)
    mgr = TransitionalManager(populated_index)
    mgr.scan_for_new_clips()
    assert len(mgr.bank) == 4
    assert any(h["file"] == "nature/new_clip.mp4" for h in mgr.bank)


def test_full_path(populated_index):
    mgr = TransitionalManager(populated_index)
    hook = mgr.pick()
    full = mgr.full_path(hook["file"])
    assert full.exists()
```

- [ ] **Step 4: Implement transitional_manager.py**

```python
"""
transitional_manager.py — Manage pre-cleared transitional hook clips.

Handles: loading index, picking clips (weighted, cooldown, diversity),
scanning for new clips, updating usage/performance.
"""
import json
import logging
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from content_engine.hook_library import pick_transitional_hook

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
DEFAULT_HOOKS_DIR = PROJECT_DIR / "content" / "hooks" / "transitional"
CATEGORIES = ["nature", "satisfying", "elemental", "sports", "craftsmanship", "illusion"]


class TransitionalManager:
    """Manages the transitional hook clip library."""

    def __init__(self, hooks_dir: Optional[Path] = None):
        self.hooks_dir = hooks_dir or DEFAULT_HOOKS_DIR
        self.index_path = self.hooks_dir / "index.json"
        self.bank: list[dict] = []
        self._load()

    def _load(self):
        if self.index_path.exists():
            self.bank = json.loads(self.index_path.read_text())
        else:
            self.bank = []

    def _save(self):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self.bank, indent=2))

    def pick(self, yesterday_category: Optional[str] = None) -> Optional[dict]:
        """Pick a transitional hook clip respecting cooldown and diversity rules."""
        if not self.bank:
            logger.warning("Transitional hook bank is empty")
            return None
        return pick_transitional_hook(self.bank, yesterday_category)

    def mark_used(self, file: str):
        """Mark a clip as used today and save."""
        for h in self.bank:
            if h["file"] == file:
                h["last_used"] = date.today().isoformat()
                h["times_used"] = h.get("times_used", 0) + 1
                break
        self._save()

    def update_score(self, file: str, new_score: float):
        """Update performance score for a clip."""
        for h in self.bank:
            if h["file"] == file:
                h["performance_score"] = new_score
                break
        self._save()

    def full_path(self, file: str) -> Path:
        """Get full filesystem path for a hook clip."""
        return self.hooks_dir / file

    def scan_for_new_clips(self):
        """Scan hooks_dir for MP4/MOV files not yet in the index."""
        existing_files = {h["file"] for h in self.bank}
        for category in CATEGORIES:
            cat_dir = self.hooks_dir / category
            if not cat_dir.exists():
                continue
            for f in cat_dir.iterdir():
                if f.suffix.lower() in (".mp4", ".mov"):
                    rel = f"{category}/{f.name}"
                    if rel not in existing_files:
                        duration = self._get_duration(str(f))
                        self.bank.append({
                            "file": rel,
                            "category": category,
                            "duration_s": duration,
                            "last_used": None,
                            "performance_score": 1.0,
                            "times_used": 0,
                        })
                        logger.info(f"Added new transitional hook: {rel} ({duration:.1f}s)")
        self._save()

    def _get_duration(self, path: str) -> float:
        """Get clip duration via ffprobe."""
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return float(result.stdout.strip())
        except Exception:
            return 3.0  # default assumption

    def health_check(self) -> dict:
        """Check bank health: total clips, per-category, cooldown pool size."""
        total = len(self.bank)
        per_cat = {}
        available = 0
        cooldown_date = date.today() - timedelta(days=7)

        for h in self.bank:
            cat = h["category"]
            per_cat[cat] = per_cat.get(cat, 0) + 1
            if not h["last_used"] or date.fromisoformat(h["last_used"]) < cooldown_date:
                available += 1

        return {
            "total": total,
            "available_after_cooldown": available,
            "per_category": per_cat,
            "healthy": available >= 7,  # need at least 7 for one per day
        }
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
python3 -m pytest content_engine/tests/test_transitional_manager.py -v
```

- [ ] **Step 6: Commit**

```bash
git add content/hooks/transitional/index.json content_engine/transitional_manager.py content_engine/tests/test_transitional_manager.py
git commit -m "feat(transitional): hook clip manager with cooldown, diversity, auto-scan"
```

---

## Task 8: Expand distributor.py — 6 Targets + Retry + Circuit Breaker

**Files:**
- Modify: `content_engine/distributor.py`
- Test: `content_engine/tests/test_distributor.py`

Add: IG Stories, FB Stories, TikTok via Buffer, retry with backoff, circuit breaker.

- [ ] **Step 1: Write failing tests**

```python
# content_engine/tests/test_distributor.py
import pytest
from content_engine.distributor import (
    DISTRIBUTION_TARGETS,
    POST_SCHEDULE,
    _scheduled_at_utc,
    CircuitBreaker,
)


def test_distribution_targets():
    expected = {"instagram", "youtube", "facebook", "tiktok", "instagram_story", "facebook_story"}
    assert set(DISTRIBUTION_TARGETS) == expected


def test_post_schedule_has_6_targets():
    for target in DISTRIBUTION_TARGETS:
        assert target in POST_SCHEDULE


def test_scheduled_at_utc():
    result = _scheduled_at_utc("instagram", 0)
    assert "T" in result  # ISO format


def test_circuit_breaker_init():
    cb = CircuitBreaker()
    assert not cb.is_open("instagram")


def test_circuit_breaker_trips_after_3():
    cb = CircuitBreaker(threshold=3)
    cb.record_failure("instagram")
    cb.record_failure("instagram")
    assert not cb.is_open("instagram")
    cb.record_failure("instagram")
    assert cb.is_open("instagram")


def test_circuit_breaker_reset():
    cb = CircuitBreaker(threshold=3)
    for _ in range(3):
        cb.record_failure("instagram")
    assert cb.is_open("instagram")
    cb.reset("instagram")
    assert not cb.is_open("instagram")


def test_circuit_breaker_success_resets():
    cb = CircuitBreaker(threshold=3)
    cb.record_failure("instagram")
    cb.record_failure("instagram")
    cb.record_success("instagram")
    assert not cb.is_open("instagram")
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python3 -m pytest content_engine/tests/test_distributor.py -v
```

- [ ] **Step 3: Add new targets and circuit breaker to distributor.py**

Add at the top of `distributor.py`:

```python
# After existing imports and constants, add:

DISTRIBUTION_TARGETS = [
    "instagram", "youtube", "facebook", "tiktok",
    "instagram_story", "facebook_story",
]

# Expand POST_SCHEDULE to include all 6 targets
POST_SCHEDULE = {
    "instagram":       ["08:30", "13:00", "19:30"],
    "youtube":         ["09:00", "13:30", "20:00"],
    "facebook":        ["09:15", "13:45", "20:15"],
    "tiktok":          ["09:30", "14:00", "20:30"],
    "instagram_story": ["08:45", "13:15", "19:45"],
    "facebook_story":  ["09:15", "13:45", "20:15"],
}


class CircuitBreaker:
    """Track consecutive failures per platform. Trip after threshold."""

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._failures: dict[str, int] = {}
        self._tripped: set[str] = set()

    def record_failure(self, platform: str):
        self._failures[platform] = self._failures.get(platform, 0) + 1
        if self._failures[platform] >= self.threshold:
            self._tripped.add(platform)
            logger.warning(f"Circuit breaker TRIPPED for {platform} after {self.threshold} consecutive failures")

    def record_success(self, platform: str):
        self._failures[platform] = 0
        self._tripped.discard(platform)

    def is_open(self, platform: str) -> bool:
        return platform in self._tripped

    def reset(self, platform: str):
        self._failures[platform] = 0
        self._tripped.discard(platform)
```

Add the Story posting functions:

```python
def post_instagram_story(video_path: str, caption: str, ig_user_id: str,
                         access_token: str, spotify_url: str = "") -> dict:
    """Post an Instagram Story via Graph API. Same flow as Reel but media_type=STORIES."""
    try:
        # Step 1: Upload video to hosting
        from outreach_agent.buffer_poster import upload_video
        video_url = upload_video(video_path)
        if not video_url:
            return {"success": False, "error": "video upload failed"}

        # Step 2: Create media container (STORIES type)
        url = f"https://graph.instagram.com/v21.0/{ig_user_id}/media"
        params = {
            "video_url": video_url,
            "media_type": "STORIES",
            "caption": caption,
            "access_token": access_token,
        }
        if spotify_url:
            params["link"] = spotify_url  # Link sticker

        resp = requests.post(url, data=params, timeout=30)
        data = resp.json()
        container_id = data.get("id")
        if not container_id:
            return {"success": False, "error": f"container creation failed: {data}"}

        # Step 3: Poll for FINISHED status (same as Reels)
        import time
        for _ in range(24):  # 2 minutes
            time.sleep(5)
            status_url = f"https://graph.instagram.com/v21.0/{container_id}"
            status_resp = requests.get(status_url, params={
                "fields": "status_code", "access_token": access_token
            }, timeout=15)
            status = status_resp.json().get("status_code", "")
            if status == "FINISHED":
                break
            if status == "ERROR":
                return {"success": False, "error": "container processing error"}

        # Step 4: Publish
        publish_url = f"https://graph.instagram.com/v21.0/{ig_user_id}/media_publish"
        pub_resp = requests.post(publish_url, data={
            "creation_id": container_id,
            "access_token": access_token,
        }, timeout=30)
        pub_data = pub_resp.json()
        return {"success": True, "post_id": pub_data.get("id", container_id)}

    except Exception as e:
        return {"success": False, "error": str(e)}


def post_facebook_story(video_path: str, page_id: str, page_token: str) -> dict:
    """Post a Facebook Story via Graph API."""
    try:
        url = f"https://graph.facebook.com/v21.0/{page_id}/video_stories"

        # Step 1: Initialize upload
        init_resp = requests.post(url, data={
            "upload_phase": "start",
            "access_token": page_token,
        }, timeout=30)
        init_data = init_resp.json()
        video_id = init_data.get("video_id")
        if not video_id:
            return {"success": False, "error": f"story init failed: {init_data}"}

        # Step 2: Upload video
        upload_url = f"https://graph.facebook.com/v21.0/{video_id}"
        with open(video_path, "rb") as f:
            upload_resp = requests.post(
                upload_url,
                files={"source": f},
                data={"access_token": page_token, "upload_phase": "transfer"},
                timeout=120,
            )

        # Step 3: Finish
        finish_resp = requests.post(url, data={
            "upload_phase": "finish",
            "video_id": video_id,
            "access_token": page_token,
        }, timeout=30)
        finish_data = finish_resp.json()
        return {"success": finish_data.get("success", False), "post_id": str(video_id)}

    except Exception as e:
        return {"success": False, "error": str(e)}
```

Update `distribute_all` to handle 6 targets with retry and circuit breaker:

```python
def distribute_all(clips: list, circuit_breaker: CircuitBreaker | None = None) -> list:
    """Distribute clips to all 6 targets with retry + circuit breaker."""
    cb = circuit_breaker or CircuitBreaker()
    results = []

    for clip in clips:
        for target in DISTRIBUTION_TARGETS:
            if cb.is_open(target):
                logger.warning(f"Skipping {target} — circuit breaker open")
                results.append({"platform": target, "success": False, "error": "circuit breaker open"})
                continue

            result = _distribute_with_retry(clip, target, max_retries=3)
            if result.get("success"):
                cb.record_success(target)
            else:
                cb.record_failure(target)
            results.append(result)

    return results


def _distribute_with_retry(clip: dict, target: str, max_retries: int = 3) -> dict:
    """Distribute to a single target with exponential backoff retry."""
    import time
    delays = [2, 8, 32]

    for attempt in range(max_retries):
        result = _distribute_single(clip, target)
        if result.get("success"):
            return result
        if attempt < max_retries - 1:
            logger.warning(f"Retry {attempt + 1}/{max_retries} for {target}: {result.get('error', '')}")
            time.sleep(delays[attempt])

    # Final fallback to Buffer (for Reel targets only, not Stories)
    if target in ("instagram", "youtube", "facebook"):
        logger.info(f"Falling back to Buffer for {target}")
        return _buffer_fallback(clip, _scheduled_at_utc(target, clip.get("clip_index", 0)))

    return result


def _distribute_single(clip: dict, target: str) -> dict:
    """Route a clip to the correct posting function."""
    platform_base = target.replace("_story", "")

    if target == "instagram":
        return distribute_clip({**clip, "platform": "instagram"})
    elif target == "youtube":
        return distribute_clip({**clip, "platform": "youtube"})
    elif target == "facebook":
        return distribute_clip({**clip, "platform": "facebook"})
    elif target == "tiktok":
        return _buffer_fallback(clip, _scheduled_at_utc("tiktok", clip.get("clip_index", 0)))
    elif target == "instagram_story":
        story_path = clip.get("story_path", clip.get("path", ""))
        ig_user_id = os.environ.get("INSTAGRAM_USER_ID", "")
        access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        spotify_url = clip.get("spotify_url", "")
        return post_instagram_story(story_path, clip.get("caption", ""), ig_user_id, access_token, spotify_url)
    elif target == "facebook_story":
        story_path = clip.get("story_path", clip.get("path", ""))
        page_id = os.environ.get("FACEBOOK_PAGE_ID", "")
        page_token = os.environ.get("FACEBOOK_PAGE_TOKEN", "")
        return post_facebook_story(story_path, page_id, page_token)

    return {"success": False, "error": f"unknown target: {target}"}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python3 -m pytest content_engine/tests/test_distributor.py -v
```

- [ ] **Step 5: Commit**

```bash
git add content_engine/distributor.py content_engine/tests/test_distributor.py
git commit -m "feat(distributor): 6 targets (IG/YT/FB/TikTok/IG Stories/FB Stories) + retry + circuit breaker"
```

---

## Task 9: Expand spotify_watcher.py — Releases + Popularity + Audio Features

**Files:**
- Modify: `content_engine/spotify_watcher.py`
- Test: `content_engine/tests/test_spotify_watcher.py`

- [ ] **Step 1: Write failing tests**

```python
# content_engine/tests/test_spotify_watcher.py
import pytest
from content_engine.spotify_watcher import (
    fetch_new_releases,
    fetch_track_popularity,
    fetch_audio_features,
    _get_user_token,
)


def test_fetch_new_releases_returns_list():
    """Should return a list (empty if no API key)."""
    result = fetch_new_releases()
    assert isinstance(result, list)


def test_fetch_track_popularity_returns_int():
    """Returns 0 if no API access."""
    result = fetch_track_popularity("fake_track_id")
    assert isinstance(result, int)
    assert 0 <= result <= 100


def test_fetch_audio_features_returns_dict():
    result = fetch_audio_features("fake_track_id")
    assert isinstance(result, dict)
    assert "bpm" in result
    assert "energy" in result
```

- [ ] **Step 2: Implement new functions in spotify_watcher.py**

Add to the existing file (after the existing follower-tracking code):

```python
# ─── New Release Detection ───────────────────────────────────────────────────

ARTIST_ID = "2Seaafm5k1hAuCkpdq7yds"


def _get_client_token() -> Optional[str]:
    """Get Spotify client credentials token."""
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        log.warning("SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET not set")
        return None

    import base64
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    if resp.status_code == 200:
        return resp.json()["access_token"]
    log.error(f"Client token failed: {resp.status_code} {resp.text[:200]}")
    return None


def _get_user_token() -> Optional[str]:
    """Get Spotify user token via refresh token (Premium required)."""
    refresh_token = os.environ.get("SPOTIFY_USER_REFRESH_TOKEN", "")
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    if not all([refresh_token, client_id, client_secret]):
        return None

    import base64
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=15,
    )
    if resp.status_code == 200:
        data = resp.json()
        # Persist new refresh token if provided
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != refresh_token:
            _update_env("SPOTIFY_USER_REFRESH_TOKEN", new_refresh)
        return data["access_token"]
    return None


def fetch_new_releases() -> list[dict]:
    """Fetch artist's recent singles. Returns [{id, title, release_date}]."""
    token = _get_client_token()
    if not token:
        return []

    try:
        resp = requests.get(
            f"https://api.spotify.com/v1/artists/{ARTIST_ID}/albums",
            headers={"Authorization": f"Bearer {token}"},
            params={"include_groups": "single", "limit": 10, "market": "US"},
            timeout=15,
        )
        if resp.status_code != 200:
            return []

        albums = resp.json().get("items", [])
        releases = []
        for album in albums:
            releases.append({
                "id": album["id"],
                "title": album["name"],
                "release_date": album.get("release_date", ""),
                "tracks": [],
            })
            # Fetch tracks for each single
            tracks_resp = requests.get(
                f"https://api.spotify.com/v1/albums/{album['id']}/tracks",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": 5},
                timeout=15,
            )
            if tracks_resp.status_code == 200:
                for track in tracks_resp.json().get("items", []):
                    releases[-1]["tracks"].append({
                        "id": track["id"],
                        "title": track["name"],
                    })
        return releases
    except Exception as e:
        log.error(f"fetch_new_releases failed: {e}")
        return []


def fetch_track_popularity(track_id: str) -> int:
    """Fetch a track's popularity score (0-100)."""
    token = _get_client_token()
    if not token:
        return 0
    try:
        resp = requests.get(
            f"https://api.spotify.com/v1/tracks/{track_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("popularity", 0)
        return 0
    except Exception:
        return 0


def fetch_audio_features(track_id: str) -> dict:
    """Fetch track audio features (BPM, energy, danceability, valence)."""
    token = _get_client_token()
    if not token:
        return {"bpm": 128, "energy": 0.7, "danceability": 0.7, "valence": 0.5}
    try:
        resp = requests.get(
            f"https://api.spotify.com/v1/audio-features/{track_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            bpm = int(round(data.get("tempo", 128)))
            if bpm < 100:
                bpm *= 2  # detect half-tempo
            return {
                "bpm": bpm,
                "energy": data.get("energy", 0.7),
                "danceability": data.get("danceability", 0.7),
                "valence": data.get("valence", 0.5),
            }
        return {"bpm": 128, "energy": 0.7, "danceability": 0.7, "valence": 0.5}
    except Exception:
        return {"bpm": 128, "energy": 0.7, "danceability": 0.7, "valence": 0.5}


def _update_env(key: str, value: str):
    """Update a key in .env file."""
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        return
    lines = env_file.read_text().splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n")
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest content_engine/tests/test_spotify_watcher.py -v
```

- [ ] **Step 4: Commit**

```bash
git add content_engine/spotify_watcher.py content_engine/tests/test_spotify_watcher.py
git commit -m "feat(spotify): new release detection, track popularity, audio features"
```

---

## Task 10: Expand learning_loop.py — Multi-Dimensional Learning

**Files:**
- Modify: `content_engine/learning_loop.py`
- Test: `content_engine/tests/test_learning_loop.py`

Expand to learn per: format, platform, template, track, transitional category.

- [ ] **Step 1: Write failing tests**

```python
# content_engine/tests/test_learning_loop.py
import json
import pytest
from pathlib import Path
from content_engine.learning_loop import (
    calculate_unified_weights,
    track_rotation_vote,
    update_template_lifecycle,
)
from content_engine.types import UnifiedWeights


def test_calculate_unified_weights():
    """Weights should update via EMA."""
    old = UnifiedWeights.defaults()
    records = [
        {
            "format_type": "transitional", "hook_template_id": "save.wait_for_drop",
            "hook_mechanism": "dare", "visual_type": "b_roll", "platform": "instagram",
            "transitional_category": "nature", "track_title": "Jericho",
            "completion_rate": 0.8, "save_rate": 0.05, "scroll_stop_rate": 0.3,
        },
        {
            "format_type": "emotional", "hook_template_id": "save.for_you_if",
            "hook_mechanism": "save", "visual_type": "phone", "platform": "youtube",
            "transitional_category": "", "track_title": "Renamed",
            "completion_rate": 0.6, "save_rate": 0.02, "scroll_stop_rate": 0.2,
        },
    ]
    new_weights = calculate_unified_weights(records, old)
    assert isinstance(new_weights, UnifiedWeights)
    assert new_weights.updated != ""


def test_track_rotation_vote():
    pool = [
        {"title": "Jericho", "spotify_popularity": 60, "video_save_rate": 0.05},
        {"title": "Renamed", "spotify_popularity": 40, "video_save_rate": 0.02},
        {"title": "Halleluyah", "spotify_popularity": 50, "video_save_rate": 0.03},
        {"title": "Fire In Our Hands", "spotify_popularity": 45, "video_save_rate": 0.01},
    ]
    new_release = {"title": "New Track", "spotify_popularity": 55, "video_save_rate": 0.04}
    result = track_rotation_vote(pool, new_release, min_days=0)
    assert "action" in result
    assert result["action"] in ("swap", "keep", "add")


def test_update_template_lifecycle():
    template_scores = {
        "save.wait_for_drop": 2.0,   # top performer
        "save.for_you_if": 0.3,      # bottom performer
        "save.pov_driving": 1.0,     # neutral
    }
    result = update_template_lifecycle(template_scores, days_active=15)
    assert result["save.wait_for_drop"]["priority"] == 2.0
    assert result["save.for_you_if"]["priority"] == 0.3
```

- [ ] **Step 2: Implement expanded learning loop functions**

Add to `content_engine/learning_loop.py`:

```python
def calculate_unified_weights(
    records: list[dict],
    old_weights: "UnifiedWeights",
    learning_rate: float = 0.3,
) -> "UnifiedWeights":
    """Calculate new unified weights from today's performance records.

    Updates per: format, platform, template, visual, track, transitional category.
    Signal = completion_rate * 0.5 + save_rate * 0.3 + scroll_stop_rate * 0.2
    """
    from datetime import datetime
    from content_engine.types import UnifiedWeights

    new = UnifiedWeights(
        hook_weights=dict(old_weights.hook_weights),
        visual_weights=dict(old_weights.visual_weights),
        format_weights=dict(old_weights.format_weights),
        platform_weights=dict(old_weights.platform_weights),
        transitional_category_weights=dict(old_weights.transitional_category_weights),
        track_weights=dict(old_weights.track_weights),
        best_clip_length=old_weights.best_clip_length,
        best_platform=old_weights.best_platform,
        updated=datetime.now().isoformat(),
    )

    if not records:
        return new

    # Compute signal per record
    signals = []
    for r in records:
        signal = (
            r.get("completion_rate", 0) * 0.5
            + r.get("save_rate", 0) * 0.3
            + r.get("scroll_stop_rate", 0) * 0.2
        )
        signals.append((r, signal))

    # Group signals by dimension and update via EMA
    _ema_update(new.format_weights, signals, "format_type", learning_rate)
    _ema_update(new.platform_weights, signals, "platform", learning_rate)
    _ema_update(new.hook_weights, signals, "hook_template_id", learning_rate)
    _ema_update(new.visual_weights, signals, "visual_type", learning_rate)
    _ema_update(new.track_weights, signals, "track_title", learning_rate)
    _ema_update(new.transitional_category_weights, signals, "transitional_category", learning_rate)

    # Update best_platform
    if new.platform_weights:
        new.best_platform = max(new.platform_weights, key=new.platform_weights.get)

    return new


def _ema_update(weights: dict, signals: list, key: str, lr: float):
    """EMA update for a single dimension."""
    from collections import defaultdict
    group_signals = defaultdict(list)
    for record, signal in signals:
        val = record.get(key, "")
        if val:
            group_signals[val].append(signal)

    for name, sigs in group_signals.items():
        avg_signal = sum(sigs) / len(sigs)
        # Normalize to 0-2 range (assuming signal is 0-1, multiply by 2)
        normalized = min(avg_signal * 2.0, 2.0)
        old = weights.get(name, 1.0)
        weights[name] = old * (1 - lr) + normalized * lr


def track_rotation_vote(
    pool: list[dict],
    new_release: dict | None = None,
    min_days: int = 7,
) -> dict:
    """Vote on track rotation.

    Composite score: spotify_popularity * 0.4 + video_save_rate * 0.6
    """
    if not pool:
        return {"action": "keep", "reason": "pool empty"}

    scored = []
    for t in pool:
        score = t.get("spotify_popularity", 0) * 0.004 + t.get("video_save_rate", 0) * 0.6
        scored.append((t["title"], score))
    scored.sort(key=lambda x: x[1])
    bottom = scored[0]

    if new_release:
        new_score = new_release.get("spotify_popularity", 0) * 0.004 + new_release.get("video_save_rate", 0) * 0.6
        if new_score > bottom[1]:
            return {
                "action": "swap",
                "remove": bottom[0],
                "add": new_release["title"],
                "reason": f"{new_release['title']} ({new_score:.3f}) > {bottom[0]} ({bottom[1]:.3f})",
            }
        return {"action": "keep", "reason": f"{new_release['title']} score too low"}

    if len(pool) < 4:
        return {"action": "add", "reason": "pool below minimum"}

    return {"action": "keep", "reason": "no change needed"}


def update_template_lifecycle(
    template_scores: dict[str, float],
    days_active: int = 14,
) -> dict:
    """Update template priorities based on EMA scores.

    - Score > 1.5 after 14d → priority = 2.0
    - Score < 0.5 after 14d → priority = 0.3
    - Score < 0.3 after 30d → deprecated (priority = 0.0)
    """
    result = {}
    for template_id, score in template_scores.items():
        if days_active < 14:
            result[template_id] = {"priority": 1.0, "status": "learning"}
        elif score > 1.5:
            result[template_id] = {"priority": 2.0, "status": "boosted"}
        elif score < 0.3 and days_active >= 30:
            result[template_id] = {"priority": 0.0, "status": "deprecated"}
        elif score < 0.5:
            result[template_id] = {"priority": 0.3, "status": "deprioritized"}
        else:
            result[template_id] = {"priority": 1.0, "status": "active"}
    return result
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest content_engine/tests/test_learning_loop.py -v
```

- [ ] **Step 4: Commit**

```bash
git add content_engine/learning_loop.py content_engine/tests/test_learning_loop.py
git commit -m "feat(learning): multi-dimensional weights, track rotation voting, template lifecycle"
```

---

## Task 11: Rewrite pipeline.py — 3 Formats, 6 Targets, Validation

**Files:**
- Modify: `content_engine/pipeline.py`
- Test: `content_engine/tests/test_pipeline.py`

This is the orchestrator. It ties everything together.

- [ ] **Step 1: Write failing tests**

```python
# content_engine/tests/test_pipeline.py
import pytest
from content_engine.pipeline import (
    build_daily_clips,
    DailyPipelineConfig,
)
from content_engine.types import ClipFormat


def test_config_defaults():
    config = DailyPipelineConfig()
    assert len(config.formats) == 3
    assert config.formats[0] == ClipFormat.TRANSITIONAL
    assert config.formats[1] == ClipFormat.EMOTIONAL
    assert config.formats[2] == ClipFormat.PERFORMANCE


def test_config_durations():
    config = DailyPipelineConfig()
    assert config.durations[ClipFormat.TRANSITIONAL] == 22
    assert config.durations[ClipFormat.EMOTIONAL] == 7
    assert config.durations[ClipFormat.PERFORMANCE] == 28
```

- [ ] **Step 2: Implement new pipeline.py**

Rewrite `content_engine/pipeline.py`:

```python
"""
pipeline.py — Unified daily content pipeline orchestrator.

Daily flow:
1. Load trend brief + weights
2. Select track from pool
3. For each of 3 formats: pick visual hook / text hook, render clip, render story variant
4. Distribute all clips to 6 targets
5. Save post registry for learning loop
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Optional

from content_engine.types import (
    ClipFormat, TrendBrief, UnifiedWeights, TransitionalHook,
)

PROJECT_DIR = Path(__file__).parent.parent
PERFORMANCE_DIR = PROJECT_DIR / "data" / "performance"

logger = logging.getLogger(__name__)


@dataclass
class DailyPipelineConfig:
    formats: list = field(default_factory=lambda: [
        ClipFormat.TRANSITIONAL,
        ClipFormat.EMOTIONAL,
        ClipFormat.PERFORMANCE,
    ])
    durations: dict = field(default_factory=lambda: {
        ClipFormat.TRANSITIONAL: 22,
        ClipFormat.EMOTIONAL: 7,
        ClipFormat.PERFORMANCE: 28,
    })
    platforms: list = field(default_factory=lambda: [
        "instagram", "youtube", "facebook", "tiktok",
        "instagram_story", "facebook_story",
    ])


def run_full_day(dry_run: bool = False, config: Optional[DailyPipelineConfig] = None) -> dict:
    """Full daily pipeline run.

    Returns {date, clips_rendered, distributed, failures, dry_run, registry}.
    """
    config = config or DailyPipelineConfig()
    date_str = _date.today().isoformat()
    logger.info(f"[pipeline] Starting unified daily run for {date_str} (dry_run={dry_run})")

    # 1. Load trend brief
    try:
        brief = TrendBrief.load_today()
        logger.info(f"[pipeline] Trend brief loaded: {brief.dominant_emotion}")
    except FileNotFoundError:
        logger.warning("[pipeline] Trend brief missing — running trend_scanner now")
        try:
            from content_engine import trend_scanner
            brief = trend_scanner.run(date_str)
        except Exception as exc:
            logger.warning(f"[pipeline] Trend scanner failed ({exc}) — using default brief")
            brief = TrendBrief(
                date=date_str,
                top_visual_formats=["performance", "b_roll"],
                dominant_emotion="euphoric",
                oversaturated="generic_dance",
                hook_pattern_of_day="tension",
                contrarian_gap="raw authentic moments",
                trend_confidence=0.5,
            )

    # 2. Load weights
    weights = UnifiedWeights.load()
    logger.info(f"[pipeline] Weights loaded — best platform: {weights.best_platform}")

    # 3. Select track
    from content_engine.audio_engine import TrackPool, detect_bpm, find_peak_sections
    pool = TrackPool.load_pool()
    track = pool.select_track(weights.track_weights)
    pool.mark_used(track.title)

    # Detect BPM if not set
    if track.bpm == 0:
        track.bpm = detect_bpm(track.file_path)
    logger.info(f"[pipeline] Track selected: {track.title} ({track.bpm} BPM)")

    # 4. Find peak audio sections (one per format)
    peak_sections = find_peak_sections(track.file_path, max(config.durations.values()), n_sections=3)

    # 5. Build clips
    output_dir = str(PROJECT_DIR / "content" / "output" / date_str)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    clips = build_daily_clips(config, brief, weights, track, peak_sections, output_dir)
    logger.info(f"[pipeline] Rendered {len(clips)} clips")

    # 6. Validate renders
    from content_engine.renderer import validate_output
    valid_clips = []
    for clip in clips:
        result = validate_output(clip["path"], clip["clip_length"])
        if result["valid"]:
            valid_clips.append(clip)
        else:
            logger.error(f"[pipeline] Invalid render for {clip['format_type']}: {result['errors']}")

    if not valid_clips:
        logger.critical("[pipeline] No valid clips rendered — aborting distribution")
        return {"date": date_str, "clips_rendered": len(clips), "distributed": 0, "failures": len(clips), "dry_run": dry_run}

    # 7. Distribute
    if dry_run:
        logger.info("[pipeline] DRY RUN — skipping distribution")
        registry_dir = PERFORMANCE_DIR / "dry-run"
    else:
        from content_engine.distributor import distribute_all
        results = distribute_all(valid_clips)
        success = [r for r in results if r.get("success")]
        failures = [r for r in results if not r.get("success")]
        if failures:
            logger.warning(f"[pipeline] {len(failures)} distribution failures")
        registry_dir = PERFORMANCE_DIR

    # 8. Save post registry
    registry = []
    for clip in valid_clips:
        entry = {
            "platform": "all",
            "format_type": clip["format_type"],
            "clip_index": clip["clip_index"],
            "variant": "a",
            "hook_mechanism": clip.get("hook_mechanism", ""),
            "hook_template_id": clip.get("hook_template_id", ""),
            "hook_sub_mode": clip.get("hook_sub_mode", ""),
            "visual_type": clip.get("visual_type", ""),
            "transitional_category": clip.get("transitional_category", ""),
            "transitional_file": clip.get("transitional_file", ""),
            "track_title": track.title,
            "clip_length": clip["clip_length"],
        }
        # If we distributed, add post IDs per platform
        if not dry_run:
            for r in results:
                if r.get("success") and r.get("platform", "") != "":
                    entry_copy = dict(entry)
                    entry_copy["platform"] = r["platform"]
                    entry_copy["post_id"] = r.get("post_id", "")
                    entry_copy["posted_at"] = r.get("posted_at", "")
                    registry.append(entry_copy)
        else:
            registry.append(entry)

    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_path = registry_dir / f"{date_str}_posts.json"
    registry_path.write_text(json.dumps(registry, indent=2))
    logger.info(f"[pipeline] Post registry saved: {registry_path} ({len(registry)} entries)")

    distributed_count = len([r for r in results if r.get("success")]) if not dry_run else 0

    return {
        "date": date_str,
        "clips_rendered": len(clips),
        "valid_clips": len(valid_clips),
        "distributed": distributed_count,
        "failures": len(clips) - len(valid_clips),
        "dry_run": dry_run,
        "registry": str(registry_path),
    }


def build_daily_clips(
    config: DailyPipelineConfig,
    brief: TrendBrief,
    weights: UnifiedWeights,
    track,
    peak_sections: list[float],
    output_dir: str,
) -> list[dict]:
    """Build 3 clips (one per format)."""
    from content_engine.renderer import render_transitional, render_emotional, render_performance, render_story_variant
    from content_engine.generator import generate_hooks_for_format, generate_caption
    from content_engine.transitional_manager import TransitionalManager
    from content_engine.audio_engine import find_peak_sections
    import random

    clips = []
    video_dirs = [
        str(PROJECT_DIR / "content" / "videos" / "b-roll"),
        str(PROJECT_DIR / "content" / "videos" / "phone-footage"),
        str(PROJECT_DIR / "content" / "videos" / "performances"),
    ]

    # Collect available videos
    all_videos = []
    for vd in video_dirs:
        vd_path = Path(vd)
        if vd_path.exists():
            for f in vd_path.iterdir():
                if f.suffix.lower() in (".mp4", ".mov"):
                    all_videos.append(str(f))

    if not all_videos:
        logger.error("[pipeline] No source videos found!")
        return []

    used_ids = set()
    track_facts = {
        "bpm": track.bpm,
        "scripture_anchor": track.scripture_anchor,
        "energy": track.energy,
    }

    for clip_idx, fmt in enumerate(config.formats):
        duration = config.durations[fmt]
        audio_start = peak_sections[clip_idx] if clip_idx < len(peak_sections) else 30.0

        # Generate hook
        hook_data = generate_hooks_for_format(fmt, track.title, track_facts, weights.hook_weights, used_ids)
        used_ids.add(hook_data["template_id"])

        # Generate caption
        caption = generate_caption(track.title, hook_data["hook"], "instagram", track_facts)

        # Pick content segments
        n_segments = {"transitional": 2, "emotional": 1, "performance": 4}.get(fmt.value, 2)
        segments = random.sample(all_videos, min(n_segments, len(all_videos)))

        output_path = str(Path(output_dir) / f"{fmt.value}_{track.title.lower().replace(' ', '_')}.mp4")
        story_path = str(Path(output_dir) / f"{fmt.value}_{track.title.lower().replace(' ', '_')}_story.mp4")

        clip_meta = {
            "clip_index": clip_idx,
            "format_type": fmt.value,
            "hook_mechanism": hook_data["mechanism"],
            "hook_template_id": hook_data["template_id"],
            "hook_sub_mode": hook_data["sub_mode"],
            "hook_text": hook_data["hook"],
            "caption": caption,
            "track_title": track.title,
            "clip_length": duration,
            "visual_type": "b_roll",  # categorize based on actual segments
            "transitional_category": "",
            "transitional_file": "",
            "spotify_url": f"https://open.spotify.com/artist/2Seaafm5k1hAuCkpdq7yds",
        }

        try:
            if fmt == ClipFormat.TRANSITIONAL:
                # Pick transitional bait clip
                tm = TransitionalManager()
                bait = tm.pick()
                if bait:
                    bait_path = str(tm.full_path(bait["file"]))
                    clip_meta["transitional_category"] = bait["category"]
                    clip_meta["transitional_file"] = bait["file"]
                    tm.mark_used(bait["file"])

                    render_transitional(
                        bait_clip=bait_path,
                        content_segments=segments,
                        audio_path=track.file_path,
                        audio_start=audio_start,
                        hook_text=hook_data["hook"],
                        track_label=f"{track.title} — Robert-Jan Mastenbroek",
                        platform="instagram",
                        output_path=output_path,
                        target_duration=duration,
                    )
                else:
                    logger.warning("[pipeline] No transitional hooks available, falling back to emotional format")
                    render_emotional(segments, track.file_path, audio_start, hook_data["hook"],
                                   "instagram", output_path, duration)

            elif fmt == ClipFormat.EMOTIONAL:
                render_emotional(segments, track.file_path, audio_start, hook_data["hook"],
                               "instagram", output_path, duration)

            elif fmt == ClipFormat.PERFORMANCE:
                render_performance(segments, track.file_path, audio_start, hook_data["hook"],
                                  "instagram", output_path, duration)

            # Render Story variant
            render_story_variant(output_path, track.title, clip_meta["spotify_url"], story_path)

            clip_meta["path"] = output_path
            clip_meta["story_path"] = story_path
            clips.append(clip_meta)

        except Exception as e:
            logger.error(f"[pipeline] Failed to render {fmt.value}: {e}")
            continue

    return clips


if __name__ == "__main__":
    import argparse
    import sys
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_full_day(dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest content_engine/tests/test_pipeline.py -v
```

- [ ] **Step 4: Commit**

```bash
git add content_engine/pipeline.py content_engine/tests/test_pipeline.py
git commit -m "feat(pipeline): unified 3-format orchestrator with validation + dry-run fix"
```

---

## Task 12: Update rjm.py + Clean Up Legacy

**Files:**
- Modify: `rjm.py`
- Delete: `content_engine/visual_engine.py`, `content_engine/assembler.py`

- [ ] **Step 1: Update rjm.py content handler**

Find the `cmd_content` function in `rjm.py` and update the legacy `content` (no subcommand) path to alias to `content viral`:

```python
# In the content command handler, replace the legacy path:
# OLD: elif not args or args[0] not in ["viral", "trend-scan", "learning"]:
#          subprocess.run([...post_today.py...])
# NEW:
elif not args:
    # Legacy: alias to viral pipeline
    print("[content] Running unified pipeline (legacy alias)...")
    return cmd_content(["viral"])
```

- [ ] **Step 2: Add reset-platform subcommand**

```python
elif args[0] == "reset-platform":
    if len(args) < 2:
        print("Usage: rjm.py content reset-platform <platform_name>")
        return
    platform = args[1]
    # Reset circuit breaker state (stored in data/circuit_breaker.json)
    cb_path = PROJECT_DIR / "data" / "circuit_breaker.json"
    if cb_path.exists():
        import json
        state = json.loads(cb_path.read_text())
        state.pop(platform, None)
        cb_path.write_text(json.dumps(state, indent=2))
        print(f"Circuit breaker reset for {platform}")
    else:
        print(f"No circuit breaker state found")
```

- [ ] **Step 3: Delete legacy files**

```bash
git rm content_engine/visual_engine.py
git rm content_engine/assembler.py
```

- [ ] **Step 4: Update content_engine/__init__.py**

```python
# content_engine/__init__.py
"""Unified Holy Rave daily content pipeline."""
```

- [ ] **Step 5: Commit**

```bash
git add rjm.py content_engine/__init__.py
git commit -m "feat(rjm): single entry point, legacy alias, delete assembler + visual_engine"
```

---

## Task 13: Integration Test — Dry Run End-to-End

**Files:**
- Test: `content_engine/tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# content_engine/tests/test_integration.py
"""
Integration test: run the full pipeline in dry-run mode.
Requires: source videos in content/videos/, audio in content/audio/masters/
"""
import pytest
import json
from pathlib import Path


@pytest.fixture
def project_dir():
    return Path(__file__).parent.parent.parent


def test_dry_run_produces_clips(project_dir):
    """Full pipeline dry-run should produce 3 clips + 3 story variants."""
    from content_engine.pipeline import run_full_day

    result = run_full_day(dry_run=True)

    assert result["dry_run"] is True
    assert result["clips_rendered"] >= 1, "Should render at least 1 clip"

    # Check registry was written to dry-run dir
    registry_path = Path(result.get("registry", ""))
    if registry_path.exists():
        registry = json.loads(registry_path.read_text())
        assert len(registry) >= 1

    # Check output directory has files
    output_dir = project_dir / "content" / "output" / result["date"]
    if output_dir.exists():
        mp4_files = list(output_dir.glob("*.mp4"))
        assert len(mp4_files) >= 1


def test_hook_variety():
    """Multiple runs should produce different hooks."""
    from content_engine.generator import generate_hooks_for_format
    from content_engine.types import ClipFormat

    hooks = set()
    for _ in range(5):
        result = generate_hooks_for_format(
            ClipFormat.EMOTIONAL, "Jericho",
            {"bpm": 140, "scripture_anchor": "Joshua 6"},
        )
        hooks.add(result["template_id"])

    # Should use at least 2 different templates in 5 runs
    assert len(hooks) >= 2, f"Only used {len(hooks)} templates in 5 runs — not enough variety"
```

- [ ] **Step 2: Run integration test**

```bash
python3 -m pytest content_engine/tests/test_integration.py -v --timeout=300
```

- [ ] **Step 3: Final commit**

```bash
git add content_engine/tests/test_integration.py
git commit -m "test: integration test — dry-run pipeline + hook variety check"
```

---

## Self-Review Checklist

**1. Spec coverage:** Every section of the spec (1-14) is covered:
- §1-2 Problem/Goal → Task 11 (pipeline rewrite)
- §3.1-3.5 Architecture → Tasks 2-7 (module migration/creation), Task 12 (cleanup)
- §4 Daily Output → Task 11 (pipeline), Task 6 (renderer)
- §5 Audio Engine → Task 4
- §6 Hook Generation → Task 3 (hook_library), Task 5 (generator)
- §7 Renderer → Task 6
- §8 Distribution → Task 8
- §9 Learning Loop → Task 10
- §10 Schedule → Task 12 (rjm.py)
- §11 Transitional Bootstrap → Task 7
- §12 Not in scope → verified no tasks cover out-of-scope items
- §13 Success criteria → Task 13 (integration test)
- §14 Risk register → Tasks 8 (circuit breaker), 11 (validation)

**2. Placeholder scan:** No TBD, TODO, or "implement later" found.

**3. Type consistency:**
- `ClipFormat` enum used consistently in hook_library, generator, pipeline
- `UnifiedWeights` used in types, learning_loop, pipeline
- `TrackInfo` used in audio_engine, pipeline
- `HookTemplate` used in hook_library, generator
- `TransitionalHook` type defined but actual bank uses plain dicts (consistent with index.json format)
- All function signatures match between definition and call sites
