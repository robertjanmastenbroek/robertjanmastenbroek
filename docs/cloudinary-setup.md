# Cloudinary Setup — One Env Var, Done

Every daily run currently warns:

```
⚠ CLOUDINARY_URL not set — add it to Railway env vars (see video_host.py header)
```

This happens because video uploads fall back to uguu.se, which has a 48-hour expiry — meaning any Buffer post scheduled more than two days out may arrive with a dead video link. One env var fixes this permanently.

---

## Step 1 — Get your Cloudinary URL

1. Go to [cloudinary.com](https://cloudinary.com) and create a free account (no card required).
2. The free tier gives you **25 GB/month** of bandwidth — more than enough for daily clip uploads.
3. After sign-in you land on the **Dashboard**. Look for the box labelled **"API Environment variable"**. It contains a value in exactly this format:

   ```
   cloudinary://API_KEY:API_SECRET@CLOUD_NAME
   ```

   Click the copy icon next to it — copy the whole string including `cloudinary://`.

---

## Step 2 — Set the env var

### On Railway (production)

1. Open your Railway project → **Variables** tab.
2. Add a new variable:
   - **Name:** `CLOUDINARY_URL`
   - **Value:** paste the full string you copied, e.g. `cloudinary://123456789012345:AbCdEfGhIjKlMnOpQrStUvWxYz@your-cloud-name`
3. Deploy / redeploy — Railway injects it automatically into every run.

### Locally (.env file)

Add one line to `.env` in the project root (create the file if it doesn't exist):

```
CLOUDINARY_URL=cloudinary://API_KEY:API_SECRET@CLOUD_NAME
```

Replace `API_KEY`, `API_SECRET`, and `CLOUD_NAME` with the actual values from the Cloudinary dashboard. The `.env` file is already in `.gitignore` — never commit it.

---

## Step 3 — Verify it works

Run a single outreach agent cycle or trigger a manual clip upload. You should see:

```
→ [Cloudinary] https://res.cloudinary.com/your-cloud-name/video/upload/v.../holy-rave/clip.mp4
```

The `⚠ CLOUDINARY_URL not set` warning disappears immediately.

---

## What changes after it's set

| Before | After |
|--------|-------|
| Videos uploaded to uguu.se (48h expiry) | Videos uploaded to Cloudinary CDN (no expiry) |
| Buffer posts may break if scheduled 2+ days out | Buffer posts are stable indefinitely |
| Daily run warns on every cycle | Warning gone; silent success |
| No cleanup — old files accumulate on uguu.se | `cleanup_old_cloudinary_uploads()` auto-deletes videos older than 7 days from the `holy-rave/` folder |

Uploaded videos land in a `holy-rave/` folder inside your Cloudinary account, so they're easy to browse or delete manually from the Cloudinary Media Library if needed.
