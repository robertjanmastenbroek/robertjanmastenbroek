# Visual Customization Guide
## Adding Your Logo, Brand Colors & Photos

You mentioned you have your logo, brand colors, and artist photos ready. Here's exactly how to add them to your website.

---

## 1. Adding Your Logo

### Option A: Replace Text Logo with Image

**Current (line 162):**
```html
<div class="logo">Robert-Jan Mastenbroek</div>
```

**Replace with:**
```html
<div class="logo">
    <img src="logo.png" alt="Robert-Jan Mastenbroek" style="height: 40px;">
</div>
```

**Steps:**
1. Save your logo as `logo.png` in the same folder as `index.html`
2. Adjust `height: 40px` to match your design (try 30px-50px)
3. If you want the logo to link to home, wrap it in: `<a href="#top">...</a>`

### Option B: Logo + Text Combo
```html
<div class="logo" style="display: flex; align-items: center; gap: 10px;">
    <img src="logo-icon.png" alt="" style="height: 30px;">
    <span>Robert-Jan Mastenbroek</span>
</div>
```

---

## 2. Adding Your Brand Colors

Find your brand color hex codes (e.g., `#FF5733`), then update the CSS variables:

**Lines 18-24 in `index.html`:**
```css
:root {
    --dark-bg: #0a0a0a;           /* Main background - keep dark */
    --secondary-bg: #1a1a1a;      /* Card backgrounds - slightly lighter */
    --accent-gold: #d4af37;       /* 👈 REPLACE with your primary accent color */
    --accent-blue: #4a90e2;       /* 👈 REPLACE with your secondary accent color */
    --text-primary: #ffffff;      /* Main text - keep white */
    --text-secondary: #b0b0b0;    /* Secondary text - keep gray */
    --border-subtle: rgba(255, 255, 255, 0.1); /* Subtle borders */
}
```

**What each color affects:**
- `--accent-gold` → Underlines, hover states, tagline, button background
- `--accent-blue` → Hero glow effect, video card hover borders
- `--dark-bg` → Main background color
- `--secondary-bg` → Cards, form background

**Example Brand Color Setup:**
```css
/* If your brand is purple/pink: */
--accent-gold: #9D4EDD;  /* Primary purple */
--accent-blue: #FF006E;  /* Accent pink */

/* If your brand is teal/orange: */
--accent-gold: #F77F00;  /* Primary orange */
--accent-blue: #06FFF0;  /* Accent teal */
```

---

## 3. Adding Artist Photos

### Hero Background Image

Add a background photo to the hero section:

**Find line 118 (`.hero` CSS):**
```css
.hero {
    height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    position: relative;
    background: linear-gradient(180deg, #0a0a0a 0%, #1a1a1a 100%);
    overflow: hidden;
}
```

**Replace with:**
```css
.hero {
    height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    position: relative;
    background: linear-gradient(rgba(10, 10, 10, 0.85), rgba(10, 10, 10, 0.95)), url('hero-photo.jpg');
    background-size: cover;
    background-position: center;
    overflow: hidden;
}
```

**Steps:**
1. Save your hero photo as `hero-photo.jpg` in the same folder
2. The gradient overlay (85%-95% dark) keeps text readable
3. Adjust opacity in `rgba(10, 10, 10, 0.85)` - lower number = lighter overlay

---

### Adding a Photo Section

Want to add an "About" section with your photo? Add this after the Story section (around line 271):

```html
<!-- About Section -->
<section id="about" style="background: var(--secondary-bg);">
    <h2>The Artist</h2>
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 4rem; align-items: center; max-width: 1000px; margin: 0 auto;">
        <img src="artist-photo.jpg" alt="Robert-Jan Mastenbroek" style="width: 100%; border-radius: 8px; border: 1px solid var(--border-subtle);">
        <div>
            <p style="font-size: 1.1rem; line-height: 1.8; color: var(--text-secondary); margin-bottom: 1.5rem;">
                From underground clubs to sacred spaces, the journey began with a single question:
                What if the dance floor could be holy ground?
            </p>
            <p style="font-size: 1.1rem; line-height: 1.8; color: var(--text-secondary);">
                Based in Tenerife, crafting melodic techno and tribal psytrance that speaks to
                something deeper than sound. Every set is an invitation. Every track, a threshold.
            </p>
        </div>
    </div>
</section>
```

**Then add to navigation (line 164):**
```html
<li><a href="#about">About</a></li>
```

---

## 4. Adding Press/Promo Photos

Create a photo gallery section:

```html
<!-- Gallery Section -->
<section id="gallery">
    <h2>Press Photos</h2>
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.5rem;">
        <img src="press-1.jpg" style="width: 100%; height: 300px; object-fit: cover; border-radius: 8px; border: 1px solid var(--border-subtle);">
        <img src="press-2.jpg" style="width: 100%; height: 300px; object-fit: cover; border-radius: 8px; border: 1px solid var(--border-subtle);">
        <img src="press-3.jpg" style="width: 100%; height: 300px; object-fit: cover; border-radius: 8px; border: 1px solid var(--border-subtle);">
        <img src="press-4.jpg" style="width: 100%; height: 300px; object-fit: cover; border-radius: 8px; border: 1px solid var(--border-subtle);">
    </div>
</section>
```

---

## 5. Favicon (Browser Tab Icon)

Add between `<head>` tags (around line 6):

```html
<link rel="icon" type="image/png" href="favicon.png">
```

**Steps:**
1. Create a square image (512x512px recommended)
2. Save as `favicon.png`
3. Place in same folder as `index.html`

---

## 6. Open Graph Image (Social Media Preview)

When someone shares your site on social media, add this preview image:

**Add to `<head>` section (around line 6):**
```html
<!-- Social Media Preview -->
<meta property="og:image" content="https://yourdomain.com/og-image.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:image" content="https://yourdomain.com/og-image.jpg">
```

**OG Image Requirements:**
- Size: 1200x630px (exact)
- Format: JPG or PNG
- Shows your name/logo + tagline
- Dark/Holy/Futuristic aesthetic

---

## 7. Custom Font (Optional)

Want a unique font that matches your brand?

**Add to `<head>` (around line 6):**
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;600;700&display=swap" rel="stylesheet">
```

**Then update line 25:**
```css
body {
    font-family: 'Montserrat', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    ...
}
```

**Recommended fonts for your aesthetic:**
- **Montserrat** - Modern, clean, geometric
- **Rajdhani** - Futuristic, tech-forward
- **Barlow** - Contemporary, professional
- **Orbitron** - Sci-fi, electronic music vibe

Browse fonts: [Google Fonts](https://fonts.google.com/)

---

## 8. File Organization

Keep your website folder organized:

```
your-website-folder/
├── index.html          (your main website file)
├── logo.png            (your logo)
├── favicon.png         (browser tab icon)
├── hero-photo.jpg      (hero background)
├── artist-photo.jpg    (about section photo)
├── press-1.jpg         (press photos)
├── press-2.jpg
├── og-image.jpg        (social media preview)
└── README.md           (documentation)
```

---

## 9. Image Optimization

Before uploading photos:

1. **Resize images:**
   - Hero background: 1920x1080px
   - Artist photos: 800x800px
   - Press photos: 1200x800px
   - OG image: 1200x630px

2. **Compress images:**
   - Use [TinyPNG.com](https://tinypng.com/) (free)
   - Or [Squoosh.app](https://squoosh.app/) (free, by Google)
   - Reduces file size 60-80% with no visible quality loss

3. **Why this matters:**
   - Faster loading = better user experience
   - Better SEO ranking
   - Lower bandwidth costs

---

## 10. Testing Your Changes

After adding photos/colors:

1. **Open `index.html` locally:**
   - Double-click the file
   - Opens in your default browser
   - Check all photos load correctly

2. **Test on mobile:**
   - Right-click page → "Inspect"
   - Click phone icon (top-left)
   - View as iPhone/Android

3. **Check different browsers:**
   - Chrome
   - Safari
   - Firefox

---

## Quick Checklist

- [ ] Logo added and displaying correctly
- [ ] Brand colors updated in CSS variables
- [ ] Hero background photo added (optional)
- [ ] Artist photo added to About section (optional)
- [ ] Press photos added (optional)
- [ ] Favicon added (shows in browser tab)
- [ ] OG image added (social media preview)
- [ ] All images optimized (compressed)
- [ ] Tested on desktop and mobile
- [ ] Ready to deploy!

---

## Need Help?

- **Can't find the right line number?** Use Ctrl+F (Cmd+F on Mac) to search for the text
- **Image not showing?** Check filename matches exactly (case-sensitive)
- **Colors look off?** Use a hex color picker: [Coolors.co](https://coolors.co/generate)

---

**Your website is already built and beautiful. These customizations make it unmistakably YOURS.**

Dark. Holy. Futuristic. 🎵
