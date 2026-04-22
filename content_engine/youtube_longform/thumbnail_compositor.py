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
from typing import Literal, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from content_engine.youtube_longform import config as cfg


# ─── Composition hints — per-track layout intent ─────────────────────────────
#
# Drives TWO things:
#   (1) The placement strategy when the subject is about-as-expected —
#       helps the compositor choose "opposite_side" vs "across_body" vs
#       "lower_third" without solely relying on the rembg mask.
#   (2) A hint for the prompt-builder to vary subject framing across the
#       catalog so we don't ship 12 identical left-third portraits.
#
# The compositor still has the final say — if Flux produced a HUGE subject
# but the hint says "subject_center_portrait", the hint is overridden to
# "opposite_side" so the title stays readable. Hints are preferences, not
# contracts.
CompositionHint = Literal[
    "subject_left",              # subject LEFT third, scene fills right — text on right
    "subject_right",             # subject RIGHT third, scene fills left — text on left
    "subject_center_portrait",   # tight center portrait, text crosses body (MrBeast style)
    "subject_center_wide",       # wide landscape / small subject center — text lower-third
    "auto",                      # let the compositor decide from the rembg mask alone
]

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
    # Auto-sizing: title font starts at title_font_px and is auto-shrunk
    # by 10px per step until the text fits `max_text_width_fraction` of
    # the horizontal allotment for the chosen strategy. Min floor prevents
    # unreadable-tiny fallback — tracks with extreme-long titles get
    # 2-line balanced wrap instead.
    title_font_px_min:   int   = 110
    # 2-line wrap policy: if even the min single-line font wouldn't fit,
    # split the title into two lines at the word break that minimizes
    # max(line1_width, line2_width) — i.e. balanced lines, not left-skewed.
    max_title_lines:     int   = 2


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
    composition_hint: CompositionHint = "auto",
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
    base_title_px   = max(48, int(style.title_font_px * scale))
    min_title_px    = max(40, int(style.title_font_px_min * scale))
    subtitle_px     = max(20, int(style.subtitle_font_px * scale))
    artist_px       = max(16, int(style.artist_font_px * scale))
    logo_h          = max(40, int(style.logo_height_px * scale))
    margin          = max(12, int(style.logo_margin_px * scale))
    bottom_margin   = max(60, int(style.text_block_bottom_margin_px * scale))
    title_tracking  = style.title_letter_spacing * scale

    artist_font     = ImageFont.truetype(str(FONT_ARTIST),   artist_px)
    subtitle_font   = ImageFont.truetype(str(FONT_SUBTITLE), subtitle_px)
    divider_thickness = max(1, int(style.divider_thickness_px * scale))

    # ── Extract subject FIRST so placement strategy + max-text-width
    # calculations can react to the real subject footprint.
    subject_png: Optional[Path] = None
    if style.text_behind_subject:
        subject_png = _extract_subject(base_image_path)

    subject_width_ratio, subject_cx_ratio, subject_density = (
        _subject_bbox_ratio(subject_png, canvas_w) if subject_png else (0.0, 0.5, 0.0)
    )
    strategy = _decide_placement_strategy(
        composition_hint, subject_width_ratio, subject_cx_ratio,
    )
    logger.info(
        "Composition | hint=%s | subject_width=%.2f cx=%.2f density=%.2f → strategy=%s",
        composition_hint, subject_width_ratio, subject_cx_ratio,
        subject_density, strategy,
    )

    # ── Horizontal allotment for the text block (max_text_width) depends on
    # the chosen strategy. opposite_side = one half-canvas (since the
    # subject occupies the other half); across_body / lower_third can use
    # most of the canvas width with a safety margin.
    side_pad = int(40 * scale)
    if strategy == "opposite_side":
        # Subject occupies one half; text gets the other half minus pad.
        max_text_width = int(canvas_w * 0.5) - side_pad * 2
    else:
        # across_body or lower_third — text can span most of the canvas.
        max_text_width = canvas_w - side_pad * 2

    # ── Resolve the title: single line if possible, balanced 2-line
    # wrap otherwise, with auto font-size shrinking.
    rendered_title = _resolve_title_rendering(
        track_title,
        max_width=max_text_width,
        base_font_px=base_title_px,
        min_font_px=min_title_px,
        max_lines=style.max_title_lines,
        tracking_px_per_em=title_tracking,
    )

    # Support-line widths (artist + scripture) don't need auto-sizing —
    # they're deterministically shorter than any title we'll see.
    artist_text  = artist_name.upper()
    artist_width = int(artist_font.getlength(artist_text))
    sub_width    = int(subtitle_font.getlength(subtitle)) if subtitle else 0

    # ── Layout math: anchor BOTTOM of text block to bottom_margin from
    # the canvas bottom (mobile-safe zone).
    gap_below_title     = int(rendered_title.font_px * 0.10)
    gap_below_divider   = int(14 * scale)
    gap_below_artist    = int(artist_px * 0.45)

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

    # Multi-line title: top of block = bottom - block_height
    title_top_y = title_bottom_y - rendered_title.block_height

    # ── Horizontal center (text_center_x) depends on strategy.
    text_center_x = canvas_w // 2
    max_line_width = max(rendered_title.line_widths)
    if strategy == "opposite_side" and subject_png is not None:
        # Snap text center to the OPPOSITE half from the subject.
        # Subject cx < 0.5 → text goes to right half; > 0.5 → left half.
        if subject_cx_ratio < 0.5:
            # Subject LEFT → text CENTER on right-half midpoint
            text_center_x = int(canvas_w * 0.72)
        else:
            text_center_x = int(canvas_w * 0.28)
        # Fine-tune with the subject-aware algorithm for minor overlap
        fine = _compute_subject_aware_text_center_x(
            subject_png, canvas_w, title_top_y, sub_bottom_y, max_line_width,
        )
        # If the fine-tune landed anywhere reasonable, use it; otherwise keep the snap.
        if fine and abs(fine - text_center_x) < int(canvas_w * 0.25):
            text_center_x = fine
    elif strategy == "lower_third":
        # Text always canvas-centered when below subject.
        text_center_x = canvas_w // 2
    else:  # across_body
        # Canvas-centered, allowing the subject to cross the text naturally.
        text_center_x = canvas_w // 2

    # Clamp so text stays in-canvas
    half_w = max_line_width // 2
    text_center_x = max(half_w + side_pad,
                        min(canvas_w - half_w - side_pad, text_center_x))

    # ── Begin compositing on an RGBA working copy.
    canvas = base.copy()

    # 1. Legibility gradient anchored around the text block.
    _apply_text_block_gradient(canvas, style, title_top_y, sub_bottom_y)

    draw = ImageDraw.Draw(canvas)

    # 2. Multi-line title — each line centered on text_center_x, stacked
    #    vertically with line_height spacing.
    title_y_cursor = title_top_y
    for line_text, line_width in zip(rendered_title.lines, rendered_title.line_widths):
        line_x = text_center_x - line_width // 2
        _draw_text_with_shadow(
            draw, canvas,
            (line_x, title_y_cursor), line_text,
            font=rendered_title.font, color=COLOR_GOLD,
            shadow_offset=max(2, int(5 * scale)),
            shadow_blur=max(4, int(10 * scale)),
            letter_spacing=rendered_title.tracking_px,
        )
        title_y_cursor += rendered_title.line_height

    # 3. Ochre horizontal divider.
    if style.divider_enabled:
        div_len = int(canvas_w * style.divider_length_pct)
        div_x1  = text_center_x - div_len // 2
        draw.rectangle(
            (div_x1, div_y, div_x1 + div_len, div_y + divider_thickness),
            fill=COLOR_OCHRE,
        )

    # 4. Artist line below divider.
    artist_x = text_center_x - artist_width // 2
    _draw_text_with_shadow(
        draw, canvas,
        (artist_x, artist_y), artist_text,
        font=artist_font, color=COLOR_WHITE_90,
        shadow_offset=max(1, int(3 * scale)),
        shadow_blur=max(2, int(5 * scale)),
    )

    # 5. Scripture anchor.
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
    #
    # SAFETY GUARD: for some subjects (full-body centered portraits,
    # dramatic wingspans, large crowds) the subject mask would cover
    # nearly all of the text pixels — making the title unreadable. In
    # those cases we SKIP pasting the subject layer and ship with text
    # fully visible on top. Losing the depth effect is acceptable; an
    # invisible title is not.
    if subject_png is not None:
        try:
            # Estimate what fraction of title pixels would be hidden
            # by the subject layer. If >60%, skip the behind-subject
            # effect entirely.
            title_line_height = rendered_title.line_height
            title_region = (
                text_center_x - max_line_width // 2,
                title_top_y,
                text_center_x + max_line_width // 2,
                title_top_y + rendered_title.block_height,
            )
            coverage = _estimate_subject_coverage(
                subject_png, canvas_w, canvas_h, title_region,
            )
            if coverage > 0.60:
                logger.info(
                    "Subject-layer SKIPPED — would cover %.0f%% of title "
                    "(> 60%% threshold). Title renders on top for readability.",
                    coverage * 100,
                )
            else:
                subj_img = Image.open(subject_png).convert("RGBA")
                if subj_img.size != canvas.size:
                    subj_img = subj_img.resize(canvas.size, Image.LANCZOS)
                canvas.alpha_composite(subj_img, dest=(0, 0))
                logger.info(
                    "Applied text-behind-subject layer (covers %.0f%% of title)",
                    coverage * 100,
                )
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


def _estimate_subject_coverage(
    subject_png_path: Path,
    canvas_w: int,
    canvas_h: int,
    region: tuple[int, int, int, int],
) -> float:
    """
    Estimate the fraction (0.0–1.0) of pixels inside `region` (x1, y1, x2, y2)
    that are opaque in the subject alpha mask. Used as the "would the subject
    layer hide the title?" guard — if >0.60, caller skips the behind-subject
    paste so the title stays visible.

    Rescales the subject PNG to canvas dimensions on the fly so coverage math
    is canvas-relative.
    """
    import numpy as np
    x1, y1, x2, y2 = region
    # Clamp to canvas
    x1 = max(0, min(canvas_w, x1))
    x2 = max(0, min(canvas_w, x2))
    y1 = max(0, min(canvas_h, y1))
    y2 = max(0, min(canvas_h, y2))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    try:
        subj = Image.open(subject_png_path).convert("RGBA")
        if subj.size != (canvas_w, canvas_h):
            subj = subj.resize((canvas_w, canvas_h), Image.LANCZOS)
        alpha = np.array(subj)[..., 3]
        region_alpha = alpha[y1:y2, x1:x2]
        if region_alpha.size == 0:
            return 0.0
        opaque_ratio = float((region_alpha > 128).mean())
        return opaque_ratio
    except Exception as e:
        logger.warning("coverage estimate failed: %s", e)
        return 0.0


def _subject_bbox_ratio(subject_png_path: Path, canvas_w: int) -> tuple[float, float, float]:
    """
    Measure the subject's horizontal footprint in the alpha mask. Returns
    (subject_width_ratio, subject_center_x_ratio, subject_density_in_bbox).

    - `subject_width_ratio`: width of the bounding box divided by canvas_w.
      0.0 = no subject, 1.0 = subject spans full width.
    - `subject_center_x_ratio`: horizontal center of mass (0.0 = far left,
      1.0 = far right).
    - `subject_density_in_bbox`: how much of the bbox is actually opaque
      subject (vs background holes). Low density = sparse subject (e.g.
      wings spread), high density = solid subject (portrait).

    Falls back to (0.0, 0.5, 0.0) on any error — caller treats that as
    "no subject detected, use canvas-center behavior".
    """
    import numpy as np
    try:
        subj = np.array(Image.open(subject_png_path).convert("RGBA"))
    except Exception as e:
        logger.warning("Could not read subject mask for bbox: %s", e)
        return (0.0, 0.5, 0.0)

    if subj.shape[1] != canvas_w:
        # Subject PNG is at a different resolution — normalize for ratio math.
        pass  # our ratio math uses its own width internally

    alpha = subj[..., 3]
    opaque = alpha > 128
    if not opaque.any():
        return (0.0, 0.5, 0.0)

    # Columns that have ANY subject pixels
    cols_with_subject = opaque.any(axis=0)
    idxs = np.where(cols_with_subject)[0]
    if len(idxs) == 0:
        return (0.0, 0.5, 0.0)
    bbox_left = int(idxs[0])
    bbox_right = int(idxs[-1])
    bbox_width = bbox_right - bbox_left + 1
    width_ratio = bbox_width / alpha.shape[1]

    # Horizontal center of mass (weighted by opaque pixel count per column)
    col_counts = opaque.sum(axis=0).astype(np.float64)
    total = col_counts.sum()
    if total <= 0:
        return (0.0, 0.5, 0.0)
    cx = float((col_counts * np.arange(alpha.shape[1])).sum() / total)
    cx_ratio = cx / alpha.shape[1]

    # Density inside the bbox (opaque pixels / bbox area)
    bbox_area = bbox_width * alpha.shape[0]
    density = float(col_counts.sum() / max(bbox_area, 1))

    return (width_ratio, cx_ratio, density)


# ─── Placement strategy ─────────────────────────────────────────────────────

PlacementStrategy = Literal["opposite_side", "across_body", "lower_third"]


def _decide_placement_strategy(
    hint: CompositionHint,
    subject_width_ratio: float,
    subject_cx_ratio: float,
) -> PlacementStrategy:
    """
    Combine the composition hint (intent) with the real subject bbox
    (observation) to pick a placement strategy for the text block.

    Rules:
      * HUGE subject (>55% canvas width) → opposite_side, regardless of hint.
        Any other strategy would cover most of the title.
      * subject_center_wide hint AND sparse/small subject → lower_third.
      * subject_center_portrait hint AND sensible size → across_body.
      * subject_left / subject_right hints → opposite_side matched to subject
        position (i.e. text goes in the empty half).
      * auto hint → infer from subject position + size.

    The compositor uses the returned strategy to pick the text horizontal
    center and the max text width allotment.
    """
    # Hard override: if subject is dominant, always opposite_side.
    if subject_width_ratio > 0.55:
        return "opposite_side"

    # Hard override: if subject is very small, always lower_third (text goes
    # below subject or spans canvas since there's plenty of room).
    if subject_width_ratio < 0.22:
        return "lower_third"

    if hint == "subject_left":
        return "opposite_side"
    if hint == "subject_right":
        return "opposite_side"
    if hint == "subject_center_portrait":
        return "across_body"
    if hint == "subject_center_wide":
        return "lower_third"

    # hint == "auto" — infer
    if subject_width_ratio > 0.40:
        # Medium-large subject — probably a portrait. across_body gives depth.
        return "across_body"
    if abs(subject_cx_ratio - 0.5) > 0.18:
        # Off-center subject — opposite_side keeps text clear
        return "opposite_side"
    return "across_body"


# ─── Title auto-sizing + balanced 2-line wrap ────────────────────────────────

@dataclass(frozen=True)
class RenderedTitle:
    """Result of _resolve_title_rendering — title ready to draw."""
    lines:           list[str]           # 1 or 2 strings, already uppercase
    font:            ImageFont.FreeTypeFont
    font_px:         int                  # resolved font size
    line_widths:     list[int]            # tracked widths per line
    tracking_px:     float                # per-char extra spacing
    line_height:     int                  # baseline-to-baseline height in px
    block_height:    int                  # total block height for all lines


def _resolve_title_rendering(
    title: str,
    max_width: int,
    base_font_px: int,
    min_font_px: int,
    max_lines: int,
    tracking_px_per_em: float,
) -> RenderedTitle:
    """
    Produce a single-line or balanced multi-line rendering that fits inside
    max_width at the largest feasible font size.

    Priority:
      1. Single line at base_font_px if it fits.
      2. Two balanced lines at base_font_px (split at the word break that
         minimizes max(line1_width, line2_width) — e.g. "HOW GOOD AND
         PLEASANT" becomes "HOW GOOD" / "AND PLEASANT", not "HOW" / "GOOD
         AND PLEASANT").
      3. Shrink the font and retry 1 → 2 until something fits or we hit
         min_font_px.
      4. If even 2-line at min_font_px overflows, return it clipped — the
         compositor will warn and ship it anyway; extremely long titles
         probably needed manual config regardless.
    """
    title_upper = title.strip().upper()
    words = title_upper.split()

    def _build_line(text: str, font_px: int) -> tuple[int, float, ImageFont.FreeTypeFont]:
        """Return (width, tracking_px, font) for one line at a given size."""
        font = ImageFont.truetype(str(FONT_TITLE), font_px)
        # Scale tracking with font size to keep visual balance
        tracking = tracking_px_per_em * (font_px / base_font_px)
        w = _measure_tracked_text(font, text, tracking)
        return w, tracking, font

    def _best_balanced_split(font_px: int) -> tuple[Optional[list[str]], Optional[list[int]], float, ImageFont.FreeTypeFont]:
        """
        Return (lines, widths, tracking, font) for the best balanced
        2-line split at this font size. Returns ([], [], tracking, font)
        if title has 1 word (can't split).
        """
        if len(words) < 2:
            _, tr, f = _build_line(words[0] if words else "", font_px)
            return None, None, tr, f

        best_split, best_max = None, float("inf")
        cached_tracking = 0.0
        cached_font = None
        for split in range(1, len(words)):
            line1 = " ".join(words[:split])
            line2 = " ".join(words[split:])
            w1, tr, font = _build_line(line1, font_px)
            w2, _,  _    = _build_line(line2, font_px)
            if cached_font is None:
                cached_tracking = tr
                cached_font = font
            mx = max(w1, w2)
            if mx < best_max:
                best_max = mx
                best_split = (line1, line2, w1, w2)
        if best_split is None:
            return None, None, cached_tracking, cached_font

        line1, line2, w1, w2 = best_split
        return [line1, line2], [w1, w2], cached_tracking, cached_font

    # Iterate from base_font_px downward in 10-px steps.
    for font_px in range(base_font_px, min_font_px - 1, -10):
        # Try single line first
        w, tracking, font = _build_line(title_upper, font_px)
        if w <= max_width:
            line_height = int(font_px * 1.1)
            return RenderedTitle(
                lines=[title_upper],
                font=font,
                font_px=font_px,
                line_widths=[w],
                tracking_px=tracking,
                line_height=line_height,
                block_height=line_height,
            )

        # Single line doesn't fit — try 2-line balanced if allowed + possible.
        if max_lines >= 2 and len(words) >= 2:
            lines, widths, tracking, font = _best_balanced_split(font_px)
            if lines is not None:
                mx = max(widths)
                if mx <= max_width:
                    line_height = int(font_px * 1.1)
                    block_height = line_height * len(lines)
                    return RenderedTitle(
                        lines=lines,
                        font=font,
                        font_px=font_px,
                        line_widths=widths,
                        tracking_px=tracking,
                        line_height=line_height,
                        block_height=block_height,
                    )

    # Nothing fit — return the best we can at min_font_px (will overflow
    # slightly but won't crash). Caller logs a warning.
    logger.warning(
        "Title %r does not fit in max_width=%dpx even at %dpx 2-line — "
        "compositor will render it overflowing.",
        title_upper, max_width, min_font_px,
    )
    w, tracking, font = _build_line(title_upper, min_font_px)
    line_height = int(min_font_px * 1.1)
    return RenderedTitle(
        lines=[title_upper],
        font=font,
        font_px=min_font_px,
        line_widths=[w],
        tracking_px=tracking,
        line_height=line_height,
        block_height=line_height,
    )


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
