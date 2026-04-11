#!/usr/bin/env python3
"""
RJM Master Agent — Command Centre Brain

Sits above rjm-discover, rjm-research, rjm-outreach-agent, rjm-playlist-discover.
Provides strategic intelligence: pipeline health, gap analysis, response escalation,
weight adjustment, daily briefings, and growth strategy portfolio management.

Usage:
  python3 master_agent.py dashboard         # Full JSON stats snapshot
  python3 master_agent.py briefing          # Human-readable strategic brief
  python3 master_agent.py responses         # Contacts who responded — needs action
  python3 master_agent.py gaps              # Niche/genre gaps in the pipeline
  python3 master_agent.py adjust curator=70 podcast=30   # Rewrite type weights in config.py
  python3 master_agent.py weekly            # Full weekly performance report
  python3 master_agent.py health            # Quick health check — is everything running?
  python3 master_agent.py podcast_targets   # Upcoming podcast outreach priorities

  --- Growth Strategy Commands ---
  python3 master_agent.py spotify           # Spotify listener stats + growth trend
  python3 master_agent.py strategy          # Full strategy portfolio table (all 16 strategies)
  python3 master_agent.py next_build        # Recommended next strategy to build + action steps
  python3 master_agent.py log_listeners <n> # Log current Spotify monthly listener count
"""

import json
import sys
import os
import re
import subprocess
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

import db
import scheduler
from config import (
    MAX_EMAILS_PER_DAY, CONTACT_TYPE_WEIGHTS, FOLLOWUP_DAYS, FOLLOWUP2_DAYS,
    DB_PATH, BASE_DIR, MAX_CONTENT_POSTS_PER_DAY
)
try:
    from learning import get_learning_context_for_template
    _LEARNING_AVAILABLE = True
except ImportError:
    _LEARNING_AVAILABLE = False

STRATEGY_REGISTRY_PATH = Path(__file__).parent / "strategy_registry.json"
SPOTIFY_TRACKER_PATH   = Path(__file__).parent / "spotify_tracker.py"


# ─── Dashboard ────────────────────────────────────────────────────────────────

def cmd_dashboard():
    db.init_db()
    with db.get_conn() as conn:
        # Pipeline counts by status
        status_rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM contacts GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["n"] for r in status_rows}

        # Pipeline counts by type
        type_rows = conn.execute(
            "SELECT type, status, COUNT(*) as n FROM contacts GROUP BY type, status"
        ).fetchall()
        by_type = defaultdict(lambda: defaultdict(int))
        for r in type_rows:
            by_type[r["type"]][r["status"]] += r["n"]

        # Response rate by type
        resp_rows = conn.execute("""
            SELECT type,
                   COUNT(*) as total_sent,
                   SUM(CASE WHEN status='responded' THEN 1 ELSE 0 END) as responded,
                   ROUND(SUM(CASE WHEN status='responded' THEN 1.0 ELSE 0 END) /
                         NULLIF(COUNT(*), 0) * 100, 1) as reply_pct
            FROM contacts
            WHERE status IN ('sent','followup_sent','followup2_sent','responded')
            GROUP BY type
        """).fetchall()
        reply_rates = {r["type"]: {"sent": r["total_sent"], "responded": r["responded"], "pct": r["reply_pct"]} for r in resp_rows}

        # Daily send volume last 14 days
        daily_rows = conn.execute("""
            SELECT date, emails_sent FROM daily_stats
            ORDER BY date DESC LIMIT 14
        """).fetchall()
        daily_volume = [{"date": r["date"], "sent": r["emails_sent"]} for r in daily_rows]

        # Follow-up queue sizes
        fu1_due = len(db.get_followup_candidates(days_since_send=FOLLOWUP_DAYS))
        fu2_due = len(db.get_followup2_candidates(days_since_followup1=FOLLOWUP2_DAYS))

        # Unresearched verified contacts
        unresearched = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE status='verified' AND (research_done IS NULL OR research_done=0)"
        ).fetchone()["n"]

        # Total verified ready to send
        verified_ready = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE status='verified'"
        ).fetchone()["n"]

        # Genre distribution (verified + sent)
        genre_rows = conn.execute("""
            SELECT genre, COUNT(*) as n FROM contacts
            WHERE status IN ('verified','sent','followup_sent','responded')
              AND genre IS NOT NULL AND genre != ''
            GROUP BY genre ORDER BY n DESC LIMIT 20
        """).fetchall()
        genres = [{"genre": r["genre"], "count": r["n"]} for r in genre_rows]

        # Recent responses (last 30 days)
        recent_responses = conn.execute("""
            SELECT email, name, type, genre, notes, response_snippet,
                   date_response_received
            FROM contacts
            WHERE status='responded'
              AND date_response_received >= ?
            ORDER BY date_response_received DESC
            LIMIT 20
        """, ((date.today() - timedelta(days=30)).isoformat(),)).fetchall()

        # Discovery log — last 7 days
        disc_rows = conn.execute("""
            SELECT contact_type, COUNT(*) as searches, SUM(results_found) as found
            FROM discovery_log WHERE searched_at >= ?
            GROUP BY contact_type
        """, ((datetime.now() - timedelta(days=7)).isoformat(),)).fetchall()
        discovery_7d = {r["contact_type"]: {"searches": r["searches"], "found": r["found"]} for r in disc_rows}

        # Podcast-specific stats
        pod_stats = dict(by_type.get("podcast", {}))
        cur_stats = dict(by_type.get("curator", {}))

        # Days since last discovery (gap detection)
        last_disc = conn.execute(
            "SELECT MAX(searched_at) as ts FROM discovery_log"
        ).fetchone()["ts"]

        # Days since last send
        last_send = conn.execute(
            "SELECT MAX(last_send_ts) as ts FROM daily_stats"
        ).fetchone()["ts"]

    out = {
        "snapshot_date": str(date.today()),
        "pipeline": {
            "by_status": by_status,
            "verified_ready_to_send": verified_ready,
            "unresearched_verified": unresearched,
            "followups_due_today": {"first": fu1_due, "second": fu2_due},
        },
        "by_type": {t: dict(v) for t, v in by_type.items()},
        "reply_rates": reply_rates,
        "genre_distribution": genres,
        "daily_volume_14d": daily_volume,
        "discovery_7d": discovery_7d,
        "last_discovery_at": last_disc,
        "last_send_at": last_send,
        "podcast_pipeline": pod_stats,
        "curator_pipeline": cur_stats,
        "current_weights": CONTACT_TYPE_WEIGHTS,
        "recent_responses": [dict(r) for r in recent_responses],
    }
    print(json.dumps(out, indent=2))


# ─── Responses — needs human action ───────────────────────────────────────────

def cmd_responses():
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT email, name, type, genre, notes, response_snippet,
                   date_response_received, website, sent_subject
            FROM contacts WHERE status='responded'
            ORDER BY date_response_received DESC
        """).fetchall()

    if not rows:
        print("No responses yet. Keep sending!")
        return

    print(f"\n=== RESPONSES REQUIRING ACTION ({len(rows)} total) ===\n")
    for r in rows:
        r = dict(r)
        days_ago = ""
        if r.get("date_response_received"):
            try:
                d = datetime.fromisoformat(r["date_response_received"])
                days_ago = f" ({(datetime.now() - d).days}d ago)"
            except Exception:
                pass

        print(f"  [{r['type'].upper()}] {r['name']} <{r['email']}>{days_ago}")
        if r.get("genre"):
            print(f"    Genre: {r['genre']}")
        if r.get("website"):
            print(f"    Website: {r['website']}")
        if r.get("sent_subject"):
            print(f"    Our subject: {r['sent_subject']}")
        if r.get("response_snippet"):
            snippet = r["response_snippet"].replace("\n", " ")[:200]
            print(f"    Their reply: \"{snippet}\"")

        # Action guidance
        ctype = r.get("type", "")
        if ctype == "podcast":
            print(f"    → ACTION: Reply with booking availability + press kit. Suggest 3 dates.")
        elif ctype == "curator":
            print(f"    → ACTION: Reply with track links + streaming stats. Ask which playlist fits best.")
        else:
            print(f"    → ACTION: Reply and continue the conversation.")
        print()


# ─── Gap Analysis ─────────────────────────────────────────────────────────────

def cmd_gaps():
    db.init_db()
    with db.get_conn() as conn:
        # Type coverage
        type_rows = conn.execute("""
            SELECT type, COUNT(*) as n FROM contacts
            WHERE status IN ('new','verified','sent','followup_sent','responded')
            GROUP BY type
        """).fetchall()
        by_type = {r["type"]: r["n"] for r in type_rows}

        # Genre coverage
        genre_rows = conn.execute("""
            SELECT genre, type, COUNT(*) as n FROM contacts
            WHERE status IN ('verified','sent','followup_sent','responded')
              AND genre IS NOT NULL AND genre != ''
            GROUP BY genre, type
            ORDER BY n DESC
        """).fetchall()

        # Unresearched
        unresearched = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE status='verified' AND (research_done IS NULL OR research_done=0)"
        ).fetchone()["n"]

        # Empty pipeline types
        responded = conn.execute(
            "SELECT type, COUNT(*) as n FROM contacts WHERE status='responded' GROUP BY type"
        ).fetchall()
        resp_by_type = {r["type"]: r["n"] for r in responded}

        # Discovery recency by type
        disc_rows = conn.execute("""
            SELECT contact_type, MAX(searched_at) as last_search
            FROM discovery_log GROUP BY contact_type
        """).fetchall()
        last_disc = {r["contact_type"]: r["last_search"] for r in disc_rows}

    print("\n=== PIPELINE GAP ANALYSIS ===\n")

    # Active type coverage
    print("Contact type distribution (active pipeline):")
    active_types = ["curator", "podcast", "youtube", "festival", "sync", "booking_agent", "wellness"]
    for t in active_types:
        weight = CONTACT_TYPE_WEIGHTS.get(t, 0)
        count = by_type.get(t, 0)
        resp = resp_by_type.get(t, 0)
        status = "✅" if count > 0 else "⚠️ "
        paused = " [PAUSED]" if weight == 0 else f" [weight={weight}]"
        print(f"  {status} {t:<16} {count:>4} contacts  {resp} responded{paused}")

    # Podcast gap (specific)
    podcast_count = by_type.get("podcast", 0)
    curator_count = by_type.get("curator", 0)
    if podcast_count < 50:
        print(f"\n⚠️  PODCAST GAP: Only {podcast_count} podcast contacts. Target 50+.")
        print("   → rjm-discover needs more podcast-specific searches")
        print("   → Priority niches: techno culture, rave spirituality, entrepreneur-turned-artist, Ibiza/Tenerife scene")

    if curator_count < 100:
        print(f"\n⚠️  CURATOR GAP: Only {curator_count} curator contacts. Target 200+.")
        print("   → Expand genre searches: tribal, psytrance, organic house, melodic techno")

    # Genre gaps
    print("\nGenre coverage in pipeline:")
    target_genres = [
        "tribal techno", "psytrance", "melodic techno", "organic house",
        "afro house", "progressive house", "christian edm", "deep techno",
        "ambient techno", "world music"
    ]
    genre_names = {r["genre"].lower() for r in genre_rows}
    for g in target_genres:
        covered = any(g in gn or gn in g for gn in genre_names)
        mark = "✅" if covered else "❌"
        print(f"  {mark} {g}")

    # Unresearched warning
    if unresearched > 20:
        print(f"\n⚠️  RESEARCH BACKLOG: {unresearched} verified contacts not yet researched.")
        print("   → rjm-research should clear this before new sends")

    # Discovery freshness
    print("\nDiscovery freshness (last search per type):")
    for t in ["curator", "podcast"]:
        last = last_disc.get(t, "never")
        if last and last != "never":
            try:
                age_h = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 3600
                age_str = f"{age_h:.0f}h ago"
                warn = " ⚠️ STALE" if age_h > 12 else ""
            except Exception:
                age_str = last
                warn = ""
        else:
            age_str = "NEVER"
            warn = " ⚠️ NEVER SEARCHED"
        print(f"  {t:<16}: {age_str}{warn}")


# ─── Strategic Briefing ───────────────────────────────────────────────────────

def cmd_briefing():
    db.init_db()
    summary = db.get_pipeline_summary()
    today_sent = summary.get("_today_sent", 0)
    reply_rate = summary.get("_reply_rate", "—")
    remaining_today = max(0, MAX_EMAILS_PER_DAY - today_sent)

    content_posts  = db.today_content_count()
    contacts_found = db.today_contacts_found()

    # Spotify freshness
    spotify_listeners = None
    spotify_last_date = None
    spotify_days_stale = None
    try:
        with db.get_conn() as _conn:
            _row = _conn.execute(
                "SELECT date, monthly_listeners FROM spotify_stats ORDER BY date DESC LIMIT 1"
            ).fetchone()
        if _row:
            spotify_listeners  = _row["monthly_listeners"]
            spotify_last_date  = _row["date"]
            spotify_days_stale = (date.today() - date.fromisoformat(spotify_last_date)).days
    except Exception:
        pass

    with db.get_conn() as conn:
        verified = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE status='verified'"
        ).fetchone()["n"]
        new_responses = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE status='responded' AND date_response_received >= ?",
            ((date.today() - timedelta(days=3)).isoformat(),)
        ).fetchone()["n"]
        fu1_due = len(db.get_followup_candidates(days_since_send=FOLLOWUP_DAYS))
        fu2_due = len(db.get_followup2_candidates(days_since_followup1=FOLLOWUP2_DAYS))
        podcast_verified = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE type='podcast' AND status='verified'"
        ).fetchone()["n"]
        curator_verified = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE type='curator' AND status='verified'"
        ).fetchone()["n"]
        total_sent_all = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE status IN ('sent','followup_sent','followup2_sent','responded')"
        ).fetchone()["n"]
        total_responded = summary.get("responded", 0)

    print(f"\n=== RJM MASTER AGENT — DAILY BRIEFING ({date.today()}) ===\n")
    print(f"NORTH STAR: 1,000,000 Spotify monthly listeners\n")

    # Spotify progress line
    if spotify_listeners is not None:
        pct  = spotify_listeners / 1_000_000 * 100
        stale_warn = f"  ⚠️  ({spotify_days_stale}d old — run: rjm.py spotify log <n>)" if spotify_days_stale and spotify_days_stale >= 3 else f"  (as of {spotify_last_date})"
        print(f"SPOTIFY: {spotify_listeners:,} monthly listeners  ({pct:.2f}% of goal){stale_warn}\n")
    else:
        print(f"SPOTIFY: ⚠️  No data — run: python3 rjm.py spotify log <n>\n")

    print(f"TODAY'S ACTIVITY:")
    print(f"  Emails sent      : {today_sent} / {MAX_EMAILS_PER_DAY}  ({remaining_today} remaining)")
    print(f"  Content posted   : {content_posts} / {MAX_CONTENT_POSTS_PER_DAY} clips")
    print(f"  Contacts found   : {contacts_found} / {MAX_CONTACTS_FOUND_PER_DAY}")
    print(f"")
    print(f"PIPELINE HEALTH:")
    print(f"  Total contacted  : {total_sent_all}")
    print(f"  Responded        : {total_responded}  (reply rate: {reply_rate})")
    print(f"  Verified & ready : {verified} ({podcast_verified} podcasts, {curator_verified} curators)")
    print(f"  Follow-ups due   : {fu1_due} first  |  {fu2_due} second")

    if new_responses > 0:
        print(f"\n🔥 NEW RESPONSES IN LAST 3 DAYS: {new_responses}")
        print("   → Run: python3 master_agent.py responses")

    print(f"\nTODAY'S PRIORITIES:")
    priority = 1

    if new_responses > 0:
        print(f"  {priority}. Reply to {new_responses} new responses — this is the most important action")
        priority += 1

    if fu2_due > 0:
        print(f"  {priority}. Send {fu2_due} second follow-ups (final touch — don't skip)")
        priority += 1

    if fu1_due > 0:
        print(f"  {priority}. Send {fu1_due} first follow-ups (day 5 bump)")
        priority += 1

    if remaining_today > 0 and verified > 0:
        batch = min(remaining_today, 7)
        print(f"  {priority}. Send {batch} new emails ({verified} contacts ready)")
        priority += 1

    if podcast_verified < 10:
        print(f"  {priority}. ⚠️  URGENT: Only {podcast_verified} podcast contacts ready — run discovery immediately")
        priority += 1

    if curator_verified < 20:
        print(f"  {priority}. Low curator pipeline ({curator_verified}) — rjm-discover needs to run")
        priority += 1

    if spotify_days_stale is not None and spotify_days_stale >= 3:
        print(f"  {priority}. ⚠️  Spotify data is {spotify_days_stale} days old — log today's count:")
        print(f"          python3 rjm.py spotify log <n>")
        priority += 1
    elif spotify_listeners is None:
        print(f"  {priority}. Log Spotify listeners to track progress toward 1M:")
        print(f"          python3 rjm.py spotify log <n>")
        priority += 1

    print(f"\nSUB-AGENT STATUS:")
    print(f"  rjm-discover      : runs 6× daily (curator + podcast discovery)")
    print(f"  rjm-research      : runs 6× daily (personalisation facts)")
    print(f"  rjm-outreach-agent: runs every 30m (sends + follow-ups)")
    print(f"  rjm-playlist-discover: on-demand (Spotify playlist sourcing)")

    print(f"\nWEIGHTS: curator={CONTACT_TYPE_WEIGHTS.get('curator',0)}  podcast={CONTACT_TYPE_WEIGHTS.get('podcast',0)}")

    # Learning insights — what's working right now
    if _LEARNING_AVAILABLE:
        for ctype in ["podcast", "curator"]:
            ctx = get_learning_context_for_template(ctype)
            if ctx:
                print(f"\nLEARNING INSIGHTS ({ctype.upper()}):")
                for line in ctx.splitlines()[:6]:
                    if line.strip():
                        print(f"  {line}")

    print(f"\nRun 'python3 master_agent.py gaps' for gap analysis.")
    print(f"Run 'python3 master_agent.py weekly' for full performance report.")
    print(f"Run 'python3 master_agent.py run <agent>' to trigger an agent.\n")


# ─── Weekly Report ────────────────────────────────────────────────────────────

def cmd_weekly():
    db.init_db()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    with db.get_conn() as conn:
        # Sends this week
        week_sends = conn.execute(
            "SELECT COALESCE(SUM(emails_sent), 0) as n FROM daily_stats WHERE date >= ?", (week_ago,)
        ).fetchone()["n"]

        # New contacts added this week
        new_contacts = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE date_added >= ?", (week_ago,)
        ).fetchone()["n"]

        # Responses this week
        week_responses = conn.execute(
            "SELECT email, name, type, response_snippet FROM contacts WHERE status='responded' AND date_response_received >= ?",
            (week_ago,)
        ).fetchall()

        # Bounces this week
        week_bounces = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE bounce != 'no' AND date_verified >= ?", (week_ago,)
        ).fetchone()["n"]

        # Best performing types
        type_stats = conn.execute("""
            SELECT type,
                   SUM(CASE WHEN status IN ('sent','followup_sent','followup2_sent','responded') THEN 1 ELSE 0 END) as sent,
                   SUM(CASE WHEN status='responded' THEN 1 ELSE 0 END) as responded
            FROM contacts GROUP BY type
        """).fetchall()

        # Template performance
        perf = db.get_template_stats()

        # Recent insights
        insights = db.get_recent_insights(limit=3)

        # Discovery this week
        disc = conn.execute("""
            SELECT contact_type, COUNT(*) as searches, SUM(results_found) as found
            FROM discovery_log WHERE searched_at >= ?
            GROUP BY contact_type
        """, (week_ago,)).fetchall()

    print(f"\n{'='*60}")
    print(f"  RJM MASTER AGENT — WEEKLY REPORT")
    print(f"  Week ending {date.today()}")
    print(f"{'='*60}\n")

    print(f"SENDS THIS WEEK     : {week_sends}")
    print(f"NEW CONTACTS ADDED  : {new_contacts}")
    print(f"RESPONSES RECEIVED  : {len(week_responses)}")
    print(f"BOUNCES             : {week_bounces}")

    if week_responses:
        print(f"\nRESPONSES ({len(week_responses)}):")
        for r in week_responses:
            r = dict(r)
            snippet = (r.get("response_snippet") or "")[:120].replace("\n", " ")
            print(f"  [{r['type']}] {r['name']} <{r['email']}>")
            if snippet:
                print(f"    \"{snippet}\"")

    print(f"\nDISCOVERY ACTIVITY:")
    for r in disc:
        r = dict(r)
        print(f"  {r['contact_type']:<16}: {r['searches']} searches → {r['found']} contacts found")

    print(f"\nCONTACT TYPE PERFORMANCE (all time):")
    for r in type_stats:
        r = dict(r)
        pct = round(r["responded"] / r["sent"] * 100, 1) if r["sent"] else 0
        print(f"  {r['type']:<16}: {r['sent']:>4} sent  {r['responded']:>3} replied  ({pct}%)")

    if insights:
        print(f"\nLEARNING INSIGHTS:")
        for ins in insights:
            ins = dict(ins)
            print(f"  [{ins['insight_type']}] {ins['content'][:200]}")

    # Strategic recommendations
    print(f"\nSTRATEGIC RECOMMENDATIONS:")
    recs = []

    # Find best-performing type
    best_type = None
    best_pct = 0
    for r in type_stats:
        r = dict(r)
        pct = round(r["responded"] / r["sent"] * 100, 1) if r["sent"] else 0
        if pct > best_pct and r["sent"] >= 5:
            best_pct = pct
            best_type = r["type"]

    if best_type and best_pct > 5:
        current_w = CONTACT_TYPE_WEIGHTS.get(best_type, 0)
        recs.append(f"  → {best_type} has {best_pct}% reply rate — consider increasing its weight (currently {current_w})")

    if week_sends < 50:
        recs.append(f"  → Only {week_sends} emails sent this week — check if rjm-outreach-agent is running")

    if len(week_responses) == 0:
        recs.append(f"  → Zero responses this week — review email content and subject lines")

    if new_contacts < 30:
        recs.append(f"  → Only {new_contacts} new contacts added — rjm-discover may need more search queries")

    for r in recs:
        print(r)

    if not recs:
        print("  → All systems healthy. Keep pushing.")

    print()


# ─── Health Check ─────────────────────────────────────────────────────────────

def cmd_health():
    db.init_db()

    print(f"\n=== RJM SYSTEM HEALTH CHECK — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

    with db.get_conn() as conn:
        # Last send
        last_send = conn.execute(
            "SELECT MAX(last_send_ts) as ts FROM daily_stats"
        ).fetchone()["ts"]

        # Last discovery
        last_disc = conn.execute(
            "SELECT contact_type, MAX(searched_at) as ts FROM discovery_log GROUP BY contact_type"
        ).fetchall()

        # Pipeline sizes
        verified = conn.execute("SELECT COUNT(*) as n FROM contacts WHERE status='verified'").fetchone()["n"]
        new_c = conn.execute("SELECT COUNT(*) as n FROM contacts WHERE status='new'").fetchone()["n"]
        today_sent = db.today_send_count()

    issues = []

    # Send recency
    if last_send:
        try:
            hours_since = (datetime.now() - datetime.fromisoformat(last_send)).total_seconds() / 3600
            if hours_since > 48:
                issues.append(f"⚠️  No emails sent in {hours_since:.0f}h — outreach agent may be down")
            else:
                print(f"  ✅ Last send: {hours_since:.1f}h ago")
        except Exception:
            pass
    else:
        issues.append("⚠️  No emails ever sent — outreach agent not yet activated")

    # Discovery recency
    last_disc_map = {r["contact_type"]: r["ts"] for r in last_disc}
    for t in ["curator", "podcast"]:
        ts = last_disc_map.get(t)
        if ts:
            try:
                age_h = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600
                if age_h > 12:
                    issues.append(f"⚠️  {t} discovery stale: last run {age_h:.0f}h ago")
                else:
                    print(f"  ✅ {t} discovery: {age_h:.1f}h ago")
            except Exception:
                pass
        else:
            issues.append(f"⚠️  {t} discovery: NEVER RUN")

    # Pipeline size
    if verified < 10:
        issues.append(f"⚠️  Only {verified} verified contacts — pipeline critically low")
    else:
        print(f"  ✅ Verified pipeline: {verified} contacts ready")

    if new_c > 50:
        issues.append(f"⚠️  {new_c} contacts stuck in 'new' status — bounce verifier may not be running")
    elif new_c > 0:
        print(f"  ℹ️  {new_c} new contacts pending verification")

    print(f"  ℹ️  Today's sends: {today_sent} / {MAX_EMAILS_PER_DAY}")
    content_today = db.today_content_count()
    print(f"  ℹ️  Content posts today: {content_today} / {MAX_CONTENT_POSTS_PER_DAY}")

    # Send window status
    window = scheduler.SendWindow()
    window_icon = "✅" if window.can_send else "⏸ "
    print(f"  {window_icon} Send window: {window.status()}")

    # ── Content engine ─────────────────────────────────────────────────────────
    content_out = BASE_DIR.parent / "content" / "output"
    if content_out.exists():
        runs = sorted([r for r in content_out.iterdir() if r.is_dir()], reverse=True)
        if runs:
            last_run_name = runs[0].name
            try:
                run_dt = datetime.strptime(last_run_name[:13], "%Y-%m-%d_%H%M")
                age_h = (datetime.now() - run_dt).total_seconds() / 3600
                if age_h > 30:
                    issues.append(f"⚠️  Content engine: last run {age_h:.0f}h ago — holy-rave-daily-run may be down")
                else:
                    print(f"  ✅ Content last run: {age_h:.1f}h ago ({last_run_name})")
            except Exception:
                print(f"  ℹ️  Content last run: {last_run_name}")
        else:
            issues.append("⚠️  Content engine: no runs yet — holy-rave-daily-run not yet active")
    else:
        print("  ℹ️  Content engine: content/output/ not found")

    # ── Spotify listeners — DB is source of truth ─────────────────────────────
    try:
        db.init_db()
        with db.get_conn() as _conn:
            row = _conn.execute(
                "SELECT monthly_listeners, date FROM spotify_stats ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row:
            n, updated = row["monthly_listeners"], row["date"]
            pct = round(n / 1_000_000 * 100, 3)
            print(f"  ✅ Spotify listeners: {n:,} ({pct}% of 1M)  [updated {updated}]")
            # Keep listeners.json in sync with DB
            import json as _json
            listeners_json = BASE_DIR.parent / "data" / "listeners.json"
            try:
                listeners_json.parent.mkdir(exist_ok=True)
                listeners_json.write_text(
                    _json.dumps({"count": n, "updatedAt": updated}, indent=2), encoding="utf-8"
                )
            except Exception:
                pass
        else:
            issues.append("⚠️  Spotify: no listener data — run: python3 master_agent.py log_listeners <n>")
    except Exception:
        issues.append("⚠️  Spotify: could not read listener count from DB")

    # ── Playlist pipeline ──────────────────────────────────────────────────────
    try:
        import playlist_db as _pdb
        _pdb.init_playlist_db()
        s = _pdb.get_summary()
        total = s.get("_total", 0)
        verified = s.get("verified", 0)
        contact_found = s.get("contact_found", 0)
        if verified > 0 and contact_found == 0:
            issues.append(f"⚠️  Playlist DB: {verified} playlists verified but no contact found yet — run rjm-playlist-discover")
        else:
            print(f"  ✅ Playlist pipeline: {total} total, {contact_found} with contact info")
    except Exception:
        pass  # playlist_db not critical for health

    if issues:
        print(f"\nISSUES FOUND ({len(issues)}):")
        for i in issues:
            print(f"  {i}")
    else:
        print("\n  ✅ All systems nominal.")
    print()


# ─── Podcast Target Priority List ─────────────────────────────────────────────

def cmd_podcast_targets():
    db.init_db()
    with db.get_conn() as conn:
        # Verified podcasts not yet sent
        verified = conn.execute("""
            SELECT email, name, genre, notes, research_notes, website, research_done
            FROM contacts
            WHERE type='podcast' AND status='verified'
            ORDER BY research_done DESC, date_added ASC
        """).fetchall()

        # Already sent podcasts
        sent = conn.execute("""
            SELECT email, name, status, date_sent, response_snippet
            FROM contacts
            WHERE type='podcast' AND status IN ('sent','followup_sent','followup2_sent','responded')
            ORDER BY date_sent DESC
        """).fetchall()

    print(f"\n=== PODCAST PIPELINE ({date.today()}) ===\n")
    print(f"VERIFIED & READY ({len(verified)}):")
    if not verified:
        print("  None — run rjm-discover to find podcast contacts")
    for r in verified:
        r = dict(r)
        researched = "✅" if r.get("research_done") else "○ "
        print(f"  {researched} {r['name']:<35} {r['email']}")
        if r.get("genre"):
            print(f"       Genre: {r['genre']}")
        if r.get("research_notes"):
            note = r["research_notes"][:120].replace("\n", " ")
            print(f"       Research: {note}")

    print(f"\nIN PROGRESS ({len(sent)}):")
    if not sent:
        print("  None yet")
    for r in sent:
        r = dict(r)
        resp_flag = " 🔥 RESPONDED" if r["status"] == "responded" else ""
        snippet = ""
        if r.get("response_snippet"):
            snippet = " — \"" + r["response_snippet"][:80].replace("\n", " ") + "\""
        print(f"  [{r['status']}] {r['name']} ({r.get('date_sent','?')}){resp_flag}{snippet}")

    print(f"\nPODCAST ANGLES (from story.py):")
    print("  1. Bible-inspired techno — music as prayer/worship in the rave")
    print("  2. Entrepreneur who lost everything → rebuilt through music")
    print("  3. Living in a campervan in Tenerife as spiritual reset")
    print("  4. Faith as the foundation of creative process")
    print("  5. Tenerife underground scene — what's happening off the tourist trail")
    print()


# ─── Adjust Weights ───────────────────────────────────────────────────────────

def cmd_adjust_weights(args):
    """
    Parse 'curator=70 podcast=30' style args and rewrite config.py weights.
    """
    new_weights = {}
    for arg in args:
        if "=" in arg:
            key, val = arg.split("=", 1)
            try:
                new_weights[key.strip()] = int(val.strip())
            except ValueError:
                print(f"⚠️  Ignoring invalid weight: {arg}")

    if not new_weights:
        print("No valid weight arguments. Example: python3 master_agent.py adjust curator=70 podcast=30")
        return

    config_path = Path(__file__).parent / "config.py"
    text = config_path.read_text()

    # Find the CONTACT_TYPE_WEIGHTS block and update values
    for key, val in new_weights.items():
        # Match e.g. '"curator":        60,' and replace the number
        pattern = rf'("{key}")\s*:\s*(\d+)'
        replacement = rf'"\g<1>":\t{val}'
        # More precise: preserve the key format
        pattern2 = rf'([\'"]{re.escape(key)}[\'"]\s*:\s*)(\d+)'
        new_text = re.sub(pattern2, lambda m: m.group(1) + str(val), text)
        if new_text != text:
            text = new_text
            print(f"  ✅ Set {key} weight → {val}")
        else:
            print(f"  ⚠️  Could not find '{key}' in config.py — check the key name")

    config_path.write_text(text)
    print(f"\nconfig.py updated. Restart any running processes to pick up new weights.")


# ─── Growth Strategy Commands ─────────────────────────────────────────────────

def _load_registry() -> dict:
    """Load strategy_registry.json, or return empty structure if missing."""
    if not STRATEGY_REGISTRY_PATH.exists():
        return {"strategies": []}
    with open(STRATEGY_REGISTRY_PATH, "r") as f:
        return json.load(f)


def cmd_spotify_status():
    """Show Spotify listener stats from spotify_tracker.py."""
    print(f"\n=== RJM SPOTIFY GROWTH TRACKER ===\n")

    # Delegate to spotify_tracker for live stats
    result = subprocess.run(
        [sys.executable, str(SPOTIFY_TRACKER_PATH), "status"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(result.stdout, end="")
    else:
        print(f"  Could not read Spotify stats: {result.stderr.strip()}")
        print(f"  Run: python3 spotify_tracker.py log <listeners>  to record your first reading.")

    # Show milestone progress
    result2 = subprocess.run(
        [sys.executable, str(SPOTIFY_TRACKER_PATH), "milestone"],
        capture_output=True, text=True
    )
    if result2.returncode == 0:
        print(result2.stdout, end="")

    # Strategy context: how many strategies are active
    registry = _load_registry()
    strategies = registry.get("strategies", [])
    active = [s for s in strategies if s.get("status") == "active"]
    estimated_monthly = sum(s.get("estimated_listeners_per_month", 0) for s in active)

    print(f"  ACTIVE STRATEGIES     : {len(active)}")
    print(f"  Estimated monthly gain: +{estimated_monthly:,} listeners from active channels")
    print(f"\n  Run 'python3 master_agent.py strategy' for full portfolio.")
    print(f"  Run 'python3 master_agent.py next_build' for the next growth lever to activate.\n")


def cmd_strategy_portfolio():
    """Print full strategy portfolio table sorted by priority."""
    registry = _load_registry()
    strategies = registry.get("strategies", [])

    if not strategies:
        print("  No strategies found in strategy_registry.json")
        return

    # Sort by priority descending
    strategies = sorted(strategies, key=lambda s: s.get("priority", 0), reverse=True)

    STATUS_ICONS = {
        "active":      "[ACTIVE]     ",
        "building":    "[BUILDING]   ",
        "not_started": "[NOT STARTED]",
        "paused":      "[PAUSED]     ",
        "abandoned":   "[ABANDONED]  ",
    }
    IMPACT_ICONS = {
        "very_high": "****",
        "high":      "*** ",
        "medium":    "**  ",
        "low":       "*   ",
    }

    active_strategies = [s for s in strategies if s.get("status") == "active"]
    total_active_estimate = sum(s.get("estimated_listeners_per_month", 0) for s in active_strategies)
    total_all_estimate    = sum(s.get("estimated_listeners_per_month", 0) for s in strategies)

    print(f"\n{'='*90}")
    print(f"  RJM GROWTH STRATEGY PORTFOLIO — Goal: 1,000,000 Spotify Monthly Listeners")
    print(f"  {registry.get('last_updated', date.today())}  |  {len(active_strategies)}/{len(strategies)} strategies active")
    print(f"{'='*90}\n")

    header = f"  {'P':>2}  {'Status':<15}  {'Impact':<6}  {'Est/mo':>8}  {'Strategy'}"
    print(header)
    print(f"  {'-'*2}  {'-'*15}  {'-'*6}  {'-'*8}  {'-'*40}")

    for s in strategies:
        prio   = s.get("priority", 0)
        status = s.get("status", "not_started")
        impact = s.get("estimated_impact", "")
        est    = s.get("estimated_listeners_per_month", 0)
        name   = s.get("name", s.get("id", ""))[:45]

        status_str = STATUS_ICONS.get(status, f"[{status}]")
        impact_str = IMPACT_ICONS.get(impact, "?   ")

        # Highlight active vs not_started
        if status == "active":
            row = f"  {prio:>2}  {status_str}  {impact_str}  {est:>8,}  {name}"
        elif status == "not_started":
            row = f"  {prio:>2}  {status_str}  {impact_str}  {est:>8,}  {name}"
        else:
            row = f"  {prio:>2}  {status_str}  {impact_str}  {est:>8,}  {name}"

        print(row)

    print(f"\n  {'─'*80}")
    print(f"  ACTIVE STRATEGIES: {len(active_strategies)} running")
    print(f"  Estimated gain from active  : +{total_active_estimate:,} listeners/month")
    print(f"  Estimated gain if all active: +{total_all_estimate:,} listeners/month")
    print(f"\n  Run 'python3 master_agent.py next_build' for the top unbuilt strategy + action plan.\n")


def cmd_next_build():
    """Recommend the single highest-priority not_started strategy with specific action steps."""
    registry = _load_registry()
    strategies = registry.get("strategies", [])

    not_started = [s for s in strategies if s.get("status") == "not_started"]
    if not not_started:
        print("\n  All strategies are active or in progress. Impressive.")
        return

    # Sort by priority descending, then by estimated_listeners_per_month descending
    not_started = sorted(
        not_started,
        key=lambda s: (s.get("priority", 0), s.get("estimated_listeners_per_month", 0)),
        reverse=True
    )

    top = not_started[0]

    print(f"\n{'='*65}")
    print(f"  NEXT BUILD RECOMMENDATION")
    print(f"{'='*65}\n")
    print(f"  Strategy    : {top['name']}")
    print(f"  Priority    : {top['priority']}/10")
    print(f"  Impact      : {top['estimated_impact'].upper()}")
    print(f"  Complexity  : {top['implementation_complexity']}")
    print(f"  Cost        : {top['cost']}")
    print(f"  Est. gain   : +{top['estimated_listeners_per_month']:,} listeners/month")
    print(f"\n  Why this next:")
    print(f"    {top['description']}")
    print(f"\n  Notes:")
    print(f"    {top.get('notes', '—')}")

    # Strategy-specific action plans
    _print_action_plan(top["id"])

    # Show queue of what comes after
    print(f"\n  NEXT IN QUEUE (after {top['id']}):")
    for s in not_started[1:4]:
        print(f"    [{s['priority']}/10]  {s['name']} — est. +{s['estimated_listeners_per_month']:,}/mo")

    print()


def _print_action_plan(strategy_id: str):
    """Print specific action steps per strategy ID."""

    plans = {
        "spotify_editorial_pitching": [
            "1. Log in to Spotify for Artists (artists.spotify.com)",
            "2. For your NEXT unreleased track: click 'Pitch a Song' at least 7 days before release",
            "3. Fill in ALL fields: mood, genre, style, instruments, story behind the track",
            "4. Story angle for pitching: 'Biblical themes translated into tribal techno — sacred frequencies'",
            "5. Target playlist categories: Electronic, Dance, Meditation/Focus, New Age",
            "6. Set release date on Friday (Spotify New Music Friday algorithm)",
            "7. Repeat for EVERY release — this is a non-negotiable habit",
            "",
            "   Code to track pitch submissions:",
            "   Add a 'spotify_pitches' table to track each submission:",
            "     CREATE TABLE spotify_pitches (",
            "       id INTEGER PRIMARY KEY, track_name TEXT,",
            "       pitch_date TEXT, release_date TEXT, result TEXT",
            "     );",
        ],
        "instagram_to_spotify": [
            "1. Update IG bio link immediately: use Linktree or Hypeddit with Spotify as primary CTA",
            "2. Add Spotify Music sticker to 3x IG stories this week",
            "3. Caption formula: end every track post with 'Link in bio to stream on Spotify'",
            "4. Create a 'Swipe Up' story template: background image + track title + Spotify logo",
            "5. Post a 'New Music Friday' story every release day with Spotify link",
            "6. IG Reel structure that converts: hook (0-3s) → content → 'Out now on Spotify' (last frame)",
            "7. Add Spotify follow button to website if not already there",
            "",
            "   Quick Python snippet to track IG-to-Spotify conversion:",
            "   Use UTM parameters: spotify.com/artist/ID?utm_source=instagram",
            "   Check Spotify for Artists 'Audience' tab to see Instagram as a referral source",
        ],
        "release_optimization": [
            "1. Log in to Spotify for Artists and update your artist bio (max 1500 chars)",
            "2. Add/update header image — high-quality, on-brand, 2660x1140px",
            "3. For each existing track: add Spotify Canvas (8-second looping video)",
            "   - Upload via Spotify for Artists → Profile → Canvas",
            "   - Tenerife visuals or abstract tribal patterns work well",
            "4. Set 'Artist Pick' to your strongest track or latest release",
            "5. Check metadata on all releases via your distributor:",
            "   - Genre: Techno (verify it's correct, not 'Electronic' generic)",
            "   - Language: Instrumental or Dutch",
            "   - Mood tags: fill all available fields",
            "6. Add all tracks to your own playlists (see spotify_playlist_creation strategy)",
            "",
            "   Canvas creation resources:",
            "   - Canva has Spotify Canvas templates (1080x1920, 8s loop)",
            "   - Adobe Express: free Canvas video tool",
        ],
        "tiktok_reels_content": [
            "1. Set up TikTok account if not active (use same handle as IG)",
            "2. Content calendar: 4 posts/week minimum",
            "   - Monday: production behind-the-scenes (DAW screen + voiceover)",
            "   - Wednesday: spiritual/personal story (1 min talking to camera)",
            "   - Friday: new music reveal with Spotify CTA",
            "   - Sunday: Tenerife life content (sunset, beach, studio)",
            "3. Hook formula: first 3 seconds MUST show something surprising",
            "   - 'I make techno based on the Bible' — say this in first 2 seconds",
            "   - Show track in DAW before revealing what inspired it",
            "4. Every video description: 'Stream on Spotify — link in bio'",
            "5. Use trending sounds/templates but overlay your own music",
            "6. Batch record 4 videos in one session to stay consistent",
            "",
            "   High-converting content ideas specific to RJM:",
            "   - 'The story behind [track name] — inspired by Psalm [X]'",
            "   - '290K Instagram followers — but I want YOU to hear this on Spotify'",
            "   - 'Building tribal techno in a campervan in Tenerife'",
            "   - 'This is what Biblical techno sounds like'",
        ],
        "artist_collaborations": [
            "1. Build a target list of 20 artists in psytrance/tribal techno with 50K-500K listeners",
            "   Target examples: Merkaba, Hilight Tribe, Govinda, Desert Dwellers, Human Design",
            "2. Listen to their latest tracks — pick one you could remix authentically",
            "3. Email template (subject: 'Remix offer — [Artist Name]'):",
            "   'Hi [Name], I'm Robert-Jan, a tribal techno producer based in Tenerife.",
            "    I love [specific track]. I'd like to offer you a free remix — no strings.",
            "    Here's my work: [Spotify link]. Would you be open to this?'",
            "4. Also pitch yourself as a featured vocalist/producer on their next release",
            "5. Target festival lineups — artists playing same festivals are natural collab partners",
            "6. Add 'collab' type to outreach pipeline in rjm-discover:",
            "   Update CONTACT_TYPE_WEIGHTS in config.py to add 'collab': 20",
            "",
            "   Code to add to discover agent query list:",
            "     COLLAB_QUERIES = [",
            "       'tribal techno producer 50000 spotify listeners email',",
            "       'psytrance artist collaboration request contact',",
            "       'organic house producer remix offer',",
            "     ]",
        ],
        "submithub_paid": [
            "1. Create account at submithub.com",
            "2. Buy 50 premium credits (~$25)",
            "3. Filter curators by: Techno, Psytrance, Tribal, Organic Electronic",
            "4. Submit to 'Hot or Not' first (free) to validate your track",
            "5. For each submission, customise the pitch message (2-3 sentences max)",
            "6. Budget $50-100/month for sustained submissions",
            "7. Also submit to blogs via SubmitHub — look for 'Electronic' music blogs",
            "8. Track results in strategy_registry.json actions_taken field",
            "",
            "   Expected outcomes at $50/month:",
            "   - 15-20 curator reviews",
            "   - 3-6 playlist placements",
            "   - 1-2 blog features",
            "   - ~1,000-3,000 new stream exposures",
        ],
        "spotify_playlist_creation": [
            "1. Create 3 playlists immediately with SEO-optimised titles:",
            "   - 'Tribal Techno 2026 — Sacred Rhythms'",
            "   - 'Psytrance Spiritual Journey Mix'",
            "   - 'Underground Techno — Tenerife Sessions'",
            "2. Each playlist: 25-30 tracks. Include 2-3 of your own tracks.",
            "3. Feature artists with 10K-500K listeners — they're reachable for cross-promo",
            "4. Update playlists monthly to stay fresh (Spotify rewards active playlists)",
            "5. Promote each playlist on IG stories with Spotify embed",
            "6. Email featured artists: 'Hey, I featured you in my playlist [X], would love a reshare'",
            "7. Submit playlists to playlist-promotion directories",
            "",
            "   Growth path: 0 → 500 → 1000 → 5000 playlist followers",
            "   At 1000 followers a playlist starts appearing in Spotify recommendations",
        ],
        "music_blog_pr": [
            "1. Build media list: 15 electronic music blogs/outlets",
            "   Priority targets: Data Transmission, Electronic Groove, When We Dip,",
            "   Decoded Magazine, The Untz, Psytrance Network, Triplag Magazine",
            "2. Write one-paragraph pitch for each outlet (personalised)",
            "   Hook: 'Dutch DJ creates Bible-inspired tribal techno from a campervan in Tenerife'",
            "3. Attach: 1 press photo (hi-res), Spotify link, short bio (150 words)",
            "4. Use rjm-outreach-agent — add 'blog' type to contact pipeline",
            "5. Follow up once after 7 days (same as curator outreach)",
            "",
            "   Subject line formula: '[Artist] — [Unique Angle] — New Release [Month Year]'",
            "   Example: 'Robert-Jan Mastenbroek — Biblical Tribal Techno — April 2026'",
        ],
        "fan_email_list": [
            "1. Sign up for Mailchimp free (up to 500 subscribers free)",
            "2. Create lead magnet: 'Free exclusive track download' or 'Unreleased demo'",
            "3. Add email capture to IG bio link (Linktree → email signup page)",
            "4. Monthly newsletter template:",
            "   - Opening: 1 personal story / spiritual reflection (3-4 sentences)",
            "   - New/upcoming release: Spotify link + Canvas preview",
            "   - CTA: 'Share with one friend who needs this frequency'",
            "5. On release day: send email at 9am — 'It's out now on Spotify'",
            "6. Target: 500 subscribers → 1000 → 5000",
            "   Even 500 subscribers generating 300 release-day streams = algorithmic signal",
        ],
        "reddit_communities": [
            "1. Create Reddit account (or use existing) — username = artist name",
            "2. Spend 2 weeks engaging BEFORE posting music:",
            "   - r/psytrance: comment on 5 posts per week, share opinions",
            "   - r/WeAreTheMusicMakers: share production tips",
            "   - r/melodictechno: participate in recommendation threads",
            "3. After 2 weeks: post in weekly 'Share Your Music' thread",
            "4. Story hook for Reddit: 'I make tribal techno inspired by the Bible — here's why'",
            "5. r/ChristianEDM: this is a perfect fit — engage authentically",
            "6. Never post links without context — always tell the story first",
        ],
        "youtube_dj_mixes": [
            "1. Record a 90-minute DJ mix — best tracks + 2-3 originals mid-set",
            "2. Title formula: 'Tribal Techno Mix 2026 | [Subtitle] | Robert-Jan Mastenbroek'",
            "3. Description template:",
            "   'Stream my music on Spotify: [link]",
            "    Follow me: [IG link]",
            "    Tracklist: [timestamped chapters]'",
            "4. Upload to YouTube with custom thumbnail (Tenerife visuals + track title)",
            "5. Add chapters (timestamps) — this boosts YouTube SEO significantly",
            "6. Share to IG stories on upload day",
            "7. Post consistently: 1 mix/month minimum",
        ],
        "beatport_soundcloud_presence": [
            "1. Ensure all releases are distributed to Beatport via your distributor",
            "2. SoundCloud: upload 2-3 free tracks/mixes with prominent Spotify links",
            "3. SoundCloud description: 'Stream full discography on Spotify: [link]'",
            "4. Engage with SoundCloud comments — it builds community",
            "5. Repost other artists — they will often return the favour",
        ],
        "discord_communities": [
            "1. Search Discord Discovery for: psytrance, tribal techno, electronic music",
            "2. Join 3-5 active servers",
            "3. Introduce yourself in #introductions: lead with the Bible+techno angle",
            "4. Share music in designated #music-sharing channels (read rules first)",
            "5. Consider creating own Discord server when Spotify followers exceed 5K",
        ],
        "press_releases": [
            "1. Write one master press release template:",
            "   - Headline: 'Dutch DJ Robert-Jan Mastenbroek Releases [Track] — Biblical Tribal Techno From Tenerife'",
            "   - Lede paragraph: who, what, when, where, why",
            "   - Quote from artist: authentic, story-driven",
            "   - Streaming links + social links",
            "2. Sign up for Groover (groover.co) — curators must respond or credits refunded",
            "3. Submit to SubmitHub press section (blogs + music press)",
            "4. Build a simple EPK page on your website",
            "5. Send to the music_blog_pr target list simultaneously",
        ],
    }

    steps = plans.get(strategy_id)
    if steps:
        print(f"\n  ACTION PLAN:")
        for step in steps:
            if step:
                print(f"    {step}")
            else:
                print()
    else:
        print(f"\n  ACTION PLAN: No specific steps defined yet for '{strategy_id}'.")
        print(f"    → Add implementation notes to strategy_registry.json")


def cmd_log_listeners(n: int):
    """Log current Spotify monthly listener count to the tracker and data/listeners.json."""
    result = subprocess.run(
        [sys.executable, str(SPOTIFY_TRACKER_PATH), "log", str(n)],
        capture_output=True, text=True
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        print(f"Error: {result.stderr.strip()}")
        return

    # Keep data/listeners.json in sync — used by rjm-master SKILL.md context
    listeners_json = BASE_DIR.parent / "data" / "listeners.json"
    try:
        import json as _json
        listeners_json.parent.mkdir(exist_ok=True)
        listeners_json.write_text(
            _json.dumps({"count": n, "updatedAt": datetime.now().isoformat()}, indent=2),
            encoding="utf-8",
        )
        print(f"✓ data/listeners.json updated → {n:,}")
    except Exception as exc:
        print(f"⚠️  Could not update data/listeners.json: {exc}")

    # Attribute listener delta to active strategies in registry
    try:
        import json as _json
        registry_path = BASE_DIR / "strategy_registry.json"
        if not registry_path.exists():
            return

        reg = _json.loads(registry_path.read_text(encoding="utf-8"))

        # Get previous listener count from spotify_stats table
        db.init_db()
        with db.get_conn() as conn:
            prev_row = conn.execute(
                "SELECT monthly_listeners FROM spotify_stats ORDER BY id DESC LIMIT 1 OFFSET 1"
            ).fetchone()
        prev_count = prev_row["monthly_listeners"] if prev_row else 0
        delta = max(0, n - prev_count)

        if delta > 0:
            active = [s for s in reg["strategies"] if s.get("status") == "active"]
            total_est = sum(s.get("estimated_listeners_per_month", 1) for s in active) or 1
            for s in reg["strategies"]:
                if s.get("status") == "active":
                    weight = s.get("estimated_listeners_per_month", 0) / total_est
                    gain = round(delta * weight)
                    s["actual_listeners_gained"] = s.get("actual_listeners_gained", 0) + gain
                    s["last_updated"] = str(date.today())
            reg["last_updated"] = str(date.today())
            registry_path.write_text(_json.dumps(reg, indent=2), encoding="utf-8")
            print(f"✓ Attributed +{delta:,} listener delta across {len(active)} active strategies")
    except Exception as exc:
        print(f"⚠️  Could not update strategy registry: {exc}")


def cmd_log_run(summary: str, contacts_added: int = 0, strategy_worked: str = ""):
    """Append a timestamped entry to data/master_log.json.

    Called at the end of every rjm-master scheduled task run.
    Usage: python3 master_agent.py log_run "Built instagram_to_spotify strategy" 5 instagram_to_spotify
    """
    import json as _json
    log_path = BASE_DIR.parent / "data" / "master_log.json"
    log_path.parent.mkdir(exist_ok=True)

    if log_path.exists():
        try:
            entries = _json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            print("⚠️  master_log.json was corrupt — starting fresh")
            entries = []
    else:
        entries = []

    entry = {
        "ts":               datetime.now().isoformat(),
        "summary":          summary,
        "contacts_added":   contacts_added,
        "strategy_worked":  strategy_worked,
    }
    entries.append(entry)

    # Keep last 500 entries to prevent unbounded growth
    entries = entries[-500:]
    log_path.write_text(_json.dumps(entries, indent=2), encoding="utf-8")
    print(f"✓ Logged run: {summary[:80]}")


# ─── Run Sub-Agents ────────────────────────────────────────────────────────────

# Map agent names → Python script paths (relative to this file's directory)
_AGENT_SCRIPTS = {
    "outreach":  BASE_DIR / "agent.py",
    "discover":  BASE_DIR / "discover_agent.py",
    "research":  BASE_DIR / "research_agent.py",
    "verify":    BASE_DIR / "agent.py",   # agent.py verify command
}

_AGENT_COMMANDS = {
    "outreach": ["run"],
    "discover": [],
    "research": [],
    "verify":   ["verify"],
}


def cmd_run(agent_name: str, extra_args: list[str]):
    """
    Trigger a sub-agent by name. Shows live output.

    Available agents: outreach, discover, research, verify
    """
    known = list(_AGENT_SCRIPTS.keys())

    if agent_name not in known:
        print(f"Unknown agent: {agent_name!r}")
        print(f"Available: {', '.join(known)}")
        return

    script = _AGENT_SCRIPTS[agent_name]
    if not script.exists():
        print(f"⚠️  Script not found: {script}")
        print(f"   Make sure {script.name} exists in {BASE_DIR}")
        return

    base_cmd = _AGENT_COMMANDS.get(agent_name, [])
    cmd = [sys.executable, str(script)] + base_cmd + extra_args

    print(f"\n→ Running {agent_name}: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=str(BASE_DIR))
    sys.exit(result.returncode)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args or args[0] == "briefing":
        cmd_briefing()
    elif args[0] == "dashboard":
        cmd_dashboard()
    elif args[0] == "responses":
        cmd_responses()
    elif args[0] == "gaps":
        cmd_gaps()
    elif args[0] == "weekly":
        cmd_weekly()
    elif args[0] == "health":
        cmd_health()
    elif args[0] == "podcast_targets":
        cmd_podcast_targets()
    elif args[0] == "adjust":
        cmd_adjust_weights(args[1:])
    elif args[0] == "spotify":
        cmd_spotify_status()
    elif args[0] == "strategy":
        cmd_strategy_portfolio()
    elif args[0] == "next_build":
        cmd_next_build()
    elif args[0] == "log_listeners":
        if len(args) > 1:
            cmd_log_listeners(int(args[1].replace(",", "")))
        else:
            print("Usage: python3 master_agent.py log_listeners <number>")
    elif args[0] == "log_run":
        summary = args[1] if len(args) > 1 else "run"
        try:
            contacts = int(args[2].replace(",", "")) if len(args) > 2 else 0
        except ValueError:
            print("Error: contacts_added must be a number")
            sys.exit(1)
        strategy = args[3] if len(args) > 3 else ""
        cmd_log_run(summary, contacts, strategy)
    elif args[0] == "run":
        agent = args[1] if len(args) > 1 else ""
        if not agent:
            print("Usage: python3 master_agent.py run <agent>")
            print("Agents: outreach, discover, research, verify")
        else:
            cmd_run(agent, args[2:])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
