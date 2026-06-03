-- Schema for robertjanmastenbroek.com
-- Run once: psql $DATABASE_URL -f lib/schema.sql

-- Subscribers (email + phone capture for Holy Rave attendees)
CREATE TABLE IF NOT EXISTS subscribers (
  id SERIAL PRIMARY KEY,
  email VARCHAR(255) UNIQUE NOT NULL,
  first_name VARCHAR(255),
  last_name VARCHAR(255),
  phone VARCHAR(30),
  source VARCHAR(50) DEFAULT 'email_form',  -- 'email_form' | 'holy_rave'
  subscribed_at TIMESTAMPTZ DEFAULT NOW(),
  unsubscribed_at TIMESTAMPTZ
);

-- Holy Rave event registrations
CREATE TABLE IF NOT EXISTS holy_rave_registrations (
  id VARCHAR(50) PRIMARY KEY,               -- hr_xxx format
  first_name VARCHAR(100) NOT NULL,
  last_name VARCHAR(100) NOT NULL,
  email VARCHAR(255) NOT NULL,
  phone VARCHAR(30),
  amount_cents INTEGER DEFAULT 0,
  quantity INTEGER DEFAULT 1,                -- number of 2-ticket reservations (default 1 = you + 1 friend)
  status VARCHAR(20) DEFAULT 'pending',      -- 'pending' | 'confirmed' | 'expired'
  week VARCHAR(10),                         -- YYYY-MM-DD (Monday) — NULL for event-based registrations
  stripe_session_id VARCHAR(255),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  confirmed_at TIMESTAMPTZ
);

-- Migration: add quantity column if it doesn't exist (for existing DBs)
DO $$ BEGIN
  ALTER TABLE holy_rave_registrations ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT 1;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- Events table (replaces weekly auto-reset)
CREATE TABLE IF NOT EXISTS events (
  id SERIAL PRIMARY KEY,
  slug VARCHAR(100) UNIQUE NOT NULL,          -- URL slug: 'june-13-2026'
  title VARCHAR(255) NOT NULL,                 -- 'Holy Rave — June 13th 2026'
  location VARCHAR(255) NOT NULL,              -- 'Tenerife South'
  location_detail TEXT,                         -- 'Coordinates emailed 24h before'
  event_date DATE NOT NULL,                     -- The actual date
  event_time VARCHAR(100) DEFAULT 'Sunset',     -- '19:00 – 23:00'
  description TEXT,                             -- Short sell copy
  ticket_limit INTEGER DEFAULT 50,
  image_url VARCHAR(500),                       -- Hero photo path
  status VARCHAR(20) DEFAULT 'upcoming',        -- 'upcoming' | 'past' | 'cancelled'
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Migration: add event_id to holy_rave_registrations
DO $$ BEGIN
  ALTER TABLE holy_rave_registrations ADD COLUMN IF NOT EXISTS event_id INTEGER REFERENCES events(id);
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- Event images (stored as BYTEA for Railway persistence)
CREATE TABLE IF NOT EXISTS event_images (
  id VARCHAR(50) PRIMARY KEY,                -- short unique id
  filename VARCHAR(255) NOT NULL,
  mime_type VARCHAR(50) NOT NULL DEFAULT 'image/jpeg',
  image_data BYTEA NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Migration: add phone column to existing tables
DO $$ BEGIN
  ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS phone VARCHAR(30);
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
DO $$ BEGIN
  ALTER TABLE holy_rave_registrations ADD COLUMN IF NOT EXISTS phone VARCHAR(30);
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_subscribers_email ON subscribers(email);
CREATE INDEX IF NOT EXISTS idx_registrations_email ON holy_rave_registrations(email);
-- Migration: allow week to be NULL for event-based registrations
DO $$ BEGIN
  ALTER TABLE holy_rave_registrations ALTER COLUMN week DROP NOT NULL;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_registrations_week ON holy_rave_registrations(week);
CREATE INDEX IF NOT EXISTS idx_registrations_event_id ON holy_rave_registrations(event_id);
CREATE INDEX IF NOT EXISTS idx_registrations_status ON holy_rave_registrations(status);
CREATE INDEX IF NOT EXISTS idx_registrations_week_status ON holy_rave_registrations(week, status);
CREATE INDEX IF NOT EXISTS idx_registrations_event_status ON holy_rave_registrations(event_id, status);
CREATE INDEX IF NOT EXISTS idx_events_slug ON events(slug);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status, event_date);
