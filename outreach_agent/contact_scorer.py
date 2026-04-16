# outreach_agent/contact_scorer.py
"""
Contact Scorer — ranks contacts by expected ROI for the current cycle.

Bridges: Learning Engine <-> Contact DB <-> Scheduler <-> Spotify Growth Tracker.

Scoring factors (total 10.5 points, clamped to 10.0):
  1. Contact type weight from config             (0–3.0)
  2. Research quality (non-empty notes)          (0–2.0)
  3. Historical reply rate for this type         (0–2.0)
  4. Spotify momentum multiplier                 (0–1.5)
  5. Schedule fit (send time proximity)          (0–1.5)
  6. Insights-driven type boost                  (0–0.5)

Factor 6 closes the learning loop: when Claude saves an insight that
mentions a contact type positively (e.g. "podcast replies doubled when…"),
future rankings for that type get a small bonus so we naturally lean into
what's actually working.
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

try:
    import db as _db
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False

try:
    from config import CONTACT_TYPE_WEIGHTS
except ImportError:
    CONTACT_TYPE_WEIGHTS = {}

try:
    import scheduler as _scheduler
    _SCHEDULER_AVAILABLE = True
except ImportError:
    _SCHEDULER_AVAILABLE = False

# Max weight across all types — used to normalise to 3.0 points
_MAX_WEIGHT = max(CONTACT_TYPE_WEIGHTS.values()) if CONTACT_TYPE_WEIGHTS else 100


def _type_score(contact_type: str) -> float:
    """0–3.0 based on CONTACT_TYPE_WEIGHTS."""
    weight = CONTACT_TYPE_WEIGHTS.get(contact_type, 0)
    return round(3.0 * weight / _MAX_WEIGHT, 2) if _MAX_WEIGHT else 0.0


def _research_score(notes: str) -> float:
    """0–2.0: no notes=0, short=0.5, medium=1.0, detailed=2.0."""
    if not notes:
        return 0.0
    length = len(notes.strip())
    if length < 20:
        return 0.5
    if length < 80:
        return 1.0
    return 2.0


def _reply_rate_score(contact_type: str) -> float:
    """0–2.0 from historical reply rates in contacts table."""
    if not _DB_AVAILABLE:
        return 0.0
    try:
        with _db.get_conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status='responded' THEN 1 ELSE 0 END) as replies
                FROM contacts WHERE contact_type = ? AND status IN ('sent','responded','followup_sent')
            """, (contact_type,)).fetchone()
            if not row or row["total"] < 3:
                return 0.5  # not enough data — neutral
            rate = row["replies"] / row["total"]
            return round(min(rate * 4.0, 2.0), 2)  # 50% reply rate = 2.0
    except Exception:
        return 0.0


def _spotify_momentum() -> float:
    """0–1.5: positive listener growth gives a boost to all outreach scores."""
    if not _DB_AVAILABLE:
        return 0.5
    try:
        with _db.get_conn() as conn:
            rows = conn.execute("""
                SELECT listeners FROM listener_log ORDER BY logged_at DESC LIMIT 2
            """).fetchall()
            if len(rows) < 2:
                return 0.5
            delta = rows[0]["listeners"] - rows[1]["listeners"]
            if delta > 500:
                return 1.5
            if delta > 0:
                return 1.0
            return 0.25  # declining — prioritise other channels
    except Exception:
        return 0.5


def _schedule_fit_score(email: str) -> float:
    """0–1.5: how well the current hour matches the contact's best send hour."""
    if not _SCHEDULER_AVAILABLE:
        return 0.75
    try:
        best_hour = _scheduler.best_send_time(email)
        current_hour = datetime.now().hour
        diff = abs(current_hour - best_hour)
        if diff == 0:
            return 1.5
        if diff <= 1:
            return 1.0
        if diff <= 3:
            return 0.5
        return 0.0
    except Exception:
        return 0.75


_POSITIVE_SIGNALS = (
    "lifted", "lift", "boost", "boosted", "double", "doubled", "up", "higher",
    "improved", "increase", "increased", "wins", "won", "stronger", "best",
    "outperform", "outperformed", "better",
)


def _insights_boost(contact_type: str) -> float:
    """0–0.5 bonus when recent insights mention this type in a positive frame.

    We look at the last few insights and grep for the type name alongside a
    positive-signal word. One matching insight → 0.25, two or more → 0.5.
    Zero when no data. Failure-safe — never raises.
    """
    if not _DB_AVAILABLE or not contact_type:
        return 0.0
    try:
        insights = _db.get_recent_insights(limit=10) or []
    except Exception:
        return 0.0

    ctype_lc = contact_type.lower().strip()
    hits = 0
    for row in insights:
        content = (row["content"] if "content" in row.keys() else "") or ""
        content_lc = content.lower()
        if ctype_lc not in content_lc:
            continue
        if any(signal in content_lc for signal in _POSITIVE_SIGNALS):
            hits += 1

    if hits >= 2:
        return 0.5
    if hits == 1:
        return 0.25
    return 0.0


def score(contact: dict) -> float:
    """
    Score a single contact dict (keys: email, contact_type, status, research_notes).
    Returns float 0.0–10.0.
    """
    s = 0.0
    s += _type_score(contact.get("contact_type", ""))
    s += _research_score(contact.get("research_notes", "") or "")
    s += _reply_rate_score(contact.get("contact_type", ""))
    s += _spotify_momentum()
    s += _schedule_fit_score(contact.get("email", ""))
    s += _insights_boost(contact.get("contact_type", ""))
    return round(min(s, 10.0), 3)


def rank(contacts: list) -> list:
    """Return contacts sorted by score descending. Adds '_score' key to each."""
    scored = []
    for c in contacts:
        c = dict(c)
        c["_score"] = score(c)
        scored.append(c)
    return sorted(scored, key=lambda x: x["_score"], reverse=True)
