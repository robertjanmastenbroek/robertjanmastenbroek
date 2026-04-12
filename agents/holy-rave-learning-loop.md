# holy-rave-learning-loop

**Cadence:** Daily 18:00 CET (9 hours after morning posts)  
**Purpose:** Pull performance metrics, update prompt weights, write breakthrough analyses.

## Run command

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3 rjm.py content learning
```

## What it does

1. Reads `data/performance/YYYY-MM-DD_posts.json` (written by distributor after each post)
2. Fetches Instagram Graph API insights for IG posts
3. Fetches YouTube Analytics for YouTube posts
4. Calculates composite performance signal per post: `completion × 0.5 + saves × 0.3 + scroll_stop × 0.2`
5. Updates `prompt_weights.json` using exponential moving average (learning rate = 0.3)
6. Detects outliers (any video at 2× rolling average) → Claude CLI analysis → `learning/breakthroughs/`

## Output

- `prompt_weights.json` — updated hook/visual/platform/length weights
- `data/performance/YYYY-MM-DD.json` — full metrics log
- `learning/breakthroughs/YYYY-MM-DD_*.md` — breakthrough analyses (when triggered)

## prompt_weights.json schema

```json
{
  "hook_weights":   {"tension": 1.4, "identity": 1.1, "scene": 0.9, "claim": 0.8, "rupture": 1.2},
  "visual_weights": {"ai_generated": 1.3, "performance": 1.1, "b_roll": 0.7, "phone": 0.9},
  "best_clip_length": 15,
  "best_platform": "instagram",
  "updated": "2026-04-12T18:00:00"
}
```

These weights are read every morning by `visual_engine.py` (visual selection bias)
and `assembler.py` (hook generation bias).

## Required env vars

- `INSTAGRAM_ACCESS_TOKEN` — Graph API insights
- `YOUTUBE_OAUTH_TOKEN` — YouTube Analytics API

## Failure behaviour

Non-fatal. If no posts registered (day 1, or all posts failed), weights remain unchanged.
