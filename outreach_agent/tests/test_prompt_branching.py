"""Regression: research-aware prompt branching in template_engine._build_prompt.

Researched contacts get a "research-first" directive that forces the opener
to reference something specific from the notes. Unresearched contacts get a
"genre-fallback" directive — no inventing details, lead with BPM/genre match.
"""
import pytest


def test_researched_prompt_has_research_first_directive():
    import template_engine

    contact = {
        "email": "alice@example.com",
        "name": "Alice",
        "type": "curator",
        "genre": "melodic techno",
        "notes": "",
        "research_notes": "Runs Friday Techno playlist, 8.4k followers, BPM 120-128",
    }
    prompt = template_engine._build_prompt(contact)
    assert "RECIPIENT RESEARCH" in prompt
    assert "research-first" in prompt.lower() or "opener must reference" in prompt.lower()
    # No fallback branch language when research exists
    assert "genre-fallback" not in prompt.lower()


def test_unresearched_prompt_has_genre_fallback_directive():
    import template_engine

    contact = {
        "email": "bob@example.com",
        "name": "Bob",
        "type": "curator",
        "genre": "psytrance",
        "notes": "",
        "research_notes": "",
    }
    prompt = template_engine._build_prompt(contact)
    assert "RECIPIENT RESEARCH" not in prompt
    assert "genre-fallback" in prompt.lower() or "do not invent" in prompt.lower()


def test_researched_batch_prompt_has_research_first_marker():
    import template_engine

    contacts = [
        {
            "email": "alice@example.com",
            "name": "Alice",
            "type": "curator",
            "genre": "melodic techno",
            "notes": "",
            "research_notes": "Friday Techno playlist, BPM 120-128",
        },
    ]
    prompt = template_engine._build_batch_prompt(contacts, {"curator": ""})
    assert "research-first" in prompt.lower() or "opener must reference" in prompt.lower()
