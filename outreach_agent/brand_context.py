"""
RJM Brand Context — Single source of truth for all AI prompts.

Every module that calls Claude imports from here.
This is the ONLY place brand identity is defined for the agent fleet.

  COMPACT_STORY     — compressed story injected into all email prompts
  SMYKM_FRAMEWORK   — email writing rules (SMYKM method)
  get_voice_rules() — key extract from BRAND_VOICE.md (lazy-loaded)
  get_podcast_angles_text() — story angles formatted for Claude prompts
  get_full_brand_context()  — story + voice, for non-email agents

Never define brand identity in more than one place.
"""

from pathlib import Path
from story import ARTIST, PODCAST_ANGLES

# ─── Compact Story (email-optimized, ~180 tokens) ─────────────────────────────
# The single compressed brand story injected into ALL Claude CLI calls.
# Edit here — it propagates to emails, follow-ups, briefings, classifiers, everything.

COMPACT_STORY = f"""YOU ARE ROBERT-JAN MASTENBROEK — write in FIRST PERSON ("I", "my") always. Never third person.

ONE-LINE SPINE (the through-line in every email):
Dutch producer. Built a €6M platform, lost it all at 30, rebuilt in Tenerife. Makes electronic music with biblical depth. 290K followers, 30+ tracks, no label.

FACTS (never invent, never exaggerate):
- 21: offered record deal → walked away, kept masters
- 27: built Dream or Donate — largest crowdfunding platform in NL & BE, €6M raised, became a millionaire
- 30: hack + blackmail + national media storm destroyed everything — businesses, properties, Bitcoin, reputation. Repaid every creditor despite no legal obligation.
- Tenerife: chose the island over rebuilding in NL where my name was destroyed. Lived in a camper van on the south coast. Performed as a €750/hr vocalist at weddings. Found more peace than I'd had in years.
- Faith: church age 10–19, left (the people, not God). One night alone in Tenerife, on my knees, I prayed one thing: to be happy again the way I was as a child. That was the return. Everything changed.
- Music: faith + dance floor. Hebrew lyrics, Biblical texts, Psalms in electronic production. Not worship music in the traditional sense — but for me it is worship. Jesus-loving raver.
- Now: 290K IG, 30+ tracks all owned, weekly releases, free Sunset Sessions every Friday in Tenerife. No label, no manager, no agent.
- Mission: ancient truth, carried by future sound, to people who need it most.
- I don't make Christian music. I make electronic music with Christian depth.

BRAND: Direct, peer-to-peer, no "world-class"/"groundbreaking". Faith is depth not headline (except faith audiences). Sign off: Robert-Jan / robertjanmastenbroek.com | {ARTIST['instagram']}"""


# ─── SMYKM Email Framework ─────────────────────────────────────────────────────
# 7-step cold email method. Used by template_engine for all outreach.

SMYKM_FRAMEWORK = """
SMYKM EMAIL FRAMEWORK — follow this 7-step sequence exactly, no skips:

1. SUBJECT: 2–4 words, lowercase. Hyper-specific to this person only — if it fits anyone else, rewrite it.
2. OPENER: One specific researched observation (episode quote, post, career move). Zero pleasantries. Must be verifiable.
3. TRANSITION: One sentence bridging their world to this pitch.
4. CHALLENGE: Their specific current problem — not a generic industry problem.
5. VALUE PROP: Direct solution. Understood in 2 seconds (One Mississippi test).
6. HIDDEN OBJECTION: One sentence defusing the likeliest reason they'll ignore this.
7. CLOSE: One interest question. No meeting ask. No calendar link.

HARD RULES:
- 120 words max (entire body)
- 1–2 sentences per paragraph, line breaks for white space
- Confident, peer-to-peer, modern — meaning discovered, never announced
- Banned phrases (any = rewrite): "I am reaching out", "I hope this finds you well", "I know you are busy", "Just following up", "My name is Robert-Jan and I", "I've been following your [show/work]", "I wanted to reach out", "We help [X] do [Y]"
- Language: EN for EN contacts, Dutch for NL/BE, German for DE/AT/CH"""


# ─── Voice Rules — lazy-loaded from BRAND_VOICE.md ────────────────────────────
_VOICE_CACHE: str | None = None
_BRAND_DNA_CACHE: str | None = None

_BRAND_VOICE_PATH = Path(__file__).parent.parent / "BRAND_VOICE.md"
_BRAND_DNA_PATH   = Path(__file__).parent.parent / "BRAND_DNA.md"


def get_voice_rules() -> str:
    """
    Returns a compact voice extract from BRAND_VOICE.md.
    Lazy-loaded and cached — reads the file once.
    Use for: social captions, content agents, any non-email Claude call.
    """
    global _VOICE_CACHE
    if _VOICE_CACHE is not None:
        return _VOICE_CACHE

    if not _BRAND_VOICE_PATH.exists():
        _VOICE_CACHE = ""
        return _VOICE_CACHE

    try:
        text = _BRAND_VOICE_PATH.read_text(encoding="utf-8")
        # First 1200 chars covers: Mission, Compass, Tone, Villain — enough for captions/briefings
        _VOICE_CACHE = text[:1200].strip()
    except Exception:
        _VOICE_CACHE = ""
    return _VOICE_CACHE


def get_brand_dna() -> str:
    """
    Returns key rules from BRAND_DNA.md.
    Lazy-loaded. Use for: strategy decisions, content audits.
    """
    global _BRAND_DNA_CACHE
    if _BRAND_DNA_CACHE is not None:
        return _BRAND_DNA_CACHE

    if not _BRAND_DNA_PATH.exists():
        _BRAND_DNA_CACHE = ""
        return _BRAND_DNA_CACHE

    try:
        text = _BRAND_DNA_PATH.read_text(encoding="utf-8")
        _BRAND_DNA_CACHE = text[:1500].strip()
    except Exception:
        _BRAND_DNA_CACHE = ""
    return _BRAND_DNA_CACHE


# ─── Podcast Angles ────────────────────────────────────────────────────────────

def get_podcast_angles_text() -> str:
    """Formatted podcast story angles for injection into any Claude prompt."""
    lines = ["PODCAST STORY ANGLES (pick the best fit for this show's audience):"]
    for key, text in PODCAST_ANGLES.items():
        label = key.replace("_", " ").title()
        lines.append(f"  [{label}] {text[:200]}")
    return "\n".join(lines)


# ─── Full brand context (non-email) ────────────────────────────────────────────

def get_full_brand_context() -> str:
    """
    Complete brand context for general Claude prompts (captions, briefings, analysis).
    Includes compact story + voice rules. Does NOT include SMYKM (email-specific).
    """
    voice = get_voice_rules()
    parts = [COMPACT_STORY]
    if voice:
        parts.append(f"\nBRAND VOICE (from BRAND_VOICE.md):\n{voice}")
    return "\n".join(parts)


# ─── Canonical URLs ────────────────────────────────────────────────────────────
SPOTIFY_ARTIST_URL = ARTIST["spotify_artist"]
ARTIST_INSTAGRAM   = ARTIST["instagram"]
ARTIST_WEBSITE     = ARTIST["website"]
ARTIST_EMAIL       = ARTIST["email"]
