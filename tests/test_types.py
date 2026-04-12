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
