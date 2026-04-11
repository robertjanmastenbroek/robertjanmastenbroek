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
  python3 rjm.py content retry            # Retry all failed posts in queue
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
  python3 rjm.py contacts status          # Contact DB overview
  python3 rjm.py contacts sync            # Import contacts.csv → SQLite
  python3 rjm.py contacts search tribal   # Search contacts
  python3 rjm.py run outreach             # Same as 'outreach run'
  python3 rjm.py run discover             # Trigger discovery agent
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


def cmd_content_retry():
    """Retry all posts in data/failed_posts.json."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent / "outreach_agent"))
    from post_queue import load_failed_posts, clear_failed_post, queue_depth
    from buffer_poster import upload_video_and_queue

    posts = load_failed_posts()
    if not posts:
        print("✓ No failed posts in queue.")
        return

    print(f"Retrying {len(posts)} failed post(s)…\n")
    # Iterate in reverse so clearing by index doesn't shift remaining indices
    for i in range(len(posts) - 1, -1, -1):
        post = posts[i]
        clip_name = Path(post["clip_path"]).name
        print(f"  [{i+1}/{len(posts)}] {clip_name} — originally failed: {post['error'][:60]}")
        try:
            results = upload_video_and_queue(
                clip_path         = post["clip_path"],
                tiktok_caption    = post["tiktok_caption"],
                instagram_caption = post["instagram_caption"],
                youtube_title     = post["youtube_title"],
                youtube_desc      = post["youtube_desc"],
                scheduled_at      = None,  # reschedule immediately
            )
            failed = [p for p, r in results.items() if not r["success"]]
            if not failed:
                clear_failed_post(i)
                print(f"    ✓ Retried successfully — removed from queue")
            else:
                print(f"    ⚠ Still failing on: {', '.join(failed)}")
        except Exception as exc:
            print(f"    ✗ Still failing: {exc}")

    remaining = queue_depth()
    print(f"\nDone. {remaining} post(s) still in queue.")


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

    # 4. Failed post queue
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent / "outreach_agent"))
    from post_queue import queue_depth
    depth = queue_depth()
    queue_status = f"⚠  {depth} post(s) waiting to retry — run: python3 rjm.py content retry" if depth > 0 else "✓  empty"
    print(f"\n[ Failed Post Queue ]\n")
    print(f"  Failed post queue:  {queue_status}")


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
    elif cmd == "run":
        agent = rest[0] if rest else ""
        if not agent:
            print("Usage: python3 rjm.py run <agent>")
            print("Agents: outreach, discover, research, verify")
        else:
            cmd_run(agent, rest[1:])
    elif cmd == "content":
        action = rest[0].lower() if rest else ""
        if action == "retry":
            cmd_content_retry()
        else:
            print("Usage: python3 rjm.py content retry")
            sys.exit(1)
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
