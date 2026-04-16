"""
generator.py — Claude-driven hook filling + caption generation with sub-mode diversity.

Migrated from outreach_agent/generator.py. Key changes:
- Works with ClipFormat enum (transitional/emotional/performance)
- Wires sub-mode diversity (COST, NAMING, DOUBT, etc.) that was coded but never executed
- Uses content_engine.hook_library for template selection
- Uses content_engine.brand_gate for validation
"""
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
)
from content_engine.brand_gate import gate_or_reject

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


def pick_sub_mode(angle: str) -> str:
    """Pick a random sub-mode for the given angle."""
    modes = ANGLE_SUB_MODES.get(angle, ANGLE_SUB_MODES["emotional"])
    return random.choice(modes)


def _call_claude(prompt: str, system: str = "", timeout: int = 120) -> Optional[str]:
    """Call Claude CLI (haiku model). Returns response text or None on failure."""
    claude_path = "/usr/local/bin/claude"
    if not os.path.exists(claude_path):
        # Try common locations
        for p in ["/opt/homebrew/bin/claude", os.path.expanduser("~/.claude/local/claude")]:
            if os.path.exists(p):
                claude_path = p
                break

    cmd = [
        claude_path, "--print",
        "--model", "claude-haiku-4-5-20251001",
        "--no-session-persistence",
        "--max-turns", "1",
        prompt,
    ]
    if system:
        cmd.insert(3, "--system-prompt")
        cmd.insert(4, system)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd="/tmp",
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        logger.warning(f"Claude call failed: {result.stderr[:300]}")
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
) -> dict:
    """Generate a hook for a specific clip format.

    Returns: {hook, template_id, mechanism, sub_mode, exploration}
    """
    # 1. Pick template
    templates = pick_templates_for_format(fmt, weights, exclude_ids)
    template = templates[0]

    # 2. Pick sub-mode
    angle = FORMAT_TO_ANGLE.get(fmt, "emotional")
    sub_mode = pick_sub_mode(angle)

    # 3. Fill template slots with Claude (or use example_fill as fallback)
    if template.slots:
        filled = _fill_template_with_claude(template, track_title, track_facts, sub_mode)
    else:
        filled = template.template  # no slots to fill

    # 4. Brand gate validation
    if filled:
        validated = gate_or_reject(filled)
        if validated:
            return {
                "hook": validated,
                "template_id": template.id,
                "mechanism": template.mechanism,
                "sub_mode": sub_mode,
                "exploration": False,
            }

    # 5. Fallback to example_fill
    logger.info(f"Using example_fill for {template.id}")
    return {
        "hook": template.example_fill,
        "template_id": template.id,
        "mechanism": template.mechanism,
        "sub_mode": sub_mode,
        "exploration": False,
    }


def _fill_template_with_claude(
    template: HookTemplate,
    track_title: str,
    track_facts: dict,
    sub_mode: str,
) -> Optional[str]:
    """Fill a single template's slots using Claude."""
    slots_desc = "\n".join(f"  {k}: {v}" for k, v in template.slots.items())
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

Register: {sub_mode} — fill the slots with this emotional register in mind.

Rules:
- Return ONLY the filled hook text, nothing else
- Keep under 280 characters
- Be concrete and specific (no generic adjectives)
- The hook must work as a text overlay on a short video
"""

    system = "You are a hook copywriter for a Dutch DJ/producer. Fill template slots with specific, concrete language. No generic adjectives. Under 280 chars. Return ONLY the filled text."

    response = _call_claude(prompt, system, timeout=60)
    if response:
        # Clean up: remove quotes, markdown, etc.
        cleaned = response.strip().strip('"').strip("'").strip("`")
        cleaned = re.sub(r'^#+\s*', '', cleaned)  # remove markdown headers
        cleaned = re.sub(r'\*+', '', cleaned)  # remove bold/italic markers
        if len(cleaned) <= 280 and len(cleaned) > 5:
            return cleaned

    return None


def generate_caption(
    track_title: str,
    hook_text: str,
    platform: str,
    track_facts: dict | None = None,
) -> str:
    """Generate a platform-specific caption for a clip.

    Falls back to a template caption if Claude is unavailable.
    """
    facts = track_facts or {}
    scripture = facts.get("scripture_anchor", "")

    prompt = f"""Write a short caption for a {platform} Reel/Short.

Track: {track_title} by Robert-Jan Mastenbroek
Hook used: {hook_text}
Scripture anchor: {scripture or 'none'}
Platform: {platform}

Rules:
- 1-3 short lines
- Include track name
- Include 3-5 relevant hashtags
- If scripture anchor exists, weave it in subtly (Matthew 5:13 — salt, not sermon)
- End with a call-to-action (save this, link in bio, full track on Spotify)
- For TikTok: more casual, emoji OK
- For Instagram: slightly more polished
- For YouTube: include "Subscribe" CTA
- For Facebook: conversational
"""

    response = _call_claude(prompt, timeout=60)
    if response and len(response) < 1000:
        return response.strip()

    # Fallback caption
    hashtags = "#melodictechno #holyrave #tenerife #newmusic #techno"
    if platform == "tiktok":
        return f"{track_title} — Robert-Jan Mastenbroek\n\nFull track on Spotify (link in bio)\n\n{hashtags}"
    elif platform == "youtube":
        return f"{track_title} — Robert-Jan Mastenbroek\n\nStream on Spotify: link in description\nSubscribe for more\n\n{hashtags}"
    else:
        return f"{track_title} — Robert-Jan Mastenbroek\n\nSave this. Full track on Spotify (link in bio)\n\n{hashtags}"
