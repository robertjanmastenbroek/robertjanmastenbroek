# content_engine/tests/test_types.py
import pytest
from content_engine.types import (
    TrendBrief, PromptWeights, PerformanceRecord, OpeningFrame,
    ClipFormat, TransitionalHook, TrackInfo, UnifiedWeights,
)


def test_clip_format_enum():
    assert ClipFormat.TRANSITIONAL.value == "transitional"
    assert ClipFormat.EMOTIONAL.value == "emotional"
    assert ClipFormat.PERFORMANCE.value == "performance"


def test_sacred_arc_in_clip_format():
    assert ClipFormat.SACRED_ARC.value == "sacred_arc"


def test_sacred_arc_in_unified_weights_defaults():
    w = UnifiedWeights.defaults()
    assert "sacred_arc" in w.format_weights
    assert w.format_weights["sacred_arc"] == 1.0


def test_transitional_hook_dataclass():
    hook = TransitionalHook(
        file="nature/lightning_01.mp4",
        category="nature",
        duration_s=3.2,
        last_used=None,
        performance_score=1.0,
        times_used=0,
    )
    assert hook.category == "nature"
    assert hook.performance_score == 1.0


def test_track_info_dataclass():
    track = TrackInfo(
        title="Jericho",
        file_path="/content/audio/masters/JERICHO_FINAL.wav",
        bpm=140,
        energy=0.8,
        danceability=0.7,
        valence=0.5,
        scripture_anchor="Joshua 6",
        spotify_id="abc123",
        spotify_popularity=45,
        pool_weight=1.0,
        entered_pool="2026-04-01",
    )
    assert track.bpm == 140
    assert track.scripture_anchor == "Joshua 6"


def test_unified_weights_defaults():
    w = UnifiedWeights.defaults()
    assert "transitional" in w.format_weights
    assert "emotional" in w.format_weights
    assert "performance" in w.format_weights
    assert w.format_weights["transitional"] == 1.0
    assert "nature" in w.transitional_category_weights
    assert len(w.track_weights) == 0


def test_unified_weights_save_load(tmp_path):
    w = UnifiedWeights.defaults()
    w.save(tmp_path / "weights.json")
    loaded = UnifiedWeights.load(tmp_path / "weights.json")
    assert loaded.format_weights == w.format_weights


def test_performance_record_expanded():
    rec = PerformanceRecord(
        post_id="123",
        platform="instagram",
        clip_index=0,
        variant="a",
        hook_mechanism="tension",
        visual_type="b_roll",
        clip_length=15,
        format_type="transitional",
        hook_template_id="save.if_heartbroken",
        hook_sub_mode="DEVOTION",
        transitional_category="nature",
        transitional_file="nature/lightning_01.mp4",
        track_title="Jericho",
    )
    assert rec.format_type == "transitional"
    assert rec.hook_template_id == "save.if_heartbroken"
