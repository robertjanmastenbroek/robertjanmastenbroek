# tests/test_contact_scorer.py
import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'outreach_agent'))

import contact_scorer


def test_score_returns_float():
    contact = {
        "email": "test@example.com",
        "contact_type": "curator",
        "status": "verified",
        "research_notes": "runs a 5000-follower melodic techno playlist on Spotify",
    }
    score = contact_scorer.score(contact)
    assert isinstance(score, float)
    assert 0.0 <= score <= 10.0


def test_score_podcast_higher_than_stale_curator():
    """Podcast contacts (high reply rate) should outscore a curator with no research."""
    podcast = {"email": "p@pod.com", "contact_type": "podcast", "status": "verified", "research_notes": "interviewed Anyma last month"}
    bare_curator = {"email": "c@cur.com", "contact_type": "curator", "status": "verified", "research_notes": ""}
    assert contact_scorer.score(podcast) >= contact_scorer.score(bare_curator)


def test_score_with_research_notes_higher():
    base = {"email": "a@b.com", "contact_type": "curator", "status": "verified", "research_notes": ""}
    researched = {"email": "a@b.com", "contact_type": "curator", "status": "verified", "research_notes": "plays melodic techno, 10k followers"}
    assert contact_scorer.score(researched) > contact_scorer.score(base)


def test_score_doesnt_crash_without_db(monkeypatch):
    """score() degrades gracefully when DB is unavailable."""
    monkeypatch.setattr(contact_scorer, "_DB_AVAILABLE", False)
    result = contact_scorer.score({"email": "x@y.com", "contact_type": "curator", "status": "verified", "research_notes": ""})
    assert isinstance(result, float)


def test_rank_contacts_sorted_descending():
    contacts = [
        {"email": "a@a.com", "contact_type": "curator", "status": "verified", "research_notes": ""},
        {"email": "b@b.com", "contact_type": "podcast", "status": "verified", "research_notes": "techno podcast 20k listeners"},
    ]
    ranked = contact_scorer.rank(contacts)
    assert ranked[0]["email"] == "b@b.com"
