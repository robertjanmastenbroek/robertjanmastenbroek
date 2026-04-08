"""
Carousel generator — creates branded multi-slide image carousels
for Instagram using Pillow. Brand: ancient × futuristic aesthetic.

CAROUSEL TYPES
==============
  event_recap        — Event photos + recap text
                       Slides: Title → 3-5 photo slides → CTA

  quote_card         — Single quote (scripture or lyric)
                       Slides: 1-3 quote slides (title slide + quote slides)

  track_announcement — New release announcement
                       Slides: Cover art style → track info → Spotify CTA

OUTPUT
======
  List of PNG file paths, 1080×1350px (4:5 Instagram optimal feed ratio).
  Ready to upload as an Instagram carousel post.
"""

import os
import math
import textwrap
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------------
# Brand palette
# ---------------------------------------------------------------------------
BG_DARK      = (10, 10, 10)       # #0a0a0a
BG_SECONDARY = (26, 26, 26)       # #1a1a1a
GOLD         = (212, 175, 55)     # #d4af37
BLUE         = (74, 144, 226)     # #4a90e2
WHITE        = (255, 255, 255)
GRAY         = (176, 176, 176)    # #b0b0b0
BLACK        = (0, 0, 0)

SLIDE_W = 1080
SLIDE_H = 1350

# ---------------------------------------------------------------------------
# Font loader
# ---------------------------------------------------------------------------
_FONT_SEARCH_PATHS = [
    # macOS
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Arial.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/SFNSDisplay.ttf",
    "/System/Library/Fonts/SFNS.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    # Windows (WSL / cross-platform fallback)
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

_font_path_cache: Optional[str] = None


def _find_font_path() -> Optional[str]:
    global _font_path_cache
    if _font_path_cache is not None:
        return _font_path_cache
    for p in _FONT_SEARCH_PATHS:
        if os.path.isfile(p):
            _font_path_cache = p
            return p
    return None


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Load a font at the given pixel size, falling back to default."""
    path = _find_font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    # PIL default — very small, but never raises
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Low-level drawing helpers
# ---------------------------------------------------------------------------

def _resolve_accent(accent_color: str) -> tuple:
    return BLUE if accent_color == "blue" else GOLD


def _new_slide() -> Image.Image:
    img = Image.new("RGB", (SLIDE_W, SLIDE_H), BG_DARK)
    return img


def _draw_chrome(draw: ImageDraw.ImageDraw, accent: tuple) -> None:
    """
    Draw shared chrome present on every slide:
      - Bottom accent strip (3 px line)
      - Top-left "RJM" label
      - Bottom-right tagline
    """
    # Bottom accent strip
    draw.rectangle(
        [(0, SLIDE_H - 20), (SLIDE_W, SLIDE_H - 17)],
        fill=accent,
    )

    # Top-left "RJM" small caps
    font_rjm = _font(24)
    draw.text((40, 40), "RJM", font=font_rjm, fill=GRAY)

    # Bottom-right tagline, right-aligned
    tagline = "Ancient Truth. Future Sound."
    font_tag = _font(18)
    bbox = draw.textbbox((0, 0), tagline, font=font_tag)
    tag_w = bbox[2] - bbox[0]
    draw.text(
        (SLIDE_W - 40 - tag_w, SLIDE_H - 50),
        tagline,
        font=font_tag,
        fill=GRAY,
    )


def _draw_diamond(draw: ImageDraw.ImageDraw, cx: int, cy: int, half: int, color: tuple, width: int = 2) -> None:
    """Draw a rotated-square (diamond) outline."""
    pts = [
        (cx,          cy - half),  # top
        (cx + half,   cy),         # right
        (cx,          cy + half),  # bottom
        (cx - half,   cy),         # left
        (cx,          cy - half),  # close
    ]
    draw.line(pts, fill=color, width=width)


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font: ImageFont.FreeTypeFont,
    fill: tuple,
    max_width: int = SLIDE_W - 120,
    line_spacing: int = 12,
) -> int:
    """
    Draw text centered horizontally, word-wrapped to max_width.
    Returns the y coordinate just below the last line.
    """
    # Estimate chars per line using a sample character width
    sample_bbox = draw.textbbox((0, 0), "W", font=font)
    char_w = max(sample_bbox[2] - sample_bbox[0], 1)
    chars_per_line = max(1, max_width // char_w)

    lines = textwrap.wrap(text, width=chars_per_line)
    if not lines:
        lines = [text]

    current_y = y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        line_h = bbox[3] - bbox[1]
        x = (SLIDE_W - line_w) // 2
        draw.text((x, current_y), line, font=font, fill=fill)
        current_y += line_h + line_spacing

    return current_y


def _draw_right_aligned_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    x_right: int,
    y: int,
    font: ImageFont.FreeTypeFont,
    fill: tuple,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    draw.text((x_right - text_w, y), text, font=font, fill=fill)


# ---------------------------------------------------------------------------
# Slide builders
# ---------------------------------------------------------------------------

def _build_title_slide(
    title: str,
    subtitle: str,
    accent: tuple,
) -> Image.Image:
    img = _new_slide()
    draw = ImageDraw.Draw(img)
    _draw_chrome(draw, accent)

    cx = SLIDE_W // 2
    center_y = SLIDE_H // 2 - 100

    # Sacred geometry diamond
    _draw_diamond(draw, cx, center_y, half=120, color=accent, width=2)

    # Thin horizontal rule between diamond and title
    line_y = center_y + 120 + 30
    draw.line(
        [(cx - 100, line_y), (cx + 100, line_y)],
        fill=accent,
        width=1,
    )

    # Title — uppercase, bold, white, 72px
    font_title = _font(72)
    title_y = line_y + 24
    end_y = _draw_centered_text(
        draw,
        title.upper(),
        title_y,
        font_title,
        WHITE,
        max_width=SLIDE_W - 120,
        line_spacing=8,
    )

    # Subtitle
    if subtitle:
        font_sub = _font(32)
        _draw_centered_text(
            draw,
            subtitle,
            end_y + 40,
            font_sub,
            GRAY,
        )

    return img


def _build_quote_slide(
    quote_text: str,
    attribution: str,
    accent: tuple,
) -> Image.Image:
    img = _new_slide()
    draw = ImageDraw.Draw(img)
    _draw_chrome(draw, accent)

    margin = 80
    # Large open-quote glyph
    font_quote_mark = _font(160)
    draw.text((margin, 160), "\u201c", font=font_quote_mark, fill=accent)

    # Quote body — 44px, white, centered with margins
    font_body = _font(44)
    text_start_y = 340
    end_y = _draw_centered_text(
        draw,
        quote_text,
        text_start_y,
        font_body,
        WHITE,
        max_width=SLIDE_W - margin * 2,
        line_spacing=16,
    )

    # Attribution
    if attribution:
        font_attr = _font(28)
        attr_text = f"\u2014 {attribution}"
        end_y = _draw_centered_text(
            draw,
            attr_text,
            end_y + 40,
            font_attr,
            GRAY,
        )

    # Three decorative dots below attribution
    dot_y = end_y + 30
    dot_r = 4
    dot_gap = 20
    dot_cx = SLIDE_W // 2
    for offset in (-1, 0, 1):
        dx = dot_cx + offset * (dot_r * 2 + dot_gap)
        draw.ellipse(
            [(dx - dot_r, dot_y - dot_r), (dx + dot_r, dot_y + dot_r)],
            fill=accent,
        )

    return img


def _build_photo_slide(
    photo_path: str,
    caption: str,
    accent: tuple,
) -> Image.Image:
    img = _new_slide()
    draw = ImageDraw.Draw(img)
    _draw_chrome(draw, accent)

    photo_h = 1080  # top portion of the 1350px slide

    # Load and crop-fit the photo to 1080×1080
    try:
        src = Image.open(photo_path).convert("RGB")
        src_w, src_h = src.size
        scale = max(SLIDE_W / src_w, photo_h / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)
        src = src.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - SLIDE_W) // 2
        top = (new_h - photo_h) // 2
        src = src.crop((left, top, left + SLIDE_W, top + photo_h))
    except Exception:
        # Placeholder gradient if photo can't be loaded
        src = Image.new("RGB", (SLIDE_W, photo_h), BG_SECONDARY)

    img.paste(src, (0, 0))

    # Redraw chrome (was painted on BG_DARK before photo paste)
    draw = ImageDraw.Draw(img)

    # Dark caption strip (bottom 270px)
    overlay_y = photo_h
    overlay = Image.new("RGBA", (SLIDE_W, SLIDE_H - photo_h), (0, 0, 0, 220))
    img.paste(overlay, (0, overlay_y), overlay)

    draw = ImageDraw.Draw(img)
    _draw_chrome(draw, accent)

    # Caption text
    if caption:
        font_cap = _font(36)
        _draw_centered_text(
            draw,
            caption,
            overlay_y + 30,
            font_cap,
            WHITE,
            max_width=SLIDE_W - 120,
        )

    # Gold corner brackets on the photo
    blen = 40   # bracket arm length
    bthk = 2    # bracket line thickness
    corners = [
        # top-left
        [(0, blen), (0, 0), (blen, 0)],
        # top-right
        [(SLIDE_W - blen, 0), (SLIDE_W, 0), (SLIDE_W, blen)],
        # bottom-left
        [(0, photo_h - blen), (0, photo_h), (blen, photo_h)],
        # bottom-right
        [(SLIDE_W - blen, photo_h), (SLIDE_W, photo_h), (SLIDE_W, photo_h - blen)],
    ]
    for pts in corners:
        draw.line(pts, fill=accent, width=bthk)

    return img


def _build_cta_slide(
    main_message: str,
    cta_text: str,
    accent: tuple,
) -> Image.Image:
    img = _new_slide()
    draw = ImageDraw.Draw(img)
    _draw_chrome(draw, accent)

    cx = SLIDE_W // 2

    # Small upward-pointing equilateral triangle above main message
    tri_base = 20
    tri_h = int(tri_base * math.sqrt(3) / 2)
    tri_cy = SLIDE_H // 2 - 180
    tri_pts = [
        (cx, tri_cy - tri_h),                    # apex
        (cx - tri_base // 2, tri_cy + tri_h // 2),  # bottom-left
        (cx + tri_base // 2, tri_cy + tri_h // 2),  # bottom-right
    ]
    draw.polygon(tri_pts, fill=accent)

    # Main message text
    font_main = _font(52)
    msg_y = tri_cy + tri_h // 2 + 40
    end_y = _draw_centered_text(
        draw,
        main_message,
        msg_y,
        font_main,
        WHITE,
        max_width=SLIDE_W - 120,
        line_spacing=12,
    )

    # CTA button — rounded rectangle
    btn_w, btn_h, btn_r = 400, 80, 40
    btn_x = (SLIDE_W - btn_w) // 2
    btn_y = end_y + 80
    draw.rounded_rectangle(
        [(btn_x, btn_y), (btn_x + btn_w, btn_y + btn_h)],
        radius=btn_r,
        fill=accent,
    )

    # CTA label inside button (dark text)
    font_cta = _font(36)
    btn_bbox = draw.textbbox((0, 0), cta_text, font=font_cta)
    cta_w = btn_bbox[2] - btn_bbox[0]
    cta_h = btn_bbox[3] - btn_bbox[1]
    draw.text(
        (btn_x + (btn_w - cta_w) // 2, btn_y + (btn_h - cta_h) // 2),
        cta_text,
        font=font_cta,
        fill=BG_DARK,
    )

    return img


def _build_track_title_slide(
    title: str,
    subtitle: str,
    release_info: str,
    accent: tuple,
    photo_path: Optional[str],
) -> Image.Image:
    """
    First slide of track_announcement.
    If a cover photo is provided, use it as a blurred background.
    Otherwise, generate concentric-circle abstract art.
    """
    img = _new_slide()

    if photo_path and os.path.isfile(photo_path):
        try:
            src = Image.open(photo_path).convert("RGB")
            src_w, src_h = src.size
            scale = max(SLIDE_W / src_w, SLIDE_H / src_h)
            new_w = int(src_w * scale)
            new_h = int(src_h * scale)
            src = src.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - SLIDE_W) // 2
            top = (new_h - SLIDE_H) // 2
            src = src.crop((left, top, left + SLIDE_W, top + SLIDE_H))
            # Heavy blur + darken for text legibility
            src = src.filter(ImageFilter.GaussianBlur(radius=18))
            darkener = Image.new("RGBA", (SLIDE_W, SLIDE_H), (0, 0, 0, 160))
            src = src.convert("RGBA")
            src.alpha_composite(darkener)
            img.paste(src.convert("RGB"), (0, 0))
        except Exception:
            pass
    else:
        # Abstract: concentric circles with decreasing opacity
        draw_bg = ImageDraw.Draw(img)
        cx, cy = SLIDE_W // 2, SLIDE_H // 2
        for i in range(12, 0, -1):
            r = i * 60
            alpha = int(255 * (i / 12) * 0.25)
            circle_color = accent + (alpha,)
            # Draw on a separate RGBA layer and composite
            layer = Image.new("RGBA", (SLIDE_W, SLIDE_H), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            ld.ellipse(
                [(cx - r, cy - r), (cx + r, cy + r)],
                outline=circle_color,
                width=2,
            )
            img = img.convert("RGBA")
            img.alpha_composite(layer)
            img = img.convert("RGB")

    draw = ImageDraw.Draw(img)
    _draw_chrome(draw, accent)

    cx = SLIDE_W // 2

    # Track title — large, white
    font_title = _font(80)
    title_y = SLIDE_H // 2 - 120
    end_y = _draw_centered_text(
        draw, title.upper(), title_y, font_title, WHITE,
        max_width=SLIDE_W - 100, line_spacing=10,
    )

    # "OUT NOW ON SPOTIFY" label
    font_spotify = _font(28)
    spotify_label = "OUT NOW ON SPOTIFY"
    lbl_bbox = draw.textbbox((0, 0), spotify_label, font=font_spotify)
    lbl_w = lbl_bbox[2] - lbl_bbox[0]
    draw.text(((SLIDE_W - lbl_w) // 2, end_y + 30), spotify_label, font=font_spotify, fill=accent)

    # Release info / stream count
    if release_info:
        font_info = _font(26)
        _draw_centered_text(
            draw, release_info, end_y + 80, font_info, GRAY,
        )

    # Subtitle
    if subtitle:
        font_sub = _font(32)
        _draw_centered_text(draw, subtitle, end_y + 140, font_sub, GRAY)

    return img


def _build_track_info_slide(
    track_title: str,
    body_text: str,
    accent: tuple,
) -> Image.Image:
    """Second slide: track details / description."""
    img = _new_slide()
    draw = ImageDraw.Draw(img)
    _draw_chrome(draw, accent)

    # Decorative diamond
    cx = SLIDE_W // 2
    _draw_diamond(draw, cx, 300, half=60, color=accent, width=2)

    font_label = _font(28)
    draw.text(
        (cx - 40, 380),
        "TRACK INFO",
        font=font_label,
        fill=accent,
    )

    font_title = _font(56)
    end_y = _draw_centered_text(
        draw, track_title.upper(), 440, font_title, WHITE,
        max_width=SLIDE_W - 100, line_spacing=8,
    )

    if body_text:
        font_body = _font(36)
        _draw_centered_text(
            draw, body_text, end_y + 50, font_body, GRAY,
            max_width=SLIDE_W - 160, line_spacing=14,
        )

    return img


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def generate_carousel(
    carousel_type: str,
    output_dir: str,
    title: str,
    subtitle: str = "",
    body_text: str = "",
    cta_text: str = "",
    photo_paths: Optional[List[str]] = None,
    accent_color: str = "gold",
    base_name: str = "carousel",
) -> List[str]:
    """
    Generate a branded multi-slide Instagram carousel.

    Parameters
    ----------
    carousel_type : str
        One of 'event_recap', 'quote_card', 'track_announcement'.
    output_dir : str
        Directory where PNG files will be written (created if absent).
    title : str
        Main title text (event name, track name, or first quote line).
    subtitle : str
        Secondary text shown on the title slide.
    body_text : str
        Body copy. For quote_card: the full quote text.
        For event_recap / track: additional description.
    cta_text : str
        Button label on the CTA slide (e.g. "LISTEN ON SPOTIFY").
    photo_paths : list of str, optional
        Local paths to photos. Used by event_recap (3-5 photos) and
        optionally by track_announcement (cover art blur background).
    accent_color : str
        'gold' (default) or 'blue'.
    base_name : str
        Filename prefix for output PNGs.

    Returns
    -------
    list of str
        Absolute paths to each generated PNG, in slide order.
    """
    os.makedirs(output_dir, exist_ok=True)
    accent = _resolve_accent(accent_color)
    photo_paths = photo_paths or []
    slides: List[Image.Image] = []

    # ------------------------------------------------------------------
    if carousel_type == "event_recap":
        # 1. Title slide
        slides.append(_build_title_slide(title, subtitle, accent))

        # 2. Photo slides (3-5, capped)
        photos_to_use = photo_paths[:5]
        if not photos_to_use:
            # Still generate placeholder photo slides so callers get a full deck
            photos_to_use = [None, None, None]

        for i, photo in enumerate(photos_to_use, start=1):
            cap = body_text if i == 1 else ""
            slides.append(_build_photo_slide(photo or "", cap, accent))

        # 3. CTA slide
        cta = cta_text or "FOLLOW FOR MORE"
        main_msg = subtitle or "See you on the dancefloor."
        slides.append(_build_cta_slide(main_msg, cta, accent))

    # ------------------------------------------------------------------
    elif carousel_type == "quote_card":
        # Title slide functions as context / speaker intro
        slides.append(_build_title_slide(title, subtitle, accent))

        # Split body_text into individual quotes by double newline
        raw_quotes = [q.strip() for q in body_text.split("\n\n") if q.strip()]
        if not raw_quotes:
            raw_quotes = [body_text or title]

        for q in raw_quotes[:3]:
            # Attempt attribution split: last line starting with "—" or "-"
            lines = q.splitlines()
            attribution = ""
            if len(lines) > 1 and lines[-1].lstrip().startswith(("-", "\u2014")):
                attribution = lines[-1].lstrip("-\u2014 ").strip()
                quote_body = " ".join(lines[:-1]).strip()
            else:
                quote_body = q

            slides.append(_build_quote_slide(quote_body, attribution, accent))

    # ------------------------------------------------------------------
    elif carousel_type == "track_announcement":
        cover_photo = photo_paths[0] if photo_paths else None

        # 1. Cover art / title slide
        slides.append(
            _build_track_title_slide(
                title=title,
                subtitle=subtitle,
                release_info=body_text,
                accent=accent,
                photo_path=cover_photo,
            )
        )

        # 2. Track info slide
        slides.append(
            _build_track_info_slide(
                track_title=title,
                body_text=body_text,
                accent=accent,
            )
        )

        # 3. CTA / Spotify slide
        cta = cta_text or "STREAM NOW"
        slides.append(
            _build_cta_slide(
                main_message="Available everywhere.\nNow on Spotify.",
                cta_text=cta,
                accent=accent,
            )
        )

    else:
        raise ValueError(
            f"Unknown carousel_type '{carousel_type}'. "
            "Choose from: event_recap, quote_card, track_announcement."
        )

    # ------------------------------------------------------------------
    # Save slides to disk
    # ------------------------------------------------------------------
    output_paths: List[str] = []
    for i, slide in enumerate(slides, start=1):
        filename = f"{base_name}_slide{i:02d}.png"
        path = os.path.join(output_dir, filename)
        slide.save(path, format="PNG", optimize=False)
        output_paths.append(os.path.abspath(path))

    return output_paths
