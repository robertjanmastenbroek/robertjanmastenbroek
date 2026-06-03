const express = require('express');
const path = require('path');
const fs = require('fs');
const db = require('./lib/db');

const app = express();
const PORT = process.env.PORT || 8080;
const SITE_URL = process.env.SITE_URL || 'https://robertjanmastenbroek.com';

// Lazy-initialize Stripe and Resend so the server starts even without env vars set
function getStripe() {
  if (!process.env.STRIPE_SECRET_KEY) return null;
  if (!getStripe._instance) {
    getStripe._instance = require('stripe')(process.env.STRIPE_SECRET_KEY);
  }
  return getStripe._instance;
}

function getResend() {
  if (!process.env.RESEND_API_KEY) return null;
  if (!getResend._instance) {
    const { Resend } = require('resend');
    getResend._instance = new Resend(process.env.RESEND_API_KEY);
  }
  return getResend._instance;
}

// Webhook route needs raw body — must come before express.json()
app.post('/api/webhook', express.raw({ type: 'application/json' }), async (req, res) => {
  const sig = req.headers['stripe-signature'];
  let event;

  const stripe = getStripe();
  if (!stripe) return res.status(503).send('Stripe not configured');

  try {
    event = stripe.webhooks.constructEvent(
      req.body,
      sig,
      process.env.STRIPE_WEBHOOK_SECRET
    );
  } catch (err) {
    console.error('Webhook signature error:', err.message);
    return res.status(400).send(`Webhook Error: ${err.message}`);
  }

  if (event.type === 'checkout.session.completed') {
    const session = event.data.object;
    const email = session.customer_details?.email;
    const name = session.customer_details?.name;
    const regId = session.client_reference_id || session.metadata?.registration_id;

    // Holy Rave ticket — mark registration as confirmed (DB-backed, with overbooking guard)
    if (regId && regId.startsWith('hr_')) {
      const week = session.metadata?.week || getWeekMonday();
      try {
        const confirmed = await db.confirmRegistration(regId, week);
        if (confirmed && email) {
          const firstName = name ? name.split(' ')[0] : '';
          const lastName = name ? name.split(' ').slice(1).join(' ') : '';
          await sendHolyRaveConfirmation(email, firstName, lastName);
          syncToResendAudience(email, firstName, lastName).catch(e =>
            console.error('Webhook audience sync error:', e.message));
        }
      } catch (err) {
        console.error('Webhook confirm error:', err.message);
      }
    } else if (email) {
      // Non-Holy-Rave checkout (offering/support) — send thank-you + sync audience
      const firstName = name ? name.split(' ')[0] : '';
      const lastName = name ? name.split(' ').slice(1).join(' ') : '';
      await sendThankYouEmail(email, name);
      syncToResendAudience(email, firstName, lastName).catch(e =>
        console.error('Offering audience sync error:', e.message));
    }
  }

  res.json({ received: true });
});

app.use(express.json());
app.use(express.static(path.join(__dirname)));

// ─── MRR Counter ────────────────────────────────────────────────────────────
// Cached so we don't hammer the Stripe API on every page load
let mrrCache = { value: 0, fetchedAt: 0 };
const MRR_CACHE_TTL = 5 * 60 * 1000; // 5 minutes

app.get('/api/mrr', async (req, res) => {
  // Serve from cache if fresh
  if (Date.now() - mrrCache.fetchedAt < MRR_CACHE_TTL) {
    return res.json({ mrr: mrrCache.value });
  }

  if (!process.env.STRIPE_SECRET_KEY) {
    return res.json({ mrr: 0 });
  }

  try {
    let mrr = 0;
    let hasMore = true;
    let startingAfter = undefined;

    const stripe = getStripe();
    // Page through all active subscriptions
    while (hasMore) {
      const params = { status: 'active', limit: 100, expand: ['data.items.data.price'] };
      if (startingAfter) params.starting_after = startingAfter;

      const subs = await stripe.subscriptions.list(params);

      for (const sub of subs.data) {
        for (const item of sub.items.data) {
          const price = item.price;
          const amount = item.quantity * price.unit_amount;
          if (price.recurring.interval === 'month') {
            mrr += amount;
          } else if (price.recurring.interval === 'year') {
            mrr += Math.round(amount / 12);
          } else if (price.recurring.interval === 'week') {
            mrr += Math.round(amount * 4.33);
          }
        }
      }

      hasMore = subs.has_more;
      if (hasMore) startingAfter = subs.data[subs.data.length - 1].id;
    }

    const mrrEuros = Math.round(mrr / 100);
    mrrCache = { value: mrrEuros, fetchedAt: Date.now() };
    res.json({ mrr: mrrEuros });
  } catch (err) {
    console.error('MRR fetch error:', err.message);
    res.json({ mrr: mrrCache.value }); // Return stale cache on error
  }
});

// ─── Supporter Count (humans, not euros) ─────────────────────────────────────
// Returns the number of people who've subscribed via email or Holy Rave.
// Cached for 5 minutes. Falls back to the Stripe active subscription count
// if the database isn't available.
let supporterCache = { count: 0, fetchedAt: 0 };
const SUPPORTER_CACHE_TTL = 5 * 60 * 1000;
const SUPPORTER_BASE_COUNT = parseInt(process.env.SUPPORTER_BASE_COUNT || '4', 10);

app.get('/api/supporters-count', async (req, res) => {
  if (Date.now() - supporterCache.fetchedAt < SUPPORTER_CACHE_TTL) {
    return res.json({ count: Math.max(SUPPORTER_BASE_COUNT, supporterCache.count) });
  }

  try {
    const dbCount = await db.getSubscriberCount();
    const total = dbCount + SUPPORTER_BASE_COUNT;
    supporterCache = { count: total, fetchedAt: Date.now() };
    res.json({ count: total });
  } catch (err) {
    console.error('Supporter count error:', err.message);
    res.json({ count: Math.max(SUPPORTER_BASE_COUNT, supporterCache.count || SUPPORTER_BASE_COUNT) });
  }
});

// ─── Tier 4 Dynamic Checkout ─────────────────────────────────────────────────
app.post('/api/create-checkout', async (req, res) => {
  const { amount } = req.body;

  if (!amount || isNaN(amount) || amount < 100) {
    return res.status(400).json({ error: 'Minimum amount is €100' });
  }

  const stripe = getStripe();
  if (!stripe) return res.status(503).json({ error: 'Stripe not configured' });

  try {
    const session = await stripe.checkout.sessions.create({
      payment_method_types: ['card'],
      mode: 'subscription',
      currency: 'eur',
      line_items: [{
        price_data: {
          currency: 'eur',
          product_data: {
            name: 'Benefactor — Holy Rave Mission Support',
            description: 'You\'re a cornerstone of this mission.',
          },
          unit_amount: Math.round(amount) * 100,
          recurring: { interval: 'month' },
        },
        quantity: 1,
      }],
      success_url: `${SITE_URL}/offering/thank-you`,
      cancel_url: `${SITE_URL}/offering`,
    });

    res.json({ url: session.url });
  } catch (err) {
    console.error('Checkout session error:', err.message);
    res.status(500).json({ error: 'Could not create checkout session.' });
  }
});

// ─── Spotify Follower Count (auto-updates via public API) ────────────────────
// Requires env vars: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
// Get them free at developer.spotify.com — register any app, copy the keys.
const SPOTIFY_ARTIST_ID = '2Seaafm5k1hAuCkpdq7yds';
let spotifyToken = { value: null, expiresAt: 0 };
let spotifyFollowers = { count: 0, fetchedAt: 0 };
const FOLLOWERS_TTL = 60 * 60 * 1000; // refresh every hour

async function getSpotifyToken() {
  if (spotifyToken.value && Date.now() < spotifyToken.expiresAt) return spotifyToken.value;
  const id = process.env.SPOTIFY_CLIENT_ID;
  const secret = process.env.SPOTIFY_CLIENT_SECRET;
  if (!id || !secret) return null;
  const res = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'Authorization': 'Basic ' + Buffer.from(id + ':' + secret).toString('base64'),
    },
    body: 'grant_type=client_credentials',
  });
  const data = await res.json();
  spotifyToken = { value: data.access_token, expiresAt: Date.now() + (data.expires_in - 60) * 1000 };
  return data.access_token;
}

app.get('/api/spotify-followers', async (req, res) => {
  if (Date.now() - spotifyFollowers.fetchedAt < FOLLOWERS_TTL) {
    return res.json({ count: spotifyFollowers.count });
  }
  try {
    const token = await getSpotifyToken();
    if (!token) return res.json({ count: 0 });
    const r = await fetch(`https://api.spotify.com/v1/artists/${SPOTIFY_ARTIST_ID}`, {
      headers: { Authorization: 'Bearer ' + token },
    });
    const data = await r.json();
    spotifyFollowers = { count: data.followers?.total || 0, fetchedAt: Date.now() };
    res.json({ count: spotifyFollowers.count });
  } catch (err) {
    console.error('Spotify followers error:', err.message);
    res.json({ count: spotifyFollowers.count });
  }
});

// ─── Email Subscribe ──────────────────────────────────────────────────────────
app.post('/api/subscribe', async (req, res) => {
  const { email } = req.body;
  if (!email || !email.includes('@')) return res.status(400).json({ error: 'Invalid email' });

  // Save to database
  try {
    await db.addSubscriber(email, null, null, 'email_form');
  } catch (e) {
    console.error('Subscriber DB save error:', e.message);
  }

  // Sync to Resend audience (fire-and-forget)
  syncToResendAudience(email, null, null).catch(e =>
    console.error('Audience sync error:', e.message));

  // Send welcome email
  const resend = getResend();
  if (resend) {
    try {
      await resend.emails.send({
        from: 'Robert-Jan <robert-jan@robertjanmastenbroek.com>',
        reply_to: 'mastenbroekrobertjan@gmail.com',
        to: email,
        subject: 'New music. Every Friday.',
        html: `<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="margin:0;padding:0;background-color:#0a0a0a;font-family:-apple-system,sans-serif"><table width="100%" cellpadding="0" cellspacing="0" bgcolor="#0a0a0a" style="background-color:#0a0a0a"><tr><td align="center"><table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background-color:#0a0a0a" bgcolor="#0a0a0a"><tr><td style="padding:48px 32px"><p style="color:#d4af37;font-size:13px;letter-spacing:2px;text-transform:uppercase;margin:0 0 24px">Robert-Jan Mastenbroek</p><h1 style="font-size:26px;color:#ffffff;margin:0 0 8px;letter-spacing:2px;text-transform:uppercase;font-weight:700">You're <span style="color:#d4af37">in.</span></h1><hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0"><p style="font-size:16px;line-height:1.8;color:#a0a0a0;margin:0 0 20px">New music drops every Friday.</p><p style="font-size:16px;line-height:1.8;color:#a0a0a0;margin:0 0 20px">You'll hear it first.</p><hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0"><p style="font-size:13px;color:#555;margin:0">All the glory belongs to Jesus.<br>— Robert-Jan</p></td></tr></table></td></tr></table></body></html>`,
      });
      // Notify RJM
      await resend.emails.send({
        from: 'robert-jan@robertjanmastenbroek.com',
        reply_to: 'mastenbroekrobertjan@gmail.com',
        to: 'robert-jan@robertjanmastenbroek.com',
        subject: `New subscriber: ${email}`,
        html: `<p style="font-family:sans-serif">New subscriber: <strong>${email}</strong></p>`,
      });
    } catch (e) { console.error('Subscribe email error:', e.message); }
  }

  res.json({ ok: true });
});

// ─── Holy Rave Events (event-specific, replaces weekly auto-reset) ──────────

// GET /api/holy-rave/events — list upcoming events with ticket counts
app.get('/api/holy-rave/events', async (req, res) => {
  try {
    const events = await db.getUpcomingEvents();
    res.json(events);
  } catch (err) {
    console.error('Events error:', err.message);
    res.json([]);
  }
});

// GET /api/holy-rave/events/:slug — single event with ticket stats
app.get('/api/holy-rave/events/:slug', async (req, res) => {
  try {
    const event = await db.getEventBySlug(req.params.slug);
    if (!event) {
      return res.status(404).json({ error: 'Event not found.' });
    }
    res.json(event);
  } catch (err) {
    console.error('Event error:', err.message);
    res.status(500).json({ error: 'Could not load event.' });
  }
});

// GET /api/holy-rave/tickets — legacy: remaining spots for the current week
const TICKETS_MAX = 50;

function getWeekMonday() {
  const now = new Date();
  const day = now.getDay();
  const diff = day === 0 ? 6 : day - 1;
  const monday = new Date(now);
  monday.setHours(0, 0, 0, 0);
  monday.setDate(now.getDate() - diff);
  return monday.toISOString().split('T')[0];
}

app.get('/api/holy-rave/tickets', async (req, res) => {
  try {
    const week = getWeekMonday();
    const stats = await db.getWeekStats(week);
    res.json(stats);
  } catch (err) {
    console.error('Tickets error:', err.message);
    res.json({ week: getWeekMonday(), total: TICKETS_MAX, sold: 0, remaining: TICKETS_MAX });
  }
});

// POST /api/holy-rave/register — create a registration + optional Stripe checkout
app.post('/api/holy-rave/register', async (req, res) => {
  const { firstName, lastName, email, amount, eventSlug } = req.body;

  if (!firstName || !lastName || !email || !email.includes('@')) {
    return res.status(400).json({ error: 'Please fill in all fields correctly.' });
  }

  const amt = Math.max(0, parseInt(amount, 10) || 0);

  try {
    let eventId = null;
    let week = null;
    let eventTitle = 'Holy Rave';

    // If eventSlug provided, use event-based registration
    if (eventSlug) {
      const event = await db.getEventBySlug(eventSlug);
      if (!event) {
        return res.status(400).json({ error: 'Event not found.' });
      }
      if (event.status !== 'upcoming') {
        return res.status(400).json({ error: 'This event has passed.' });
      }
      if (event.remaining <= 0) {
        return res.status(400).json({ error: 'All tickets are taken for this event.' });
      }
      eventId = event.id;
      eventTitle = event.title;

      // Check duplicate email for this event
      if (await db.isDuplicateEmailForEvent(eventId, email)) {
        return res.status(400).json({ error: 'This email already has a spot for this event.' });
      }
    } else {
      // Fall back to weekly registration (legacy)
      week = getWeekMonday();
      const stats = await db.getWeekStats(week);
      if (stats.remaining <= 0) {
        return res.status(400).json({ error: 'All tickets are taken this week.' });
      }
      if (await db.isDuplicateEmail(week, email)) {
        return res.status(400).json({ error: 'This email already has a spot this week.' });
      }
    }

    const id = 'hr_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);

    // Free ticket — confirm immediately
    if (amt === 0) {
      await db.createRegistration({ id, firstName, lastName, email, amount: 0, week, eventId });
      sendHolyRaveConfirmation(email, firstName, lastName).catch(e =>
        console.error('Free ticket email error:', e.message));
      syncToResendAudience(email, firstName, lastName).catch(e =>
        console.error('Free ticket audience sync error:', e.message));
      return res.json({ id, confirmed: true });
    }

    // Paid ticket — create Stripe Checkout
    const stripe = getStripe();
    if (!stripe) {
      return res.status(503).json({ error: 'Payment system not available.' });
    }

    const successUrl = eventSlug
      ? `${SITE_URL}/holy-rave/${eventSlug}?confirmed=${id}`
      : `${SITE_URL}/holy-rave/confirmed?id=${id}`;
    const cancelUrl = eventSlug
      ? `${SITE_URL}/holy-rave/${eventSlug}`
      : `${SITE_URL}/holy-rave`;

    const session = await stripe.checkout.sessions.create({
      payment_method_types: ['card'],
      mode: 'payment',
      currency: 'eur',
      client_reference_id: id,
      customer_email: email,
      line_items: [{
        price_data: {
          currency: 'eur',
          product_data: {
            name: eventTitle + ' — Ticket',
            description: `${firstName} ${lastName} — pay what feels right`,
          },
          unit_amount: amt,
        },
        quantity: 1,
      }],
      metadata: eventId
        ? { registration_id: id, event_id: String(eventId) }
        : { registration_id: id, week },
      success_url: successUrl,
      cancel_url: cancelUrl,
    });

    await db.createRegistration({
      id, firstName, lastName, email, amount: amt, week, eventId,
      stripeSessionId: session.id,
    });

    res.json({ id, checkoutUrl: session.url });
  } catch (err) {
    console.error('Holy Rave register error:', err.message);
    const message = err.message || 'Could not complete registration. Please try again.';
    const isStripe = err.type && err.type.startsWith('Stripe');
    res.status(isStripe ? 402 : 500).json({ error: message });
  }
});

// GET /api/holy-rave/verify — check registration status
app.get('/api/holy-rave/verify', async (req, res) => {
  const { id } = req.query;
  if (!id) return res.status(400).json({ error: 'Missing registration ID.' });

  try {
    const reg = await db.getRegistrationById(id);
    if (!reg) {
      return res.status(404).json({ error: 'Registration not found.' });
    }

    let eventName = null;
    let eventDate = null;
    let eventLocation = null;
    if (reg.event_id) {
      try {
        const sql = db.getSql();
        const [ev] = await sql`
          SELECT title, event_date, location FROM events WHERE id = ${reg.event_id}
        `;
        if (ev) {
          eventName = ev.title;
          eventDate = ev.event_date;
          eventLocation = ev.location;
        }
      } catch (e) {}
    }

    res.json({
      id: reg.id,
      firstName: reg.first_name,
      lastName: reg.last_name,
      email: reg.email,
      status: reg.status,
      amount: reg.amount_cents,
      eventName,
      eventDate,
      eventLocation,
    });
  } catch (err) {
    console.error('Verify error:', err.message);
    res.status(500).json({ error: 'Could not verify registration.' });
  }
});

// ─── Admin: Sync all existing DB contacts to Resend audience ──────────────────
app.get('/api/admin/sync-audience', async (req, res) => {
  try {
    const contacts = await db.getSubscribersWithHolyRave();
    let synced = 0;
    let errors = 0;
    for (const c of contacts) {
      try {
        await syncToResendAudience(c.email, c.first_name, c.last_name);
        synced++;
      } catch (e) {
        errors++;
        console.error(`Sync failed for ${c.email}:`, e.message);
      }
    }
    res.json({ total: contacts.length, synced, errors });
  } catch (err) {
    console.error('Admin sync error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ─── Health check ─────────────────────────────────────────────────────────────
app.get('/health', (req, res) => res.send('OK'));

// ─── Holy Rave event pages — serve holy-rave/index.html for /holy-rave/:slug ──
app.get('/holy-rave/:slug', (req, res) => {
  const slug = req.params.slug;

  // confirmation page is a separate file
  if (slug === 'confirmed') {
    return res.sendFile(path.join(__dirname, 'holy-rave', 'confirmed.html'), (err) => {
      if (err) res.sendFile(path.join(__dirname, 'index.html'));
    });
  }

  // Everything else serves the holy-rave index.html; JS reads the slug from URL
  res.sendFile(path.join(__dirname, 'holy-rave', 'index.html'), (err) => {
    if (err) res.sendFile(path.join(__dirname, 'index.html'));
  });
});

// ─── SPA-style routing — serve index.html for any unmatched routes ────────────
app.get('*', (req, res) => {
  const reqPath = req.path === '/' ? '' : req.path;

  // Try 1: /path/index.html (e.g. /offering → /offering/index.html)
  const indexPath = path.join(__dirname, reqPath, 'index.html');
  // Try 2: /path.html (e.g. /holy-rave/confirmed → /holy-rave/confirmed.html)
  const htmlPath = path.join(__dirname, reqPath + '.html');

  res.sendFile(indexPath, (err) => {
    if (!err) return;
    res.sendFile(htmlPath, (err2) => {
      if (!err2) return;
      res.sendFile(path.join(__dirname, 'index.html'));
    });
  });
});

// ─── Resend Audience Sync ─────────────────────────────────────────────────────
// Adds contacts to a Resend audience for broadcast/automation emails.
// Requires RESEND_AUDIENCE_ID in Railway variables.
async function syncToResendAudience(email, firstName, lastName) {
  const audienceId = process.env.RESEND_AUDIENCE_ID;
  if (!audienceId) return; // silently skip if not configured

  try {
    await fetch(`https://api.resend.com/audiences/${audienceId}/contacts`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${process.env.RESEND_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        email,
        first_name: firstName || '',
        last_name: lastName || '',
        unsubscribed: false,
      }),
    });
    console.log(`[resend] Contact synced: ${email}`);
  } catch (err) {
    console.error('[resend] Audience sync error:', err.message);
  }
}

// ─── Holy Rave Confirmation Email ─────────────────────────────────────────────
async function sendHolyRaveConfirmation(email, firstName, lastName) {
  const name = firstName || '';
  const greeting = name ? `Hey ${name},` : 'Hey,';

  const resend = getResend();
  if (!resend) { console.warn('Resend not configured, skipping confirmation email'); return; }

  try {
    await resend.emails.send({
      from: 'Robert-Jan <robert-jan@robertjanmastenbroek.com>',
        reply_to: 'mastenbroekrobertjan@gmail.com',
      to: email,
      subject: "You're in — Holy Rave",
      html: `<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body style="margin:0;padding:0;background-color:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"><table width="100%" cellpadding="0" cellspacing="0" bgcolor="#0a0a0a" style="background-color:#0a0a0a"><tr><td align="center"><table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background-color:#0a0a0a" bgcolor="#0a0a0a"><tr><td style="padding:48px 32px;color:#a0a0a0;font-size:16px;line-height:1.8"><p style="color:#d4af37;font-size:13px;letter-spacing:2px;text-transform:uppercase;margin:0 0 24px">Holy Rave · Sunset Sessions</p><h1 style="font-size:28px;color:#ffffff;margin:0 0 8px;letter-spacing:2px;text-transform:uppercase;font-weight:700">You're <span style="color:#d4af37">in.</span></h1><hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0"><p style="margin:0 0 20px;color:#a0a0a0">${greeting}</p><p style="margin:0 0 20px;color:#ffffff">Your spot is confirmed — you + one friend.</p><p style="margin:0 0 20px;color:#a0a0a0">The location and time are on your ticket. Check your confirmation for the exact meeting point.</p><p style="margin:0 0 20px;color:#a0a0a0">Come to dance, stay to connect. Whether it's your first time or your tenth — you belong here.</p><hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0"><p style="font-size:13px;color:#555;margin:0 0 8px">Stay connected:</p><a href="https://chat.whatsapp.com/KNdLsExB8sP4bVomnjkqp3" style="display:inline-block;color:#d4af37;font-size:14px;text-decoration:none;letter-spacing:1px;text-transform:uppercase">WhatsApp Community →</a>&nbsp;&nbsp;&nbsp;<a href="https://www.instagram.com/robertjanmastenbroek/" style="display:inline-block;color:#d4af37;font-size:14px;text-decoration:none;letter-spacing:1px;text-transform:uppercase">Instagram →</a><hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0"><p style="font-size:13px;color:#555;margin:0">All the glory belongs to Jesus.<br>— Robert-Jan</p></td></tr></table></td></tr></table></body></html>`,
    });
    console.log(`Holy Rave confirmation sent to ${email}`);
  } catch (err) {
    console.error('Confirmation email error:', err.message);
  }
}

// ─── Offering Thank-You Email ─────────────────────────────────────────────────
async function sendThankYouEmail(email, name) {
  const firstName = name ? name.split(' ')[0] : '';
  const greeting = firstName ? `Hey ${firstName},` : 'Hey,';

  const resend = getResend();
  if (!resend) { console.warn('Resend not configured, skipping email'); return; }

  try {
    await resend.emails.send({
      from: 'Robert-Jan <robert-jan@robertjanmastenbroek.com>',
        reply_to: 'mastenbroekrobertjan@gmail.com',
      to: email,
      subject: "You're part of Holy Rave now.",
      html: `<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="margin:0;padding:0;background-color:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"><table width="100%" cellpadding="0" cellspacing="0" bgcolor="#0a0a0a" style="background-color:#0a0a0a"><tr><td align="center"><table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background-color:#0a0a0a" bgcolor="#0a0a0a"><tr><td style="padding:48px 32px"><p style="color:#d4af37;font-size:13px;letter-spacing:2px;text-transform:uppercase;margin:0 0 24px">Holy Rave</p><h1 style="font-size:28px;color:#ffffff;margin:0 0 8px;letter-spacing:2px;text-transform:uppercase;font-weight:700">You're <span style="color:#d4af37">in.</span></h1><hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:32px 0"><p style="font-size:16px;line-height:1.8;color:#a0a0a0;margin:0 0 20px">${greeting}</p><p style="font-size:16px;line-height:1.8;color:#a0a0a0;margin:0 0 20px">Something just shifted.</p><p style="font-size:16px;line-height:1.8;color:#ffffff;margin:0 0 20px">Your name is now part of what keeps this going — every free event, every track released as an offering, every person who finds their way to a dancefloor and screams Hallelujah without knowing why.</p><p style="font-size:16px;line-height:1.8;color:#a0a0a0;margin:0 0 20px">That's partly yours now.</p><p style="font-size:16px;line-height:1.8;color:#a0a0a0;margin:0 0 20px">I don't take that lightly. Every euro that comes in goes back out — toward the sound, the travel, the food at the door, the ability to say "free" without hesitation to anyone who shows up.</p><p style="font-size:16px;line-height:1.8;color:#a0a0a0;margin:0 0 20px">Watch your inbox. You'll hear from me personally — not a newsletter, not a broadcast. Just me, writing to the people who've decided to be part of this.</p><hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:32px 0"><p style="font-size:13px;color:#555;margin:0 0 8px">Come find us:</p><a href="https://www.instagram.com/robertjanmastenbroek/" style="display:inline-block;color:#d4af37;font-size:14px;text-decoration:none;letter-spacing:1px;text-transform:uppercase">Instagram →</a>&nbsp;&nbsp;&nbsp;<a href="https://chat.whatsapp.com/KNdLsExB8sP4bVomnjkqp3" style="display:inline-block;color:#d4af37;font-size:14px;text-decoration:none;letter-spacing:1px;text-transform:uppercase">WhatsApp Community →</a><hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:32px 0"><p style="font-size:13px;color:#555;margin:0">All the glory belongs to Jesus.<br>— Robert-Jan</p></td></tr></table></td></tr></table></body></html>`,
    });
    console.log(`Thank-you email sent to ${email}`);
  } catch (err) {
    console.error('Email send error:', err.message);
  }
}

app.listen(PORT, async () => {
  console.log(`Server running on port ${PORT}`);
  // Auto-run DB schema migration (idempotent — skips if tables exist)
  try {
    await db.ensureSchema();
  } catch (err) {
    console.warn('[db] Schema migration skipped — DATABASE_URL not set?', err.message);
  }

  // Auto-seed first event if none exist
  try {
    const events = await db.getUpcomingEvents();
    if (events.length === 0) {
      await db.seedEvent({
        slug: 'june-13-2026',
        title: 'Holy Rave — June 13th 2026',
        location: 'Tenerife South',
        location_detail: 'Open-air venue in Tenerife South — exact address on your ticket',
        event_date: '2026-06-13',
        event_time: 'Sunset (~19:00)',
        description: 'An evening of melodic Afrohouse and organic electronic music as the sun drops into the Atlantic. No dress code. No faith test. Just you, the music, and whoever you brought.',
        ticket_limit: 50,
      });
      await db.seedEvent({
        slug: 'june-20-2026',
        title: 'Holy Rave — June 20th 2026',
        location: 'Tenerife South',
        location_detail: 'Open-air venue in Tenerife South — exact address on your ticket',
        event_date: '2026-06-20',
        event_time: 'Sunset (~19:00)',
        description: 'Another intimate sunset session. Melodic Afrohouse and organic electronic as the sun drops. 50 people. Pay what feels right.',
        ticket_limit: 50,
      });
      console.log('[seed] Events seeded');
    }
  } catch (err) {
    console.warn('[seed] Skipped — database not available?', err.message);
  }
});
