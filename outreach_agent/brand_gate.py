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
from typing import Optional

# ── Banned generic phrases (Uniqueness + Falsifiability) ──────────────────────
_GENERIC_ADJECTIVES = [
    r"\bamazing\b", r"\bincredible\b", r"\bawesome\b", r"\bepic\b",
    r"\bstunning\b", r"\bbeautiful music\b", r"\bspiritual vibe\b",
    r"\breally cool\b", r"\bvery (?:spiritual|deep|powerful)\b",
    r"\beveryone\b", r"\bfor all\b",
]

# ── Hard-ban openers (inner-monologue drift traps) ────────────────────────────
# These were the patterns the previous generation produced that failed every
# stranger-test. Any hook opening with one of these is auto-rejected.
_HARD_BAN_OPENERS = [
    r"^for the ones?\b",            # "For the ones / For the one"
    r"^for those who\b",            # "For those who still…"
    r"^for the version of\b",       # "For the version of you…"
    r"^shoulders? release\b",       # therapy-journal voice
    r"^feet left the floor\b",      # describes-the-video-redundantly
    r"^built for\b",                # generic dedication
    r"^if you ?(?:are|'re) ready\b",
    r"^to the one\b",
]

# ── Hard-ban phrase patterns (therapy drift, generic dedication) ──────────────
_HARD_BAN_PHRASES = [
    r"\brebuilding from\b",
    r"\bjoy became a weapon\b",     # inner-monologue poetry
    r"\bstopped hiding\b",
    r"\bheld something burning\b",
    r"\beverything shifted\b",
    r"\bstopped asking why\b",
    r"\bnothing but honesty\b",
]

# ── Visual / concrete language markers ────────────────────────────────────────
_VISUAL_MARKERS = [
    r"\b\d+\s*(?:BPM|bpm)\b",           # BPM number
    r"\b(?:dust|sweat|smoke|candle|crowd|dark|light|fire|water|stone|sand)\b",
    r"\b(?:tribal|techno|psytrance|melodic|bass|drop|synth|kick)\b",
    r"\b(?:midnight|sunrise|dancefloor|festival|stage|cliff|sunset)\b",
    r"\b(?:Jericho|Living Water|Halleluyah|Renamed|Holy Rave|Tenerife)\b",
    r"\b(?:Joshua|John|Isaiah|Psalm|Jeremiah|Matthew)\s*\d",  # Scripture ref
    r"\b\d+\s*(?:seconds?|minutes?|am|pm)\b",   # Specific time markers
    r"\b(?:knees|shoulders|jaw|hands|teeth|sternum|back|collarbone|neck)\b",
]


def validate_contact(contact: dict) -> dict:
    """
    Validate a contact against brand safety rules before adding to pipeline.

    Checks: pagan/occult signals, drug-ceremony framing, booking venue safety.
    Returns {"safe": bool, "reason": str}
    """
    text = " ".join([
        contact.get("notes", ""),
        contact.get("genre", ""),
        contact.get("name", ""),
        contact.get("website", ""),
    ]).lower()

    # Hard excludes — explicit pagan/occult/drug ceremony
    _PAGAN_HARD = re.compile(
        r"\b(pagan\b|wicca|witchcraft|occult|satanic|satanism|baphomet|"
        r"ayahuasca ceremony|psilocybin ceremony|plant medicine ceremony|"
        r"drug ceremony|psychedelic ceremony|dark magic|spell casting|"
        r"black mass|demonology)\b",
        re.IGNORECASE,
    )
    m = _PAGAN_HARD.search(text)
    if m:
        return {"safe": False, "reason": f"pagan/occult hard-exclude: '{m.group()}'"}

    # Venue safety — drunk/drugged as primary selling point
    _VENUE_UNSAFE = re.compile(
        r"\b(free drugs|open bar unlimited|drunk encouraged|rolling hard|"
        r"mdma friendly|drug friendly|ketamine lounge|open pill|"
        r"alcohol freeflow|alcohol free flow)\b",
        re.IGNORECASE,
    )
    m = _VENUE_UNSAFE.search(text)
    if m:
        return {"safe": False, "reason": f"venue hard-exclude: '{m.group()}'"}

    # Ecstatic Dance / sober — explicit green light
    _ED_SIGNALS = re.compile(
        r"\b(ecstatic dance|sober dance|no alcohol|substance.free|alcohol.free|"
        r"conscious rave|conscious dance|authentic movement)\b",
        re.IGNORECASE,
    )
    if _ED_SIGNALS.search(text):
        return {"safe": True, "reason": "ecstatic dance / sober qualified"}

    return {"safe": True, "reason": "passed"}


def validate_content(text: str) -> dict:
    """
    Validate text against the 5 brand voice tests.

    Returns dict with keys:
      - passes (bool): True if score >= 3 and no critical failures
      - score (int): 0–5, one point per test passed
      - flags (list[str]): descriptions of failed tests
      - suggestion (str): guidance when failing
      - hard_fail (bool): True if a hard-ban pattern matched (auto-reject regardless of score)
    """
    text_lower = text.lower()
    flags = []
    score = 0
    hard_fail = False

    # Hard-ban check runs first. If any banned opener or phrase matches, the
    # hook is auto-rejected regardless of how many other tests it passes.
    # These patterns were identified from the 2026-04-15 failing batch where
    # poetic inner-monologue drift produced hooks that passed the existing
    # numeric score but were objectively unusable.
    stripped = text.strip().strip('"').strip("'")
    for pat in _HARD_BAN_OPENERS:
        if re.search(pat, stripped, re.IGNORECASE):
            flags.append(f"HARD-BAN opener: matches '{pat}' — drift into generic dedication")
            hard_fail = True
            break
    for pat in _HARD_BAN_PHRASES:
        if re.search(pat, text, re.IGNORECASE):
            flags.append(f"HARD-BAN phrase: matches '{pat}' — inner-monologue drift")
            hard_fail = True
            break

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

    # A hook passes only if it clears the score threshold AND has no hard fail.
    passes = (score >= 3) and not hard_fail

    suggestion = ""
    if not passes:
        parts = []
        if hard_fail:
            parts.append("rewrite from scratch — the opener or phrase is on the hard-ban list")
        if "Visualization" in str(flags):
            parts.append("add a concrete detail (BPM, track name, physical scene)")
        if "Falsifiability" in str(flags):
            parts.append("replace adjectives with facts")
        if "Uniqueness" in str(flags):
            parts.append("remove boilerplate; anchor to RJM/Holy Rave specifics")
        if "Point A→B" in str(flags):
            parts.append("add a contrast word (dark/light, chaos/peace, ancient/future)")
        suggestion = "; ".join(parts) if parts else "Rewrite with specific, visual, falsifiable language."

    return {
        "passes": passes,
        "score": score,
        "flags": flags,
        "suggestion": suggestion,
        "hard_fail": hard_fail,
    }


def gate_or_warn(text: str, context: str = "") -> str:
    """
    Validate and return the text unchanged.
    Prints a warning to stderr if validation fails (non-blocking — never silences output).

    Use for telemetry only. For any new code path where a failing hook must NOT
    reach render, use gate_or_reject instead.
    """
    result = validate_content(text)
    if not result["passes"]:
        prefix = f"[brand_gate:{context}] " if context else "[brand_gate] "
        print(
            f"{prefix}WARN score={result['score']}/5 flags={result['flags']}",
            file=sys.stderr,
        )
    return text


def gate_or_reject(text: str, context: str = "") -> Optional[str]:
    """
    Validate and return the text if it passes, or None if it fails.

    This is the blocking version. Callers use it in a retry loop:

        hook = gate_or_reject(candidate)
        if hook is None:
            hook = try_next_template()

    A None return must be treated as "do not ship this hook". Never substitute
    warning-only behaviour here — that's how the 2026-04-15 batch shipped.
    """
    result = validate_content(text)
    if result["passes"]:
        return text
    prefix = f"[brand_gate:{context}] " if context else "[brand_gate] "
    reason = "HARD-BAN" if result.get("hard_fail") else f"score={result['score']}/5"
    print(
        f"{prefix}REJECT {reason} flags={result['flags']} text={text!r}",
        file=sys.stderr,
    )
    return None
