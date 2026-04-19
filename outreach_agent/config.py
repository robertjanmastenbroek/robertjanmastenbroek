"""
RJM Autonomous Outreach Agent — Configuration
All tunable parameters in one place.
"""

import os
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
PROJECT_DIR    = BASE_DIR.parent
DB_PATH        = Path(os.getenv("RJM_DB_PATH", str(BASE_DIR / "outreach.db")))
CREDS_PATH     = BASE_DIR / "credentials.json"       # Google OAuth client secret
TOKEN_PATH     = BASE_DIR / "token.json"              # Stored OAuth token
DRAFTS_DIR     = BASE_DIR / "drafts"                  # Optional: save email copies
LOG_PATH       = BASE_DIR / "agent.log"
LEGACY_CSV     = PROJECT_DIR / "contacts.csv"

# ─── Gmail ────────────────────────────────────────────────────────────────────
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]
FROM_EMAIL     = os.getenv("RJM_FROM_EMAIL", "motomotosings@gmail.com")
FROM_NAME      = "Robert-Jan Mastenbroek"

# ─── Safety Net ───────────────────────────────────────────────────────────────
# Maximum send attempts before a contact is moved to 'dead_letter' and stops
# consuming quota. Set in Lake 1 of the outreach-completeness plan to prevent
# transient errors (Gmail 5xx, brand-gate repeated rejection) from infinite-retrying
# against the same address.
MAX_SEND_ATTEMPTS    = 3

# ─── Rate Limits ──────────────────────────────────────────────────────────────
MAX_EMAILS_PER_DAY   = 150          # Daily cap — safe for warmed personal Gmail doing targeted cold outreach
MAX_CONTENT_POSTS_PER_DAY = 3       # Daily cap for Buffer video posts (TikTok + IG Reels + YouTube count as 1 batch)
MAX_CONTACTS_FOUND_PER_DAY = 50     # Daily cap for new contacts discovered via find_contacts.py
ACTIVE_HOUR_START    = 8            # 08:00 — start of send window
ACTIVE_HOUR_END      = 23           # 23:00 — end of send window
MIN_INTERVAL_SECONDS = 60           # 1 min minimum between sends — fits 7 emails in 30-min cycle
MAX_INTERVAL_SECONDS = 120          # 2 min max gap — still looks human, avoids spam triggers
BATCH_SIZE           = 7            # Max per cycle — 30 runs/day × 7 = 210 theoretical, capped at 80 by daily limit
SMALL_PLAYLIST_PER_CYCLE = 5        # Reserve ~70% of each cycle for small contacts (curators + podcasts)
SMALL_PLAYLIST_MIN  = 500           # Saves / followers / downloads — lower bound for "small"
SMALL_PLAYLIST_MAX  = 10_000        # Saves / followers / downloads — upper bound for "small"

# ─── Follow-up ────────────────────────────────────────────────────────────────
FOLLOWUP_DAYS        = 5            # Days after initial send before first follow-up
FOLLOWUP2_DAYS       = 7            # Days after first follow-up before second (= day 12 total)
MAX_FOLLOWUPS        = 2            # Two follow-ups per contact

# ─── Claude CLI ───────────────────────────────────────────────────────────────
# Uses the Claude Code CLI (your Max plan) — no separate API key needed.
# Auto-detected from ~/Library/Application Support/Claude/claude-code/
# Override with env var if needed: export CLAUDE_CLI_PATH="/path/to/claude"
CLAUDE_CLI_PATH      = os.getenv("CLAUDE_CLI_PATH", "")   # blank = auto-detect

# Model selection — use fast/cheap model for simple tasks to save tokens
CLAUDE_MODEL_EMAIL   = "claude-haiku-4-5-20251001"   # initial email generation (Haiku — 6x cheaper, fits Pro plan)
CLAUDE_MODEL_FAST    = "claude-haiku-4-5-20251001"   # follow-ups, insights, analysis

# Scheduled task run interval (minutes) — must match the cron expression
CRON_INTERVAL_MINUTES = 30

# ─── Bounce Rate Guard ─────────────────────────────────────────────────────────
BOUNCE_RATE_LIMIT        = 0.05   # Pause sends if all-time bounce rate exceeds this

# ─── Draft Mode ───────────────────────────────────────────────────────────────
# When True: creates Gmail drafts instead of sending. Use during testing.
DRAFT_MODE           = os.getenv("RJM_DRAFT_MODE", "false").lower() == "true"

# ─── Contact Types ────────────────────────────────────────────────────────────
CONTACT_TYPES = {
    # Legacy types — kept for backward compat with existing pipeline
    "curator":       "Spotify playlist curator",
    "podcast":       "Podcast / radio show / livestream guest pitch",
    "youtube":       "YouTube channel / mix series",
    "festival":      "Festival booking / performance inquiry",
    "sync":          "Sync licensing / music supervisor",
    "booking_agent": "Booking agent",
    "wellness":      "Wellness / yoga retreat",
}

# ─── Relationship-First Personas ──────────────────────────────────────────────
# New persona system for fan + relationship outreach (2026-04-17 redesign).
# Persona lives in contacts.persona; type is kept for pipeline compat.
CONTACT_PERSONAS = {
    # Zone 1 — Sacred Intersection
    "faith_creator":      "Christian / spiritual content creator",
    "church":             "Church or Christian event organizer",
    "retreat":            "Faith-based or conscious retreat organizer",
    "ecstatic_dance":     "Ecstatic Dance organizer (sober, spiritually-open)",
    # Zone 2 — Scene Insiders
    "rave_photographer":  "Event / rave photographer or videographer",
    "sound_engineer":     "Live sound engineer at electronic music events",
    "conscious_promoter": "Conscious rave / sober event promoter",
    "lifestyle_creator":  "Rave fashion, festival lifestyle content creator",
    # Zone 3 — Lifestyle Overlap
    "digital_nomad":      "Digital nomad creator (Tenerife / location-independent)",
    "surfer":             "Surf / ocean lifestyle creator",
    "sacred_artist":      "Sacred geometry, dark aesthetic visual artist",
    "genre_creator":      "YouTube / TikTok melodic techno or psytrance creator",
    # Relationship-upgraded legacy
    "community_leader":   "Reddit / Discord / Facebook group admin or mod",
    "event_promoter":     "Underground venue or festival promoter",
}

# Relationship stages (contacts.relationship_stage)
RELATIONSHIP_STAGES = [
    "discovered",    # found, basic info only
    "researched",    # know who they are, what to say
    "first_touch",   # sent genuine first message about THEM
    "responded",     # they replied — this is the new "win"
    "nurturing",     # active conversation, building trust
    "collaborating", # featuring, sharing, or working with RJM
    "advocate",      # promoting RJM unprompted to their audience
]

# Outreach goals (contacts.outreach_goal)
OUTREACH_GOALS = ["relationship", "booking", "music_share", "collaboration"]

# ─── Outreach Priority Weights ────────────────────────────────────────────────
# Controls how the batch planner picks contact types each cycle.
# Goal: build streams + live bookings + press first. Labels come later.
#
# NOTE: The YouTube branch uses a *hard floor* (YOUTUBE_SHARE_FLOOR below) enforced
# by _weighted_order_with_youtube_floor() in run_cycle.py — the weight below is
# only used to sort YouTube relative to other types when the floor is already met.
CONTACT_TYPE_WEIGHTS = {
    "curator":        40,   # auto-adjusted by auto_weights (neutral growth state)
    "podcast":        60,   # auto-adjusted by auto_weights (neutral growth state)
    "youtube":        50,   # ACTIVATED — hard-floor allocator enforces ≥50% share
    "sync":           0,    # Paused
    "booking_agent":  0,    # Paused
    "wellness":       0,    # Paused
}

# ─── YouTube Outreach Branch ─────────────────────────────────────────────────
# PRE-REQUISITE: Content ID must be DISABLED in BandLab Music on the 5 psytrance
# tracks used for YouTube outreach (Halleluyah, Kavod, Jericho, Renamed, Fire In
# Our Hands). Otherwise the email template's "keep 100% ad rev" promise is a lie.
# See docs/superpowers/specs/2026-04-14-youtube-outreach-branch-design.md
YOUTUBE_SHARE_FLOOR      = 0.5       # 50% of each batch reserved for YouTube (overflow when supply short)
YOUTUBE_API_DAILY_UNITS_CAP = 8000   # abort discovery if > this many units used today (YT quota = 10K)
# Lowered from 10K→2K after first discovery run rejected 437/498 channels for subs<10K.
# Many real promo channels live at 2K–10K subs and post weekly; the original floor
# was too tight given RJM's aggressive-growth stance on YouTube as a Spotify driver.
YOUTUBE_MIN_SUBS         = 2_000
# Tightened from 500K → 80K: YouTube rate-limits "View email address" to ~10/day,
# so every unlock must go to a channel that might actually respond. Big artist-
# owned channels (Captain Hook, Astrix, etc.) will never upload someone else's
# track regardless of pitch quality — don't waste unlocks on them. Small/mid-tier
# promo channels in the 2K-80K range are the sweet spot.
YOUTUBE_MAX_SUBS         = 80_000
YOUTUBE_MIN_TOTAL_VIEWS  = 30_000
YOUTUBE_MIN_VIDEO_COUNT  = 20        # still signals active channel, not brand-new
YOUTUBE_MAX_UPLOAD_AGE_DAYS = 30

# Discovery seed queries — primary focus is (progressive) psytrance, with
# secondary melodic techno / progressive house and tertiary Christian EDM /
# organic house coverage. Ordered by priority; search budget = 100 units per
# query × ~25 queries = 2500 units per discovery run (well under 8K cap).
YOUTUBE_DISCOVERY_QUERIES = [
    # ── PRIMARY: psytrance / progressive psytrance ──
    "progressive psytrance mix 2026",
    "tribal psytrance mix",
    "psytrance promo channel",
    "psytrance 2026",
    "progressive psytrance 2026",
    # Psytrance sound-reference artists (from story.py:31 sound_refs)
    "Vini Vici",
    "Astrix",
    "Symphonix",
    "Ranji",
    "Ace Ventura",

    # ── SECONDARY: melodic techno / progressive house ──
    "melodic techno mix 2026",
    "melodic house mix 2026",
    "progressive house mix 2026",
    "Afterlife melodic techno",
    # Melodic/progressive artists
    "Colyn",
    "Massano",
    "Argy",
    "Anyma",

    # ── TERTIARY: Christian EDM ──
    "christian edm mix",
    "worship edm",
    "christian electronic music",

    # ── TERTIARY: organic house ──
    "organic house mix",
    "all day i dream mix",
    "afro house mix",
]

# Channel titles that match these names exactly are rejected as artist-owned
# (they won't upload our music regardless of how good the pitch is).
YOUTUBE_ARTIST_CHANNEL_BLOCKLIST = {
    "vini vici", "astrix", "symphonix", "ranji", "ace ventura",
    "colyn", "massano", "argy", "anyma", "rufus du sol",
    "robert-jan mastenbroek",  # don't re-discover RJM's own channel
}
# Regex OR'd into the reject heuristic for channel descriptions
YOUTUBE_ARTIST_CHANNEL_MARKERS = [
    "official artist channel",
    "verified artist",
    "official music videos",
    "official youtube channel",
    "Topic",  # YouTube auto-generated artist pages end in "- Topic"
    "my official",
    "booking:",
    "management:",
    "my music",
    "my debut",
    "my latest release",
    "my upcoming",
    "solo project",
    "solo artist",
    "live performances",
    "sign my",
]
# Channel titles ending in these tokens are almost always artist-owned
# (Captain Hook Official, Astrix Official, Robert-Jan Mastenbroek Official)
# — reject as artist channel regardless of subs/genre fit.
YOUTUBE_ARTIST_CHANNEL_NAME_SUFFIXES = (
    " official",
    " music",
    " records",  # wait — labels ARE promo channels — remove this
)
# Actually labels ARE who we want to pitch. Only reject on " official" suffix.
YOUTUBE_ARTIST_CHANNEL_NAME_SUFFIXES = (
    " official",
)
# Keywords that must appear in a channel's description/title to pass the genre
# filter. PRIMARY keywords (psytrance focus) are weighted 2x in _genre_score
# so psytrance channels outrank secondary/tertiary genres in the send queue.
YOUTUBE_GENRE_KEYWORDS_PRIMARY = [
    "psytrance", "psy-trance", "psy trance", "progressive psytrance",
    "tribal psytrance", "psy",
]
YOUTUBE_GENRE_KEYWORDS_SECONDARY = [
    # Melodic techno / progressive / Christian EDM / organic house — all welcome
    # but these score lower than primary psytrance keywords.
    "progressive", "tribal", "melodic techno", "melodic house",
    "progressive house", "techno", "trance", "melodic",
    "christian", "worship", "gospel", "christian edm",
    "organic house", "afro house", "deep house",
    # Channel-type indicators (not genre per se, but signal promo intent)
    "mix", "promo", "compilation", "set", "dj set",
]
# Backwards-compatible flat list (still referenced for regex/search operations)
YOUTUBE_GENRE_KEYWORDS = YOUTUBE_GENRE_KEYWORDS_PRIMARY + YOUTUBE_GENRE_KEYWORDS_SECONDARY

# ─── Spotify Growth Velocity → Weight Adjustment ──────────────────────────────
# auto_weights command reads listener velocity and rewrites CONTACT_TYPE_WEIGHTS.
# Thresholds: listeners gained per 7-day window.
GROWTH_VELOCITY_THRESHOLDS = {
    "fast":   500,   # >500/week → scale up curators (algo momentum)
    "slow":  -100,   # <-100/week → scale up podcasts (story-driven recovery)
    # between slow and fast: keep current weights
}

# Weight profiles per growth state
GROWTH_WEIGHT_PROFILES = {
    "fast":    {"curator": 70, "podcast": 30},   # ride the algo wave
    "neutral": {"curator": 40, "podcast": 60},   # current default
    "slow":    {"curator": 20, "podcast": 80},   # podcasts for story-driven recovery
}

# Faith/Christian contacts are welcomed across all types but not quota-targeted.
# Tag contacts with "christian" or "faith" in their notes — template engine leads with the faith angle for them.

# ─── Bounce Rate Circuit Breaker ──────────────────────────────────────────────
# If actual hard bounce rate all-time exceeds the limit, sends are paused
# automatically. Uses all-time data so a long clean history isn't erased by a
# short noisy window. Denominator includes bounced contacts so rate is never
# artificially inflated.
BOUNCE_RATE_LIMIT       = 0.15   # 15% — pause sends if all-time bounce rate exceeds this

# ─── Warm-up Buffer ───────────────────────────────────────────────────────────
# agent_discovered contacts go to 'warm_up' status after verification.
# They are sent to at a lower daily cap to protect Gmail sender reputation
# while the discovery pipeline's address quality is unproven.
# csv_legacy / manual contacts bypass the warm-up and go straight to 'verified'.
WARM_UP_DAILY_CAP = 10   # max agent_discovered sends per day

# ─── Reply Check ──────────────────────────────────────────────────────────────
REPLY_CHECK_INBOX_DAYS = 30   # Look back this many days when scanning for replies

# ─── Learning ─────────────────────────────────────────────────────────────────
LEARNING_REPORT_AFTER_N_REPLIES = 10   # Generate insight report after this many replies
MIN_SENDS_FOR_STATS = 5                # Minimum sends before surfacing template stats

# ─── Boil the Lake Protocol ──────────────────────────────────────────────────

# Experiment limits
BTL_MAX_CONCURRENT_EXPERIMENTS = 5
BTL_MIN_EXPERIMENT_DAYS = 7
BTL_MAX_EXPERIMENT_DAYS = 28
BTL_MIN_DATA_POINTS = 6

# Bandit config
BTL_BANDIT_WINDOW_DAYS = 28
BTL_BANDIT_COLD_START_MIN = 5
BTL_BANDIT_EXPLORE_COLD = 0.20
BTL_BANDIT_EXPLORE_WARM = 0.10
BTL_BANDIT_WARM_THRESHOLD = 20
BTL_BANDIT_OUTLIER_MULTIPLIER = 2.0

# Reallocation
BTL_REALLOCATION_LEARNING_RATE = 0.3
BTL_CHANNEL_WEIGHT_FLOOR = 0.05
BTL_CHANNEL_WEIGHT_CEILING = 0.40
BTL_CHANNEL_BREAKTHROUGH_CEILING = 0.50
BTL_UNDERPERFORM_WEEKS_TO_PAUSE = 4
BTL_UNDERPERFORM_ROI_THRESHOLD = 0.2

# Veto system
BTL_VETO_WINDOW_HOURS = 24
BTL_DIGEST_HOUR_CET = 8
BTL_DIGEST_EMAIL = FROM_EMAIL

# Budget
BTL_DONATION_ALLOCATION_PCT = 0.50
BTL_AUTO_SPEND_MAX_EUR = 5.0
BTL_VETO_SPEND_MAX_EUR = 25.0
BTL_DAILY_SPEND_CAP_EUR = 15.0
BTL_DAILY_SPEND_CAP_PCT = 0.30
BTL_RESERVE_MIN_EUR = 5.0

# Stripe
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")

# Score thresholds
BTL_SCORE_STAY_COURSE = 80
BTL_SCORE_INCREASE_DISCOVERY = 60
BTL_SCORE_EMERGENCY = 40
BTL_SCORE_RED_ALERT = 20

# Layer cadences
BTL_L1_RUNS_PER_DAY = 4
BTL_L2_DAY = "sunday"
BTL_L2_HOUR_CET = 20
BTL_L3_DAYS = ["tuesday", "friday"]
BTL_L3_HOUR_CET = 10

# Platform safety limits
BTL_REDDIT_MAX_POSTS_PER_WEEK = 2
BTL_IG_MAX_REELS_PER_DAY = 3
BTL_TIKTOK_MAX_POSTS_PER_DAY = 3

DRAFTS_DIR.mkdir(exist_ok=True)
