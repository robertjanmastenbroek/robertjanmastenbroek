"""
RJM Reply Responder — Autonomous reply pipeline

Reads classified replies from the DB and sends personalised responses via Gmail.
Handles three intents:
  booking_intent / booking_inquiry — offer 3 interview slots (Tue/Wed/Thu 11-17 CET)
  positive                         — thank + deepen the conversation / pitch track
  question                         — answer what they asked (links, stats, SoundCloud)

Skips:
  - Contacts already replied to (date_replied IS NOT NULL)
  - Contacts with no gmail_thread_id AND no usable context
  - Negative / auto-reply intents

Usage:
  python3 reply_responder.py            # run and send
  python3 reply_responder.py --dry-run  # print generated replies, don't send
"""

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import db
import gmail_client
from brand_context import (
    COMPACT_STORY, get_voice_rules, get_track_for_contact,
    TRACK_SCRIPTURE, SPOTIFY_ARTIST_URL,
)
from config import FROM_NAME, CLAUDE_MODEL_FAST
from template_engine import _call_claude

log = logging.getLogger("outreach.reply_responder")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

BASE_DIR = Path(__file__).parent
DB_PATH  = BASE_DIR / "outreach.db"


# ─── DB migration ─────────────────────────────────────────────────────────────

def _ensure_schema():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    # Add date_replied column if missing
    c.execute("PRAGMA table_info(contacts)")
    cols = {r[1] for r in c.fetchall()}
    if "date_replied" not in cols:
        c.execute("ALTER TABLE contacts ADD COLUMN date_replied TEXT")
        conn.commit()
        log.info("Added date_replied column to contacts")
    conn.close()


# ─── Fetch pending replies ─────────────────────────────────────────────────────

def get_and_claim_pending_replies() -> list[dict]:
    """
    Atomically fetch and claim pending replies in a single transaction.
    Sets date_replied = 'processing' before returning, so concurrent runs
    cannot pick up the same contacts.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN EXCLUSIVE")
    c = conn.cursor()
    c.execute("""
        SELECT email, name, type, genre, notes, website,
               reply_intent, reply_action, reply_message_id,
               gmail_thread_id, response_snippet,
               sent_subject, sent_body
        FROM contacts
        WHERE reply_intent IN ('booking_intent','booking_inquiry','positive','question')
          AND reply_action IS NOT NULL
          AND date_replied IS NULL
        ORDER BY reply_classified_at ASC
    """)
    rows = [dict(r) for r in c.fetchall()]
    if rows:
        emails = [r["email"] for r in rows]
        placeholders = ",".join("?" * len(emails))
        c.execute(
            f"UPDATE contacts SET date_replied = 'processing' WHERE email IN ({placeholders})",
            emails,
        )
    conn.commit()
    conn.close()
    return rows


def mark_replied(email: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE contacts SET date_replied = ? WHERE email = ?",
        (datetime.now().isoformat(), email)
    )
    conn.commit()
    conn.close()


def unmark_claimed(email: str):
    """Release a claim if sending failed, so it can be retried next cycle."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE contacts SET date_replied = NULL WHERE email = ? AND date_replied = 'processing'",
        (email,)
    )
    conn.commit()
    conn.close()


# ─── Thread fetch ──────────────────────────────────────────────────────────────

def _get_reply_body(thread_id: str) -> str:
    """Fetch the latest incoming message body from a Gmail thread."""
    if not thread_id:
        return ""
    try:
        messages = gmail_client.get_thread_messages(thread_id)
        # Find last message NOT from us
        from config import FROM_EMAIL
        for msg in reversed(messages):
            sender = msg.get("from", "")
            if FROM_EMAIL.lower() not in sender.lower():
                return msg.get("body", msg.get("snippet", ""))[:1000]
    except Exception as e:
        log.warning("Could not fetch thread %s: %s", thread_id, e)
    return ""


# ─── Slot calculator ──────────────────────────────────────────────────────────

def _next_interview_slots(n=3) -> list[str]:
    """Return next n Tuesday/Wednesday/Thursday dates as 'Day Month D' strings."""
    target_weekdays = {1: "Tuesday", 2: "Wednesday", 3: "Thursday"}  # Mon=0
    slots = []
    day = datetime.now() + timedelta(days=1)
    while len(slots) < n:
        if day.weekday() in target_weekdays:
            slots.append(f"{target_weekdays[day.weekday()]} {day.strftime('%B %-d')}")
        day += timedelta(days=1)
    return slots


# ─── Response generation ──────────────────────────────────────────────────────

def _generate_booking_reply(contact: dict, reply_body: str) -> tuple[str, str]:
    """Generate subject + body for a booking inquiry."""
    slots = _next_interview_slots(3)
    slot_list = "\n".join(f"  - {s}, 11:00–17:00 CET" for s in slots)

    prompt = f"""You are writing a reply email on behalf of {FROM_NAME}, a DJ/producer based in Tenerife.

{COMPACT_STORY}

{get_voice_rules()}

CONTEXT:
- Contact: {contact['name']} ({contact['type']}) — {contact.get('notes','') or contact.get('genre','')}
- Their reply: "{reply_body or contact.get('response_snippet','')}"
- Original email subject we sent: "{contact.get('sent_subject','')}"

TASK: Write a warm, brief reply accepting the booking/podcast inquiry.
Include these 3 availability slots (pick the 3 below, offer all three):
{slot_list}

Instructions:
- Mention Tenerife (CET timezone)
- Offer to send a press kit / one-sheet once they confirm
- Keep it under 6 sentences. Human, warm, not corporate.
- Sign off as: Robert-Jan
- Output JSON only: {{"subject": "...", "body": "..."}}
- Subject line: keep it as "Re: " + original subject or a short natural continuation"""

    raw = _call_claude(prompt, model=CLAUDE_MODEL_FAST, timeout=120)
    return _parse_subject_body(raw, contact)


def _generate_positive_reply(contact: dict, reply_body: str) -> tuple[str, str]:
    """Generate subject + body for a positive/interested reply."""
    # Use canonical track selection from brand_context (fixes wrong hardcoded Spotify links)
    track_entry = get_track_for_contact(
        genre=contact.get("genre") or "",
        notes=contact.get("notes") or "",
    )
    track_label = f"{track_entry['title']} ({track_entry['bpm']} BPM {track_entry['genre']})"
    track_link  = track_entry["spotify"]

    prompt = f"""You are writing a reply email on behalf of {FROM_NAME}, a DJ/producer based in Tenerife.

{COMPACT_STORY}

{get_voice_rules()}

CONTEXT:
- Contact: {contact['name']} ({contact['type']}) — {contact.get('notes','') or contact.get('genre','')}
- Their reply: "{reply_body or contact.get('response_snippet','')}"
- Original email subject we sent: "{contact.get('sent_subject','')}"
- Best track fit: {track_label} — {track_link}

TASK: Write a warm, brief follow-up that deepens the connection.
- Thank them genuinely (reference something specific from their reply if possible)
- Naturally share the Spotify link for {track_entry['title']}
- Ask one clear next-step question (e.g. "Would this fit your playlist?" or "Happy to send a WAV if that works better")
- Under 5 sentences. No fluff.
- Sign off as: Robert-Jan
- Output JSON only: {{"subject": "...", "body": "..."}}"""

    raw = _call_claude(prompt, model=CLAUDE_MODEL_FAST, timeout=120)
    return _parse_subject_body(raw, contact)


def _generate_question_reply(contact: dict, reply_body: str) -> tuple[str, str]:
    """Generate subject + body answering their question."""
    prompt = f"""You are writing a reply email on behalf of {FROM_NAME}, a DJ/producer based in Tenerife.

{COMPACT_STORY}

{get_voice_rules()}

CONTEXT:
- Contact: {contact['name']} ({contact['type']}) — {contact.get('notes','') or contact.get('genre','')}
- Their reply / question: "{reply_body or contact.get('response_snippet','')}"
- Original email subject we sent: "{contact.get('sent_subject','')}"
- Suggested action from classifier: "{contact.get('reply_action','')}"

KEY LINKS to include as relevant:
- Spotify artist: {SPOTIFY_ARTIST_URL}
- Renamed (tribal techno, 130 BPM): {TRACK_SCRIPTURE['Renamed']['spotify']}
- Halleluyah (psytrance, 140 BPM): {TRACK_SCRIPTURE['Halleluyah']['spotify']}
- Press kit: available on request (mention it)

TASK: Answer their question directly and completely.
- Give them exactly what they asked for
- Keep it concise and helpful
- If they want SoundCloud links, explain tracks are on Spotify (public) and offer a private WAV via email
- Sign off as: Robert-Jan
- Output JSON only: {{"subject": "...", "body": "..."}}"""

    raw = _call_claude(prompt, model=CLAUDE_MODEL_FAST, timeout=120)
    return _parse_subject_body(raw, contact)


def _parse_subject_body(raw: str, contact: dict) -> tuple[str, str]:
    """Parse Claude's JSON output into (subject, body). Falls back gracefully."""
    import json, re
    # Strip markdown code fences if present
    clean = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    try:
        data = json.loads(clean)
        return data["subject"], data["body"]
    except Exception:
        # Try to extract with regex
        subj = re.search(r'"subject"\s*:\s*"([^"]+)"', raw)
        body = re.search(r'"body"\s*:\s*"([\s\S]+?)"\s*}', raw)
        if subj and body:
            return subj.group(1), body.group(1).replace("\\n", "\n")
        # Last resort: return raw as body
        original_subj = contact.get("sent_subject", "following up") or "following up"
        return f"Re: {original_subj}", raw


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    _ensure_schema()

    if dry_run:
        # Dry-run: read-only query, no claims
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(str(DB_PATH))
        conn.row_factory = _sqlite3.Row
        pending = [dict(r) for r in conn.execute("""
            SELECT email, name, type, genre, notes, website,
                   reply_intent, reply_action, reply_message_id,
                   gmail_thread_id, response_snippet,
                   sent_subject, sent_body
            FROM contacts
            WHERE reply_intent IN ('booking_intent','booking_inquiry','positive','question')
              AND reply_action IS NOT NULL
              AND date_replied IS NULL
            ORDER BY reply_classified_at ASC
        """).fetchall()]
        conn.close()
    else:
        # Live run: atomically claim contacts before processing
        pending = get_and_claim_pending_replies()

    if not pending:
        log.info("No pending replies to respond to.")
        return {"sent": 0, "skipped": 0, "failed": 0}

    log.info("Found %d replies to respond to", len(pending))

    sent = skipped = failed = 0

    for contact in pending:
        email       = contact["email"]
        name        = contact["name"] or email
        thread_id   = contact.get("gmail_thread_id")
        intent      = contact.get("reply_intent", "")
        reply_body  = _get_reply_body(thread_id) if thread_id else ""

        log.info("Processing [%s] %s — %s", intent.upper(), email, name)

        try:
            # ── Guard: check Gmail thread directly before sending ──────────────
            # Prevents duplicates when master agent or other paths already replied
            # without updating the DB.
            if thread_id and gmail_client.already_replied_in_thread(
                thread_id, contact.get("reply_message_id")
            ):
                log.info("SKIP %s — already replied in Gmail thread %s", email, thread_id)
                if not dry_run:
                    mark_replied(email)   # sync DB with reality
                skipped += 1
                continue
            # ──────────────────────────────────────────────────────────────────

            if intent in ("booking_intent", "booking_inquiry"):
                subject, body = _generate_booking_reply(contact, reply_body)
            elif intent == "positive":
                subject, body = _generate_positive_reply(contact, reply_body)
            elif intent == "question":
                subject, body = _generate_question_reply(contact, reply_body)
            else:
                log.info("Skipping %s — intent '%s' not handled", email, intent)
                if not dry_run:
                    unmark_claimed(email)
                skipped += 1
                continue

            if dry_run:
                print(f"\n{'='*60}")
                print(f"TO:      {name} <{email}>")
                print(f"INTENT:  {intent}")
                print(f"SUBJECT: {subject}")
                print(f"---\n{body}\n")
                sent += 1
                continue

            # Send as reply in the existing thread
            result = gmail_client.send_email(
                to_email=email,
                to_name=name,
                subject=subject,
                body=body,
                reply_to_thread_id=thread_id,
                in_reply_to_message_id=contact.get("reply_message_id"),
            )
            mark_replied(email)
            log.info("Reply sent to %s — message_id=%s", email, result.get("id"))
            sent += 1

        except Exception as exc:
            log.error("Failed to reply to %s: %s", email, exc)
            unmark_claimed(email)  # release claim so it retries next cycle
            failed += 1

    log.info("Reply responder done — sent: %d, skipped: %d, failed: %d", sent, skipped, failed)
    return {"sent": sent, "skipped": skipped, "failed": failed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send autonomous replies to classified contacts")
    parser.add_argument("--dry-run", action="store_true", help="Print replies without sending")
    args = parser.parse_args()
    result = run(dry_run=args.dry_run)
    sys.exit(0 if result["failed"] == 0 else 1)
