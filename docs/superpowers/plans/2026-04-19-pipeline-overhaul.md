# Pipeline Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate duplicate TikTok posts, lock 2-of-3 daily slots to the proven "sacred arc" format (transitional hook → performance footage), and ensure the analytics learning loop is operational.

**Architecture:** Three surgical fixes to `content_engine/pipeline.py` and `content_engine/types.py`, plus a new `ClipFormat.SACRED_ARC` enum value wired through the generator. No new files created. The learning loop plist already exists — just needs a load verification step.

**Tech Stack:** Python 3.13, FFmpeg (via renderer), launchd (macOS), existing test suite at `content_engine/tests/`

---

## Root Cause: Why TikTok double-posts

`pipeline.py:526` builds the output filename as:
```python
f"{fmt.value}_{track.title.lower().replace(' ', '_')}.mp4"
```
When formats = [TRANSITIONAL, TRANSITIONAL, PERFORMANCE], clips 0 and 1 both write to `transitional_halleluyah.mp4`. The second render **overwrites** the first. The distributor then queues that same file to TikTok at 09:30 (clip_index=0) and 14:00 (clip_index=1) — identical video, two time slots. Fix: include `clip_idx` in the filename.

## Secondary bug: Performance footage not found

`pipeline.py:448` uses `vd_path.iterdir()` which is one level deep. All performance `.mp4` files live inside a subdirectory (`performances/wetransfer_luc9221-mp4.../`). `iterdir()` finds the subdirectory but not the files inside it. Fix: use `rglob("*")`.

---

## Task 1: Fix output path collision (double-post root cause)

**Files:**
- Modify: `content_engine/pipeline.py:526`
- Test: `content_engine/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add to `content_engine/tests/test_pipeline.py`:

```python
def test_two_transitional_clips_get_distinct_output_paths(tmp_path):
    """Two TRANSITIONAL clips must write to different files — same path = same video = double-post."""
    config = DailyPipelineConfig(
        formats=[ClipFormat.TRANSITIONAL, ClipFormat.TRANSITIONAL],
        durations={ClipFormat.TRANSITIONAL: 22},
    )

    with patch("content_engine.renderer.render_transitional") as mock_render, \
         patch("content_engine.transitional_manager.TransitionalManager") as mock_tm, \
         patch("content_engine.generator.generate_hooks_for_format") as mock_hook, \
         patch("content_engine.generator.generate_caption") as mock_caption:

        mock_tm.return_value.pick.return_value = {"file": "satisfying/123.mp4", "category": "satisfying"}
        mock_tm.return_value.full_path.return_value = Path("/fake/bait.mp4")
        mock_hook.return_value = {
            "hook": "test hook", "template_id": "t1",
            "mechanism": "save", "sub_mode": "COST", "exploration": False,
        }
        mock_caption.return_value = "test caption"
        mock_render.side_effect = lambda **kw: Path(kw["output_path"]).touch()

        clips = build_daily_clips(
            config, _make_brief(), _make_weights(), _make_track(),
            peak_sections=[0.0, 0.0], output_dir=str(tmp_path),
        )

    assert len(clips) == 2
    paths = [c["path"] for c in clips]
    assert paths[0] != paths[1], (
        f"Both TRANSITIONAL clips wrote to the same path: {paths[0]!r}. "
        "This is the double-post bug — fix by including clip_idx in the filename."
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 -m pytest content_engine/tests/test_pipeline.py::test_two_transitional_clips_get_distinct_output_paths -v
```

Expected: FAIL — `AssertionError: Both TRANSITIONAL clips wrote to the same path`

- [ ] **Step 3: Fix pipeline.py — include clip_idx in output filename**

In `content_engine/pipeline.py`, find line ~526:
```python
        output_path = str(Path(output_dir) / f"{fmt.value}_{track.title.lower().replace(' ', '_')}.mp4")
```

Replace with:
```python
        output_path = str(Path(output_dir) / f"{fmt.value}_{clip_idx}_{track.title.lower().replace(' ', '_')}.mp4")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3.13 -m pytest content_engine/tests/test_pipeline.py::test_two_transitional_clips_get_distinct_output_paths -v
```

Expected: PASS

- [ ] **Step 5: Run full pipeline test suite — no regressions**

```bash
python3.13 -m pytest content_engine/tests/test_pipeline.py -v
```

Expected: all tests PASS (update `test_config_defaults` if it asserts specific filenames)

- [ ] **Step 6: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add content_engine/pipeline.py content_engine/tests/test_pipeline.py
git commit -m "fix(pipeline): include clip_idx in output filename to prevent double-post

Two TRANSITIONAL clips in the same day run were writing to identical paths
(e.g. transitional_halleluyah.mp4). The second render silently overwrote the
first, so TikTok received the same video at 09:30 and 14:00.

Root fix: add clip_idx to the filename so each slot produces a unique file."
```

---

## Task 2: Fix performance footage discovery (recursive scan)

**Files:**
- Modify: `content_engine/pipeline.py:448` (the `for f in vd_path.iterdir()` loop)
- Test: `content_engine/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add to `content_engine/tests/test_pipeline.py`:

```python
def test_performance_footage_found_in_subdirectory(tmp_path):
    """Videos nested inside a subdirectory of content/videos/performances/ must be found."""
    import os
    from unittest.mock import patch as _patch

    # Build a fake video tree with a nested performance clip
    perf_dir = tmp_path / "performances" / "wetransfer_abc123"
    perf_dir.mkdir(parents=True)
    nested_mp4 = perf_dir / "LUC9222.MP4"
    nested_mp4.touch()

    broll_dir = tmp_path / "b-roll"
    broll_dir.mkdir()

    phone_dir = tmp_path / "phone-footage"
    phone_dir.mkdir()

    from content_engine import pipeline as _pipeline
    orig_dir = _pipeline.PROJECT_DIR

    # Patch PROJECT_DIR so video_dirs resolve to our tmp layout
    with _patch.object(_pipeline, "PROJECT_DIR", tmp_path):
        config = DailyPipelineConfig(
            formats=[ClipFormat.PERFORMANCE],
            durations={ClipFormat.PERFORMANCE: 28},
        )
        with patch("content_engine.renderer.render_performance") as mock_render, \
             patch("content_engine.generator.generate_hooks_for_format") as mock_hook, \
             patch("content_engine.generator.generate_caption") as mock_caption:

            mock_hook.return_value = {
                "hook": "test hook", "template_id": "t1",
                "mechanism": "save", "sub_mode": "BODY", "exploration": False,
            }
            mock_caption.return_value = "test caption"

            # Capture the segments passed to the renderer
            captured = {}
            def fake_render(**kw):
                captured["segments"] = kw.get("content_segments", [])
                Path(kw["output_path"]).touch()
            mock_render.side_effect = fake_render

            build_daily_clips(
                config, _make_brief(), _make_weights(), _make_track(),
                peak_sections=[0.0], output_dir=str(tmp_path / "output"),
            )

    assert any("LUC9222.MP4" in s for s in captured.get("segments", [])), (
        "Performance footage nested in subdirectory was not found. "
        "iterdir() is one level deep — fix by using rglob()."
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3.13 -m pytest content_engine/tests/test_pipeline.py::test_performance_footage_found_in_subdirectory -v
```

Expected: FAIL

- [ ] **Step 3: Fix pipeline.py — use rglob instead of iterdir**

In `content_engine/pipeline.py`, find the video collection block (~lines 448-454):

```python
    all_videos = []
    for vd in video_dirs:
        vd_path = Path(vd)
        if vd_path.exists():
            for f in vd_path.iterdir():
                if f.suffix.lower() in (".mp4", ".mov"):
                    all_videos.append(str(f))
```

Replace with:

```python
    all_videos = []
    for vd in video_dirs:
        vd_path = Path(vd)
        if vd_path.exists():
            for f in vd_path.rglob("*"):
                if f.is_file() and f.suffix.lower() in (".mp4", ".mov"):
                    all_videos.append(str(f))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3.13 -m pytest content_engine/tests/test_pipeline.py::test_performance_footage_found_in_subdirectory -v
```

Expected: PASS

- [ ] **Step 5: Run full pipeline test suite**

```bash
python3.13 -m pytest content_engine/tests/test_pipeline.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add content_engine/pipeline.py content_engine/tests/test_pipeline.py
git commit -m "fix(pipeline): use rglob to find footage nested in subdirectories

iterdir() is one level deep — performance clips stored inside
performances/wetransfer_abc123/ were never found, so the
PERFORMANCE format was silently falling through to b-roll footage."
```

---

## Task 3: Add SACRED_ARC format

The Sacred Arc is a transitional-hook clip where the content segments are **restricted to performance footage** (people dancing, high-energy crowd/stage visuals). Same renderer as TRANSITIONAL, different footage pool.

**Files:**
- Modify: `content_engine/types.py` (ClipFormat enum + UnifiedWeights defaults)
- Modify: `content_engine/generator.py` (FORMAT_TO_ANGLE + n_segments)
- Modify: `content_engine/pipeline.py` (bait pick logic + segment filter + render dispatch + DailyPipelineConfig + run_full_day)
- Test: `content_engine/tests/test_pipeline.py`, `content_engine/tests/test_types.py`

### 3a — Add ClipFormat.SACRED_ARC + update weights defaults

- [ ] **Step 1: Write the failing test**

Add to `content_engine/tests/test_types.py` (create it if it doesn't exist):

```python
from content_engine.types import ClipFormat, UnifiedWeights

def test_sacred_arc_in_clip_format():
    assert ClipFormat.SACRED_ARC.value == "sacred_arc"

def test_sacred_arc_in_unified_weights_defaults():
    w = UnifiedWeights.defaults()
    assert "sacred_arc" in w.format_weights
    assert w.format_weights["sacred_arc"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3.13 -m pytest content_engine/tests/test_types.py -v
```

Expected: FAIL — `AttributeError: SACRED_ARC`

- [ ] **Step 3: Add SACRED_ARC to ClipFormat enum**

In `content_engine/types.py`, find the ClipFormat class (line ~101):

```python
class ClipFormat(Enum):
    TRANSITIONAL = "transitional"
    EMOTIONAL = "emotional"
    PERFORMANCE = "performance"
```

Replace with:

```python
class ClipFormat(Enum):
    TRANSITIONAL = "transitional"
    EMOTIONAL = "emotional"
    PERFORMANCE = "performance"
    SACRED_ARC = "sacred_arc"
```

- [ ] **Step 4: Add "sacred_arc" to UnifiedWeights.defaults() format_weights**

In `content_engine/types.py`, find UnifiedWeights.defaults() format_weights (~line 168):

```python
            format_weights={
                "transitional": 1.0, "emotional": 1.0, "performance": 1.0,
            },
```

Replace with:

```python
            format_weights={
                "transitional": 1.0, "emotional": 1.0, "performance": 1.0,
                "sacred_arc": 1.0,
            },
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python3.13 -m pytest content_engine/tests/test_types.py -v
```

Expected: PASS

### 3b — Wire SACRED_ARC into the generator

- [ ] **Step 6: Write the failing test**

Add to `content_engine/tests/test_generator.py`:

```python
from content_engine.types import ClipFormat
from content_engine.generator import FORMAT_TO_ANGLE, pick_sub_mode

def test_sacred_arc_has_angle():
    assert ClipFormat.SACRED_ARC in FORMAT_TO_ANGLE

def test_sacred_arc_angle_is_emotional():
    assert FORMAT_TO_ANGLE[ClipFormat.SACRED_ARC] == "emotional"
```

- [ ] **Step 7: Run test to verify it fails**

```bash
python3.13 -m pytest content_engine/tests/test_generator.py::test_sacred_arc_has_angle content_engine/tests/test_generator.py::test_sacred_arc_angle_is_emotional -v
```

Expected: FAIL

- [ ] **Step 8: Add SACRED_ARC to FORMAT_TO_ANGLE**

In `content_engine/generator.py`, find FORMAT_TO_ANGLE (~line 43):

```python
FORMAT_TO_ANGLE = {
    ClipFormat.TRANSITIONAL: "emotional",
    ClipFormat.EMOTIONAL: "emotional",
    ClipFormat.PERFORMANCE: "energy",
}
```

Replace with:

```python
FORMAT_TO_ANGLE = {
    ClipFormat.TRANSITIONAL: "emotional",
    ClipFormat.EMOTIONAL: "emotional",
    ClipFormat.PERFORMANCE: "energy",
    ClipFormat.SACRED_ARC: "emotional",
}
```

- [ ] **Step 9: Run tests to verify they pass**

```bash
python3.13 -m pytest content_engine/tests/test_generator.py::test_sacred_arc_has_angle content_engine/tests/test_generator.py::test_sacred_arc_angle_is_emotional -v
```

Expected: PASS

### 3c — Wire SACRED_ARC into build_daily_clips

- [ ] **Step 10: Write the failing tests**

Add to `content_engine/tests/test_pipeline.py`:

```python
def test_sacred_arc_uses_performance_footage_only(tmp_path):
    """SACRED_ARC segments must come exclusively from the performances/ directory."""
    from unittest.mock import patch as _patch
    from content_engine import pipeline as _pipeline

    # Build a fake video tree
    perf_dir = tmp_path / "performances" / "wetransfer_abc"
    perf_dir.mkdir(parents=True)
    (perf_dir / "DANCE1.MP4").touch()
    (perf_dir / "DANCE2.MP4").touch()

    broll_dir = tmp_path / "b-roll"
    broll_dir.mkdir()
    (broll_dir / "NATURE1.mp4").touch()

    phone_dir = tmp_path / "phone-footage"
    phone_dir.mkdir()

    with _patch.object(_pipeline, "PROJECT_DIR", tmp_path):
        config = DailyPipelineConfig(
            formats=[ClipFormat.SACRED_ARC],
            durations={ClipFormat.SACRED_ARC: 22},
        )
        with patch("content_engine.renderer.render_transitional") as mock_render, \
             patch("content_engine.transitional_manager.TransitionalManager") as mock_tm, \
             patch("content_engine.generator.generate_hooks_for_format") as mock_hook, \
             patch("content_engine.generator.generate_caption") as mock_caption:

            mock_tm.return_value.pick.return_value = {"file": "satisfying/123.mp4", "category": "satisfying"}
            mock_tm.return_value.full_path.return_value = Path("/fake/bait.mp4")
            mock_hook.return_value = {
                "hook": "test hook", "template_id": "t1",
                "mechanism": "save", "sub_mode": "COST", "exploration": False,
            }
            mock_caption.return_value = "test caption"

            captured = {}
            def fake_render(**kw):
                captured["segments"] = kw.get("content_segments", [])
                Path(kw["output_path"]).touch()
            mock_render.side_effect = fake_render

            build_daily_clips(
                config, _make_brief(), _make_weights(), _make_track(),
                peak_sections=[0.0], output_dir=str(tmp_path / "output"),
            )

    segments = captured.get("segments", [])
    assert segments, "No segments were passed to the renderer"
    for s in segments:
        assert "performances" in s, (
            f"SACRED_ARC used non-performance footage: {s!r}. "
            "Only content/videos/performances/ is allowed."
        )
    assert not any("b-roll" in s for s in segments), "b-roll leaked into SACRED_ARC segments"


def test_daily_config_default_uses_sacred_arc():
    """Default daily mix must be [SACRED_ARC, SACRED_ARC, TRANSITIONAL]."""
    config = DailyPipelineConfig()
    assert config.formats[0] == ClipFormat.SACRED_ARC
    assert config.formats[1] == ClipFormat.SACRED_ARC
    assert config.formats[2] == ClipFormat.TRANSITIONAL
```

- [ ] **Step 11: Run tests to verify they fail**

```bash
python3.13 -m pytest content_engine/tests/test_pipeline.py::test_sacred_arc_uses_performance_footage_only content_engine/tests/test_pipeline.py::test_daily_config_default_uses_sacred_arc -v
```

Expected: both FAIL

- [ ] **Step 12: Update DailyPipelineConfig default format mix**

In `content_engine/pipeline.py`, find DailyPipelineConfig.formats default (~line 127):

```python
    formats: list = field(default_factory=lambda: [
        ClipFormat.TRANSITIONAL,
        ClipFormat.TRANSITIONAL,
        ClipFormat.PERFORMANCE,
    ])
```

Replace with:

```python
    formats: list = field(default_factory=lambda: [
        ClipFormat.SACRED_ARC,
        ClipFormat.SACRED_ARC,
        ClipFormat.TRANSITIONAL,
    ])
```

Also add `ClipFormat.SACRED_ARC: 22` to the durations default (find ~line 132):

```python
    durations: dict = field(default_factory=lambda: {
        ClipFormat.TRANSITIONAL: 22,
        ClipFormat.EMOTIONAL: 7,
        ClipFormat.PERFORMANCE: 28,
    })
```

Replace with:

```python
    durations: dict = field(default_factory=lambda: {
        ClipFormat.SACRED_ARC: 22,
        ClipFormat.TRANSITIONAL: 22,
        ClipFormat.EMOTIONAL: 7,
        ClipFormat.PERFORMANCE: 28,
    })
```

- [ ] **Step 13: Update run_full_day config construction to lock SACRED_ARC slots**

In `content_engine/pipeline.py`, find the `if config is None:` block (~line 240):

```python
    if config is None:
        config = DailyPipelineConfig(
            formats=derive_format_mix(weights.format_weights),
            durations={
                ClipFormat.TRANSITIONAL: 22,
                ClipFormat.EMOTIONAL: emotional_duration_from_weights(weights.best_clip_length),
                ClipFormat.PERFORMANCE: 28,
            },
        )
```

Replace with:

```python
    if config is None:
        config = DailyPipelineConfig(
            formats=[
                ClipFormat.SACRED_ARC,
                ClipFormat.SACRED_ARC,
                ClipFormat.TRANSITIONAL,
            ],
            durations={
                ClipFormat.SACRED_ARC: 22,
                ClipFormat.TRANSITIONAL: 22,
                ClipFormat.EMOTIONAL: emotional_duration_from_weights(weights.best_clip_length),
                ClipFormat.PERFORMANCE: 28,
            },
        )
```

- [ ] **Step 14: Add SACRED_ARC bait pick + performance segment filter to build_daily_clips**

In `content_engine/pipeline.py`, find the visual context / bait pick block (~line 480):

```python
        if fmt == ClipFormat.TRANSITIONAL:
            tm = TransitionalManager()
            bait = tm.pick(category_weights=weights.transitional_category_weights)
            if bait:
                bait_path = str(tm.full_path(bait["file"]))
                tm.mark_used(bait["file"])
                visual_context = {"category": bait["category"], "file": bait["file"]}
        elif fmt == ClipFormat.PERFORMANCE:
            visual_context = {"category": "performance"}
        elif fmt == ClipFormat.EMOTIONAL:
            visual_context = {"category": "emotional"}
```

Replace with:

```python
        if fmt in (ClipFormat.TRANSITIONAL, ClipFormat.SACRED_ARC):
            tm = TransitionalManager()
            bait = tm.pick(category_weights=weights.transitional_category_weights)
            if bait:
                bait_path = str(tm.full_path(bait["file"]))
                tm.mark_used(bait["file"])
                visual_context = {"category": bait["category"], "file": bait["file"]}
        elif fmt == ClipFormat.PERFORMANCE:
            visual_context = {"category": "performance"}
        elif fmt == ClipFormat.EMOTIONAL:
            visual_context = {"category": "emotional"}
```

Then find the segment selection block (~line 519):

```python
        n_segments = {"transitional": 3, "emotional": 2, "performance": 5}.get(fmt.value, 3)
        available = [v for v in all_videos if v not in used_segments]
        if len(available) < n_segments:
            available = list(all_videos)
        segments = random.sample(available, min(n_segments, len(available)))
        used_segments.update(segments)
```

Replace with:

```python
        n_segments = {
            "transitional": 3, "emotional": 2, "performance": 5, "sacred_arc": 3,
        }.get(fmt.value, 3)

        if fmt == ClipFormat.SACRED_ARC:
            perf_dir = str(PROJECT_DIR / "content" / "videos" / "performances")
            perf_pool = [v for v in all_videos if perf_dir in v and v not in used_segments]
            if len(perf_pool) < n_segments:
                perf_pool = [v for v in all_videos if perf_dir in v]
            available = perf_pool if perf_pool else [v for v in all_videos if v not in used_segments]
        else:
            available = [v for v in all_videos if v not in used_segments]
            if len(available) < n_segments:
                available = list(all_videos)

        segments = random.sample(available, min(n_segments, len(available)))
        used_segments.update(segments)
```

- [ ] **Step 15: Add SACRED_ARC to render dispatch**

In `content_engine/pipeline.py`, find the render dispatch block (~line 546):

```python
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
                    render_emotional(segments, track.file_path, audio_start, hook_data["hook"],
                                     "youtube", output_path, duration)
```

Replace with:

```python
            if fmt in (ClipFormat.TRANSITIONAL, ClipFormat.SACRED_ARC):
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
                    logger.warning(f"[pipeline] No transitional bait available for {fmt.value}, falling back to emotional")
                    render_emotional(segments, track.file_path, audio_start, hook_data["hook"],
                                     "youtube", output_path, duration)
```

- [ ] **Step 16: Run the new tests**

```bash
python3.13 -m pytest content_engine/tests/test_pipeline.py::test_sacred_arc_uses_performance_footage_only content_engine/tests/test_pipeline.py::test_daily_config_default_uses_sacred_arc -v
```

Expected: both PASS

- [ ] **Step 17: Run full content_engine test suite**

```bash
python3.13 -m pytest content_engine/tests/ -v
```

Expected: all PASS. Fix any assertion that expected the old [TRANSITIONAL, TRANSITIONAL, PERFORMANCE] default — update it to expect [SACRED_ARC, SACRED_ARC, TRANSITIONAL].

- [ ] **Step 18: Commit**

```bash
git add content_engine/types.py content_engine/generator.py content_engine/pipeline.py \
        content_engine/tests/test_types.py content_engine/tests/test_generator.py content_engine/tests/test_pipeline.py
git commit -m "feat(pipeline): add SACRED_ARC format — transitional hook + performance footage only

Sacred Arc is the proven viral format: bait hook clip → high-energy
performance footage (people dancing). Allocates 2-of-3 daily slots;
slot 3 stays TRANSITIONAL for experimentation.

- ClipFormat.SACRED_ARC added to types.py
- FORMAT_TO_ANGLE wired in generator.py (emotional angle)
- build_daily_clips filters segments to performances/ for SACRED_ARC
- DailyPipelineConfig default updated to [SACRED_ARC, SACRED_ARC, TRANSITIONAL]
- run_full_day locks mix (bypasses derive_format_mix bandit for sacred arc slots)"
```

---

## Task 4: Verify analytics learning loop is active

The `com.rjm.viral-learning.plist` already exists and fires at 18:00 CET. Verify it is properly loaded and will fire tonight.

**Files:**
- Read: `/Users/motomoto/Library/LaunchAgents/com.rjm.viral-learning.plist`
- No code changes required unless the plist is unloaded

- [ ] **Step 1: Check launchd load status**

```bash
launchctl list | grep "rjm.viral-learning"
```

Expected output (loaded and waiting):
```
-	0	com.rjm.viral-learning
```

The `-` for PID is correct (not currently running). The `0` exit code means last run succeeded.

If the job is NOT listed, load it:
```bash
launchctl bootstrap gui/$(id -u) /Users/motomoto/Library/LaunchAgents/com.rjm.viral-learning.plist
```

- [ ] **Step 2: Trigger a manual learning loop run to verify it works end-to-end**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 rjm.py content learning 2>&1 | tail -30
```

Expected: exits 0, prints updated weight snapshot, `prompt_weights.json` updated timestamp changes to today.

If it fails, check the log:
```bash
tail -50 "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/logs/launchd_viral_learning.log"
```

- [ ] **Step 3: Verify prompt_weights.json was updated**

```bash
python3.13 -c "
import json
w = json.load(open('prompt_weights.json'))
print('updated:', w['updated'])
print('format_weights:', w['format_weights'])
"
```

Expected: `updated` timestamp is today (2026-04-19), `format_weights` includes `sacred_arc` (from Task 3 defaults applied on first post-overhaul run).

- [ ] **Step 4: No commit needed** — plist was pre-existing. If you had to add `sacred_arc` to an existing on-disk `prompt_weights.json` manually, do:

```bash
python3.13 -c "
import json
from pathlib import Path
p = Path('prompt_weights.json')
w = json.loads(p.read_text())
if 'sacred_arc' not in w.get('format_weights', {}):
    w['format_weights']['sacred_arc'] = 1.0
    p.write_text(json.dumps(w, indent=2))
    print('Added sacred_arc to format_weights')
else:
    print('sacred_arc already present')
"
git add prompt_weights.json
git commit -m "fix(weights): add sacred_arc to format_weights in prompt_weights.json"
```

---

## Task 5: Full dry-run smoke test

Run the full pipeline in dry-run mode to confirm all three changes work together.

- [ ] **Step 1: Run dry-run**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 rjm.py content viral --dry-run 2>&1
```

Expected:
- Exit code 0
- 3 clips rendered
- Clips are named: `sacred_arc_0_<track>.mp4`, `sacred_arc_1_<track>.mp4`, `transitional_2_<track>.mp4`
- No "No valid clips rendered" errors
- Registry written to `data/performance/dry-run/`

- [ ] **Step 2: Verify clip filenames are distinct**

```bash
ls "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/content/output/$(date +%Y-%m-%d)/"
```

Must show 3 different filenames including `sacred_arc_0_` and `sacred_arc_1_`.

- [ ] **Step 3: Check that SACRED_ARC clips used performance footage**

The dry-run log should show segments from `content/videos/performances/`. If it falls back to b-roll (because performances/ has too few files), check that Task 2 fix correctly finds the nested files.

- [ ] **Step 4: Final commit if any last fixes were needed**

```bash
git add -p
git commit -m "fix(pipeline): <describe any last fix>"
```

---

## Verification checklist (run before calling done)

- [ ] `python3.13 -m pytest content_engine/tests/ -v` — all tests pass
- [ ] `python3.13 rjm.py content viral --dry-run` — exit code 0, 3 clips with distinct names
- [ ] `ls content/output/$(date +%Y-%m-%d)/` — confirms `sacred_arc_0_*`, `sacred_arc_1_*`, `transitional_2_*`
- [ ] `launchctl list | grep rjm.viral-learning` — shows the plist is loaded
- [ ] `python3.13 rjm.py content learning` — exits 0, updates prompt_weights.json
- [ ] `git log --oneline -5` — confirms all commits landed

---

## Summary of changes

| File | Change |
|------|--------|
| `content_engine/pipeline.py:526` | Add `clip_idx` to output filename — fixes double-post |
| `content_engine/pipeline.py:448` | `iterdir()` → `rglob("*")` — finds nested performance footage |
| `content_engine/pipeline.py:480` | Handle SACRED_ARC same as TRANSITIONAL for bait pick |
| `content_engine/pipeline.py:519` | Filter segments to `performances/` for SACRED_ARC |
| `content_engine/pipeline.py:546` | SACRED_ARC uses `render_transitional` |
| `content_engine/pipeline.py:127` | Default mix: [SACRED_ARC, SACRED_ARC, TRANSITIONAL] |
| `content_engine/pipeline.py:241` | `run_full_day` locks SACRED_ARC slots (bypasses bandit) |
| `content_engine/types.py:104` | `ClipFormat.SACRED_ARC = "sacred_arc"` |
| `content_engine/types.py:168` | `"sacred_arc": 1.0` in format_weights defaults |
| `content_engine/generator.py:43` | `ClipFormat.SACRED_ARC: "emotional"` in FORMAT_TO_ANGLE |
| `prompt_weights.json` | Add `"sacred_arc": 1.0` if not already present on disk |
