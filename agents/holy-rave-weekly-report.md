# Agent: holy-rave-weekly-report

**Cadence:** Weekly · Mondays 09:13 CET
**Role:** Analytics pulse — Spotify KPIs primary (North Star is 1M monthly listeners), content + outreach secondary.

## Inputs

Read the last 7 days of state from:

| Source | Path | What's in it |
|--------|------|--------------|
| Spotify scrape | `data/listeners.json` + `data/spotify_stats/` | Monthly listeners, followers, stream counts |
| Daily registries | `data/performance/YYYY-MM-DD_posts.json` | Post attempts, success/fail per target |
| Performance data | `data/performance/*` | IG/YT/FB metrics fetched by the 18:00 learning loop |
| Weights snapshot | `data/weights_snapshot.json` | Per-format / per-platform / per-template bandit weights |
| Learning log | `logs/launchd_viral_learning.log` | Breakthrough analyses, reward composites |
| Outreach state | `data/outreach_state.db` (via `rjm.py status`) | Emails sent, replies, bookings |

## Report Structure

### 1. Spotify KPIs (primary — North Star)

- **Monthly listeners** — current vs. 7d ago, vs. 1M target. % progress.
- **Stream count by track** — rank all tracks; flag any track with a 2× week-over-week spike (possible algorithmic pickup).
- **Playlist adds** — new placements this week.
- **Saves and follows** — 7d delta.
- **Top markets** — Spain, US, Germany, UK, Poland, NL, France, Brazil. Flag new top-10 entrants.

### 2. Content Pipeline (new unified pipeline metrics)

- **Posts shipped** — `X/126 targets` (18/day × 7 days). Break down by platform: IG / YT / FB / TikTok / IG Stories / FB Stories.
- **Save-driver performance** — median `save_rate` per clip. Save-rate is the primary leading indicator of Spotify-stream conversion (designed into the pipeline at PR #6).
- **Best-performing hook template** — from `data/weights_snapshot.json`, surface the top-3 template IDs by composite reward. Name the template's mechanism (tension / identity / curiosity / contrast) and sub-mode.
- **Best-performing format** — transitional / emotional / performance. Per-format weight deltas week-over-week.
- **Best-performing platform** — which distribution target earned the highest median reward.
- **Worst-performing combinations** — any (format, platform, template) with <10% of the pool median. These are candidates for the bandit to de-weight automatically, but flag them so the user knows.
- **Track rotation health** — which tracks were picked from `TrackPool` this week. If a new weekly Spotify release existed and wasn't rotated in, flag it.
- **Breakthrough analyses** — any clip that hit 2× rolling-mean views. From `learning/breakthrough_*.md`.

### 3. Outreach

- Emails sent this week (cap is 150/day).
- Reply rate.
- Curator placements secured.
- Podcast bookings confirmed (check Google Calendar for booked slots).
- Bounce rate — if >5%, flag the list for cleaning.

### 4. Infrastructure Health

- Any failed launchd runs last 7 days (`grep -l "Traceback\|ERROR" logs/launchd_*.log`).
- IG / YT / FB / Spotify token expiry — flag any token with <14 days left.
- Buffer channel sanity — no non-TikTok Buffer posts should appear (enforced at distributor level since PR #6).

### 5. Action Items

Prioritised list of what to double down on, what to cut, what to fix. Order:
1. Content publishing (feeds algorithm daily — any posting failure is critical)
2. Outreach replies (warm leads — time-sensitive)
3. Discover new contacts (pipeline fuel)
4. Research + personalisation
5. Analytics + reporting

### 6. North Star progress

Single line: `Current monthly listeners: X / 1,000,000 (Y%). Week delta: +Z.`

## Output

- Overwrite `RJM_Daily_Report.md` with this week's pulse.
- Append a one-line summary to `data/weekly_pulse_log.jsonl` (date, listeners, stream delta, save rate, top template) so future weekly reports can plot trends.

## Guardrails

- Do **not** send emails, book calls, or run the content pipeline. This agent is read-only on the world — it writes only the two local files above.
- Do **not** modify `data/weights_snapshot.json`. The 18:00 learning loop owns bandit state.
- If a data file is missing, note it in §4 and proceed — don't stall the report.
