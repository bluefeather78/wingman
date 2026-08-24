-- Create deadline_check_log table for runtime deadline check audit trail
-- Run this in Supabase SQL editor before deadline checks start logging

CREATE TABLE IF NOT EXISTS deadline_check_log (
    id                  bigint generated always as identity primary key,
    opportunity_id      text not null,
    checked_at          timestamptz not null,
    -- 'cached'              served from the 7-day cache, no API call
    -- 'mock'                no ANTHROPIC_API_KEY; fabricated and deliberately not stored
    -- 'fresh, real search'  verified and written to the opportunities row
    -- 'unverified-fallback' phase 1 never searched; nothing written, row left due
    -- 'unparsed-fallback'   searched, but phase 2's JSON was unreadable; nothing written
    -- 'kept-existing'       searched, found no dates, row kept the ones it had
    -- 'stale-fallback'      the check raised; served whatever was cached, however old
    source              text not null,
    status              text,            -- 'running', 'not_running', 'unknown', or null for cached/fallback checks
    web_searches        integer,         -- number of web searches performed (null for cached/mock)
    cost_usd            numeric,         -- cost of the API call (null for cached/mock)
    was_estimated       boolean,         -- whether any deadline data was estimated
    notes               text             -- error messages, mock reason, etc.
);

-- Index on (opportunity_id, checked_at desc) for querying checks per opportunity
CREATE INDEX IF NOT EXISTS deadline_check_log_opp_time
    ON deadline_check_log(opportunity_id, checked_at desc);

-- Index on (source) for filtering by check type (cached vs fresh vs mock, etc.)
CREATE INDEX IF NOT EXISTS deadline_check_log_source
    ON deadline_check_log(source);
