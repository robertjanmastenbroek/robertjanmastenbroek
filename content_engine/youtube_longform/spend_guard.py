"""
spend_guard.py — Hard dollar cap on fal.ai spend, enforced per-call.

RJM-directed safety rail (post Selah-re-render-bug 2026-04-22). The pipeline
already caps publishes per day (watcher.max_per_day=1) and auto-deletes
Shotstack storage, but nothing stops a single botched render from eating
through the fal.ai balance — a loop generation that regenerates all 9
keyframes + all 9 Kling morphs can bill $10+ in a few minutes. If a
deeper bug (dedup regression, prompt-loop, Kling retry storm) fired,
runaway could be $50-100 before we noticed.

This module:
  1. Tracks cumulative spend per UTC day at data/youtube_longform/spend_ledger.jsonl
  2. Exposes check_budget(estimated_cost_usd) which raises BudgetExceededError
     if today's ledger + the estimated next call would exceed
     FAL_DAILY_USD_CAP (default $15 — covers 1 Selah-class publish with margin)
  3. record_spend(actual_cost_usd, kind, note) appends a ledger row after
     each call so the running total is accurate

Usage pattern (inside motion._generate_keyframe, motion._animate_morph, etc.):

    from content_engine.youtube_longform import spend_guard
    spend_guard.check_budget(0.075, kind="flux_keyframe", note="rjm_warrior")
    # ... make the fal.ai call ...
    spend_guard.record_spend(0.075, kind="flux_keyframe", note="rjm_warrior")

Opt-out: set FAL_DAILY_USD_CAP=0 to disable.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from content_engine.youtube_longform import config as cfg

logger = logging.getLogger(__name__)

LEDGER_FILE = cfg.REGISTRY_DIR / "spend_ledger.jsonl"

# Default cap — one Selah-class publish ($10.81) + a retry/regen margin.
# Cron is capped at 1 publish/day, so this cap should never be hit in
# healthy operation. If it IS hit, something's wrong.
DEFAULT_CAP_USD = 15.0


class BudgetExceededError(Exception):
    """Raised when today's spend + an about-to-happen call would exceed the daily cap."""


def _cap_usd() -> float:
    raw = os.getenv("FAL_DAILY_USD_CAP", str(DEFAULT_CAP_USD))
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("FAL_DAILY_USD_CAP=%r is not a float; using default %s", raw, DEFAULT_CAP_USD)
        return DEFAULT_CAP_USD


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _today_total_usd() -> float:
    """Sum every recorded spend for today's UTC date."""
    if not LEDGER_FILE.exists():
        return 0.0
    today = _today_utc()
    total = 0.0
    with open(LEDGER_FILE) as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("date") == today:
                total += float(row.get("usd", 0.0))
    return total


def check_budget(estimated_cost_usd: float, *, kind: str = "", note: str = "") -> None:
    """
    Raise BudgetExceededError if today's spend + estimated_cost_usd would
    exceed FAL_DAILY_USD_CAP. Cap <= 0 disables the check (for local
    dev / explicit override).
    """
    cap = _cap_usd()
    if cap <= 0:
        return
    today_total = _today_total_usd()
    projected = today_total + max(0.0, float(estimated_cost_usd))
    if projected > cap:
        raise BudgetExceededError(
            f"Daily fal.ai budget would be exceeded. "
            f"Today so far: ${today_total:.2f} + about-to-spend ${estimated_cost_usd:.2f} "
            f"= ${projected:.2f} > cap ${cap:.2f} (kind={kind!r} note={note!r}). "
            f"Override by setting FAL_DAILY_USD_CAP higher in .env or to 0 to disable."
        )


def record_spend(actual_cost_usd: float, *, kind: str = "", note: str = "") -> None:
    """Append a ledger row after a successful call. Cheap; safe to call always."""
    cfg.ensure_workspace()
    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_FILE, "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "date":      _today_utc(),
            "usd":       round(float(actual_cost_usd), 4),
            "kind":      kind,
            "note":      note,
        }) + "\n")


def today_summary() -> dict:
    """Convenience for /status commands / weekly reports."""
    total = _today_total_usd()
    cap = _cap_usd()
    return {
        "date":              _today_utc(),
        "today_total_usd":   round(total, 4),
        "cap_usd":           cap,
        "remaining_usd":     round(max(0.0, cap - total), 4) if cap > 0 else None,
        "cap_enabled":       cap > 0,
    }
