# holy-rave-trend-scanner

**Cadence:** Daily 06:00 CET  
**Purpose:** Scan YouTube trending music + Spotify featured playlists. Synthesize Today's Brief via Claude CLI. Output feeds the 08:00 assembly run.

## Run command

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3 rjm.py content trend-scan
```

## What it does

1. Calls YouTube Data API v3 for trending music videos (category 10, US region)
2. Calls Spotify Web API for featured playlists (mood/energy signals)
3. Passes all data to Claude CLI (Haiku) for synthesis
4. Outputs `data/trend_brief/YYYY-MM-DD.json`

## Output schema

```json
{
  "date": "2026-04-12",
  "top_visual_formats": ["crowd ecstasy", "sacred geometry", "aerial rave"],
  "dominant_emotion": "euphoric release",
  "oversaturated": "lo-fi chill — avoid",
  "hook_pattern_of_day": "open with contrast, resolve with drop",
  "contrarian_gap": "silence as tension — nobody doing this in techno",
  "trend_confidence": 0.82
}
```

## Required env vars

- `YOUTUBE_API_KEY` — YouTube Data API v3 (optional; falls back to Claude general knowledge)
- `SPOTIFY_ACCESS_TOKEN` — Spotify Web API token (optional; same fallback)

## Failure behaviour

Non-fatal. If scraping fails, Claude synthesizes from general music trend knowledge.
The 08:00 assembly run will still proceed — it loads the brief or generates one on the fly.
