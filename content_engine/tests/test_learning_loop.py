# content_engine/tests/test_learning_loop.py
import json
import pytest
from pathlib import Path
from content_engine.learning_loop import (
    calculate_unified_weights,
    track_rotation_vote,
    update_template_lifecycle,
)
from content_engine.types import UnifiedWeights


def test_calculate_unified_weights():
    """Weights should update via EMA."""
    old = UnifiedWeights.defaults()
    records = [
        {
            "format_type": "transitional", "hook_template_id": "save.wait_for_drop",
            "hook_mechanism": "dare", "visual_type": "b_roll", "platform": "instagram",
            "transitional_category": "nature", "track_title": "Jericho",
            "completion_rate": 0.8, "save_rate": 0.05, "scroll_stop_rate": 0.3,
        },
        {
            "format_type": "emotional", "hook_template_id": "save.for_you_if",
            "hook_mechanism": "save", "visual_type": "phone", "platform": "youtube",
            "transitional_category": "", "track_title": "Renamed",
            "completion_rate": 0.6, "save_rate": 0.02, "scroll_stop_rate": 0.2,
        },
    ]
    new_weights = calculate_unified_weights(records, old)
    assert isinstance(new_weights, UnifiedWeights)
    assert new_weights.updated != ""


def test_track_rotation_vote():
    pool = [
        {"title": "Jericho", "spotify_popularity": 60, "video_save_rate": 0.05},
        {"title": "Renamed", "spotify_popularity": 40, "video_save_rate": 0.02},
        {"title": "Halleluyah", "spotify_popularity": 50, "video_save_rate": 0.03},
        {"title": "Fire In Our Hands", "spotify_popularity": 45, "video_save_rate": 0.01},
    ]
    new_release = {"title": "New Track", "spotify_popularity": 55, "video_save_rate": 0.04}
    result = track_rotation_vote(pool, new_release, min_days=0)
    assert "action" in result
    assert result["action"] in ("swap", "keep", "add")


def test_update_template_lifecycle():
    template_scores = {
        "save.wait_for_drop": 2.0,   # top performer
        "save.for_you_if": 0.3,      # bottom performer
        "save.pov_driving": 1.0,     # neutral
    }
    result = update_template_lifecycle(template_scores, days_active=15)
    assert result["save.wait_for_drop"]["priority"] == 2.0
    assert result["save.for_you_if"]["priority"] == 0.3
