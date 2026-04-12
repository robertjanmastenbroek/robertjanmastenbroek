import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from content_engine.trend_scanner import (
    scrape_youtube_trending,
    scrape_spotify_featured,
    synthesize_brief,
    run,
)
from content_engine.types import TrendBrief


def test_scrape_youtube_trending_returns_list():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"items": [
        {"snippet": {"title": "Tribal Techno Mix 2026", "categoryId": "10"}},
        {"snippet": {"title": "Melodic Psytrance Set", "categoryId": "10"}},
    ]}
    with patch("content_engine.trend_scanner.requests.get", return_value=mock_resp):
        results = scrape_youtube_trending("fake_key")
    assert isinstance(results, list)
    assert len(results) == 2
    assert "Tribal Techno Mix 2026" in results


def test_scrape_youtube_trending_empty_on_no_key():
    results = scrape_youtube_trending("")
    assert results == []


def test_scrape_spotify_featured_returns_list():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"playlists": {"items": [
        {"name": "Tribal Heat", "description": "Deep rhythmic energy"},
        {"name": "Morning Meditation", "description": "Calm focus"},
    ]}}
    with patch("content_engine.trend_scanner.requests.get", return_value=mock_resp):
        results = scrape_spotify_featured("Bearer token")
    assert isinstance(results, list)
    assert len(results) == 2
    assert "Tribal Heat" in results[0]


def test_scrape_spotify_empty_on_no_token():
    results = scrape_spotify_featured("")
    assert results == []


def test_synthesize_brief_returns_trend_brief():
    youtube_data = ["Tribal Techno Mix", "Psytrance Journey"]
    spotify_data = ["Tribal Heat playlist", "Deep Focus"]
    with patch("content_engine.trend_scanner._call_claude") as mock_claude:
        mock_claude.return_value = json.dumps({
            "top_visual_formats": ["crowd ecstasy", "sacred geometry", "desert rave"],
            "dominant_emotion": "euphoric release",
            "oversaturated": "lo-fi chill",
            "hook_pattern_of_day": "open with contrast",
            "contrarian_gap": "silence before the drop",
            "trend_confidence": 0.78,
        })
        brief = synthesize_brief("2026-04-12", youtube_data, spotify_data)
    assert isinstance(brief, TrendBrief)
    assert brief.dominant_emotion == "euphoric release"
    assert 0 < brief.trend_confidence <= 1.0
    assert len(brief.top_visual_formats) == 3


def test_synthesize_brief_handles_json_with_preamble():
    """Claude sometimes adds text before the JSON."""
    with patch("content_engine.trend_scanner._call_claude") as mock_claude:
        mock_claude.return_value = 'Here is the analysis:\n' + json.dumps({
            "top_visual_formats": ["x", "y", "z"],
            "dominant_emotion": "primal",
            "oversaturated": "chill",
            "hook_pattern_of_day": "contrast",
            "contrarian_gap": "silence",
            "trend_confidence": 0.6,
        })
        brief = synthesize_brief("2026-04-12", [], [])
    assert brief.dominant_emotion == "primal"


def test_run_saves_brief_json(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    monkeypatch.setenv("SPOTIFY_ACCESS_TOKEN", "")
    import content_engine.trend_scanner as ts
    import content_engine.types as t
    monkeypatch.setattr(ts, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(t, "PROJECT_DIR", tmp_path)
    with patch("content_engine.trend_scanner.synthesize_brief") as mock_synth:
        mock_synth.return_value = TrendBrief(
            date="2026-04-12", top_visual_formats=["x"], dominant_emotion="euphoric",
            oversaturated="lo-fi", hook_pattern_of_day="contrast", contrarian_gap="silence",
            trend_confidence=0.8,
        )
        result = run(date_str="2026-04-12")
    assert isinstance(result, TrendBrief)
    brief_file = tmp_path / "data" / "trend_brief" / "2026-04-12.json"
    assert brief_file.exists()
