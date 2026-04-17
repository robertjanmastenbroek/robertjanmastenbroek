"""
RJM Outreach Agent — Follow-up Engine

Identifies contacts who received an initial email 7+ days ago without responding,
generates follow-up content, and sends it via the Gmail API.

Design principles:
  - Exactly ONE follow-up per contact (no pestering)
  - Under 80 words — just reopening the door, not repeating the pitch
  - Uses the fast model (haiku) — follow-ups are short and formulaic
  - Sends in-thread when gmail_thread_id is stored, fresh email otherwise
"""

import logging
import time

import db
import gmail_client
import scheduler
import template_engine
from config import FOLLOWUP_DAYS, DRAFT_MODE

log = logging.getLogger("outreach.followup")


def get_followup_queue(limit: int = 20) -> list[dict]:
    """
    Return contacts eligible for first follow-up:
    - Status 'sent', sent > FOLLOWUP_DAYS ago, no reply
    """
    candidates = db.get_followup_candidates(days_since_send=FOLLOWUP_DAYS)
    eligible = candidates[:limit]
    log.info("Follow-up queue: %d contacts eligible (checked %d candidates)",
             len(eligible), len(candidates))
    return eligible


def get_followup2_queue(limit: int = 10) -> list[dict]:
    """
    Return contacts eligible for second follow-up:
    - Status 'followup_sent', first follow-up sent > 7 days ago, no reply
    """
    candidates = db.get_followup2_candidates(days_since_followup1=7)
    eligible = candidates[:limit]
    log.info("Follow-up2 queue: %d contacts eligible (checked %d candidates)",
             len(eligible), len(candidates))
    return eligible


def _send_followup(contact: dict, is_second: bool = False) -> bool:
    """
    Generate and send one follow-up email for a contact.
    Returns True on success, False on failure.
    """
    email      = contact["email"]
    name       = contact.get("name", "")
    thread_id  = contact.get("gmail_thread_id") or ""
    msg_id     = contact.get("gmail_message_id") or ""

    try:
        subject, body = template_engine.generate_followup_email(contact, is_second=is_second)
    except Exception as exc:
        log.error("Failed to generate follow-up for %s: %s", email, exc)
        return False

    try:
        if DRAFT_MODE:
            result = gmail_client.create_draft(
                to_email=email,
                to_name=name,
                subject=subject,
                body=body,
                reply_to_thread_id=thread_id or None,
                in_reply_to_message_id=msg_id or None,
            )
            new_msg_id = result.get("message", {}).get("id", "draft")
            log.info("[DRAFT] Follow-up draft created for %s — %r", email, subject)
        else:
            result = gmail_client.send_email(
                to_email=email,
                to_name=name,
                subject=subject,
                body=body,
                reply_to_thread_id=thread_id or None,
                in_reply_to_message_id=msg_id or None,
            )
            new_msg_id = result.get("id", "")
            log.info("Follow-up sent to %s — %r", email, subject)

        if is_second:
            db.mark_followup2_sent(email, new_msg_id, subject, body)
        else:
            db.mark_followup_sent(email, new_msg_id, subject, body[:300])

        return True

    except Exception as exc:
        log.error("Follow-up send failed for %s: %s", email, exc)
        return False


def run_followup_batch(max_generates: int = 10) -> dict:
    """
    Generate and send follow-ups for eligible contacts.
    Handles both first and second follow-ups.
    Returns summary dict: {followups_sent, followups_failed, followup2_sent, followup2_failed}
    """
    sent1 = 0
    failed1 = 0
    sent2 = 0
    failed2 = 0

    # ── First follow-ups ────────────────────────────────────────────────────────
    queue1 = get_followup_queue(limit=max_generates)
    for contact in queue1:
        ok = _send_followup(contact, is_second=False)
        if ok:
            sent1 += 1
        else:
            failed1 += 1
        if sent1 + sent2 < max_generates:
            time.sleep(scheduler.random_interval())

    # ── Second follow-ups (use remaining quota) ─────────────────────────────────
    # Only successful sends count against the quota — failures don't consume capacity.
    remaining = max_generates - sent1
    if remaining > 0:
        queue2 = get_followup2_queue(limit=remaining)
        for contact in queue2:
            ok = _send_followup(contact, is_second=True)
            if ok:
                sent2 += 1
            else:
                failed2 += 1
            if sent1 + sent2 < max_generates:
                time.sleep(2)

    log.info(
        "Follow-up batch complete — 1st: %d sent / %d failed | 2nd: %d sent / %d failed",
        sent1, failed1, sent2, failed2,
    )
    return {
        "followups_sent":    sent1,
        "followups_failed":  failed1,
        "followup2_sent":    sent2,
        "followup2_failed":  failed2,
    }
