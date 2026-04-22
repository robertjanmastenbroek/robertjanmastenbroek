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
    title_position:      str   = "safe_center"     # "safe_center" | "bottom_center" | "top_center"
    title_font_px:       int   = 180               # on a 1920-wide canvas (bumped for text-behind-subject impact)
    subtitle_font_px:    int   = 48
    artist_font_px:      int   = 38
    logo_height_px:      int   = 110
    logo_margin_px:      int   = 44
    # Distance from the BOTTOM edge to the baseline of the scripture anchor
    # (the lowest text line). Mobile safe boundary per 2026 research is
    # 12.5% bottom padding = 135px on 1080-tall canvas. 160px gives
    # headroom + clears the watch-history progress-bar clip zone (bottom 4%).
    text_block_bottom_margin_px: int = 160
    title_letter_spacing: float = 14                # extra px between title letters
    # Gradient overlay for text legibility — a soft darkening band where the
    # text sits so it reads on any background. Anchored to the text block,
    # not the bottom edge, so when text moves up the gradient follows.
    gradient_strength:   float = 0.55              # 0=off, 1=full black at anchor edge
    gradient_height_pct: float = 0.55              # gradient band height (% of canvas)
    # Horizontal gold divider between title and artist for polish.
    divider_enabled:     bool  = True
    divider_length_pct:  float = 0.10              # % of canvas width
    divider_thickness_px: int  = 2
    # Text-behind-subject effect — segment the foreground subject (person,
    # hero character) and re-paste it on top of the text layer so the text
    # appears to exist behind the subject in 3D space. THE single biggest
    # CTR lever in music-YouTube (Cafe de Anatolia / Sol Selectas / every
    # MrBeast thumbnail). Uses fal.ai rembg (~$0.002 per thumbnail).
    text_behind_subject: bool  = True


DEFAULT_STYLE = CompositorStyle()


# ─── Subject extraction via fal.ai rembg ────────────────────────────────────

_REMBG_ENDPOINT        = "fal-ai/imageutils/rembg"
_REMBG_COST_USD        = 0.005      # conservative upper-bound for spend_guard
_REMBG_CACHE_SUFFIX    = "_subject.png"


def _extract_subject(base_image_path: Path) -> Optional[Path]:
    """
    Run subject/background segmentation on a base Flux image and return a
    PNG with the subject cut out (transparent background).

    Uses fal.ai rembg (stateless, ~$0.002/call, returns PNG with alpha).
    Uploads the input to Cloudinary if it's not already public. Caches
    the output at `<stem>_subject.png` alongside the base so repeated
    composites on the same input are zero-cost.

    Returns None on any failure — caller falls back to pasting the text
    directly over the base (the old behavior). This is graceful: a rembg
    outage never fails the whole publish.
    """
    import hashlib
    from content_engine.youtube_longform import spend_guard
    from content_engine.youtube_longform.render import upload_image_for_render

    # Cache hit check — rembg is deterministic for same input.
    cache_path = base_image_path.with_name(
        base_image_path.stem + _REMBG_CACHE_SUFFIX
    )
    if cache_path.exists():
        logger.info("Subject extraction cached: %s", cache_path.name)
        return cache_path

    # Budget gate — skip the call (return None → fall back to flat compositing)
    # if we can't afford it rather than raising and killing the publish.
    try:
        spend_guard.check_budget(
            _REMBG_COST_USD,
            kind="rembg_subject_extract",
            note=base_image_path.name,
        )
    except Exception as e:
        logger.warning(
            "Spend cap hit — skipping subject extraction, falling back to "
            "flat composite (non-fatal): %s", e,
        )
        return None

    # Upload to Cloudinary to get a public URL for fal.ai.
    try:
        digest = hashlib.sha256(base_image_path.read_bytes()).hexdigest()[:10]
        input_url = upload_image_for_render(
            base_image_path,
            public_id=f"rembg_input_{digest}",
        )
    except Exception as e:
        logger.warning("Cloudinary upload failed for rembg input: %s", e)
        return None

    # Import fal_client lazily — same pattern as motion.py / image_gen.py
    try:
        from content_engine.youtube_longform.image_gen import _fal_client
    except Exception as e:
        logger.warning("Could not load fal_client: %s", e)
        return None

    try:
        client = _fal_client()
        result = client.subscribe(
            _REMBG_ENDPOINT,
            arguments={"image_url": input_url},
            with_logs=False,
        )
        image_entry = (result or {}).get("image") or {}
        output_url = image_entry.get("url")
        if not output_url:
            logger.warning("rembg response missing image.url: %s", result)
            return None
    except Exception as e:
        logger.warning("rembg subscribe failed (non-fatal): %s", e)
        return None

    # Download PNG with alpha to cache_path.
    try:
        import requests
        r = requests.get(output_url, timeout=60)
        r.raise_for_status()
        cache_path.write_bytes(r.content)
    except Exception as e:
        logger.warning("Could not download rembg result: %s", e)
        return None

    # Record actual spend only on success.
    spend_guard.record_spend(
        _REMBG_COST_USD,
        kind="rembg_subject_extract",
        note=base_image_path.name,
    )
    logger.info("Subject extracted → %s", cache_path.name)
    return cache_path


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
    Composite track title + artist + scripture anchor + Holy Rave logo
    onto a base image with the text-behind-subject effect.

    Layer order (bottom → top):
      1. Flux base image
      2. Soft darkening gradient anchored to the text-block band (for legibility)
      3. Title / ochre divider / artist / scripture anchor (all text)
      4. Holy Rave logo (corner mark)
      5. Subject-only cutout from fal.ai rembg — pasted on top so the text
         appears to sit BEHIND the subject, the single biggest CTR lever
         in music-YouTube thumbnails. Disabled if style.text_behind_subject
         is False, or if rembg fails (graceful fallback).

    Text vertical position respects the 2026 mobile-safe zone research —
    scripture-anchor bottom edge lands at ~85% of canvas height, clear of
    both the mobile viewport crop and the watch-history progress bar.

    The base_image_path is the clean Flux output (used by Kling for
    morph interpolation). The titled version is saved separately and
    only the titled version is what YouTube sees as the video thumbnail.
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
    bottom_margin = max(60, int(style.text_block_bottom_margin_px * scale))
    title_tracking = style.title_letter_spacing * scale

    title_font    = ImageFont.truetype(str(FONT_TITLE),    title_px)
    artist_font   = ImageFont.truetype(str(FONT_ARTIST),   artist_px)
    subtitle_font = ImageFont.truetype(str(FONT_SUBTITLE), subtitle_px)

    # ── Layout math: anchor BOTTOM of text block to bottom_margin from
    # the canvas bottom. That puts the entire block inside the 87.5%
    # mobile-safe boundary while keeping title LOW enough to cross the
    # subject's torso (enabling the text-behind-subject depth effect).
    divider_thickness   = max(1, int(style.divider_thickness_px * scale))
    gap_below_title     = int(title_px * 0.08)    # space from title to divider
    gap_below_divider   = int(12 * scale)         # space from divider to artist
    gap_below_artist    = int(artist_px * 0.45)   # space from artist to scripture

    # Going UP from the bottom margin:
    sub_bottom_y = canvas_h - bottom_margin
    if subtitle:
        sub_y = sub_bottom_y - subtitle_px
        artist_bottom_y = sub_y - gap_below_artist
    else:
        artist_bottom_y = sub_bottom_y

    artist_y = artist_bottom_y - artist_px

    if style.divider_enabled:
        div_y_bottom = artist_y - gap_below_divider
        div_y        = div_y_bottom - divider_thickness
        title_bottom_y = div_y - gap_below_title
    else:
        title_bottom_y = artist_y - gap_below_title

    # Flux title glyphs sit ~1.1x font_px tall
    title_y = title_bottom_y - int(title_px * 1.1)

    # ── Pre-compute text widths so we know the maximum horizontal footprint
    # before deciding text_center_x (subject-aware placement uses this).
    title_text   = track_title.upper()
    title_width  = _measure_tracked_text(title_font, title_text, title_tracking)
    artist_text  = artist_name.upper()
    artist_width = int(artist_font.getlength(artist_text))
    sub_width    = int(subtitle_font.getlength(subtitle)) if subtitle else 0
    max_text_width = max(title_width, artist_width, sub_width)

    # ── Extract subject FIRST (if enabled) so we can use its mask for
    # subject-aware text placement. The subject PNG is cached so re-running
    # this compositor on the same base is free.
    subject_png: Optional[Path] = None
    if style.text_behind_subject:
        subject_png = _extract_subject(base_image_path)

    # ── Subject-aware text center x: default to canvas center, but if we
    # have a subject mask, shift the text into the largest subject-free
    # horizontal zone. This guarantees the title stays READABLE even
    # when the behind-subject effect is applied (otherwise a big subject
    # like a warrior covers half the title).
    if subject_png is not None:
        text_center_x = _compute_subject_aware_text_center_x(
            subject_png, canvas_w, title_y, sub_bottom_y, max_text_width,
        )
    else:
        text_center_x = canvas_w // 2

    # ── Begin compositing on an RGBA working copy.
    canvas = base.copy()

    # 1. Legibility gradient — a soft darkening band anchored around the
    # TEXT BLOCK (not the canvas bottom), so moving text up moves the
    # gradient with it. The gradient fades from fully transparent at
    # its top to `gradient_strength` darkness at the bottom-block edge.
    _apply_text_block_gradient(canvas, style, title_y, sub_bottom_y)

    draw = ImageDraw.Draw(canvas)

    # 2. Main title — UPPERCASE, horizontally centered on text_center_x.
    title_x = text_center_x - title_width // 2

    _draw_text_with_shadow(
        draw, canvas,
        (title_x, title_y), title_text,
        font=title_font, color=COLOR_GOLD,
        shadow_offset=max(2, int(5 * scale)),
        shadow_blur=max(4, int(10 * scale)),
        letter_spacing=title_tracking,
    )

    # 3. Ochre horizontal divider (centered on text_center_x).
    if style.divider_enabled:
        div_len = int(canvas_w * style.divider_length_pct)
        div_x1  = text_center_x - div_len // 2
        draw.rectangle(
            (div_x1, div_y, div_x1 + div_len, div_y + divider_thickness),
            fill=COLOR_OCHRE,
        )

    # 4. Artist line below divider (centered on text_center_x).
    artist_x = text_center_x - artist_width // 2
    _draw_text_with_shadow(
        draw, canvas,
        (artist_x, artist_y), artist_text,
        font=artist_font, color=COLOR_WHITE_90,
        shadow_offset=max(1, int(3 * scale)),
        shadow_blur=max(2, int(5 * scale)),
    )

    # 5. Scripture anchor (subtle salt — users who know the Word recognize it).
    if subtitle:
        sub_x = text_center_x - sub_width // 2
        _draw_text_with_shadow(
            draw, canvas,
            (sub_x, sub_y), subtitle,
            font=subtitle_font, color=COLOR_GOLD_SOFT,
            shadow_offset=max(1, int(2 * scale)),
            shadow_blur=max(2, int(4 * scale)),
        )

    # 6. Holy Rave logo in bottom-right corner, alpha from luminance.
    logo = _logo_with_alpha()
    aspect = logo.width / logo.height
    logo_w = int(logo_h * aspect)
    logo_resized = logo.resize((logo_w, logo_h), Image.LANCZOS)
    logo_pos = (
        canvas_w - logo_w - margin,
        canvas_h - logo_h - margin,
    )
    canvas.alpha_composite(logo_resized, dest=logo_pos)

    # 7. Text-behind-subject — paste the subject cut-out on top of the
    # text so the text appears to sit behind the subject in 3D space.
    # We already extracted subject_png upstream for subject-aware text
    # placement, so this is a free (cached) re-use of the same PNG.
    if subject_png is not None:
        try:
            subj_img = Image.open(subject_png).convert("RGBA")
            if subj_img.size != canvas.size:
                subj_img = subj_img.resize(canvas.size, Image.LANCZOS)
            canvas.alpha_composite(subj_img, dest=(0, 0))
            logger.info("Applied text-behind-subject layer")
        except Exception as e:
            logger.warning(
                "Could not overlay subject PNG (non-fatal): %s", e,
            )

    # ── Flatten RGBA → RGB and save as JPEG.
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


def _compute_subject_aware_text_center_x(
    subject_png_path: Path,
    canvas_w: int,
    text_top_y: int,
    text_bottom_y: int,
    max_text_width: int,
    overlap_fraction: float = 0.08,
) -> int:
    """
    Look at the subject alpha mask within the vertical text band and return
    the ideal horizontal center-x for the text block.

    Heuristic:
      1. Measure subject horizontal density in the text-band rows.
      2. Find the widest contiguous "subject-free" run of columns
         (columns where the subject alpha covers <15% of the band height).
      3. Center the text within that free run.
      4. Nudge the text CENTER toward the subject by `overlap_fraction`
         × text_width so the edge letters tuck behind the subject's edge
         (that's the depth-effect without clobbering readability).

    Returns an x-coordinate usable as the text's horizontal center. If
    no useful free run exists, falls back to canvas center.
    """
    import numpy as np
    try:
        subj = np.array(Image.open(subject_png_path).convert("RGBA"))
    except Exception as e:
        logger.warning("Could not read subject mask for layout: %s", e)
        return canvas_w // 2

    if subj.shape[0] < text_bottom_y or subj.shape[1] < canvas_w:
        return canvas_w // 2

    # Alpha within the text band
    band = subj[text_top_y:text_bottom_y, :, 3]
    if band.size == 0:
        return canvas_w // 2

    # Fraction of each column that's opaque subject within the text band.
    col_coverage = (band > 128).mean(axis=0)
    # A column is "free" if <15% of the text band's rows are subject.
    FREE_THRESHOLD = 0.15
    free_cols = col_coverage < FREE_THRESHOLD

    # Find the widest contiguous run of free columns.
    best_start = 0
    best_end = 0
    run_start = None
    for i, is_free in enumerate(free_cols):
        if is_free:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                if (i - run_start) > (best_end - best_start):
                    best_start, best_end = run_start, i
                run_start = None
    if run_start is not None:   # trailing run
        if (len(free_cols) - run_start) > (best_end - best_start):
            best_start, best_end = run_start, len(free_cols)

    free_width = best_end - best_start
    if free_width < max_text_width * 0.7:
        # Not enough free space to shift — keep text centered as a safe fallback.
        logger.info(
            "Subject-aware layout | no large free zone (widest=%dpx < 70%% of title) — "
            "using canvas center", free_width,
        )
        return canvas_w // 2

    # Center of the largest free zone — this is where text would sit with zero overlap.
    free_center_x = (best_start + best_end) // 2

    # Pull the text center toward the subject for partial overlap (the depth effect).
    # Pull direction is toward canvas center if subject is off-center.
    subject_center_x = int((col_coverage * np.arange(canvas_w)).sum()
                           / max(col_coverage.sum(), 1e-9))
    pull_direction = 1 if subject_center_x < free_center_x else -1
    overlap_px = int(max_text_width * overlap_fraction)
    target_center_x = free_center_x - pull_direction * overlap_px

    # Clamp so the text still fits in the canvas horizontally
    half_text = max_text_width // 2
    target_center_x = max(half_text + 20, min(canvas_w - half_text - 20, target_center_x))

    logger.info(
        "Subject-aware layout | subject_cx=%d free_zone=[%d..%d] free_cx=%d → text_cx=%d",
        subject_center_x, best_start, best_end, free_center_x, target_center_x,
    )
    return target_center_x


def _apply_text_block_gradient(
    canvas: Image.Image,
    style: CompositorStyle,
    text_top_y: int,
    text_bottom_y: int,
) -> None:
    """
    Apply a soft top-transparent → bottom-dark gradient anchored around
    the text block so the text reads on any background. Unlike the old
    bottom-anchored gradient, this one follows the text UP when the text
    moves (safe-zone layout).
    """
    if style.gradient_strength <= 0:
        return
    import numpy as np
    w, h = canvas.size
    # Gradient spans from (text_top_y - pad) to (text_bottom_y + pad) to
    # soften edges. Clamp to canvas bounds.
    pad = int(h * 0.08)
    g_top    = max(0, text_top_y - pad)
    g_bottom = min(h, text_bottom_y + pad)
    g_height = g_bottom - g_top
    if g_height <= 0:
        return

    # Bell-shaped alpha: 0 at top edge, peaks at mid-block, 0.7×strength at bottom.
    ramp_up   = np.linspace(0, style.gradient_strength, g_height // 2)
    ramp_down = np.linspace(style.gradient_strength, style.gradient_strength * 0.55,
                            g_height - g_height // 2)
    ramp = np.concatenate([ramp_up, ramp_down])
    alpha = (ramp * 255).astype(np.uint8)

    overlay_rgba = np.zeros((g_height, w, 4), dtype=np.uint8)
    overlay_rgba[..., 3] = alpha[:, None]  # alpha only, RGB stays 0 (black)
    overlay = Image.fromarray(overlay_rgba, mode="RGBA")
    canvas.alpha_composite(overlay, dest=(0, g_top))


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
