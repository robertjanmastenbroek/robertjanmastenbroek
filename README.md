# Robert-Jan Mastenbroek Website Package

## What's Included

- **index.html** - Your complete website (single file, ready to deploy)
- **DEPLOYMENT_GUIDE.md** - Detailed setup instructions for hosting & APIs
- **BRAND_DNA.md** - Your master brand blueprint (Subtle Salt protocol, tactical copy rules)

---

## Your Brand Tagline

**"ANCIENT TRUTH. FUTURE SOUND."**

This is now your hero tagline — perfect Subtle Salt. Biblical weight without a single religious word.

---

## Quick Start (5 Minutes to Live Site)

### 1. Update Your API Keys
Open `index.html` and find line 486. Replace these placeholders:
```javascript
const YOUTUBE_API_KEY = 'YOUR_YOUTUBE_API_KEY';
const YOUTUBE_CHANNEL_ID = 'YOUR_YOUTUBE_CHANNEL_ID';
const SPOTIFY_ARTIST_ID = 'YOUR_SPOTIFY_ARTIST_ID';
```

**How to get these:**
- **YouTube API Key:** [Google Cloud Console](https://console.cloud.google.com/) → Enable YouTube Data API v3
- **YouTube Channel ID:** YouTube Studio → Settings → Channel → Advanced → Copy the ID that starts with `UC`
  - Your handle is `@robertjanmastenbroekofficial` but you need the Channel ID (format: `UCaBc123...`)
  - Full instructions in `RAILWAY_DEPLOYMENT.md`
- **Spotify Artist ID:** Coming soon - update when your Spotify page is live

### 2. Deploy to Railway
Since you already have Railway's $5/mo plan, follow the detailed guide:
- See **`RAILWAY_DEPLOYMENT.md`** for complete step-by-step instructions
- Covers: GitHub deployment, manual deployment, domain connection, troubleshooting

**Alternative:** Netlify (free, simpler for static sites) - see `DEPLOYMENT_GUIDE.md`

### 3. Connect Your Domain
- Full instructions in **`RAILWAY_DEPLOYMENT.md`**
- DNS setup with your registrar
- Railway auto-provisions SSL (HTTPS)

---

## What This Website Does

✅ **Auto-updates** with your latest YouTube videos (pulls 6 most recent)
✅ **Auto-updates** with your latest Spotify releases (embedded player)
✅ **Dark/Holy/Futuristic** aesthetic (Anyma scale, Rüfüs mood, Argy texture)
✅ **Subtle Salt** storytelling (discovery over announcement)
✅ **Fully responsive** (perfect on phone, tablet, desktop)
✅ **One-file deployment** (no build process, no dependencies)
✅ **Contact form** ready (just needs form service connected)

---

## Website Sections

1. **Hero** - Your name, genre, mission statement
2. **Latest Releases** - Auto-updating Spotify + YouTube
3. **Story** - "Electronic Worship" explained (Subtle Salt approach)
4. **Booking** - Contact form for event inquiries
5. **Footer** - Social links to all platforms

---

## What Makes This Site "Subtle Salt"

✅ **No "Christianese"** - No overt religious language
✅ **Discovery over announcement** - "Bringing light into the darkness" (not "Christian DJ")
✅ **Sacred weight** - "126 BPM where ancient rhythm meets modern pulse"
✅ **Professional, cool, credible** - Secular venues welcome, spiritual depth discoverable

Example from the Story section:
> "In dark rooms filled with searching souls, **electronic worship** emerges—not as announcement, but as discovery."

---

## Customization

### Change Colors:
Edit lines 18-24 in `index.html`:
```css
:root {
    --dark-bg: #0a0a0a;
    --accent-gold: #d4af37;
    --accent-blue: #4a90e2;
}
```

### Update Story:
Edit lines 252-270 in `index.html`

### Add Sections:
Follow the existing `<section>` structure

---

## Your Social Links (Already Connected)

- **YouTube:** https://www.youtube.com/@robertjanmastenbroekofficial
- **Instagram:** https://www.instagram.com/robertjanmastenbroek/
- **TikTok:** https://tiktok.com/@robertjanmastenbroek
- **Spotify:** Coming soon

All links are already in the website footer and working.

---

## Cost

- **Railway:** $5/mo (your current plan)
- **YouTube API:** FREE
- **Spotify Embed:** FREE
- **Domain:** You already own it

**Total: $5/month**

*(Alternative: Netlify FREE tier also works - see DEPLOYMENT_GUIDE.md)*

---

## Next Steps

1. **Today:** Update API keys → Deploy to Netlify → Test
2. **This week:** Connect custom domain → Set up contact form
3. **Ongoing:** Post new music → Website auto-updates → No manual work

---

## Technical Details

- **Built with:** Pure HTML/CSS/JavaScript (no frameworks)
- **APIs:** YouTube Data API v3, Spotify Embed API
- **Hosting:** Static site (works on any host)
- **Performance:** Lightweight, fast loading
- **SEO:** Optimized meta tags, semantic HTML

---

## Support

Everything you need is in **DEPLOYMENT_GUIDE.md** - step-by-step instructions for:
- Getting API keys
- Deploying to hosting
- Connecting custom domain
- Setting up contact form
- Troubleshooting common issues

---

**Your mission:** Bringing light into the darkness.
**This website:** Your digital home for that mission.

Let the music speak. 🎵
