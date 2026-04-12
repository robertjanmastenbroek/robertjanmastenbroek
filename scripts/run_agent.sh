#!/usr/bin/env bash
# RJM Agent Runner — called by launchd for each scheduled agent
# Usage: run_agent.sh <agent-name>
#   outreach    → python outreach agent (email sends)
#   master      → rjm-master.md  (8×/day)
#   discover    → rjm-discover.md (6×/day)
#   research    → rjm-research.md (6×/day)
#   daily       → holy-rave-daily-run.md (daily 09:00)
#   weekly      → holy-rave-weekly-report.md (Monday 09:00)

set -euo pipefail

PROJECT_ROOT="/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
LOGS_DIR="$PROJECT_ROOT/logs"
AGENT="${1:-}"

if [[ -z "$AGENT" ]]; then
  echo "Usage: run_agent.sh <agent-name>" >&2
  exit 1
fi

mkdir -p "$LOGS_DIR"
LOG="$LOGS_DIR/agent_${AGENT}_$(date +%Y%m%d).log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# ── Find claude CLI (version-agnostic) ───────────────────────────────────────
find_claude() {
  # Check PATH first (e.g. if symlinked by user)
  if command -v claude &>/dev/null; then
    command -v claude
    return
  fi
  # Claude Code desktop app — find latest installed version
  local dir="$HOME/Library/Application Support/Claude/claude-code"
  local bin
  bin=$(ls -d "$dir"/*/claude.app/Contents/MacOS/claude 2>/dev/null | sort -V | tail -1 || true)
  if [[ -n "$bin" && -x "$bin" ]]; then
    echo "$bin"; return
  fi
  # VM variant
  bin=$(ls "$HOME/Library/Application Support/Claude/claude-code-vm"/*/claude 2>/dev/null | sort -V | tail -1 || true)
  if [[ -n "$bin" && -x "$bin" ]]; then
    echo "$bin"; return
  fi
  echo "" # not found
}

# ── Run Python outreach agent ────────────────────────────────────────────────
run_outreach() {
  log "Starting outreach agent"
  cd "$PROJECT_ROOT"
  /opt/homebrew/bin/python3 rjm.py outreach run 2>&1 | tee -a "$LOG"
  log "Outreach agent finished (exit $?)"
}

# ── Run a Claude markdown agent ──────────────────────────────────────────────
run_claude_agent() {
  local agent_file="$1"
  local label="$2"

  if [[ ! -f "$agent_file" ]]; then
    log "ERROR: agent file not found: $agent_file"
    exit 1
  fi

  local CLAUDE
  CLAUDE=$(find_claude)
  if [[ -z "$CLAUDE" ]]; then
    log "ERROR: claude CLI not found — install Claude Code desktop app"
    exit 1
  fi

  log "Starting $label (claude: $CLAUDE)"
  cd "$PROJECT_ROOT"

  # --print: non-interactive; pipe agent file as prompt
  "$CLAUDE" --print \
    --allowedTools "Bash,Read,Write,Edit,Grep,Glob,WebSearch,WebFetch" \
    < "$agent_file" 2>&1 | tee -a "$LOG"

  log "$label finished"
}

# ── Dispatch ─────────────────────────────────────────────────────────────────
case "$AGENT" in
  outreach)
    run_outreach
    ;;
  master)
    run_claude_agent "$PROJECT_ROOT/agents/rjm-master.md" "rjm-master"
    ;;
  discover)
    run_claude_agent "$PROJECT_ROOT/agents/rjm-discover.md" "rjm-discover"
    ;;
  research)
    run_claude_agent "$PROJECT_ROOT/agents/rjm-research.md" "rjm-research"
    ;;
  daily)
    run_claude_agent "$PROJECT_ROOT/agents/holy-rave-daily-run.md" "holy-rave-daily-run"
    ;;
  weekly)
    run_claude_agent "$PROJECT_ROOT/agents/holy-rave-weekly-report.md" "holy-rave-weekly-report"
    ;;
  *)
    log "ERROR: unknown agent '$AGENT'"
    exit 1
    ;;
esac
