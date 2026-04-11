#!/usr/bin/env python3
"""
RJM Discover Agent — Claude CLI wrapper for rjm-discover.

Reads the rjm-discover scheduled task SKILL.md and runs it via Claude CLI
in headless mode. This is the same agent that runs 6× daily via the
Claude Code scheduler — this wrapper lets you trigger it manually:

  python3 discover_agent.py            # run discovery (adds 25 contacts)
  python3 discover_agent.py --dry-run  # print the skill prompt, don't run

Called by master_agent.py run discover and rjm.py run discover.
"""

import argparse
import subprocess
import sys
from pathlib import Path

# ─── Skill path ───────────────────────────────────────────────────────────────
SKILL_PATH   = Path.home() / ".claude/scheduled-tasks/rjm-discover/SKILL.md"
PROJECT_ROOT = Path(__file__).parent.parent   # Command Centre root
BASE_DIR     = Path(__file__).parent          # outreach_agent/


# ─── Find Claude CLI ──────────────────────────────────────────────────────────

def _find_claude() -> str:
    """Find the Claude CLI binary (same logic as template_engine)."""
    import os

    env_path = os.getenv("CLAUDE_CLI_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    base = Path(os.path.expanduser("~/Library/Application Support/Claude/claude-code"))
    if base.exists():
        for ver_dir in sorted(base.iterdir(), reverse=True):
            candidate = ver_dir / "claude.app" / "Contents" / "MacOS" / "claude"
            if candidate.is_file():
                return str(candidate)

    for name in ("claude", "claude-code"):
        r = subprocess.run(["which", name], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()

    raise FileNotFoundError(
        "Cannot find Claude CLI. Set CLAUDE_CLI_PATH env var or create a symlink."
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RJM Discover Agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the skill prompt without running Claude")
    args = parser.parse_args()

    if not SKILL_PATH.exists():
        print(f"⚠️  Skill not found: {SKILL_PATH}")
        print("   Install or re-sync the rjm-discover scheduled task.")
        sys.exit(1)

    skill_content = SKILL_PATH.read_text(encoding="utf-8")

    if args.dry_run:
        print("─── rjm-discover SKILL.md ──────────────────────────────")
        print(skill_content)
        print("────────────────────────────────────────────────────────")
        print("(dry-run: not invoking Claude)")
        return

    try:
        claude_bin = _find_claude()
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        sys.exit(1)

    print(f"→ Running rjm-discover via Claude CLI…\n")

    result = subprocess.run(
        [
            claude_bin,
            "--print",
            "--dangerously-skip-permissions",
            "--model", "claude-haiku-4-5-20251001",
            "-p", skill_content,
        ],
        cwd=str(PROJECT_ROOT),
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
