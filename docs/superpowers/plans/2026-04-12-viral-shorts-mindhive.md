# Viral Shorts Mindhive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully autonomous 5-module video pipeline that posts 9 clips/day across TikTok/IG/YouTube, learns from performance data nightly, and compounds toward 500K+ views per video.

**Architecture:** Visual-first pipeline — opening frame selected first (AI-generated or footage-scored), audio matched to it, rendered platform-specifically, distributed via native APIs, performance fed back as prompt weights nightly.

**Tech Stack:** Python 3.11+, ffmpeg, librosa, Pillow, requests, Playwright (scraping), Runway ML API, Instagram Graph API, TikTok Content Posting API v2, YouTube Data API v3, Claude CLI (Max plan), pytest

---

## File Map

**New files:**
- `content_engine/__init__.py`
- `content_engine/types.py` — shared dataclasses
- `content_engine/trend_scanner.py` — Module 1
- `content_engine/footage_scorer.py` — Module 2a
- `content_engine/visual_engine.py` — Module 2b (Runway ML)
- `content_engine/assembler.py` — Module 3 (visual-first pipeline)
- `content_engine/distributor.py` — Module 4 (native APIs + Buffer fallback)
- `content_engine/learning_loop.py` — Module 5
- `prompt_weights.json` — live weights, updated nightly
- `tests/test_trend_scanner.py`
- `tests/test_footage_scorer.py`
- `tests/test_visual_engine.py`
- `tests/test_assembler.py`
- `tests/test_distributor.py`
- `tests/test_learning_loop.py`
- `agents/holy-rave-trend-scanner.md`
- `agents/holy-rave-visual-engine.md`
- `agents/holy-rave-learning-loop.md`

**Modified files:**
- `outreach_agent/post_today.py` — add `--engine viral` flag
- `agents/holy-rave-daily-run.md` — updated orchestration steps
- `rjm.py` — add `content viral` subcommand

**Unchanged:** `outreach_agent/generator.py`, `outreach_agent/processor.py`, `outreach_agent/buffer_poster.py`

---

## Task 1: Foundation — package, types, initial weights

**Files:**
- Create: `content_engine/__init__.py`
- Create: `content_engine/types.py`
- Create: `prompt_weights.json`
- Create: `data/trend_brief/.gitkeep`, `data/opening_frames/.gitkeep`, `data/performance/.gitkeep`, `learning/.gitkeep`
- Test: `tests/test_types.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_types.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from content_engine.types import TrendBrief, OpeningFrame, PerformanceRecord, PromptWeights

def test_trend_brief_defaults():
    b = TrendBrief(date="2026-04-12", top_visual_formats=[], dominant_emotion="euphoric",
                   oversaturated="lo-fi", hook_pattern_of_day="open with question",
                   contrarian_gap="silence as tension", trend_confidence=0.8)
    assert b.date == "2026-04-12"
    assert b.trend_confidence == 0.8

def test_opening_frame_source_types():
    f = OpeningFrame(clip_index=0, source="ai_generated", source_file="out.mp4",
                     emotion_tag="euphoric", visual_category="sacred_geometry", footage_score=9.0)
    assert f.source in ("ai_generated", "footage")

def test_prompt_weights_load_defaults():
    w = PromptWeights.defaults()
    assert w.hook_weights["tension"] > 0
    assert w.visual_weights["ai_generated"] > 0
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/optimistic-perlman"
python -m pytest tests/test_types.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'content_engine'`

- [ ] **Step 3: Create package and types**

```python
# content_engine/__init__.py
"""Viral Shorts Mindhive — autonomous video pipeline."""
```

```python
# content_engine/types.py
from dataclasses import dataclass, field
from typing import Literal
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent

@dataclass
class TrendBrief:
    date: str
    top_visual_formats: list[str]
    dominant_emotion: str
    oversaturated: str
    hook_pattern_of_day: str
    contrarian_gap: str
    trend_confidence: float

    def save(self):
        out = PROJECT_DIR / "data" / "trend_brief" / f"{self.date}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.__dict__, indent=2))

    @classmethod
    def load(cls, date: str) -> "TrendBrief":
        path = PROJECT_DIR / "data" / "trend_brief" / f"{date}.json"
        return cls(**json.loads(path.read_text()))

    @classmethod
    def load_today(cls) -> "TrendBrief":
        from datetime import date
        return cls.load(date.today().isoformat())


@dataclass
class OpeningFrame:
    clip_index: int
    source: Literal["ai_generated", "footage"]
    source_file: str
    emotion_tag: str
    visual_category: str
    footage_score: float


@dataclass
class PerformanceRecord:
    post_id: str
    platform: str          # tiktok | instagram | youtube
    clip_index: int
    variant: str           # a | b
    hook_mechanism: str    # tension | identity | scene | claim | rupture
    visual_type: str       # ai_generated | performance | b_roll | phone
    clip_length: int       # 5 | 9 | 15
    views: int = 0
    completion_rate: float = 0.0
    scroll_stop_rate: float = 0.0
    share_rate: float = 0.0
    save_rate: float = 0.0
    recorded_at: str = ""


@dataclass
class PromptWeights:
    hook_weights: dict[str, float]
    visual_weights: dict[str, float]
    best_clip_length: int
    best_platform: str
    updated: str

    @classmethod
    def defaults(cls) -> "PromptWeights":
        return cls(
            hook_weights={"tension": 1.0, "identity": 1.0, "scene": 1.0, "claim": 1.0, "rupture": 1.0},
            visual_weights={"ai_generated": 1.0, "performance": 1.0, "b_roll": 1.0, "phone": 1.0},
            best_clip_length=15,
            best_platform="instagram",
            updated="",
        )

    def save(self):
        path = PROJECT_DIR / "prompt_weights.json"
        path.write_text(json.dumps(self.__dict__, indent=2))

    @classmethod
    def load(cls) -> "PromptWeights":
        path = PROJECT_DIR / "prompt_weights.json"
        if not path.exists():
            w = cls.defaults()
            w.save()
            return w
        return cls(**json.loads(path.read_text()))
```

- [ ] **Step 4: Create prompt_weights.json seed and data dirs**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/optimistic-perlman"
python3 -c "
import sys; sys.path.insert(0, '.')
from content_engine.types import PromptWeights
PromptWeights.defaults().save()
print('prompt_weights.json written')
"
mkdir -p data/trend_brief data/opening_frames data/performance learning/breakthroughs
touch data/trend_brief/.gitkeep data/opening_frames/.gitkeep data/performance/.gitkeep learning/.gitkeep
```

- [ ] **Step 5: Run tests to verify pass**

```bash
python -m pytest tests/test_types.py -v
```
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add content_engine/ prompt_weights.json data/ learning/ tests/test_types.py
git commit -m "feat: content_engine foundation — types, weights, data dirs"
```

---

## Task 2: Trend Scanner

**Files:**
- Create: `content_engine/trend_scanner.py`
- Test: `tests/test_trend_scanner.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_trend_scanner.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from content_engine.trend_scanner import (
    scrape_youtube_trending,
    scrape_spotify_featured,
    synthesize_brief,
    run,
)
from content_engine.types import TrendBrief

def test_scrape_youtube_trending_returns_list():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"items": [
        {"snippet": {"title": "Tribal Techno Mix 2026", "categoryId": "10"}},
        {"snippet": {"title": "Melodic Psytrance Set", "categoryId": "10"}},
    ]}
    with patch("content_engine.trend_scanner.requests.get", return_value=mock_resp):
        results = scrape_youtube_trending("YOUR_KEY")
    assert isinstance(results, list)
    assert len(results) >= 1

def test_scrape_spotify_featured_returns_list():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"playlists": {"items": [
        {"name": "Tribal Heat", "description": "Deep rhythmic energy"},
        {"name": "Morning Meditation", "description": "Calm focus"},
    ]}}
    with patch("content_engine.trend_scanner.requests.get", return_value=mock_resp):
        results = scrape_spotify_featured("Bearer token")
    assert isinstance(results, list)

def test_synthesize_brief_returns_trend_brief():
    youtube_data = ["Tribal Techno Mix", "Psytrance Journey"]
    spotify_data = ["Tribal Heat playlist", "Deep Focus"]
    with patch("content_engine.trend_scanner._call_claude") as mock_claude:
        mock_claude.return_value = json.dumps({
            "top_visual_formats": ["crowd ecstasy", "sacred geometry", "desert rave"],
            "dominant_emotion": "euphoric release",
            "oversaturated": "lo-fi chill",
            "hook_pattern_of_day": "open with contrast",
            "contrarian_gap": "silence before the drop",
            "trend_confidence": 0.78,
        })
        brief = synthesize_brief("2026-04-12", youtube_data, spotify_data)
    assert isinstance(brief, TrendBrief)
    assert brief.dominant_emotion == "euphoric release"
    assert 0 < brief.trend_confidence <= 1.0

def test_run_saves_brief_json(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test_key")
    monkeypatch.setenv("SPOTIFY_ACCESS_TOKEN", "test_token")
    with patch("content_engine.trend_scanner.scrape_youtube_trending", return_value=["techno mix"]), \
         patch("content_engine.trend_scanner.scrape_spotify_featured", return_value=["tribal heat"]), \
         patch("content_engine.trend_scanner.synthesize_brief") as mock_synth, \
         patch("content_engine.trend_scanner.PROJECT_DIR", tmp_path):
        mock_synth.return_value = TrendBrief(
            date="2026-04-12", top_visual_formats=["x"], dominant_emotion="euphoric",
            oversaturated="lo-fi", hook_pattern_of_day="contrast", contrarian_gap="silence",
            trend_confidence=0.8,
        )
        result = run(date_str="2026-04-12")
    assert isinstance(result, TrendBrief)
```

- [ ] **Step 2: Run to verify fail**

```bash
python -m pytest tests/test_trend_scanner.py -v 2>&1 | head -10
```
Expected: `ImportError`

- [ ] **Step 3: Implement trend_scanner.py**

```python
# content_engine/trend_scanner.py
"""
Module 1: Trend Scanner
Runs daily at 06:00 CET. Scrapes YouTube trending + Spotify featured playlists,
synthesizes a Today's Brief via Claude CLI, saves to data/trend_brief/YYYY-MM-DD.json.
"""
import json
import logging
import os
import subprocess
import sys
from datetime import date as _date
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).parent.parent
logger = logging.getLogger(__name__)

NICHE_KEYWORDS = ["techno", "psytrance", "melodic techno", "tribal techno", "spiritual music"]

YOUTUBE_TRENDING_URL = "https://www.googleapis.com/youtube/v3/videos"
SPOTIFY_FEATURED_URL = "https://api.spotify.com/v1/browse/featured-playlists"


def _call_claude(prompt: str, temperature: float = 0.3) -> str:
    """Call Claude CLI. Returns stdout text."""
    claude = os.environ.get("CLAUDE_CLI_PATH", "") or _find_claude()
    cmd = [claude, "--print", "--model", "claude-haiku-4-5-20251001", prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error: {result.stderr[:200]}")
    return result.stdout.strip()


def _find_claude() -> str:
    candidates = [
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
        os.path.expanduser("~/.local/bin/claude"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return "claude"


def scrape_youtube_trending(api_key: str) -> list[str]:
    """Return list of trending music video titles from YouTube."""
    if not api_key:
        return []
    try:
        resp = requests.get(YOUTUBE_TRENDING_URL, params={
            "part": "snippet",
            "chart": "mostPopular",
            "videoCategoryId": "10",  # Music
            "regionCode": "US",
            "maxResults": 20,
            "key": api_key,
        }, timeout=15)
        if resp.status_code != 200:
            return []
        items = resp.json().get("items", [])
        return [i["snippet"]["title"] for i in items]
    except Exception as e:
        logger.warning(f"YouTube trending scrape failed: {e}")
        return []


def scrape_spotify_featured(access_token: str) -> list[str]:
    """Return featured playlist names + descriptions from Spotify."""
    if not access_token:
        return []
    try:
        resp = requests.get(SPOTIFY_FEATURED_URL, headers={
            "Authorization": access_token if access_token.startswith("Bearer") else f"Bearer {access_token}",
        }, params={"limit": 20}, timeout=15)
        if resp.status_code != 200:
            return []
        playlists = resp.json().get("playlists", {}).get("items", [])
        return [f"{p['name']}: {p.get('description', '')}" for p in playlists]
    except Exception as e:
        logger.warning(f"Spotify featured scrape failed: {e}")
        return []


def synthesize_brief(date_str: str, youtube_data: list[str], spotify_data: list[str]) -> "TrendBrief":
    """Use Claude CLI to synthesize a TrendBrief from scraped data."""
    from content_engine.types import TrendBrief

    prompt = f"""You are analyzing social media trends for a Melodic Techno / Tribal Psytrance artist (RJM / Holy Rave).

Today's date: {date_str}

YouTube trending music titles (top 20):
{json.dumps(youtube_data, indent=2)}

Spotify featured playlists:
{json.dumps(spotify_data, indent=2)}

Based on these signals, output a JSON object with EXACTLY these keys:
- top_visual_formats: list of 3 short strings (e.g. "crowd ecstasy shot", "aerial landscape", "sacred geometry animation")
- dominant_emotion: one string (e.g. "euphoric release", "deep introspection", "primal energy")
- oversaturated: one string describing what to AVOID (what everyone is doing)
- hook_pattern_of_day: one string describing the caption/text pattern gaining traction
- contrarian_gap: one string describing what NOBODY in the techno/spiritual niche is doing = opportunity
- trend_confidence: float 0-1 (how confident you are in these signals)

Output ONLY the JSON object, no commentary."""

    raw = _call_claude(prompt, temperature=0.3)

    # Extract JSON from response
    start = raw.find("{")
    end = raw.rfind("}") + 1
    data = json.loads(raw[start:end])

    return TrendBrief(
        date=date_str,
        top_visual_formats=data["top_visual_formats"],
        dominant_emotion=data["dominant_emotion"],
        oversaturated=data["oversaturated"],
        hook_pattern_of_day=data["hook_pattern_of_day"],
        contrarian_gap=data["contrarian_gap"],
        trend_confidence=float(data["trend_confidence"]),
    )


def run(date_str: str = None) -> "TrendBrief":
    """Full trend scanner run. Returns and saves TrendBrief."""
    from content_engine.types import TrendBrief

    if date_str is None:
        date_str = _date.today().isoformat()

    youtube_key = os.environ.get("YOUTUBE_API_KEY", "")
    spotify_token = os.environ.get("SPOTIFY_ACCESS_TOKEN", "")

    logger.info(f"[trend_scanner] Scraping trends for {date_str}")
    youtube_data = scrape_youtube_trending(youtube_key)
    spotify_data = scrape_spotify_featured(spotify_token)

    if not youtube_data and not spotify_data:
        logger.warning("[trend_scanner] No external data — using Claude general knowledge")

    brief = synthesize_brief(date_str, youtube_data, spotify_data)
    brief.save()
    logger.info(f"[trend_scanner] Brief saved: emotion={brief.dominant_emotion}, confidence={brief.trend_confidence}")
    return brief


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    b = run()
    print(json.dumps(b.__dict__, indent=2))
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_trend_scanner.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add content_engine/trend_scanner.py tests/test_trend_scanner.py
git commit -m "feat: trend_scanner — YouTube + Spotify signals → Today's Brief via Claude CLI"
```

---

## Task 3: Footage Scorer

**Files:**
- Create: `content_engine/footage_scorer.py`
- Test: `tests/test_footage_scorer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_footage_scorer.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from content_engine.footage_scorer import score_clip, pick_best_opening_frame, SCORE_THRESHOLD

def test_score_clip_returns_float():
    with patch("content_engine.footage_scorer._get_motion_score", return_value=7.0), \
         patch("content_engine.footage_scorer._get_contrast_score", return_value=8.0), \
         patch("content_engine.footage_scorer._get_freshness_score", return_value=9.0), \
         patch("content_engine.footage_scorer._get_emotion_match", return_value=6.0):
        score = score_clip("fake.mp4", dominant_emotion="euphoric", last_used_days=5)
    assert isinstance(score, float)
    assert 0 <= score <= 10

def test_score_clip_penalises_recent_use():
    with patch("content_engine.footage_scorer._get_motion_score", return_value=8.0), \
         patch("content_engine.footage_scorer._get_contrast_score", return_value=8.0), \
         patch("content_engine.footage_scorer._get_freshness_score") as mock_fresh, \
         patch("content_engine.footage_scorer._get_emotion_match", return_value=8.0):
        mock_fresh.return_value = 2.0  # used yesterday
        score_recent = score_clip("fake.mp4", "euphoric", last_used_days=1)
        mock_fresh.return_value = 10.0  # not used in 30 days
        score_old = score_clip("fake.mp4", "euphoric", last_used_days=30)
    assert score_old > score_recent

def test_pick_best_returns_highest_scorer():
    clips = [
        {"path": "a.mp4", "last_used_days": 30, "category": "performance"},
        {"path": "b.mp4", "last_used_days": 1,  "category": "b_roll"},
        {"path": "c.mp4", "last_used_days": 20, "category": "phone"},
    ]
    scores = {"a.mp4": 8.5, "b.mp4": 3.0, "c.mp4": 6.0}
    with patch("content_engine.footage_scorer.score_clip", side_effect=lambda p, e, **kw: scores[p]):
        best_path, best_score = pick_best_opening_frame(clips, dominant_emotion="euphoric")
    assert best_path == "a.mp4"
    assert best_score == 8.5

def test_score_threshold_exists():
    assert isinstance(SCORE_THRESHOLD, float)
    assert SCORE_THRESHOLD > 0
```

- [ ] **Step 2: Run to verify fail**

```bash
python -m pytest tests/test_footage_scorer.py -v 2>&1 | head -10
```
Expected: `ImportError`

- [ ] **Step 3: Implement footage_scorer.py**

```python
# content_engine/footage_scorer.py
"""
Module 2a: Footage Scorer
Replaces simple timestamp rotation with semantic scoring.
Scores existing video library against today's TrendBrief.
Returns best opening frame candidate or signals AI generation needed.
"""
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

SCORE_THRESHOLD = 7.0   # Score below this → trigger AI generation
PROJECT_DIR = Path(__file__).parent.parent
VIDEO_ROTATION_FILE = PROJECT_DIR / "video_rotation.json"

# Weights for composite score
W_MOTION    = 0.30
W_CONTRAST  = 0.25
W_FRESHNESS = 0.25
W_EMOTION   = 0.20


def _get_motion_score(video_path: str) -> float:
    """Score 0-10: velocity of movement in first 3 seconds via ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "frame=pkt_pts_time,pict_type",
            "-read_intervals", "%+3",
            "-of", "json",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return 5.0
        data = json.loads(result.stdout)
        frames = data.get("frames", [])
        # Count non-I frames (P/B frames = motion)
        motion_frames = sum(1 for f in frames if f.get("pict_type") in ("P", "B"))
        total = len(frames) or 1
        ratio = motion_frames / total
        return min(10.0, ratio * 12)  # scale: 0.83 ratio → 10
    except Exception:
        return 5.0


def _get_contrast_score(video_path: str) -> float:
    """Score 0-10: visual contrast of first frame."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        subprocess.run([
            "ffmpeg", "-y", "-ss", "0", "-i", video_path,
            "-vframes", "1", "-q:v", "2", tmp,
        ], capture_output=True, timeout=15)
        from PIL import Image, ImageStat
        img = Image.open(tmp).convert("L")
        stat = ImageStat.Stat(img)
        stddev = stat.stddev[0]  # 0-127 range
        os.unlink(tmp)
        return min(10.0, (stddev / 60) * 10)
    except Exception:
        return 5.0


def _get_freshness_score(last_used_days: int) -> float:
    """Score 0-10: higher = not used recently. 14+ days = full score."""
    if last_used_days >= 14:
        return 10.0
    return (last_used_days / 14) * 10.0


def _get_emotion_match(video_path: str, dominant_emotion: str) -> float:
    """Score 0-10: Claude CLI scoring of visual match to today's dominant emotion."""
    try:
        claude = os.environ.get("CLAUDE_CLI_PATH", "") or "claude"
        prompt = (
            f"Video file: {Path(video_path).name}\n"
            f"Target emotion: {dominant_emotion}\n"
            "Rate 0-10 how likely this video's VISUAL content (based on filename/category) "
            "evokes the target emotion. Reply with ONLY a single number."
        )
        result = subprocess.run(
            [claude, "--print", "--model", "claude-haiku-4-5-20251001", prompt],
            capture_output=True, text=True, timeout=30,
        )
        return min(10.0, float(result.stdout.strip()))
    except Exception:
        return 5.0


def score_clip(video_path: str, dominant_emotion: str, last_used_days: int = 7) -> float:
    """Composite score 0-10 for a video clip as an opening frame candidate."""
    motion    = _get_motion_score(video_path)
    contrast  = _get_contrast_score(video_path)
    freshness = _get_freshness_score(last_used_days)
    emotion   = _get_emotion_match(video_path, dominant_emotion)

    return (
        motion    * W_MOTION +
        contrast  * W_CONTRAST +
        freshness * W_FRESHNESS +
        emotion   * W_EMOTION
    )


def _load_rotation() -> dict:
    if VIDEO_ROTATION_FILE.exists():
        return json.loads(VIDEO_ROTATION_FILE.read_text())
    return {}


def build_candidate_list(video_dirs: list[str]) -> list[dict]:
    """Scan video dirs, return list of {path, last_used_days, category}."""
    from datetime import datetime
    rotation = _load_rotation()
    candidates = []
    exts = {".mp4", ".mov", ".m4v", ".avi"}

    for vdir in video_dirs:
        p = Path(vdir)
        if not p.exists():
            continue
        category = p.name  # 'performances', 'b-roll', 'phone-footage'
        for f in p.rglob("*"):
            if f.suffix.lower() in exts:
                last_used_ts = rotation.get(str(f), 0)
                if last_used_ts:
                    days = (datetime.now().timestamp() - last_used_ts) / 86400
                else:
                    days = 30  # never used
                candidates.append({
                    "path": str(f),
                    "last_used_days": days,
                    "category": category,
                })
    return candidates


def pick_best_opening_frame(candidates: list[dict], dominant_emotion: str) -> tuple[str, float]:
    """Score all candidates, return (best_path, best_score)."""
    if not candidates:
        return ("", 0.0)

    best_path  = ""
    best_score = -1.0

    for c in candidates:
        s = score_clip(c["path"], dominant_emotion, last_used_days=int(c.get("last_used_days", 7)))
        logger.debug(f"  {Path(c['path']).name}: {s:.2f}")
        if s > best_score:
            best_score = s
            best_path  = c["path"]

    return (best_path, best_score)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_footage_scorer.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add content_engine/footage_scorer.py tests/test_footage_scorer.py
git commit -m "feat: footage_scorer — semantic scoring replaces timestamp rotation"
```

---

## Task 4: Visual Engine (Runway ML)

**Files:**
- Create: `content_engine/visual_engine.py`
- Test: `tests/test_visual_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_visual_engine.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from content_engine.visual_engine import build_prompt, generate_clip, pick_opening_frame
from content_engine.types import TrendBrief, OpeningFrame

BRIEF = TrendBrief(
    date="2026-04-12",
    top_visual_formats=["crowd ecstasy", "sacred geometry", "aerial rave"],
    dominant_emotion="euphoric release",
    oversaturated="lo-fi chill",
    hook_pattern_of_day="contrast then drop",
    contrarian_gap="silence before the beat",
    trend_confidence=0.8,
)

def test_build_prompt_contains_emotion():
    prompt = build_prompt(BRIEF, clip_index=0)
    assert "euphoric" in prompt.lower()

def test_build_prompt_avoids_oversaturated():
    prompt = build_prompt(BRIEF, clip_index=0)
    # Should not repeat the oversaturated format verbatim as a positive directive
    assert "lo-fi chill" not in prompt.lower() or "avoid" in prompt.lower()

def test_generate_clip_returns_path_on_success(tmp_path):
    mock_init = MagicMock()
    mock_init.status_code = 200
    mock_init.json.return_value = {"id": "task_abc123"}

    mock_poll = MagicMock()
    mock_poll.status_code = 200
    mock_poll.json.return_value = {"status": "SUCCEEDED", "output": ["https://example.com/video.mp4"]}

    mock_video = MagicMock()
    mock_video.status_code = 200
    mock_video.iter_content = MagicMock(return_value=[b"fake_video_data"])

    with patch("content_engine.visual_engine.requests.post", return_value=mock_init), \
         patch("content_engine.visual_engine.requests.get", side_effect=[mock_poll, mock_video]), \
         patch("content_engine.visual_engine.OUTPUT_DIR", tmp_path):
        path = generate_clip("euphoric sacred geometry dissolving into dancers", "2026-04-12", 0)
    assert path.endswith(".mp4")

def test_pick_opening_frame_uses_footage_when_score_high(tmp_path):
    with patch("content_engine.visual_engine.footage_scorer.build_candidate_list", return_value=[{"path": "good.mp4", "last_used_days": 20, "category": "performance"}]), \
         patch("content_engine.visual_engine.footage_scorer.pick_best_opening_frame", return_value=("good.mp4", 8.5)):
        frame = pick_opening_frame(BRIEF, clip_index=0, video_dirs=[str(tmp_path)])
    assert frame.source == "footage"
    assert frame.source_file == "good.mp4"

def test_pick_opening_frame_generates_when_score_low(tmp_path):
    with patch("content_engine.visual_engine.footage_scorer.build_candidate_list", return_value=[{"path": "bad.mp4", "last_used_days": 1, "category": "b_roll"}]), \
         patch("content_engine.visual_engine.footage_scorer.pick_best_opening_frame", return_value=("bad.mp4", 3.0)), \
         patch("content_engine.visual_engine.generate_clip", return_value=str(tmp_path / "gen.mp4")) as mock_gen:
        frame = pick_opening_frame(BRIEF, clip_index=0, video_dirs=[str(tmp_path)])
    assert frame.source == "ai_generated"
    mock_gen.assert_called_once()
```

- [ ] **Step 2: Verify fail**

```bash
python -m pytest tests/test_visual_engine.py -v 2>&1 | head -10
```

- [ ] **Step 3: Implement visual_engine.py**

```python
# content_engine/visual_engine.py
"""
Module 2b: Visual Engine
Generates AI video clips via Runway ML Gen-4 Turbo when footage score < threshold.
Decision: score_clip() >= SCORE_THRESHOLD → use footage; else → generate.
"""
import logging
import os
import time
from pathlib import Path

import requests

from content_engine import footage_scorer
from content_engine.types import TrendBrief, OpeningFrame
from content_engine.footage_scorer import SCORE_THRESHOLD

logger = logging.getLogger(__name__)

RUNWAY_API_BASE = "https://api.runwayml.com/v1"
RUNWAY_VERSION  = "2024-11-06"
PROJECT_DIR     = Path(__file__).parent.parent
OUTPUT_DIR      = str(PROJECT_DIR / "data" / "opening_frames")

# Brand formula injected into every prompt
BRAND_FORMULA = (
    "ancient visual language fused with electronic energy. "
    "Sacred geometry, tribal ritual, dark atmospheric lighting. "
    "High contrast. Cinematic. No text overlays."
)

CLIP_CONCEPTS = [
    "crowd of dancers in ecstatic movement, aerial perspective, bass-wave ripple effect",
    "sacred geometry patterns dissolving into a rave floor, particle trails, slow motion",
    "aerial coastline at golden hour, light trails from synthesizer pulse, fog rising",
]


def build_prompt(brief: TrendBrief, clip_index: int) -> str:
    """Build a Runway text-to-video prompt from today's brief."""
    concept = CLIP_CONCEPTS[clip_index % len(CLIP_CONCEPTS)]
    visual_format = brief.top_visual_formats[clip_index % len(brief.top_visual_formats)]
    return (
        f"{brief.dominant_emotion} atmosphere. "
        f"{visual_format}. "
        f"{concept}. "
        f"{BRAND_FORMULA} "
        f"Style: {brief.hook_pattern_of_day}. "
        f"Avoid: {brief.oversaturated}."
    )


def generate_clip(prompt_text: str, date_str: str, clip_index: int) -> str:
    """
    Submit text-to-video job to Runway ML, poll until complete, download clip.
    Returns local file path.
    Raises RuntimeError if RUNWAY_API_KEY not set or generation fails.
    """
    api_key = os.environ.get("RUNWAY_API_KEY", "")
    if not api_key:
        raise RuntimeError("RUNWAY_API_KEY not set — cannot generate AI clip")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Runway-Version": RUNWAY_VERSION,
        "Content-Type": "application/json",
    }

    # Submit job
    resp = requests.post(f"{RUNWAY_API_BASE}/text_to_video", headers=headers, json={
        "model": "gen4_turbo",
        "promptText": prompt_text,
        "ratio": "768:1344",  # 9:16 vertical
        "duration": 5,
    }, timeout=30)

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Runway submission failed {resp.status_code}: {resp.text[:200]}")

    task_id = resp.json()["id"]
    logger.info(f"[visual_engine] Runway task {task_id} submitted")

    # Poll until complete (max 5 minutes)
    for attempt in range(60):
        time.sleep(5)
        poll = requests.get(f"{RUNWAY_API_BASE}/tasks/{task_id}", headers=headers, timeout=15)
        status = poll.json().get("status", "")
        if status == "SUCCEEDED":
            video_url = poll.json()["output"][0]
            break
        if status == "FAILED":
            raise RuntimeError(f"Runway generation failed: {poll.json()}")
        logger.debug(f"[visual_engine] Task {task_id}: {status} (attempt {attempt+1})")
    else:
        raise RuntimeError(f"Runway task {task_id} timed out after 5 minutes")

    # Download clip
    out_dir = Path(OUTPUT_DIR) / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / f"ai_clip_{clip_index}.mp4")

    video_resp = requests.get(video_url, stream=True, timeout=60)
    with open(out_path, "wb") as f:
        for chunk in video_resp.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info(f"[visual_engine] AI clip saved: {out_path}")
    return out_path


def pick_opening_frame(brief: TrendBrief, clip_index: int, video_dirs: list[str]) -> OpeningFrame:
    """
    Decide: use existing footage (if score >= threshold) OR generate with Runway.
    Returns OpeningFrame with source_file set.
    """
    candidates = footage_scorer.build_candidate_list(video_dirs)
    best_path, best_score = footage_scorer.pick_best_opening_frame(candidates, brief.dominant_emotion)

    logger.info(f"[visual_engine] clip {clip_index}: best footage score={best_score:.2f} (threshold={SCORE_THRESHOLD})")

    if best_score >= SCORE_THRESHOLD and best_path:
        return OpeningFrame(
            clip_index=clip_index,
            source="footage",
            source_file=best_path,
            emotion_tag=brief.dominant_emotion,
            visual_category=_infer_category(best_path),
            footage_score=best_score,
        )

    # Generate with Runway
    prompt = build_prompt(brief, clip_index)
    generated_path = generate_clip(prompt, brief.date, clip_index)
    return OpeningFrame(
        clip_index=clip_index,
        source="ai_generated",
        source_file=generated_path,
        emotion_tag=brief.dominant_emotion,
        visual_category="ai_generated",
        footage_score=0.0,
    )


def _infer_category(path: str) -> str:
    p = path.lower()
    if "performance" in p:   return "performance"
    if "b-roll" in p:        return "b_roll"
    if "phone" in p:         return "phone"
    return "b_roll"
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_visual_engine.py -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add content_engine/visual_engine.py tests/test_visual_engine.py
git commit -m "feat: visual_engine — Runway ML generation with footage fallback scoring"
```

---

## Task 5: Assembler (visual-first pipeline)

**Files:**
- Create: `content_engine/assembler.py`
- Test: `tests/test_assembler.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_assembler.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock, call
from pathlib import Path
from content_engine.assembler import build_clip, run_assembly
from content_engine.types import TrendBrief, OpeningFrame, PromptWeights

BRIEF = TrendBrief(
    date="2026-04-12",
    top_visual_formats=["crowd ecstasy"],
    dominant_emotion="euphoric release",
    oversaturated="lo-fi chill",
    hook_pattern_of_day="contrast",
    contrarian_gap="silence",
    trend_confidence=0.8,
)

FRAME = OpeningFrame(
    clip_index=0, source="footage", source_file="perf.mp4",
    emotion_tag="euphoric", visual_category="performance", footage_score=8.5,
)

WEIGHTS = PromptWeights.defaults()

def test_build_clip_calls_ffmpeg(tmp_path):
    with patch("content_engine.assembler.subprocess.run") as mock_run, \
         patch("content_engine.assembler._find_best_audio_segment", return_value=30.0), \
         patch("content_engine.assembler._render_platform_clip", return_value=str(tmp_path / "out.mp4")):
        mock_run.return_value = MagicMock(returncode=0)
        result = build_clip(
            opening_frame=FRAME,
            audio_path="track.wav",
            track_title="Jericho",
            clip_length=15,
            platform="instagram",
            variant="a",
            hook_text="The drop nobody saw coming",
            output_dir=str(tmp_path),
        )
    assert result.endswith(".mp4")

def test_run_assembly_produces_nine_clips(tmp_path):
    mock_frames = [FRAME, FRAME, FRAME]
    with patch("content_engine.assembler.visual_engine.pick_opening_frame", side_effect=mock_frames), \
         patch("content_engine.assembler.build_clip", return_value=str(tmp_path / "clip.mp4")), \
         patch("content_engine.assembler._pick_audio", return_value=("track.wav", "Jericho")), \
         patch("content_engine.assembler._generate_hooks", return_value=_mock_hooks()), \
         patch("content_engine.assembler._generate_captions", return_value=_mock_captions()):
        clips = run_assembly(brief=BRIEF, weights=WEIGHTS, video_dirs=[str(tmp_path)], output_dir=str(tmp_path))
    # 3 clips × 3 platforms = 9
    assert len(clips) == 9

def test_variant_assignment_alternates():
    """Clip 0 → TikTok=A, Instagram=B, YouTube=A; Clip 1 → TikTok=B, Instagram=A, YouTube=B"""
    from content_engine.assembler import VARIANT_MAP
    assert VARIANT_MAP[0]["tiktok"] == "a"
    assert VARIANT_MAP[0]["instagram"] == "b"
    assert VARIANT_MAP[1]["tiktok"] == "b"
    assert VARIANT_MAP[1]["instagram"] == "a"

def _mock_hooks():
    return {i: {l: {"a": "hook a", "b": "hook b"} for l in [5, 9, 15]} for i in range(3)}

def _mock_captions():
    return {i: {"tiktok": "cap", "instagram": "cap", "youtube": "cap"} for i in range(3)}
```

- [ ] **Step 2: Verify fail**

```bash
python -m pytest tests/test_assembler.py -v 2>&1 | head -10
```

- [ ] **Step 3: Implement assembler.py**

```python
# content_engine/assembler.py
"""
Module 3: Assembler — visual-first pipeline.
Opening frame selected first, audio matched to its energy, rendered platform-specifically.
Produces 9 clips per day: 3 clips × 3 platforms (TikTok / Instagram / YouTube).
"""
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from content_engine import visual_engine
from content_engine.types import TrendBrief, OpeningFrame, PromptWeights

logger = logging.getLogger(__name__)

PROJECT_DIR  = Path(__file__).parent.parent
CLIP_LENGTHS = [5, 9, 15]   # seconds — one per clip index
PLATFORMS    = ["tiktok", "instagram", "youtube"]

# Variant A/B assignment: alternates per clip so each platform sees both across the week
VARIANT_MAP = {
    0: {"tiktok": "a", "instagram": "b", "youtube": "a"},
    1: {"tiktok": "b", "instagram": "a", "youtube": "b"},
    2: {"tiktok": "a", "instagram": "b", "youtube": "a"},
}

# Platform rendering settings
PLATFORM_SETTINGS = {
    "tiktok":     {"font_size": 72, "color_grade": "punchy",  "cut_in": "blur",   "text_y": 0.22, "safe_bottom": 0.15},
    "instagram":  {"font_size": 58, "color_grade": "warm",    "cut_in": "cut",    "text_y": 0.50, "safe_bottom": 0.20},
    "youtube":    {"font_size": 64, "color_grade": "neutral", "cut_in": "static", "text_y": 0.22, "safe_bottom": 0.15},
}

# Add outreach_agent to path for reuse of existing generator + processor
sys.path.insert(0, str(PROJECT_DIR / "outreach_agent"))
import generator as _gen


def _pick_audio() -> tuple[str, str]:
    """Pick next track via existing rotation in post_today.py logic."""
    import post_today as pt
    audio_path, title = pt.pick_next_track()
    return str(audio_path), title


def _find_best_audio_segment(audio_path: str, clip_duration: int) -> float:
    """Find audio start time whose energy best matches the opening frame's energy.
    Uses librosa to get beat grid + energy curve, returns start_time in seconds."""
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(audio_path, offset=30, duration=120, mono=True)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        # Find segment of clip_duration seconds with highest mean onset strength
        hop = librosa.get_duration(y=y, sr=sr) / len(onset_env)
        window = int(clip_duration / hop)
        best_start_frame = int(np.argmax([onset_env[i:i+window].mean() for i in range(len(onset_env)-window)]))
        return 30.0 + best_start_frame * hop
    except Exception:
        return 30.0  # fallback


def _generate_hooks(track_title: str) -> dict:
    """Generate A/B hooks for all 3 clips via existing generator."""
    clips_config = [
        {"index": 0, "length": 5,  "angle": "emotional"},
        {"index": 1, "length": 9,  "angle": "signal"},
        {"index": 2, "length": 15, "angle": "energy"},
    ]
    return _gen.generate_run_hooks(track_title, clips_config)


def _generate_captions(track_title: str, hooks: dict, brief: TrendBrief, weights: PromptWeights) -> dict:
    """Generate platform captions for all 3 clips via existing generator, injecting weights."""
    clips_data = []
    for i in range(3):
        clip_hooks = hooks.get(i, {})
        length = CLIP_LENGTHS[i]
        clip_hooks_by_length = clip_hooks.get(length, {})
        clips_data.append({
            "index": i,
            "length": length,
            "hooks": clip_hooks_by_length,
            "trend_context": f"Today's dominant emotion: {brief.dominant_emotion}. Gap: {brief.contrarian_gap}.",
        })
    return _gen.generate_run_captions(track_title, clips_data)


def _render_platform_clip(
    opening_frame_path: str,
    audio_path: str,
    audio_start: float,
    clip_length: int,
    hook_text: str,
    platform: str,
    output_path: str,
) -> str:
    """Render a single platform-specific clip via ffmpeg."""
    import post_today as pt
    from processor import format_to_vertical_multiclip

    settings = PLATFORM_SETTINGS[platform]
    category = "performances" if "perf" in opening_frame_path.lower() else "b-roll"

    # Mix audio into clip first (temp output)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        tmp_mixed = f.name

    format_to_vertical_multiclip(
        video_sources=[(opening_frame_path, 0)],
        output_path=tmp_mixed,
        clip_duration=float(clip_length),
        hook_text=hook_text,
        angle="energy",
        source_categories=[category],
    )

    # Overlay audio from the track
    cmd = [
        "ffmpeg", "-y",
        "-i", tmp_mixed,
        "-i", audio_path,
        "-ss", str(audio_start),
        "-t", str(clip_length),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=120)
    os.unlink(tmp_mixed)
    return output_path


def build_clip(
    opening_frame: OpeningFrame,
    audio_path: str,
    track_title: str,
    clip_length: int,
    platform: str,
    variant: str,
    hook_text: str,
    output_dir: str,
) -> str:
    """Build one platform-specific clip. Returns output path."""
    ts = datetime.now().strftime("%H%M")
    fname = f"clip{opening_frame.clip_index}_{platform}_{variant}_{ts}.mp4"
    output_path = str(Path(output_dir) / fname)

    audio_start = _find_best_audio_segment(audio_path, clip_length)

    return _render_platform_clip(
        opening_frame_path=opening_frame.source_file,
        audio_path=audio_path,
        audio_start=audio_start,
        clip_length=clip_length,
        hook_text=hook_text,
        platform=platform,
        output_path=output_path,
    )


def run_assembly(
    brief: TrendBrief,
    weights: PromptWeights,
    video_dirs: list[str],
    output_dir: str,
) -> list[dict]:
    """
    Full assembly run. Returns list of 9 dicts:
    {clip_index, platform, variant, path, hook_text, caption, hook_mechanism, visual_type}
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    audio_path, track_title = _pick_audio()
    logger.info(f"[assembler] Track: {track_title}")

    hooks    = _generate_hooks(track_title)
    captions = _generate_captions(track_title, hooks, brief, weights)

    results = []

    for clip_idx in range(3):
        clip_length = CLIP_LENGTHS[clip_idx]
        opening_frame = visual_engine.pick_opening_frame(brief, clip_idx, video_dirs)
        logger.info(f"[assembler] Clip {clip_idx}: {opening_frame.source} ({opening_frame.source_file})")

        clip_hooks = hooks.get(clip_idx, {}).get(clip_length, {})

        for platform in PLATFORMS:
            variant      = VARIANT_MAP[clip_idx][platform]
            hook_text    = clip_hooks.get(variant, clip_hooks.get("a", ""))
            caption      = captions.get(clip_idx, {}).get(platform, "")

            path = build_clip(
                opening_frame=opening_frame,
                audio_path=audio_path,
                track_title=track_title,
                clip_length=clip_length,
                platform=platform,
                variant=variant,
                hook_text=hook_text,
                output_dir=output_dir,
            )

            results.append({
                "clip_index":    clip_idx,
                "platform":      platform,
                "variant":       variant,
                "path":          path,
                "hook_text":     hook_text,
                "caption":       caption,
                "hook_mechanism": "tension",  # default; overridden by learning loop analysis
                "visual_type":   opening_frame.visual_category,
                "clip_length":   clip_length,
                "track_title":   track_title,
            })

    return results
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_assembler.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add content_engine/assembler.py tests/test_assembler.py
git commit -m "feat: assembler — visual-first 3-act pipeline, 9 clips/day, platform-specific rendering"
```

---

## Task 6: Distributor (native APIs + Buffer fallback)

**Files:**
- Create: `content_engine/distributor.py`
- Test: `tests/test_distributor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_distributor.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from content_engine.distributor import (
    post_instagram_reel, post_tiktok, post_youtube_short,
    distribute_clip, POST_SCHEDULE,
)

CLIP = {
    "clip_index": 0, "platform": "instagram", "variant": "b",
    "path": "/tmp/clip.mp4", "hook_text": "The drop", "caption": "Listen.",
    "hook_mechanism": "tension", "visual_type": "ai_generated",
    "clip_length": 15, "track_title": "Jericho",
}

def test_post_instagram_reel_calls_graph_api():
    mock_container = MagicMock(status_code=200)
    mock_container.json.return_value = {"id": "container_123"}
    mock_status = MagicMock(status_code=200)
    mock_status.json.return_value = {"status_code": "FINISHED"}
    mock_publish = MagicMock(status_code=200)
    mock_publish.json.return_value = {"id": "media_456"}

    with patch("content_engine.distributor.requests.post", side_effect=[mock_container, mock_publish]), \
         patch("content_engine.distributor.requests.get", return_value=mock_status), \
         patch("content_engine.distributor._upload_to_cloudinary", return_value="https://cdn/video.mp4"):
        result = post_instagram_reel("/tmp/clip.mp4", "Caption here", ig_user_id="123", access_token="tok")
    assert result["success"] is True
    assert result["post_id"] == "media_456"

def test_post_instagram_reel_returns_error_on_failure():
    mock_fail = MagicMock(status_code=400)
    mock_fail.json.return_value = {"error": {"message": "bad request"}}
    with patch("content_engine.distributor.requests.post", return_value=mock_fail), \
         patch("content_engine.distributor._upload_to_cloudinary", return_value="https://cdn/v.mp4"):
        result = post_instagram_reel("/tmp/clip.mp4", "Cap", ig_user_id="123", access_token="tok")
    assert result["success"] is False
    assert "error" in result

def test_distribute_clip_routes_to_correct_platform():
    with patch("content_engine.distributor.post_instagram_reel", return_value={"success": True, "post_id": "ig1"}) as mock_ig, \
         patch("content_engine.distributor._upload_to_cloudinary", return_value="https://cdn/v.mp4"):
        result = distribute_clip({**CLIP, "platform": "instagram"})
    mock_ig.assert_called_once()
    assert result["success"] is True

def test_post_schedule_has_three_clips_per_platform():
    for platform in ("tiktok", "instagram", "youtube"):
        assert len(POST_SCHEDULE[platform]) == 3
```

- [ ] **Step 2: Verify fail**

```bash
python -m pytest tests/test_distributor.py -v 2>&1 | head -10
```

- [ ] **Step 3: Implement distributor.py**

```python
# content_engine/distributor.py
"""
Module 4: Distributor
Native API uploads: Instagram Graph API → TikTok Content Posting API → YouTube Data API.
Falls back to Buffer (existing buffer_poster.py) if native API unavailable.
Posts 3 clips × 3 platforms = 9 posts/day on staggered schedule.
"""
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR / "outreach_agent"))

logger = logging.getLogger(__name__)

# Peak posting times CET (clip_index → time string)
POST_SCHEDULE = {
    "tiktok":    ["09:00", "12:00", "19:00"],
    "instagram": ["09:00", "11:00", "19:00"],
    "youtube":   ["10:00", "13:00", "20:00"],
}

INSTAGRAM_GRAPH_BASE = "https://graph.instagram.com/v21.0"
TIKTOK_API_BASE      = "https://open.tiktokapis.com/v2"
YOUTUBE_UPLOAD_BASE  = "https://www.googleapis.com/upload/youtube/v3"
YOUTUBE_API_BASE     = "https://www.googleapis.com/youtube/v3"


def _upload_to_cloudinary(video_path: str) -> str:
    """Upload video to Cloudinary, return public URL. Falls back to catbox."""
    cloudinary_url = os.environ.get("CLOUDINARY_URL", "")
    if cloudinary_url:
        try:
            from video_host import upload_video
            return upload_video(video_path)
        except Exception as e:
            logger.warning(f"Cloudinary upload failed: {e}")
    # Catbox fallback
    try:
        resp = requests.post("https://catbox.moe/user/api.php", data={
            "reqtype": "fileupload", "userhash": "",
        }, files={"fileToUpload": open(video_path, "rb")}, timeout=120)
        if resp.status_code == 200 and resp.text.startswith("https://"):
            return resp.text.strip()
    except Exception as e:
        logger.warning(f"Catbox upload failed: {e}")
    raise RuntimeError(f"All video upload methods failed for {video_path}")


def post_instagram_reel(video_path: str, caption: str, ig_user_id: str, access_token: str) -> dict:
    """Upload Reel via Instagram Graph API. Returns {success, post_id, error}."""
    try:
        video_url = _upload_to_cloudinary(video_path)

        # Step 1: Create media container
        resp = requests.post(f"{INSTAGRAM_GRAPH_BASE}/{ig_user_id}/media", params={
            "access_token": access_token,
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
        }, timeout=30)

        if resp.status_code != 200:
            return {"success": False, "error": resp.json().get("error", {}).get("message", resp.text)}

        creation_id = resp.json()["id"]

        # Step 2: Wait for container to be FINISHED
        for _ in range(24):  # up to 2 minutes
            time.sleep(5)
            status_resp = requests.get(f"{INSTAGRAM_GRAPH_BASE}/{creation_id}", params={
                "fields": "status_code",
                "access_token": access_token,
            }, timeout=15)
            if status_resp.json().get("status_code") == "FINISHED":
                break

        # Step 3: Publish
        pub_resp = requests.post(f"{INSTAGRAM_GRAPH_BASE}/{ig_user_id}/media_publish", params={
            "creation_id": creation_id,
            "access_token": access_token,
        }, timeout=30)

        if pub_resp.status_code != 200:
            return {"success": False, "error": pub_resp.json().get("error", {}).get("message", pub_resp.text)}

        return {"success": True, "post_id": pub_resp.json()["id"], "platform": "instagram"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def post_tiktok(video_path: str, caption: str, access_token: str) -> dict:
    """Upload video via TikTok Content Posting API v2."""
    try:
        video_url = _upload_to_cloudinary(video_path)
        video_size = Path(video_path).stat().st_size

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

        # Initialize upload
        init_resp = requests.post(f"{TIKTOK_API_BASE}/post/publish/video/init/", headers=headers, json={
            "post_info": {
                "title": caption[:150],
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": video_url,
            },
        }, timeout=30)

        if init_resp.status_code not in (200, 201):
            return {"success": False, "error": init_resp.text[:200]}

        publish_id = init_resp.json().get("data", {}).get("publish_id", "")

        # Poll for completion
        for _ in range(24):
            time.sleep(5)
            status_resp = requests.post(f"{TIKTOK_API_BASE}/post/publish/status/fetch/",
                headers=headers, json={"publish_id": publish_id}, timeout=15)
            status = status_resp.json().get("data", {}).get("status", "")
            if status == "PUBLISH_COMPLETE":
                return {"success": True, "post_id": publish_id, "platform": "tiktok"}
            if status in ("FAILED", "PUBLISH_FAILED"):
                return {"success": False, "error": f"TikTok publish failed: {status}"}

        return {"success": False, "error": "TikTok publish timed out"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def post_youtube_short(video_path: str, title: str, description: str, api_key: str, oauth_token: str) -> dict:
    """Upload YouTube Short via YouTube Data API v3."""
    try:
        video_size = Path(video_path).stat().st_size

        # Initiate resumable upload
        init_resp = requests.post(
            f"{YOUTUBE_UPLOAD_BASE}/videos",
            params={"uploadType": "resumable", "part": "snippet,status", "key": api_key},
            headers={
                "Authorization": f"Bearer {oauth_token}",
                "Content-Type": "application/json",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(video_size),
            },
            json={
                "snippet": {
                    "title": title[:100],
                    "description": description,
                    "tags": ["techno", "psytrance", "holyrave", "RJM", "#shorts"],
                    "categoryId": "10",
                },
                "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
            },
            timeout=30,
        )

        if init_resp.status_code not in (200, 201):
            return {"success": False, "error": init_resp.text[:200]}

        upload_url = init_resp.headers.get("Location", "")
        if not upload_url:
            return {"success": False, "error": "No upload URL in response"}

        # Upload video bytes
        with open(video_path, "rb") as f:
            upload_resp = requests.put(upload_url, data=f, headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(video_size),
            }, timeout=300)

        if upload_resp.status_code not in (200, 201):
            return {"success": False, "error": upload_resp.text[:200]}

        video_id = upload_resp.json().get("id", "")
        return {"success": True, "post_id": video_id, "platform": "youtube"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def _buffer_fallback(clip: dict) -> dict:
    """Fall back to existing Buffer poster if native API fails."""
    try:
        import buffer_poster
        result = buffer_poster.upload_video_and_queue(
            clip_path=clip["path"],
            tiktok_caption=clip.get("caption", ""),
            instagram_caption=clip.get("caption", ""),
            youtube_title=clip.get("track_title", "RJM"),
            youtube_desc=clip.get("caption", ""),
        )
        success = any(v.get("success") for v in result.values())
        return {"success": success, "post_id": "buffer", "platform": clip["platform"], "via": "buffer_fallback"}
    except Exception as e:
        return {"success": False, "error": f"Buffer fallback also failed: {e}"}


def distribute_clip(clip: dict) -> dict:
    """
    Distribute a single clip to its platform using native API.
    Falls back to Buffer on failure.
    clip dict: {platform, path, caption, hook_text, track_title, ...}
    """
    platform = clip["platform"]
    path     = clip["path"]
    caption  = clip.get("caption", "")

    if platform == "instagram":
        ig_user_id    = os.environ.get("INSTAGRAM_USER_ID", "")
        access_token  = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        if ig_user_id and access_token:
            result = post_instagram_reel(path, caption, ig_user_id, access_token)
        else:
            result = _buffer_fallback(clip)

    elif platform == "tiktok":
        access_token = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
        if access_token:
            result = post_tiktok(path, caption, access_token)
        else:
            result = _buffer_fallback(clip)

    elif platform == "youtube":
        api_key     = os.environ.get("YOUTUBE_API_KEY", "")
        oauth_token = os.environ.get("YOUTUBE_OAUTH_TOKEN", "")
        title       = f"{clip.get('track_title', 'RJM')} | Holy Rave #shorts"
        if api_key and oauth_token:
            result = post_youtube_short(path, title, caption, api_key, oauth_token)
        else:
            result = _buffer_fallback(clip)

    else:
        result = {"success": False, "error": f"Unknown platform: {platform}"}

    if not result["success"]:
        logger.warning(f"[distributor] {platform} native failed: {result.get('error')} — trying Buffer fallback")
        result = _buffer_fallback(clip)

    result["clip_index"] = clip.get("clip_index")
    result["variant"]    = clip.get("variant")
    logger.info(f"[distributor] {platform} clip {clip.get('clip_index')}{clip.get('variant')}: {'✓' if result['success'] else '✗'}")
    return result


def distribute_all(clips: list[dict]) -> list[dict]:
    """Distribute all 9 clips. Returns list of result dicts."""
    return [distribute_clip(c) for c in clips]
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_distributor.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add content_engine/distributor.py tests/test_distributor.py
git commit -m "feat: distributor — native IG/TikTok/YouTube APIs with Buffer fallback"
```

---

## Task 7: Learning Loop

**Files:**
- Create: `content_engine/learning_loop.py`
- Test: `tests/test_learning_loop.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_learning_loop.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from content_engine.learning_loop import (
    fetch_instagram_metrics, calculate_new_weights, detect_outliers, run,
)
from content_engine.types import PerformanceRecord, PromptWeights

def _make_record(**kwargs) -> PerformanceRecord:
    defaults = dict(
        post_id="p1", platform="instagram", clip_index=0, variant="a",
        hook_mechanism="tension", visual_type="ai_generated", clip_length=15,
        views=1000, completion_rate=0.35, scroll_stop_rate=0.08,
        share_rate=0.02, save_rate=0.05, recorded_at="2026-04-12T18:00:00",
    )
    return PerformanceRecord(**{**defaults, **kwargs})

def test_fetch_instagram_metrics_returns_records():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"data": [
        {"id": "post1", "insights": {"data": [
            {"name": "plays", "values": [{"value": 5000}]},
            {"name": "saved", "values": [{"value": 250}]},
            {"name": "shares", "values": [{"value": 100}]},
            {"name": "reach", "values": [{"value": 62500}]},
        ]}},
    ]}
    with patch("content_engine.learning_loop.requests.get", return_value=mock_resp):
        records = fetch_instagram_metrics(
            post_ids=[{"post_id": "post1", "clip_index": 0, "variant": "a",
                       "hook_mechanism": "tension", "visual_type": "ai_generated", "clip_length": 15}],
            access_token="tok",
        )
    assert len(records) >= 1
    assert records[0].platform == "instagram"

def test_calculate_new_weights_boosts_winning_mechanism():
    records = [
        _make_record(hook_mechanism="tension",  completion_rate=0.45),
        _make_record(hook_mechanism="tension",  completion_rate=0.40),
        _make_record(hook_mechanism="identity", completion_rate=0.10),
        _make_record(hook_mechanism="identity", completion_rate=0.12),
    ]
    old_weights = PromptWeights.defaults()
    new_weights = calculate_new_weights(records, old_weights)
    assert new_weights.hook_weights["tension"] > new_weights.hook_weights["identity"]

def test_calculate_new_weights_boosts_winning_visual():
    records = [
        _make_record(visual_type="ai_generated", completion_rate=0.50),
        _make_record(visual_type="ai_generated", completion_rate=0.48),
        _make_record(visual_type="b_roll",        completion_rate=0.05),
    ]
    old_weights = PromptWeights.defaults()
    new_weights = calculate_new_weights(records, old_weights)
    assert new_weights.visual_weights["ai_generated"] > new_weights.visual_weights["b_roll"]

def test_detect_outliers_flags_2x_average():
    records = [_make_record(views=v) for v in [1000, 1100, 950, 8000, 900]]
    outliers = detect_outliers(records)
    assert len(outliers) == 1
    assert outliers[0].views == 8000
```

- [ ] **Step 2: Verify fail**

```bash
python -m pytest tests/test_learning_loop.py -v 2>&1 | head -10
```

- [ ] **Step 3: Implement learning_loop.py**

```python
# content_engine/learning_loop.py
"""
Module 5: Learning Loop
Runs daily at 18:00 CET. Pulls performance metrics from IG/TikTok/YouTube,
recalculates prompt_weights.json, writes breakthrough analyses for outliers.
"""
import json
import logging
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, date as _date
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).parent.parent
PERFORMANCE_DIR = PROJECT_DIR / "data" / "performance"
LEARNING_DIR    = PROJECT_DIR / "learning"
WEIGHTS_FILE    = PROJECT_DIR / "prompt_weights.json"

logger = logging.getLogger(__name__)

INSTAGRAM_GRAPH_BASE = "https://graph.instagram.com/v21.0"

# Learning rate: how aggressively weights shift each day (0 = no change, 1 = full replacement)
LEARNING_RATE = 0.3


def fetch_instagram_metrics(post_ids: list[dict], access_token: str) -> list:
    """Fetch per-post insights from Instagram Graph API."""
    from content_engine.types import PerformanceRecord

    records = []
    for post_meta in post_ids:
        post_id = post_meta["post_id"]
        try:
            resp = requests.get(
                f"{INSTAGRAM_GRAPH_BASE}/{post_id}/insights",
                params={
                    "metric": "plays,reach,saved,shares,total_interactions",
                    "period": "lifetime",
                    "access_token": access_token,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                continue

            metrics = {d["name"]: d["values"][0]["value"] for d in resp.json().get("data", [])}
            plays  = metrics.get("plays", 0)
            reach  = metrics.get("reach", 1)
            saved  = metrics.get("saved", 0)
            shares = metrics.get("shares", 0)

            records.append(PerformanceRecord(
                post_id=post_id,
                platform="instagram",
                clip_index=post_meta.get("clip_index", 0),
                variant=post_meta.get("variant", "a"),
                hook_mechanism=post_meta.get("hook_mechanism", "tension"),
                visual_type=post_meta.get("visual_type", "b_roll"),
                clip_length=post_meta.get("clip_length", 15),
                views=plays,
                completion_rate=0.0,   # IG doesn't expose completion directly
                scroll_stop_rate=round(plays / max(reach, 1), 4),
                share_rate=round(shares / max(plays, 1), 4),
                save_rate=round(saved / max(plays, 1), 4),
                recorded_at=datetime.now().isoformat(),
            ))
        except Exception as e:
            logger.warning(f"[learning_loop] IG metrics fetch failed for {post_id}: {e}")

    return records


def fetch_youtube_metrics(post_ids: list[dict], oauth_token: str) -> list:
    """Fetch YouTube video analytics."""
    from content_engine.types import PerformanceRecord

    records = []
    for post_meta in post_ids:
        video_id = post_meta.get("post_id", "")
        if not video_id:
            continue
        try:
            resp = requests.get(
                "https://youtubeanalytics.googleapis.com/v2/reports",
                params={
                    "ids": "channel==MINE",
                    "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
                    "filters": f"video=={video_id}",
                    "startDate": _date.today().isoformat(),
                    "endDate": _date.today().isoformat(),
                    "dimensions": "video",
                },
                headers={"Authorization": f"Bearer {oauth_token}"},
                timeout=15,
            )
            if resp.status_code != 200:
                continue

            rows = resp.json().get("rows", [])
            if not rows:
                continue

            _, views, watch_mins, avg_dur, avg_pct = rows[0]
            clip_length = post_meta.get("clip_length", 15)
            records.append(PerformanceRecord(
                post_id=video_id, platform="youtube",
                clip_index=post_meta.get("clip_index", 0),
                variant=post_meta.get("variant", "a"),
                hook_mechanism=post_meta.get("hook_mechanism", "tension"),
                visual_type=post_meta.get("visual_type", "b_roll"),
                clip_length=clip_length,
                views=int(views),
                completion_rate=round(float(avg_pct) / 100, 4),
                scroll_stop_rate=0.0,
                share_rate=0.0,
                save_rate=0.0,
                recorded_at=datetime.now().isoformat(),
            ))
        except Exception as e:
            logger.warning(f"[learning_loop] YouTube metrics failed for {video_id}: {e}")

    return records


def calculate_new_weights(records: list, old_weights) -> "PromptWeights":
    """
    Recalculate prompt weights based on completion_rate + save_rate per dimension.
    Uses exponential moving average: new = old * (1 - LR) + signal * LR
    """
    from content_engine.types import PromptWeights

    # Group by hook_mechanism and visual_type, average completion + save rate
    hook_scores:   defaultdict = defaultdict(list)
    visual_scores: defaultdict = defaultdict(list)
    platform_scores: defaultdict = defaultdict(list)
    length_scores: defaultdict  = defaultdict(list)

    for r in records:
        score = r.completion_rate * 0.5 + r.save_rate * 0.3 + r.scroll_stop_rate * 0.2
        hook_scores[r.hook_mechanism].append(score)
        visual_scores[r.visual_type].append(score)
        platform_scores[r.platform].append(score)
        length_scores[r.clip_length].append(score)

    def _avg(d: dict, key: str) -> float:
        vals = d.get(key, [])
        return sum(vals) / len(vals) if vals else 0.0

    def _update_weights(old: dict, scores: defaultdict) -> dict:
        if not scores:
            return old
        max_score = max((sum(v)/len(v)) for v in scores.values() if v) or 1.0
        new = {}
        for k in old:
            signal = _avg(scores, k) / max_score * 2.0  # normalize to 0-2 range
            if signal > 0:
                new[k] = round(old[k] * (1 - LEARNING_RATE) + signal * LEARNING_RATE, 3)
            else:
                new[k] = old[k]  # no data → keep current
        return new

    best_platform = max(platform_scores, key=lambda k: _avg(platform_scores, k)) if platform_scores else old_weights.best_platform
    best_length   = max(length_scores,   key=lambda k: _avg(length_scores, k))   if length_scores   else old_weights.best_clip_length

    return PromptWeights(
        hook_weights=_update_weights(old_weights.hook_weights, hook_scores),
        visual_weights=_update_weights(old_weights.visual_weights, visual_scores),
        best_clip_length=int(best_length),
        best_platform=best_platform,
        updated=datetime.now().isoformat(),
    )


def detect_outliers(records: list) -> list:
    """Return records with views > 2× rolling average."""
    if not records:
        return []
    avg_views = sum(r.views for r in records) / len(records)
    return [r for r in records if r.views > avg_views * 2]


def _write_breakthrough(outlier, date_str: str):
    """Use Claude CLI to analyze a breakthrough post and save notes."""
    claude = os.environ.get("CLAUDE_CLI_PATH", "") or "claude"
    prompt = (
        f"A social media post went viral today (2x average):\n"
        f"Platform: {outlier.platform}\n"
        f"Views: {outlier.views}\n"
        f"Completion rate: {outlier.completion_rate:.1%}\n"
        f"Save rate: {outlier.save_rate:.1%}\n"
        f"Hook mechanism: {outlier.hook_mechanism}\n"
        f"Visual type: {outlier.visual_type}\n"
        f"Clip length: {outlier.clip_length}s\n\n"
        "In 3-5 bullet points, explain what likely made this work and what to repeat tomorrow. "
        "Be specific. Focus on the combination of hook + visual that drove completion."
    )
    result = subprocess.run(
        [claude, "--print", "--model", "claude-haiku-4-5-20251001", prompt],
        capture_output=True, text=True, timeout=60,
    )
    out_dir = LEARNING_DIR / "breakthroughs"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{date_str}_{outlier.platform}_{outlier.post_id[:8]}.md").write_text(
        f"# Breakthrough: {outlier.platform} {date_str}\n\n"
        f"**Views:** {outlier.views}  **Completion:** {outlier.completion_rate:.1%}  **Saves:** {outlier.save_rate:.1%}\n\n"
        + result.stdout.strip()
    )


def _save_performance_log(records: list, date_str: str):
    """Append today's records to data/performance/YYYY-MM-DD.json"""
    PERFORMANCE_DIR.mkdir(parents=True, exist_ok=True)
    path = PERFORMANCE_DIR / f"{date_str}.json"
    path.write_text(json.dumps([r.__dict__ for r in records], indent=2))


def run(date_str: str = None, post_registry: list[dict] = None) -> "PromptWeights":
    """
    Full learning loop run.
    post_registry: list of {post_id, platform, clip_index, variant, hook_mechanism, visual_type, clip_length}
    Reads from data/performance/YYYY-MM-DD_posts.json if post_registry not provided.
    """
    from content_engine.types import PromptWeights

    if date_str is None:
        date_str = _date.today().isoformat()

    # Load post registry (written by distributor after each successful post)
    registry_path = PERFORMANCE_DIR / f"{date_str}_posts.json"
    if post_registry is None:
        if registry_path.exists():
            post_registry = json.loads(registry_path.read_text())
        else:
            logger.warning("[learning_loop] No post registry found — skipping metric fetch")
            post_registry = []

    ig_posts  = [p for p in post_registry if p.get("platform") == "instagram"]
    yt_posts  = [p for p in post_registry if p.get("platform") == "youtube"]

    ig_token  = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    yt_token  = os.environ.get("YOUTUBE_OAUTH_TOKEN", "")

    records = []
    if ig_posts and ig_token:
        records += fetch_instagram_metrics(ig_posts, ig_token)
    if yt_posts and yt_token:
        records += fetch_youtube_metrics(yt_posts, yt_token)

    logger.info(f"[learning_loop] {len(records)} performance records collected")

    if not records:
        logger.warning("[learning_loop] No records — weights unchanged")
        return PromptWeights.load()

    _save_performance_log(records, date_str)

    old_weights  = PromptWeights.load()
    new_weights  = calculate_new_weights(records, old_weights)
    new_weights.save()
    logger.info(f"[learning_loop] Weights updated: best={new_weights.best_platform}, length={new_weights.best_clip_length}s")

    # Breakthrough analysis
    outliers = detect_outliers(records)
    for o in outliers:
        logger.info(f"[learning_loop] Breakthrough detected: {o.platform} {o.views} views")
        _write_breakthrough(o, date_str)

    return new_weights


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    run()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_learning_loop.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add content_engine/learning_loop.py tests/test_learning_loop.py
git commit -m "feat: learning_loop — performance ingestion, weight evolution, breakthrough analysis"
```

---

## Task 8: Wiring — post_today.py + rjm.py

**Files:**
- Modify: `outreach_agent/post_today.py` — add `--engine viral` flag
- Modify: `rjm.py` — add `content viral` subcommand
- Create: `content_engine/pipeline.py` — top-level orchestrator

- [ ] **Step 1: Create pipeline.py**

```python
# content_engine/pipeline.py
"""
Top-level orchestrator for the viral shorts pipeline.
Called by: rjm.py content viral, holy-rave-daily-run agent, cron at 08:00 CET.
"""
import json
import logging
from datetime import date as _date
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
PERFORMANCE_DIR = PROJECT_DIR / "data" / "performance"

logger = logging.getLogger(__name__)


def run_full_day(dry_run: bool = False) -> dict:
    """
    Full daily pipeline:
    1. Load today's trend brief (must have been run at 06:00 by trend_scanner)
    2. Load prompt weights
    3. Assemble 9 clips
    4. Distribute to platforms (unless dry_run)
    5. Save post registry for learning loop
    Returns summary dict.
    """
    from content_engine.types import TrendBrief, PromptWeights
    from content_engine import assembler, distributor

    date_str = _date.today().isoformat()

    # Load today's brief — fall back to generating one now if missing
    try:
        brief = TrendBrief.load_today()
    except FileNotFoundError:
        logger.warning("[pipeline] No trend brief found — running trend_scanner now")
        from content_engine import trend_scanner
        brief = trend_scanner.run(date_str)

    weights   = PromptWeights.load()
    video_dirs = [
        str(PROJECT_DIR / "content" / "videos" / "performances"),
        str(PROJECT_DIR / "content" / "videos" / "b-roll"),
        str(PROJECT_DIR / "content" / "videos" / "phone-footage"),
    ]
    output_dir = str(PROJECT_DIR / "content" / "output" / date_str)

    logger.info(f"[pipeline] Assembling 9 clips for {date_str}")
    clips = assembler.run_assembly(brief=brief, weights=weights, video_dirs=video_dirs, output_dir=output_dir)
    logger.info(f"[pipeline] Assembly complete: {len(clips)} clips")

    if dry_run:
        logger.info("[pipeline] DRY RUN — skipping distribution")
        return {"clips": len(clips), "distributed": 0, "dry_run": True}

    results = distributor.distribute_all(clips)
    successes = [r for r in results if r.get("success")]
    logger.info(f"[pipeline] Distribution: {len(successes)}/{len(results)} succeeded")

    # Save post registry for learning loop
    registry = []
    for clip, result in zip(clips, results):
        if result.get("success"):
            registry.append({
                "post_id":       result.get("post_id", ""),
                "platform":      clip["platform"],
                "clip_index":    clip["clip_index"],
                "variant":       clip["variant"],
                "hook_mechanism": clip["hook_mechanism"],
                "visual_type":   clip["visual_type"],
                "clip_length":   clip["clip_length"],
            })

    PERFORMANCE_DIR.mkdir(parents=True, exist_ok=True)
    (PERFORMANCE_DIR / f"{date_str}_posts.json").write_text(json.dumps(registry, indent=2))

    return {"clips": len(clips), "distributed": len(successes), "dry_run": False}
```

- [ ] **Step 2: Add `--engine viral` to post_today.py**

Find the argparse section in `outreach_agent/post_today.py` (around line 100) and add:

```python
# In the argparse setup block, add:
parser.add_argument("--engine", choices=["legacy", "viral"], default="legacy",
                    help="Use 'viral' for the new content_engine pipeline")
```

In the `main()` function, before the existing logic runs, add at the top:

```python
if args.engine == "viral":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from content_engine.pipeline import run_full_day
    result = run_full_day(dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return
```

- [ ] **Step 3: Add `content viral` to rjm.py**

Find the subcommands section in `rjm.py` and add handling for `content viral`:

```python
elif args.command == "content" and getattr(args, "subcommand", None) == "viral":
    import sys
    sys.path.insert(0, str(BASE_DIR))
    from content_engine.pipeline import run_full_day
    dry = getattr(args, "dry_run", False)
    result = run_full_day(dry_run=dry)
    print(json.dumps(result, indent=2))
```

And add to the argparse setup:

```python
# Under 'content' subparser, add:
content_sub = content_parser.add_subparsers(dest="subcommand")
viral_parser = content_sub.add_parser("viral", help="Run viral shorts pipeline")
viral_parser.add_argument("--dry-run", action="store_true")
```

- [ ] **Step 4: Smoke test pipeline entry point**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/optimistic-perlman"
python3 -c "
import sys; sys.path.insert(0, '.')
from content_engine.pipeline import run_full_day
print('pipeline.py imports OK')
"
```
Expected: `pipeline.py imports OK` (no errors)

- [ ] **Step 5: Run all tests**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: all existing tests pass, new tests pass

- [ ] **Step 6: Commit**

```bash
git add content_engine/pipeline.py outreach_agent/post_today.py rjm.py
git commit -m "feat: wire pipeline — content viral command, --engine viral flag in post_today"
```

---

## Task 9: Agent Definitions

**Files:**
- Create: `agents/holy-rave-trend-scanner.md`
- Create: `agents/holy-rave-visual-engine.md`
- Create: `agents/holy-rave-learning-loop.md`
- Modify: `agents/holy-rave-daily-run.md`

- [ ] **Step 1: Create trend scanner agent**

```markdown
<!-- agents/holy-rave-trend-scanner.md -->
# holy-rave-trend-scanner

**Cadence:** Daily 06:00 CET  
**Purpose:** Scrape YouTube trending music + Spotify featured playlists, synthesize Today's Brief via Claude CLI.

## Run command
```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3 -c "
import sys; sys.path.insert(0, '.')
from content_engine.trend_scanner import run
import json
brief = run()
print(json.dumps(brief.__dict__, indent=2))
"
```

## Success criteria
- `data/trend_brief/YYYY-MM-DD.json` written
- `dominant_emotion` field populated
- `trend_confidence` > 0.3

## On failure
- Log warning and continue — assembler will generate clips using Claude general knowledge
- Do NOT block the 08:00 assembly run
```

- [ ] **Step 2: Create visual engine agent**

```markdown
<!-- agents/holy-rave-visual-engine.md -->
# holy-rave-visual-engine

**Cadence:** Daily 07:00 CET (called by holy-rave-daily-run, not standalone)  
**Purpose:** Score footage library against today's brief. Generate AI clips via Runway ML when footage score < 7.0.

## Trigger
Called internally by `content_engine/assembler.py` → `visual_engine.pick_opening_frame()`.
Not invoked directly by cron — runs as part of the 08:00 assembly.

## Required env vars
- `RUNWAY_API_KEY` — Runway ML Gen-4 Turbo
- `CLOUDINARY_URL` — for video hosting

## Cost guard
AI generation only fires when `footage_score < 7.0`. Check `prompt_weights.json`
for `visual_weights.ai_generated` — if > 1.3, the learning loop has confirmed AI
clips outperform footage; lean into it.
```

- [ ] **Step 3: Create learning loop agent**

```markdown
<!-- agents/holy-rave-learning-loop.md -->
# holy-rave-learning-loop

**Cadence:** Daily 18:00 CET  
**Purpose:** Pull 9-hour performance data from IG/YouTube, update prompt_weights.json, analyze breakthroughs.

## Run command
```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3 -c "
import sys; sys.path.insert(0, '.')
from content_engine.learning_loop import run
import json
weights = run()
print(json.dumps(weights.__dict__, indent=2))
"
```

## Success criteria
- `prompt_weights.json` updated with new `updated` timestamp
- `data/performance/YYYY-MM-DD.json` written
- If outlier detected: `learning/breakthroughs/YYYY-MM-DD_*.md` written

## Required env vars
- `INSTAGRAM_ACCESS_TOKEN`
- `YOUTUBE_OAUTH_TOKEN`
```

- [ ] **Step 4: Update holy-rave-daily-run.md**

Replace the existing run steps in `agents/holy-rave-daily-run.md` with:

```markdown
## Daily Run Steps (Viral Engine)

**06:00** — `holy-rave-trend-scanner` runs → `data/trend_brief/YYYY-MM-DD.json`

**08:00** — Main assembly + distribution:
```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3 rjm.py content viral
```
Produces 9 clips (3 clips × TikTok / Instagram / YouTube).
Saves post registry to `data/performance/YYYY-MM-DD_posts.json`.

**18:00** — `holy-rave-learning-loop` runs → updates `prompt_weights.json`

## Dry run (test without posting)
```bash
python3 rjm.py content viral --dry-run
```

## Success criteria
- 9 posts live across platforms
- `data/performance/YYYY-MM-DD_posts.json` contains entries for all successful posts
- No unhandled exceptions in logs
```

- [ ] **Step 5: Commit agent definitions**

```bash
git add agents/holy-rave-trend-scanner.md agents/holy-rave-visual-engine.md \
        agents/holy-rave-learning-loop.md agents/holy-rave-daily-run.md
git commit -m "feat: agent definitions — trend-scanner, visual-engine, learning-loop; update daily-run"
```

---

## Task 10: Cron Scheduling

**Files:**
- No new files — use existing cron infrastructure via `rjm.py`

- [ ] **Step 1: Check existing cron setup**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/optimistic-perlman"
grep -n "cron\|schedule\|CronCreate" rjm.py | head -20
```

- [ ] **Step 2: Add viral pipeline cron schedules**

Using the project's existing scheduling mechanism (RemoteTrigger / CronCreate):

```bash
# Trend Scanner — 06:00 CET daily (05:00 UTC)
# Visual pipeline + Distribution — 08:00 CET (07:00 UTC)  
# Learning Loop — 18:00 CET (17:00 UTC)
```

Check whether a `scripts/` directory has existing cron setup:

```bash
ls scripts/
cat scripts/*.sh 2>/dev/null | head -30
```

- [ ] **Step 3: Create launchd plist for macOS scheduling**

```xml
<!-- ~/Library/LaunchAgents/com.rjm.trend-scanner.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>          <string>com.rjm.trend-scanner</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/rjm.py</string>
        <string>content</string><string>trend-scan</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
    <key>StandardOutPath</key>
    <string>/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/data/logs/trend-scanner.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/data/logs/trend-scanner-err.log</string>
</dict>
</plist>
```

Create equivalent plists for `content viral` (08:00) and `content learning` (18:00).

- [ ] **Step 4: Add `content trend-scan` and `content learning` to rjm.py**

```python
elif args.command == "content" and getattr(args, "subcommand", None) == "trend-scan":
    from content_engine.trend_scanner import run
    import json
    brief = run()
    print(json.dumps(brief.__dict__, indent=2))

elif args.command == "content" and getattr(args, "subcommand", None) == "learning":
    from content_engine.learning_loop import run
    import json
    weights = run()
    print(json.dumps(weights.__dict__, indent=2))
```

- [ ] **Step 5: Create log directory**

```bash
mkdir -p "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/data/logs"
```

- [ ] **Step 6: Commit**

```bash
git add rjm.py data/logs/
git commit -m "feat: cron commands — content trend-scan, content viral, content learning"
```

---

## Task 11: Integration Dry-Run

- [ ] **Step 1: Run full test suite**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/optimistic-perlman"
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```
Expected: all tests pass (existing + new)

- [ ] **Step 2: Import smoke test for full pipeline**

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from content_engine.types import TrendBrief, OpeningFrame, PerformanceRecord, PromptWeights
from content_engine import trend_scanner, footage_scorer, visual_engine, assembler, distributor, learning_loop, pipeline
print('All content_engine modules import OK')
print('PromptWeights:', PromptWeights.load().__dict__)
"
```
Expected: `All content_engine modules import OK`

- [ ] **Step 3: Dry-run trend scanner (uses real Claude CLI)**

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from content_engine.trend_scanner import synthesize_brief
brief = synthesize_brief('2026-04-12', ['Tribal Techno Mix', 'Psytrance Festival'], ['Tribal Heat playlist'])
print('emotion:', brief.dominant_emotion)
print('confidence:', brief.trend_confidence)
print('gap:', brief.contrarian_gap)
"
```
Expected: JSON with real values from Claude CLI

- [ ] **Step 4: Dry-run footage scorer (no video files needed)**

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from content_engine.footage_scorer import build_candidate_list, pick_best_opening_frame
candidates = build_candidate_list([
    'content/videos/performances',
    'content/videos/b-roll',
])
print(f'Found {len(candidates)} video candidates')
if candidates:
    path, score = pick_best_opening_frame(candidates, 'euphoric release')
    print(f'Best: {path} (score={score:.2f})')
"
```

- [ ] **Step 5: Dry-run full pipeline**

```bash
python3 rjm.py content viral --dry-run 2>&1
```
Expected: JSON output with `"dry_run": true`, no exceptions

- [ ] **Step 6: Final commit + tag**

```bash
git add -A
git commit -m "feat: viral shorts mindhive — full pipeline complete (trend→visual→assemble→distribute→learn)"
git tag v2.0.0-viral-mindhive
```

---

## Environment Variables Checklist

Before going live, verify these are set in the project `.env`:

```bash
# Required — new
RUNWAY_API_KEY=          # Runway ML Gen-4 Turbo
INSTAGRAM_USER_ID=       # Numeric IG user ID (not username)
INSTAGRAM_ACCESS_TOKEN=  # Long-lived IG Graph API token
TIKTOK_ACCESS_TOKEN=     # TikTok for Developers OAuth token
YOUTUBE_API_KEY=         # YouTube Data API v3 key
YOUTUBE_OAUTH_TOKEN=     # YouTube OAuth token for upload + analytics
SPOTIFY_ACCESS_TOKEN=    # Spotify Web API token (optional — enhances trend scanner)

# Existing — verify still set
BUFFER_API_KEY=          # Fallback distribution
CLOUDINARY_URL=          # Primary video hosting
```

---

## Self-Review Checklist

- [x] All 5 modules covered: trend_scanner, footage_scorer, visual_engine, assembler, learning_loop
- [x] No TBDs — every function has actual code
- [x] Types defined in Task 1 used consistently throughout (TrendBrief, OpeningFrame, PerformanceRecord, PromptWeights)
- [x] Claude CLI pattern matches existing generator.py exactly (_call_claude with subprocess)
- [x] Tests use mocking pattern matching existing tests/test_buffer_poster.py
- [x] Buffer fallback preserved — nothing breaks if native APIs aren't configured yet
- [x] Pipeline is additive — `--engine legacy` still works in post_today.py
