"""
RJM Outreach Agent — Reply Detector

Two complementary strategies:
  1. Thread scan: check if any thread we opened has new incoming messages
  2. Bounce scan: search inbox for delivery failure notifications (mailer-daemon)

Both run on every agent cycle. Results update the DB immediately.
"""

import logging
from datetime import date

import db
import gmail_client
import reply_classifier
from bounce import add_confirmed_dead_address, add_confirmed_dead_domain

log = logging.getLogger("outreach.reply_detector")


def scan_for_replies() -> dict:
    """
    Scan all open threads (contacts in 'sent' or 'followup_sent' status)
    for incoming replies. Update DB on any found.

    Returns summary dict.
    """
    # Get contacts who have thread IDs and haven't responded yet
    contacts_sent       = db.get_contacts_by_status("sent")
    contacts_followup   = db.get_contacts_by_status("followup_sent")
    open_contacts       = contacts_sent + contacts_followup

    # Build thread_id → contact map (skip contacts without thread IDs)
    thread_map = {}
    for c in open_contacts:
        tid = c.get("gmail_thread_id") or ""
        if tid:
            thread_map[tid] = c

    if not thread_map:
        log.info("No open threads to scan for replies")
        return {"threads_scanned": 0, "replies_found": 0}

    log.info("Scanning %d open threads for replies...", len(thread_map))

    replies = gmail_client.scan_inbox_for_replies(list(thread_map.keys()))

    found_count = 0
    for thread_id, reply_info in replies.items():
        contact = thread_map.get(thread_id)
        if not contact:
            continue

        email = contact["email"]
        log.info(
            "Reply detected from %s (thread %s): %s",
            email, thread_id, reply_info["snippet"][:80]
        )

        db.mark_responded(
            email=email,
            reply_snippet=reply_info["snippet"],
            reply_message_id=reply_info["message_id"],
            thread_id=thread_id,
        )

        # Update template performance stats
        template_type = contact.get("template_type") or "unknown"
        contact_type  = contact.get("type") or "unknown"
        db.record_reply_for_template(template_type, contact_type)

        found_count += 1

    log.info("Reply scan complete — %d/%d threads had replies",
             found_count, len(thread_map))

    return {
        "threads_scanned": len(thread_map),
        "replies_found": found_count,
    }


def scan_for_bounces() -> dict:
    """
    Scan inbox for mailer-daemon delivery failure messages.
    Update contacts as 'bounced' and add domains/addresses to hard-block list.

    Returns summary dict.
    """
    log.info("Scanning inbox for bounce/delivery-failure messages...")

    try:
        failures = gmail_client.check_bounced_delivery_failures(since_days=30)
    except Exception as exc:
        log.warning("Bounce scan failed: %s", exc)
        return {"bounces_found": 0, "error": str(exc)}

    if not failures:
        log.info("No bounce messages found")
        return {"bounces_found": 0}

    bounced_count = 0
    domain_bounce_counts: dict[str, int] = {}

    for failure in failures:
        to_addr = (failure.get("to_address") or "").strip().lower()
        if not to_addr or "@" not in to_addr:
            log.debug("Could not extract recipient from bounce: %s", failure["snippet"][:60])
            continue

        domain = to_addr.split("@")[1]

        # Always add to dead_addresses regardless of whether contact is in DB
        add_confirmed_dead_address(to_addr)

        contact = db.get_contact(to_addr)
        if contact and contact["status"] not in ("bounced",):
            log.info("Marking actual bounce: %s (reason: %s)", to_addr, failure["snippet"][:60])
            db.mark_bounced_full(
                email=to_addr,
                reason=failure["snippet"][:200],
                bounce_type="actual",
            )
            bounced_count += 1

        # Track domain bounce frequency
        domain_bounce_counts[domain] = domain_bounce_counts.get(domain, 0) + 1

    # If 2+ addresses on the same custom domain bounced, blacklist the whole domain
    # (skip major providers — bad mailboxes there are normal)
    from bounce import MAJOR_PROVIDERS, add_confirmed_dead_domain
    for domain, count in domain_bounce_counts.items():
        if count >= 2 and domain not in MAJOR_PROVIDERS:
            log.warning("Domain %s has %d bounces — blacklisting entire domain", domain, count)
            add_confirmed_dead_domain(domain, reason=f"{count} address bounces")

    log.info("Bounce scan complete — %d actual bounces recorded", bounced_count)
    return {"bounces_found": bounced_count}


def run_full_inbox_check() -> dict:
    """Run reply scan, bounce scan, then classify any new replies. Called once per agent cycle."""
    reply_result  = scan_for_replies()
    bounce_result = scan_for_bounces()
    classify_result = reply_classifier.classify_pending()
    return {**reply_result, **bounce_result, **classify_result}
