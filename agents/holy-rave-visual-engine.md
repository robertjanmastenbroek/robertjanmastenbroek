# holy-rave-visual-engine

**Cadence:** Runs internally as part of the 08:00 assembly (not a standalone cron)  
**Purpose:** Select or generate the scroll-stopping opening frame for each of the 3 daily clips.

## How it decides

For each clip:
1. Score every video in `content/videos/` with composite score (motion + contrast + freshness + emotion match)
2. If best score ≥ 7.0 → use that footage clip as opening frame
3. If best score < 7.0 → generate a 5-second AI clip via Runway ML Gen-4 Turbo

## AI generation

- **Model:** Runway ML Gen-4 Turbo (text-to-video)
- **Aspect ratio:** 768:1344 (9:16 vertical)
- **Duration:** 5 seconds
- **Prompt formula:** `{dominant_emotion} + {visual_format} + {brand formula: ancient visual language fused with electronic energy}`
- **Cost:** ~$0.50/day on average (1-2 generations when footage library scores low)

## Required env vars

- `RUNWAY_API_KEY` — Runway ML API key
- `CLOUDINARY_URL` — for hosting generated clips (also used by distributor)

## Footage scoring weights

| Signal | Weight |
|--------|--------|
| Motion velocity (first 3s) | 30% |
| Visual contrast | 25% |
| Freshness (days since last use) | 25% |
| Emotion match (Claude CLI) | 20% |

## Files touched

- `data/opening_frames/YYYY-MM-DD/ai_clip_{n}.mp4` — AI-generated clips
- `video_rotation.json` — updated when footage is selected
