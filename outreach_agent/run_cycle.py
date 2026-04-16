#!/usr/bin/env python3
"""
RJM Outreach Agent — Cycle Planner (pure Python, no Gmail API)

Called by the Claude Code scheduled task to:
  1. Figure out what needs to happen this cycle (plan)
  2. Update DB after actions are taken (mark_sent, mark_responded, mark_bounced)
  3. Report status

Usage:
  python3 run_cycle.py plan              # Output JSON action plan for this cycle (with pre-generated emails)
  python3 run_cycle.py contacts          # Output JSON contacts needing action — NO email generation (agent generates inline)
  python3 run_cycle.py gmail_url         # Read JSON from stdin {email,subject,body} → output encoded Gmail URL
  python3 run_cycle.py status            # Human-readable status
  python3 run_cycle.py verify_pending    # Bounce-check all new contacts
  python3 run_cycle.py mark_sent <email> <subject> <thread_url>
  python3 run_cycle.py mark_responded <email> <snippet>
  python3 run_cycle.py mark_bounced <email>
  python3 run_cycle.py mark_followup_sent <email> <subject>
  python3 run_cycle.py add_contact <email> <name> <type> <genre> <notes> [website] [playlist_size]
  python3 run_cycle.py store_research <email> <research_notes>
  python3 run_cycle.py pending_research     # list contacts needing research
  python3 run_cycle.py set_playlist_size <email> <size>   # size: small|medium|large
"""

import json
import sys
import os
from datetime import datetime, timedelta
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(__file__))

import db
import bounce
import scheduler
import followup_engine
import template_engine
import learning

try:
    import contact_scorer as _contact_scorer
    _SCORER_AVAILABLE = True
except ImportError:
    _SCORER_AVAILABLE = False

try:
    import playlist_enricher as _playlist_enricher
    _ENRICHER_AVAILABLE = True
except ImportError:
    _ENRICHER_AVAILABLE = False

from config import (
    MAX_EMAILS_PER_DAY, FOLLOWUP_DAYS, FOLLOWUP2_DAYS,
    CONTACT_TYPE_WEIGHTS, SMALL_PLAYLIST_PER_CYCLE,
    YOUTUBE_SHARE_FLOOR,
)

import random as _random

try:
    import events as _events
    import fleet_state as _fleet_state
    _HIVE_AVAILABLE = True
except ImportError:
    _HIVE_AVAILABLE = False


def _weighted_order_with_youtube_floor(contacts, youtube_share: float = YOUTUBE_SHARE_FLOOR):
    """
    Weighted-order a batch of contacts, with a guaranteed floor for type='youtube'.

    Semantics:
      - Sort non-YouTube contacts by their type's weight (higher weight first),
        shuffled within a type.
      - YouTube contacts are interleaved so they reach `youtube_share` of the
        output whenever supply allows.
      - If YouTube supply is short, others fill the slot (overflow DOWN).
      - If only YouTube supply is present, the whole batch is YouTube (overflow UP).

    The goal is a per-batch floor, not just a per-day floor — even small batches
    of 7 get ~3–4 YouTube contacts when the queue has supply.
    """
    yt = [c for c in contacts if c.get("type") == "youtube"]
    others = [c for c in contacts if c.get("type") != "youtube"]
    # Sort YouTube contacts by genre_match_score DESC so psytrance/progressive
    # channels (primary focus, higher scores) dispatch before Christian EDM /
    # organic house / melodic techno (secondary). Ties are randomized via a
    # secondary shuffle key for fairness within the same score band.
    import random as _r
    yt.sort(
        key=lambda c: (
            -(c.get("youtube_genre_match_score") or 0.0),
            _r.random(),
        )
    )

    # Weighted order for non-YouTube types
    by_type: dict[str, list] = {}
    for c in others:
        by_type.setdefault(c.get("type", ""), []).append(c)
    types_sorted = sorted(
        by_type.keys(),
        key=lambda t: CONTACT_TYPE_WEIGHTS.get(t, 1),
        reverse=True,
    )
    ordered_others: list = []
    for t in types_sorted:
        bucket = by_type[t]
        _random.shuffle(bucket)
        ordered_others.extend(bucket)

    # Interleave to hit the floor
    result: list = []
    total = len(yt) + len(ordered_others)
    yt_used = other_used = 0
    for i in range(total):
        yt_remaining = len(yt) - yt_used
        other_remaining = len(ordered_others) - other_used

        if yt_remaining == 0:
            result.append(ordered_others[other_used]); other_used += 1
            continue
        if other_remaining == 0:
            result.append(yt[yt_used]); yt_used += 1
            continue

        # Are we ahead or behind on the YouTube target after this pick?
        yt_target_so_far = int((i + 1) * youtube_share + 0.5)
        if yt_used < yt_target_so_far:
            result.append(yt[yt_used]); yt_used += 1
        else:
            result.append(ordered_others[other_used]); other_used += 1
    return result


def _rescue_stale_queued():
    """
    Contacts stuck in 'queued' for > 2 hours were likely abandoned by a crashed task.
    Reset them to 'verified' so they can be retried — unless they've hit
    MAX_SEND_ATTEMPTS, in which case dead-letter them to stop consuming quota.
    """
    from config import MAX_SEND_ATTEMPTS
    cutoff = (datetime.now() - timedelta(hours=2)).isoformat()
    rescued = 0
    dead_lettered = 0
    with db.get_conn() as conn:
        stale = conn.execute("""
            SELECT email, send_attempts FROM contacts
            WHERE status = 'queued' AND date_queued < ?
        """, (cutoff,)).fetchall()
        for row in stale:
            attempts = row["send_attempts"] or 0
            if attempts >= MAX_SEND_ATTEMPTS:
                conn.execute(
                    "UPDATE contacts SET status='dead_letter',"
                    " notes = COALESCE(notes, '') || ' | DEAD_LETTER: stale_queued' "
                    "WHERE email=?",
                    (row["email"],),
                )
                dead_lettered += 1
            else:
                conn.execute(
                    "UPDATE contacts SET status='verified' WHERE email=?", (row["email"],)
                )
                rescued += 1
    if rescued or dead_lettered:
        try:
            import events as _events
            _events.publish(
                "pipeline.stale_queued",
                source="run_cycle._rescue_stale_queued",
                payload={"rescued": rescued, "dead_lettered": dead_lettered},
            )
        except Exception:
            pass


def _detect_stale_research(max_age_days: int = 3) -> int:
    """Publish an event if contacts have been waiting for research too long.

    Returns the count of stale contacts. Does NOT change state — unresearched
    contacts are still eligible for sending, just ranked lower. Purpose is
    telemetry: if this number grows, rjm-research is likely broken.
    """
    cutoff = (datetime.now() - timedelta(days=max_age_days)).date().isoformat()
    with db.get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM contacts
            WHERE status = 'verified'
              AND (research_done IS NULL OR research_done = 0)
              AND date_verified IS NOT NULL
              AND date_verified < ?
            """,
            (cutoff,),
        ).fetchone()
    count = int(row["n"]) if row else 0
    if count:
        try:
            import events as _events
            _events.publish(
                "pipeline.stale_research",
                source="run_cycle._detect_stale_research",
                payload={"count": count, "max_age_days": max_age_days},
            )
        except Exception:
            pass
    return count


def _publish_plan_gaps(plan: dict) -> None:
    """Emit a `pipeline.gap_detected` event when the plan has nothing to do.

    Called by `cmd_plan` after the plan is built. Silence is the steady state:
    we only publish when something is wrong (empty queue, closed window,
    exhausted quota, or a plan that somehow produced zero actions despite
    having capacity). The master dashboard listens for these events to show
    *why* the fleet is idle.
    """
    actions = plan.get("actions", [])
    action_count = len(actions)
    if action_count > 0:
        return  # plan is healthy — say nothing

    reasons = []
    if not plan.get("window_open", False):
        reasons.append("window_closed")
    if (plan.get("quota_remaining") or 0) <= 0:
        reasons.append("quota_exhausted")

    verified_count = 0
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM contacts WHERE status='verified'"
            ).fetchone()
            verified_count = int(row["n"]) if row else 0
    except Exception:
        pass

    if verified_count == 0:
        reasons.append("no_sendable_contacts")
    if not reasons:
        # Window open, quota OK, verified contacts exist, yet the plan is
        # empty. Something upstream (template gen, scoring) is eating work.
        reasons.append("empty_plan_unexplained")

    try:
        import events as _events
        _events.publish(
            "pipeline.gap_detected",
            source="run_cycle.cmd_plan",
            payload={
                "reasons": reasons,
                "verified_count": verified_count,
                "action_count": action_count,
                "window_open": bool(plan.get("window_open", False)),
                "quota_remaining": int(plan.get("quota_remaining") or 0),
            },
        )
    except Exception:
        pass


def cmd_plan():
    """
    Output a JSON action plan for the current cycle.
    Claude Code reads this and executes the actions via Chrome.
    """
    db.init_db()
    _rescue_stale_queued()
    _detect_stale_research()

    plan = {
        "window_open":    scheduler.is_within_active_window(),
        "quota_remaining": scheduler.remaining_quota_today(),
        "window_status":  scheduler.SendWindow().status(),
        "actions": []
    }

    # --- Verify new contacts ---
    new_contacts = db.get_contacts_by_status("new")
    for c in new_contacts:
        result, reason = bounce.verify_email(c["email"])
        if result == "invalid":
            db.mark_bounced_full(c["email"], reason, bounce_type="pre-check")
        else:
            db.mark_verified(c["email"])

    # --- Follow-ups: second touch (day 12) before first touch (day 5) ---
    if scheduler.is_within_active_window() and scheduler.remaining_quota_today() > 0:
        # Second follow-ups first (older contacts, higher urgency)
        fu2_candidates = db.get_followup2_candidates(days_since_followup1=FOLLOWUP2_DAYS)
        for c in fu2_candidates[:3]:
            if scheduler.remaining_quota_today() <= 0:
                break
            fresh = db.get_contact(c["email"])
            if not fresh or fresh.get("status") != "followup_sent":
                continue
            try:
                subject, body = template_engine.generate_followup_email(c)
            except Exception:
                continue
            gmail_url = (
                "https://mail.google.com/mail/u/0/?view=cm"
                + "&to=" + quote(c["email"], safe="")
                + "&su=" + quote(subject, safe="")
                + "&body=" + quote(body, safe="")
            )
            plan["actions"].append({
                "type":      "followup2",
                "email":     c["email"],
                "subject":   subject,
                "gmail_url": gmail_url,
            })

        # First follow-ups (day 5)
        fu1_candidates = db.get_followup_candidates(days_since_send=FOLLOWUP_DAYS)
        for c in fu1_candidates[:5]:
            if scheduler.remaining_quota_today() <= 0:
                break
            fresh = db.get_contact(c["email"])
            if not fresh or fresh.get("status") != "sent":
                continue
            try:
                subject, body = template_engine.generate_followup_email(c)
            except Exception:
                continue
            gmail_url = (
                "https://mail.google.com/mail/u/0/?view=cm"
                + "&to=" + quote(c["email"], safe="")
                + "&su=" + quote(subject, safe="")
                + "&body=" + quote(body, safe="")
            )
            plan["actions"].append({
                "type":      "followup",
                "email":     c["email"],
                "subject":   subject,
                "gmail_url": gmail_url,
            })

    # --- Initial sends (YouTube-floor enforced + type-weighted) ---
    batch_size = scheduler.compute_batch_size()
    if scheduler.is_within_active_window() and batch_size > 0:
        _active_types = {t for t, w in CONTACT_TYPE_WEIGHTS.items() if w > 0}
        verified_all = db.get_contacts_by_status("verified", limit=100)
        verified_all = [c for c in verified_all if c.get("type") in _active_types]

        # Prefer researched contacts; fall back to unresearched to keep volume
        researched   = [c for c in verified_all if c.get("research_done") == 1]
        unresearched = [c for c in verified_all if c.get("research_done") != 1]

        # Step 1: Small-tagged contacts (500–10k) — prioritise researched first
        small_researched   = [c for c in researched   if c.get("playlist_size") == "small"]
        small_unresearched = [c for c in unresearched if c.get("playlist_size") == "small"]
        small_pool = (
            _weighted_order_with_youtube_floor(small_researched)
            + _weighted_order_with_youtube_floor(small_unresearched)
        )
        small_contacts = small_pool[:SMALL_PLAYLIST_PER_CYCLE]
        small_emails   = {c["email"] for c in small_contacts}

        # Step 2: Fill remaining slots — researched first, then unresearched
        rest_researched   = [c for c in researched   if c["email"] not in small_emails]
        rest_unresearched = [c for c in unresearched if c["email"] not in small_emails]
        rest_pool = (
            _weighted_order_with_youtube_floor(rest_researched)
            + _weighted_order_with_youtube_floor(rest_unresearched)
        )

        ordered = small_contacts + rest_pool

        # Collect contacts for this batch (capped at batch_size)
        batch_contacts = ordered[:batch_size]

        # Re-rank batch by ROI score (learning + reply rates + Spotify momentum + schedule fit).
        # contact_scorer expects 'contact_type' but DB rows use 'type' — adapt in a shallow copy,
        # then map ranked order back to the original dicts so downstream keys stay intact.
        if _SCORER_AVAILABLE and batch_contacts:
            try:
                adapted = [
                    {**c, "contact_type": c.get("type", ""),
                     "research_notes": c.get("research_notes", "") or ""}
                    for c in batch_contacts
                ]
                ranked_adapted = _contact_scorer.rank(adapted)
                order_index = {c["email"]: i for i, c in enumerate(ranked_adapted)}
                batch_contacts = sorted(
                    batch_contacts,
                    key=lambda c: order_index.get(c["email"], len(order_index))
                )
            except Exception:
                pass  # scoring is advisory — never break the cycle

        # Enrich with playlist DB context (best track, matching playlist row).
        # Advisory — never block the cycle if enrichment fails.
        if _ENRICHER_AVAILABLE and batch_contacts:
            try:
                batch_contacts = _playlist_enricher.enrich_batch(batch_contacts)
            except Exception:
                pass

        # Build learning context per type (one lookup per type, not per contact)
        _learn_cache = {}
        for c in batch_contacts:
            ctype = c.get("type", "curator")
            if ctype not in _learn_cache:
                _learn_cache[ctype] = learning.get_learning_context_for_template(ctype)

        # Generate ALL emails in one Claude CLI call (avoids N×120s subprocess overhead)
        generated = template_engine.generate_emails_batch(batch_contacts, _learn_cache)

        for c in batch_contacts:
            result = generated.get(c["email"])
            if not result:
                continue  # generation failed for this contact — skip silently
            subject, body = result
            gmail_url = (
                "https://mail.google.com/mail/u/0/?view=cm"
                + "&to=" + quote(c["email"], safe="")
                + "&su=" + quote(subject, safe="")
                + "&body=" + quote(body, safe="")
            )
            plan["actions"].append({
                "type":      "send",
                "email":     c["email"],
                "subject":   subject,
                "gmail_url": gmail_url,
            })

    # --- Threads to check for replies ---
    # Contacts sent in the last 30 days with a stored thread URL
    with db.get_conn() as conn:
        open_threads = conn.execute("""
            SELECT email, gmail_thread_id as thread_url
            FROM contacts
            WHERE status IN ('sent','followup_sent')
              AND gmail_thread_id IS NOT NULL
              AND gmail_thread_id != ''
            ORDER BY date_sent DESC
            LIMIT 30
        """).fetchall()

    plan["threads_to_check"] = [dict(r) for r in open_threads]

    # --- Pipeline gap telemetry ---
    # Emit a single `pipeline.gap_detected` event if this plan has nothing to
    # do, so the master/health view can surface why the fleet is idle.
    _publish_plan_gaps(plan)

    if _HIVE_AVAILABLE:
        _fleet_state.heartbeat("run_cycle", status="ok", result={
            "actions": len(plan.get("actions", [])),
            "quota_remaining": plan.get("quota_remaining", 0)
        })

    # Sort send/followup actions by proximity to each contact's optimal send hour
    try:
        from scheduler import best_send_time as _best_send_time
        _current_hour = datetime.now().hour
        def _send_score(action: dict) -> int:
            if action.get("action") not in ("send", "followup", "followup2"):
                return -1  # non-send actions go first
            return abs(_best_send_time(action.get("email", "")) - _current_hour)
        plan["actions"].sort(key=_send_score)
    except Exception:
        pass  # never break the plan on sorting failure

    print(json.dumps(plan, indent=2))


def cmd_status():
    db.init_db()
    summary = db.get_pipeline_summary()
    window  = scheduler.SendWindow()

    print("\n=== RJM OUTREACH STATUS ===")
    for k, v in summary.items():
        if not k.startswith("_"):
            print(f"  {k:<22} {v}")
    print(f"  {'Today sent':<22} {summary.get('_today_sent', 0)} / {MAX_EMAILS_PER_DAY}")
    print(f"  {'Reply rate':<22} {summary.get('_reply_rate', '—')}")
    print(f"\n  Scheduler: {window.status()}")

    # Show next 5 in queue
    verified = db.get_contacts_by_status("verified")
    new      = db.get_contacts_by_status("new")
    pending  = verified + new
    if pending:
        print(f"\n  Next in queue ({len(pending)} total):")
        for c in pending[:5]:
            print(f"    {c['email']:<40} {c['type']}")
    print()


def cmd_mark_sent(email, subject, thread_url=""):
    db.init_db()
    db.mark_sent(
        email=email,
        message_id=thread_url,   # store thread URL in message_id field
        thread_id=thread_url,    # also store as thread_id for reply checking
        subject=subject,
        body_snippet="",
        template_type=db.get_contact(email).get("type","") if db.get_contact(email) else "",
    )
    db.increment_today_count()
    ctype = (db.get_contact(email) or {}).get("type", "unknown")
    db.record_send_for_template(ctype, ctype)
    if _HIVE_AVAILABLE:
        _events.publish("email.sent", "run_cycle", {
            "email": email,
            "subject": subject,
        })
    print(f"✅ Marked sent: {email}")


def cmd_mark_responded(email, snippet=""):
    db.init_db()
    db.mark_responded(email, reply_snippet=snippet)
    # Update template performance
    c = db.get_contact(email)
    if c:
        db.record_reply_for_template(c.get("template_type","unknown"), c.get("type","unknown"))
    if _HIVE_AVAILABLE:
        _events.publish("reply.detected", "run_cycle", {
            "email": email,
            "snippet": snippet[:200] if snippet else "",
        })
    print(f"✅ Marked responded: {email}")


def cmd_mark_bounced(email):
    db.init_db()
    db.mark_bounced_full(email, reason="Actual delivery failure seen in browser", bounce_type="actual")
    if _HIVE_AVAILABLE:
        _events.publish("bounce.detected", "run_cycle", {"email": email})
    print(f"✅ Marked bounced: {email}")


def cmd_mark_followup_sent(email, subject):
    db.init_db()
    db.mark_followup_sent(email, message_id="browser", subject=subject, body_snippet="")
    db.increment_today_count()
    print(f"✅ Marked followup sent: {email}")


def cmd_mark_followup2_sent(email, subject):
    db.init_db()
    db.mark_followup2_sent(email, message_id="browser", subject=subject, body_snippet="")
    db.increment_today_count()
    print(f"✅ Marked followup2 sent: {email}")


def cmd_add_contact(email, name, ctype, genre="", notes="", website="", playlist_size="", search_query=""):
    db.init_db()
    ok, reason = db.add_contact(email, name, ctype, genre, notes, source="agent_discovered")
    if ok:
        updates = {}
        if website:
            updates["website"] = website
        if playlist_size and playlist_size in ("small", "medium", "large"):
            updates["playlist_size"] = playlist_size
        if updates:
            db.update_contact(email, **updates)
        # Auto-verify via bounce check
        result, breason = bounce.verify_email(email)
        if result == "invalid":
            db.mark_bounced_full(email, breason, bounce_type="pre-check")
            print(f"❌ Bounced ({breason}): {email}")
        else:
            db.mark_verified(email)
            size_tag = f" [{playlist_size}]" if playlist_size else ""
            print(f"✅ Added + verified{size_tag}: {email}")
    else:
        print(f"⏭  Skipped ({reason}): {email}")

    # Lake 2 Task 9: always record the discovery attempt so rjm-discover's
    # search-dedup guard (recently_searched) has ground truth. Query falls back
    # to "manual_add:<type>" when no explicit search context was passed.
    try:
        query = search_query or f"manual_add:{ctype}"
        db.log_discovery(query, ctype, 1 if ok else 0)
    except Exception as exc:
        print(f"⚠️  log_discovery failed: {exc}")


def cmd_set_playlist_size(email, size):
    db.init_db()
    if size not in ("small", "medium", "large"):
        print(f"❌ Invalid size '{size}'. Use: small | medium | large")
        return
    db.update_contact(email, playlist_size=size)
    print(f"✅ Set playlist_size={size} for {email}")


def cmd_store_research(email, research_notes):
    db.init_db()
    db.store_research(email, research_notes)
    print(f"✅ Research stored for: {email}")


def cmd_pending_research():
    db.init_db()
    contacts = db.get_unresearched_verified(limit=20)
    if not contacts:
        print("No contacts pending research.")
        return
    import json
    print(json.dumps([{
        "email": c["email"],
        "name": c["name"],
        "type": c["type"],
        "genre": c.get("genre", ""),
        "notes": c.get("notes", ""),
        "website": c.get("website", ""),
    } for c in contacts], indent=2))


def cmd_contacts():
    """
    Output JSON with contacts needing action this cycle — track recs pre-computed,
    NO email content generated. The task agent (Claude) writes emails inline.
    This eliminates the subprocess bottleneck entirely.
    """
    db.init_db()
    _rescue_stale_queued()

    # --- Verify new contacts ---
    new_contacts = db.get_contacts_by_status("new")
    for c in new_contacts:
        result, reason = bounce.verify_email(c["email"])
        if result == "invalid":
            db.mark_bounced_full(c["email"], reason, bounce_type="pre-check")
        else:
            db.mark_verified(c["email"])

    output = {
        "window_open":     scheduler.is_within_active_window(),
        "quota_remaining": scheduler.remaining_quota_today(),
        "window_status":   scheduler.SendWindow().status(),
        "send_contacts":   [],
        "followup_contacts": [],
        "threads_to_check": [],
    }

    if not scheduler.is_within_active_window():
        print(json.dumps(output, indent=2))
        return

    # --- Follow-ups ---
    if scheduler.remaining_quota_today() > 0:
        # Second follow-ups (day 12)
        for c in db.get_followup2_candidates(days_since_followup1=FOLLOWUP2_DAYS)[:3]:
            fresh = db.get_contact(c["email"])
            if not fresh or fresh.get("status") != "followup_sent":
                continue
            output["followup_contacts"].append({
                "email":        c["email"],
                "name":         c.get("name", ""),
                "sent_subject": c.get("sent_subject", ""),
                "followup_num": 2,
            })
        # First follow-ups (day 5)
        for c in db.get_followup_candidates(days_since_send=FOLLOWUP_DAYS)[:5]:
            fresh = db.get_contact(c["email"])
            if not fresh or fresh.get("status") != "sent":
                continue
            output["followup_contacts"].append({
                "email":        c["email"],
                "name":         c.get("name", ""),
                "sent_subject": c.get("sent_subject", ""),
                "followup_num": 1,
            })

    # --- Initial sends (YouTube-floor enforced + type-weighted) ---
    batch_size = scheduler.compute_batch_size()
    if batch_size > 0:
        _active_types = {t for t, w in CONTACT_TYPE_WEIGHTS.items() if w > 0}
        verified_all  = db.get_contacts_by_status("verified", limit=100)
        verified_all  = [c for c in verified_all if c.get("type") in _active_types]

        researched   = [c for c in verified_all if c.get("research_done") == 1]
        unresearched = [c for c in verified_all if c.get("research_done") != 1]

        small_researched   = [c for c in researched   if c.get("playlist_size") == "small"]
        small_unresearched = [c for c in unresearched if c.get("playlist_size") == "small"]
        small_pool = (
            _weighted_order_with_youtube_floor(small_researched)
            + _weighted_order_with_youtube_floor(small_unresearched)
        )
        small_contacts = small_pool[:SMALL_PLAYLIST_PER_CYCLE]
        small_emails   = {c["email"] for c in small_contacts}

        rest_researched   = [c for c in researched   if c["email"] not in small_emails]
        rest_unresearched = [c for c in unresearched if c["email"] not in small_emails]
        rest_pool = (
            _weighted_order_with_youtube_floor(rest_researched)
            + _weighted_order_with_youtube_floor(rest_unresearched)
        )

        ordered = small_contacts + rest_pool
        batch_contacts = ordered[:batch_size]

        # Build learning context per type (one lookup per type)
        _learn_cache = {}
        for c in batch_contacts:
            ctype = c.get("type", "curator")
            if ctype not in _learn_cache:
                _learn_cache[ctype] = learning.get_learning_context_for_template(ctype)

        for c in batch_contacts:
            c_dict = {
                "email":            c["email"],
                "name":             c.get("name", ""),
                "type":             c.get("type", "curator"),
                "genre":            c.get("genre", ""),
                "notes":            c.get("notes", ""),
                "research_notes":   c.get("research_notes", ""),
                "recommended_tracks": template_engine._get_track_recs(
                    c.get("type", "curator"),
                    c.get("genre", ""),
                    c.get("notes", ""),
                ),
            }
            output["send_contacts"].append(c_dict)

    # --- Threads to check ---
    with db.get_conn() as conn:
        open_threads = conn.execute("""
            SELECT email, gmail_thread_id as thread_url
            FROM contacts
            WHERE status IN ('sent','followup_sent')
              AND gmail_thread_id IS NOT NULL
              AND gmail_thread_id != ''
            ORDER BY date_sent DESC
            LIMIT 30
        """).fetchall()
    output["threads_to_check"] = [dict(r) for r in open_threads]

    print(json.dumps(output, indent=2))


def cmd_gmail_url():
    """
    Read JSON from stdin: {"email":"..","subject":"..","body":".."}
    Output a properly URL-encoded Gmail compose URL.
    Handles all special characters, newlines, Unicode safely.
    """
    data = json.loads(sys.stdin.read())
    email   = data["email"]
    subject = data["subject"]
    body    = data["body"]
    url = (
        "https://mail.google.com/mail/u/0/?view=cm"
        + "&to="   + quote(email,   safe="")
        + "&su="   + quote(subject, safe="")
        + "&body=" + quote(body,    safe="")
    )
    print(url)


def cmd_skip_inactive_types():
    """Mark all verified contacts whose type is not active (weight=0 or not in config) as 'skip'."""
    active_types = {t for t, w in CONTACT_TYPE_WEIGHTS.items() if w > 0}
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT email, type FROM contacts WHERE status='verified'"
        ).fetchall()
        skipped = []
        for row in rows:
            if row["type"] not in active_types:
                conn.execute(
                    "UPDATE contacts SET status='skip' WHERE email=?",
                    (row["email"],)
                )
                skipped.append((row["email"], row["type"]))
    print(f"Marked {len(skipped)} inactive-type contacts as 'skip':")
    for email, ctype in skipped:
        print(f"  [{ctype}] {email}")


def main():
    args = sys.argv[1:]
    if not args or args[0] == "plan":
        cmd_plan()
    elif args[0] == "contacts":
        cmd_contacts()
    elif args[0] == "gmail_url":
        cmd_gmail_url()
    elif args[0] == "status":
        cmd_status()
    elif args[0] == "skip_inactive":
        cmd_skip_inactive_types()
    elif args[0] == "mark_sent" and len(args) >= 3:
        cmd_mark_sent(args[1], args[2], args[3] if len(args) > 3 else "")
    elif args[0] == "mark_responded" and len(args) >= 2:
        cmd_mark_responded(args[1], args[2] if len(args) > 2 else "")
    elif args[0] == "mark_bounced" and len(args) >= 2:
        cmd_mark_bounced(args[1])
    elif args[0] == "mark_followup_sent" and len(args) >= 3:
        cmd_mark_followup_sent(args[1], args[2])
    elif args[0] == "mark_followup2_sent" and len(args) >= 3:
        cmd_mark_followup2_sent(args[1], args[2])
    elif args[0] == "add_contact" and len(args) >= 5:
        cmd_add_contact(args[1], args[2], args[3], args[4],
                        args[5] if len(args) > 5 else "",
                        args[6] if len(args) > 6 else "",
                        args[7] if len(args) > 7 else "",
                        args[8] if len(args) > 8 else "")
    elif args[0] == "set_playlist_size" and len(args) >= 3:
        cmd_set_playlist_size(args[1], args[2])
    elif args[0] == "store_research" and len(args) >= 3:
        cmd_store_research(args[1], args[2])
    elif args[0] == "pending_research":
        cmd_pending_research()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
