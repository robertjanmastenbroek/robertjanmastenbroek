# Robert-Jan Mastenbroek — Command Centre

## Identity
Dutch DJ/producer, 36, Tenerife. Melodic Techno + Tribal Psytrance. 290K IG (@holyraveofficial). 30+ original tracks, independently owned. No label, no manager.

- Spotify artist ID: `2Seaafm5k1hAuCkpdq7yds`
- Gmail: motomotosings@gmail.com
- Brand: "Ancient Truth. Future Sound."

## North Star
**1,000,000 Spotify monthly listeners.** Every deliverable — content, email, caption, agent run — is evaluated against this goal. Always ask: does this drive streams?

## Subtle Salt (Non-Negotiable)
All content, emails, and agents are rooted in Jesus Christ's teachings. Biblical references woven in subtly — present and flavoring everything, never preachy (Matt 5:13). Those who know the Word recognize it; others simply feel it. Never compromise the message for reach.

## Brand Voice
Full brand voice document: `BRAND_VOICE.md` — read it before generating any content, caption, bio, or email.

Summary rules — apply to every output:
Every caption, email, and output must pass all 5 tests:

1. **Visualization Test** — Can the reader *see* the words? ("The dust on the synth" > "The legacy of the music")
2. **Falsifiability Test** — Facts over adjectives. ("126 BPM tribal rhythm" > "really cool fast beat")
3. **Uniqueness Rule** — Could a competitor sign their name to this? If yes, rewrite.
4. **One Mississippi Test** — Value prop understood in under 2 seconds.
5. **Point A→B** — Bridge secular/searching fan to experiencing sacred energy.

Tone: modern, professional, cool, credible. Never preachy. Lead with music; the story surfaces naturally.

## Visual Identity
Dark, Holy, Futuristic. References: Anyma (visual scale), Rüfüs Du Sol (mood/depth), Argy (tribal/techno texture). Sacred geometry aesthetics. High contrast, centered subjects. Visual objects over abstract concepts (a candle in a dark club > "spiritual").

## Holy Rave Brand
"Holy Rave" = RJM's live show and tour brand. Not tied to a church venue or sober crowd. Wherever RJM plays, the rave becomes holy. "Selah" is reserved for a venue/residency/EP only — Holy Rave has equity (1.9M IG views, 500K TikTok). Viral formula: real footage of people dancing + contrast hook in text overlay + track as audio + under 40s + location tag.

## Track Catalogue
| Track | BPM | Style | Scripture anchor |
|-------|-----|-------|-----------------|
| Renamed | — | — | Isaiah 62 |
| Halleluyah | 140 | Psytrance | — |
| Jericho | 140 | Psytrance | Joshua 6 |
| Fire In Our Hands | 130 | — | — |
| Living Water | 124 | — | John 4 |
| He Is The Light | — | — | John 8 |

When promoting a specific track, find its scripture anchor and lead with it — subtly.

## Autonomous Agent Fleet
| Agent | Cadence | Purpose |
|-------|---------|---------|
| `rjm-master` | 8×/day | Orchestrates all growth activity — never idles |
| `holy-rave-daily-run` | Daily | 3 beat-synced clips → TikTok/IG/YouTube |
| `holy-rave-weekly-report` | Weekly | Analytics — Spotify KPI primary, Buffer secondary |
| `rjm-discover` | 6×/day | Curator + podcast discovery (25 contacts/run) |
| `rjm-outreach-agent` | Every 30min | Email outreach via Gmail OAuth |
| `rjm-research` | 6×/day | Contact personalisation |
| `rjm-playlist-discover` | 6×/day | Build 200-playlist database |

Outreach limits: max 150 emails/day, active window 08:00–23:00 CET, 8hr overnight break.

## Email Reply Authorization (Standing, No Confirmation Needed)
- **Curator replies:** thank + send track link + ask if it fits their playlist
- **Podcast replies:** book the interview — offer 3 slots Tue/Wed/Thu 11:00–17:00 CET, mention Tenerife timezone, offer press kit
- Podcast bookings → Google Calendar with 1-hour warning

## Priority Order (when tasks conflict)
1. Content publishing (daily clips + captions — feeds algorithm daily)
2. Outreach replies (warm leads — time-sensitive)
3. Discover new contacts (pipeline fuel)
4. Research + personalisation (quality multiplier)
5. Analytics + reporting (weekly pulse)

## Key Files
| File | Purpose |
|------|---------|
| `BRAND_DNA.md` | Canonical brand rules, story, platform tactics |
| `outreach_agent/agent.py` | Main outreach agent |
| `outreach_agent/config.py` | Email settings, limits, timing |
| `outreach_agent/post_today.py` | Daily content post script |
| `contacts.csv` | 84 contacts (labels, curators) |
| `content/audio/` | Source audio for clips |
| `content/videos/` | Source video footage |
| `content/output/` | Rendered clips |
| `data/listeners.json` | Spotify listener tracking |
| `SMYKM_Framework.md` | Growth strategy framework |

## Context Navigation
Use `graphify-out/wiki/index.md` to navigate this codebase before reading raw files.
Graph: 650 nodes · 889 edges · 109 communities · 149x token reduction vs reading raw files.
