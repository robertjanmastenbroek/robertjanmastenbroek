"""
Tests for scheduler.plan_week — rotation + BPM tier spread + priority math.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from content_engine.youtube_longform import scheduler


def test_plan_week_produces_three_slots_fresh_registry(monkeypatch, tmp_path):
    """With empty registry, the scheduler should fill all 3 weekly slots."""
    monkeypatch.setattr(
        "content_engine.youtube_longform.registry.REGISTRY_FILE",
        tmp_path / "empty.jsonl",
    )
    # Fixed "now" for deterministic test (Monday 2026-04-20)
    fake_now = datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc)
    plans = scheduler.plan_week(now=fake_now)
    assert len(plans) == 3
    titles = {p.track_title for p in plans}
    assert len(titles) == 3, f"Duplicates in weekly schedule: {titles}"


def test_plan_week_respects_rotation_lock(monkeypatch, tmp_path):
    """Tracks published within MIN_DAYS_BETWEEN_SAME_TRACK must be excluded."""
    registry_path = tmp_path / "reg.jsonl"
    monkeypatch.setattr(
        "content_engine.youtube_longform.registry.REGISTRY_FILE",
        registry_path,
    )
    # Publish Jericho 3 days ago — should be excluded
    fake_now = datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc)
    recent_ts = (fake_now.replace(day=17)).isoformat().replace("+00:00", "Z")
    registry_path.write_text(
        '{"track_title": "Jericho", "youtube_id": "abc", "error": null, '
        f'"dry_run": false, "timestamp": "{recent_ts}"}}\n'
    )
    plans = scheduler.plan_week(now=fake_now)
    picked = {p.track_title.lower() for p in plans}
    assert "jericho" not in picked, f"Jericho should be locked out for 45 days; got {picked}"


def test_plan_week_prefers_bpm_tier_spread(monkeypatch, tmp_path):
    """Three slots should distribute across BPM tiers, not all ecstatic."""
    monkeypatch.setattr(
        "content_engine.youtube_longform.registry.REGISTRY_FILE",
        tmp_path / "empty.jsonl",
    )
    fake_now = datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc)
    plans = scheduler.plan_week(now=fake_now)
    tiers = {scheduler._tier_for_bpm(p.bpm) for p in plans}
    # At least 2 distinct BPM tiers out of 3 slots
    assert len(tiers) >= 2, f"Insufficient tier spread: {tiers}"


def test_format_schedule_handles_empty_list():
    """Empty schedule should format cleanly."""
    out = scheduler.format_schedule([])
    assert "no tracks" in out.lower()
