"""
RJM Outreach Agent — YouTube Manual Review Tool

Interactive terminal workflow for turning qualified-but-email-skipped
YouTube contacts into sendable contacts.

How it works:
  1. Loads top N skip-status youtube contacts, ordered by genre_match_score DESC
     (so psytrance channels come first, then melodic, then organic/Christian).
  2. For each channel: prints name/subs/score/recent upload title + description
     snippet, opens the channel's About page in the default browser.
  3. You click "View email address" on YouTube, copy the email, paste it back
     into the terminal. The script validates, writes it to the DB, and
     promotes status: 'skip' → 'new' so the outreach agent picks it up
     on the next send cycle.
  4. Press 's' to skip (channel has no email / not worth emailing).
     Press 'b' to blocklist (never show again — wrong fit).
     Press 'q' to quit and resume later.
     Empty enter re-opens the About page (in case you closed the tab).

Resumable: each run shows only contacts still in 'skip' status with
no real email — so quitting + restarting picks up where you left off.

Entry: `python3 rjm.py youtube review` or `python3 youtube_manual_review.py`
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import webbrowser
from typing import Any

import db

log = logging.getLogger("outreach.youtube_review")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Terminal colors (fallback to plain text if not a TTY)
if sys.stdout.isatty():
    _BOLD = "\033[1m"
    _DIM  = "\033[2m"
    _CYAN = "\033[36m"
    _GREEN = "\033[32m"
    _YELLOW = "\033[33m"
    _RED = "\033[31m"
    _RESET = "\033[0m"
    _CLEAR = "\033[2J\033[H"
else:
    _BOLD = _DIM = _CYAN = _GREEN = _YELLOW = _RED = _RESET = _CLEAR = ""


def _load_pending(limit: int) -> list[dict]:
    """Fetch skip-status youtube contacts awaiting manual email review."""
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, email, name, notes, youtube_channel_id, youtube_channel_url,
                   youtube_subs, youtube_video_count, youtube_genre_match_score,
                   youtube_recent_upload_title, youtube_last_upload_at
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


def _short(s: str | None, n: int) -> str:
    if not s:
        return ""
    s = " ".join(s.split())  # collapse whitespace
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
    subs  = _fmt_subs(c.get("youtube_subs"))
    vids  = c.get("youtube_video_count") or 0
    url   = c.get("youtube_channel_url") or ""
    print(f"{_BOLD}{_CYAN}━━━ YouTube Manual Review ━━━{_RESET}")
    print(f"{_DIM}Channel {i} of {total} · {total - i} remaining{_RESET}")
    print()
    print(f"{_BOLD}{c.get('name', '')[:60]}{_RESET}")
    print(f"  {_DIM}score{_RESET}  {score:.2f}   {_DIM}subs{_RESET}  {subs}   {_DIM}videos{_RESET}  {vids}")
    print(f"  {_DIM}last upload{_RESET}  {_short(c.get('youtube_recent_upload_title'), 70)}")
    print()
    # notes field contains "title: description[:200]"
    notes = c.get("notes") or ""
    if ":" in notes:
        _, desc = notes.split(":", 1)
        desc = desc.strip()
    else:
        desc = notes
    if desc:
        print(f"  {_DIM}{_short(desc, 300)}{_RESET}")
        print()
    print(f"  {_CYAN}{url}/about{_RESET}")
    print()


def _prompt_email() -> str:
    """
    Read an email or control char from stdin. Returns:
      - valid email string  → save it
      - 's' → skip (leave as skip, blocklist via empty follow-up)
      - 'b' → blocklist (status='closed')
      - 'q' → quit session
      - ''  → re-open the About page in the browser
    """
    while True:
        try:
            raw = input(f"  {_BOLD}email{_RESET} (or {_YELLOW}s{_RESET}=skip / {_RED}b{_RESET}=blocklist / {_DIM}q{_RESET}=quit / enter=reopen): ").strip()
        except (KeyboardInterrupt, EOFError):
            return "q"
        if raw in ("q", "Q", "quit", "exit"):
            return "q"
        if raw in ("s", "S", "skip"):
            return "s"
        if raw in ("b", "B", "block", "blocklist"):
            return "b"
        if raw == "":
            return ""
        # Validate email
        if _EMAIL_RE.match(raw):
            return raw.lower()
        print(f"  {_RED}✗ not a valid email — try again (or s/b/q){_RESET}")


def _update_email(contact_id: int, email: str) -> bool:
    """
    Promote a skip-status youtube contact to 'new' with its real email.
    Returns False if the email is already owned by another contact
    (email-level dedup) so the user can try a different one.
    """
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT id, name, type, status FROM contacts WHERE email = ? AND id != ?",
            (email, contact_id),
        ).fetchone()
        if existing:
            print(
                f"  {_YELLOW}⚠  already in DB as {existing['type']}/"
                f"{existing['status']}: {existing['name']}{_RESET}"
            )
            return False
        conn.execute(
            "UPDATE contacts SET email = ?, status = 'new' WHERE id = ?",
            (email, contact_id),
        )
    return True


def _blocklist(contact_id: int) -> None:
    """Mark contact as closed — won't show up in review again or send."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET status = 'closed' WHERE id = ?",
            (contact_id,),
        )


def run_review(limit: int = 50, auto_open: bool = True) -> dict[str, int]:
    """Main interactive loop. Returns {reviewed, saved, skipped, blocked}."""
    pending = _load_pending(limit)
    if not pending:
        print(f"{_GREEN}No skip-status YouTube contacts to review. All clear.{_RESET}")
        return {"reviewed": 0, "saved": 0, "skipped": 0, "blocked": 0}

    total = len(pending)
    stats = {"reviewed": 0, "saved": 0, "skipped": 0, "blocked": 0}

    print(f"\n{_BOLD}Loading top {total} skip-status YouTube contacts by score...{_RESET}")
    print(f"{_DIM}Opens each channel's About page in your browser.{_RESET}")
    print(f"{_DIM}Click 'View email address' → copy → paste back here.{_RESET}\n")
    try:
        input(f"  {_DIM}Press Enter to start (Ctrl+C to abort)...{_RESET}")
    except (KeyboardInterrupt, EOFError):
        print("\naborted.")
        return stats

    for i, c in enumerate(pending, start=1):
        while True:
            _print_card(i, total, c)
            url = f"{c.get('youtube_channel_url', '')}/about"
            if auto_open and url and url.startswith("http"):
                try:
                    webbrowser.open(url, new=2)
                except Exception:
                    pass
            action = _prompt_email()
            if action == "q":
                print(f"\n{_DIM}Quit — progress saved. Run again to resume.{_RESET}")
                return stats
            if action == "":
                # Loop back — reopen
                continue
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
            # email
            ok = _update_email(c["id"], action)
            if not ok:
                # Loop back — ask again
                continue
            stats["saved"] += 1
            stats["reviewed"] += 1
            print(f"  {_GREEN}✓ saved {action}{_RESET}")
            break

    print(f"\n{_BOLD}{_GREEN}━━━ Review complete ━━━{_RESET}")
    for k, v in stats.items():
        print(f"  {k:<10} {v}")
    return stats


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,  # keep the UI clean
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Interactive review of skip-status YouTube contacts — "
                    "open About page, paste email, move on."
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="How many top-scored contacts to review in this session (default 50)",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="Don't auto-open the About page in the browser (just print the URL)",
    )
    args = parser.parse_args()

    try:
        run_review(limit=args.limit, auto_open=not args.no_open)
    except KeyboardInterrupt:
        print("\ninterrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
