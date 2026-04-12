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
BOUNCE_RATE_LIMIT        = 0.05   # Pause sends if bounce rate exceeds 5% over window
BOUNCE_RATE_WINDOW_DAYS  = 14     # Rolling window for bounce rate calculation

# ─── Draft Mode ───────────────────────────────────────────────────────────────
# When True: creates Gmail drafts instead of sending. Use during testing.
DRAFT_MODE           = os.getenv("RJM_DRAFT_MODE", "false").lower() == "true"

# ─── Contact Types ────────────────────────────────────────────────────────────
CONTACT_TYPES = {
    "curator":       "Spotify playlist curator",
    "podcast":       "Podcast / radio show / livestream guest pitch",
    "youtube":       "YouTube channel / mix series",
    "festival":      "Festival booking / performance inquiry",
    "sync":          "Sync licensing / music supervisor",
    "booking_agent": "Booking agent",
    "wellness":      "Wellness / yoga retreat",
}

# ─── Outreach Priority Weights ────────────────────────────────────────────────
# Controls how the batch planner picks contact types each cycle.
# Goal: build streams + live bookings + press first. Labels come later.
CONTACT_TYPE_WEIGHTS = {
    "curator":        40,   # auto-adjusted by auto_weights (neutral growth state)
    "podcast":        60,   # auto-adjusted by auto_weights (neutral growth state)
    "youtube":        0,    # Paused
    "sync":           0,    # Paused
    "booking_agent":  0,    # Paused
    "wellness":       0,    # Paused
}

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
# If actual hard bounce rate over the window exceeds the limit, sends are paused
# automatically. Protects sender reputation before Gmail flags the account.
BOUNCE_RATE_LIMIT       = 0.15   # 15% — pause sends if bounce rate exceeds this
BOUNCE_RATE_WINDOW_DAYS = 7      # 7-day rolling window — larger sample = more stable rate

# ─── Reply Check ──────────────────────────────────────────────────────────────
REPLY_CHECK_INBOX_DAYS = 30   # Look back this many days when scanning for replies

# ─── Learning ─────────────────────────────────────────────────────────────────
LEARNING_REPORT_AFTER_N_REPLIES = 10   # Generate insight report after this many replies
MIN_SENDS_FOR_STATS = 5                # Minimum sends before surfacing template stats

DRAFTS_DIR.mkdir(exist_ok=True)
