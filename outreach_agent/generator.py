"""
Content generator — uses Claude to write hooks, captions, and hashtags
for each processed clip, targeted by angle.

ANGLE SYSTEM (per-clip hook targeting)
  emotional  → Artist interior. What this track cost. Why it exists.
  signal     → Who this track is for. The specific person/moment.
  energy     → What happens to a body in that room.

All captions drive to Spotify — link in bio is the artist page.
"""

import glob as _glob
import os
import json
import logging
import re
import subprocess

logger = logging.getLogger(__name__)

# ── Claude CLI ─────────────────────────────────────────────────────────────────

def _find_claude() -> str:
    for path in ["/usr/local/bin/claude", "/opt/homebrew/bin/claude"]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    pattern = os.path.expanduser(
        "~/Library/Application Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude"
    )
    matches = sorted(_glob.glob(pattern), reverse=True)
    if matches:
        return matches[0]
    raise RuntimeError("claude CLI not found — make sure Claude Code is installed.")

CLAUDE_BIN = _find_claude()


def _call_claude(system_prompt: str, user_prompt: str, timeout: int = 120) -> str:
    result = subprocess.run(
        [CLAUDE_BIN, "--print", "--no-session-persistence",
         "--system-prompt", system_prompt, user_prompt],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error: {result.stderr[:300]}")
    return result.stdout.strip()


# ── Artist context ─────────────────────────────────────────────────────────────

ARTIST_CONTEXT = """
You are writing short-form social media content for Robert-Jan Mastenbroek (RJM).

WHO HE IS:
Robert-Jan Mastenbroek is a Dutch producer who moved to the Atlantic edge of Tenerife. He plays
abandoned lots, cliff edges, rooftops, beach bars — anywhere a speaker fits and a crowd finds its
way. Every week he runs Sunset Sessions: free gatherings in unexpected outdoor locations, no ticket,
no stage. He makes Hebrew psytrance, melodic techno, tribal psytrance — all rooted in Scripture,
none of it sounds like it should be.

THE TENSION THAT MAKES THIS INTERESTING:
He is not trying to bring you to God. He is making music from inside his relationship with God,
in rooms where that relationship has no official standing. The rave floor and the sanctuary use
the same neural hardware — both suppress ego, both produce communal states people call transcendent.

BRAND VOICE:
Raw. Specific. Unhurried. Never argues for itself. Never explains the faith. Shows up somewhere
specific, makes something real, lets people find their own way in. The spiritual content arrives
late or by implication — never as the opening move. The secular listener should feel the hook
fully. The believer sees a second layer.

Spotify goal: 1 million monthly listeners.
"""

# ── Hook failure modes (what NOT to write) ────────────────────────────────────

HOOK_FAILURE_MODES = """
HOOK FAILURE MODES — never write these:
- "Follow for more sacred techno" — genre label + CTA, no reason to stop
- "You won't want to miss this" — empty urgency
- "This music will move your spirit" — vague promise, nothing to simulate
- "Bringing the gospel to the dancefloor" — mission statement, not a hook
- CROSS-CONTAMINATION: "this drop hits different when you know who made the universe"
- BUZZWORD STACK: "Sacred. Soul. Spirit. Transcendence."
- APOLOGY HOOK: "I know this might sound weird but it's kind of spiritual..."
"""

# ── Per-angle hook instructions ────────────────────────────────────────────────

ANGLE_HOOKS = {
    'emotional': """ANGLE: EMOTIONAL — Artist interior. What this track cost. Why it exists.
- Start with a SPECIFIC MOMENT: a time, place, physical detail. Not "when I was struggling".
- NEVER open with "I". Start with the situation.
- END on unresolved tension — never the resolution.
- Must contain one concrete anchor: time, number, location, physical sensation.
- The spiritual dimension arrives as subtext — an unexplained weight in the specific moment.""",

    'signal': """ANGLE: SIGNAL — Who this track is for. The specific person/moment it reaches.
- Name the EXACT situation or person this track is FOR. Not for everyone — for one person.
- "For the version of you that..." followed by something hyper-specific.
- Creates the felt need to save for later — "I will need this."
- NEVER open with "I". Start with "For", a person, or drop into the specific situation.""",

    'energy': """ANGLE: ENERGY — What happens to a body in that room. The collective moment.
- Describe what happens to a SPECIFIC BODY PART. Sternum. Spine. Feet. Not "the crowd".
- Present tense, declarative. No questions.
- NEVER open with "I". Start with a body part, a number, a room, or a moment.""",
}

# ── Content types ──────────────────────────────────────────────────────────────

CONTENT_TYPES = {
    'event':        'Crowd footage from Sunset Sessions — people, energy, atmosphere',
    'studio':       'Behind-the-scenes music production / studio content',
    'talking_head': 'Robert-Jan speaking directly to camera',
    'music_video':  'Finished music video or performance footage',
}


def detect_content_type(filename: str) -> str:
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


# ── Main generation call ───────────────────────────────────────────────────────

def generate_content(filename: str, clip_lengths: list,
                     angle: str = None, strategy_notes: str = None) -> dict:
    """
    Generate hooks (A/B/C) + platform captions for a single clip.

    angle: 'emotional' | 'signal' | 'energy' | None
           Controls which hook targeting system is used.

    Returns:
    {
        'content_type': str,
        'angle': str | None,
        'clips': {
            '5':  {'hook_a': str, 'hook_b': str, 'hook_c': str,
                   'tiktok': {...}, 'instagram': {...}, 'youtube': {...},
                   'best_posting_time': str},
            '9':  {...},
            '15': {...},
        }
    }
    """
    content_type = detect_content_type(filename)
    content_desc = CONTENT_TYPES.get(content_type, CONTENT_TYPES['event'])

    angle_instruction = ANGLE_HOOKS.get(angle, "")
    strategy_block    = f"\nPERFORMANCE LEARNINGS:\n{strategy_notes}" if strategy_notes else ""

    json_template = ', '.join(
        f'"{l}": {{"hook_a": "", "hook_b": "", "hook_c": "", '
        f'"tiktok": {{"caption": "", "hashtags": ""}}, '
        f'"instagram": {{"caption": "", "hashtags": ""}}, '
        f'"youtube": {{"title": "", "description": ""}}, '
        f'"best_posting_time": ""}}'
        for l in clip_lengths
    )

    system_prompt = f"""You are a social media content specialist for underground electronic music.
You write for Holy Rave / Robert-Jan Mastenbroek.
Your output must sit credibly alongside Anyma, Rüfüs Du Sol, and Argy in terms of content quality.
Return only valid JSON — no explanation, no markdown fences."""

    user_prompt = f"""{ARTIST_CONTEXT}
{HOOK_FAILURE_MODES}

VIDEO: {filename}
CONTENT TYPE: {content_desc}
CLIP LENGTHS: {', '.join(str(l) + 's' for l in clip_lengths)}
CTA DIRECTION: Every caption drives to Spotify — "Full track on Spotify — link in bio" or equivalent.
{angle_instruction}
{strategy_block}

HOOK RULES (all three variants must use DIFFERENT mechanisms):
Mechanisms: [tension] specific moment + unresolved cause | [identity] names a tribe without naming the religion |
[scene] 3 concrete specifics + 1 spatial subversion | [claim] dares the viewer to fact-check |
[rupture] creator betrays expected identity
- 5-8 words each. Never open with "I".
- No exclamation marks. Energy is internal.
- Every hook must be LOCATED: time, place, physical sensation, or concrete detail.
- hook_a = most scroll-stopping (tension/rupture preferred)
- hook_b = identity or scene mechanism
- hook_c = contrarian or claim mechanism

HASHTAG STRATEGY — always mix 3 tiers:
- Tier 1 (1-2 tags): 10M+ posts — #techno #electronicmusic
- Tier 2 (3-4 tags): 100k-1M — #melodictechno #psytrance #undergroundtechno
- Tier 3 (3-4 tags): under 100k — #holyrave #sunsetsessions #sacredtechno

YOUTUBE TITLES: Hook-first, 50-60 chars. Patterns:
  "[Bible ref] at [BPM] BPM — [location]" | "Holy Rave Tenerife — [what's unique]"
  "[Song title] — Sacred Melodic Techno"

POSTING TIME: based on platform peaks — TikTok Tue/Thu/Fri evenings, IG Mon/Wed/Fri 6-9pm CET.

For EACH clip length, generate hooks + captions + best posting time.

Return ONLY valid JSON:
{{
  "content_type": "{content_type}",
  "clips": {{
    {json_template}
  }}
}}"""

    try:
        raw = _call_claude(system_prompt, user_prompt, timeout=120)

        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            raw = raw.rsplit('```', 1)[0]

        result = json.loads(raw)
        result['angle'] = angle
        logger.info(f"Content generated: {filename} | angle={angle}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}\nRaw: {raw[:300] if 'raw' in dir() else 'no output'}")
        return _fallback_content(filename, clip_lengths, content_type, angle)
    except Exception as e:
        logger.error(f"Content generation failed: {e}")
        return _fallback_content(filename, clip_lengths, content_type, angle)


def _fallback_content(filename: str, clip_lengths: list,
                      content_type: str, angle: str = None) -> dict:
    clips = {}
    for length in clip_lengths:
        clips[str(length)] = {
            "hook_a": "Made this the night everything fell apart.",
            "hook_b": "For the version of you that stopped.",
            "hook_c": "Spine knows before the mind does.",
            "best_posting_time": "Friday 7pm CET",
            "tiktok":    {"caption": "Free events. Atlantic coast. Tenerife. Full track on Spotify — link in bio.", "hashtags": "#holyrave #sunsetsessions #melodictechno #tenerife"},
            "instagram": {"caption": "Free. Every week. Atlantic coast. Full track on Spotify — link in bio.", "hashtags": "#holyrave #sunsetsessions #melodictechno #tenerife #sacredtechno #robertjanmastenbroek"},
            "youtube":   {"title": "Holy Rave — Sacred Melodic Techno | Tenerife", "description": "Weekly Sunset Sessions in Tenerife. Free entry. Music rooted in Scripture. Full track on Spotify."},
        }
    return {
        "content_type": content_type,
        "angle":        angle,
        "clips":        clips,
    }


# ── Caption file formatter ─────────────────────────────────────────────────────

def format_caption_file(filename: str, generated: dict) -> str:
    """Format generated content into a clean readable .txt file."""
    base         = os.path.splitext(os.path.basename(filename))[0]
    content_type = generated.get('content_type', 'unknown')
    angle        = generated.get('angle')
    clips        = generated.get('clips', {})

    angle_line = f"  Angle:  {angle.upper()}" if angle else ""

    lines = [
        "═══════════════════════════════════════════════",
        "  HOLY RAVE CONTENT ENGINE",
        f"  Source: {base}",
        f"  Type:   {content_type.replace('_', ' ').title()}",
    ]
    if angle_line:
        lines.append(angle_line)
    lines += [
        "═══════════════════════════════════════════════",
        "",
        "  POSTING CHECKLIST:",
        "  ✅ POST ON: TIKTOK",
        "  ✅ POST ON: INSTAGRAM REELS",
        "  ✅ POST ON: YOUTUBE SHORTS",
        "  ✅ POST ON: INSTAGRAM STORIES (add Spotify sticker → link in bio)",
        "",
        "═══════════════════════════════════════════════",
        "",
    ]

    for length_str, data in sorted(clips.items(), key=lambda x: int(x[0])):
        length   = int(length_str)
        tiktok   = data.get('tiktok', {})
        instagram = data.get('instagram', {})
        youtube  = data.get('youtube', {})
        hook_a   = data.get('hook_a', data.get('hook', ''))
        hook_b   = data.get('hook_b', '')
        hook_c   = data.get('hook_c', '')
        posting_time = data.get('best_posting_time', '')

        lines += [
            f"┌─────────────────────────────────────────────",
            f"│  {length}-SECOND CLIP — File: {base}_{length}s.mp4",
            f"└─────────────────────────────────────────────",
            "",
            "HOOK VARIANTS (A/B/C test — post same clip with different hooks on different days):",
            f'   A: "{hook_a}"',
            f'   B: "{hook_b}"',
            f'   C: "{hook_c}"',
            "",
            f"BEST POSTING TIME: {posting_time}",
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
