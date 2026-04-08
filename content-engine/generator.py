"""
Content generator — uses Claude to write hooks, captions, and hashtags
for each processed clip, tailored per platform and growth bucket.

DAILY CONTENT STRATEGY
======================
3 buckets, 3 posts per day. Each raw clip you record gets assigned one bucket.
All posts cross-post across platforms from the same clip.

  Bucket 1 — REACH
    Goal:     Maximum views and algorithm reach (push to non-followers)
    Captions: No external CTA, designed to be shared/saved/watched twice
    Platform: TikTok + IG Reel + YT Short
    Format:   15-30s high energy, visual hook, no talking required

  Bucket 2 — FOLLOW
    Goal:     Convert viewers into followers (make them care about YOU)
    Captions: Soft CTA — "follow for more", tease next event/release
    Platform: TikTok + IG Reel
    Format:   30-60s personality, story, behind-the-scenes, authentic moment

  Bucket 3 — SPOTIFY
    Goal:     Drive streams and monthly listener count
    Captions: Direct CTA — "link in bio", "out now on Spotify"
    Platform: TikTok + IG Reel + IG Stories (repurpose)
    Format:   15-30s music-forward, song name visible, emotional pull

FILENAME CONVENTION
  Prefix filename to assign bucket manually:
    reach_<name>.mp4   → Bucket 1
    follow_<name>.mp4  → Bucket 2
    spotify_<name>.mp4 → Bucket 3
  If no prefix, bucket is auto-detected from content type.
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

# NOTE: generate_content() is available for one-off use but is NOT called by the
# daily automated pipeline. Captions come from caption_bank.py (local, zero cost).
# Only import anthropic if explicitly needed.
def _anthropic_client():
    try:
        import anthropic
        return anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    except ImportError:
        raise RuntimeError("anthropic package not installed — run: pip install anthropic")

# Artist context injected into every prompt so Claude understands the mission
ARTIST_CONTEXT = """
You are writing short-form social media content for Robert-Jan Mastenbroek (RJM).

WHO HE IS:
- Jesus-loving electronic music producer and raver
- Every song is rooted in Scripture (Hebrew psytrance, melodic techno, tribal psytrance)
- Runs "Sunset Sessions" — free weekly gatherings in the name of Jesus, in unexpected locations
- The mission: bringing the gospel through music to places mainstream church would never go
- Audience: people in rave/electronic music culture, spiritual seekers, Christians who love music
- Brand voice: raw, authentic, no performance of faith. Sacred but not religious. Bold but not preachy.
- Spotify goal: 1 million monthly listeners

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

# Default bucket per content type (overridden by filename prefix)
CONTENT_TYPE_DEFAULT_BUCKET = {
    'event':        'reach',
    'studio':       'follow',
    'talking_head': 'follow',
    'music_video':  'spotify',
}

BUCKET_STRATEGY = {
    'reach': {
        'label': 'REACH — Max Views',
        'goal': 'Push to non-followers. Optimise for shares, saves, and rewatches.',
        'cta': 'No hard CTA. Content should be satisfying on its own — shareable.',
        'platforms': ['tiktok', 'instagram', 'youtube'],
        'story_repurpose': False,
    },
    'follow': {
        'label': 'FOLLOW — Grow Audience',
        'goal': 'Turn viewers into followers. Show personality, story, or process.',
        'cta': 'Soft CTA: "follow for more", tease next event or drop.',
        'platforms': ['tiktok', 'instagram'],
        'story_repurpose': False,
    },
    'spotify': {
        'label': 'SPOTIFY — Drive Streams',
        'goal': 'Push people to Spotify. Song name visible. Emotional pull.',
        'cta': 'Direct CTA: "link in bio" or "out now on Spotify". Song name in caption.',
        'platforms': ['tiktok', 'instagram', 'youtube'],
        'story_repurpose': True,
    },
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


def detect_bucket(filename: str, content_type: str) -> str:
    """
    Determine growth bucket from filename prefix or content type.

    Manual override via prefix:
      reach_*   → Bucket 1: reach
      follow_*  → Bucket 2: follow
      spotify_* → Bucket 3: spotify

    Falls back to content-type default.
    """
    name = filename.lower()
    if name.startswith('reach_'):
        return 'reach'
    if name.startswith('follow_'):
        return 'follow'
    if name.startswith('spotify_'):
        return 'spotify'
    return CONTENT_TYPE_DEFAULT_BUCKET.get(content_type, 'reach')


def generate_content(filename: str, clip_lengths: list, strategy_notes: str = None) -> dict:
    """
    Generate hooks and captions for a video.

    Returns dict:
    {
        'content_type': str,
        'bucket': str,          # 'reach', 'follow', or 'spotify'
        'bucket_label': str,
        'story_repurpose': bool,
        'clips': {
            15: {'hook': str, 'tiktok': {...}, 'instagram': {...}, 'youtube': {...}},
            30: {...},
            60: {...},
        }
    }
    """
    client = _anthropic_client()
    content_type = detect_content_type(filename)
    content_desc = CONTENT_TYPES.get(content_type, CONTENT_TYPES['event'])
    bucket = detect_bucket(filename, content_type)
    bucket_info = BUCKET_STRATEGY[bucket]

    strategy_block = ''
    if strategy_notes:
        strategy_block = f"\n\nPERFORMANCE LEARNINGS (apply these to improve results):\n{strategy_notes}"

    # Which platforms to generate for this bucket
    youtube_instruction = (
        "4. YOUTUBE SHORTS title + description\n"
        "   TITLE RULES (critical — this is what gets clicked):\n"
        "   - Max 70 chars, ideally 50-60 so it doesn't truncate on mobile\n"
        "   - Hook-first: lead with the most magnetic part of the concept\n"
        "   - Include AT LEAST ONE of: bible reference, BPM, \"Holy Rave\", location (Tenerife)\n"
        "   - Proven title patterns for music Shorts:\n"
        "       * \"[Bible ref] at [BPM] BPM — [location]\"\n"
        "       * \"If [biblical figure] was a techno DJ\"\n"
        "       * \"[Song title] — Sacred Melodic Techno\"\n"
        "       * \"Holy Rave Tenerife — [what makes it unique]\"\n"
        "   - NO: clickbait, ALL CAPS entire title, question marks unless powerful\n"
        "   DESCRIPTION RULES:\n"
        "   - Line 1: One punchy sentence expanding on the title hook\n"
        "   - Line 2: \"Robert-Jan Mastenbroek — Ancient Truth. Future Sound.\"\n"
        "   - Line 3: \"Free weekly Sunset Sessions in Tenerife. Link in bio.\"\n"
        "   - Max 200 chars total\n"
        "   TOPIC: Music (YouTube category 10 — will be set automatically)"
    )

    json_clip_template = ', '.join(
        f'"{l}": {{"hook_a": "", "hook_b": "", "hook_c": "", '
        f'"tiktok": {{"caption": "", "hashtags": ""}}, '
        f'"instagram": {{"caption": "", "hashtags": ""}}, '
        f'"youtube": {{"title": "", "description": ""}}, '
        f'"best_posting_time": ""}}'
        for l in clip_lengths
    )

    prompt = f"""{ARTIST_CONTEXT}

BENCHMARK ARTISTS (match this production quality of content):
- Anyma: Cinematic scale, dark atmosphere, every post feels like a movie trailer
- Rüfüs Du Sol: Emotional depth, raw authenticity, builds genuine connection
- Argy: Tribal energy, underground credibility, mysterious and magnetic
Study how these artists write hooks and captions. Your content must sit credibly alongside them.

HOOK WRITING RULES (study these patterns from top-performing short-form content):
- Pattern interrupt: Start with something unexpected ("Nobody talks about this", "They told me not to post this")
- Curiosity gap: Leave an open loop ("What happened next changed everything")
- Contrast: Pair two opposing ideas ("Sacred music. In a rave.")
- Specificity: Concrete details beat vague claims ("126 BPM" beats "really fast")
- Visual language: Make them see it ("The dust on the synth still smells like that night")
- Avoid: Generic opener hooks ("Check this out", "Here's something cool")

HASHTAG STRATEGY — always mix 3 tiers:
- Tier 1 (1-2 tags): Massive (10M+ posts) — #techno #electronicmusic #rave
- Tier 2 (3-4 tags): Mid-size (100k-1M) — #melodictechno #psytrance #undergroundtechno
- Tier 3 (3-4 tags): Niche (under 100k) — #holyrave #sunsetsessions #sacredtechno #christianrave
This mix maximizes both discovery and relevance ranking.

TASK: Generate short-form social media content for this video.

VIDEO FILE: {filename}
CONTENT TYPE: {content_desc}
CLIP LENGTHS TO COVER: {', '.join(str(l) + 's' for l in clip_lengths)}

GROWTH BUCKET: {bucket_info['label']}
BUCKET GOAL: {bucket_info['goal']}
CALL TO ACTION DIRECTION: {bucket_info['cta']}
{strategy_block}

For EACH clip length, generate:

1. THREE HOOK VARIANTS (hook_a, hook_b, hook_c) — max 8 words each
   Each uses a DIFFERENT hook pattern (pattern interrupt, curiosity gap, contrast, etc.)
   These are burned into the video — A/B/C test them by posting the same clip with different hooks on different days.
   For event footage: capture energy, contrast, or transformation
   For talking head: the most provocative first line
   For studio: the mystery or process that's visually interesting

2. TIKTOK caption (max 150 chars) + tiered hashtags (8-10 total, mix all 3 tiers)
   - Voice: raw, personal, first-person, conversational
   - No full sentences — punchy fragments that stop the scroll
   - For 7s clips: caption should match the urgency — very short, 1-2 lines max
   - CTA: {bucket_info['cta']}

3. INSTAGRAM REELS caption (max 220 chars, NO hashtags in caption body)
   - Slightly more crafted than TikTok but still punchy
   - For 7s clips: lean into the loop — something that makes them watch again
   - Apply the Subtle Salt protocol: poetic, open-ended, faith visible but not forced
   - CTA: {bucket_info['cta']}
   - Hashtags block (12-15 tags, tiered mix, paste in first comment)

{youtube_instruction}

5. BEST POSTING TIME — single string like "Tuesday 7pm CET" or "Friday 6pm CET"
   Based on platform algorithm patterns: TikTok peaks Tue/Thu/Fri evenings; IG peaks Mon/Wed/Fri 6-9pm.
   For reach bucket: pick highest-traffic times. For follow bucket: slightly off-peak for less competition.
   For spotify bucket: Friday release day timing (new music Fridays).

Return ONLY valid JSON in this exact structure, no explanation:

{{
  "content_type": "{content_type}",
  "bucket": "{bucket}",
  "clips": {{
    {json_clip_template}
  }}
}}"""

    try:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()

        # Strip markdown code fences if present
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            raw = raw.rsplit('```', 1)[0]

        result = json.loads(raw)
        # Enrich with bucket metadata not in the JSON response
        result['bucket'] = result.get('bucket', bucket)
        result['bucket_label'] = bucket_info['label']
        result['story_repurpose'] = bucket_info['story_repurpose']
        logger.info(f"Generated content for {filename} ({content_type}, bucket={bucket})")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error from Claude: {e}\nRaw: {raw[:300]}")
        return _fallback_content(filename, clip_lengths, content_type, bucket)
    except Exception as e:
        logger.error(f"Content generation failed: {e}")
        return _fallback_content(filename, clip_lengths, content_type, bucket)


def _fallback_content(filename: str, clip_lengths: list, content_type: str, bucket: str = 'reach') -> dict:
    """Minimal fallback if Claude call fails."""
    clips = {}
    for length in clip_lengths:
        clips[str(length)] = {
            "hook_a": "Sacred music for every dancefloor",
            "hook_b": "Nobody expected this at a rave",
            "hook_c": "126 BPM. In the name of Jesus.",
            "best_posting_time": "Friday 7pm CET",
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
    bucket_info = BUCKET_STRATEGY.get(bucket, BUCKET_STRATEGY['reach'])
    return {
        "content_type": content_type,
        "bucket": bucket,
        "bucket_label": bucket_info['label'],
        "story_repurpose": bucket_info['story_repurpose'],
        "clips": clips,
    }


def format_caption_file(filename: str, generated: dict) -> str:
    """
    Format generated content into a clean, readable .txt file
    that sits next to the clips in Google Drive.
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    content_type = generated.get('content_type', 'unknown')
    bucket = generated.get('bucket', 'reach')
    bucket_label = generated.get('bucket_label', bucket.upper())
    story_repurpose = generated.get('story_repurpose', False)
    clips = generated.get('clips', {})
    bucket_info = BUCKET_STRATEGY.get(bucket, BUCKET_STRATEGY['reach'])
    platforms = bucket_info['platforms']

    # Posting guide — what to post where
    posting_lines = []
    for platform in ['tiktok', 'instagram', 'youtube']:
        if platform in platforms:
            posting_lines.append(f"  ✅ POST ON: {platform.upper()}")
        else:
            posting_lines.append(f"  ⬜ SKIP:    {platform.upper()} (not used for this bucket)")
    if story_repurpose:
        posting_lines.append(f"  ✅ POST ON: INSTAGRAM STORIES (repurpose with Spotify sticker)")

    lines = [
        f"═══════════════════════════════════════════════",
        f"  HOLY RAVE CONTENT ENGINE",
        f"  Source: {base}",
        f"  Type:   {content_type.replace('_', ' ').title()}",
        f"═══════════════════════════════════════════════",
        f"",
        f"  BUCKET: {bucket_label}",
        f"  GOAL:   {bucket_info['goal']}",
        f"  CTA:    {bucket_info['cta']}",
        f"",
        f"  POSTING CHECKLIST:",
    ] + posting_lines + [
        f"",
        f"═══════════════════════════════════════════════",
        f"",
    ]

    for length_str, data in sorted(clips.items(), key=lambda x: int(x[0])):
        length = int(length_str)
        hook = data.get('hook', '')
        tiktok = data.get('tiktok', {})
        instagram = data.get('instagram', {})
        youtube = data.get('youtube', {})

        hook_a = data.get('hook_a') or hook  # fallback to legacy 'hook' key
        hook_b = data.get('hook_b', '')
        hook_c = data.get('hook_c', '')
        posting_time = data.get('best_posting_time', '')

        clip_lines = [
            f"┌─────────────────────────────────────────────",
            f"│  {length}-SECOND CLIP — File: {base}_{length}s.mp4",
            f"└─────────────────────────────────────────────",
            f"",
            f"HOOK VARIANTS (A/B/C test — post same clip with different hooks on different days):",
            f'   A: "{hook_a}"',
            f'   B: "{hook_b}"',
            f'   C: "{hook_c}"',
            f"",
            f"BEST POSTING TIME: {posting_time}",
            f"",
        ]

        if 'tiktok' in platforms:
            clip_lines += [
                f"━━━ TIKTOK ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"Caption:",
                f"   {tiktok.get('caption', '')}",
                f"",
                f"Hashtags:",
                f"   {tiktok.get('hashtags', '')}",
                f"",
            ]

        if 'instagram' in platforms:
            clip_lines += [
                f"━━━ INSTAGRAM REELS ━━━━━━━━━━━━━━━━━━━━━━━━",
                f"Caption:",
                f"   {instagram.get('caption', '')}",
                f"",
                f"Hashtags (paste in first comment):",
                f"   {instagram.get('hashtags', '')}",
                f"",
            ]
            if story_repurpose:
                clip_lines += [
                    f"━━━ INSTAGRAM STORIES ━━━━━━━━━━━━━━━━━━━━━━",
                    f"   Repost this clip to Stories.",
                    f"   Add Spotify sticker → link to the track.",
                    f"   Add a poll or question sticker to boost engagement.",
                    f"",
                ]

        if 'youtube' in platforms and youtube.get('title'):
            clip_lines += [
                f"━━━ YOUTUBE SHORTS ━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"Title:",
                f"   {youtube.get('title', '')}",
                f"",
                f"Description:",
                f"   {youtube.get('description', '')}",
                f"",
            ]

        clip_lines.append("")
        lines += clip_lines

    lines += [
        "═══════════════════════════════════════════════",
        "  All glory to Jesus.",
        "═══════════════════════════════════════════════",
    ]

    return '\n'.join(lines)
