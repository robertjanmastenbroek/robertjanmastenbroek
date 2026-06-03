const express = require('express');
const session = require('express-session');
const rateLimit = require('express-rate-limit');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const db = require('./lib/db');
const supabase = require('./lib/supabase');

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 10 * 1024 * 1024 }, // 10MB max
  fileFilter: (req, file, cb) => {
    if (file.mimetype.startsWith('image/')) cb(null, true);
    else cb(new Error('Only image files allowed'));
  },
});

const app = express();
const PORT = process.env.PORT || 8080;
const SITE_URL = process.env.SITE_URL || 'https://robertjanmastenbroek.com';
const STRIPE_PUBLISHABLE_KEY = process.env.STRIPE_PUBLISHABLE_KEY || '';

// Lazy-initialize Stripe, Resend, and Twilio so the server starts even without env vars
let twilioClient = null;
function getTwilio() {
  if (!process.env.TWILIO_ACCOUNT_SID || !process.env.TWILIO_AUTH_TOKEN) return null;
  if (!twilioClient) {
    twilioClient = require('twilio')(process.env.TWILIO_ACCOUNT_SID, process.env.TWILIO_AUTH_TOKEN);
  }
  return twilioClient;
}

const TWILIO_FROM_NUMBER = process.env.TWILIO_FROM_NUMBER || ''; // e.g. '+1234567890'
const TWILIO_ALPHA_SENDER = process.env.TWILIO_ALPHA_SENDER || ''; // Set to 'HolyRave' to enable alpha sender for EU

// EU country codes that support alpha sender IDs
const EU_COUNTRY_CODES = new Set([
  '31','32','33','34','36','39','40','41','43','44','45','46','47','48',
  '49','212','213','220','221','222','223','224','225','226','227','228',
  '229','230','231','232','233','234','235','236','237','238','239','240',
  '241','242','243','244','245','246','247','248','249','250','251','252',
  '253','254','255','256','257','258','260','261','262','263','264','265',
  '266','267','268','269','290','291','297','298','299','350','351','352',
  '353','354','355','356','357','358','359','370','371','372','373','374',
  '375','376','377','378','379','380','381','382','383','385','386','387',
  '389','420','421','423','500','501','502','503','504','505','506','507',
  '508','509','590','591','592','593','594','595','596','597','598','599',
  '670','672','673','674','675','676','677','678','679','680','681','682',
  '683','685','686','687','688','689','690','691','692','800','808','850',
  '852','853','855','856','870','880','881','882','883','886','960','961',
  '962','963','964','965','966','967','968','970','971','972','973','974',
  '975','976','977','992','993','994','995','996','998',
]);
// Focused set for EU countries that definitely support alpha sender
const EU_ALPHA_SUPPORTED = new Set([
  '31','32','33','34','36','39','40','41','43','44','45','46','47','48',
  '49','350','351','352','353','354','355','356','357','358','359','370',
  '371','372','373','374','375','376','377','378','379','380','381','382',
  '383','385','386','387','389','420','421','423',
]);

function getFromNumber(recipientPhone) {
  if (!TWILIO_FROM_NUMBER) return '';
  // Only use alpha sender if explicitly configured and recipient is in EU
  if (TWILIO_ALPHA_SENDER) {
    const cleaned = recipientPhone.replace(/\s+/g, '');
    const match = cleaned.match(/^\+(\d{1,3})/);
    if (match && EU_ALPHA_SUPPORTED.has(match[1])) {
      return TWILIO_ALPHA_SENDER;
    }
  }
  // Default: use the phone number (works for all countries)
  return TWILIO_FROM_NUMBER;
}
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

  // ─── Payment succeeded ─────────────────────────────────────────────────
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

          // Send ticket SMS with event details
          try {
            const reg = await db.getRegistrationById(regId);
            if (reg && reg.phone) {
              const eventMeta = session.metadata || {};
              let evTitle = 'Holy Rave', evDate = '', evTime = '', evLoc = '', evSlug = 'holy-rave', evDetail = '', evMaps = '';
              if (reg.event_id) {
                try {
                  const sql = db.getSql();
                  const [ev] = await sql`SELECT title, event_date, event_time, location, location_detail, maps_url, slug FROM events WHERE id = ${reg.event_id}`;
                  if (ev) { evTitle = ev.title; evDate = ev.event_date; evTime = ev.event_time; evLoc = ev.location; evSlug = ev.slug; evDetail = ev.location_detail; evMaps = ev.maps_url; }
                } catch(e) {}
              }
              sendTicketSMS(
                reg.phone,
                evTitle,
                evDate || eventMeta.event_date,
                evTime,
                evLoc || eventMeta.event_location,
                evSlug,
                regId,
                evDetail,
                evMaps || null,
                reg.confirmation_code || ''
              ).catch(e => console.error('Webhook SMS error:', e.message));
            }
          } catch (e) {
            console.error('Webhook SMS lookup error:', e.message);
          }
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

  // ─── PaymentIntent succeeded (inline card payment) ─────────────────────
  if (event.type === 'payment_intent.succeeded') {
    const pi = event.data.object;
    const regId = pi.metadata?.registration_id;
    if (regId && regId.startsWith('hr_')) {
      try {
        const confirmed = await db.confirmRegistration(regId, null);
        if (confirmed) {
          const reg = await db.getRegistrationById(regId);
          if (reg) {
            const firstName = reg.first_name || '';
            const lastName = reg.last_name || '';
            if (reg.email) {
              await sendHolyRaveConfirmation(reg.email, firstName, lastName);
              syncToResendAudience(reg.email, firstName, lastName).catch(e =>
                console.error('PI webhook audience sync error:', e.message));
            }
            if (reg.phone) {
              let evTitle = 'Holy Rave', evDate = '', evTime = '', evLoc = '', evSlug = 'holy-rave', evDetail = '', evMaps = '';
              if (reg.event_id) {
                try {
                  const sql = db.getSql();
                  const [ev] = await sql`SELECT title, event_date, event_time, location, location_detail, maps_url, slug FROM events WHERE id = ${reg.event_id}`;
                  if (ev) { evTitle = ev.title; evDate = ev.event_date; evTime = ev.event_time; evLoc = ev.location; evSlug = ev.slug; evDetail = ev.location_detail; evMaps = ev.maps_url; }
                } catch(e) {}
              }
              sendTicketSMS(
                reg.phone, evTitle,
                evDate || pi.metadata?.event_date,
                evTime,
                evLoc || pi.metadata?.event_location,
                evSlug, regId, evDetail,
                evMaps || null, reg.confirmation_code || ''
              ).catch(e => console.error('PI webhook SMS error:', e.message));
            }
          }
        }
      } catch (err) {
        console.error('PI webhook confirm error:', err.message);
      }
    }
  }

  // ─── Payment session expired (user didn't complete) ─────────────────────
  if (event.type === 'checkout.session.expired') {
    const session = event.data.object;
    const sessionId = session.id;
    try {
      await db.expireRegistrationBySessionId(sessionId);
    } catch (err) {
      console.error('Webhook expire error:', err.message);
    }
  }

  // ─── Charge refunded ────────────────────────────────────────────────────
  if (event.type === 'charge.refunded') {
    const charge = event.data.object;
    // Look up the session that created this charge to find the registration
    try {
      if (charge.payment_intent) {
        // Search for registration by checking sessions in Stripe or by metadata
        const stripe = getStripe();
        if (stripe) {
          const sessions = await stripe.checkout.sessions.list({
            payment_intent: charge.payment_intent,
            limit: 1,
          });
          const session = sessions.data[0];
          if (session) {
            const regId = session.client_reference_id || session.metadata?.registration_id;
            if (regId && regId.startsWith('hr_')) {
              await db.refundRegistration(regId);
            }
          }
        }
      }
    } catch (err) {
      console.error('Webhook refund error:', err.message);
    }
  }

  res.json({ received: true });
});

app.use(express.json());
app.use(express.static(path.join(__dirname)));

// ─── Admin auth — signed token (avoids session cookie issues) ────────────────
function signAdminToken() {
  const secret = process.env.ADMIN_API_KEY || 'fallback-dev-secret';
  const payload = JSON.stringify({ role: 'admin', t: Date.now() });
  const b64 = Buffer.from(payload).toString('base64url');
  const sig = crypto.createHmac('sha256', secret).update(b64).digest('base64url');
  return b64 + '.' + sig;
}

function verifyAdminToken(token) {
  try {
    const parts = token.split('.');
    if (parts.length !== 2) return false;
    const secret = process.env.ADMIN_API_KEY || 'fallback-dev-secret';
    const sig = crypto.createHmac('sha256', secret).update(parts[0]).digest('base64url');
    if (sig !== parts[1]) return false;
    return JSON.parse(Buffer.from(parts[0], 'base64url').toString()).role === 'admin';
  } catch (e) { return false; }
}

function requireAdmin(req, res, next) {
  // 1) Signed admin token (x-admin-token header) — primary method
  const token = req.headers['x-admin-token'];
  if (token && verifyAdminToken(token)) return next();
  // 2) Legacy session-based (still checked for compatibility)
  if (req.session && req.session.isAdmin) return next();
  // 3) Supabase Auth (Authorization: Bearer)
  const auth = req.headers['authorization'];
  if (auth && auth.startsWith('Bearer ')) {
    supabase.getUserFromToken(auth.slice(7)).then(user => {
      if (user) { req.adminUser = user; return next(); }
      return res.status(401).json({ error: 'Unauthorized. Please log in at /admin.' });
    }).catch(() => res.status(401).json({ error: 'Unauthorized.' }));
    return;
  }
  // 4) API key header (programmatic use)
  if (req.headers['x-api-key'] === process.env.ADMIN_API_KEY) return next();
  return res.status(401).json({ error: 'Unauthorized. Please log in at /admin.' });
}

// ─── Admin login (token-based, no session cookie dependency) ─────────────────
app.post('/api/admin/login', express.json(), (req, res) => {
  const { password } = req.body;
  const key = process.env.ADMIN_API_KEY;
  if (!key) return res.status(503).json({ error: 'Admin panel not configured.' });
  if (password === key || password === process.env.ADMIN_PASSWORD) {
    return res.json({ ok: true, token: signAdminToken() });
  }
  return res.status(401).json({ error: 'Incorrect password.' });
});

app.post('/api/admin/logout', (req, res) => {
  if (req.session) try { req.session.destroy(() => {}); } catch(e) {}
  res.json({ ok: true });
});

app.get('/api/admin/me', (req, res) => {
  const token = req.headers['x-admin-token'];
  if (token && verifyAdminToken(token)) return res.json({ authenticated: true });
  if (req.session && req.session.isAdmin) return res.json({ authenticated: true });
  res.json({ authenticated: false });
});

// ─── Supabase Auth config (expose URL + publishable key to frontend) ─────────
app.get('/api/auth/config', (req, res) => {
  res.json({
    url: process.env.SUPABASE_URL || '',
    key: process.env.SUPABASE_PUBLISHABLE_KEY || process.env.SUPABASE_ANON_KEY || '',
  });
});

// ─── Supabase Auth Endpoints (user accounts) ─────────────────────────────────

// POST /api/auth/signup — create account with email + password
app.post('/api/auth/signup', async (req, res) => {
  const { email, password, firstName, lastName, phone } = req.body;
  if (!email || !password || password.length < 6) {
    return res.status(400).json({ error: 'Email and password (min 6 chars) required.' });
  }

  const client = supabase.getPublicClient();
  if (!client) return res.status(503).json({ error: 'Auth system not configured.' });

  const { data, error } = await client.auth.signUp({
    email,
    password,
    options: {
      data: { first_name: firstName || '', last_name: lastName || '', phone: phone || '' },
    },
  });

  if (error) return res.status(400).json({ error: error.message });
  res.json({ ok: true, userId: data.user?.id });
});

// POST /api/auth/login — sign in with email + password
app.post('/api/auth/login', async (req, res) => {
  const { email, password } = req.body;
  if (!email || !password) return res.status(400).json({ error: 'Email and password required.' });

  const client = supabase.getPublicClient();
  if (!client) return res.status(503).json({ error: 'Auth system not configured.' });

  const { data, error } = await client.auth.signInWithPassword({ email, password });
  if (error) return res.status(401).json({ error: error.message });

  // Return session token for client-side storage
  res.json({
    ok: true,
    accessToken: data.session?.access_token,
    refreshToken: data.session?.refresh_token,
    user: {
      id: data.user?.id,
      email: data.user?.email,
      firstName: data.user?.user_metadata?.first_name || '',
      lastName: data.user?.user_metadata?.last_name || '',
      phone: data.user?.user_metadata?.phone || '',
    },
  });
});

// POST /api/auth/logout — invalidate session
app.post('/api/auth/logout', async (req, res) => {
  const authHeader = req.headers['authorization'];
  const token = authHeader?.startsWith('Bearer ') ? authHeader.slice(7) : null;

  if (token) {
    const client = supabase.getPublicClient();
    if (client) await client.auth.admin.signOut(token);
  }
  res.json({ ok: true });
});

// GET /api/auth/me — get current user from access token
app.get('/api/auth/me', async (req, res) => {
  const authHeader = req.headers['authorization'];
  const token = authHeader?.startsWith('Bearer ') ? authHeader.slice(7) : null;

  if (!token) return res.json({ authenticated: false });

  const user = await supabase.getUserFromToken(token);
  if (!user) return res.json({ authenticated: false });

  res.json({
    authenticated: true,
    user: {
      id: user.id,
      email: user.email,
      firstName: user.user_metadata?.first_name || '',
      lastName: user.user_metadata?.last_name || '',
      phone: user.user_metadata?.phone || '',
    },
  });
});

// ─── Input Validation Helpers ────────────────────────────────────────────────

const DISPOSABLE_DOMAINS = new Set([
  'mailinator.com', 'guerrillamail.com', 'guerrillamail.net', 'guerrillamail.org',
  'guerrillamail.biz', '10minutemail.com', '10minutemail.net', '10minutemail.org',
  'throwaway.email', 'tempmail.com', 'tempmail.net', 'tempmail.org',
  'yopmail.com', 'yopmail.fr', 'yopmail.net', 'yopmail.org',
  'sharklasers.com', 'grr.la', 'spam4.me', 'mailmetrash.com',
  'trashmail.com', 'trashmail.net', 'trashmail.org', 'trashmail.ws',
  'mailexpire.com', 'mailcatch.com', 'dispostable.com', 'maildrop.cc',
  'getairmail.com', 'emailtemporanea.net', 'fakemailgenerator.com',
  'spambox.us', 'spamgourmet.com', 'maileater.com', 'emailondeck.com',
  'mailnator.com', 'mintemail.com', 'spamthisplease.com', 'e4ward.com',
  'mytrashmail.com', 'mailnull.com', 'sneakemail.com', 'incognitomail.com',
  'thankyou2010.com', 'trash2009.com', 'mt2009.com', 'trashymail.com',
  'tyldd.com', 'rppkn.com', 'awsoo.com', 'zippymail.info',
  'pookmail.com', 'dontreg.com', 'hidemail.net', 'hidemail.org',
  'hidemail.us', 'poofy.org', 'wh4f.org', 'jetable.org',
  'nospam.ze.tc', 'nomail.xl.cx', 'remove.spam.me.nu', 'nobulk.ml',
]);

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const PHONE_REGEX = /^\+\d{1,4}\s?\d{4,15}$/;

function validateEmail(email) {
  if (!email || !EMAIL_REGEX.test(email)) return 'Please enter a valid email address.';
  const domain = email.split('@')[1]?.toLowerCase();
  if (domain && DISPOSABLE_DOMAINS.has(domain)) return 'Please use a permanent email address (not a disposable/temporary one).';
  return null;
}

function validatePhone(phone) {
  if (!phone) return 'Phone number is required — your ticket will be sent via WhatsApp.';
  const cleaned = phone.replace(/\s/g, '');
  if (!PHONE_REGEX.test(cleaned)) return 'Please enter a valid phone number with country code (e.g. +34 612 345 678).';
  return null;
}

// ─── Rate Limiting — prevent bot hoarding on 50-person events ──────────────
const registerLimiter = rateLimit({
  windowMs: 10 * 60 * 1000, // 10 minutes
  max: 20,                   // 20 registrations per IP per window (generous for testing)
  message: { error: 'Too many requests. Join the WhatsApp community to be notified of future events.' },
  standardHeaders: true,
  legacyHeaders: false,
});

// ─── Phone Verification Endpoints ────────────────────────────────────────────

// Rate limiter for code sends: 1 per 60s per phone
const verifyLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 1,
  keyGenerator: (req) => req.body?.phone || req.ip,
  message: { error: 'Please wait 60 seconds before requesting another code.' },
  standardHeaders: true,
  legacyHeaders: false,
});

// POST /api/verify/phone/send — generate + SMS a 6-digit code
app.post('/api/verify/phone/send', verifyLimiter, async (req, res) => {
  const { phone } = req.body;
  if (!phone) return res.status(400).json({ error: 'Phone number is required.' });

  const phoneErr = validatePhone(phone);
  if (phoneErr) return res.status(400).json({ error: phoneErr });

  try {
    const code = String(Math.floor(100000 + Math.random() * 900000));

    await db.storeVerificationCode(phone, code);

    // Send SMS via Twilio (fire-and-forget with timeout — code is already stored)
    const twilio = getTwilio();
    if (twilio && TWILIO_FROM_NUMBER) {
      const sender = getFromNumber(phone);
      console.log(`[verify] Sending to ${phone} from: "${sender}" (alpha=${!!TWILIO_ALPHA_SENDER})`);
      const sendPromise = twilio.messages.create({
        body: `Your Holy Rave verification code: ${code}. Valid for 5 minutes.`,
        from: sender,
        to: phone.replace(/\s+/g, ''),
      });
      // Race the send against a 5s timeout so we never hang the response
      Promise.race([
        sendPromise,
        new Promise((_, reject) => setTimeout(() => reject(new Error('SMS timeout')), 5000)),
      ]).then(() => {
        console.log(`[verify] Code sent to ${phone} from "${sender}"`);
      }).catch(err => {
        console.error(`[verify] SMS send issue (code still valid): ${err.message}`);
      });
    } else {
      // Fallback: log the code (dev mode)
      console.log(`[verify] DEV MODE — Code for ${phone}: ${code}`);
    }

    // Respond immediately — code is stored in DB regardless of SMS delivery
    const cleaned = phone.replace(/\s+/g, '');
    const masked = cleaned.slice(0, -4).replace(/\d/g, '*') + cleaned.slice(-4);

    res.json({ ok: true, masked });
  } catch (err) {
    console.error('[verify] Send error:', err);
    res.status(500).json({ error: 'Failed to send verification code. ' + (err.message || err) });
  }
});

// POST /api/verify/phone/check — validate the 6-digit code
app.post('/api/verify/phone/check', async (req, res) => {
  const { phone, code } = req.body;
  if (!phone || !code) return res.status(400).json({ error: 'Phone and code are required.' });
  if (!/^\d{6}$/.test(code)) return res.status(400).json({ error: 'Code must be 6 digits.' });

  try {
    const result = await db.checkVerificationCode(phone, code);
    if (!result) {
      return res.status(400).json({ error: 'Invalid or expired code. Request a new one.' });
    }
    res.json({ ok: true, verified: true });
  } catch (err) {
    console.error('[verify] Check error:', err.message);
    res.status(500).json({ error: 'Could not verify code. Try again.' });
  }
});

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
const SUPPORTER_BASE_COUNT = parseInt(process.env.SUPPORTER_BASE_COUNT || '0', 10);

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
  const emailErr = validateEmail(email);
  if (emailErr) return res.status(400).json({ error: emailErr });

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

// GET /api/holy-rave/events/:slug/recent — social proof: recent registrations
app.get('/api/holy-rave/events/:slug/recent', async (req, res) => {
  try {
    const event = await db.getEventBySlug(req.params.slug);
    if (!event) return res.status(404).json({ error: 'Event not found.' });
    const recent = await db.getRecentRegistrations(event.id, 5);
    const anonymized = recent.map(r => ({
      name: r.first_name + ' ' + r.last_name.charAt(0) + '.',
      time: r.created_at,
    }));
    res.json(anonymized);
  } catch (err) {
    console.error('Recent registrations error:', err.message);
    res.json([]);
  }
});

// GET /api/holy-rave/events/:slug/velocity — how many tickets sold in last 24h
app.get('/api/holy-rave/events/:slug/velocity', async (req, res) => {
  try {
    const event = await db.getEventBySlug(req.params.slug);
    if (!event) return res.status(404).json({ error: 'Event not found.' });
    const count = await db.getRegistrationVelocity(event.id);
    res.json({ count });
  } catch (err) {
    console.error('Velocity error:', err.message);
    res.json({ count: 0 });
  }
});

// GET /api/holy-rave/ticket/:id/calendar.ics — add-to-calendar download
app.get('/api/holy-rave/ticket/:id/calendar.ics', async (req, res) => {
  try {
    const reg = await db.getRegistrationById(req.params.id);
    if (!reg) return res.status(404).send('Not found');

    let eventTitle = 'Holy Rave';
    let eventDate = new Date();
    let eventLocation = 'Tenerife';

    if (reg.event_id) {
      const sql = db.getSql();
      const [ev] = await sql`
        SELECT title, event_date, event_time, location
        FROM events WHERE id = ${reg.event_id}
      `;
      if (ev) {
        eventTitle = ev.title;
        eventDate = new Date(ev.event_date + 'T' + (ev.event_time === 'Sunset' ? '19:00:00' : '18:00:00'));
        eventLocation = ev.location;
      }
    }

    const endDate = new Date(eventDate);
    endDate.setHours(endDate.getHours() + 3);

    const fmt = (d) => d.toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';

    const ics = [
      'BEGIN:VCALENDAR',
      'VERSION:2.0',
      'PRODID:-//Holy Rave//Events//EN',
      'BEGIN:VEVENT',
      'UID:' + reg.id + '@robertjanmastenbroek.com',
      'DTSTAMP:' + fmt(new Date()),
      'DTSTART:' + fmt(eventDate),
      'DTEND:' + fmt(endDate),
      'SUMMARY:' + eventTitle,
      'DESCRIPTION:Holy Rave Sunset Session — you + 1 friend. Pay what feels right. All the glory belongs to Jesus.',
      'LOCATION:' + eventLocation,
      'END:VEVENT',
      'END:VCALENDAR',
    ].join('\r\n');

    res.setHeader('Content-Type', 'text/calendar; charset=utf-8');
    res.setHeader('Content-Disposition', 'attachment; filename="holy-rave.ics"');
    res.send(ics);
  } catch (err) {
    console.error('Calendar error:', err.message);
    res.status(500).send('Could not generate calendar file.');
  }
});

// POST /api/holy-rave/register — create a registration + optional Stripe checkout
app.post('/api/holy-rave/register', registerLimiter, async (req, res) => {
  const { firstName, lastName, email, phone, amount, eventSlug } = req.body;

  if (!firstName || !lastName || !email) {
    return res.status(400).json({ error: 'Please fill in all fields correctly.' });
  }

  const emailErr = validateEmail(email);
  if (emailErr) return res.status(400).json({ error: emailErr });

  const phoneErr = validatePhone(phone);
  if (phoneErr) return res.status(400).json({ error: phoneErr });

  const amt = Math.max(100, parseInt(amount, 10) || 100); // Minimum €1 (100 cents)

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

    // Build event description for Stripe
    let eventDateStr = '';
    let eventLocationStr = '';
    if (eventSlug) {
      const ev = await db.getEventBySlug(eventSlug);
      if (ev) {
        eventDateStr = ev.event_date || '';
        eventLocationStr = ev.location || '';
      }
    }

    // Save contact info FIRST so user data is captured even if Stripe fails
    await db.addSubscriber(email, firstName, lastName, 'holy_rave', phone);
    console.log(`[register] Subscriber saved: ${email}`);

    // Then create Stripe PaymentIntent for inline payment
    const stripe = getStripe();
    if (!stripe) {
      // Stripe not configured — save as pending registration
      await db.createRegistration({
        id, firstName, lastName, email, phone, amount: amt, week, eventId,
      });
      return res.json({ id, confirmed: false, note: 'Payment system not available. Registration saved without payment.' });
    }

    console.log(`[register] Creating PaymentIntent for ${email} — €${(amt / 100).toFixed(2)}`);

    // Statement descriptor shows on bank statements
    // Cards: ACCOUNT_PREFIX*HOLY RAVE 13 JUN (via suffix, 22 chars)
    // iDEAL: HOLY RAVE 13 JUN (via descriptor, 22 chars)
    const MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
    const day = String(eventDateStr || '').slice(8, 10);
    const monthNum = String(eventDateStr || '').slice(5, 7);
    const monthCode = MONTHS[parseInt(monthNum, 10) - 1] || '';
    const dateCode = (day && monthCode) ? `${day} ${monthCode}` : 'EVENT';
    const stmtDesc = `HOLY RAVE ${dateCode}`.substring(0, 22).toUpperCase();

    // Timeout the Stripe call after 15 seconds to prevent hanging
    const paymentIntent = await Promise.race([
      stripe.paymentIntents.create({
        amount: amt,
        currency: 'eur',
        metadata: {
          registration_id: id,
          event_id: eventId ? String(eventId) : '',
          event_date: eventDateStr || '',
          event_location: eventLocationStr || '',
          event_slug: eventSlug || '',
        },
        description: `${firstName} ${lastName} — ${eventTitle}`,
        statement_descriptor: stmtDesc,
        statement_descriptor_suffix: stmtDesc,
        automatic_payment_methods: { enabled: true },
      }),
      new Promise((_, reject) => setTimeout(() => reject(new Error('Stripe API timeout after 15s')), 15000)),
    ]).catch(err => {
      console.error('[register] Stripe PaymentIntent error:', err.message);
      throw err;
    });

    console.log(`[register] PaymentIntent created: ${paymentIntent.id}`);

    await db.createRegistration({
      id, firstName, lastName, email, phone, amount: amt, week, eventId,
      stripeSessionId: paymentIntent.id,
    });

    // Sync to Resend (fire-and-forget, happens regardless)
    syncToResendAudience(email, firstName, lastName, phone).catch(e =>
      console.error('[register] Resend sync error:', e.message));

    // Return clientSecret for inline Payment Element — no redirect needed
    res.json({ id, clientSecret: paymentIntent.client_secret, paymentIntentId: paymentIntent.id });
  } catch (err) {
    console.error('[register] Holy Rave register error:', err);
    console.error('[register] Error details:', JSON.stringify(err, Object.getOwnPropertyNames(err)));
    const message = err.message || 'Could not complete registration. Please try again.';
    const isStripe = err.type && err.type.startsWith('Stripe');
    res.status(isStripe ? 402 : 500).json({ error: message + (isStripe ? ' (Stripe error — check Railway logs)' : '') });
  }
});

// POST /api/holy-rave/confirm-payment — confirm a registration after successful PaymentIntent
// Called by the frontend after stripe.confirmPayment() succeeds, so we don't rely
// solely on the webhook (which requires STRIPE_WEBHOOK_SECRET to be configured).
app.post('/api/holy-rave/confirm-payment', async (req, res) => {
  let { regId, paymentIntentId } = req.body;

  // If regId looks like a Stripe PaymentIntent (pi_...), look up by it
  if (!regId || regId.startsWith('pi_')) {
    const found = await db.getRegistrationByStripeSession(paymentIntentId || regId);
    if (found) regId = found.id;
  }

  if (!regId) return res.status(400).json({ error: 'Registration ID required.' });

  try {
    const reg = await db.getRegistrationById(regId);
    if (!reg) return res.status(404).json({ error: 'Registration not found.' });
    if (reg.status === 'confirmed') return res.json({ ok: true, already: true });

    const confirmed = await db.confirmRegistration(regId, null);
    if (!confirmed) return res.status(409).json({ error: 'Could not confirm — event may be full.' });

    // Fire-and-forget: send email + SMS + sync (don't await — return immediately)
    setImmediate(async () => {
      // Build event details for both email and SMS
      let ev = null;
      if (reg.event_id) {
        try {
          const sql = db.getSql();
          [ev] = await sql`SELECT title, event_date, event_time, location, location_detail, maps_url, slug FROM events WHERE id = ${reg.event_id}`;
        } catch(e) {}
      }
      const eventDetails = ev ? {
        eventDate: ev.event_date ? new Date(ev.event_date + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' }) : '',
        eventTime: ev.event_time || '20:00 – 23:00',
        eventLocation: ev.location || '',
        locationDetail: ev.location_detail || '',
        mapsUrl: ev.maps_url || '',
        confirmationCode: reg.confirmation_code || '',
      } : null;

      if (reg.email) {
        sendHolyRaveConfirmation(reg.email, reg.first_name, reg.last_name, eventDetails).catch(e =>
          console.error('Confirm email error:', e.message));
        syncToResendAudience(reg.email, reg.first_name, reg.last_name, reg.phone).catch(e =>
          console.error('Confirm sync error:', e.message));
      }
      if (reg.phone && ev) {
            sendTicketSMS(
              reg.phone,
              ev.title || 'Holy Rave',
              ev.event_date,
              ev.event_time,
              ev.location,
              ev.slug || 'holy-rave',
              regId,
              ev.location_detail || '',
              ev.maps_url || null,
              reg.confirmation_code || ''
            ).catch(e => console.error('Confirm SMS error:', e.message));
      }
    });

    res.json({ ok: true });
  } catch (err) {
    console.error('[confirm-payment] Error:', err.message);
    res.status(500).json({ error: err.message });
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

// GET /api/holy-rave/events/past — past events
app.get('/api/holy-rave/events/past', async (req, res) => {
  try {
    const events = await db.getPastEvents(20);
    res.json(events);
  } catch (err) {
    console.error('Past events error:', err.message);
    res.json([]);
  }
});

// ─── Pexels image search (protected by PEXELS_API_KEY) ───────────────────────
app.get('/api/admin/pexels-search', requireAdmin, async (req, res) => {
  const query = req.query.q || 'sunset ocean tenerife';
  const perPage = Math.min(parseInt(req.query.per_page, 10) || 20, 40);
  const page = parseInt(req.query.page, 10) || 1;
  const apiKey = process.env.PEXELS_API_KEY;

  if (!apiKey) {
    return res.status(503).json({ error: 'Pexels API key not configured. Set PEXELS_API_KEY in Railway.' });
  }

  try {
    const r = await fetch(`https://api.pexels.com/v1/search?query=${encodeURIComponent(query)}&per_page=${perPage}&page=${page}`, {
      headers: { 'Authorization': apiKey },
    });
    if (!r.ok) throw new Error('Pexels API error: ' + r.status);
    const data = await r.json();
    res.json(data);
  } catch (err) {
    console.error('Pexels search error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ─── Admin: Registrations management ─────────────────────────────────────────

// GET /api/admin/events/:slug/registrations — list all registrations for an event
app.get('/api/admin/events/:slug/registrations', requireAdmin, async (req, res) => {
  try {
    const event = await db.getEventBySlug(req.params.slug);
    if (!event) return res.status(404).json({ error: 'Event not found' });
    const registrations = await db.getRegistrationsByEventId(event.id);
    res.json(registrations);
  } catch (err) {
    console.error('Admin registrations error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// DELETE /api/admin/registrations/:id — cancel a registration
app.delete('/api/admin/registrations/:id', requireAdmin, async (req, res) => {
  try {
    const cancelled = await db.cancelRegistration(req.params.id);
    if (!cancelled) return res.status(400).json({ error: 'Could not cancel — registration may already be confirmed' });
    res.json({ ok: true });
  } catch (err) {
    console.error('Admin cancel registration error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ─── Admin: Create/update/delete events ──────────────────────────────────────

// GET /api/admin/events — list all events (for admin panel)
app.get('/api/admin/events', requireAdmin, async (req, res) => {
  try {
    const events = await db.getAllEvents();
    res.json(events);
  } catch (err) {
    console.error('Admin events error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// POST /api/admin/events — create a new event
app.post('/api/admin/events', requireAdmin, async (req, res) => {
  const { slug, title, location, location_detail, event_date, event_time, description, ticket_limit, image_url, maps_url } = req.body;
  if (!slug || !title || !location || !event_date) {
    return res.status(400).json({ error: 'Missing required fields: slug, title, location, event_date' });
  }

  try {
    await db.seedEvent({ slug, title, location, location_detail, event_date, event_time, description, ticket_limit: ticket_limit || 50, image_url: image_url || null, maps_url: maps_url || null });
    res.json({ ok: true, slug });
  } catch (err) {
    console.error('Admin create event error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// PUT /api/admin/events/:slug — update event status (upcoming/past/cancelled)
app.put('/api/admin/events/:slug', requireAdmin, async (req, res) => {
  const { status } = req.body;
  if (!['upcoming', 'past', 'cancelled'].includes(status)) {
    return res.status(400).json({ error: 'Status must be: upcoming, past, or cancelled' });
  }
  try {
    await db.updateEventStatus(req.params.slug, status);
    res.json({ ok: true });
  } catch (err) {
    console.error('Admin update error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// DELETE /api/admin/events/:slug — delete an event
app.delete('/api/admin/events/:slug', requireAdmin, async (req, res) => {
  try {
    await db.deleteEvent(req.params.slug);
    res.json({ ok: true });
  } catch (err) {
    console.error('Admin delete error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ─── Admin: List all subscribers with contact info ───────────────────────────
app.get('/api/admin/subscribers', requireAdmin, async (req, res) => {
  try {
    const subscribers = await db.getSubscribersWithHolyRave();
    res.json(subscribers);
  } catch (err) {
    console.error('Admin subscribers error:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ─── Admin: Sync all existing DB contacts to Resend audience ──────────────────
app.get('/api/admin/sync-audience', requireAdmin, async (req, res) => {
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

// ─── Event image upload (admin, protected) ───────────────────────────────────
app.post('/api/admin/event-images/upload', requireAdmin, upload.single('image'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'No image file provided' });

    const ext = path.extname(req.file.originalname).toLowerCase() || '.jpg';
    const shortId = crypto.randomBytes(6).toString('base64url');
    const id = 'evimg_' + shortId;

    await db.insertEventImage({
      id,
      filename: req.file.originalname,
      mimeType: req.file.mimetype,
      imageData: req.file.buffer,
    });

    const url = `/api/images/event/${shortId}${ext}`;
    res.json({ ok: true, id, url, filename: req.file.originalname });
  } catch (err) {
    console.error('Image upload error:', err.message);
    res.status(500).json({ error: 'Upload failed: ' + err.message });
  }
});

// ─── Serve uploaded event images ───────────────────────────────────────────
app.get('/api/images/event/:shortId', async (req, res) => {
  try {
    // Strip any file extension that might be in the URL
    const base = req.params.shortId.replace(/\.[^.]+$/, '');
    const id = 'evimg_' + base;
    const img = await db.getEventImage(id);
    if (!img) return res.status(404).send('Image not found');

    res.setHeader('Content-Type', img.mime_type);
    res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
    res.send(img.image_data);
  } catch (err) {
    console.error('Image serve error:', err.message);
    res.status(500).send('Error serving image');
  }
});

// ─── Twilio configuration check (for debugging) ─────────────────────────────
app.get('/api/debug/twilio', (req, res) => {
  const sid = process.env.TWILIO_ACCOUNT_SID || '';
  const token = process.env.TWILIO_AUTH_TOKEN || '';
  const fromNumber = process.env.TWILIO_FROM_NUMBER || '';
  res.json({
    sidSet: !!sid,
    sidPrefix: sid ? sid.substring(0, 6) + '...' : null,
    tokenSet: !!token,
    fromNumberSet: !!fromNumber,
    fromNumber: fromNumber || null,
    testTwilio: (() => {
      try {
        const t = getTwilio();
        return !!t;
      } catch (e) { return false; }
    })(),
  });
});

// ─── Resend configuration check (for debugging) ──────────────────────────────
app.get('/api/debug/resend', (req, res) => {
  const apiKey = process.env.RESEND_API_KEY || '';
  const audienceId = process.env.RESEND_AUDIENCE_ID || '';
  res.json({
    apiKeySet: !!apiKey,
    apiKeyPrefix: apiKey ? apiKey.substring(0, 8) + '...' : null,
    audienceIdSet: !!audienceId,
    audienceIdPrefix: audienceId ? audienceId.substring(0, 8) + '...' : null,
    testResend: (() => {
      const r = getResend();
      return !!r;
    })(),
  });
});

// POST /api/debug/test-email — send a test confirmation email to verify domain
app.post('/api/debug/test-email', async (req, res) => {
  const { email } = req.body;
  if (!email) return res.status(400).json({ error: 'Email required' });

  const resend = getResend();
  if (!resend) return res.status(503).json({ error: 'Resend not configured' });

  try {
    const result = await resend.emails.send({
      from: 'Robert-Jan <robert-jan@robertjanmastenbroek.com>',
      reply_to: 'mastenbroekrobertjan@gmail.com',
      to: email,
      subject: 'Test — Holy Rave Confirmation Email',
      html: `<p style="font-family:sans-serif;color:#333;">Test email from Holy Rave server.</p><p style="font-family:sans-serif;color:#333;">If you're seeing this, Resend can send from robert-jan@robertjanmastenbroek.com successfully.</p>`,
    });
    res.json({ ok: true, id: result.id });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── Stripe configuration check (for debugging) ──────────────────────────────
app.get('/api/debug/stripe', (req, res) => {
  const secretKey = process.env.STRIPE_SECRET_KEY || '';
  const pubKey = process.env.STRIPE_PUBLISHABLE_KEY || '';
  res.json({
    secretKeySet: !!secretKey,
    secretKeyPrefix: secretKey ? secretKey.substring(0, 8) + '...' : null,
    pubKeySet: !!pubKey,
    pubKeyPrefix: pubKey ? pubKey.substring(0, 8) + '...' : null,
    testStripe: (() => {
      try {
        const stripe = getStripe();
        return !!stripe;
      } catch(e) { return false; }
    })(),
  });
});

// ─── Stripe publishable key (for frontend Embedded Checkout) ─────────────────
app.get('/api/stripe/publishable-key', (req, res) => {
  res.json({ key: STRIPE_PUBLISHABLE_KEY });
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
async function syncToResendAudience(email, firstName, lastName, phone) {
  const audienceId = process.env.RESEND_AUDIENCE_ID;
  if (!audienceId) return; // silently skip if not configured

  try {
    const body = {
      email,
      first_name: firstName || '',
      last_name: lastName || '',
      unsubscribed: false,
    };
    if (phone) body.phone_number = phone;

    await fetch(`https://api.resend.com/audiences/${audienceId}/contacts`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${process.env.RESEND_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    console.log(`[resend] Contact synced: ${email}${phone ? ' + phone' : ''}`);
  } catch (err) {
    console.error('[resend] Audience sync error:', err.message);
  }
}

// ─── Holy Rave Ticket SMS ────────────────────────────────────────────────────
// Sends a ticket SMS with event location and time via Twilio.
// Fire-and-forget with timeout — doesn't block the caller.
async function sendTicketSMS(phone, eventTitle, eventDate, eventTime, eventLocation, slug, regId, locationDetail, mapsUrl, confirmationCode) {
  if (!phone || !TWILIO_FROM_NUMBER) {
    console.log(`[sms] Skipping SMS (no phone or from number) for ${phone}`);
    return;
  }

  const twilio = getTwilio();
  if (!twilio) {
    console.log('[sms] Twilio not configured — skipping SMS send');
    return;
  }

  // Format date robustly — handles Date objects from DB and string formats
  let dateStr = 'TBA';
  try {
    const d = eventDate ? (typeof eventDate === 'string' && !eventDate.includes('Invalid') ? new Date(eventDate + 'T12:00:00') : new Date(eventDate)) : null;
    if (d && !isNaN(d.getTime())) dateStr = d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
  } catch (e) {}

  const timeStr = eventTime || '20:00 – 23:00';
  // Use location_detail instead of both location + detail to avoid duplication
  const locStr = locationDetail || eventLocation || 'Tenerife South';
  const codeStr = confirmationCode ? `\n🔑 ${confirmationCode}` : '';
  const mapStr = mapsUrl ? `\n🗺️ ${mapsUrl}` : '';
  // Prefix slug with 'holy-rave/' for correct link
  const slugPart = slug && slug.startsWith('holy-rave/') ? slug : 'holy-rave/' + (slug || '');
  const link = `${SITE_URL}/${slugPart}${regId ? '?confirmed=' + regId : ''}`;

  const message = `You're in for Holy Rave! ✨\n\n📍 ${locStr}${mapStr}\n📅 ${dateStr}\n🕐 ${timeStr}\n👥 You + 1 friend${codeStr}\n\nShow at the door.\n\n${link}`;

  const sender = getFromNumber(phone);
  console.log(`[sms] Sending ticket to ${phone} from: "${sender}"`);
  const sendPromise = twilio.messages.create({
    body: message,
    from: sender,
    to: phone.replace(/\s+/g, ''),
  });

  Promise.race([
    sendPromise,
    new Promise((_, reject) => setTimeout(() => reject(new Error('SMS timeout')), 5000)),
  ]).then(result => {
    console.log(`[sms] Ticket SMS sent to ${phone} from "${sender}" (sid: ${result.sid})`);
  }).catch(err => {
    console.error('[sms] Failed to send SMS:', err.message);
  });
}

// ─── Holy Rave Confirmation Email ─────────────────────────────────────────────
async function sendHolyRaveConfirmation(email, firstName, lastName, eventDetails) {
  const name = firstName || '';
  const greeting = name ? `Hey ${name},` : 'Hey,';

  // Build ticket details section from event data
  const hasDetails = eventDetails && (eventDetails.eventDate || eventDetails.eventTime || eventDetails.eventLocation);
  const dateLine = eventDetails?.eventDate ? `<tr><td style="padding:4px 0;color:#a0a0a0;font-size:14px;"><span style="color:#d4af37;font-weight:600;">📅</span> ${eventDetails.eventDate}</td></tr>` : '';
  const timeLine = eventDetails?.eventTime ? `<tr><td style="padding:4px 0;color:#a0a0a0;font-size:14px;"><span style="color:#d4af37;font-weight:600;">🕐</span> ${eventDetails.eventTime}</td></tr>` : '';
  const locLine = eventDetails?.eventLocation ? `<tr><td style="padding:4px 0;color:#a0a0a0;font-size:14px;"><span style="color:#d4af37;font-weight:600;">📍</span> ${eventDetails.eventLocation}</td></tr>` : '';
  const detailLine = eventDetails?.locationDetail ? `<tr><td style="padding:4px 0;color:#a0a0a0;font-size:14px;"><span style="color:#d4af37;font-weight:600;">📌</span> ${eventDetails.locationDetail}</td></tr>` : '';
  const mapLine = eventDetails?.mapsUrl ? `<tr><td style="padding:4px 0;color:#a0a0a0;font-size:14px;"><span style="color:#d4af37;font-weight:600;">🗺️</span> <a href="${eventDetails.mapsUrl}" style="color:#d4af37;text-decoration:underline;">Open in Google Maps</a></td></tr>` : '';
  const codeLine = eventDetails?.confirmationCode ? `<tr><td style="padding:4px 0;color:#a0a0a0;font-size:14px;"><span style="color:#d4af37;font-weight:600;">🔑</span> Confirmation: <strong style="color:#ffffff;">${eventDetails.confirmationCode}</strong></td></tr>` : '';
  const ticketSection = hasDetails ? `
    <hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0">
    <table style="width:100%;max-width:400px;margin:0 auto;">
      ${dateLine}${timeLine}${locLine}${detailLine}${mapLine}${codeLine}
    </table>
  ` : '';

  const resend = getResend();
  if (!resend) { console.warn('Resend not configured, skipping confirmation email'); return; }

  try {
    await resend.emails.send({
      from: 'Robert-Jan <robert-jan@robertjanmastenbroek.com>',
        reply_to: 'mastenbroekrobertjan@gmail.com',
      to: email,
      subject: "You're in — Holy Rave",
      html: `<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body style="margin:0;padding:0;background-color:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"><table width="100%" cellpadding="0" cellspacing="0" bgcolor="#0a0a0a" style="background-color:#0a0a0a"><tr><td align="center"><table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background-color:#0a0a0a" bgcolor="#0a0a0a"><tr><td style="padding:48px 32px;color:#a0a0a0;font-size:16px;line-height:1.8"><p style="color:#d4af37;font-size:13px;letter-spacing:2px;text-transform:uppercase;margin:0 0 24px">Holy Rave · Sunset Sessions</p><h1 style="font-size:28px;color:#ffffff;margin:0 0 8px;letter-spacing:2px;text-transform:uppercase;font-weight:700">Payment <span style="color:#d4af37">Confirmed</span></h1><p style="margin:0 0 4px;color:#7a7266;font-size:13px;letter-spacing:1px;text-transform:uppercase">Receipt</p><hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0"><p style="margin:0 0 20px;color:#a0a0a0">${greeting}</p><p style="margin:0 0 20px;color:#ffffff">Your payment went through. Your spot is confirmed — you + one friend.</p>${ticketSection}<hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0"><p style="margin:0 0 20px;color:#a0a0a0">Your ticket with the exact location and time is being sent to your phone via SMS. It arrives within 2–5 minutes depending on your carrier. If it doesn't arrive, check your WhatsApp for a message from us or show this email at the door.</p><p style="margin:0 0 20px;color:#ffffff">🔑 Your confirmation code: <strong>${eventDetails?.confirmationCode || '—'}</strong></p><hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0"><p style="font-size:13px;color:#555;margin:0 0 8px">Stay connected:</p><a href="https://chat.whatsapp.com/KNdLsExB8sP4bVomnjkqp3" style="display:inline-block;color:#d4af37;font-size:14px;text-decoration:none;letter-spacing:1px;text-transform:uppercase">WhatsApp Community →</a>&nbsp;&nbsp;&nbsp;<a href="https://www.instagram.com/robertjanmastenbroek/" style="display:inline-block;color:#d4af37;font-size:14px;text-decoration:none;letter-spacing:1px;text-transform:uppercase">Instagram →</a><hr style="border:none;border-top:1px solid rgba(255,255,255,0.08);margin:28px 0"><p style="font-size:13px;color:#555;margin:0">All the glory belongs to Jesus.<br>— Robert-Jan</p></td></tr></table></td></tr></table></body></html>`,
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
        location: 'Scallywags, Puerto Colón, Tenerife South',
        location_detail: 'Scallywags bar, Puerto Colón marina — sunset views over the Atlantic',
        event_date: '2026-06-13',
        event_time: '20:00 – 23:00',
        description: 'An evening of melodic Afrohouse and organic electronic music as the sun drops into the Atlantic. No dress code. No faith test. Just you, the music, and whoever you brought.',
        ticket_limit: 50,
      });
      await db.seedEvent({
        slug: 'june-20-2026',
        title: 'Holy Rave — June 20th 2026',
        location: 'Scallywags, Puerto Colón, Tenerife South',
        location_detail: 'Scallywags bar, Puerto Colón marina — sunset views over the Atlantic',
        event_date: '2026-06-20',
        event_time: '20:00 – 23:00',
        description: 'Another intimate sunset session. Melodic Afrohouse and organic electronic as the sun drops over Puerto Colón. 50 people. Pay what feels right.',
        ticket_limit: 50,
      });
      console.log('[seed] Events seeded');
    }
  } catch (err) {
    console.warn('[seed] Skipped — database not available?', err.message);
  }
});
