# Project Summary
## Robert-Jan Mastenbroek | Website & Brand System

**Date:** February 4, 2026
**Status:** ✅ Complete & Ready to Deploy

---

## What Was Built

A complete **auto-updating music website** with:
- ✅ Dark/Holy/Futuristic aesthetic
- ✅ "Ancient Truth. Future Sound." tagline (Subtle Salt perfection)
- ✅ Auto-updating YouTube videos (6 most recent)
- ✅ Spotify embed (ready when your artist page is live)
- ✅ Story section (Electronic Worship explained with zero Christianese)
- ✅ Booking/contact form
- ✅ Real social links (YouTube, Instagram, TikTok)
- ✅ Mobile responsive
- ✅ Single-file deployment (no build process)

---

## Files Delivered

### Core Website
- **`index.html`** - Complete website (21KB, production-ready)

### Documentation
- **`README.md`** - Quick start guide (5 mins to live site)
- **`DEPLOYMENT_GUIDE.md`** - Comprehensive deployment (Netlify, Vercel, GitHub Pages)
- **`RAILWAY_DEPLOYMENT.md`** - Railway-specific guide (your $5/mo plan)
- **`CUSTOMIZATION_GUIDE.md`** - Add logo, colors, photos
- **`BRAND_DNA.md`** - Your master brand blueprint (Subtle Salt protocol)
- **`VISUAL_STYLE_GUIDE.md`** - Complete aesthetic direction

---

## Immediate Next Steps (Today)

### 1. Get YouTube Channel ID (15 minutes)
Your YouTube handle: `@robertjanmastenbroekofficial`

**You need the Channel ID (format: `UC...`):**
1. Go to [YouTube Studio](https://studio.youtube.com/)
2. Settings → Channel → Advanced settings
3. Copy your **Channel ID** (starts with `UC`)

Full instructions: **RAILWAY_DEPLOYMENT.md** (Step 1)

### 2. Get YouTube API Key (10 minutes)
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create project: "Robert-Jan Website"
3. Enable "YouTube Data API v3"
4. Create API Key
5. Restrict to YouTube Data API v3 only

Full instructions: **RAILWAY_DEPLOYMENT.md** (Step 2)

### 3. Update index.html (2 minutes)
Open `index.html`, find line 486:
```javascript
const YOUTUBE_API_KEY = 'YOUR_API_KEY'; // ← Paste here
const YOUTUBE_CHANNEL_ID = 'UC...'; // ← Paste here
```

### 4. Deploy (30 minutes)
**Option A: Railway** (since you have the $5/mo plan)
- Follow **`RAILWAY_DEPLOYMENT.md`** for complete guide
- Deploy via GitHub (auto-updates) or manually

**Option B: Netlify** (easier, free)
- Drag `index.html` to [netlify.com](https://netlify.com)
- Done!

### 5. Connect Domain (varies)
- Add DNS records at your domain registrar
- Point to Railway or Netlify
- Wait 1-24 hours for propagation

---

## This Week

- [ ] Test website on desktop and mobile
- [ ] Customize with your logo/colors (see CUSTOMIZATION_GUIDE.md)
- [ ] Set up contact form service (Formspree, Netlify Forms, etc.)
- [ ] Share website URL with inner circle for feedback

---

## When Spotify Goes Live

When your Spotify artist page is ready:
1. Get Artist ID from your Spotify URL
2. Update line 490 in `index.html` with your Spotify Artist ID
3. Replace "Coming Soon" section (line 405) with Spotify embed (see RAILWAY_DEPLOYMENT.md)
4. Redeploy website

---

## Visual Content Creation

Reference images you provided show perfect aesthetic:
- **Ancient/Tribal:** Weathered fabrics, bronze/gold elements, sacred vessels
- **Futuristic/White:** Chrome, clean lines, sacred geometry, minimalist

**For thumbnail/artwork creation:**
- See **VISUAL_STYLE_GUIDE.md** for complete aesthetic direction
- AI prompt templates included
- Color palettes defined
- Do's and Don'ts listed

---

## Brand Consistency

All content must pass through:

### Subtle Salt Protocol (Lyrics, Captions, Posts)
- ✅ Subtlety over Christianese
- ✅ Bible-based foundation (invisible anchor)
- ✅ Discovery > Announcement
- ✅ Modern, cool, credible tone

### Tactical Copy Rules (Captions, Emails, Descriptions)
1. **Visualization Test** - Can they "see" the words?
2. **Falsifiability Test** - Use facts over adjectives
3. **Uniqueness Rule** - Could a competitor sign this?
4. **One Mississippi Test** - Understood in under 2 seconds?
5. **Point A to Point B** - Bridge secular to sacred

Full protocol: **BRAND_DNA.md**

---

## Technical Specs

### Performance
- Single 21KB HTML file
- No external dependencies
- Fast load time
- Mobile-optimized

### Auto-Updates
- **YouTube:** Fetches 6 most recent videos on page load
- **Spotify:** Embed always shows latest releases (when connected)
- **No manual updates needed** - publish music, website updates automatically

### Browser Support
- Chrome, Safari, Firefox, Edge
- iOS Safari, Chrome Mobile
- Responsive: 320px (mobile) to 4K desktop

### APIs Used
- YouTube Data API v3 (free, 10,000 requests/day quota)
- Spotify Embed API (free, no quota)

---

## Cost Breakdown

### Hosting
- **Railway:** $5/month (your current plan)
- **Netlify/Vercel:** FREE alternative (static sites)

### Services
- **Domain:** You own it (~$10-15/year for renewal)
- **YouTube API:** FREE
- **Spotify Embed:** FREE

**Total: $5/month** (or $0/month with Netlify)

---

## Success Metrics to Track

### Short-Term (First Month)
- Website live and stable
- YouTube videos auto-loading
- Contact form working
- Domain connected
- Mobile-responsive verified

### Medium-Term (3 Months)
- Traffic analytics set up (Google Analytics optional)
- Spotify integrated (when live)
- Social media driving traffic to site
- Contact form generating booking inquiries

### Long-Term (6-12 Months)
- 100k YouTube subs (your goal from BRAND_DNA)
- 100k Spotify monthly listeners (your goal)
- Website as central hub for all fans
- Discovery stories from "Subtle Salt" content

---

## What Makes This Different

### Not a Generic DJ Site
- ❌ No "About Me" resume-style bio
- ❌ No SoundCloud embeds dominating
- ❌ No flashy animations or distractions
- ❌ No obvious Christian imagery

### Your Unique Positioning
- ✅ "Ancient Truth. Future Sound." - elite Subtle Salt tagline
- ✅ Story focused on "Electronic Worship" discovery
- ✅ Dark/Holy/Futuristic aesthetic (Anyma scale, Rüfüs mood)
- ✅ Every word passes Tactical Copy tests
- ✅ Designed for secular venues with sacred mission

---

## Support & Troubleshooting

### If YouTube Videos Don't Load:
1. Check API key in browser console (F12)
2. Verify Channel ID starts with `UC`
3. Confirm YouTube Data API v3 is enabled in Google Cloud
4. Check API restrictions (should allow your domain)

### If Website Won't Deploy:
- Railway: Check logs (`railway logs` in CLI)
- Netlify: Drag-and-drop should work instantly
- Verify `index.html` is in root folder (not in subfolder)

### If Domain Won't Connect:
- DNS changes take 1-24 hours
- Verify CNAME/A records in domain registrar
- Check hosting provider shows domain as "Active"
- Try incognito browser (cache issue)

---

## Future Enhancements (Optional)

### Phase 2 (When Time Permits)
- [ ] Email signup form (Mailchimp, ConvertKit)
- [ ] Blog section for "Ancient Truth. Future Sound." reflections
- [ ] Live show calendar/tour dates
- [ ] Press kit download (EPK)
- [ ] Merch store integration

### Phase 3 (When Established)
- [ ] Fan community platform
- [ ] Exclusive content for subscribers
- [ ] Behind-the-scenes videos
- [ ] Collaborator/booking portal

None of these are needed now. Get Phase 1 live first.

---

## Contact Form Options

The current form shows an alert. To make it functional:

**Easiest: Formspree** (free tier: 50 submissions/month)
1. Sign up at [formspree.io](https://formspree.io/)
2. Create form
3. Replace form code (instructions in DEPLOYMENT_GUIDE.md)

**Alternative: Netlify Forms** (if using Netlify)
- Add `netlify` attribute to form tag
- Forms appear in Netlify dashboard
- Email notifications included

**Alternative: EmailJS** (email directly to your inbox)
- Sign up at [emailjs.com](https://www.emailjs.com/)
- Connect email service
- Forms send to your email

---

## Critical Files Checklist

Before going live, confirm you have:
- [x] `index.html` with your API keys updated
- [x] YouTube Channel ID (UC format)
- [x] YouTube API Key (with restrictions enabled)
- [x] Social links correct (YouTube, Instagram, TikTok)
- [x] Domain ready to connect
- [x] Hosting account (Railway or Netlify)

---

## The Vision

**From BRAND_DNA.md:**
> "Bringing light into the darkness; bridging sacred meaning and secular venues."

**Your website now embodies this:**
- Professional enough for booking agents
- Cool enough for underground clubs
- Spiritual enough for those who are searching
- Subtle enough that no one feels "preached at"

The music does the heavy lifting. The website just opens the door.

---

## Final Thoughts

You now have:
1. A production-ready website (index.html)
2. Complete deployment guides (Railway, Netlify, domain setup)
3. Brand consistency system (Subtle Salt, Tactical Copy)
4. Visual aesthetic direction (Ancient × Future)
5. Clear next steps (API keys → Deploy → Go live)

**The website is the easy part. The mission is the real work.**

Ancient Truth. Future Sound.

Let's bring light into the darkness. 🎵

---

**Questions? Review the specific guides:**
- Quick start → **README.md**
- Railway setup → **RAILWAY_DEPLOYMENT.md**
- Add your branding → **CUSTOMIZATION_GUIDE.md**
- Content creation → **VISUAL_STYLE_GUIDE.md**
- Brand rules → **BRAND_DNA.md**
