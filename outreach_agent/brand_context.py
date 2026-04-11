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


# ─── Track → Scripture mapping ────────────────────────────────────────────────
# Single source of truth for track-scripture anchors and pitch angles.
# Import this in any agent that selects a track to pitch per contact type.
# Spotify links sourced from story.py TRACKS — do not hardcode links elsewhere.

from story import TRACKS as _TRACKS


def _track_spotify(title: str) -> str:
    """Look up canonical Spotify link for a track by title."""
    for tracks in _TRACKS.values():
        for t in tracks:
            if t["title"] == title:
                return t["spotify"]
    return SPOTIFY_ARTIST_URL  # fallback to artist page


TRACK_SCRIPTURE: dict[str, dict] = {
    "Renamed": {
        "ref":     "Isaiah 62",
        "angle":   "new name, new identity — the moment everything changed",
        "bpm":     130,
        "genre":   "tribal techno",
        "spotify": _track_spotify("Renamed"),
    },
    "Halleluyah": {
        "ref":     None,
        "angle":   "pure praise, 140 BPM — the body as an act of worship",
        "bpm":     140,
        "genre":   "psytrance",
        "spotify": _track_spotify("Halleluyah"),
    },
    "Jericho": {
        "ref":     "Joshua 6",
        "angle":   "walls come down — eventually, always",
        "bpm":     140,
        "genre":   "psytrance",
        "spotify": _track_spotify("Jericho"),
    },
    "Fire In Our Hands": {
        "ref":     None,
        "angle":   "130 BPM tribal — the call to act",
        "bpm":     130,
        "genre":   "tribal techno",
        "spotify": _track_spotify("Fire In Our Hands"),
    },
    "Living Water": {
        "ref":     "John 4",
        "angle":   "what the soul is actually thirsty for",
        "bpm":     124,
        "genre":   "melodic techno",
        "spotify": _track_spotify("Living Water"),
    },
    "He Is The Light": {
        "ref":     "John 8",
        "angle":   "light in every room, including this one",
        "bpm":     122,
        "genre":   "melodic techno",
        "spotify": _track_spotify("He Is The Light"),
    },
}


def get_track_for_contact(genre: str = "", notes: str = "") -> dict:
    """
    Select the best TRACK_SCRIPTURE entry for an outreach contact.
    Falls back to Renamed (strongest crossover) if no match.

    Args:
        genre: contact's playlist/podcast genre string
        notes: contact's personalisation notes

    Returns:
        TRACK_SCRIPTURE entry dict with title included as 'title' key.
    """
    combined = (genre + " " + notes).lower()
    if any(w in combined for w in ("psytrance", "goa", "140")):
        title = "Halleluyah"
    elif any(w in combined for w in ("tribal", "ethnic", "130")):
        title = "Renamed"
    elif any(w in combined for w in ("faith", "christian", "worship", "church")):
        title = "He Is The Light"
    elif any(w in combined for w in ("melodic", "house", "124", "deep")):
        title = "Living Water"
    else:
        title = "Renamed"  # default crossover track

    entry = dict(TRACK_SCRIPTURE[title])
    entry["title"] = title
    return entry


# ─── Seeker audience profile ──────────────────────────────────────────────────
# Compact extract of BRAND_VOICE.md Part 3. Defined inline (not file-read)
# so it never silently fails if the file moves or is renamed.
# Use in discovery and research agents to score audience fit.

SEEKER_PROFILE = """THE SEEKER — RJM's Target Audience

WHO: Someone searching for God in their own way — through music, substances, experiences, escapes.
Not finding it. Trying harder. Still not finding it. Then walking into a room where something is different.

EXTERNAL PROBLEM: They've tried everything — the substances, the scenes, the self-help books.
None of it lasts. The escape ends. Something is still missing.

INTERNAL PROBLEM: They feel spiritually homeless. They believe in *something* but the church felt
hypocritical or irrelevant, and the rave felt true but hollow. No room was made for both halves.

PHILOSOPHICAL PROBLEM: Nobody told them that sacred experience and a 140 BPM dance floor can exist
in the same room. The church took one half. The club took the other. The lie is they have to choose.

FALSE BELIEFS THEY CARRY:
- "I can't dance like that sober."
- "Believing in God means Sunday morning in a building I don't belong in."
- "Jesus doesn't show up at raves."
- "I'm too far gone for the church crowd."

WHO THIS IS NOT FOR: People who came for the content. The explicitly churched. Those who need
the artist to lead with their faith credentials.

SEEKER MARKERS: Goes searching. Stays curious. Suspicious of institutions but not of truth.
Loves music that moves something deeper than the body. Has felt God in unexpected places
(a sunset, a conversation, a melody) — even if they don't call it God."""


# ─── Discovery filter ──────────────────────────────────────────────────────────

def get_discovery_filter() -> str:
    """
    Returns targeting rules for injection into discovery/research agent Claude prompts.
    Call this in any agent that qualifies new contacts before adding to the pipeline.
    """
    return """DISCOVERY FILTER — RJM Brand Fit (Subtle Salt Principle)

TARGET audiences — music leads, faith surfaces naturally:
- Melodic techno, tribal psytrance, world/ethnic electronic listeners
- Rave culture, consciousness, flow state, secular wellness audiences
- People who feel something sacred without having a vocabulary for it
- "Spiritual but not religious" communities
- Podcast audiences: electronic music, personal transformation, expat/nomad life

AVOID — do not qualify these contacts:
- Christian-specific playlists (worship, gospel, CCM, contemporary Christian music)
- Explicitly faith-branded podcasts or platforms that expect a Christian artist identity
- Church music editorial playlists
- Audiences where secular rave attendees would feel out of place

AUDIENCE TYPE TAGS — assign exactly one per contact:
  seeker         → searching, spiritual-but-not-religious, rave/festival culture
  music-first    → genre-only focus, no spiritual dimension visible or needed
  faith-adjacent → open to spiritual themes, not explicitly Christian
  avoid          → explicitly Christian/church audience — do not add to pipeline

SUBTLE SALT CHECK (mandatory gate before adding any contact):
  Ask: "Would a secular rave attendee who has never been to church share this
  playlist / appear on this podcast willingly — and enjoy it?"
  If yes: qualify the contact.
  If no: tag as 'avoid', do not append to contacts.csv."""
