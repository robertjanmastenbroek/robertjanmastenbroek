# outreach_agent/brand_gate.py
"""
Brand Gate — validates content against RJM brand voice rules.
Bridges Brand Voice/DNA communities to Content Generator and Template Engine.

Five tests from BRAND_VOICE.md:
  1. Visualization Test — can the reader SEE the words?
  2. Falsifiability Test — facts over adjectives
  3. Uniqueness Rule — could a competitor sign this?
  4. One Mississippi Test — value prop in < 2 seconds
  5. Point A→B — secular/searching fan → sacred energy

Returns: {"passes": bool, "score": int (0-5), "flags": list[str], "suggestion": str}
"""

import re
import sys
import os

# ── Banned generic phrases (Uniqueness + Falsifiability) ──────────────────────
_GENERIC_ADJECTIVES = [
    r"\bamazing\b", r"\bincredible\b", r"\bawesome\b", r"\bepic\b",
    r"\bstunning\b", r"\bbeautiful music\b", r"\bspiritual vibe\b",
    r"\breally cool\b", r"\bvery (?:spiritual|deep|powerful)\b",
    r"\beveryone\b", r"\bfor all\b",
]

# ── Visual / concrete language markers ────────────────────────────────────────
_VISUAL_MARKERS = [
    r"\b\d+\s*(?:BPM|bpm)\b",           # BPM number
    r"\b(?:dust|sweat|smoke|candle|crowd|dark|light|fire|water|stone|sand)\b",
    r"\b(?:tribal|techno|psytrance|melodic|bass|drop|synth|kick)\b",
    r"\b(?:midnight|sunrise|dancefloor|festival|stage)\b",
    r"\b(?:Jericho|Living Water|Halleluyah|Renamed|Holy Rave)\b",
    r"\b(?:Joshua|John|Isaiah|Psalm)\s*\d",  # Scripture ref
]


def validate_content(text: str) -> dict:
    """
    Validate text against the 5 brand voice tests.

    Returns dict with keys:
      - passes (bool): True if score >= 3 and no critical failures
      - score (int): 0–5, one point per test passed
      - flags (list[str]): descriptions of failed tests
      - suggestion (str): guidance when failing
    """
    text_lower = text.lower()
    flags = []
    score = 0

    # Test 1: Visualization — at least one visual/concrete marker
    if any(re.search(p, text, re.IGNORECASE) for p in _VISUAL_MARKERS):
        score += 1
    else:
        flags.append("Visualization: no concrete sensory language (add BPM, track name, physical detail)")

    # Test 2: Falsifiability — no more than 1 generic adjective
    generic_hits = [p for p in _GENERIC_ADJECTIVES if re.search(p, text, re.IGNORECASE)]
    if len(generic_hits) <= 1:
        score += 1
    else:
        flags.append(f"Falsifiability: too many generic adjectives ({len(generic_hits)} found — replace with facts)")

    # Test 3: Uniqueness — competitor test (no brand-agnostic boilerplate)
    boilerplate = [r"\bpassion(?:ate)?\b", r"\bjourney\b", r"\bunique sound\b", r"\bspecial\b"]
    if not any(re.search(p, text, re.IGNORECASE) for p in boilerplate):
        score += 1
    else:
        flags.append("Uniqueness: boilerplate language — could belong to any artist, rewrite with RJM specifics")

    # Test 4: One Mississippi — value prop length proxy (under 280 chars)
    if len(text.strip()) <= 280:
        score += 1
    else:
        flags.append("One Mississippi: content exceeds 280 chars — consider splitting or tightening")

    # Test 5: Point A→B — at least a subtle tension/contrast word or scripture reference
    ab_markers = [
        r"\b(?:dark|light|lost|found|broken|whole|chaos|peace|ancient|future)\b",
        r"\b(?:John|Joshua|Isaiah|Psalm|Matthew|truth|sacred|holy)\b",
        r"\b(?:rave|worship|dance|pray|club|cathedral)\b",
    ]
    if any(re.search(p, text, re.IGNORECASE) for p in ab_markers):
        score += 1
    else:
        flags.append("Point A→B: no contrast/tension language — add secular/sacred bridge word")

    passes = score >= 3

    suggestion = ""
    if not passes:
        parts = []
        if "Visualization" in str(flags):
            parts.append("add a concrete detail (BPM, track name, physical scene)")
        if "Falsifiability" in str(flags):
            parts.append("replace adjectives with facts")
        if "Uniqueness" in str(flags):
            parts.append("remove boilerplate; anchor to RJM/Holy Rave specifics")
        if "Point A→B" in str(flags):
            parts.append("add a contrast word (dark/light, chaos/peace, ancient/future)")
        suggestion = "; ".join(parts) if parts else "Rewrite with specific, visual, falsifiable language."

    return {"passes": passes, "score": score, "flags": flags, "suggestion": suggestion}


def gate_or_warn(text: str, context: str = "") -> str:
    """
    Validate and return the text unchanged.
    Prints a warning to stderr if validation fails (non-blocking — never silences output).
    """
    result = validate_content(text)
    if not result["passes"]:
        prefix = f"[brand_gate:{context}] " if context else "[brand_gate] "
        print(
            f"{prefix}WARN score={result['score']}/5 flags={result['flags']}",
            file=sys.stderr,
        )
    return text
