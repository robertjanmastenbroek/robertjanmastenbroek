const { createClient } = require('@supabase/supabase-js');

// New Supabase API key naming (2026):
//   SUPABASE_PUBLISHABLE_KEY  — client-side (replaces anon key, prefix: sbp_)
//   SUPABASE_SECRET_KEY       — server-side (replaces service_role key, prefix: sbs_)
// Falls back to legacy SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY for existing setups.
const supabaseUrl    = process.env.SUPABASE_URL || '';
const pubKey         = process.env.SUPABASE_PUBLISHABLE_KEY || process.env.SUPABASE_ANON_KEY || '';
const secretKey      = process.env.SUPABASE_SECRET_KEY || process.env.SUPABASE_SERVICE_ROLE_KEY || '';

// Admin client — secret key bypasses RLS for server-side operations
function getAdminClient() {
  if (!supabaseUrl || !secretKey) return null;
  return createClient(supabaseUrl, secretKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
}

// Public client — publishable key, respects RLS. Used for user-facing auth flows.
function getPublicClient() {
  if (!supabaseUrl || !pubKey) return null;
  return createClient(supabaseUrl, pubKey, {
    auth: { autoRefreshToken: true, persistSession: true, detectSessionInUrl: false },
  });
}

// Validate a session token from a cookie/header
async function getUserFromToken(accessToken) {
  if (!accessToken || !supabaseUrl || !pubKey) return null;
  const supabase = createClient(supabaseUrl, pubKey);
  const { data, error } = await supabase.auth.getUser(accessToken);
  if (error || !data?.user) return null;
  return data.user;
}

module.exports = { getAdminClient, getPublicClient, getUserFromToken };
