# tests/test_viral_hook_library.py
"""
Tests for the viral_hook_library + the template-driven generator.

These tests lock in the behaviour we built on 2026-04-16 after the
2026-04-15 batch shipped 9 brand-failing hooks. The goals:

  1. Every curated example_fill in the library must pass the brand gate.
     If a library author adds a new template that fails, tests fail loudly.

  2. The 9 failing hooks from 2026-04-15 must be hard-rejected by the
     brand gate going forward — no backsliding.

  3. generator.generate_run_hooks must retry onto the next template when a
     primary Claude fill fails the brand gate, not ship the failing text.

  4. When the Claude call raises, generator.generate_run_hooks must still
     produce library-grade hooks via the deterministic example_fill path.
"""

import os
import sys
import random
import pytest

_THIS = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_THIS, '..', 'outreach_agent'))

import brand_gate
import generator
import viral_hook_library as vhl


# ── 1. Library structural integrity ───────────────────────────────────────────

def test_library_has_templates_for_every_angle():
    """Every clip angle must have at least three templates so retries work."""
    for angle in ("contrast", "body-drop", "identity"):
        pool = vhl.BY_ANGLE[angle]
        assert len(pool) >= 3, f"angle {angle} has only {len(pool)} templates"


def test_every_template_has_required_fields():
    for t in vhl.TEMPLATES:
        assert t.id,             f"missing id on {t}"
        assert t.template,       f"{t.id}: missing template string"
        assert t.slots,          f"{t.id}: no slots declared"
        assert t.example_fill,   f"{t.id}: missing example_fill"
        assert t.source_credit,  f"{t.id}: missing source_credit"
        assert t.angle in ("contrast", "body-drop", "identity"), \
            f"{t.id}: invalid angle {t.angle!r}"
        assert t.mechanism in ("tension", "identity", "scene", "claim", "rupture"), \
            f"{t.id}: invalid mechanism {t.mechanism!r}"


def test_every_example_fill_passes_brand_gate():
    """
    The example_fills are the fallback hooks the pipeline ships when every
    Claude call fails. They MUST all pass the brand gate — otherwise the
    retry path can ship a failing hook.
    """
    failures = []
    for t in vhl.TEMPLATES:
        result = brand_gate.validate_content(t.example_fill)
        if not result["passes"]:
            failures.append((t.id, result["flags"]))
    assert not failures, f"example_fills failing brand gate: {failures}"


# ── 2. Track facts lookup ─────────────────────────────────────────────────────

@pytest.mark.parametrize("title,bpm,scripture", [
    ("Fire In Our Hands",                       130, "Jeremiah 23:29"),
    ("Robert-Jan Mastenbroek & LUCID - Fire In Our Hands", 130, "Jeremiah 23:29"),
    ("Renamed",                                 128, "Isaiah 62"),
    ("Jericho",                                 140, "Joshua 6"),
    ("Halleluyah",                              140, "Psalm 150"),
])
def test_track_facts_lookup_fuzzy_matches(title, bpm, scripture):
    facts = vhl.get_track_facts(title)
    assert facts["bpm"] == bpm
    assert facts["scripture_anchor"] == scripture


def test_track_facts_unknown_track_returns_default():
    facts = vhl.get_track_facts("Some Random Track Name")
    assert "bpm" in facts
    assert "canonical_location" in facts


# ── 3. Brand gate — retroactive check on 2026-04-15 failing batch ─────────────

# These 7 hooks shipped in the 2026-04-15 Fire In Our Hands + Renamed batches
# and were objectively unusable. The brand gate MUST hard-reject every one of
# them going forward.
FAILING_HOOKS_2026_04_15 = [
    "Held something burning once, dropped it // this time it stays",
    "Shoulders release three months on the drop // and you stop asking why it took so long",
    "For the ones joy became a weapon // and you stopped hiding it",
    "Feet left the floor // everything shifted at the drop",
    "For the one rebuilding // from nothing but honesty",
]


@pytest.mark.parametrize("hook", FAILING_HOOKS_2026_04_15)
def test_brand_gate_rejects_2026_04_15_failing_hooks(hook):
    assert brand_gate.gate_or_reject(hook, context="test") is None, \
        f"HARD-BAN regression: {hook!r} slipped through the brand gate"


def test_brand_gate_still_accepts_survivors_from_2026_04_15():
    """
    Two hooks from the 2026-04-15 batch were actually good. Make sure the
    tightened ban list doesn't over-reject.
    """
    for hook in ("POV: fire meets the psalm", "8 seconds until your knees forget"):
        assert brand_gate.gate_or_reject(hook, context="test") == hook


# ── 4. gate_or_reject vs gate_or_warn ─────────────────────────────────────────

def test_gate_or_reject_returns_none_on_hard_ban():
    assert brand_gate.gate_or_reject("For the ones still rebuilding from nothing") is None


def test_gate_or_warn_is_non_blocking():
    # gate_or_warn must return the text unchanged even when it fails, so we
    # can use it for telemetry-only code paths without breaking them.
    bad = "For the ones still rebuilding from nothing"
    assert brand_gate.gate_or_warn(bad) == bad


# ── 5. Generator — template-driven hook generation ───────────────────────────

CLIPS_CONFIG = [
    {"length": 7,  "angle": "contrast"},
    {"length": 15, "angle": "body-drop"},
    {"length": 28, "angle": "identity"},
]


def _stub_claude(response: str):
    """Return a drop-in replacement for generator._call_claude."""
    def _fake(system_prompt, user_prompt, timeout=180):
        return response
    return _fake


def test_generate_run_hooks_uses_claude_fill_when_it_passes(monkeypatch):
    monkeypatch.setattr(generator, "_call_claude", _stub_claude(
        "7: POV: 140 BPM meets Jericho horns\n"
        "15: 7 seconds until the front row stops breathing\n"
        "28: Made this for the drummer who kept time through chemo. Found it in Jericho."
    ))
    random.seed(42)
    hooks = generator.generate_run_hooks("Jericho", CLIPS_CONFIG)
    assert "Jericho horns" in hooks[7]["hook"]
    assert "front row" in hooks[15]["hook"]
    assert "drummer" in hooks[28]["hook"]
    for length in (7, 15, 28):
        assert hooks[length]["template_id"]
        assert hooks[length]["mechanism"]


def test_generate_run_hooks_retries_when_claude_returns_banned_hooks(monkeypatch):
    """If Claude drifts back into inner-monologue, retry onto a curated example_fill."""
    monkeypatch.setattr(generator, "_call_claude", _stub_claude(
        "7: For the ones still rebuilding from nothing\n"
        "15: Shoulders release the weight of three years\n"
        "28: For the one who stopped answering to names"
    ))
    random.seed(42)
    hooks = generator.generate_run_hooks("Jericho", CLIPS_CONFIG)

    for length in (7, 15, 28):
        h = hooks[length]["hook"].lower()
        # Banned Claude output MUST NOT ship
        assert "rebuilding from" not in h
        assert "shoulders release" not in h
        assert "stopped answering" not in h
        # Retry path must have tried more than one template
        assert len(hooks[length]["tried_templates"]) >= 2


def test_generate_run_hooks_survives_claude_exception(monkeypatch):
    """If Claude explodes, produce library-grade hooks via deterministic fill."""
    def _boom(*a, **kw):
        raise RuntimeError("Claude CLI exploded")
    monkeypatch.setattr(generator, "_call_claude", _boom)

    random.seed(42)
    hooks = generator.generate_run_hooks("Halleluyah", CLIPS_CONFIG)

    for length, angle in [(7, "contrast"), (15, "body-drop"), (28, "identity")]:
        h = hooks[length]["hook"]
        assert h, f"empty hook for {length}s"
        t = vhl.get_template_by_id(hooks[length]["template_id"])
        assert t is not None
        assert t.angle == angle


def test_generate_run_hooks_return_shape_matches_legacy_contract():
    """post_today.py relies on a specific return shape — lock it in."""
    def _boom(*a, **kw):
        raise RuntimeError("mock")
    import outreach_agent.generator  # noqa  (ensures import path works either way)

    # Simpler: just call with the explode path; signature should match.
    generator._call_claude = _boom
    random.seed(42)
    hooks = generator.generate_run_hooks("Renamed", CLIPS_CONFIG)
    for length in (7, 15, 28):
        meta = hooks[length]
        assert "hook" in meta and isinstance(meta["hook"], str)
        assert "mechanism" in meta
        assert "exploration" in meta
        assert "template_id" in meta


# ── 6. Parser ─────────────────────────────────────────────────────────────────

def test_parse_filled_templates_handles_various_formats():
    raw = """
7: POV: fire meets the psalm
15 - 8 seconds until your knees forget
28: "Made this for the friend who texted at 3am"
"""
    out = generator._parse_filled_templates(raw, [7, 15, 28])
    assert out[7]["hook_text"].startswith("POV:")
    assert out[15]["hook_text"].startswith("8 seconds")
    # Wrapping quotes must be stripped
    assert not out[28]["hook_text"].startswith('"')
