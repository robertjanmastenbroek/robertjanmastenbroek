"""
YouTube long-form pipeline — configuration.

All tunables in one place. Reads from environment where sensitive.
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_env_walking_up() -> None:
    """
    Load .env from the nearest ancestor directory that contains one.
    When this module runs from a git worktree, the real .env often lives
    at the main project root (two levels above the worktree). Without
    this, CLOUDINARY_URL etc. silently fail to resolve.
    """
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip()
                if len(v) >= 2 and (
                    (v[0] == '"' and v[-1] == '"')
                    or (v[0] == "'" and v[-1] == "'")
                ):
                    v = v[1:-1]
                os.environ.setdefault(k, v)
            return


_load_env_walking_up()


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

# ─── fal.ai endpoints ────────────────────────────────────────────────────────
# Verified against fal.ai live docs 2026-04-21.
FAL_KEY             = os.getenv("FAL_KEY", "")

# Primary: Flux 2 Pro — $0.03 for 1st MP + $0.015/extra MP. Accepts
# width/height as ImageSize object. Does NOT accept num_inference_steps,
# guidance_scale, negative_prompt, or loras. Negatives are merged into
# the positive prompt via _merge_negative_into_prompt().
FAL_FLUX_2_PRO_EP   = "fal-ai/flux-2-pro"

# Flux 2 Pro Edit — reference-image conditioning. Up to 9 references, 9 MP
# input total. Output cost same as flux-2-pro ($0.03 first MP), input cost
# $0.015 per MP of reference image. A 2-reference generation @ 1920x1080
# typically costs ~$0.075. Used when reference_urls is supplied to
# image_gen.generate_hero() — anchors style on proven-viral thumbnails
# from content/images/proven_viral/.
FAL_FLUX_2_PRO_EDIT_EP = "fal-ai/flux-2-pro/edit"

# LoRA-inference path: Flux 1 with LoRA support at $0.035/MP. Accepts
# loras array, width/height, num_inference_steps, guidance_scale,
# negative_prompt. Used ONLY when FAL_BRAND_LORA_URL is set.
#
# NOTE: Flux 2 Dev LoRA inference is a separate endpoint not yet wired in;
# if we train on the Flux 2 trainer, inference path needs revisiting.
FAL_FLUX_LORA_EP    = "fal-ai/flux-lora"

# LoRA training (optional; expensive). $0.0255 per step on Flux 2 Trainer V2:
#   1000 steps = $25.50, 2000 steps = $51.
# Consider Flux 1 lora-fast-training for cheaper LoRAs ($0.008/step) if needed.
FAL_FLUX_2_TRAINER_EP = "fal-ai/flux-2-trainer-v2"
FAL_FLUX_1_TRAINER_EP = "fal-ai/flux-lora-fast-training"     # Cheaper fallback

# Text-overlay thumbnail fallback (not currently used in default path)
FAL_IDEOGRAM_EP     = "fal-ai/ideogram/v3"

# ─── Motion (image → video loops) ────────────────────────────────────────────
# Two Kling tiers; we use them for different roles.
#
# Kling 2.1 Standard — image-to-video with a SINGLE input frame. Good for
# per-scene subtle motion (flutter, blink, drift). Pricing: $0.28/5s clip.
# Used ONLY for ambient in-scene motion — not for scene-to-scene morph.
#
# Kling O3 Standard — image-to-video with start AND end frame. This is
# the tool for seamless scene-to-scene morph chaining: feed the end frame
# of scene N as "image_url" and the first frame of scene N+1 as
# "end_image_url", and Kling generates the morphing transition between
# them. Pricing (verified 2026-04-21): $0.084/second → $0.84 per 10s clip.
# For a 30s seamless hypnotic loop (Omiki 'Wana' style), we chain 3×10s
# O3 clips where the last clip's end frame = the first clip's start frame.
#
# Kling O3 schema:
#   prompt         str  — what transforms, and how (morph type, camera)
#   image_url      str  — START frame (public URL)
#   end_image_url  str  — END frame (public URL)
#   duration       int  — "5" or "10"  seconds
#   aspect_ratio   str  — "16:9" | "9:16" | "1:1"
FAL_KLING_21_STANDARD_EP = "fal-ai/kling-video/v2.1/standard/image-to-video"
FAL_KLING_O3_STANDARD_EP = "fal-ai/kling-video/o3/standard/image-to-video"

# Veo 3.1 first-last-frame-to-video — Google's premium keyframe-chain
# engine. 2.4× the cost of Kling O3 but often higher fidelity on faces.
# Reserved for a later quality A/B; not used in the default morph path.
FAL_VEO_31_FIRSTLAST_EP  = "fal-ai/veo3.1/first-last-frame-to-video"

KLING_CLIP_SECONDS_DEFAULT = 5         # 5s keeps Standard cost at $0.28/clip
KLING_O3_CLIP_SECONDS      = 10        # 10s at $0.084/s = $0.84 per morph
KLING_ASPECT_16_9          = "16:9"    # Long-form background
KLING_ASPECT_9_16          = "9:16"    # Shorts repurpose path
KLING_MOTION_TIMEOUT_SECONDS = 360     # O3 morphs can run 3-5 min

# Brand LoRA — populated after training. Leave empty to use pure prompt baseline.
# If empty, generation uses fal-ai/flux-2-pro (baseline).
# If set, generation switches to fal-ai/flux-lora (Flux 1) + the brand LoRA.
FAL_BRAND_LORA_URL  = os.getenv("FAL_BRAND_LORA_URL", "")
FAL_BRAND_LORA_SCALE = float(os.getenv("FAL_BRAND_LORA_SCALE", "0.80"))

# Reference-image pool — CLIP-style anchoring for quality lift
PROVEN_VIRAL_DIR    = CONTENT_DIR / "images" / "proven_viral"
REFERENCE_COUNT_PER_GEN = 2       # Number of references per fal-ai/flux-2-pro/edit call
                                  # Higher = stronger style lock, also pricier

# Image gen parameters (defaults match research recommendations)
HERO_WIDTH, HERO_HEIGHT        = 1920, 1080
THUMB_WIDTH, THUMB_HEIGHT      = 1280, 720
THUMB_VARIANT_COUNT            = 3                  # For YouTube Test & Compare
FAL_TIMEOUT_SECONDS            = 180
FAL_POLL_INTERVAL_SECONDS      = 2

# ─── Cloudinary (primary render) ─────────────────────────────────────────────
# The cloudinary Python SDK auto-parses CLOUDINARY_URL in the form
#   cloudinary://<api_key>:<api_secret>@<cloud_name>
# We accept either the consolidated URL OR the three split vars for flexibility.
CLOUDINARY_URL        = os.getenv("CLOUDINARY_URL", "")
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY    = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")


def cloudinary_configured() -> bool:
    """True if either CLOUDINARY_URL or the three split vars are set."""
    return bool(CLOUDINARY_URL) or all(
        (CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET)
    )

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
# Channel-scoped OAuth isolation:
#   The app-level credentials (CLIENT_ID / CLIENT_SECRET) are shared with the
#   existing Shorts pipeline — one OAuth app can authorize many channels.
#
#   The REFRESH_TOKEN is channel-specific. The existing Shorts pipeline reads
#   YOUTUBE_REFRESH_TOKEN (authorized against @robertjanmastenbroekofficial).
#   The Holy Rave long-form pipeline reads HOLYRAVE_REFRESH_TOKEN first,
#   falling back to YOUTUBE_REFRESH_TOKEN only if the Holy Rave-specific
#   token is not set.
#
#   Setup: run scripts/setup_youtube_oauth.py while logged into the Holy Rave
#   channel; that script writes HOLYRAVE_REFRESH_TOKEN to .env.
#
#   This isolation means the Shorts pipeline and the long-form pipeline never
#   accidentally publish to the wrong channel. The existing distributor.py
#   is untouched.
YT_CLIENT_ID            = os.getenv("YOUTUBE_CLIENT_ID", "")
YT_CLIENT_SECRET        = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YT_REFRESH_TOKEN        = (
    os.getenv("HOLYRAVE_REFRESH_TOKEN")             # Holy Rave channel (preferred)
    or os.getenv("YOUTUBE_REFRESH_TOKEN", "")        # Fallback — will publish to main channel
)
YT_HOLY_RAVE_CHANNEL_ID = os.getenv("YOUTUBE_HOLY_RAVE_CHANNEL_ID", "")


def youtube_oauth_is_holyrave() -> bool:
    """True if the dedicated Holy Rave refresh token is set (vs falling back to main)."""
    return bool(os.getenv("HOLYRAVE_REFRESH_TOKEN"))

# Playlist IDs for auto-add (optional — publisher skips gracefully if empty).
# Consolidated to TWO playlists per user direction (2026-04-21):
#   - YT_PLAYLIST_ETHNIC_TRIBAL  → 128-136 BPM organic-house / ethnic / world
#   - YT_PLAYLIST_TRIBAL_PSY     → 140+ BPM tribal psytrance
YT_PLAYLIST_ETHNIC_TRIBAL = os.getenv("YOUTUBE_PLAYLIST_ETHNIC_TRIBAL", "")
YT_PLAYLIST_TRIBAL_PSY    = os.getenv("YOUTUBE_PLAYLIST_TRIBAL_PSY", "")
# Legacy (deprecated, kept for back-compat) — remove after full migration:
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
    pool_dir_130 = PROVEN_VIRAL_DIR / "bucket_130_organic"
    pool_dir_140 = PROVEN_VIRAL_DIR / "bucket_140_psytrance"
    pool_130 = len(list(pool_dir_130.glob("*.[jp][pn]g"))) if pool_dir_130.exists() else 0
    pool_140 = len(list(pool_dir_140.glob("*.[jp][pn]g"))) if pool_dir_140.exists() else 0
    return {
        "fal_key":                 bool(FAL_KEY),
        "fal_brand_lora":          bool(FAL_BRAND_LORA_URL),
        "cloudinary":              cloudinary_configured(),
        "shotstack":               bool(SHOTSTACK_API_KEY),
        "shotstack_env":           SHOTSTACK_ENV,
        "youtube_oauth":           all((YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN)),
        "youtube_oauth_isolated":  youtube_oauth_is_holyrave(),
        "holy_rave_channel_id":    bool(YT_HOLY_RAVE_CHANNEL_ID),
        "featurefm":               bool(FEATUREFM_API_KEY),
        "audio_masters_present":   AUDIO_MASTERS.exists(),
        "lora_training_set_size":  len(list(LORA_TRAINING_DIR.glob("*.[jp][pn]g"))) if LORA_TRAINING_DIR.exists() else 0,
        "proven_viral_130_count":  pool_130,
        "proven_viral_140_count":  pool_140,
        "proven_viral_total":      pool_130 + pool_140,
    }
