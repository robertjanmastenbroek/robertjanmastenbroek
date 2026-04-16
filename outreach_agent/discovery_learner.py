"""
discovery_learner.py — Feedback loop: reply data → discovery query allocation

Reads what's actually converting (reply data, template performance, learning
insights) and returns allocation guidance that rjm-discover can use to spend
its query budget more intelligently.

Key outputs:
  get_query_allocation()    → dict of contact_type → recommended query slots
  get_winning_angles()      → list of high-converting angles/hooks
  get_discovery_priorities() → ranked list of discovery actions

Called by:
  - SKILL.md (rjm-discover) at Step 0 to inform query assembly
  - rjm.py discover learn  (manual inspection)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))

import db

log = logging.getLogger("outreach.discovery_learner")

# ─── Default allocations (used when no data yet) ──────────────────────────────
_DEFAULT_ALLOCATIONS = {
    "podcast": 3,
    "curator": 2,
    "blog":    2,
    "youtube": 1,
}

# ─── Minimum sends before trusting a type's reply rate ────────────────────────
_MIN_SENDS = 10

# ─── Bonus slots awarded for high reply rates ─────────────────────────────────
# contact_type with reply_rate > threshold gets +bonus query slots
_BONUS_THRESHOLDS = [
    (0.20, +2),   # >20% reply rate → +2 query slots
    (0.12, +1),   # >12% reply rate → +1 query slot
    (0.05,  0),   # >5%  reply rate → no change
    (0.00, -1),   # ≤5%  reply rate → -1 query slot (redirect budget)
]


def _reply_rate(total_sent: int, total_replies: int) -> float:
    if not total_sent:
        return 0.0
    return total_replies / total_sent


def _apply_bonus(base: int, rate: float) -> int:
    for threshold, bonus in _BONUS_THRESHOLDS:
        if rate > threshold:
            return max(1, base + bonus)
    return max(1, base)


# ─── Core functions ───────────────────────────────────────────────────────────

def get_query_allocation(total_slots: int = 8) -> dict[str, int]:
    """
    Return a dict mapping contact_type → number of query slots for this run.

    Uses template_performance data from DB to weight allocation toward
    contact types that are actually converting. Falls back to defaults when
    insufficient data.

    total_slots: total search queries available (default 8 for Part A of discover).
    """
    try:
        stats = db.get_template_stats()
    except Exception as e:
        log.warning("Could not read template stats: %s — using defaults", e)
        return _scale_to_total(_DEFAULT_ALLOCATIONS, total_slots)

    # Group by contact_type, sum across templates
    by_type: dict[str, dict] = {}
    for s in stats:
        ctype = s.get("contact_type", "unknown")
        if ctype in ("unknown", "label", "festival", "sync", "booking_agent", "wellness"):
            continue  # don't allocate discovery slots to paused/irrelevant types
        if ctype not in by_type:
            by_type[ctype] = {"total_sent": 0, "total_replies": 0}
        by_type[ctype]["total_sent"]   += s.get("total_sent", 0) or 0
        by_type[ctype]["total_replies"] += s.get("total_replies", 0) or 0

    # Start with defaults, then apply performance bonuses
    allocation = dict(_DEFAULT_ALLOCATIONS)

    for ctype, data in by_type.items():
        if data["total_sent"] < _MIN_SENDS:
            continue  # not enough data
        rate = _reply_rate(data["total_sent"], data["total_replies"])
        base = allocation.get(ctype, 1)
        allocation[ctype] = _apply_bonus(base, rate)
        log.info("Allocation: %s → %d slots (reply_rate=%.1f%%, sends=%d)",
                 ctype, allocation[ctype], rate * 100, data["total_sent"])

    # Also check DB for types that are starved on supply
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT type, COUNT(*) as n FROM contacts GROUP BY type"
            ).fetchall()
        counts = {r["type"]: r["n"] for r in rows}
        # Types with < 20 contacts get an extra slot regardless of reply rate
        for ctype in ["blog", "youtube", "curator", "podcast"]:
            if counts.get(ctype, 0) < 20:
                allocation[ctype] = allocation.get(ctype, 1) + 1
                log.info("Allocation boost for low-supply type: %s (only %d contacts)",
                         ctype, counts.get(ctype, 0))
    except Exception:
        pass

    return _scale_to_total(allocation, total_slots)


def _scale_to_total(allocation: dict[str, int], total_slots: int) -> dict[str, int]:
    """Scale allocation so it sums to exactly total_slots, preserving ratios."""
    current_total = sum(allocation.values())
    if current_total == 0:
        return {k: 0 for k in allocation}

    scaled = {}
    remaining = total_slots
    items = sorted(allocation.items(), key=lambda x: -x[1])  # largest first
    for i, (k, v) in enumerate(items):
        if i == len(items) - 1:
            scaled[k] = max(1, remaining)  # give remainder to last
        else:
            scaled[k] = max(1, round(v / current_total * total_slots))
            remaining -= scaled[k]

    return scaled


def get_winning_angles() -> list[dict]:
    """
    Return a ranked list of subject line / opener angles that have performed well.

    Each dict: {angle, insight_type, based_on_n}

    These can be injected into SKILL.md query templates so we search for
    contacts who are likely to respond to these angles.
    """
    try:
        insights = db.get_recent_insights(limit=15)
    except Exception:
        return []

    angles = []
    for insight in insights:
        content = insight.get("content", "")
        itype = insight.get("insight_type", "pattern")

        # Extract actionable angles (skip meta-observations)
        if any(kw in content.lower() for kw in [
            "faith", "biblical", "spiritual", "halleluyah", "living water",
            "story", "personal narrative", "testimony", "founder",
            "dutch", "nl ", "regional", "language",
            "podcast", "interview", "guest",
        ]):
            angles.append({
                "angle": content[:200],
                "insight_type": itype,
                "based_on_n": insight.get("based_on_n", 0),
            })

    return angles[:5]


def get_discovery_priorities() -> list[dict]:
    """
    Return a ranked list of high-priority discovery actions for this run.

    Each dict: {action, reason, priority (1=highest)}

    Used by SKILL.md to decide what to do first.
    """
    priorities: list[dict] = []

    try:
        db.init_db()
    except Exception:
        return priorities

    # 1. Check playlist supply — if playlists table has verified (no contact) rows,
    #    running find_contacts.py is the highest-ROI action.
    try:
        import playlist_db
        playlist_db.init_playlist_db()
        with playlist_db.get_conn() as conn:
            verified_count = conn.execute(
                "SELECT COUNT(*) FROM playlists WHERE status='verified'"
            ).fetchone()[0]
            contact_found_count = conn.execute(
                "SELECT COUNT(*) FROM playlists WHERE status='contact_found' "
                "AND curator_email IS NOT NULL AND TRIM(curator_email) != ''"
            ).fetchone()[0]
    except Exception:
        verified_count = 0
        contact_found_count = 0

    if contact_found_count > 0:
        priorities.append({
            "action": f"promote_playlist_contacts",
            "reason": f"{contact_found_count} playlist contacts with emails ready to promote",
            "priority": 1,
        })

    if verified_count > 0:
        priorities.append({
            "action": "run_find_contacts",
            "reason": f"{verified_count} playlists need contact research (run find_contacts.py)",
            "priority": 2,
        })

    # 2. Check type gaps in contacts table
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT type, COUNT(*) as n FROM contacts GROUP BY type"
            ).fetchall()
        counts = {r["type"]: r["n"] for r in rows}
    except Exception:
        counts = {}

    for ctype, threshold in [("blog", 10), ("youtube", 20), ("curator", 50)]:
        current = counts.get(ctype, 0)
        if current < threshold:
            priorities.append({
                "action": f"discover_{ctype}",
                "reason": f"Only {current} {ctype} contacts (target: {threshold})",
                "priority": 3,
            })

    # 3. Check query diversity (avoid running same geography 3+ runs in a row)
    try:
        with db.get_conn() as conn:
            recent = conn.execute(
                "SELECT search_query FROM discovery_log "
                "WHERE searched_at > datetime('now', '-24 hours') "
                "ORDER BY searched_at DESC LIMIT 20"
            ).fetchall()
        recent_queries = [r["search_query"] for r in recent]
        recent_text = " ".join(recent_queries).lower()

        # Suggest geographic rotation
        markets = {
            "spain": ("spain", "español", "ibiza"),
            "netherlands": ("netherlands", "nl ", "dutch", "amsterdam"),
            "germany": ("germany", "deutsch", "berlin"),
            "uk": ("united kingdom", "uk ", "british", "london"),
            "brazil": ("brazil", "brasil", "portuguese"),
        }
        for market, keywords in markets.items():
            recent_coverage = sum(1 for kw in keywords if kw in recent_text)
            if recent_coverage < 2:
                priorities.append({
                    "action": f"target_market_{market}",
                    "reason": f"{market.title()} market underrepresented in last 24h queries",
                    "priority": 4,
                })
                break  # suggest only one market per run
    except Exception:
        pass

    return sorted(priorities, key=lambda x: x["priority"])


def print_report():
    """Print a human-readable discovery guidance report."""
    allocation = get_query_allocation()
    angles = get_winning_angles()
    priorities = get_discovery_priorities()

    print("\n=== Discovery Learner Report ===")
    print("\nQuery Allocation (8 slots):")
    for ctype, slots in sorted(allocation.items(), key=lambda x: -x[1]):
        print(f"  {ctype:<12} {slots} query slot{'s' if slots != 1 else ''}")

    if angles:
        print("\nWinning Angles (inject into queries):")
        for a in angles:
            print(f"  [{a['insight_type']}] {a['angle'][:100]}")

    if priorities:
        print("\nPriorities This Run:")
        for p in priorities:
            print(f"  P{p['priority']}: {p['action']} — {p['reason']}")

    print()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")
    print_report()
