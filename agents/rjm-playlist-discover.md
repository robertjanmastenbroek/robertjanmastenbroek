# Agent: rjm-playlist-discover
**Cadence:** 6×/day
**Role:** Build 200-playlist Spotify database — Spotify playlists ONLY

> NOTE: Podcasts, blogs, and non-Spotify curators are handled by `rjm-discover`.
> This agent touches only `data/playlist_database.json`.

---

## Hard Execution Limits (non-negotiable)
- **Max WebSearch queries per run: 6**
- **Max playlists to evaluate: 15**
- **Target net-new added: 5–10**
- **Stop condition:** once `playlist_database.json` has 200 entries, skip all future runs and log `{"skipped": true, "reason": "database_complete"}`

---

## Step 1 — Pre-Check

Read `data/playlist_database.json`. Extract all existing playlist IDs/URLs into a dedup set.
Count current total. If ≥ 200, log skipped and exit immediately.

---

## Step 2 — Run 6 Parallel Searches

Use WebSearch. Run all 6 simultaneously:

1. `site:open.spotify.com/playlist "melodic techno" followers`
2. `site:open.spotify.com/playlist "psytrance" OR "tribal" followers`
3. `site:open.spotify.com/playlist "consciousness" OR "flow state" electronic`
4. `site:open.spotify.com/playlist "ethnic electronic" OR "world electronic"`
5. `"spotify playlist" melodic techno curator contact submit 2025`
6. `"spotify playlist" psytrance tribal rave consciousness 5000 followers`

---

## Step 3 — Evaluate (max 15 candidates)

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

## Step 4 — Update playlist_database.json

Append new entries. Flag top 5 by follower count as `priority: high` for this run.

---

## Step 5 — Log

Append to `data/discover_log.json`:

```json
{
  "ts": "<ISO timestamp>",
  "agent": "rjm-playlist-discover",
  "queries_used": <number>,
  "playlists_evaluated": <number>,
  "playlists_added": <number>,
  "database_total": <number>
}
```

Then STOP.
