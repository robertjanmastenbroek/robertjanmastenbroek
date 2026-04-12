import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from content_engine.learning_loop import (
    fetch_instagram_metrics, fetch_youtube_metrics,
    calculate_new_weights, detect_outliers, run,
)
from content_engine.types import PerformanceRecord, PromptWeights


def _rec(**kwargs) -> PerformanceRecord:
    defaults = dict(
        post_id="p1", platform="instagram", clip_index=0, variant="a",
        hook_mechanism="tension", visual_type="ai_generated", clip_length=15,
        views=1000, completion_rate=0.35, scroll_stop_rate=0.08,
        share_rate=0.02, save_rate=0.05, recorded_at="2026-04-12T18:00:00",
    )
    return PerformanceRecord(**{**defaults, **kwargs})


def test_fetch_instagram_metrics_returns_records():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"data": [
        {"name": "plays",  "values": [{"value": 5000}]},
        {"name": "saved",  "values": [{"value": 250}]},
        {"name": "shares", "values": [{"value": 100}]},
        {"name": "reach",  "values": [{"value": 62500}]},
    ]}
    posts = [{"post_id": "post1", "clip_index": 0, "variant": "a",
              "hook_mechanism": "tension", "visual_type": "ai_generated", "clip_length": 15}]
    with patch("content_engine.learning_loop.requests.get", return_value=mock_resp):
        records = fetch_instagram_metrics(posts, access_token="tok")
    assert len(records) == 1
    assert records[0].platform == "instagram"
    assert records[0].views == 5000
    assert records[0].save_rate == round(250 / 5000, 4)


def test_fetch_instagram_metrics_skips_bad_status():
    mock_resp = MagicMock(status_code=400)
    posts = [{"post_id": "bad", "clip_index": 0, "variant": "a",
              "hook_mechanism": "tension", "visual_type": "b_roll", "clip_length": 5}]
    with patch("content_engine.learning_loop.requests.get", return_value=mock_resp):
        records = fetch_instagram_metrics(posts, access_token="tok")
    assert records == []


def test_calculate_new_weights_boosts_winning_hook():
    records = [
        _rec(hook_mechanism="tension",  completion_rate=0.45, save_rate=0.08),
        _rec(hook_mechanism="tension",  completion_rate=0.42, save_rate=0.07),
        _rec(hook_mechanism="identity", completion_rate=0.10, save_rate=0.01),
        _rec(hook_mechanism="identity", completion_rate=0.12, save_rate=0.01),
    ]
    old = PromptWeights.defaults()
    new = calculate_new_weights(records, old)
    assert new.hook_weights["tension"] > new.hook_weights["identity"]


def test_calculate_new_weights_boosts_winning_visual():
    records = [
        _rec(visual_type="ai_generated", completion_rate=0.50, scroll_stop_rate=0.15),
        _rec(visual_type="ai_generated", completion_rate=0.48, scroll_stop_rate=0.14),
        _rec(visual_type="b_roll",       completion_rate=0.05, scroll_stop_rate=0.02),
    ]
    old = PromptWeights.defaults()
    new = calculate_new_weights(records, old)
    assert new.visual_weights["ai_generated"] > new.visual_weights["b_roll"]


def test_calculate_new_weights_no_records_returns_unchanged():
    old = PromptWeights.defaults()
    new = calculate_new_weights([], old)
    assert new.hook_weights == old.hook_weights
    assert new.visual_weights == old.visual_weights


def test_detect_outliers_flags_2x_average():
    records = [_rec(views=v) for v in [1000, 1100, 950, 8000, 900]]
    outliers = detect_outliers(records)
    assert len(outliers) == 1
    assert outliers[0].views == 8000


def test_detect_outliers_no_outliers():
    records = [_rec(views=v) for v in [1000, 1050, 980, 1100, 990]]
    outliers = detect_outliers(records)
    assert outliers == []


def test_detect_outliers_empty():
    assert detect_outliers([]) == []


def test_run_returns_weights_when_no_registry(tmp_path, monkeypatch):
    import content_engine.learning_loop as ll
    import content_engine.types as t
    monkeypatch.setattr(ll, "PERFORMANCE_DIR", tmp_path / "perf")
    monkeypatch.setattr(ll, "LEARNING_DIR",    tmp_path / "learn")
    monkeypatch.setattr(t,  "PROJECT_DIR",     tmp_path)

    # No registry file → should return defaults
    (tmp_path).mkdir(exist_ok=True)
    PromptWeights.defaults().save()
    monkeypatch.setattr(t, "PROJECT_DIR", tmp_path)

    result = run(date_str="2026-04-12")
    assert isinstance(result, PromptWeights)


def test_run_updates_weights_with_records(tmp_path, monkeypatch):
    import content_engine.learning_loop as ll
    import content_engine.types as t
    monkeypatch.setattr(ll, "PERFORMANCE_DIR", tmp_path / "perf")
    monkeypatch.setattr(ll, "LEARNING_DIR",    tmp_path / "learn")
    monkeypatch.setattr(t,  "PROJECT_DIR",     tmp_path)
    PromptWeights.defaults().save()

    registry = [
        {"post_id": "ig1", "platform": "instagram", "clip_index": 0, "variant": "a",
         "hook_mechanism": "tension", "visual_type": "ai_generated", "clip_length": 15},
    ]
    mock_records = [_rec(hook_mechanism="tension", completion_rate=0.5, save_rate=0.1)]

    with patch("content_engine.learning_loop.fetch_instagram_metrics", return_value=mock_records), \
         patch.dict(os.environ, {"INSTAGRAM_ACCESS_TOKEN": "tok"}):
        result = run(date_str="2026-04-12", post_registry=registry)

    assert isinstance(result, PromptWeights)
    assert result.updated != ""
    assert (tmp_path / "perf" / "2026-04-12.json").exists()
