# Metrics OAuth Setup — Unblock the Learning Loop

The bandit in `content_engine/learning_loop.py` runs every night at 18:00 CET
and computes arm weights from three signal sources:

1. **Instagram Graph API** — reach, saves, shares, comments, plays per post
2. **YouTube Data API v3 + Analytics API v2** — views, likes, comments + completion rate
3. **Spotify Web API** — daily follower delta attribution to post batches

Currently:

| Source | Status | What's missing |
|---|---|---|
| IG Graph API | ❌ dead | Token expired, `INSTAGRAM_USER_ID` is a username not a numeric ID |
| YT Data API v3 | ✅ working | — (just API key, no OAuth needed) |
| YT Analytics v2 | ❌ 403 | Existing token only has `youtube.upload`, needs `yt-analytics.readonly` |
| Spotify API | ❌ not set | Needs `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` |

Follow the three sections below and the loop will start producing real
reward signal within 24 hours. **All three can be done in one sitting.**

---

## 1. Instagram Graph API — ~10 minutes

The Instagram Graph API requires the numeric **Business Account ID**
(not a handle) and a long-lived access token with `instagram_manage_insights`.

### Steps

1. Open the Meta for Developers app you're already using:
   <https://developers.facebook.com/apps/>

2. Click your Holy Rave app → **Tools → Graph API Explorer**.

3. In the **User or Page** dropdown, select **Get User Access Token**.

4. Add these permissions:
   - `instagram_basic`
   - `instagram_manage_insights`
   - `pages_show_list`
   - `pages_read_engagement`
   - `business_management`

5. Click **Generate Access Token** → log in to the Facebook account that
   manages `@holyraveofficial` → grant permissions.

6. Copy the token. This is a **short-lived** token (1 hour).

7. Exchange it for a **long-lived** token (60 days) by calling this in a
   terminal — replace `{APP_ID}`, `{APP_SECRET}` (from the app's Basic
   Settings page), and `{SHORT_TOKEN}` with the values from step 6:

   ```bash
   curl -s "https://graph.facebook.com/v21.0/oauth/access_token?\
   grant_type=fb_exchange_token&\
   client_id={APP_ID}&\
   client_secret={APP_SECRET}&\
   fb_exchange_token={SHORT_TOKEN}"
   ```

   The response contains `"access_token": "EAA..."` — that's the long-lived
   token.

8. Find the **Instagram Business Account numeric ID** by calling:

   ```bash
   curl -s "https://graph.facebook.com/v21.0/me/accounts?access_token={LONG_TOKEN}"
   ```

   Pick your page (Holy Rave). Note its `id`. Then:

   ```bash
   curl -s "https://graph.facebook.com/v21.0/{PAGE_ID}?\
   fields=instagram_business_account&\
   access_token={LONG_TOKEN}"
   ```

   The `instagram_business_account.id` field is a numeric string like
   `17841405822555555`. **That** is the value `INSTAGRAM_USER_ID` must
   carry — not the handle `@robertjanmastenbroek`.

9. Update `.env` (replace both lines):

   ```
   INSTAGRAM_ACCESS_TOKEN={long_lived_token_from_step_7}
   INSTAGRAM_USER_ID={numeric_id_from_step_8}
   ```

10. Verify:

    ```bash
    python3.13 -c "
    import os, requests
    from pathlib import Path
    for line in Path('.env').read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k,_,v = line.partition('='); os.environ[k.strip()] = v.strip()
    tok = os.environ['INSTAGRAM_ACCESS_TOKEN']
    uid = os.environ['INSTAGRAM_USER_ID']
    r = requests.get(f'https://graph.facebook.com/v21.0/{uid}/media',
                     params={'fields':'id,caption,timestamp','limit':3,'access_token':tok})
    print(r.status_code, r.json())
    "
    ```

    Expected: `200` plus a JSON list of your 3 most recent IG posts.

11. Refresh reminder: long-lived tokens expire every 60 days. Put a
    calendar note 55 days out, or add a cron job that POSTs the refresh
    endpoint weekly (Meta re-ups the 60 days each time as long as the
    token is still valid).

---

## 2. YouTube Analytics API v2 — ~5 minutes

The existing `YOUTUBE_OAUTH_TOKEN` in `.env` only has the `youtube.upload`
scope (it was minted when Buffer / the uploader was set up). To read
retention / completion_rate, we need an additional scope.

### Steps

1. Go to <https://console.cloud.google.com/apis/credentials> and open the
   project that owns the existing YouTube OAuth client (the one that issued
   the current `YOUTUBE_OAUTH_TOKEN`).

2. Make sure **YouTube Data API v3** and **YouTube Analytics API** are both
   enabled under **APIs & Services → Library**. Enable YouTube Analytics
   API if it isn't already.

3. Go to **OAuth consent screen → Scopes → Add or Remove Scopes** and make
   sure these four are listed:
   - `.../auth/youtube.upload`
   - `.../auth/youtube.readonly`
   - `.../auth/yt-analytics.readonly`
   - `.../auth/yt-analytics-monetary.readonly` (optional, for revenue)

   If you add new scopes, republish the OAuth consent.

4. Generate a fresh refresh token via the OAuth 2.0 Playground:
   <https://developers.google.com/oauthplayground/>

   a. Click the gear icon (top right) → **Use your own OAuth credentials** →
      paste the Client ID + Client Secret from step 1.

   b. In Step 1, scroll to **YouTube Data API v3** and tick
      `https://www.googleapis.com/auth/youtube.upload` and
      `https://www.googleapis.com/auth/youtube.readonly`.

   c. Scroll further to **YouTube Analytics API v2** and tick
      `https://www.googleapis.com/auth/yt-analytics.readonly`.

   d. Click **Authorize APIs** → sign in with the Google account that
      owns the RJM YouTube channel → grant permissions.

   e. Click **Exchange authorization code for tokens**. Copy both the
      **access token** and the **refresh token**.

5. Update `.env`:

   ```
   YOUTUBE_OAUTH_TOKEN={access_token_from_step_4e}
   YOUTUBE_REFRESH_TOKEN={refresh_token_from_step_4e}
   YOUTUBE_CLIENT_ID={your_oauth_client_id}
   YOUTUBE_CLIENT_SECRET={your_oauth_client_secret}
   ```

6. Verify — this should return rows, not `403 Insufficient Permission`:

   ```bash
   python3.13 -c "
   import os, requests
   from pathlib import Path
   for line in Path('.env').read_text().splitlines():
       if '=' in line and not line.startswith('#'):
           k,_,v = line.partition('='); os.environ[k.strip()] = v.strip()
   tok = os.environ['YOUTUBE_OAUTH_TOKEN']
   r = requests.get(
       'https://youtubeanalytics.googleapis.com/v2/reports',
       params={
           'ids':'channel==MINE',
           'metrics':'views,averageViewPercentage',
           'startDate':'2026-03-20','endDate':'2026-04-16',
           'dimensions':'video'},
       headers={'Authorization': f'Bearer {tok}'})
   print(r.status_code, str(r.json())[:300])
   "
   ```

   Expected: `200` with `rows` containing `[video_id, views, pct]` triples.

7. Access tokens expire after 1 hour. The refresh token never expires
   (until revoked) so the Python side can mint new access tokens on demand.
   `content_engine/learning_loop.py` does not auto-refresh yet — if a run
   fails with 401, paste a fresh access token into `.env` (or add an auto
   refresh helper, which is trivial with the refresh token saved).

---

## 3. Spotify Web API (follower watcher) — ~3 minutes

Client-credentials OAuth — no user login, no browser dance, free tier.

### Steps

1. Go to <https://developer.spotify.com/dashboard>.

2. Log in with the Spotify account tied to the RJM artist (or any account —
   this is a developer app, it doesn't need to own the artist).

3. Click **Create app**:
   - **App name:** `RJM follower watcher`
   - **App description:** `Daily follower count for learning loop`
   - **Redirect URI:** `http://localhost:8888` (required but unused)
   - Check the box for **Web API**
   - Agree to the ToS → **Save**

4. Open the app → **Settings** → copy the **Client ID** and **Client Secret**.

5. Update `.env`:

   ```
   SPOTIFY_CLIENT_ID={client_id}
   SPOTIFY_CLIENT_SECRET={client_secret}
   ```

6. Verify:

   ```bash
   python3.13 content_engine/spotify_watcher.py --dry-run
   ```

   Expected:
   ```
   spotify_watcher: Robert-Jan Mastenbroek — followers=NNN  popularity=NN
   spotify_watcher: [dry-run] not writing
   ```

7. Schedule the daily watcher via launchd (one-time setup). Create
   `~/Library/LaunchAgents/com.rjm.spotify-watcher.plist` with a
   StartCalendarInterval firing at 02:00 local every day, running
   `python3.13 content_engine/spotify_watcher.py`. `launchctl load` it.

---

## Verify the loop end-to-end

After all three sections are done:

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre"
python3.13 content_engine/spotify_watcher.py       # writes today's follower row
python3.13 content_engine/learning_loop.py         # full pass
python3.13 content_engine/learning_loop.py --show  # pretty print the snapshot
```

Look for:

- `YT: N/N basic, N/N retention` — retention should now be non-zero
- `IG media in window: N` — should discover recent IG posts
- `Spotify rows loaded: N` — should be ≥ the number of days since setup
- `Computed weights: n=N, ε=0.20, pooled=X.XX` — pooled reward should be > 0

The next daily content run will automatically pick up the new snapshot via
`content_engine.learning_loop.load_latest_weights()` — no code change needed.

---

## What each scope actually unlocks

| Scope | Unlocks in reward formula |
|---|---|
| `instagram_manage_insights` | `saves_per_reach`, `shares_per_reach`, `comments_per_reach` (0.45 weight combined) |
| `yt-analytics.readonly` | `completion_rate` (0.35 weight — biggest single signal) |
| Spotify client-credentials | `listener_delta_share` (0.20 weight — north-star proxy) |

Without any of them: reward is 0 for every post, the bandit falls back
to uniform random + ε=0.20 exploration. Harmless but not learning.

With all three: the bandit can discriminate mechanisms within ~2 weeks
of real posting data. Once `sample_size >= 30` it flips to warm-regime
ε=0.10 and starts exploiting the clearly-winning arms.
