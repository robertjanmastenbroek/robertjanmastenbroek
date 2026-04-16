"""Regression: agent.cmd_run records a fleet_state heartbeat at cycle end."""
import pytest


def test_cmd_run_heartbeats(temp_db, mock_gmail, fake_claude, monkeypatch):
    import agent
    import fleet_state
    import scheduler

    # Neutralise the cycle so we don't touch network, sleep, or Claude CLI.
    monkeypatch.setattr(agent, "_verify_pending_contacts", lambda: 0)
    monkeypatch.setattr(agent, "_send_batch", lambda *a, **k: {"sent": 0, "failed": 0, "skipped": 0})

    class _ClosedWindow:
        can_send = False
        def status(self): return "closed"
        def record_send(self): pass
    monkeypatch.setattr(scheduler, "SendWindow", _ClosedWindow)
    monkeypatch.setattr(scheduler, "compute_batch_size", lambda: 0)
    monkeypatch.setattr(scheduler, "remaining_quota_today", lambda: 150)

    # Stub non-fatal steps so _run_step wraps return values only.
    import reply_detector, reply_classifier, reply_responder, followup_engine, learning
    monkeypatch.setattr(reply_detector, "run_full_inbox_check", lambda: {"checked": 0})
    monkeypatch.setattr(reply_classifier, "classify_pending", lambda: {"classified": 0})
    monkeypatch.setattr(reply_responder, "run", lambda dry_run=False: {"sent": 0})
    monkeypatch.setattr(learning, "maybe_generate_insights", lambda: None)
    monkeypatch.setattr(followup_engine, "run_followup_batch", lambda max_generates=0: {"followups_sent": 0, "followup2_sent": 0})

    agent.cmd_run()

    all_agents = fleet_state.get_all()
    names = [a["agent_name"] for a in all_agents]
    assert "outreach_cycle" in names, f"heartbeat missing; got {names}"

    hb = next(a for a in all_agents if a["agent_name"] == "outreach_cycle")
    assert hb["status"] == "ok"
    assert hb["run_count"] >= 1
