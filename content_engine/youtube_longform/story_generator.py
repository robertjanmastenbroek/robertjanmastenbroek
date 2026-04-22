"""
story_generator.py — Autonomous biblical visual story generator.

For any Holy Rave track with a scripture anchor, generates a unique 9-keyframe
MorphStory grounded in the biblical passage, matching the System A or B
aesthetic doctrine from motion.py, with subtle futuristic-electronic blend.

Uses the Claude CLI as a subprocess (same pattern as
outreach_agent/template_engine.py) — leverages the user's Claude Max plan,
no separate API key or billing.

Entry points:
  generate_story_for_track(track_title, scripture_anchor, bpm)  → MorphStory
  main() — CLI: `python3 -m content_engine.youtube_longform.story_generator \
                  --track "Not By Might" --anchor "Zechariah 4:6" --bpm 140`

Output paths:
  data/youtube_longform/generated_stories/{slug}.json   — cache (loadable)
  data/youtube_longform/generated_stories/{slug}.py     — snippet (paste-target)
  data/youtube_longform/story_generator_log.jsonl       — audit trail

Load at runtime via load_cached_story(track_title). Ship permanently by
pasting the .py snippet into motion.py.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform import scripture
from content_engine.youtube_longform.motion import (
    Keyframe, MorphClip, MorphStory, STORIES, TRACK_STORIES, system_for_bpm,
)

logger = logging.getLogger(__name__)


# ─── Claude CLI plumbing (mirrors outreach_agent/template_engine.py) ─────────
# Uses the Claude Max plan via subprocess — per RJM instruction "only Claude
# CLI like always" (the anthropic SDK would need a separate API key + billing
# which we're avoiding).

_ISOLATED_HOME = Path(__file__).parent / ".claude_subprocess_home"
_CLAUDE_CLI: Optional[str] = None
_DEFAULT_MODEL = os.getenv("STORY_GENERATOR_MODEL", "claude-opus-4-7")
_DEFAULT_TIMEOUT_S = int(os.getenv("STORY_GENERATOR_TIMEOUT_S", "300"))


def _ensure_isolated_home() -> str:
    """Minimal HOME dir for subprocess calls to avoid hook/MCP conflicts."""
    claude_dir = _ISOLATED_HOME / ".claude"
    settings = claude_dir / "settings.json"
    if not settings.exists():
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings.write_text("{}\n")
    return str(_ISOLATED_HOME)


def _find_claude_cli() -> str:
    """Locate the Claude CLI binary. Same algorithm as template_engine."""
    env_path = os.getenv("CLAUDE_CLI_PATH")
    if env_path and Path(env_path).is_file():
        return env_path
    base = Path(os.path.expanduser(
        "~/Library/Application Support/Claude/claude-code"
    ))
    if base.exists():
        for ver_dir in sorted(base.iterdir(), reverse=True):
            candidate = ver_dir / "claude.app" / "Contents" / "MacOS" / "claude"
            if candidate.is_file():
                return str(candidate)
    for name in ("claude", "claude-code"):
        r = subprocess.run(["which", name], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    raise FileNotFoundError(
        "Cannot find Claude CLI. Set CLAUDE_CLI_PATH env var or symlink into PATH."
    )


def _get_claude_cli() -> str:
    global _CLAUDE_CLI
    if not _CLAUDE_CLI:
        _CLAUDE_CLI = _find_claude_cli()
        logger.info("Using Claude CLI: %s", _CLAUDE_CLI)
    return _CLAUDE_CLI


def _call_claude(
    prompt: str,
    model: str = _DEFAULT_MODEL,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> str:
    """Run one Claude CLI invocation. Returns stdout text."""
    cli = _get_claude_cli()
    env = {**os.environ, "HOME": _ensure_isolated_home()}
    # subprocess.run with list-form args is shell-safe — no injection risk.
    result = subprocess.run(
        [cli, "--model", model, "-p", prompt],
        capture_output=True, text=True, timeout=timeout_s,
        stdin=subprocess.DEVNULL, env=env,
    )
    if result.returncode != 0:
        raise StoryGenerationError(
            f"Claude CLI exited {result.returncode}: "
            f"{(result.stderr or '').strip()[:500]}"
        )
    return result.stdout.strip()


class StoryGenerationError(Exception):
    """Raised when autonomous story generation fails."""


# ─── Doctrine prompt (embedded from motion.py header) ────────────────────────

_SYSTEM_PROMPT = """\
You are the Holy Rave visual story writer.

Holy Rave is a YouTube music channel for Robert-Jan Mastenbroek's "nomadic
electronic" music — organic house (128-138 BPM) through tribal psytrance
(140+ BPM). Every track has a scripture anchor. For each track you write a
unique MorphStory: 9 hero-portrait keyframes + 9 drone-camera morph clips
that seamlessly loop back, deployed on fal.ai's Flux 2 Pro /edit + Kling
O3 image-to-video pipeline.

## Visual doctrine — two systems by BPM

SYSTEM A (128-138 BPM, organic house):
  time-of-day:   dawn / golden hour / dusk (daytime dominant)
  palette:       warm amber + terracotta + soft gold + pale indigo + ochre
  subject:       solo contemplative figure(s), veiled portraits,
                 handpan/oud players, elders in prayer, lone watchers
  setting:       oasis, cedar grove, cliffside vigil, prayer tent,
                 dawn caravan
  camera:        floating drone, slow cinematic glide, gentle orbital arcs
  lighting:      soft warm key, gradient shadows, ambient softness
  register:      contemplation, stillness, reverence, ceremony
  reference DNA: Café de Anatolia / Sol Selectas / Bedouin / Monolink /
                 Ben Böhmer / All Day I Dream

SYSTEM B (140+ BPM, tribal psytrance):
  time-of-day:   deep night — torchlight, firelight, moonlight, starlight
                 (NO daytime)
  palette:       indigo-black + ember-orange + amber-crimson + silver +
                 gold-as-fire-accent only
  subject:       warriors / priests mid-ritual, ecstatic crowds,
                 fire-bearers, sound-wave moments, architectural macro
  setting:       fire circle, night altar, temple at night, ziggurat
                 under stars, macro sacred stone detail
  camera:        sweeping arcs, fast orbital zooms, kinetic aerial swoops
  lighting:      hard amber key from below (firelight), deep obsidian
                 shadows, rim-lit silhouettes, high contrast
  register:      celebration, trance, ecstasy, confrontation, sacred awe
  reference DNA: Astrix / Ace Ventura / Vini Vici / Symphonix / Ranji /
                 Iboga Records

## Futuristic-electronic blend (SUBTLE, 10% presence, both systems)

Holy Rave is electronic music. Weave subtle futuristic qualities into
ancient biblical imagery. Pick 1-3 elements per story, never all:
  · Ancient carved symbols or unreadable glyphic marks glowing with
    holographic pale light  (NEVER specify a specific letter / word /
    inscription — diffusion models can't render non-Latin script and
    will substitute a visually-similar Latin character; see HARD BAN
    below)
  · Ancient flames with subtle plasma/energy quality (clean halos)
  · Dust particles rendered as faintly luminescent motes
  · Bronze/silver ornament with subtle iridescent sheen
  · Prismatic light shafts through smoke or dust
  · Fiber-optic glinting threads in hand-woven garments
  · Architectural ornament with subtle geometric motion
  · Constellations arranging into abstract geometric patterns or sacred
    symbols  (NOT specific alphabetic letters — same reason as above)
  · Particle effects for breath / wind / spirit as clean rendered geometry
  · Minor chromatic aberration / lens flares at peak intensity moments

BANNED as too-literal sci-fi: cybernetic body parts, holographic UI,
cyberpunk-color neon (purple/teal/hot-pink), floating text overlays,
AR HUD frames, robot or android subjects. No modern clothing. No
contemporary technology visible.

HARD BAN — specific letters, words, inscriptions, or readable text:
Never write prompts like "the letter ר glows", "the word שְׁמַע
appears", "Paleo-Hebrew calligraphy spelling YHWH", etc. Flux will
substitute a visually-similar Latin glyph (the 2026-04-22 Not By Might
thumbnail asked for ר and got a Latin R — breaks the Abrahamic-nomadic
brand illusion instantly). Use imagery-only language: "ancient carved
marks", "unreadable ritual inscriptions", "glyphic scroll ornament",
"abstract glowing symbols on stone". If a specific text overlay is
truly needed for a track, that's a Shotstack post-process job, not a
Flux prompt.

ALSO BANNED (brand rules):
  · Mesoamerican / Aztec / Mayan imagery (feather headdresses, pyramids,
    stepped-temple pre-Columbian ornament)
  · Hindu / Buddhist / OM / yantra / mandala-as-Hindu iconography
  · Islamic calligraphy or arabesque pattern (Hebrew/Paleo-Canaanite only)
  · Christian Latin-cross crucifix / gothic cathedral
  · Generic New Age cosmic / sacred geometry unrelated to Temple/Tabernacle

Locked palette tokens (ALWAYS used, never drift):
  terracotta #b8532a · indigo-night #1a2a4a · liturgical gold #d4af37
  · ochre #c8883a · obsidian #0a0a0a · bone-white #ffffff

## Biblical sourcing — non-negotiable

Every keyframe's subject MUST come from specific imagery in the scripture
passage, or the wider biblical narrative around it. If the passage
mentions "a lamp stand all of gold" one keyframe IS that lamp stand. If
"breath came into them and they lived" one keyframe shows that moment.
Do NOT invent non-biblical imagery because it looks cool. The Bible is
the source — you adapt, you don't improvise.

## Within-system variety rule

You'll be given already-shipped story_ids. Your story MUST differ from
each on at least 2 dimensions: subject, palette, camera, setting,
time-of-day, register. No repeat "fire circle at night with ecstatic
dancers" after Halleluyah; no repeat "throne room with seraphim and
smoke" after Kadosh.

## Output format

Return ONLY a JSON object (no markdown, no code fences, no prose). Schema:

{
  "story_id": "<track_slug>_<theme>_<register>",
  "keyframes": [
    {
      "keyframe_id": "rjm_<track_slug>_<subject_slug>",
      "still_prompt": "<FULL Flux 2 Pro /edit prompt, 80-140 words. One subject per keyframe. Period era + palette + composition + subtle futuristic element if relevant + 'photographic realism' + '16:9' + '--style raw'>"
    },
    ... (exactly 9 entries)
  ],
  "morphs": [
    {
      "clip_id": "rjm_<track_slug>_<from_slug>__to__<to_slug>",
      "from_kf_id": "<matches a keyframe_id>",
      "to_kf_id":   "<matches a keyframe_id>",
      "motion_prompt": "<50-90 words. Cinematic drone camera + what transforms + register keyword + 'seamlessly closes the hypnotic loop' on the final morph>"
    },
    ... (exactly 9 entries; LAST to_kf_id MUST equal FIRST from_kf_id)
  ],
  "thumbnail_keyframe": {
    "keyframe_id": "rjm_<track_slug>_thumbnail",
    "still_prompt": "<ultra-close CTR thumbnail. Face 70% of frame. High contrast. Direct eye contact. Same palette. 80-140 words. 'photographic realism', '16:9', '--style raw'>"
  }
}

Every keyframe_id in morphs MUST match an id in keyframes. The loop MUST close.
"""


# ─── Main generator ──────────────────────────────────────────────────────────

def generate_story_for_track(
    track_title:      str,
    scripture_anchor: str,
    bpm:              int,
    scripture_text:   Optional[str] = None,
    language:         str = "en",
    model:            str = _DEFAULT_MODEL,
) -> MorphStory:
    """
    Generate a unique MorphStory grounded in the track's scripture anchor.
    Validates structure + loop closure. Raises StoryGenerationError on failure.
    """
    if scripture_text is None:
        scripture_text = scripture.verse_for(scripture_anchor) or (
            f"[Full text for {scripture_anchor} not cached — generate visuals "
            f"from your knowledge of that specific biblical passage.]"
        )

    system = system_for_bpm(bpm)
    shipped_ids = sorted(STORIES.keys())

    user_message = f"""\
Generate a MorphStory for this Holy Rave track.

TRACK: {track_title}
SCRIPTURE ANCHOR: {scripture_anchor}
BPM: {bpm}
SYSTEM: {system}  ({"psytrance 140+" if system == "B" else "organic 128-138"})
LANGUAGE: {language}  ({"Hebrew vocals" if language == "he" else "English vocals"})

SCRIPTURE PASSAGE (your visual source):
{scripture_text}

Already-shipped story_ids (your story MUST differ on at least 2 dimensions):
{", ".join(shipped_ids)}

Write 9 keyframes + 9 morphs + 1 thumbnail keyframe, all grounded in the
scripture. Include 1-3 subtle futuristic-electronic elements. Match System
{system} vocabulary. Close the chain. Return ONLY the JSON.
"""

    full_prompt = _SYSTEM_PROMPT + "\n\n---\n\n" + user_message

    logger.info(
        "Generating: track=%r anchor=%r bpm=%d system=%s",
        track_title, scripture_anchor, bpm, system,
    )
    raw = _call_claude(full_prompt, model=model)
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise StoryGenerationError(
            f"Invalid JSON from Claude: {e}\nRaw[:600]: {raw[:600]}"
        )

    if not isinstance(data, dict):
        raise StoryGenerationError(f"Expected object, got {type(data).__name__}")
    for key in ("story_id", "keyframes", "morphs"):
        if key not in data:
            raise StoryGenerationError(f"Response missing key: {key!r}")

    try:
        keyframes = [Keyframe(**kf) for kf in data["keyframes"]]
        morphs    = [MorphClip(**m)  for m in data["morphs"]]
        thumb_kf  = (
            Keyframe(**data["thumbnail_keyframe"])
            if data.get("thumbnail_keyframe") else None
        )
    except TypeError as e:
        raise StoryGenerationError(f"Bad keyframe/morph shape: {e}")

    if len(keyframes) != 9 or len(morphs) != 9:
        raise StoryGenerationError(
            f"Expected 9+9, got {len(keyframes)} keyframes / {len(morphs)} morphs"
        )

    # MorphStory.__post_init__ validates loop closure + keyframe_id refs
    try:
        story = MorphStory(
            story_id=data["story_id"],
            keyframes=keyframes,
            morphs=morphs,
            thumbnail_keyframe=thumb_kf,
        )
    except ValueError as e:
        raise StoryGenerationError(f"MorphStory validation failed: {e}")

    _log_generation(track_title, scripture_anchor, bpm, story)
    save_cached_story(track_title, story)
    logger.info(
        "Story ready: %s  (%d kf / %d morphs, thumbnail=%s)",
        story.story_id, len(keyframes), len(morphs),
        "yes" if thumb_kf else "no",
    )
    return story


# ─── JSON cache (safe runtime-loadable format) ───────────────────────────────

_SNIPPET_DIR = cfg.REGISTRY_DIR / "generated_stories"
_LOG_FILE = cfg.REGISTRY_DIR / "story_generator_log.jsonl"


def _track_slug(track_title: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in track_title.lower()).strip("_")


def save_cached_story(track_title: str, story: MorphStory) -> Path:
    """Write the story as JSON (loadable at runtime, no code execution)."""
    _SNIPPET_DIR.mkdir(parents=True, exist_ok=True)
    path = _SNIPPET_DIR / f"{_track_slug(track_title)}.json"
    payload = {
        "track_title":         track_title,
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "story_id":            story.story_id,
        "keyframes":           [asdict(k) for k in story.keyframes],
        "morphs":              [asdict(m) for m in story.morphs],
        "thumbnail_keyframe":  (
            asdict(story.thumbnail_keyframe) if story.thumbnail_keyframe else None
        ),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def load_cached_story(track_title: str) -> Optional[MorphStory]:
    """Load a previously-generated story from the JSON cache. No code exec."""
    path = _SNIPPET_DIR / f"{_track_slug(track_title)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        keyframes = [Keyframe(**kf) for kf in data["keyframes"]]
        morphs    = [MorphClip(**m)  for m in data["morphs"]]
        thumb     = (
            Keyframe(**data["thumbnail_keyframe"])
            if data.get("thumbnail_keyframe") else None
        )
        story = MorphStory(
            story_id=data["story_id"],
            keyframes=keyframes, morphs=morphs,
            thumbnail_keyframe=thumb,
        )
        # Register in the runtime dicts so story_for_track finds it
        STORIES[story.story_id] = story
        TRACK_STORIES[track_title.lower().strip()] = story
        return story
    except Exception as e:
        logger.warning("load_cached_story %s: %s", path, e)
        return None


def _log_generation(
    track_title: str, anchor: str, bpm: int, story: MorphStory,
) -> None:
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_FILE, "a") as f:
        f.write(json.dumps({
            "timestamp":        datetime.now(timezone.utc).isoformat(),
            "track_title":      track_title,
            "scripture_anchor": anchor,
            "bpm":              bpm,
            "story_id":         story.story_id,
            "keyframe_count":   len(story.keyframes),
            "morph_count":      len(story.morphs),
            "has_thumbnail":    story.thumbnail_keyframe is not None,
        }) + "\n")


# ─── Publisher-facing resolver ───────────────────────────────────────────────

def resolve_or_generate_story(
    track_title:      str,
    scripture_anchor: str,
    bpm:              int,
    language:         str = "en",
) -> MorphStory:
    """
    Public helper for publisher.py. Priority:
      1. motion.TRACK_STORIES (hand-written, highest quality)
      2. JSON cache (previously auto-generated, registered in runtime)
      3. Generate fresh via Claude CLI
      4. Fall back to motion.DEFAULT_STORY (last resort)
    """
    key = track_title.lower().strip()
    if key in TRACK_STORIES:
        return TRACK_STORIES[key]

    cached = load_cached_story(track_title)
    if cached:
        logger.info("Loaded cached story for %s", track_title)
        return cached

    if scripture_anchor:
        try:
            story = generate_story_for_track(
                track_title=track_title,
                scripture_anchor=scripture_anchor,
                bpm=bpm,
                language=language,
            )
            # Registered in STORIES + TRACK_STORIES via save_cached_story +
            # load_cached_story round-trip path; but make sure:
            STORIES[story.story_id] = story
            TRACK_STORIES[key] = story
            return story
        except StoryGenerationError as e:
            logger.warning(
                "Auto-generation failed for %s: %s — falling back to DEFAULT_STORY",
                track_title, e,
            )

    from content_engine.youtube_longform.motion import DEFAULT_STORY
    return DEFAULT_STORY


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--track",    required=True, help="e.g. 'Not By Might'")
    parser.add_argument("--anchor",   required=True, help="Scripture ref, e.g. 'Zechariah 4:6'")
    parser.add_argument("--bpm",      type=int, required=True, help="Track BPM")
    parser.add_argument("--language", default="en")
    parser.add_argument("--model",    default=_DEFAULT_MODEL)
    args = parser.parse_args()

    try:
        story = generate_story_for_track(
            track_title=args.track,
            scripture_anchor=args.anchor,
            bpm=args.bpm,
            language=args.language,
            model=args.model,
        )
    except StoryGenerationError as e:
        print(f"✗ Generation failed: {e}", file=sys.stderr)
        return 1

    print(f"\n✓ Generated story {story.story_id!r}")
    for kf in story.keyframes:
        print(f"  kf: {kf.keyframe_id:40s}  {kf.still_prompt[:90]}…")
    for m in story.morphs:
        print(f"  morph: {m.from_kf_id:35s} → {m.to_kf_id}")
    if story.thumbnail_keyframe:
        print(f"  thumbnail: {story.thumbnail_keyframe.keyframe_id}")

    cache = _SNIPPET_DIR / f"{_track_slug(args.track)}.json"
    print(f"\n  Cached at: {cache}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
