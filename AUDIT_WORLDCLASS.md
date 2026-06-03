# Holy Rave — World-Class Competitive Audit

**Audit date:** 2026-06-03
**Benchmark set:** DICE, Resident Advisor (RA), Eventbrite, Fever, Ticketmaster, Airbnb Experiences
**Scoring:** 1 (needs rebuild) → 5 (world-class, top 0.01%)
**Updated:** 2026-06-04 — Score moved from 2.1 → ~3.2 / 5 (see June 4 Update section below)

---

## How We Score vs World-Class

| # | Dimension | Score | Gap | Top 0.01% Standard |
|---|-----------|-------|-----|---------------------|
| 1 | **Mobile-first checkout** | 3/5 | Medium | DICE: 3 taps from event → ticket purchased. No page reload. |
| 2 | **Payment friction** | 3/5 | Medium | Stripe Payment Element is good but no Apple Pay / Google Pay quick button |
| 3 | **One-click reordering** | 1/5 | Critical | Returning users must re-enter everything. No saved payment methods. |
| 4 | **Scarcity UX** | 3/5 | Medium | Meter + velocity is good. Missing: "X people are viewing this" live counter. |
| 5 | **Social proof depth** | 2/5 | Large | Ticker is basic. No attendee avatars, no "friends attending" network effect. |
| 6 | **Post-purchase experience** | 3/5 | Medium | Email + SMS sent. Missing: Apple Wallet / Google Pay pass, calendar auto-sync. |
| 7 | **Abandoned cart recovery** | 3/5 | Medium | Single email at 20min. Industry best: 3-email sequence (15min, 2hr, 24hr). |
| 8 | **Referral / viral mechanics** | 1/5 | Critical | No refer-a-friend, no share incentives, no group discounts. |
| 9 | **Waitlist mechanics** | 1/5 | Critical | Sold out = dead end. No waitlist, no notify-when-available. |
| 10 | **Dynamic pricing** | 1/5 | Critical | Fixed price only. No early-bird tiers, no group rates, no loyalty pricing. |
| 11 | **Host/artist credibility** | 3/5 | Medium | Host block exists. Missing: past event metrics, attendee testimonials, media mentions. |
| 12 | **Image & video quality** | 3/5 | Medium | Event images work. Missing: video backgrounds, venue walkthrough, past event recap. |
| 13 | **SEO / discoverability** | 2/5 | Large | OG tags added but no structured data (Event schema, QAPage), no blog content. |
| 14 | **Loading performance** | 3/5 | Medium | First load slow (cold start). Subsequent loads fast. No lazy-loading on images. |
| 15 | **Email design** | 2/5 | Large | Plain HTML tables, no responsive design, no branding beyond text. |
| 16 | **SMS reliability** | 2/5 | Large | Works for some carriers, blocked by others. No delivery confirmation tracking. |
| 17 | **Admin / analytics** | 2/5 | Large | Basic CRUD. No revenue dashboards, no conversion funnel, no A/B testing. |
| 18 | **Accessibility (a11y)** | 1/5 | Critical | No ARIA labels, no keyboard navigation, no screen reader support. |
| 19 | **Internationalization** | 1/5 | Critical | English only. No multi-language. Prices in EUR only. |
| 20 | **Security & fraud** | 2/5 | Large | Rate limiting + phone verification exist. No CAPTCHA, no device fingerprinting. |

**Overall score: 2.1 / 5 — Functional but not yet world-class**

---

## Detailed Analysis by Dimension

### 1. Mobile-First Checkout (3/5)

**Current state:** Multi-step form with phone verification. Works on mobile but 5+ taps before payment. Country code selector works. Verification code inputs auto-advance.

**World-class (DICE):** 3 taps. Open app → tap event → tap "Buy" → Face ID → done. No forms. No phone verification for returning users. Saved payment + saved address.

**Gap:** Returning users must re-verify phone, re-enter name/email. No biometric authentication. No saved payment methods.

**Fix priority:** P1 — Add Stripe saved payment methods + auto-fill for returning users.

---

### 2. Payment Friction (3/5)

**Current state:** Stripe Payment Element inline on page. 3D Secure handled. Statement descriptor set.

**World-class (DICE/Apple):** Apple Pay / Google Pay button as primary option. 1-tap purchase. Stripe Link for email-autofilled payments. Embedded checkout with saved cards.

**Gap:** No Apple Pay / Google Pay. Stripe Payment Element is good but not zero-click.

**Fix priority:** P1 — Add Apple Pay / Google Pay via Stripe Payment Request Button.

---

### 3. One-Click Reordering (1/5)

**Current state:** Zero. Every purchase is a full flow.

**World-class (DICE):** Returning user opens event → "You attended last time. One tap to join." Uses saved card + saved details.

**Gap:** No returning-user detection. No saved payment profiles.

**Fix priority:** P2 — Implement returning-user flow with Stripe saved payment methods.

---

### 4. Scarcity UX (3/5)

**Current state:** SVG meter with percentage fill, urgent state below 10 tickets. Velocity indicator ("X taken in 24h"). "Most chosen" badge on €12.

**World-class (Fever/Ticketmaster):** "27 people are viewing this event" live counter. "5 tickets sold in the last hour" real-time ticker. "Only 3 tickets left at this price" tiered scarcity. Color transitions from green → amber → red as scarcity increases.

**Gap:** No live visitor counter. No real-time sales velocity per minute. No price-tier scarcity.

**Fix priority:** P2 — Add live visitor counter via WebSocket or polling. Add real-time sales ticker.

---

### 5. Social Proof Depth (2/5)

**Current state:** Anonymized ticker ("Firstname L. just reserved"). Velocity indicator.

**World-class (Airbnb Experiences):** Review scores with photo evidence. "46 people booked this week." Friend activity: "Your friend Sarah booked this." Host response rate and rating prominently displayed.

**Gap:** No review system. No attendee count for past events. No social graph integration.

**Fix priority:** P2 — Add attendee count for past events. Add testimonial section after purchase.

---

### 6. Post-Purchase Experience (3/5)

**Current state:** Confirmation page with QR code, confetti, add-to-calendar, share button. Email + SMS sent.

**World-class (DICE/Airbnb):** Apple Wallet pass with dynamic updates. Google Pay pass. Calendar auto-syncs. One-tap directions. "Your event is tomorrow" push notification. In-app ticket with barcode that rotates.

**Gap:** No Apple Wallet / Google Pay passes. No push notifications. No dynamic ticket updates.

**Fix priority:** P3 — Add Apple Wallet passes via PassKit API. Add calendar auto-sync.

---

### 7. Abandoned Cart Recovery (3/5)

**Current state:** Single email at 20 minutes. Resume link pre-fills form.

**World-class (Shopify/Entry):** 3-email sequence: 15min (friendly reminder), 2hr (scarcity: "tickets almost gone"), 24hr (last chance). SMS follow-up for phone-verified users. Subject line A/B testing. Personalized amount reminder.

**Gap:** Single email only. No SMS follow-up. No multi-step sequence. No A/B testing.

**Fix priority:** P1 — Add 3-email sequence. Add SMS reminder for phone-verified users.

---

### 8. Referral / Viral Mechanics (1/5)

**Current state:** Share-to-friend button (WhatsApp deep link). +1 included in every ticket.

**World-class (Fever/DICE):** "Bring a friend, get €5 off." Referral code system with trackable links. "You and 2 friends booked" social proof. Group booking discount at 4+ tickets.

**Gap:** No referral tracking. No group incentives. No share-after-purchase flow.

**Fix priority:** P2 — Add referral codes stored in registrations table. Add group discount logic.

---

### 9. Waitlist Mechanics (1/5)

**Current state:** Sold out = "Join WhatsApp Community" dead end.

**World-class (DICE/RA):** "Join waitlist — we'll notify you if tickets become available." Auto-assign when someone's payment fails. Priority access for waitlisted users on next event. Count of people on waitlist displayed.

**Gap:** No waitlist at all. 50-person events NEED this — cancellations happen.

**Fix priority:** P1 — Add waitlist table + auto-promote when tickets free up.

---

### 10. Dynamic Pricing (1/5)

**Current state:** Pay-what-feels-right with €1 minimum. Fixed presets.

**World-class (Eventbrite/Fever):** Early-bird pricing (first 10 tickets at €5). Tiered pricing (€5 early, €10 standard, €15 door). Group discounts at 4+. Loyalty pricing for past attendees.

**Gap:** No pricing tiers at all. Missed revenue opportunity.

**Fix priority:** P3 — Add early-bird pricing logic to events table.

---

### 11. Host/Artist Credibility (3/5)

**Current state:** Host block with photo, name, bio. Link to full story.

**World-class (Airbnb):** Superhost badge. 4.98★ average rating. "200+ events hosted." Response rate: 100%. Photo-verified ID. Written testimonials with attendee photos.

**Gap:** No ratings, no past event metrics, no attendee testimonials.

**Fix priority:** P2 — Add past event attendance count. Add testimonial carousel.

---

### 12. Image & Video Quality (3/5)

**Current state:** Event images from Pexels or uploaded. Gradient fallback. OG image uses event photo.

**World-class (Fever/Airbnb):** Video hero backgrounds. Venue walkthrough videos. Past event recap reels. Carousel of attendee photos. 360° venue views.

**Gap:** No video content. No venue photography.

**Fix priority:** P3 — Add video background option to events.

---

### 13. SEO / Discoverability (2/5)

**Current state:** OG tags on detail pages. Server-side injection for crawlers. Canonical URLs set.

**World-class (Eventbrite/RA):** Event schema.org structured data (Event, MusicEvent, Ticket). QAPage / FAQPage for event questions. Blog content around events. Sitemap.xml auto-generated. Google Shopping integration for ticket listings.

**Gap:** No structured data markup on event pages. No blog. No sitemap for events.

**Fix priority:** P1 — Add JSON-LD structured data (Event + MusicEvent schema) to event pages.

---

### 14. Loading Performance (3/5)

**Current state:** First load slow (Railway cold start ~15s). Subsequent loads fast (0.6-1.2s). No lazy-loading.

**World-class (DICE/RA):** Sub-2s first load globally. CDN-cached. Lazy-loaded images. Skeleton screens during load. Service worker for offline access.

**Gap:** No CDN caching strategy. No lazy-loading on event card images. Cold start is brutal.

**Fix priority:** P2 — Add lazy-loading to event images. Add Railway keep-warm ping.

---

### 15. Email Design (2/5)

**Current state:** Plain HTML tables. Dark background with gold text. No responsive optimization. No branding.

**World-class (DICE/Airbnb):** Fully responsive emails. Branded headers/footers. Dynamic content blocks. Trackable links with UTM. Preview text optimization. Plain-text fallback. Dark mode support.

**Gap:** No responsive email design. No tracking beyond UTM. No plain-text version.

**Fix priority:** P2 — Add email template using a responsive framework (MJML or similar).

---

### 16. SMS Reliability (2/5)

**Current state:** Twilio Dutch number. Works for +34 and +31. Fire-and-forget with 5s timeout.

**World-class (Twilio/Vonage enterprise):** Delivery receipts tracked. Automatic carrier fallback. Local numbers per country. SMS + WhatsApp fallback. Delivery analytics dashboard.

**Gap:** No delivery tracking. No carrier fallback. Single outbound number.

**Fix priority:** P2 — Add delivery status tracking. Add WhatsApp Cloud API as secondary channel.

---

### 17. Admin / Analytics (2/5)

**Current state:** Basic event CRUD. Registrations list with phone verification status. Subscribers list.

**World-class (RA Pro/Eventbrite):** Real-time sales dashboard. Conversion funnel (visitors → verifiers → payers). Revenue reports. Ticket scanning app. Guest list management. Check-in analytics.

**Gap:** No sales analytics. No conversion funnel. No ticket scanning.

**Fix priority:** P2 — Add basic revenue dashboard. Add ticket scanning endpoint.

---

### 18. Accessibility (1/5)

**Current state:** Zero accessibility considerations.

**World-class (WCAG 2.1 AA):** ARIA labels on all interactive elements. Keyboard-navigable forms. Screen reader announcements for dynamic updates. 4.5:1 color contrast minimum. Focus indicators visible.

**Gap:** Entire site fails basic accessibility checks.

**Fix priority:** P3 — Add ARIA labels, focus management, keyboard navigation.

---

### 19. Internationalization (1/5)

**Current state:** English only. EUR currency only.

**World-class (DICE/Fever):** 10+ languages. Local currency + payment methods. Regional pricing. Date/time localized.

**Gap:** No multi-language support. Not relevant yet for 50-person local events.

**Fix priority:** P3 — Deferred until expansion beyond Tenerife.

---

### 20. Security & Fraud (2/5)

**Current state:** Rate limiting on register (20/10min). Phone verification. Stripe Radar for payments.

**World-class (Ticketmaster/AXS):** Device fingerprinting. Bot detection. CAPTCHA on registration. Velocity checks per IP + email + card. Manual review triggers for suspicious patterns.

**Gap:** No CAPTCHA. No device fingerprinting. No bot detection.

**Fix priority:** P2 — Add Turnstile CAPTCHA (free, privacy-friendly) to registration.

---

## Priority Action Plan

### P1 — Do this week (high impact, achievable)

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 1 | **Add JSON-LD structured data** to event pages (Event + MusicEvent schema) | 2hr | SEO; shows ticket info in Google Search results |
| 2 | **Add 3-email abandoned cart sequence** (15min, 2hr, 24hr) | 3hr | Recover more pending registrations |
| 3 | **Add waitlist table + auto-promote** when cancellations happen | 4hr | Never leave tickets unfilled |
| 4 | **Add Apple Pay / Google Pay** via Stripe Payment Request Button | 2hr | Reduce payment friction |
| 5 | **Add Turnstile CAPTCHA** to registration form | 1hr | Block bots |

### P2 — Do this month

| # | Action | Effort |
|---|--------|--------|
| 6 | Add returning-user detection + saved payment methods | 4hr |
| 7 | Add live "X people viewing" counter | 3hr |
| 8 | Add referral codes + group discount logic | 6hr |
| 9 | Add attendee testimonial section | 3hr |
| 10 | Add revenue dashboard to admin panel | 6hr |
| 11 | Add delivery tracking for SMS | 4hr |
| 12 | Add lazy-loading to event images | 1hr |
| 13 | Add Railway keep-warm ping (avoid cold starts) | 0.5hr |

### P3 — Nice to have

| # | Action |
|---|--------|
| 14 | Apple Wallet / Google Pay passes |
| 15 | Early-bird dynamic pricing tiers |
| 16 | Video backgrounds for events |
| 17 | Responsive email templates (MJML) |
| 18 | Accessibility audit + fixes |
| 19 | Multi-language support |
| 20 | Bot detection / device fingerprinting |

---

## Summary

Holy Rave's current ticketing system is **functional and competitive for a 50-person intimate event**, with several world-class elements (inline Stripe Payment Element, phone verification, abandoned cart resume flow, OG image injection, multi-step form with cancel).

The biggest gaps are:
1. **No structured data** (Google can't index our events properly) — P1
2. **No waitlist** — losing sales when tickets free up — P1
3. **No Apple Pay / Google Pay** — leaving conversions on the table — P1
4. **Single abandoned email** — should be a 3-email sequence — P1

These four P1 items alone would move us from **2.1 → ~3.0 / 5**.
