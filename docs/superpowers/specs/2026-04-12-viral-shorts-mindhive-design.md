# Viral Shorts Mindhive — Design Spec
**Date:** 2026-04-12  
**Author:** Robert-Jan Mastenbroek / Claude  
**Status:** Approved for implementation planning

---

## Overview

A fully autonomous, self-improving short-form video machine that posts 3 videos × 3 platforms = 9 posts/day with zero manual involvement. Target: 500K+ views per video minimum. The system replaces the current `holy-rave-daily-run` agent with a 5-module pipeline that learns from every post and compounds performance daily.

**North Star:** 1,000,000 Spotify monthly listeners. Every clip drives discovery.

**Core architectural flip:** Current system builds from audio outward. New system builds from visual shock frame inward — the scroll-stop is engineered first, everything else supports it.

---

## Constraints

- **No RJM involvement** — fully headless, autonomous
- **No WhatsApp integration** — removed from scope
- **Claude CLI only** — no Anthropic API calls (stays within Pro Max plan)
- **Cost target:** ~$18/month new spend (primarily AI video generation)
- **3 clips/day per platform** — 9 total posts/day across TikTok, Instagram Reels, YouTube Shorts
- **No dependency on specific venue footage** — system must work without Holy Rave church setup

---

## Architecture

Five modules run in a daily cycle. Output of each feeds the next; Learning Loop feeds back to day 1.

```
06:00  TREND SCANNER       → Today's Brief (JSON)
07:00  VISUAL SHOCK ENGINE → 3 opening frames
08:00  ASSEMBLY PIPELINE   → 9 platform-specific clips (3 clips × 3 platforms)
09:00  DISTRIBUTION LAYER  → Posted to TikTok / Instagram / YouTube
18:00  LEARNING LOOP       → prompt_weights.json updated for tomorrow
```

---

## Module 1: Trend Scanner

**Agent:** `holy-rave-trend-scanner` (new)  
**Runs:** Daily 06:00 CET  
**Trigger:** Cron via `rjm-master`

### What it monitors
- TikTok trending sounds + visual formats: electronic music, psytrance, techno, spirituality/mindfulness niches
- Instagram Reels trending audio and format patterns (same niches)
- YouTube Shorts trending topics in electronic music
- Spotify for Artists dashboard scraping (artists.spotify.com) — audience demographics, top cities, playlist adds, listener momentum signals what energy is being rewarded right now

### How
1. Web scraping of TikTok trending page + IG Reels explore via headless browser or lightweight HTTP scraper
2. Spotify for Artists dashboard scrape (artists.spotify.com) — logged in via stored session cookies — pulls listener momentum, playlist adds, top audience demographics
3. Claude CLI call (temperature 0.3) — analyzes scraped data and outputs structured Today's Brief

### Output: `data/trend_brief/YYYY-MM-DD.json`
```json
{
  "date": "2026-04-12",
  "top_visual_formats": ["format_a", "format_b", "format_c"],
  "dominant_emotion": "euphoric release",
  "oversaturated": "lo-fi chill aesthetic — avoid",
  "hook_pattern_of_day": "open with question, resolve with drop",
  "contrarian_gap": "nobody in techno niche is using silence as tension",
  "trend_confidence": 0.82
}
```

---

## Module 2: Visual Shock Engine

**Agent:** `holy-rave-visual-engine` (new)  
**Runs:** Daily 07:00 CET  
**Input:** `data/trend_brief/YYYY-MM-DD.json` + `prompt_weights.json`

### Purpose
Select or generate 3 scroll-stopping opening frames — one per clip. Each frame must create cognitive dissonance in under 1 second: the brain cannot categorize it immediately, so it must keep watching to resolve the tension.

### Decision tree (per clip)
1. Score existing footage library (`content/videos/`) against today's brief
   - Scoring criteria: motion velocity, visual contrast, unexpectedness, emotion alignment, freshness (not used in last 14 days)
   - Score threshold: 7.0/10 → use footage
2. If score < 7.0 → generate with AI (Runway ML or Kling API)

### Footage scoring
New `footage_scorer.py` module. Replaces simple timestamp rotation with semantic scoring:
- `motion_score`: velocity of movement in first 2 seconds (via ffprobe motion vectors)
- `contrast_score`: visual contrast ratio (light vs dark, busy vs empty)
- `freshness_score`: days since last use (penalizes recent use)
- `emotion_match`: Claude CLI scoring against today's dominant emotion

### AI generation
- **Tool:** Runway ML Gen-3 Alpha API — chosen for API stability, mature documentation, and reliable autonomous operation
- **Prompt template:**
  ```
  [today's dominant emotion] + [brand formula: ancient visual language + electronic energy]
  Examples:
  - "Sacred geometry dissolving into a sea of dancing bodies, tribal fire, particle effects"
  - "Aerial coastline at golden hour, light trails from a synth pulse, slow motion"
  - "Stone ruins overgrown with bioluminescent plants, crowd silhouettes, bass ripple"
  ```
- Only fires when footage score < threshold — cost control
- Estimated usage: 1-2 generations/day = ~$0.50/day

### Output
`data/opening_frames/YYYY-MM-DD/` — 3 video clips (2-3s each) + metadata JSON per clip:
```json
{
  "clip_index": 1,
  "source": "ai_generated|footage",
  "source_file": "...",
  "emotion_tag": "euphoric_release",
  "visual_category": "sacred_geometry",
  "footage_score": 8.2
}
```

---

## Module 3: Video Assembly Pipeline (refactored)

**Agent:** `holy-rave-daily-run` (refactored)  
**Runs:** Daily 08:00 CET  
**Input:** Opening frames from Module 2 + `prompt_weights.json`

### Audio-visual matching (new)
Replace ffprobe RMS peak detection with `librosa`-based analysis:
- Beat grid detection (actual downbeats, not just volume peaks)
- Energy curve mapping — find the audio segment whose energy arc matches the opening frame's visual energy
- Key detection — future-proofing for multi-track mixing

### Video structure (per clip)
Three acts, hard-coded durations matching current clip lengths:
```
ACT 1 — SHOCK:      0–3s   → opening frame (from Module 2)
ACT 2 — ESCALATION: 3–8s   → beat-sync cut to performance/crowd energy
ACT 3 — PAYOFF:     8–15s  → drop/peak + logo/track title fade
```

### Hook overlay (updated)
- Text appears at **0.5s**, not frame 0 — viewer sees visual first, text amplifies
- Two Claude CLI calls per clip (same as now, unchanged architecture):
  - **Call 1** (temp 1.0): 5 hook candidates, A/B/C variants per hook mechanism
  - **Call 2** (temp 0.4): Platform-specific captions using winning hooks
- Hook injected with today's `prompt_weights.json` so generation biases toward what's been working

### Platform-specific rendering
Each clip rendered 3 times with platform-specific settings:

| Setting | TikTok | Instagram Reels | YouTube Shorts |
|---|---|---|---|
| Font size | 72pt | 58pt | 64pt |
| Color grade | High contrast, punchy | Warm, refined | Neutral |
| Cut-in | Motion blur transition | Clean cut | Static first frame |
| Text position | Upper third | Center | Upper third |
| Safe zone padding | 15% bottom | 20% bottom | 15% bottom |

### Variant A vs B
Same video, two hook texts + two captions generated per clip. Variant assignment per platform:
- Clip 1: TikTok→A, Instagram→B, YouTube→A
- Clip 2: TikTok→B, Instagram→A, YouTube→B  
- Clip 3: TikTok→A, Instagram→B, YouTube→A
- Rotates weekly so each platform sees balanced variant exposure

### Output
`content/output/YYYY-MM-DD_HHMM_trackname/` — 9 .mp4 files (3 clips × 3 platforms) + metadata JSON

---

## Module 4: Distribution Layer (upgraded)

**Agent:** `holy-rave-daily-run` (handles posting)  
**Runs:** Daily 09:00 CET (staggered by platform)

### Upload stack — native APIs first
1. **Instagram Graph API** — direct Reels upload (better algorithm treatment than Buffer)
2. **TikTok for Developers API** — direct upload
3. **YouTube Data API v3** — direct Shorts upload
4. **Buffer GraphQL API** — fallback only if any native API fails

### Posting schedule (peak engagement windows, CET)
| Clip | TikTok | Instagram Reels | YouTube Shorts |
|---|---|---|---|
| Clip 1 | 09:00 | 09:00 | 10:00 |
| Clip 2 | 12:00 | 11:00 | 13:00 |
| Clip 3 | 19:00 | 19:00 | 20:00 |

### Video hosting for API uploads
- Cloudinary (primary, if `CLOUDINARY_URL` set) — permanent URLs
- Catbox.moe (fallback) — permanent, 200MB limit
- Local file path for native API direct upload where supported

### Metadata per post
- Caption (platform-specific, from Assembly Pipeline)
- Hashtags (generated by Claude CLI, biased by today's trend brief)
- Track name + Spotify link in description (YouTube + TikTok bio link)
- Location tag: Tenerife (where applicable)

---

## Module 5: Learning Loop

**Agent:** `holy-rave-learning-loop` (new)  
**Runs:** Daily 18:00 CET (9 hours post-morning posts)  
**Input:** Instagram Graph API, TikTok Analytics API, YouTube Analytics API

### Metrics collected per post
- `completion_rate`: % who watched to end
- `scroll_stop_rate`: views ÷ impressions (how many stopped vs. swiped)
- `share_rate`: shares ÷ views
- `save_rate`: saves ÷ views (strongest signal — means they want to return)
- `comment_rate`: comments ÷ views

### What gets scored
| Dimension | Tracked values |
|---|---|
| Hook mechanism | tension / identity / scene / claim / rupture |
| Visual type | ai_generated / performance / b_roll / phone |
| Trend alignment | matched today's brief / did not match |
| Clip length | 5s / 9s / 15s |
| Variant | A / B |
| Platform | TikTok / Instagram / YouTube |

### Output: `prompt_weights.json`
Updated daily. Read by Module 2 (Visual Shock Engine) and Module 3 (Assembly Pipeline) every morning.

```json
{
  "hook_weights": {
    "tension": 1.4,
    "identity": 1.1,
    "scene": 0.9,
    "claim": 0.8,
    "rupture": 1.2
  },
  "visual_weights": {
    "ai_generated": 1.3,
    "performance": 1.1,
    "b_roll": 0.7,
    "phone": 0.9
  },
  "best_clip_length": 15,
  "best_platform": "instagram",
  "updated": "2026-04-12T18:00:00"
}
```

### Outlier detection
Any video hitting 2× rolling average → Claude CLI deep analysis → writes `learning/breakthroughs/YYYY-MM-DD.md` → injected into next morning's generation prompts as a "what worked" context block.

### Rolling averages stored in
`data/performance/rolling_averages.json` — 7-day and 30-day windows per metric per platform.

---

## Agent Fleet

| Agent | Status | Cadence | Responsibility |
|---|---|---|---|
| `holy-rave-trend-scanner` | NEW | Daily 06:00 CET | Trend intelligence, Today's Brief |
| `holy-rave-visual-engine` | NEW | Daily 07:00 CET | Footage scoring, AI generation |
| `holy-rave-daily-run` | REFACTORED | Daily 08:00–09:00 CET | Assembly + distribution |
| `holy-rave-learning-loop` | NEW | Daily 18:00 CET | Performance ingestion, weight updates |
| `rjm-master` | EXISTING | 8×/day | Health checks, gap detection, escalation |
| `holy-rave-weekly-report` | EXISTING | Weekly | Analytics rollup |

---

## New Files & Modules

| File | Purpose |
|---|---|
| `content_engine/trend_scanner.py` | TikTok/IG scraping + Spotify for Artists scraping + Claude CLI analysis |
| `content_engine/footage_scorer.py` | Semantic scoring of existing video library |
| `content_engine/visual_engine.py` | AI generation orchestration (Runway ML) |
| `content_engine/assembler.py` | Refactored pipeline (visual-first, librosa beat detection) |
| `content_engine/distributor.py` | Native API uploads + Buffer fallback |
| `content_engine/learning_loop.py` | Performance ingestion + weight calculation |
| `data/trend_brief/` | Daily trend briefs (JSON) |
| `data/opening_frames/` | Generated/selected opening frames |
| `data/performance/` | Per-post metrics + rolling averages |
| `learning/` | Breakthrough analyses + learning logs |
| `prompt_weights.json` | Live prompt weights updated by learning loop |
| `agents/holy-rave-trend-scanner.md` | New agent definition |
| `agents/holy-rave-visual-engine.md` | New agent definition |
| `agents/holy-rave-learning-loop.md` | New agent definition |

---

## Environment Variables Required

| Variable | Purpose |
|---|---|
| `RUNWAY_API_KEY` | Runway ML video generation |
| `SPOTIFY_SESSION_COOKIE` | Spotify for Artists dashboard scraping |
| `INSTAGRAM_ACCESS_TOKEN` | Graph API — posting + analytics |
| `TIKTOK_ACCESS_TOKEN` | TikTok for Developers — posting + analytics |
| `YOUTUBE_API_KEY` | YouTube Data API v3 — posting + analytics |
| `CLOUDINARY_URL` | Video hosting (existing) |
| `BUFFER_API_KEY` | Buffer fallback (existing) |

---

## Cost Estimate (monthly)

| Service | Cost |
|---|---|
| Runway ML AI video generation (~1.5 clips/day avg) | ~$15 |
| Spotify for Artists scraping (session-based, no API cost) | $0 |
| Claude CLI (Pro Max plan covers all calls) | $0 |
| Native platform APIs (all free) | $0 |
| **Total new spend** | **~$15/month** |

---

## Success Metrics

| Metric | Current | 30-day target | 90-day target |
|---|---|---|---|
| Avg views/video | 1,000 | 25,000 | 500,000 |
| Avg completion rate | 2–3% | 20% | 40% |
| Avg watch time | 2–4s | 8s | 12s+ |
| Spotify monthly listeners | baseline | +15% | +100% |

---

## Out of Scope

- WhatsApp community notifications
- Paid advertising / boosting
- Manual content review or approval
- Story-specific content (Stories remain as-is via Buffer)
- Audio mixing / new track production
