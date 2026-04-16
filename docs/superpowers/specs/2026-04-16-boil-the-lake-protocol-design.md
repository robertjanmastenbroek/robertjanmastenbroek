# Boil the Lake Protocol — Design Spec

**Date:** 2026-04-16
**Author:** Claude (brainstorming session with RJM)
**Status:** Draft — awaiting user review
**Scope:** Autonomous self-improving growth system for 1M Spotify monthly listeners

---

## 1. Problem Statement

RJM has 325 Spotify monthly listeners. The target is 1,000,000. The existing agent fleet runs a fixed playbook — same schedules, same channels, same tactics. It executes but doesn't learn at the strategic level, doesn't discover new channels, doesn't reallocate resources toward what's working, and doesn't earn its own budget.

The fleet needs to become an autonomous growth machine that:
- Continuously tests new growth tactics (free and self-funded)
- Learns from results at three levels (tactical, strategic, discovery)
- Reallocates resources toward what actually drives listeners
- Funds its own paid experiments via the offering page
- Gets measurably better every week
- Operates within brand, legal, and ethical guardrails

## 2. Design Decisions (from brainstorming)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Autonomy model | **Propose-and-execute with 24hr veto** | RJM reviews daily digest; system auto-executes if no veto |
| Channel scope | **All free methods + self-funded paid** | Free channels prioritized by expected ROI; paid unlocked via 50% of offering page donations |
| Measurement | **System decides its own intermediate metrics** | North Star = Spotify monthly listeners; system determines which signals correlate and adjusts scoring weights |
| Learning layers | **All three from day one** | L1 tactical optimization + L2 strategic reallocation + L3 discovery/invention — simultaneous |
| Offering page | **System can modify** | Full latitude to A/B test copy, layout, CTAs — must stay truthful and on-brand |
| Payment tracking | **Stripe API** | Daily poll for donation amounts; 50% allocated to growth budget |

## 3. Architecture Overview

The Boil the Lake (BTL) protocol is an **additive layer** on top of the existing fleet. Nothing gets replaced — the existing master agent, outreach pipeline, content engine, and discover/research agents continue running. BTL wraps them with strategic intelligence.

```
                    ┌─────────────────────────────────┐
                    │        GROWTH BRAIN              │
                    │   (growth_brain.py — new)        │
                    │                                  │
                    │  ┌───────┐ ┌───────┐ ┌────────┐ │
                    │  │Layer 1│ │Layer 2│ │Layer 3 │ │
                    │  │Tactical│ │Strat. │ │Discover│ │
                    │  │Optimize│ │Realloc│ │+Invent │ │
                    │  └───┬───┘ └───┬───┘ └───┬────┘ │
                    │      │         │         │      │
                    │  ┌───┴─────────┴─────────┴───┐  │
                    │  │    EXPERIMENT ENGINE        │  │
                    │  │  (experiment_engine.py)     │  │
                    │  └────────────┬───────────────┘  │
                    │               │                   │
                    │  ┌────────────┴───────────────┐  │
                    │  │     VETO SYSTEM             │  │
                    │  │  (veto_system.py)           │  │
                    │  └────────────┬───────────────┘  │
                    │               │                   │
                    │  ┌────────────┴───────────────┐  │
                    │  │   REVENUE TRACKER           │  │
                    │  │  (revenue_tracker.py)       │  │
                    │  └───────────────────────────┘  │
                    └──────────────┬────────────────────┘
                                   │
                    ┌──────────────┴────────────────────┐
                    │       EXISTING FLEET               │
                    │                                    │
                    │  rjm-master (8x/day)               │
                    │  holy-rave-daily-run (daily)        │
                    │  rjm-outreach-agent (every 30min)  │
                    │  rjm-discover (6x/day)             │
                    │  rjm-research (6x/day)             │
                    │  holy-rave-weekly-report (weekly)   │
                    │                                    │
                    │  content_engine/ (5 modules)       │
                    │  outreach_agent/ (full pipeline)   │
                    │  events.py (event backbone)        │
                    │  fleet_state.py (heartbeats)       │
                    └────────────────────────────────────┘
```

## 4. The Experiment Framework

Everything the system tries beyond its existing playbook is an **Experiment**. This is the atomic unit of self-improvement.

### 4.1 Experiment Schema

```python
{
    "id": "exp_2026-04-17_reddit_melodic",       # auto-generated
    "channel": "reddit",                          # from channel registry
    "hypothesis": "Posting track previews in r/melodictechno with behind-the-scenes context will drive 50+ Spotify profile visits per post",
    "tactic": "Post 2x/week in r/melodictechno with 30s preview + production story",
    "cost_type": "free",                          # free | self_funded
    "cost_estimate": 0,                           # EUR, 0 for free
    "expected_impact": {
        "metric": "spotify_profile_visits",
        "target": 50,
        "unit": "per_post",
        "confidence": 0.6                         # system's confidence in hypothesis
    },
    "duration_days": 21,                          # minimum viable test period
    "success_criteria": "50+ profile visits per post OR 10+ new monthly listeners attributable to Reddit referral within 21 days",
    "failure_criteria": "<10 profile visits per post after 6 posts",
    "guardrails": [
        "Max 2 posts/week per subreddit (anti-spam)",
        "No self-promo in rules-enforced subs",
        "All posts pass Compass Test + 5 brand tests"
    ],
    "status": "proposed",                         # proposed → active → completed → analyzed
    "proposed_at": "2026-04-17T08:00:00+01:00",
    "execute_after": "2026-04-18T08:00:00+01:00", # 24hr veto window
    "started_at": null,
    "ended_at": null,
    "result": null,                               # success | failure | inconclusive
    "learning": null,                             # what did we learn
    "metrics_log": []                             # periodic measurements during experiment
}
```

### 4.2 Experiment Lifecycle

```
PROPOSED ──24hr veto──→ ACTIVE ──duration──→ COMPLETED ──analysis──→ ANALYZED
    │                      │                                            │
    ↓                      ↓                                            ↓
  VETOED              PAUSED (if budget runs out            LEARNING recorded
                      or guardrail triggered)               Channel weight updated
                                                            Next experiment proposed
```

**Proposal → Experiment relationship:** A Proposal (in `proposals` table) is the veto-queue entry. When a proposal's `execute_after` timestamp passes without a veto, the veto_system creates a corresponding Experiment (in `experiments` table) and sets the proposal status to `executed`. The proposal is the approval gate; the experiment is the execution record. One proposal creates exactly one experiment.

### 4.3 Experiment Limits

To prevent chaos, the system enforces:
- **Max 5 concurrent experiments** (across all channels)
- **Min 7 days** per experiment (no knee-jerk kills)
- **Max 28 days** per experiment (no zombie experiments)
- **Fair trial rule:** At least 6 data points before judging (e.g., 6 posts, 6 emails, 6 days of ads)
- **One experiment per channel** at a time (clean attribution)
- **Budget hard cap:** Never spend more than the available 50% allocation

## 5. The Three Learning Layers

### 5.1 Layer 1 — Tactical Optimization (runs 4x/day)

Extends the existing multi-armed bandit framework to ALL channels. Each channel gets its own bandit instance.

**Existing bandits (already built, extend):**
- Content bandit: hook_mechanism, visual_type, clip_length, platform, posting_time
- Email bandit: template_style (via learning.py)

**New bandits to create:**
| Channel | Arms | Reward Signal |
|---------|------|--------------|
| Outreach | subject_pattern, personalization_depth, follow_up_timing, track_pitched, time_of_day | reply_rate weighted by outcome (playlist_add > positive_reply > any_reply) |
| Reddit | subreddit, post_type (preview/story/discussion), time_of_day, title_style | upvotes + comments + spotify_referral_clicks |
| Playlist pitch | pitch_angle, track_selected, playlist_size_tier, curator_type | add_rate, listener_lift_after_add |
| Offering page | headline_variant, CTA_text, page_layout, social_proof_element | donation_conversion_rate, average_donation_amount |
| YouTube long-form | mix_length, title_style, thumbnail_style, description_keywords | watch_time, sub_rate, spotify_link_clicks |

**Bandit configuration (consistent across all):**
- Algorithm: Thompson Sampling
- Window: 28 days rolling
- Cold-start: minimum 5 samples per arm before exploitation
- Exploration rate: 0.20 (cold, <20 samples), 0.10 (warm, >=20 samples)
- Outlier detection: 2x rolling mean triggers breakthrough analysis

**How L1 runs:**
1. Before each action (send email, post content, publish reddit post), the relevant bandit selects arm values
2. After the action, the outcome is logged with arm values
3. 4x/day, all bandits recalculate weights from their rolling windows
4. Breakthrough detector flags any arm value performing >2x mean — triggers Claude analysis to understand why

### 5.2 Layer 2 — Strategic Reallocation (runs 1x/week, Sundays 20:00 CET)

The portfolio manager. Reviews all channels and shifts resources toward winners.

**What it reallocates:**
- **Agent cadence** — adjust batch sizes and per-run limits (e.g., outreach sends 7→12 emails per run, or discover evaluates 20→30 candidates). Cron schedules stay fixed; throughput per run changes.
- **Email volume per contact type** — shift CONTACT_TYPE_WEIGHTS in config.py (curator vs podcast vs blog vs label)
- **Content posting frequency per platform** — prioritize platforms where completion rates are highest
- **Discovery query allocation** — which types of contacts to hunt (shift search slots toward types with best reply rates)
- **Budget allocation across paid channels** — when self-funded, distribute EUR toward highest-ROI paid channel

**The reallocation algorithm:**

```
For each active channel:
  1. Calculate "Listener Equivalent Impact" (LEI) this week:
     - Direct: Spotify listeners gained attributable to this channel
     - Indirect: proxy signals * estimated conversion rate
       (e.g., 100 Reddit upvotes * 0.02 est. conversion = 2 LEI)
  
  2. Calculate "Cost" this week:
     - Free channels: agent-hours (estimated from run count * avg duration)
     - Paid channels: EUR spent
  
  3. Calculate ROI = LEI / Cost
  
  4. New allocation weight = current_weight * (1 + learning_rate * (ROI - mean_ROI) / std_ROI)
     - learning_rate = 0.3 (aggressive but bounded)
     - Weights are normalized to sum to 1.0
     - Floor: no channel drops below 0.05 (5%) allocation unless paused
     - Ceiling: no channel exceeds 0.40 (40%) to prevent over-concentration

  5. If a channel has ROI < 0.2 * mean_ROI for 4 consecutive weeks → propose pausing
  6. If a channel has ROI > 3.0 * mean_ROI → flag as breakthrough, increase ceiling to 0.50
```

**Portfolio constraints:**
- Content publishing: minimum 1 post/day/platform (algorithm penalty for going dark)
- Outreach: minimum 5 emails/day (keep pipeline warm)
- Discovery: minimum 1 run/day (never stop filling the funnel)

**Output:** Updated `strategy_portfolio.json` + weekly reallocation report in the weekly digest

### 5.3 Layer 3 — Discovery + Invention (runs 2x/week, Tuesdays + Fridays 10:00 CET)

The strategist. Actively searches for new growth opportunities the system hasn't tried.

**Research protocol (each run):**

1. **Comparable artist analysis** — Track 5-10 artists in the 10K-500K listener range who play melodic techno, tribal psytrance, or similar. When any shows a growth spike (>20% week-over-week), investigate what they did:
   - New playlist placements?
   - Viral content?
   - Collaboration?
   - Platform feature?
   - Store the finding and assess replicability

2. **Tactic scanning** — Web search for current music marketing tactics:
   - "how to grow spotify listeners 2026"
   - "independent artist growth tactics"
   - "electronic music marketing strategies"
   - Music marketing subreddits, blogs, newsletters
   - Filter for tactics that are free or self-fundable

3. **Platform opportunity scan** — Check for emerging platforms or features:
   - New Spotify features (e.g., Clips, Canvas updates, Marquee changes)
   - New social platform features (TikTok music attribution, IG features)
   - Emerging platforms where early presence = advantage

4. **Gap analysis** — Compare the current channel registry against discovered tactics. Identify channels we're not using but should be.

5. **Experiment proposal** — For each promising discovery, draft an experiment proposal:
   - Hypothesis (specific, measurable)
   - Expected impact (with confidence level)
   - Implementation plan (what agent handles it, what new code needed)
   - Cost (free or estimated EUR if self-funded)
   - Risk assessment

**Output:** 1-3 new experiment proposals per run, added to the veto queue.

**Meta-learning:** Layer 3 also reviews completed experiments to find patterns:
- "Reddit posts with production stories outperform track-only posts 3:1"
- "Curators in Germany respond 2x more than UK curators"
- "Podcasts that feature origin stories get 5x more listener spikes"
- These patterns become **strategic insights** stored in `data/strategic_insights.json` and fed back into L1 and L2.

## 6. Growth Channel Registry

The system maintains a living registry of all channels it can use, their current status, and performance data.

### 6.1 Free Channels

| ID | Channel | Tactic | Agent | Status | Est. Listeners/Mo |
|----|---------|--------|-------|--------|-------------------|
| `ch_playlist_outreach` | Spotify Playlists | Curator email outreach | rjm-outreach | **active** | 2,000-10,000 |
| `ch_content_tiktok` | TikTok | Daily viral shorts | holy-rave-daily-run | **active** | 5,000-8,000 |
| `ch_content_reels` | Instagram Reels | Daily clips from 290K base | holy-rave-daily-run | **active** | 2,000-5,000 |
| `ch_content_ytshorts` | YouTube Shorts | Daily clips | holy-rave-daily-run | **active** | 1,000-3,000 |
| `ch_podcast_pitch` | Podcasts | Guest appearance pitching | rjm-outreach | **active** | 500-2,000 |
| `ch_editorial` | Spotify Editorial | Pitch for editorial playlists | manual + template | **active** | 10,000-100,000 |
| `ch_ig_conversion` | Instagram → Spotify | Convert 290K followers to listeners | NEW: ig-conversion | **queued** | 5,000-14,500 |
| `ch_reddit` | Reddit | r/melodictechno, r/psytrance, r/electronicmusic | NEW: reddit-seeder | **queued** | 500-800 |
| `ch_soundcloud` | SoundCloud | Reposts, groups, track presence | NEW: soundcloud-agent | **queued** | 300-600 |
| `ch_yt_longform` | YouTube Long-form | DJ mixes, studio sessions | NEW: yt-longform | **queued** | 800-1,200 |
| `ch_collab` | Artist Collabs | Remix exchanges, playlist swaps | NEW: collab-agent | **queued** | 2,000-5,000 |
| `ch_blog_pr` | Music Blogs | Review/feature outreach | rjm-outreach (extend) | **queued** | 300-800 |
| `ch_discord` | Discord | Electronic music servers | NEW: community-agent | **queued** | 200-400 |
| `ch_fan_email` | Fan Email List | Release-day streaming spikes | NEW: email-list-agent | **queued** | 1,000-1,500 |
| `ch_presave` | Pre-save Campaigns | Spotify pre-save for releases | release system (extend) | **queued** | 500-2,000 |
| `ch_festival` | Festival Submissions | Festival/showcase applications | NEW: festival-agent | **queued** | 200-1,000 |
| `ch_sync` | Sync Licensing | Film/TV/game music libraries | NEW: sync-agent | **queued** | 100-500 |
| `ch_bandcamp` | Bandcamp | Free downloads → Spotify redirect | NEW: bandcamp-agent | **queued** | 100-300 |
| `ch_forum` | Music Forums | KVR, Gearslutz, EDM forums | community-agent | **queued** | 100-200 |
| `ch_offering` | Offering Page | Optimize for donations → fund paid growth | NEW: offering-optimizer | **queued** | indirect |

### 6.2 Self-Funded Channels (unlocked when growth_budget > 0)

| ID | Channel | Cost/Unit | Platform | Est. ROI |
|----|---------|-----------|----------|----------|
| `ch_submithub` | SubmitHub Premium | $1-3/submission | submithub.com | 5-20 listeners/$ |
| `ch_groover` | Groover | EUR 2/submission | groover.co | 3-15 listeners/EUR |
| `ch_meta_ads` | Meta Ads | EUR 5-10/day | Facebook/Instagram | 10-50 listeners/EUR |
| `ch_tiktok_ads` | TikTok Ads | EUR 5-10/day | TikTok | 15-80 listeners/EUR |
| `ch_yt_ads` | YouTube Ads | EUR 5-10/day | YouTube | 5-30 listeners/EUR |
| `ch_playlist_push` | Playlist Push Services | varies | Various | 2-10 listeners/$ |

## 7. Self-Funding Loop

### 7.1 Revenue Flow

```
Fan visits robertjanmastenbroek.com/offering
         │
         ↓
    Stripe processes donation
         │
         ↓
    revenue_tracker.py polls Stripe API (daily, 09:00 CET)
         │
         ↓
    50% → growth_budget ledger
    50% → RJM personal (untouched by system)
         │
         ↓
    Master agent reviews budget weekly (Layer 2)
         │
         ↓
    Allocates to highest-ROI paid channel
         │
         ↓
    Experiment runs → results tracked
         │
         ↓
    ROI feeds back into allocation weights
```

### 7.2 Budget Ledger Schema

```python
# data/growth_budget.json
{
    "total_donations": 0.00,           # EUR, all-time from Stripe
    "total_allocated": 0.00,           # EUR, 50% of donations
    "total_spent": 0.00,               # EUR, spent on paid channels
    "available_balance": 0.00,         # allocated - spent
    "transactions": [
        {
            "date": "2026-04-20",
            "type": "donation",         # donation | spend | refund
            "amount": 25.00,
            "source": "stripe_pi_xxx",  # Stripe payment intent ID
            "allocated": 12.50,         # 50% to growth
            "note": "First offering donation"
        },
        {
            "date": "2026-04-21",
            "type": "spend",
            "amount": -3.00,
            "channel": "ch_submithub",
            "experiment_id": "exp_2026-04-21_submithub_tribal",
            "note": "SubmitHub submission: Jericho to Tribal Techno Weekly"
        }
    ]
}
```

### 7.3 Offering Page Optimization

The system treats the offering page as a conversion funnel and can run A/B tests:

**What it can modify:**
- Page headline and subheadline copy
- CTA button text and placement
- Social proof elements (listener counts, testimonial placement)
- Suggested donation amounts
- Page layout and visual hierarchy
- Thank-you page upsells (e.g., "share on social" after donation)

**Constraints:**
- Must stay truthful — no fabricated numbers, no false urgency
- Must pass all 5 brand voice tests
- Must pass Compass Test
- Must maintain the subtle salt principle
- Changes logged in `data/offering_experiments.json`
- A/B test minimum duration: 14 days (need enough traffic for significance)

### 7.4 Spend Authorization

- **Under EUR 5/transaction:** Auto-approved (no veto needed)
- **EUR 5-25/transaction:** Goes through standard 24hr veto
- **Over EUR 25/transaction:** Requires explicit RJM approval (email + wait)
- **Daily spend cap:** EUR 15 or 30% of available balance, whichever is lower
- **Never spend to zero:** Always keep EUR 5 reserve minimum

## 8. Veto System (Propose-and-Execute)

### 8.1 How It Works

1. Growth Brain identifies a new experiment or strategic change
2. Creates a **Proposal** in `data/proposals.json`:
   ```python
   {
       "id": "prop_2026-04-17_001",
       "type": "new_experiment",         # new_experiment | reallocation | channel_pause | channel_activate | budget_spend | page_modification
       "title": "Test Reddit r/melodictechno with track previews",
       "description": "Post 2x/week in r/melodictechno...",
       "hypothesis": "50+ profile visits per post",
       "risk_level": "low",              # low | medium | high
       "estimated_impact": "500-800 listeners/month",
       "proposed_at": "2026-04-17T08:00:00+01:00",
       "execute_after": "2026-04-18T08:00:00+01:00",
       "status": "pending",              # pending | approved | vetoed | expired | executed
       "veto_reason": null
   }
   ```

3. **Daily digest** at 08:00 CET via Gmail:
   - Subject: "BTL Daily — [N] proposals executing today"
   - Body: List of proposals hitting their 24hr window today, plus yesterday's results
   - Reply "veto [id]" to block any proposal

4. If not vetoed by `execute_after` → auto-execute
5. **Emergency brake:** `python3 rjm.py veto <id>` or `python3 rjm.py veto all` to halt everything

### 8.2 Auto-Reject Guardrails (no veto needed — system blocks itself)

| Guardrail | Trigger | Action |
|-----------|---------|--------|
| Brand violation | Content fails Compass Test or 5 brand tests | Block + log |
| Budget overspend | Transaction would exceed available balance | Block + log |
| Spam risk | Posting frequency exceeds platform safe limits | Block + throttle |
| Email volume | Would exceed 150 emails/day | Block + queue for tomorrow |
| Account safety | Action could trigger platform ban/shadowban | Block + flag |
| Legal boundary | Action involves deceptive practices, fake accounts, astroturfing | Block + permanent ban on tactic |
| ToS violation | Action violates platform terms of service | Block + permanent ban |

### 8.3 What Requires Veto Window vs. Auto-Approved

| Action | Veto Needed? |
|--------|-------------|
| New experiment on a new channel | Yes — 24hr |
| Tactical parameter adjustment (L1 bandit) | No — auto |
| Weekly reallocation (L2 weight shift <15%) | No — auto |
| Weekly reallocation (L2 weight shift >15%) | Yes — 24hr |
| Activating a paused channel | Yes — 24hr |
| Pausing an active channel | Yes — 24hr |
| Spending under EUR 5 | No — auto |
| Spending EUR 5-25 | Yes — 24hr |
| Spending over EUR 25 | Yes — explicit approval required |
| Offering page copy change | Yes — 24hr |
| Offering page layout change | Yes — 24hr |

## 9. Self-Assessment System

### 9.1 Growth Health Score (0-100)

Calculated weekly, tracks whether the system is actually improving.

```
Growth Health Score = weighted average of:

  Listener Velocity (30%)
    = (listeners_this_week - listeners_last_week) / listeners_last_week
    Score: >10% growth = 100, 5% = 80, 1% = 60, 0% = 40, negative = 20

  Experiment Hit Rate (20%)
    = successful_experiments / completed_experiments (last 30 days)
    Score: >50% = 100, 30% = 80, 15% = 60, <10% = 40

  Pipeline Health (15%)
    = new_contacts_added / target_contacts_per_week
    Score: >100% of target = 100, 75% = 80, 50% = 60, <25% = 40

  Channel Diversity (10%)
    = number of active channels with positive LEI / total active channels
    Score: >80% = 100, 60% = 80, 40% = 60, <20% = 40

  Content Performance (10%)
    = average completion rate across platforms (rolling 7 days)
    Score: >60% = 100, 40% = 80, 25% = 60, <15% = 40

  Budget Efficiency (10%)
    = listeners gained per EUR spent (if spending, else default 70)
    Score: >20 listeners/EUR = 100, 10 = 80, 5 = 60, <2 = 40

  System Reliability (5%)
    = (total_agent_runs - failed_runs) / total_agent_runs
    Score: >99% = 100, 95% = 80, 90% = 60, <85% = 40
```

### 9.2 Score-Triggered Actions

| Score Range | System Response |
|-------------|----------------|
| 80-100 | Stay the course. Log what's working. |
| 60-79 | Increase L3 discovery frequency to 3x/week. Propose 2 new experiments. |
| 40-59 | **Emergency strategy review.** L3 runs daily for 1 week. Propose bolder experiments. Review all channel allocations. |
| 20-39 | **Red alert.** Send RJM an urgent email. Pause all spending. Focus 100% on highest-ROI free channels. Request human guidance. |
| 0-19 | **System pause.** Something is fundamentally broken. Halt all experiments. Send detailed diagnostic to RJM. |

### 9.3 Self-Assessment Report (weekly, in digest)

```
=== BTL Growth Health Score: 67/100 ===

Listener Velocity:     +12 this week (325 → 337) | +3.7% | Score: 62
Experiment Hit Rate:   1/3 succeeded (Reddit yes, Discord no, SoundCloud inconclusive) | Score: 60
Pipeline Health:       42 new contacts (target: 50) | 84% | Score: 78
Channel Diversity:     4/6 active channels positive | 67% | Score: 72
Content Performance:   38% avg completion | Score: 70
Budget Efficiency:     No spend this week | Score: 70 (default)
System Reliability:    98.2% uptime | Score: 80

Trend: Score 67 → 67 (flat from last week)
Action: Increasing L3 discovery to 3x/week. Proposing 2 new experiments.

Top performer this week: Playlist outreach (+8 listeners, 3 adds)
Worst performer this week: Discord (0 attributable listeners after 14 days)
Recommendation: Pause Discord experiment, reallocate to Reddit (showing promise)
```

## 10. Master Agent Upgrade

The existing master agent gains new operational modes. It remains the queen — all new components report to it.

### 10.1 Operational Modes

| Mode | Cadence | Trigger | What It Does |
|------|---------|---------|-------------|
| **Monitor** | 8x/day | Existing schedule | Check agent health, trigger runs, detect stale agents |
| **Optimize** | 4x/day | After monitor | Run L1 bandits, update tactical parameters |
| **Reallocate** | 1x/week (Sun 20:00) | Cron | Run L2 portfolio review, shift weights |
| **Discover** | 2x/week (Tue+Fri 10:00) | Cron | Run L3 research, propose experiments |
| **Self-assess** | 1x/week (Sun 21:00) | After Reallocate | Calculate Growth Health Score, trigger responses |
| **Fund** | 1x/day (09:00) | Cron | Check Stripe, update budget, trigger paid experiments |
| **Digest** | 1x/day (08:00) | Cron | Send daily veto digest email to RJM |
| **Veto-check** | 4x/day | With Optimize | Check if any proposals passed their veto window, execute them |

### 10.2 Decision Priority (extends existing)

```
1. Content publishing       (daily clips — feeds the algorithm)
2. Outreach replies         (warm leads — time-sensitive)
3. Experiment execution     (NEW — active experiments need feeding)
4. Discover new contacts    (pipeline fuel)
5. Research + personalise   (quality multiplier)
6. L3 Discovery runs        (NEW — find new channels)
7. Analytics + reporting    (weekly pulse)
8. Offering optimization    (NEW — self-funding loop)
```

## 11. New Components to Build

### 11.1 Core BTL Modules (in `outreach_agent/`)

| Module | Purpose | Size Est. |
|--------|---------|-----------|
| `growth_brain.py` | Orchestrates L1/L2/L3. Main BTL entry point. | ~400 lines |
| `experiment_engine.py` | Experiment CRUD, lifecycle management, analysis | ~350 lines |
| `strategy_portfolio.py` | Channel registry, allocation weights, reallocation logic | ~300 lines |
| `veto_system.py` | Proposal queue, veto digest email, execution trigger | ~250 lines |
| `revenue_tracker.py` | Stripe API polling, budget ledger, spend authorization | ~200 lines |
| `bandit_framework.py` | Generalized Thompson Sampling bandit (replaces per-module bandits) | ~250 lines |
| `competitor_tracker.py` | Monitor comparable artists' Spotify growth | ~200 lines |
| `self_assessment.py` | Growth Health Score calculation + triggered actions | ~200 lines |

### 11.2 Channel Agent Stubs (in `outreach_agent/channel_agents/`)

Each channel agent follows a common interface:

```python
class ChannelAgent:
    def can_run(self) -> bool: ...          # credentials available, not rate-limited
    def execute(self, config: dict) -> dict: ...  # run one action, return metrics
    def get_metrics(self, days: int) -> dict: ... # historical performance
    def get_arms(self) -> list[str]: ...    # bandit arms for this channel
```

| Agent | File | Priority | Depends On |
|-------|------|----------|------------|
| `ig_conversion.py` | Convert IG followers → Spotify | P0 | Instagram Graph API token |
| `reddit_seeder.py` | Reddit community posts | P1 | Reddit API (free) |
| `yt_longform.py` | YouTube mixes + studio content | P1 | YouTube API token |
| `soundcloud_agent.py` | SoundCloud presence | P2 | SoundCloud API |
| `collab_agent.py` | Artist collaboration finder | P2 | Web search |
| `community_agent.py` | Discord/Telegram communities | P3 | Discord bot token |
| `festival_agent.py` | Festival submission tracker | P3 | Web search |
| `sync_agent.py` | Sync licensing submissions | P3 | Web search |
| `offering_optimizer.py` | A/B test offering page | P1 | Stripe API key |
| `fan_email_agent.py` | Email list building + blasts | P2 | Email service (Mailchimp/Resend) |
| `submithub_agent.py` | SubmitHub submissions | P2 (self-funded) | SubmitHub account + budget |
| `groover_agent.py` | Groover submissions | P2 (self-funded) | Groover account + budget |

### 11.3 Data Files (in `data/`)

| File | Purpose |
|------|---------|
| `experiments.json` | All experiment records (append-only) |
| `proposals.json` | Veto queue (active proposals) |
| `growth_budget.json` | Revenue + spend ledger |
| `growth_score.json` | Weekly self-assessment history |
| `strategic_insights.json` | L3 meta-learnings |
| `channel_registry.json` | Living channel status + allocation weights |
| `competitor_tracking.json` | Comparable artist Spotify metrics |
| `offering_experiments.json` | Offering page A/B test log |

### 11.4 Database Schema Extensions

```sql
-- New tables in outreach.db

CREATE TABLE experiments (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    tactic TEXT,
    cost_type TEXT DEFAULT 'free',
    cost_estimate REAL DEFAULT 0,
    expected_metric TEXT,
    expected_target REAL,
    expected_confidence REAL,
    duration_days INTEGER DEFAULT 21,
    success_criteria TEXT,
    failure_criteria TEXT,
    guardrails TEXT,  -- JSON array
    status TEXT DEFAULT 'proposed',
    proposed_at TEXT,
    execute_after TEXT,
    started_at TEXT,
    ended_at TEXT,
    result TEXT,
    learning TEXT,
    metrics_log TEXT,  -- JSON array
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE proposals (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    hypothesis TEXT,
    risk_level TEXT DEFAULT 'low',
    estimated_impact TEXT,
    proposed_at TEXT NOT NULL,
    execute_after TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    veto_reason TEXT,
    executed_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE growth_budget (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    type TEXT NOT NULL,  -- donation, spend, refund
    amount REAL NOT NULL,
    source TEXT,
    channel TEXT,
    experiment_id TEXT,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE channel_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    date TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE bandit_state (
    channel TEXT NOT NULL,
    arm_name TEXT NOT NULL,
    arm_value TEXT NOT NULL,
    successes REAL DEFAULT 0,
    failures REAL DEFAULT 0,
    samples INTEGER DEFAULT 0,
    last_updated TEXT,
    PRIMARY KEY (channel, arm_name, arm_value)
);

CREATE TABLE strategic_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,  -- experiment_id, l3_discovery, competitor_analysis
    insight TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    applicable_channels TEXT,  -- JSON array
    discovered_at TEXT DEFAULT (datetime('now')),
    validated BOOLEAN DEFAULT 0,
    applied_count INTEGER DEFAULT 0
);

-- Extend daily_stats
ALTER TABLE daily_stats ADD COLUMN listeners_delta INTEGER DEFAULT 0;
ALTER TABLE daily_stats ADD COLUMN growth_score INTEGER DEFAULT 0;
ALTER TABLE daily_stats ADD COLUMN active_experiments INTEGER DEFAULT 0;
ALTER TABLE daily_stats ADD COLUMN budget_available REAL DEFAULT 0;
```

## 12. CLI Extensions (`rjm.py`)

```
# Experiment management
python3 rjm.py experiment list              # All experiments by status
python3 rjm.py experiment active             # Currently running experiments
python3 rjm.py experiment results            # Completed experiments + learnings
python3 rjm.py experiment propose <channel>  # Manually trigger experiment proposal

# Veto system
python3 rjm.py veto <id>                    # Veto a specific proposal
python3 rjm.py veto all                     # Emergency brake — veto all pending
python3 rjm.py proposals                    # List pending proposals

# Growth intelligence
python3 rjm.py brain status                 # Full brain state: layers, bandits, score
python3 rjm.py brain discover               # Trigger L3 discovery now
python3 rjm.py brain assess                 # Run self-assessment now
python3 rjm.py brain insights               # View strategic insights

# Budget
python3 rjm.py budget                       # Growth budget status + history
python3 rjm.py budget spend <amount> <channel> <reason>  # Manual spend record

# Channels
python3 rjm.py channels                     # Channel performance + allocation table
python3 rjm.py channels activate <id>       # Manually activate a channel
python3 rjm.py channels pause <id>          # Manually pause a channel

# Score
python3 rjm.py score                        # Current Growth Health Score
python3 rjm.py score history                # Score trend over time

# Offering
python3 rjm.py offering status              # Offering page stats + A/B test status
python3 rjm.py offering optimize            # Trigger offering page A/B test
```

## 13. Integration with Existing Systems

### 13.1 Event Backbone Integration

All BTL components publish events via the existing `events.py`:

| Event Type | Source | Consumer |
|------------|--------|----------|
| `experiment.proposed` | experiment_engine | veto_system |
| `experiment.started` | experiment_engine | growth_brain |
| `experiment.completed` | experiment_engine | growth_brain, self_assessment |
| `experiment.analyzed` | growth_brain | strategy_portfolio |
| `proposal.pending` | veto_system | digest email |
| `proposal.executed` | veto_system | experiment_engine |
| `proposal.vetoed` | veto_system | growth_brain |
| `budget.donation` | revenue_tracker | growth_brain |
| `budget.spend` | revenue_tracker | strategy_portfolio |
| `channel.activated` | strategy_portfolio | growth_brain |
| `channel.paused` | strategy_portfolio | growth_brain |
| `score.calculated` | self_assessment | master_agent |
| `insight.discovered` | growth_brain | all agents |
| `bandit.updated` | bandit_framework | channel agents |
| `bandit.breakthrough` | bandit_framework | growth_brain |

### 13.2 Fleet State Integration

All new channel agents register with `fleet_state.py` via heartbeats. Master agent monitors them through the existing stale-detection system.

### 13.3 Strategy Registry Migration

The existing `strategy_registry.json` (18 strategies) migrates into `channel_registry.json`. Each strategy maps to a channel. Historical data preserved. The strategy registry becomes read-only (archived) after migration.

### 13.4 Learning Loop Integration

The existing content learning loop (`content_engine/learning_loop.py`) continues as-is. The BTL bandit framework wraps it — the content bandit reads from `weights_snapshot.json` and the learning loop continues to write it. No disruption to the existing pipeline.

## 14. Comparable Artists to Track

Initial list for Layer 3 competitor analysis (10K-500K range, similar genre):

| Artist | Spotify ID | Genre | Current Est. Listeners | Why Track |
|--------|-----------|-------|----------------------|-----------|
| Anyma | — | Melodic Techno | 500K+ | Visual scale reference, similar trajectory goal |
| Argy | — | Tribal/Techno | 200K+ | Tribal texture overlap |
| Agents Of Time | — | Melodic Techno | 100K-200K | Similar production style |
| Colyn | — | Melodic Techno | 50K-100K | Independent growth trajectory |
| Innellea | — | Melodic Techno | 100K-200K | Similar visual aesthetic |

*Spotify IDs will be resolved at runtime via Spotify API search. System will discover and add 5 more comparable artists during first L3 run, prioritizing artists in the 1K-50K range who are actively growing (closer to RJM's current trajectory).*

## 15. Guardrails Summary

### 15.1 Brand Guardrails (enforced on ALL output)
- Compass Test: "Does this serve The Seeker (secular/searching audience), not the churched crowd?"
- All 5 brand voice tests must pass
- Subtle Salt principle — biblical references present but never preachy
- Banned words list enforced
- No fabricated numbers, testimonials, or false urgency

### 15.2 Platform Safety Guardrails
- Reddit: Max 2 posts/week per subreddit, no spam, follow sub rules
- Instagram: Max 3 Reels/day, no engagement pod behavior
- TikTok: Max 3 posts/day, no artificial trending manipulation
- YouTube: Respect API quotas, no misleading metadata
- Email: 150/day cap, 08:00-23:00 CET, 8hr overnight break
- All platforms: No fake accounts, no bot engagement, no astroturfing

### 15.3 Financial Guardrails
- Never spend more than 50% of donation revenue
- EUR 5 reserve minimum
- Daily spend cap: EUR 15 or 30% of balance
- Large transactions require explicit approval
- Full audit trail in growth_budget.json

### 15.4 Legal Guardrails
- No deceptive marketing practices
- No copyright infringement (only use owned content)
- No purchased followers/streams/plays
- GDPR-compliant contact handling (existing in outreach_agent)
- All platform ToS respected

## 16. Success Metrics

### 16.1 System Success (is BTL working?)

| Metric | Baseline (today) | 30-day target | 90-day target | 1-year target |
|--------|------------------|---------------|---------------|---------------|
| Monthly listeners | 325 | 1,000 | 10,000 | 100,000 |
| Active channels | 5 | 8 | 12 | 15+ |
| Experiments completed | 0 | 5 | 25 | 100+ |
| Experiment hit rate | — | >20% | >35% | >45% |
| Growth Health Score | — | >50 | >65 | >75 |
| Self-funded budget | EUR 0 | EUR 0-50 | EUR 50-500 | EUR 500+ |

### 16.2 North Star Trajectory

```
325 → 1,000 → 5,000 → 10,000 → 50,000 → 100,000 → 500,000 → 1,000,000

Phase 1 (months 1-3):   325 → 10,000    (foundation: fix leaks, activate channels)
Phase 2 (months 4-6):   10K → 50,000    (acceleration: paid channels, collabs)
Phase 3 (months 7-12):  50K → 500,000   (compounding: algorithm, editorial, viral)
Phase 4 (months 12-18): 500K → 1,000,000 (scale: everything working together)
```

The system's job is to find the fastest path through these phases. The trajectory is aspirational — the system adapts based on what actually works.

## 17. Implementation Approach

**This is a lake, not an ocean.** The infrastructure exists. We're adding ~2,500 lines of new Python across 8 core modules + 12 channel agent stubs + CLI extensions + DB migrations. Estimated CC time: 2-3 hours for the full implementation.

**Build order (dependency-driven):**

1. **Foundation:** bandit_framework.py, DB migrations, data file scaffolding
2. **Core engine:** experiment_engine.py, strategy_portfolio.py, channel_registry.json
3. **Intelligence:** growth_brain.py (L1+L2+L3), self_assessment.py, competitor_tracker.py
4. **Veto + Budget:** veto_system.py, revenue_tracker.py (Stripe integration)
5. **Channel agents:** ig_conversion, reddit_seeder, offering_optimizer (P0+P1 first)
6. **CLI:** rjm.py extensions for all new commands
7. **Master upgrade:** Add new operational modes to master_agent.py
8. **Wiring:** Event subscriptions, fleet state registration, cron schedules
9. **Testing:** End-to-end dry run of full BTL cycle

## 18. Open Questions (for RJM review)

1. **Stripe API key:** Do you have a Stripe API key available, or do we need to set one up?
2. **Reddit account:** Do you have a Reddit account for r/melodictechno posting, or should the system create content for you to post manually?
3. **Offering page location:** Is the offering page a standalone HTML file in this repo, or hosted externally? (Found `offering/thank-you/index.html` but need the main page)
4. **Comparable artists:** Any specific artists you want tracked beyond the initial list?
5. **Notification preference:** Daily digest via Gmail — is that the right channel, or would you prefer Telegram/WhatsApp/other?
