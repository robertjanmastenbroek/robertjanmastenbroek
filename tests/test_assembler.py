import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock, call
from pathlib import Path
from content_engine.assembler import (
    VARIANT_MAP, PLATFORMS, CLIP_LENGTHS,
    _find_best_audio_segment, _extract_caption, run_assembly,
)
from content_engine.types import TrendBrief, OpeningFrame, PromptWeights

BRIEF = TrendBrief(
    date="2026-04-12",
    top_visual_formats=["crowd ecstasy"],
    dominant_emotion="euphoric release",
    oversaturated="lo-fi chill",
    hook_pattern_of_day="contrast",
    contrarian_gap="silence",
    trend_confidence=0.8,
)

FRAME = OpeningFrame(
    clip_index=0, source="footage", source_file="perf.mp4",
    emotion_tag="euphoric", visual_category="performance", footage_score=8.5,
)

WEIGHTS = PromptWeights.defaults()


def test_variant_map_has_all_platforms():
    for clip_idx in range(3):
        for platform in PLATFORMS:
            assert platform in VARIANT_MAP[clip_idx]


def test_variant_assignment_alternates_clip_0():
    assert VARIANT_MAP[0]["instagram"] == "a"
    assert VARIANT_MAP[0]["youtube"]   == "b"


def test_variant_assignment_alternates_clip_1():
    assert VARIANT_MAP[1]["instagram"] == "b"
    assert VARIANT_MAP[1]["youtube"]   == "a"


def test_find_best_audio_segment_fallback_on_missing_file():
    start = _find_best_audio_segment("/nonexistent/track.wav", 15)
    assert start == 30.0


def test_extract_caption_from_nested_dict():
    captions = {15: {"instagram": {"caption": "This is the caption", "hashtags": "#techno"}}}
    result = _extract_caption(captions, 15, "instagram")
    assert result == "This is the caption"


def test_extract_caption_missing_returns_empty():
    result = _extract_caption({}, 15, "instagram")
    assert result == ""


def test_run_assembly_produces_six_clips(tmp_path):
    mock_frame = OpeningFrame(
        clip_index=0, source="footage", source_file=str(tmp_path / "v.mp4"),
        emotion_tag="euphoric", visual_category="performance", footage_score=8.0,
    )

    def mock_hooks(title):
        return {5: {"a": "hook a", "b": "hook b", "c": "hook c"},
                9: {"a": "hook a", "b": "hook b", "c": "hook c"},
                15: {"a": "hook a", "b": "hook b", "c": "hook c"}}

    def mock_captions(title, hooks_by_length, brief):
        return {5:  {"instagram": {"caption": "cap"}, "youtube": {"title": "t", "caption": "cap"}},
                9:  {"instagram": {"caption": "cap"}, "youtube": {"title": "t", "caption": "cap"}},
                15: {"instagram": {"caption": "cap"}, "youtube": {"title": "t", "caption": "cap"}}}

    with patch("content_engine.assembler._pick_audio",            return_value=("track.wav", "Jericho")), \
         patch("content_engine.assembler._generate_hooks",        side_effect=mock_hooks), \
         patch("content_engine.assembler._generate_captions",     side_effect=mock_captions), \
         patch("content_engine.assembler.visual_engine.pick_opening_frame", return_value=mock_frame), \
         patch("content_engine.assembler.build_clip",             return_value=str(tmp_path / "out.mp4")):
        clips = run_assembly(brief=BRIEF, weights=WEIGHTS, video_dirs=[str(tmp_path)], output_dir=str(tmp_path))

    assert len(clips) == 6

    platforms_seen = [c["platform"] for c in clips]
    for p in PLATFORMS:
        assert platforms_seen.count(p) == 3

    clip_indices = [c["clip_index"] for c in clips]
    for i in range(3):
        assert clip_indices.count(i) == 2


def test_run_assembly_result_has_required_keys(tmp_path):
    mock_frame = OpeningFrame(
        clip_index=0, source="footage", source_file="v.mp4",
        emotion_tag="euphoric", visual_category="b_roll", footage_score=7.5,
    )
    with patch("content_engine.assembler._pick_audio",            return_value=("track.wav", "Jericho")), \
         patch("content_engine.assembler._generate_hooks",        return_value={5: {"a": "h"}, 9: {"a": "h"}, 15: {"a": "h"}}), \
         patch("content_engine.assembler._generate_captions",     return_value={5: {}, 9: {}, 15: {}}), \
         patch("content_engine.assembler.visual_engine.pick_opening_frame", return_value=mock_frame), \
         patch("content_engine.assembler.build_clip",             return_value="out.mp4"):
        clips = run_assembly(brief=BRIEF, weights=WEIGHTS, video_dirs=[], output_dir=str(tmp_path))

    required_keys = {"clip_index", "platform", "variant", "path", "hook_text",
                     "caption", "hook_mechanism", "visual_type", "clip_length", "track_title"}
    for clip in clips:
        assert required_keys.issubset(set(clip.keys()))
