"""
RJM Outreach Agent — YouTube Manual Review (Chrome-integrated auto version)

An upgrade over youtube_manual_review.py. Controls the user's real Google
Chrome via AppleScript, so:

  1. Channels open in-place in the same Chrome tab (no tab explosion)
  2. Your existing Chrome session is used — YouTube's CAPTCHA state persists
     across channels, so solving it once typically unlocks the rest of the
     session (YouTube marks your cookies as human for hours after one solve)
  3. After you click "View email address" on a channel, press Enter and the
     tool reads the email directly from the page DOM via JavaScript — no
     copy-paste required

Flow per channel:
  1. Tool navigates the Chrome tab to the channel's About page
  2. You (optionally) click "View email address" on the page — on first
     channel Google shows a CAPTCHA; click it once and the session is
     unlocked for the rest of the run
  3. Press Enter in the Terminal when you've clicked through
  4. Tool scrapes the current page via Chrome's JavaScript engine and pulls
     the email out. If found, auto-saves it. If multiple emails, picks the
     most-business-y one (promo@/demo@/business@ etc.).
  5. Next channel

First-run setup the tool will walk you through:
  - macOS will prompt: "Terminal wants access to control Google Chrome"
    → click OK (one time only, per Terminal app)
  - Chrome menu: View → Developer → Allow JavaScript from Apple Events
    → check this box (one time only)

Fallbacks:
  - If AppleScript permission is denied → manual paste mode
  - If Chrome's "Allow JavaScript from Apple Events" is off → manual paste
  - If the page has no extractable email → manual paste prompt
  - If you quit mid-session, the next run picks up where you left off
    (same as the basic youtube_manual_review.py)

Entry: `python3 rjm.py youtube review-auto` or direct execution.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import time
from typing import Any

import db

log = logging.getLogger("outreach.youtube_review_auto")

# ─── Email regex + ranker ─────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PREFERRED_EMAIL_PREFIXES = (
    "business", "promo", "submissions", "submission", "contact",
    "demo", "music", "hello", "info", "booking", "management",
)
# Emails matching these patterns are automatically filtered out — they're
# YouTube's own infrastructure, boilerplate, or known junk from About page HTML.
_EMAIL_BLOCKLIST_DOMAINS = ("youtube.com", "google.com", "example.com", "www.aaa.com")
_EMAIL_BLOCKLIST_LOCALPARTS = ("press", "legal", "abuse", "copyright", "support",
                                "noreply", "no-reply", "privacy")


def _rank_emails(emails: list[str]) -> list[str]:
    """Filter junk + sort so preferred prefixes come first."""
    good = []
    for e in emails:
        e_lower = e.lower()
        local, _, domain = e_lower.partition("@")
        if any(domain.endswith(d) for d in _EMAIL_BLOCKLIST_DOMAINS):
            continue
        if local in _EMAIL_BLOCKLIST_LOCALPARTS:
            continue
        good.append(e_lower)
    seen = set()
    uniq = []
    for e in good:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    return sorted(uniq, key=lambda e: (
        0 if e.split("@", 1)[0] in _PREFERRED_EMAIL_PREFIXES else 1,
        e,
    ))


# ─── Terminal colors ──────────────────────────────────────────────────────────
if sys.stdout.isatty():
    _BOLD, _DIM = "\033[1m", "\033[2m"
    _CYAN, _GREEN, _YELLOW, _RED = "\033[36m", "\033[32m", "\033[33m", "\033[31m"
    _RESET = "\033[0m"
    _CLEAR = "\033[2J\033[H"
else:
    _BOLD = _DIM = _CYAN = _GREEN = _YELLOW = _RED = _RESET = _CLEAR = ""


# ─── AppleScript helpers ──────────────────────────────────────────────────────

def _run_applescript(script: str, timeout: int = 10) -> tuple[bool, str]:
    """Execute an AppleScript. Returns (success, stdout_or_stderr)."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)
    if result.returncode != 0:
        return False, (result.stderr or "").strip()
    return True, (result.stdout or "").strip()


def _ensure_chrome_ready() -> tuple[bool, str]:
    """
    Make sure Chrome is running and has a front window. Creates a new window
    if Chrome is running but has no windows. Returns (ready, message).
    """
    script = '''
    tell application "Google Chrome"
        if not running then
            activate
            delay 1
        end if
        activate
        if (count of windows) = 0 then
            make new window
            delay 0.5
        end if
        return "ok"
    end tell
    '''
    return _run_applescript(script, timeout=15)


def _chrome_navigate(url: str) -> tuple[bool, str]:
    """Set the URL of the active tab of Chrome's front window."""
    # Double any " in the URL (there shouldn't be any, but defensive)
    safe_url = url.replace('"', '\\"')
    script = f'''
    tell application "Google Chrome"
        activate
        set URL of active tab of front window to "{safe_url}"
        return "ok"
    end tell
    '''
    return _run_applescript(script, timeout=10)


def _chrome_read_page_text() -> tuple[bool, str]:
    """
    Read document.body.innerText from Chrome's active tab.
    Requires View → Developer → Allow JavaScript from Apple Events to be ON.
    Returns (success, text_or_error).
    """
    script = '''
    tell application "Google Chrome"
        tell active tab of front window
            execute javascript "document.body.innerText"
        end tell
    end tell
    '''
    return _run_applescript(script, timeout=15)


def _chrome_read_visible_html() -> tuple[bool, str]:
    """
    Read document.documentElement.outerHTML from Chrome's active tab. Useful
    for catching emails in data attributes / JSON blobs that innerText misses.
    """
    script = '''
    tell application "Google Chrome"
        tell active tab of front window
            execute javascript "document.documentElement.outerHTML"
        end tell
    end tell
    '''
    return _run_applescript(script, timeout=15)


# ─── DB helpers (shared pattern with youtube_manual_review.py) ───────────────

def _load_pending(limit: int) -> list[dict]:
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, email, name, notes, youtube_channel_id, youtube_channel_url,
                   youtube_subs, youtube_video_count, youtube_genre_match_score,
                   youtube_recent_upload_title
            FROM contacts
            WHERE type = 'youtube'
              AND status = 'skip'
              AND youtube_channel_id IS NOT NULL
              AND (email IS NULL OR email LIKE 'no-email-%')
            ORDER BY COALESCE(youtube_genre_match_score, 0) DESC,
                     COALESCE(youtube_subs, 0) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _update_email(contact_id: int, email: str) -> tuple[bool, str]:
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT id, name, type, status FROM contacts WHERE email = ? AND id != ?",
            (email, contact_id),
        ).fetchone()
        if existing:
            return False, f"already in DB as {existing['type']}/{existing['status']}: {existing['name']}"
        conn.execute(
            "UPDATE contacts SET email = ?, status = 'new' WHERE id = ?",
            (email, contact_id),
        )
    return True, "saved"


def _blocklist(contact_id: int) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE contacts SET status = 'closed' WHERE id = ?", (contact_id,))


# ─── UI helpers ───────────────────────────────────────────────────────────────

def _short(s: str | None, n: int) -> str:
    if not s:
        return ""
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


def _fmt_subs(n: int | None) -> str:
    if not n:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _print_card(i: int, total: int, c: dict) -> None:
    print(_CLEAR, end="")
    score = c.get("youtube_genre_match_score") or 0.0
    subs = _fmt_subs(c.get("youtube_subs"))
    vids = c.get("youtube_video_count") or 0
    print(f"{_BOLD}{_CYAN}━━━ YouTube Review (Auto · Chrome) ━━━{_RESET}")
    print(f"{_DIM}Channel {i} of {total} · {total - i} remaining{_RESET}")
    print()
    print(f"{_BOLD}{c.get('name', '')[:60]}{_RESET}")
    print(f"  {_DIM}score{_RESET}  {score:.2f}   {_DIM}subs{_RESET}  {subs}   {_DIM}videos{_RESET}  {vids}")
    print(f"  {_DIM}last upload{_RESET}  {_short(c.get('youtube_recent_upload_title'), 70)}")
    print()
    notes = c.get("notes") or ""
    if ":" in notes:
        _, desc = notes.split(":", 1)
        desc = desc.strip()
    else:
        desc = notes
    if desc:
        print(f"  {_DIM}{_short(desc, 280)}{_RESET}")
        print()


# ─── First-run setup flow ─────────────────────────────────────────────────────

def _first_run_check() -> bool:
    """
    Verify:
      1. Chrome is installed and can be activated via AppleScript
      2. AppleScript can navigate a tab (tests Automation permission)
      3. Chrome can execute JavaScript from Apple Events

    If any step fails, print human instructions and return False.
    """
    print(f"{_BOLD}Checking Chrome integration...{_RESET}")

    ok, msg = _ensure_chrome_ready()
    if not ok:
        print(f"{_RED}✗ Chrome not reachable: {msg}{_RESET}")
        if "not authori" in msg.lower() or "1743" in msg:
            print()
            print(f"{_YELLOW}macOS needs permission to let Terminal control Chrome.{_RESET}")
            print(f"  1. A dialog may have just appeared — click {_BOLD}OK{_RESET}.")
            print(f"  2. If no dialog: System Settings → Privacy & Security → Automation")
            print(f"     Find {_BOLD}Terminal{_RESET} in the list and enable {_BOLD}Google Chrome{_RESET}.")
            print(f"  3. Re-run this tool.")
        else:
            print(f"  Make sure Google Chrome is installed at /Applications/Google Chrome.app")
        return False
    print(f"{_GREEN}✓ Chrome reachable{_RESET}")

    # Test JS execution with a harmless command
    ok, msg = _run_applescript(
        'tell application "Google Chrome" to tell active tab of front window to execute javascript "1+1"',
        timeout=5,
    )
    if not ok or msg.strip() not in ("2", "2.0"):
        print(f"{_YELLOW}⚠  JavaScript from Apple Events is OFF in Chrome.{_RESET}")
        print(f"    Error: {msg[:100]}")
        print()
        print(f"{_BOLD}To enable (one-time setup):{_RESET}")
        print(f"  1. Open Chrome → menu bar → {_BOLD}View{_RESET}")
        print(f"  2. → {_BOLD}Developer{_RESET}")
        print(f"  3. → {_BOLD}Allow JavaScript from Apple Events{_RESET} (toggle on)")
        print(f"  4. Re-run this tool.")
        print()
        print(f"{_DIM}Falling back to manual paste mode (still works, just slower).{_RESET}")
        return False
    print(f"{_GREEN}✓ JavaScript from Apple Events is enabled{_RESET}")
    print()
    return True


# ─── Main loop ────────────────────────────────────────────────────────────────

def _scrape_email_from_chrome() -> list[str]:
    """
    After navigation + user action, read the current Chrome page and extract
    all email candidates. Checks both innerText (what's visible) and outerHTML
    (catches emails in JSON blobs and data attributes).
    """
    found: list[str] = []
    ok, text = _chrome_read_page_text()
    if ok and text:
        found.extend(_EMAIL_RE.findall(text))
    # Also scan the raw HTML for good measure (catches ytInitialData blobs)
    ok2, html = _chrome_read_visible_html()
    if ok2 and html:
        found.extend(_EMAIL_RE.findall(html[:600_000]))
    return _rank_emails(found)


def _prompt_after_navigate() -> str:
    """
    After we've navigated to the channel, prompt the user for their next move.
    Returns one of:
      'scrape'  → scrape Chrome and save what we find (Enter / just press Enter)
      's'       → skip this channel
      'b'       → blocklist
      'q'       → quit
      email str → user pasted a specific email to save
    """
    while True:
        try:
            raw = input(
                f"  {_BOLD}Click 'View email address' on the page, then press Enter{_RESET}\n"
                f"  {_DIM}(or {_YELLOW}s{_RESET}{_DIM}=skip / {_RED}b{_RESET}{_DIM}=blocklist / "
                f"{_DIM}q=quit / paste an email to save directly){_RESET}: "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            return "q"
        if raw == "":
            return "scrape"
        if raw in ("q", "Q", "quit"):
            return "q"
        if raw in ("s", "S", "skip"):
            return "s"
        if raw in ("b", "B", "block", "blocklist"):
            return "b"
        if _EMAIL_RE.match(raw):
            return raw.lower()
        print(f"  {_RED}✗ not a valid email or command — try again{_RESET}")


def run_review_auto(limit: int = 50, allow_fallback: bool = True) -> dict[str, int]:
    pending = _load_pending(limit)
    if not pending:
        print(f"{_GREEN}No skip-status YouTube contacts to review. All clear.{_RESET}")
        return {"reviewed": 0, "saved": 0, "skipped": 0, "blocked": 0, "auto_scraped": 0}

    total = len(pending)
    stats = {"reviewed": 0, "saved": 0, "skipped": 0, "blocked": 0, "auto_scraped": 0}

    chrome_ready = _first_run_check()
    if not chrome_ready and not allow_fallback:
        print(f"{_RED}Chrome not ready and fallback disabled — aborting.{_RESET}")
        return stats
    if not chrome_ready:
        print(f"{_YELLOW}Continuing in manual-paste fallback mode.{_RESET}")
        print(f"{_DIM}Fix the issues above to enable auto-scraping on the next run.{_RESET}")
        print()

    print(f"{_BOLD}Loaded {total} skip-status YouTube contacts.{_RESET}")
    print(f"{_DIM}Ordered by genre_match_score DESC (psytrance first).{_RESET}")
    print()
    try:
        input(f"  {_DIM}Press Enter to start (Ctrl+C to abort)...{_RESET}")
    except (KeyboardInterrupt, EOFError):
        return stats

    for i, c in enumerate(pending, start=1):
        _print_card(i, total, c)

        url = f"{c.get('youtube_channel_url', '')}/about"
        if chrome_ready and url.startswith("http"):
            ok, msg = _chrome_navigate(url)
            if ok:
                print(f"  {_GREEN}→ opened in Chrome{_RESET}  {url}")
            else:
                print(f"  {_RED}✗ Chrome navigation failed: {msg}{_RESET}")
                print(f"  {_DIM}Open manually: {url}{_RESET}")
            time.sleep(1.5)  # give Chrome a beat to render before any auto-scrape
        else:
            print(f"  {_CYAN}{url}{_RESET}")

        while True:  # inner loop per channel so failed scrapes can retry
            action = _prompt_after_navigate()
            if action == "q":
                print(f"\n{_DIM}Quit — progress saved. Run again to resume.{_RESET}")
                return stats
            if action == "s":
                stats["skipped"] += 1
                stats["reviewed"] += 1
                print(f"  {_DIM}skipped{_RESET}")
                break
            if action == "b":
                _blocklist(c["id"])
                stats["blocked"] += 1
                stats["reviewed"] += 1
                print(f"  {_RED}blocklisted{_RESET}")
                break
            if action == "scrape":
                if not chrome_ready:
                    print(f"  {_YELLOW}Auto-scrape disabled — please paste the email directly{_RESET}")
                    continue
                print(f"  {_DIM}Reading page...{_RESET}")
                candidates = _scrape_email_from_chrome()
                if not candidates:
                    print(f"  {_YELLOW}No email found on page. Options:{_RESET}")
                    print(f"  {_DIM}  - Click 'View email address' then press Enter again{_RESET}")
                    print(f"  {_DIM}  - Paste the email directly{_RESET}")
                    print(f"  {_DIM}  - Type 's' to skip or 'b' to blocklist{_RESET}")
                    continue
                # Pick the best-ranked candidate
                chosen = candidates[0]
                if len(candidates) > 1:
                    print(f"  {_DIM}found {len(candidates)}: {', '.join(candidates[:4])}{_RESET}")
                ok, reason = _update_email(c["id"], chosen)
                if not ok:
                    print(f"  {_YELLOW}⚠  {reason}{_RESET}")
                    print(f"  {_DIM}Try pasting a different one, or 's' to skip{_RESET}")
                    continue
                stats["saved"] += 1
                stats["auto_scraped"] += 1
                stats["reviewed"] += 1
                print(f"  {_GREEN}✓ saved (auto) {chosen}{_RESET}")
                break
            # action is a pasted email
            ok, reason = _update_email(c["id"], action)
            if not ok:
                print(f"  {_YELLOW}⚠  {reason}{_RESET}")
                continue
            stats["saved"] += 1
            stats["reviewed"] += 1
            print(f"  {_GREEN}✓ saved {action}{_RESET}")
            break

    print(f"\n{_BOLD}{_GREEN}━━━ Review complete ━━━{_RESET}")
    for k, v in stats.items():
        print(f"  {k:<14} {v}")
    return stats


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(
        description="Chrome-integrated manual review — auto-scrapes email from "
                    "the current Chrome page after you click 'View email address'."
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--no-fallback", action="store_true",
        help="Abort instead of falling back to manual paste mode when Chrome "
             "integration isn't ready.",
    )
    args = parser.parse_args()
    try:
        run_review_auto(limit=args.limit, allow_fallback=not args.no_fallback)
    except KeyboardInterrupt:
        print("\ninterrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
