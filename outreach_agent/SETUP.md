# RJM Autonomous Outreach Agent — Setup Guide

This agent sends personalised emails to labels, curators, festivals, YouTube channels, and podcast hosts — fully automatically. It uses Gmail for sending, Claude (Anthropic) for email generation, and a local SQLite database for tracking.

---

## What it does

| Feature | Detail |
|---|---|
| Email generation | Claude Opus writes each email fresh, personalised to the contact |
| Pre-send validation | DNS bounce check before every send — no wasted emails |
| Rate limiting | Max 150/day, spread across 08:00–23:00, 8-hour overnight break |
| Reply detection | Scans inbox every cycle — marks contacts as responded automatically |
| Auto follow-up | 7 days after initial send, one short warm follow-up in the same thread |
| Bounce recovery | Detects mailer-daemon failures, marks contacts, never retries dead addresses |
| Self-improvement | After every 10 replies, Claude analyses patterns and improves future emails |
| Draft mode | Test everything as Gmail drafts before going live |

---

## Prerequisites

- Python 3.10+
- A Google account (motomotosings@gmail.com)
- Claude Code installed (already running — this IS the email generation brain)

---

## Step 1 — Install dependencies

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
pip install -r requirements.txt
```

---

## Step 2 — Get Google Gmail credentials

1. Go to **[console.cloud.google.com](https://console.cloud.google.com)**
2. Create a new project (e.g. "RJM Outreach Agent")
3. Go to **APIs & Services → Enable APIs** → search for **Gmail API** → Enable it
4. Go to **APIs & Services → Credentials**
5. Click **Create Credentials → OAuth 2.0 Client ID**
6. Application type: **Desktop app** → Name it anything → Create
7. Download the JSON file → **rename it `credentials.json`**
8. Move `credentials.json` into this folder:
   ```
   outreach_agent/credentials.json
   ```

---

## Step 3 — First-time setup

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
python agent.py setup
```

This will:
- Initialise the SQLite database
- Open a browser window for Gmail OAuth consent (sign in as motomotosings@gmail.com)
- Save your token — subsequent runs are silent and automatic

---

## Step 4 — Import existing contacts

```bash
python agent.py import ../contacts.csv
```

Or to migrate everything from the legacy system (including podcast pitches):

```bash
python migrate.py
```

---

## Step 5 — Test in draft mode first

```bash
export RJM_DRAFT_MODE=true
python agent.py run
```

This creates **Gmail drafts** instead of sending — check them in Gmail to verify quality before going live.

---

## Step 6 — Preview an email before sending

```bash
python agent.py preview info@somefestival.com
```

---

## Step 7 — Set up the cron job (automated)

This makes the agent run automatically every 15 minutes:

```bash
crontab -e
```

Add this line:
```
*/15 * * * * cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent" && python agent.py run >> /tmp/rjm_outreach.log 2>&1
```

The agent will automatically:
- Only send between 08:00–23:00
- Respect the 150/day cap
- Check for replies and follow-ups
- Improve its emails as replies come in

---

## Daily commands

```bash
# Check pipeline status
python agent.py status

# See full performance report
python agent.py report

# Add a contact manually
python agent.py add

# Preview an email without sending
python agent.py preview email@domain.com

# Run follow-ups now (without waiting for cron)
python agent.py followups

# Check the queue
python agent.py queue
```

---

## Adding contacts

### Manually (one at a time)
```bash
python agent.py add
```

### From a CSV or text file
```bash
python agent.py import my_contacts.csv
```

CSV format (any delimiter):
```
email,name,type,genre,notes
booking@festival.com,Festival Name,festival,Psytrance,2027 lineup open
curator@spotify.com,Playlist Name,curator,Organic House,42K saves
host@podcast.com,Podcast Name,podcast,Music Business,Indie artist episodes
```

Types: `label` | `curator` | `youtube` | `festival` | `podcast`

---

## Contact lifecycle

```
new → verified → sent → followup_sent → responded
           ↓
         bounced (pre-check or actual delivery failure)
```

The agent handles every transition automatically.

---

## Tuning the agent

All settings are in `config.py`:

| Setting | Default | What it does |
|---|---|---|
| `MAX_EMAILS_PER_DAY` | 150 | Hard daily cap |
| `ACTIVE_HOUR_START` | 8 | Window opens (08:00) |
| `ACTIVE_HOUR_END` | 23 | Window closes (23:00) |
| `FOLLOWUP_DAYS` | 7 | Days before follow-up |
| `DRAFT_MODE` | false | `true` = drafts only |
| `BATCH_SIZE` | 5 | Max emails per 15-min cycle |
| `CLAUDE_MODEL` | claude-opus-4-6 | Email generation model |

---

## Files in this directory

| File | Purpose |
|---|---|
| `agent.py` | Main entrypoint — run this |
| `config.py` | All tunable settings |
| `brand_context.py` | **Single source of truth** for brand identity injected into all AI prompts |
| `story.py` | RJM's canonical facts (ARTIST, TRACKS, STORY_BEATS) — imported by brand_context |
| `db.py` | SQLite database layer |
| `bounce.py` | Email pre-validation (DNS, catch-all detection, dead address cache) |
| `gmail_client.py` | Gmail API wrapper |
| `template_engine.py` | Claude email generator (imports from brand_context) |
| `generator.py` | Batch email generator |
| `scheduler.py` | Rate limiter + timing + bounce circuit breaker |
| `reply_detector.py` | Inbox scanning + reply classification trigger |
| `reply_classifier.py` | Claude-powered reply intent classifier (8 intents) |
| `followup_engine.py` | Follow-up logic (day 5 + day 12) |
| `learning.py` | Performance tracking + self-improvement insights |
| `master_agent.py` | Strategic brain — briefing, gaps, health, run sub-agents |
| `migrate.py` | One-time legacy CSV import |
| `outreach.db` | SQLite database (auto-created) |
| `credentials.json` | Google OAuth creds (you provide — never commit) |
| `token.json` | Gmail token (auto-created on first auth — never commit) |
| `drafts/` | Local copies of every sent email |
| `agent.log` | Running log of all activity |

> **Brand identity:** Edit `brand_context.py` (or its source `story.py`) to update facts.
> Changes propagate to all email generation, follow-ups, and briefings automatically.

---

## Security

The **security-guidance** skill is installed globally and fires automatically as a
PreToolUse hook on every Edit/Write operation. It proactively catches dangerous
code patterns before they land — especially relevant for this directory because:

- `template_engine.py`, `generator.py`, `reply_classifier.py` — call Claude CLI via subprocess
- `gmail_client.py` — handles OAuth tokens
- `bounce.py` — makes outbound DNS/HTTP requests

If the hook warns during a code edit, acknowledge and fix the pattern before proceeding.
You do not need to do anything to activate it — it is always on.

---

## Story updates

If Robert-Jan's story changes (new milestones, new tracks, new stats), edit `story.py`. The agent reads this file fresh every cycle — no restart needed.

---

## Important note on volume

Gmail's sending limits for personal accounts are ~500 emails/day. The agent caps at 150 to stay well under this limit and maintain healthy deliverability. Do not increase beyond 200/day on a personal Gmail account.

For higher volume, set up a Google Workspace account with a custom domain and update `FROM_EMAIL` in `config.py`.
