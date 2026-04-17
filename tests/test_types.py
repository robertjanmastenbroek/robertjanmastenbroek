import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from content_engine.types import TrendBrief, OpeningFrame, PerformanceRecord, PromptWeights


def test_trend_brief_defaults():
    b = TrendBrief(date="2026-04-12", top_visual_formats=[], dominant_emotion="euphoric",
                   oversaturated="lo-fi", hook_pattern_of_day="open with question",
                   contrarian_gap="silence as tension", trend_confidence=0.8)
    assert b.date == "2026-04-12"
    assert b.trend_confidence == 0.8


def test_opening_frame_source_types():
    f = OpeningFrame(clip_index=0, source="ai_generated", source_file="out.mp4",
                     emotion_tag="euphoric", visual_category="sacred_geometry", footage_score=9.0)
    assert f.source in ("ai_generated", "footage")


def test_prompt_weights_load_defaults():
    w = PromptWeights.defaults()
    assert w.hook_weights["tension"] > 0
    assert w.visual_weights["ai_generated"] > 0


def test_prompt_weights_save_load(tmp_path, monkeypatch):
    import content_engine.types as t
    monkeypatch.setattr(t, "PROJECT_DIR", tmp_path)
    w = PromptWeights.defaults()
    w.updated = "2026-04-12T06:00:00"
    w.save()
    loaded = PromptWeights.load()
    assert loaded.updated == "2026-04-12T06:00:00"
    assert loaded.hook_weights["tension"] == 1.0


def test_trend_brief_save_load(tmp_path, monkeypatch):
    import content_engine.types as t
    monkeypatch.setattr(t, "PROJECT_DIR", tmp_path)
    b = TrendBrief(date="2026-04-12", top_visual_formats=["crowd"], dominant_emotion="joy",
                   oversaturated="lo-fi", hook_pattern_of_day="contrast", contrarian_gap="silence",
                   trend_confidence=0.75)
    b.save()
    loaded = TrendBrief.load("2026-04-12")
    assert loaded.dominant_emotion == "joy"
    assert loaded.trend_confidence == 0.75


import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from content_engine.generator import pick_sub_mode

def test_pick_sub_mode_no_weights_returns_valid_mode():
    result = pick_sub_mode("emotional", {})
    assert result in ["COST", "NAMING", "DOUBT", "DEVOTION", "RUPTURE"]

def test_pick_sub_mode_weighted_favors_high_weight():
    weights = {"COST": 0.001, "NAMING": 0.001, "DOUBT": 0.001, "DEVOTION": 100.0, "RUPTURE": 0.001}
    results = [pick_sub_mode("emotional", weights) for _ in range(200)]
    assert results.count("DEVOTION") > 160, f"Expected DEVOTION to dominate, got {results.count('DEVOTION')}/200"

def test_pick_sub_mode_zero_weights_still_returns_valid():
    weights = {"COST": 0.0, "NAMING": 0.0, "DOUBT": 0.0, "DEVOTION": 0.0, "RUPTURE": 0.0}
    result = pick_sub_mode("emotional", weights)
    assert result in ["COST", "NAMING", "DOUBT", "DEVOTION", "RUPTURE"]

def test_pick_sub_mode_unknown_angle_uses_emotional_modes():
    result = pick_sub_mode("nonexistent_angle", {})
    assert result in ["COST", "NAMING", "DOUBT", "DEVOTION", "RUPTURE"]


from content_engine.pipeline import derive_format_mix
from content_engine.types import ClipFormat

def test_derive_format_mix_equal_weights_all_formats_possible():
    weights = {"transitional": 1.0, "emotional": 1.0, "performance": 1.0}
    seen = set()
    for _ in range(200):
        mix = derive_format_mix(weights)
        seen.update(mix)
    assert ClipFormat.TRANSITIONAL in seen
    assert ClipFormat.EMOTIONAL in seen
    assert ClipFormat.PERFORMANCE in seen

def test_derive_format_mix_dominant_weight_favors_format():
    weights = {"transitional": 10.0, "emotional": 0.01, "performance": 0.01}
    mixes = [derive_format_mix(weights) for _ in range(50)]
    transitional_counts = [m.count(ClipFormat.TRANSITIONAL) for m in mixes]
    assert sum(transitional_counts) / len(transitional_counts) > 1.5

def test_derive_format_mix_caps_at_two_per_format():
    weights = {"transitional": 999.0, "emotional": 0.0, "performance": 0.0}
    for _ in range(20):
        mix = derive_format_mix(weights)
        assert mix.count(ClipFormat.TRANSITIONAL) <= 2, "No format should occupy all 3 slots"

def test_derive_format_mix_returns_three_clips():
    weights = {"transitional": 1.0, "emotional": 1.0, "performance": 1.0}
    assert len(derive_format_mix(weights)) == 3
