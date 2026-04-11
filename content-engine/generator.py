"""
Content generator — two-call architecture.

Call 1 (generate_hooks):   Hook-only, temperature 1.0, 5 ranked candidates per clip length.
                            No JSON pressure. Pure creative output.
Call 2 (generate_content): Platform captions + structured JSON, temperature 0.4.
                            Uses hooks selected from Call 1.

Filename convention: trackname_angle_description.mp4
  renamed_emotional_3am-in-studio.mp4       → track=Renamed, angle=emotional, seed="3am in studio"
  renamed_signal_for-the-rebuilding.mp4     → track=Renamed, angle=signal,   seed="for the rebuilding"
  renamed_energy_hamburg-set.mp4            → track=Renamed, angle=energy,   seed="hamburg set"
  renamed_energy.mp4                        → track=Renamed, angle=energy,   seed=None
"""

import os
import re
import json
import logging
import anthropic

logger = logging.getLogger(__name__)

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

# ── Hook mechanism library (annotated — Claude internalises the principle) ─────
HOOK_MECHANISM_LIBRARY = """
HOOK MECHANISM LIBRARY — study why each works, not the words themselves:

[TENSION] "I made this at 3am and couldn't stop crying"
WHY: Specific time + physical reaction + unresolved cause. Brain needs to know what happened.
AVOID: "This song makes me feel things" — same intent, zero specificity, no gap to close.

[IDENTITY] "This one's for the believers nobody sees"
WHY: Names a real tribe without naming the religion. The right person feels found.
AVOID: "For all the Christians who love music" — labels instead of resonates.

[SCENE] "400 people, one sunset, no stage"
WHY: Three concrete specifics, one spatial subversion. Brain builds the image before deciding.
AVOID: "Live at Sunset Sessions" — event name means nothing to a stranger.

[CLAIM] "Nobody is making music like this right now"
WHY: Dares the viewer to fact-check it. Resistance is engagement.
AVOID: "Check out this unique track" — describes rather than challenges.

[RUPTURE] "I used to hate this kind of music"
WHY: The creator betrays the expected identity. Brain recalibrates and wants the explanation.
AVOID: "Music that transcends genre" — genre commentary, not human truth.
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
  — pre-managing the audience's discomfort means you've already decided they can't handle the real thing
- CONVERSION INVERSION: implying the music will do something spiritual to you
  — turns a transcendent experience into a product feature
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

# ── Per-angle hook instructions (mechanism-based, not outcome-based) ───────────
ANGLE_INSTRUCTIONS = {

    'emotional': """ANGLE: EMOTIONAL — The artist's interior. What this track cost. Why it exists.

Psychological target: Self-referential processing + Zeigarnik effect (unresolved tension).
The viewer's brain must involuntarily map this onto their own experience — then stay because
the tension is not resolved.

Hook rules:
- Start with a SPECIFIC MOMENT: a time of day, a number, a place, a physical detail.
  Not "when I was struggling" — name the actual situation.
- NEVER open with "I". Start with the time, place, or situation. "I" can appear later.
- END on unresolved tension — never the resolution. Leave the loop open.
- Avoid all category words: sacred, techno, rave, worship, feel, soul, journey, emotion, music.
- Must contain one concrete anchor: a time, a number, a location, a physical sensation.
- Specificity test: could this only have been written by someone who lived this exact thing?
- The spiritual dimension arrives as subtext — an unexplained weight in the specific moment.""",

    'signal': """ANGLE: SIGNAL — Where this track finds you. The specific person or moment it was made for.

Psychological target: Personal relevance trigger + anticipatory ownership.
The viewer must feel this track was sent specifically to them, for a situation they already know.
The save becomes an act of keeping something they don't want to lose access to.

Hook rules:
- Name the EXACT situation, state, or moment this track is FOR. Not for everyone — for one person.
- Think: "this track is for the person who..." then name something hyper-specific.
  NOT "for anyone going through hard times" — worthless.
  YES "for the version of you that stopped telling people how you actually are" — that's the target.
- Can address directly ("you") or describe a specific third-party situation.
- Stakes must be present: what does this person need that this track provides?
- Creates the felt need to save for later — "I will need this."
- NEVER open with "I". Start with "For", a person, or drop straight into the specific situation.""",

    'energy': """ANGLE: ENERGY — What happens to a room. The collective moment.

Psychological target: Embodied simulation + interoceptive cue triggering + motor cortex priming.
The viewer must simulate being inside a body on that floor — not watching from outside.

Hook rules:
- Describe what happens to a SPECIFIC BODY PART, not "the crowd" or "the room" generically.
  Sternum. Spine. Feet. Chest cavity. The body's anticipation before the beat lands.
- Techno at 130-145 BPM entrains motor neurons involuntarily — reference this physical pull.
- The sacred/rave collision can be asserted with confidence — assert it, don't explain it.
- Present tense, declarative. No questions.
- Reference ego dissolution if the footage supports it (the moment individual identity dissolves).
- Avoid all rave/genre clichés: dark, pounding, euphoric, underground, transcendent.
  These words are processed as noise by the exact audience you're reaching.
- NEVER open with "I". Start with a body part, a number, a room, or a moment.""",
}

ANGLE_DEFAULT_INSTRUCTION = """ANGLE: Undetected — default to Signal.
Where does this track find its listener? What specific moment is it for?
All other hook rules apply: specific, located, never open with "I", no category language."""


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
    Track name  = first segment (title-cased)
    Angle       = detected from keywords anywhere in the filename
    Seed hint   = description after angle segment (3rd segment onwards), spaces restored
    """
    name = os.path.splitext(filename)[0].lower()

    # Detect angle
    angle = None
    for angle_key, keywords in ANGLE_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            angle = angle_key
            break

    # Split on -- or _ to extract segments
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
    Produces 5 ranked candidates per clip length, selects rank 1.

    angle_override: bypasses filename detection and uses this angle directly.
                    Supplied by the auto-cycle in main.py when no angle is in the filename.

    Returns:
    {
        'track_name': str | None,
        'angle':      str | None,
        'seed_hint':  str | None,
        'hooks':      {15: 'hook text', 30: 'hook text', 60: 'hook text'},
    }
    """
    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    meta = parse_filename_metadata(filename)
    track_name = meta['track_name']
    angle      = angle_override or meta['angle']
    seed_hint  = meta['seed_hint']

    angle_instruction = ANGLE_INSTRUCTIONS.get(angle, ANGLE_DEFAULT_INSTRUCTION)

    track_block    = f"TRACK: {track_name}" if track_name else "TRACK: Unknown"
    seed_block     = f"SEED CONTEXT (use this as the specific moment to build from): {seed_hint}" if seed_hint else ""
    strategy_block = f"\nPERFORMANCE LEARNINGS (apply to improve results):\n{strategy_notes}" if strategy_notes else ""

    system_prompt = f"""You write hooks for short-form video. Your only job right now is hooks —
not captions, not hashtags. Just the 5-7 words burned into the first frame that make someone
stop scrolling on a rave-adjacent social feed.

{ARTIST_CONTEXT}
{HOOK_MECHANISM_LIBRARY}
{HOOK_FAILURE_MODES}

UNIVERSAL HOOK RULES:
- 5-7 words. Must be readable in under 2 seconds.
- NEVER open with "I". Start with a situation, time, place, number, or body.
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
After each hook, add: | mechanism: [tension/identity/scene/claim/rupture/other]

Format exactly as shown below — no preamble, no explanation, nothing else:

--- 15s ---
1. Hook text here | mechanism: tension
2. Hook text here | mechanism: identity
3. Hook text here | mechanism: scene
4. Hook text here | mechanism: claim
5. Hook text here | mechanism: rupture

--- 30s ---
1. Hook text here | mechanism: tension
2. ...

--- 60s ---
1. Hook text here | mechanism: tension
2. ...

Do not explain your choices. Do not apologise for bold hooks. Bold is correct."""

    try:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            temperature=1.0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        raw = message.content[0].text.strip()
        hooks = _parse_hook_candidates(raw, clip_lengths)
        logger.info(f"Hooks generated: {filename} | track={track_name} | angle={angle}")
        for l, h in hooks.items():
            logger.info(f"  {l}s → \"{h}\"")

    except Exception as e:
        logger.error(f"Hook generation failed for {filename}: {e}")
        hooks = {l: _fallback_hook(angle) for l in clip_lengths}

    return {
        'track_name': track_name,
        'angle':      angle,
        'seed_hint':  seed_hint,
        'hooks':      hooks,
    }


def _parse_hook_candidates(raw: str, clip_lengths: list) -> dict:
    """
    Parse ranked hook output from Call 1. Returns top-ranked hook per clip length.
    """
    hooks = {}
    sections = re.split(r'---\s*(\d+)s\s*---', raw)
    # sections: [preamble?, length, content, length, content, ...]
    for i in range(1, len(sections) - 1, 2):
        try:
            length  = int(sections[i].strip())
            content = sections[i + 1].strip()
            match   = re.search(r'^1\.\s*(.+?)(?:\s*\|\s*mechanism:.+)?$', content, re.MULTILINE)
            if match:
                hooks[length] = match.group(1).strip()
        except (ValueError, IndexError):
            continue

    # Fill any missing lengths with fallback
    for l in clip_lengths:
        if l not in hooks:
            hooks[l] = _fallback_hook(None)

    return hooks


def _fallback_hook(angle: str) -> str:
    """Minimal fallback hooks — specific and located, never generic."""
    fallbacks = {
        'emotional': 'Made this the night I almost stopped.',
        'signal':    'For the version of you still figuring it out.',
        'energy':    'The room stopped. Then everything moved.',
    }
    return fallbacks.get(angle, 'Something shifted here. Atlantic coast. 4am.')


# ── Call 2: Caption generation ─────────────────────────────────────────────────

def generate_content(filename: str, clip_lengths: list,
                     hooks_meta: dict, strategy_notes: str = None) -> dict:
    """
    Call 2 — Platform captions + structured JSON. Temperature 0.4.
    Uses hooks already selected by generate_hooks().

    hooks_meta: return value from generate_hooks()

    Returns:
    {
        'content_type': str,
        'track_name':   str | None,
        'angle':        str | None,
        'clips': {
            '15': {'hook': str, 'tiktok': {...}, 'instagram': {...}, 'youtube': {...}},
            '30': {...},
            '60': {...},
        }
    }
    """
    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))

    content_type = detect_content_type(filename)
    content_desc = CONTENT_TYPES.get(content_type, CONTENT_TYPES['event'])
    track_name   = hooks_meta.get('track_name')
    angle        = hooks_meta.get('angle')
    hooks        = hooks_meta.get('hooks', {})

    track_block  = f"TRACK: {track_name}" if track_name else "TRACK: Unknown"
    spotify_cta  = f"Search {track_name} on Spotify" if track_name else "Search this track on Spotify"

    angle_instruction = ANGLE_INSTRUCTIONS.get(angle, ANGLE_DEFAULT_INSTRUCTION)

    strategy_block = f"\nPERFORMANCE LEARNINGS:\n{strategy_notes}" if strategy_notes else ""

    hooks_block = "HOOKS ALREADY SELECTED — use these exactly, do not change them:\n"
    for length in clip_lengths:
        hooks_block += f'  {length}s: "{hooks.get(length, "")}"\n'

    json_template = ', '.join(
        f'"{l}": {{"hook": "", "tiktok": {{"caption": "", "hashtags": ""}}, '
        f'"instagram": {{"caption": "", "hashtags": ""}}, '
        f'"youtube": {{"title": "", "description": ""}}}}'
        for l in clip_lengths
    )

    prompt = f"""{ARTIST_CONTEXT}

TASK: Generate platform-specific captions for a short-form video.

{track_block}
VIDEO TYPE: {content_desc}
CLIP LENGTHS: {', '.join(str(l) + 's' for l in clip_lengths)}
ANGLE: {angle or 'general'}

{angle_instruction}

{hooks_block}
{strategy_block}

For EACH clip length, using the hook already selected above:

1. TIKTOK caption (max 150 chars) + 5-8 hashtags
   - Voice: raw, first person, direct — like a text, not a press release
   - Hook is Act 1. Caption is Act 2 — deliver the story the hook opened, then close.
   - End with: "{spotify_cta}" (text only, no URL)
   - Hashtags: 3-4 mid-tier specific tags (100K–1M posts), 2-3 niche/artist tags.
     No mega-tags (#techno, #electronicmusic). Specific beats broad.

2. INSTAGRAM REELS caption (max 200 chars) + hashtags block separate
   - Slightly more considered than TikTok, same Act 2 logic
   - End with: "{spotify_cta} — link in bio"
   - Hashtags block: 8-12 hashtags, paste in first comment

3. YOUTUBE SHORTS title (max 60 chars) + description (2-3 sentences)
   - Title: include track name + artist name, make it searchable
   - Description: name the track, briefly mention Sunset Sessions / free events / Tenerife

CAPTION RULES:
- Never include a URL anywhere. CTAs are text only.
- Never start a caption with the track name or artist name — earn the mention.
- Caption opens where the hook left off — never repeats it.
- Tenerife, Atlantic coast, or Sunset Sessions should surface naturally where relevant.

Return ONLY valid JSON, no explanation, no markdown fences:

{{
  "content_type": "{content_type}",
  "clips": {{
    {json_template}
  }}
}}"""

    try:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2500,
            temperature=0.4,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()

        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            raw = raw.rsplit('```', 1)[0]

        result = json.loads(raw)

        # Embed the selected hooks into the result
        for length in clip_lengths:
            key = str(length)
            if key in result.get('clips', {}):
                result['clips'][key]['hook'] = hooks.get(length, '')

        result['track_name'] = track_name
        result['angle']      = angle
        logger.info(f"Captions generated: {filename}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}\nRaw: {raw[:300]}")
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
        clips[str(length)] = {
            "hook": hooks.get(length, "Something sacred happened here."),
            "tiktok": {
                "caption": f"Free events. Atlantic coast. In the name of Jesus. {cta}",
                "hashtags": "#holyrave #sunsetsessions #sacredmusic #melodictechno #tenerife"
            },
            "instagram": {
                "caption": f"Every week. Free. Tenerife. {cta} — link in bio",
                "hashtags": "#holyrave #sunsetsessions #sacredmusic #melodictechno #tenerife #psytrance #electronicmusic #dancefloor #atlantic #robertjanmastenbroek"
            },
            "youtube": {
                "title": f"Holy Rave — {track_name or 'Sacred Music'} | Robert-Jan Mastenbroek",
                "description": "Weekly Sunset Sessions in Tenerife. Free entry, always. Music rooted in Scripture."
            }
        }
    return {
        "content_type": content_type,
        "track_name":   track_name,
        "angle":        angle,
        "clips":        clips,
    }


# ── Caption file formatter ─────────────────────────────────────────────────────

def format_caption_file(filename: str, generated: dict) -> str:
    """
    Format generated content into a clean .txt file for Google Drive.
    """
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
        length   = int(length_str)
        hook     = data.get('hook', '')
        tiktok   = data.get('tiktok', {})
        instagram = data.get('instagram', {})
        youtube  = data.get('youtube', {})

        lines += [
            f"┌─────────────────────────────────────────────",
            f"│  {length}-SECOND CLIP",
            f"│  File: {base}_{length}s.mp4",
            f"└─────────────────────────────────────────────",
            "",
            "HOOK (burned into video):",
            f'   "{hook}"',
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
            "",
        ]

    lines += [
        "═══════════════════════════════════════════════",
        "  All glory to Jesus.",
        "═══════════════════════════════════════════════",
    ]

    return '\n'.join(lines)
