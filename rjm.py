#!/usr/bin/env python3
"""
RJM Command Centre — Unified Entry Point

Single dispatcher for the entire agent fleet. Run from the project root.

Usage:
  python3 rjm.py                          # Show this help
  python3 rjm.py status                   # Full system health check
  python3 rjm.py briefing                 # Master agent daily briefing
  python3 rjm.py outreach [cmd]           # Outreach agent (run, status, verify, add, ...)
  python3 rjm.py master [cmd]             # Master agent (dashboard, gaps, weekly, run, ...)
  python3 rjm.py contacts [cmd]           # Contact manager (status, queue, sync, add, ...)
  python3 rjm.py content [--dry-run]      # Holy Rave daily content run (3 clips → Buffer)
  python3 rjm.py playlist [cmd]           # Playlist DB (status, add, pending_contact, list)
  python3 rjm.py spotify [cmd]            # Spotify growth tracker (status, log <n>, history)
  python3 rjm.py run <agent>              # Trigger a sub-agent directly
  python3 rjm.py sync                     # Sync contacts.csv → outreach.db

Examples:
  python3 rjm.py status                   # Is everything running?
  python3 rjm.py briefing                 # What should I focus on today?
  python3 rjm.py outreach run             # Fire the outreach agent now
  python3 rjm.py outreach status          # Outreach pipeline snapshot
  python3 rjm.py master health            # Quick health check
  python3 rjm.py master dashboard         # Full JSON stats
  python3 rjm.py master gaps             # Genre/type gaps
  python3 rjm.py master weekly            # Weekly report
  python3 rjm.py master spotify           # Spotify listener stats + trend
  python3 rjm.py master log_listeners 333 # Log current Spotify monthly listeners
  python3 rjm.py contacts status          # Contact DB overview
  python3 rjm.py contacts sync            # Import contacts.csv → SQLite
  python3 rjm.py contacts search tribal   # Search contacts
  python3 rjm.py content                  # Render 3 clips + queue to Buffer
  python3 rjm.py content --dry-run        # Render only, skip Buffer
  python3 rjm.py playlist status          # Playlist discovery progress
  python3 rjm.py spotify status           # Listener count + milestone
  python3 rjm.py spotify log 333          # Record today's listener count
  python3 rjm.py spotify history          # ASCII chart of last 30 readings
  python3 rjm.py run outreach             # Same as 'outreach run'
  python3 rjm.py run discover             # Trigger discovery agent
  python3 rjm.py run research             # Trigger research agent
"""

import subprocess
import sys
import os
from pathlib import Path

# ─── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT    = Path(__file__).parent
OUTREACH_DIR    = PROJECT_ROOT / "outreach_agent"
AGENT_PY        = OUTREACH_DIR / "agent.py"
MASTER_PY       = OUTREACH_DIR / "master_agent.py"
CONTACT_MGR_PY  = PROJECT_ROOT / "contact_manager.py"
POST_TODAY_PY   = OUTREACH_DIR / "post_today.py"
PLAYLIST_RUN_PY = OUTREACH_DIR / "playlist_run.py"
SPOTIFY_PY      = OUTREACH_DIR / "spotify_tracker.py"
VENV_PYTHON     = OUTREACH_DIR / "venv" / "bin" / "python3"

# Use venv python for outreach_agent scripts if available, else system python
_OUTREACH_PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
_BASE_PYTHON     = sys.executable


def _run(cmd: list[str], cwd: str | None = None) -> int:
    """Run a command, streaming output live. Returns exit code."""
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode


def cmd_outreach(args: list[str]):
    """Delegate to outreach_agent/agent.py"""
    if not AGENT_PY.exists():
        print(f"✗ {AGENT_PY} not found")
        sys.exit(1)
    sys.exit(_run([_OUTREACH_PYTHON, str(AGENT_PY)] + args, cwd=str(OUTREACH_DIR)))


def cmd_master(args: list[str]):
    """Delegate to outreach_agent/master_agent.py"""
    if not MASTER_PY.exists():
        print(f"✗ {MASTER_PY} not found")
        sys.exit(1)
    sys.exit(_run([_OUTREACH_PYTHON, str(MASTER_PY)] + args, cwd=str(OUTREACH_DIR)))


def cmd_contacts(args: list[str]):
    """Delegate to contact_manager.py"""
    if not CONTACT_MGR_PY.exists():
        print(f"✗ {CONTACT_MGR_PY} not found")
        sys.exit(1)
    sys.exit(_run([_BASE_PYTHON, str(CONTACT_MGR_PY)] + args, cwd=str(PROJECT_ROOT)))


def cmd_run(agent_name: str, extra_args: list[str]):
    """Trigger a named agent via master_agent.py run <agent>"""
    sys.exit(_run(
        [_OUTREACH_PYTHON, str(MASTER_PY), "run", agent_name] + extra_args,
        cwd=str(OUTREACH_DIR)
    ))


def cmd_sync():
    """Shorthand for: python3 rjm.py contacts sync"""
    sys.exit(_run([_BASE_PYTHON, str(CONTACT_MGR_PY), "sync"], cwd=str(PROJECT_ROOT)))


def cmd_briefing():
    """Shorthand for: python3 rjm.py master briefing"""
    sys.exit(_run([_OUTREACH_PYTHON, str(MASTER_PY), "briefing"], cwd=str(OUTREACH_DIR)))


def cmd_content(args: list[str]):
    """Run the Holy Rave daily content engine (post_today.py)."""
    if not POST_TODAY_PY.exists():
        print(f"✗ {POST_TODAY_PY} not found")
        sys.exit(1)
    sys.exit(_run([_OUTREACH_PYTHON, str(POST_TODAY_PY)] + args, cwd=str(PROJECT_ROOT)))


def cmd_playlist(args: list[str]):
    """Delegate to outreach_agent/playlist_run.py"""
    if not PLAYLIST_RUN_PY.exists():
        print(f"✗ {PLAYLIST_RUN_PY} not found")
        sys.exit(1)
    sys.exit(_run([_OUTREACH_PYTHON, str(PLAYLIST_RUN_PY)] + args, cwd=str(OUTREACH_DIR)))


def cmd_spotify(args: list[str]):
    """Delegate to outreach_agent/spotify_tracker.py"""
    if not SPOTIFY_PY.exists():
        print(f"✗ {SPOTIFY_PY} not found")
        sys.exit(1)
    sys.exit(_run([_OUTREACH_PYTHON, str(SPOTIFY_PY)] + args, cwd=str(OUTREACH_DIR)))


def cmd_skills():
    """Print the installed skill trigger reference."""
    print("""
╔══════════════════════════════════════════════════════════════╗
║           RJM COMMAND CENTRE — INSTALLED SKILLS              ║
╚══════════════════════════════════════════════════════════════╝

SUPERPOWERS (14 lifecycle workflows) — invoke before key actions:
  /brainstorming              Before any new feature or growth strategy
  /writing-plans              Before building anything — spec it first
  /executing-plans            Run a plan in an isolated session
  /systematic-debugging       Before proposing any fix to outreach_agent/
  /test-driven-development    Before writing implementation code
  /verification-before-completion  Before claiming work done
  /requesting-code-review     Before merging changes
  /using-git-worktrees        Feature isolation (already in use)

FRONTEND-DESIGN               /frontend-design
  Use for: index.html, selah.html, Holy Rave visuals, social UI
  Style: Dark, Holy, Futuristic — Anyma / Rüfüs Du Sol aesthetics

CODE-REVIEW                   /code-review <PR-number>
  Mandatory after: outreach_agent/ changes, rjm.py, agent behaviour
  Runs 5 parallel agents, filters <80% confidence issues
  Example: /code-review 42

SECURITY-GUIDANCE             AUTO — fires on every Edit/Write
  Proactively catches dangerous patterns before they land in code
  Especially active on outreach_agent/ (OAuth, subprocess, DNS)

GSTACK                        /gstack
  QA test Holy Rave website — screenshots, responsive layout, forms
  Requires bun: curl -fsSL https://bun.sh/install | bash
  Then: ~/.claude/skills/gstack/setup
""")


def _fleet_status():
    """Print fleet-wide status: Spotify, content, playlist, strategies."""
    import json as _json

    # ── Spotify listeners ──────────────────────────────────────────────────────
    print("\n[ Spotify Growth ]\n")
    listeners_json = PROJECT_ROOT / "data" / "listeners.json"
    if listeners_json.exists():
        try:
            d = _json.loads(listeners_json.read_text())
            n = d.get("count", 0)
            updated = d.get("updatedAt", "")[:10]
            pct = round(n / 1_000_000 * 100, 3)
            print(f"  Monthly listeners : {n:,}  ({pct}% of 1M goal)  [as of {updated}]")
            remaining = 1_000_000 - n
            print(f"  Remaining to goal : {remaining:,}")
        except Exception:
            print("  Could not read data/listeners.json")
    else:
        print("  No listener data yet — run: python3 rjm.py spotify log <number>")

    # ── Content last run ───────────────────────────────────────────────────────
    print("\n[ Content Engine ]\n")
    content_out = PROJECT_ROOT / "content" / "output"
    if content_out.exists():
        runs = sorted([r for r in content_out.iterdir() if r.is_dir()], reverse=True)
        if runs:
            print(f"  Last run  : {runs[0].name}")
            print(f"  Total runs: {len(runs)}")
        else:
            print("  No content runs yet — run: python3 rjm.py content")
    else:
        print("  content/output/ not found — content engine not yet initialized")

    # ── Playlist progress ──────────────────────────────────────────────────────
    print("\n[ Playlist Pipeline ]\n")
    _run([_OUTREACH_PYTHON, str(PLAYLIST_RUN_PY), "status"], cwd=str(OUTREACH_DIR))

    # ── Strategy portfolio ─────────────────────────────────────────────────────
    print("\n[ Strategy Portfolio ]\n")
    registry_path = OUTREACH_DIR / "strategy_registry.json"
    if registry_path.exists():
        try:
            reg = _json.loads(registry_path.read_text())
            strategies = reg.get("strategies", [])
            active      = [s for s in strategies if s.get("status") == "active"]
            building    = [s for s in strategies if s.get("status") == "building"]
            not_started = [s for s in strategies if s.get("status") == "not_started"]
            est = sum(s.get("estimated_listeners_per_month", 0) for s in active)
            print(f"  Active     : {len(active)}/{len(strategies)} strategies")
            print(f"  Building   : {len(building)}")
            print(f"  Not started: {len(not_started)}")
            print(f"  Est. gain from active: +{est:,} listeners/month")
            if not_started:
                top = sorted(not_started, key=lambda s: s.get("priority", 0), reverse=True)[0]
                print(f"  Next to build: {top['name']} (priority {top['priority']}/10)")
        except Exception:
            print("  Could not read strategy_registry.json")
    else:
        print("  strategy_registry.json not found")


def cmd_status():
    """
    Full system status — runs master health + outreach status in sequence.
    """
    print("\n" + "═" * 60)
    print("  RJM COMMAND CENTRE — SYSTEM STATUS")
    print("═" * 60)

    # 1. Master health check
    print("\n[ Master Agent Health ]\n")
    _run([_OUTREACH_PYTHON, str(MASTER_PY), "health"], cwd=str(OUTREACH_DIR))

    # 2. Outreach agent status
    if AGENT_PY.exists():
        print("\n[ Outreach Agent Status ]\n")
        _run([_OUTREACH_PYTHON, str(AGENT_PY), "status"], cwd=str(OUTREACH_DIR))

    # 3. Contact DB overview
    if CONTACT_MGR_PY.exists():
        print("\n[ Contact Manager (CSV) ]\n")
        _run([_BASE_PYTHON, str(CONTACT_MGR_PY), "status"], cwd=str(PROJECT_ROOT))

    # 4. Fleet-wide status (Spotify, content, playlist, strategies)
    _fleet_status()


def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        return

    cmd = args[0].lower()
    rest = args[1:]

    if cmd == "status":
        cmd_status()
    elif cmd == "briefing":
        cmd_briefing()
    elif cmd == "outreach":
        cmd_outreach(rest)
    elif cmd == "master":
        cmd_master(rest)
    elif cmd == "contacts":
        cmd_contacts(rest)
    elif cmd in ("content", "post"):
        cmd_content(rest)
    elif cmd == "playlist":
        cmd_playlist(rest)
    elif cmd == "spotify":
        cmd_spotify(rest)
    elif cmd == "run":
        agent = rest[0] if rest else ""
        if not agent:
            print("Usage: python3 rjm.py run <agent>")
            print("Agents: outreach, discover, research, verify")
        else:
            cmd_run(agent, rest[1:])
    elif cmd == "sync":
        cmd_sync()
    elif cmd in ("skills", "skill"):
        cmd_skills()
    elif cmd in ("help", "--help", "-h"):
        print(__doc__)
    else:
        # Try to delegate unknown commands to master_agent as a convenience
        print(f"Unknown command: {cmd!r}")
        print("Run 'python3 rjm.py' for help.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
