-- db/two_tier_free_migration.sql — retire the 7-day trial, move everyone to the permanent
-- Free tier (TWO_TIER_AI_PLAN.md §2b).
--
-- Paste this whole file into the Supabase SQL editor and run it. PostgREST (all the app can
-- reach) has no DDL, so this cannot run from the app — the same one-time-manual-step pattern
-- as db/subscription_schema.sql. Safe to run more than once (idempotent).
--
-- This LOOSENS access: it turns every trial account (including the dateless pre-migration
-- rows and expired trials) into a Free account, which under the two-tier model has full app
-- access. Nobody who had access loses it, so it is safe to run before the client ships.
--
-- active / beta / canceled / past_due rows are left untouched — subscription_state() already
-- reads them correctly, and ai_tier() derives Unlimited-vs-Free from the live paid period.

-- New accounts default to the Free tier (create_user writes 'free' explicitly too).
ALTER TABLE users ALTER COLUMN subscription_status SET DEFAULT 'free';

-- Migrate existing trial (and any NULL) rows to Free.
UPDATE users
   SET subscription_status = 'free'
 WHERE subscription_status = 'trial'
    OR subscription_status IS NULL;

-- trial_ends_at is intentionally left as-is on migrated rows: it is legacy data that
-- subscription_state() now ignores (a Free account is never gated on it), and clearing it
-- buys nothing. New accounts write it NULL.

-- The subscription_status index from db/subscription_schema.sql still applies.
