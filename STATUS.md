# Holy Rave — STATUS

Last updated: 2026-06-03

## Current State

The Holy Rave ticketing system is fully operational. Below is a summary of all features, known issues, and what was built today.

---

## What's Live

### Event-Specific Ticketing
- Events table with slug, title, location, date, time, ticket_limit, image_url, maps_url
- Hub page (`/holy-rave`) shows upcoming events with live ticket meters
- Detail page (`/holy-rave/:slug`) with SVG ticket meter, countdown, registration form
- Past events collapsible section on hub
- Social proof ticker: "Firstname L. just reserved"
- Velocity indicator: "X spots taken in last 24h"
- Add-to-calendar (.ics) download
- Share-to-friend (WhatsApp deep link)

### Registration & Payment
- Multi-step form: Step 1 (name, email, phone + country code + 6-digit verification) → Step 2 (amount selection with "Most chosen" badge on €12) → Step 3 (payment processing)
- Cancel button on step 3 to go back and adjust amount
- Phone verification via 6-digit SMS code (Twilio Dutch number +3197010259446)
- Phone fields stay unlocked after verification (user can correct typos)
- Stripe Payment Element (inline, no redirect) — card form mounts on page
- Statement descriptor: "HOLY RAVE 13 JUN" (shows on bank/iDEAL)
- Fallback: redirect to Stripe if embedded checkout fails
- 3D Secure handling via redirect: 'if_required'
- Resend confirmation email with payment amount
- Ticket SMS via Twilio (location, date, time, maps link, confirmation code)
- Abandoned registration reminders (20 min delay, 5 min cron)
- Resume flow: pre-fill form + skip to payment from email link

### Admin Panel (`/admin`)
- Login via session + signed HMAC token
- Event CRUD: create, edit status (upcoming/past), delete
- Registrations view per event with cancel option
- Subscribers list with copy-all buttons
- Image upload (drag-and-drop + crop via Cropper.js)
- Pexels image search modal
- Sync to Resend button
- Phone verification status (✓ green / ? grey)
- Registration UTM source tracking (to be added)

### Homepage (`/index.html`)
- Hero stats bar: tracks, Spotify listeners, next event date, spots left
- Events section with live data from API
- Email subscribe with live subscriber count
- "Listen on Spotify" + "Reserve Your Spot" hero buttons
- Spotify embed with latest tracks

### OG Image / Social Sharing
- Server-side OG tag injection for `/holy-rave` and `/holy-rave/:slug`
- Uses event's uploaded image as og:image
- Dynamic JS OG update for in-app previews
- `og:type` and `fb:app_id` for Facebook validation

### Google Analytics
- GA4 (G-JZG345ND51) on all public pages:
  - index.html, holy-rave/index.html, holy-rave/confirmed.html
  - auth/login.html, auth/signup.html, auth/callback.html
  - offering/index.html, offering/thank-you/index.html
- Conversion events: phone_verified, registration_submitted, payment_opened, payment_success
- UTM tracking on all email links (?utm_source=email)
- UTM tracking on all social links (WhatsApp, Instagram, Spotify)

### SMS Providers
- **Primary:** Twilio (Dutch number +3197010259446) — verified working for +34 and +31
- **Fallback:** Vonage (alpha sender "HOLY RAVE") — if Twilio not configured

---

## Technical Architecture

### Stack
- Node.js/Express server at `Centre/server.js`
- PostgreSQL on Supabase via `postgres.js` tagged template
- Static HTML/CSS/JS frontend (no framework)
- Stripe for payments
- Twilio for SMS
- Resend for email
- Vonage as SMS fallback

### Key Files
| File | Purpose |
|------|---------|
| `server.js` | Express server, API routes, webhooks, SMS, email, cron |
| `lib/db.js` | PostgreSQL queries, migrations, schema |
| `lib/schema.sql` | Full database schema |
| `holy-rave/index.html` | Hub + detail page (dual mode via URL dispatch) |
| `holy-rave/confirmed.html` | Post-Stripe redirect confirmation |
| `admin/index.html` | Admin panel |
| `index.html` | Homepage |
| `auth/login.html` | Login page |
| `auth/signup.html` | Signup page |
| `auth/callback.html` | OAuth callback |
| `press-kit/index.html` | Press kit |
| `story/index.html` | Artist story / biography |

### Database Tables
- `events` — event data with image, maps, location
- `holy_rave_registrations` — registrations with phone, payment, verification status
- `subscribers` — email + phone subscribers
- `event_images` — BYTEA image storage (survives Railway deploys)
- `phone_verifications` — SMS verification codes

---

## Known Issues & TODOs

### High Priority
- P3: UTM source attribution — store utm_source/medium/campaign in registrations table and show in admin
- P3: Add `source` column to holy_rave_registrations
- Browser extension sandboxing affects some users (workaround: incognito mode)
- Email client sandboxed browsers block JS (fixed with CSP + target=_blank)

### Medium Priority
- Add Webhook endpoint in Stripe Dashboard for `payment_intent.succeeded`, `checkout.session.expired`, `charge.refunded`
- QR code on confirmation page for future ticket scanning
- Verification endpoint at `/verify/:code` for door scanning

### Low Priority
- Preset amounts should fill custom input (UX polish)
- Homepage: "36 tracks" in hero stats should be dynamic
- RLS policies on all 5 Supabase tables (currently policies exist but server bypasses RLS as superuser)

---

## Key Decisions
- **No SSR** — static HTML with server-side OG injection for crawlers. JS reads slug from URL and dispatches hub vs detail mode
- **PaymentIntent** (not Checkout Session) — inline card form on our page, no redirect
- **Payment confirmed via direct endpoint** (`/api/holy-rave/confirm-payment`) — not reliant on webhook
- **Phone verification** — 6-digit code via Twilio, stored in phone_verifications table, 5 min expiry
- **Reminder timing** — 20 min inactivity before first reminder, every 5 min cron
- **Vonage kept as fallback** — Twilio primary works for both Spanish and Dutch numbers
- **Email is receipt only** — ticket details only in SMS (to drive phone number collection)
