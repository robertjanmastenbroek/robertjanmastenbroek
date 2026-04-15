#!/bin/bash
#
# RJM — YouTube Channel Email Review (Desktop Launcher)
# ──────────────────────────────────────────────────────
# Double-click to start a review session. Opens a dedicated Chromium window
# that navigates channel-by-channel in a single tab. Click "View email
# address" on each page, return to this window, press Enter, repeat.
#
# No macOS permission prompts. No Chrome developer toggles. Just works.
#
# First channel requires a YouTube CAPTCHA solve — after that the session
# is unlocked for hours (and persists across future runs via a dedicated
# browser profile at outreach_agent/.playwright_profile/).
#

set -e
echo -ne "\033]0;RJM YouTube Email Review\007"

PROJECT_ROOT="/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
WORKTREE="$PROJECT_ROOT/.claude/worktrees/musing-pascal"

if [ -f "$WORKTREE/outreach_agent/youtube_review_pw.py" ]; then
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
echo "  RJM — YouTube Channel Email Review"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  A dedicated Chromium window will open."
echo "  Each channel navigates in the SAME tab (no tab explosion)."
echo "  Click 'View email address' on each About page."
echo "  Return here and press Enter — the tool scrapes automatically."
echo ""
echo "  Controls: Enter=auto-scrape  <email>=paste directly"
echo "            s=skip  b=blocklist  q=quit"
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
