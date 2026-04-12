import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock, call
from content_engine.visual_engine import build_prompt, generate_clip, pick_opening_frame
from content_engine.types import TrendBrief, OpeningFrame

BRIEF = TrendBrief(
    date="2026-04-12",
    top_visual_formats=["crowd ecstasy", "sacred geometry", "aerial rave"],
    dominant_emotion="euphoric release",
    oversaturated="lo-fi chill",
    hook_pattern_of_day="contrast then drop",
    contrarian_gap="silence before the beat",
    trend_confidence=0.8,
)


def test_build_prompt_contains_emotion():
    prompt = build_prompt(BRIEF, clip_index=0)
    assert "euphoric" in prompt.lower()


def test_build_prompt_contains_visual_format():
    prompt = build_prompt(BRIEF, clip_index=0)
    assert "crowd ecstasy" in prompt


def test_build_prompt_mentions_oversaturated_as_avoid():
    prompt = build_prompt(BRIEF, clip_index=0)
    assert "lo-fi chill" in prompt.lower()
    assert "avoid" in prompt.lower()


def test_build_prompt_cycles_concepts():
    p0 = build_prompt(BRIEF, clip_index=0)
    p1 = build_prompt(BRIEF, clip_index=1)
    p2 = build_prompt(BRIEF, clip_index=2)
    # All three should be different (different clip concepts)
    assert p0 != p1
    assert p1 != p2


def test_generate_clip_raises_without_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("RUNWAY_API_KEY", raising=False)
    import pytest
    with pytest.raises(RuntimeError, match="RUNWAY_API_KEY"):
        generate_clip("some prompt", "2026-04-12", 0)


def test_generate_clip_returns_path_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNWAY_API_KEY", "fake_key")

    mock_submit = MagicMock(status_code=200)
    mock_submit.json.return_value = {"id": "task_abc123"}

    mock_poll = MagicMock(status_code=200)
    mock_poll.json.return_value = {"status": "SUCCEEDED", "output": ["https://example.com/video.mp4"]}

    mock_video = MagicMock(status_code=200)
    mock_video.iter_content = MagicMock(return_value=[b"fake_video_data"])

    import content_engine.visual_engine as ve
    monkeypatch.setattr(ve, "PROJECT_DIR", tmp_path)

    with patch("content_engine.visual_engine.requests.post", return_value=mock_submit), \
         patch("content_engine.visual_engine.requests.get", side_effect=[mock_poll, mock_video]), \
         patch("content_engine.visual_engine.time.sleep"):
        path = generate_clip("euphoric sacred geometry", "2026-04-12", 0)

    assert path.endswith(".mp4")
    assert "ai_clip_0" in path


def test_generate_clip_raises_on_runway_failure(monkeypatch):
    monkeypatch.setenv("RUNWAY_API_KEY", "fake_key")
    mock_fail = MagicMock(status_code=500)
    mock_fail.text = "Internal Server Error"
    with patch("content_engine.visual_engine.requests.post", return_value=mock_fail):
        import pytest
        with pytest.raises(RuntimeError, match="Runway submission failed"):
            generate_clip("some prompt", "2026-04-12", 0)


def test_pick_opening_frame_uses_footage_when_score_high(tmp_path):
    with patch("content_engine.visual_engine.footage_scorer.build_candidate_list",
               return_value=[{"path": "good.mp4", "last_used_days": 20, "category": "performance"}]), \
         patch("content_engine.visual_engine.footage_scorer.pick_best_opening_frame",
               return_value=("good.mp4", 8.5)):
        frame = pick_opening_frame(BRIEF, clip_index=0, video_dirs=[str(tmp_path)])
    assert frame.source == "footage"
    assert frame.source_file == "good.mp4"
    assert frame.footage_score == 8.5


def test_pick_opening_frame_generates_when_score_low(tmp_path):
    with patch("content_engine.visual_engine.footage_scorer.build_candidate_list",
               return_value=[{"path": "bad.mp4", "last_used_days": 1, "category": "b_roll"}]), \
         patch("content_engine.visual_engine.footage_scorer.pick_best_opening_frame",
               return_value=("bad.mp4", 3.0)), \
         patch("content_engine.visual_engine.generate_clip",
               return_value=str(tmp_path / "gen.mp4")) as mock_gen:
        frame = pick_opening_frame(BRIEF, clip_index=1, video_dirs=[str(tmp_path)])
    assert frame.source == "ai_generated"
    assert frame.visual_category == "ai_generated"
    mock_gen.assert_called_once()


def test_pick_opening_frame_generates_when_no_footage(tmp_path):
    with patch("content_engine.visual_engine.footage_scorer.build_candidate_list", return_value=[]), \
         patch("content_engine.visual_engine.footage_scorer.pick_best_opening_frame",
               return_value=("", 0.0)), \
         patch("content_engine.visual_engine.generate_clip",
               return_value=str(tmp_path / "gen.mp4")):
        frame = pick_opening_frame(BRIEF, clip_index=0, video_dirs=[str(tmp_path)])
    assert frame.source == "ai_generated"
