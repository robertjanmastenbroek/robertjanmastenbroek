# Agent: holy-rave-daily-run

**Cadence:** Daily, 09:07 CET — fires ~1 hour after the unified pipeline at 08:00.
**Role:** **Post-pipeline supervisor**. Do not re-run the pipeline. Read the registry + launchd logs, verify the 08:00 run shipped, and surface anomalies before the user's morning check.

## Why this agent exists

The unified content pipeline runs autonomously under three direct-python launchd jobs:

| Time (CET) | Job | Command |
|------------|-----|---------|
| 06:00 | `com.rjm.viral-trend` | `python3 rjm.py content trend-scan` |
| 08:00 | `com.rjm.viral-daily` | `python3 rjm.py content viral` |
| 18:00 | `com.rjm.viral-learning` | `python3 rjm.py content learning` |

Those jobs *are* the pipeline. This agent is a watchdog that runs after 08:00 and reports whether the 08:00 run actually shipped, which targets failed, and what to investigate.

## What to do

Do **NOT** run `python3 rjm.py content viral`. The pipeline already ran at 08:00. Running it again risks re-posting (the pipeline is idempotent via `data/performance/YYYY-MM-DD_posts.json`, but double-posting is the historical failure mode — stay hands-off).

### 1. Read today's registry

```bash
cat "data/performance/$(date +%Y-%m-%d)_posts.json"
```

Expected shape: 18 entries (3 clips × 6 distribution targets — IG, YT, FB, TikTok, IG Stories, FB Stories). Every entry should have `success: true`. Flag any `success: false`.

### 2. Check the launchd log

```bash
tail -80 "logs/launchd_viral_daily.log"
```

Look for:
- Python tracebacks
- Brand gate rejections (lines starting with `[brand_gate:...] REJECT`)
- Distributor errors (401/403 on native APIs → token expired)
- Buffer fan-out warnings (should only fire for TikTok; IG/YT/FB go native)

### 3. Spot-check one posted clip

Grab the first `success: true` entry for each platform and confirm:
- `post_id` is non-empty
- `platform` matches the target channel
- `via` is either omitted (native API) or `buffer_fallback` (TikTok only, or a legitimate fallback after native failure)

### 4. Report

Write `RJM_Daily_Report.md` with:

1. **Pipeline status** — shipped / partial / failed
2. **Success count** — `X/18 targets shipped`
3. **Failures** — list each failed target with the `error` field
4. **Action items** — what the user should do before tomorrow's 08:00 run (rotate token, clear circuit breaker, etc.)
5. **North Star check** — 1-line sanity check that today's clips are plausibly shareable by a secular rave attendee

## Success criteria for this supervisor run

- `RJM_Daily_Report.md` exists and was touched today
- If any target failed, the action-items section names the specific fix
- No `python3 rjm.py content viral` invocation in this agent's run history

## Do-not touch

- `.env` — token refresh runs separately (`com.rjm.token-refresh`, Mon 07:00)
- `data/weights_snapshot.json` — the 18:00 learning loop owns this
- The registry file — read-only for this agent

## Legacy context

Before 2026-04-16 this agent *was* the pipeline — it invoked the old Buffer-fanout 3-clip × 3-platform flow via Claude. The unified pipeline merge ([PR #6](https://github.com/robertjanmastenbroek/robertjanmastenbroek/pull/6)) retired that path. If you find yourself tempted to "just run the pipeline", re-read §What to do.
