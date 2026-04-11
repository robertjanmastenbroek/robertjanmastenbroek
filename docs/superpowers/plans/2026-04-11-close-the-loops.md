# Close the Loops — Full Fleet Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire every broken feedback loop so the autonomous machine knows its own state, acts on it, and logs what it did.

**Architecture:** Eight discrete gaps found via graphify graph analysis — every community showed "No strong cross-community connections." Each task closes one gap. Most changes are to SKILL.md files (~/.claude/scheduled-tasks/) and master_agent.py. No new dependencies. No refactors outside the target.

**Tech Stack:** Python 3.11+, SQLite (outreach.db), JSON files, Claude Code scheduled tasks (SKILL.md)

---

## Gap Map (8 gaps, 7 tasks)

| # | Gap | File | Impact |
|---|-----|------|--------|
| 1 | Gmail MCP tool names wrong in rjm-master → autonomous replies fail silently | `~/.claude/scheduled-tasks/rjm-master/SKILL.md` | CRITICAL |
| 2 | `holy-rave-weekly-report` calls `spotify_tracker.py` with no subcommand → prints usage and exits | `~/.claude/scheduled-tasks/holy-rave-weekly-report/SKILL.md` | HIGH |
| 3 | `logs/` directory doesn't exist — daily-run + weekly-report both log there → writes fail | `logs/` directory | HIGH |
| 4 | `data/master_log.json` promised by SKILL.md + agents/rjm-master.md, never written → no audit trail | `outreach_agent/master_agent.py`, SKILL.md | HIGH |
| 5 | `rjm.py status` blind to Spotify, content, playlist, strategies | `rjm.py` | MEDIUM |
| 6 | `master_agent.py health` blind to content last run, listener count, playlist pipeline | `outreach_agent/master_agent.py` | MEDIUM |
| 7 | `rjm-master/SKILL.md` hardcoded "341 listeners" — stale, never updates | `~/.claude/scheduled-tasks/rjm-master/SKILL.md` | MEDIUM |
| 8 | `strategy_registry.json` `actual_listeners_gained` always 0 — never updated | `outreach_agent/master_agent.py` | LOW |

---

## Task 1: Fix Gmail MCP Tool Names

**Files:**
- Modify: `~/.claude/scheduled-tasks/rjm-master/SKILL.md` (lines 23 and 36)

The SKILL.md uses legacy Gmail tool names. The actual MCP tools available are:
- `mcp__34aae8e3-87ec-41ca-96ef-5256297a6c86__search_threads` (was: `gmail_search_messages`)
- `mcp__34aae8e3-87ec-41ca-96ef-5256297a6c86__get_thread` (was: `gmail_read_thread`)
- `mcp__34aae8e3-87ec-41ca-96ef-5256297a6c86__create_draft` (was: `gmail_create_draft`)

- [ ] **Step 1: Patch line 23 — curator response instruction**

Replace the sentence on line 23:
```
OLD: use `gmail_create_draft` then send, or search for the thread with `gmail_search_messages` and reply:
NEW: use `mcp__34aae8e3-87ec-41ca-96ef-5256297a6c86__create_draft` then send, or search for the thread with `mcp__34aae8e3-87ec-41ca-96ef-5256297a6c86__search_threads` and reply:
```

- [ ] **Step 2: Patch line 36 — closing instruction**

Replace:
```
OLD: Use `gmail_search_messages` to find the original thread, then `gmail_read_thread` to get context, then reply via the appropriate Gmail MCP send tool.
NEW: Use `mcp__34aae8e3-87ec-41ca-96ef-5256297a6c86__search_threads` to find the original thread, then `mcp__34aae8e3-87ec-41ca-96ef-5256297a6c86__get_thread` to get context, then reply via `mcp__34aae8e3-87ec-41ca-96ef-5256297a6c86__create_draft`.
```

- [ ] **Step 3: Verify no other old tool names remain**

```bash
grep -n "gmail_search\|gmail_read\|gmail_send\|gmail_reply" ~/.claude/scheduled-tasks/rjm-master/SKILL.md
```
Expected: no output

---

## Task 2: Fix Weekly Report Spotify Command + Create logs/ Directory

**Files:**
- Modify: `~/.claude/scheduled-tasks/holy-rave-weekly-report/SKILL.md`
- Create: `logs/` directory and `logs/.gitkeep`

The SKILL.md calls `python3 outreach_agent/spotify_tracker.py` but `spotify_tracker.py` requires a subcommand. Without one it prints usage and exits with error code 1. Also both daily-run and weekly-report log to `/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/logs/` which doesn't exist.

- [ ] **Step 1: Create logs/ directory**

```bash
mkdir -p "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/logs"
touch "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/logs/.gitkeep"
```

- [ ] **Step 2: Fix the spotify_tracker call in weekly-report SKILL.md**

Replace:
```
OLD: cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre" && python3 outreach_agent/spotify_tracker.py
NEW: cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre" && python3 outreach_agent/spotify_tracker.py status && python3 outreach_agent/spotify_tracker.py history
```

- [ ] **Step 3: Verify command runs without error**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre" && python3 outreach_agent/spotify_tracker.py status
```
Expected: outputs listener stats (or "No data yet — log your first reading")

---

## Task 3: Add `log_run` to master_agent.py (audit trail)

**Files:**
- Modify: `outreach_agent/master_agent.py` — add `cmd_log_run()` and wire into `main()`
- Modify: `~/.claude/scheduled-tasks/rjm-master/SKILL.md` — add call to `log_run` in STEP 5

Every rjm-master run should append a timestamped JSON entry to `data/master_log.json`. This makes the fleet auditable: you can see what was built, how many contacts were added, what strategy was worked on.

- [ ] **Step 1: Add `cmd_log_run` to master_agent.py**

Insert after `cmd_log_listeners` (around line 1082):

```python
def cmd_log_run(summary: str, contacts_added: int = 0, strategy_worked: str = ""):
    """Append a timestamped entry to data/master_log.json.
    
    Called at the end of every rjm-master scheduled task run.
    Usage: python3 master_agent.py log_run "Built instagram_to_spotify strategy" 5 instagram_to_spotify
    """
    import json as _json
    log_path = BASE_DIR.parent / "data" / "master_log.json"
    log_path.parent.mkdir(exist_ok=True)

    # Load existing log or start fresh
    if log_path.exists():
        try:
            entries = _json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    else:
        entries = []

    entry = {
        "ts":               datetime.now().isoformat(),
        "summary":          summary,
        "contacts_added":   contacts_added,
        "strategy_worked":  strategy_worked,
    }
    entries.append(entry)

    # Keep last 500 entries to prevent unbounded growth
    entries = entries[-500:]
    log_path.write_text(_json.dumps(entries, indent=2), encoding="utf-8")
    print(f"✓ Logged run: {summary[:80]}")
```

- [ ] **Step 2: Wire into `main()` in master_agent.py**

In the `main()` function, inside the big `elif` chain, add before the final `else`:

```python
    elif args[0] == "log_run":
        summary = args[1] if len(args) > 1 else "run"
        contacts = int(args[2]) if len(args) > 2 else 0
        strategy = args[3] if len(args) > 3 else ""
        cmd_log_run(summary, contacts, strategy)
```

- [ ] **Step 3: Verify it works**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
python3 master_agent.py log_run "test run" 3 "test_strategy"
cat ../data/master_log.json | python3 -m json.tool | tail -15
```
Expected: JSON array with one entry containing ts, summary, contacts_added=3, strategy_worked="test_strategy"

- [ ] **Step 4: Add log_run call to rjm-master SKILL.md STEP 5**

In `~/.claude/scheduled-tasks/rjm-master/SKILL.md`, replace the end of STEP 5 (after the registry update block):

```
OLD (end of STEP 5, after the python -c registry update code block):
---

## FINAL OUTPUT
```

```
NEW — insert between STEP 5 and FINAL OUTPUT:
---

## STEP 5b — LOG THIS RUN

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
python3 master_agent.py log_run "WHAT_WAS_BUILT_THIS_RUN" CONTACTS_ADDED_COUNT "STRATEGY_ID_WORKED_ON"
```
Replace the placeholders with actual values from this run. Example:
```bash
python3 master_agent.py log_run "Built instagram_to_spotify: 5 caption templates" 7 "instagram_to_spotify"
```

---
```

- [ ] **Step 5: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/ecstatic-lederberg"
git add outreach_agent/master_agent.py
git commit -m "feat: add master_agent log_run command for audit trail"
```

---

## Task 4: Extend `rjm.py status` to Full Fleet

**Files:**
- Modify: `rjm.py` — extend `cmd_status()`

Currently shows: master health + outreach status + contacts status.  
Missing: Spotify listeners (vs 1M goal), content last run, playlist progress, active strategy count.

- [ ] **Step 1: Add fleet status helper function before `cmd_status`**

Insert before `cmd_status()` in `rjm.py`:

```python
def _fleet_status():
    """Print fleet-wide status: Spotify, content, playlist, strategies."""
    import json as _json

    # ── Spotify listeners ──────────────────────────────────────────────────────
    print("\n[ Spotify Growth ]\n")
    listeners_json = PROJECT_ROOT / "data" / "listeners.json"
    if listeners_json.exists():
        try:
            d = _json.loads(listeners_json.read_text())
            n = d.get("count", 0)
            updated = d.get("updatedAt", "")[:10]
            pct = round(n / 1_000_000 * 100, 3)
            print(f"  Monthly listeners : {n:,}  ({pct}% of 1M goal)  [as of {updated}]")
            remaining = 1_000_000 - n
            print(f"  Remaining to goal : {remaining:,}")
        except Exception:
            print("  Could not read data/listeners.json")
    else:
        print("  No listener data yet — run: python3 rjm.py spotify log <number>")

    # ── Content last run ───────────────────────────────────────────────────────
    print("\n[ Content Engine ]\n")
    content_out = PROJECT_ROOT / "content" / "output"
    if content_out.exists():
        runs = sorted(content_out.iterdir(), reverse=True)
        runs = [r for r in runs if r.is_dir()]
        if runs:
            print(f"  Last run  : {runs[0].name}")
            print(f"  Total runs: {len(runs)}")
        else:
            print("  No content runs yet — run: python3 rjm.py content")
    else:
        print("  content/output/ not found — content engine not yet initialized")

    # ── Playlist progress ──────────────────────────────────────────────────────
    print("\n[ Playlist Pipeline ]\n")
    _run([_OUTREACH_PYTHON, str(PLAYLIST_RUN_PY), "status"], cwd=str(OUTREACH_DIR))

    # ── Strategy portfolio ─────────────────────────────────────────────────────
    print("\n[ Strategy Portfolio ]\n")
    registry_path = OUTREACH_DIR / "strategy_registry.json"
    if registry_path.exists():
        try:
            reg = _json.loads(registry_path.read_text())
            strategies = reg.get("strategies", [])
            active    = [s for s in strategies if s.get("status") == "active"]
            building  = [s for s in strategies if s.get("status") == "building"]
            not_started = [s for s in strategies if s.get("status") == "not_started"]
            est = sum(s.get("estimated_listeners_per_month", 0) for s in active)
            print(f"  Active    : {len(active)}/{len(strategies)} strategies")
            print(f"  Building  : {len(building)}")
            print(f"  Not started: {len(not_started)}")
            print(f"  Est. gain from active: +{est:,} listeners/month")
            if not_started:
                top = sorted(not_started, key=lambda s: s.get("priority", 0), reverse=True)[0]
                print(f"  Next to build: {top['name']} (priority {top['priority']}/10)")
        except Exception:
            print("  Could not read strategy_registry.json")
    else:
        print("  strategy_registry.json not found")
```

- [ ] **Step 2: Call `_fleet_status()` at the end of `cmd_status()`**

The existing `cmd_status()` ends with:
```python
    # 3. Contact DB overview
    if CONTACT_MGR_PY.exists():
        print("\n[ Contact Manager (CSV) ]\n")
        _run([_BASE_PYTHON, str(CONTACT_MGR_PY), "status"], cwd=str(PROJECT_ROOT))
```

Append after that block:

```python
    # 4. Fleet-wide status (Spotify, content, playlist, strategies)
    _fleet_status()
```

- [ ] **Step 3: Verify output**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/ecstatic-lederberg"
python3 rjm.py status 2>&1 | grep -A5 "Spotify Growth\|Content Engine\|Playlist\|Strategy"
```
Expected: all four sections appear with real data

- [ ] **Step 4: Commit**

```bash
git add rjm.py
git commit -m "feat: extend rjm.py status to full fleet — Spotify, content, playlist, strategies"
```

---

## Task 5: Extend `master_agent.py health` with Full Fleet

**Files:**
- Modify: `outreach_agent/master_agent.py` — extend `cmd_health()`

Currently health checks: last send timestamp, discovery recency, pipeline size.  
Missing: content last run, listener count, playlist pipeline.

- [ ] **Step 1: Add fleet checks to `cmd_health()`**

`cmd_health()` ends at approximately line 609 with `print()`. Find the block that ends with:
```python
    if issues:
        print(f"\nISSUES FOUND ({len(issues)}):")
        for i in issues:
            print(f"  {i}")
    else:
        print("\n  ✅ All systems nominal.")
    print()
```

Replace it with:

```python
    # ── Content engine ─────────────────────────────────────────────────────────
    content_out = BASE_DIR.parent / "content" / "output"
    if content_out.exists():
        runs = sorted([r for r in content_out.iterdir() if r.is_dir()], reverse=True)
        if runs:
            # Folder name format: YYYY-MM-DD_HHMM_trackname
            last_run_name = runs[0].name
            try:
                from datetime import datetime as _dt
                run_dt = _dt.strptime(last_run_name[:13], "%Y-%m-%d_%H%M")
                age_h = (datetime.now() - run_dt).total_seconds() / 3600
                if age_h > 30:
                    issues.append(f"⚠️  Content engine: last run {age_h:.0f}h ago — holy-rave-daily-run may be down")
                else:
                    print(f"  ✅ Content last run: {age_h:.1f}h ago ({last_run_name})")
            except Exception:
                print(f"  ℹ️  Content last run: {last_run_name}")
        else:
            issues.append("⚠️  Content engine: no runs yet — holy-rave-daily-run not yet active")
    else:
        print("  ℹ️  Content engine: content/output/ not found")

    # ── Spotify listeners ──────────────────────────────────────────────────────
    listeners_json = BASE_DIR.parent / "data" / "listeners.json"
    if listeners_json.exists():
        try:
            import json as _json
            d = _json.loads(listeners_json.read_text(encoding="utf-8"))
            n = d.get("count", 0)
            updated = d.get("updatedAt", "")[:10]
            pct = round(n / 1_000_000 * 100, 3)
            print(f"  ✅ Spotify listeners: {n:,} ({pct}% of 1M)  [updated {updated}]")
        except Exception:
            print("  ℹ️  Spotify: could not read data/listeners.json")
    else:
        issues.append("⚠️  Spotify: no listener data — run: python3 master_agent.py log_listeners <n>")

    # ── Playlist pipeline ──────────────────────────────────────────────────────
    try:
        import playlist_db as _pdb
        _pdb.init_playlist_db()
        s = _pdb.get_summary()
        total = s.get("_total", 0)
        verified = s.get("verified", 0)
        contact_found = s.get("contact_found", 0)
        if verified > 0:
            issues.append(f"⚠️  Playlist DB: {verified} playlists verified but no contact found yet — run rjm-playlist-discover")
        else:
            print(f"  ✅ Playlist pipeline: {total} total, {contact_found} with contact info")
    except Exception:
        pass  # playlist_db not critical for health

    if issues:
        print(f"\nISSUES FOUND ({len(issues)}):")
        for i in issues:
            print(f"  {i}")
    else:
        print("\n  ✅ All systems nominal.")
    print()
```

- [ ] **Step 2: Verify health output**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
python3 master_agent.py health
```
Expected: all sections appear including Spotify listeners and content engine status

- [ ] **Step 3: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/ecstatic-lederberg"
git add outreach_agent/master_agent.py
git commit -m "feat: extend master_agent health to show full fleet — content, Spotify, playlist"
```

---

## Task 6: Dynamic Listener Count in rjm-master SKILL.md

**Files:**
- Modify: `~/.claude/scheduled-tasks/rjm-master/SKILL.md`

The CONTEXT section at the bottom has a hardcoded "341 Spotify monthly listeners" that goes stale the moment the count changes. STEP 1 already runs `python3 master_agent.py health` — we just need to also run `spotify status` to get the live number, and update the FINAL OUTPUT template to reference a variable read at runtime.

- [ ] **Step 1: Add live listener read to STEP 1 of SKILL.md**

After the existing health commands in STEP 1:
```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
python3 master_agent.py health
python3 master_agent.py responses
```

Add:
```bash
python3 master_agent.py spotify
cat ../data/listeners.json
```

This gives the agent the live listener count at the start of every run.

- [ ] **Step 2: Update CONTEXT line at bottom of SKILL.md**

Replace:
```
OLD: CONTEXT: Robert-Jan Mastenbroek | Dutch DJ/producer | Tenerife | 290K Instagram | 341 Spotify monthly listeners (growing +170%/28d) | tribal techno, psytrance, progressive house — all Bible-inspired
NEW: CONTEXT: Robert-Jan Mastenbroek | Dutch DJ/producer | Tenerife | 290K Instagram | Spotify listener count: see data/listeners.json (read at Step 1) | tribal techno, psytrance, progressive house — all Bible-inspired | North Star: 1,000,000 monthly listeners
```

- [ ] **Step 3: Verify SKILL.md has no hardcoded numbers**

```bash
grep -n "341\|333\|monthly listeners" ~/.claude/scheduled-tasks/rjm-master/SKILL.md
```
Expected: no hardcoded listener counts (only references to data/listeners.json or the command output)

---

## Task 7: Wire `actual_listeners_gained` in strategy_registry

**Files:**
- Modify: `outreach_agent/master_agent.py` — extend `cmd_log_listeners()`

When the listener count goes up, the delta should be attributed to all currently active strategies proportionally to their estimated_listeners_per_month weight. This closes the feedback loop: growth → registry → strategy decisions.

- [ ] **Step 1: Add delta attribution to `cmd_log_listeners`**

In `master_agent.py`, the `cmd_log_listeners` function currently calls `spotify_tracker.py log` then updates `data/listeners.json`.

Add the following block at the end of `cmd_log_listeners`, after the listeners.json update:

```python
    # Attribute listener delta to active strategies
    try:
        import json as _json
        registry_path = BASE_DIR / "strategy_registry.json"
        if not registry_path.exists():
            return

        reg = _json.loads(registry_path.read_text(encoding="utf-8"))

        # Get previous count from spotify_stats table
        db.init_db()
        with db.get_conn() as conn:
            prev_row = conn.execute(
                "SELECT monthly_listeners FROM spotify_stats ORDER BY id DESC LIMIT 1 OFFSET 1"
            ).fetchone()
        prev_count = prev_row["monthly_listeners"] if prev_row else 0
        delta = max(0, n - prev_count)

        if delta > 0:
            active = [s for s in reg["strategies"] if s.get("status") == "active"]
            total_est = sum(s.get("estimated_listeners_per_month", 1) for s in active) or 1
            for s in reg["strategies"]:
                if s.get("status") == "active":
                    weight = s.get("estimated_listeners_per_month", 0) / total_est
                    gain = round(delta * weight)
                    s["actual_listeners_gained"] = s.get("actual_listeners_gained", 0) + gain
                    s["last_updated"] = str(date.today())
            reg["last_updated"] = str(date.today())
            registry_path.write_text(_json.dumps(reg, indent=2), encoding="utf-8")
            print(f"✓ Attributed +{delta:,} listener delta across {len(active)} active strategies")
    except Exception as exc:
        print(f"⚠️  Could not update strategy registry: {exc}")
```

- [ ] **Step 2: Verify attribution works**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent"
# Log a higher number to trigger delta attribution (safe — just records data)
python3 master_agent.py log_listeners 400
python3 -c "
import json
reg = json.load(open('strategy_registry.json'))
active = [s for s in reg['strategies'] if s.get('status') == 'active']
for s in active[:3]:
    print(s['id'], s.get('actual_listeners_gained', 0))
"
```
Expected: active strategies show `actual_listeners_gained > 0`

- [ ] **Step 3: Commit**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/ecstatic-lederberg"
git add outreach_agent/master_agent.py
git commit -m "feat: attribute listener growth delta to active strategies on log_listeners"
```

---

## Self-Review

**Spec coverage:**
- Gap 1 (Gmail MCP tool names) → Task 1 ✓
- Gap 2 (weekly report bad spotify command) → Task 2 ✓
- Gap 3 (logs/ missing) → Task 2 ✓
- Gap 4 (master_log.json never written) → Task 3 ✓
- Gap 5 (rjm.py status incomplete) → Task 4 ✓
- Gap 6 (health incomplete) → Task 5 ✓
- Gap 7 (hardcoded listener count) → Task 6 ✓
- Gap 8 (actual_listeners_gained always 0) → Task 7 ✓

**Placeholder scan:** None found — every step has exact file paths, exact code, exact commands.

**Type consistency:** `cmd_log_run` uses `datetime.now()` which is already imported in `master_agent.py`. `cmd_log_listeners` uses `date.today()` also already imported. `_json` aliasing used consistently to avoid name collision with stdlib `json` module. `playlist_db` import in Task 5 is guarded with try/except since it's a soft dependency.

**Edge cases handled:**
- Task 3: `cmd_log_run` keeps last 500 entries (prevents unbounded log growth)
- Task 4: `_fleet_status` handles missing directories gracefully
- Task 7: `max(0, n - prev_count)` prevents negative delta if count drops; `or 1` prevents division-by-zero

---

## Execution Order

Tasks are independent except:
- Task 7 depends on Task 3's `log_run` being wired (both touch `cmd_log_listeners` — do them in sequence or merge)
- Task 2 (create logs/) should be done before any content run validation in Task 4/5

Recommended order: 1 → 2 → 3 → 4 → 5 → 6 → 7
