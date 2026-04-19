# content_engine/tests/test_renderer.py
import pytest
from pathlib import Path
from content_engine.renderer import (
    validate_output,
    get_platform_color_grade,
    PLATFORM_GRADES,
    render_transitional,
    render_emotional,
    render_performance,
    render_performance_fast_cut,
    render_story_variant,
)
from content_engine.types import ClipFormat


def test_platform_grades_exist():
    for p in ["instagram", "youtube", "facebook", "tiktok"]:
        assert p in PLATFORM_GRADES


def test_validate_output_rejects_missing(tmp_path):
    result = validate_output(str(tmp_path / "nonexistent.mp4"), target_duration=15)
    assert result["valid"] is False


def test_validate_output_rejects_tiny(tmp_path):
    f = tmp_path / "tiny.mp4"
    f.write_bytes(b"x" * 50)  # 50 bytes < 100KB threshold
    result = validate_output(str(f), target_duration=15)
    assert result["valid"] is False


def test_get_platform_color_grade():
    grade = get_platform_color_grade("instagram")
    assert "contrast" in grade
    assert "saturation" in grade


def test_hook_overlay_dwell_capped():
    """Hook text must never dwell longer than 2.4s — attention-window research."""
    from content_engine.renderer import _HOOK_DWELL_MAX_S
    assert _HOOK_DWELL_MAX_S <= 2.4, (
        f"_HOOK_DWELL_MAX_S={_HOOK_DWELL_MAX_S} is too long. "
        "Viral hook research caps at 2.5s before attention drops."
    )


def test_hook_overlay_dwell_floor():
    """Minimum hook dwell — a 7-word hook needs at least ~1.5s to be legible."""
    from content_engine.renderer import _HOOK_DWELL_MAX_S
    assert _HOOK_DWELL_MAX_S >= 1.8


def test_render_performance_fast_cut_signature():
    """Smoke test — exists, takes the slice list and the same other args."""
    import inspect
    sig = inspect.signature(render_performance_fast_cut)
    params = set(sig.parameters)
    assert "bait_clip" in params
    assert "segment_slices" in params
    assert "audio_path" in params
    assert "audio_start" in params
    assert "hook_text" in params
    assert "track_label" in params
    assert "platform" in params
    assert "output_path" in params
    assert "target_duration" in params


def test_render_performance_fast_cut_rejects_empty_slices():
    """Renderer must refuse to run on empty slice list — safety net for upstream bugs."""
    with pytest.raises(ValueError):
        render_performance_fast_cut(
            bait_clip="/fake/bait.mp4",
            segment_slices=[],
            audio_path="/fake/audio.mp3",
            audio_start=30.0,
            hook_text="test",
            track_label="test",
            platform="instagram",
            output_path="/tmp/out.mp4",
            target_duration=22.0,
        )
