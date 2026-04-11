"""
Event backbone for the RJM hive-mind.

Every agent publishes events on key actions.
Every orchestrator subscribes to relevant event types.

Event type convention: <domain>.<action>
  email.sent            — outreach email delivered
  email.followup_sent   — follow-up delivered
  reply.detected        — inbound reply processed
  bounce.detected       — email bounced
  spotify.listeners_logged — Spotify monthly listeners recorded
  content.post_published — video posted to a platform
  template.insight_generated — learning engine produced new insight
  release.campaign_fired — release trigger activated
"""

import json
import logging
from datetime import datetime

import db

log = logging.getLogger("outreach.events")


def publish(event_type: str, source: str, payload: dict) -> int:
    """Publish an event. Returns new event id."""
    now = datetime.utcnow().isoformat()
    with db.get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO events (event_type, source, payload, created_at) VALUES (?,?,?,?)",
            (event_type, source, json.dumps(payload), now),
        )
        return cursor.lastrowid


def subscribe(
    event_types: list[str],
    since: str | None = None,
    exclude_consumed_by: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return events matching event_types, newest first."""
    placeholders = ",".join("?" * len(event_types))
    params: list = list(event_types)
    where_clauses = [f"event_type IN ({placeholders})"]

    if since:
        where_clauses.append("created_at > ?")
        params.append(since)

    if exclude_consumed_by:
        where_clauses.append("(consumed_by IS NULL OR consumed_by NOT LIKE ?)")
        params.append(f"%{exclude_consumed_by}%")

    where = " AND ".join(where_clauses)
    params.append(limit)

    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM events WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def mark_consumed(event_id: int, consumer: str) -> None:
    """Mark an event as consumed by a named consumer."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT consumed_by FROM events WHERE id=?", (event_id,)
        ).fetchone()
        if row is None:
            return
        existing = row["consumed_by"] or ""
        if consumer in existing.split(","):
            return
        updated = f"{existing},{consumer}".lstrip(",")
        conn.execute("UPDATE events SET consumed_by=? WHERE id=?", (updated, event_id))


def recent(event_type: str | None = None, limit: int = 20) -> list[dict]:
    """Return the N most recent events, optionally filtered by type."""
    with db.get_conn() as conn:
        if event_type:
            rows = conn.execute(
                "SELECT * FROM events WHERE event_type=? ORDER BY created_at DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]
