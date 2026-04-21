"""
audio_engine.py — Track pool management, BPM detection, beat-sync.

Extracted from outreach_agent/post_today.py audio logic.
"""
import json
import logging
import os
import random
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from content_engine.types import TrackInfo

PROJECT_DIR = Path(__file__).parent.parent
AUDIO_DIR = PROJECT_DIR / "content" / "audio" / "masters"
ROTATION_PATH = PROJECT_DIR / "data" / "track_rotation.json"

logger = logging.getLogger(__name__)

# ─── Scripture anchors (artist-curated, cannot be automated) ─────────────────

SCRIPTURE_ANCHORS = {
    "renamed": "Isaiah 62",
    "halleluyah": "",
    "jericho": "Joshua 6",
    "fire in our hands": "",
    "living water": "John 4",
    "he is the light": "John 8",
    "exodus": "Exodus 14",
    "abba": "Romans 8:15",
    "selah": "Psalm 46",
    "kadosh": "Isaiah 6:3",         # "Holy, Holy, Holy is the Lord"
    "side by side": "",
}


# ─── Per-track audio language (ISO 639-1, YouTube defaultAudioLanguage) ─────
# Multilingual catalogue — set per-track rather than channel-wide so YouTube
# classifies each upload correctly. Affects caption eligibility + discovery.
# Fallback for missing entries: "en".
TRACK_LANGUAGES: dict[str, str] = {
    "halleluyah":        "he",    # Hebrew chant
    "jericho":           "he",    # Hebrew vocals
    "kadosh":            "he",    # Hebrew — "Holy"
    "selah":             "he",    # Hebrew word + handpan/oud
    "renamed":           "en",    # English + chanting
    "fire in our hands": "en",
    "living water":      "en",    # English (John 4)
    "he is the light":   "en",    # English (John 8)
    "exodus":            "en",
    "abba":              "en",    # "Abba" is Aramaic but lyrics are English
    "side by side":      "en",    # English (unreleased)
}


# ─── Per-track lyrics block for YouTube description SEO ─────────────────────
# @osso-so puts the full lyrics (20-40 lines) in every video description.
# This drives (a) engaged watch-time (viewers read while listening), and
# (b) long-tail search SEO (lyric keywords are a major search vector).
#
# Empty string → publisher falls back to scripture-verse rendering when a
# SCRIPTURE_ANCHOR is set. If both are empty, the lyrics block is skipped.
TRACK_LYRICS: dict[str, str] = {
    # Fill in as we confirm the actual vocal content per track.
    # For now: empty → scripture fallback kicks in.
    "halleluyah":        "",
    "jericho":           "",
    "kadosh":            "",
    "selah":             "",
    "renamed":           "",
    "fire in our hands": "",
    "living water":      "",
    "he is the light":   "",
    "exodus":            "",
    "abba":              "",
    "side by side":      "",
}


# ─── Holy Rave channel whitelist ─────────────────────────────────────────────
# The autonomous watcher (content_engine.youtube_longform.watcher) ONLY
# considers tracks in this set for auto-publish to the Holy Rave channel.
# This is decoupled from TRACK_BPMS (which is the BPM metadata database) so
# that older / slower / off-brand tracks in the catalogue are never auto-
# uploaded to Holy Rave even if they have BPM metadata on file.
#
# Inclusion rule: Holy Rave = 130–145 BPM Nomadic Electronic (organic house
# through tribal psytrance). Tracks in this band get included by default.
# Borderline tracks (128 BPM) are OUT by default — flip them in explicitly
# if the user decides they fit the channel brand.
#
# Rationale: YouTube's recommender builds channel-level audience embeddings;
# publishing a 124 BPM track next to a 140 BPM track confuses the cluster.
# Keeping the channel genre-tight protects algorithmic momentum.
HOLY_RAVE_TRACKS: set[str] = {
    "jericho",             # 140 — tribal psytrance (Joshua 6)
    "halleluyah",          # 140 — tribal psytrance
    "kadosh",              # 142 — tribal psytrance, Hebrew (unreleased)
    "selah",               # 130 — handpan / oud / Middle Eastern (Psalm 46)
    "fire in our hands",   # 130 — organic tribal house
    "exodus",              # 138 — fits tribal tier
    "abba",                # 132 — fits organic tier
    "side by side",        # 130 — organic house (unreleased)
    # ↓ Borderline — edge tracks. Flip in by uncommenting if you want them on Holy Rave.
    # "renamed",           # 128 — organic house, just under the 130 cutoff
    # "he is the light",   # 128 — same
    # ↓ Explicitly OFF Holy Rave — too slow for the channel's genre promise.
    # Publish these to the main @robertjanmastenbroekofficial channel instead.
    # "living water",      # 124 — organic house, below Holy Rave BPM floor
}

# ─── Active track seed (top 4 by save rate) ─────────────────────────────────

SEED_TRACKS = ["halleluyah", "renamed", "jericho", "fire in our hands", "selah"]

# Artist-verified BPMs — never rely on librosa for these (librosa doubles psytrance
# BPMs: half-time detection reports ~92 BPM → doubled to 185 for a 140 BPM track).
TRACK_BPMS: dict[str, int] = {
    "halleluyah":        140,
    "renamed":           128,
    "jericho":           140,
    "fire in our hands": 130,
    "selah":             130,
    "living water":      124,
    "he is the light":   128,
    "exodus":            138,
    "abba":              132,
}


class TrackPool:
    """Manages the active track pool with weighted selection and rotation."""

    def __init__(self, max_size: int = 6):
        self.max_size = max_size
        self.rotation_path = ROTATION_PATH
        self.tracks: list[TrackInfo] = []
        self._load_seed_tracks()

    def _load_seed_tracks(self):
        """Seed pool from WAV files matching SEED_TRACKS.

        If the audio file is not found on disk, the track is still added
        with an empty file_path so the pool always contains seed tracks.
        """
        for title in SEED_TRACKS:
            path = self._find_track_file(title)
            self.tracks.append(TrackInfo(
                title=title,
                file_path=str(path) if path else "",
                bpm=TRACK_BPMS.get(title, 0),
                energy=0.7,
                danceability=0.7,
                valence=0.5,
                scripture_anchor=SCRIPTURE_ANCHORS.get(title, ""),
                spotify_id="",
                spotify_popularity=50,
                pool_weight=1.0,
                entered_pool=date.today().isoformat(),
            ))

    def _find_track_file(self, title: str) -> Optional[Path]:
        """Find WAV file matching track title (case-insensitive partial match)."""
        if not AUDIO_DIR.exists():
            return None
        title_lower = title.lower().replace(" ", "_")
        for f in AUDIO_DIR.iterdir():
            if f.suffix.lower() in (".wav", ".flac", ".mp3"):
                fname = f.stem.lower().replace(" ", "_").replace("-", "_")
                if title_lower in fname or fname in title_lower:
                    return f
        # Broader match
        for f in AUDIO_DIR.iterdir():
            if f.suffix.lower() in (".wav", ".flac", ".mp3"):
                if title.lower().split()[0] in f.stem.lower():
                    return f
        return None

    def select_track(self, weights: dict | None = None) -> TrackInfo:
        """Weighted random selection from pool. LRU bias via rotation."""
        if not self.tracks:
            raise ValueError("Track pool is empty")

        rotation = self._load_rotation()
        w = weights or {}

        scored = []
        for t in self.tracks:
            # Base weight from learning loop
            base = w.get(t.title, t.pool_weight)
            # LRU bonus: longer since last use = higher score
            last_used = rotation.get(t.title, "2020-01-01")
            days_since = (date.today() - date.fromisoformat(last_used[:10])).days
            lru_bonus = min(days_since / 7.0, 2.0)  # cap at 2x
            scored.append((t, base * (1.0 + lru_bonus)))

        total = sum(s for _, s in scored)
        if total == 0:
            return random.choice(self.tracks)

        r = random.random() * total
        cumulative = 0.0
        for t, s in scored:
            cumulative += s
            if r <= cumulative:
                return t
        return scored[-1][0]

    def mark_used(self, title: str):
        """Record that a track was used today."""
        rotation = self._load_rotation()
        rotation[title] = datetime.now().isoformat()
        self.rotation_path.parent.mkdir(parents=True, exist_ok=True)
        self.rotation_path.write_text(json.dumps(rotation, indent=2))

    def add_track(self, track: TrackInfo):
        """Add a track to the pool. If at max_size, don't add (learning loop handles eviction)."""
        if len(self.tracks) >= self.max_size:
            logger.warning(f"Pool at max size ({self.max_size}), not adding {track.title}")
            return
        if any(t.title == track.title for t in self.tracks):
            return  # already in pool
        self.tracks.append(track)

    def remove_lowest(self) -> Optional[TrackInfo]:
        """Remove the track with the lowest pool_weight. Returns removed track."""
        if len(self.tracks) <= 4:
            return None  # maintain minimum
        worst = min(self.tracks, key=lambda t: t.pool_weight)
        self.tracks.remove(worst)
        return worst

    def _load_rotation(self) -> dict:
        if self.rotation_path.exists():
            return json.loads(self.rotation_path.read_text())
        return {}

    def save_pool(self, path: Optional[Path] = None):
        """Persist pool state to JSON."""
        path = path or (PROJECT_DIR / "data" / "track_pool.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "title": t.title, "file_path": t.file_path, "bpm": t.bpm,
                "energy": t.energy, "danceability": t.danceability,
                "valence": t.valence, "scripture_anchor": t.scripture_anchor,
                "spotify_id": t.spotify_id, "spotify_popularity": t.spotify_popularity,
                "pool_weight": t.pool_weight, "entered_pool": t.entered_pool,
            }
            for t in self.tracks
        ]
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load_pool(cls, path: Optional[Path] = None) -> "TrackPool":
        """Load pool from JSON, falling back to seed tracks."""
        path = path or (PROJECT_DIR / "data" / "track_pool.json")
        pool = cls.__new__(cls)
        pool.max_size = 6
        pool.rotation_path = ROTATION_PATH
        pool.tracks = []
        if path.exists():
            data = json.loads(path.read_text())
            for d in data:
                pool.tracks.append(TrackInfo(**d))
        if not pool.tracks:
            pool._load_seed_tracks()
        return pool


# ─── BPM + Beat-Sync Functions ──────────────────────────────────────────────

def detect_bpm(audio_path: str) -> int:
    """Detect BPM via librosa. Returns integer BPM (doubles if < 100)."""
    try:
        import librosa
        y, sr = librosa.load(audio_path, sr=22050, duration=60)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo) if not hasattr(tempo, '__len__') else float(tempo[0])
        if bpm < 100:
            bpm *= 2
        return int(round(bpm))
    except Exception as e:
        logger.warning(f"BPM detection failed for {audio_path}: {e}")
        return 128  # safe default for melodic techno


def find_peak_sections(
    audio_path: str,
    section_duration: float,
    n_sections: int = 5,
) -> list[float]:
    """Find top N high-energy start times in the track.

    Uses librosa onset_strength to find energy peaks, then returns
    start times for the highest-energy windows of `section_duration` seconds.
    """
    try:
        import librosa
        import numpy as np

        y, sr = librosa.load(audio_path, sr=22050)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        hop_length = 512
        hop_secs = hop_length / sr
        total_duration = len(y) / sr

        # Sliding window: sum onset strength over section_duration
        window_frames = int(section_duration / hop_secs)
        if window_frames >= len(onset_env):
            return [0.0]

        energies = []
        for start_frame in range(len(onset_env) - window_frames):
            energy = float(np.sum(onset_env[start_frame:start_frame + window_frames]))
            start_time = start_frame * hop_secs
            # Don't start in first 15s (intro) or last 10s (outro)
            if start_time < 15.0 or start_time + section_duration > total_duration - 10.0:
                continue
            energies.append((start_time, energy))

        energies.sort(key=lambda x: x[1], reverse=True)

        # Deduplicate: no two sections within 5s of each other
        selected = []
        for start_time, energy in energies:
            if all(abs(start_time - s) > 5.0 for s in selected):
                selected.append(start_time)
            if len(selected) >= n_sections:
                break

        return selected if selected else [30.0]

    except Exception as e:
        logger.warning(f"Peak section detection failed: {e}")
        return [30.0]


def snap_to_beat(audio_path: str, target_time: float) -> float:
    """Snap a target time to the nearest beat boundary."""
    try:
        import librosa
        y, sr = librosa.load(audio_path, sr=22050, duration=min(target_time + 30, 300))
        _, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        if len(beat_times) == 0:
            return target_time
        diffs = [abs(bt - target_time) for bt in beat_times]
        nearest_idx = diffs.index(min(diffs))
        return float(beat_times[nearest_idx])
    except Exception as e:
        logger.warning(f"Beat snap failed: {e}")
        return target_time


def mix_audio_onto_video(
    video_path: str,
    audio_path: str,
    start_time: float,
    duration: float,
    output_path: str,
    fade_out_s: float = 1.5,
) -> str:
    """Replace video audio with a segment of the track. Returns output path.

    Audio pipeline (in order):
      1. Extract track segment (start_time, duration)
      2. Normalize sample rate + channel layout to 44.1kHz stereo — social
         platforms transcode anything weirder and introduce sync drift
      3. Light loudness boost (volume=1.2, hard-capped via alimiter) so our
         clips land at the platform target loudness (~-14 LUFS) without
         getting gained-down in the feed
      4. Fade-out in last fade_out_s seconds
      5. Mux into video stream with copy (video untouched), AAC 192k audio,
         regenerated timestamps and faststart for streaming playback
    """
    fade_start = max(0, duration - fade_out_s)
    # aresample guarantees 44.1kHz, aformat locks stereo s16, volume boosts
    # perceived loudness, alimiter prevents peaks clipping above -1dBFS, and
    # afade rolls off the tail so the cut doesn't feel abrupt.
    audio_filter = (
        f"[1:a]aresample=44100,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"volume=1.2,"
        f"alimiter=level_in=1:level_out=0.95:limit=0.95,"
        f"afade=t=out:st={fade_start}:d={fade_out_s}[a]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-fflags", "+genpts",
        "-i", video_path,
        "-ss", str(start_time), "-t", str(duration), "-i", audio_path,
        "-filter_complex", audio_filter,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return output_path
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode()[:500] if e.stderr else ""
        logger.error(f"Audio mix failed: {err}")
        # Fallback without alimiter (some builds lack it). Still normalizes
        # sample rate / channels which is the most critical fix for sync.
        try:
            fallback_filter = (
                f"[1:a]aresample=44100,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"afade=t=out:st={fade_start}:d={fade_out_s}[a]"
            )
            cmd_fb = [
                "ffmpeg", "-y",
                "-fflags", "+genpts",
                "-i", video_path,
                "-ss", str(start_time), "-t", str(duration), "-i", audio_path,
                "-filter_complex", fallback_filter,
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                "-movflags", "+faststart",
                "-shortest",
                output_path,
            ]
            subprocess.run(cmd_fb, check=True, capture_output=True, timeout=120)
            logger.info("[audio_engine] fell back to simple audio mix (no alimiter)")
            return output_path
        except subprocess.CalledProcessError as e2:
            logger.error(f"Audio mix fallback also failed: {e2.stderr.decode()[:500] if e2.stderr else ''}")
            raise
