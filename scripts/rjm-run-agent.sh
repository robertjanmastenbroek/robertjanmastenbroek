#!/bin/bash
# RJM Agent Runner (wrapper) — lives outside ~/Documents/ so /bin/bash can read it.
#
# Why this exists:
#   macOS TCC denies /bin/bash read access to ~/Documents/ when invoked by launchd
#   (no Full Disk Access). Launchd agents calling scripts inside ~/Documents/ fail
#   with "Operation not permitted". But /opt/homebrew/bin/python3.13 HAS FDA (granted
#   at install), so we bypass bash's limitation by exec'ing python directly here.
#
# This wrapper is referenced by every ~/Library/LaunchAgents/com.rjm.*.plist.
# The plists point to /Users/motomoto/bin/rjm-run-agent.sh instead of
# /Users/motomoto/Documents/.../scripts/run_agent.sh.
#
# Usage: rjm-run-agent.sh <agent-name>

set -uo pipefail

PROJECT_ROOT="/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
PYTHON="/opt/homebrew/bin/python3.13"
AGENT="${1:-}"

if [[ -z "$AGENT" ]]; then
  echo "Usage: rjm-run-agent.sh <agent-name>" >&2
  exit 1
fi

# Stdout/stderr go to the plist's StandardOutPath (launchd writes it, no TCC issue).
# cd into project root — chdir syscall doesn't need TCC read access.
cd "$PROJECT_ROOT" 2>/dev/null || { echo "cd failed (is launchd granted FDA?)"; exit 1; }

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# ── Find claude CLI (version-agnostic) ───────────────────────────────────────
find_claude() {
  if command -v claude &>/dev/null; then command -v claude; return; fi
  local dir="$HOME/Library/Application Support/Claude/claude-code"
  local bin
  bin=$(ls -d "$dir"/*/claude.app/Contents/MacOS/claude 2>/dev/null | sort -V | tail -1 || true)
  if [[ -n "$bin" && -x "$bin" ]]; then echo "$bin"; return; fi
  bin=$(ls "$HOME/Library/Application Support/Claude/claude-code-vm"/*/claude 2>/dev/null | sort -V | tail -1 || true)
  if [[ -n "$bin" && -x "$bin" ]]; then echo "$bin"; return; fi
  echo ""
}

# ── Run a Claude markdown agent ─────────────────────────────────────────────
# Uses python to read the agent .md file (python has FDA; bash does not),
# then pipes the content to claude.
run_claude_agent() {
  local agent_file="$1"
  local label="$2"
  local CLAUDE
  CLAUDE=$(find_claude)
  if [[ -z "$CLAUDE" ]]; then
    echo "[$(ts)] ERROR: claude CLI not found"
    exit 1
  fi
  echo "[$(ts)] Starting $label (claude: $CLAUDE)"
  "$PYTHON" -c "import sys; sys.stdout.write(open(sys.argv[1]).read())" "$agent_file" | \
    "$CLAUDE" --print \
      --allowedTools "Bash,Read,Write,Edit,Grep,Glob,WebSearch,WebFetch" \
      2>&1
  echo "[$(ts)] $label finished (exit $?)"
}

# ── Dispatch ─────────────────────────────────────────────────────────────────
case "$AGENT" in
  outreach)
    echo "[$(ts)] Starting outreach agent"
    "$PYTHON" rjm.py outreach run 2>&1
    echo "[$(ts)] Outreach agent finished (exit $?)"
    ;;
  master)       run_claude_agent "$PROJECT_ROOT/agents/rjm-master.md" "rjm-master" ;;
  discover)     run_claude_agent "$PROJECT_ROOT/agents/rjm-discover.md" "rjm-discover" ;;
  research)     run_claude_agent "$PROJECT_ROOT/agents/rjm-research.md" "rjm-research" ;;
  daily)        run_claude_agent "$PROJECT_ROOT/agents/holy-rave-daily-run.md" "holy-rave-daily-run" ;;
  weekly)       run_claude_agent "$PROJECT_ROOT/agents/holy-rave-weekly-report.md" "holy-rave-weekly-report" ;;
  viral-trend)
    echo "[$(ts)] Starting viral trend scanner"
    "$PYTHON" rjm.py content trend-scan 2>&1
    echo "[$(ts)] Viral trend scanner finished (exit $?)"
    ;;
  viral-daily)
    echo "[$(ts)] Starting viral daily pipeline"
    "$PYTHON" rjm.py content viral 2>&1
    echo "[$(ts)] Viral daily pipeline finished (exit $?)"
    ;;
  viral-learning)
    echo "[$(ts)] Starting viral learning loop"
    "$PYTHON" rjm.py content learning 2>&1
    echo "[$(ts)] Viral learning loop finished (exit $?)"
    ;;
  metrics-fetch|weights-learn|learning)
    echo "[$(ts)] Starting content_engine.learning_loop ($AGENT)"
    "$PYTHON" content_engine/learning_loop.py 2>&1
    echo "[$(ts)] learning_loop finished (exit $?)"
    ;;
  brain-l1)
    echo "[$(ts)] Starting BTL brain L1"
    "$PYTHON" rjm.py brain l1 2>&1
    echo "[$(ts)] BTL brain L1 finished (exit $?)"
    ;;
  brain-l2)
    echo "[$(ts)] Starting BTL brain L2"
    "$PYTHON" rjm.py brain l2 2>&1
    echo "[$(ts)] BTL brain L2 finished (exit $?)"
    ;;
  brain-veto-check)
    echo "[$(ts)] Starting BTL veto check"
    "$PYTHON" rjm.py brain veto_check 2>&1
    echo "[$(ts)] BTL veto check finished (exit $?)"
    ;;
  brain-assess)
    echo "[$(ts)] Starting BTL brain assess"
    "$PYTHON" rjm.py brain assess 2>&1
    echo "[$(ts)] BTL brain assess finished (exit $?)"
    ;;
  *)
    echo "[$(ts)] ERROR: unknown agent '$AGENT'"
    exit 1
    ;;
esac
