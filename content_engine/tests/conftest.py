"""Shared test fixtures.

Disables production-side feedback loops during the test run so tests see a
clean slate — the cooldown / history / weights files mustn't leak from one
test run to the next (or from a real pipeline run into a test).
"""
import os

# Template cooldown: point to /dev/null — the module treats that as "disabled"
# so tests exploring the full template pool (like test_pick_templates_reaches_all_*)
# see every template, and tests don't pollute data/hook_template_history.json.
os.environ.setdefault("RJM_TEMPLATE_HISTORY_PATH", "")

# Disable Claude CLI subprocess calls during tests — otherwise test_generator
# shells out to the real haiku endpoint (~60s/call) on every run. Generator's
# _call_claude honours this env var and returns None so callers exercise their
# deterministic fallback paths (example_fill for hooks, default caption).
os.environ.setdefault("RJM_DISABLE_CLAUDE_CLI", "1")
