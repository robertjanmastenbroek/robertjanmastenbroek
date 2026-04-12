import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from content_engine.footage_scorer import (
    score_clip, pick_best_opening_frame, build_candidate_list, SCORE_THRESHOLD,
    _get_freshness_score,
)


def test_score_threshold_is_float():
    assert isinstance(SCORE_THRESHOLD, float)
    assert SCORE_THRESHOLD > 0


def test_freshness_score_max_at_14_days():
    assert _get_freshness_score(14) == 10.0
    assert _get_freshness_score(30) == 10.0


def test_freshness_score_penalises_recent():
    assert _get_freshness_score(1) < _get_freshness_score(7)
    assert _get_freshness_score(0) < 1.0


def test_score_clip_returns_float_in_range():
    with patch("content_engine.footage_scorer._get_motion_score",   return_value=7.0), \
         patch("content_engine.footage_scorer._get_contrast_score",  return_value=8.0), \
         patch("content_engine.footage_scorer._get_freshness_score", return_value=9.0), \
         patch("content_engine.footage_scorer._get_emotion_match",   return_value=6.0):
        score = score_clip("fake.mp4", dominant_emotion="euphoric", last_used_days=5)
    assert isinstance(score, float)
    assert 0 <= score <= 10


def test_score_clip_penalises_recent_use():
    with patch("content_engine.footage_scorer._get_motion_score",   return_value=8.0), \
         patch("content_engine.footage_scorer._get_contrast_score",  return_value=8.0), \
         patch("content_engine.footage_scorer._get_emotion_match",   return_value=8.0):
        score_recent = score_clip("fake.mp4", "euphoric", last_used_days=0)
        score_old    = score_clip("fake.mp4", "euphoric", last_used_days=30)
    assert score_old > score_recent


def test_pick_best_returns_highest_scorer():
    candidates = [
        {"path": "a.mp4", "last_used_days": 30, "category": "performance"},
        {"path": "b.mp4", "last_used_days": 1,  "category": "b_roll"},
        {"path": "c.mp4", "last_used_days": 20, "category": "phone"},
    ]
    scores = {"a.mp4": 8.5, "b.mp4": 3.0, "c.mp4": 6.0}
    with patch("content_engine.footage_scorer.score_clip",
               side_effect=lambda p, e, **kw: scores[p]):
        best_path, best_score = pick_best_opening_frame(candidates, dominant_emotion="euphoric")
    assert best_path == "a.mp4"
    assert best_score == 8.5


def test_pick_best_empty_candidates_returns_empty():
    path, score = pick_best_opening_frame([], "euphoric")
    assert path == ""
    assert score == 0.0


def test_build_candidate_list_skips_missing_dirs():
    candidates = build_candidate_list(["/nonexistent/path/abc"])
    assert candidates == []


def test_build_candidate_list_finds_videos(tmp_path):
    vid = tmp_path / "performances" / "test.mp4"
    vid.parent.mkdir()
    vid.write_bytes(b"fake")
    candidates = build_candidate_list([str(tmp_path / "performances")])
    assert len(candidates) == 1
    assert candidates[0]["category"] == "performances"
    assert candidates[0]["last_used_days"] == 30.0  # never used
