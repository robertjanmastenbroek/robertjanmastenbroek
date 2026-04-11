"""
RJM Outreach Agent — Email Bounce Pre-Verifier

Verification pipeline (in order):
  1. Confirmed-dead cache (DB + seeds) — instant block
  2. Major provider fast-path — skip DNS for known good domains
  3. Disify.com API — MX check + disposable/throwaway email detection (free, no key)
  4. Google DNS-over-HTTPS MX/A fallback — catches dead domains if Disify is down

Dead domains/addresses are persisted to the DB so they survive restarts.
Everything uncertain is allowed through — better a risky send than a false block.
"""

import time
import urllib.request
import urllib.parse
import json
import logging

log = logging.getLogger("outreach.bounce")

# Major email providers — always valid, skip DNS check
MAJOR_PROVIDERS = {
    "gmail.com","googlemail.com","hotmail.com","outlook.com","live.com","msn.com",
    "yahoo.com","yahoo.co.uk","yahoo.fr","yahoo.de","yahoo.es","mail.com","email.com",
    "gmx.com","gmx.ch","gmx.de","gmx.net","icloud.com","me.com","mac.com",
    "protonmail.com","pm.me","aol.com","zoho.com","live.co.uk",
}

# Seed list — these were hard-confirmed dead before the DB existed.
# New dead domains/addresses are persisted to the DB automatically.
_SEED_DEAD_DOMAINS = {
    "blackmirrorrecordings.com",
    "alldayidream.com",
    "widerbergmusic.com",
    "nanostate.family",
    "ozorafest.hu",
}
_SEED_DEAD_ADDRESSES = {
    "demos@cercle.io",
    "demo@innervisions.net",
    "deepershades@mail.com",
}

# In-memory cache — loaded once from DB + seeds on first use
_dead_domains = None   # type: set or None
_dead_addresses = None  # type: set or None


def _load_dead_lists():
    """Load confirmed dead lists from DB (merged with seeds). Called once."""
    global _dead_domains, _dead_addresses
    if _dead_domains is not None:
        return
    try:
        import db as _db
        _dead_domains  = _SEED_DEAD_DOMAINS  | _db.get_confirmed_dead_domains()
        _dead_addresses = _SEED_DEAD_ADDRESSES | _db.get_confirmed_dead_addresses()
    except Exception:
        # DB not initialised yet — fall back to seeds only
        _dead_domains   = set(_SEED_DEAD_DOMAINS)
        _dead_addresses = set(_SEED_DEAD_ADDRESSES)


def _doh_query(domain, record_type="MX"):
    """Query Google DNS-over-HTTPS. Returns (status_code, answers_list)."""
    try:
        url = (
            f"https://dns.google/resolve"
            f"?name={urllib.parse.quote(domain)}&type={record_type}"
        )
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return data.get("Status", -1), data.get("Answer", [])
    except Exception as exc:
        log.debug("DoH query failed for %s/%s: %s", domain, record_type, exc)
        return -1, []


def _disify_check(email: str) -> tuple[str, str]:
    """
    Query Disify.com — free, no API key, no signup required.
    Checks: email format, MX record existence, disposable/throwaway domain.
    Returns: ('invalid', reason) | ('valid', reason) | ('unknown', reason)
    """
    try:
        url = f"https://disify.com/api/email/{urllib.parse.quote(email)}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; email-verifier/1.0)",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())

        if not data.get("format", True):
            return "invalid", "Disify: invalid email format"
        if not data.get("dns", True):
            return "invalid", "Disify: domain has no MX record"
        if data.get("disposable", False):
            return "invalid", f"Disify: disposable/throwaway email domain"
        if data.get("alias", False):
            # Alias/forwarding addresses are risky but not definitively dead
            log.debug("Disify flagged %s as alias — allowing through", email)

        return "valid", "Disify: format OK, MX OK, not disposable"

    except Exception as exc:
        log.debug("Disify check failed for %s: %s", email, exc)
        return "unknown", f"Disify unreachable: {exc}"


def verify_email(email: str) -> tuple[str, str]:
    """
    Returns: ('valid', reason) | ('invalid', reason) | ('unknown', reason)
    'unknown' means all checks were inconclusive — we don't block on uncertainty.
    """
    _load_dead_lists()

    email = email.strip()
    if not email or "@" not in email:
        return "invalid", "Malformed email address"

    domain   = email.split("@")[1].lower()
    email_lc = email.lower()

    # Stage 0: Confirmed dead from DB + seed history
    if email_lc in _dead_addresses:
        return "invalid", "Address confirmed dead from prior bounce"
    if domain in _dead_domains:
        return "invalid", f"Domain {domain} confirmed dead from prior bounce"

    # Stage 1: Major providers — skip DNS (can't pre-verify mailboxes anyway)
    if domain in MAJOR_PROVIDERS:
        return "valid", f"Major provider ({domain}) — skipping DNS"

    # Stage 2: Disify — MX check + disposable domain detection (free, no key)
    disify_result, disify_reason = _disify_check(email)
    if disify_result == "invalid":
        return "invalid", disify_reason
    if disify_result == "valid":
        return "valid", disify_reason

    # Stage 3: Fallback to direct DoH MX check (if Disify was unreachable)
    status, answers = _doh_query(domain, "MX")
    if status == 0 and answers:
        return "valid", f"MX OK ({len(answers)} record(s))"
    if status == 3:
        return "invalid", f"NXDOMAIN — {domain} does not exist"
    if status == 0 and not answers:
        a_status, a_answers = _doh_query(domain, "A")
        if a_status == 0 and a_answers:
            return "valid", "No MX but A record exists — domain live"
        return "invalid", "No MX and no A record — domain appears dead"

    # All checks inconclusive — allow through
    return "unknown", "DNS unreachable — allowing through"


def verify_batch(emails: list[str], delay: float = 0.3) -> dict[str, tuple[str, str]]:
    """Verify a list of emails. Returns dict {email: (result, reason)}."""
    results = {}
    for email in emails:
        results[email] = verify_email(email)
        time.sleep(delay)
    return results


def add_confirmed_dead_domain(domain: str, reason: str = "manually confirmed"):
    """Add a dead domain to the in-memory cache and persist to DB."""
    _load_dead_lists()
    domain = domain.lower().strip()
    _dead_domains.add(domain)
    try:
        import db as _db
        _db.save_dead_domain(domain, reason)
    except Exception as exc:
        log.warning("Could not persist dead domain %s to DB: %s", domain, exc)
    log.info("Added confirmed dead domain: %s", domain)


def add_confirmed_dead_address(email: str, reason: str = "manually confirmed"):
    """Add a dead address to the in-memory cache and persist to DB."""
    _load_dead_lists()
    email = email.lower().strip()
    _dead_addresses.add(email)
    try:
        import db as _db
        _db.save_dead_address(email, reason)
    except Exception as exc:
        log.warning("Could not persist dead address %s to DB: %s", email, exc)
    log.info("Added confirmed dead address: %s", email)
