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

**Locked design tokens** (do not change without explicit user approval):
- Dark: `#0a0a0a` · Gold: `#d4af37` · Text: `#ffffff` · Muted: `#a0a0a0`
- Serif: `Cormorant Garamond` · Sans: `Inter`
- No Cinzel, no Spectral, no teal, no constellation/canvas animations, no overflow-text heroes.

**Visual change discipline.** Any edit to `index.html`, `offering/index.html`, `/story`, `/links`, or any other public-facing visual asset MUST:
1. Land in its own dedicated commit — never bundled with content-engine, pipeline, outreach, or "chore" work.
2. Use a commit title that starts with `visual:` or `design:` so it's easy to audit in log.
3. Preserve the locked design tokens above unless the user explicitly requests a palette/font change in writing.

If an agent (rjm-master, frontend-design, or any other) is about to touch these files as part of a larger task, stop and isolate the change first.

## Holy Rave Brand
"Holy Rave" = RJM's live show and tour brand. Not tied to a church venue or sober crowd. Wherever RJM plays, the rave becomes holy. "Selah" is reserved for a venue/residency/EP only — Holy Rave has equity (1.9M IG views, 500K TikTok). Viral formula: real footage of people dancing + contrast hook in text overlay + track as audio + under 40s + location tag.

## Track Catalogue
| Track | BPM | Style | Scripture anchor |
|-------|-----|-------|-----------------|
| Renamed | 128 | Melodic Techno | Isaiah 62 |
| Halleluyah | 140 | Psytrance | — |
| Jericho | 140 | Psytrance | Joshua 6 |
| Fire In Our Hands | 130 | — | — |
| Selah | 130 | Handpan / Oud / Middle Eastern | Psalm 46 |
| Living Water | 124 | — | John 4 |
| He Is The Light | 128 | — | John 8 |

When promoting a specific track, find its scripture anchor and lead with it — subtly.

## Autonomous Agent Fleet
| Agent | Cadence | Purpose |
|-------|---------|---------|
| `rjm-master` | 8×/day | Orchestrates all growth activity — never idles |
| `holy-rave-daily-run` | Daily | 3 beat-synced clips → TikTok/IG/YouTube |
| `holy-rave-weekly-report` | Weekly | Analytics — Spotify KPI primary, Buffer secondary |
| `rjm-discover` | 6×/day | Contact + Spotify playlist discovery (merged pipeline) |
| `rjm-outreach-agent` | Every 30min | Email outreach via Gmail OAuth |
| `rjm-research` | 6×/day | Contact personalisation |

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
| `content_engine/pipeline.py` | Unified daily content pipeline (`rjm.py content viral`) |
| `contacts.csv` | 84 contacts (labels, curators) |
| `content/audio/` | Source audio for clips |
| `content/videos/` | Source video footage |
| `content/output/` | Rendered clips |
| `data/listeners.json` | Spotify listener tracking |
| `SMYKM_Framework.md` | Growth strategy framework |

## Context Navigation
Use `graphify-out/wiki/index.md` to navigate this codebase before reading raw files.
Graph: 650 nodes · 889 edges · 109 communities · 149x token reduction vs reading raw files.

## Installed Skills — When to Use Each

Five skills are installed globally and integrated into this project's workflow.

### superpowers (14 lifecycle workflows)
Trigger each at the right moment — mandatory gates, not suggestions:

| Trigger | When |
|---------|------|
| `/brainstorming` | Before any new feature, agent, or growth strategy |
| `/writing-plans` | Before building anything non-trivial — spec it first |
| `/executing-plans` | When running a plan in an isolated session |
| `/systematic-debugging` | Before proposing ANY fix to outreach_agent/ or the fleet |
| `/test-driven-development` | Before writing implementation code |
| `/verification-before-completion` | Before claiming any task done |
| `/requesting-code-review` | Before merging changes to outreach_agent/ |
| `/using-git-worktrees` | Already in use — all feature work happens in worktrees |

### frontend-design — `/frontend-design`
Use for: `index.html`, `selah.html`, any Holy Rave visual assets or social media UI.
Visual identity: Dark, Holy, Futuristic (Anyma / Rüfüs Du Sol / Argy references).
No generic fonts (no Inter, no Arial). No purple gradients. Sacred geometry aesthetics.

### code-review — `/code-review <PR-number>`
Mandatory after any changes to `outreach_agent/` (Gmail OAuth, bounce logic, rate limiting),
`rjm.py`, or any autonomous agent behaviour. Runs 5 parallel review agents, filters below 80% confidence.
Run from project root: `/code-review 42`

### security-guidance — AUTO (PreToolUse hook, no trigger needed)
Fires on every Edit/Write. Proactively catches dangerous patterns in code before they land.
Especially relevant for `outreach_agent/` — subprocess calls to Claude CLI, Gmail OAuth token
handling, and DNS lookups in bounce.py are all in scope.

### gstack — `/gstack`
Use for QA testing the Holy Rave website before publishing or scheduling Buffer posts.
Screenshots, responsive layout (mobile/desktop), form validation, before/after diffs.
Requires `bun` — install once with: `curl -fsSL https://bun.sh/install | bash`
Then run setup: `~/.claude/skills/gstack/setup`

## Continuous Claude (Intelligence Layer)

Hooks auto-fire on every session — injecting TLDR code summaries, architecture context, and edit helpers.

Active hooks: `tldr-context-inject` (Task), `arch-context-inject` (Task), `edit-context-inject` (Edit), `smart-search-router` (Grep), `path-rules` (Read/Edit/Write), `import-validator` (Edit/Write), `session-end-cleanup` (SessionEnd)

57 agents in `~/.claude/agents/` — 9 RJM brand agents + 48 Continuous Claude agents:
- **maestro** — orchestrator, coordinates other CC agents
- **kraken** — TDD implementation
- **critic** — code review
- **architect** — system design
- **aegis** — security analysis

## RuFlo Swarm (Orchestration Layer)

Swarm config: `ruflo/config/rjm-swarm.json` | Discovery: `AGENTS.md`

```
python3 rjm.py swarm init       # Start the swarm
python3 rjm.py swarm status     # Agent health
python3 rjm.py memory list      # Shared memory keys
python3 rjm.py memory get <key> # Read memory
```

Topology: hierarchical | Consensus: raft | Queen: rjm-master

## Unified Entry Point
All agent commands run through `rjm.py` at the project root:
```
python3 rjm.py status          # system health
python3 rjm.py briefing        # daily priorities
python3 rjm.py outreach run    # fire outreach agent
python3 rjm.py master gaps     # pipeline gaps
python3 rjm.py contacts sync   # CSV → SQLite bridge
python3 rjm.py skills          # show skill trigger reference
```
