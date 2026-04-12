"""
RJM Outreach Agent — Learning Engine (Claude CLI edition)

Analyses what's working and feeds insights back into future email generation.
Uses the Claude CLI (your Max plan) — no separate API billing.
"""

import json
import logging
import re

import db
from config import LEARNING_REPORT_AFTER_N_REPLIES, MIN_SENDS_FOR_STATS, CLAUDE_MODEL_FAST
from template_engine import _call_claude

try:
    import events as _events
    _HIVE_AVAILABLE = True
except ImportError:
    _HIVE_AVAILABLE = False

log = logging.getLogger("outreach.learning")


def get_learning_context_for_template(contact_type: str) -> str:
    """
    Returns formatted insights to inject into email generation prompts.
    Called by template_engine.generate_email().
    """
    insights = db.get_recent_insights(limit=3)
    stats    = db.get_template_stats()

    context_parts = []

    relevant_stats = [
        s for s in stats
        if s["contact_type"] == contact_type and s["total_sent"] >= MIN_SENDS_FOR_STATS
    ]
    if relevant_stats:
        context_parts.append("WHAT'S WORKING:")
        for s in relevant_stats[:3]:
            context_parts.append(
                f"  • Template '{s['template_type']}' for {s['contact_type']}: "
                f"{s['reply_rate']}% reply rate ({s['total_replies']}/{s['total_sent']} replies)"
            )

    if insights:
        context_parts.append("\nRECENT PATTERN INSIGHTS:")
        for insight in insights:
            context_parts.append(f"  • {insight['content']}")

    return "\n".join(context_parts) if context_parts else ""


def maybe_generate_insights() -> bool:
    """
    Check if we have enough new replies to generate a fresh insight report.
    Returns True if insights were generated.
    """
    recent_insights = db.get_recent_insights(limit=1)
    last_insight_ts = recent_insights[0]["generated_at"] if recent_insights else "2000-01-01"

    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT type, template_type, sent_subject, response_snippet, date_response_received
            FROM contacts
            WHERE status = 'responded'
              AND response_snippet IS NOT NULL
              AND response_snippet != ''
              AND date_response_received > ?
            ORDER BY date_response_received DESC
        """, (last_insight_ts,)).fetchall()
        responses = [dict(r) for r in rows]

    if len(responses) < LEARNING_REPORT_AFTER_N_REPLIES:
        log.debug("Not enough new replies for insights (%d/%d)",
                  len(responses), LEARNING_REPORT_AFTER_N_REPLIES)
        return False

    log.info("Generating insights from %d new replies...", len(responses))
    _run_insight_analysis(responses)
    return True


def _run_insight_analysis(responses: list[dict]):
    """Ask Claude to analyse reply patterns and extract actionable insights."""
    response_data = "\n\n".join([
        f"Contact type: {r['type']}\n"
        f"Template: {r['template_type'] or 'unknown'}\n"
        f"Subject sent: {r['sent_subject'] or 'unknown'}\n"
        f"Reply snippet: {r['response_snippet']}"
        for r in responses[:20]
    ])

    prompt = f"""Analyse these replies from a cold email outreach campaign (artist to labels/curators/podcasts/festivals).
Each block = one reply:

{response_data}

Identify up to 5 actionable insights: what openers/angles got engagement, which contact types responded, subject line patterns, what replies asked for.
Each insight: max 2 sentences, specific and actionable.
Return ONLY a JSON array of strings. No markdown."""

    try:
        raw = _call_claude(prompt, model=CLAUDE_MODEL_FAST)
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            log.warning("Could not parse insights JSON: %s", raw[:200])
            return

        insights_list = json.loads(match.group(0))
        for insight in insights_list[:5]:
            db.save_insight(
                insight_type="pattern",
                content=insight,
                based_on_n=len(responses),
            )
            log.info("Saved insight: %s", insight[:80])
            if _HIVE_AVAILABLE:
                _events.publish("template.insight_generated", "learning", {
                    "based_on_n": len(responses),
                })

    except Exception as exc:
        log.error("Insight generation failed: %s", exc)


def run_subject_line_analysis():
    """Identify which subject line patterns correlate with replies."""
    with db.get_conn() as conn:
        replied = conn.execute(
            "SELECT sent_subject FROM contacts WHERE status = 'responded' AND sent_subject IS NOT NULL"
        ).fetchall()
        all_sent = conn.execute(
            "SELECT sent_subject FROM contacts WHERE sent_subject IS NOT NULL"
        ).fetchall()

    replied_subjects = [r["sent_subject"] for r in replied if r["sent_subject"]]
    all_subjects     = [r["sent_subject"] for r in all_sent  if r["sent_subject"]]

    if len(replied_subjects) < 5:
        return

    prompt = f"""Analyse these email subject lines from an outreach campaign.

SUBJECTS THAT GOT REPLIES ({len(replied_subjects)} total):
{chr(10).join(f'  • {s}' for s in replied_subjects[:20])}

ALL SUBJECTS SENT ({len(all_subjects)} total):
{chr(10).join(f'  • {s}' for s in all_subjects[:40])}

What patterns make subject lines more likely to get a reply?
Give 2-3 actionable rules for writing subject lines for this artist's outreach.
Return ONLY a JSON array of rule strings. No markdown."""

    try:
        raw = _call_claude(prompt, model=CLAUDE_MODEL_FAST)
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            rules = json.loads(match.group(0))
            for rule in rules[:3]:
                db.save_insight(
                    insight_type="subject_line_winner",
                    content=rule,
                    based_on_n=len(replied_subjects),
                )
                log.info("Subject line insight: %s", rule[:80])
    except Exception as exc:
        log.error("Subject line analysis failed: %s", exc)


def print_performance_report():
    """Print a human-readable performance report to stdout."""
    summary  = db.get_pipeline_summary()
    stats    = db.get_template_stats()
    insights = db.get_recent_insights(limit=5)

    print("\n" + "=" * 60)
    print("  RJM OUTREACH AGENT — PERFORMANCE REPORT")
    print("=" * 60)

    print("\n  PIPELINE STATUS:")
    for k, v in summary.items():
        if not k.startswith("_"):
            print(f"    {k:<20} {v}")
    print(f"    {'Today sent':<20} {summary.get('_today_sent', 0)}")
    print(f"    {'Reply rate':<20} {summary.get('_reply_rate', '—')}")

    if stats:
        print("\n  TEMPLATE PERFORMANCE:")
        print(f"  {'Template':<20} {'Type':<12} {'Sent':>6} {'Replies':>8} {'Rate':>7}")
        print("  " + "-" * 56)
        for s in stats:
            rate = f"{s['reply_rate']}%" if s["reply_rate"] else "—"
            print(f"  {(s['template_type'] or '?'):<20} {s['contact_type']:<12} "
                  f"{s['total_sent']:>6} {s['total_replies']:>8} {rate:>7}")

    if insights:
        print("\n  RECENT INSIGHTS:")
        for i in insights:
            print(f"    [{i['insight_type']}] {i['content']}")

    print()
