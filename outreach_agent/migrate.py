#!/usr/bin/env python3
"""
Migrate existing contacts.csv (legacy system) into the new outreach_agent SQLite DB.

Handles:
  - Deduplication (won't re-add contacts already in DB)
  - Maps old status values to new schema
  - Preserves sent/bounce history — won't re-send to already-contacted addresses
  - Also imports podcast pitches from OUTREACH/Podcast_Session1_Pitches.txt

Run once:
  cd outreach_agent
  python migrate.py
"""

import csv
import re
import sys
from pathlib import Path

# Add parent to path for config/db imports
sys.path.insert(0, str(Path(__file__).parent))

import db
from config import LEGACY_CSV, PROJECT_DIR

PODCAST_PITCHES_FILE = PROJECT_DIR / "OUTREACH" / "Podcast_Session1_Pitches.txt"

# Map old status values → new schema
STATUS_MAP = {
    "sent":     "sent",
    "pending":  "new",
    "bounced":  "bounced",
    "response": "responded",
    "skip":     "skip",
}
# Old status values that should be treated as already verified
PRE_VERIFIED = {"sent", "response", "skip"}


def migrate_contacts_csv():
    """Import contacts.csv into outreach.db."""
    if not LEGACY_CSV.exists():
        print(f"⚠️  Legacy CSV not found at {LEGACY_CSV}")
        print("   Skipping CSV migration.")
        return 0

    print(f"\n📂 Migrating: {LEGACY_CSV}")

    with open(LEGACY_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("   File is empty — nothing to migrate.")
        return 0

    added = 0
    skipped = 0
    errors = 0

    for row in rows:
        email  = (row.get("email") or "").strip().lower()
        name   = (row.get("name") or "").strip()
        ctype  = (row.get("type") or "curator").strip().lower()
        genre  = (row.get("genre") or "").strip()
        notes  = (row.get("notes") or "").strip()
        old_status = (row.get("status") or "pending").strip().lower()
        date_sent  = (row.get("date_sent") or "").strip()
        bounce     = (row.get("bounce") or "no").strip().lower()
        date_added = (row.get("date_added") or "").strip()
        msg_id     = (row.get("gmail_message_id") or "").strip()
        thread_id  = (row.get("gmail_thread_id") or "").strip()
        subject    = (row.get("sent_subject") or "").strip()

        if not email or "@" not in email:
            continue

        # Map to new status
        new_status = STATUS_MAP.get(old_status, "new")
        is_bounced = bounce == "yes"

        # Check for exact duplicate
        existing = db.get_contact(email)
        if existing:
            skipped += 1
            print(f"  ↩️  Skip (exists): {email}")
            continue

        # Insert directly with legacy status preserved
        try:
            with db.get_conn() as conn:
                conn.execute("""
                    INSERT INTO contacts
                        (email, name, type, genre, notes, status, bounce,
                         date_added, date_verified, date_sent,
                         gmail_message_id, gmail_thread_id, sent_subject,
                         send_attempts, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    email, name, ctype, genre, notes,
                    "bounced" if is_bounced else new_status,
                    "actual" if (is_bounced and date_sent) else ("pre-check" if is_bounced else "no"),
                    date_added or None,
                    date_added if old_status in PRE_VERIFIED else None,
                    date_sent or None,
                    msg_id or None,
                    thread_id or None,
                    subject or None,
                    1 if date_sent else 0,
                    "csv_legacy",
                ))
            added += 1
            print(f"  ✅ Imported: {email} ({new_status})")
        except Exception as exc:
            errors += 1
            print(f"  ❌ Error importing {email}: {exc}")

    print(f"\n  CSV migration: {added} imported, {skipped} skipped, {errors} errors")
    return added


def extract_podcast_emails_from_pitches():
    """
    Parse the Podcast_Session1_Pitches.txt file and extract email addresses
    to add as podcast contacts.
    """
    if not PODCAST_PITCHES_FILE.exists():
        print(f"\n⚠️  Podcast pitches file not found: {PODCAST_PITCHES_FILE}")
        return 0

    print(f"\n📂 Parsing podcast pitches: {PODCAST_PITCHES_FILE}")

    content = PODCAST_PITCHES_FILE.read_text(encoding="utf-8")

    # Parse each pitch block — extract TO line
    # Pattern: "TO: email@domain.com" and "PITCH N — Name"
    pitch_pattern = re.compile(
        r"PITCH\s+\d+\s+[—\-]+\s+(.+?)\n"    # podcast name
        r"TO:\s+([\w.+-]+@[\w.-]+\.[a-zA-Z]{2,})"  # email
        r".*?CATEGORY:\s+(.+?)(?:\n|$)",
        re.DOTALL | re.MULTILINE,
    )

    matches = pitch_pattern.findall(content)

    if not matches:
        # Fallback: just extract all emails from the file
        emails = re.findall(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", content)
        print(f"  Found {len(emails)} emails (no structured data)")
        added = 0
        for email in emails:
            email = email.lower().strip()
            if email.endswith(".txt") or "mailer-daemon" in email:
                continue
            ok, result = db.add_contact(
                email=email,
                name=email.split("@")[0].replace("-", " ").replace(".", " ").title(),
                ctype="podcast",
                genre="Podcast",
                notes="Imported from Podcast_Session1_Pitches.txt",
                source="podcast_legacy",
            )
            if ok:
                added += 1
                print(f"  ✅ Podcast: {email}")
            else:
                print(f"  ↩️  Skip: {email}: {result}")
        return added

    added = 0
    for name, email, category in matches:
        name  = name.strip()
        email = email.strip().lower()
        category = category.strip()

        ok, result = db.add_contact(
            email=email,
            name=name,
            ctype="podcast",
            genre=category,
            notes=f"Podcast pitch — {category}",
            source="podcast_legacy",
        )
        if ok:
            added += 1
            print(f"  ✅ Podcast: {email} ({name})")
        else:
            print(f"  ↩️  Skip {email}: {result}")

    print(f"\n  Podcast migration: {added} contacts added")
    return added


def main():
    print("\n" + "=" * 60)
    print("  RJM OUTREACH — DATABASE MIGRATION")
    print("=" * 60)

    # Init DB first
    db.init_db()
    print("✅ Database ready")

    # Migrate CSV
    csv_count = migrate_contacts_csv()

    # Extract podcast contacts
    podcast_count = extract_podcast_emails_from_pitches()

    # Final summary
    summary = db.get_pipeline_summary()
    print("\n" + "=" * 60)
    print("  MIGRATION COMPLETE")
    print(f"  Total contacts in DB: {sum(v for k, v in summary.items() if not k.startswith('_'))}")
    print("  Status breakdown:")
    for k, v in summary.items():
        if not k.startswith("_"):
            print(f"    {k:<20} {v}")
    print()
    print("Next steps:")
    print("  python agent.py verify_all    # Bounce-check all new contacts")
    print("  python agent.py queue         # Review pending contacts")
    print("  python agent.py run           # Start the agent\n")


if __name__ == "__main__":
    main()
