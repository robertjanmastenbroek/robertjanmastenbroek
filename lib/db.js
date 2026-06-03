const { Pool } = require('pg');

// Lazy-initialize a connection pool. Uses standard TCP — works with Railway
// Postgres, Supabase, and any PostgreSQL. The `sql` tagged template function
// is constructed from the pool so all existing `await sql\`...\`` calls work.
function getSql() {
  if (!process.env.DATABASE_URL) {
    throw new Error('DATABASE_URL is not set — database not configured.');
  }
  if (!getSql._pool) {
    getSql._pool = new Pool({
      connectionString: process.env.DATABASE_URL,
      ssl: { rejectUnauthorized: false },
    });
  }
  // Return a tagged-template function backed by the pool
  return async (strings, ...values) => {
    let text = '';
    const params = [];
    for (let i = 0; i < strings.length; i++) {
      text += strings[i];
      if (i < values.length) {
        text += `$${params.length + 1}`;
        params.push(values[i]);
      }
    }
    const { rows } = await getSql._pool.query(text, params);
    return rows;
  };
}

// Auto-run schema migration on first connect (idempotent — uses IF NOT EXISTS)
let migrated = false;
async function ensureSchema() {
  if (migrated) return;
  const fs = require('fs');
  const path = require('path');
  const schemaPath = path.join(__dirname, 'schema.sql');
  const schema = fs.readFileSync(schemaPath, 'utf8');
  const sql = getSql();
  await sql(schema);
  migrated = true;
  console.log('[db] Schema ensured');
}

// ─── Subscribers ────────────────────────────────────────────────────────────

async function addSubscriber(email, firstName, lastName, source, phone) {
  const sql = getSql();
  // Upsert: if email already exists, update name + source but keep subscribed_at
  await sql`
    INSERT INTO subscribers (email, first_name, last_name, source, phone)
    VALUES (${email}, ${firstName || null}, ${lastName || null}, ${source || 'email_form'}, ${phone || null})
    ON CONFLICT (email) DO UPDATE SET
      first_name = COALESCE(subscribers.first_name, EXCLUDED.first_name),
      last_name = COALESCE(subscribers.last_name, EXCLUDED.last_name),
      phone = COALESCE(EXCLUDED.phone, subscribers.phone),
      source = CASE
        WHEN subscribers.source = 'holy_rave' THEN subscribers.source
        ELSE EXCLUDED.source
      END,
      unsubscribed_at = NULL
  `;
}

async function getSubscriberCount() {
  const sql = getSql();
  const [row] = await sql`
    SELECT COUNT(*)::int AS count FROM subscribers WHERE unsubscribed_at IS NULL
  `;
  return row.count;
}

async function getSubscriberByEmail(email) {
  const sql = getSql();
  const rows = await sql`
    SELECT * FROM subscribers WHERE email = ${email} AND unsubscribed_at IS NULL
  `;
  return rows[0] || null;
}

// ─── Events ──────────────────────────────────────────────────────────────────

async function getUpcomingEvents() {
  const sql = getSql();
  const events = await sql`
    SELECT
      e.id, e.slug, e.title, e.location, e.location_detail,
      e.event_date, e.event_time, e.description, e.ticket_limit,
      e.image_url, e.status,
      COALESCE(r.confirmed_count, 0)::int AS tickets_sold
    FROM events e
    LEFT JOIN LATERAL (
      SELECT COUNT(*)::int AS confirmed_count
      FROM holy_rave_registrations hr
      WHERE hr.event_id = e.id AND hr.status = 'confirmed'
    ) r ON true
    WHERE e.status = 'upcoming'
    ORDER BY e.event_date ASC
  `;
  return events.map(e => ({
    ...e,
    event_date: e.event_date ? e.event_date.toISOString().split('T')[0] : e.event_date,
    remaining: Math.max(0, e.ticket_limit - e.tickets_sold),
  }));
}

async function getEventBySlug(slug) {
  const sql = getSql();
  const [event] = await sql`
    SELECT
      e.id, e.slug, e.title, e.location, e.location_detail,
      e.event_date, e.event_time, e.description, e.ticket_limit,
      e.image_url, e.status,
      COALESCE(r.confirmed_count, 0)::int AS tickets_sold
    FROM events e
    LEFT JOIN LATERAL (
      SELECT COUNT(*)::int AS confirmed_count
      FROM holy_rave_registrations hr
      WHERE hr.event_id = e.id AND hr.status = 'confirmed'
    ) r ON true
    WHERE e.slug = ${slug}
  `;
  if (!event) return null;
  return {
    ...event,
    event_date: event.event_date ? event.event_date.toISOString().split('T')[0] : event.event_date,
    remaining: Math.max(0, event.ticket_limit - event.tickets_sold),
  };
}

async function getPastEvents(limit = 10) {
  const sql = getSql();
  const events = await sql`
    SELECT
      e.id, e.slug, e.title, e.location, e.location_detail,
      e.event_date, e.event_time, e.description, e.ticket_limit,
      e.image_url, e.status,
      COALESCE(r.confirmed_count, 0)::int AS tickets_sold
    FROM events e
    LEFT JOIN LATERAL (
      SELECT COUNT(*)::int AS confirmed_count
      FROM holy_rave_registrations hr
      WHERE hr.event_id = e.id AND hr.status = 'confirmed'
    ) r ON true
    WHERE e.status = 'past'
    ORDER BY e.event_date DESC
    LIMIT ${limit}
  `;
  return events.map(e => ({
    ...e,
    event_date: e.event_date ? e.event_date.toISOString().split('T')[0] : e.event_date,
    remaining: 0,
  }));
}

async function updateEventStatus(slug, status) {
  const sql = getSql();
  await sql`
    UPDATE events SET status = ${status} WHERE slug = ${slug}
  `;
}

async function deleteEvent(slug) {
  const sql = getSql();
  await sql`DELETE FROM events WHERE slug = ${slug}`;
}

async function getAllEvents() {
  const sql = getSql();
  const events = await sql`
    SELECT
      e.id, e.slug, e.title, e.location, e.location_detail,
      e.event_date, e.event_time, e.description, e.ticket_limit,
      e.image_url, e.status,
      COALESCE(r.confirmed_count, 0)::int AS tickets_sold
    FROM events e
    LEFT JOIN LATERAL (
      SELECT COUNT(*)::int AS confirmed_count
      FROM holy_rave_registrations hr
      WHERE hr.event_id = e.id AND hr.status = 'confirmed'
    ) r ON true
    ORDER BY e.event_date DESC
  `;
  return events.map(e => ({
    ...e,
    event_date: e.event_date ? e.event_date.toISOString().split('T')[0] : e.event_date,
    remaining: Math.max(0, e.ticket_limit - e.tickets_sold),
  }));
}

async function seedEvent({ slug, title, location, location_detail, event_date, event_time, description, ticket_limit, image_url }) {
  const sql = getSql();
  await sql`
    INSERT INTO events (slug, title, location, location_detail, event_date, event_time, description, ticket_limit, image_url)
    VALUES (${slug}, ${title}, ${location}, ${location_detail || null}, ${event_date}, ${event_time || 'Sunset'}, ${description || null}, ${ticket_limit || 50}, ${image_url || null})
    ON CONFLICT (slug) DO UPDATE SET
      title = EXCLUDED.title,
      location = EXCLUDED.location,
      location_detail = EXCLUDED.location_detail,
      event_date = EXCLUDED.event_date,
      event_time = EXCLUDED.event_time,
      description = EXCLUDED.description,
      ticket_limit = EXCLUDED.ticket_limit,
      image_url = EXCLUDED.image_url,
      status = 'upcoming'
  `;
}

// ─── Holy Rave Registrations ────────────────────────────────────────────────

async function generateConfirmationCode() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let code = '';
  for (let i = 0; i < 4; i++) code += chars[Math.floor(Math.random() * chars.length)];
  return 'HR-' + code;
}

async function createRegistration({ id, firstName, lastName, email, phone, amount, quantity, week, stripeSessionId, eventId }) {
  const sql = getSql();
  const status = amount === 0 ? 'confirmed' : 'pending';
  const qty = Math.max(1, quantity || 1);
  const confirmationCode = await generateConfirmationCode();
  await sql`
    INSERT INTO holy_rave_registrations (id, first_name, last_name, email, phone, amount_cents, quantity, status, week, stripe_session_id, event_id, confirmation_code)
    VALUES (${id}, ${firstName}, ${lastName}, ${email}, ${phone || null}, ${amount}, ${qty}, ${status}, ${week || null}, ${stripeSessionId || null}, ${eventId || null}, ${confirmationCode})
  `;

  // Also add/update subscriber record
  await addSubscriber(email, firstName, lastName, 'holy_rave', phone);
}

async function getRegistrationById(id) {
  const sql = getSql();
  const rows = await sql`
    SELECT * FROM holy_rave_registrations WHERE id = ${id}
  `;
  return rows[0] || null;
}

async function confirmRegistration(id, week) {
  const sql = getSql();

  // If this registration is for an event, use event-based overbooking guard
  const [reg] = await sql`
    SELECT event_id FROM holy_rave_registrations WHERE id = ${id}
  `;

  if (reg && reg.event_id) {
    const [event] = await sql`
      SELECT ticket_limit FROM events WHERE id = ${reg.event_id}
    `;
    if (!event) return false;
    const limit = event.ticket_limit || 50;
    const [row] = await sql`
      SELECT COUNT(*)::int AS used
      FROM holy_rave_registrations
      WHERE event_id = ${reg.event_id} AND status = 'confirmed'
    `;
    if (row.used >= limit) {
      console.warn(`[db] Overbook blocked: ${id} — event ${reg.event_id} at ${row.used}/${limit} tickets`);
      return false;
    }
  } else if (week) {
    // Fallback: weekly overbooking guard (legacy)
    const TICKETS_MAX = 50;
    const [row] = await sql`
      SELECT COUNT(*)::int AS used
      FROM holy_rave_registrations
      WHERE week = ${week} AND status = 'confirmed'
    `;
    if (row.used >= TICKETS_MAX) {
      console.warn(`[db] Overbook blocked: ${id} — week ${week} at ${row.used}/${TICKETS_MAX} tickets`);
      return false;
    }
  }

  await sql`
    UPDATE holy_rave_registrations
    SET status = 'confirmed', confirmed_at = NOW()
    WHERE id = ${id} AND status = 'pending'
  `;
  console.log(`[db] Registration confirmed: ${id}`);
  return true;
}

async function getEventStats(eventId) {
  const sql = getSql();

  // Expire pending registrations older than 1 hour
  await sql`
    UPDATE holy_rave_registrations
    SET status = 'expired'
    WHERE event_id = ${eventId}
      AND status = 'pending'
      AND created_at < NOW() - INTERVAL '1 hour'
  `;

  const [event] = await sql`
    SELECT ticket_limit FROM events WHERE id = ${eventId}
  `;
  if (!event) return null;

  const limit = event.ticket_limit || 50;
  const [row] = await sql`
    SELECT COUNT(*)::int AS used
    FROM holy_rave_registrations
    WHERE event_id = ${eventId} AND status = 'confirmed'
  `;
  return {
    event_id: eventId,
    total: limit,
    sold: row.used,
    remaining: Math.max(0, limit - row.used),
  };
}

async function isDuplicateEmailForEvent(eventId, email) {
  const sql = getSql();
  const rows = await sql`
    SELECT 1 FROM holy_rave_registrations
    WHERE event_id = ${eventId}
      AND email = ${email}
      AND status IN ('confirmed', 'pending')
    LIMIT 1
  `;
  return rows.length > 0;
}

async function getWeekStats(week) {
  const sql = getSql();

  // Expire pending registrations older than 1 hour
  await sql`
    UPDATE holy_rave_registrations
    SET status = 'expired'
    WHERE week = ${week}
      AND status = 'pending'
      AND created_at < NOW() - INTERVAL '1 hour'
  `;

  const TICKETS_MAX = 50;
  const [row] = await sql`
    SELECT COUNT(*)::int AS used
    FROM holy_rave_registrations
    WHERE week = ${week} AND status = 'confirmed'
  `;
  return {
    week,
    total: TICKETS_MAX,
    sold: row.used,
    remaining: Math.max(0, TICKETS_MAX - row.used),
  };
}

// ─── Phone Verification ─────────────────────────────────────────────────────

async function storeVerificationCode(phone, code) {
  const sql = getSql();
  // Invalidate any previous unverified codes for this phone
  await sql`
    UPDATE phone_verifications
    SET verified = TRUE, expires_at = NOW()
    WHERE phone = ${phone} AND verified = FALSE
  `;
  // Insert new code with 5-minute expiry
  await sql`
    INSERT INTO phone_verifications (phone, code, expires_at)
    VALUES (${phone}, ${code}, NOW() + INTERVAL '5 minutes')
  `;
}

async function checkVerificationCode(phone, code) {
  const sql = getSql();
  const [row] = await sql`
    SELECT id, expires_at FROM phone_verifications
    WHERE phone = ${phone}
      AND code = ${code}
      AND verified = FALSE
      AND expires_at > NOW()
    ORDER BY created_at DESC
    LIMIT 1
  `;
  if (!row) return null;
  // Mark as verified
  await sql`
    UPDATE phone_verifications SET verified = TRUE WHERE id = ${row.id}
  `;
  return row;
}

async function getLastVerificationAttempt(phone) {
  const sql = getSql();
  const [row] = await sql`
    SELECT created_at FROM phone_verifications
    WHERE phone = ${phone}
    ORDER BY created_at DESC
    LIMIT 1
  `;
  return row || null;
}

// ─── Event Images (BYTEA storage for uploaded images) ─────────────────────

async function insertEventImage({ id, filename, mimeType, imageData }) {
  const sql = getSql();
  await sql`
    INSERT INTO event_images (id, filename, mime_type, image_data)
    VALUES (${id}, ${filename}, ${mimeType}, ${imageData})
  `;
}

async function getEventImage(id) {
  const sql = getSql();
  const [row] = await sql`
    SELECT id, filename, mime_type, image_data FROM event_images WHERE id = ${id}
  `;
  return row || null;
}

// ─── Social Proof & Velocity ──────────────────────────────────────────────

async function getRecentRegistrations(eventId, limit = 5) {
  const sql = getSql();
  return await sql`
    SELECT first_name, last_name, created_at
    FROM holy_rave_registrations
    WHERE event_id = ${eventId} AND status = 'confirmed'
    ORDER BY created_at DESC
    LIMIT ${limit}
  `;
}

async function getRegistrationVelocity(eventId) {
  const sql = getSql();
  const [row] = await sql`
    SELECT COUNT(*)::int AS count
    FROM holy_rave_registrations
    WHERE event_id = ${eventId}
      AND status = 'confirmed'
      AND created_at >= NOW() - INTERVAL '24 hours'
  `;
  return row.count;
}

async function expireRegistrationBySessionId(stripeSessionId) {
  const sql = getSql();
  const [row] = await sql`
    UPDATE holy_rave_registrations
    SET status = 'expired'
    WHERE stripe_session_id = ${stripeSessionId} AND status = 'pending'
    RETURNING id
  `;
  if (row) console.log(`[db] Registration expired via webhook: ${row.id}`);
  return row || null;
}

async function refundRegistration(id) {
  const sql = getSql();
  const [row] = await sql`
    UPDATE holy_rave_registrations
    SET status = 'refunded'
    WHERE id = ${id} AND status = 'confirmed'
    RETURNING id
  `;
  if (row) console.log(`[db] Registration refunded: ${row.id}`);
  return row || null;
}

async function isDuplicateEmail(week, email) {
  const sql = getSql();
  const rows = await sql`
    SELECT 1 FROM holy_rave_registrations
    WHERE week = ${week}
      AND email = ${email}
      AND status IN ('confirmed', 'pending')
    LIMIT 1
  `;
  return rows.length > 0;
}

// ─── Cross-reference ─────────────────────────────────────────────────────────

async function getSubscribersWithHolyRave() {
  const sql = getSql();
  return await sql`
    SELECT
      s.email,
      s.first_name,
      s.last_name,
      s.phone,
      s.source,
      s.subscribed_at,
      COUNT(DISTINCT hr.id)::int AS holy_rave_visits,
      MAX(hr.created_at) AS last_holy_rave
    FROM subscribers s
    LEFT JOIN holy_rave_registrations hr ON s.email = hr.email AND hr.status = 'confirmed'
    WHERE s.unsubscribed_at IS NULL
    GROUP BY s.email, s.first_name, s.last_name, s.phone, s.source, s.subscribed_at
    ORDER BY s.subscribed_at DESC
  `;
}

module.exports = {
  getSql,
  ensureSchema,
  addSubscriber,
  getSubscriberCount,
  getSubscriberByEmail,
  createRegistration,
  getRegistrationById,
  confirmRegistration,
  getWeekStats,
  getEventStats,
  isDuplicateEmail,
  isDuplicateEmailForEvent,
  getUpcomingEvents,
  getEventBySlug,
  getAllEvents,
  getPastEvents,
  seedEvent,
  updateEventStatus,
  deleteEvent,
  getRecentRegistrations,
  getRegistrationVelocity,
  insertEventImage,
  getEventImage,
  storeVerificationCode,
  checkVerificationCode,
  getLastVerificationAttempt,
  expireRegistrationBySessionId,
  refundRegistration,
  getSubscribersWithHolyRave,
};
