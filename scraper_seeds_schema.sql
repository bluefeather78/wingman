-- scraper_seeds — the scrape_opportunities.py search angles, moved out of source so the
-- admin console can add/edit/enable/disable/delete them and track which ones earn their keep.
--
-- Run this once in the Supabase SQL editor, then populate it with:
--     python migrate_seeds_to_supabase.py --dry-run
--     python migrate_seeds_to_supabase.py
--
-- RLS: enabled with NO policies, matching the `users` table. That means the anon key gets
-- zero access and only server.py's service-role calls can read or write these rows. Seeds
-- drive paid API spend, so they must never be reachable from browser-side code the way the
-- public read-only `opportunities` table is.

create table if not exists scraper_seeds (
    id           bigint generated always as identity primary key,
    mode         text    not null,                   -- 'national' | 'seattle'
    category     text    not null,                   -- drives fallback `type` + provenance
    angle        text    not null,                   -- interpolated into the scraper prompt
    is_enabled   boolean not null default true,
    sort_order   integer,

    -- Lifetime running yield totals, PATCHed after each time this seed runs.
    -- seeds_common.record_seed_result() maintains these.
    total_runs   integer default 0,
    total_found  integer default 0,                  -- raw candidates the model returned
    total_added  integer default 0,                  -- rows actually inserted after dedup
    total_dupes  integer default 0,                  -- candidates already in the catalog
    total_cost   numeric default 0,                  -- lifetime USD spent on this angle
    last_run_at  timestamptz,
    created_at   timestamptz default now()
);

-- The scraper reads enabled seeds for one mode ordered by sort_order on every run.
create index if not exists scraper_seeds_mode_order_idx
    on scraper_seeds (mode, sort_order, id);

alter table scraper_seeds enable row level security;
