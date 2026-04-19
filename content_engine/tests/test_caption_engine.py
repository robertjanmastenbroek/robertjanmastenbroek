"""Tests for caption_engine — kinetic text timing + punch-in."""
from content_engine.caption_engine import (
    CAPTION_MIN_WORD_DURATION,
    CAPTION_MAX_WORD_DURATION,
    _compute_windows,
    _split_to_word_groups,
)


def test_max_word_duration_is_snappy():
    """Captions must not linger more than 0.35s per word group."""
    assert CAPTION_MAX_WORD_DURATION <= 0.35, (
        f"CAPTION_MAX_WORD_DURATION={CAPTION_MAX_WORD_DURATION} is too slow. "
        "2026 viral benchmark is 180-250ms/word; cap at 0.35."
    )


def test_min_word_duration_allows_fast_words():
    """Min word duration should allow ≤200ms for on-beat fast pacing."""
    assert CAPTION_MIN_WORD_DURATION <= 0.20


def test_compute_windows_respects_max():
    """Even with sparse beats, no window should exceed 3×MAX."""
    groups = ["HOLD", "THE", "LINE"]
    beats = [0.0, 2.0, 4.0, 6.0]  # sparse: 2s apart
    windows = _compute_windows(groups, beats, total_duration=6.0, start_offset=0.0)
    for s, e in windows:
        assert (e - s) <= CAPTION_MAX_WORD_DURATION * 3 + 0.01, (
            f"window {(s, e)} exceeds 3×MAX ({CAPTION_MAX_WORD_DURATION*3}s)"
        )


def test_punch_in_scale_interpolation():
    """Punch-in helper must go scale 1.12 → 1.0 over 0-100ms, then stay at 1.0."""
    from content_engine.caption_engine import _punch_in_scale

    assert abs(_punch_in_scale(0.0) - 1.12) < 0.001
    assert 1.05 < _punch_in_scale(0.05) < 1.07
    assert abs(_punch_in_scale(0.10) - 1.0) < 0.001
    assert _punch_in_scale(0.5) == 1.0


def test_punch_in_alpha_interpolation():
    """Alpha must ramp 0 → 255 over 0-100ms, then stay at 255."""
    from content_engine.caption_engine import _punch_in_alpha

    assert _punch_in_alpha(0.0) == 0
    assert 120 < _punch_in_alpha(0.05) < 135
    assert _punch_in_alpha(0.10) == 255
    assert _punch_in_alpha(0.5) == 255


def test_get_caption_at_returns_window_start():
    """_get_caption_at must return (text, window_start) for punch-in t_in math."""
    from content_engine.caption_engine import _get_caption_at

    sorted_windows = [((1.0, 1.3), "HOLD"), ((1.3, 1.6), "THE"), ((1.6, 1.9), "LINE")]
    text, start = _get_caption_at(1.05, sorted_windows)
    assert text == "HOLD"
    assert start == 1.0

    text, start = _get_caption_at(1.45, sorted_windows)
    assert text == "THE"
    assert start == 1.3

    text, start = _get_caption_at(5.0, sorted_windows)
    assert text is None
    assert start is None
