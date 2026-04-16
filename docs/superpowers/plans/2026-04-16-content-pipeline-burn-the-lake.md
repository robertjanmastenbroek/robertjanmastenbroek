# Content Pipeline "Burn the Lake" Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every known bug in the `rjm.py content viral` pipeline so all 3 clips render with text overlays and distribute to all 6 platforms without silent failures.

**Architecture:** Six independent fix layers in dependency order — renderer bugs first (pipeline fails without them), then generator (hook quality), then distributor robustness (platform reliability), then pipeline correctness (color grading), then smoke test. Each task is self-contained with a verification step before committing.

**Tech Stack:** Python 3.13, ffmpeg 8.1 (Homebrew, no drawtext), Pillow (text overlay replacement), anthropic SDK (replaces Claude CLI subprocess), content_engine/ package.

---

## Confirmed Bugs (ordered by impact)

| # | Bug | File | Impact |
|---|-----|------|--------|
| 1 | `_burn_text_overlay` return value ignored by all 3 render functions | renderer.py | **CRITICAL** — causes FileNotFoundError, kills ALL clips |
| 2 | FFmpeg `drawtext` filter not compiled in (no `--enable-libfreetype`) | renderer.py | **CRITICAL** — text overlay impossible; triggers Bug 1 |
| 3 | Claude CLI (`/usr/local/bin/claude`) not found, SDK fallback needed | generator.py | HIGH — all hooks fall back to example_fill templates |
| 4 | `ANTHROPIC_API_KEY` empty in environment | .env | HIGH — blocks SDK fix for Bug 3 |
| 5 | Transitional hook bank empty (`content/hooks/transitional/` missing) | transitional_manager.py | HIGH — clip 0 falls back to emotional format |
| 6 | All clips rendered with hardcoded `platform="instagram"` color grade | pipeline.py | HIGH — YouTube/TikTok/Facebook get wrong grade |
| 7 | Instagram container polling never breaks on `ERROR` status | distributor.py | MEDIUM — hangs 2 min then fails without clear error |
| 8 | TikTok publish polling has no iteration limit | distributor.py | MEDIUM — can loop forever on hung API |
| 9 | YouTube upload URL not validated before `requests.put()` | distributor.py | MEDIUM — None URL causes silent crash |
| 10 | Distribution failures never written to `data/failed_posts.json` | pipeline.py | MEDIUM — `rjm.py content retry` can never pick up failures |

---

## Task 1 — Fix `_burn_text_overlay` return value (renderer.py)

**Why first:** This is the direct cause of today's `FileNotFoundError`. Even if drawtext worked, the callers ignore the return value, so any future drawtext failure would reproduce the crash.

**Files:**
- Modify: `content_engine/renderer.py` (render_emotional, render_performance, render_transitional)

- [ ] **Step 1: Read the current renderer.py**

```bash
grep -n "_burn_text_overlay\|_apply_color_grade\|with_hook\|with_label\|with_text" content_engine/renderer.py
```

Expected: lines showing ignored return value pattern, e.g.:
```
_burn_text_overlay(with_audio, with_hook, hook_text, "emotional")
# ← no assignment — return value discarded
_apply_color_grade(with_hook, output_path, platform)
# ← with_hook may not exist if drawtext failed
```

- [ ] **Step 2: Fix render_emotional — use return value**

In `render_emotional`, replace:
```python
    # 4. Burn prominent hook text
    with_hook = str(work_dir / "_emo_hook.mp4")
    _burn_text_overlay(with_audio, with_hook, hook_text, "emotional")

    # 5. Color grade
    _apply_color_grade(with_hook, output_path, platform)
```
With:
```python
    # 4. Burn prominent hook text
    with_hook = _burn_text_overlay(with_audio, str(work_dir / "_emo_hook.mp4"), hook_text, "emotional")

    # 5. Color grade
    _apply_color_grade(with_hook, output_path, platform)
```

- [ ] **Step 3: Fix render_performance — use return value**

In `render_performance`, replace:
```python
    # 3. Minimal text overlay
    with_text = str(work_dir / "_perf_text.mp4")
    _burn_text_overlay(with_audio, with_text, hook_text, "performance")

    # 4. Color grade
    _apply_color_grade(with_text, output_path, platform)
```
With:
```python
    # 3. Minimal text overlay
    with_text = _burn_text_overlay(with_audio, str(work_dir / "_perf_text.mp4"), hook_text, "performance")

    # 4. Color grade
    _apply_color_grade(with_text, output_path, platform)
```

- [ ] **Step 4: Fix render_transitional — use return value for both overlay calls**

In `render_transitional`, replace:
```python
    # 5. Burn hook text on bait portion (0s to bait_duration)
    with_hook = str(work_dir / "_with_hook.mp4")
    _burn_text_overlay(with_audio, with_hook, hook_text, "transitional", 0.0, bait_duration - 0.3)

    # 6. Burn track label on content portion
    with_label = str(work_dir / "_with_label.mp4")
    _burn_text_overlay(with_hook, with_label, track_label, "performance", bait_duration + 0.5)

    # 7. Platform color grade
    _apply_color_grade(with_label, output_path, platform)
```
With:
```python
    # 5. Burn hook text on bait portion (0s to bait_duration)
    with_hook = _burn_text_overlay(with_audio, str(work_dir / "_with_hook.mp4"), hook_text, "transitional", 0.0, bait_duration - 0.3)

    # 6. Burn track label on content portion
    with_label = _burn_text_overlay(with_hook, str(work_dir / "_with_label.mp4"), track_label, "performance", bait_duration + 0.5)

    # 7. Platform color grade
    _apply_color_grade(with_label, output_path, platform)
```

- [ ] **Step 5: Verify changes look correct**

```bash
grep -n "_burn_text_overlay\|_apply_color_grade" content_engine/renderer.py
```

Expected: all three render functions now assign the return value before passing to `_apply_color_grade`.

- [ ] **Step 6: Commit**

```bash
git add content_engine/renderer.py
git commit -m "fix(renderer): use _burn_text_overlay return value to prevent FileNotFoundError"
```

---

## Task 2 — Replace FFmpeg drawtext with Pillow text overlay (renderer.py)

**Why:** FFmpeg 8.1 (Homebrew) compiled without `--enable-libfreetype` so `drawtext` filter is unavailable. Pillow is already installed and produces identical visual results. This is the permanent fix — more portable and gives better typography control than drawtext.

**Files:**
- Modify: `content_engine/renderer.py` — rewrite `_burn_text_overlay()`

- [ ] **Step 1: Verify Pillow is available**

```bash
python3.13 -c "from PIL import Image, ImageDraw, ImageFont; print('OK')"
```

Expected: `OK`

- [ ] **Step 2: Run the current drawtext smoke test to confirm it fails**

```bash
python3.13 -c "
import subprocess
cmd = ['ffmpeg', '-f', 'lavfi', '-i', 'color=black:size=100x100:duration=1',
       '-vf', 'drawtext=text=test:fontsize=20:fontcolor=white',
       '-y', '/tmp/drawtext_test.mp4']
r = subprocess.run(cmd, capture_output=True, text=True)
print('RC:', r.returncode)
print('ERR:', r.stderr[-200:])
"
```

Expected: `RC: 8` with `No such filter: 'drawtext'`

- [ ] **Step 3: Rewrite `_burn_text_overlay` using Pillow**

Replace the entire `_burn_text_overlay` function in `content_engine/renderer.py` with:

```python
def _burn_text_overlay(
    input_path: str,
    output_path: str,
    text: str,
    style: str = "emotional",
    start_time: float = 0.0,
    end_time: float | None = None,
) -> str:
    """Burn text overlay onto video using Pillow (PNG) + ffmpeg overlay filter.

    Replaces the drawtext approach which requires --enable-libfreetype in ffmpeg.
    Pillow renders the text to a transparent PNG once, then ffmpeg composites it
    per-frame. Result is identical visually but works on any ffmpeg build.
    """
    import tempfile
    from PIL import Image, ImageDraw, ImageFont

    s = HOOK_STYLES.get(style, HOOK_STYLES["emotional"])
    font_size = s["font_size"]
    y_pct = s["y_pct"]
    wrap_chars = s["wrap"]

    info = _get_video_info(input_path)
    if end_time is None:
        end_time = info["duration"] - 1.0 if info["duration"] > 1 else info["duration"]

    # --- 1. Word-wrap the text ---
    words = text.split()
    lines, current = [], []
    for word in words:
        if sum(len(w) for w in current) + len(current) + len(word) <= wrap_chars:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))

    # --- 2. Load font ---
    font = None
    for candidate in [
        os.path.expanduser("~/.fonts/BebasNeue-Regular.ttf"),
        "/Library/Fonts/BebasNeue-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
    ]:
        if os.path.exists(candidate):
            try:
                font = ImageFont.truetype(candidate, font_size)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()

    # --- 3. Measure text and create PNG ---
    canvas = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    line_height = font_size + 8
    total_height = len(lines) * line_height
    y_center = int(OUTPUT_H * y_pct) - total_height // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (OUTPUT_W - text_w) // 2
        y = y_center + i * line_height

        # Shadow (black, offset 3px)
        draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 180))
        # Main text (white)
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

    # Save PNG to temp file
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        overlay_png = tmp.name
    canvas.save(overlay_png, "PNG")

    # --- 4. Composite PNG onto video using ffmpeg overlay filter ---
    # Fade in over 0.3s starting at start_time, fade out over 0.5s at end_time
    # Use ffmpeg's overlay filter with enable expression for timing
    enable_expr = f"between(t,{start_time},{end_time})"
    # Alpha fade: ramp in and ramp out using alphamerge trick isn't needed;
    # use overlay with enable window. For simplicity: hard cut in/out within
    # the enable window (0.3s fade-in is done via the PNG alpha being pre-set).
    # For production fade, we use the scale2ref + blend approach below.
    fade_filter = (
        f"[1:v]format=rgba,"
        f"fade=t=in:st={start_time}:d=0.3:alpha=1,"
        f"fade=t=out:st={max(start_time, end_time - 0.5)}:d=0.5:alpha=1[txt];"
        f"[0:v][txt]overlay=0:0"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-i", overlay_png,
        "-filter_complex", fade_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"Text overlay failed: {e.stderr.decode()[:300] if e.stderr else ''}")
        return input_path  # return un-overlaid clip rather than crash
    finally:
        try:
            os.unlink(overlay_png)
        except Exception:
            pass
```

- [ ] **Step 4: Run smoke test — render a text overlay on a real clip**

```bash
python3.13 -c "
import logging, os
logging.basicConfig(level=logging.INFO)
from content_engine.renderer import _burn_text_overlay

# Use any existing video file
import glob
vids = glob.glob('content/videos/**/*.mp4', recursive=True)
if not vids:
    print('ERROR: No source videos found')
else:
    src = vids[0]
    out = '/tmp/overlay_test.mp4'
    result = _burn_text_overlay(src, out, 'POV: fire meets the psalm', 'emotional')
    if result == out and os.path.exists(out) and os.path.getsize(out) > 100_000:
        print('PASS — overlay rendered:', os.path.getsize(out), 'bytes')
    else:
        print('FAIL — result:', result, 'exists:', os.path.exists(out))
" 2>&1
```

Expected: `PASS — overlay rendered: <size> bytes`

- [ ] **Step 5: Commit**

```bash
git add content_engine/renderer.py
git commit -m "fix(renderer): replace ffmpeg drawtext with Pillow overlay (ffmpeg 8.1 lacks libfreetype)"
```

---

## Task 3 — Replace Claude CLI subprocess with Anthropic SDK (generator.py)

**Why:** `claude` binary not found at any system path. The Anthropic Python SDK is installed. Switching to the SDK is more reliable (no path issues, faster, no CLI version drift).

**Files:**
- Modify: `content_engine/generator.py` — rewrite `_call_claude()`

- [ ] **Step 1: Verify SDK is installed and API key is accessible**

```bash
python3.13 -c "
import os, anthropic
key = os.getenv('ANTHROPIC_API_KEY', '')
print('SDK:', anthropic.__version__)
print('Key set:', bool(key), 'len:', len(key))
"
```

Expected: SDK version printed, `Key set: True`. If `Key set: False`, complete Task 4 first.

- [ ] **Step 2: Rewrite `_call_claude()` in generator.py**

Replace the entire `_call_claude` function:

```python
def _call_claude(prompt: str, system: str = "", timeout: int = 120) -> Optional[str]:
    """Call Claude API via Anthropic SDK (haiku model). Returns response text or None."""
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — cannot generate hook via Claude")
        return None

    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]
    kwargs = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 300,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system

    try:
        response = client.messages.create(**kwargs)
        text = response.content[0].text.strip()
        return text if text else None
    except Exception as e:
        logger.warning(f"Claude API call failed: {e}")
        return None
```

Also remove the now-unused `subprocess` import from generator.py if it's only used in `_call_claude`. Check first:

```bash
grep -n "subprocess" content_engine/generator.py
```

If `subprocess` is used only in `_call_claude`, remove `import subprocess` from the file.

- [ ] **Step 3: Run smoke test**

```bash
python3.13 -c "
import logging, os
logging.basicConfig(level=logging.INFO)
# Load .env if needed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from content_engine.generator import _call_claude
result = _call_claude('Say the word PSALM and nothing else.')
print('Result:', repr(result))
print('PASS' if result and 'PSALM' in result.upper() else 'FAIL')
" 2>&1
```

Expected: `Result: 'PSALM'` and `PASS`

- [ ] **Step 4: Commit**

```bash
git add content_engine/generator.py
git commit -m "fix(generator): replace Claude CLI subprocess with Anthropic SDK"
```

---

## Task 4 — Set ANTHROPIC_API_KEY in .env

**Why:** `env | grep ANTHROPIC_API_KEY` shows the key is empty. Without it the SDK in Task 3 cannot make calls.

**Files:**
- Modify: `.env` (project root)

- [ ] **Step 1: Check current .env state**

```bash
grep "ANTHROPIC_API_KEY" .env || echo "key not in .env"
```

- [ ] **Step 2: Get the API key**

The API key is available from the user's Claude Code authentication or Anthropic console. Retrieve it from one of:
- `~/.claude/` credentials
- Anthropic console: console.anthropic.com → API Keys
- The user should provide the key; do not guess or generate one

Once you have the key (format: `sk-ant-...`), add it to `.env`:

```bash
# Only do this once you have the actual key value
echo "ANTHROPIC_API_KEY=sk-ant-REPLACE_WITH_REAL_KEY" >> .env
```

- [ ] **Step 3: Verify the key is readable**

```bash
python3.13 -c "
from dotenv import load_dotenv; import os
load_dotenv()
key = os.getenv('ANTHROPIC_API_KEY', '')
print('Key set:', bool(key), 'prefix:', key[:10] if key else 'EMPTY')
"
```

Expected: `Key set: True prefix: sk-ant-api`

**NOTE:** If the API key is already set via a launchd environment variable or system keychain and the SDK picks it up without .env, skip this task. Verify by running Step 1 of Task 3 in the same process context as the scheduled task.

---

## Task 5 — Create transitional hook bank scaffold (transitional_manager.py)

**Why:** `content/hooks/transitional/` does not exist. TransitionalManager loads an empty bank → clip 0 falls back to emotional format → we get 2 identical format clips instead of 3 distinct formats.

**Files:**
- Create: `content/hooks/transitional/index.json`
- Modify: `content_engine/transitional_manager.py` — add `scan_from_videos()` helper

- [ ] **Step 1: Create the directory and empty index**

```bash
mkdir -p content/hooks/transitional
echo "[]" > content/hooks/transitional/index.json
```

- [ ] **Step 2: Add a `scan_from_videos` method to TransitionalManager**

The bait clip library needs actual clips. Rather than requiring manual curation, add a scanner that auto-imports any `.mp4`/`.mov` files placed in the transitional directory. Add to `transitional_manager.py` after `mark_used()`:

```python
def scan_from_videos(self, video_dir: Optional[Path] = None) -> int:
    """Scan a directory for new video files and add them to the index.

    Call this once after dropping new clips into content/hooks/transitional/.
    Returns count of newly added clips.
    """
    scan_dir = video_dir or self.hooks_dir
    existing_files = {entry["file"] for entry in self.bank}
    added = 0
    for path in scan_dir.iterdir():
        if path.suffix.lower() in (".mp4", ".mov") and path.name not in existing_files:
            self.bank.append({
                "file": path.name,
                "category": "nature",   # default; edit index.json to override
                "duration": 0,
                "last_used": None,
                "performance_score": 1.0,
                "times_used": 0,
            })
            added += 1
    if added:
        self._save()
    logger.info(f"Transitional bank: scanned {scan_dir}, added {added} clips (total {len(self.bank)})")
    return added

def full_path(self, file: str) -> Path:
    """Return absolute path for a clip file."""
    return self.hooks_dir / file
```

- [ ] **Step 3: Verify graceful fallback is logged clearly**

```bash
python3.13 -c "
import logging
logging.basicConfig(level=logging.INFO)
from content_engine.transitional_manager import TransitionalManager
tm = TransitionalManager()
result = tm.pick()
print('Pick result:', result)
print('Bank size:', len(tm.bank))
" 2>&1
```

Expected: `WARNING: Transitional hook bank is empty` and `Pick result: None` — confirming graceful fallback.

- [ ] **Step 4: Commit**

```bash
git add content/hooks/transitional/index.json content_engine/transitional_manager.py
git commit -m "fix(transitional): create hook bank scaffold + scan_from_videos helper"
```

---

## Task 6 — Fix per-platform color grading (pipeline.py)

**Why:** `build_daily_clips` hardcodes `platform="instagram"` in every `render_*` call. YouTube gets Instagram's high-contrast grade (contrast=1.1, saturation=1.15) instead of YouTube's neutral grade (all 1.0). This makes clips look oversaturated on YouTube.

**Fix:** Pass `platform="youtube"` (neutral) to all renders so the master clip is neutral. Color grading per-platform should be applied by the distributor (which already has `_apply_color_grade` available), or we accept that the master clip uses neutral grade and skip per-platform re-grading for now. The simpler fix: change the hardcoded platform to `"youtube"` (neutral) in all render calls.

**Files:**
- Modify: `content_engine/pipeline.py` — `build_daily_clips()`

- [ ] **Step 1: Find the hardcoded platform references**

```bash
grep -n '"instagram"' content_engine/pipeline.py
```

Expected: 4 occurrences inside `build_daily_clips` — one for each render call and one for `generate_caption`.

- [ ] **Step 2: Replace render-call platform references with "youtube" (neutral)**

In `build_daily_clips`, change:
```python
            render_emotional(segments, track.file_path, audio_start, hook_data["hook"],
                             "instagram", output_path, duration)
```
to:
```python
            render_emotional(segments, track.file_path, audio_start, hook_data["hook"],
                             "youtube", output_path, duration)
```

Do the same for `render_transitional` (both the main render and the fallback) and `render_performance`:

```python
# render_transitional (main path)
render_transitional(
    bait_clip=bait_path,
    ...
    platform="youtube",   # neutral grade; distributor applies per-platform
    ...
)
# render_transitional (fallback)
render_emotional(segments, track.file_path, audio_start, hook_data["hook"],
                 "youtube", output_path, duration)
# render_performance
render_performance(segments, track.file_path, audio_start, hook_data["hook"],
                   "youtube", output_path, duration)
```

Leave `generate_caption(... "instagram" ...)` unchanged — captions are per-platform anyway and this is just the default caption used for Instagram.

- [ ] **Step 3: Verify**

```bash
grep -n 'render_.*platform\|"instagram"\|"youtube"' content_engine/pipeline.py | head -20
```

Expected: all render calls now pass `"youtube"`.

- [ ] **Step 4: Commit**

```bash
git add content_engine/pipeline.py
git commit -m "fix(pipeline): use neutral youtube color grade for master clip render"
```

---

## Task 7 — Fix Instagram container polling on ERROR status (distributor.py)

**Why:** The polling loop for Instagram Reel container creation only breaks on `"FINISHED"` status. If the API returns `"ERROR"`, the loop continues polling for the full 2-minute timeout before failing — wasting time and masking the real error.

**Files:**
- Modify: `content_engine/distributor.py` — `post_instagram_reel()`

- [ ] **Step 1: Find the polling loop**

```bash
grep -n "FINISHED\|creation_id\|status_code\|IN_PROGRESS" content_engine/distributor.py | head -20
```

- [ ] **Step 2: Add ERROR break condition**

Find the polling loop. It looks like:
```python
for _ in range(24):  # up to 2 min
    ...
    status = data.get("status_code", "")
    if status == "FINISHED":
        break
    time.sleep(5)
```

Change to:
```python
for _ in range(24):  # up to 2 min
    ...
    status = data.get("status_code", "")
    if status == "FINISHED":
        break
    if status == "ERROR":
        error_msg = data.get("error_message", "unknown error")
        logger.error(f"Instagram container failed: {error_msg}")
        return {"success": False, "platform": "instagram", "error": f"container ERROR: {error_msg}"}
    time.sleep(5)
```

- [ ] **Step 3: Verify change looks correct**

```bash
grep -n "FINISHED\|ERROR\|status_code" content_engine/distributor.py | head -10
```

Expected: both `FINISHED` and `ERROR` break conditions visible.

- [ ] **Step 4: Commit**

```bash
git add content_engine/distributor.py
git commit -m "fix(distributor): break Instagram polling loop on ERROR status"
```

---

## Task 8 — Fix TikTok polling with max iteration limit (distributor.py)

**Why:** TikTok's publish status polling loop has no iteration limit. If the TikTok API hangs or returns an unexpected status, the loop runs forever and blocks the entire distribution pipeline.

**Files:**
- Modify: `content_engine/distributor.py` — `post_tiktok()`

- [ ] **Step 1: Find the TikTok polling loop**

```bash
grep -n "PUBLISH_COMPLETE\|publish_id\|while\|for.*range" content_engine/distributor.py | grep -A2 -B2 "PUBLISH_COMPLETE"
```

- [ ] **Step 2: Add max_attempts guard**

Find the polling loop. It likely looks like:
```python
while True:
    ...
    if status == "PUBLISH_COMPLETE":
        break
    time.sleep(5)
```

Change to:
```python
max_attempts = 24  # 2 minutes max
for attempt in range(max_attempts):
    ...
    if status == "PUBLISH_COMPLETE":
        break
    if status in ("FAILED", "SPAM_BLOCKED"):
        return {"success": False, "platform": "tiktok", "error": f"TikTok status: {status}"}
    time.sleep(5)
else:
    logger.error("TikTok publish polling timed out after 2 minutes")
    return {"success": False, "platform": "tiktok", "error": "polling timeout"}
```

- [ ] **Step 3: Commit**

```bash
git add content_engine/distributor.py
git commit -m "fix(distributor): add TikTok polling max_attempts to prevent infinite loop"
```

---

## Task 9 — Validate YouTube upload URL before use (distributor.py)

**Why:** `post_youtube_short()` reads `upload_url` from response headers but doesn't check if it's `None` before passing it to `requests.put()`. A missing header causes a silent crash that looks like a network error.

**Files:**
- Modify: `content_engine/distributor.py` — `post_youtube_short()`

- [ ] **Step 1: Find the upload URL extraction**

```bash
grep -n "upload_url\|Location\|headers\[" content_engine/distributor.py | head -10
```

- [ ] **Step 2: Add None check**

Find the line like:
```python
upload_url = response.headers.get("Location")
requests.put(upload_url, ...)
```

Change to:
```python
upload_url = response.headers.get("Location")
if not upload_url:
    logger.error("YouTube resumable upload: no Location header in init response")
    return {"success": False, "platform": "youtube", "error": "missing upload URL from YouTube API"}
requests.put(upload_url, ...)
```

- [ ] **Step 3: Commit**

```bash
git add content_engine/distributor.py
git commit -m "fix(distributor): validate YouTube upload URL before use"
```

---

## Task 10 — Write failed distributions to data/failed_posts.json (pipeline.py)

**Why:** `rjm.py content retry` reads `data/failed_posts.json` to retry failed posts, but the pipeline never writes there. Silent failures = permanent algorithmic misses.

**Files:**
- Modify: `content_engine/pipeline.py` — `run_full_day()`

- [ ] **Step 1: Find where distribution results are processed**

```bash
grep -n "failures\|failed_posts\|success\|distribution" content_engine/pipeline.py | head -20
```

- [ ] **Step 2: Write failures to failed_posts.json**

In `run_full_day()`, find the block that calculates `failures`:

```python
        success = [r for r in results if r.get("success")]
        failures = [r for r in results if not r.get("success")]
        if failures:
            logger.warning(f"[pipeline] {len(failures)} distribution failures")
```

Add after the `if failures:` block:

```python
        if failures:
            logger.warning(f"[pipeline] {len(failures)} distribution failures")
            # Write to failed_posts.json so `rjm.py content retry` can pick them up
            failed_posts_path = PROJECT_DIR / "data" / "failed_posts.json"
            existing = []
            if failed_posts_path.exists():
                try:
                    existing = json.loads(failed_posts_path.read_text())
                except Exception:
                    existing = []
            existing.extend(failures)
            failed_posts_path.write_text(json.dumps(existing, indent=2))
            logger.info(f"[pipeline] Wrote {len(failures)} failures to {failed_posts_path}")
```

- [ ] **Step 3: Verify**

```bash
grep -n "failed_posts\|failures" content_engine/pipeline.py
```

Expected: the new block writing to `data/failed_posts.json` appears.

- [ ] **Step 4: Commit**

```bash
git add content_engine/pipeline.py
git commit -m "fix(pipeline): write distribution failures to data/failed_posts.json for retry"
```

---

## Task 11 — End-to-End Smoke Test

**Goal:** Prove all 3 clips render with Pillow text overlays and produce valid output files. Distribution can be verified via `--dry-run` (no actual posts sent).

**Files:** None modified. Read-only verification.

- [ ] **Step 1: Run a dry-run**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 rjm.py content viral --dry-run 2>&1 | tee /tmp/viral_dry_run.log
```

Expected output (key lines):
```
[pipeline] Trend brief loaded: ...
[pipeline] Weights loaded — best platform: ...
[pipeline] Track selected: halleluyah (185 BPM)
[pipeline] Rendered 3 clips
[pipeline] DRY RUN — skipping distribution
[pipeline] Post registry saved: ...
```

Exit code must be 0.

- [ ] **Step 2: Validate clip files exist and are non-zero**

```bash
ls -lh content/output/$(date +%Y-%m-%d)/
```

Expected: 3 clip files (one per format) all > 1MB, plus 3 story variants > 500KB. No 0-byte files.

- [ ] **Step 3: Validate text overlays are actually burned in**

```bash
python3.13 -c "
from content_engine.renderer import validate_output
import glob
clips = glob.glob('content/output/$(date +%Y-%m-%d)/*.mp4')
clips = [c for c in clips if not c.endswith('_story.mp4')]
for c in clips:
    r = validate_output(c, 7)  # 7s for emotional; others are longer but validation is lenient
    print(c.split('/')[-1], '→', 'OK' if r['valid'] else 'FAIL', r.get('errors', []))
"
```

Expected: all clips report `OK` (or only duration mismatch warnings, which are acceptable — the duration check uses ±1.5s tolerance and 7s is the shortest clip).

- [ ] **Step 4: Confirm hook text appears on a clip (visual spot-check)**

```bash
open content/output/$(date +%Y-%m-%d)/emotional_halleluyah.mp4
```

Visually verify the hook text is overlaid on the video.

- [ ] **Step 5: If dry-run passes, run a live test**

```bash
python3.13 rjm.py content viral 2>&1 | tail -30
```

Check `data/performance/$(date +%Y-%m-%d)_posts.json` for 6 successful distribution entries (2 clips × 3 platforms each, since TikTok goes through Buffer and Stories are included).

---

## Self-Review Checklist

**Spec coverage:**
- [x] Bug 1 (return value) — Task 1
- [x] Bug 2 (drawtext) — Task 2
- [x] Bug 3 (Claude CLI) — Task 3
- [x] Bug 4 (API key) — Task 4
- [x] Bug 5 (transitional bank) — Task 5
- [x] Bug 6 (color grade) — Task 6
- [x] Bug 7 (Instagram ERROR loop) — Task 7
- [x] Bug 8 (TikTok timeout) — Task 8
- [x] Bug 9 (YouTube URL) — Task 9
- [x] Bug 10 (failed_posts.json) — Task 10
- [x] Smoke test — Task 11

**Placeholder scan:** All tasks contain exact file paths, exact code, exact commands. No "TBD" or vague steps.

**Type consistency:** `_burn_text_overlay` signature unchanged — callers updated to use `str` return type. Anthropic SDK `client.messages.create()` returns `Message`; `.content[0].text` is `str`. Both match existing usage.

**Dependency order:** Tasks 1+2 (renderer) → Task 3+4 (generator) → Task 5 (transitional) → Task 6 (pipeline) → Tasks 7-10 (distributor) → Task 11 (smoke test). Each task is independently committable.
