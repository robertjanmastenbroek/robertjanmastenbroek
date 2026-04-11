#!/usr/bin/env python3
"""
RJM Spotify Growth Tracker
Tracks monthly listeners, followers, and progress toward 1M goal.

Usage:
  python3 spotify_tracker.py log <monthly_listeners>         # Manually log current count
  python3 spotify_tracker.py log <monthly_listeners> <followers>  # Log with follower count
  python3 spotify_tracker.py status                          # Show current stats and trend
  python3 spotify_tracker.py milestone                       # Show next milestone and % progress
  python3 spotify_tracker.py history                         # Show ASCII bar chart of last 30 readings
"""

import sys
import os
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(__file__))

import db

GOAL = 1_000_000
SPOTIFY_ARTIST_ID = "2Seaafm5k1hAuCkpdq7yds"

MILESTONES = [
    (1_000,    "1K"),
    (5_000,    "5K"),
    (10_000,   "10K"),
    (25_000,   "25K"),
    (50_000,   "50K"),
    (100_000,  "100K"),
    (250_000,  "250K"),
    (500_000,  "500K"),
    (1_000_000, "1M"),
]


# ─── Schema ──────────────────────────────────────────────────────────────────

SPOTIFY_STATS_SCHEMA = """
CREATE TABLE IF NOT EXISTS spotify_stats (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT    NOT NULL,
    monthly_listeners INTEGER NOT NULL,
    followers        INTEGER DEFAULT 0,
    source           TEXT    DEFAULT 'manual',
    notes            TEXT    DEFAULT ''
);
"""


def ensure_table():
    """Create spotify_stats table if it doesn't exist yet."""
    with db.get_conn() as conn:
        conn.executescript(SPOTIFY_STATS_SCHEMA)


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_log(monthly_listeners: int, followers: int = 0, notes: str = "", source: str = "manual"):
    ensure_table()
    today = str(date.today())

    with db.get_conn() as conn:
        # Check if we already have a reading today
        existing = conn.execute(
            "SELECT id, monthly_listeners FROM spotify_stats WHERE date = ? ORDER BY id DESC LIMIT 1",
            (today,)
        ).fetchone()

        if existing:
            print(f"  Note: replacing today's earlier reading ({existing['monthly_listeners']:,} listeners)")
            conn.execute(
                "UPDATE spotify_stats SET monthly_listeners=?, followers=?, notes=?, source=? WHERE id=?",
                (monthly_listeners, followers, notes, source, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO spotify_stats (date, monthly_listeners, followers, source, notes) VALUES (?, ?, ?, ?, ?)",
                (today, monthly_listeners, followers, source, notes)
            )

    print(f"\n  Logged: {monthly_listeners:,} monthly listeners on {today}")
    print(f"  Progress to 1M: {monthly_listeners / GOAL * 100:.2f}%")

    # Show delta if previous reading exists
    with db.get_conn() as conn:
        prev = conn.execute(
            "SELECT date, monthly_listeners FROM spotify_stats WHERE date < ? ORDER BY date DESC LIMIT 1",
            (today,)
        ).fetchone()
        if prev:
            delta = monthly_listeners - prev["monthly_listeners"]
            days_between = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(prev["date"], "%Y-%m-%d")).days
            sign = "+" if delta >= 0 else ""
            print(f"  Change since {prev['date']}: {sign}{delta:,} listeners ({days_between} days)")


def cmd_status():
    ensure_table()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, monthly_listeners, followers FROM spotify_stats ORDER BY date DESC LIMIT 10"
        ).fetchall()

    if not rows:
        print("\n  No data yet. Run: python3 spotify_tracker.py log <listeners>")
        return

    rows = [dict(r) for r in rows]
    current = rows[0]
    current_listeners = current["monthly_listeners"]
    current_date = current["date"]

    print(f"\n{'='*55}")
    print(f"  RJM SPOTIFY STATUS — {date.today()}")
    print(f"  Artist ID: {SPOTIFY_ARTIST_ID}")
    print(f"{'='*55}\n")

    print(f"  Monthly Listeners  : {current_listeners:>12,}")
    if current.get("followers"):
        print(f"  Followers          : {current['followers']:>12,}")
    print(f"  Goal               : {GOAL:>12,}")
    print(f"  Remaining          : {GOAL - current_listeners:>12,}")
    print(f"  Last updated       : {current_date}")

    # Growth trend (compare to previous reading)
    if len(rows) >= 2:
        prev = rows[1]
        delta = current_listeners - prev["monthly_listeners"]
        days_between = (
            datetime.strptime(current_date, "%Y-%m-%d") -
            datetime.strptime(prev["date"], "%Y-%m-%d")
        ).days or 1

        weekly_growth = (delta / days_between) * 7
        sign = "+" if delta >= 0 else ""
        print(f"\n  Growth since {prev['date']}:")
        print(f"    Change            : {sign}{delta:,}")
        print(f"    Days between      : {days_between}")
        print(f"    Weekly rate       : {sign}{weekly_growth:,.0f} listeners/week")

        if weekly_growth > 0:
            listeners_needed = GOAL - current_listeners
            weeks_needed = listeners_needed / weekly_growth
            eta = date.today() + timedelta(weeks=weeks_needed)
            print(f"    ETA to 1M (rate)  : {eta.strftime('%b %Y')}  ({weeks_needed:.0f} weeks)")
        elif delta <= 0:
            print(f"    ETA to 1M         : N/A — need positive growth")

    # Next milestone
    next_ms = None
    for threshold, label in MILESTONES:
        if current_listeners < threshold:
            next_ms = (threshold, label)
            break

    if next_ms:
        gap = next_ms[0] - current_listeners
        print(f"\n  Next milestone     : {next_ms[1]} — {gap:,} away")

    # Top performing tracks (known data — update as tracks release)
    print(f"\n  TOP TRACKS (update as Spotify for Artists data updates):")
    tracks = [
        ("Track data not yet logged", "—", "—"),
    ]
    print(f"    {'Track':<35} {'Streams':>8}  {'Saves':>6}")
    for name, streams, saves in tracks:
        print(f"    {name:<35} {streams:>8}  {saves:>6}")

    print()


def cmd_milestone():
    ensure_table()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT monthly_listeners FROM spotify_stats ORDER BY date DESC LIMIT 1"
        ).fetchone()

    current = row["monthly_listeners"] if row else 0

    print(f"\n{'='*55}")
    print(f"  RJM MILESTONE TRACKER — Goal: 1,000,000")
    print(f"{'='*55}\n")

    for threshold, label in MILESTONES:
        if current >= threshold:
            bar = _progress_bar(100, 30)
            marker = "REACHED"
            print(f"  {label:>6}  {bar}  {marker}")
        else:
            # This is our next milestone
            # Find previous milestone
            prev_threshold = 0
            for i, (t, _) in enumerate(MILESTONES):
                if t == threshold:
                    if i > 0:
                        prev_threshold = MILESTONES[i-1][0]
                    break

            span = threshold - prev_threshold
            gained = current - prev_threshold
            pct = max(0, min(100, int(gained / span * 100))) if span > 0 else 0
            bar = _progress_bar(pct, 30)
            gap = threshold - current
            print(f"  {label:>6}  {bar}  {pct:>3}%  ({gap:,} to go)  <-- NEXT")

            # Show remaining milestones as empty
            for remaining_threshold, remaining_label in MILESTONES:
                if remaining_threshold > threshold:
                    empty_bar = _progress_bar(0, 30)
                    print(f"  {remaining_label:>6}  {empty_bar}    0%")
            break

    overall_pct = current / GOAL * 100
    overall_bar = _progress_bar(int(overall_pct), 40)
    print(f"\n  OVERALL PROGRESS TO 1M:")
    print(f"  {overall_bar}  {overall_pct:.2f}%")
    print(f"  {current:,} / {GOAL:,} listeners\n")


def cmd_history():
    ensure_table()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, monthly_listeners, followers FROM spotify_stats ORDER BY date DESC LIMIT 30"
        ).fetchall()

    if not rows:
        print("\n  No history yet. Run: python3 spotify_tracker.py log <listeners>")
        return

    rows = list(reversed([dict(r) for r in rows]))

    max_val = max(r["monthly_listeners"] for r in rows) or 1
    bar_max = 40

    print(f"\n{'='*65}")
    print(f"  RJM SPOTIFY HISTORY — Last {len(rows)} readings")
    print(f"{'='*65}\n")
    print(f"  {'Date':<12}  {'Listeners':>10}  {'Bar'}")
    print(f"  {'-'*12}  {'-'*10}  {'-'*bar_max}")

    prev_val = None
    for r in rows:
        val = r["monthly_listeners"]
        bar_len = int(val / max_val * bar_max)
        bar = "█" * bar_len + "░" * (bar_max - bar_len)

        if prev_val is not None:
            delta = val - prev_val
            sign = "+" if delta >= 0 else ""
            delta_str = f"  {sign}{delta:,}"
        else:
            delta_str = ""

        print(f"  {r['date']:<12}  {val:>10,}  {bar}{delta_str}")
        prev_val = val

    print()

    # Summary stats
    if len(rows) >= 2:
        first = rows[0]["monthly_listeners"]
        last = rows[-1]["monthly_listeners"]
        total_delta = last - first
        days_span = (
            datetime.strptime(rows[-1]["date"], "%Y-%m-%d") -
            datetime.strptime(rows[0]["date"], "%Y-%m-%d")
        ).days or 1
        weekly_avg = (total_delta / days_span) * 7
        sign = "+" if total_delta >= 0 else ""
        print(f"  Period: {rows[0]['date']} → {rows[-1]['date']}")
        print(f"  Total change: {sign}{total_delta:,}")
        print(f"  Avg weekly growth: {sign}{weekly_avg:,.0f} listeners/week\n")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _progress_bar(pct: int, width: int = 20) -> str:
    """Render [████████░░░░░░░░░░░] style progress bar."""
    filled = int(width * pct / 100)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        return

    cmd = args[0].lower()

    if cmd == "log":
        if len(args) < 2:
            print("Usage: python3 spotify_tracker.py log <monthly_listeners> [followers]")
            sys.exit(1)
        try:
            listeners = int(args[1].replace(",", "").replace(".", ""))
        except ValueError:
            print(f"Invalid listener count: {args[1]}")
            sys.exit(1)
        followers = 0
        if len(args) >= 3:
            try:
                followers = int(args[2].replace(",", "").replace(".", ""))
            except ValueError:
                pass
        notes = args[3] if len(args) >= 4 else ""
        cmd_log(listeners, followers, notes)

    elif cmd == "status":
        cmd_status()

    elif cmd == "milestone":
        cmd_milestone()

    elif cmd == "history":
        cmd_history()

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
