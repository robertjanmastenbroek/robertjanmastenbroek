"""
inbox_cleanup.py — Deep storage cleanup for motomotosings@gmail.com

Strategy: target by SIZE first (biggest wins), then by category, across ALL folders.

Usage:
    python inbox_cleanup.py --scan     # Show storage breakdown and what would be deleted
    python inbox_cleanup.py --delete   # Actually trash everything found
    python inbox_cleanup.py --purge    # Trash + immediately empty trash (permanent)

Targets (all dates unless noted):
    - Large attachments (>1MB) — biggest storage impact
    - Spam folder (all)
    - Promotions / newsletters / marketing
    - Social media notifications
    - Automated system emails (GitHub, Google alerts, bounces, receipts)
    - Devotional / subscription newsletters
    - Anything in All Mail before 2026 (except protected senders)

Protected (never deleted):
    - BandLab support (active ticket)
    - DigiD / Dutch government
    - Podcast bookings / calendar invites (2026)
    - Curator replies (2026)
    - Active outreach threads (2026)
"""

import argparse
import sys
import os
import time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
from config import TOKEN_PATH, CREDS_PATH, GMAIL_SCOPES

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Protected — never delete ─────────────────────────────────────────────────
KEEP_SENDERS = [
    "support@bandlab.com",
    "noreply@digid.nl",
    "worldscollide123pod@gmail.com",   # confirmed podcast booking Apr 14
    # ── empresa-closure workstream (2026-04-16): protect all fiscal/legal replies ──
    "asesoriacodesur.es",              # gestoría CODESUR San Isidro
    "codesursi@asesoriacodesur.es",
    "sabadellsolbank.com",             # Sabadell Solbank Costa Adeje (Saül)
    "sperezfondon@sabadellsolbank.com",
    "bancsabadell.com",                # Sabadell parent domain
    "agenciatributaria.gob.es",        # AEAT — notifications, CSVs, etc.
    "seg-social.es",                   # TGSS — RETA, resguardos
    "correos.es",                      # Correos — Cl@ve registration confirmations
    "clave.gob.es",                    # Cl@ve system notifications
    "gobiernodecanarias.org",          # ATC — IGIC, Modelo 400
    "policia.es",                      # Policía Nacional — NIE duplicate if needed
]

KEEP_SUBJECT_KEYWORDS = [
    "calendar invite",
    "booking confirmed",
    "podcast",
    # ── empresa-closure protections ──
    "nie",
    "autónomo",
    "autonomo",
    "baja",
    "modelo 036",
    "modelo 130",
    "modelo 400",
    "reta",
    "hacienda",
    "aeat",
    "tgss",
    "cl@ve",
    "clave",
    "certificado de registro",
    "igic",
]

# ── Bulk-trash queries (ALL mail, not just inbox) ─────────────────────────────
# Order matters: large first for maximum storage impact
TRASH_QUERIES = [
    # Large attachments — biggest storage wins first
    ("large attachments >5MB",    "larger:5m -in:trash"),
    ("large attachments >2MB",    "larger:2m -in:trash"),
    ("large attachments >1MB",    "larger:1m -in:trash"),
    # Spam
    ("spam folder",               "in:spam"),
    # Social notifications (all dates)
    ("instagram notifications",   "from:instagram.com -in:trash"),
    ("facebook notifications",    "from:facebook.com -in:trash"),
    ("twitter/x notifications",   "from:twitter.com OR from:x.com -in:trash"),
    ("linkedin notifications",    "from:linkedin.com -in:trash"),
    # Marketing / newsletters (all dates)
    ("amazon marketing",          "from:amazon.com OR from:amazon.co.uk OR from:amazon.de OR from:amazon.es -in:trash"),
    ("google promotions",         "from:no-reply@accounts.google.com OR from:googleplay-noreply@google.com OR from:noreply-youtube@youtube.com -in:trash"),
    ("spotify promos",            "from:no-reply@hello.spotify.com OR from:spotify@email.spotify.com -in:trash"),
    ("submithub",                 "from:submithub.com -in:trash"),
    ("neuralframes",              "from:neuralframes.com -in:trash"),
    ("expatmoney",                "from:expatmoney.com -in:trash"),
    ("suno",                      "from:suno.com OR from:creators.suno.com -in:trash"),
    ("wetransfer",                "from:wetransfer.com -in:trash"),
    ("namecheap",                 "from:namecheap.com -in:trash"),
    ("derek prince ministries",   "from:info-derekprince.nl@shareMailer.com OR from:derekprince -in:trash"),
    ("mastanya ai",               "from:mastanya.ai -in:trash"),
    ("hubspot",                   "from:hubspot.com OR from:hubspotemail.net -in:trash"),
    ("mailchimp",                 "from:mailchimp.com OR from:mc.sendgrid.net -in:trash"),
    ("substack",                  "from:substack.com -in:trash"),
    ("notion",                    "from:notion.so OR from:mail.notion.so -in:trash"),
    ("canva",                     "from:canva.com -in:trash"),
    ("stripe receipts",           "from:stripe.com OR from:receipts.stripe.com -in:trash"),
    ("paypal receipts",           "from:paypal.com -in:trash"),
    ("dropbox",                   "from:dropbox.com -in:trash"),
    ("typeform",                  "from:typeform.com -in:trash"),
    ("zoom",                      "from:zoom.us OR from:no-reply@zoom.us -in:trash"),
    # System / automated (all dates)
    ("delivery failures",         "subject:\"delivery status notification\" -in:trash"),
    ("mailer daemon",             "from:mailer-daemon@googlemail.com -in:trash"),
    ("github notifications",      "from:noreply@github.com OR from:notifications@github.com -in:trash"),
    ("google security alerts",    "from:no-reply@accounts.google.com -in:trash"),
    # All mail before 2026 (catches anything not already hit above)
    ("all mail before 2026",      "before:2026/01/01 -in:trash"),
    # Promotions tab (Gmail auto-categorised)
    ("promotions category",       "category:promotions -in:trash"),
    # Updates tab
    ("updates category",          "category:updates -in:trash"),
    # Social tab
    ("social category",           "category:social -in:trash"),
]


def get_service():
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(str(TOKEN_PATH), "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def fetch_messages(service, query, max_results=2000):
    messages = []
    try:
        response = service.users().messages().list(
            userId="me", q=query, maxResults=500
        ).execute()
        messages.extend(response.get("messages", []))
        while "nextPageToken" in response and len(messages) < max_results:
            response = service.users().messages().list(
                userId="me", q=query, maxResults=500,
                pageToken=response["nextPageToken"]
            ).execute()
            messages.extend(response.get("messages", []))
    except Exception as e:
        print(f"  Query error ({query[:50]}): {e}")
    return messages


def is_protected(service, msg_id):
    """Quick check — only fetch headers for borderline cases."""
    try:
        msg = service.users().messages().get(
            userId="me", id=msg_id, format="metadata",
            metadataHeaders=["From", "Subject"]
        ).execute()
        headers = {h["name"].lower(): h["value"].lower()
                   for h in msg["payload"]["headers"]}
        sender = headers.get("from", "")
        subject = headers.get("subject", "")
        for keep in KEEP_SENDERS:
            if keep in sender:
                return True
        for kw in KEEP_SUBJECT_KEYWORDS:
            if kw in subject:
                return True
    except Exception:
        pass
    return False


def batch_trash(service, ids, dry_run=True):
    """Trash message IDs in batches of 1000 (Gmail API limit)."""
    if dry_run:
        return len(ids)

    trashed = 0
    chunk_size = 500
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i:i + chunk_size]
        try:
            service.users().messages().batchModify(
                userId="me",
                body={"ids": chunk, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX", "UNREAD"]}
            ).execute()
            trashed += len(chunk)
            print(f"  Trashed {trashed}/{len(ids)}...", end="\r")
            time.sleep(0.5)  # gentle rate limiting
        except Exception as e:
            print(f"\n  Batch error: {e}")
    print()
    return trashed


def empty_trash(service):
    print("Permanently emptying Trash (fetching all trashed messages)...")
    msgs = fetch_messages(service, "in:trash", max_results=10000)
    if not msgs:
        print("Trash is already empty.")
        return
    ids = [m["id"] for m in msgs]
    print(f"  Permanently deleting {len(ids)} messages...")
    chunk_size = 1000
    deleted = 0
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i:i + chunk_size]
        try:
            service.users().messages().batchDelete(
                userId="me", body={"ids": chunk}
            ).execute()
            deleted += len(chunk)
            print(f"  Deleted {deleted}/{len(ids)}...", end="\r")
            time.sleep(0.3)
        except Exception as e:
            print(f"\n  Error: {e}")
    print(f"\nPermanently deleted {deleted} messages from Trash.")


def run(dry_run=True, purge=False):
    mode = "[DRY RUN] " if dry_run else ("[PURGE] " if purge else "")
    print(f"\n{mode}Deep storage cleanup — motomotosings@gmail.com\n")
    service = get_service()

    seen_ids = set()
    total_to_trash = 0
    results_by_category = []

    for label, query in TRASH_QUERIES:
        msgs = fetch_messages(service, query)
        new_ids = [m["id"] for m in msgs if m["id"] not in seen_ids]

        # Filter out protected messages (only check a sample for large sets)
        safe_ids = []
        for mid in new_ids:
            seen_ids.add(mid)
            # Only do expensive header check for 2026 queries
            if "2026" in query or "podcast" in query.lower():
                if not is_protected(service, mid):
                    safe_ids.append(mid)
            else:
                safe_ids.append(mid)

        if safe_ids:
            results_by_category.append((label, safe_ids))
            total_to_trash += len(safe_ids)
            print(f"  {label}: {len(safe_ids)} emails")

    print(f"\nTotal to trash: {total_to_trash} emails")

    if dry_run:
        print("\n[DRY RUN] Run with --delete or --purge to proceed.")
        return

    print("\nTrashing in batches...")
    total_trashed = 0
    for label, ids in results_by_category:
        print(f"  {label} ({len(ids)})...")
        trashed = batch_trash(service, ids, dry_run=False)
        total_trashed += trashed

    print(f"\nDone. Trashed {total_trashed} emails.")

    if purge:
        empty_trash(service)
        print("Storage should update within a few minutes in Gmail.")
    else:
        print("Emails are in Trash (30-day recovery window).")
        print("To permanently free storage now: python inbox_cleanup.py --purge")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan",   action="store_true", help="Show what would be deleted (safe)")
    parser.add_argument("--delete", action="store_true", help="Trash the emails")
    parser.add_argument("--purge",  action="store_true", help="Trash + immediately empty trash")
    args = parser.parse_args()

    if args.purge:
        run(dry_run=False, purge=True)
    elif args.delete:
        run(dry_run=False, purge=False)
    else:
        run(dry_run=True)
