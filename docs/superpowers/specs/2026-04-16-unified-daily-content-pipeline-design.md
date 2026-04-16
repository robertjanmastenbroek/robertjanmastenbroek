# Unified Holy Rave Daily Content Pipeline — 10/10 Design Spec

**Date:** 2026-04-16
**Author:** Claude Opus 4.6 + Robert-Jan Mastenbroek
**Status:** Approved
**North Star:** 1,000,000 Spotify monthly listeners

---

## 1. Problem Statement

Two content creation systems exist in parallel:

- **System 1 (`content_engine/`):** Visual-first pipeline, native API distribution (IG + FB + YT), trend scanner, learning loop with metrics. Missing: TikTok, Stories, hook library, BPM beat-sync, proper clip lengths.
- **System 2 (`outreach_agent/post_today.py` + `processor.py` + `generator.py`):** Audio-first pipeline, BPM detection, beat-sync, viral hook library, brand gate, Buffer distribution. Missing: learning loop, trend scanner, native APIs, Facebook.

Neither is complete. The daily output is 6 clips/day on 2-3 platforms with 100-852 views, 0 saves, monotone "tension" hooks, and zero hook/visual diversity. No transitional hooks. No Stories. No Spotify funnel.

## 2. Goal

One unified system that ships 18 posts/day (3 clips x 6 targets), uses three distinct clip formats optimized for different engagement levers, learns what works per format/platform/hook/track, and drives saves that convert to Spotify streams.

## 3. Architecture

### 3.1 Single Entry Point

`rjm.py content viral` calls `content_engine/pipeline.py`. Legacy `rjm.py content` (no subcommand) becomes an alias. No other content command exists.

### 3.2 Module Migration

Move into `content_engine/`:

| From | To | What |
|------|----|------|
| `outreach_agent/viral_hook_library.py` | `content_engine/hook_library.py` | All hook templates (existing 21 + new 25 music save-drivers + transitional bank) |
| `outreach_agent/brand_gate.py` | `content_engine/brand_gate.py` | 5-test validation + hard-ban rules |
| `outreach_agent/generator.py` (functions: `generate_run_hooks`, `generate_run_captions`, `_fill_templates_with_claude`, `_call_claude`, `TRACK_FACTS`, `ANGLE_INSTRUCTIONS`) | `content_engine/generator.py` | Claude-driven slot filling, sub-mode selection. Email-related functions in generator.py (if any) stay in outreach_agent/. |
| `outreach_agent/processor.py` (video rendering) | `content_engine/renderer.py` | ffmpeg pipeline, text overlay, platform color grading |
| `outreach_agent/post_today.py` (audio parts) | `content_engine/audio_engine.py` | Track selection, BPM detection, onset mapping, beat-sync |

Stays in `outreach_agent/`:
- `buffer_poster.py` (shared with email outreach; imported by `content_engine/distributor.py`)
- `post_today.py` stripped to audio utility functions only (or deprecated if fully absorbed)

### 3.3 Existing `content_engine/` Modules (Upgraded)

| Module | Changes |
|--------|---------|
| `pipeline.py` | Orchestrates 3 format types, 6 distribution targets, pre-render validation |
| `distributor.py` | +IG Stories, +FB Stories, +TikTok via Buffer, retry with backoff, circuit breaker |
| `learning_loop.py` | Per-format, per-platform, per-template, per-track learning. Breakthrough fix. |
| `trend_scanner.py` | No changes (works correctly) |
| `footage_scorer.py` | +Transitional hook category matching |
| `types.py` | +New dataclasses for formats, transitional hooks, expanded PerformanceRecord |
| `spotify_watcher.py` | +New release detection, +track popularity, +audio features extraction |

### 3.4 New Modules

| Module | Purpose |
|--------|---------|
| `content_engine/audio_engine.py` | Track selection, BPM detection, librosa onset mapping, beat-sync segment extraction |
| `content_engine/renderer.py` | All ffmpeg rendering: 3 format types, text overlays, color grading, Stories CTA, output validation |
| `content_engine/generator.py` | Claude-driven hook filling with sub-mode diversity, caption generation |
| `content_engine/hook_library.py` | Unified hook bank: transitional visual hooks + text hook templates (save-drivers + existing) |
| `content_engine/brand_gate.py` | 5-test + hard-ban validation (moved from outreach_agent/) |

### 3.5 Deleted After Merge

- Legacy `rjm.py content` code path (the no-subcommand handler)
- Duplicate video assembly logic in `outreach_agent/post_today.py`
- `content_engine/visual_engine.py` (Runway ML integration removed; 100% real footage)
- `content_engine/assembler.py` (replaced by `renderer.py` which consolidates assembler + processor)

## 4. Daily Output

### 4.1 Three Clip Formats

**Clip 1 — Transitional Hook (15-28s total)**
- 3-7s pre-cleared bait clip (muted) hard-cuts into Holy Rave content
- Track audio plays from frame 0 over entire video (Shazam-identifiable from second 1)
- Text overlay on bait portion: music save-driver hook (Category 1 or 2)
- Bait clip sourced from `content/hooks/transitional/` library
- Content portion: b-roll or phone footage, beat-synced to track

**Clip 2 — Emotional/POV Text Hook (7s)**
- Short, punchy, identity-matching text hook is the star
- Conditional emotional triggers or POV scene-setters
- Cinematic b-roll or phone footage behind text
- Drives saves through "I'll want to feel this again" psychology

**Clip 3 — Performance Energy (28s)**
- Music carries it. Minimal text (track + artist, or "wait for the drop")
- Performance, crowd, DJ booth, or high-energy footage
- Beat-synced multi-segment cuts
- Drives shares through energy and FOMO

### 4.2 Six Distribution Targets Per Clip

| # | Target | Method | Notes |
|---|--------|--------|-------|
| 1 | Instagram Reel | Native Graph API v21 | Primary platform |
| 2 | YouTube Short | Native Data API v3 | Scheduled via publishAt |
| 3 | Facebook Reel | Native Graph API v21 | Page token exchange |
| 4 | TikTok | Buffer API | Only Buffer; native API dropped |
| 5 | Instagram Story | Native Graph API v21 | media_type=STORIES + Spotify link sticker |
| 6 | Facebook Story | Native Graph API v21 | /{page-id}/stories endpoint |

**Total: 3 clips x 6 targets = 18 posts/day**

### 4.3 Staggered Schedule (CET)

| Time | Clip | Format |
|------|------|--------|
| 09:00 | Clip 1 | Transitional hook |
| 13:00 | Clip 2 | Emotional/POV text hook |
| 19:00 | Clip 3 | Performance energy |

## 5. Audio Engine

### 5.1 Audio-First Approach

For Clips 2 and 3, audio is selected first. Visual segments are beat-synced to the track.

Pipeline:
1. Select track from pool (weighted by Spotify popularity + video save rate)
2. Load WAV via librosa
3. Detect BPM (librosa.beat.beat_track)
4. Map onset strength envelope (librosa.onset.onset_strength)
5. Find top N high-energy windows matching clip duration
6. Extract segment timestamps for beat-synced video cuts

For Clip 1 (transitional), track audio plays from 0:00 but the bait clip's visual is unrelated. Audio segment selection still matters for the post-cut content portion.

### 5.2 Track Pool + Auto-Rotation

**Pool size:** 4-6 tracks at any time.

**Current pool (top 4 by save rate):** Configured in `audio_engine.py`, seeded from current TRACK_FACTS.

**Auto-rotation via Spotify:**
1. `spotify_watcher.py` checks `/v1/artists/{id}/albums?include_groups=single` daily
2. New release detected → enters pool at weight 1.0
3. Audio features auto-extracted via `/v1/audio-features/{id}` (BPM, energy, danceability)
4. After 7 days, learning loop compares new track's video save rate + Spotify popularity vs bottom of pool
5. If new track wins → swap. If not → remove from pool (unless manually pinned)
6. Pool always contains at least 4 tracks

**Track facts auto-populated:**
- BPM from Spotify audio features (no more hardcoded)
- Scripture anchor from TRACK_FACTS table (manual, artist-curated — cannot be automated)
- Energy/danceability from Spotify → influences which clip format the track is assigned to

### 5.3 Spotify Premium Integration

Extends `spotify_watcher.py`:

| Endpoint | Data | Use |
|----------|------|-----|
| `/v1/artists/{id}/albums?include_groups=single` | New releases | Auto-detect weekly drops |
| `/v1/tracks/{id}` | Popularity 0-100 | Daily tracking, rotation decisions |
| `/v1/audio-features/{id}` | BPM, energy, danceability, valence | Auto-populate track facts |
| `/v1/artists/{id}` | Follower count | Daily delta (existing) |

**Not available via API (stays manual):** Monthly listener count. Only accessible in Spotify for Artists dashboard.

**Auth flow:** Authorization Code Flow with PKCE. Scopes: `user-read-private`, `user-library-read`. Refresh token stored in `.env` as `SPOTIFY_USER_REFRESH_TOKEN`.

## 6. Hook Generation

### 6.1 Three-Tier Hook System

**Tier 1 — Transitional Visual Hooks (Clip 1)**

Local library at `content/hooks/transitional/` with subdirectories by category:
- `nature/` — lightning, waves, aurora, time-lapse
- `satisfying/` — paint pouring, pressure washing, soap cutting
- `elemental/` — slow-mo water, fire sparks, rain on glass
- `sports/` — skydiving POV, surfing barrel, mountain biking
- `craftsmanship/` — glassblowing, pottery, calligraphy
- `illusion/` — forced perspective, impossible objects

Index file: `content/hooks/transitional/index.json`
```json
[
  {
    "file": "nature/lightning_strike_01.mp4",
    "category": "nature",
    "duration_s": 3.2,
    "last_used": null,
    "performance_score": 1.0,
    "times_used": 0
  }
]
```

Selection rules:
- Weighted random by performance_score
- 7-day cooldown (never reuse within a week)
- Category diversity: don't pick same category 2 days in a row
- Learning loop updates performance_score based on Clip 1's completion rate

Text overlay on the bait portion: a music save-driver hook from Tier 2 bank (the text hook and the visual hook are independent — mix and match).

**Tier 2 — Music Save-Driver Text Hooks (Clip 2, also used on Clip 1)**

25 new templates added to `hook_library.py`, organized by save-driver category:

**Conditional Emotional Triggers (highest save rate):**
- `save.if_heartbroken` — "If you've had your heart broken, don't listen to this song..."
- `save.for_you_if` — "This song is for you if you {specific_emotional_state}"
- `save.vibes_were_song` — "If {season_mood} vibes were a song, it would be this one"
- `save.feeling_recently` — "If you've been feeling {emotion} recently, this song might help"
- `save.anyone_ever_felt` — "For anyone who's ever felt {state}, this song's for you"

**POV Scene-Setters (high saves + shares):**
- `save.pov_listening` — "POV: you're listening to a new track from a {age}-year-old {location} musician"
- `save.imagine_moment` — "Ok but imagine it's {season}, you're {activity}, and this song starts playing..."
- `save.pov_discovered` — "POV: you just discovered your new favourite artist"
- `save.pov_last_song` — "POV: this is the last song of the set and nobody wants to leave"
- `save.pov_driving` — "POV: {time_of_day}, windows down, this on repeat"

**Social Proof (saves + follows):**
- `save.about_to_blow` — "This artist is about to blow up and you heard it here first"
- `save.imagine_opening` — "Imagine {reference_artist} opening their set with this"
- `save.friend_said` — "My friend said this sounds like {reference_artist} meets {reference_artist}"

**Direct Meaning (deep engagement + saves):**
- `save.your_sign` — "This song is your sign to {action}"
- `save.what_it_felt` — "This song is exactly what it felt like {experience}"
- `save.hidden_message` — "Did you catch the hidden message in this?"

**Challenge/Dare (completion rate + shares):**
- `save.bet_you_cant` — "Bet you can't get through this drop without {reaction}"
- `save.dare_listen` — "I dare you to listen without {physical_response}"
- `save.wait_for_drop` — "Wait for the drop. Just wait."

**Conversion (Spotify pipeline):**
- `save.song_name_go` — "Song: {title}. Now go find it."
- `save.turn_up_11` — "This is the song you'll want to turn up to 11..."

Additional templates to fill gaps found during implementation. Target: 20-25 total save-driver templates. The exact count is flexible; quality and brand-gate compliance matter more than hitting 25.

Each template is a `HookTemplate` dataclass with: id, angle, mechanism, template, slots, example_fill, source_credit, priority, tags. Same structure as existing library.

**Tier 3 — Performance Hooks (Clip 3)**

Minimal text, music-forward. Subset of existing body-drop templates + new minimal templates:
- `perf.wait_drop` — "Wait for the drop."
- `perf.track_artist` — "{track_title} — {artist_name}"
- `perf.turn_volume` — "Turn your volume up for this one."
- `perf.front_row` — "Watch the front row at {timestamp}"

### 6.2 Sub-Mode Diversity (Fix)

`generator.py` has 5 sub-modes per angle documented but never executed. Fix:

1. When picking a template, also pick a sub-mode (weighted random)
2. Include sub-mode instruction in the Claude prompt: "Fill these slots in the {sub_mode} register"
3. Track sub-mode in post registry for learning loop

Sub-modes per angle (from existing code):
- Emotional: COST, NAMING, DOUBT, DEVOTION, RUPTURE
- Signal: FINDER, PERMISSION, RECOGNITION, SEASON, UNSAID
- Energy: BODY, TIME, GEOGRAPHY, THRESHOLD, DISSOLUTION

### 6.3 Brand Gate (Moved + Unchanged)

Five tests: Visualization, Falsifiability, Uniqueness, One Mississippi, Point A-B.
Hard-ban openers and phrases unchanged.
Score >= 3 + no hard fail = passes.

Gate runs on every hook before it ships. Rejected hooks fall back to template's curated `example_fill`. Emergency fallback ships unvetted `example_fill` (existing behavior, acceptable).

## 7. Renderer

### 7.1 Consolidation

`renderer.py` replaces both `assembler.py` and `processor.py`. Single module, three render paths:

### 7.2 Transitional Hook Render (Clip 1)

```
[bait_clip (3-7s, muted)] [hard_cut] [content_segments (beat-synced, 8-21s)]
[--- track audio from 0:00 across entire timeline ---]
[text overlay on bait portion: save-driver hook text]
[text overlay on content portion: track + artist (small, lower third)]
```

ffmpeg pipeline:
1. Strip audio from bait clip
2. Beat-sync content segments (reuse audio_engine onset mapping)
3. Concat: bait + content (no transition, hard cut)
4. Overlay track audio from 0:00
5. Burn text overlays with timing (bait text: 0s to cut point; content text: cut+1s to end-1s)
6. Platform color grade
7. h264 encode (CRF 22, fast preset)

### 7.3 Emotional/POV Render (Clip 2)

```
[content_segments (single or multi, 7s total)]
[--- track audio, high-energy segment ---]
[PROMINENT text overlay: save-driver hook (68px, Bebas Neue, white + shadow)]
```

Text appears at 0.0s, fades out at duration - 1.0s. Two-part hooks split at 55% mark: first line visible immediately, second line fades in at midpoint.

### 7.4 Performance Energy Render (Clip 3)

```
[multi-segment performance footage (beat-synced, 28s)]
[--- track audio, peak energy section ---]
[minimal text: track + artist OR "wait for the drop" (smaller font, upper/lower third)]
```

Emphasis on motion and energy. More segments, faster cuts synced to BPM. Phone footage gets grain filter. Performance footage gets slight contrast boost.

### 7.5 Stories Render (All Clips)

Same clip, plus:
- Spotify CTA overlay in bottom 15%: hardcoded "Listen on Spotify" text + swipe-up arrow icon + Spotify track URL (from track pool metadata). Not Claude-generated.
- Rendered as a separate output file: `{clip_name}_story.mp4`
- No additional cropping needed (already 9:16)

### 7.6 Output Validation

After every render, ffprobe check:
- Valid MP4 container
- Duration within +/- 1.5s of target
- Resolution 1080x1920
- Audio stream present
- File size > 100KB (catches empty/corrupt renders)

Fail = log error, skip distribution for that clip, continue with others.

## 8. Distribution

### 8.1 Platform Matrix

| Platform | API | Auth | Notes |
|----------|-----|------|-------|
| Instagram Reel | Graph API v21 | Long-lived token, auto-refresh | Primary. Upload → poll FINISHED → publish |
| YouTube Short | Data API v3 | OAuth2 refresh via client_secret.json | Resumable upload. publishAt for scheduling |
| Facebook Reel | Graph API v21 | Page token (exchanged from user token) | 3-phase resumable upload |
| TikTok | Buffer API | Buffer OAuth | buffer_poster.upload_video_and_queue() |
| Instagram Story | Graph API v21 | Same token as Reel | media_type=STORIES, link sticker for Spotify |
| Facebook Story | Graph API v21 | Same page token as Reel | /{page-id}/stories endpoint |

### 8.2 Retry + Circuit Breaker

- 3 retries per platform per clip, exponential backoff (2s, 8s, 32s)
- Buffer is final fallback for Reel failures (not for Stories — no Buffer equivalent)
- If a platform fails 3 consecutive days → pause + log alert. Resume on manual `rjm.py content reset-platform {name}`
- Per-clip: if render failed, skip all 6 targets for that clip. Don't distribute broken video.

### 8.3 Dry-Run Fix

`--dry-run` flag now:
- Runs full render pipeline (so you can inspect output clips)
- Writes registry to `data/performance/dry-run/` (not main dir)
- Skips all distribution
- Learning loop ignores dry-run registries

## 9. Learning Loop

### 9.1 Dimensions Tracked

Every post in the registry now includes:

```json
{
  "post_id": "...",
  "platform": "instagram",
  "format_type": "transitional",
  "clip_index": 0,
  "hook_template_id": "save.if_heartbroken",
  "hook_sub_mode": "DEVOTION",
  "visual_type": "b_roll",
  "transitional_category": "nature",
  "transitional_file": "nature/lightning_01.mp4",
  "track_title": "Jericho",
  "clip_length": 22,
  "posted_at": "2026-04-16T09:00:00"
}
```

### 9.2 Weight Updates (18:00 Daily)

Signal formula (unchanged): `completion_rate * 0.5 + save_rate * 0.3 + scroll_stop_rate * 0.2`

Updated weights:
- `hook_weights` — per template_id EMA (alpha=0.3)
- `visual_weights` — per visual_type EMA
- `format_weights` — per format_type (transitional / emotional / performance) EMA
- `platform_weights` — per platform EMA (informs "best_platform" per format)
- `transitional_category_weights` — per bait category EMA
- `track_weights` — per track_title, combines Spotify popularity + video save rate

### 9.3 Track Rotation Voting

Daily at 18:00:
1. Fetch latest Spotify popularity for all pool tracks
2. Compute composite score: `spotify_popularity * 0.4 + video_save_rate * 0.6`
3. If new weekly release has been in pool >= 7 days and scores below pool bottom → remove
4. If new weekly release scores above pool bottom → swap

### 9.4 Breakthrough Analysis Fix

Current: "claude CLI unavailable" because subprocess path is wrong at 18:00 runtime.
Fix: Use same Claude subprocess pattern as `trend_scanner.py` (explicit path resolution, /tmp working dir, --no-session-persistence).

### 9.5 Template Lifecycle

- New templates enter at priority 1.0
- After 14 days with data: if template's EMA score < 0.5 (bottom quartile) → priority drops to 0.3
- If template's EMA score > 1.5 (top quartile) → priority boosts to 2.0
- Templates with priority < 0.3 after 30 days → deprecated (removed from active rotation, kept in code)

## 10. Daily Schedule

| Time (CET) | Command | What |
|------------|---------|------|
| 06:00 | `rjm.py content trend-scan` | Trend scanner + Spotify new release check |
| 08:00 | `rjm.py content viral` | Full pipeline: 3 clips rendered, 18 posts distributed (staggered 09:00/13:00/19:00) |
| 18:00 | `rjm.py content learning` | Fetch metrics from all platforms, update all weights, track rotation, breakthrough analysis |

Launchd plists: existing 3 plists updated, no new plists needed.

## 11. Transitional Hook Bootstrap

Before first run, populate `content/hooks/transitional/`:

1. Create directory structure: `content/hooks/transitional/{nature,satisfying,elemental,sports,craftsmanship,illusion}/`
2. Download 30-50 free pre-cleared clips from free tiers of VideoHooks.app, TransitionalHooks.com, AISEO transitional hooks
3. All clips: 1080x1920 (9:16), 2-7 seconds, MP4/MOV
4. Create `index.json` with metadata per clip
5. Initial performance_score = 1.0 for all (learning loop updates from day 1)

Category selection for downloads: prioritize nature + elemental + satisfying (best brand alignment with Dark/Holy/Futuristic).

## 12. NOT in Scope

- Runway ML / AI-generated video (removed entirely)
- Email outreach system changes (`outreach_agent/agent.py` untouched)
- Trend scanner data source changes (works correctly)
- New launchd plists (existing 3 cover the schedule)
- Spotify monthly listener auto-tracking (not exposed by API)
- Changing the brand gate rules or hard-ban lists
- Creating new audio tracks or sourcing new performance footage (asset bootstrap is manual)

## 13. Success Criteria

| Metric | Current | Target (14 days) | Target (30 days) |
|--------|---------|-------------------|-------------------|
| Posts shipped/day | 6 | 18 | 18 |
| Platforms | 2-3 | 6 | 6 |
| Hook variety (unique templates/week) | 1 | 10+ | 15+ |
| Visual variety (format types/day) | 1 | 3 | 3 |
| Instagram save rate | 0% | >1% | >3% |
| Avg views per clip | 100-852 | 500+ | 2000+ |
| Track pool auto-rotation | Manual | Automated | Automated |
| Learning loop functional | Partial | Full (all dimensions) | Full + template lifecycle |

## 14. Risk Register

| Risk | Mitigation |
|------|------------|
| Instagram token expires mid-day | Auto-refresh already implemented; add pre-flight check at 08:00 |
| Buffer rate limits for TikTok | Max 3 posts/day to TikTok (within Buffer free tier) |
| Transitional hook clips run out (30-50 with 7-day cooldown) | 50 clips / 7 days = 7 unique per week. Need minimum 7. Download more if pool shrinks below 14. |
| Claude CLI unavailable at runtime | Fallback to example_fill (existing). Fix subprocess path in all modules. |
| All 3 clips fail render | Circuit breaker: if 0/3 clips render, log critical alert, do not distribute stubs |
| Spotify API rate limits | 30 req/min for client credentials. Daily check = ~5 requests. No risk. |
| Facebook Story API changes | Monitor Graph API changelog. FB Stories is newer API — may shift. |
