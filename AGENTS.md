# RJM Command Centre — Agent Roster

## North Star
1,000,000 Spotify monthly listeners. Every agent action is evaluated against this goal.

## Identity
Robert-Jan Mastenbroek — Dutch DJ/producer, 36, Tenerife. Melodic Techno + Tribal Psytrance.
Spotify: 2Seaafm5k1hAuCkpdq7yds | Instagram: @holyraveofficial (290K)

## Swarm Topology (from ruflo/config/rjm-swarm.json)
- **Queen**: rjm-master — orchestrates all growth activity 8×/day
- **Workers** (priority order):
  1. holy-rave-daily-run — 3 beat-synced clips → TikTok/IG/YouTube (daily)
  2. rjm-outreach-agent — email outreach via Gmail OAuth (every 30min)
  3. rjm-discover — contacts + Spotify playlists (6×/day, merged pipeline)
  4. rjm-research — contact personalisation (6×/day)
  5. holy-rave-weekly-report — Spotify KPI analytics (weekly)

## Pipeline
discover → enrich → publish → report

## Agent Definitions
All agent markdown files live in `/agents/` directory.
Entry point: `python3 rjm.py <command>`

## Brand Rules
- Brand voice: BRAND_VOICE.md (all 5 tests required)
- Subtle salt: biblical references woven in, never preachy (Matt 5:13)
- Banned words: blessed, anointed, curated, authentic, vibration, energy, intentional, journey

## Memory Keys (shared across agents)
- contacts_db — outreach_agent/outreach.db
- playlist_database — data/playlists.json
- master_log — data/master_log.json
- template_performance — data/template_performance.json

## Outreach Limits
Max 150 emails/day | Active window 08:00–23:00 CET | 8hr overnight break
