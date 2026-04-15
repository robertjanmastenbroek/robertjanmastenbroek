"""
RJM Outreach Agent — YouTube Manual Review (Playwright edition)

This is the simplest, most reliable version of the manual review workflow.
It drives a dedicated Chromium window via Playwright instead of trying to
automate the user's main Chrome through AppleScript.

Why this is better than the AppleScript version:
  - No macOS Automation permission prompts
  - No "Allow JavaScript from Apple Events" toggle required
  - One tab, navigated in-place — `page.goto()` reuses the same tab
  - Persistent user_data_dir: cookies/localStorage persist between sessions,
    so the CAPTCHA on channel 1 unlocks every channel for hours afterward
    AND across future runs until the cookie expires
  - Full DOM access for reliable email extraction
  - Works the same way on every macOS version

Flow per channel:
  1. Chromium navigates (in-place) to the channel's About page
  2. You click "View email address" in the Chromium window (first channel
     needs a CAPTCHA solve — YouTube remembers you after that)
  3. Return to the terminal and press Enter
  4. The tool reads the page DOM via page.evaluate(), regexes for email,
     picks the best candidate (business@/promo@/demo@/contact@ preferred,
     junk like press@youtube.com filtered), saves to DB, promotes status
     'skip' → 'new', navigates to the next channel

Persistent state location:
  outreach_agent/.playwright_profile/
  This is a dedicated browser profile (cookies, localStorage, cache). It's
  gitignored and survives restarts. Delete it to reset CAPTCHA state.

Entry: `python3 rjm.py youtube review` (default after this change)
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

import db

log = logging.getLogger("outreach.youtube_review_pw")

# ─── Email regex + ranker ─────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PREFERRED_EMAIL_PREFIXES = (
    "business", "promo", "submissions", "submission", "contact",
    "demo", "music", "hello", "info", "booking", "management",
)
_EMAIL_BLOCKLIST_DOMAINS = (
    "youtube.com", "google.com", "example.com", "www.aaa.com",
    "schema.org", "w3.org",
)
_EMAIL_BLOCKLIST_LOCALPARTS = (
    "press", "legal", "abuse", "copyright", "support",
    "noreply", "no-reply", "privacy", "dmca",
)

_PROFILE_DIR = Path(__file__).parent / ".playwright_profile"


def _rank_emails(emails: list[str]) -> list[str]:
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


# ─── DB helpers ───────────────────────────────────────────────────────────────

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
    print(f"{_BOLD}{_CYAN}━━━ YouTube Review (Playwright) ━━━{_RESET}")
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


def _prompt() -> str:
    """
    Returns:
      'scrape'  → press Enter (auto-scrape current page)
      's' / 'b' / 'q'
      email str → user pasted a specific email
    """
    while True:
        try:
            raw = input(
                f"  {_BOLD}Click 'View email address' in Chromium → back here → press Enter{_RESET}\n"
                f"  {_DIM}(or paste an email / s=skip / b=blocklist / q=quit): {_RESET}"
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


def _extract_emails_from_page(page) -> list[str]:
    """
    Pull all emails from the current page — both visible text and raw HTML.
    Catches emails in description text AND in embedded ytInitialData JSON.
    """
    found: list[str] = []
    try:
        text = page.evaluate("() => document.body.innerText || ''")
        if text:
            found.extend(_EMAIL_RE.findall(text))
    except Exception as e:
        log.debug("innerText read failed: %s", e)
    try:
        html = page.content()
        if html:
            found.extend(_EMAIL_RE.findall(html[:800_000]))
    except Exception as e:
        log.debug("content read failed: %s", e)
    return _rank_emails(found)


def _try_auto_click_view_email(page) -> bool:
    """
    Best-effort: click any visible 'View email address' button on the page
    before the user does. Returns True if a button was clicked. YouTube will
    then show a CAPTCHA — the user handles it.
    """
    try:
        # Try a few selectors that YouTube uses for the business-email reveal
        selectors = [
            "text=/view email address/i",
            "text=/business inquiries/i",
            "button:has-text('View email address')",
            "yt-button-renderer:has-text('View email')",
        ]
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if locator.count() > 0 and locator.is_visible():
                    locator.click(timeout=2000)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


# ─── Main loop ────────────────────────────────────────────────────────────────

def run_review_pw(limit: int = 50) -> dict[str, int]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"{_RED}✗ playwright not installed in this Python.{_RESET}")
        print(f"  Run: {_BOLD}outreach_agent/venv/bin/python3 -m pip install playwright && "
              f"outreach_agent/venv/bin/python3 -m playwright install chromium{_RESET}")
        return {"reviewed": 0, "saved": 0, "skipped": 0, "blocked": 0, "auto_scraped": 0}

    pending = _load_pending(limit)
    if not pending:
        print(f"{_GREEN}No skip-status YouTube contacts to review. All clear.{_RESET}")
        return {"reviewed": 0, "saved": 0, "skipped": 0, "blocked": 0, "auto_scraped": 0}

    total = len(pending)
    stats = {"reviewed": 0, "saved": 0, "skipped": 0, "blocked": 0, "auto_scraped": 0}

    _PROFILE_DIR.mkdir(exist_ok=True)

    print(f"{_BOLD}YouTube Review — Playwright edition{_RESET}")
    print(f"{_DIM}Loaded {total} skip-status channels, ordered by genre match score.{_RESET}")
    print(f"{_DIM}A dedicated Chromium window will open. First channel will{_RESET}")
    print(f"{_DIM}need a CAPTCHA solve — after that YouTube remembers you.{_RESET}")
    print(f"{_DIM}Profile dir: {_PROFILE_DIR}{_RESET}")
    print()
    try:
        input(f"  {_DIM}Press Enter to launch Chromium and start...{_RESET}")
    except (KeyboardInterrupt, EOFError):
        return stats

    with sync_playwright() as pw:
        # Persistent context — cookies + localStorage survive across runs
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(_PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )
        # Reuse the first open page if Playwright created one, otherwise make one
        if context.pages:
            page = context.pages[0]
        else:
            page = context.new_page()

        try:
            for i, c in enumerate(pending, start=1):
                _print_card(i, total, c)

                url = f"{c.get('youtube_channel_url', '')}/about"
                if not url.startswith("http"):
                    print(f"  {_YELLOW}no channel URL — skipping{_RESET}")
                    stats["skipped"] += 1
                    stats["reviewed"] += 1
                    continue

                try:
                    page.goto(url, timeout=30_000, wait_until="domcontentloaded")
                    time.sleep(1.5)  # let the About section render
                    print(f"  {_GREEN}→ opened in Chromium{_RESET}  {url}")
                except Exception as e:
                    print(f"  {_RED}✗ navigation failed: {e}{_RESET}")
                    stats["skipped"] += 1
                    stats["reviewed"] += 1
                    continue

                # Best-effort: try to auto-click the "View email address" button
                # so the user doesn't have to find it themselves
                if _try_auto_click_view_email(page):
                    print(f"  {_DIM}auto-clicked 'View email address' — solve the CAPTCHA if shown{_RESET}")

                # Also do an opportunistic scrape before any user action — some
                # channels put emails directly in the description, no click needed
                pre_emails = _extract_emails_from_page(page)
                if pre_emails:
                    chosen = pre_emails[0]
                    # Confirm via prompt (fast path — user just hits Enter)
                    print(f"  {_GREEN}Found email without clicking: {chosen}{_RESET}")
                    print(f"  {_DIM}(other candidates: {', '.join(pre_emails[1:4]) or 'none'}){_RESET}")

                while True:
                    action = _prompt()
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
                        candidates = _extract_emails_from_page(page)
                        if not candidates:
                            print(f"  {_YELLOW}No email found on page. Options:{_RESET}")
                            print(f"  {_DIM}  - Click 'View email address' then press Enter again{_RESET}")
                            print(f"  {_DIM}  - Paste the email directly{_RESET}")
                            print(f"  {_DIM}  - 's' to skip, 'b' to blocklist{_RESET}")
                            continue
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
                    # pasted email
                    ok, reason = _update_email(c["id"], action)
                    if not ok:
                        print(f"  {_YELLOW}⚠  {reason}{_RESET}")
                        continue
                    stats["saved"] += 1
                    stats["reviewed"] += 1
                    print(f"  {_GREEN}✓ saved {action}{_RESET}")
                    break
        finally:
            try:
                context.close()
            except Exception:
                pass

    print(f"\n{_BOLD}{_GREEN}━━━ Review complete ━━━{_RESET}")
    for k, v in stats.items():
        print(f"  {k:<14} {v}")
    return stats


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(
        description="Playwright-based YouTube channel email review — drives a "
                    "dedicated Chromium window, auto-scrapes emails from the DOM."
    )
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    try:
        run_review_pw(limit=args.limit)
    except KeyboardInterrupt:
        print("\ninterrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
