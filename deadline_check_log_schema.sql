-- Create deadline_check_log table for runtime deadline check audit trail
-- Run this in Supabase SQL editor before deadline checks start logging

CREATE TABLE IF NOT EXISTS deadline_check_log (
    id                  bigint generated always as identity primary key,
    opportunity_id      text not null,
    checked_at          timestamptz not null,
    source              text not null,  -- 'cached', 'mock', 'fresh, real search', 'fresh, silent search', 'stale-fallback'
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
