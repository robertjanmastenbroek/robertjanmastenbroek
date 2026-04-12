# tests/test_brand_gate.py
import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'outreach_agent'))

import brand_gate


def test_validate_passes_visual_content():
    """Visualization test: concrete sensory language passes."""
    result = brand_gate.validate_content(
        "The dust on the synth, 126 BPM tribal rhythm — Jericho drops at midnight."
    )
    assert result["passes"] is True
    assert result["score"] >= 3


def test_validate_fails_generic_content():
    """Generic marketing-speak fails brand gate."""
    result = brand_gate.validate_content(
        "Really cool music with an amazing spiritual vibe for everyone."
    )
    assert result["passes"] is False
    assert len(result["flags"]) >= 2


def test_validate_returns_improved_suggestion_on_fail():
    """Failed validation returns a non-empty suggestion string."""
    result = brand_gate.validate_content("Amazing music, very spiritual and cool.")
    assert result["passes"] is False
    assert isinstance(result["suggestion"], str)
    assert len(result["suggestion"]) > 10


def test_validate_passes_falsifiable_content():
    """Falsifiable facts (BPM, track name, scripture) pass."""
    result = brand_gate.validate_content(
        "Living Water — 124 BPM melodic techno. John 4."
    )
    assert result["passes"] is True


def test_score_range():
    """Score is always 0–5."""
    r = brand_gate.validate_content("test")
    assert 0 <= r["score"] <= 5


def test_gate_or_warn_never_raises_and_returns_text_unchanged(capsys):
    text = "Amazing music with incredible spiritual vibes for everyone."
    result = brand_gate.gate_or_warn(text, context="test")
    assert result == text           # text is never modified
    captured = capsys.readouterr()
    assert captured.out == ""       # nothing on stdout
    assert "WARN" in captured.err   # warning goes to stderr
