#!/usr/bin/env python3
"""
RJM Autonomous Outreach Agent
==============================

Robert-Jan Mastenbroek — automated email outreach to labels, curators,
festivals, YouTube channels, and podcast hosts.

Usage:
  python agent.py run          # Run one outreach cycle (call this via cron)
  python agent.py setup        # First-time Gmail OAuth setup + DB init
  python agent.py status       # Show pipeline summary
  python agent.py report       # Full performance report
  python agent.py add          # Add a contact manually (interactive)
  python agent.py queue        # Show pending contacts
  python agent.py preview <email>  # Preview email for a contact (no send)
  python agent.py followups    # Run follow-up batch now (outside cron)
  python agent.py verify_all   # Bounce-check all pending contacts
  python agent.py import <csv> # Import contacts from CSV file
"""

import argparse
import csv
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# ─── Logging setup (file + console) ──────────────────────────────────────────
from config import LOG_PATH, DRAFT_MODE, BATCH_SIZE, WARM_UP_DAILY_CAP, MAX_SEND_ATTEMPTS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_PATH), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("outreach.agent")

import db
import bounce
import events
import gmail_client
import template_engine
import scheduler
import reply_detector
import reply_classifier
import reply_responder
import followup_engine
import learning


def _run_step(step_name: str, fn, *args, **kwargs):
    """Run a non-fatal cycle step with structured logging + event on failure.

    Replaces the silent ``try/except log.warning`` pattern that hid recurring
    problems (Gmail OAuth expiry, rate limits, schema drift). Every failure is
    now published to the event bus as ``agent.step_failed`` so rjm.py status
    and the master agent can surface it.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log.warning("Cycle step '%s' failed (non-fatal): %s", step_name, exc, exc_info=True)
        try:
            events.publish(
                "agent.step_failed",
                source="outreach_agent.cmd_run",
                payload={
                    "step": step_name,
                    "error": str(exc)[:500],
                    "error_type": type(exc).__name__,
                },
            )
        except Exception as pub_exc:
            log.warning("Could not publish agent.step_failed event: %s", pub_exc)
        return None


# ─── Core send pipeline ───────────────────────────────────────────────────────

def _verify_pending_contacts():
    """Bounce-check all contacts still in 'new' status."""
    new_contacts = db.get_contacts_by_status("new")
    if not new_contacts:
        log.info("No new contacts to verify")
        return 0

    log.info("Verifying %d new contacts...", len(new_contacts))
    verified = 0
    rejected = 0

    for c in new_contacts:
        email = c["email"]
        result, reason = bounce.verify_email(email)
        if result == "invalid":
            log.warning("Pre-check FAILED for %s: %s", email, reason)
            db.mark_bounced_full(email, reason, bounce_type="pre-check")
            rejected += 1
        else:
            # agent_discovered contacts go to warm_up (throttled queue).
            # Trusted sources (csv_legacy, manual, podcast_legacy) go straight to verified.
            source = c.get("source", "manual")
            if source == "agent_discovered":
                db.mark_warm_up(email)
            else:
                db.mark_verified(email)
            verified += 1
        time.sleep(0.3)   # Be gentle on DNS

    log.info("Verification done — verified: %d, rejected: %d", verified, rejected)
    return verified


def _send_batch(batch_size: int) -> dict:
    """
    Send a batch of emails to verified/queued contacts.
    Returns summary dict.
    """
    # Pull verified contacts — researched ones first (higher reply rates)
    contacts = db.get_verified_contacts_prioritized(limit=batch_size * 3)
    if not contacts:
        # Fall back to 'queued' (shouldn't normally happen but safe)
        contacts = db.get_contacts_by_status("queued", limit=batch_size * 3)

    # Append warm_up contacts up to the daily cap.
    # They go after verified contacts so trusted sources are always prioritised.
    warm_up_sent_today = db.get_warm_up_sent_today()
    warm_up_budget = max(0, WARM_UP_DAILY_CAP - warm_up_sent_today)
    if warm_up_budget > 0:
        warm_up_contacts = db.get_warm_up_contacts(limit=warm_up_budget)
        contacts = contacts + warm_up_contacts
        if warm_up_contacts:
            log.info(
                "Warm-up queue: %d eligible, budget %d/day (used %d today)",
                len(warm_up_contacts), WARM_UP_DAILY_CAP, warm_up_sent_today,
            )

    if not contacts:
        log.info("No verified contacts in queue to send")
        return {"sent": 0, "failed": 0, "skipped": 0}

    sent = 0
    failed = 0
    skipped = 0

    # Reload dead address/domain lists so any bounces detected this cycle
    # are honoured before we send anything new.
    bounce._dead_domains  = None
    bounce._dead_addresses = None
    bounce._load_dead_lists()

    for contact in contacts:
        if sent >= batch_size:
            break

        scheduler.wait_for_interval()

        window = scheduler.SendWindow()
        if not window.can_send:
            log.info("Send window closed: %s", window.status())
            break

        email = contact["email"]
        name  = contact.get("name", "")
        ctype = contact.get("type", "curator")

        # Last-resort format sanity: reject malformed addresses before touching Gmail
        email_lc = email.lower()
        _parts = email_lc.split("@")
        if len(_parts) != 2 or not _parts[0] or not _parts[1]:
            log.warning("Skipping %s — malformed address (no @)", email)
            db.update_contact(email, status="skip")
            skipped += 1
            continue
        _tld = _parts[1].split(".")[-1]
        if not _tld.isalpha() or len(_tld) < 2:
            log.warning("Skipping %s — invalid TLD '%s'", email, _tld)
            db.update_contact(email, status="skip")
            skipped += 1
            continue

        # Final pre-send dead-list check (catches bounces found in this cycle)
        domain   = _parts[1]
        if email_lc in bounce._dead_addresses or domain in bounce._dead_domains:
            log.info("Skipping %s — confirmed dead (caught before send)", email)
            db.update_contact(email, status="skip")
            skipped += 1
            continue

        # Mark as queued so we don't double-process on concurrent runs
        db.mark_queued(email)

        # Get learning context for this contact type
        try:
            learn_ctx = learning.get_learning_context_for_template(ctype)
        except Exception:
            learn_ctx = ""

        # Generate personalised email
        try:
            subject, body = template_engine.generate_email(contact, learn_ctx)
        except template_engine.BrandGateRejected as exc:
            # Brand gate refused two drafts in a row — do NOT ship, do NOT
            # dead_letter, do NOT bump attempts. This is a content problem,
            # not a contact problem. Put the row back to 'verified' so the
            # next cycle can try again (by then, learning may have shifted).
            log.warning("Brand gate rejected %s: %s", email, exc)
            db.update_contact(email, status="verified")
            try:
                import events as _events
                _events.publish(
                    "template.brand_gate_rejected",
                    source="agent._send_batch",
                    payload={"email": email, "reason": str(exc)},
                )
            except Exception:
                pass
            skipped += 1
            continue
        except Exception as exc:
            log.error("Email generation failed for %s: %s", email, exc)
            attempts = db.get_send_attempts(email)
            if attempts >= MAX_SEND_ATTEMPTS:
                log.warning(
                    "Dead-lettering %s after %d failed attempts (last: template crash)",
                    email, attempts,
                )
                db.mark_dead_letter(email, reason=f"template crash: {exc}")
            else:
                db.update_contact(email, status="verified")   # put back in queue
            failed += 1
            continue

        # Send or draft
        try:
            if DRAFT_MODE:
                result = gmail_client.create_draft(
                    to_email=email,
                    to_name=name,
                    subject=subject,
                    body=body,
                )
                msg_id    = result.get("message", {}).get("id", "draft")
                thread_id = result.get("message", {}).get("threadId", "")
                log.info("[DRAFT] Created draft for %s — %r", email, subject)
            else:
                result    = gmail_client.send_email(to_email=email, to_name=name,
                                                    subject=subject, body=body)
                msg_id    = result.get("id", "")
                thread_id = result.get("threadId", "")
                log.info("Sent to %s — %r", email, subject)

            db.mark_sent(
                email=email,
                message_id=msg_id,
                thread_id=thread_id,
                subject=subject,
                body_snippet=body,
                template_type=ctype,
            )
            db.increment_today_count()
            db.record_send_for_template(ctype, ctype)
            window.record_send()
            sent += 1

            # Save draft copy locally
            _save_draft_copy(email, subject, body)

            # Enforce minimum send interval AFTER each send (before next iteration)
            # This guarantees throttling even if wait_for_interval() skips due to
            # stale DB reads or other timing issues.
            if sent < batch_size:
                interval = scheduler.random_interval()
                log.info("Post-send delay: sleeping %ds before next send...", interval)
                time.sleep(interval)

        except Exception as exc:
            log.error("Send failed for %s: %s", email, exc)
            attempts = db.get_send_attempts(email)
            if attempts >= MAX_SEND_ATTEMPTS:
                log.warning(
                    "Dead-lettering %s after %d failed attempts (last: %s)",
                    email, attempts, exc,
                )
                db.mark_dead_letter(email, reason=f"send error: {exc}")
            else:
                db.update_contact(email, status="verified")   # put back in queue
            failed += 1

    return {"sent": sent, "failed": failed, "skipped": skipped}


def _save_draft_copy(email: str, subject: str, body: str):
    """Save a local copy of every sent email for review."""
    from config import DRAFTS_DIR
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_email = email.replace("@", "_at_").replace(".", "_")
    filepath = DRAFTS_DIR / f"{ts}_{safe_email}.txt"
    try:
        filepath.write_text(
            f"TO: {email}\nSUBJECT: {subject}\n\n{body}", encoding="utf-8"
        )
    except Exception:
        pass   # Non-critical


# ─── CLI commands ─────────────────────────────────────────────────────────────

def cmd_run():
    """One full outreach cycle — verify → reply check → follow-ups → send."""
    log.info("=" * 60)
    log.info("RJM OUTREACH AGENT — CYCLE START  [draft_mode=%s]", DRAFT_MODE)
    log.info("=" * 60)

    # 1. Init DB
    db.init_db()

    # 2. Check rate limits before doing anything expensive
    window = scheduler.SendWindow()
    log.info("Scheduler: %s", window.status())

    # 3. Verify any new contacts (runs regardless of window)
    _verify_pending_contacts()

    # 4. Scan inbox for replies + bounces (non-fatal — Gmail OAuth may be temporarily unavailable)
    inbox_result = _run_step("inbox_check", reply_detector.run_full_inbox_check)
    if inbox_result is not None:
        log.info("Inbox check: %s", inbox_result)

    # 5. Classify any unclassified replies (catches backlog + anything from step 4)
    classify_result = _run_step("reply_classify", reply_classifier.classify_pending)
    if classify_result and classify_result.get("classified", 0) > 0:
        log.info("Reply classification: %s", classify_result)

    # 5b. Auto-reply to warm leads (positive, question, booking_intent)
    reply_result = _run_step("reply_responder", reply_responder.run, dry_run=DRAFT_MODE)
    if reply_result and reply_result.get("sent", 0) > 0:
        log.info("Auto-replies sent: %s", reply_result)

    # 6. Maybe generate learning insights (only if enough data)
    _run_step("learning_insights", learning.maybe_generate_insights)

    # 7. Send follow-ups (they count toward daily quota)
    followup_quota = min(10, scheduler.remaining_quota_today() // 3)
    if followup_quota > 0 and window.can_send:
        followup_result = followup_engine.run_followup_batch(max_generates=followup_quota)
        total_followups = followup_result.get("followups_sent", 0) + followup_result.get("followup2_sent", 0)
        # Account for follow-up sends in today's quota
        for _ in range(total_followups):
            db.increment_today_count()
        log.info("Follow-ups: %s", followup_result)

    # 8. Send initial emails
    batch_size = scheduler.compute_batch_size()
    if batch_size > 0 and window.can_send:
        send_result = _send_batch(batch_size)
        log.info("Initial sends: %s", send_result)
    else:
        log.info("No sends this cycle — %s", window.status())

    # 9. Summary
    summary = db.get_pipeline_summary()
    log.info(
        "Cycle complete — DB: %s | Today: %d sent | Reply rate: %s",
        {k: v for k, v in summary.items() if not k.startswith("_")},
        summary.get("_today_sent", 0),
        summary.get("_reply_rate", "—"),
    )

    # 10. Heartbeat so rjm.py status / master_agent can detect outreach-cycle staleness.
    try:
        import fleet_state
        fleet_state.heartbeat(
            "outreach_cycle",
            status="ok",
            result={
                "today_sent": summary.get("_today_sent", 0),
                "quota_remaining": scheduler.remaining_quota_today(),
            },
        )
    except Exception as exc:
        log.warning("fleet_state heartbeat skipped: %s", exc)


def cmd_setup():
    """First-time setup: init DB, authenticate Gmail."""
    print("\n=== RJM Outreach Agent Setup ===\n")
    db.init_db()
    print("✅ Database initialised")

    try:
        profile = gmail_client.verify_auth()
        print(f"✅ Gmail authenticated as: {profile.get('emailAddress')}")
    except FileNotFoundError as exc:
        print(f"\n❌ {exc}")
        print("\nTo get credentials.json:")
        print("  1. Go to console.cloud.google.com")
        print("  2. Create a project → Enable Gmail API")
        print("  3. Create OAuth 2.0 credentials (Desktop app)")
        print(f"  4. Download as: {Path(__file__).parent / 'credentials.json'}")
        print("  5. Run: python agent.py setup\n")
        sys.exit(1)

    print("\n✅ Setup complete. Run 'python agent.py status' to check pipeline.")


def cmd_status():
    db.init_db()
    summary = db.get_pipeline_summary()
    print("\n=== RJM OUTREACH — PIPELINE STATUS ===")
    for k, v in summary.items():
        if not k.startswith("_"):
            label = k.replace("_", " ").title()
            print(f"  {label:<25} {v}")
    print(f"  {'Today Sent':<25} {summary.get('_today_sent', 0)} / 150")
    print(f"  {'Reply Rate':<25} {summary.get('_reply_rate', '—')}")

    window = scheduler.SendWindow()
    print(f"\n  Scheduler: {window.status()}")
    print()


def cmd_report():
    db.init_db()
    learning.print_performance_report()


def cmd_queue():
    db.init_db()
    verified = db.get_contacts_by_status("verified")
    new      = db.get_contacts_by_status("new")
    pending  = verified + new

    if not pending:
        print("\n✅ No contacts in queue. Add some with: python agent.py add\n")
        return

    print(f"\n{'='*65}")
    print(f"  OUTREACH QUEUE — {len(pending)} contacts pending")
    print(f"{'='*65}")
    print(f"  {'EMAIL':<38} {'NAME':<25} {'TYPE':<10} STATUS")
    print(f"  {'-'*80}")
    for c in pending:
        print(f"  {c['email']:<38} {c['name']:<25} {c['type']:<10} {c['status']}")
    print()


def cmd_add():
    """Interactively add a single contact."""
    db.init_db()
    print("\n=== Add Contact ===")
    email = input("Email: ").strip()
    if not email:
        print("Cancelled.")
        return

    name  = input("Name / Organisation: ").strip()
    print("Type: label | curator | youtube | festival | podcast")
    ctype = input("Type: ").strip().lower()
    genre = input("Genre (e.g. Organic House, Tribal Psytrance, Nomadic Electronic): ").strip()
    notes = input("Notes (playlist name, show focus, etc.): ").strip()

    ok, result = db.add_contact(email, name, ctype, genre, notes, source="manual")
    if ok:
        print(f"\n✅ Added: {email} — id={result}")
    else:
        print(f"\n⚠️  Skipped: {result}")


def cmd_preview(email: str):
    """Preview the email that would be sent to a contact, without sending."""
    db.init_db()
    contact = db.get_contact(email)
    if not contact:
        print(f"\n❌ Contact not found: {email}")
        print("   Add them first with: python agent.py add\n")
        sys.exit(1)

    print(f"\nGenerating preview for {email}...")
    learn_ctx = learning.get_learning_context_for_template(contact.get("type", "curator"))
    subject, body = template_engine.generate_email(contact, learn_ctx)

    print("\n" + "=" * 60)
    print(f"  TO:      {contact['name']} <{email}>")
    print(f"  SUBJECT: {subject}")
    print("=" * 60)
    print(body)
    print("=" * 60 + "\n")


def cmd_followups():
    """Run follow-up batch immediately."""
    db.init_db()
    result = followup_engine.run_followup_batch(max_generates=20)
    print(f"\nFollow-ups: sent={result['followups_sent']}, failed={result['followups_failed']}\n")


def cmd_backfill_sent():
    """
    Fetch all sent emails from Gmail and store subject + body in the DB.
    Run once to recover data for contacts sent via Chrome or old agent versions.
    """
    db.init_db()
    print("\n=== Backfilling sent email data from Gmail Sent folder ===\n")

    sent_messages = gmail_client.fetch_sent_messages(max_results=500)
    if not sent_messages:
        print("No sent messages found.")
        return

    updated = 0
    skipped = 0

    for msg in sent_messages:
        to_email = msg["to_email"]
        contact  = db.get_contact(to_email)
        if not contact:
            skipped += 1
            continue

        # Backfill if full body is missing (always populate sent_body)
        needs_update = (
            not contact.get("sent_body")
            or not contact.get("sent_subject")
            or not contact.get("gmail_thread_id")
        )
        if not needs_update:
            skipped += 1
            continue

        full_body = msg["body"] or contact.get("sent_body_snippet", "")
        db.update_contact(
            to_email,
            sent_subject      = msg["subject"] or contact.get("sent_subject", ""),
            sent_body_snippet = full_body[:300],
            sent_body         = full_body,
            gmail_message_id  = msg["message_id"] or contact.get("gmail_message_id", ""),
            gmail_thread_id   = msg["thread_id"]  or contact.get("gmail_thread_id", ""),
        )
        print(f"  ✅ Backfilled: {to_email} — {msg['subject'][:60]!r}")
        updated += 1

    print(f"\nBackfill complete — updated: {updated}, skipped/not-in-db: {skipped}\n")


def cmd_verify_all():
    """Bounce-check all pending contacts."""
    db.init_db()
    verified = _verify_pending_contacts()
    print(f"\n✅ Verified {verified} contacts\n")


def cmd_import(csv_path: str):
    """
    Import contacts from a CSV file.
    Expected columns (flexible): email, name, type, genre, notes
    OR pipe-delimited: email | name | type | genre | notes
    """
    db.init_db()
    path = Path(csv_path)
    if not path.exists():
        print(f"\n❌ File not found: {csv_path}\n")
        sys.exit(1)

    added = 0
    skipped = 0

    with open(path, encoding="utf-8") as f:
        # Detect delimiter
        sample = f.read(512)
        f.seek(0)
        delimiter = "|" if "|" in sample else ","

        reader = csv.DictReader(f, delimiter=delimiter)
        # Normalize column names
        for row in reader:
            row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
            email = row.get("email", "").strip()
            if not email or "@" not in email:
                continue
            name  = row.get("name", email.split("@")[0])
            ctype = row.get("type", "curator").lower()
            genre = row.get("genre", "")
            notes = row.get("notes", "")

            ok, result = db.add_contact(email, name, ctype, genre, notes, source="csv_import")
            if ok:
                added += 1
                print(f"  ✅ Added: {email}")
            else:
                skipped += 1
                print(f"  ⚠️  Skipped {email}: {result}")

    print(f"\nImport complete — added: {added}, skipped: {skipped}\n")


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="RJM Autonomous Outreach Agent",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run",          help="Run one outreach cycle (use this in cron)")
    sub.add_parser("setup",        help="First-time Gmail setup + DB init")
    sub.add_parser("status",       help="Show pipeline summary")
    sub.add_parser("report",       help="Full performance report with learning insights")
    sub.add_parser("queue",        help="Show pending contacts")
    sub.add_parser("add",          help="Add a contact interactively")
    sub.add_parser("followups",    help="Run follow-up batch now")
    sub.add_parser("verify_all",   help="Bounce-check all pending contacts")
    sub.add_parser("backfill_sent",help="Fetch sent emails from Gmail and store bodies in DB")

    preview_p = sub.add_parser("preview", help="Preview email for a contact (no send)")
    preview_p.add_argument("email", help="Contact email address")

    import_p = sub.add_parser("import", help="Import contacts from CSV/pipe-delimited file")
    import_p.add_argument("csv",  help="Path to CSV file")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run()
    elif args.command == "setup":
        cmd_setup()
    elif args.command == "status":
        cmd_status()
    elif args.command == "report":
        cmd_report()
    elif args.command == "queue":
        cmd_queue()
    elif args.command == "add":
        cmd_add()
    elif args.command == "preview":
        cmd_preview(args.email)
    elif args.command == "followups":
        cmd_followups()
    elif args.command == "verify_all":
        cmd_verify_all()
    elif args.command == "backfill_sent":
        cmd_backfill_sent()
    elif args.command == "import":
        cmd_import(args.csv)
    else:
        parser.print_help()
        print("\nQuick start:")
        print("  python agent.py setup        # First time only")
        print("  python agent.py import <csv> # Import existing contacts")
        print("  python agent.py run          # Start sending\n")


if __name__ == "__main__":
    main()
