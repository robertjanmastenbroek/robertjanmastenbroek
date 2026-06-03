-- Row Level Security policies for Holy Rave
-- Run: psql $DATABASE_URL -f lib/rls.sql
--
-- IMPORTANT: Our server connects as 'postgres' superuser (via pg connection string),
-- which bypasses RLS entirely. These policies are a defense-in-depth layer that
-- blocks direct Supabase REST API access. All data access still goes through our
-- server-side API endpoints.

-- Enable RLS on all tables
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscribers ENABLE ROW LEVEL SECURITY;
ALTER TABLE holy_rave_registrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_images ENABLE ROW LEVEL SECURITY;
ALTER TABLE phone_verifications ENABLE ROW LEVEL SECURITY;

-- ── Events ───────────────────────────────────────────────────────────────
-- Anyone can view events (public listing pages)
-- Only server (service_role) can create/update/delete
DROP POLICY IF EXISTS "events_select" ON events;
CREATE POLICY "events_select" ON events FOR SELECT USING (true);

DROP POLICY IF EXISTS "events_insert" ON events;
CREATE POLICY "events_insert" ON events FOR INSERT WITH CHECK (auth.role() = 'service_role');

DROP POLICY IF EXISTS "events_update" ON events;
CREATE POLICY "events_update" ON events FOR UPDATE USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "events_delete" ON events;
CREATE POLICY "events_delete" ON events FOR DELETE USING (auth.role() = 'service_role');

-- ── Subscribers ──────────────────────────────────────────────────────────
-- Anyone can insert (the subscribe form is public)
-- Only server can select and update (admin panel + email sync)
DROP POLICY IF EXISTS "subscribers_insert" ON subscribers;
CREATE POLICY "subscribers_insert" ON subscribers FOR INSERT WITH CHECK (true);

DROP POLICY IF EXISTS "subscribers_select" ON subscribers;
CREATE POLICY "subscribers_select" ON subscribers FOR SELECT USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "subscribers_update" ON subscribers;
CREATE POLICY "subscribers_update" ON subscribers FOR UPDATE USING (auth.role() = 'service_role');

-- ── Holy Rave Registrations ──────────────────────────────────────────────
-- All operations go through our server API only
DROP POLICY IF EXISTS "registrations_insert" ON holy_rave_registrations;
CREATE POLICY "registrations_insert" ON holy_rave_registrations FOR INSERT WITH CHECK (auth.role() = 'service_role');

DROP POLICY IF EXISTS "registrations_select" ON holy_rave_registrations;
CREATE POLICY "registrations_select" ON holy_rave_registrations FOR SELECT USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "registrations_update" ON holy_rave_registrations;
CREATE POLICY "registrations_update" ON holy_rave_registrations FOR UPDATE USING (auth.role() = 'service_role');

-- ── Event Images ─────────────────────────────────────────────────────────
-- Anyone can view images (they're served on public pages)
-- Only server can upload new images
DROP POLICY IF EXISTS "event_images_select" ON event_images;
CREATE POLICY "event_images_select" ON event_images FOR SELECT USING (true);

DROP POLICY IF EXISTS "event_images_insert" ON event_images;
CREATE POLICY "event_images_insert" ON event_images FOR INSERT WITH CHECK (auth.role() = 'service_role');

-- ── Phone Verifications ──────────────────────────────────────────────────
-- All operations through server API only
DROP POLICY IF EXISTS "phone_verifications_insert" ON phone_verifications;
CREATE POLICY "phone_verifications_insert" ON phone_verifications FOR INSERT WITH CHECK (auth.role() = 'service_role');

DROP POLICY IF EXISTS "phone_verifications_select" ON phone_verifications;
CREATE POLICY "phone_verifications_select" ON phone_verifications FOR SELECT USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "phone_verifications_update" ON phone_verifications;
CREATE POLICY "phone_verifications_update" ON phone_verifications FOR UPDATE USING (auth.role() = 'service_role');
