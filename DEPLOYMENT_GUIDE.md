# Website Deployment Guide
## Robert-Jan Mastenbroek | Auto-Updating Music Website

This guide walks you through deploying your website and configuring auto-updates from YouTube and Spotify.

---

## Step 1: Get Your API Keys & IDs

### YouTube Setup

1. **Get Your YouTube Channel ID:**
   - Go to your YouTube channel
   - Click on your profile picture → "Your channel"
   - Copy the URL - it will look like: `youtube.com/channel/UC...` or `youtube.com/@YOUR_HANDLE`
   - If it's the @ format, you'll need to find the actual Channel ID:
     - Go to YouTube Studio → Settings → Channel → Advanced settings
     - Copy the "Channel ID"

2. **Get YouTube API Key:**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project (or select existing)
   - Enable "YouTube Data API v3"
   - Go to "Credentials" → "Create Credentials" → "API Key"
   - Copy the API key
   - **Restrict the key:** Click "Restrict Key" → HTTP referrers → Add your domain

### Spotify Setup

1. **Get Your Spotify Artist ID:**
   - Go to your Spotify artist page
   - Click the "..." menu → "Share" → "Copy link to artist"
   - The URL will look like: `open.spotify.com/artist/ABC123XYZ`
   - Copy the part after `/artist/` - that's your Artist ID

---

## Step 2: Update the Website Code

Open `index.html` and update these values:

### Line 365-367 (JavaScript Configuration):
```javascript
const YOUTUBE_API_KEY = 'YOUR_YOUTUBE_API_KEY'; // Paste your YouTube API key here
const YOUTUBE_CHANNEL_ID = 'YOUR_YOUTUBE_CHANNEL_ID'; // Paste your Channel ID here
const SPOTIFY_ARTIST_ID = 'YOUR_SPOTIFY_ARTIST_ID'; // Paste your Spotify Artist ID here
```

### Line 227 (Spotify Embed):
```html
src="https://open.spotify.com/embed/artist/YOUR_SPOTIFY_ARTIST_ID"
```
Replace `YOUR_SPOTIFY_ARTIST_ID` with your actual Spotify Artist ID.

### Footer Social Links (Lines 294-337):
Update all the social media URLs:
- Spotify: Replace `YOUR_SPOTIFY_ID`
- YouTube: Replace `YOUR_CHANNEL`
- Instagram: Replace `YOUR_HANDLE`
- SoundCloud: Replace `YOUR_HANDLE`

---

## Step 3: Hosting Options

### Option A: Netlify (Recommended - Easiest)

**Why Netlify:**
- Free tier is generous
- Automatic HTTPS
- Easy custom domain setup
- Drag-and-drop deployment

**Steps:**
1. Go to [netlify.com](https://www.netlify.com/) and sign up
2. Click "Add new site" → "Deploy manually"
3. Drag your `index.html` file into the upload area
4. Netlify will give you a URL like `random-name-123.netlify.app`
5. **Connect your domain:**
   - Go to "Domain settings"
   - Click "Add custom domain"
   - Enter your domain name
   - Follow the DNS configuration instructions (usually adding a CNAME record)

**Updating the site:**
- Make changes to `index.html`
- Go to Netlify dashboard → "Deploys" → Drag the new file

---

### Option B: Vercel (Great for Developers)

**Why Vercel:**
- Fast global CDN
- Excellent performance
- Easy GitHub integration

**Steps:**
1. Go to [vercel.com](https://vercel.com/) and sign up
2. Click "Add New" → "Project"
3. Choose "Deploy from template" or upload manually
4. Drag your `index.html` file
5. **Connect your domain:**
   - Go to "Settings" → "Domains"
   - Add your custom domain
   - Update your DNS settings as instructed

---

### Option C: GitHub Pages (Free, Version Control)

**Steps:**
1. Create a GitHub repository
2. Upload `index.html`
3. Go to repository Settings → Pages
4. Select main branch as source
5. Your site will be at `your-username.github.io/repo-name`
6. **Custom domain:** Add a CNAME file with your domain

---

## Step 4: DNS Configuration (Connect Your Domain)

You mentioned you already have a domain. Here's how to connect it:

1. **Log into your domain registrar** (GoDaddy, Namecheap, etc.)

2. **Find DNS settings** (usually called "DNS Management" or "Nameservers")

3. **Add these records:**

**For Netlify:**
```
Type: CNAME
Name: www
Value: [your-site].netlify.app
```
```
Type: A
Name: @
Value: 75.2.60.5
```

**For Vercel:**
```
Type: CNAME
Name: www
Value: cname.vercel-dns.com
```
```
Type: A
Name: @
Value: 76.76.21.21
```

4. **Wait 24-48 hours** for DNS propagation (usually faster, often within 1 hour)

---

## Step 5: Set Up Contact Form

The contact form currently shows an alert. For production, use one of these services:

### Option A: Formspree (Easiest)
1. Go to [formspree.io](https://formspree.io/)
2. Create an account
3. Create a new form
4. Replace the form submission code (line 408) with:
```javascript
fetch('https://formspree.io/f/YOUR_FORM_ID', {
    method: 'POST',
    body: JSON.stringify(data),
    headers: { 'Content-Type': 'application/json' }
})
.then(() => alert('Message sent successfully!'))
.catch(() => alert('Failed to send message. Please try again.'));
```

### Option B: Netlify Forms (If using Netlify)
1. Add `netlify` attribute to form tag:
```html
<form class="contact-form" name="contact" netlify>
```
2. Forms will appear in Netlify dashboard

### Option C: EmailJS
1. Go to [emailjs.com](https://www.emailjs.com/)
2. Create account and email service
3. Follow their integration guide

---

## Step 6: Testing Auto-Updates

After deployment:

1. **YouTube Videos:**
   - Videos auto-fetch from your channel (6 most recent)
   - Updates happen when visitors load the page
   - No manual refresh needed

2. **Spotify Player:**
   - Automatically shows your latest releases
   - Spotify handles the updates
   - Embedded player always pulls live data

---

## Step 7: Performance & SEO

### Add to `<head>` section for better SEO:
```html
<!-- Open Graph / Social Media -->
<meta property="og:type" content="website">
<meta property="og:url" content="https://your-domain.com/">
<meta property="og:title" content="Robert-Jan Mastenbroek | Electronic Worship">
<meta property="og:description" content="Melodic Techno & Tribal Psytrance - Bringing light into the darkness">
<meta property="og:image" content="https://your-domain.com/og-image.jpg">

<!-- Twitter -->
<meta property="twitter:card" content="summary_large_image">
<meta property="twitter:url" content="https://your-domain.com/">
<meta property="twitter:title" content="Robert-Jan Mastenbroek | Electronic Worship">
<meta property="twitter:description" content="Melodic Techno & Tribal Psytrance">
<meta property="twitter:image" content="https://your-domain.com/og-image.jpg">
```

### Google Analytics (Optional):
Add before `</head>`:
```html
<!-- Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-YOUR-ID"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-YOUR-ID');
</script>
```

---

## Troubleshooting

### YouTube Videos Not Loading:
- Check API key is correct
- Verify API key restrictions allow your domain
- Check Channel ID is correct
- Check browser console for errors (F12)

### Spotify Not Embedding:
- Verify Artist ID is correct
- Check if Spotify URL works when pasted in browser

### Domain Not Connecting:
- DNS changes take time (up to 48 hours)
- Verify CNAME/A records are correct
- Clear browser cache

### SSL Certificate Issues:
- Both Netlify and Vercel provide automatic HTTPS
- Wait 24 hours after domain connection
- Check "Force HTTPS" is enabled in hosting settings

---

## Maintenance

**To update content:**
1. Edit `index.html` locally
2. Re-upload to hosting provider
3. Changes appear immediately

**To add new sections:**
- Follow the existing HTML structure
- Keep the Dark/Holy/Futuristic aesthetic
- Maintain Subtle Salt approach in copy

**To change colors:**
- Edit CSS variables in `:root` (lines 18-24)

---

## Cost Breakdown

- **Domain:** ~$10-15/year (you already have this)
- **Hosting:** FREE (Netlify/Vercel/GitHub Pages free tiers)
- **YouTube API:** FREE (up to 10,000 requests/day - more than enough)
- **Spotify Embed:** FREE
- **Formspree:** FREE tier (50 submissions/month)

**Total: $0/month** (just domain renewal annually)

---

## Next Steps

1. ✅ Update API keys and IDs in `index.html`
2. ✅ Choose hosting provider (Netlify recommended)
3. ✅ Deploy website
4. ✅ Connect custom domain
5. ✅ Set up contact form
6. ✅ Test all functionality
7. ✅ Share with the world

---

## Support Resources

- **Netlify Docs:** https://docs.netlify.com/
- **Vercel Docs:** https://vercel.com/docs
- **YouTube API:** https://developers.google.com/youtube/v3
- **Spotify Embeds:** https://developer.spotify.com/documentation/embeds

---

**Questions?** The website is built with clean, commented code. Everything is in one file (`index.html`) for easy editing and deployment.

Your mission: **Bringing light into the darkness.** Now you have a digital home that does the same.
