#!/usr/bin/env python3
"""
RJM Command Centre — Unified Entry Point

Single dispatcher for the entire agent fleet. Run from the project root.

Usage:
  python3 rjm.py                          # Show this help
  python3 rjm.py status                   # Full system health check
  python3 rjm.py briefing                 # Master agent daily briefing
  python3 rjm.py outreach [cmd]           # Outreach agent (run, status, verify, add, ...)
  python3 rjm.py master [cmd]             # Master agent (dashboard, gaps, weekly, run, ...)
  python3 rjm.py contacts [cmd]           # Contact manager (status, queue, sync, add, ...)
  python3 rjm.py content [--dry-run]      # Holy Rave daily content run (legacy, 3 clips → Buffer)
  python3 rjm.py content viral            # Viral pipeline: trend→visual→assemble→distribute
  python3 rjm.py content viral --dry-run  # Viral pipeline dry-run (no posting)
  python3 rjm.py content trend-scan       # Run trend scanner (06:00 CET)
  python3 rjm.py content learning         # Run learning loop (18:00 CET)
  python3 rjm.py content retry            # Retry all failed posts in queue
  python3 rjm.py playlist [cmd]           # Playlist DB (status, add, pending_contact, list)
  python3 rjm.py spotify [cmd]            # Spotify growth tracker (status, log <n>, history)
  python3 rjm.py youtube discover         # Find YouTube promo channels → contacts DB
  python3 rjm.py youtube review           # Interactive: click-through to find emails
  python3 rjm.py youtube status           # YouTube-type pipeline counts
  python3 rjm.py youtube budget           # Today's YouTube API unit usage vs cap
  python3 rjm.py run <agent>              # Trigger a sub-agent directly
  python3 rjm.py fleet                    # Live fleet health — all agents + recent events
  python3 rjm.py release list             # Pending track releases
  python3 rjm.py release add Jericho 2026-05-01  # Schedule a release
  python3 rjm.py release check            # Fire campaigns for releases due this week
  python3 rjm.py signals                  # Full hive-mind signal dashboard
  python3 rjm.py sync                     # Sync contacts.csv → outreach.db
  python3 rjm.py swarm init               # Initialise RuFlo agent swarm
  python3 rjm.py swarm status             # Show swarm agent status
  python3 rjm.py memory list              # List shared memory keys
  python3 rjm.py memory get <key>         # Read a memory key
  python3 rjm.py schedule [install|uninstall|status]  # Manage launchd fleet schedules

Examples:
  python3 rjm.py status                   # Is everything running?
  python3 rjm.py briefing                 # What should I focus on today?
  python3 rjm.py outreach run             # Fire the outreach agent now
  python3 rjm.py outreach status          # Outreach pipeline snapshot
  python3 rjm.py master health            # Quick health check
  python3 rjm.py master dashboard         # Full JSON stats
  python3 rjm.py master gaps             # Genre/type gaps
  python3 rjm.py master weekly            # Weekly report
  python3 rjm.py master spotify           # Spotify listener stats + trend
  python3 rjm.py master log_listeners 333 # Log current Spotify monthly listeners
  python3 rjm.py master auto_weights      # Auto-adjust CONTACT_TYPE_WEIGHTS from Spotify velocity
  python3 rjm.py auto-weights             # Shortcut for master auto_weights
  python3 rjm.py contacts status          # Contact DB overview
  python3 rjm.py contacts sync            # Import contacts.csv → SQLite
  python3 rjm.py contacts search tribal   # Search contacts
  python3 rjm.py content                  # Render 3 clips + queue to Buffer
  python3 rjm.py content --dry-run        # Render only, skip Buffer
  python3 rjm.py playlist status          # Playlist discovery progress
  python3 rjm.py spotify status           # Listener count + milestone
  python3 rjm.py spotify log 333          # Record today's listener count
  python3 rjm.py spotify history          # ASCII chart of last 30 readings
  python3 rjm.py run outreach             # Same as 'outreach run'
  python3 rjm.py run discover             # Trigger discovery agent
  python3 rjm.py run research             # Trigger research agent
  python3 rjm.py schedule install         # Load all launchd fleet schedules
  python3 rjm.py schedule status          # Show schedule state
"""

from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# Load .env from project root
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ─── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT    = Path(__file__).parent
OUTREACH_DIR     = PROJECT_ROOT / "outreach_agent"
AGENT_PY         = OUTREACH_DIR / "agent.py"
MASTER_PY        = OUTREACH_DIR / "master_agent.py"
YT_DISCOVER_PY   = OUTREACH_DIR / "youtube_discover.py"
YT_REVIEW_PY     = OUTREACH_DIR / "youtube_manual_review.py"
YT_REVIEW_AUTO_PY = OUTREACH_DIR / "youtube_review_auto.py"
CONTACT_MGR_PY  = PROJECT_ROOT / "contact_manager.py"
POST_TODAY_PY   = OUTREACH_DIR / "post_today.py"
PLAYLIST_RUN_PY = OUTREACH_DIR / "playlist_run.py"
SPOTIFY_PY      = OUTREACH_DIR / "spotify_tracker.py"
VENV_PYTHON     = OUTREACH_DIR / "venv" / "bin" / "python3"

# Use venv python for outreach_agent scripts if available, else system python
_OUTREACH_PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
_BASE_PYTHON     = sys.executable


def _run(cmd: list[str], cwd: str | None = None) -> int:
    """Run a command, streaming output live. Returns exit code."""
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode


def cmd_outreach(args: list[str]):
    """Delegate to outreach_agent/agent.py"""
    if not AGENT_PY.exists():
        print(f"✗ {AGENT_PY} not found")
        sys.exit(1)
    sys.exit(_run([_OUTREACH_PYTHON, str(AGENT_PY)] + args, cwd=str(OUTREACH_DIR)))


def cmd_master(args: list[str]):
    """Delegate to outreach_agent/master_agent.py"""
    if not MASTER_PY.exists():
        print(f"✗ {MASTER_PY} not found")
        sys.exit(1)
    sys.exit(_run([_OUTREACH_PYTHON, str(MASTER_PY)] + args, cwd=str(OUTREACH_DIR)))


def cmd_contacts(args: list[str]):
    """Delegate to contact_manager.py"""
    if not CONTACT_MGR_PY.exists():
        print(f"✗ {CONTACT_MGR_PY} not found")
        sys.exit(1)
    sys.exit(_run([_BASE_PYTHON, str(CONTACT_MGR_PY)] + args, cwd=str(PROJECT_ROOT)))


def cmd_run(agent_name: str, extra_args: list[str]):
    """Trigger a named agent via master_agent.py run <agent>"""
    sys.exit(_run(
        [_OUTREACH_PYTHON, str(MASTER_PY), "run", agent_name] + extra_args,
        cwd=str(OUTREACH_DIR)
    ))


def cmd_sync():
    """Shorthand for: python3 rjm.py contacts sync"""
    sys.exit(_run([_BASE_PYTHON, str(CONTACT_MGR_PY), "sync"], cwd=str(PROJECT_ROOT)))


def cmd_briefing():
    """Shorthand for: python3 rjm.py master briefing"""
    sys.exit(_run([_OUTREACH_PYTHON, str(MASTER_PY), "briefing"], cwd=str(OUTREACH_DIR)))


def cmd_youtube(args: list[str]):
    """YouTube outreach branch — discover channels, show pipeline status, show API budget."""
    if not args:
        print("Usage:")
        print("  python3 rjm.py youtube discover [--dry-run] [--per-query N]")
        print("  python3 rjm.py youtube review [--limit 50]  # manual email click-through")
        print("  python3 rjm.py youtube status     # pipeline counts by status")
        print("  python3 rjm.py youtube budget     # today's YouTube API unit usage vs cap")
        sys.exit(1)

    action = args[0].lower()
    rest = args[1:]

    if action == "discover":
        if not YT_DISCOVER_PY.exists():
            print(f"✗ {YT_DISCOVER_PY} not found")
            sys.exit(1)
        sys.exit(_run([_OUTREACH_PYTHON, str(YT_DISCOVER_PY)] + rest, cwd=str(OUTREACH_DIR)))

    elif action == "review":
        # Default to the Chrome-integrated auto version; fall back to the basic
        # manual-paste tool if the auto script is missing for some reason.
        target = YT_REVIEW_AUTO_PY if YT_REVIEW_AUTO_PY.exists() else YT_REVIEW_PY
        if not target.exists():
            print(f"✗ review tool not found")
            sys.exit(1)
        sys.exit(_run([_OUTREACH_PYTHON, str(target)] + rest, cwd=str(OUTREACH_DIR)))

    elif action in ("review-manual", "review_manual"):
        # Force the older manual-paste version (no Chrome automation)
        if not YT_REVIEW_PY.exists():
            print(f"✗ {YT_REVIEW_PY} not found")
            sys.exit(1)
        sys.exit(_run([_OUTREACH_PYTHON, str(YT_REVIEW_PY)] + rest, cwd=str(OUTREACH_DIR)))

    elif action == "status":
        # Pipeline counts by status for type='youtube'
        code = (
            "import db, sqlite3;"
            "db.init_db();"
            "c = sqlite3.connect(str(db.DB_PATH));"
            "c.row_factory = sqlite3.Row;"
            "rows = c.execute(\"SELECT status, COUNT(*) AS n FROM contacts WHERE type='youtube' GROUP BY status ORDER BY n DESC\").fetchall();"
            "total = c.execute(\"SELECT COUNT(*) FROM contacts WHERE type='youtube'\").fetchone()[0];"
            "print('\\n=== YouTube Pipeline ===');"
            "[print(f\"  {r['status']:<18} {r['n']}\") for r in rows];"
            "print(f\"  {'TOTAL':<18} {total}\");"
            "w = c.execute(\"SELECT COUNT(*) FROM contacts WHERE type='youtube' AND youtube_channel_id IS NOT NULL AND (email IS NULL OR email LIKE 'no-email-%')\").fetchone()[0];"
            "print(f\"\\n  tracked-without-email: {w}  (phase-2 manual enrichment)\")"
        )
        sys.exit(_run([_OUTREACH_PYTHON, "-c", code], cwd=str(OUTREACH_DIR)))

    elif action == "budget":
        code = (
            "import db;"
            "from config import YOUTUBE_API_DAILY_UNITS_CAP;"
            "used = db.get_api_units_today('youtube');"
            "remaining = YOUTUBE_API_DAILY_UNITS_CAP - used;"
            "pct = (used / YOUTUBE_API_DAILY_UNITS_CAP) * 100;"
            "print('\\n=== YouTube API Budget (today) ===');"
            "print(f\"  Used      {used} units\");"
            "print(f\"  Cap       {YOUTUBE_API_DAILY_UNITS_CAP} units\");"
            "print(f\"  Remaining {remaining} units ({100-pct:.0f}% free)\")"
        )
        sys.exit(_run([_OUTREACH_PYTHON, "-c", code], cwd=str(OUTREACH_DIR)))

    else:
        print(f"Unknown youtube action: {action!r}")
        print("Valid: discover, status, budget")
        sys.exit(1)


def cmd_content(args: list[str]):
    """Run the Holy Rave daily content engine."""
    subcommand = args[0].lower() if args else ""

    if subcommand == "viral":
        # New 5-module viral pipeline: trend→visual→assemble→distribute→learn
        dry_run = "--dry-run" in args
        sys.path.insert(0, str(PROJECT_ROOT))
        import logging
        logging.basicConfig(level=logging.INFO)
        import json
        from content_engine.pipeline import run_full_day
        result = run_full_day(dry_run=dry_run)
        print(json.dumps(result, indent=2))
        sys.exit(0)

    elif subcommand == "trend-scan":
        # Run trend scanner standalone (scheduled at 06:00 CET)
        sys.path.insert(0, str(PROJECT_ROOT))
        import logging, json
        logging.basicConfig(level=logging.INFO)
        from content_engine.trend_scanner import run as trend_run
        brief = trend_run()
        print(json.dumps(brief.__dict__, indent=2))
        sys.exit(0)

    elif subcommand == "learning":
        # Run learning loop standalone (scheduled at 18:00 CET)
        sys.path.insert(0, str(PROJECT_ROOT))
        import logging, json
        logging.basicConfig(level=logging.INFO)
        from content_engine.learning_loop import run as learning_run
        weights = learning_run()
        print(json.dumps(weights.__dict__, indent=2))
        sys.exit(0)

    else:
        # Legacy pipeline (post_today.py)
        if not POST_TODAY_PY.exists():
            print(f"✗ {POST_TODAY_PY} not found")
            sys.exit(1)
        sys.exit(_run([_OUTREACH_PYTHON, str(POST_TODAY_PY)] + args, cwd=str(PROJECT_ROOT)))


def cmd_playlist(args: list[str]):
    """Delegate to outreach_agent/playlist_run.py"""
    if not PLAYLIST_RUN_PY.exists():
        print(f"✗ {PLAYLIST_RUN_PY} not found")
        sys.exit(1)
    sys.exit(_run([_OUTREACH_PYTHON, str(PLAYLIST_RUN_PY)] + args, cwd=str(OUTREACH_DIR)))


def cmd_spotify(args: list[str]):
    """Delegate to outreach_agent/spotify_tracker.py"""
    if not SPOTIFY_PY.exists():
        print(f"✗ {SPOTIFY_PY} not found")
        sys.exit(1)
    sys.exit(_run([_OUTREACH_PYTHON, str(SPOTIFY_PY)] + args, cwd=str(OUTREACH_DIR)))


def cmd_skills():
    """Print the installed skill trigger reference."""
    print("""
╔══════════════════════════════════════════════════════════════╗
║           RJM COMMAND CENTRE — INSTALLED SKILLS              ║
╚══════════════════════════════════════════════════════════════╝

SUPERPOWERS (14 lifecycle workflows) — invoke before key actions:
  /brainstorming              Before any new feature or growth strategy
  /writing-plans              Before building anything — spec it first
  /executing-plans            Run a plan in an isolated session
  /systematic-debugging       Before proposing any fix to outreach_agent/
  /test-driven-development    Before writing implementation code
  /verification-before-completion  Before claiming work done
  /requesting-code-review     Before merging changes
  /using-git-worktrees        Feature isolation (already in use)

FRONTEND-DESIGN               /frontend-design
  Use for: index.html, selah.html, Holy Rave visuals, social UI
  Style: Dark, Holy, Futuristic — Anyma / Rüfüs Du Sol aesthetics

CODE-REVIEW                   /code-review <PR-number>
  Mandatory after: outreach_agent/ changes, rjm.py, agent behaviour
  Runs 5 parallel agents, filters <80% confidence issues
  Example: /code-review 42

SECURITY-GUIDANCE             AUTO — fires on every Edit/Write
  Proactively catches dangerous patterns before they land in code
  Especially active on outreach_agent/ (OAuth, subprocess, DNS)

GSTACK                        /gstack
  QA test Holy Rave website — screenshots, responsive layout, forms
  Requires bun: curl -fsSL https://bun.sh/install | bash
  Then: ~/.claude/skills/gstack/setup
""")


def _fleet_status():
    """Print fleet-wide status: Spotify, content, playlist, strategies."""
    import json as _json

    # ── Spotify listeners ──────────────────────────────────────────────────────
    print("\n[ Spotify Growth ]\n")
    listeners_json = PROJECT_ROOT / "data" / "listeners.json"
    if listeners_json.exists():
        try:
            d = _json.loads(listeners_json.read_text())
            n = d.get("count", 0)
            updated = d.get("updatedAt", "")[:10]
            pct = round(n / 1_000_000 * 100, 3)
            print(f"  Monthly listeners : {n:,}  ({pct}% of 1M goal)  [as of {updated}]")
            remaining = 1_000_000 - n
            print(f"  Remaining to goal : {remaining:,}")
        except Exception:
            print("  Could not read data/listeners.json")
    else:
        print("  No listener data yet — run: python3 rjm.py spotify log <number>")

    # ── Content last run ───────────────────────────────────────────────────────
    print("\n[ Content Engine ]\n")
    content_out = PROJECT_ROOT / "content" / "output"
    if content_out.exists():
        runs = sorted([r for r in content_out.iterdir() if r.is_dir()], reverse=True)
        if runs:
            print(f"  Last run  : {runs[0].name}")
            print(f"  Total runs: {len(runs)}")
        else:
            print("  No content runs yet — run: python3 rjm.py content")
    else:
        print("  content/output/ not found — content engine not yet initialized")

    # ── Playlist progress ──────────────────────────────────────────────────────
    print("\n[ Playlist Pipeline ]\n")
    _run([_OUTREACH_PYTHON, str(PLAYLIST_RUN_PY), "status"], cwd=str(OUTREACH_DIR))

    # ── Strategy portfolio ─────────────────────────────────────────────────────
    print("\n[ Strategy Portfolio ]\n")
    registry_path = OUTREACH_DIR / "strategy_registry.json"
    if registry_path.exists():
        try:
            reg = _json.loads(registry_path.read_text())
            strategies = reg.get("strategies", [])
            active      = [s for s in strategies if s.get("status") == "active"]
            building    = [s for s in strategies if s.get("status") == "building"]
            not_started = [s for s in strategies if s.get("status") == "not_started"]
            est = sum(s.get("estimated_listeners_per_month", 0) for s in active)
            print(f"  Active     : {len(active)}/{len(strategies)} strategies")
            print(f"  Building   : {len(building)}")
            print(f"  Not started: {len(not_started)}")
            print(f"  Est. gain from active: +{est:,} listeners/month")
            if not_started:
                top = sorted(not_started, key=lambda s: s.get("priority", 0), reverse=True)[0]
                print(f"  Next to build: {top['name']} (priority {top['priority']}/10)")
        except Exception:
            print("  Could not read strategy_registry.json")
    else:
        print("  strategy_registry.json not found")


def cmd_content_retry():
    """Retry all posts in data/failed_posts.json."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent / "outreach_agent"))
    from post_queue import load_failed_posts, clear_failed_post, queue_depth
    from buffer_poster import upload_video_and_queue

    posts = load_failed_posts()
    if not posts:
        print("✓ No failed posts in queue.")
        return

    print(f"Retrying {len(posts)} failed post(s)…\n")
    for i in range(len(posts) - 1, -1, -1):
        post = posts[i]
        clip_name = Path(post["clip_path"]).name
        print(f"  [{i+1}/{len(posts)}] {clip_name} — originally failed: {post['error'][:60]}")
        try:
            results = upload_video_and_queue(
                clip_path         = post["clip_path"],
                tiktok_caption    = post["tiktok_caption"],
                instagram_caption = post["instagram_caption"],
                youtube_title     = post["youtube_title"],
                youtube_desc      = post["youtube_desc"],
                scheduled_at      = None,
            )
            failed = [p for p, r in results.items() if not r["success"]]
            if not failed:
                clear_failed_post(i)
                print(f"    ✓ Retried successfully — removed from queue")
            else:
                print(f"    ⚠ Still failing on: {', '.join(failed)}")
        except Exception as exc:
            print(f"    ✗ Still failing: {exc}")

    remaining = queue_depth()
    print(f"\nDone. {remaining} post(s) still in queue.")


def cmd_swarm(args: list[str]):
    """Manage the RuFlo agent swarm."""
    import shutil
    ruflo = shutil.which("ruflo") or "npx ruflo@latest"
    swarm_config = str(PROJECT_ROOT / "ruflo" / "config" / "rjm-swarm.json")
    agents_dir = str(PROJECT_ROOT / "ruflo" / ".agents")

    sub = args[0] if args else "status"

    if sub == "init":
        print("Initialising RJM swarm (hierarchical, raft consensus)...")
        result = subprocess.run(
            f"{ruflo} swarm init --config {swarm_config} --topology hierarchical --agents-dir {agents_dir}",
            shell=True, cwd=str(PROJECT_ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    elif sub == "status":
        print("RJM Swarm — agent status:")
        result = subprocess.run(
            f"{ruflo} swarm status",
            shell=True, cwd=str(PROJECT_ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    elif sub == "stop":
        result = subprocess.run(
            f"{ruflo} swarm stop",
            shell=True, cwd=str(PROJECT_ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    else:
        print(f"Unknown swarm sub-command: {sub}")
        print("Usage: rjm.py swarm [init|status|stop]")
        sys.exit(1)


def cmd_memory(args: list[str]):
    """Read/write shared swarm memory keys."""
    import shutil
    ruflo = shutil.which("ruflo") or "npx ruflo@latest"

    if len(args) < 1:
        print("Usage: rjm.py memory get <key> | rjm.py memory set <key> <value>")
        sys.exit(1)

    sub = args[0]

    if sub == "get" and len(args) >= 2:
        key = args[1]
        result = subprocess.run(
            f"{ruflo} memory get --key {key}",
            shell=True, cwd=str(PROJECT_ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    elif sub == "set" and len(args) >= 3:
        key, value = args[1], args[2]
        result = subprocess.run(
            f"{ruflo} memory set --key {key} --value \"{value}\"",
            shell=True, cwd=str(PROJECT_ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    elif sub == "list":
        result = subprocess.run(
            f"{ruflo} memory list",
            shell=True, cwd=str(PROJECT_ROOT / "ruflo")
        )
        sys.exit(result.returncode)

    else:
        print(f"Unknown memory sub-command or missing args: {args}")
        print("Usage: rjm.py memory get <key> | set <key> <value> | list")
        sys.exit(1)


def cmd_token(args: list[str]):
    """Refresh platform OAuth tokens. Usage: python3 rjm.py token refresh [instagram|facebook|youtube|all]"""
    from content_engine import distributor

    target = args[0].lower() if args else "all"

    if target in ("instagram", "all"):
        token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        if not token:
            print("✗ INSTAGRAM_ACCESS_TOKEN not set in .env")
        else:
            new = distributor.refresh_instagram_token(token)
            if new and new != token:
                print("✓ Instagram token refreshed and saved to .env")
            elif new == token:
                print("⚠ Instagram token unchanged (may already be fresh, or expired — re-auth required if posting fails)")
            else:
                print("✗ Instagram token refresh failed")

    if target in ("facebook", "all"):
        user_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        page_id    = os.environ.get("FACEBOOK_PAGE_ID", "")
        if not user_token:
            print("✗ INSTAGRAM_ACCESS_TOKEN not set — needed to fetch Facebook page token")
        else:
            page_token = distributor.get_facebook_page_token(user_token, page_id)
            if page_token:
                print("✓ Facebook page token obtained and saved to .env")
            else:
                print("✗ Facebook page token fetch failed — check FACEBOOK_PAGE_ID in .env and that your token has pages_manage_posts permission")

    if target in ("youtube", "all"):
        token = distributor._refresh_youtube_token()
        if token:
            print("✓ YouTube token refreshed")
        else:
            print("✗ YouTube token refresh failed (check YOUTUBE_REFRESH_TOKEN in .env)")


def cmd_schedule(args: list[str]):
    """Manage macOS launchd fleet schedules via scripts/setup_schedule.sh"""
    setup_sh = PROJECT_ROOT / "scripts" / "setup_schedule.sh"
    if not setup_sh.exists():
        print(f"✗ {setup_sh} not found — check that scripts/ is present")
        sys.exit(1)
    sub = args[0] if args else "status"
    sys.exit(_run(["/bin/bash", str(setup_sh), sub], cwd=str(PROJECT_ROOT)))


def cmd_status():
    """
    Full system status — runs master health + outreach status in sequence.
    """
    print("\n" + "═" * 60)
    print("  RJM COMMAND CENTRE — SYSTEM STATUS")
    print("═" * 60)

    # ── Rate-limit snapshot (inline — no subprocess) ─────────────────────────
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent / "outreach_agent"))
        import scheduler as _sched
        import db as _db
        from config import MAX_EMAILS_PER_DAY, MAX_CONTENT_POSTS_PER_DAY, MAX_CONTACTS_FOUND_PER_DAY
        _db.init_db()
        window = _sched.SendWindow()
        icon   = "✅" if window.can_send else "⏸ "
        print(f"\n[ Rate Limits ]\n")
        print(f"  {icon} {window.status()}")
        print(f"  📧  Emails today:    {_db.today_send_count()} / {MAX_EMAILS_PER_DAY}")
        print(f"  🎬  Content posts:   {_db.today_content_count()} / {MAX_CONTENT_POSTS_PER_DAY}")
        print(f"  🔍  Contacts found:  {_db.today_contacts_found()} / {MAX_CONTACTS_FOUND_PER_DAY}")
        # IG DM count (separate table, same DB)
        try:
            from datetime import date as _date
            import sqlite3 as _sqlite3
            from config import DB_PATH as _DB_PATH
            _ig_conn = _sqlite3.connect(str(_DB_PATH))
            _ig_row = _ig_conn.execute(
                "SELECT COUNT(*) FROM instagram_outreach WHERE date_sent=? AND status='sent'",
                (str(_date.today()),)
            ).fetchone()
            _ig_conn.close()
            ig_today = _ig_row[0] if _ig_row else 0
            print(f"  📱  IG DMs today:    {ig_today} / 20")
        except Exception:
            pass
    except Exception as _e:
        print(f"\n[ Rate Limits ]\n  (unavailable: {_e})")

    # 1. Master health check
    print("\n[ Master Agent Health ]\n")
    _run([_OUTREACH_PYTHON, str(MASTER_PY), "health"], cwd=str(OUTREACH_DIR))

    # 2. Outreach agent status
    if AGENT_PY.exists():
        print("\n[ Outreach Agent Status ]\n")
        _run([_OUTREACH_PYTHON, str(AGENT_PY), "status"], cwd=str(OUTREACH_DIR))

    # 3. Fleet-wide status (Spotify, content, playlist, strategies)
    _fleet_status()

    # 5. Failed post queue
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent / "outreach_agent"))
    from post_queue import queue_depth
    depth = queue_depth()
    queue_status = f"⚠  {depth} post(s) waiting to retry — run: python3 rjm.py content retry" if depth > 0 else "✓  empty"
    print(f"\n[ Failed Post Queue ]\n")
    print(f"  Failed post queue:  {queue_status}")

    # 6. Quality gate summary (last 24h)
    from datetime import timedelta
    try:
        import json as _json
        from quality_gate import LOG_PATH as QUALITY_LOG_PATH
        q_log = _json.loads(QUALITY_LOG_PATH.read_text()) if QUALITY_LOG_PATH.exists() else []
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        recent = [e for e in q_log if e.get("checked_at", "") >= cutoff]
        passed_count = sum(1 for e in recent if e["passed"])
        failed_count = sum(1 for e in recent if not e["passed"])
        fail_details = [f"  • {e['clip']}: {e['reason']}" for e in recent if not e["passed"]]
        quality_str = f"✓  {passed_count} passed, {failed_count} failed (last 24h)" if recent else "no clips checked yet"
        print(f"\n[ Quality Gate ]")
        print(f"  {quality_str}")
        for detail in fail_details[:3]:
            print(detail)
    except Exception:
        print("\n[ Quality Gate ]\n  (log unavailable)")


def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        return

    cmd = args[0].lower()
    rest = args[1:]

    if cmd == "status":
        cmd_status()
    elif cmd == "briefing":
        cmd_briefing()
    elif cmd == "outreach":
        cmd_outreach(rest)
    elif cmd == "master":
        cmd_master(rest)
    elif cmd == "fleet":
        sys.exit(_run([_OUTREACH_PYTHON, str(MASTER_PY), "fleet"], cwd=str(OUTREACH_DIR)))
    elif cmd == "release":
        sys.exit(_run([_OUTREACH_PYTHON, str(MASTER_PY), "release"] + sys.argv[2:], cwd=str(OUTREACH_DIR)))
    elif cmd == "signals":
        sys.exit(_run([_OUTREACH_PYTHON, str(MASTER_PY), "signals"], cwd=str(OUTREACH_DIR)))
    elif cmd in ("auto-weights", "auto_weights"):
        cmd_master(["auto_weights"])
    elif cmd == "contacts":
        cmd_contacts(rest)
    elif cmd in ("content", "post"):
        cmd_content(rest)
    elif cmd == "playlist":
        cmd_playlist(rest)
    elif cmd == "spotify":
        cmd_spotify(rest)
    elif cmd == "youtube":
        cmd_youtube(rest)
    elif cmd == "run":
        agent = rest[0] if rest else ""
        if not agent:
            print("Usage: python3 rjm.py run <agent>")
            print("Agents: outreach, discover, research, verify")
        else:
            cmd_run(agent, rest[1:])
    elif cmd == "content":
        action = rest[0].lower() if rest else ""
        if action == "retry":
            cmd_content_retry()
        else:
            print("Usage: python3 rjm.py content retry")
            sys.exit(1)
    elif cmd == "swarm":
        cmd_swarm(rest)
    elif cmd == "memory":
        cmd_memory(rest)
    elif cmd == "sync":
        cmd_sync()
    elif cmd == "token":
        cmd_token(rest)
    elif cmd == "schedule":
        cmd_schedule(rest)
    elif cmd in ("skills", "skill"):
        cmd_skills()
    elif cmd in ("help", "--help", "-h"):
        print(__doc__)
    else:
        # Try to delegate unknown commands to master_agent as a convenience
        print(f"Unknown command: {cmd!r}")
        print("Run 'python3 rjm.py' for help.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
