# Viral Hook Library + Kinetic Text Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade daily clip quality by (a) restricting bait picks to viral-style clips only and (b) tightening text overlay pacing with beat-snapped punch-in animation.

**Architecture:** Two orthogonal concerns. **Library side:** bias `TransitionalManager.pick()` via `category_weights` so non-`viral` categories get zero weight, and archive the 83 Pexels clips out of the index. **Text side:** reduce `CAPTION_MAX_WORD_DURATION` from 0.9s → 0.3s and add a per-word punch-in (scale 1.12 → 1.0 + alpha 0 → 255 over 100ms) inside the PyAV frame loop. Hook overlay gets a shorter dwell (hard-cap at 2.4s after start) and a single fade-in, no punch — it's already large.

**Tech Stack:** Python 3.13, PyAV (libav), Pillow for frame-level drawing, FFmpeg filter_complex for hook overlay, pytest for TDD, librosa for beat times (already in use).

---

## Files

- Modify: `content_engine/caption_engine.py` — tighten dwell, add punch-in
- Modify: `content_engine/renderer.py:266-431` — shorten hook overlay dwell
- Modify: `content_engine/pipeline.py` — pass `category_weights` with viral=1.0, others=0.0 on bait pick
- Modify: `content/hooks/transitional/index.json` — filtered to viral-only entries
- Create: `content/hooks/transitional/archive/` — parking lot for Pexels clips
- Create: `scripts/archive_pexels_hooks.py` — one-shot archival script
- Test: `content_engine/tests/test_caption_engine.py` — dwell + punch math
- Test: `content_engine/tests/test_pipeline.py` — viral-only bias assertion
- Test: `content_engine/tests/test_renderer.py` — hook dwell clamp

---

## Task 1: Force viral-only bait picks in pipeline

**Files:**
- Modify: `content_engine/pipeline.py` (bait pick site — around line 486 where `TransitionalManager().pick(...)` is called)
- Test: `content_engine/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add to `content_engine/tests/test_pipeline.py`:

```python
def test_bait_pick_biases_to_viral_category(tmp_path):
    """Bait pick must pass category_weights={viral: 1.0, others: 0.0} so only viral/ clips are used."""
    config = DailyPipelineConfig(
        formats=[ClipFormat.TRANSITIONAL],
        durations={ClipFormat.TRANSITIONAL: 22},
    )

    captured = {}

    with patch("content_engine.renderer.render_transitional"), \
         patch("content_engine.transitional_manager.TransitionalManager") as mock_tm, \
         patch("content_engine.generator.generate_hooks_for_format") as mock_hook, \
         patch("content_engine.generator.generate_caption") as mock_caption, \
         patch("content_engine.renderer.validate_output", return_value={"valid": True, "errors": []}):

        def capture_pick(*args, **kwargs):
            captured["weights"] = kwargs.get("category_weights") or (args[1] if len(args) > 1 else None)
            return {"file": "viral/Wrap-It-LOL.mp4", "category": "viral"}

        mock_tm.return_value.pick.side_effect = capture_pick
        mock_tm.return_value.full_path.return_value = Path("/fake/bait.mp4")
        mock_hook.return_value = {
            "hook": "test", "template_id": "t1",
            "mechanism": "save", "sub_mode": "COST", "exploration": False,
        }
        mock_caption.return_value = "test"

        build_daily_clips(
            config, _make_brief(), _make_weights(), _make_track(), [30.0], str(tmp_path)
        )

    w = captured.get("weights")
    assert w is not None, "pipeline did not pass category_weights to TransitionalManager.pick"
    assert w.get("viral", 0.0) > 0.0, f"viral weight should be >0, got {w}"
    non_viral = {k: v for k, v in w.items() if k != "viral"}
    assert all(v == 0.0 for v in non_viral.values()), (
        f"non-viral categories should be suppressed, got {non_viral}"
    )
```

- [ ] **Step 2: Run to verify fail**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3 -m pytest content_engine/tests/test_pipeline.py::test_bait_pick_biases_to_viral_category -xvs
```

Expected: FAIL with assertion error on `w is not None` or non-viral weight > 0.

- [ ] **Step 3: Implement the bias**

In `content_engine/pipeline.py`, find the `TransitionalManager().pick(...)` call inside `build_daily_clips` (around line 486 based on prior changes). Replace the call with an explicit `category_weights` dict:

```python
# Viral-only bias: only clips from content/hooks/transitional/viral/ are
# eligible for SACRED_ARC / TRANSITIONAL bait. Pexels stock clips in the
# other six categories are effectively archived until we see evidence they
# can match viral/ CTR.
VIRAL_ONLY_CATEGORY_WEIGHTS = {
    "viral": 1.0,
    "nature": 0.0,
    "satisfying": 0.0,
    "elemental": 0.0,
    "sports": 0.0,
    "craftsmanship": 0.0,
    "illusion": 0.0,
}
```

Put that constant near the top of `pipeline.py` (after imports). Then update the pick call:

```python
if fmt in (ClipFormat.TRANSITIONAL, ClipFormat.SACRED_ARC):
    tm = TransitionalManager()
    bait_meta = tm.pick(
        yesterday_category=yesterday_category,
        category_weights=VIRAL_ONLY_CATEGORY_WEIGHTS,
    )
    ...
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m pytest content_engine/tests/test_pipeline.py::test_bait_pick_biases_to_viral_category -xvs
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add content_engine/pipeline.py content_engine/tests/test_pipeline.py
git commit -m "feat(pipeline): bias bait picks to viral/ category only

Pexels stock clips in 6 other categories have never outperformed the
15 viral/ clips. Suppress them with category_weights=0 until we see
CTR evidence otherwise.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Archive Pexels clips from live index

**Files:**
- Create: `scripts/archive_pexels_hooks.py`
- Modify: `content/hooks/transitional/index.json` (regenerated)
- Create: `content/hooks/transitional/archive/` directory

- [ ] **Step 1: Write archival script**

Create `scripts/archive_pexels_hooks.py`:

```python
"""One-shot: move Pexels clips to archive/ and rebuild the live index to only viral clips."""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent
HOOKS = ROOT / "content" / "hooks" / "transitional"
ARCHIVE = HOOKS / "archive"
INDEX = HOOKS / "index.json"

# Only the viral/ category stays in the active index. Everything else
# (nature, satisfying, elemental, sports, craftsmanship, illusion) is
# Pexels stock and gets parked.
KEEP_CATEGORIES = {"viral"}
ARCHIVE_CATEGORIES = {"nature", "satisfying", "elemental", "sports", "craftsmanship", "illusion"}


def main():
    data = json.loads(INDEX.read_text())
    keep = [c for c in data if c["category"] in KEEP_CATEGORIES]
    drop = [c for c in data if c["category"] in ARCHIVE_CATEGORIES]

    ARCHIVE.mkdir(exist_ok=True)
    moved = 0
    for entry in drop:
        src = HOOKS / entry["file"]
        if not src.exists():
            continue
        dst_dir = ARCHIVE / entry["category"]
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        shutil.move(str(src), str(dst))
        moved += 1

    INDEX.write_text(json.dumps(keep, indent=2))

    print(f"Kept {len(keep)} viral clips in live index.")
    print(f"Archived {moved} Pexels clips to {ARCHIVE}/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3 scripts/archive_pexels_hooks.py
```

Expected output: "Kept 15 viral clips" / "Archived 83 Pexels clips".

- [ ] **Step 3: Verify index is clean**

```bash
python3 -c "import json; d=json.load(open('content/hooks/transitional/index.json')); print(len(d), set(c['category'] for c in d))"
```

Expected: `15 {'viral'}`

- [ ] **Step 4: Commit**

```bash
git add scripts/archive_pexels_hooks.py content/hooks/transitional/
git commit -m "chore(hooks): archive 83 Pexels clips, keep only 15 viral bait clips

Pexels stock footage has 0 evidence of viral performance. The 15 clips
in viral/ are all from transitionalhooks-style sources and carry the
proven format. Archive rather than delete so we can A/B later.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Tighten caption dwell

**Files:**
- Modify: `content_engine/caption_engine.py:27-28`
- Test: `content_engine/tests/test_caption_engine.py` (create if not present)

- [ ] **Step 1: Check if test file exists**

```bash
ls content_engine/tests/test_caption_engine.py 2>/dev/null || echo "missing"
```

If missing, create `content_engine/tests/test_caption_engine.py` with:

```python
"""Tests for caption_engine — kinetic text timing + punch-in."""
from content_engine.caption_engine import (
    CAPTION_MIN_WORD_DURATION,
    CAPTION_MAX_WORD_DURATION,
    _compute_windows,
    _split_to_word_groups,
)


def test_max_word_duration_is_snappy():
    """Captions must not linger more than 0.35s per word group."""
    assert CAPTION_MAX_WORD_DURATION <= 0.35, (
        f"CAPTION_MAX_WORD_DURATION={CAPTION_MAX_WORD_DURATION} is too slow. "
        "2026 viral benchmark is 180-250ms/word; cap at 0.35."
    )


def test_min_word_duration_allows_fast_words():
    """Min word duration should allow ≤200ms for on-beat fast pacing."""
    assert CAPTION_MIN_WORD_DURATION <= 0.20


def test_compute_windows_respects_max():
    """Even with sparse beats, no window should exceed 3×MAX."""
    groups = ["HOLD", "THE", "LINE"]
    beats = [0.0, 2.0, 4.0, 6.0]  # sparse: 2s apart
    windows = _compute_windows(groups, beats, total_duration=6.0, start_offset=0.0)
    for s, e in windows:
        assert (e - s) <= CAPTION_MAX_WORD_DURATION * 3 + 0.01, (
            f"window {(s, e)} exceeds 3×MAX ({CAPTION_MAX_WORD_DURATION*3}s)"
        )
```

- [ ] **Step 2: Run to verify fail**

```bash
python3 -m pytest content_engine/tests/test_caption_engine.py::test_max_word_duration_is_snappy -xvs
```

Expected: FAIL with `CAPTION_MAX_WORD_DURATION=0.9 is too slow`.

- [ ] **Step 3: Tighten the constants**

In `content_engine/caption_engine.py`, lines 27-28:

```python
# Kinetic pacing targets (2026 viral benchmark: 180-250ms/word).
# MIN=0.18 lets tight beats snap to fast pacing; MAX=0.30 prevents
# dead air when beats are sparse. A 128 BPM track is ~234ms/beat,
# so 0.30 leaves a single-beat buffer for readability.
CAPTION_MIN_WORD_DURATION = 0.18
CAPTION_MAX_WORD_DURATION = 0.30
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m pytest content_engine/tests/test_caption_engine.py -xvs
```

Expected: PASS (all three tests).

- [ ] **Step 5: Commit**

```bash
git add content_engine/caption_engine.py content_engine/tests/test_caption_engine.py
git commit -m "feat(captions): tighten word dwell to 180-300ms (was 250-900ms)

Previous 900ms cap left captions on screen for ~3 beats at 128 BPM,
well past the 2026 viral benchmark of 180-250ms/word. Lock min to
180ms and max to 300ms so beat-snapped onsets land as punchy hits.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Add per-word punch-in animation

**Files:**
- Modify: `content_engine/caption_engine.py` (inside `_encode_video_with_captions` around line 164-190)
- Test: `content_engine/tests/test_caption_engine.py`

- [ ] **Step 1: Write failing test**

Append to `content_engine/tests/test_caption_engine.py`:

```python
def test_punch_in_scale_interpolation():
    """Punch-in helper must go scale 1.12 → 1.0 over 0-100ms, then stay at 1.0."""
    from content_engine.caption_engine import _punch_in_scale

    # At t=0 (onset): scale peaks at 1.12
    assert abs(_punch_in_scale(0.0) - 1.12) < 0.001

    # At 50ms: midway (scale ~1.06)
    assert 1.05 < _punch_in_scale(0.05) < 1.07

    # At 100ms: settled at 1.0
    assert abs(_punch_in_scale(0.10) - 1.0) < 0.001

    # Beyond 100ms: stays at 1.0 (not overshooting to <1.0)
    assert _punch_in_scale(0.5) == 1.0


def test_punch_in_alpha_interpolation():
    """Alpha must ramp 0 → 255 over 0-100ms, then stay at 255."""
    from content_engine.caption_engine import _punch_in_alpha

    assert _punch_in_alpha(0.0) == 0
    assert 120 < _punch_in_alpha(0.05) < 135
    assert _punch_in_alpha(0.10) == 255
    assert _punch_in_alpha(0.5) == 255
```

- [ ] **Step 2: Run to verify fail**

```bash
python3 -m pytest content_engine/tests/test_caption_engine.py::test_punch_in_scale_interpolation -xvs
```

Expected: FAIL — `_punch_in_scale` doesn't exist.

- [ ] **Step 3: Implement the punch helpers and wire them into the frame loop**

In `content_engine/caption_engine.py`, add these constants after `CAPTION_MAX_WORD_DURATION`:

```python
PUNCH_DURATION = 0.10      # seconds — time to settle from 1.12 → 1.0
PUNCH_SCALE_START = 1.12   # initial oversize
PUNCH_SCALE_END = 1.0
```

Add these helpers above `_draw_text_centered`:

```python
def _punch_in_scale(t_in_window: float) -> float:
    """Linear scale interpolation 1.12 → 1.0 across PUNCH_DURATION."""
    if t_in_window >= PUNCH_DURATION:
        return PUNCH_SCALE_END
    if t_in_window <= 0:
        return PUNCH_SCALE_START
    progress = t_in_window / PUNCH_DURATION
    return PUNCH_SCALE_START + (PUNCH_SCALE_END - PUNCH_SCALE_START) * progress


def _punch_in_alpha(t_in_window: float) -> int:
    """Linear alpha ramp 0 → 255 across PUNCH_DURATION."""
    if t_in_window >= PUNCH_DURATION:
        return 255
    if t_in_window <= 0:
        return 0
    return int(255 * (t_in_window / PUNCH_DURATION))
```

Replace `_get_caption_at` with a version that also returns the window start (needed for t-in-window math):

```python
def _get_caption_at(t: float, sorted_windows: list):
    """Return (caption_text, window_start) for timestamp t, or (None, None) if not in any window."""
    for (s, e), text in sorted_windows:
        if s <= t < e:
            return text, s
        if s > t:
            break
    return None, None
```

Update `_draw_text_centered` to accept scale + alpha:

```python
def _draw_text_centered(draw, text: str, font, frame_width: int, y: int,
                        scale: float = 1.0, alpha: int = 255) -> None:
    """Draw white text with black outline, optionally scaled + alpha-blended, centered at y."""
    if scale == 1.0 and alpha == 255:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        x = (frame_width - text_w) // 2
        draw.text(
            (x, y), text, font=font,
            fill=(255, 255, 255, 255),
            stroke_width=CAPTION_OUTLINE,
            stroke_fill=(0, 0, 0, 255),
        )
        return

    # Scaled + alpha path: render to a tmp RGBA layer and paste.
    # We keep the font constant and scale in pixel space so the outline
    # doesn't balloon at scale 1.12.
    from PIL import Image, ImageDraw as _ID
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad = CAPTION_OUTLINE * 2 + 4
    layer_w = text_w + pad * 2
    layer_h = text_h + pad * 2
    layer = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
    ld = _ID.Draw(layer)
    ld.text(
        (pad, pad), text, font=font,
        fill=(255, 255, 255, alpha),
        stroke_width=CAPTION_OUTLINE,
        stroke_fill=(0, 0, 0, alpha),
    )
    if scale != 1.0:
        new_w = max(1, int(layer_w * scale))
        new_h = max(1, int(layer_h * scale))
        layer = layer.resize((new_w, new_h), Image.LANCZOS)
        layer_w, layer_h = new_w, new_h

    x = (frame_width - layer_w) // 2
    target_y = y - (layer_h - text_h) // 2
    draw._image.paste(layer, (x, target_y), layer)
```

Wire into the frame loop inside `_encode_video_with_captions`:

```python
caption, window_start = _get_caption_at(t, sorted_windows)
if caption:
    t_in = t - window_start
    scale = _punch_in_scale(t_in)
    alpha = _punch_in_alpha(t_in)
    img  = frame.to_image()
    draw = ImageDraw.Draw(img)
    _draw_text_centered(draw, caption, font, v_in.width, CAPTION_POS_Y,
                        scale=scale, alpha=alpha)
    new_frame = av.VideoFrame.from_image(img).reformat(format="yuv420p")
else:
    ...  # unchanged
```

- [ ] **Step 4: Run to verify pass**

```bash
python3 -m pytest content_engine/tests/test_caption_engine.py -xvs
```

Expected: PASS (all five tests).

- [ ] **Step 5: Commit**

```bash
git add content_engine/caption_engine.py content_engine/tests/test_caption_engine.py
git commit -m "feat(captions): add per-word punch-in animation (scale 1.12→1.0, alpha 0→255 over 100ms)

Static hard-cut word appearances read as dead typography on TikTok.
Punch-in gives each word a kinetic onset that reads as intentional
motion — same timing pattern as the reference 20k-view IG post.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Shorten hook overlay dwell

**Files:**
- Modify: `content_engine/renderer.py:266-431` (specifically the `end_time` default calc at line 296-297 and the fade filter at line 399-405)
- Test: `content_engine/tests/test_renderer.py`

- [ ] **Step 1: Check test file**

```bash
ls content_engine/tests/test_renderer.py 2>/dev/null || echo "missing"
```

If missing, create it:

```python
"""Tests for renderer — text overlay timing."""
```

- [ ] **Step 2: Write failing test**

Append to `content_engine/tests/test_renderer.py`:

```python
from content_engine.renderer import _HOOK_DWELL_MAX_S


def test_hook_overlay_dwell_capped():
    """Hook text must never dwell longer than 2.4s — attention-window research."""
    assert _HOOK_DWELL_MAX_S <= 2.4, (
        f"_HOOK_DWELL_MAX_S={_HOOK_DWELL_MAX_S} is too long. "
        "Viral hook research caps at 2.5s before attention drops."
    )


def test_hook_overlay_dwell_floor():
    """Minimum hook dwell — a 7-word hook needs at least ~1.5s to be legible."""
    assert _HOOK_DWELL_MAX_S >= 1.8
```

- [ ] **Step 3: Run to verify fail**

```bash
python3 -m pytest content_engine/tests/test_renderer.py::test_hook_overlay_dwell_capped -xvs
```

Expected: FAIL — `_HOOK_DWELL_MAX_S` doesn't exist.

- [ ] **Step 4: Add the cap and use it**

In `content_engine/renderer.py`, add near the top of the file (after imports, before `HOOK_STYLES`):

```python
# Hook overlay attention window. Research: ~2.5s before scroll risk.
# We cap at 2.2s so the viewer's eye lands on motion (bait clip action)
# before the hook cements, then snaps to the drop.
_HOOK_DWELL_MAX_S = 2.2
```

Update `_burn_text_overlay` to honor the cap. Replace the `end_time` default block (around line 296-297):

```python
if end_time is None:
    natural_end = max(start_time + 0.5, info["duration"] - 1.0) if info["duration"] > 1 else info["duration"]
    end_time = min(natural_end, start_time + _HOOK_DWELL_MAX_S)
else:
    # Honor explicit end_time from caller, but never exceed the cap.
    end_time = min(end_time, start_time + _HOOK_DWELL_MAX_S)
```

- [ ] **Step 5: Run to verify pass**

```bash
python3 -m pytest content_engine/tests/test_renderer.py -xvs
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add content_engine/renderer.py content_engine/tests/test_renderer.py
git commit -m "feat(renderer): cap hook overlay dwell at 2.2s

Previously the hook sat on-screen for the full bait duration (~4-5s),
past the 2.5s attention window. Cap at 2.2s so the viewer's eye lands
on the bait action before the drop cuts in.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Dry-run verification

- [ ] **Step 1: Regression tests**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3 -m pytest content_engine/tests/ -x --ignore=content_engine/tests/test_distributor.py
```

Expected: all pass. (Distributor tests are pre-existing failures, unrelated.)

- [ ] **Step 2: Full pipeline dry-run**

```bash
python3 rjm.py content viral --dry-run 2>&1 | tee /tmp/viral-dryrun.log
```

Expected in log:
- 3 clips planned: 2× SACRED_ARC + 1× TRANSITIONAL
- All three bait clips resolve to files under `content/hooks/transitional/viral/`
- No picks from nature/, satisfying/, elemental/, sports/, craftsmanship/, illusion/

- [ ] **Step 3: Spot-check one rendered clip**

```bash
python3 rjm.py content viral  # real run
ls -lt content/output/ | head -5
```

Open the newest MP4 and verify:
- Hook overlay disappears around 2.2s (not 4-5s)
- Captions punch in crisply — no soft fades on individual words
- Bait segment is from `viral/` (human-named files, not numeric Pexels IDs)

- [ ] **Step 4: Final commit if dry-run surfaces fixes**

If any step needed an additional fix, commit it as a separate `fix:` commit. Otherwise no-op.

---

## Out of scope (deliberately)

- **Downloading new viral clips.** The user will curate from `thetransitionalhooks.com` / `videohooks.app` manually and drop them into `content/hooks/transitional/viral/`. Running `python3 -c "from content_engine.transitional_manager import TransitionalManager; TransitionalManager().scan_for_new_clips()"` picks them up.
- **Per-word beat snapping tightening.** `_compute_windows` already does beat-aligned onsets; we're only changing dwell + animation, not the onset picker.
- **Learning-loop reads of new metrics.** Text kinetics won't have a directly measurable signal beyond the existing share_rate / completion_rate. Keep the existing EMA.
