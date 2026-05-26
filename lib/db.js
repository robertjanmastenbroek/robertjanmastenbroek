const { Pool } = require('@neondatabase/serverless');

// Lazy-initialize a connection pool. Works with Railway Postgres, Neon, Supabase —
// any standard PostgreSQL. The `sql` tagged template function is constructed from
// the pool so all existing `await sql\`...\`` calls work unchanged.
function getSql() {
  if (!process.env.DATABASE_URL) {
    throw new Error('DATABASE_URL is not set — database not configured.');
  }
  if (!getSql._pool) {
    getSql._pool = new Pool({ connectionString: process.env.DATABASE_URL });
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

async function addSubscriber(email, firstName, lastName, source) {
  const sql = getSql();
  // Upsert: if email already exists, update name + source but keep subscribed_at
  await sql`
    INSERT INTO subscribers (email, first_name, last_name, source)
    VALUES (${email}, ${firstName || null}, ${lastName || null}, ${source || 'email_form'})
    ON CONFLICT (email) DO UPDATE SET
      first_name = COALESCE(subscribers.first_name, EXCLUDED.first_name),
      last_name = COALESCE(subscribers.last_name, EXCLUDED.last_name),
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

// ─── Holy Rave Registrations ────────────────────────────────────────────────

async function createRegistration({ id, firstName, lastName, email, amount, quantity, week, stripeSessionId }) {
  const sql = getSql();
  const status = amount === 0 ? 'confirmed' : 'pending';
  const qty = Math.max(1, quantity || 1);
  await sql`
    INSERT INTO holy_rave_registrations (id, first_name, last_name, email, amount_cents, quantity, status, week, stripe_session_id)
    VALUES (${id}, ${firstName}, ${lastName}, ${email}, ${amount}, ${qty}, ${status}, ${week}, ${stripeSessionId || null})
  `;

  // Also add/update subscriber record
  await addSubscriber(email, firstName, lastName, 'holy_rave');
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
  // 50 reservation slots max (each admits 2 people)
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
  await sql`
    UPDATE holy_rave_registrations
    SET status = 'confirmed', confirmed_at = NOW()
    WHERE id = ${id} AND status = 'pending'
  `;
  console.log(`[db] Registration confirmed: ${id}`);
  return true;
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
      s.source,
      s.subscribed_at,
      COUNT(DISTINCT hr.id)::int AS holy_rave_visits,
      MAX(hr.created_at) AS last_holy_rave
    FROM subscribers s
    LEFT JOIN holy_rave_registrations hr ON s.email = hr.email AND hr.status = 'confirmed'
    WHERE s.unsubscribed_at IS NULL
    GROUP BY s.email, s.first_name, s.last_name, s.source, s.subscribed_at
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
  isDuplicateEmail,
  getSubscribersWithHolyRave,
};
