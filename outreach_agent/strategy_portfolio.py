"""
BTL Strategy Portfolio + Channel Registry
=========================================

Tracks every growth channel ("strategy") in the RJM portfolio and reallocates
budget weights based on the most recent Listener Efficiency Index (LEI).

The registry lives in `data/channel_registry.json` (override with the
`BTL_CHANNEL_REGISTRY_PATH` env var). Channel-level metric history lives in
the `channel_metrics` SQLite table, created by btl_db.init_btl_tables().

L2 reallocation algorithm
-------------------------
1. Compute LEI per active channel = sum of `listeners_gained` events in the
   last `days` window (default 7).
2. Compute z-score (normalised distance from mean LEI).
3. Shift each channel's weight toward its z-score using a learning rate
   (`BTL_REALLOCATION_LEARNING_RATE`, default 0.3).
4. Clamp every weight between `BTL_CHANNEL_WEIGHT_FLOOR` (0.05) and
   `BTL_CHANNEL_WEIGHT_CEILING` (0.40). Channels classified as a
   "breakthrough" (z >= 1.0 AND lei > 0) may stretch up to
   `BTL_CHANNEL_BREAKTHROUGH_CEILING` (0.50).
5. Renormalise so weights sum to 1.0 across active channels.

Inactive (queued / paused / locked) channels keep their stored weight and are
not touched by reallocation.
"""

from __future__ import annotations

import json
import logging
import math
import os
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import config
import db

log = logging.getLogger("outreach.strategy_portfolio")


# ── path resolution ────────────────────────────────────────────────────────────


def _registry_path() -> Path:
    """Resolve registry path. Env override → default `data/channel_registry.json`."""
    override = os.getenv("BTL_CHANNEL_REGISTRY_PATH")
    if override:
        return Path(override)
    return Path(config.BASE_DIR).parent / "data" / "channel_registry.json"


# ── load / save ────────────────────────────────────────────────────────────────


def load_registry() -> dict:
    """Return the registry as a dict. Empty skeleton if the file is missing."""
    path = _registry_path()
    if not path.exists():
        return {"channels": [], "last_reallocation": None}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_registry(reg: dict) -> None:
    """Persist the registry to disk (pretty-printed for diff-friendliness)."""
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(reg, fh, indent=2)


# ── lookup helpers ─────────────────────────────────────────────────────────────


def get_channel(channel_id: str) -> Optional[dict]:
    reg = load_registry()
    for ch in reg.get("channels", []):
        if ch.get("id") == channel_id:
            return ch
    return None


def get_active_channels() -> list[dict]:
    reg = load_registry()
    return [ch for ch in reg.get("channels", []) if ch.get("status") == "active"]


# ── lifecycle ──────────────────────────────────────────────────────────────────


def _set_status(channel_id: str, status: str) -> None:
    reg = load_registry()
    for ch in reg.get("channels", []):
        if ch.get("id") == channel_id:
            ch["status"] = status
            save_registry(reg)
            log.info("channel %s → status=%s", channel_id, status)
            return
    log.warning("channel %s not found in registry; cannot set status=%s",
                channel_id, status)


def activate_channel(channel_id: str) -> None:
    _set_status(channel_id, "active")


def pause_channel(channel_id: str) -> None:
    _set_status(channel_id, "paused")


# ── metrics ────────────────────────────────────────────────────────────────────


def record_channel_metric(
    channel_id: str,
    metric_name: str,
    metric_value: float,
) -> None:
    """Insert a metric row into channel_metrics."""
    today = datetime.utcnow().date().isoformat()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO channel_metrics (channel_id, date, metric_name, metric_value) "
            "VALUES (?, ?, ?, ?)",
            (channel_id, today, metric_name, float(metric_value)),
        )


def get_channel_lei(channel_id: str, days: int = 7) -> float:
    """Sum of `listeners_gained` for a channel inside the last `days` window."""
    cutoff = (datetime.utcnow().date() - timedelta(days=days)).isoformat()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(metric_value), 0) AS lei "
            "FROM channel_metrics "
            "WHERE channel_id = ? "
            "  AND metric_name = 'listeners_gained' "
            "  AND date >= ?",
            (channel_id, cutoff),
        ).fetchone()
    if row is None:
        return 0.0
    return float(row["lei"] or 0.0)


# ── reallocation ───────────────────────────────────────────────────────────────


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def reallocate_weights() -> dict:
    """
    L2 reallocation pass — see module docstring for the algorithm.

    Returns the (mutated, persisted) registry dict.
    """
    reg = load_registry()
    actives = [ch for ch in reg.get("channels", []) if ch.get("status") == "active"]

    if not actives:
        log.info("reallocate_weights: no active channels — nothing to do")
        reg["last_reallocation"] = datetime.utcnow().isoformat()
        save_registry(reg)
        return reg

    learning_rate = float(getattr(config, "BTL_REALLOCATION_LEARNING_RATE", 0.3))
    floor = float(getattr(config, "BTL_CHANNEL_WEIGHT_FLOOR", 0.05))
    ceiling = float(getattr(config, "BTL_CHANNEL_WEIGHT_CEILING", 0.40))
    breakthrough_ceiling = float(
        getattr(config, "BTL_CHANNEL_BREAKTHROUGH_CEILING", 0.50)
    )

    leis = {ch["id"]: get_channel_lei(ch["id"], days=7) for ch in actives}
    values = list(leis.values())
    mean_lei = statistics.fmean(values) if values else 0.0
    stdev_lei = statistics.pstdev(values) if len(values) > 1 else 0.0

    # Compute new weights — shift toward z-score signal.
    new_weights: dict[str, float] = {}
    for ch in actives:
        cid = ch["id"]
        current = float(ch.get("weight", 1.0 / len(actives)))
        lei = leis[cid]

        if stdev_lei > 0:
            z = (lei - mean_lei) / stdev_lei
        else:
            z = 0.0

        # Multiplicative nudge: 1 + lr * z. Negative z shrinks, positive grows.
        # Clamp the multiplier so a single bad week can't zero out a channel.
        multiplier = 1.0 + (learning_rate * z)
        multiplier = _clamp(multiplier, 0.5, 2.0)
        proposed = current * multiplier

        is_breakthrough = z >= 1.0 and lei > 0
        cap = breakthrough_ceiling if is_breakthrough else ceiling
        clamped = _clamp(proposed, floor, cap)
        new_weights[cid] = clamped

    # Renormalise to sum=1.0 across active channels — but respect the cap.
    # Iterate a few times so floor/ceiling are still satisfied after normalisation.
    for _ in range(20):
        total = sum(new_weights.values())
        if total <= 0:
            equal = 1.0 / len(new_weights)
            new_weights = {cid: equal for cid in new_weights}
            break
        scaled = {cid: w / total for cid, w in new_weights.items()}
        # Re-clamp.
        clamped = {}
        for ch in actives:
            cid = ch["id"]
            lei = leis[cid]
            stdev = stdev_lei
            if stdev > 0:
                z = (lei - mean_lei) / stdev
            else:
                z = 0.0
            is_breakthrough = z >= 1.0 and lei > 0
            cap = breakthrough_ceiling if is_breakthrough else ceiling
            clamped[cid] = _clamp(scaled[cid], floor, cap)
        if all(abs(clamped[c] - scaled[c]) < 1e-9 for c in clamped):
            new_weights = clamped
            break
        new_weights = clamped

    # Final pass: if floor+ceiling clamping breaks the sum, distribute the
    # residual equally among channels that still have headroom.
    total = sum(new_weights.values())
    residual = 1.0 - total
    if abs(residual) > 1e-6:
        # Pick channels that have headroom in the right direction.
        if residual > 0:
            adjustable = [
                cid for cid in new_weights
                if new_weights[cid] < ceiling - 1e-9
            ]
        else:
            adjustable = [
                cid for cid in new_weights
                if new_weights[cid] > floor + 1e-9
            ]
        if adjustable:
            share = residual / len(adjustable)
            for cid in adjustable:
                new_weights[cid] = _clamp(
                    new_weights[cid] + share, floor, breakthrough_ceiling
                )

    # Write back into the registry.
    for ch in reg["channels"]:
        if ch["id"] in new_weights:
            ch["weight"] = round(new_weights[ch["id"]], 6)

    reg["last_reallocation"] = datetime.utcnow().isoformat()
    save_registry(reg)
    log.info(
        "reallocate_weights: updated %d active channels (sum=%.4f)",
        len(actives),
        sum(new_weights.values()),
    )
    return reg


# ── summary ────────────────────────────────────────────────────────────────────


def get_portfolio_summary() -> dict:
    """Compact view of the portfolio for dashboards / digests."""
    reg = load_registry()
    channels = reg.get("channels", [])
    actives = [c for c in channels if c.get("status") == "active"]
    queued = [c for c in channels if c.get("status") == "queued"]
    paused = [c for c in channels if c.get("status") == "paused"]
    locked = [c for c in channels if c.get("status") == "locked"]

    rows = []
    for ch in channels:
        lei = get_channel_lei(ch["id"], days=7) if ch.get("status") == "active" else 0.0
        rows.append({
            "id": ch["id"],
            "name": ch.get("name"),
            "status": ch.get("status"),
            "weight": ch.get("weight", 0.0),
            "cost_type": ch.get("cost_type", "free"),
            "lei_7d": lei,
        })

    return {
        "active_count": len(actives),
        "queued_count": len(queued),
        "paused_count": len(paused),
        "locked_count": len(locked),
        "total_count": len(channels),
        "last_reallocation": reg.get("last_reallocation"),
        "active_weight_sum": round(sum(c.get("weight", 0.0) for c in actives), 6),
        "channels": rows,
    }


__all__ = [
    "load_registry",
    "save_registry",
    "get_channel",
    "get_active_channels",
    "activate_channel",
    "pause_channel",
    "record_channel_metric",
    "get_channel_lei",
    "reallocate_weights",
    "get_portfolio_summary",
]
