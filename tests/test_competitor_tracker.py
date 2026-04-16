"""Tests for competitor_tracker — comparable artist monitoring (BTL Layer 3)."""
import sys
import json
import tempfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "outreach_agent"))

import competitor_tracker as ct


@pytest.fixture(autouse=True)
def isolated_tracking_file(tmp_path, monkeypatch):
    """Redirect TRACKING_FILE to a temp file for each test."""
    tmp_file = tmp_path / "competitor_tracking.json"
    tmp_file.write_text(json.dumps({"artists": [], "snapshots": []}))
    monkeypatch.setattr(ct, "TRACKING_FILE", tmp_file)
    yield tmp_file


# ---------- load_tracking ----------

def test_load_tracking_seeds_when_artists_empty(isolated_tracking_file):
    data = ct.load_tracking()
    assert len(data["artists"]) == len(ct.SEED_ARTISTS)
    names = {a["name"] for a in data["artists"]}
    assert "Anyma" in names
    assert "Argy" in names


def test_load_tracking_persists_seed_to_disk(isolated_tracking_file):
    ct.load_tracking()
    on_disk = json.loads(isolated_tracking_file.read_text())
    assert len(on_disk["artists"]) == len(ct.SEED_ARTISTS)


def test_load_tracking_does_not_reseed_when_already_populated(isolated_tracking_file):
    isolated_tracking_file.write_text(json.dumps({
        "artists": [{"name": "Solo", "genre": "Test", "reason": "Test"}],
        "snapshots": [],
    }))
    data = ct.load_tracking()
    assert len(data["artists"]) == 1
    assert data["artists"][0]["name"] == "Solo"


def test_load_tracking_returns_seed_when_file_missing(isolated_tracking_file, monkeypatch):
    isolated_tracking_file.unlink()
    data = ct.load_tracking()
    assert data["artists"] == ct.SEED_ARTISTS
    assert data["snapshots"] == []


# ---------- add_artist ----------

def test_add_artist_appends_new_entry(isolated_tracking_file):
    ct.load_tracking()  # seed
    ct.add_artist("Tale Of Us", genre="Melodic Techno", reason="Aesthetic peer")
    data = ct.load_tracking()
    assert any(a["name"] == "Tale Of Us" for a in data["artists"])


def test_add_artist_dedupes_case_insensitive(isolated_tracking_file):
    ct.load_tracking()
    before = len(ct.load_tracking()["artists"])
    ct.add_artist("ANYMA")  # already in seed
    after = len(ct.load_tracking()["artists"])
    assert before == after


# ---------- record_snapshot ----------

def test_record_snapshot_appends_with_today_date(isolated_tracking_file):
    ct.record_snapshot("Anyma", 5_000_000)
    data = ct.load_tracking()
    assert len(data["snapshots"]) == 1
    snap = data["snapshots"][0]
    assert snap["artist"] == "Anyma"
    assert snap["listeners"] == 5_000_000
    assert "date" in snap and len(snap["date"]) == 10  # YYYY-MM-DD


def test_record_snapshot_preserves_existing(isolated_tracking_file):
    ct.record_snapshot("Anyma", 1000)
    ct.record_snapshot("Argy", 2000)
    data = ct.load_tracking()
    assert len(data["snapshots"]) == 2


# ---------- detect_spikes ----------

def test_detect_spikes_returns_empty_with_insufficient_data(isolated_tracking_file):
    ct.record_snapshot("Anyma", 1000)
    assert ct.detect_spikes() == []


def test_detect_spikes_flags_growth_above_threshold(isolated_tracking_file):
    isolated_tracking_file.write_text(json.dumps({
        "artists": [{"name": "Anyma", "genre": "", "reason": ""}],
        "snapshots": [
            {"artist": "Anyma", "listeners": 1000, "date": "2026-01-01"},
            {"artist": "Anyma", "listeners": 1500, "date": "2026-02-01"},
        ],
    }))
    spikes = ct.detect_spikes(threshold_pct=20.0)
    assert len(spikes) == 1
    assert spikes[0]["artist"] == "Anyma"
    assert spikes[0]["previous"] == 1000
    assert spikes[0]["current"] == 1500
    assert spikes[0]["growth_pct"] == 50.0


def test_detect_spikes_ignores_growth_below_threshold(isolated_tracking_file):
    isolated_tracking_file.write_text(json.dumps({
        "artists": [{"name": "Anyma", "genre": "", "reason": ""}],
        "snapshots": [
            {"artist": "Anyma", "listeners": 1000, "date": "2026-01-01"},
            {"artist": "Anyma", "listeners": 1050, "date": "2026-02-01"},  # 5%
        ],
    }))
    assert ct.detect_spikes(threshold_pct=20.0) == []


def test_detect_spikes_handles_zero_previous(isolated_tracking_file):
    isolated_tracking_file.write_text(json.dumps({
        "artists": [{"name": "Anyma", "genre": "", "reason": ""}],
        "snapshots": [
            {"artist": "Anyma", "listeners": 0, "date": "2026-01-01"},
            {"artist": "Anyma", "listeners": 1000, "date": "2026-02-01"},
        ],
    }))
    # Division-by-zero guarded — no crash, no spike emitted
    assert ct.detect_spikes() == []


def test_detect_spikes_uses_chronological_order(isolated_tracking_file):
    # Out-of-order snapshots — should still compare latest two by date
    isolated_tracking_file.write_text(json.dumps({
        "artists": [{"name": "Anyma", "genre": "", "reason": ""}],
        "snapshots": [
            {"artist": "Anyma", "listeners": 2000, "date": "2026-03-01"},
            {"artist": "Anyma", "listeners": 1000, "date": "2026-01-01"},
            {"artist": "Anyma", "listeners": 1500, "date": "2026-02-01"},
        ],
    }))
    spikes = ct.detect_spikes(threshold_pct=20.0)
    assert len(spikes) == 1
    assert spikes[0]["previous"] == 1500
    assert spikes[0]["current"] == 2000


# ---------- get_status ----------

def test_get_status_reports_counts_and_names(isolated_tracking_file):
    ct.load_tracking()  # seed
    ct.record_snapshot("Anyma", 1000)
    status = ct.get_status()
    assert status["artists_tracked"] == len(ct.SEED_ARTISTS)
    assert status["total_snapshots"] == 1
    assert "Anyma" in status["artists"]
    assert isinstance(status["recent_spikes"], list)


# ---------- CLI smoke ----------

def test_cli_status_runs(isolated_tracking_file, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["competitor_tracker.py", "status"])
    ct.main()
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "artists_tracked" in payload


def test_cli_spikes_no_spikes(isolated_tracking_file, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["competitor_tracker.py", "spikes"])
    ct.main()
    assert "No growth spikes detected." in capsys.readouterr().out


def test_cli_unknown_command_exits_nonzero(isolated_tracking_file, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["competitor_tracker.py", "bogus"])
    with pytest.raises(SystemExit) as exc:
        ct.main()
    assert exc.value.code == 1


def test_cli_no_args_exits_nonzero(isolated_tracking_file, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["competitor_tracker.py"])
    with pytest.raises(SystemExit) as exc:
        ct.main()
    assert exc.value.code == 1
