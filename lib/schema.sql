-- Schema for robertjanmastenbroek.com
-- Run once: psql $DATABASE_URL -f lib/schema.sql

-- Subscribers (email capture + Holy Rave attendees)
CREATE TABLE IF NOT EXISTS subscribers (
  id SERIAL PRIMARY KEY,
  email VARCHAR(255) UNIQUE NOT NULL,
  first_name VARCHAR(255),
  last_name VARCHAR(255),
  source VARCHAR(50) DEFAULT 'email_form',  -- 'email_form' | 'holy_rave'
  subscribed_at TIMESTAMPTZ DEFAULT NOW(),
  unsubscribed_at TIMESTAMPTZ
);

-- Holy Rave weekly registrations
CREATE TABLE IF NOT EXISTS holy_rave_registrations (
  id VARCHAR(50) PRIMARY KEY,               -- hr_xxx format
  first_name VARCHAR(100) NOT NULL,
  last_name VARCHAR(100) NOT NULL,
  email VARCHAR(255) NOT NULL,
  amount_cents INTEGER DEFAULT 0,
  status VARCHAR(20) DEFAULT 'pending',      -- 'pending' | 'confirmed' | 'expired'
  week VARCHAR(10) NOT NULL,                 -- YYYY-MM-DD (Monday)
  stripe_session_id VARCHAR(255),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  confirmed_at TIMESTAMPTZ
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_subscribers_email ON subscribers(email);
CREATE INDEX IF NOT EXISTS idx_registrations_email ON holy_rave_registrations(email);
CREATE INDEX IF NOT EXISTS idx_registrations_week ON holy_rave_registrations(week);
CREATE INDEX IF NOT EXISTS idx_registrations_status ON holy_rave_registrations(status);
CREATE INDEX IF NOT EXISTS idx_registrations_week_status ON holy_rave_registrations(week, status);
