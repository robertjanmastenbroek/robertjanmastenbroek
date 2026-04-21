# Agent: rjm-discover
**Cadence:** 6×/day
**Role:** Contact + Spotify playlist discovery — runs both pipelines in one session

> This agent covers both pipelines:
> - **Part A:** podcasts, SoundCloud curators, Mixcloud, Bandcamp labels, blogs, editorial contacts (non-Spotify)
> - **Part B:** Spotify playlists only → `data/playlist_database.json`

---

## Hard Execution Limits (non-negotiable)
- **Part A — Max WebSearch queries per run: 8**
- **Part A — Max contacts to evaluate: 20**
- **Part A — Target net-new appended: 10**
- **Part A — STOP after 8 searches**, even if target isn't reached — append what you have and continue to Part B
- **Part B — Max WebSearch queries per run: 6**
- **Part B — Max playlists to evaluate: 15**
- **Part B — Target net-new added: 5–10**
- **Part B — Stop condition:** once `playlist_database.json` has ≥200 entries, skip Part B and log `{"skipped": true, "reason": "database_complete"}`
- **Total queries per run: 14 (8 + 6) — fire all in parallel within each part**

---

## PART A — Contact Discovery (podcasts, blogs, labels, curators)

### Step 0 — Intelligence Brief (run before any search)

Pull live signal from the existing systems to make searches targeted, not generic.

**A. Pipeline gap analysis** — read `contacts.csv`:
- Count contacts by `type` (label, curator, podcast, blog, festival)
- Count contacts by `audience_type` (seeker, music-first, faith-adjacent)
- Identify which types are UNDERREPRESENTED (< 15 entries = gap, < 5 = critical gap)
- Allocate your 8 query slots proportionally to fill the gaps first

Example logic:
```
labels=45  → DO NOT search for more labels this run
festivals=23 → skip unless a different geography
curators=16  → 2 query slots
podcasts=3   → 4 query slots (critical gap)
blogs=2      → 2 query slots (critical gap)
```

**B. Learning engine signal** — read `outreach_agent/db.py` via the Python learning engine:
```python
from outreach_agent.learning import get_learning_context_for_template
from outreach_agent.db import get_recent_insights, get_template_stats

# Pull: which contact types are replying? Which aren't?
# Use this to UP-WEIGHT queries toward high-reply types
# Use this to DOWN-WEIGHT contact types with 0 replies after 10+ sends
```
If learning data is unavailable (DB not accessible): skip this sub-step, continue.

**C. Discovery query dedup** — check `outreach_agent/db.py` `discovery_log` table:
```python
# SELECT search_query FROM discovery_log WHERE searched_at > (now - 48hrs)
# Do NOT repeat a query that ran in the last 48 hours — rotate to fresh angles
```

**D. Brand filter** — call `brand_context.get_discovery_filter()`:
```python
from outreach_agent.brand_context import get_discovery_filter
filter_rules = get_discovery_filter()
# Inject this as your evaluation gate in Step 3
```
This is the canonical Subtle Salt targeting filter. Use it verbatim, do not re-derive it.

---

### Step 1 — Assemble 8 Search Queries

Based on the gap analysis from Step 0, assemble exactly 8 queries. Prioritize gaps.

**Podcast query templates (use when podcast is a gap):**
- `"[genre] podcast" host guest submit electronic music 2025`
- `"consciousness music podcast" electronic rave festival festival 2025`
- `"electronic music podcast" [target market: Spain|Germany|Netherlands|UK] 2025`
- `"spiritual not religious podcast" music rave DJ interview`

**Curator/blog query templates (use when curator/blog is a gap):**
- `"organic house" OR "tribal psytrance" blog submit demos contact email 2025`
- `"[genre] curator" mixcloud soundcloud contact booking email`
- `"ethnic electronic" OR "nomadic electronic" world fusion rave curator blog 2025`
- `"underground psytrance" OR "Goa trance" editorial blog playlist curator email submit`
- `"Middle Eastern electronic" OR "desert electronic" curator blog 2025`

**Target markets (inject into queries for geographic diversification):**
Spain, Germany, Netherlands, UK, Poland, France, Brazil, US
— rotate markets across runs based on discovery_log recency

---

### Step 2 — Run Queries in Parallel

Fire all 8 WebSearch queries **simultaneously** (single parallel call block). Do not wait for one before launching the next.

---

### Step 3 — Evaluate Results (max 20 candidates)

From all search results combined, extract up to 20 unique candidates.

**Skip immediately if:**
- Email already in contacts.csv (check dedup set from Step 0A)
- No contact email findable in the result (don't spend time hunting it)
- Audience < 1,000 podcast downloads/episode OR < 5,000 followers

**Apply `get_discovery_filter()` rules** (loaded in Step 0D) — assign one `audience_type`:
- `seeker` — rave/festival/consciousness/spiritual-not-religious
- `music-first` — genre-only, no spiritual dimension needed
- `faith-adjacent` — open to spiritual themes
- `avoid` — explicitly Christian/church → do NOT append

**Mandatory Subtle Salt gate:**
"Would a secular rave attendee who has never been to church share this or appear on this willingly — and enjoy it?"
- Yes → qualify
- No → tag `avoid`, skip

---

### Step 4 — Append to contacts.csv

Format per row:
```
email, name, type, genre, notes, status, date_added, date_sent, bounce, audience_type
```
- `type`: `podcast` | `curator` | `blog` | `label`
- `status`: `new`
- `date_sent`: blank
- `bounce`: `unknown`

---

### Step 5 — Log to DB + File

**Write to `outreach_agent/db.py` `discovery_log` table** (already exists):
```python
# For each query used:
# INSERT INTO discovery_log (search_query, contact_type, results_found, searched_at)
```

**Append to `data/discover_log.json`:**
```json
{
  "ts": "<ISO timestamp>",
  "agent": "rjm-discover",
  "part": "A",
  "queries_used": <number>,
  "query_slots_by_type": {"podcast": 4, "curator": 2, "blog": 2},
  "candidates_evaluated": <number>,
  "contacts_appended": <number>,
  "contacts_skipped_dedup": <number>,
  "contacts_skipped_avoid": <number>,
  "learning_signal_used": true|false
}
```

Then proceed to Part B.

---

## PART B — Spotify Playlist Discovery

### Step 1 — Pre-Check

Read `data/playlist_database.json`. Extract all existing playlist IDs/URLs into a dedup set.
Count current total. If ≥ 200, log skipped and exit immediately.

---

### Step 2 — Run 6 Parallel Searches

Use WebSearch. Run all 6 simultaneously:

1. `site:open.spotify.com/playlist "organic house" OR "tribal psytrance" followers`
2. `site:open.spotify.com/playlist "psytrance" OR "tribal" OR "Goa" followers`
3. `site:open.spotify.com/playlist "consciousness" OR "flow state" electronic`
4. `site:open.spotify.com/playlist "ethnic electronic" OR "world electronic" OR "desert electronic"`
5. `"spotify playlist" organic house OR Middle Eastern electronic curator contact submit 2025`
6. `"spotify playlist" psytrance tribal rave consciousness 5000 followers`

---

### Step 3 — Evaluate (max 15 candidates)

For each playlist, collect:

```
playlist_name | spotify_url | playlist_id | follower_count | genre_tags | curator_name | curator_contact (if findable)
```

**Skip immediately if:**
- Already in playlist_database.json
- Followers < 5,000
- Playlist name/description contains: "worship", "gospel", "CCM", "praise", "church", "Jesus" (as explicit category, not subtle)

**Tag each:**
- `audience_type`: `seeker` | `music-first` | `faith-adjacent` | `avoid`
- `priority`: `high` (>50K followers) | `medium` (10K–50K) | `low` (5K–10K)

---

### Step 4 — Update playlist_database.json

Append new entries. Flag top 5 by follower count as `priority: high` for this run.

---

### Step 5 — Log

Append to `data/discover_log.json`:

```json
{
  "ts": "<ISO timestamp>",
  "agent": "rjm-discover",
  "part": "B",
  "queries_used": <number>,
  "playlists_evaluated": <number>,
  "playlists_added": <number>,
  "database_total": <number>
}
```

Then STOP. Do not trigger any downstream agent.

---

## Execution Order

Run Part A first, then Part B. Both parts log to `data/discover_log.json` with `"agent": "rjm-discover"` and their respective `"part": "A"` or `"part": "B"` tag.
