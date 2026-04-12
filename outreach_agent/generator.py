"""
Content generator — two-call architecture.

Call 1 (generate_hooks):   Hook-only, temperature 1.0.
                            Produces 5 ranked candidates per clip with mechanism labels.
                            Returns A/B/C variants:
                              A = scroll-stopper (tension/rupture preferred)
                              B = identity or scene mechanism
                              C = claim or contrarian mechanism

Call 2 (generate_content): Platform captions + structured JSON, temperature 0.4.
                            Uses A/B/C hooks from Call 1 for context.
                            Captions are written to work with the angle — any of the 3 variants.

Filename convention: trackname_angle_description.mp4
  renamed_emotional_3am-in-studio.mp4   → track=Renamed, angle=emotional, seed="3am in studio"
  renamed_signal_for-the-rebuilding.mp4 → track=Renamed, angle=signal,   seed="for the rebuilding"
  renamed_energy_hamburg-set.mp4        → track=Renamed, angle=energy,   seed="hamburg set"
"""

import glob as _glob
import os
import re
import json
import logging
import subprocess

logger = logging.getLogger(__name__)

# Brand voice gate (optional, non-blocking)
try:
    import brand_gate as _brand_gate
    _BRAND_GATE_AVAILABLE = True
except ImportError:
    _BRAND_GATE_AVAILABLE = False

# ── Claude CLI ─────────────────────────────────────────────────────────────────

def _find_claude() -> str:
    """Return path to the claude CLI binary (uses Max plan OAuth — no API key needed)."""
    for path in ["/usr/local/bin/claude", "/opt/homebrew/bin/claude"]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    pattern = os.path.expanduser(
        "~/Library/Application Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude"
    )
    matches = sorted(_glob.glob(pattern), reverse=True)
    if matches:
        return matches[0]
    raise RuntimeError("claude CLI not found. Make sure Claude Code is installed.")

CLAUDE_BIN = _find_claude()


def _call_claude(system_prompt: str, user_prompt: str, timeout: int = 120) -> str:
    """Call claude CLI and return the text response. Uses Max plan — no API key."""
    result = subprocess.run(
        [CLAUDE_BIN, "--model", "claude-haiku-4-5-20251001",
         "--print", "--no-session-persistence",
         "--system-prompt", system_prompt, user_prompt],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error: {result.stderr[:300]}")
    return result.stdout.strip()


# ── Artist context ─────────────────────────────────────────────────────────────
ARTIST_CONTEXT = """
WHO HE IS (feel this, don't just read it):
Robert-Jan Mastenbroek is a Dutch producer who moved to the Atlantic edge of Tenerife and never
left. He plays abandoned lots, cliff edges, rooftops, beach bars — anywhere a speaker fits and a
crowd finds its way. Every week he runs Sunset Sessions: free gatherings in unexpected outdoor
locations, no ticket, no stage, no church bulletin. He makes Hebrew psytrance, melodic techno,
tribal psytrance — all of it rooted in Scripture, none of it sounds like it should be.

THE TENSION THAT MAKES THIS INTERESTING:
He is not trying to bring you to God. He is making music from inside his relationship with God,
in rooms where that relationship has no official standing. The rave floor and the sanctuary use
the same neural hardware — both suppress ego, both produce communal states people call
transcendent. He knows this. Most of his audience feels it without knowing why.

HIS WORLD:
Atlantic light. Volcanic rock. A Dutchman who chose an island at the edge of Europe. The ocean
fifty meters from where he works. Crowds that don't know they're in a congregation until the drop
hits and something shifts in the room. Tenerife is not a backdrop — it is the specific place this
music comes from.

BRAND VOICE:
Raw. Specific. Unhurried. Never argues for itself. Never explains the faith. Shows up somewhere
specific, makes something real, lets people find their own way in. No performance of belief.
No apology for it either. The spiritual content arrives late or by implication — never as the
opening move. The secular listener should feel the hook fully. The believer sees a second layer.
"""

# ── Subtle Salt principle ──────────────────────────────────────────────────────
SUBTLE_SALT_LAYER = """
SUBTLE SALT (Matthew 5:13) — the faith principle for all content:
Faith is woven in as undertone. Never as pitch. Never as the opening move.

- "The room went quiet for the wrong reasons" → better than "God moved tonight"
- "Made this at 3am when the only honest thing left was the track" → the faith is in the specificity
- A hook about unresolved tension at 3am is more spiritual than a hook about transcendence
- The sacred/secular collision lives in the specific detail, not in the claim about it
- If the hook could appear on any Christian music page, it has failed this filter
- The secular listener should feel the hook fully. The believer sees a second layer.
"""

# ── Hook mechanism library ─────────────────────────────────────────────────────
HOOK_MECHANISM_LIBRARY = """
HOOK MECHANISM LIBRARY — study why each works, not the words themselves:

[TENSION] "Made this the night everything fell apart."
WHY: Specific time + physical reaction + unresolved cause. Brain needs to know what happened.
AVOID: "This song makes me feel things" — same intent, zero specificity, no gap to close.

[IDENTITY] "This one's for the believers nobody sees."
WHY: Names a real tribe without naming the religion. The right person feels found.
AVOID: "For all the Christians who love music" — labels instead of resonates.

[SCENE] "400 people, one sunset, no stage."
WHY: Three concrete specifics, one spatial subversion. Brain builds the image before deciding.
AVOID: "Live at Sunset Sessions" — event name means nothing to a stranger.

[CLAIM] "Nobody is making music like this right now."
WHY: Dares the viewer to fact-check it. Resistance is engagement.
AVOID: "Check out this unique track" — describes rather than challenges.

[RUPTURE] "I used to hate this kind of music."
WHY: The creator betrays the expected identity. Brain recalibrates and wants the explanation.
AVOID: "Music that transcends genre" — genre commentary, not human truth.

A/B/C variants — ALL THREE stay inside their assigned angle. Angle rules dominate.
Use different mechanisms across A/B/C for variety, but never let mechanism selection
override angle identity. A "signal" clip must still feel like Signal even in variant C.
"""

# ── Named failure modes ────────────────────────────────────────────────────────
HOOK_FAILURE_MODES = """
FAILURE MODES — these are exactly what you are NOT writing:

Generic failures:
- "Follow for more sacred techno" — genre label + CTA, no reason to stop
- "You won't want to miss this" — empty urgency the brain has learned to ignore
- "This music will move your spirit" — vague promise, nothing to simulate
- "Bringing the gospel to the dancefloor" — mission statement, not a hook

Spiritual failures (manipulative even when unintentional):
- CROSS-CONTAMINATION: "this drop hits different when you know who made the universe"
  — sacred and secular stapled together without integration, the seam is visible
- TESTIMONY PIVOT: starts secular, pivots to religious payoff as the punchline
  — instrumentalises a dark period as contrast material
- CREDENTIAL HOOK: using faith to claim superior access to something
  — positions RJM between the audience and God
- BUZZWORD STACK: "Sacred. Soul. Spirit. Transcendence."
  — spiritually loaded, functionally empty in aggregation
- APOLOGY HOOK: "I know this might sound weird, but it's kind of spiritual..."
  — pre-managing the audience's discomfort
- CONVERSION INVERSION: implying the music will do something spiritual to you
  — turns a transcendent experience into a product feature
"""

# ── Brand voice layer (from BRAND_VOICE.md — built from 23-question interview) ──
BRAND_VOICE_LAYER = """
BRAND VOICE — THE SIX RULES (from RJM's own words, not invented)

1. DESCENDING, NOT ASCENDING
Every other guru, self-help system, and spiritual brand tells people to climb up.
This brand descends — comes toward people, meets them where they are.
Never position RJM above the audience. Never lecture, improve, fix, or elevate.
The voice comes from the floor, not the stage.

2. JOY IS THE PRIMARY EMOTION — NOT URGENCY, NOT SCARCITY
RJM said: "I feel happy as a child all day, every day now. Because I know I have a father in heaven who loves me."
The underlying emotional register of everything is child-like, unguarded, free joy.
Not "here's what you're missing" — "here is what I found and I want you to feel it too."
No hustle. No countdown. No pressure.

3. HONEST ABOUT UNCERTAINTY
His exact words: "Nobody knows the truth, until we die. But I believe."
The voice is allowed to not have all the answers. Conviction without crushing doubt.
This disarms cynicism instantly. It does not claim superiority. It shares.

4. PLANT SEEDS — DON'T HARVEST
"All we can do is plant seeds. The rest God will do."
Content does not pressure. Does not convert. Does not explain.
Places a moment, a lyric, a beat — trusts that something else does the rest.
Never close the loop for the audience. Leave space.

5. THE TWO-STAGE FUNNEL — music builds, events convert
MUSIC CONTENT (clips, studio, track promo — the daily engine):
Goal: audience growth. Target feeling: "I need to save this / follow this artist / hear more."
The hook and caption create music pull — curiosity, recognition, the need to find the full track.
CTA always drives to Spotify. This is how 1M listeners is built.

EVENT CONTENT (crowd footage, live sets, Holy Rave):
Goal: conversion. Target feeling: "I need to be in that room."
Casual listeners become true fans when they experience the live show.
"You just should have been there" is the ideal word-of-mouth — but the CTA still drives Spotify,
because new people discovering the event clip haven't heard the music yet.

6. THE SEEKER IS THE AUDIENCE
Not Christians. Not techno fans. The person searching for God in their own way —
through drugs, sex, relationships, experiences — finding something different in the room.
A joy, a hope, a freedom they didn't find anywhere else.
They may not call it God. They don't need to yet.
The SIGNAL angle specifically targets this person.

WORDS NEVER TO USE:
blessed / anointed / fellowship / spiritual journey / elevated consciousness /
manifestation / vibration (in new-age sense) / safe space / worship music /
Christian music / sober rave / exclusive / ascend / better version of yourself /
curated / intentional (as performance word) / sacred energy / transcendence (as a claim)

THE SCHIJNHEILIG CONTRAST (the villain — name it in your head, never say it aloud):
The enemy is performative holiness — people who found God and built a wall around Him.
Content should feel like the opposite of that: open, present, unguarded, no dress code.
"""

# ── Spotify growth model (Nic D method) ───────────────────────────────────────
NIC_D_SPOTIFY_LAYER = """
SPOTIFY GROWTH MODEL — every caption drives search + save:
The goal is not passive streams. It is active saves. Spotify's algorithm rewards tracks with
>5% save rate — that triggers Discover Weekly, Radio, Release Radar.

CTA rules:
- Always name the track: "Search [track name] on Spotify" or "Find [track name] — link in bio"
- Never include a URL. Text CTAs only.
- "Link in bio" alone is not enough — the track name must be in the CTA.
- The CTA belongs in the caption body, never in the hook.
"""

# ── Content type descriptions ──────────────────────────────────────────────────
CONTENT_TYPES = {
    'event':        'Crowd footage from Sunset Sessions — people, energy, atmosphere',
    'studio':       'Behind-the-scenes music production / studio content',
    'talking_head': 'Robert-Jan speaking directly to camera',
    'music_video':  'Finished music video or performance footage',
}

# ── Angle detection keywords ───────────────────────────────────────────────────
ANGLE_KEYWORDS = {
    'emotional': ['emotional', 'story', 'personal', 'heart', 'soul', 'why', 'meaning', 'wrote'],
    'signal':    ['signal', 'stakes', 'person', 'for-the', 'forthe', 'need', 'when'],
    'energy':    ['energy', 'live', 'crowd', 'rave', 'set', 'dance', 'floor', 'sunset', 'session'],
}

# ── Per-angle hook instructions (multi-sub-mode, not single-route) ──────────────
ANGLE_INSTRUCTIONS = {

    'emotional': """ANGLE: EMOTIONAL — The artist's interior. What this track cost. Why it exists.

Psychological target: Self-referential processing + Zeigarnik effect (unresolved tension).
The viewer's brain must involuntarily map this onto their own experience — then stay because
the tension is not resolved.

FIVE SUB-MODES — pick whichever produces the sharpest hook for this specific track.
Do NOT default to sub-mode 1 every time. Vary across runs.

[COST] The price of making it. Hours lost, versions abandoned, the moment it almost didn't exist.
  Entry points: specific session time, number of attempts, what was sacrificed to finish it.
  "Six versions before this one // none of them were honest enough"

[NAMING] Tracks often exist before they have a name. What was it called before? What does the
  title actually mean to the person who made it — not the public definition.
  Entry points: the moment the name arrived, what it replaced, what the name carries.
  "Named it after the one thing // I stopped expecting to come back"

[DOUBT] The wall. The session where it felt like nothing. What made him keep going anyway.
  Entry points: the specific form doubt took, what the room felt like, what broke the block.
  "Sat with it for three weeks // still don't know what made it move"

[DEVOTION] Making from inside a relationship with something larger. Not explaining the faith —
  showing what it looks like from inside. The discipline, the silence, the unknown receiver.
  Entry points: why he makes music nobody asked for, what he's making toward, the unseen audience.
  "Built for one person who // hasn't heard it yet and already needs it"

[RUPTURE] The moment his identity broke from the expected. Dutch, producer, island, Scripture —
  none of it should coexist, but it does. The contradiction that became the art.
  Entry points: what he gave up to be here, what surprised him about his own work, the before.
  "Used to make music for rooms // now makes it for whatever's between the rooms"

UNIVERSAL RULES FOR EMOTIONAL:
- NEVER open with "I". Start with the moment, number, or situation.
- END on unresolved tension — never the resolution. Leave the loop open.
- Avoid all category words: sacred, techno, rave, worship, feel, soul, journey, emotion, music.
- One concrete anchor required: a time, number, physical sensation, or specific action.
- NO geographic place names — geography belongs in captions.
- Specificity test: could this only have been written by someone who lived this exact thing?""",

    'signal': """ANGLE: SIGNAL — Where this track finds you. The specific person or moment it was made for.

Psychological target: Personal relevance trigger + anticipatory ownership.
The viewer must feel this track was sent specifically to them, for a situation they already know.
The save becomes an act of keeping something they don't want to lose access to.

FIVE SUB-MODES — pick whichever produces the sharpest hook for this specific track.
Do NOT default to sub-mode 1 every time. Vary across runs.

[FINDER] The exact person this track finds, named with surgical precision — not a type,
  a specific situation or internal state almost nobody talks about publicly.
  Entry points: a behavior, a secret, a quiet decision, something they stopped doing.
  "For the person who deleted the draft // and called it not ready"

[PERMISSION] What this track gives someone that they couldn't give themselves.
  Not comfort — permission. The right to stop performing, stop explaining, stop waiting.
  Entry points: something they've been holding, something they've been postponing.
  "Permission to stop translating it // for people who weren't there"

[RECOGNITION] The moment they realize this is for them. Not described — enacted.
  The hook IS the recognition. They feel found before they understand why.
  Entry points: the specific detail that only the right person understands.
  "Still checking your phone at 3am // for a message that isn't coming"

[SEASON] The specific life season this track belongs to — not a mood, a period.
  After something ended. Before something starts. The in-between that has no name.
  Entry points: what just happened, what hasn't happened yet, what's quietly true right now.
  "Made for the months between // the ending and knowing what comes next"

[UNSAID] Something the listener hasn't been able to say, that this track says for them.
  The save is an act of deputizing the music to carry something they can't carry in words.
  Entry points: an emotion without a word, a truth they're holding, a conversation they haven't had.
  "Everything you haven't said // to the one person it would actually matter to"

UNIVERSAL RULES FOR SIGNAL:
- Name the EXACT situation — not a general mood, a specific state.
- Stakes must be present: what does this person need that this track provides?
- Creates the felt need to save for later — "I will need this."
- NEVER open with "I". Start with "For", a direct address, or drop into the situation.
- Can address directly ("you") or describe a third-party situation at a distance.""",

    'energy': """ANGLE: ENERGY — What happens to a room. The collective moment.

Psychological target: Embodied simulation + interoceptive cue triggering + motor cortex priming.
The viewer must simulate being inside a body on that floor — not watching from outside.

FIVE SUB-MODES — pick whichever produces the sharpest hook for this specific track.
Do NOT default to sub-mode 1 every time. Vary across runs.

[BODY] A specific body part responds before the mind does. Involuntary. Immediate. Precise.
  Pick surprising body parts — NOT sternum (banned). NOT chest. NOT heart.
  Fresh targets: collarbone, the small of the back, the soft inside of the elbow, the roof of the mouth,
  the space behind the eyes, knuckles, the base of the skull, the inside of the wrist.
  "Collarbone lifts on the second bar // nobody told it to"

[TIME] At high BPM the brain loses its grip on clock time. Hours collapse into minutes.
  Reference the specific disorientation — what they planned to do that didn't happen.
  Entry points: what time they thought it was, how long they actually stayed, what they forgot.
  "Planned to stay one hour // two hours later nobody had moved"

[GEOGRAPHY] Atlantic cliff + volcanic rock + 130 BPM. This is a specific place on earth.
  The collision of natural landscape and electronic music is rare. Name it precisely.
  Entry points: the salt, the wind, the height, the ocean underneath the bass.
  "Salt air and a 130 BPM kick // on a cliff nobody planned to stay this long"

[THRESHOLD] The exact moment before the drop. The held breath. The stillness before the shift.
  Describe what the room is like in that 2-3 second window. What happens in bodies, not speakers.
  Entry points: what people do with their hands, whether they're looking at each other or away.
  "Eight seconds of silence // and the whole cliff already knew"

[DISSOLUTION] The moment the individual disappears into the collective. Not metaphor — neuroscience.
  At high volume + BPM, the brain's default mode network quiets. Ego drops. Self-reference stops.
  This is what makes a rave sacred without anyone calling it sacred.
  Entry points: the moment they forgot their name, what they stopped worrying about, the after.
  "Nobody remembers their name // for the length of the breakdown"

UNIVERSAL RULES FOR ENERGY:
- Present tense, declarative. No questions.
- Avoid all rave/genre clichés: dark, pounding, euphoric, underground, transcendent — noise words.
- NEVER open with "I". Start with a body part, a number, a room, a sensory detail, or a moment.
- The sacred/rave collision: assert it, don't explain it.""",
}

ANGLE_DEFAULT_INSTRUCTION = """ANGLE: Undetected — default to Signal.
Where does this track find its listener? What specific life moment or state is it for?
All other hook rules apply: specific, located, never open with "I", no category language."""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _song_title_only(track_name: str) -> str:
    """Build a short but findable Spotify search term.
    'Robert-Jan Mastenbroek & LUCID - Fire In Our Hands' → 'Fire In Our Hands Robert-Jan'
    'Renamed' → 'Renamed Robert-Jan'
    """
    if not track_name:
        return track_name
    song = track_name.rsplit(' - ', 1)[-1].strip() if ' - ' in track_name else track_name
    return f"{song} Robert-Jan"


# ── Content type detection ─────────────────────────────────────────────────────

def detect_content_type(filename: str) -> str:
    """Guess content type from filename keywords."""
    name = filename.lower()
    if any(k in name for k in ['crowd', 'event', 'session', 'rave', 'dance', 'sunset']):
        return 'event'
    if any(k in name for k in ['studio', 'produce', 'making', 'daw', 'desk']):
        return 'studio'
    if any(k in name for k in ['talk', 'face', 'selfie', 'vlog']):
        return 'talking_head'
    if any(k in name for k in ['mv', 'video', 'official', 'clip']):
        return 'music_video'
    return 'event'


def parse_filename_metadata(filename: str) -> dict:
    """
    Extract track name, angle, and seed hint from filename.
    Naming convention: trackname_angle_description.mp4
    """
    name = os.path.splitext(filename)[0].lower()

    angle = None
    for angle_key, keywords in ANGLE_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            angle = angle_key
            break

    for sep in ['--', '_']:
        if sep in name:
            segments = name.split(sep)
            track_name = segments[0].strip().title() if segments[0].strip() else None
            seed_hint = None
            if len(segments) >= 3:
                seed_hint = ' '.join(segments[2:]).replace('-', ' ').strip()
            return {
                'track_name': track_name,
                'angle':      angle,
                'seed_hint':  seed_hint,
            }

    return {'track_name': None, 'angle': angle, 'seed_hint': None}


# ── Call 1: Hook generation ────────────────────────────────────────────────────

def generate_hooks(filename: str, clip_lengths: list,
                   strategy_notes: str = None, angle_override: str = None) -> dict:
    """
    Call 1 — Hook-only generation. Temperature 1.0. No JSON.
    Produces 5 ranked candidates per clip length with mechanism labels.
    Assigns A/B/C variants from those 5 candidates.

    angle_override: bypasses filename detection (used when main assigns fixed angle per clip).

    Returns:
    {
        'track_name': str | None,
        'angle':      str | None,
        'seed_hint':  str | None,
        'hooks':      {
            5:  {'a': str, 'b': str, 'c': str},
            9:  {'a': str, 'b': str, 'c': str},
            15: {'a': str, 'b': str, 'c': str},
        },
    }
    """
    meta = parse_filename_metadata(filename)
    track_name = meta['track_name']
    angle      = angle_override or meta['angle']
    seed_hint  = meta['seed_hint']

    angle_instruction = ANGLE_INSTRUCTIONS.get(angle, ANGLE_DEFAULT_INSTRUCTION)

    track_block    = f"TRACK: {track_name}" if track_name else "TRACK: Unknown"
    seed_block     = f"SEED CONTEXT (build from this specific moment): {seed_hint}" if seed_hint else ""
    strategy_block = f"\nPERFORMANCE LEARNINGS (apply to improve results):\n{strategy_notes}" if strategy_notes else ""

    lengths_example = clip_lengths[0] if clip_lengths else 5

    system_prompt = f"""You write hooks for short-form video. Your only job right now is hooks —
not captions, not hashtags. Just the 5-8 words burned into the first frame that make someone
stop scrolling on a rave-adjacent social feed.

{ARTIST_CONTEXT}
{SUBTLE_SALT_LAYER}
{HOOK_MECHANISM_LIBRARY}
{HOOK_FAILURE_MODES}

UNIVERSAL HOOK RULES:
- 5-8 words. Must be readable in under 2 seconds.
- NEVER open with "I". Start with situation, time, place, number, or body.
- No exclamation marks. The energy is internal, not performative.
- The hook that feels too much is usually the right one. Do not self-censor toward safe.
- Every hook must be LOCATED: a specific time, place, physical sensation, or concrete detail.
  Generic hooks are about music. Holy Rave hooks are about a specific moment music made real.
- Never describe what the music sounds like. Describe what it does to a body or a moment.
- No Spotify CTAs in the hook. That belongs in the caption."""

    user_prompt = f"""{track_block}
{seed_block}
{strategy_block}

CLIP LENGTHS NEEDED: {', '.join(str(l) + 's' for l in clip_lengths)}

{angle_instruction}

For EACH clip length, generate 5 hook candidates.
Rank them 1 (most scroll-stopping) to 5.
After each hook, add: | mechanism: [tension/identity/scene/claim/rupture]

Format exactly as shown below — no preamble, no explanation, nothing else:

--- {lengths_example}s ---
1. Hook text here | mechanism: tension
2. Hook text here | mechanism: identity
3. Hook text here | mechanism: scene
4. Hook text here | mechanism: claim
5. Hook text here | mechanism: rupture

(repeat block for each clip length)

Do not explain your choices. Do not apologise for bold hooks. Bold is correct."""

    try:
        raw        = _call_claude(system_prompt, user_prompt, timeout=120)
        candidates = _parse_hook_candidates(raw, clip_lengths)
        hooks      = _assign_abc_hooks(candidates)
        logger.info(f"Hooks generated: {filename} | angle={angle}")
        for l, abc in hooks.items():
            logger.info(f"  {l}s A→ \"{abc['a']}\"")

    except Exception as e:
        logger.error(f"Hook generation failed for {filename}: {e}")
        fb = _fallback_hook(angle)
        hooks = {l: {'a': fb, 'b': fb, 'c': fb} for l in clip_lengths}

    if _BRAND_GATE_AVAILABLE:
        for length, abc in hooks.items():
            for variant in ('a', 'b', 'c'):
                if abc.get(variant):
                    abc[variant] = _brand_gate.gate_or_warn(abc[variant], context=f"generator.hooks.{variant}")

    return {
        'track_name': track_name,
        'angle':      angle,
        'seed_hint':  seed_hint,
        'hooks':      hooks,
    }


def _force_two_part(text: str) -> str:
    """
    Split a single-line hook into OPENER // REVEAL at the best natural break:
    comma, semicolon, 'and'/'but'/'then'/'so', or word midpoint fallback.
    """
    # Try splitting at a comma or semicolon near the middle
    words = text.split()
    mid   = len(words) // 2
    for i in range(mid, len(words)):
        if words[i].endswith(',') or words[i].endswith(';'):
            opener = ' '.join(words[:i + 1]).rstrip(',;')
            reveal = ' '.join(words[i + 1:])
            if reveal:
                return f"{opener} // {reveal}"
    for i in range(mid - 1, 0, -1):
        if words[i].endswith(',') or words[i].endswith(';'):
            opener = ' '.join(words[:i + 1]).rstrip(',;')
            reveal = ' '.join(words[i + 1:])
            if reveal:
                return f"{opener} // {reveal}"
    # Try splitting before a conjunction near the middle
    conjunctions = {'and', 'but', 'then', 'so', 'because', 'until', 'before', 'after', 'while'}
    for i in range(mid, len(words)):
        if words[i].lower() in conjunctions:
            opener = ' '.join(words[:i])
            reveal = ' '.join(words[i:])
            if opener and reveal:
                return f"{opener} // {reveal}"
    # Hard split at word midpoint
    return ' '.join(words[:mid]) + ' // ' + ' '.join(words[mid:])


def _normalize_hook(text) -> str:
    """Ensure hook is a plain string. Handles dict objects or dict-syntax strings from Claude."""
    if isinstance(text, dict):
        return text.get('a') or (list(text.values()) or [''])[0]
    if not isinstance(text, str):
        return str(text)
    # Detect dict-syntax string like "{'a': 'foo', 'b': 'bar'}" and extract 'a'
    if text.strip().startswith('{') and "'a'" in text:
        m = re.search(r"'a'\s*:\s*['\"](.+?)['\"]", text)
        if m:
            return m.group(1).strip()
    # Strip markdown bold markers that Claude sometimes adds
    text = text.strip('*').strip()
    return text


def generate_run_hooks(track_title: str, clips_config: list) -> dict:
    """
    Single Claude call that generates hooks for ALL clips/angles in one run.
    Ensures hooks are unique across angles — no repeated imagery, themes, or words.

    clips_config: [{'length': 5, 'angle': 'emotional'}, {'length': 9, 'angle': 'signal'}, ...]

    Returns: {5: {'a': str, 'b': str, 'c': str}, 9: {...}, 15: {...}}
    """
    clips_by_length = {c['length']: c['angle'] for c in clips_config}

    # Build per-angle sections for the prompt
    angle_sections = ""
    for clip in clips_config:
        length = clip['length']
        angle  = clip['angle']
        instr  = ANGLE_INSTRUCTIONS.get(angle, ANGLE_DEFAULT_INSTRUCTION)
        angle_sections += f"\n\n{'='*50}\nCLIP {length}s — ANGLE: {angle.upper()}\n{'='*50}\n{instr}\n"

    system_prompt = f"""You write hooks for short-form video. Your only job is hooks.

{ARTIST_CONTEXT}
{SUBTLE_SALT_LAYER}
{BRAND_VOICE_LAYER}
{HOOK_MECHANISM_LIBRARY}
{HOOK_FAILURE_MODES}

UNIVERSAL HOOK RULES:
- 5-8 words. Readable in under 2 seconds.
- NEVER open with "I". Start with situation, time, place, number, or body.
- No exclamation marks. Energy is internal.
- Every hook must be LOCATED: a specific time, place, physical sensation, or concrete detail.
- Never describe what music sounds like — describe what it does to a body or a moment.
- No Spotify CTAs in hooks.

CRITICAL — UNIQUENESS ACROSS ALL CLIPS:
You are writing for {len(clips_config)} different clips in the same run.
Each clip has a different angle. The hooks MUST use completely different:
- Imagery and metaphors
- Time references and locations
- Body parts or sensations
- Emotional registers
If two clips share any visual image, word, or theme, the system has failed.
Write each angle as if the others don't exist.

FORMAT OVERRIDE — THIS RUN USES A DIFFERENT FORMAT THAN THE A/B/C SYSTEM ABOVE:
The A/B/C variant section in the mechanism library does NOT apply here.
Do NOT return dict-style output like {{'a': '...', 'b': '...', 'c': '...'}}
Return ranked numbered lists ONLY, in the exact format shown in the user prompt.
Rank 1 must follow the "OPENER // REVEAL" two-part format.
Ranks 2–5 are single-line hooks. No dict syntax anywhere."""

    user_prompt = f"""TRACK: {track_title}

Generate hooks for {len(clips_config)} clips. Each clip has a different angle.
{angle_sections}

TWO-PART REVEAL FORMAT (rank 1 only):
Rank 1 MUST use the format: "OPENER // REVEAL"
  OPENER: 3–5 words that open a loop — incomplete, located, tension or rupture.
  REVEAL: 3–5 words that escalate or pivot. NOT a resolution. Deepens the wound or shifts the frame.
  The two parts feel like one thought with a held breath in the middle.
  Examples:
    "Started the night everything fell // and that room held it first"
    "For you still carrying it // three years is not a rounding error"
    "Jaw drops before the mind // knows what just happened in there"
  Ranks 2–5 can be single-line hooks (no " // " needed).

For EACH clip, generate 5 ranked candidates (1 = most scroll-stopping).
After each hook add: | mechanism: [tension/identity/scene/claim/rupture]

Format EXACTLY as shown — no preamble, no explanation:

--- {clips_config[0]['length']}s ---
1. OPENER // REVEAL | mechanism: tension
2. Hook text | mechanism: identity
3. Hook text | mechanism: scene
4. Hook text | mechanism: claim
5. Hook text | mechanism: rupture

(repeat block for each clip)

Bold is correct. Do not self-censor."""

    try:
        raw        = _call_claude(system_prompt, user_prompt, timeout=300)
        candidates = _parse_hook_candidates(raw, list(clips_by_length.keys()))

        # Pick rank 1 per clip, enforcing the OPENER // REVEAL two-part format.
        hooks = {}
        for length, cands in candidates.items():
            if not cands:
                hooks[length] = _fallback_hook(clips_by_length.get(length))
                continue
            # Prefer any candidate that already has //
            two_part = next((c['text'] for c in cands if ' // ' in c['text']), None)
            if two_part:
                hooks[length] = two_part
            else:
                # Claude didn't follow the format — split rank 1 at a natural break
                hooks[length] = _force_two_part(cands[0]['text'])

        # Safety: if Claude returned dict-syntax strings, extract the 'a' value
        hooks = {l: _normalize_hook(h) for l, h in hooks.items()}
        logger.info(f"Run hooks generated: {track_title}")
        for length, hook in hooks.items():
            logger.info(f"  {length}s [{clips_by_length.get(length,'?')}] → \"{hook}\"")
        if _BRAND_GATE_AVAILABLE:
            hooks = {l: _brand_gate.gate_or_warn(h, context="generator.run_hooks") for l, h in hooks.items()}
        return hooks

    except Exception as e:
        logger.error(f"Run hook generation failed: {e}")
        return {
            c['length']: _fallback_hook(c['angle'])
            for c in clips_config
        }


def _parse_hook_candidates(raw: str, clip_lengths: list) -> dict:
    """
    Parse ranked hook output from Call 1.
    Returns all candidates per length with mechanisms.
    Returns: {length: [{'text': str, 'mechanism': str}, ...]}
    """
    all_candidates = {}
    sections = re.split(r'---\s*(\d+)s\s*---', raw)
    # sections: [preamble?, length, content, length, content, ...]
    for i in range(1, len(sections) - 1, 2):
        try:
            length  = int(sections[i].strip())
            content = sections[i + 1].strip()
            candidates = []
            for line in content.split('\n'):
                line = line.strip()
                match = re.search(r'^\d+\.\s*(.+?)(?:\s*\|\s*mechanism:\s*(\w+))?$', line)
                if match:
                    candidates.append({
                        'text':      match.group(1).strip(),
                        'mechanism': (match.group(2) or 'other').lower(),
                    })
            if candidates:
                all_candidates[length] = candidates
        except (ValueError, IndexError):
            continue

    # Fill any missing lengths with a single fallback candidate
    for l in clip_lengths:
        if l not in all_candidates:
            all_candidates[l] = [{'text': _fallback_hook(None), 'mechanism': 'tension'}]

    return all_candidates


def _assign_abc_hooks(candidates_by_length: dict) -> dict:
    """
    Assign A/B/C variants from 5 ranked candidates:
    - A = rank 1 overall (tension/rupture preferred — most scroll-stopping)
    - B = best identity or scene mechanism from remaining
    - C = best claim or rupture mechanism not already used
    """
    hooks = {}
    for length, candidates in candidates_by_length.items():
        if not candidates:
            fb = _fallback_hook(None)
            hooks[length] = {'a': fb, 'b': fb, 'c': fb}
            continue

        # A = rank 1
        hook_a = candidates[0]['text']

        # B = first identity or scene not equal to A
        hook_b = next(
            (c['text'] for c in candidates
             if c['mechanism'] in ('identity', 'scene') and c['text'] != hook_a),
            candidates[1]['text'] if len(candidates) > 1 else hook_a,
        )

        # C = first claim or rupture not already used
        used = {hook_a, hook_b}
        hook_c = next(
            (c['text'] for c in candidates
             if c['mechanism'] in ('claim', 'rupture') and c['text'] not in used),
            next((c['text'] for c in candidates if c['text'] not in used), hook_a),
        )

        hooks[length] = {'a': hook_a, 'b': hook_b, 'c': hook_c}

    return hooks


def _fallback_hook(angle: str) -> str:
    """Minimal fallback hooks in two-part reveal format."""
    fallbacks = {
        'emotional': 'Made this the night I almost stopped // and the track knew before I did.',
        'signal':    'For the version of you still carrying it // without telling anyone why.',
        'energy':    'The room stopped. Then everything // moved at once.',
    }
    return fallbacks.get(angle, 'Something shifted here // Atlantic coast, 4am.')


# ── Call 2: Caption generation ─────────────────────────────────────────────────

def generate_content(filename: str, clip_lengths: list,
                     hooks_meta: dict, strategy_notes: str = None) -> dict:
    """
    Call 2 — Platform captions + structured JSON. Temperature 0.4.
    Uses A/B/C hooks from generate_hooks() as angle context.
    Captions work with any of the 3 hook variants — they serve the angle, not one specific hook.

    hooks_meta: return value from generate_hooks()

    Returns:
    {
        'content_type': str,
        'track_name':   str | None,
        'angle':        str | None,
        'clips': {
            '5':  {
                'hook_a': str, 'hook_b': str, 'hook_c': str,
                'tiktok':    {'caption': str, 'hashtags': str},
                'instagram': {'caption': str, 'hashtags': str},
                'youtube':   {'title': str, 'description': str},
            },
            '9':  {...},
            '15': {...},
        }
    }
    """
    content_type = detect_content_type(filename)
    content_desc = CONTENT_TYPES.get(content_type, CONTENT_TYPES['event'])
    track_name   = hooks_meta.get('track_name')
    angle        = hooks_meta.get('angle')
    hooks        = hooks_meta.get('hooks', {})

    track_block       = f"TRACK: {track_name}" if track_name else "TRACK: Unknown"
    spotify_cta       = f"Search {_song_title_only(track_name)} on Spotify" if track_name else "Search this track on Spotify"
    angle_instruction = ANGLE_INSTRUCTIONS.get(angle, ANGLE_DEFAULT_INSTRUCTION)
    strategy_block    = f"\nPERFORMANCE LEARNINGS:\n{strategy_notes}" if strategy_notes else ""

    # Show all 3 hook variants for context — captions work with the angle, not one specific hook
    hooks_block = "HOOK VARIANTS (A/B/C) — captions must work with the angle, not tied to one specific hook:\n"
    for length in clip_lengths:
        abc = hooks.get(length, {})
        hooks_block += f'  {length}s  A: "{abc.get("a", "")}"\n'
        hooks_block += f'         B: "{abc.get("b", "")}"\n'
        hooks_block += f'         C: "{abc.get("c", "")}"\n'

    json_template = ', '.join(
        f'"{l}": {{"tiktok": {{"caption": "", "hashtags": ""}}, '
        f'"instagram": {{"caption": "", "hashtags": ""}}, '
        f'"youtube": {{"title": "", "description": "", "hashtags": ""}}}}'
        for l in clip_lengths
    )

    prompt = f"""{ARTIST_CONTEXT}
{NIC_D_SPOTIFY_LAYER}

TASK: Generate platform-specific captions for a short-form video.

{track_block}
VIDEO TYPE: {content_desc}
CLIP LENGTHS: {', '.join(str(l) + 's' for l in clip_lengths)}
ANGLE: {angle or 'general'}

{angle_instruction}

{hooks_block}
{strategy_block}

For EACH clip length, write platform captions:

1. TIKTOK caption (max 150 chars) + 5-8 hashtags
   - Voice: raw, first person, direct — like a text, not a press release
   - Opens where the hook left off (Act 2) — never repeats the hook
   - End with: "{spotify_cta}"
   - Hashtag tiers: 1-2 mega (#techno #electronicmusic) + 3-4 mid-tier (100K-1M posts)
     + 3-4 niche under 100K (#holyrave #sunsetsessions #sacredtechno)

2. INSTAGRAM REELS caption (max 200 chars) + hashtags block (8-12 tags, paste in first comment)
   - Slightly more considered than TikTok, same Act 2 logic
   - End with: "{spotify_cta} — link in bio"

3. YOUTUBE SHORTS title (50-60 chars) + description (2-3 sentences) + 3-5 hashtags
   - Title patterns: "[Bible ref] at [BPM] BPM — Tenerife" | "Holy Rave Tenerife — [what's unique]"
     | "[Song title] — Sacred Melodic Techno"
   - Description: name the track, mention Sunset Sessions / free events / Tenerife
   - Hashtags: append at end of description field as space-separated tags (YouTube renders first 3 above title)
     Mix: 1-2 broad (#techno #shorts) + 2-3 niche (#holyrave #melodictechno #sacredtechno)

CAPTION RULES:
- Never include a URL. CTAs are text only.
- Never open caption with the track name or artist name — earn the mention.
- Tenerife, Atlantic coast, or Sunset Sessions should surface naturally where relevant.
- Content quality benchmark: must sit alongside Anyma, Rüfüs Du Sol, Argy.
- Biblical references woven in naturally — never forced. Subtle Salt principle.

Return ONLY valid JSON, no explanation, no markdown fences:

{{
  "content_type": "{content_type}",
  "clips": {{
    {json_template}
  }}
}}"""

    try:
        raw = _call_claude(
            "You are a social media caption writer for underground electronic music. Return only valid JSON.",
            prompt, timeout=120,
        )

        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            raw = raw.rsplit('```', 1)[0]

        result = json.loads(raw)

        # Embed A/B/C hooks into result (from Call 1 — do not re-generate)
        for length in clip_lengths:
            key = str(length)
            if key in result.get('clips', {}):
                abc = hooks.get(length, {})
                result['clips'][key]['hook_a'] = abc.get('a', '')
                result['clips'][key]['hook_b'] = abc.get('b', '')
                result['clips'][key]['hook_c'] = abc.get('c', '')

        result['track_name'] = track_name
        result['angle']      = angle
        logger.info(f"Captions generated: {filename}")
        if _BRAND_GATE_AVAILABLE:
            for _clip in result.get('clips', {}).values():
                # YouTube excluded: descriptions exceed 280-char proxy; gate not calibrated for long-form copy
                for platform in ('tiktok', 'instagram'):
                    cap = _clip.get(platform, {}).get('caption')
                    if cap:
                        _brand_gate.gate_or_warn(cap, context=f"generator.captions.{platform}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}\nRaw: {raw[:300] if 'raw' in dir() else 'no output'}")
        return _fallback_content(filename, clip_lengths, content_type, hooks_meta)
    except Exception as e:
        logger.error(f"Caption generation failed: {e}")
        return _fallback_content(filename, clip_lengths, content_type, hooks_meta)


def _fallback_content(filename: str, clip_lengths: list,
                      content_type: str, hooks_meta: dict = None) -> dict:
    """Minimal fallback if Call 2 fails."""
    hooks      = (hooks_meta or {}).get('hooks', {})
    track_name = (hooks_meta or {}).get('track_name', '')
    angle      = (hooks_meta or {}).get('angle')
    cta        = f"Search {track_name} on Spotify" if track_name else "Search this track on Spotify"

    clips = {}
    for length in clip_lengths:
        abc = hooks.get(length, {})
        clips[str(length)] = {
            "hook_a": abc.get('a', 'Made this the night everything fell apart.'),
            "hook_b": abc.get('b', 'For the version of you that stopped.'),
            "hook_c": abc.get('c', 'Spine knows before the mind does.'),
            "tiktok": {
                "caption": f"Free events. Atlantic coast. In the name of Jesus. {cta}",
                "hashtags": "#holyrave #sunsetsessions #sacredmusic #melodictechno #tenerife",
            },
            "instagram": {
                "caption": f"Every week. Free. Tenerife. {cta} — link in bio",
                "hashtags": "#holyrave #sunsetsessions #sacredmusic #melodictechno #tenerife #psytrance #electronicmusic #dancefloor #atlantic #robertjanmastenbroek",
            },
            "youtube": {
                "title": f"Holy Rave — {track_name or 'Sacred Music'} | Robert-Jan Mastenbroek",
                "description": "Weekly Sunset Sessions in Tenerife. Free entry, always. Music rooted in Scripture.",
            },
        }
    return {
        "content_type": content_type,
        "track_name":   track_name,
        "angle":        angle,
        "clips":        clips,
    }


# ── Single-call multi-clip captions ───────────────────────────────────────────

def generate_run_captions(track_title: str, clips_data: list) -> dict:
    """
    Single Claude call that generates captions for ALL clips in one run.
    Enforces 100% unique captions across clips.

    clips_data: [
        {'length': 5, 'angle': 'emotional', 'hook': str},
        {'length': 9, 'angle': 'signal',    'hook': str},
        {'length': 15, 'angle': 'energy',   'hook': str},
    ]

    Returns:
    {
        5:  {'tiktok': {'caption': str, 'hashtags': str}, 'instagram': {...}, 'youtube': {...}},
        9:  {...},
        15: {...},
    }
    """
    spotify_cta = f"Search {_song_title_only(track_title)} on Spotify"

    hooks_context = ""
    for c in clips_data:
        hooks_context += (
            f"\n{c['length']}s [{c['angle'].upper()}]\n"
            f'  Hook: "{c.get("hook", "")}"\n'
        )

    lengths_str = ", ".join(str(c['length']) + "s" for c in clips_data)
    json_template = ", ".join(
        f'"{c["length"]}": {{"tiktok": {{"caption": "", "hashtags": ""}}, '
        f'"instagram": {{"caption": "", "hashtags": ""}}, '
        f'"youtube": {{"title": "", "description": "", "hashtags": ""}}}}'
        for c in clips_data
    )

    prompt = f"""{ARTIST_CONTEXT}
{SUBTLE_SALT_LAYER}
{BRAND_VOICE_LAYER}
{NIC_D_SPOTIFY_LAYER}

TASK: Write platform captions for {len(clips_data)} short-form clips.

TRACK: {track_title}
CLIP LENGTHS: {lengths_str}

HOOK VARIANTS PER CLIP (captions must serve the ANGLE, not one specific hook):
{hooks_context}

ANGLE CONTEXT:
- 5s = EMOTIONAL: artist's interior, cost, why this exists
- 9s = SIGNAL: who this is for, exact moment/person
- 15s = ENERGY: what happens to a body in that room

CAPTION RULES:
1. EVERY clip must have a 100% UNIQUE caption — zero recycled phrases across the 3 clips.
   Each clip's caption opens from a completely different angle on the track and moment.
2. Captions continue Act 2 — they open where the hook left off. Never repeat the hook.
3. Never open with the track name or artist name.
4. End every TikTok and Instagram caption with: "{spotify_cta}"
5. Instagram caption ends: "{spotify_cta} — link in bio"
6. No URLs. Text CTAs only.
7. Tenerife / Atlantic coast / Sunset Sessions can appear — but only in ONE of the three clips.
8. Biblical references woven in naturally where they fit — never forced.
9. Content quality benchmark: must sit alongside Anyma, Rüfüs Du Sol, Argy.

HUMAN VOICE — this is the most important rule:
Write like a real person who just got home from a session, not a brand manager.
Short sentences. Fragments are fine. Don't over-explain. Don't wrap everything up neatly.
The reader should feel like this was written in 30 seconds by someone who actually lived it.
Bad: "This track represents a journey of spiritual discovery set against the backdrop of Tenerife."
Good: "wrote this at 4am. didn't plan to finish it."
Bad: "Experience the sacred energy of Holy Rave's latest release."
Good: "every week. free. show up."
Imperfect grammar is allowed if it sounds natural. Avoid all marketing language.

For EACH clip:
1. TIKTOK caption (max 150 chars) + 5-8 hashtags
   - Casual first-person, like a voice note turned text
   - Short bursts, not polished sentences
   - Hashtag tiers: 1-2 mega (#techno #electronicmusic) + 3-4 mid + 3-4 niche (#holyrave)

2. INSTAGRAM REELS caption (max 200 chars) + hashtags (8-12 tags, for first comment)
   - Slightly more considered than TikTok, but still raw — not curated

3. YOUTUBE SHORTS title (50-60 chars) + description (2-3 sentences) + 3-5 hashtags
   - Title patterns: "[Bible ref] at [BPM] BPM" | "Holy Rave Tenerife — [specific]"
                     | "[Song title] — Sacred Melodic Techno"
   - Hashtags: space-separated, appended in the hashtags field (YouTube shows first 3 above title)
     Mix: 1-2 broad (#techno #shorts) + 2-3 niche (#holyrave #melodictechno)

Return ONLY valid JSON, no explanation, no markdown:

{{
  "clips": {{
    {json_template}
  }}
}}"""

    try:
        raw = _call_claude(
            "You write social media captions for underground electronic music. "
            "You write like a real person — short, raw, imperfect. Never polished, never marketing. "
            "Return only valid JSON.",
            prompt, timeout=300,
        )

        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            raw = raw.rsplit('```', 1)[0]

        result = json.loads(raw)
        captions = {}
        for c in clips_data:
            key  = str(c['length'])
            data = result.get('clips', {}).get(key, {})
            captions[c['length']] = {
                'tiktok':    data.get('tiktok',    {'caption': '', 'hashtags': ''}),
                'instagram': data.get('instagram', {'caption': '', 'hashtags': ''}),
                'youtube':   data.get('youtube',   {'title': '', 'description': '', 'hashtags': ''}),
            }

        logger.info(f"Run captions generated for track: {track_title}")
        if _BRAND_GATE_AVAILABLE:
            for _length, _platforms in captions.items():
                # YouTube excluded: descriptions exceed 280-char proxy; gate not calibrated for long-form copy
                for platform in ('tiktok', 'instagram'):
                    cap = _platforms.get(platform, {}).get('caption')
                    if cap:
                        _brand_gate.gate_or_warn(cap, context=f"generator.run_captions.{platform}")
        return captions

    except json.JSONDecodeError as e:
        logger.error(f"Caption JSON parse error: {e}")
        return _fallback_run_captions(track_title, clips_data)
    except Exception as e:
        logger.error(f"generate_run_captions failed: {e}")
        return _fallback_run_captions(track_title, clips_data)


def _fallback_run_captions(track_title: str, clips_data: list) -> dict:
    cta = f"Search {_song_title_only(track_title)} on Spotify"
    result = {}
    for c in clips_data:
        angle = c['angle']
        if angle == 'emotional':
            cap = f"Made this the night I almost stopped. {cta}"
        elif angle == 'signal':
            cap = f"For the version of you that's still figuring it out. {cta}"
        else:
            cap = f"Free events. Tenerife. Every week. {cta}"
        result[c['length']] = {
            'tiktok':    {'caption': cap, 'hashtags': '#holyrave #sunsetsessions #melodictechno'},
            'instagram': {'caption': cap + ' — link in bio', 'hashtags': '#holyrave #sunsetsessions #sacredmusic #melodictechno #tenerife'},
            'youtube':   {'title': f"Holy Rave — {track_title} | Robert-Jan Mastenbroek", 'description': 'Weekly Sunset Sessions in Tenerife. Free entry, always.', 'hashtags': '#holyrave #melodictechno #shorts #techno #tenerife'},
        }
    return result


# ── Caption file formatter ─────────────────────────────────────────────────────

def format_caption_file(filename: str, generated: dict) -> str:
    """Format generated content into a clean .txt file for Google Drive."""
    base         = os.path.splitext(os.path.basename(filename))[0]
    content_type = generated.get('content_type', 'unknown')
    track_name   = generated.get('track_name') or '—'
    angle        = generated.get('angle') or '—'
    clips        = generated.get('clips', {})

    lines = [
        "═══════════════════════════════════════════════",
        "  HOLY RAVE CONTENT ENGINE",
        f"  Source: {base}",
        f"  Type:   {content_type.replace('_', ' ').title()}",
        f"  Track:  {track_name}",
        f"  Angle:  {angle.upper() if angle != '—' else angle}",
        "═══════════════════════════════════════════════",
        "",
    ]

    for length_str, data in sorted(clips.items(), key=lambda x: int(x[0])):
        length    = int(length_str)
        hook_a    = data.get('hook_a', data.get('hook', ''))
        hook_b    = data.get('hook_b', '')
        hook_c    = data.get('hook_c', '')
        tiktok    = data.get('tiktok', {})
        instagram = data.get('instagram', {})
        youtube   = data.get('youtube', {})

        lines += [
            f"┌─────────────────────────────────────────────",
            f"│  {length}-SECOND CLIP",
            f"│  File: {base}_{length}s.mp4",
            f"└─────────────────────────────────────────────",
            "",
            "HOOKS (A/B/C — post same clean clip on different days with each hook):",
            f'   A [scroll-stopper]:  "{hook_a}"',
            f'   B [identity/scene]:  "{hook_b}"',
            f'   C [claim/contrast]:  "{hook_c}"',
            "",
            "━━━ TIKTOK ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Caption:",
            f"   {tiktok.get('caption', '')}",
            "",
            "Hashtags:",
            f"   {tiktok.get('hashtags', '')}",
            "",
            "━━━ INSTAGRAM REELS ━━━━━━━━━━━━━━━━━━━━━━━━",
            "Caption:",
            f"   {instagram.get('caption', '')}",
            "",
            "Hashtags (paste in first comment):",
            f"   {instagram.get('hashtags', '')}",
            "",
            "━━━ YOUTUBE SHORTS ━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Title:",
            f"   {youtube.get('title', '')}",
            "",
            "Description:",
            f"   {youtube.get('description', '')}",
            "",
            "Hashtags:",
            f"   {youtube.get('hashtags', '')}",
            "",
            "",
        ]

    lines += [
        "═══════════════════════════════════════════════",
        "  All glory to Jesus.",
        "═══════════════════════════════════════════════",
    ]

    return '\n'.join(lines)


# ── Caption quality gate ───────────────────────────────────────────────────────

_BRAND_TEST_SYSTEM = """You are a brand quality reviewer for Robert-Jan Mastenbroek / Holy Rave.
Evaluate captions against these 5 mandatory tests:

1. VISUALIZATION — Can the reader physically see the words? ("The dust on the synth" passes. "The legacy of the music" fails.)
2. FALSIFIABILITY — Facts over adjectives. Numbers, places, and specifics pass. Vague praise fails.
3. UNIQUENESS — Could a competitor artist sign their name to this caption? If yes, it fails.
4. ONE MISSISSIPPI — Is the value prop clear in under 2 seconds? A confused reader fails.
5. A→B BRIDGE — Does it move a secular/searching person toward experiencing sacred energy? Pure club-marketing fails. Preachy religion fails.

Respond ONLY with valid JSON:
{"pass": true}          ← all 5 tests pass
{"pass": false, "failures": ["test_name: reason", ...], "fix": "rewritten caption that passes all 5"}
"""


def validate_and_fix_caption(caption: str, platform: str, angle: str) -> tuple[bool, str]:
    """
    Run the 5 brand tests on a single caption.
    Returns (passed, caption_to_use).
    On failure, returns the Claude-suggested fix.
    Falls back to original on any error (never blocks posting).
    """
    try:
        user_msg = f"Platform: {platform}\nAngle: {angle}\nCaption to review:\n{caption}"
        raw = _call_claude(_BRAND_TEST_SYSTEM, user_msg, timeout=30)
        # strip markdown fences if present
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        if data.get("pass"):
            return True, caption
        fix = data.get("fix", "").strip()
        if fix:
            logger.info("Brand test failed (%s %s) — using Claude fix", platform, angle)
            return False, fix
        return False, caption
    except Exception as exc:
        logger.debug("Caption validator error (non-fatal): %s", exc)
        return True, caption  # fail-open: never block posting on validator error


def validate_run_captions(run_captions: dict, clips_data: list) -> dict:
    """
    Run the 5 brand tests on TikTok + Instagram captions for each clip.
    YouTube descriptions are skipped (different format, less visible).
    Returns a (possibly improved) captions dict with the same structure.
    Bad captions are replaced in-place; passing captions are untouched.
    """
    angle_map = {c["length"]: c["angle"] for c in clips_data}
    fixed_any = False

    for clip_len, platforms in run_captions.items():
        angle = angle_map.get(clip_len, "unknown")
        for platform in ("tiktok", "instagram"):
            cap_data = platforms.get(platform, {})
            caption  = cap_data.get("caption", "")
            if not caption:
                continue
            passed, final = validate_and_fix_caption(caption, platform, angle)
            if not passed:
                run_captions[clip_len][platform]["caption"] = final
                fixed_any = True

    if fixed_any:
        logger.info("Caption quality gate: some captions were improved")
    else:
        logger.info("Caption quality gate: all captions passed")

    return run_captions
