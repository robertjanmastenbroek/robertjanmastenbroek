# Agent: rjm-discover
**Cadence:** 6×/day
**Role:** Podcast + non-Spotify curator discovery — 10 net-new contacts per run

> NOTE: Spotify playlist discovery is handled exclusively by `rjm-playlist-discover`.
> This agent covers: podcasts, SoundCloud curators, Mixcloud, Bandcamp labels, blogs, editorial contacts.

---

## Hard Execution Limits (non-negotiable)
- **Max WebSearch queries per run: 8**
- **Max contacts to evaluate: 20**
- **Target net-new appended: 10**
- **STOP after 8 searches**, even if target isn't reached — append what you have and exit

---

## Step 0 — Intelligence Brief (run before any search)

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

## Step 1 — Assemble 8 Search Queries

Based on the gap analysis from Step 0, assemble exactly 8 queries. Prioritize gaps.

**Podcast query templates (use when podcast is a gap):**
- `"[genre] podcast" host guest submit electronic music 2025`
- `"consciousness music podcast" electronic rave festival festival 2025`
- `"electronic music podcast" [target market: Spain|Germany|Netherlands|UK] 2025`
- `"spiritual not religious podcast" music rave DJ interview`

**Curator/blog query templates (use when curator/blog is a gap):**
- `"melodic techno blog" submit demos contact email 2025`
- `"[genre] curator" mixcloud soundcloud contact booking email`
- `"ethnic electronic music" world fusion rave curator blog 2025`
- `"underground techno" editorial blog playlist curator email submit`

**Target markets (inject into queries for geographic diversification):**
Spain, Germany, Netherlands, UK, Poland, France, Brazil, US
— rotate markets across runs based on discovery_log recency

---

## Step 2 — Run Queries in Parallel

Fire all 8 WebSearch queries **simultaneously** (single parallel call block). Do not wait for one before launching the next.

---

## Step 3 — Evaluate Results (max 20 candidates)

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

## Step 4 — Append to contacts.csv

Format per row:
```
email, name, type, genre, notes, status, date_added, date_sent, bounce, audience_type
```
- `type`: `podcast` | `curator` | `blog` | `label`
- `status`: `new`
- `date_sent`: blank
- `bounce`: `unknown`

---

## Step 5 — Log to DB + File

**Write to `outreach_agent/db.py` `discovery_log` table** (already exists):
```python
# For each query used:
# INSERT INTO discovery_log (search_query, contact_type, results_found, searched_at)
```

**Append to `data/discover_log.json`:**
```json
{
  "ts": "<ISO timestamp>",
  "queries_used": <number>,
  "query_slots_by_type": {"podcast": 4, "curator": 2, "blog": 2},
  "candidates_evaluated": <number>,
  "contacts_appended": <number>,
  "contacts_skipped_dedup": <number>,
  "contacts_skipped_avoid": <number>,
  "learning_signal_used": true|false
}
```

Then STOP. Do not trigger any downstream agent.
