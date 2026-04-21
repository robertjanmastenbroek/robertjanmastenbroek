#!/usr/bin/env python3
"""
generate_brand_assets.py — Generate Holy Rave logo variants + channel banner via fal.ai Flux 2.

Produces all candidate logo options (A: Shin, C: Shofar/Hexagon, D: Abstract Menorah)
and two banner variants (Wadi Rum dusk, Petra treasury dusk). You pick one of each,
upload to YouTube Studio.

Prerequisites:
    FAL_KEY set in .env (see .env.example.youtube_longform)
    pip install -r content_engine/youtube_longform/requirements.txt

Usage:
    python3 scripts/generate_brand_assets.py                  # Generate all options
    python3 scripts/generate_brand_assets.py --only logo      # Just logos
    python3 scripts/generate_brand_assets.py --only banner    # Just banners
    python3 scripts/generate_brand_assets.py --option A       # One specific logo option

Output:
    content/images/brand/logo_A_shin_<timestamp>.jpg
    content/images/brand/logo_C_shofar_<timestamp>.jpg
    content/images/brand/logo_D_menorah_<timestamp>.jpg
    content/images/brand/banner_wadi_rum_<timestamp>.jpg
    content/images/brand/banner_petra_<timestamp>.jpg
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from content_engine.youtube_longform import config as cfg
from content_engine.youtube_longform.image_gen import _download, _fal_client


BRAND_DIR = cfg.PROJECT_DIR / "content" / "images" / "brand"

logger = logging.getLogger("brand_assets")


# ─── Logo prompts ────────────────────────────────────────────────────────────
# Profile picture crops to a circle in YouTube, so composition must be
# centered with generous margin. Target source size: 1024x1024.

LOGO_PROMPTS = {
    "A_shin": {
        "description": "The Hebrew letter Shin (ש) — biblical opener of Shema Yisrael",
        "prompt": (
            "minimalist gold logo mark of the Hebrew letter Shin (ש) rendered as "
            "three upward-reaching flames of liturgical gold #d4af37 on pure "
            "obsidian black #0a0a0a background, subtle metallic shimmer, "
            "subtle 35mm film grain, centered composition with wide margin, "
            "vector-clean yet organic, editorial typography sculpture, "
            "high contrast, mysterious, ancient, no text, no letters, "
            "no other characters, sacred seal aesthetic"
        ),
        "negative": (
            "text, typography, words, latin letters, any letters other than the Shin, "
            "watermark, neon glow, purple, teal, cross, Christian cross, "
            "cyberpunk, plastic 3D render, New Age mandala, yantra, flower of life, "
            "OM symbol, Buddha, multiple colors, busy background"
        ),
    },
    "C_shofar_hexagon": {
        "description": "Shofar ram's horn inside a hexagonal frame — Jericho reference baked in",
        "prompt": (
            "minimalist gold logo mark, a single elegant curled ram's-horn (shofar) "
            "silhouette inside a thin hexagonal frame, liturgical gold #d4af37 on "
            "pure obsidian black #0a0a0a, etched line weight, ancient seal "
            "aesthetic, Temple-courtyard geometric framing, centered composition, "
            "circular crop suitable for profile picture, high contrast, mysterious"
        ),
        "negative": (
            "text, typography, watermark, neon glow, purple, teal, cross, Christian cross, "
            "New Age mandala, yantra, flower of life, cyberpunk, "
            "plastic 3D render, multiple colors, busy background, realistic shofar "
            "photograph, modern trumpet"
        ),
    },
    "D_menorah_abstract": {
        "description": "Abstract 7-line menorah — Exodus 25 Tabernacle lampstand, minimalist",
        "prompt": (
            "minimalist gold logo mark, seven thin vertical lines of liturgical "
            "gold #d4af37 rising from a single base point, abstracted to pure "
            "geometry (reads as a menorah, flame, or bar-chart depending on viewer), "
            "on pure obsidian black #0a0a0a, etched line weight, sacred seal "
            "aesthetic, centered composition, circular crop suitable for profile "
            "picture, high contrast, very minimal"
        ),
        "negative": (
            "text, typography, watermark, neon glow, purple, teal, cross, "
            "New Age mandala, yantra, flower of life, cyberpunk, Hanukkah decorations, "
            "realistic menorah photograph, candles, flames with orange or red, "
            "plastic 3D render, multiple colors"
        ),
    },
}

# ─── Banner prompts ──────────────────────────────────────────────────────────
# YouTube banner safe-zone is 1235x338 (for all devices). Full template is
# 2560x1440 but only the central horizontal band is guaranteed to show.
# We generate at 2560x1440 and let fal upscale/downscale as needed.

BANNER_PROMPTS = {
    "wadi_rum_dusk": {
        "description": "Wadi Rum desert at dusk — terracotta monoliths, first star, shaft of gold",
        "prompt": (
            "cinematic ultra-wide landscape of Wadi Rum at dusk, terracotta sandstone "
            "monoliths rising from red desert sand, single shaft of liturgical gold "
            "#d4af37 light piercing through a rock fissure in the far distance, "
            "indigo night #1a2a4a sky with the first star just appearing, fine "
            "dust suspended in the air, 35mm film grain, shot on ARRI Alexa 65, "
            "color palette obsidian black #0a0a0a + liturgical gold #d4af37 + "
            "terracotta #b8532a + indigo night #1a2a4a + ochre #c8883a, "
            "cathedral-scale awe, centered horizon composition with generous sky "
            "headroom for banner crop, wide aspect ratio"
        ),
        "negative": (
            "text, typography, logos, watermarks, people in foreground, camels, "
            "tents, modern buildings, power lines, roads, purple gradients, teal, "
            "neon, stock photo lighting, plastic 3D render, AI sheen, "
            "vacation photography, travel magazine feel"
        ),
        "aspect_width":  2560,
        "aspect_height": 1440,
    },
    "petra_treasury_dusk": {
        "description": "Petra Treasury carved facade at blue hour — ochre stone + gold light",
        "prompt": (
            "cinematic ultra-wide horizontal frame of the Petra Treasury at blue "
            "hour, rose-red Nabataean carved sandstone facade catching the last "
            "shaft of liturgical gold #d4af37 sunlight on the upper columns, deep "
            "indigo night #1a2a4a shadow in the foreground canyon siq, fine dust "
            "and incense smoke suspended, 35mm film grain, shot on ARRI Alexa 65, "
            "color palette obsidian black #0a0a0a + gold #d4af37 + terracotta "
            "#b8532a + ochre #c8883a + indigo night #1a2a4a, cathedral-scale awe, "
            "ancient sacred architecture, wide cinematic aspect ratio"
        ),
        "negative": (
            "text, typography, logos, watermarks, tourists, modern signage, crowds, "
            "purple gradients, teal, neon, sunny noon lighting, tourist photograph, "
            "vacation postcard, stock photo feel, travel magazine, plastic 3D, AI sheen"
        ),
        "aspect_width":  2560,
        "aspect_height": 1440,
    },
}


# ─── Generation helpers ──────────────────────────────────────────────────────

def _generate(prompt: str, negative: str, width: int, height: int) -> str:
    client = _fal_client()
    endpoint = cfg.FAL_FLUX_2_LORA_EP if cfg.FAL_BRAND_LORA_URL else cfg.FAL_FLUX_2_PRO_EP

    arguments = {
        "prompt":               prompt,
        "negative_prompt":      negative,
        "image_size":           {"width": width, "height": height},
        "num_inference_steps":  28,
        "guidance_scale":       3.5,
    }
    if cfg.FAL_BRAND_LORA_URL:
        arguments["loras"] = [{
            "path":  cfg.FAL_BRAND_LORA_URL,
            "scale": cfg.FAL_BRAND_LORA_SCALE,
        }]

    logger.info("fal.ai generate | endpoint=%s | %dx%d", endpoint, width, height)
    result = client.subscribe(endpoint, arguments=arguments, with_logs=False)
    images = result.get("images") if isinstance(result, dict) else None
    if not images:
        raise RuntimeError(f"fal.ai returned no images: {result!r}")
    return images[0]["url"]


def _emit(
    key: str,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    timestamp: str,
) -> Path:
    dest = BRAND_DIR / f"{key}_{timestamp}.jpg"
    if dest.exists():
        logger.info("Already exists, skipping: %s", dest.name)
        return dest
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    url = _generate(prompt, negative, width, height)
    _download(url, dest)
    logger.info("Generated %s in %.1fs → %s", key, time.time() - t0, dest.name)
    return dest


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=["logo", "banner"],
        help="Generate only one category",
    )
    parser.add_argument(
        "--option",
        choices=list(LOGO_PROMPTS.keys()),
        help="Generate only one specific logo option (e.g. A_shin)",
    )
    args = parser.parse_args()

    if not cfg.FAL_KEY:
        print("✗ FAL_KEY not set in environment. See .env.example.youtube_longform", file=sys.stderr)
        return 1

    ts = time.strftime("%Y%m%d_%H%M%S")
    generated: list[Path] = []

    if args.only != "banner":
        logo_keys = [args.option] if args.option else list(LOGO_PROMPTS.keys())
        for key in logo_keys:
            spec = LOGO_PROMPTS[key]
            print(f"\n— Logo {key}: {spec['description']}")
            path = _emit(f"logo_{key}", spec["prompt"], spec["negative"], 1024, 1024, ts)
            generated.append(path)

    if args.only != "logo":
        for key, spec in BANNER_PROMPTS.items():
            print(f"\n— Banner {key}: {spec['description']}")
            path = _emit(
                f"banner_{key}",
                spec["prompt"],
                spec["negative"],
                spec["aspect_width"],
                spec["aspect_height"],
                ts,
            )
            generated.append(path)

    print("\n" + "=" * 70)
    print(f"Generated {len(generated)} asset(s):")
    for p in generated:
        print(f"  {p}")
    print("\nNext step: open each, pick your favorite, upload to YouTube Studio.")
    print(f"  Logo      → Customization → Branding → Picture")
    print(f"  Banner    → Customization → Branding → Banner image")
    return 0


if __name__ == "__main__":
    sys.exit(main())
