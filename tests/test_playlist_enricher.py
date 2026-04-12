# tests/test_playlist_enricher.py
import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'outreach_agent'))

import playlist_enricher


def test_enrich_contact_returns_dict():
    contact = {"email": "dj@example.com", "contact_type": "curator", "genre": "melodic techno"}
    result = playlist_enricher.enrich_contact(contact)
    assert isinstance(result, dict)
    assert "email" in result


def test_enrich_adds_playlist_match_key():
    contact = {"email": "x@y.com", "contact_type": "curator", "genre": "techno"}
    result = playlist_enricher.enrich_contact(contact)
    assert "_playlist_match" in result  # may be None if no match, but key must exist


def test_enrich_does_not_crash_without_playlists(monkeypatch):
    """Graceful degradation when playlist_db has no data."""
    monkeypatch.setattr(playlist_enricher, "_PLAYLIST_DB_AVAILABLE", False)
    contact = {"email": "z@z.com", "contact_type": "curator", "genre": ""}
    result = playlist_enricher.enrich_contact(contact)
    assert result["email"] == "z@z.com"


def test_get_best_track_for_genre_returns_string():
    track = playlist_enricher.get_best_track_for_genre("melodic techno")
    assert isinstance(track, str)


def test_enrich_batch_returns_same_length():
    contacts = [
        {"email": "a@a.com", "contact_type": "curator", "genre": "techno"},
        {"email": "b@b.com", "contact_type": "curator", "genre": "psytrance"},
    ]
    result = playlist_enricher.enrich_batch(contacts)
    assert len(result) == 2
