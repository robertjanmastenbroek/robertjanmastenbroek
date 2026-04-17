# content_engine/tests/test_audio_engine.py
import pytest
import json
from pathlib import Path
from content_engine.audio_engine import (
    TrackPool,
    detect_bpm,
    find_peak_sections,
    snap_to_beat,
    mix_audio_onto_video,
)
from content_engine.types import TrackInfo


def test_track_pool_init():
    pool = TrackPool()
    assert len(pool.tracks) >= 4  # seeded with current top tracks


def test_track_pool_select_weighted():
    pool = TrackPool()
    track = pool.select_track()
    assert isinstance(track, TrackInfo)
    assert track.title != ""


def test_track_pool_add_track():
    pool = TrackPool()
    new_track = TrackInfo(
        title="Test Track", file_path="/tmp/test.wav", bpm=128,
        energy=0.7, danceability=0.8, valence=0.5,
        scripture_anchor="", spotify_id="test123",
        spotify_popularity=50, pool_weight=1.0,
        entered_pool="2026-04-16",
    )
    initial_count = len(pool.tracks)
    pool.add_track(new_track)
    assert len(pool.tracks) == initial_count + 1


def test_track_pool_max_size():
    pool = TrackPool(max_size=4)
    for i in range(5):
        pool.add_track(TrackInfo(
            title=f"Track {i}", file_path=f"/tmp/{i}.wav", bpm=128,
            energy=0.7, danceability=0.8, valence=0.5,
            scripture_anchor="", spotify_id=f"id{i}",
            spotify_popularity=50 + i, pool_weight=1.0,
            entered_pool="2026-04-16",
        ))
    assert len(pool.tracks) <= 6  # seed tracks (up to 4) + max_size cap


def test_track_pool_rotation(tmp_path):
    """Track rotation persists to JSON."""
    pool = TrackPool()
    pool.rotation_path = tmp_path / "track_rotation.json"
    track = pool.select_track()
    pool.mark_used(track.title)
    data = json.loads(pool.rotation_path.read_text())
    assert track.title in data


def test_seed_track_bpms_hardcoded():
    """Seed tracks must have non-zero BPM without needing audio files."""
    pool = TrackPool()
    by_title = {t.title: t for t in pool.tracks}
    assert by_title["halleluyah"].bpm == 140, f"Expected 140, got {by_title['halleluyah'].bpm}"
    assert by_title["jericho"].bpm == 140, f"Expected 140, got {by_title['jericho'].bpm}"
    assert by_title["fire in our hands"].bpm == 130, f"Expected 130, got {by_title['fire in our hands'].bpm}"
    for t in pool.tracks:
        assert t.bpm > 0, f"Track '{t.title}' still has bpm=0"
