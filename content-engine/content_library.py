"""
Content library — discovers, tracks, and rotates all video and audio assets
in the Robert-Jan Mastenbroek Command Centre.

Only songs live on Spotify are eligible for daily rotation.
Add new songs to SPOTIFY_LIVE_SONGS as they go live.

Current focus: ACTIVE_FOCUS_SONGS (subset for intensive rotation).
Set ACTIVE_FOCUS_SONGS = None to rotate through all Spotify-live songs.

Usage:
  from content_library import pick_today, record_run, get_run_history

  song, videos = pick_today()          # smart daily selection
  record_run(song, videos, output_dir) # log after successful run
"""

import os
import sqlite3
import random
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
CC_ROOT    = Path("~/Documents/Robert-Jan Mastenbroek Command Centre").expanduser()
OUTPUT_DIR = CC_ROOT / "content" / "output"
MASTERS_DIR = CC_ROOT / "content" / "audio" / "masters"
DB_PATH    = CC_ROOT / ".content_library.db"

VIDEO_SCAN_DIRS = [
    CC_ROOT / "content" / "videos" / "phone-footage",
    CC_ROOT / "content" / "videos" / "performances",
    CC_ROOT / "content" / "videos" / "music-videos",
]

SONG_SCAN_DIRS = [
    CC_ROOT / "content" / "audio" / "masters",
]

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.m4v', '.webm',
                    '.MP4', '.MOV', '.MKV', '.M4V', '.AVI'}

BLACKLIST_FILE = CC_ROOT / "content" / "videos" / ".blacklist"


def _load_blacklist() -> set:
    """Load blacklisted clip stems from .blacklist file (one stem per line)."""
    if not BLACKLIST_FILE.exists():
        return set()
    return {
        line.strip().lower()
        for line in BLACKLIST_FILE.read_text().splitlines()
        if line.strip() and not line.startswith('#')
    }

# Songs: prefer _FINAL or _MASTER suffix — deduplicate by base stem
PREFERRED_SUFFIXES = ['_FINAL', '_MASTER', 'FINAL', 'MASTER']

# ── Spotify allowlist ─────────────────────────────────────────────────────────
# Only songs with a live Spotify release are used in daily rotation.
# Add new songs here the day they go live. Case-insensitive substring match
# against the WAV filename stem.
SPOTIFY_LIVE_SONGS = [
    # All 31 songs live on Spotify as of April 2026
    "fire in our hands",
    "he is the light",
    "lord in the fullness",
    "halleluyah",
    "abba",
    "my hope is in you",
    "shema",
    "renamed",
    "you see it all",
    "white as snow",
    "wait on the lord",
    "under your wings",
    "thunder",
    "strong tower",
    "rise up my love",
    "quieted soul",
    "power from above",
    "not by might",
    "living water",
    "kavod",
    "jericho",
    "how good and pleasant",
    "he reigns",
    "halala king jesus",
    "good to me",
    "give thanks",
    "first face",
    "exodus",
    "chaos bends",
    "better is one day",
    "at the door",
    # Add each new weekly release here as it goes live
]

# Current focus — rotate only through these songs right now.
# Set to None to rotate through all SPOTIFY_LIVE_SONGS above.
ACTIVE_FOCUS_SONGS = [
    "halleluyah",
    "jericho",
    "renamed",
    "fire in our hands",
]

# Exact filenames to prefer when deduplication produces multiple matches.
# Key = pattern from allowlist, value = preferred filename stem (case-insensitive).
PREFERRED_EXACT = {
    "fire in our hands": "Robert-Jan Mastenbroek & LUCID - Fire In Our Hands",
}


# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS songs (
            path        TEXT PRIMARY KEY,
            filename    TEXT,
            last_used   TEXT,
            use_count   INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS videos (
            path        TEXT PRIMARY KEY,
            filename    TEXT,
            folder_type TEXT,   -- phone | event | music_video | other
            last_used   TEXT,
            use_count   INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date    TEXT,
            song_path   TEXT,
            video_paths TEXT,   -- JSON array
            output_dir  TEXT,
            posted      INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()


# ── Asset discovery ───────────────────────────────────────────────────────────

def _folder_type(path: Path) -> str:
    parts = path.parts
    if 'phone-footage' in parts:
        return 'phone'
    if 'performances' in parts or any('LUC' in p for p in parts):
        return 'event'
    if 'music-video' in parts or 'music_video' in parts:
        return 'music_video'
    return 'other'


def scan_videos() -> list[str]:
    """Return all video file paths found in VIDEO_SCAN_DIRS, excluding blacklisted clips."""
    blacklist = _load_blacklist()
    found = []
    skipped = 0
    for d in VIDEO_SCAN_DIRS:
        if not d.exists():
            continue
        for p in d.rglob('*'):
            if p.suffix in VIDEO_EXTENSIONS and p.is_file():
                # Skip already-processed output files
                if 'output' in p.parts or '_7s' in p.name or '_15s' in p.name:
                    continue
                # Skip blacklisted clips
                if p.stem.lower() in blacklist:
                    skipped += 1
                    continue
                found.append(str(p))
    if skipped:
        logger.info(f"Skipped {skipped} blacklisted clip(s)")
    return sorted(set(found))


def _deduplicate_songs(paths: list[str]) -> list[str]:
    """
    When both JERICHO_FINAL.wav and JERICHO_MASTER.wav exist, keep only FINAL.
    Deduplication is by normalised stem (strip _FINAL, _MASTER, spaces).
    """
    seen: dict[str, str] = {}  # normalised_stem → best_path
    for p in paths:
        stem = Path(p).stem.upper()
        # Normalise: remove FINAL/MASTER suffixes + underscores
        for suffix in ['_FINAL', '_MASTER', ' FINAL', ' MASTER', '__MASTER']:
            stem = stem.replace(suffix, '')
        stem = stem.strip('_').strip()

        existing = seen.get(stem)
        if existing is None:
            seen[stem] = p
        else:
            # Prefer FINAL > MASTER > anything else
            existing_name = Path(existing).name.upper()
            new_name = Path(p).name.upper()
            if 'FINAL' in new_name and 'FINAL' not in existing_name:
                seen[stem] = p
            elif 'MASTER' in new_name and 'MASTER' not in existing_name and 'FINAL' not in existing_name:
                seen[stem] = p
    return sorted(seen.values())


def scan_songs() -> list[str]:
    """Return deduplicated master WAV file paths."""
    found = []
    for d in SONG_SCAN_DIRS:
        if not d.exists():
            continue
        for p in d.rglob('*.wav'):
            if p.is_file():
                found.append(str(p))
        for p in d.rglob('*.WAV'):
            if p.is_file():
                found.append(str(p))
    # Exclude obvious non-originals: show versions, demo, remix, etc.
    filtered = [
        p for p in found
        if not any(k in Path(p).name.lower() for k in ['show)', 'demo', 'remix', 'test', 'verkoop', 'bomba', 'beat)', 'book', 'sand in', 'roni bat'])
    ]
    return _deduplicate_songs(filtered)


# ── Smart selection ───────────────────────────────────────────────────────────

def _sync_library():
    """Ensure all discovered assets are registered in the DB."""
    init_db()
    conn = sqlite3.connect(str(DB_PATH))

    songs = scan_songs()
    for p in songs:
        conn.execute(
            "INSERT OR IGNORE INTO songs (path, filename) VALUES (?, ?)",
            (p, Path(p).name)
        )

    videos = scan_videos()
    for p in videos:
        conn.execute(
            "INSERT OR IGNORE INTO videos (path, filename, folder_type) VALUES (?, ?, ?)",
            (p, Path(p).name, _folder_type(Path(p)))
        )

    conn.commit()
    conn.close()
    logger.info(f"Library synced: {len(songs)} songs, {len(videos)} videos")
    return songs, videos


def _song_is_active(path: str) -> bool:
    """
    Return True if this song is in the active rotation allowlist.
    When PREFERRED_EXACT specifies a preferred file for a pattern,
    only that file matches (the duplicate is excluded).
    """
    focus = ACTIVE_FOCUS_SONGS if ACTIVE_FOCUS_SONGS else SPOTIFY_LIVE_SONGS
    stem = Path(path).stem
    stem_lower = stem.lower()

    for pattern in focus:
        if pattern.lower() not in stem_lower:
            continue
        # Check if there's a preferred exact match for this pattern
        preferred = PREFERRED_EXACT.get(pattern.lower())
        if preferred:
            return preferred.lower() == stem_lower
        return True
    return False


def pick_song(force: str = None) -> str:
    """
    Pick today's song from the active Spotify allowlist.
    - If force is given, use that path (or match by filename stem).
    - Otherwise: least-recently-used song in the active allowlist.
      Rotates evenly so no song repeats until all have been used once.
    """
    _sync_library()

    if force:
        force_path = Path(force)
        if force_path.exists():
            return str(force_path)
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute("SELECT path FROM songs").fetchall()
        conn.close()
        stem = force_path.stem.lower()
        for (p,) in rows:
            if stem in Path(p).stem.lower():
                return p
        candidates = scan_songs()
        for p in candidates:
            if stem in Path(p).stem.lower():
                return p
        raise FileNotFoundError(f"Song not found: {force}")

    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("""
        SELECT path, last_used, use_count FROM songs
        WHERE path IS NOT NULL
        ORDER BY
            CASE WHEN last_used IS NULL THEN 0 ELSE 1 END,
            last_used ASC,
            use_count ASC
    """).fetchall()
    conn.close()

    # Filter to active allowlist only
    active_rows = [(p, lu, uc) for p, lu, uc in rows if _song_is_active(p)]

    if not active_rows:
        focus = ACTIVE_FOCUS_SONGS or SPOTIFY_LIVE_SONGS
        raise RuntimeError(
            f"No songs found matching active focus list: {focus}\n"
            f"Check that the WAV files exist in content/audio/masters/"
        )

    # Show what's in rotation
    logger.info(f"Active rotation: {len(active_rows)} songs — "
                + ", ".join(Path(p).stem for p, _, _ in active_rows))

    # Least-recently-used, with top-3 pool for slight randomness
    pool = active_rows[:min(3, len(active_rows))]
    chosen_path, last_used, use_count = random.choice(pool)
    logger.info(f"Selected song: {Path(chosen_path).name} "
                f"(used {use_count}x, last: {last_used or 'never'})")
    return chosen_path


def pick_videos(n: int = 12, song_path: str = None) -> list[str]:
    """
    Pick n video clips, spread across folder types for visual variety.
    Prioritises least-recently-used clips from each category.

    Distribution:
      - 30% phone footage (candid, authentic)
      - 60% event/performance footage (higher quality, crowd energy)
      - 10% music-video / other (cinematic)
    """
    _sync_library()
    conn = sqlite3.connect(str(DB_PATH))

    def fetch_pool(folder_type: str, limit: int) -> list[str]:
        rows = conn.execute("""
            SELECT path FROM videos
            WHERE folder_type = ? AND path IS NOT NULL
            ORDER BY
                CASE WHEN last_used IS NULL THEN 0 ELSE 1 END,
                last_used ASC,
                use_count ASC
            LIMIT ?
        """, (folder_type, limit * 3)).fetchall()  # fetch 3x and sample
        paths = [r[0] for r in rows if Path(r[0]).exists()]
        random.shuffle(paths)
        return paths[:limit]

    # Distribution across types
    n_event  = max(1, int(n * 0.60))
    n_phone  = max(1, int(n * 0.30))
    n_other  = max(1, n - n_phone - n_event)

    phone_clips = fetch_pool('phone', n_phone)
    event_clips = fetch_pool('event', n_event)
    other_clips = fetch_pool('music_video', n_other // 2) + fetch_pool('other', n_other - n_other // 2)

    conn.close()

    combined = phone_clips + event_clips + other_clips
    random.shuffle(combined)

    # If we don't have enough from categories, fill with any least-used
    if len(combined) < 6:
        conn = sqlite3.connect(str(DB_PATH))
        fallback = conn.execute("""
            SELECT path FROM videos ORDER BY use_count ASC, last_used ASC LIMIT 20
        """).fetchall()
        conn.close()
        extras = [r[0] for r in fallback if r[0] not in combined and Path(r[0]).exists()]
        combined += extras

    result = combined[:n]
    logger.info(f"Selected {len(result)} clips "
                f"({sum(1 for p in result if _folder_type(Path(p))=='phone')} phone, "
                f"{sum(1 for p in result if _folder_type(Path(p))=='event')} event, "
                f"{sum(1 for p in result if _folder_type(Path(p)) not in ('phone','event'))} other)")
    return result


def pick_today(n_videos: int = 12, force_song: str = None) -> tuple[str, list[str]]:
    """
    Single call to get today's song + video selection.
    Returns (song_path, [video_paths]).
    """
    song = pick_song(force=force_song)
    videos = pick_videos(n=n_videos, song_path=song)
    return song, videos


# ── Run tracking ──────────────────────────────────────────────────────────────

def record_run(song: str, videos: list[str], output_dir: str, posted: bool = False):
    """Log a completed run to the database and update usage counts."""
    import json
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(DB_PATH))

    conn.execute(
        "INSERT INTO runs (run_date, song_path, video_paths, output_dir, posted) VALUES (?,?,?,?,?)",
        (now, song, json.dumps(videos), str(output_dir), int(posted))
    )
    conn.execute(
        "UPDATE songs SET last_used=?, use_count=use_count+1 WHERE path=?",
        (now, song)
    )
    for v in videos:
        conn.execute(
            "UPDATE videos SET last_used=?, use_count=use_count+1 WHERE path=?",
            (now, v)
        )
    conn.commit()
    conn.close()
    logger.info(f"Run recorded: {Path(song).name} + {len(videos)} clips → {output_dir}")


def get_run_history(limit: int = 14) -> list[dict]:
    """Return recent run history for reporting."""
    import json
    init_db()
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT run_date, song_path, video_paths, output_dir, posted FROM runs "
        "ORDER BY run_date DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [
        {
            "date":       r[0],
            "song":       Path(r[1]).name if r[1] else "?",
            "n_videos":   len(json.loads(r[2])) if r[2] else 0,
            "output_dir": r[3],
            "posted":     bool(r[4]),
        }
        for r in rows
    ]


def library_stats() -> dict:
    """Return counts and usage stats for the library."""
    songs, videos = _sync_library()
    active_songs = [p for p in songs if _song_is_active(p)]
    conn = sqlite3.connect(str(DB_PATH))
    n_songs  = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    n_videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    n_runs   = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    unused_videos = conn.execute("SELECT COUNT(*) FROM videos WHERE last_used IS NULL").fetchone()[0]

    # Per-song usage for active rotation
    active_usage = []
    for p in active_songs:
        row = conn.execute(
            "SELECT last_used, use_count FROM songs WHERE path=?", (p,)
        ).fetchone()
        if row:
            active_usage.append({
                "name": Path(p).stem,
                "last_used": row[0] or "never",
                "use_count": row[1],
            })
    conn.close()
    return {
        "active_songs":  active_usage,
        "songs_total":   n_songs,
        "videos":        n_videos,
        "videos_unused": unused_videos,
        "runs_total":    n_runs,
    }
