"""
thumbnail_compositor.py — Turn a raw Flux-generated base into a YouTube
thumbnail that can actually win clicks.

Raw Flux output is beautiful but missing the three CTR levers every
viral music-YouTube thumbnail uses:
  1. Big legible track title (the #1 CTR lever per every A/B study)
  2. Artist name (creator recognition)
  3. Channel mark (Holy Rave logo — repeat-click signal to the algo)

This module adds all three via pure PIL compositing. NO Flux /edit text
bleed — text is burned on AFTER image generation, so Kling morph start-
frame (the clean base) stays clean and interpolates smoothly.

Architecture:
  Flux base (kf_<id>_<digest>.jpg)         ← Kling morph input (CLEAN)
           │
           ▼  composite_thumbnail()
  Titled version (kf_<id>_<digest>_titled.jpg)   ← YouTube thumbnail upload

Both files live on disk. Publisher points result.hero_image.local_path
at the titled version, motion.generate_preroll_clip uses the clean base
via the keyframe cache (which is keyed on the original still_prompt
digest, not the titled suffix).

Brand locks applied (from CLAUDE.md):
  Core:          Dark #0a0a0a · Liturgical gold #d4af37 · Text #ffffff
  Earth accent:  Terracotta #b8532a · Indigo night #1a2a4a · Ochre #c8883a
  Serif:         Cormorant Garamond
  Sans:          Inter
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from content_engine.youtube_longform import config as cfg

logger = logging.getLogger(__name__)

# ─── Asset paths ────────────────────────────────────────────────────────────

_ASSETS      = Path(__file__).parent / "assets"
_FONTS_DIR   = _ASSETS / "fonts"
_LOGOS_DIR   = _ASSETS / "logos"

FONT_TITLE    = _FONTS_DIR / "CormorantGaramond-Bold.ttf"
FONT_SUBTITLE = _FONTS_DIR / "CormorantGaramond-Medium.ttf"
FONT_ARTIST   = _FONTS_DIR / "Inter-Medium.ttf"
LOGO_PATH     = _LOGOS_DIR / "holy-rave-logo.png"

# ─── Brand tokens ───────────────────────────────────────────────────────────

COLOR_GOLD          = (212, 175, 55, 255)   # liturgical gold  #d4af37
COLOR_GOLD_SOFT     = (200, 160, 50, 220)   # subtler gold for subtitles
COLOR_WHITE_90      = (255, 255, 255, 235)  # slightly off-white for artist
COLOR_BLACK_SHADOW  = (0,   0,   0,   180)  # drop-shadow color
COLOR_OCHRE         = (200, 136, 58,  255)  # ochre  #c8883a


@dataclass(frozen=True)
class CompositorStyle:
    """Tunable layout for the compositor — default values match Holy Rave spec."""
    title_position:      str   = "bottom_center"   # "bottom_center" | "top_center"
    title_font_px:       int   = 160               # on a 1920-wide canvas
    subtitle_font_px:    int   = 44
    artist_font_px:      int   = 34
    logo_height_px:      int   = 110
    logo_margin_px:      int   = 44
    title_margin_y_px:   int   = 120               # distance from edge to title baseline
    title_letter_spacing: float = 12                # extra px between title letters (feels more cinematic)
    # Gradient overlay for text legibility (bottom-anchored darkening).
    # Without this, bright thumbnails render text as unreadable noise.
    gradient_strength:   float = 0.55              # 0=off, 1=full black at anchor edge
    gradient_height_pct: float = 0.45              # gradient covers bottom 45% of frame
    # Horizontal gold divider between title and artist for polish.
    divider_enabled:     bool  = True
    divider_length_pct:  float = 0.08              # % of canvas width
    divider_thickness_px: int  = 2


DEFAULT_STYLE = CompositorStyle()


# ─── Logo alpha extraction ──────────────────────────────────────────────────

def _logo_with_alpha() -> Image.Image:
    """
    Load the Holy Rave logo and compute an alpha channel from luminance
    (the source PNG has a solid black background, not transparency).

    Black pixels → alpha 0, gold pixels → alpha 255, gradient between.
    We clamp the alpha ramp so any pixel brighter than ~5% luminance is
    fully opaque — otherwise the gold flame edges look smoky.
    """
    img = Image.open(LOGO_PATH).convert("RGB")
    r, g, b = img.split()

    # Luminance = 0.299 R + 0.587 G + 0.114 B  (Rec. 601)
    import numpy as np
    arr = np.array(img, dtype=np.float32)
    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    # Clamp: <=5 → 0, >=40 → 255, linear in between. Preserves gold edge detail.
    alpha = np.clip((lum - 5) / 35 * 255, 0, 255).astype(np.uint8)
    alpha_img = Image.fromarray(alpha, mode="L")

    out = Image.merge("RGBA", (r, g, b, alpha_img))
    return out


# ─── Drop shadow helper ─────────────────────────────────────────────────────

def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    canvas: Image.Image,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int, int],
    *,
    shadow_offset: int = 4,
    shadow_blur: int = 6,
    letter_spacing: float = 0,
) -> None:
    """
    Draw text with a blurred drop shadow for legibility on any background.
    Uses a secondary RGBA layer for the shadow pass so the blur stays
    confined to the shadow (not the crisp text).
    """
    # Shadow layer — same size as canvas, fully transparent except for
    # a softly-blurred dark copy of the text.
    shadow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)

    if letter_spacing > 0:
        _draw_text_tracked(
            shadow_draw, (xy[0] + shadow_offset, xy[1] + shadow_offset),
            text, font, COLOR_BLACK_SHADOW, letter_spacing,
        )
    else:
        shadow_draw.text(
            (xy[0] + shadow_offset, xy[1] + shadow_offset),
            text, font=font, fill=COLOR_BLACK_SHADOW,
        )

    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
    canvas.alpha_composite(shadow_layer)

    # Main text on top — crisp.
    if letter_spacing > 0:
        _draw_text_tracked(draw, xy, text, font, color, letter_spacing)
    else:
        draw.text(xy, text, font=font, fill=color)


def _draw_text_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int, int],
    letter_spacing_px: float,
) -> None:
    """
    Draw text character-by-character with extra horizontal spacing.
    PIL's default draw.text doesn't expose letter-spacing — this manual
    path does.
    """
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=color)
        advance = font.getlength(ch) + letter_spacing_px
        x += int(advance)


def _measure_tracked_text(
    font: ImageFont.FreeTypeFont,
    text: str,
    letter_spacing_px: float,
) -> int:
    total = 0.0
    for ch in text:
        total += font.getlength(ch) + letter_spacing_px
    # Remove trailing letter-spacing after the last character
    return int(total - letter_spacing_px) if text else 0


# ─── Gradient overlay ───────────────────────────────────────────────────────

def _apply_bottom_gradient(
    canvas: Image.Image,
    style: CompositorStyle,
) -> None:
    """
    Apply a transparent-to-dark gradient over the bottom portion so the
    text below is readable on any background. Mutates canvas in place.
    """
    if style.gradient_strength <= 0:
        return
    import numpy as np
    w, h = canvas.size
    g_height = int(h * style.gradient_height_pct)
    # 0 at top of gradient, 1 at bottom
    ramp = np.linspace(0, style.gradient_strength, g_height)
    alpha = (ramp * 255).astype(np.uint8)
    overlay_rgba = np.zeros((g_height, w, 4), dtype=np.uint8)
    overlay_rgba[..., 3] = alpha[:, None]  # alpha only, RGB stays 0 (black)
    overlay = Image.fromarray(overlay_rgba, mode="RGBA")
    canvas.alpha_composite(overlay, dest=(0, h - g_height))


# ─── Main public API ────────────────────────────────────────────────────────

def composite_thumbnail(
    base_image_path: Path,
    track_title: str,
    *,
    artist_name: str = "Robert-Jan Mastenbroek",
    subtitle: Optional[str] = None,           # e.g. scripture anchor "Joshua 6"
    output_path: Optional[Path] = None,
    style: CompositorStyle = DEFAULT_STYLE,
) -> Path:
    """
    Composite track title + artist + Holy Rave logo onto a base image.

    The base_image_path is the clean Flux output (used by Kling for
    morph interpolation). The titled version is saved separately and is
    what YouTube sees as the video thumbnail. Both files coexist.

    Returns the output path (titled JPEG). Defaults to
    `<base_stem>_titled.jpg` alongside the base.
    """
    if not FONT_TITLE.exists() or not FONT_ARTIST.exists() or not LOGO_PATH.exists():
        raise FileNotFoundError(
            f"Missing assets. Ensure {_ASSETS} contains fonts/ and logos/ "
            f"(run `setup_thumbnail_assets.sh` or check git-lfs)."
        )

    base = Image.open(base_image_path).convert("RGBA")
    canvas_w, canvas_h = base.size

    # Scale font sizes off the canvas width so the compositor works on
    # both 1920x1080 heroes and 1280x720 thumbnails with no manual tuning.
    scale = canvas_w / 1920.0
    title_px    = max(48, int(style.title_font_px * scale))
    subtitle_px = max(20, int(style.subtitle_font_px * scale))
    artist_px   = max(16, int(style.artist_font_px * scale))
    logo_h      = max(40, int(style.logo_height_px * scale))
    margin      = max(12, int(style.logo_margin_px * scale))
    title_margin_y = max(40, int(style.title_margin_y_px * scale))
    title_tracking = style.title_letter_spacing * scale

    title_font    = ImageFont.truetype(str(FONT_TITLE),    title_px)
    artist_font   = ImageFont.truetype(str(FONT_ARTIST),   artist_px)
    subtitle_font = ImageFont.truetype(str(FONT_SUBTITLE), subtitle_px)

    canvas = base.copy()

    # 1. Legibility gradient at the bottom.
    _apply_bottom_gradient(canvas, style)

    draw = ImageDraw.Draw(canvas)

    # 2. Main title — UPPERCASE, centered horizontally, bottom-positioned.
    title_text = track_title.upper()
    title_width = _measure_tracked_text(title_font, title_text, title_tracking)
    title_x = (canvas_w - title_width) // 2
    if style.title_position == "top_center":
        title_y = title_margin_y
    else:  # bottom_center
        # Anchor baseline roughly title_margin_y above the bottom.
        # Cormorant's glyph metrics put the baseline ~0.85 of font_px down
        # from the top y; account for that so the text sits visually at
        # the target margin.
        title_y = canvas_h - title_margin_y - int(title_px * 1.1)

    _draw_text_with_shadow(
        draw, canvas,
        (title_x, title_y), title_text,
        font=title_font, color=COLOR_GOLD,
        shadow_offset=max(2, int(4 * scale)),
        shadow_blur=max(4, int(8 * scale)),
        letter_spacing=title_tracking,
    )

    # 3. Artist subtitle under the title, smaller Inter Medium, muted white.
    artist_text = artist_name.upper()
    artist_width = int(artist_font.getlength(artist_text))
    artist_x = (canvas_w - artist_width) // 2
    artist_y = title_y + int(title_px * 1.25)   # below title glyph

    # 3b. Horizontal gold divider between title and artist.
    if style.divider_enabled:
        div_len = int(canvas_w * style.divider_length_pct)
        div_x1 = (canvas_w - div_len) // 2
        div_y  = title_y + int(title_px * 1.05)
        div_thick = max(1, int(style.divider_thickness_px * scale))
        draw.rectangle(
            (div_x1, div_y, div_x1 + div_len, div_y + div_thick),
            fill=COLOR_OCHRE,
        )
        # Nudge the artist text down a few more px so it clears the divider
        artist_y = div_y + div_thick + int(12 * scale)

    _draw_text_with_shadow(
        draw, canvas,
        (artist_x, artist_y), artist_text,
        font=artist_font, color=COLOR_WHITE_90,
        shadow_offset=max(1, int(2 * scale)),
        shadow_blur=max(2, int(4 * scale)),
    )

    # 4. Optional third line (scripture anchor / chapter marker).
    if subtitle:
        sub_width = int(subtitle_font.getlength(subtitle))
        sub_x = (canvas_w - sub_width) // 2
        sub_y = artist_y + int(artist_px * 1.4)
        _draw_text_with_shadow(
            draw, canvas,
            (sub_x, sub_y), subtitle,
            font=subtitle_font, color=COLOR_GOLD_SOFT,
            shadow_offset=max(1, int(2 * scale)),
            shadow_blur=max(2, int(3 * scale)),
        )

    # 5. Holy Rave logo in bottom-right corner, with alpha from luminance.
    logo = _logo_with_alpha()
    # Resize preserving aspect ratio to target height.
    aspect = logo.width / logo.height
    logo_w = int(logo_h * aspect)
    logo_resized = logo.resize((logo_w, logo_h), Image.LANCZOS)
    logo_pos = (
        canvas_w - logo_w - margin,
        canvas_h - logo_h - margin,
    )
    canvas.alpha_composite(logo_resized, dest=logo_pos)

    # Save as JPEG (flatten onto white — actually flatten onto the canvas
    # itself; RGBA→JPEG needs an RGB basis).
    if output_path is None:
        output_path = base_image_path.with_name(
            base_image_path.stem + "_titled.jpg"
        )
    rgb = Image.new("RGB", canvas.size, (0, 0, 0))
    rgb.paste(canvas, mask=canvas.split()[3])
    rgb.save(output_path, "JPEG", quality=92, optimize=True)
    logger.info(
        "Composited titled thumbnail | track=%r → %s (%dx%d, %d bytes)",
        track_title, output_path.name, canvas_w, canvas_h,
        output_path.stat().st_size,
    )
    return output_path


# ─── CLI / smoke test ───────────────────────────────────────────────────────

def _smoke_test(base: Path, track: str, out: Optional[Path] = None) -> Path:
    return composite_thumbnail(
        base_image_path=base,
        track_title=track,
        artist_name="Robert-Jan Mastenbroek",
        output_path=out,
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: thumbnail_compositor.py <base.jpg> <track title> [out.jpg]")
        sys.exit(1)
    logging.basicConfig(level=logging.INFO)
    out = _smoke_test(Path(sys.argv[1]), sys.argv[2],
                      Path(sys.argv[3]) if len(sys.argv) > 3 else None)
    print(out)
