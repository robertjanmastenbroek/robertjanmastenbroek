#!/bin/bash
#
# RJM — YouTube Review Daily Runner
# ──────────────────────────────────
# Wrapper invoked by ~/Library/LaunchAgents/com.rjm.youtube-review.plist.
# Fires once per day at the scheduled time and:
#   1. Shows a macOS notification so the user knows the session is starting
#   2. Opens the Desktop launcher .command in Terminal.app
#   3. Logs the run to ~/Library/Logs/rjm-youtube-review-daily.log
#
# Never runs if the launcher file isn't where expected (e.g. Desktop cleanup),
# so failures are silent from the user's perspective.
#

LAUNCHER="/Users/motomoto/Desktop/RJM YouTube Email Review.command"
LOG_FILE="$HOME/Library/Logs/rjm-youtube-review-daily.log"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

log "daily runner fired"

if [ ! -f "$LAUNCHER" ]; then
    log "ERROR: launcher not found at $LAUNCHER — skipping"
    exit 0
fi

# Native macOS notification — non-blocking, auto-dismisses after a few seconds
osascript -e 'display notification "Time for your YouTube review session — opening the tool…" with title "RJM YouTube Review" sound name "Glass"' 2>/dev/null || true

# Small delay so the notification is visible before Terminal steals focus
sleep 2

# Open the launcher in Terminal.app (same as double-clicking the Desktop icon)
open -a Terminal "$LAUNCHER"
log "launcher opened in Terminal"
