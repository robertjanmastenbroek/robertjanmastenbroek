"""
RJM Outreach Agent — Reply Classifier (Claude CLI edition)

Classifies incoming replies using Claude CLI — no API key, uses your Max plan.
Runs after reply_detector finds new replies and routes each one to the right
action so nothing sits in a generic "responded" bucket.

Intents:
  positive        — interested, wants to hear more / positive engagement
  playlist_added  — curator confirmed adding the track (WIN)
  booking_intent  — wants to book RJM for a gig/event (URGENT)
  question        — asking for EPK / Spotify link / stems / social / etc.
  negative_fit    — polite decline, not the right fit right now
  negative_hard   — clear no, don't contact again
  auto_reply      — out-of-office or bot response, not a real human (revert to sent)
  unsubscribe     — wants off the list (add to dead_addresses)

Usage:
  from reply_classifier import classify_pending
  result = classify_pending()   # classifies all unclassified responded contacts
"""

import json
import logging
import re

import db
import gmail_client
from template_engine import _call_claude
from config import CLAUDE_MODEL_FAST
from bounce import add_confirmed_dead_address

log = logging.getLogger("outreach.reply_classifier")

# ─── Intent definitions ───────────────────────────────────────────────────────

VALID_INTENTS = {
    "positive",
    "playlist_added",
    "booking_intent",
    "question",
    "negative_fit",
    "negative_hard",
    "auto_reply",
    "unsubscribe",
}

_LOG_LABELS = {
    "positive":       "POSITIVE",
    "playlist_added": "*** TRACK ADDED TO PLAYLIST ***",
    "booking_intent": "!!! BOOKING INQUIRY !!!",
    "question":       "QUESTION — needs answer",
    "negative_fit":   "Negative (fit)",
    "negative_hard":  "Negative (hard no)",
    "auto_reply":     "Auto-reply (bot/OOO)",
    "unsubscribe":    "Unsubscribe request",
}


# ─── Prompt ───────────────────────────────────────────────────────────────────

def _build_prompt(contact: dict, reply_body: str) -> str:
    name          = contact.get("name") or contact["email"]
    ctype         = contact.get("type") or "curator"
    sent_subject  = contact.get("sent_subject") or "(unknown)"
    text          = (reply_body or "").strip()[:1500]

    return f"""You are classifying an email reply for RJM (Robert-Jan Mastenbroek), a Dutch DJ/producer
doing cold outreach to Spotify playlist curators and podcast hosts.

CONTACT: {name} ({ctype})
OUR SUBJECT: {sent_subject}
THEIR REPLY:
{text}

Classify into exactly one intent:
- positive: interested, wants to hear more, positive engagement (but hasn't confirmed adding yet)
- playlist_added: explicitly confirmed they added the track to a playlist
- booking_intent: interested in booking RJM for a show, festival, or event
- question: asking for more info — Spotify link, EPK, stems, social links, pricing, etc.
- negative_fit: polite decline, track doesn't fit their playlist or show right now
- negative_hard: clear no, not interested, stop contacting
- auto_reply: automated out-of-office or bot response, not a real human reply
- unsubscribe: asking to be removed from the contact list

Respond with ONLY valid JSON, no markdown, no explanation:
{{"intent": "...", "confidence": 0.95, "summary": "one sentence describing the reply", "suggested_action": "specific next step for RJM or the agent"}}"""


# ─── Core classification ──────────────────────────────────────────────────────

def _parse_claude_response(raw: str, email: str) -> dict | None:
    """Parse JSON from Claude's response. Handles prose wrapping around JSON."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Find the first JSON object in the response
    match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    log.warning("No valid JSON in Claude response for %s: %r", email, raw[:200])
    return None


def classify_one(contact: dict) -> dict | None:
    """
    Classify a single contact's reply.
    Returns the classification dict (with keys: intent, confidence, summary,
    suggested_action) or None on failure.
    """
    email      = contact["email"]
    message_id = contact.get("reply_message_id") or ""

    # Prefer full body; fall back to stored snippet
    reply_body = ""
    if message_id:
        try:
            reply_body = gmail_client.get_full_message_body(message_id)
        except Exception as exc:
            log.debug("Could not fetch full body for %s: %s", email, exc)

    if not reply_body:
        reply_body = contact.get("response_snippet") or ""

    if not reply_body:
        log.warning("No reply text for %s — skipping", email)
        return None

    prompt = _build_prompt(contact, reply_body)

    try:
        raw = _call_claude(prompt, model=CLAUDE_MODEL_FAST, timeout=60)
    except Exception as exc:
        log.error("Claude classification failed for %s: %s", email, exc)
        return None

    result = _parse_claude_response(raw, email)
    if not result:
        return None

    # Normalise intent
    intent = result.get("intent", "").lower().strip()
    if intent not in VALID_INTENTS:
        log.warning("Unknown intent %r for %s — defaulting to negative_fit", intent, email)
        intent = "negative_fit"
    result["intent"] = intent

    return result


# ─── Routing ──────────────────────────────────────────────────────────────────

def _apply_routing(contact: dict, classification: dict):
    """Write classification to DB and take the appropriate action per intent."""
    email   = contact["email"]
    intent  = classification["intent"]
    summary = classification.get("summary", "")
    action  = classification.get("suggested_action", "")

    log.info("[%s] %s — %s | Action: %s",
             _LOG_LABELS.get(intent, intent.upper()), email, summary, action)

    # Persist classification — always
    db.update_reply_classification(email=email, intent=intent, suggested_action=action)

    if intent == "playlist_added":
        db.update_contact(email, status="won")
        log.info("*** WIN *** %s confirmed track in playlist", email)

    elif intent == "booking_intent":
        # Leave as "responded" so it stays visible; flag loudly
        log.info("!!! BOOKING LEAD — respond manually: %s", email)

    elif intent in ("negative_fit", "negative_hard"):
        db.update_contact(email, status="closed")

    elif intent == "auto_reply":
        # Not a real response — revert status so we catch the real reply later.
        # Keep reply_classified_at set so we don't re-classify the same OOO endlessly.
        db.update_contact(email, status="sent",
                          response_snippet=None, date_response_received=None)
        log.info("Auto-reply from %s — reverted to sent", email)

    elif intent == "unsubscribe":
        db.update_contact(email, status="closed")
        add_confirmed_dead_address(email)
        log.info("Unsubscribed %s — added to dead_addresses", email)

    # positive / question: stay as "responded"; suggested_action is logged above
    # and will surface in master_agent briefings


# ─── Batch entry point ────────────────────────────────────────────────────────

def classify_pending() -> dict:
    """
    Classify all replied contacts that haven't been classified yet.
    Idempotent — safe to call every agent cycle.
    Returns {"classified": n, "failed": n}.
    """
    unclassified = db.get_unclassified_replies()
    if not unclassified:
        log.debug("No unclassified replies")
        return {"classified": 0, "failed": 0}

    log.info("Classifying %d unclassified %s...",
             len(unclassified),
             "reply" if len(unclassified) == 1 else "replies")

    classified = 0
    failed     = 0

    for contact in unclassified:
        result = classify_one(contact)
        if result:
            _apply_routing(contact, result)
            classified += 1
        else:
            failed += 1

    log.info("Classification complete — classified: %d, failed: %d", classified, failed)
    return {"classified": classified, "failed": failed}
