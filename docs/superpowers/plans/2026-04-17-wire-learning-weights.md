# Wire Viral Learning Weights Into Content Generator

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 4 most impactful dormant weight dimensions — `sub_mode_weights`, `transitional_category_weights`, `format_weights`, and `best_clip_length` — actually drive tomorrow's video generation, so the learning loop closes the feedback loop it was built for.

**Architecture:** Four surgical changes, each touching exactly one function. No new files. No new abstractions. Each change adds one `weights` parameter to an existing function and swaps `random.choice()` for weighted random. The pipeline already passes `UnifiedWeights` end-to-end — we just need to thread the right sub-fields one level deeper.

**Tech Stack:** Python 3.13, existing `content_engine/` module, `pytest` in `tests/`

---

## File Map

| File | Change |
|------|--------|
| `content_engine/generator.py:49-52` | `pick_sub_mode()` — add `weights` param, weighted random |
| `content_engine/generator.py:218-269` | `generate_hooks_for_format()` — pass `sub_mode_weights` through |
| `content_engine/hook_library.py:614-662` | `pick_transitional_hook()` — add `category_weights` param |
| `content_engine/transitional_manager.py:42-47` | `TransitionalManager.pick()` — accept + pass `category_weights` |
| `content_engine/pipeline.py:79-97` | `DailyPipelineConfig` constructor — derive format mix + EMOTIONAL duration from weights |
| `content_engine/pipeline.py:290-388` | `build_daily_clips()` — pass `category_weights` to `TransitionalManager.pick()` |
| `tests/test_viral_hook_library.py` | New tests for `pick_transitional_hook` with category weights |
| `tests/test_types.py` | New tests for `pick_sub_mode` with weights, `derive_format_mix` |

---

## Task 1: Wire `sub_mode_weights` into `pick_sub_mode()`

**Files:**
- Modify: `content_engine/generator.py:49-52` (`pick_sub_mode`)
- Modify: `content_engine/generator.py:218-269` (`generate_hooks_for_format`)
- Modify: `content_engine/pipeline.py:350-360` (`build_daily_clips` call site)
- Test: `tests/test_types.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_types.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from content_engine.generator import pick_sub_mode

def test_pick_sub_mode_no_weights_returns_valid_mode():
    result = pick_sub_mode("emotional", {})
    assert result in ["COST", "NAMING", "DOUBT", "DEVOTION", "RUPTURE"]

def test_pick_sub_mode_weighted_favors_high_weight():
    # DEVOTION at 1000x should win almost every time
    weights = {"COST": 0.001, "NAMING": 0.001, "DOUBT": 0.001, "DEVOTION": 100.0, "RUPTURE": 0.001}
    results = [pick_sub_mode("emotional", weights) for _ in range(200)]
    assert results.count("DEVOTION") > 160, f"Expected DEVOTION to dominate, got {results.count('DEVOTION')}/200"

def test_pick_sub_mode_zero_weights_still_returns_valid():
    # All zeros → fallback to equal probability
    weights = {"COST": 0.0, "NAMING": 0.0, "DOUBT": 0.0, "DEVOTION": 0.0, "RUPTURE": 0.0}
    result = pick_sub_mode("emotional", weights)
    assert result in ["COST", "NAMING", "DOUBT", "DEVOTION", "RUPTURE"]

def test_pick_sub_mode_unknown_angle_uses_emotional_modes():
    result = pick_sub_mode("nonexistent_angle", {})
    assert result in ["COST", "NAMING", "DOUBT", "DEVOTION", "RUPTURE"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python -m pytest tests/test_types.py::test_pick_sub_mode_no_weights_returns_valid_mode tests/test_types.py::test_pick_sub_mode_weighted_favors_high_weight -v 2>&1 | tail -20
```

Expected: `TypeError: pick_sub_mode() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Implement weighted `pick_sub_mode()`**

Replace `content_engine/generator.py` lines 49-52:

```python
def pick_sub_mode(angle: str, sub_mode_weights: dict | None = None) -> str:
    """Pick a sub-mode for the given angle, weighted by learned performance."""
    modes = ANGLE_SUB_MODES.get(angle, ANGLE_SUB_MODES["emotional"])
    w = sub_mode_weights or {}
    scores = [(m, max(w.get(m, 1.0), 0.0)) for m in modes]
    total = sum(s for _, s in scores)
    if total == 0:
        return random.choice(modes)
    r = random.random() * total
    cumulative = 0.0
    for m, s in scores:
        cumulative += s
        if r <= cumulative:
            return m
    return scores[-1][0]
```

- [ ] **Step 4: Thread `sub_mode_weights` through `generate_hooks_for_format()`**

In `content_engine/generator.py`, update the function signature at line 218 and the `pick_sub_mode` call at line 235:

```python
def generate_hooks_for_format(
    fmt: ClipFormat,
    track_title: str,
    track_facts: dict,
    weights: dict | None = None,
    exclude_ids: set | None = None,
    visual_context: dict | None = None,
    sub_mode_weights: dict | None = None,   # ← add this param
) -> dict:
```

Then update the `pick_sub_mode` call (currently line 235):
```python
    sub_mode = pick_sub_mode(angle, sub_mode_weights)
```

- [ ] **Step 5: Thread `sub_mode_weights` through `build_daily_clips()`**

In `content_engine/pipeline.py`, find the `generate_hooks_for_format` call (around line 355) and add the kwarg:

```python
        hook_data = generate_hooks_for_format(
            fmt, track.title, track_facts, weights.hook_weights, used_ids,
            visual_context=visual_context,
            sub_mode_weights=weights.sub_mode_weights,   # ← add this line
        )
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python -m pytest tests/test_types.py::test_pick_sub_mode_no_weights_returns_valid_mode tests/test_types.py::test_pick_sub_mode_weighted_favors_high_weight tests/test_types.py::test_pick_sub_mode_zero_weights_still_returns_valid tests/test_types.py::test_pick_sub_mode_unknown_angle_uses_emotional_modes -v 2>&1 | tail -20
```

Expected: all 4 PASSED

- [ ] **Step 7: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add content_engine/generator.py content_engine/pipeline.py tests/test_types.py
git commit -m "feat(generator): wire sub_mode_weights into pick_sub_mode — hook emotional angle now weight-driven"
```

---

## Task 2: Wire `transitional_category_weights` into bait clip selection

**Files:**
- Modify: `content_engine/hook_library.py:614-662` (`pick_transitional_hook`)
- Modify: `content_engine/transitional_manager.py:42-47` (`TransitionalManager.pick`)
- Modify: `content_engine/pipeline.py` (`build_daily_clips` call site)
- Test: `tests/test_viral_hook_library.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_viral_hook_library.py` (append below existing tests):

```python
# ── category_weights tests ─────────────────────────────────────────────────

def _make_bank():
    return [
        {"file": "nature/a.mp4", "category": "nature", "last_used": None, "performance_score": 1.0, "times_used": 0},
        {"file": "satisfying/b.mp4", "category": "satisfying", "last_used": None, "performance_score": 1.0, "times_used": 0},
        {"file": "elemental/c.mp4", "category": "elemental", "last_used": None, "performance_score": 1.0, "times_used": 0},
    ]

def test_pick_transitional_hook_category_weights_bias():
    from content_engine.hook_library import pick_transitional_hook
    bank = _make_bank()
    weights = {"nature": 100.0, "satisfying": 0.001, "elemental": 0.001}
    results = [pick_transitional_hook(bank, category_weights=weights) for _ in range(100)]
    categories = [r["category"] for r in results]
    assert categories.count("nature") > 80, f"Expected nature to dominate, got {categories.count('nature')}/100"

def test_pick_transitional_hook_no_category_weights_unchanged():
    from content_engine.hook_library import pick_transitional_hook
    bank = _make_bank()
    result = pick_transitional_hook(bank)  # no category_weights — existing behaviour
    assert result is not None
    assert result["category"] in {"nature", "satisfying", "elemental"}

def test_pick_transitional_hook_zero_weight_category_excluded():
    from content_engine.hook_library import pick_transitional_hook
    bank = _make_bank()
    weights = {"nature": 0.0, "satisfying": 1.0, "elemental": 1.0}
    results = [pick_transitional_hook(bank, category_weights=weights) for _ in range(50)]
    categories = [r["category"] for r in results]
    assert "nature" not in categories, "nature had 0.0 weight, should never be picked"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python -m pytest tests/test_viral_hook_library.py::test_pick_transitional_hook_category_weights_bias -v 2>&1 | tail -15
```

Expected: `TypeError: pick_transitional_hook() got an unexpected keyword argument 'category_weights'`

- [ ] **Step 3: Add `category_weights` to `pick_transitional_hook()`**

In `content_engine/hook_library.py`, update the function starting at line 614. Change the signature and the weighted-random section at the bottom:

```python
def pick_transitional_hook(
    bank: list[dict],
    yesterday_category: str | None = None,
    category_weights: dict | None = None,   # ← add this param
) -> dict | None:
    """Pick a transitional visual hook clip from the bank.

    Rules:
    - 7-day cooldown: skip if last_used within 7 days
    - Category diversity: skip yesterday's category
    - Weighted random by performance_score × category_weight
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
        eligible = [
            h for h in bank
            if not h["last_used"] or date.fromisoformat(h["last_used"]) < cooldown
        ]
    if not eligible:
        eligible = bank

    if not eligible:
        return None

    # Weighted random by performance_score × learned category weight
    cw = category_weights or {}
    scores = [(h, h["performance_score"] * max(cw.get(h["category"], 1.0), 0.0)) for h in eligible]
    total = sum(s for _, s in scores)
    if total == 0:
        return random.choice(eligible)
    r = random.random() * total
    cumulative = 0.0
    for h, s in scores:
        cumulative += s
        if r <= cumulative:
            return h
    return scores[-1][0]
```

- [ ] **Step 4: Thread `category_weights` through `TransitionalManager.pick()`**

In `content_engine/transitional_manager.py`, update `pick()` at line 42:

```python
    def pick(self, yesterday_category: Optional[str] = None, category_weights: Optional[dict] = None) -> Optional[dict]:
        """Pick a transitional hook clip respecting cooldown, diversity, and category weights."""
        if not self.bank:
            logger.warning("Transitional hook bank is empty")
            return None
        return pick_transitional_hook(self.bank, yesterday_category, category_weights=category_weights)
```

- [ ] **Step 5: Pass `transitional_category_weights` from `build_daily_clips()`**

In `content_engine/pipeline.py`, find the `tm.pick()` call (around line 344) and add the kwarg:

```python
            bait = tm.pick(category_weights=weights.transitional_category_weights)
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python -m pytest tests/test_viral_hook_library.py::test_pick_transitional_hook_category_weights_bias tests/test_viral_hook_library.py::test_pick_transitional_hook_no_category_weights_unchanged tests/test_viral_hook_library.py::test_pick_transitional_hook_zero_weight_category_excluded -v 2>&1 | tail -15
```

Expected: all 3 PASSED

- [ ] **Step 7: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add content_engine/hook_library.py content_engine/transitional_manager.py content_engine/pipeline.py tests/test_viral_hook_library.py
git commit -m "feat(pipeline): wire transitional_category_weights into bait clip selection"
```

---

## Task 3: Wire `format_weights` into daily clip format mix

**Files:**
- Modify: `content_engine/pipeline.py:79-97` (`DailyPipelineConfig` + new helper `derive_format_mix`)
- Test: `tests/test_types.py`

Context: `DailyPipelineConfig.formats` is currently hard-coded to `[TRANSITIONAL, TRANSITIONAL, PERFORMANCE]`. We'll derive this from `format_weights` at pipeline startup. The helper caps any single format at 2 of 3 slots to maintain variety.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_types.py`:

```python
from content_engine.pipeline import derive_format_mix
from content_engine.types import ClipFormat

def test_derive_format_mix_equal_weights_all_formats_possible():
    weights = {"transitional": 1.0, "emotional": 1.0, "performance": 1.0}
    seen = set()
    for _ in range(200):
        mix = derive_format_mix(weights)
        seen.update(mix)
    assert ClipFormat.TRANSITIONAL in seen
    assert ClipFormat.EMOTIONAL in seen
    assert ClipFormat.PERFORMANCE in seen

def test_derive_format_mix_dominant_weight_favors_format():
    weights = {"transitional": 10.0, "emotional": 0.01, "performance": 0.01}
    mixes = [derive_format_mix(weights) for _ in range(50)]
    transitional_counts = [m.count(ClipFormat.TRANSITIONAL) for m in mixes]
    assert sum(transitional_counts) / len(transitional_counts) > 1.5  # avg > 1.5 of 3 slots

def test_derive_format_mix_caps_at_two_per_format():
    weights = {"transitional": 999.0, "emotional": 0.0, "performance": 0.0}
    for _ in range(20):
        mix = derive_format_mix(weights)
        assert mix.count(ClipFormat.TRANSITIONAL) <= 2, "No format should occupy all 3 slots"

def test_derive_format_mix_returns_three_clips():
    weights = {"transitional": 1.0, "emotional": 1.0, "performance": 1.0}
    assert len(derive_format_mix(weights)) == 3
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python -m pytest tests/test_types.py::test_derive_format_mix_returns_three_clips -v 2>&1 | tail -10
```

Expected: `ImportError: cannot import name 'derive_format_mix' from 'content_engine.pipeline'`

- [ ] **Step 3: Implement `derive_format_mix()` in pipeline.py**

Add this function to `content_engine/pipeline.py` immediately before the `DailyPipelineConfig` class definition:

```python
def derive_format_mix(format_weights: dict, n_clips: int = 3) -> list:
    """Derive a clip format mix from learned weights.

    Each format can occupy at most n_clips-1 slots so the mix always has
    at least two distinct formats. Zero-weight formats are never picked.
    """
    formats = [ClipFormat.TRANSITIONAL, ClipFormat.EMOTIONAL, ClipFormat.PERFORMANCE]
    base_weights = [max(format_weights.get(f.value, 1.0), 0.0) for f in formats]
    result = []
    counts = {f: 0 for f in formats}
    max_per_format = n_clips - 1

    while len(result) < n_clips:
        available = [
            (f, w) for f, w in zip(formats, base_weights)
            if counts[f] < max_per_format
        ]
        if not available:
            available = list(zip(formats, base_weights))
        total = sum(w for _, w in available)
        if total == 0:
            # All weights zero — fall back to uniform
            f = random.choice([f for f, _ in available])
        else:
            r = random.random() * total
            cumulative = 0.0
            f = available[-1][0]
            for fmt, w in available:
                cumulative += w
                if r <= cumulative:
                    f = fmt
                    break
        result.append(f)
        counts[f] += 1

    return result
```

- [ ] **Step 4: Use `derive_format_mix()` in pipeline.py `run_pipeline()`**

In `content_engine/pipeline.py`, find where `DailyPipelineConfig` is instantiated (search for `DailyPipelineConfig()`). Update it to pass the derived format mix:

```python
    weights = UnifiedWeights.load()
    logger.info(f"[pipeline] Weights loaded — best platform: {weights.best_platform}")

    config = DailyPipelineConfig(
        formats=derive_format_mix(weights.format_weights),
    )
```

If `DailyPipelineConfig` is currently instantiated without arguments (using all defaults), you just need to add the `formats=` kwarg. The `durations` dict stays as-is at this step (handled in Task 4).

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python -m pytest tests/test_types.py::test_derive_format_mix_equal_weights_all_formats_possible tests/test_types.py::test_derive_format_mix_dominant_weight_favors_format tests/test_types.py::test_derive_format_mix_caps_at_two_per_format tests/test_types.py::test_derive_format_mix_returns_three_clips -v 2>&1 | tail -15
```

Expected: all 4 PASSED

- [ ] **Step 6: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add content_engine/pipeline.py tests/test_types.py
git commit -m "feat(pipeline): derive format mix from format_weights — transitional/emotional/performance now weight-driven"
```

---

## Task 4: Wire `best_clip_length` into EMOTIONAL clip duration

**Files:**
- Modify: `content_engine/pipeline.py` (`DailyPipelineConfig` instantiation)
- Test: `tests/test_types.py`

Context: TRANSITIONAL (22s) and PERFORMANCE (28s) are structurally constrained by bait-clip length and segment count. EMOTIONAL is fully flexible — currently hard-coded at 7s. We clamp `best_clip_length` to [5, 15] so an extreme learning signal doesn't produce a 1s or 60s clip.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_types.py`:

```python
from content_engine.pipeline import emotional_duration_from_weights

def test_emotional_duration_returns_best_clip_length_clamped():
    assert emotional_duration_from_weights(5) == 5
    assert emotional_duration_from_weights(7) == 7
    assert emotional_duration_from_weights(15) == 15

def test_emotional_duration_clamps_below_minimum():
    assert emotional_duration_from_weights(2) == 5
    assert emotional_duration_from_weights(0) == 5

def test_emotional_duration_clamps_above_maximum():
    assert emotional_duration_from_weights(30) == 15
    assert emotional_duration_from_weights(60) == 15
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python -m pytest tests/test_types.py::test_emotional_duration_returns_best_clip_length_clamped -v 2>&1 | tail -10
```

Expected: `ImportError: cannot import name 'emotional_duration_from_weights' from 'content_engine.pipeline'`

- [ ] **Step 3: Implement `emotional_duration_from_weights()` in pipeline.py**

Add this one-liner helper to `content_engine/pipeline.py` alongside `derive_format_mix`:

```python
def emotional_duration_from_weights(best_clip_length: int, min_s: int = 5, max_s: int = 15) -> int:
    return max(min_s, min(max_s, best_clip_length))
```

- [ ] **Step 4: Wire into `DailyPipelineConfig` instantiation**

Update the `DailyPipelineConfig` construction in `run_pipeline()` to pass the derived durations dict:

```python
    config = DailyPipelineConfig(
        formats=derive_format_mix(weights.format_weights),
        durations={
            ClipFormat.TRANSITIONAL: 22,
            ClipFormat.EMOTIONAL: emotional_duration_from_weights(weights.best_clip_length),
            ClipFormat.PERFORMANCE: 28,
        },
    )
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python -m pytest tests/test_types.py::test_emotional_duration_returns_best_clip_length_clamped tests/test_types.py::test_emotional_duration_clamps_below_minimum tests/test_types.py::test_emotional_duration_clamps_above_maximum -v 2>&1 | tail -15
```

Expected: all 3 PASSED

- [ ] **Step 6: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add content_engine/pipeline.py tests/test_types.py
git commit -m "feat(pipeline): wire best_clip_length into emotional clip duration — clamped to [5,15]s"
```

---

## Task 5: Smoke-test the full pipeline end-to-end

**Files:** None modified — integration check only.

- [ ] **Step 1: Run the existing test suite to check for regressions**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python -m pytest tests/test_viral_hook_library.py tests/test_types.py tests/test_learning_loop.py -v 2>&1 | tail -30
```

Expected: all tests pass, no regressions in `test_learning_loop.py`

- [ ] **Step 2: Dry-run the pipeline to confirm weights are flowing**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python -m pytest tests/ -v --tb=short 2>&1 | grep -E "PASSED|FAILED|ERROR" | tail -30
```

- [ ] **Step 3: Check the pipeline reads weights correctly**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
/opt/homebrew/bin/python3.13 -c "
from content_engine.pipeline import derive_format_mix, emotional_duration_from_weights
from content_engine.types import UnifiedWeights
w = UnifiedWeights.load()
print('format_mix:', derive_format_mix(w.format_weights))
print('emotional_duration:', emotional_duration_from_weights(w.best_clip_length))
print('sub_mode_weights top-3:', sorted(w.sub_mode_weights.items(), key=lambda x: -x[1])[:3])
print('category_weights top-3:', sorted(w.transitional_category_weights.items(), key=lambda x: -x[1])[:3])
"
```

Expected: prints 4 lines with actual values from `prompt_weights.json`, no exceptions.

- [ ] **Step 4: Final commit if smoke test passes (no code changes needed)**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git log --oneline -5
```

Confirm the 4 task commits are present. No further commit needed for this step.

---

## What this does NOT change (intentional scope)

- `visual_weights` — would require video files to be tagged by type (b_roll/performance/phone). Infrastructure doesn't exist yet.
- `platform_weights` / `best_platform` — platform mix is a distributor concern, not a generator concern. Separate plan needed.
- `time_of_day_weights` / `best_time_of_day` — no scheduling integration in the pipeline. Separate plan needed.
- `track_weights` — already wired. No change needed.
- `hook_weights` — already wired. No change needed.
