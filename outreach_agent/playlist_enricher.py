"""
playlist_enricher.py — bridges the Playlist Discovery DB to the Contact DB
and the Template Engine.

For each contact the cycle is about to act on, look up whether we already
have a matching playlist record (by curator email or by genre overlap) and
annotate the contact dict with:

    _playlist_match : dict | None   — the matched playlist row (if any)
    _best_track     : str           — best RJM track for this contact's genre
    research_notes  : str           — human-readable context appended, never replaced

This module is intentionally defensive: if playlist_db or story can't be
imported, enrichment is a no-op and contacts pass through unchanged.
"""

from __future__ import annotations

import logging
import sys
import os

# Ensure sibling outreach_agent modules are importable when this file is
# loaded from tests or from run_cycle.
sys.path.insert(0, os.path.dirname(__file__))

log = logging.getLogger("outreach.playlist_enricher")

# ─── Defensive imports ────────────────────────────────────────────────────────
try:
    import playlist_db as _playlist_db  # type: ignore
    _PLAYLIST_DB_AVAILABLE = True
except Exception:  # ImportError, sqlite errors, config issues
    _playlist_db = None  # type: ignore
    _PLAYLIST_DB_AVAILABLE = False

try:
    import story as _story  # type: ignore
    _STORY_AVAILABLE = True
except Exception:
    _story = None  # type: ignore
    _STORY_AVAILABLE = False


# ─── Genre → Track routing ────────────────────────────────────────────────────
# Ordered so that the first matching keyword wins. Keep keys lowercase.
_GENRE_TRACK_MAP = {
    "psytrance":      "Halleluyah",
    "psy":            "Halleluyah",
    "trance":         "Halleluyah",
    "tribal":         "Jericho",
    "techno":         "Jericho",
    "ethnic":         "Jericho",
    "melodic":        "Living Water",
    "house":          "Living Water",
    "progressive":    "Living Water",
    "deep":           "Living Water",
    "christian":      "He Is The Light",
    "worship":        "He Is The Light",
    "faith":          "He Is The Light",
    "dark":           "He Is The Light",
}

_DEFAULT_TRACK = "Living Water"  # most accessible crossover


def get_best_track_for_genre(genre: str) -> str:
    """
    Return the best-fit RJM track name for a given free-text genre string.

    Never returns None — falls back to the default crossover track. The
    return is always a plain string so callers can embed it directly into
    email templates.
    """
    if not genre:
        return _DEFAULT_TRACK
    g = genre.lower()
    for keyword, track in _GENRE_TRACK_MAP.items():
        if keyword in g:
            return track
    return _DEFAULT_TRACK


# ─── Playlist DB lookup ───────────────────────────────────────────────────────
def _find_matching_playlist(email: str, genre: str) -> dict | None:
    """
    Look up the playlists table for a row that matches this contact.

    Strategy (cheapest → broadest):
      1. Exact curator_email match
      2. genre_tags overlap (substring), ordered by relevance_score DESC

    Returns a dict (row) or None. Never raises — all DB failures become None
    so enrichment degrades gracefully.
    """
    if not _PLAYLIST_DB_AVAILABLE or _playlist_db is None:
        return None
    if not email and not genre:
        return None

    try:
        with _playlist_db.get_conn() as conn:
            # 1. Try exact email match first
            if email:
                row = conn.execute(
                    "SELECT * FROM playlists WHERE curator_email = ? "
                    "ORDER BY relevance_score DESC, follower_count DESC LIMIT 1",
                    (email,)
                ).fetchone()
                if row:
                    return dict(row)

            # 2. Fall back to genre keyword overlap
            if genre:
                like = f"%{genre.lower()}%"
                row = conn.execute(
                    "SELECT * FROM playlists "
                    "WHERE LOWER(genre_tags) LIKE ? "
                    "ORDER BY relevance_score DESC, follower_count DESC LIMIT 1",
                    (like,)
                ).fetchone()
                if row:
                    return dict(row)
    except Exception as e:
        log.debug("playlist lookup failed for %s: %s", email, e)
        return None

    return None


# ─── Public enrichment API ────────────────────────────────────────────────────
def enrich_contact(contact: dict) -> dict:
    """
    Return a new dict based on `contact`, annotated with playlist context.

    Never mutates the input. Always adds two keys:
        _playlist_match : dict | None
        _best_track     : str

    If a playlist match was found, a short human-readable note is appended to
    `research_notes` so the template engine can reference it without having
    to understand the playlist schema.
    """
    # Defensive copy — input is never touched.
    enriched = dict(contact) if contact else {}

    email = enriched.get("email", "") or ""
    genre = enriched.get("genre", "") or ""

    best_track = get_best_track_for_genre(genre)
    enriched["_best_track"] = best_track

    match = _find_matching_playlist(email, genre)
    enriched["_playlist_match"] = match

    if match:
        existing_notes = enriched.get("research_notes", "") or ""
        playlist_name = match.get("name", "unknown playlist")
        followers = match.get("follower_count") or 0
        best_match_track = match.get("best_track_match") or best_track
        note_line = (
            f"[playlist] {playlist_name} ({followers} followers) — "
            f"best RJM track fit: {best_match_track}"
        )
        enriched["research_notes"] = (
            f"{existing_notes}\n{note_line}".strip() if existing_notes else note_line
        )

    return enriched


def enrich_batch(contacts: list) -> list:
    """Enrich a list of contacts. Always returns a list of the same length."""
    if not contacts:
        return []
    return [enrich_contact(c) for c in contacts]
