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


def get_hooks_for_file(filepath: str, bucket: str = 'reach', limit: int = 10) -> list[str]:
    """
    Match filepath to database, return hook texts for that track.
    Falls back to generic hooks if track not found.

    Args:
        filepath: Full or partial path to the audio file.
        bucket:   'reach' (viral/top-of-funnel) or 'depth' (engaged audience).
        limit:    Maximum number of hooks to return.

    Returns:
        List of hook text strings, best-performing first.
    """
    pattern = _match_track(filepath)

    if not pattern:
        return get_generic_hooks(bucket)[:limit]

    rows = get_hooks_for_track(pattern, bucket=bucket)

    if not rows:
        # Fallback: try without bucket filter
        rows = get_hooks_for_track(pattern)

    if not rows:
        return get_generic_hooks(bucket)[:limit]

    return [row['hook_text'] for row in rows[:limit]]


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


def get_generic_hooks(bucket: str = 'reach') -> list[str]:
    """
    Generic Holy Rave hooks for tracks not yet in the database.
    Still high-quality, just not track-specific.

    bucket='reach'  → viral, short-form, curiosity-gap, broad audience
    bucket='depth'  → for engaged fans, more layered meaning
    """

    reach_hooks = [
        # Holy Rave concept
        'What if the rave was always sacred?',
        'This is what church sounds like when nobody has to whisper.',
        'Holy Rave: where the ancient text meets the modern dancefloor.',
        'You didn\'t come to a club. You came to a cathedral.',
        'The Psalms were written to be felt in your chest. That\'s why this is techno.',
        'If David had a sub bass, it sounded like this.',
        'Some of us only hear God when the music is this loud.',
        'Sacred and secular — the line was always thinner than they told you.',
        'This is what happens when the ancient texts get a sound system.',
        'Church music for people who can\'t sit still.',

        # Sunset Sessions Tenerife
        'Tenerife at 6am. Sun rising. Music still playing. This is that feeling.',
        'Sunset Sessions: where the Atlantic Ocean is the dance floor.',
        'Built on volcanic rock. Played under the stars. That\'s the vibe.',
        'Some music belongs outdoors. Under a sky this big.',
        'Tenerife is where the ancient and the futuristic collapse into one.',

        # Sacred/secular contrast
        'The Bible is more electronic than you think.',
        'Every prophet was also a poet. Every psalm was also a song.',
        'Worship was never meant to be quiet.',
        'The temple walls shook. The dancefloor shakes. Same frequency.',
        'Scripture was meant to be performed, not just read.',
        'This is for the people who feel God more in music than in silence.',
        'If you\'ve ever cried on a dancefloor, you already know what this is.',
        'Electronic worship — not a trend. A return.',

        # Electronic faith
        'Some people find God at the altar. Others find Him at the drop.',
        'Faith encoded in frequencies.',
        'The Spirit moves where the Spirit moves. Sometimes it\'s here.',
        'This is what it sounds like when someone builds music as a prayer.',
        'The Bible says make a joyful noise. This is that.',
        'Not background music. Sacred music.',

        # Curiosity / no-explicit-Bible
        'Every track I make has a secret. This is one of them.',
        'The lyrics are older than you think.',
        'The source material for this is 3,000 years old.',
        'Wait until you find out where this comes from.',
        'This started as a prayer. It ended up as a track.',
    ]

    depth_hooks = [
        # Holy Rave depth
        'The Holy Rave isn\'t ironic. It\'s the most serious thing I do.',
        'Techno as liturgy — form, repetition, surrender. It was always this.',
        'The trance state on a dancefloor and the trance state in prayer are closer than you\'ve been told.',
        'Electronic music and sacred music share the same architecture: rhythm, repetition, release.',
        'The ancients used rhythm to enter the presence of God. We call it a rave now.',
        'What I\'m building is not "Christian techno." It\'s something older than both categories.',
        'The church lost the dancefloor. I\'m taking it back.',
        'Every track I produce is an act of translation — ancient truth into contemporary frequency.',

        # Bible / Scripture depth
        'The Bible is full of people who worshipped God loudly, physically, communally. The rave already existed.',
        'Hebrew worship was participatory, embodied, rhythmic. Sound familiar?',
        'The Psalms are 150 songs that were sung, not read. This is how they should be heard.',
        'Sacred music was always for the body, not just the mind.',
        'Scripture was composed for performance in communal spaces. You\'re in that space right now.',

        # Sunset Sessions / Tenerife depth
        'I build music in Tenerife because the island is itself a kind of crossing — between Africa and Europe, ancient and modern, ocean and sky.',
        'The Sunset Sessions started as a personal experiment. It became a congregation.',
        'There is something about playing music at the edge of the ocean that is not metaphorical.',
        'Tenerife is where I go to hear clearly. The music that comes out of that clarity is what you\'re hearing.',

        # Faith / personal
        'Every track is a record of where I was spiritually when I made it. Nothing is decoration.',
        'I stopped making music for audiences and started making it as offerings. The audiences got bigger.',
        'The most vulnerable thing I do is put a Bible verse in the centre of a club track and mean it completely.',
        'I don\'t choose the Bible verses. They choose the music. I just show up and produce.',
    ]

    if bucket == 'depth':
        return depth_hooks
    return reach_hooks


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
