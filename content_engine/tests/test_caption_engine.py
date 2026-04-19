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
