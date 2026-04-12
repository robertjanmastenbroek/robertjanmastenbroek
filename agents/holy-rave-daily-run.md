# Agent: holy-rave-daily-run

**Cadence:** Daily (orchestrates 3 timed runs)  
**Role:** Orchestrate the full viral shorts pipeline — 9 posts/day across TikTok / IG / YouTube

## Daily Schedule

| Time (CET) | Command | What happens |
|------------|---------|--------------|
| 06:00 | `python3 rjm.py content trend-scan` | Trend Scanner scrapes YouTube + Spotify, synthesizes Today's Brief |
| 08:00 | `python3 rjm.py content viral` | Assembles 9 clips (3 clips × 3 platforms), distributes via native APIs |
| 18:00 | `python3 rjm.py content learning` | Pulls 9hr performance data, updates prompt_weights.json |

## Pipeline Overview

```
06:00  Trend Scanner     → data/trend_brief/YYYY-MM-DD.json
07:00  Visual Engine     → opening frames (footage scored or Runway ML generated)
08:00  Assembler         → 9 clips (visual-first, platform-specific rendering)
09:00  Distributor       → posted to TikTok / IG Reels / YouTube Shorts
18:00  Learning Loop     → prompt_weights.json updated for tomorrow
```

## Dry Run (test without posting)

```bash
python3 rjm.py content viral --dry-run
```

## Success Criteria

- `data/performance/YYYY-MM-DD_posts.json` contains ≥ 6 entries (at least 6 of 9 posted)
- `prompt_weights.json` timestamp updated by 18:30
- No unhandled exceptions in logs

## North Star Check

Every clip must be plausibly shareable by a secular rave attendee who has never been to church.  
Every post drives toward 1,000,000 Spotify monthly listeners.

## Legacy Pipeline (fallback)

The original 3-clip Buffer pipeline is still available:
```bash
python3 rjm.py content --dry-run   # legacy mode
```
