#!/bin/bash
# claude-desktop-launch.sh — launcher for Claude Desktop with Chromium resource caps.
#
# Addresses known Claude Desktop bloat:
#   - anthropics/claude-code#43390 (Claude Desktop writes 8–13 GB/hr to disk)
#   - anthropics/claude-code#11315 (129 GB virtual memory leak)
#   - anthropics/claude-code#48223 (renderer pegged at 500%+ CPU on launch)
#   - anthropics/claude-code#8968  (exceptional memory usage v2.0.8+)
#
# These flags are Chromium-native and honored by Electron's HangWatcher + IPC
# watchdog, unlike external cpulimit / taskpolicy which collide with the
# watchdog and get renderers killed (see throttle-claude.sh header).
#
# Usage: invoke this instead of launching Claude via the Dock. To make it
# stick, create a .app wrapper (Automator: Run Shell Script → open the .app
# bundle) and drag that to the Dock in place of Claude.app.

set -euo pipefail

CLAUDE_BIN="/Applications/Claude.app/Contents/MacOS/Claude"

if [ ! -x "$CLAUDE_BIN" ]; then
  echo "Claude Desktop not found at $CLAUDE_BIN" >&2
  exit 1
fi

# --disk-cache-size=268435456        Cap Chromium disk cache at 256 MB.
# --js-flags=--max-old-space-size=N  Cap V8 old-gen heap at N MB per renderer
#                                    (must be set at launch before V8 init).
#                                    Inner quotes omitted — no spaces in the flag,
#                                    and some Electron versions drop outer quotes.
# --renderer-process-limit=6         Cap concurrent renderer processes.
exec "$CLAUDE_BIN" \
  --disk-cache-size=268435456 \
  --js-flags=--max-old-space-size=768 \
  --renderer-process-limit=6 \
  "$@"
