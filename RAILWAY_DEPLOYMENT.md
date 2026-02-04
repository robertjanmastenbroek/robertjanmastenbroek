# Railway Deployment Guide
## Deploy Your Website to Railway ($5/mo plan)

Since you've already purchased Railway's $5/mo plan, here's how to deploy your website and set it up with auto-updates.

---

## What is Railway?

Railway is a modern hosting platform that's perfect for deploying websites and apps. Your $5/mo plan gives you:
- 500 GB bandwidth/month
- Automatic HTTPS
- Custom domain support
- Easy deployments

---

## Step 1: Get Your YouTube Channel ID

Your YouTube URL is: `https://www.youtube.com/@robertjanmastenbroekofficial`

But the YouTube API needs your **Channel ID** (format: `UC...`), not your handle.

### How to Get Your Channel ID:

**Method 1: YouTube Studio (Easiest)**
1. Go to [YouTube Studio](https://studio.youtube.com/)
2. Click **Settings** (bottom left, gear icon)
3. Click **Channel** → **Advanced settings**
4. Copy your **Channel ID** (starts with `UC`)
   - Example format: `UCaBcDeFgH123456789`

**Method 2: From Your Channel Page**
1. Go to your channel: `youtube.com/@robertjanmastenbroekofficial`
2. Right-click the page → **View Page Source**
3. Search for `"channelId"` (Ctrl+F / Cmd+F)
4. Copy the ID next to `"channelId": "UC..."`

**Method 3: Use a Tool**
- Go to: https://commentpicker.com/youtube-channel-id.php
- Paste your channel URL
- Get your Channel ID

---

## Step 2: Get Your YouTube API Key

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. **Create a new project:**
   - Click project dropdown (top bar)
   - Click "New Project"
   - Name it: "Robert-Jan Website"
   - Click "Create"
3. **Enable YouTube Data API v3:**
   - Click "Enable APIs and Services"
   - Search for "YouTube Data API v3"
   - Click it → Click "Enable"
4. **Create API Key:**
   - Go to "Credentials" (left sidebar)
   - Click "Create Credentials" → "API Key"
   - Copy the API key
5. **Restrict the API Key (IMPORTANT for security):**
   - Click "Edit API Key"
   - Under "API restrictions" → Select "Restrict key"
   - Check only "YouTube Data API v3"
   - Under "Website restrictions" → Add your domain
   - Click "Save"

---

## Step 3: Update index.html with Your IDs

Open `index.html` and find the configuration section (around line 486):

```javascript
// Configuration - IMPORTANT: Update these values!
const YOUTUBE_API_KEY = 'AIza...'; // ← Paste your API key here
const YOUTUBE_CHANNEL_ID = 'UC...'; // ← Paste your Channel ID here
const SPOTIFY_ARTIST_ID = 'YOUR_SPOTIFY_ARTIST_ID'; // ← Update when Spotify is live
```

**Example (with fake IDs):**
```javascript
const YOUTUBE_API_KEY = 'AIzaSyABC123def456GHI789jkl012MNO345pqr';
const YOUTUBE_CHANNEL_ID = 'UCaBcDeFgH123456789jKlMnOpQrStU';
const SPOTIFY_ARTIST_ID = 'YOUR_SPOTIFY_ARTIST_ID'; // Update later
```

Save the file.

---

## Step 4: Deploy to Railway

### Option A: Deploy via GitHub (Recommended - Auto-updates)

**Why this way:** Every time you push changes to GitHub, Railway automatically redeploys.

1. **Create a GitHub repository:**
   - Go to [github.com](https://github.com/)
   - Click "New repository"
   - Name it: `robertjan-website`
   - Make it **Private** (recommended)
   - Don't add README, gitignore, or license
   - Click "Create repository"

2. **Upload your website to GitHub:**
   ```bash
   # Open Terminal/Command Prompt in your website folder
   git init
   git add index.html
   git commit -m "Initial commit - Robert-Jan website"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/robertjan-website.git
   git push -u origin main
   ```

3. **Connect Railway to GitHub:**
   - Go to [railway.app](https://railway.app/) and log in
   - Click "New Project"
   - Click "Deploy from GitHub repo"
   - Select your `robertjan-website` repository
   - Railway will auto-detect it's a static site

4. **Configure the deployment:**
   - Railway should automatically detect `index.html`
   - No build command needed (it's a static site)
   - Deploy!

---

### Option B: Deploy Manually (Quickest Start)

1. **Install Railway CLI:**
   ```bash
   npm install -g @railway/cli
   ```
   Or download from: https://docs.railway.app/develop/cli

2. **Login to Railway:**
   ```bash
   railway login
   ```

3. **Initialize project:**
   ```bash
   cd /path/to/your/website/folder
   railway init
   ```

4. **Deploy:**
   ```bash
   railway up
   ```

5. **Get your URL:**
   ```bash
   railway domain
   ```

---

## Step 5: Add a Static Server Configuration

Since `index.html` is a static file, Railway needs to know how to serve it.

**Create a `Dockerfile` in the same folder as `index.html`:**

```dockerfile
FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

**Or use a simple Node.js server (Alternative):**

Create `server.js`:
```javascript
const express = require('express');
const path = require('path');
const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.static(__dirname));

app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, 'index.html'));
});

app.listen(PORT, () => {
    console.log(`Server running on port ${PORT}`);
});
```

Create `package.json`:
```json
{
  "name": "robertjan-website",
  "version": "1.0.0",
  "scripts": {
    "start": "node server.js"
  },
  "dependencies": {
    "express": "^4.18.2"
  }
}
```

Then deploy with:
```bash
railway up
```

---

## Step 6: Connect Your Custom Domain

1. **In Railway Dashboard:**
   - Go to your project
   - Click "Settings" tab
   - Scroll to "Domains"
   - Click "Add Domain"
   - Enter your domain (e.g., `robertjanmastenbroek.com`)

2. **In Your Domain Registrar (GoDaddy, Namecheap, etc.):**

   **Add these DNS records:**
   ```
   Type: CNAME
   Name: www
   Value: [your-railway-domain].railway.app
   TTL: 3600
   ```

   ```
   Type: A
   Name: @
   Value: [Railway will provide an IP]
   TTL: 3600
   ```

   Railway will give you the exact values after you add the domain.

3. **Wait for DNS propagation** (1-24 hours)

4. **Railway auto-provisions SSL certificate** (HTTPS)

---

## Step 7: Test Everything

After deployment:

1. **Visit your Railway URL** (e.g., `your-project.railway.app`)
2. **Check YouTube videos load** - Should show your 6 most recent videos
3. **Click social links** - Should go to your YouTube, Instagram, TikTok
4. **Test contact form** - Should show alert (until you connect a form service)
5. **Test on mobile** - Open on your phone

---

## Updating Your Website

### If using GitHub deployment:
```bash
# Make changes to index.html
git add index.html
git commit -m "Updated content"
git push
# Railway automatically redeploys!
```

### If using manual deployment:
```bash
railway up
```

---

## Cost Breakdown

- **Railway:** $5/mo (covers everything)
- **Domain:** ~$10-15/year (you already have this)
- **YouTube API:** FREE
- **Spotify Embed:** FREE

**Total: $5/month**

---

## Troubleshooting

### YouTube videos not loading:
- Check API key is correct
- Verify Channel ID starts with `UC`
- Check browser console (F12) for errors
- Make sure API key has "YouTube Data API v3" enabled

### Website not loading:
- Check Railway logs: `railway logs`
- Verify `index.html` is in the root folder
- Check if Dockerfile or `server.js` is properly configured

### Domain not connecting:
- DNS changes take time (up to 24 hours)
- Verify DNS records in your domain registrar
- Check Railway shows domain as "Active"

---

## Alternative: Keep Using Netlify

If Railway feels complex, **Netlify is still easier for static sites:**

1. Go to [netlify.com](https://netlify.com)
2. Drag `index.html` into upload area
3. Done!
4. Connect custom domain in settings

**Netlify is FREE** for static sites like this and might be simpler than Railway.

---

## When Your Spotify Goes Live

When your Spotify artist page is ready:

1. Get your Spotify Artist ID from your artist URL
2. Update line 490 in `index.html`:
   ```javascript
   const SPOTIFY_ARTIST_ID = 'your_actual_spotify_id';
   ```
3. Update line 407 (replace "Coming Soon" section) with:
   ```html
   <div class="music-card">
       <iframe
           class="spotify-embed"
           src="https://open.spotify.com/embed/artist/YOUR_SPOTIFY_ARTIST_ID"
           frameborder="0"
           allowtransparency="true"
           allow="encrypted-media">
       </iframe>
   </div>
   ```
4. Redeploy

---

## Next Steps

1. ✅ Get YouTube Channel ID (Method 1 in Step 1)
2. ✅ Get YouTube API Key (Step 2)
3. ✅ Update `index.html` with both IDs (Step 3)
4. ✅ Choose deployment method (GitHub or Manual)
5. ✅ Deploy to Railway (Step 4)
6. ✅ Connect custom domain (Step 6)
7. ✅ Test everything (Step 7)

---

**Your mission: Ancient Truth. Future Sound. Now you have the infrastructure to share it with the world.**
