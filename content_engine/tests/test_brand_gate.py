# content_engine/tests/test_brand_gate.py
"""Tests for the brand gate validation logic."""
import pytest

from content_engine.brand_gate import validate_content, gate_or_reject, gate_or_warn


# ── validate_content ────────────────────────────────────────────────────────


class TestValidateContent:
    def test_passing_hook(self):
        """A hook with visual marker, no generic adj, no boilerplate, under 280 chars, with contrast."""
        result = validate_content(
            "140 BPM tribal rhythm at the Tenerife cliff -- ancient meets future"
        )
        assert result["passes"] is True
        assert result["score"] >= 3
        assert result["hard_fail"] is False

    def test_hard_ban_opener_for_the_ones(self):
        result = validate_content("For the ones who dance alone at 4am")
        assert result["hard_fail"] is True
        assert result["passes"] is False

    def test_hard_ban_opener_for_those_who(self):
        result = validate_content("For those who still search at midnight")
        assert result["hard_fail"] is True
        assert result["passes"] is False

    def test_hard_ban_phrase_joy_became_weapon(self):
        result = validate_content(
            "Joy became a weapon on the dancefloor at 140 BPM"
        )
        assert result["hard_fail"] is True
        assert result["passes"] is False

    def test_hard_ban_phrase_rebuilding_from(self):
        result = validate_content("Rebuilding from the ashes at 130 BPM")
        assert result["hard_fail"] is True

    def test_too_many_generic_adjectives(self):
        result = validate_content("Amazing incredible beautiful music")
        assert result["passes"] is False
        assert result["score"] < 5

    def test_boilerplate_fails_uniqueness(self):
        result = validate_content("A unique sound on a passionate journey")
        assert result["passes"] is False
        any_uniqueness_flag = any("Uniqueness" in f for f in result["flags"])
        assert any_uniqueness_flag

    def test_over_280_chars_fails_one_mississippi(self):
        long_text = "A " * 200  # well over 280 chars
        result = validate_content(long_text)
        any_mississippi_flag = any("One Mississippi" in f for f in result["flags"])
        assert any_mississippi_flag

    def test_no_contrast_fails_point_ab(self):
        # text with a visual marker and short, but no contrast/tension words
        result = validate_content("130 BPM synth loop recorded Tuesday")
        any_ab_flag = any("Point A" in f for f in result["flags"])
        assert any_ab_flag

    def test_perfect_score(self):
        """Hook that should hit all 5 tests."""
        result = validate_content(
            "Jericho at 140 BPM -- the walls came down in the dark"
        )
        assert result["passes"] is True
        assert result["score"] == 5

    def test_result_keys(self):
        """Ensure the return dict always contains the expected keys."""
        result = validate_content("test")
        for key in ("passes", "score", "flags", "suggestion", "hard_fail"):
            assert key in result


# ── gate_or_reject ──────────────────────────────────────────────────────────


class TestGateOrReject:
    def test_passes_returns_text(self):
        text = "Jericho at 140 BPM -- the walls came down on the dancefloor"
        assert gate_or_reject(text) == text

    def test_blocks_returns_none(self):
        text = "For the ones who feel the rhythm"
        assert gate_or_reject(text) is None

    def test_hard_ban_returns_none(self):
        text = "For those who stopped hiding at the rave"
        assert gate_or_reject(text) is None


# ── gate_or_warn ────────────────────────────────────────────────────────────


class TestGateOrWarn:
    def test_always_returns_text(self):
        """gate_or_warn is non-blocking -- always returns the original text."""
        bad_text = "Amazing incredible beautiful music"
        assert gate_or_warn(bad_text) == bad_text

    def test_good_text_returns_text(self):
        good_text = "140 BPM tribal rhythm -- ancient meets future"
        assert gate_or_warn(good_text) == good_text
