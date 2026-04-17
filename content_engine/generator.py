"""
generator.py — Claude-driven hook filling + caption generation with sub-mode diversity.

Migrated from outreach_agent/generator.py. Key changes:
- Works with ClipFormat enum (transitional/emotional/performance)
- Wires sub-mode diversity (COST, NAMING, DOUBT, etc.) that was coded but never executed
- Uses content_engine.hook_library for template selection
- Uses content_engine.brand_gate for validation
"""
import glob as _glob
import json
import logging
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from content_engine.types import ClipFormat
from content_engine.hook_library import (
    HookTemplate,
    pick_templates_for_format,
    get_all_templates,
    log_template_use,
)
from content_engine.brand_gate import gate_or_reject, gate_or_warn, gate_caption

PROJECT_DIR = Path(__file__).parent.parent
logger = logging.getLogger(__name__)

# ─── Sub-mode diversity (was documented but never executed) ──────────────────

ANGLE_SUB_MODES = {
    "emotional": ["COST", "NAMING", "DOUBT", "DEVOTION", "RUPTURE"],
    "signal": ["FINDER", "PERMISSION", "RECOGNITION", "SEASON", "UNSAID"],
    "energy": ["BODY", "TIME", "GEOGRAPHY", "THRESHOLD", "DISSOLUTION"],
}

# Map ClipFormat to angle
FORMAT_TO_ANGLE = {
    ClipFormat.TRANSITIONAL: "emotional",
    ClipFormat.EMOTIONAL: "emotional",
    ClipFormat.PERFORMANCE: "energy",
}


def pick_sub_mode(angle: str, sub_mode_weights: dict | None = None) -> str:
    """Pick a sub-mode for the given angle, weighted by learned performance."""
    modes = ANGLE_SUB_MODES.get(angle, ANGLE_SUB_MODES["emotional"])
    w = sub_mode_weights or {}
    scores = [(m, max(w.get(m, 1.0), 0.0)) for m in modes]
    total = sum(s for _, s in scores)
    if total == 0:
        return random.choice(modes)
    r = random.random() * total
    cumulative = 0.0
    for m, s in scores:
        cumulative += s
        if r <= cumulative:
            return m
    return scores[-1][0]


# ─── Sub-mode register guidance (forces Claude toward specific emotional tones) ──

SUB_MODE_REGISTER = {
    # Emotional angle (transitional + emotional clips)
    "COST":       "What the artist had to give up / what a listener sacrifices. Concrete losses only.",
    "NAMING":     "Name the unnamed feeling. The word nobody uses for this specific thing.",
    "DOUBT":      "The moment before certainty. Questioning the path. No answers — only the ache.",
    "DEVOTION":   "Pouring out. Surrender. Offering. What you give that cannot come back.",
    "RUPTURE":    "The moment something breaks. The before and after. No slow fade — a tear.",
    # Signal angle
    "FINDER":     "Who finds this and exactly what they were looking for. Specific identity marker.",
    "PERMISSION": "What the listener finally has permission to feel / do / be.",
    "RECOGNITION":"The moment of being seen. I see you. This is for you specifically.",
    "SEASON":     "The time of life this meets you in. No season is accidental.",
    "UNSAID":     "The thing you never said out loud — spoken aloud on the track.",
    # Energy angle (performance clips)
    "BODY":       "Physical sensation — where in the body the track lands. Hands, chest, feet.",
    "TIME":       "Duration collapse. 3am. The hour nobody else is awake. The long night.",
    "GEOGRAPHY":  "A place on earth this belongs. Named. Coordinates, dust, altitude, salt.",
    "THRESHOLD":  "The line crossed. Before/after. Entry into a new state.",
    "DISSOLUTION":"Ego loss. What remains when the self isn't the self anymore.",
}


def _load_trend_brief() -> dict:
    """Load today's trend brief (from trend_scanner). Returns empty dict if absent.

    Keys of interest:
      dominant_emotion — what's winning on the FYP today
      oversaturated   — what to AVOID (opposite of the contrarian gap)
      contrarian_gap  — the opening we're designed to fill
      hook_pattern_of_day — current surface-level hook shape
    """
    from datetime import date as _d
    brief_path = PROJECT_DIR / "data" / "trend_brief" / f"{_d.today().isoformat()}.json"
    if brief_path.exists():
        try:
            return json.loads(brief_path.read_text())
        except Exception:
            pass
    # Yesterday is close enough — trends don't turn overnight
    from datetime import timedelta as _td
    yest = (_d.today() - _td(days=1)).isoformat()
    fallback = PROJECT_DIR / "data" / "trend_brief" / f"{yest}.json"
    if fallback.exists():
        try:
            return json.loads(fallback.read_text())
        except Exception:
            pass
    return {}


def _format_trend_context(brief: dict) -> str:
    """Format the trend brief as a prompt-injection block for Claude.

    We lean HARD on the contrarian gap — that's where we have edge. The
    dominant emotion is "what everyone is doing" → we don't want to echo it.
    """
    if not brief:
        return ""
    parts = []
    gap = brief.get("contrarian_gap", "")
    if gap:
        parts.append(f"Contrarian gap to exploit (this is our edge): {gap}")
    dom = brief.get("dominant_emotion", "")
    if dom:
        parts.append(f"What's oversaturated today (do NOT echo): {dom}")
    over = brief.get("oversaturated", "")
    if over:
        parts.append(f"Avoid these patterns: {over}")
    if not parts:
        return ""
    return "\n\n=== Today's trend intelligence ===\n" + "\n".join(parts) + "\n=== End trend intel ===\n"


def _find_claude_cli() -> str:
    """Locate the Claude CLI binary. Checks fixed paths then the desktop app bundle.

    The Claude Code desktop app ships a versioned binary at:
        ~/Library/Application Support/Claude/claude-code/<version>/claude.app/Contents/MacOS/claude

    We glob for the latest version so this survives app updates.
    Returns a resolved path string, or 'claude' as a last-resort PATH fallback.
    """
    fixed = [
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
        os.path.expanduser("~/.claude/local/claude"),
    ]
    for p in fixed:
        if os.path.exists(p):
            return p

    # Desktop app bundle — glob across all installed versions, pick latest
    pattern = os.path.expanduser(
        "~/Library/Application Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude"
    )
    candidates = sorted(_glob.glob(pattern), reverse=True)  # descending = newest first
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c

    return "claude"  # system PATH last resort


_CLAUDE_ERROR_MARKERS = (
    "Error: Reached max turns",
    "There's an issue with the selected model",
    "Run --model to pick a different model",
    "Invalid argument",
    "unknown option",
)


def _call_claude(prompt: str, system: str = "", timeout: int = 120) -> Optional[str]:
    """Call Claude CLI (haiku model). Returns response text or None on failure.

    IMPORTANT: build argv with --system-prompt appended AFTER --model <value>
    and BEFORE the trailing prompt. The old version used cmd.insert(3, ...)
    which pushed the model name out of position, so the CLI saw
    ``--model --system-prompt`` and errored with:
        "There's an issue with the selected model (--system-prompt)".
    That error string then leaked into captions as real text. Do not touch
    the argv order without testing with both system and no-system calls.

    Test-isolation escape hatch: if RJM_DISABLE_CLAUDE_CLI is set we skip the
    subprocess entirely and return None so callers fall back to deterministic
    example_fill / default caption paths. Production code leaves it unset.
    """
    if os.environ.get("RJM_DISABLE_CLAUDE_CLI"):
        return None
    claude_path = _find_claude_cli()

    cmd = [
        claude_path, "--print",
        "--model", "claude-haiku-4-5-20251001",
        "--no-session-persistence",
        "--max-turns", "1",
    ]
    if system:
        cmd += ["--system-prompt", system]
    cmd.append(prompt)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd="/tmp",
        )
        stdout = result.stdout.strip() if result.stdout else ""
        # Reject output that's obviously an error message leaking through
        # the CLI's stdout channel.
        if stdout and not any(m in stdout for m in _CLAUDE_ERROR_MARKERS):
            return stdout
        if stdout:
            logger.warning(f"Claude CLI returned error text on stdout: {stdout[:200]}")
        else:
            logger.warning(f"Claude call failed: {result.stderr[:300] if result.stderr else 'no output'}")
        return None
    except Exception as e:
        logger.warning(f"Claude call exception: {e}")
        return None


def generate_hooks_for_format(
    fmt: ClipFormat,
    track_title: str,
    track_facts: dict,
    weights: dict | None = None,
    exclude_ids: set | None = None,
    visual_context: dict | None = None,
    sub_mode_weights: dict | None = None,
) -> dict:
    """Generate a hook for a specific clip format.

    Returns: {hook, template_id, mechanism, sub_mode, exploration}
    """
    # 1. Pick template
    templates = pick_templates_for_format(fmt, weights, exclude_ids)
    template = templates[0]

    # 2. Pick sub-mode
    angle = FORMAT_TO_ANGLE.get(fmt, "emotional")
    sub_mode = pick_sub_mode(angle, sub_mode_weights)

    # 3. Fill template slots with Claude (or use example_fill as fallback)
    if template.slots:
        filled = _fill_template_with_claude(
            template, track_title, track_facts, sub_mode, visual_context=visual_context
        )
    else:
        filled = template.template  # no slots to fill

    # 4. Brand gate validation
    if filled:
        validated = gate_or_reject(filled)
        if validated:
            # Record template use for cooldown tracking (3-day lockout)
            log_template_use(template.id, fmt.value if hasattr(fmt, "value") else str(fmt))
            return {
                "hook": validated,
                "template_id": template.id,
                "mechanism": template.mechanism,
                "sub_mode": sub_mode,
                "exploration": False,
            }

    # 5. Fallback to example_fill
    logger.info(f"Using example_fill for {template.id}")
    log_template_use(template.id, fmt.value if hasattr(fmt, "value") else str(fmt))
    return {
        "hook": template.example_fill,
        "template_id": template.id,
        "mechanism": template.mechanism,
        "sub_mode": sub_mode,
        "exploration": False,
    }


_VISUAL_DESCRIPTIONS = {
    "satisfying":   "slow satisfying process footage (sand art, precision machines, fluid motion)",
    "nature":       "outdoor nature footage (forest, ocean, sky, mountains)",
    "urban":        "city / street scene footage",
    "performance":  "live DJ or crowd performance footage",
    "emotional":    "moody atmospheric b-roll (candlelight, long shadows, slow motion)",
    "abstract":     "abstract visual / light patterns",
}


def _visual_block(visual_context: dict | None) -> str:
    """Return a prompt line describing on-screen visuals, or empty string."""
    if not visual_context:
        return ""
    cat = visual_context.get("category", "")
    desc = _VISUAL_DESCRIPTIONS.get(cat, cat) if cat else "unspecified b-roll"
    return f"\nVisuals on screen: {desc}\nThe hook must be coherent with — or work as contrast to — this visual.\n"


def _fill_template_with_claude(
    template: HookTemplate,
    track_title: str,
    track_facts: dict,
    sub_mode: str,
    visual_context: dict | None = None,
) -> Optional[str]:
    """Fill a single template's slots using Claude."""
    slots_desc = "\n".join(f"  {k}: {v}" for k, v in template.slots.items())
    register_guidance = SUB_MODE_REGISTER.get(
        sub_mode, "Concrete, specific, felt-sense language.",
    )
    trend_ctx = _format_trend_context(_load_trend_brief())

    prompt = f"""Fill the slots in this hook template for the track "{track_title}".

Template: {template.template}
Slots:
{slots_desc}

Example (quality bar): {template.example_fill}

Track facts:
- BPM: {track_facts.get('bpm', 'unknown')}
- Scripture anchor: {track_facts.get('scripture_anchor', 'none')}
- Style: melodic techno / tribal psytrance
- Artist: Robert-Jan Mastenbroek (Dutch, 36, Tenerife)
- Energy: {track_facts.get('energy', 'high')}
{_visual_block(visual_context)}
Register: {sub_mode} — {register_guidance}
{trend_ctx}
Rules:
- Return ONLY the filled hook text, nothing else
- Keep under 280 characters
- Be concrete and specific (no generic adjectives — never "amazing", "beautiful", "incredible")
- Must pass the Visualization Test: the reader must SEE the words
- Must pass the Uniqueness Rule: no competing DJ could sign their name to it
- The hook must work as a text overlay on a short video
"""

    system = "You are a hook copywriter for a Dutch DJ/producer. Fill template slots with specific, concrete language. No generic adjectives. Under 280 chars. Return ONLY the filled text."

    response = _call_claude(prompt, system, timeout=90)
    if response:
        # Clean up: remove quotes, markdown, etc.
        cleaned = response.strip().strip('"').strip("'").strip("`")
        cleaned = re.sub(r'^#+\s*', '', cleaned)  # remove markdown headers
        cleaned = re.sub(r'\*+', '', cleaned)  # remove bold/italic markers
        if len(cleaned) <= 280 and len(cleaned) > 5:
            return cleaned

    return None


SPOTIFY_ARTIST_URL = "https://open.spotify.com/artist/2Seaafm5k1hAuCkpdq7yds"


def _title_case_track(track_title: str) -> str:
    """'fire in our hands' → 'Fire In Our Hands' — track titles display title-cased."""
    return " ".join(w.capitalize() for w in (track_title or "").split())


def _build_fallback_caption(track_title: str, platform: str, track_facts: dict) -> str:
    """Rich template caption used when Claude is unavailable or returns garbage.

    Includes: display-cased track title, artist name, BPM if known, scripture
    anchor if available, platform-specific CTA, and a 5-hashtag set that
    mirrors the hashtag bible in BRAND_VOICE.
    """
    display = _title_case_track(track_title) or "New Track"
    bpm = track_facts.get("bpm") or 0
    scripture = track_facts.get("scripture_anchor", "")
    bpm_tag = f"{int(bpm)} BPM melodic techno" if bpm else "Melodic techno"
    scripture_line = f"\n\n{scripture} — ancient truth, future sound." if scripture else ""
    hashtags = "#melodictechno #psytrance #holyrave #tenerife #RobertJanMastenbroek"

    if platform == "tiktok":
        cta = "Full track on Spotify 🔗 bio"
    elif platform == "youtube":
        cta = f"Stream on Spotify → {SPOTIFY_ARTIST_URL}\nSubscribe for more Holy Rave drops."
    elif platform == "facebook":
        cta = f"Full track on Spotify → {SPOTIFY_ARTIST_URL}"
    else:  # instagram, instagram_story, facebook_story
        cta = "Save this. Full track on Spotify (link in bio)."

    return (
        f"{display} — Robert-Jan Mastenbroek\n"
        f"{bpm_tag} · Holy Rave"
        f"{scripture_line}\n\n"
        f"{cta}\n\n"
        f"{hashtags}"
    )


def _caption_is_usable(text: str, track_title: str) -> bool:
    """Defensive check — reject model output that is empty, too short, or
    obviously off-brand. Prevents one-line "Halleluyah — Holy Rave | 140 BPM"
    style captions from shipping when the model gets lazy.
    """
    if not text:
        return False
    trimmed = text.strip()
    if len(trimmed) < 40 or len(trimmed) > 2200:
        return False
    # Caption must at least mention the track title or the artist name — the
    # two pieces of brand information we never want missing from a post.
    t_lower = trimmed.lower()
    if not (track_title.lower() in t_lower or "mastenbroek" in t_lower or "holy rave" in t_lower):
        return False
    # Caption must end with something actionable or hashtagged — avoids the
    # "one liner with no CTA" failure mode.
    if "#" not in trimmed and "spotify" not in t_lower and "bio" not in t_lower:
        return False
    return True


def generate_caption(
    track_title: str,
    hook_text: str,
    platform: str,
    track_facts: dict | None = None,
    visual_context: dict | None = None,
) -> str:
    """Generate a platform-specific caption for a clip.

    Structure every caption hits:
      Line 1: track + artist
      Line 2: BPM / genre tag
      (scripture block if available)
      CTA line(s): Spotify + platform-specific ask
      Hashtags: 5 brand-safe tags

    Falls back to a rich template if Claude is unavailable or returns a
    caption that doesn't meet the brand minimum (track name mention + CTA/hashtag).
    """
    facts = track_facts or {}
    scripture = facts.get("scripture_anchor", "")
    bpm = facts.get("bpm") or 0
    display_title = _title_case_track(track_title)

    platform_note = {
        "tiktok":          "Casual. Short punchy lines. 1-2 emojis OK.",
        "youtube":         "Slightly more descriptive. Include 'Subscribe' once. Safe for SEO.",
        "facebook":        "Conversational. Written for a 35+ audience that likes context.",
        "instagram":       "Polished. Three short lines max before hashtags.",
        "instagram_story": "Single bold line + CTA. No hashtags for Stories.",
        "facebook_story":  "Single bold line + CTA. No hashtags for Stories.",
    }.get(platform, "Polished. Short lines.")

    trend_ctx = _format_trend_context(_load_trend_brief())

    # TikTok's feed punishes "normal Instagram caption" patterns — no hashtag
    # block; casual phrasing; nothing that reads like marketing copy. Hashtags
    # embed inline (#holyrave) rather than appearing as a bottom block.
    if platform == "tiktok":
        tiktok_rules = (
            "\nTIKTOK-SPECIFIC (strict):\n"
            "- NO hashtag block at the end. Hashtags (2-3 max) must embed inline.\n"
            "- Casual, almost-private voice. Not promotional.\n"
            "- First line MUST be the hook or a dead-honest observation.\n"
            "- Max 2 emojis total.\n"
            "- End with 'full track: link in bio' or the Spotify URL — nothing else.\n"
        )
    else:
        tiktok_rules = ""

    visual_line = _visual_block(visual_context)

    prompt = f"""Write a {platform} caption for a short music video.

Track: {display_title} by Robert-Jan Mastenbroek (RJM / Holy Rave).
BPM: {int(bpm) if bpm else 'unknown'}
Genre: melodic techno / tribal psytrance
Hook shown on-screen: {hook_text or 'none'}
{visual_line}Scripture anchor (REQUIRED — weave in subtly, do not quote chapter:verse literally): {scripture or 'none'}
Spotify link to include verbatim: {SPOTIFY_ARTIST_URL}

Caption voice:
- Dark, Holy, Futuristic. Ancient truth, future sound.
- Visual and concrete. No adjectives like 'amazing', 'epic', 'beautiful'.
- Never preachy. Scripture is SUBTLE salt — reference or implication only.
- Hook the reader in the first 4 words.

Platform-specific direction:
- {platform_note}{tiktok_rules}
{trend_ctx}
MUST include (in this order):
1. Line with track title and 'Robert-Jan Mastenbroek'
2. Line with BPM + genre (e.g. '{int(bpm) if bpm else 130} BPM tribal techno · Holy Rave')
3. A 1-2 line creative hook/body that references a visual detail from the hook or scripture
4. A call-to-action line that includes 'Spotify' and either the URL or 'link in bio'
5. A final line with exactly 5 hashtags — must include #holyrave and #RobertJanMastenbroek
   (TIKTOK EXCEPTION: no hashtag block — embed 2-3 hashtags inline in the body)

Return ONLY the caption text — no commentary, no markdown fences.
"""

    system = (
        "You are the caption writer for Robert-Jan Mastenbroek (Dutch DJ/producer, "
        "melodic techno + tribal psytrance, Tenerife). Brand: Holy Rave — Dark, "
        "Holy, Futuristic. Never preachy. Never generic. Always concrete. "
        "Biblical subtly, visual always, CTA always, hashtags always."
    )

    response = _call_claude(prompt, system=system, timeout=90)
    if response:
        cleaned = _strip_meta_commentary(response)
        if _caption_is_usable(cleaned, track_title):
            # Platform + scripture-anchor checks run here (warn-only — telemetry)
            gate_caption(cleaned, platform=platform, track_title=track_title,
                         context=f"caption:{platform}")
            return gate_or_warn(cleaned, context=f"caption:{platform}")
        logger.info(
            f"[generator] Caption from Claude did not meet brand minimum "
            f"(len={len(cleaned)}) — using branded fallback for {platform}"
        )

    fallback = _build_fallback_caption(track_title, platform, facts)
    gate_caption(fallback, platform=platform, track_title=track_title,
                 context=f"caption-fallback:{platform}")
    return fallback


def _strip_meta_commentary(raw: str) -> str:
    """Claude sometimes appends chatty suffixes like:
        "---
        **Why this works:**
        - walls don't build overnight echoes Joshua 6 ..."
        "Want me to adjust the tone ...?"
    Strip everything from the first meta marker onward, remove markdown
    fences / bold, and trim surrounding whitespace.
    """
    if not raw:
        return ""
    text = raw.strip().strip("`").strip()
    # Strip leading/trailing ``` markdown fences entirely
    if text.startswith("```"):
        text = text.split("```", 2)[-1].strip()
    # Meta-commentary cut points — keep everything BEFORE these markers
    markers = [
        "\n---\n",
        "\n**Why this works",
        "\nWhy this works",
        "\n\nWant me to",
        "\nWant me to",
        "\nWould you like",
        "\nLet me know if",
    ]
    for m in markers:
        idx = text.find(m)
        if idx > 0:
            text = text[:idx].rstrip()
    # Strip markdown bold/italic that looks unprofessional in a feed
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    return text.strip()
