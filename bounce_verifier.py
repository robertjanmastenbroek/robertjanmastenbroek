#!/usr/bin/env python3
"""
RJM Bounce Verifier — Pre-send email validation
Uses Google DNS-over-HTTPS to check MX records (works from any environment).

Usage:
  python3 bounce_verifier.py check <email>
  python3 bounce_verifier.py check_all    # Verify all pending in contacts.csv
  python3 bounce_verifier.py fix_db       # Re-run and restore false positives
"""

import csv
import sys
import os
import time
import urllib.request
import urllib.parse
import json
import re

DB_PATH = os.path.join(os.path.dirname(__file__), "contacts.csv")
FIELDNAMES = ["email", "name", "type", "genre", "notes", "status", "date_added", "date_sent", "bounce"]

# Domains that are always valid (major email providers) — skip MX check
MAJOR_PROVIDERS = {
    "gmail.com", "googlemail.com",
    "hotmail.com", "outlook.com", "live.com", "msn.com", "live.co.uk",
    "yahoo.com", "yahoo.co.uk", "yahoo.fr", "yahoo.de", "yahoo.es",
    "mail.com", "email.com",
    "gmx.com", "gmx.ch", "gmx.de", "gmx.net",
    "icloud.com", "me.com", "mac.com",
    "protonmail.com", "pm.me",
    "aol.com", "zoho.com",
}

# Confirmed dead from actual bounce history (email sent + mailer-daemon bounced)
# These are the ONLY emails we block hard — everything else gets attempted
CONFIRMED_DEAD_DOMAINS = {
    "blackmirrorrecordings.com",  # bounced — domain not found
    "alldayidream.com",           # bounced
    "widerbergmusic.com",         # bounced
    "nanostate.family",           # bounced — address not found
    "ozorafest.hu",               # bounced — address not found
}
CONFIRMED_DEAD_ADDRESSES = {
    "demos@cercle.io",            # bounced
    "demo@innervisions.net",      # bounced
    "deepershades@mail.com",      # bounced — address not found
}


def check_mx_via_doh(domain):
    """Check MX records using Google's DNS-over-HTTPS API."""
    try:
        url = f"https://dns.google/resolve?name={urllib.parse.quote(domain)}&type=MX"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        status = data.get("Status", -1)
        answers = data.get("Answer", [])
        if status == 0 and answers:
            # MX records found
            return True, f"MX OK via DoH ({len(answers)} record(s))"
        elif status == 3:
            return False, f"NXDOMAIN — {domain} does not exist"
        elif status == 0 and not answers:
            # No MX record — try A record fallback
            return check_a_record(domain)
        else:
            return None, f"DNS status code {status} — uncertain"
    except Exception as e:
        return None, f"DoH lookup failed: {e}"


def check_a_record(domain):
    """Fallback: check if domain at least has an A record."""
    try:
        url = f"https://dns.google/resolve?name={urllib.parse.quote(domain)}&type=A"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        status = data.get("Status", -1)
        answers = data.get("Answer", [])
        if status == 0 and answers:
            return True, f"No MX but A record exists — domain is live (may accept email)"
        else:
            return False, f"No MX and no A record — domain appears dead"
    except Exception as e:
        return None, f"A-record fallback failed: {e}"


def verify_email(email):
    """
    Returns: ('valid', reason) | ('invalid', reason) | ('unknown', reason)
    """
    email = email.strip()
    if not email or "@" not in email:
        return "invalid", "Malformed email address"

    domain = email.split("@")[1].lower()

    # Stage 0: Confirmed dead from actual sent bounces
    if email.lower() in CONFIRMED_DEAD_ADDRESSES:
        return "invalid", f"Address {email} confirmed dead (previous send bounce)"
    if domain in CONFIRMED_DEAD_DOMAINS:
        return "invalid", f"Domain {domain} confirmed dead (previous send bounce)"

    # Stage 1: Major email providers — always valid, skip DNS check
    if domain in MAJOR_PROVIDERS:
        return "valid", f"Major email provider ({domain}) — MX check skipped"

    # Stage 2: DNS MX check via Google DoH
    has_mx, reason = check_mx_via_doh(domain)
    if has_mx is True:
        return "valid", reason
    elif has_mx is False:
        return "invalid", reason
    else:
        # Unknown — DoH unavailable; don't block, mark as uncertain
        return "unknown", reason


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


def fix_db():
    """
    Re-evaluate all bounced entries — restore false positives
    (entries marked bounced only by the failed local DNS check, not by actual email bounce).
    """
    rows = load_db()
    restored = 0
    for r in rows:
        if r["bounce"] == "yes" and "PRE-CHECK FAILED" in r.get("notes", "") and r["date_sent"] == "":
            # This was marked bounced ONLY by our pre-check, not by an actual send bounce
            # Re-verify it
            email = r["email"].strip()
            domain = email.split("@")[1].lower() if "@" in email else ""
            result, reason = verify_email(email)
            if result == "valid":
                r["status"] = "pending"
                r["bounce"] = "no"
                # Strip the pre-check failed note
                r["notes"] = re.sub(r" \| PRE-CHECK FAILED:.*$", "", r["notes"])
                print(f"  ✅ RESTORED: {email} — {reason}")
                restored += 1
            elif result == "invalid" and "confirmed dead" not in reason:
                # Still invalid, but update reason
                print(f"  ❌ STILL INVALID: {email} — {reason}")
            else:
                print(f"  ❌ CONFIRMED INVALID: {email} — {reason}")
    if restored:
        save_db(rows)
        print(f"\n  Restored {restored} contacts to pending queue.\n")
    else:
        print("\n  No contacts restored.\n")


def check_all_pending(update_db=True):
    rows = load_db()
    pending = [r for r in rows if r["status"] == "pending"]

    if not pending:
        print("\n✅ No pending contacts to verify.\n")
        return

    print(f"\n{'='*65}")
    print(f"  BOUNCE PRE-CHECK — {len(pending)} pending contacts")
    print(f"{'='*65}")

    safe = []
    flagged = []

    for r in pending:
        email = r["email"].strip()
        print(f"  Checking {email:<42}", end="", flush=True)
        result, reason = verify_email(email)
        if result in ("valid", "unknown"):
            icon = "✅" if result == "valid" else "⚠️ "
            print(f"{icon} {reason[:50]}")
            safe.append(r)
        else:
            print(f"❌ {reason[:55]}")
            flagged.append((r, reason))
            if update_db:
                for row in rows:
                    if row["email"].strip().lower() == email.lower():
                        row["status"] = "bounced"
                        row["bounce"] = "yes"
                        row["notes"] = row["notes"] + f" | PRE-CHECK FAILED: {reason}"
                        break
        time.sleep(0.3)

    if update_db and flagged:
        save_db(rows)

    print(f"\n{'='*65}")
    print(f"  ✅ Safe to send:   {len(safe)}")
    print(f"  ❌ Pre-rejected:   {len(flagged)}")
    print(f"{'='*65}\n")

    if safe:
        print("  READY TO SEND:")
        for r in safe:
            print(f"    {r['email']:<42} {r['name']}")
    print()


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "check" and len(args) >= 2:
        result, reason = verify_email(args[1])
        icon = "✅" if result == "valid" else ("❌" if result == "invalid" else "⚠️")
        print(f"\n  {icon} {args[1]}: {result.upper()} — {reason}\n")
    elif args[0] == "check_all":
        check_all_pending(update_db=True)
    elif args[0] == "fix_db":
        fix_db()
    else:
        print(__doc__)

if __name__ == "__main__":
    main()
