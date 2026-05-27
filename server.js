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
        }
      } catch (err) {
        console.error('Webhook confirm error:', err.message);
      }
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
    // Continue — don't block the welcome email on DB failure
  }

  // Send welcome email
  const resend = getResend();
  if (resend) {
    try {
      await resend.emails.send({
        from: 'Robert-Jan <robert-jan@robertjanmastenbroek.com>',
        to: email,
        subject: 'New music. Every Friday.',
        html: `<!DOCTYPE html><html><head><meta charset="UTF-8"><style>body{margin:0;padding:0;background:#0a0a0a;font-family:-apple-system,sans-serif}.w{max-width:520px;margin:0 auto;padding:48px 32px}h1{font-size:26px;color:#fff;margin:0 0 8px;letter-spacing:2px;text-transform:uppercase}.gold{color:#d4af37}p{font-size:16px;line-height:1.8;color:#a0a0a0;margin:0 0 20px}hr{border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0}.footer{font-size:13px;color:#555}</style></head><body><div class="w"><p class="gold" style="font-size:13px;letter-spacing:2px;text-transform:uppercase;margin-bottom:24px">Robert-Jan Mastenbroek</p><h1>You're <span class="gold">in.</span></h1><hr><p>New music drops every Friday.</p><p>You'll hear it first.</p><hr><p class="footer">All the glory belongs to Jesus.<br>— Robert-Jan</p></div></body></html>`,
      });
      // Notify RJM
      await resend.emails.send({
        from: 'robert-jan@robertjanmastenbroek.com',
        to: 'robert-jan@robertjanmastenbroek.com',
        subject: `New subscriber: ${email}`,
        html: `<p style="font-family:sans-serif">New subscriber: <strong>${email}</strong></p>`,
      });
    } catch (e) { console.error('Subscribe email error:', e.message); }
  }

  res.json({ ok: true });
});

// ─── Holy Rave Weekly Tickets ────────────────────────────────────────────────
const TICKETS_MAX = 50; // reservation slots (each admits 2 people)

function getWeekMonday() {
  const now = new Date();
  const day = now.getDay(); // 0=Sun, 1=Mon, ...
  const diff = day === 0 ? 6 : day - 1;
  const monday = new Date(now);
  monday.setHours(0, 0, 0, 0);
  monday.setDate(now.getDate() - diff);
  return monday.toISOString().split('T')[0];
}

// GET /api/holy-rave/tickets — remaining spots for the current week
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
  const { firstName, lastName, email, amount } = req.body;

  if (!firstName || !lastName || !email || !email.includes('@')) {
    return res.status(400).json({ error: 'Please fill in all fields correctly.' });
  }

  const amt = Math.max(0, parseInt(amount, 10) || 0);
  const week = getWeekMonday();

  try {
    // Check availability
    const stats = await db.getWeekStats(week);
    if (stats.remaining <= 0) {
      return res.status(400).json({ error: 'All tickets are taken this week. Check back soon.' });
    }

    // Check duplicate email
    if (await db.isDuplicateEmail(week, email)) {
      return res.status(400).json({ error: 'This email already has a spot this week.' });
    }

    const id = 'hr_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);

    // Free ticket — confirm immediately
    if (amt === 0) {
      await db.createRegistration({ id, firstName, lastName, email, amount: 0, week });
      sendHolyRaveConfirmation(email, firstName, lastName).catch(e =>
        console.error('Free ticket email error:', e.message));
      return res.json({ id, confirmed: true });
    }

    // Paid ticket — create Stripe Checkout
    const stripe = getStripe();
    if (!stripe) {
      return res.status(503).json({ error: 'Payment system not available.' });
    }

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
            name: 'Holy Rave — Weekly Ticket',
            description: `${firstName} ${lastName} — pay what feels right`,
          },
          unit_amount: amt,
        },
        quantity: 1,
      }],
      metadata: {
        registration_id: id,
        week,
      },
      success_url: `${SITE_URL}/holy-rave/confirmed?id=${id}`,
      cancel_url: `${SITE_URL}/holy-rave`,
    });

    await db.createRegistration({
      id, firstName, lastName, email, amount: amt, week,
      stripeSessionId: session.id,
    });

    res.json({ id, checkoutUrl: session.url });
  } catch (err) {
    console.error('Holy Rave register error:', err.message);
    // Pass through Stripe and DB errors so the user sees what's wrong
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
    res.json({
      id: reg.id,
      firstName: reg.first_name,
      lastName: reg.last_name,
      email: reg.email,
      status: reg.status,
      amount: reg.amount_cents,
    });
  } catch (err) {
    console.error('Verify error:', err.message);
    res.status(500).json({ error: 'Could not verify registration.' });
  }
});

// ─── Health check ─────────────────────────────────────────────────────────────
app.get('/health', (req, res) => res.send('OK'));

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

// ─── Holy Rave Confirmation Email ─────────────────────────────────────────────
async function sendHolyRaveConfirmation(email, firstName, lastName) {
  const name = firstName || '';
  const greeting = name ? `Hey ${name},` : 'Hey,';

  const resend = getResend();
  if (!resend) { console.warn('Resend not configured, skipping confirmation email'); return; }

  try {
    await resend.emails.send({
      from: 'Robert-Jan <robert-jan@robertjanmastenbroek.com>',
      to: email,
      subject: "You're in — Holy Rave this weekend",
      html: `<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><style>body{margin:0;padding:0;background:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.w{max-width:520px;margin:0 auto;padding:48px 32px}h1{font-size:28px;color:#fff;margin:0 0 8px;letter-spacing:2px;text-transform:uppercase}.gold{color:#d4af37}p{font-size:16px;line-height:1.8;color:#a0a0a0;margin:0 0 20px}.highlight{color:#fff}.hr{border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0}.cta{display:inline-block;color:#d4af37;font-size:14px;text-decoration:none;letter-spacing:1px;text-transform:uppercase}.ft{font-size:13px;color:#555}</style></head><body><div class="w"><p class="gold" style="font-size:13px;letter-spacing:2px;text-transform:uppercase;margin-bottom:24px">Holy Rave · Sunset Sessions</p><h1>You're <span class="gold">in.</span></h1><hr class="hr"><p>${greeting}</p><p class="highlight">Your spot is confirmed — you + one friend.</p><p>The session is this weekend (Friday, Saturday, or Sunday). The exact location and time will land in your inbox <strong>24 hours before</strong> the event.</p><p>Come to dance, stay to connect. Whether it's your first time or your tenth — you belong here.</p><hr class="hr"><p class="ft">Location + more info:</p><a href="https://chat.whatsapp.com/KNdLsExB8sP4bVomnjkqp3" class="cta">WhatsApp Community →</a>&nbsp;&nbsp;&nbsp;<a href="https://www.instagram.com/robertjanmastenbroek/" class="cta">Instagram →</a><hr class="hr"><p class="ft">All the glory belongs to Jesus.<br>— Robert-Jan</p></div></body></html>`,
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
      to: email,
      subject: "You're part of Holy Rave now.",
      html: `
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body { margin: 0; padding: 0; background: #0a0a0a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
    .wrapper { max-width: 560px; margin: 0 auto; padding: 48px 32px; }
    h1 { font-size: 28px; color: #ffffff; margin: 0 0 8px 0; letter-spacing: 2px; text-transform: uppercase; }
    .gold { color: #d4af37; }
    p { font-size: 16px; line-height: 1.8; color: #a0a0a0; margin: 0 0 20px 0; }
    .highlight { color: #ffffff; }
    .divider { border: none; border-top: 1px solid rgba(255,255,255,0.08); margin: 32px 0; }
    .cta-link { display: inline-block; color: #d4af37; font-size: 14px; text-decoration: none; letter-spacing: 1px; text-transform: uppercase; }
    .footer { font-size: 13px; color: #555; }
  </style>
</head>
<body>
  <div class="wrapper">
    <p style="color: #d4af37; font-size: 13px; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 24px;">Holy Rave · Sacred Music for Every Dancefloor</p>
    <h1>You're <span class="gold">in.</span></h1>
    <hr class="divider">
    <p>${greeting}</p>
    <p>Something just shifted.</p>
    <p class="highlight">Your name is now part of what keeps this going — every free event, every track released as an offering, every person who finds their way to a dancefloor and screams Hallelujah without knowing why.</p>
    <p>That's partly yours now.</p>
    <p>I don't take that lightly. Every euro that comes in goes back out — toward the sound, the travel, the food at the door, the ability to say "free" without hesitation to anyone who shows up.</p>
    <p>Watch your inbox. You'll hear from me personally — not a newsletter, not a broadcast. Just me, writing to the people who've decided to be part of this.</p>
    <hr class="divider">
    <p class="footer">Come find us:</p>
    <a href="https://www.instagram.com/robertjanmastenbroek/" class="cta-link">Instagram →</a>&nbsp;&nbsp;&nbsp;
    <a href="https://chat.whatsapp.com/KNdLsExB8sP4bVomnjkqp3" class="cta-link">WhatsApp Community →</a>
    <hr class="divider">
    <p class="footer">All the glory belongs to Jesus.<br>— Robert-Jan</p>
  </div>
</body>
</html>
      `,
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
});
