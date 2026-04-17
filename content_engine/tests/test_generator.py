# content_engine/tests/test_generator.py
import pytest
from content_engine.generator import (
    generate_hooks_for_format,
    generate_caption,
    pick_sub_mode,
    ANGLE_SUB_MODES,
)
from content_engine.types import ClipFormat
from content_engine.hook_library import HookTemplate


def test_sub_modes_exist():
    assert "emotional" in ANGLE_SUB_MODES
    assert "signal" in ANGLE_SUB_MODES
    assert "energy" in ANGLE_SUB_MODES
    assert len(ANGLE_SUB_MODES["emotional"]) == 5


def test_pick_sub_mode():
    mode = pick_sub_mode("emotional")
    assert mode in ANGLE_SUB_MODES["emotional"]


def test_generate_hooks_returns_dict():
    """Test with fallback (Claude unavailable = uses example_fill)."""
    result = generate_hooks_for_format(
        fmt=ClipFormat.EMOTIONAL,
        track_title="Jericho",
        track_facts={"bpm": 140, "scripture_anchor": "Joshua 6"},
    )
    assert "hook" in result
    assert "template_id" in result
    assert "mechanism" in result
    assert "sub_mode" in result
    assert len(result["hook"]) > 0


def test_generate_caption():
    caption = generate_caption(
        track_title="Jericho",
        hook_text="Wait for the drop. Just wait.",
        platform="instagram",
    )
    assert isinstance(caption, str)
    assert len(caption) > 0


def test_generate_hooks_accepts_visual_context():
    """visual_context kwarg must be accepted without error."""
    result = generate_hooks_for_format(
        fmt=ClipFormat.TRANSITIONAL,
        track_title="Jericho",
        track_facts={"bpm": 140, "scripture_anchor": "Joshua 6"},
        visual_context={"category": "nature", "file": "nature/855278.mp4"},
    )
    assert "hook" in result
    assert len(result["hook"]) > 0


def test_generate_caption_accepts_visual_context():
    """visual_context kwarg must be accepted without error."""
    caption = generate_caption(
        track_title="Jericho",
        hook_text="The walls fall when you stop holding them up.",
        platform="instagram",
        track_facts={"bpm": 140},
        visual_context={"category": "satisfying", "file": "satisfying/855416.mp4"},
    )
    assert isinstance(caption, str)
    assert len(caption) > 0
