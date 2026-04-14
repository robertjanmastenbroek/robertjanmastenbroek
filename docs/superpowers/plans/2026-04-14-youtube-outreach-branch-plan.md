# YouTube Outreach Branch — Implementation Plan

Spec: [2026-04-14-youtube-outreach-branch-design.md](../specs/2026-04-14-youtube-outreach-branch-design.md)

Ordered by dependency — each phase is verifiable in isolation. Rollback = `git checkout` on the single phase's files.

## Phase 0 — Content ID disable (manual, user-owned pre-requisite)

**Owner**: RJM (not automatable).

**Action**: In BandLab Music distribution dashboard, for each of these 5 releases, disable YouTube Content ID / YouTube monetization:
1. Halleluyah
2. Kavod
3. Jericho
4. Renamed
5. Fire In Our Hands

**Why**: the email template promises "100% of the ad revenue" to any channel that uploads. If Content ID is still on, that promise is a lie — BandLab auto-claims the upload and routes ad rev away from the channel. Disable at release level, one time.

**Blocking**: Phase 9 (verification) includes a reminder to confirm Phase 0 is complete before the first real send. Phases 1-8 (code changes) do not depend on Phase 0, so implementation can proceed in parallel.

## Phase 1 — Database schema

**Goal**: Add 7 youtube_* columns to `contacts` + new `api_budget` table, idempotent migration.

**Files**: `outreach_agent/db.py`

**Changes**:
- Extend `_COLUMN_MIGRATIONS` list (or equivalent idempotent pattern already in db.py)
- Add `CREATE TABLE IF NOT EXISTS api_budget (...)` in `init_db()`
- New helpers: `record_api_units(service, units)`, `get_api_units_today(service)`

**Verification**:
```bash
cd outreach_agent
python3 -c "import db; db.init_db(); import sqlite3; c = sqlite3.connect('outreach.db'); print([r[1] for r in c.execute('PRAGMA table_info(contacts)').fetchall() if r[1].startswith('youtube_')])"
# Expect: ['youtube_channel_id','youtube_channel_url','youtube_subs','youtube_video_count','youtube_last_upload_at','youtube_genre_match_score','youtube_recent_upload_title']
python3 -c "import db; db.init_db(); db.record_api_units('youtube', 10); print(db.get_api_units_today('youtube'))"
# Expect: 10
```

## Phase 2 — Config constants + activate youtube weight

**Goal**: Add YOUTUBE_* constants, flip `CONTACT_TYPE_WEIGHTS['youtube']` from 0 to 50.

**Files**: `outreach_agent/config.py`

**Verification**:
```bash
cd outreach_agent && python3 -c "import config; print('yt_weight', config.CONTACT_TYPE_WEIGHTS['youtube']); print('floor', config.YOUTUBE_SHARE_FLOOR); print('queries', len(config.YOUTUBE_DISCOVERY_QUERIES))"
# Expect: yt_weight 50; floor 0.5; queries 15
```

## Phase 3 — story.py track update

**Goal**: Add Kavod with empty URL placeholder. Filter empty-URL tracks in `_get_track_recs`.

**Files**: `outreach_agent/story.py`, `outreach_agent/template_engine.py`

**Changes**:
- `story.py`: add Kavod dict to `TRACKS['psytrance']`
- `template_engine.py:288-330` `_get_track_recs`: skip tracks where `t['spotify'] == ''`

**Verification**:
```bash
cd outreach_agent && python3 -c "from story import TRACKS; print([t['title'] for t in TRACKS['psytrance']])"
# Expect: ['Halleluyah', 'Kavod', 'Jericho']
python3 -c "from template_engine import _get_track_recs; print(_get_track_recs('youtube','psytrance tribal',''))"
# Expect: output includes Halleluyah and Jericho with URLs, but NOT Kavod (empty URL)
```

## Phase 4 — template_engine.py: rewrite youtube addon + CAN-SPAM footer

**Goal**: Replace the existing `_TYPE_ADDONS['youtube']` (lines 164-168) with the upload-strategy prompt. Add CAN-SPAM footer to both sign-off injection points (lines 387-388 and 500-501).

**Files**: `outreach_agent/template_engine.py`

**Changes**:
- Replace `_TYPE_ADDONS['youtube']` block
- Extract the sign-off + footer into a constant `_SIGNATURE_BLOCK`, use in both places
- Force psytrance genre path for type='youtube' in `_get_track_recs` (one-line override at function top)

**Verification**:
```bash
cd outreach_agent && python3 -c "
from template_engine import _TYPE_ADDONS, _SIGNATURE_BLOCK, _ensure_signature
assert '100% of the ad revenue' in _TYPE_ADDONS['youtube'] or '100% ad rev' in _TYPE_ADDONS['youtube']
assert 'Content ID' in _TYPE_ADDONS['youtube']
assert 'robertjanmastenbroek.com' in _SIGNATURE_BLOCK
# Idempotence test
body = 'Hey.\n\nRobert-Jan\nrobertjanmastenbroek.com | https://instagram.com/robertjanmastenbroek'
assert _ensure_signature(body) == body
print('OK')
"
```

## Phase 5 — SKIPPED (was: youtube_auth.py shared OAuth)

**Reason for skip**: YouTube Data API v3 read-only operations (search.list, channels.list, playlistItems.list) accept a plain API key — OAuth is only needed for write operations. Discovery uses `os.environ["YOUTUBE_API_KEY"]` directly inside `youtube_discover.py`. The content distributor's OAuth flow is left untouched.

Phase 6 (below) adds a private `_get_api_key()` helper inside the discovery module.

## Phase 6 — youtube_discover.py discovery module

**Goal**: Full discovery pipeline. 3-pass search + enrichment + email extraction + DB writes.

**Files**: `outreach_agent/youtube_discover.py` (new)

**Structure**:
```python
def run_discovery() -> dict:
    """Main entry. Returns {'found': N, 'qualified': N, 'with_email': N, 'written': N}."""
    ...

def _search_seeds() -> set[str]: ...
def _enrich_channels(channel_ids: list[str]) -> list[dict]: ...
def _qualify(channel: dict) -> bool: ...
def _extract_email(channel: dict) -> str | None: ...
def _write_to_db(channels: list[dict]) -> int: ...
```

**Verification** (dry-run flag):
```bash
cd outreach_agent && python3 youtube_discover.py --dry-run
# Expect: prints found/qualified counts without hitting DB or YouTube API (uses cached fixture)
python3 youtube_discover.py --limit 5
# Expect: hits API with limit=5 per query (real API call, low quota burn), writes to DB, prints summary
```

## Phase 7 — run_cycle.py allocator

**Goal**: Add `_weighted_order_with_youtube_floor()` and call it from `cmd_plan()` in place of the existing `_weighted_order()`.

**Files**: `outreach_agent/run_cycle.py`

**Changes**:
- Add new function above `cmd_plan` (around line 170)
- Replace both `_weighted_order(...)` call sites inside `cmd_plan` with the new function

**Verification**:
```bash
cd outreach_agent && python3 -c "
# Unit test the allocator in isolation
from run_cycle import _weighted_order_with_youtube_floor
mix = [{'type':'youtube','email':f'y{i}'} for i in range(4)] + [{'type':'curator','email':f'c{i}'} for i in range(6)]
result = _weighted_order_with_youtube_floor(mix, youtube_share=0.5)
yt_in_first_4 = sum(1 for c in result[:4] if c['type']=='youtube')
print(f'YouTube in first 4: {yt_in_first_4}')
assert yt_in_first_4 >= 2, f'Expected ≥ 2 YouTube in first 4, got {yt_in_first_4}'
print('OK')
"
```

Then dry-run the full planner:
```bash
python3 run_cycle.py plan > /tmp/plan.json
python3 -c "
import json
p = json.load(open('/tmp/plan.json'))
actions = [a for a in p['actions'] if a.get('type')=='send']
# Can't assert 50% without seeded YouTube contacts, but should at least run without errors
print(f'send actions: {len(actions)}')
"
```

## Phase 8 — rjm.py CLI commands

**Goal**: Add `rjm.py youtube discover|status|budget` subcommands.

**Files**: `rjm.py`

**Verification**:
```bash
python3 rjm.py youtube status
# Expect: table of youtube-type contact counts per status
python3 rjm.py youtube budget
# Expect: today's API unit usage vs cap
python3 rjm.py youtube discover --dry-run
# Expect: discovery summary, no DB writes
```

## Phase 9 — End-to-end verification

**Goal**: Confirm the full loop works without regressions.

1. **Import check** — every touched module imports without errors:
   ```bash
   cd outreach_agent && python3 -c "
   import db, config, story, template_engine, run_cycle, youtube_auth, youtube_discover
   print('imports OK')
   "
   ```

2. **Dry-run plan with seeded YouTube contacts**:
   ```bash
   python3 -c "
   import db
   db.init_db()
   for i in range(5):
       db.add_contact(
           email=f'test.yt.{i}@example.com', name=f'Test YT {i}',
           type='youtube', status='verified', source='test',
       )
   "
   python3 outreach_agent/run_cycle.py plan > /tmp/plan_with_yt.json
   # Eyeball the output: first 3-4 sends should be type='youtube'
   # (can't verify directly without DB inspection — log what's in the plan)
   ```

3. **Regression check** — confirm existing curator/podcast behaviour:
   ```bash
   python3 outreach_agent/run_cycle.py status
   # Expect: unchanged output format, existing contacts still visible
   ```

4. **Template smoke test** (generates one real email, requires Claude CLI available):
   ```bash
   python3 -c "
   from template_engine import generate_email
   c = {
       'email': 'demo@example.com',
       'name': 'Progressive Psytrance Promo',
       'type': 'youtube',
       'genre': 'psytrance tribal progressive',
       'notes': 'Promo channel, 150K subs, last upload: Vini Vici remix',
       'research_notes': 'Recent upload: Vini Vici - Adhana (Tribal Extended Mix)',
   }
   subj, body = generate_email(c)
   assert '100%' in body and 'ad rev' in body.lower(), 'Missing 100% ad revenue promise'
   assert 'spotify' in body.lower(), 'Missing Spotify link ask'
   assert 'robertjanmastenbroek.com' in body, 'Missing sign-off'
   print(subj); print(body)
   "
   ```

5. **Phase 0 pre-flight reminder**: before any YouTube contact is sent in production mode, confirm Content ID is disabled in BandLab for the 5 psytrance tracks. This is a manual user check — the code cannot verify it.

## Rollback plan

Each phase touches distinct files. To roll back:
- Phase 1 (db): `git checkout outreach_agent/db.py` — existing sqlite DB is untouched (new columns are NULL)
- Phase 2 (config): `git checkout outreach_agent/config.py` (youtube weight returns to 0, branch deactivates)
- Phase 3-4 (story + template): `git checkout outreach_agent/story.py outreach_agent/template_engine.py`
- Phase 5-6 (auth + discover): delete `outreach_agent/youtube_auth.py outreach_agent/youtube_discover.py`
- Phase 7 (allocator): `git checkout outreach_agent/run_cycle.py`
- Phase 8 (CLI): `git checkout rjm.py`

Full rollback: `git reset --hard HEAD` inside the worktree (worktree is isolated, main branch unaffected).

## Not in this plan

- Scheduled task creation (`mcp__scheduled-tasks__create_scheduled_task` for `rjm-youtube-discover` 3×/day). Will be set up after the first successful manual discovery run.
- Phase 2 upload-detection loop (future).
- Content ID claim-release automation (manual for VIP replies only).
- Apple Music / Deezer / Songlink integration (future).
