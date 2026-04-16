# YouTube Outreach Branch — Design Spec

Date: 2026-04-14
Branch: `claude/musing-pascal`
Status: Approved to build (user waived interactive review gates)

## Problem

The outreach agent currently sends to Spotify curators and podcasts. RJM needs a third branch — YouTube music-promo channels — to absorb **at least 50%** of outgoing email volume. The ask: channels upload RJM's Progressive/Tribal Psytrance tracks (Halleluyah, Kavod, Jericho, Renamed, Fire In Our Hands) to their channel; in exchange they get genre-fit content and RJM's Spotify/Apple links in the description drive streams back to him.

The user has seen this strategy work. We're building it with "free placements only" as the initial scope.

## Non-goals

- Paid placements (phase 2, not in this spec)
- Unreleased tracks in cold outreach (Selah, Step by Step, Side by Side — released tracks only)
- Apple Music / Deezer URLs (Spotify only until the user provides a link sheet)
- Upload-detection tracking (is a channel actually uploading after we send? — phase 2)
- Track-specific landing pages (phase 2)

## Strategic framing — the Content ID reality

RJM distributes through **BandLab Music**, which registers tracks with YouTube Content ID by default. If Content ID stays on, any upload of Halleluyah/Kavod/Jericho/etc. gets auto-claimed and the channel earns nothing — the outreach pitch "keep 100% ad rev" would be a lie.

**Decision: RJM disables Content ID on the 5 psytrance outreach tracks in BandLab.** This is a pre-requisite (Phase 0) before any cold email ships. BandLab lets you opt out of YouTube monetization at the release level — setting it off on these tracks means any upload is legitimately keep-the-ad-rev.

Why this over alternatives:
- **Money is the strongest motivator** for a promo channel deciding whether to upload 4 minutes of unfamiliar audio. "Keep 100% of the ad revenue" is a concrete, verifiable promise. "IG reciprocity" is a softer, harder-to-value trade.
- **Operational simplicity**: no per-channel claim-release workflow, no dispute queue, no trust issues if a claim slips through.
- **Cost to RJM is tiny**: the passive Content ID revenue on a 36-year-old Dutch producer's psytrance catalog is currently near-zero. Opting out of Content ID on these 5 tracks trades a few dollars/month for dozens of potential uploads driving Spotify streams — the direction of the 1M monthly listener north star.
- **Reversible**: Content ID can be re-enabled on any track at any time if the strategy underperforms.

RJM must complete the BandLab toggle before the first outreach cycle runs. The spec + template + CLI assume Content ID is off on these tracks; if it's still on when emails go out, the promise becomes dishonest.

Tracks to disable Content ID on:
1. Halleluyah
2. Kavod
3. Jericho
4. Renamed
5. Fire In Our Hands

## Architecture (Approach A from brainstorm)

```
┌─────────────────────────────────────────────────────────────┐
│ Discovery (new)                                             │
│                                                             │
│ rjm.py youtube discover                                     │
│   └─ youtube_discover.py                                    │
│        ├─ youtube_auth.py (shared with content_engine)      │
│        ├─ YouTube Data API v3 search → channels → uploads   │
│        ├─ Email extraction (description regex → web fallback)│
│        ├─ Qualification filter (subs, recency, genre match) │
│        └─ db.add_contact(type='youtube', status='new')      │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ Outreach (existing, extended)                               │
│                                                             │
│ rjm.py outreach run (no change to CLI)                      │
│   └─ run_cycle.py                                           │
│        ├─ plan_with_youtube_floor() ← NEW                   │
│        ├─ template_engine.generate_emails_batch()           │
│        │   └─ new "youtube_channel_upload" addon prompt     │
│        └─ gmail_client.send_email()                         │
└─────────────────────────────────────────────────────────────┘
```

All heavy plumbing (Gmail OAuth, rate limits, bounce detection, reply classification, brand gate, warm-up buffer, learning loop) is **reused unchanged**.

## Component 1 — Database schema

New columns on `contacts` (NULL-safe for existing rows):

| Column | Type | Purpose |
|---|---|---|
| `youtube_channel_id` | TEXT | `UC...` ID — unique index for dedup |
| `youtube_channel_url` | TEXT | Public channel URL |
| `youtube_subs` | INTEGER | Subscriber count at discovery |
| `youtube_video_count` | INTEGER | Total uploads at discovery |
| `youtube_last_upload_at` | TEXT | ISO date of latest upload |
| `youtube_genre_match_score` | REAL | 0.0–1.0 keyword density |
| `youtube_recent_upload_title` | TEXT | Title of latest relevant upload (for personalization) |

New table `api_budget` (tracks YouTube Data API v3 quota):
```sql
CREATE TABLE api_budget (
    date TEXT PRIMARY KEY,       -- YYYY-MM-DD (Pacific time — matches YT quota reset)
    service TEXT NOT NULL,       -- 'youtube'
    units_used INTEGER DEFAULT 0
);
```

Migration: `db.init_db()` is extended with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` logic. Existing code already uses a try/except pattern for idempotent migrations — the new columns follow the same pattern.

## Component 2 — YouTube API key (read-only, no OAuth)

**Revised from original plan.** YouTube Data API v3 read-only operations (`search.list`, `channels.list`, `playlistItems.list`) accept a plain API key — no OAuth required. Only *write* operations (upload, comment, rate) need OAuth, and those belong to `content_engine/distributor.py`.

Discovery therefore reads `os.environ["YOUTUBE_API_KEY"]` directly inside `youtube_discover.py`. No new auth module is needed. The content distributor's OAuth flow is left untouched.

The existing `YOUTUBE_API_KEY` env var (documented in `docs/superpowers/specs/2026-04-12-viral-shorts-mindhive-design.md`) is the same key; one key covers both read-only discovery and the existing trend_scanner usage.

## Component 3 — Discovery module

New file: `outreach_agent/youtube_discover.py`

### Search strategy (3 passes, ~1.5K API units per run)

**Pass 1 — Seed queries.** 15 queries, 50 results each = 750 candidate channel IDs. Cost: 15 × 100 = 1500 units.

Genre queries (5):
- `"progressive psytrance mix 2026"`
- `"tribal psytrance mix"`
- `"melodic techno mix 2026"`
- `"psytrance promo channel"`
- `"progressive house mix 2026"`

Artist-adjacent queries (10):
- Psytrance: `"Vini Vici"`, `"Astrix"`, `"Symphonix"`, `"Ranji"`, `"Ace Ventura"`
- Progressive/tribal: `"Colyn"`, `"Massano"`, `"Argy"`, `"Anyma"`, `"Rüfüs Du Sol"`

These are the sound-reference artists already declared in `story.py:31` (`sound_refs`). Channels that upload their tracks are by definition aligned to RJM's aesthetic.

**Pass 2 — Channel enrichment.** `channels.list(part='snippet,statistics', id=<batch of 50>)`. Cost: 1 unit per 50 channels = ~15 units for 750 channels.

**Pass 3 — Recency check.** For each qualified channel, `playlistItems.list(playlistId=<uploads_playlist>, maxResults=1)` to get the latest upload date + title. Cost: 1 unit per channel = ~300 units for filtered set.

**Total per run: ~1.8K units.** YouTube Data API v3 default quota is 10K/day → can run 5×/day safely with headroom. Scheduled at 3×/day (08:30, 13:30, 18:30 CET).

### Qualification filter (Python, post-enrichment)

A channel enters the outreach pool if ALL are true:
1. `10_000 <= subscriberCount <= 500_000`
2. `viewCount > 100_000` (total — filters brand-new or abandoned channels)
3. `videoCount >= 20` (minimum catalog depth)
4. `last_upload_date >= today - 30 days`
5. Description OR title contains any of: `psytrance, progressive, tribal, techno, trance, psy, mix, promo, compilation, set, dj set`
6. **NOT an artist-owned channel.** Rejection heuristics:
   - Channel title exactly matches a known artist name (Vini Vici, Astrix, etc. — blocklist)
   - Description contains `official artist channel`, `verified artist`, `my music`, `Topic` (YouTube's auto-generated artist pages)
   - YouTube "Artist" badge on the channel (inferred from `snippet.customUrl` patterns — not 100% reliable, best-effort)

### Email extraction (the 70% coverage path)

For each qualified channel:

1. **Pass 1 — description email regex.**
   Regex: `[\w\.\-+]+@[\w\.\-]+\.\w+`
   Prefer addresses matching `^(business|promo|submissions|contact|demo|music|hello|info)@` over personal-looking ones.

2. **Pass 2 — description URL fallback.**
   Regex for URLs. Skip social-only links (instagram.com, tiktok.com, twitter.com/x.com — login-walled or not useful). Keep:
   - Personal domains
   - Linktree / Beacons / Bio.link (scrapable)

3. **Pass 3 — website scrape.**
   For each surviving URL: HTTP GET `/`, `/contact`, `/about`, `/submit`. User-agent `"RJM Outreach/1.0"`. Timeout 10s. Regex page text for email.

4. **Pass 4 — LinkTree API** (free, official). `https://linktr.ee/<handle>` → `window.__INITIAL_STATE__` JSON. Extract all contact-like links.

5. If still no email: write channel to DB with `email=''`, `status='skip'`, full metadata intact. Tracked but never sent. Phase-2 manual enrichment can revisit.

### Rate limiting & safety

- **Daily API unit budget.** `api_budget` table tracks `units_used` per day. If budget > 8000, discovery aborts immediately. 2K headroom protects against spiky calls.
- **Per-host HTTP rate limit.** Max 5 HTTP requests per host per minute during Pass 3. Prevents hammering a single website.
- **Dedup.** UNIQUE constraint on `youtube_channel_id`. If a channel is re-discovered with the same ID, UPDATE (refresh subs/last_upload) rather than INSERT.
- **Warm-up buffer.** New YouTube contacts are written with `source='agent_discovered'`, which triggers the existing `WARM_UP_DAILY_CAP = 10` gate. Only after graduation (proven non-bouncing) do they join the main send queue.

## Component 4 — Allocator (50% hard floor)

New function in `outreach_agent/run_cycle.py`:

```python
def _weighted_order_with_youtube_floor(contacts, youtube_share=0.5):
    """
    Weighted-order contacts, guaranteeing YouTube contacts get at least
    `youtube_share` of the output whenever supply allows (overflow: when
    YouTube supply is short, others fill the slot).
    """
    # Separate YouTube from others
    yt = [c for c in contacts if c.get("type") == "youtube"]
    others = [c for c in contacts if c.get("type") != "youtube"]
    random.shuffle(yt)

    # Weighted-order others using the existing logic
    by_type = {}
    for c in others:
        by_type.setdefault(c["type"], []).append(c)
    types_sorted = sorted(by_type.keys(),
                          key=lambda t: CONTACT_TYPE_WEIGHTS.get(t, 1),
                          reverse=True)
    ordered_others = []
    for t in types_sorted:
        bucket = by_type[t]
        random.shuffle(bucket)
        ordered_others.extend(bucket)

    # Interleave YouTube and others to hit the floor per batch
    result = []
    total = len(yt) + len(ordered_others)
    yt_used = other_used = 0

    for i in range(total):
        yt_remaining = len(yt) - yt_used
        other_remaining = len(ordered_others) - other_used

        if yt_remaining == 0:
            result.append(ordered_others[other_used]); other_used += 1
        elif other_remaining == 0:
            result.append(yt[yt_used]); yt_used += 1
        else:
            yt_target_so_far = int((i + 1) * youtube_share + 0.5)
            if yt_used < yt_target_so_far:
                result.append(yt[yt_used]); yt_used += 1
            else:
                result.append(ordered_others[other_used]); other_used += 1
    return result
```

The existing `_weighted_order` is replaced by this function inside `cmd_plan()`. All other logic (batch_size, ROI ranking, enrichment, learning context) is unchanged.

**Result**: every batch of 7 gets at least 3–4 YouTube contacts (50% rounded up) **whenever the YouTube queue has supply**. When the queue is empty, curator/podcast fill the slot. Over a full day of 30 cycles × 7 emails = 210 theoretical, capped at 150, YouTube gets a natural 50% floor.

**Note on warm-up**: during the first 2–3 weeks, `WARM_UP_DAILY_CAP = 10` limits new agent-discovered contacts (YouTube + any agent-discovered curators) to 10/day. This is the natural ramp. Only after contacts graduate to `verified` status do they enter the 50%-floor pool. Expected volume curve:

- Week 1–2: ~10 YouTube/day (warm-up cap)
- Week 3–4: ramps as early warm-up contacts graduate
- Week 5+: approaches 50% floor as the verified pool fills

## Component 5 — Email template (youtube_channel_upload)

In `template_engine.py`, the existing `_TYPE_ADDONS["youtube"]` (lines 164–168) is **replaced** with a prompt geared for the upload strategy. The headline value prop is MONEY — keep 100% of the ad revenue — because it's the strongest motivator for a commercial actor deciding whether to spend 10 minutes uploading audio.

```python
"youtube": """CONTEXT: YouTube music-promo channel — ask them to upload our track to their channel.
The pitch is money: they keep 100% of the ad revenue from the upload. Content ID
is DISABLED on these tracks at the distributor — the promise is real.

- Subject formula: "[Track] ([BPM] BPM [genre]) — free track, you keep 100% ad rev"
  Example: "Kavod (140 BPM Hebrew psytrance) — free track, you keep 100% ad rev"
- Opening (2 sentences): name ONE specific recent upload of theirs (genre, vibe).
  Show you watched it, don't flatter generically.
- Offer (3 sentences): ONE track (WAV + artwork). Name BPM, genre, one-word visual.
  Spotify link inline.
- The deal (2 sentences, clear and concrete):
  "You keep 100% of the ad revenue — Content ID is off on this track. I just ask
   you to put my Spotify artist link in the description so listeners can go stream."
- CTA (1 sentence): "Reply 'yes' and I'll send the WAV + artwork within the hour."
- Email length: 80–110 words.
- Signature: Robert-Jan | robertjanmastenbroek.com | 290K IG @holyraveofficial
- NEVER hedge the ad rev promise. NEVER mention BandLab by name. NEVER mention claims.
- The deal is simple: free track → they monetize the upload → RJM gets Spotify streams.
- Faith angle: OFF unless the channel description explicitly says christian/worship/spiritual."""
```

The template engine's existing helpers (`_get_track_recs`, `_inject_spotify_links`, brand gate, sign-off enforcement) work as-is — the addon is just swapped.

**Track recommendation override** for `type='youtube'`: force the psytrance genre path in `_get_track_recs` regardless of the contact's `genre` field, since YouTube channels in our pipeline are filtered to psytrance/tribal/progressive at discovery. This ensures we always pitch Halleluyah / Kavod / Jericho / Renamed / Fire In Our Hands — never Living Water (melodic/accessible, wrong fit for these channels).

## Component 6 — Sign-off hygiene (idempotent)

**Decision**: Keep the original minimal artist sign-off — no CAN-SPAM-style legal footer, no unsubscribe line, no postal address. The email is from an artist's personal Gmail, not a B2B SaaS; a legal footer makes it feel corporate and hurts deliverability more than it helps compliance.

**Change**: refactor the two sign-off injection sites in `template_engine.py` (`_parse_response` and `generate_emails_batch`) to call a single `_ensure_signature()` helper. The helper is **idempotent** — if the body already contains the website URL (strong signal Claude wrote the sign-off), it's returned unchanged instead of appending a duplicate "Robert-Jan\nrobertjanmastenbroek.com" tail. This is a quality improvement to the existing sign-off logic, not a compliance change.

Canonical sign-off (unchanged from before this branch):
```
Robert-Jan
robertjanmastenbroek.com | https://instagram.com/robertjanmastenbroek
```

The existing `reply_classifier.py:191` already handles natural-language unsubscribe requests (intent='unsubscribe' → `add_confirmed_dead_address()` + `status='closed'`), so the opt-out path exists behaviorally without needing to advertise it.

## Component 7 — Track metadata (story.py)

Add Kavod to `TRACKS['psytrance']`:

```python
"psytrance": [
    {"title": "Halleluyah", "bpm": 140, "spotify": "https://open.spotify.com/track/4ysTzCDCezKhxIDOKIV4gG", "notes": "Hebrew lyrics, tribal percussion"},
    {"title": "Kavod",      "bpm": 140, "spotify": "",  "notes": "Hebrew 'glory', driving psytrance"},
    {"title": "Jericho",    "bpm": 140, "spotify": "https://open.spotify.com/track/2M7cL3KynPGzE1DonuldrN", "notes": "Hebrew lyrics, heavy psytrance energy"},
],
```

`_get_track_recs` is modified to **skip tracks with empty spotify URLs** (since the brand rule requires Spotify URL + track name together). Kavod will automatically activate the moment RJM pastes the URL into story.py — no code changes needed. A TODO comment at the top of the TRACKS dict flags this.

## Component 8 — Config changes

Add to `config.py`:

```python
# ─── YouTube Outreach Branch ─────────────────────────────────────────────────
YOUTUBE_SHARE_FLOOR      = 0.5     # 50% of each batch reserved for YouTube
YOUTUBE_API_DAILY_UNITS_CAP = 8000 # abort discovery if > this many units used today
YOUTUBE_DISCOVERY_QUERIES = [
    # Genre seeds
    "progressive psytrance mix 2026",
    "tribal psytrance mix",
    "melodic techno mix 2026",
    "psytrance promo channel",
    "progressive house mix 2026",
    # Sound-reference artists (from story.py:31 sound_refs)
    "Vini Vici", "Astrix", "Symphonix", "Ranji", "Ace Ventura",
    "Colyn", "Massano", "Argy", "Anyma", "Rüfüs Du Sol",
]
YOUTUBE_MIN_SUBS         = 10_000
YOUTUBE_MAX_SUBS         = 500_000
YOUTUBE_MIN_TOTAL_VIEWS  = 100_000
YOUTUBE_MIN_VIDEO_COUNT  = 20
YOUTUBE_MAX_UPLOAD_AGE_DAYS = 30
YOUTUBE_ARTIST_CHANNEL_BLOCKLIST = {
    "vini vici", "astrix", "symphonix", "ranji", "ace ventura",
    "colyn", "massano", "argy", "anyma", "rufus du sol",
    "robert-jan mastenbroek",  # don't re-discover RJM's own channel
}
```

Update `CONTACT_TYPE_WEIGHTS`:

```python
CONTACT_TYPE_WEIGHTS = {
    "curator":        40,
    "podcast":        60,
    "youtube":        50,  # ACTIVATED — the hard-floor allocator enforces ≥50% share regardless
    "sync":           0,
    "booking_agent":  0,
    "wellness":       0,
}
```

The weight value (50) is only used for the non-YouTube types' relative sort. The hard-floor allocator is what enforces the 50% share — so the value doesn't need to be "100" or special.

## Component 9 — CLI commands

New subcommand group in `rjm.py`:

```
python3 rjm.py youtube discover         # Run one discovery cycle
python3 rjm.py youtube status           # Show YouTube-type pipeline counts
python3 rjm.py youtube budget           # Show today's API unit usage
```

Outreach send is unchanged — `python3 rjm.py outreach run` picks up YouTube contacts via the new allocator automatically.

## Phase 2 (flagged, not in scope)

- Upload-detection loop: for each `status='sent'` YouTube contact older than 7 days, search the channel's recent uploads for RJM track titles. If found, update `status='uploaded'`, log the view count and upload date. Measures real ROI.
- Linkfire/Songlink smart-links so we can add Apple Music, Deezer, YouTube Music, SoundCloud in one URL.
- Instagram Story reciprocity automation — auto-post when a channel uploads one of RJM's tracks.
- Paid placements (for channels with >500K subs who ask).

## File changes summary

**New files:**
- `outreach_agent/youtube_auth.py`
- `outreach_agent/youtube_discover.py`
- `docs/superpowers/specs/2026-04-14-youtube-outreach-branch-design.md` (this file)
- `docs/superpowers/plans/2026-04-14-youtube-outreach-branch-plan.md`

**Modified files:**
- `outreach_agent/db.py` — schema migration
- `outreach_agent/config.py` — YOUTUBE_* constants + activate youtube weight
- `outreach_agent/story.py` — add Kavod placeholder
- `outreach_agent/template_engine.py` — rewrite youtube addon + CAN-SPAM footer
- `outreach_agent/run_cycle.py` — YouTube floor allocator
- `rjm.py` — youtube subcommand group

## Open questions the user still owns

1. **Kavod Spotify URL.** I'll add Kavod with an empty URL placeholder. User pastes the URL once; no code changes needed.
2. **Tenerife postal address for CAN-SPAM footer.** I'll use `"Tenerife, Canary Islands, Spain"` — sufficient for CAN-SPAM (city-level is acceptable). User can refine to a street address if they want stronger compliance.
3. **Content ID claim-release workflow.** The cold email promises nothing ad-rev-wise. If a VIP channel replies asking for ad rev, RJM handles the BandLab dispute manually. No automation in this spec.

## Success criteria

1. `python3 rjm.py youtube discover` runs end-to-end without errors, writes ≥20 qualified channels to the DB with emails extracted in a single run.
2. `python3 rjm.py outreach plan` (dry run) shows the next batch contains ≥50% YouTube contacts when the YouTube queue has supply.
3. A dry-run generated email for a YouTube contact contains: specific recent-upload reference, track recommendation with Spotify URL, explicit "100% ad revenue" promise, Spotify-link-in-description ask, unsubscribe footer, Tenerife address.
4. Existing curator/podcast send behavior is unchanged when the YouTube queue is empty.
5. All existing tests (if any) still pass.
6. **Pre-requisite check**: before the first outreach cycle is run against YouTube contacts, RJM has confirmed Content ID is disabled on Halleluyah, Kavod, Jericho, Renamed, Fire In Our Hands in BandLab. (Manual, one-time.)
