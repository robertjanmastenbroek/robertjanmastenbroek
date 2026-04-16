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
  viral-trend)
    log "Starting viral trend scanner"
    cd "$PROJECT_ROOT"
    /opt/homebrew/bin/python3.13 rjm.py content trend-scan 2>&1 | tee -a "$LOG"
    log "Viral trend scanner finished (exit $?)"
    ;;
  viral-daily)
    log "Starting viral daily pipeline (assembly + distribution)"
    cd "$PROJECT_ROOT"
    /opt/homebrew/bin/python3.13 rjm.py content viral 2>&1 | tee -a "$LOG"
    log "Viral daily pipeline finished (exit $?)"
    ;;
  viral-learning)
    log "Starting viral learning loop"
    cd "$PROJECT_ROOT"
    /opt/homebrew/bin/python3.13 rjm.py content learning 2>&1 | tee -a "$LOG"
    log "Viral learning loop finished (exit $?)"
    ;;
  metrics-fetch|weights-learn|learning)
    # Single unified pass: fetch IG + YouTube + Spotify metrics and recompute
    # arm weights from a rolling 28-day window. The new content_engine
    # learning loop does fetch + recompute in one shot — the old split
    # (metrics_fetcher + weights_learner) has been quarantined.
    # Generator + assembler read the new snapshot (data/weights_snapshot.json)
    # on their next run via content_engine.learning_loop.load_latest_weights().
    log "Starting content_engine.learning_loop ($AGENT)"
    cd "$PROJECT_ROOT"
    /opt/homebrew/bin/python3.13 content_engine/learning_loop.py 2>&1 | tee -a "$LOG"
    log "learning_loop finished (exit $?)"
    ;;
  brain-l1)
    # BTL Layer 1 — tactical pass: refresh bandits, breakthrough scan.
    # Cadence: 4×/day per config.BTL_L1_RUNS_PER_DAY. Cheap operation.
    log "Starting BTL brain L1 (bandit refresh + breakthrough scan)"
    cd "$PROJECT_ROOT"
    /opt/homebrew/bin/python3.13 rjm.py brain l1 2>&1 | tee -a "$LOG"
    log "BTL brain L1 finished (exit $?)"
    ;;
  brain-l2)
    # BTL Layer 2 — strategic pass: channel weight reallocation based on LEI.
    # Cadence: weekly (Sunday 20:00 CET per config.BTL_L2_DAY/HOUR_CET).
    # Moves % allocation across outreach/content/email channels — higher
    # blast radius than L1, so less frequent.
    log "Starting BTL brain L2 (channel reallocation)"
    cd "$PROJECT_ROOT"
    /opt/homebrew/bin/python3.13 rjm.py brain l2 2>&1 | tee -a "$LOG"
    log "BTL brain L2 finished (exit $?)"
    ;;
  brain-veto-check)
    # BTL veto check — executes proposals whose 24h veto window has passed.
    # Cadence: hourly. With a 24h BTL_VETO_WINDOW_HOURS, running hourly
    # bounds post-window execution latency to ≤1h. Idempotent thanks to
    # the atomic-claim fix in veto_system.execute_proposal.
    log "Starting BTL veto check"
    cd "$PROJECT_ROOT"
    /opt/homebrew/bin/python3.13 rjm.py brain veto_check 2>&1 | tee -a "$LOG"
    log "BTL veto check finished (exit $?)"
    ;;
  brain-assess)
    # BTL Growth Health Score — daily snapshot of listener trajectory vs.
    # the 1M target. Fires at 08:00 CET alongside the veto digest so the
    # score is fresh when the morning review happens.
    log "Starting BTL brain assess (Growth Health Score)"
    cd "$PROJECT_ROOT"
    /opt/homebrew/bin/python3.13 rjm.py brain assess 2>&1 | tee -a "$LOG"
    log "BTL brain assess finished (exit $?)"
    ;;
  *)
    log "ERROR: unknown agent '$AGENT'"
    exit 1
    ;;
esac
