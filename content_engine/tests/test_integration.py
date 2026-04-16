"""
Integration test: run the full pipeline in dry-run mode.
Requires: source videos in content/videos/, audio in content/audio/masters/
"""
import pytest
import json
from pathlib import Path


@pytest.fixture
def project_dir():
    return Path(__file__).parent.parent.parent


def test_dry_run_produces_clips(project_dir):
    """Full pipeline dry-run should produce at least 1 clip.

    Skips gracefully when source assets are missing — this is a smoke test
    that exercises the pipeline wiring end-to-end, not a hard requirement.
    Comprehensive coverage lives in the unit tests.
    """
    # Check for minimum assets; skip if missing
    video_dir = project_dir / "content" / "videos"
    audio_dir = project_dir / "content" / "audio" / "masters"

    has_videos = video_dir.exists() and (
        any(video_dir.rglob("*.mp4")) or any(video_dir.rglob("*.mov"))
    )
    has_audio = audio_dir.exists() and any(audio_dir.iterdir())

    if not has_videos:
        pytest.skip("No source videos in content/videos/")
    if not has_audio:
        pytest.skip("No audio in content/audio/masters/")

    from content_engine.pipeline import run_full_day

    result = run_full_day(dry_run=True)

    assert result["dry_run"] is True
    assert result["clips_rendered"] >= 1, "Should render at least 1 clip"

    # Check registry was written to dry-run dir
    registry_path = Path(result.get("registry", ""))
    if registry_path.exists():
        registry = json.loads(registry_path.read_text())
        assert len(registry) >= 1

    # Check output directory has files
    output_dir = project_dir / "content" / "output" / result["date"]
    if output_dir.exists():
        mp4_files = list(output_dir.glob("*.mp4"))
        assert len(mp4_files) >= 1


def test_hook_variety():
    """Multiple runs should produce different hooks."""
    from content_engine.generator import generate_hooks_for_format
    from content_engine.types import ClipFormat

    hooks = set()
    for _ in range(5):
        result = generate_hooks_for_format(
            ClipFormat.EMOTIONAL, "Jericho",
            {"bpm": 140, "scripture_anchor": "Joshua 6"},
        )
        hooks.add(result["template_id"])

    # Should use at least 2 different templates in 5 runs
    assert len(hooks) >= 2, f"Only used {len(hooks)} templates in 5 runs — not enough variety"
