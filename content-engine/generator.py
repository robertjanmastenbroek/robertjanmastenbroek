"""
Content generator — uses Claude to write hooks, captions, and hashtags
for each processed clip, tailored per platform.
"""

import os
import json
import logging
import anthropic

logger = logging.getLogger(__name__)

# Artist context injected into every prompt so Claude understands the mission
ARTIST_CONTEXT = """
You are writing short-form social media content for Robert-Jan Mastenbroek.

WHO HE IS:
- Jesus-loving electronic music producer and raver
- Every song is rooted in Scripture (Hebrew psytrance, melodic techno, tribal psytrance)
- Runs "Sunset Sessions" — free weekly gatherings in the name of Jesus, in unexpected locations
- The mission: bringing the gospel through music to places mainstream church would never go
- Audience: people in rave/electronic music culture, spiritual seekers, Christians who love music
- Brand voice: raw, authentic, no performance of faith. Sacred but not religious. Bold but not preachy.

TONE GUIDELINES:
- Never preachy or cringe-Christian
- Lead with the music and the feeling, let the faith be visible but not forced
- "Hallelujah" and "Jesus" can appear naturally but not as buzzwords
- Think: someone who genuinely loves both Jesus and the dancefloor and doesn't apologize for either
- Short sentences. Punchy. High energy.
"""

CONTENT_TYPES = {
    'event': 'Crowd footage from Sunset Sessions — people dancing, energy, atmosphere',
    'studio': 'Behind-the-scenes music production / studio content',
    'talking_head': 'Robert-Jan speaking directly to camera',
    'music_video': 'Finished music video or performance footage',
}


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
    return 'event'  # Default assumption for Sunset Sessions content


def generate_content(filename: str, clip_lengths: list, strategy_notes: str = None) -> dict:
    """
    Generate hooks and captions for a video.

    Returns dict:
    {
        'content_type': str,
        'clips': {
            15: {'hook': str, 'tiktok': {...}, 'instagram': {...}, 'youtube': {...}},
            30: {...},
            60: {...},
        }
    }
    """
    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    content_type = detect_content_type(filename)
    content_desc = CONTENT_TYPES.get(content_type, CONTENT_TYPES['event'])

    strategy_block = ''
    if strategy_notes:
        strategy_block = f"\n\nPERFORMANCE LEARNINGS (apply these to improve results):\n{strategy_notes}"

    prompt = f"""{ARTIST_CONTEXT}

TASK: Generate short-form social media content for this video.

VIDEO FILE: {filename}
CONTENT TYPE: {content_desc}
CLIP LENGTHS TO COVER: {', '.join(str(l) + 's' for l in clip_lengths)}
{strategy_block}

For EACH clip length, generate:

1. HOOK TEXT (max 8 words) — burned into the video itself. Must stop the scroll instantly.
   For event footage: capture the energy or the contrast (sacred + rave)
   For talking head: the first thing they'll say on screen
   For studio: the intrigue

2. TIKTOK caption (max 150 chars) + 5-8 hashtags
   - TikTok voice: more raw, more personal, first person
   - End with something that invites a response or save

3. INSTAGRAM REELS caption (max 200 chars, no hashtags in caption)
   - Slightly more polished than TikTok
   - Include a call to action (link in bio, join community, etc.)
   - Hashtags block (10-15 hashtags, separate from caption)

4. YOUTUBE SHORTS title (max 60 chars) + description (2-3 sentences)
   - Title should be searchable (include "Holy Rave", "Sacred Music", "Christian Rave" etc.)
   - Description should mention free events and the mission briefly

Return ONLY valid JSON in this exact structure, no explanation:

{{
  "content_type": "{content_type}",
  "clips": {{
    {', '.join(f'"{l}": {{"hook": "", "tiktok": {{"caption": "", "hashtags": ""}}, "instagram": {{"caption": "", "hashtags": ""}}, "youtube": {{"title": "", "description": ""}}}}' for l in clip_lengths)}
  }}
}}"""

    try:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()

        # Strip markdown code fences if present
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            raw = raw.rsplit('```', 1)[0]

        result = json.loads(raw)
        logger.info(f"Generated content for {filename} ({content_type})")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error from Claude: {e}\nRaw: {raw[:300]}")
        return _fallback_content(filename, clip_lengths, content_type)
    except Exception as e:
        logger.error(f"Content generation failed: {e}")
        return _fallback_content(filename, clip_lengths, content_type)


def _fallback_content(filename: str, clip_lengths: list, content_type: str) -> dict:
    """Minimal fallback if Claude call fails."""
    clips = {}
    for length in clip_lengths:
        clips[str(length)] = {
            "hook": "Sacred music for every dancefloor",
            "tiktok": {
                "caption": "Free events. Sacred music. In the name of Jesus. 🙌",
                "hashtags": "#holyrave #sunsetsessions #sacredmusic #christianrave #jesus"
            },
            "instagram": {
                "caption": "Every week. Free. In the name of Jesus. Link in bio to join the community.",
                "hashtags": "#holyrave #sunsetsessions #sacredmusic #christianrave #jesus #dancefloor #melodictechno #psytrance #gospel #tenerife"
            },
            "youtube": {
                "title": "Holy Rave — Sacred Music Every Week",
                "description": "Weekly Sunset Sessions in Tenerife. Free entry, always. Dancing in the name of Jesus."
            }
        }
    return {"content_type": content_type, "clips": clips}


def format_caption_file(filename: str, generated: dict) -> str:
    """
    Format generated content into a clean, readable .txt file
    that sits next to the clips in Google Drive.
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    content_type = generated.get('content_type', 'unknown')
    clips = generated.get('clips', {})

    lines = [
        f"═══════════════════════════════════════════════",
        f"  HOLY RAVE CONTENT ENGINE",
        f"  Source: {base}",
        f"  Type: {content_type.replace('_', ' ').title()}",
        f"═══════════════════════════════════════════════",
        "",
    ]

    for length_str, data in sorted(clips.items(), key=lambda x: int(x[0])):
        length = int(length_str)
        hook = data.get('hook', '')
        tiktok = data.get('tiktok', {})
        instagram = data.get('instagram', {})
        youtube = data.get('youtube', {})

        lines += [
            f"┌─────────────────────────────────────────────",
            f"│  📱 {length}-SECOND CLIP",
            f"│  File: {base}_{length}s.mp4",
            f"└─────────────────────────────────────────────",
            "",
            f"🔥 HOOK (burned into video):",
            f'   "{hook}"',
            "",
            f"━━━ TIKTOK ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Caption:",
            f"   {tiktok.get('caption', '')}",
            f"",
            f"Hashtags:",
            f"   {tiktok.get('hashtags', '')}",
            "",
            f"━━━ INSTAGRAM REELS ━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Caption:",
            f"   {instagram.get('caption', '')}",
            f"",
            f"Hashtags (paste in first comment):",
            f"   {instagram.get('hashtags', '')}",
            "",
            f"━━━ YOUTUBE SHORTS ━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Title:",
            f"   {youtube.get('title', '')}",
            f"",
            f"Description:",
            f"   {youtube.get('description', '')}",
            "",
            "",
        ]

    lines += [
        "═══════════════════════════════════════════════",
        "  All glory to Jesus. 🙏",
        "═══════════════════════════════════════════════",
    ]

    return '\n'.join(lines)
