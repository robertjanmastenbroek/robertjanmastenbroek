"""
Hook generator — retrieves and generates viral hook text for RJM content.

Usage:
  hooks = get_hooks_for_file('/path/to/JERICHO_FINAL.wav', bucket='reach')
  # Returns list of hook strings, best-performing first

  bible_info = get_bible_info('/path/to/JERICHO_FINAL.wav')
  # Returns {verse_reference, verse_text, theme, track_name}
"""

from pathlib import Path
from hook_database import (
    init_db,
    seed_initial_data,
    get_hooks_for_track,
    get_track_info,
)

# Ensure the database exists and is seeded on import
init_db()
seed_initial_data()


def _match_track(filepath: str) -> str:
    """
    Extract filename pattern from full path for DB lookup.

    Uppercases the filename stem and checks it against known patterns
    stored in the database.  Matching strategy (in order):

    1. Exact substring match  — pattern is wholly contained in the stem.
    2. Word-intersection match — all underscore-separated words of the
       pattern appear (in order) somewhere in the stem.  This handles
       cases like 'CREATE_CLEAN_HEART' matching
       'CREATE_IN_ME_A_CLEAN_HEART_FINAL'.

    Returns the matching pattern string, or '' if no match is found.
    """
    from hook_database import _get_conn

    stem = Path(filepath).stem.upper()

    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT filename_pattern FROM tracks")
    patterns = [row['filename_pattern'] for row in cur.fetchall()]
    conn.close()

    # Pass 1: exact substring
    for pattern in patterns:
        if pattern.upper() in stem:
            return pattern

    # Pass 2: word-intersection (all words of pattern present in stem)
    for pattern in patterns:
        words = pattern.upper().split('_')
        if all(w in stem for w in words):
            return pattern

    return ''


def get_hooks_for_file(filepath: str, bucket: str = 'reach', limit: int = 10,
                       max_chars: int = 40) -> list[str]:
    """
    Match filepath to database, return hook texts for that track.
    Falls back to generic hooks if track not found.

    Args:
        filepath:  Full or partial path to the audio file.
        bucket:    'reach', 'follow', or 'spotify'.
        limit:     Maximum number of hooks to return.
        max_chars: Only return hooks shorter than this (for video overlays).
                   Pass None to disable filtering.

    Returns:
        List of hook text strings, best-performing first.
    """
    pattern = _match_track(filepath)

    if not pattern:
        return get_generic_hooks(bucket, max_chars=max_chars)[:limit]

    rows = get_hooks_for_track(pattern, bucket=bucket)

    if not rows:
        rows = get_hooks_for_track(pattern)

    if not rows:
        return get_generic_hooks(bucket, max_chars=max_chars)[:limit]

    hooks = [row['hook_text'] for row in rows]
    if max_chars:
        hooks = [h for h in hooks if len(h) <= max_chars]

    # If filtering left nothing, fall back to generics
    if not hooks:
        hooks = get_generic_hooks(bucket, max_chars=max_chars)

    return hooks[:limit]


def get_bible_info(filepath: str) -> dict:
    """
    Return bible verse info for a track. Empty dict if not found.

    Returns keys: verse_reference, verse_text, theme, track_name,
                  bible_book, bible_chapter, bible_verse_start, bible_verse_end
    """
    pattern = _match_track(filepath)
    if not pattern:
        return {}

    info = get_track_info(pattern)
    if not info:
        return {}

    return {
        'verse_reference': info.get('verse_reference', ''),
        'verse_text':      info.get('verse_text', ''),
        'theme':           info.get('theme', ''),
        'track_name':      info.get('track_name', ''),
        'bible_book':      info.get('bible_book', ''),
        'bible_chapter':   info.get('bible_chapter', ''),
        'bible_verse_start': info.get('bible_verse_start', ''),
        'bible_verse_end':   info.get('bible_verse_end', ''),
    }


def get_generic_hooks(bucket: str = 'reach', max_chars: int = 40) -> list[str]:
    """
    Generic Holy Rave hooks for tracks not yet in the database.
    Short, punchy — designed for video text overlays.
    """
    reach_hooks = [
        'Sacred music. Loud.',
        'The rave was always holy.',
        'This is what prayer sounds like.',
        'Ancient text. Modern drop.',
        'Worship was never quiet.',
        'Church for the dancefloor.',
        'Faith at 130 BPM.',
        'God moves here too.',
        'The psalms had bass.',
        'Built on sacred ground.',
        'Holy Rave. Tenerife.',
        'This started as a prayer.',
        '3000 years old. Still hits.',
        'Find God at the drop.',
        'Free. Every Friday. Tenerife.',
    ]

    follow_hooks = [
        'Free every Friday. Tenerife.',
        'Follow for weekly drops.',
        'Sunset Sessions. Always free.',
        'Holy Rave. Follow along.',
        'New music every week.',
        'Join the congregation.',
    ]

    spotify_hooks = [
        'Stream it. Save it.',
        'On Spotify now.',
        'Full track on Spotify.',
        'Add it to your rotation.',
        'Now streaming.',
    ]

    pool = {'follow': follow_hooks, 'spotify': spotify_hooks}.get(bucket, reach_hooks)
    if max_chars:
        pool = [h for h in pool if len(h) <= max_chars]
    return pool or reach_hooks


if __name__ == '__main__':
    # Quick smoke-test
    test_files = [
        '/audio/JERICHO_FINAL.wav',
        '/audio/Not_By_Might_FINAL.wav',
        '/audio/Let_My_People_Go_FINAL.wav',
        '/audio/Create_In_Me_A_Clean_Heart_FINAL.wav',
        '/audio/UNKNOWN_TRACK.wav',
    ]

    for f in test_files:
        print(f'\n=== {Path(f).name} ===')
        info = get_bible_info(f)
        if info:
            print(f'  Verse : {info["verse_reference"]}')
            print(f'  Track : {info["track_name"]}')
        else:
            print('  No bible info — using generic hooks')

        hooks = get_hooks_for_file(f, bucket='reach', limit=3)
        for i, h in enumerate(hooks, 1):
            print(f'  [{i}] {h}')
