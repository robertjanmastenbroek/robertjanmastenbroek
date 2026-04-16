#!/usr/bin/env bash
# RJM Fleet Scheduler — installs/uninstalls macOS launchd agents
# Usage:
#   ./scripts/setup_schedule.sh install    # load all agents into launchd
#   ./scripts/setup_schedule.sh uninstall  # unload and remove all agents
#   ./scripts/setup_schedule.sh status     # show loaded/unloaded state

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHD_DIR="$PROJECT_ROOT/scripts/launchd"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

PLISTS=(
  com.rjm.outreach
  com.rjm.master
  com.rjm.discover
  com.rjm.research
  com.rjm.daily
  com.rjm.weekly
  com.rjm.viral-trend
  com.rjm.viral-daily
  com.rjm.viral-learning
  # BTL (Boil the Lake) growth brain — self-improving orchestration layer
  com.rjm.brain-l1          # tactical pass, every 6h
  com.rjm.brain-l2          # strategic pass, weekly Sun 20:00 CET
  com.rjm.brain-veto-check  # execute due proposals, hourly
  com.rjm.brain-assess      # Growth Health Score, daily 08:05 CET
)

cmd="${1:-status}"

install_all() {
  echo "Installing RJM fleet schedules..."
  chmod +x "$PROJECT_ROOT/scripts/run_agent.sh"
  mkdir -p "$LAUNCH_AGENTS_DIR"
  mkdir -p "$PROJECT_ROOT/logs"

  for label in "${PLISTS[@]}"; do
    src="$LAUNCHD_DIR/${label}.plist"
    dst="$LAUNCH_AGENTS_DIR/${label}.plist"

    if [[ ! -f "$src" ]]; then
      echo "  SKIP  $label (plist not found)"
      continue
    fi

    # Unload first if already loaded (ignore errors)
    launchctl unload "$dst" 2>/dev/null || true
    cp "$src" "$dst"
    launchctl load "$dst"
    echo "  OK    $label → loaded"
  done

  echo ""
  echo "Fleet schedules installed. Check status with:"
  echo "  ./scripts/setup_schedule.sh status"
  echo ""
  echo "Schedule summary:"
  echo "  outreach         — every 30 min (active window 08:00–23:00)"
  echo "  master           — every 3 hrs  (8×/day)"
  echo "  discover         — every 4 hrs  (6×/day)"
  echo "  research         — 6×/day at 02:17 06:17 10:17 14:17 18:17 22:17"
  echo "  daily            — daily at 09:07"
  echo "  weekly           — Monday at 09:13"
  echo "  brain-l1         — every 6 hrs  (BTL tactical: bandit refresh)"
  echo "  brain-l2         — Sunday 20:00 CET (BTL strategic: channel reallocation)"
  echo "  brain-veto-check — hourly (BTL: execute due proposals)"
  echo "  brain-assess     — daily at 08:05 CET (BTL: Growth Health Score)"
}

uninstall_all() {
  echo "Uninstalling RJM fleet schedules..."
  for label in "${PLISTS[@]}"; do
    dst="$LAUNCH_AGENTS_DIR/${label}.plist"
    if [[ -f "$dst" ]]; then
      launchctl unload "$dst" 2>/dev/null || true
      rm "$dst"
      echo "  REMOVED $label"
    else
      echo "  SKIP    $label (not installed)"
    fi
  done
  echo "Done."
}

show_status() {
  echo "RJM Fleet Schedule Status"
  echo "─────────────────────────────────────────────"
  printf "%-20s %-12s %s\n" "Agent" "State" "Next fire"
  echo "─────────────────────────────────────────────"
  for label in "${PLISTS[@]}"; do
    dst="$LAUNCH_AGENTS_DIR/${label}.plist"
    if [[ -f "$dst" ]]; then
      # launchctl list shows PID and status
      info=$(launchctl list "$label" 2>/dev/null || echo "not loaded")
      if echo "$info" | grep -q '"PID"'; then
        state="RUNNING"
      elif echo "$info" | grep -q 'not loaded'; then
        state="NOT LOADED"
      else
        state="LOADED"
      fi
      printf "%-20s %-12s\n" "$label" "$state"
    else
      printf "%-20s %-12s\n" "$label" "NOT INSTALLED"
    fi
  done
  echo ""
  echo "Logs: $PROJECT_ROOT/logs/launchd_*.log"
}

case "$cmd" in
  install)   install_all ;;
  uninstall) uninstall_all ;;
  status)    show_status ;;
  *)
    echo "Usage: $0 [install|uninstall|status]"
    exit 1
    ;;
esac
