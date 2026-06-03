# Holy Rave — STATUS

Last updated: 2026-06-04

## Current State

The Holy Rave ticketing system is fully operational. Below is a summary of all features, known issues, and what was built.

---

## What's Live

### Event-Specific Ticketing
- Events table with slug, title, location, date, time, ticket_limit, image_url, maps_url
- Hub page (`/holy-rave`) shows upcoming events with live ticket meters + hero image
- Detail page (`/holy-rave/:slug`) with SVG ticket meter, countdown, registration form
- Past events collapsible section on hub
- Social proof ticker: "First L. just reserved" with cross-fade animation
- Velocity indicator: "X spots taken in last 24h"
- Add-to-calendar (.ics) download from browser
- Share-to-friend (WhatsApp deep link)
- Host credibility block (Airbnb pattern with photo)

### Registration & Payment
- Multi-step form: Step 1 (name, email, phone + country code + 6-digit verification) → auto-advances to Step 2 (amount selection with "Most chosen" badge on €12) → Step 3 (payment processing)
- Cancel button on step 3 to go back and adjust amount
- Phone verification via 6-digit SMS code (Twilio Dutch number +3197010259446)
- Vonage fallback if Twilio not configured
- Phone fields stay unlocked after verification (user can correct typos)
- Stripe Payment Element (inline, no redirect) — card form mounts on page
- Apple Pay / Google Pay via Stripe Payment Request Button
- Statement descriptor: "HOLY RAVE 13 JUN" (shows on bank/iDEAL)
- Fallback: redirect to Stripe if inline card fails
- 3D Secure handling via redirect with sessionStorage persistence
- Resend confirmation email with payment amount, event details, confirmation code
- Ticket SMS via Twilio/Vonage (location, date, time, maps link, confirmation code)
- 3-email abandoned sequence (15min friendly nudge → 2hr scarcity → 24hr last call)
- Resume flow: pre-fill form + skip to payment from email link
- UTM capture: source/medium/campaign stored per registration
- SMS delivery status tracking per registration

### Admin Panel (`/admin`)
- Login via password → signed HMAC token (no session cookies)
- Event CRUD: create, edit status (upcoming/past), delete
- Registrations view per event with cancel option, UTM source, SMS status
- Subscribers list with copy-all buttons
- Image upload: drag-and-drop → Cropper.js (crop, zoom, rotate, flip, aspect ratios)
- Pexels image search modal with grid + infinite scroll
- "+20 Tickets" button to increase ticket limit for sold-out events
- Sync to Resend button
- Phone verification status (✓ green / ? grey)

### Homepage (`/index.html`)
- Hero stats bar: tracks, Spotify listeners, next event date, spots left
- Events section with live data from API, next-3-events grid with images
- Email subscribe with live subscriber count
- "Listen on Spotify" + "Reserve Your Spot" hero buttons
- "Get Your Tickets" CTA with lock icon

### SEO / Structured Data
- JSON-LD MusicEvent schema on detail pages (name, date, location, offers, organizer)
- JSON-LD ItemList schema on hub page (upcoming events)
- Server-side OG tag injection for `/holy-rave` and `/holy-rave/:slug`
- Event-specific OG image, title, description
- Server-rendered event data (no API call needed on first load)
- `og:type` and `fb:app_id` for Facebook validation
- Frame-ancestors CSP header

### Google Analytics
- GA4 (G-JZG345ND51) on all public pages
- Conversion events: phone_verified, registration_submitted, payment_opened, payment_success
- UTM tracking on all email links, social links
- Goal: track source attribution per registration

### Performance
- font-display: swap (no invisible text while fonts load)
- Preconnect hints for Google Fonts, Stripe, QR server
- Google Fonts loads non-blocking via media="print"
- Lazy-load event card images
- Dead code removed (unused scrollToForm function)

### SMS Providers
- **Primary:** Twilio (Dutch number +3197010259446) — verified working for +34 and +31
- **Fallback:** Vonage (alpha sender "HolyRave") — if Twilio not configured

---

## Technical Architecture

### Stack
- Node.js/Express server at `Centre/server.js`
- PostgreSQL on Supabase via `postgres.js` tagged template
- Static HTML/CSS/JS frontend (no framework)
- Stripe for payments
- Twilio for SMS, Vonage as fallback
- Resend for email
- Supabase Auth (login/signup infra exists, not wired into holy-rave flow)

### Key Files
| File | Purpose |
|------|---------|
| `server.js` | Express server, API routes, webhooks, SMS, email, cron |
| `lib/db.js` | PostgreSQL queries, migrations, schema |
| `lib/schema.sql` | Full database schema |
| `lib/supabase.js` | Supabase client (admin + public) |
| `holy-rave/index.html` | Hub + detail page (dual mode via URL dispatch) |
| `holy-rave/confirmed.html` | Post-Stripe redirect confirmation |
| `admin/index.html` | Admin panel |
| `index.html` | Homepage |
| `auth/login.html` | Login page with Google OAuth |
| `auth/signup.html` | Signup page |
| `auth/callback.html` | OAuth callback |
| `nixpacks.toml` | Railway build config (caching, deploy exclusions) |

### Database Tables
- `events` — event data with image, maps, location
- `holy_rave_registrations` — registrations with phone, payment, UTM source, SMS status, email_sequence_step
- `subscribers` — email + phone subscribers
- `event_images` — BYTEA image storage (survives Railway deploys)
- `phone_verifications` — SMS verification codes (5 min expiry)

---

## Known Issues & TODOs

### FIXED in this session (June 3-4)
- ✅ Event-specific ticketing (replaced weekly auto-reset)
- ✅ Server-rendered event pages with SEO data
- ✅ JSON-LD structured data (MusicEvent + ItemList)
- ✅ 3-email abandoned sequence (15min, 2hr, 24hr)
- ✅ Waitlist form on sold-out events
- ✅ Admin "+20 Tickets" button
- ✅ UTM capture + source attribution per registration
- ✅ SMS delivery status tracking
- ✅ Apple Pay / Google Pay
- ✅ Google Analytics on all pages
- ✅ Conversion tracking events
- ✅ Phone inputs unlocked after verification
- ✅ Cancel button on step 3 loading state
- ✅ Smart sticky mobile CTA (context-aware)
- ✅ Auto-advance after phone verification
- ✅ Frame-busting + CSP headers
- ✅ Phone required validation
- ✅ Stripe inline Payment Element (no redirect)
- ✅ Resume flow from email link
- ✅ Phone verification with 6-digit code
- ✅ "Most chosen" badge on €12
- ✅ Image upload with Cropper.js
- ✅ Pexels image search
- ✅ Admin registrations panel
- ✅ Subscribers list with copy-all
- ✅ Railway build caching + deploy exclusions

### Remaining
- User accounts (Supabase Auth) — infra exists, not wired into flow
- SEO blog content pipeline
- /verify/:code endpoint for door scanning
- Apple Wallet / Google Pay passes
- Responsive email templates (MJML)
- Accessibility audit (ARIA, keyboard nav)
- Multi-language support (deferred)
- Dynamic pricing tiers (early-bird)
