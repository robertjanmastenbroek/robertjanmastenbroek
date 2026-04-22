#!/usr/bin/env python3
"""
extract_viral_dna.py — One-time distillation of the viral-thumbnail corpus
into a structured style guide per genre family.

Takes the top-N highest-viewed thumbnails from each bucket in
content/images/proven_viral/ and sends them to Claude Vision (via the
Claude CLI subprocess, same pattern as story_generator.py — NO Anthropic
SDK per project rule). Claude analyzes the batch and extracts the
shared compositional DNA: lighting, palette, subject framing, depth,
shock elements, composition anti-patterns.

Output per genre:
  content_engine/youtube_longform/viral_dna/viral_dna_<genre>.json

Those files are then loaded at prompt-build time by viral_dna.py and
prepended to every thumbnail still_prompt as a "what makes a viral
thumbnail in this scene" preamble — the distilled 100-image DNA
informs every generation going forward, at ZERO per-generation cost.

Re-run this script when:
  - The viral visual vocabulary shifts (every 6-12 months)
  - We harvest a fresh corpus (e.g. 2027 proven-viral run)
  - We change the target aesthetic (major brand pivot)

Usage:
    python3 scripts/extract_viral_dna.py                  # both genres
    python3 scripts/extract_viral_dna.py --genre organic  # one genre
    python3 scripts/extract_viral_dna.py --top-n 20       # fewer refs

Budget: expect ~$0.50-1.00 total (Claude Sonnet via CLI, one call per
genre, ~30 images per call). Falls well under FAL_DAILY_USD_CAP.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from content_engine.youtube_longform import config as cfg


logger = logging.getLogger(__name__)

# ─── Claude CLI wiring (same pattern as story_generator.py) ──────────────────

_ISOLATED_HOME = (
    Path(__file__).resolve().parent.parent
    / "content_engine" / "youtube_longform" / ".claude_subprocess_home"
)


def _ensure_isolated_home() -> str:
    """Create an isolated Claude HOME so the subprocess doesn't pollute the user's sessions."""
    _ISOLATED_HOME.mkdir(parents=True, exist_ok=True)
    return str(_ISOLATED_HOME)


def _find_claude_cli() -> str:
    """Locate the Claude CLI binary. Same algorithm as story_generator.py."""
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


# ─── The extraction prompt ──────────────────────────────────────────────────

_SYSTEM_PREAMBLE = """\
You are the Holy Rave visual analyst.

Holy Rave is a YouTube music channel for Robert-Jan Mastenbroek's "nomadic
electronic" music (organic house 128-138 BPM through tribal psytrance 140+
BPM). Every track has a scripture anchor and Iron Age Hebrew / Middle
Eastern / desert-tribal visual DNA.

I'm attaching the top highest-viewed YouTube thumbnails from our viral
reference corpus in the {genre_label} bucket ({n_images} images, all
combined view count > 100M). Each is a proven high-CTR thumbnail.

Your job: extract the SHARED compositional DNA that makes these
thumbnails viral for this genre. NOT the individual subjects — the
patterns across all of them. I'll use the output to distill a text
preamble that conditions Flux 2 Pro image generation on these patterns.
"""

_TASK_PROMPT = """\
For the {genre_label} bucket, analyze all {n_images} attached
thumbnails and extract the following compositional DNA. Output must be
valid JSON and ONLY valid JSON — no prose, no code fences, no preamble.

{{
  "genre_label": "{genre_label}",
  "n_images_analyzed": {n_images},
  "lighting": {{
    "time_of_day":       "dominant time-of-day pattern (one phrase)",
    "key_light":         "direction + color of the main light source",
    "fill_light":        "ambient fill pattern + intensity",
    "shadow_character":  "soft / hard / dramatic; luminous or obsidian"
  }},
  "palette": {{
    "dominant_warm":     "one hex color or descriptor",
    "dominant_cool":     "one hex color or descriptor",
    "accent":            "one hex color or descriptor",
    "ratio":             "approximate warm/cool/accent ratio (e.g. 60/30/10)",
    "saturation":        "low / medium / high / ultra-saturated"
  }},
  "subject_framing": {{
    "subject_width_pct":         "how much of the frame the subject occupies (e.g. '25-40%')",
    "subject_position":          "left third / center / right third / full frame",
    "subject_gaze":              "direct-camera / looking-up / looking-away / eyes-closed",
    "crop_style":                "tight portrait / waist-up / wide establishing / scene"
  }},
  "scene_depth": {{
    "foreground":        "what anchors the foreground",
    "midground":         "what fills the middle of the image",
    "background":        "what's behind, with detail",
    "atmospheric_effect": "dust / smoke / haze / ember / none"
  }},
  "shock_element": {{
    "description":       "the single visual element that stops the scroll",
    "frequency":         "how often this element appears (e.g. '22/30 thumbnails')"
  }},
  "anti_patterns": [
    "top 3-5 compositional moves that these viral thumbnails AVOID (what NOT to do)"
  ],
  "prompt_preamble": "A single paragraph (120-180 words) distilling the above into a Flux 2 Pro prompt preamble. This will be prepended to every thumbnail generation prompt for the {genre_label} bucket. Use photographic language Flux understands: specific lens, depth-of-field, lighting ratios, palette hex codes where helpful, framing percentages. NO text overlays, NO logos, NO festival names — those are stripped."
}}

Be specific and concrete. "Warm cinematic lighting" is worthless. "Golden-hour key light from 45° right casting long warm shadows with indigo ambient fill and amber rim-light" is useful. The prompt_preamble field is what Flux actually receives — make it count.
"""


def _call_claude_with_images(
    prompt: str,
    image_paths: list[Path],
    model: str = "claude-opus-4-5",
    timeout_s: int = 600,
) -> str:
    """
    Run Claude CLI with prompt + attached images via @-references.
    The CLI accepts image file paths in the prompt via the @path syntax,
    which it loads as image attachments.
    """
    cli = _find_claude_cli()
    env = {**os.environ, "HOME": _ensure_isolated_home()}

    # Prepend image @-references to the prompt. Claude CLI parses these
    # as attachments.
    ref_block = "\n".join(f"@{p.resolve()}" for p in image_paths)
    full_prompt = f"{ref_block}\n\n{prompt}"

    logger.info(
        "Claude CLI invoke | model=%s | %d image refs | prompt=%d chars",
        model, len(image_paths), len(full_prompt),
    )
    result = subprocess.run(
        [cli, "--model", model, "-p", full_prompt],
        capture_output=True, text=True, timeout=timeout_s,
        stdin=subprocess.DEVNULL, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Claude CLI exited {result.returncode}: "
            f"{(result.stderr or '').strip()[:800]}"
        )
    return result.stdout.strip()


# ─── Corpus selection ───────────────────────────────────────────────────────

def _pick_top_n(genre_bucket_short: str, n: int) -> list[tuple[Path, dict]]:
    """
    Return the top-n highest-viewed thumbnails from the given bucket.
    `genre_bucket_short` is the manifest field value — "130_organic"
    or "140_psytrance".
    """
    manifest_path = cfg.PROVEN_VIRAL_DIR / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Viral corpus manifest missing: {manifest_path}. Run the harvester first."
        )
    with open(manifest_path) as f:
        manifest = json.load(f)

    rows = [r for r in manifest if r.get("bucket") == genre_bucket_short]
    if not rows:
        raise RuntimeError(
            f"No entries in manifest with bucket={genre_bucket_short!r}. "
            f"Known buckets: {sorted(set(r.get('bucket', '') for r in manifest))}"
        )

    rows.sort(key=lambda r: r.get("view_count_estimate", 0), reverse=True)

    picked: list[tuple[Path, dict]] = []
    for row in rows:
        if len(picked) >= n:
            break
        bucket_dir = cfg.PROVEN_VIRAL_DIR / f"bucket_{genre_bucket_short}"
        p = bucket_dir / row["filename"]
        if p.exists():
            picked.append((p, row))
        else:
            logger.warning("Manifest references missing file: %s", p.name)

    if len(picked) < n:
        logger.warning(
            "Only %d / %d requested thumbnails available on disk for %s",
            len(picked), n, genre_bucket_short,
        )
    return picked


# ─── Main extraction ────────────────────────────────────────────────────────

_GENRE_LABELS = {
    "organic_house":    ("130_organic",   "Organic house / ethnic house (128-138 BPM)"),
    "tribal_psytrance": ("140_psytrance", "Tribal psytrance / Goa psytrance (140+ BPM)"),
}


def extract_genre(
    genre_family: str,
    top_n: int = 30,
    out_dir: Optional[Path] = None,
    model: str = "claude-opus-4-5",
) -> Path:
    """
    Run the Claude extraction for one genre family. Returns the path
    to the written JSON file.
    """
    if genre_family not in _GENRE_LABELS:
        raise ValueError(
            f"Unknown genre_family {genre_family!r}. "
            f"Known: {list(_GENRE_LABELS)}"
        )
    bucket_short, genre_label = _GENRE_LABELS[genre_family]

    picked = _pick_top_n(bucket_short, top_n)
    logger.info(
        "Selected %d images for %s (top-viewed: %s)",
        len(picked), genre_family,
        picked[0][1].get("title", "?")[:60] if picked else "(none)",
    )

    # Build the prompt
    n = len(picked)
    prompt = (
        _SYSTEM_PREAMBLE.format(genre_label=genre_label, n_images=n)
        + "\n\n"
        + _TASK_PROMPT.format(genre_label=genre_label, n_images=n)
    )

    paths = [p for p, _ in picked]
    raw_output = _call_claude_with_images(prompt, paths, model=model)

    # Claude may wrap the JSON in prose despite instructions. Extract.
    parsed = _extract_json_blob(raw_output)
    # Enrich with metadata that's useful to the consumer
    parsed["_meta"] = {
        "genre_family":    genre_family,
        "genre_bucket":    bucket_short,
        "n_images":        n,
        "sampled_images":  [
            {
                "filename":       row["filename"],
                "title":          row.get("title", ""),
                "view_count":     row.get("view_count_estimate", 0),
                "channel":        row.get("channel", ""),
            }
            for _, row in picked
        ],
    }

    # Write to viral_dna/
    if out_dir is None:
        out_dir = (
            PROJECT_ROOT
            / "content_engine" / "youtube_longform" / "viral_dna"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"viral_dna_{genre_family}.json"
    out_path.write_text(json.dumps(parsed, indent=2))
    logger.info("Wrote viral DNA → %s", out_path)
    return out_path


def _extract_json_blob(s: str) -> dict:
    """
    Tolerant JSON extractor — handles Claude wrapping the JSON in code
    fences, prose, etc. Finds the first balanced {...} block and parses it.
    """
    s = s.strip()
    # Strip common code-fence wrappers
    for fence in ("```json", "```JSON", "```"):
        if s.startswith(fence):
            s = s[len(fence):].lstrip()
            if s.endswith("```"):
                s = s[:-3].rstrip()
    # Find first { and walk to the matching }
    start = s.find("{")
    if start < 0:
        raise ValueError(f"No JSON object found in output:\n{s[:500]}")
    depth = 0
    end = -1
    for i, ch in enumerate(s[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise ValueError(f"Unbalanced JSON in output:\n{s[:500]}")
    blob = s[start:end]
    return json.loads(blob)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--genre", default="all",
        choices=["all", "organic_house", "tribal_psytrance",
                 "organic", "psytrance"],   # friendly aliases
        help="Which genre family to extract (default: both)",
    )
    parser.add_argument(
        "--top-n", type=int, default=30,
        help="How many top-viewed thumbnails to feed Claude per genre",
    )
    parser.add_argument(
        "--model", default="claude-opus-4-5",
        help="Claude CLI model (default: claude-opus-4-5)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Alias resolution
    alias = {"organic": "organic_house", "psytrance": "tribal_psytrance"}
    genre = alias.get(args.genre, args.genre)

    genres = ["organic_house", "tribal_psytrance"] if genre == "all" else [genre]

    for g in genres:
        print(f"\n══════ Extracting viral DNA for {g} ══════")
        out = extract_genre(g, top_n=args.top_n, model=args.model)
        data = json.loads(out.read_text())
        print(f"✓ Written: {out}")
        print(f"  prompt_preamble ({len(data.get('prompt_preamble', ''))} chars):")
        print(f"  {data.get('prompt_preamble', '')[:240]}{'...' if len(data.get('prompt_preamble', '')) > 240 else ''}")

    print("\n✓ All done. Run publisher next — viral_dna.py loader will "
          "pick up the new JSON automatically on next thumbnail generation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
