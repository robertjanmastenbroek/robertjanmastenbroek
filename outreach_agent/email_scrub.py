"""
email_scrub.py — Pre-verify pending contacts to lower bounce rate.

Runs bounce.verify_email() on all 'new'/'verified' contacts not yet sent.
Marks confirmed-invalid as status='skip', bounce='pre-check'.

The send system pauses when bounce rate > 15%. This scrub clears bad
emails from the queue so the rate recovers and sends resume.

Usage:
  python3 email_scrub.py              # scrub all pending contacts
  python3 email_scrub.py --limit 50   # max 50 checks (default 100)
  python3 email_scrub.py --dry-run    # report without writing
"""

from __future__ import annotations

import argparse
import logging
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

import db
import bounce

log = logging.getLogger("outreach.email_scrub")

_RATE_LIMIT_S = 0.5  # Disify allows ~2 req/s


def scrub(limit: int = 100, dry_run: bool = False) -> dict:
    """
    Verify pending contacts, mark invalid ones as skip.

    Returns: {checked, marked_skip, confirmed_valid, inconclusive}
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    with db.get_conn() as conn:
        try:
            rows = conn.execute(
                """SELECT email, name, COALESCE(warmth_score, 0) FROM contacts
                   WHERE status IN ('new', 'verified')
                   AND (bounce IS NULL OR bounce = 'no')
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        except Exception:
            # warmth_score column absent in older schemas — default to 0
            rows = [
                (e, n, 0) for e, n in conn.execute(
                    """SELECT email, name FROM contacts
                       WHERE status IN ('new', 'verified')
                       AND (bounce IS NULL OR bounce = 'no')
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
            ]

    log.info("Scrubbing %d pending contacts...", len(rows))

    checked = 0
    marked_skip = 0
    confirmed_valid = 0
    inconclusive = 0

    for email, name, warmth in rows:
        result, reason = bounce.verify_email(email)
        checked += 1

        is_catchall = result == "unknown" and "catch-all" in reason.lower()

        # Skip catch-all unknowns only for low-warmth auto-mined contacts.
        # High-warmth contacts (warmth_score >= 6) are hand-researched or
        # manually added — catch-all on their domain is normal for orgs.
        skip_catchall = is_catchall and (warmth or 0) < 6

        if result == "invalid" or skip_catchall:
            label = "Catch-all (low warmth)" if skip_catchall else "Invalid"
            log.info("❌ %s: %s (%s) — %s", label, email, name, reason)
            marked_skip += 1
            if not dry_run:
                with db.get_conn() as conn:
                    conn.execute(
                        "UPDATE contacts SET status='skip', bounce='pre-check' WHERE email=?",
                        (email,),
                    )
        elif result == "valid":
            log.debug("✅ Valid: %s", email)
            confirmed_valid += 1
        else:
            log.debug("❓ Inconclusive: %s — %s", email, reason)
            inconclusive += 1

        time.sleep(_RATE_LIMIT_S)

    log.info(
        "Scrub complete: %d checked, %d skip, %d valid, %d inconclusive",
        checked, marked_skip, confirmed_valid, inconclusive,
    )

    return {
        "checked": checked,
        "marked_skip": marked_skip,
        "confirmed_valid": confirmed_valid,
        "inconclusive": inconclusive,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-verify pending contacts to cut bounce rate")
    parser.add_argument("--limit", type=int, default=100, help="Max contacts to check")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    result = scrub(limit=args.limit, dry_run=args.dry_run)
    print(f"\n{'DRY-RUN ' if args.dry_run else ''}Email Scrub complete:")
    print(f"  Checked:            {result['checked']}")
    print(f"  Marked skip:        {result['marked_skip']}")
    print(f"  Confirmed valid:    {result['confirmed_valid']}")
    print(f"  Inconclusive:       {result['inconclusive']}")


if __name__ == "__main__":
    main()
