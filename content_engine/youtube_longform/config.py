"""
YouTube long-form pipeline — configuration.

All tunables in one place. Reads from environment where sensitive.
"""
from __future__ import annotations

import os
from pathlib import Path

# ─── Project paths ────────────────────────────────────────────────────────────
PROJECT_DIR     = Path(__file__).resolve().parent.parent.parent
CONTENT_DIR     = PROJECT_DIR / "content"
AUDIO_MASTERS   = CONTENT_DIR / "audio" / "masters"

# Long-form pipeline workspace
WORK_DIR        = CONTENT_DIR / "output" / "youtube_longform"
IMAGE_DIR       = WORK_DIR / "images"
VIDEO_DIR       = WORK_DIR / "videos"
REGISTRY_DIR    = PROJECT_DIR / "data" / "youtube_longform"

# LoRA training set lives here (NOT uploaded anywhere by this pipeline)
LORA_TRAINING_DIR = CONTENT_DIR / "images" / "lora_training" / "holy_rave_v1"

# ─── fal.ai (Flux 2 Pro + LoRA) ──────────────────────────────────────────────
FAL_KEY             = os.getenv("FAL_KEY", "")
FAL_FLUX_2_PRO_EP   = "fal-ai/flux-2-pro"          # Primary image gen
FAL_FLUX_2_LORA_EP  = "fal-ai/flux-2/lora"         # Flux 2 with LoRA support
FAL_IDEOGRAM_EP     = "fal-ai/ideogram/v3"         # Fallback for text-overlay thumbnails

# Brand LoRA — populated after training. Leave empty to use pure prompt baseline.
FAL_BRAND_LORA_URL  = os.getenv("FAL_BRAND_LORA_URL", "")
FAL_BRAND_LORA_SCALE = float(os.getenv("FAL_BRAND_LORA_SCALE", "0.80"))

# Image gen parameters (defaults match research recommendations)
HERO_WIDTH, HERO_HEIGHT        = 1920, 1080
THUMB_WIDTH, THUMB_HEIGHT      = 1280, 720
THUMB_VARIANT_COUNT            = 3                  # For YouTube Test & Compare
FAL_TIMEOUT_SECONDS            = 180
FAL_POLL_INTERVAL_SECONDS      = 2

# ─── Cloudinary (primary render) ─────────────────────────────────────────────
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY    = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")

# ─── Shotstack (fallback render) ─────────────────────────────────────────────
SHOTSTACK_API_KEY   = os.getenv("SHOTSTACK_API_KEY", "")
SHOTSTACK_ENV       = os.getenv("SHOTSTACK_ENV", "stage")    # "stage" or "v1"
SHOTSTACK_TIMEOUT   = 600                                    # Render poll timeout (s)

# Video output specs — YouTube-recommended for music uploads
VIDEO_WIDTH, VIDEO_HEIGHT = 1920, 1080
VIDEO_FPS                 = 30
VIDEO_CODEC               = "h264"
VIDEO_BITRATE_KBPS        = 8000
AUDIO_CODEC               = "aac"
AUDIO_SAMPLE_RATE         = 48000
AUDIO_BITRATE_KBPS        = 320

# ─── YouTube Data API v3 ─────────────────────────────────────────────────────
# Reuses the existing RJM OAuth credentials. The same Google account can
# authorize against multiple channels — Holy Rave uploads will target
# YOUTUBE_HOLY_RAVE_CHANNEL_ID if set, otherwise the account default.
YT_CLIENT_ID            = os.getenv("YOUTUBE_CLIENT_ID", "")
YT_CLIENT_SECRET        = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YT_REFRESH_TOKEN        = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
YT_HOLY_RAVE_CHANNEL_ID = os.getenv("YOUTUBE_HOLY_RAVE_CHANNEL_ID", "")

# Playlist IDs for auto-add (optional — publisher skips gracefully if empty)
YT_PLAYLIST_TRIBAL_PSY   = os.getenv("YOUTUBE_PLAYLIST_TRIBAL_PSY", "")
YT_PLAYLIST_ORGANIC_HOUSE = os.getenv("YOUTUBE_PLAYLIST_ORGANIC_HOUSE", "")
YT_PLAYLIST_MIDDLE_EASTERN = os.getenv("YOUTUBE_PLAYLIST_MIDDLE_EASTERN", "")

# Quota budgeting (default 10,000 units/day)
YT_DAILY_QUOTA_CAP       = int(os.getenv("YOUTUBE_DAILY_QUOTA_CAP", "10000"))
YT_UNITS_INSERT          = 1600
YT_UNITS_THUMBNAIL       = 50
YT_UNITS_PLAYLIST_ADD    = 50

# Resumable upload tuning
YT_CHUNK_SIZE_BYTES      = 8 * 1024 * 1024              # 8 MB
YT_MAX_RETRIES           = 10

# ─── Feature.fm smart link (Spotify funnel instrumentation) ──────────────────
FEATUREFM_API_KEY    = os.getenv("FEATUREFM_API_KEY", "")
FEATUREFM_ACCOUNT_ID = os.getenv("FEATUREFM_ACCOUNT_ID", "")
# Fallback: Odesli (free) if Feature.fm is unavailable
ODESLI_API_BASE      = "https://api.song.link/v1-alpha.1"

# UTM convention for YouTube-sourced clicks
UTM_SOURCE   = "youtube"
UTM_MEDIUM   = "holyrave_longform"
# Per-track UTM campaign derived from track title slug at publish time.

# ─── Title / description template ────────────────────────────────────────────
CHANNEL_BRAND_NAME   = "Holy Rave"
ARTIST_FULL_NAME     = "Robert-Jan Mastenbroek"
ARTIST_WEBSITE       = "https://robertjanmastenbroek.com"
ARTIST_INSTAGRAM     = "https://instagram.com/robertjanmastenbroek"
ARTIST_TIKTOK        = "https://tiktok.com/@robertjanmastenbroek"
SPOTIFY_ARTIST_URL   = "https://open.spotify.com/artist/2Seaafm5k1hAuCkpdq7yds"
APPLE_MUSIC_URL      = os.getenv("APPLE_MUSIC_ARTIST_URL", "https://music.apple.com/us/artist/robert-jan-mastenbroek/1875064180")

# Hashtag stacks by BPM tier — top 3 are hoisted above the title by YouTube,
# bottom set is appended to description footer.
HASHTAGS_TOP = {
    "psytrance":       ["#TribalPsytrance", "#Psytrance", "#HolyRave"],
    "organic_tribal":  ["#OrganicHouse", "#TribalHouse", "#HolyRave"],
    "organic_house":   ["#OrganicHouse", "#NomadicElectronic", "#HolyRave"],
    "middle_eastern":  ["#MiddleEasternElectronic", "#CafeDeAnatolia", "#HolyRave"],
}

HASHTAGS_BOTTOM = [
    "#nomadicelectronic", "#organichouse", "#tribalpsytrance", "#goatrance",
    "#middleeasternelectronic", "#cafedeanatolia", "#solselectas", "#sabo",
    "#bedouin", "#acidarab", "#aceventura", "#vertex", "#aioaska",
    "#handpan", "#oud", "#tribaldrums", "#sacredgeometry", "#desertrave",
    "#holyrave", "#ancienttruthfuturesound",
]

# Default schedule: Thursday 17:00 UTC — EU evening + US afternoon overlap
DEFAULT_PUBLISH_WEEKDAY = 3   # 0=Mon, 3=Thu
DEFAULT_PUBLISH_HOUR_UTC = 17


def ensure_workspace() -> None:
    """Create all required directories. Idempotent."""
    for d in (WORK_DIR, IMAGE_DIR, VIDEO_DIR, REGISTRY_DIR, LORA_TRAINING_DIR):
        d.mkdir(parents=True, exist_ok=True)


def config_summary() -> dict[str, bool]:
    """Health-check dict for `rjm.py content youtube status`."""
    return {
        "fal_key":                 bool(FAL_KEY),
        "fal_brand_lora":          bool(FAL_BRAND_LORA_URL),
        "cloudinary":              all((CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET)),
        "shotstack":               bool(SHOTSTACK_API_KEY),
        "youtube_oauth":           all((YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN)),
        "holy_rave_channel_id":    bool(YT_HOLY_RAVE_CHANNEL_ID),
        "featurefm":               bool(FEATUREFM_API_KEY),
        "audio_masters_present":   AUDIO_MASTERS.exists(),
        "lora_training_set_size":  len(list(LORA_TRAINING_DIR.glob("*.[jp][pn]g"))) if LORA_TRAINING_DIR.exists() else 0,
    }
