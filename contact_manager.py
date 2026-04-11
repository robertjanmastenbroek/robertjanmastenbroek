#!/usr/bin/env python3
"""
RJM Contact Manager — Robert-Jan Mastenbroek Promotion Engine
Manages the curator/label contact database, deduplication, and send queue.

Usage:
  python3 contact_manager.py status          # Show pipeline overview
  python3 contact_manager.py queue           # Show pending contacts ready to send
  python3 contact_manager.py add <email> <name> <type> <genre> "<notes>"
  python3 contact_manager.py mark_sent <email>
  python3 contact_manager.py mark_bounced <email>
  python3 contact_manager.py check <email>   # Check if email already in DB
  python3 contact_manager.py search <keyword> # Search DB by name/genre/notes
  python3 contact_manager.py new_from_file <file.txt>  # Bulk add from file (one email per line)
"""

import csv
import sys
import os
from pathlib import Path
from datetime import date

DB_PATH = os.path.join(os.path.dirname(__file__), "contacts.csv")
FIELDNAMES = ["email", "name", "type", "genre", "notes", "status", "date_added", "date_sent", "bounce"]

# Shared email providers where different orgs use the same domain — do NOT treat as org-level duplicates
SHARED_PROVIDERS = {
    "gmail.com","googlemail.com","hotmail.com","outlook.com","live.com",
    "yahoo.com","mail.com","gmx.com","gmx.ch","gmx.de","icloud.com",
    "protonmail.com","aol.com","zoho.com","msn.com","live.co.uk",
}

def root_domain(email):
    """Return root domain (strips subdomains, handles .co.uk etc)."""
    domain = email.strip().lower().split("@")[-1]
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-1] in ("uk","au","nz","za","br","ar"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain

def load_db():
    if not os.path.exists(DB_PATH):
        return []
    with open(DB_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def save_db(rows):
    with open(DB_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

def get_all_emails(rows):
    return {r["email"].strip().lower() for r in rows}

def get_contacted_custom_domains(rows):
    """Return set of custom domains (non-shared-provider) already sent or bounced."""
    contacted = set()
    for r in rows:
        if r["status"] in ("sent", "skip"):
            dom = root_domain(r["email"])
            if dom not in SHARED_PROVIDERS:
                contacted.add(dom)
    return contacted

def check_org_duplicate(rows, email):
    """
    Returns (True, conflict_email) if the org behind this email was already contacted.
    Uses domain matching for custom domains. Shared providers (gmail etc) are ignored.
    """
    dom = root_domain(email)
    if dom in SHARED_PROVIDERS:
        return False, None
    contacted_domains = get_contacted_custom_domains(rows)
    if dom in contacted_domains:
        # Find the email we already sent
        conflict = next((r["email"] for r in rows if root_domain(r["email"]) == dom
                         and r["status"] in ("sent", "skip")), None)
        return True, conflict
    return False, None

def status_report(rows):
    total = len(rows)
    sent = [r for r in rows if r["status"] == "sent"]
    pending = [r for r in rows if r["status"] == "pending"]
    bounced = [r for r in rows if r["bounce"] == "yes"]
    response = [r for r in rows if r["status"] == "response"]
    curators = [r for r in rows if r["type"] == "curator"]
    labels = [r for r in rows if r["type"] == "label"]

    print("\n" + "="*55)
    print("  RJM PROMOTION ENGINE — CONTACT DATABASE STATUS")
    print("="*55)
    print(f"  Total contacts:     {total}")
    print(f"  Sent:               {len(sent)}")
    print(f"  Pending (to send):  {len(pending)}")
    print(f"  Bounced:            {len(bounced)}")
    print(f"  Got response:       {len(response)}")
    print(f"  Curators:           {len(curators)}")
    print(f"  Labels:             {len(labels)}")
    print("="*55)

    if bounced:
        print("\n  ❌ BOUNCED (do not resend):")
        for r in bounced:
            print(f"     {r['email']} — {r['name']}")

    print(f"\n  📬 PENDING QUEUE: {len(pending)} contacts waiting to be emailed.")
    print("  Run 'python3 contact_manager.py queue' to see them.\n")

def show_queue(rows):
    pending = [r for r in rows if r["status"] == "pending"]
    if not pending:
        print("\n✅ No pending contacts — all have been emailed!\n")
        return
    print(f"\n{'='*65}")
    print(f"  SEND QUEUE — {len(pending)} contacts pending")
    print(f"{'='*65}")
    print(f"  {'EMAIL':<35} {'NAME':<25} {'TYPE'}")
    print(f"  {'-'*60}")
    for r in pending:
        print(f"  {r['email']:<35} {r['name']:<25} {r['type']}")
    print()

def check_email(rows, email):
    email = email.strip().lower()
    emails = get_all_emails(rows)
    if email in emails:
        match = next(r for r in rows if r["email"].strip().lower() == email)
        print(f"\n⚠️  EXACT DUPLICATE: {email}")
        print(f"   Name:    {match['name']}")
        print(f"   Status:  {match['status']}")
        print(f"   Bounce:  {match['bounce']}")
        print(f"   Sent:    {match['date_sent'] or 'not yet'}\n")
        return True
    # Org-level check
    is_org_dup, conflict = check_org_duplicate(rows, email)
    if is_org_dup:
        print(f"\n⛔ ORG DUPLICATE: {email}")
        print(f"   Already contacted this org via: {conflict}\n")
        return True
    print(f"\n✅ NEW: {email} not in database. Safe to add.\n")
    return False

def add_contact(rows, email, name, ctype, genre, notes):
    email = email.strip().lower()
    emails = get_all_emails(rows)
    if email in emails:
        print(f"\n⚠️  SKIPPED — {email} already in database (exact duplicate).")
        return rows
    # Org-level duplicate check: don't send to 2 addresses at the same custom domain
    is_org_dup, conflict = check_org_duplicate(rows, email)
    if is_org_dup:
        print(f"\n⛔ SKIPPED — {email}: org already contacted via {conflict}")
        # Still add to DB but mark as skip so we have the record
        new_row = {
            "email": email, "name": name, "type": ctype, "genre": genre,
            "notes": notes + f" | SKIPPED: org already contacted via {conflict}",
            "status": "skip", "date_added": str(date.today()), "date_sent": "", "bounce": "no"
        }
        rows.append(new_row)
        save_db(rows)
        return rows
    new_row = {
        "email": email,
        "name": name,
        "type": ctype,
        "genre": genre,
        "notes": notes,
        "status": "pending",
        "date_added": str(date.today()),
        "date_sent": "",
        "bounce": "no"
    }
    rows.append(new_row)
    save_db(rows)
    print(f"\n✅ ADDED: {email} ({name}) — status: pending")
    return rows

def mark_sent(rows, email):
    email = email.strip().lower()
    updated = False
    for r in rows:
        if r["email"].strip().lower() == email:
            r["status"] = "sent"
            r["date_sent"] = str(date.today())
            updated = True
            break
    if updated:
        save_db(rows)
        print(f"\n✅ Marked as SENT: {email}")
    else:
        print(f"\n❌ Not found: {email}")
    return rows

def mark_bounced(rows, email):
    email = email.strip().lower()
    updated = False
    for r in rows:
        if r["email"].strip().lower() == email:
            r["status"] = "bounced"
            r["bounce"] = "yes"
            updated = True
            break
    if updated:
        save_db(rows)
        print(f"\n✅ Marked as BOUNCED: {email}")
    else:
        print(f"\n❌ Not found: {email}")
    return rows

def search_db(rows, keyword):
    keyword = keyword.lower()
    matches = [r for r in rows if
               keyword in r["email"].lower() or
               keyword in r["name"].lower() or
               keyword in r["genre"].lower() or
               keyword in r["notes"].lower()]
    if not matches:
        print(f"\n  No results for '{keyword}'\n")
        return
    print(f"\n  Found {len(matches)} match(es) for '{keyword}':")
    print(f"  {'EMAIL':<35} {'NAME':<25} {'STATUS'}")
    print(f"  {'-'*70}")
    for r in matches:
        print(f"  {r['email']:<35} {r['name']:<25} {r['status']}")
    print()

def bulk_add_from_file(rows, filepath):
    """
    Expects a file with lines like:
    email@domain.com | Name | type | genre | notes
    or just email addresses (one per line) for basic import.
    """
    if not os.path.exists(filepath):
        print(f"\n❌ File not found: {filepath}\n")
        return rows

    added = 0
    skipped = 0
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 5:
                email, name, ctype, genre, notes = parts[0], parts[1], parts[2], parts[3], parts[4]
            elif len(parts) == 1:
                # Just an email address
                email = parts[0]
                name = email.split("@")[0]
                ctype = "curator"
                genre = "Electronic"
                notes = "Imported from bulk file"
            else:
                print(f"  Skipping malformed line: {line}")
                continue

            email_lower = email.strip().lower()
            if email_lower in get_all_emails(rows):
                skipped += 1
                print(f"  DUPLICATE skipped: {email}")
            else:
                new_row = {
                    "email": email_lower,
                    "name": name,
                    "type": ctype,
                    "genre": genre,
                    "notes": notes,
                    "status": "pending",
                    "date_added": str(date.today()),
                    "date_sent": "",
                    "bounce": "no"
                }
                rows.append(new_row)
                added += 1
                print(f"  ✅ Added: {email}")

    save_db(rows)
    print(f"\n  Done. Added: {added}, Skipped (duplicates): {skipped}\n")
    return rows

def sync_to_sqlite():
    """
    Import contacts from contacts.csv into outreach_agent/outreach.db.

    Skips contacts already in SQLite. Maps CSV status/bounce → SQLite status:
      CSV 'pending'  → SQLite 'new'    (will go through bounce verification)
      CSV 'sent'     → SQLite 'sent'
      CSV 'bounced'  → SQLite 'closed' + bounce='actual'
      CSV 'response' → SQLite 'responded'
      other          → SQLite 'new'

    outreach.db is authoritative for all contacts the outreach engine knows about.
    This sync is a one-way import bridge: CSV → SQLite, never the reverse.
    """
    # Locate outreach_agent db module
    outreach_dir = Path(__file__).parent / "outreach_agent"
    if not (outreach_dir / "db.py").exists():
        print(f"\n✗ outreach_agent/db.py not found at {outreach_dir}")
        print("  Make sure you run this from the project root.\n")
        return

    sys.path.insert(0, str(outreach_dir))
    import db as _db
    _db.init_db()

    STATUS_MAP = {
        "pending":  "new",
        "sent":     "sent",
        "bounced":  "closed",
        "response": "responded",
        "responded": "responded",
        "skip":     "closed",
    }
    BOUNCE_MAP = {
        "bounced": "actual",
        "skip":    "no",
    }

    rows = load_db()
    if not rows:
        print("\n  contacts.csv is empty or not found.\n")
        return

    added   = 0
    skipped = 0
    errors  = 0

    print(f"\n  Syncing {len(rows)} contacts from contacts.csv → outreach.db ...\n")

    with _db.get_conn() as conn:
        for r in rows:
            email = r.get("email", "").strip().lower()
            if not email or "@" not in email:
                continue

            # Check if already in SQLite
            existing = conn.execute(
                "SELECT id FROM contacts WHERE email = ?", (email,)
            ).fetchone()

            if existing:
                skipped += 1
                continue

            csv_status  = r.get("status", "pending").strip().lower()
            sqlite_status = STATUS_MAP.get(csv_status, "new")
            bounce_val    = BOUNCE_MAP.get(csv_status, "no")
            if r.get("bounce", "no").lower() == "yes":
                bounce_val = "actual"

            name  = r.get("name", "").strip()
            ctype = r.get("type", "curator").strip()
            genre = r.get("genre", "").strip()
            notes = r.get("notes", "").strip()
            date_added = r.get("date_added", str(date.today()))
            date_sent  = r.get("date_sent", "") or None

            try:
                conn.execute("""
                    INSERT INTO contacts
                      (email, name, type, genre, notes, status, bounce,
                       date_added, date_sent, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'csv_sync')
                """, (email, name, ctype, genre, notes, sqlite_status,
                      bounce_val, date_added, date_sent))
                added += 1
                print(f"  + {email} ({ctype}) → {sqlite_status}")
            except Exception as exc:
                print(f"  ✗ {email}: {exc}")
                errors += 1

    print(f"\n  Done. Added: {added}  |  Already in DB: {skipped}  |  Errors: {errors}\n")


def main():
    rows = load_db()
    args = sys.argv[1:]

    if not args or args[0] == "status":
        status_report(rows)
    elif args[0] == "queue":
        show_queue(rows)
    elif args[0] == "check" and len(args) >= 2:
        check_email(rows, args[1])
    elif args[0] == "add" and len(args) >= 6:
        rows = add_contact(rows, args[1], args[2], args[3], args[4], args[5])
    elif args[0] == "mark_sent" and len(args) >= 2:
        rows = mark_sent(rows, args[1])
    elif args[0] == "mark_bounced" and len(args) >= 2:
        rows = mark_bounced(rows, args[1])
    elif args[0] == "search" and len(args) >= 2:
        search_db(rows, args[1])
    elif args[0] == "new_from_file" and len(args) >= 2:
        rows = bulk_add_from_file(rows, args[1])
    elif args[0] == "sync":
        sync_to_sqlite()
    else:
        print(__doc__)

if __name__ == "__main__":
    main()
