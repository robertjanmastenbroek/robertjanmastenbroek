#!/bin/bash
#
# RJM — YouTube Channel Email Review (Desktop Launcher, Chrome auto)
# ──────────────────────────────────────────────────────────────────
# Double-click to start a review session that drives your real Chrome.
#
# First-run setup (one time only):
#   1. macOS may ask "Terminal wants access to control Google Chrome"
#      → click OK
#   2. In Chrome: View → Developer → Allow JavaScript from Apple Events
#      → toggle ON
#
# After setup, each channel auto-opens in your current Chrome tab. Click
# "View email address" on the About page, press Enter in this window, and
# the tool reads the email directly from the page — no copy-paste needed.
#

set -e
echo -ne "\033]0;RJM YouTube Email Review\007"

PROJECT_ROOT="/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
WORKTREE="$PROJECT_ROOT/.claude/worktrees/musing-pascal"

if [ -f "$WORKTREE/outreach_agent/youtube_review_auto.py" ]; then
    WORKDIR="$WORKTREE"
else
    WORKDIR="$PROJECT_ROOT"
fi

cd "$WORKDIR"

if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env" 2>/dev/null || true
    set +a
fi

clear
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  RJM — YouTube Channel Email Review (Chrome auto)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Each channel opens in your current Chrome tab."
echo "  Click 'View email address' on the page."
echo "  Press Enter → the tool reads the email automatically."
echo ""
echo "  Controls: Enter=auto-scrape  <email>=paste directly"
echo "            s=skip  b=blocklist  q=quit"
echo ""
echo "  First run only: enable in Chrome →"
echo "    View → Developer → Allow JavaScript from Apple Events"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 rjm.py youtube review --limit 50

EXIT_CODE=$?

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ $EXIT_CODE -eq 0 ]; then
    echo "  Session complete."
    echo ""
    echo "  Next: python3 rjm.py outreach run  (fires the new contacts)"
else
    echo "  Session ended with exit code $EXIT_CODE"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Press Enter to close this window..."
read -r _
