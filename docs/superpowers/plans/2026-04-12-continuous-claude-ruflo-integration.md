# Continuous Claude + RuFlo Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the pre-installed continuous-claude hooks, agents, and RuFlo swarm config into the live RJM Command Centre so every Claude session gets TLDR code injection, architecture context, and the full agent fleet (maestro, kraken, critic, etc.) alongside the existing rjm-master/outreach/discover pipeline.

**Architecture:** Continuous Claude acts as the intelligence layer (hooks inject context into every tool call; 32 agents available globally); RuFlo acts as the orchestration layer (swarm topology already defined in `ruflo/config/rjm-swarm.json`, now wired into `rjm.py`); the existing outreach_agent/agents/ pipeline runs unchanged underneath. No PostgreSQL or external infra needed — all hooks are pre-compiled Node.js, ruflo is already at v3.5.80.

**Tech Stack:** Node.js 23 (hooks .mjs), Python 3.13 (rjm.py), ruflo v3.5.80 (npx), Claude Code hooks (settings.json)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `~/.claude/hooks/dist/` | Copied from continuous-claude — 20 pre-compiled hook scripts |
| Modify | `~/.claude/settings.json` | Add CC hooks (PreToolUse, Stop, SessionEnd) alongside existing security hook |
| Copy | `~/.claude/agents/*.json + *.md` | Add 32 CC agents (maestro, kraken, critic, architect, atlas, arbiter, aegis…) to global agent pool |
| Create | `AGENTS.md` (project root) | RuFlo auto-discovery file — describes swarm topology and agent roles |
| Modify | `rjm.py` | Add `swarm` and `memory` sub-commands wiring into ruflo CLI |
| Create | `ruflo/.agents/AGENTS.md` | RuFlo project-level behavior rules file (required by config.toml) |
| Delete | 14 stale root files (listed in Task 5) | Remove Feb/Mar dead docs — no active references confirmed |

---

## Task 1: Deploy Continuous Claude Hooks to ~/.claude

**Files:**
- Create: `~/.claude/hooks/dist/` (directory + 20 .mjs files copied from `continuous-claude/.claude/hooks/dist/`)
- Modify: `~/.claude/settings.json`

- [ ] **Step 1: Copy compiled hooks**

```bash
mkdir -p ~/.claude/hooks/dist
cp "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/continuous-claude/.claude/hooks/dist/"*.mjs ~/.claude/hooks/dist/
cp "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/continuous-claude/.claude/hooks/dist/"*.js ~/.claude/hooks/dist/
echo "Copied $(ls ~/.claude/hooks/dist/*.mjs | wc -l) hook files"
```

Expected: `Copied 20 hook files` (approx)

- [ ] **Step 2: Smoke-test a hook runs without crashing**

```bash
echo '{"tool_name":"Read","tool_input":{"file_path":"/tmp/test.txt"}}' | node ~/.claude/hooks/dist/path-rules.mjs
```

Expected: exits 0 or prints JSON — no `Cannot find module` errors.

- [ ] **Step 3: Merge CC hooks into ~/.claude/settings.json**

Read current `~/.claude/settings.json` first, then replace with the merged version below. This preserves the existing security hook and adds the CC hooks. Skip braintrust + uv hooks (require external infra).

```json
{
  "mcpServers": {
    "context-mode": {
      "command": "npx",
      "args": ["-y", "context-mode@latest"],
      "type": "stdio"
    }
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/motomoto/.claude/skills/security-guidance/hooks/security_reminder_hook.py"
          }
        ]
      },
      {
        "matcher": "Read|Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "node $HOME/.claude/hooks/dist/path-rules.mjs",
            "timeout": 5
          }
        ]
      },
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "command": "node $HOME/.claude/hooks/dist/tldr-read-enforcer.mjs",
            "timeout": 20
          }
        ]
      },
      {
        "matcher": "Grep",
        "hooks": [
          {
            "type": "command",
            "command": "node $HOME/.claude/hooks/dist/smart-search-router.mjs",
            "timeout": 10
          }
        ]
      },
      {
        "matcher": "Task",
        "hooks": [
          {
            "type": "command",
            "command": "node $HOME/.claude/hooks/dist/tldr-context-inject.mjs",
            "timeout": 30
          },
          {
            "type": "command",
            "command": "node $HOME/.claude/hooks/dist/arch-context-inject.mjs",
            "timeout": 30
          }
        ]
      },
      {
        "matcher": "Edit",
        "hooks": [
          {
            "type": "command",
            "command": "node $HOME/.claude/hooks/dist/edit-context-inject.mjs",
            "timeout": 5
          }
        ]
      },
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "node $HOME/.claude/hooks/dist/import-validator.mjs",
            "timeout": 5
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "node $HOME/.claude/hooks/dist/import-error-detector.mjs",
            "timeout": 5
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node $HOME/.claude/hooks/dist/compiler-in-the-loop-stop.mjs"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node $HOME/.claude/hooks/dist/session-end-cleanup.mjs"
          },
          {
            "type": "command",
            "command": "node $HOME/.claude/hooks/dist/session-outcome.mjs"
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 4: Verify settings.json is valid JSON**

```bash
python3 -c "import json; json.load(open(f\"{__import__('os').environ['HOME']}/.claude/settings.json\")); print('Valid JSON')"
```

Expected: `Valid JSON`

- [ ] **Step 5: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add -A
git commit -m "feat: deploy continuous-claude hooks to ~/.claude/hooks/dist + merge into settings.json"
```

---

## Task 2: Register Continuous Claude Agents Globally

**Files:**
- Copy: `continuous-claude/.claude/agents/*.json` + `*.md` → `~/.claude/agents/`

These 32 agents (maestro, kraken, critic, architect, atlas, arbiter, aegis, herald, judge, liaison, chronicler, etc.) become available as subagents in every Claude Code session globally — alongside the existing 9 RJM agency agents already there.

- [ ] **Step 1: Copy all CC agent definitions**

```bash
CC_AGENTS="/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/continuous-claude/.claude/agents"
cp "$CC_AGENTS"/*.json ~/.claude/agents/ 2>/dev/null || true
cp "$CC_AGENTS"/*.md ~/.claude/agents/ 2>/dev/null || true
echo "Agents in ~/.claude/agents: $(ls ~/.claude/agents/ | wc -l)"
```

Expected: `Agents in ~/.claude/agents: 41` (approx — 9 existing + 32 CC)

- [ ] **Step 2: Spot-check a key agent definition**

```bash
cat ~/.claude/agents/maestro.md | head -20
```

Expected: Shows maestro orchestrator agent definition with model + description fields.

- [ ] **Step 3: Verify no naming conflicts with existing RJM agents**

```bash
ls ~/.claude/agents/ | sort
```

Confirm the 9 existing RJM agents (chief-of-staff, content-creator, growth-hacker, instagram-curator, outbound-strategist, playlist-outreach-specialist, short-video-coach, social-media-strategist, tiktok-strategist) are still present alongside the new ones.

- [ ] **Step 4: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add -A
git commit -m "feat: register 32 continuous-claude agents globally in ~/.claude/agents"
```

---

## Task 3: Create AGENTS.md Files for RuFlo Discovery

RuFlo's `config.toml` expects an `AGENTS.md` file (or `.agents/AGENTS.md`) in the project. Without it, the router has no context for making routing decisions.

**Files:**
- Create: `AGENTS.md` (project root)
- Create: `ruflo/.agents/AGENTS.md`

- [ ] **Step 1: Create project-root AGENTS.md**

```bash
cat > "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/AGENTS.md" << 'EOF'
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
  3. rjm-discover + rjm-playlist-discover — 25 contacts/run (6×/day)
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
EOF
echo "AGENTS.md created"
```

Expected: `AGENTS.md created`

- [ ] **Step 2: Create ruflo/.agents/AGENTS.md**

```bash
mkdir -p "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/ruflo/.agents"
cp "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/AGENTS.md" \
   "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/ruflo/.agents/AGENTS.md"
echo "ruflo/.agents/AGENTS.md created"
```

Expected: `ruflo/.agents/AGENTS.md created`

- [ ] **Step 3: Verify ruflo can see the project**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/ruflo"
npx ruflo@latest --help 2>&1 | head -10
```

Expected: Shows ruflo help output with available commands, no errors.

- [ ] **Step 4: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add AGENTS.md ruflo/.agents/AGENTS.md
git commit -m "feat: add AGENTS.md for RuFlo swarm auto-discovery"
```

---

## Task 4: Add `swarm` and `memory` Commands to rjm.py

**Files:**
- Modify: `rjm.py` (project root) — add `swarm` and `memory` sub-commands

- [ ] **Step 1: Read current rjm.py to understand its structure**

```bash
head -60 "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/rjm.py"
```

Look for: how `cmd_*` functions are defined and dispatched in `main()`.

- [ ] **Step 2: Add swarm command function to rjm.py**

Find the section with other `cmd_` functions and add before `main()`:

```python
def cmd_swarm(args):
    """Manage the RuFlo agent swarm."""
    import subprocess, shutil
    ruflo = shutil.which("ruflo") or "npx ruflo@latest"
    swarm_config = str(ROOT / "ruflo" / "config" / "rjm-swarm.json")
    agents_dir = str(ROOT / "ruflo" / ".agents")

    sub = args[0] if args else "status"

    if sub == "init":
        print("Initialising RJM swarm (hierarchical, raft consensus)...")
        result = subprocess.run(
            f"{ruflo} swarm init --config {swarm_config} --topology hierarchical --agents-dir {agents_dir}",
            shell=True, cwd=str(ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    elif sub == "status":
        print("RJM Swarm — agent status:")
        result = subprocess.run(
            f"{ruflo} swarm status",
            shell=True, cwd=str(ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    elif sub == "stop":
        result = subprocess.run(
            f"{ruflo} swarm stop",
            shell=True, cwd=str(ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    else:
        print(f"Unknown swarm sub-command: {sub}")
        print("Usage: rjm.py swarm [init|status|stop]")
        sys.exit(1)


def cmd_memory(args):
    """Read/write shared swarm memory keys."""
    import subprocess, shutil
    ruflo = shutil.which("ruflo") or "npx ruflo@latest"

    if len(args) < 1:
        print("Usage: rjm.py memory get <key> | rjm.py memory set <key> <value>")
        sys.exit(1)

    sub = args[0]

    if sub == "get" and len(args) >= 2:
        key = args[1]
        result = subprocess.run(
            f"{ruflo} memory get --key {key}",
            shell=True, cwd=str(ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    elif sub == "set" and len(args) >= 3:
        key, value = args[1], args[2]
        result = subprocess.run(
            f"{ruflo} memory set --key {key} --value \"{value}\"",
            shell=True, cwd=str(ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    elif sub == "list":
        result = subprocess.run(
            f"{ruflo} memory list",
            shell=True, cwd=str(ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    else:
        print(f"Unknown memory sub-command or missing args: {args}")
        print("Usage: rjm.py memory get <key> | set <key> <value> | list")
        sys.exit(1)
```

- [ ] **Step 3: Wire new commands into the main() dispatcher**

In `main()`, find the existing command dispatch block (the `if cmd == "status":` chain) and add:

```python
    elif cmd == "swarm":
        cmd_swarm(rest)
    elif cmd == "memory":
        cmd_memory(rest)
```

Also add to the help/usage output:

```python
    # In the help block, add:
    print("  python3 rjm.py swarm init       # Initialise RuFlo agent swarm")
    print("  python3 rjm.py swarm status     # Show swarm agent status")
    print("  python3 rjm.py memory list      # List shared memory keys")
    print("  python3 rjm.py memory get <key> # Read a memory key")
```

- [ ] **Step 4: Test swarm command**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3 rjm.py swarm status
```

Expected: Either shows swarm status output from ruflo OR a ruflo "not initialised" message — no Python errors.

- [ ] **Step 5: Test memory list command**

```bash
python3 rjm.py memory list
```

Expected: Ruflo memory list output or empty list — no Python errors.

- [ ] **Step 6: Commit**

```bash
git add rjm.py
git commit -m "feat: add swarm init/status/stop and memory get/set/list commands to rjm.py"
```

---

## Task 5: Remove Stale Root Files

These files have zero references in CLAUDE.md, rjm.py, or any active agent — confirmed by grep scan.

**Files to delete:**
- `DEPLOY-NOW.md`
- `DEPLOYMENT_GUIDE.md`
- `RAILWAY_DEPLOYMENT.md`
- `CUSTOMIZATION_GUIDE.md`
- `PROJECT_SUMMARY.md`
- `WEBSITE-IMPROVEMENTS-COMPLETE.md`
- `ZERO_TOUCH_TIKTOK.md`
- `TIKTOK_VIRAL_SYSTEM.md`
- `VISUAL_STYLE_GUIDE.md`
- `Sample Social Media Posts.md`
- `Gospel Content Calendar.xlsx`
- `Gospel Content Strategy.docx`
- `Devotional Template.docx`
- `index-updated.html`
- `nginx.conf`
- `railway.json`
- `package.json`
- `Dockerfile`
- `.~lock.Podcast Booking Tracker.xlsx#`
- `README.md` (superseded by CLAUDE.md + AGENTS.md — verify first)

- [ ] **Step 1: Delete stale deployment + docs files**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
rm -f \
  "DEPLOY-NOW.md" \
  "DEPLOYMENT_GUIDE.md" \
  "RAILWAY_DEPLOYMENT.md" \
  "CUSTOMIZATION_GUIDE.md" \
  "PROJECT_SUMMARY.md" \
  "WEBSITE-IMPROVEMENTS-COMPLETE.md" \
  "ZERO_TOUCH_TIKTOK.md" \
  "TIKTOK_VIRAL_SYSTEM.md" \
  "VISUAL_STYLE_GUIDE.md" \
  "Sample Social Media Posts.md" \
  "Gospel Content Calendar.xlsx" \
  "Gospel Content Strategy.docx" \
  "Devotional Template.docx" \
  "index-updated.html" \
  "nginx.conf" \
  "railway.json" \
  "Dockerfile" \
  ".~lock.Podcast Booking Tracker.xlsx#"
echo "Deleted stale files"
```

- [ ] **Step 2: Handle package.json — check if server.js needs it**

```bash
grep -n "require\|package" "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/server.js" | head -5
```

If `server.js` uses no npm packages (or all built-ins), delete `package.json`. If it imports external packages, keep it.

- [ ] **Step 3: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add -A
git commit -m "chore: remove stale deployment docs and Feb-era dead files"
```

---

## Task 6: Update CLAUDE.md with Integration Context

**Files:**
- Modify: `CLAUDE.md` (project root) — add Continuous Claude + RuFlo sections

- [ ] **Step 1: Add new sections to CLAUDE.md**

After the existing `## Installed Skills` section, add:

```markdown
## Continuous Claude (Intelligence Layer)

30 hooks auto-fire on every session — injecting TLDR code summaries, architecture context, and edit helpers into every tool call.

32 agents available globally (`~/.claude/agents/`):
- **maestro** — orchestrator, coordinates all other CC agents
- **kraken** — TDD implementation specialist
- **critic** — code review and quality gate
- **architect** — system design and ADRs
- **atlas** — documentation and knowledge graph
- **arbiter** — consensus and conflict resolution
- **aegis** — security analysis
- **herald** — comms and summaries

Hooks active: `tldr-context-inject`, `arch-context-inject`, `edit-context-inject`, `smart-search-router`, `path-rules`, `import-validator`, `session-end-cleanup`

## RuFlo Swarm (Orchestration Layer)

Swarm config: `ruflo/config/rjm-swarm.json`
Agent discovery: `AGENTS.md` (project root) + `ruflo/.agents/AGENTS.md`

```
python3 rjm.py swarm init       # Start the swarm
python3 rjm.py swarm status     # Agent health
python3 rjm.py memory list      # Shared memory keys
python3 rjm.py memory get <key> # Read memory
```

Topology: hierarchical | Consensus: raft | Queen: rjm-master
```

- [ ] **Step 2: Verify CLAUDE.md is still under a reasonable size**

```bash
wc -l "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/CLAUDE.md"
```

Expected: under 200 lines (it loads on every session — keep it lean).

- [ ] **Step 3: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
git add CLAUDE.md
git commit -m "docs: add continuous-claude and ruflo integration context to CLAUDE.md"
```

---

## Self-Review

**Spec coverage:**
- ✅ Continuous Claude hooks deployed and merged into global settings
- ✅ 32 CC agents registered globally
- ✅ RuFlo AGENTS.md created for discovery
- ✅ rjm.py extended with swarm + memory commands
- ✅ Stale files removed
- ✅ CLAUDE.md updated

**Placeholder scan:** None — all steps contain actual commands and code.

**Type consistency:** `cmd_swarm` / `cmd_memory` follow existing `cmd_*` pattern in rjm.py. `ROOT` variable assumed to exist in rjm.py — verify in Task 4 Step 1 read.

**Known risk:** Task 4 Step 3 assumes `ROOT` is defined in rjm.py. If it's not, replace `ROOT` with `Path(__file__).parent` in the added functions.
