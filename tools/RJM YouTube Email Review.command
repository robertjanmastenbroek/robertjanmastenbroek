#!/bin/bash
#
# RJM — YouTube Channel Email Review (Desktop Launcher)
# ──────────────────────────────────────────────────────
# Double-click this file to start a 50-channel email review session.
# Opens one YouTube channel at a time in your browser. Click "View email
# address" on each About page, paste the email back in this window, repeat.
#
# The session is resumable — quit anytime with 'q' and pick up where you
# left off the next time you double-click this file.
#

set -e

# Terminal window title
echo -ne "\033]0;RJM YouTube Email Review\007"

PROJECT_ROOT="/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
WORKTREE="$PROJECT_ROOT/.claude/worktrees/musing-pascal"

# Prefer the musing-pascal worktree (where this feature was built) while
# it's unmerged. After merge to main, this auto-falls-through to the
# main project directory without any edit.
if [ -f "$WORKTREE/outreach_agent/youtube_manual_review.py" ]; then
    WORKDIR="$WORKTREE"
else
    WORKDIR="$PROJECT_ROOT"
fi

cd "$WORKDIR"

# Load env vars (YOUTUBE_API_KEY etc.)
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
echo "  Opens 50 channels one at a time in your browser."
echo "  Click 'View email address' on each About page."
echo "  Paste the email back here and press Enter."
echo ""
echo "  Controls: <email>=save  s=skip  b=blocklist  q=quit"
echo "            (Enter alone re-opens the About page)"
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
    echo "  Next: python3 rjm.py outreach run  (sends to the new contacts)"
else
    echo "  Session ended with exit code $EXIT_CODE"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Press Enter to close this window..."
read -r _
