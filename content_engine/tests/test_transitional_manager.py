# content_engine/tests/test_transitional_manager.py
import json
import pytest
from pathlib import Path
from content_engine.transitional_manager import TransitionalManager


@pytest.fixture
def populated_index(tmp_path):
    hooks_dir = tmp_path / "hooks" / "transitional"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "nature").mkdir()
    (hooks_dir / "satisfying").mkdir()

    # Create dummy video files
    (hooks_dir / "nature" / "wave_01.mp4").write_bytes(b"x" * 1000)
    (hooks_dir / "nature" / "aurora_01.mp4").write_bytes(b"x" * 1000)
    (hooks_dir / "satisfying" / "soap_01.mp4").write_bytes(b"x" * 1000)

    index = [
        {"file": "nature/wave_01.mp4", "category": "nature", "duration_s": 3.5,
         "last_used": None, "performance_score": 1.0, "times_used": 0},
        {"file": "nature/aurora_01.mp4", "category": "nature", "duration_s": 4.0,
         "last_used": None, "performance_score": 1.5, "times_used": 0},
        {"file": "satisfying/soap_01.mp4", "category": "satisfying", "duration_s": 3.0,
         "last_used": None, "performance_score": 1.0, "times_used": 0},
    ]
    (hooks_dir / "index.json").write_text(json.dumps(index, indent=2))
    return hooks_dir


def test_load_bank(populated_index):
    mgr = TransitionalManager(populated_index)
    assert len(mgr.bank) == 3


def test_pick_hook(populated_index):
    mgr = TransitionalManager(populated_index)
    hook = mgr.pick()
    assert hook is not None
    assert "file" in hook


def test_mark_used_updates_index(populated_index):
    mgr = TransitionalManager(populated_index)
    hook = mgr.pick()
    mgr.mark_used(hook["file"])
    reloaded = json.loads((populated_index / "index.json").read_text())
    used_hook = next(h for h in reloaded if h["file"] == hook["file"])
    assert used_hook["last_used"] is not None
    assert used_hook["times_used"] == 1


def test_pick_respects_cooldown(populated_index):
    mgr = TransitionalManager(populated_index)
    from datetime import date
    today = date.today().isoformat()
    # Mark all nature hooks as used today
    for h in mgr.bank:
        if h["category"] == "nature":
            h["last_used"] = today
    hook = mgr.pick()
    # Should pick satisfying since nature is on cooldown
    assert hook["category"] == "satisfying"


def test_scan_for_new_clips(populated_index):
    # Add a new file not in index
    (populated_index / "nature" / "new_clip.mp4").write_bytes(b"x" * 1000)
    mgr = TransitionalManager(populated_index)
    mgr.scan_for_new_clips()
    assert len(mgr.bank) == 4
    assert any(h["file"] == "nature/new_clip.mp4" for h in mgr.bank)


def test_full_path(populated_index):
    mgr = TransitionalManager(populated_index)
    hook = mgr.pick()
    full = mgr.full_path(hook["file"])
    assert full.exists()
