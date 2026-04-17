# Pipeline Fixes: BPM, Story Variant, Visual Context

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three bugs in the daily content pipeline: wrong BPM from librosa, redundant story-variant re-render, and hook/caption text generated without knowing the visual.

**Architecture:** All three fixes live in `content_engine/` only. Fix 1 is a data change (hardcode BPM dict). Fix 2 is a deletion (remove story re-render, alias story_path = path). Fix 3 is a reorder + signature extension (pick visual before generating hook, add `visual_context` param to generator functions).

**Tech Stack:** Python 3.13, PyAV 17, Pillow, pytest, Claude Haiku CLI for hook generation.

---

## Files Changed

| File | Change |
|------|--------|
| `content_engine/audio_engine.py` | Add `TRACK_BPMS` dict; use it in `_load_seed_tracks()` instead of always `bpm=0` |
| `content_engine/pipeline.py` | Reorder loop: pick visual first → generate hook → render; alias `story_path = output_path` |
| `content_engine/generator.py` | Add `visual_context: dict \| None` param to `generate_hooks_for_format`, `_fill_template_with_claude`, `generate_caption` |
| `content_engine/tests/test_audio_engine.py` | Add `test_seed_track_bpms_hardcoded` |
| `content_engine/tests/test_generator.py` | Add `test_generate_hooks_with_visual_context`, `test_generate_caption_with_visual_context` |

No new files. No changes outside `content_engine/`.

---

## Task 1 — Hardcode BPM values in audio_engine.py

**Files:**
- Modify: `content_engine/audio_engine.py:56-70`
- Test: `content_engine/tests/test_audio_engine.py`

### Step 1 — Write the failing test

```python
# Add to content_engine/tests/test_audio_engine.py

def test_seed_track_bpms_hardcoded():
    """Seed tracks must have non-zero BPM without needing audio files."""
    pool = TrackPool()
    by_title = {t.title: t for t in pool.tracks}
    assert by_title["halleluyah"].bpm == 140, f"Expected 140, got {by_title['halleluyah'].bpm}"
    assert by_title["jericho"].bpm == 140, f"Expected 140, got {by_title['jericho'].bpm}"
    assert by_title["fire in our hands"].bpm == 130, f"Expected 130, got {by_title['fire in our hands'].bpm}"
    # All seed tracks must have non-zero BPM
    for t in pool.tracks:
        assert t.bpm > 0, f"Track '{t.title}' still has bpm=0"
```

### Step 2 — Run to verify it fails

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 -m pytest content_engine/tests/test_audio_engine.py::test_seed_track_bpms_hardcoded -v
```
Expected: **FAIL** — `AssertionError: Track 'halleluyah' still has bpm=0`

### Step 3 — Add TRACK_BPMS dict and wire it in _load_seed_tracks

In `content_engine/audio_engine.py`, after the `SEED_TRACKS` line (~line 38), add:

```python
# Artist-verified BPMs — never rely on librosa for these (librosa doubles psytrance
# BPMs: 70 BPM half-time detection → reports 140; 92.5 → reports 185).
TRACK_BPMS: dict[str, int] = {
    "halleluyah":       140,
    "renamed":          128,
    "jericho":          140,
    "fire in our hands": 130,
    "living water":     124,
    "he is the light":  128,
    "exodus":           138,
    "abba":             132,
}
```

Then in `_load_seed_tracks()`, change `bpm=0` to `bpm=TRACK_BPMS.get(title, 0)`:

```python
    def _load_seed_tracks(self):
        for title in SEED_TRACKS:
            path = self._find_track_file(title)
            self.tracks.append(TrackInfo(
                title=title,
                file_path=str(path) if path else "",
                bpm=TRACK_BPMS.get(title, 0),   # ← was hardcoded 0
                energy=0.7,
                danceability=0.7,
                valence=0.5,
                scripture_anchor=SCRIPTURE_ANCHORS.get(title, ""),
                spotify_id="",
                spotify_popularity=50,
                pool_weight=1.0,
                entered_pool=date.today().isoformat(),
            ))
```

### Step 4 — Run test to verify it passes

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 -m pytest content_engine/tests/test_audio_engine.py -v
```
Expected: **all PASS** including `test_seed_track_bpms_hardcoded`

### Step 5 — Commit

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add content_engine/audio_engine.py content_engine/tests/test_audio_engine.py
git commit -m "fix(audio): hardcode artist-verified BPMs — librosa doubles psytrance tempos"
```

---

## Task 2 — Eliminate redundant story-variant re-render

**Files:**
- Modify: `content_engine/pipeline.py:374-504`

The story variant only adds "Listen on Spotify: {track_title}" text burned in the lower 12% of the same clip. The distributor already falls back to `clip["path"]` when `story_path` is absent. Skipping the re-render halves output files and removes a 20-30s render per clip.

### Step 1 — Write the failing test

Add to `content_engine/tests/test_audio_engine.py` (or create `content_engine/tests/test_pipeline.py`):

```python
# content_engine/tests/test_pipeline.py
import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


def test_story_path_equals_main_path():
    """After build_daily_clips, story_path must equal path (no separate re-render)."""
    from content_engine.pipeline import build_daily_clips, DailyPipelineConfig
    from content_engine.types import ClipFormat, TrendBrief, UnifiedWeights, TrackInfo
    from datetime import date

    config = DailyPipelineConfig(
        formats=[ClipFormat.TRANSITIONAL],
        durations={ClipFormat.TRANSITIONAL: 22},
    )
    brief = TrendBrief(summary="test", dominant_emotion="test", contrarian_gap="test",
                       oversaturated="", hook_pattern_of_day="")
    weights = UnifiedWeights(hook_weights={}, platform_weights={}, format_weights={})
    track = TrackInfo(
        title="jericho", file_path="", bpm=140, energy=0.7, danceability=0.7,
        valence=0.5, scripture_anchor="Joshua 6", spotify_id="", spotify_popularity=50,
        pool_weight=1.0, entered_pool=date.today().isoformat(),
    )

    rendered_path = "/tmp/fake_render.mp4"

    with patch("content_engine.pipeline.render_transitional") as mock_render, \
         patch("content_engine.pipeline.render_story_variant") as mock_story, \
         patch("content_engine.pipeline.TransitionalManager") as mock_tm, \
         patch("content_engine.pipeline.generate_hooks_for_format") as mock_hook, \
         patch("content_engine.pipeline.generate_caption") as mock_caption, \
         patch("content_engine.renderer.validate_output") as mock_validate, \
         patch("os.path.exists", return_value=True):

        mock_tm.return_value.pick.return_value = {"file": "test/123.mp4", "category": "satisfying"}
        mock_tm.return_value.full_path.return_value = Path("/fake/bait.mp4")
        mock_hook.return_value = {"hook": "test hook", "template_id": "t1", "mechanism": "save", "sub_mode": "COST", "exploration": False}
        mock_caption.return_value = "test caption"
        # simulate renderer creating the output file
        Path(rendered_path).touch()

        clips = build_daily_clips(config, brief, weights, track, [30.0], "/tmp")

    # render_story_variant must NOT have been called
    mock_story.assert_not_called()

    if clips:
        assert clips[0]["story_path"] == clips[0]["path"]
```

### Step 2 — Run to verify it fails

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 -m pytest content_engine/tests/test_pipeline.py::test_story_path_equals_main_path -v
```
Expected: **FAIL** — `AssertionError: mock_story was called` (or similar)

### Step 3 — Remove render_story_variant from pipeline loop

In `content_engine/pipeline.py`:

**a) Remove `render_story_variant` from the import at ~line 374:**
```python
    from content_engine.renderer import (
        render_transitional, render_emotional, render_performance,
        # render_story_variant removed — story platforms now use the main clip
    )
```

**b) Remove the `story_path` local variable and the `render_story_variant` call at ~line 444-500:**

Replace this block:
```python
        output_path = str(Path(output_dir) / f"{fmt.value}_{track.title.lower().replace(' ', '_')}.mp4")
        story_path = str(Path(output_dir) / f"{fmt.value}_{track.title.lower().replace(' ', '_')}_story.mp4")
```
With:
```python
        output_path = str(Path(output_dir) / f"{fmt.value}_{track.title.lower().replace(' ', '_')}.mp4")
```

**c) Replace the `render_story_variant` call and the clip_meta story_path assignment:**

Remove this line (~line 500):
```python
            # Render Story variant
            render_story_variant(output_path, track.title, clip_meta["spotify_url"], story_path)
```

Change this (~line 502-503):
```python
            clip_meta["path"] = output_path
            clip_meta["story_path"] = story_path
```
To:
```python
            clip_meta["path"] = output_path
            clip_meta["story_path"] = output_path   # stories use the same clip
```

### Step 4 — Run test to verify it passes

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 -m pytest content_engine/tests/test_pipeline.py -v
```
Expected: **PASS**

### Step 5 — Verify dry run produces 2 files not 4

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 rjm.py content viral --dry-run 2>&1 | tail -10
ls content/output/latest/
```
Expected: 2 files (`transitional_*.mp4`, `performance_*.mp4`) — no `*_story.mp4` files.

### Step 6 — Commit

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add content_engine/pipeline.py content_engine/tests/test_pipeline.py
git commit -m "fix(pipeline): skip story re-render — story platforms reuse main clip"
```

---

## Task 3 — Thread visual context into hook and caption generation

**Files:**
- Modify: `content_engine/generator.py:218-266` (generate_hooks_for_format)
- Modify: `content_engine/generator.py:269-319` (_fill_template_with_claude)
- Modify: `content_engine/generator.py:384-500` (generate_caption)
- Modify: `content_engine/pipeline.py:409-510` (build_daily_clips loop)
- Test: `content_engine/tests/test_generator.py`

The goal: the Claude prompt receives a human-readable description of what's on screen so the hook doesn't reference the wrong BPM or invent irrelevant imagery.

### Step 1 — Write the failing tests

Add to `content_engine/tests/test_generator.py`:

```python
def test_generate_hooks_accepts_visual_context():
    """visual_context kwarg must be accepted without error."""
    result = generate_hooks_for_format(
        fmt=ClipFormat.TRANSITIONAL,
        track_title="Jericho",
        track_facts={"bpm": 140, "scripture_anchor": "Joshua 6"},
        visual_context={"category": "nature", "file": "nature/855278.mp4"},
    )
    assert "hook" in result
    assert len(result["hook"]) > 0


def test_generate_caption_accepts_visual_context():
    """visual_context kwarg must be accepted without error."""
    caption = generate_caption(
        track_title="Jericho",
        hook_text="The walls fall when you stop holding them up.",
        platform="instagram",
        track_facts={"bpm": 140},
        visual_context={"category": "satisfying", "file": "satisfying/855416.mp4"},
    )
    assert isinstance(caption, str)
    assert len(caption) > 0
```

### Step 2 — Run to verify they fail

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 -m pytest content_engine/tests/test_generator.py::test_generate_hooks_accepts_visual_context content_engine/tests/test_generator.py::test_generate_caption_accepts_visual_context -v
```
Expected: **FAIL** — `TypeError: unexpected keyword argument 'visual_context'`

### Step 3 — Add visual_context param to generator functions

**a) `generate_hooks_for_format` — add param and thread to `_fill_template_with_claude`:**

```python
def generate_hooks_for_format(
    fmt: ClipFormat,
    track_title: str,
    track_facts: dict,
    weights: dict | None = None,
    exclude_ids: set | None = None,
    visual_context: dict | None = None,       # ← new
) -> dict:
    templates = pick_templates_for_format(fmt, weights, exclude_ids)
    template = templates[0]
    angle = FORMAT_TO_ANGLE.get(fmt, "emotional")
    sub_mode = pick_sub_mode(angle)
    if template.slots:
        filled = _fill_template_with_claude(
            template, track_title, track_facts, sub_mode,
            visual_context=visual_context,     # ← thread through
        )
    else:
        filled = template.template
    if filled:
        validated = gate_or_reject(filled)
        if validated:
            log_template_use(template.id, fmt.value if hasattr(fmt, "value") else str(fmt))
            return {
                "hook": validated,
                "template_id": template.id,
                "mechanism": template.mechanism,
                "sub_mode": sub_mode,
                "exploration": False,
            }
    logger.info(f"Using example_fill for {template.id}")
    log_template_use(template.id, fmt.value if hasattr(fmt, "value") else str(fmt))
    return {
        "hook": template.example_fill,
        "template_id": template.id,
        "mechanism": template.mechanism,
        "sub_mode": sub_mode,
        "exploration": False,
    }
```

**b) `_fill_template_with_claude` — add param and inject into prompt:**

```python
def _fill_template_with_claude(
    template: HookTemplate,
    track_title: str,
    track_facts: dict,
    sub_mode: str,
    visual_context: dict | None = None,      # ← new
) -> Optional[str]:
    slots_desc = "\n".join(f"  {k}: {v}" for k, v in template.slots.items())
    register_guidance = SUB_MODE_REGISTER.get(sub_mode, "Concrete, specific, felt-sense language.")

    # Build a human-readable visual description for the prompt
    if visual_context:
        cat = visual_context.get("category", "")
        _VISUAL_DESCRIPTIONS = {
            "satisfying": "slow satisfying process footage (sand art, precision machines, fluid motion)",
            "nature":     "outdoor nature footage (forest, ocean, sky, mountains)",
            "urban":      "city / street scene footage",
            "performance":"live DJ or crowd performance footage",
            "emotional":  "moody atmospheric b-roll (candlelight, long shadows, slow motion)",
            "abstract":   "abstract visual / light patterns",
        }
        visual_desc = _VISUAL_DESCRIPTIONS.get(cat, cat) if cat else "unspecified b-roll"
        visual_block = f"\nVisuals on screen: {visual_desc}\nThe hook must be coherent with — or work as contrast to — this visual.\n"
    else:
        visual_block = ""

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
{visual_block}
Register: {sub_mode} — {register_guidance}
{_format_trend_context(_load_trend_brief())}
Rules:
- Return ONLY the filled hook text, nothing else
- Keep under 280 characters
- Be concrete and specific (no generic adjectives — never "amazing", "beautiful", "incredible")
- Must pass the Visualization Test: the reader must SEE the words
- Must pass the Uniqueness Rule: no competing DJ could sign their name to it
- The hook must work as a text overlay on a short video
"""
    system = "You are a hook copywriter for a Dutch DJ/producer. Fill template slots with specific, concrete language. No generic adjectives. Under 280 chars. Return ONLY the filled text."
    response = _call_claude(prompt, system, timeout=90)
    if response:
        cleaned = response.strip().strip('"').strip("'").strip("`")
        cleaned = re.sub(r'^#+\s*', '', cleaned)
        cleaned = re.sub(r'\*+', '', cleaned)
        if len(cleaned) <= 280 and len(cleaned) > 5:
            return cleaned
    return None
```

**c) `generate_caption` — add param and inject into prompt:**

Change signature:
```python
def generate_caption(
    track_title: str,
    hook_text: str,
    platform: str,
    track_facts: dict | None = None,
    visual_context: dict | None = None,       # ← new
) -> str:
```

Add a visual line to the prompt body. After the `Hook shown on-screen:` line, insert:
```python
    if visual_context:
        cat = visual_context.get("category", "")
        _VISUAL_DESCRIPTIONS = {
            "satisfying": "slow satisfying process footage",
            "nature":     "outdoor nature footage",
            "urban":      "city / street scene footage",
            "performance": "live DJ or crowd performance footage",
            "emotional":  "moody atmospheric b-roll",
            "abstract":   "abstract light patterns",
        }
        visual_line = f"Visuals in the clip: {_VISUAL_DESCRIPTIONS.get(cat, cat) or 'b-roll'}\n"
    else:
        visual_line = ""
```

In the prompt f-string, after the `Hook shown on-screen:` line add:
```
{visual_line}
```

### Step 4 — Thread visual_context through pipeline.py build_daily_clips

In `content_engine/pipeline.py`, reorder the per-clip loop so visual context is determined **before** hook generation. Replace the existing loop body (lines ~409-510) with:

```python
    for clip_idx, fmt in enumerate(config.formats):
        duration = config.durations[fmt]
        audio_start = peak_sections[clip_idx] if clip_idx < len(peak_sections) else 30.0

        # ── Step 1: Determine visual context before generating hook text ──────
        visual_context: dict = {}
        bait = None
        bait_path = None

        if fmt == ClipFormat.TRANSITIONAL:
            tm = TransitionalManager()
            bait = tm.pick()
            if bait:
                bait_path = str(tm.full_path(bait["file"]))
                tm.mark_used(bait["file"])
                visual_context = {
                    "category": bait["category"],
                    "file": bait["file"],
                }

        elif fmt == ClipFormat.PERFORMANCE:
            visual_context = {
                "category": "performance",
                "description": "live DJ or crowd performance footage",
            }
        elif fmt == ClipFormat.EMOTIONAL:
            visual_context = {
                "category": "emotional",
                "description": "moody atmospheric b-roll",
            }

        # ── Step 2: Generate hook with visual context ─────────────────────────
        hook_data = generate_hooks_for_format(
            fmt, track.title, track_facts, weights.hook_weights, used_ids,
            visual_context=visual_context,
        )
        used_ids.add(hook_data["template_id"])

        # ── Step 3: Generate captions with visual context ─────────────────────
        caption_platforms = [
            "instagram", "youtube", "tiktok", "facebook",
            "instagram_story", "facebook_story",
        ]
        caption_by_platform = {
            p: generate_caption(
                track.title, hook_data["hook"], p, track_facts,
                visual_context=visual_context,
            )
            for p in caption_platforms
        }
        caption = caption_by_platform.get("instagram", "")

        n_segments = {"transitional": 3, "emotional": 2, "performance": 5}.get(fmt.value, 3)
        available = [v for v in all_videos if v not in used_segments]
        if len(available) < n_segments:
            available = list(all_videos)
        segments = random.sample(available, min(n_segments, len(available)))
        used_segments.update(segments)

        output_path = str(Path(output_dir) / f"{fmt.value}_{track.title.lower().replace(' ', '_')}.mp4")

        clip_meta = {
            "clip_index": clip_idx,
            "format_type": fmt.value,
            "hook_mechanism": hook_data["mechanism"],
            "hook_template_id": hook_data["template_id"],
            "hook_sub_mode": hook_data["sub_mode"],
            "hook_text": hook_data["hook"],
            "caption": caption,
            "caption_by_platform": caption_by_platform,
            "track_title": track.title,
            "clip_length": duration,
            "visual_type": "b_roll",
            "transitional_category": visual_context.get("category", ""),
            "transitional_file": visual_context.get("file", ""),
            "spotify_url": "https://open.spotify.com/artist/2Seaafm5k1hAuCkpdq7yds",
        }

        try:
            if fmt == ClipFormat.TRANSITIONAL:
                if bait_path:
                    render_transitional(
                        bait_clip=bait_path,
                        content_segments=segments,
                        audio_path=track.file_path,
                        audio_start=audio_start,
                        hook_text=hook_data["hook"],
                        track_label=f"{track.title} — Robert-Jan Mastenbroek",
                        platform="youtube",
                        output_path=output_path,
                        target_duration=duration,
                    )
                else:
                    logger.warning("[pipeline] No transitional bait available, falling back to emotional")
                    render_emotional(
                        segments, track.file_path, audio_start,
                        hook_data["hook"], "youtube", output_path, duration,
                    )
            elif fmt == ClipFormat.EMOTIONAL:
                render_emotional(
                    segments, track.file_path, audio_start,
                    hook_data["hook"], "youtube", output_path, duration,
                )
            elif fmt == ClipFormat.PERFORMANCE:
                render_performance(
                    segments, track.file_path, audio_start,
                    hook_data["hook"], "youtube", output_path, duration,
                )

            clip_meta["path"] = output_path
            clip_meta["story_path"] = output_path   # stories reuse the main clip
            clips.append(clip_meta)

        except Exception as e:
            logger.error(f"[pipeline] Failed to render {fmt.value}: {e}")
            continue
```

### Step 5 — Run tests

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 -m pytest content_engine/tests/test_generator.py -v
```
Expected: **all PASS** including the two new visual_context tests.

```bash
python3.13 -m pytest content_engine/tests/ -v --tb=short 2>&1 | tail -20
```
Expected: all tests green (or only pre-existing failures unrelated to this change).

### Step 6 — Commit

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add content_engine/generator.py content_engine/pipeline.py content_engine/tests/test_generator.py
git commit -m "feat(generator): thread visual context into hook and caption prompts"
```

---

## Task 4 — Integration dry run + push

### Step 1 — Full dry run

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 rjm.py content viral --dry-run 2>&1 | tail -20
```

Expected output contains:
- `[pipeline] Track selected: <track> (140 BPM)` — not 185
- `[pipeline] Rendered 3 clips`
- No `Caption burn error` lines
- No story-related lines

Expected files in `content/output/latest/`:
- `transitional_<track>.mp4`
- `performance_<track>.mp4`
- (no `*_story.mp4` files)

### Step 2 — Quick sanity check on output

```bash
python3.13 -c "
import av, glob, os
for f in sorted(glob.glob('content/output/latest/*.mp4')):
    c = av.open(f)
    v, a = c.streams.video[0], c.streams.audio[0]
    print(f'{os.path.basename(f)}: {float(v.duration*v.time_base):.1f}s audio={a.codec.name}')
    c.close()
"
```
Expected: 2 files, each with audio.

### Step 3 — Push to remote

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git push
```

---

## Self-Review Checklist

- [x] **BPM spec covered**: TRACK_BPMS dict with all seed tracks; `_load_seed_tracks` uses it; test asserts exact values
- [x] **Story variant spec covered**: `render_story_variant` call removed; `story_path = output_path`; test asserts `mock_story.assert_not_called()`
- [x] **Visual context spec covered**: new param on both `generate_hooks_for_format` and `generate_caption`; pipeline picks visual BEFORE calling generator; prompt block injected
- [x] **No placeholders**: all code blocks are complete and runnable
- [x] **Type consistency**: `visual_context: dict | None = None` used consistently in all three function signatures; `_VISUAL_DESCRIPTIONS` dict defined locally in each function (DRY tradeoff: keeping it local avoids a shared constant that could get out of sync)
- [x] **Story path in distributor**: distributor already does `clip.get("story_path", clip.get("path", ""))` — no distributor changes needed
- [x] **Import cleanup**: `render_story_variant` removed from pipeline import to avoid unused import lint warning
