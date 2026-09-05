-- ============================================================================
-- Everything Phase S1 needs, in one paste. See SECURITY_HARDENING_PLAN.md 0b.
--
-- STRICT ASCII ONLY. A previous version of this file carried a section sign and
-- em-dashes in these comments; a mangled multibyte character eats the bytes
-- around it and shifts everything after, which corrupted the first ALTER on
-- paste ("token_vfault 0;"). Nothing in this file may be non-ASCII. If you edit
-- it, check with:  LC_ALL=C grep -n '[^ -~]' db/RUN_ME_S1.sql
--
-- This is a CONVENIENCE COPY, not a new source of record. Each block is the
-- executable content of the file named above it; those files stay the place to
-- read WHY each statement exists, and the place to edit.
--
-- Paste the whole thing into the Supabase SQL editor and run it once. Every
-- statement is idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING), so running
-- it twice does nothing the second time.
--
-- AFTER RUNNING, CONFIRM IN THE DASHBOARD (Table Editor > table > RLS):
-- conversations, agent_runs and deadline_check_log show "RLS enabled". Those
-- three may already exist, created by hand from a code comment with no RLS
-- line. The ALTERs below turn it on; the confirmation is what actually closes
-- the finding. The last query in this file checks all five for you.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- db/auth_schema.sql - S1-2, refresh-token rotation with reuse detection.
-- Until this runs, rotation does not engage and auth behaves as it did before.
-- ---------------------------------------------------------------------------
alter table users add column if not exists token_version integer not null default 0;
alter table users add column if not exists refresh_jtis text[] not null default '{}';


-- ---------------------------------------------------------------------------
-- db/promo_codes_schema.sql - S1-10, promo codes out of the source.
-- Until this runs, the built-in BETAUSER/FREEMONTH/WELCOME10 still work.
-- ---------------------------------------------------------------------------
create table if not exists promo_codes (
    code             text        primary key,
    kind             text        not null default 'checkout',
    status           text,
    grant_days       integer,
    discount_months  integer,
    discount_percent integer,
    description      text,
    is_active        boolean     not null default true,
    expires_at       timestamptz,
    max_redemptions  integer,
    redemption_count integer     not null default 0,
    created_at       timestamptz not null default now()
);

alter table promo_codes add column if not exists kind text not null default 'checkout';
alter table promo_codes add column if not exists status text;
alter table promo_codes add column if not exists grant_days integer;
alter table promo_codes add column if not exists discount_months integer;
alter table promo_codes add column if not exists discount_percent integer;
alter table promo_codes add column if not exists description text;
alter table promo_codes add column if not exists is_active boolean not null default true;
alter table promo_codes add column if not exists expires_at timestamptz;
alter table promo_codes add column if not exists max_redemptions integer;
alter table promo_codes add column if not exists redemption_count integer not null default 0;
alter table promo_codes add column if not exists created_at timestamptz not null default now();

alter table promo_codes enable row level security;
create index if not exists promo_codes_active_idx on promo_codes (is_active);

insert into promo_codes (code, kind, status, grant_days, discount_months,
                         discount_percent, description)
values
    ('BETAUSER',  'grant',    'beta', 7,    null, null, 'Beta access for 1 more week'),
    ('FREEMONTH', 'checkout', null,   null, 1,    null, '1 free month'),
    ('WELCOME10', 'checkout', null,   null, null, 10,   '10% off first month')
on conflict (code) do nothing;


-- ---------------------------------------------------------------------------
-- db/conversations_schema.sql - S1-9. THE ONE THAT MATTERS MOST. This table
-- holds a minor describing themselves in their own words, and its only
-- definition was a comment in app/core.py with no RLS line. The DROP COLUMN is
-- what makes "we no longer store the address" true of the rows already
-- written, not just the next ones.
-- ---------------------------------------------------------------------------
create table if not exists conversations (
    id            bigint generated always as identity primary key,
    created_at    timestamptz not null default now(),
    userid        text,
    mode          text,
    system_prompt text,
    user_content  text
);

alter table conversations add column if not exists userid text;
alter table conversations add column if not exists mode text;
alter table conversations add column if not exists system_prompt text;
alter table conversations add column if not exists user_content text;

alter table conversations enable row level security;
create index if not exists conversations_created_at_idx on conversations (created_at desc);

alter table conversations drop column if exists client_ip;


-- ---------------------------------------------------------------------------
-- db/agent_runs_schema.sql - S1-9. No schema file existed; its only definition
-- was a docstring in agents/check_deadlines.py, also with no RLS line.
-- ---------------------------------------------------------------------------
create table if not exists agent_runs (
    id                  bigint generated always as identity primary key,
    agent               text not null,
    mode                text,
    started_at          timestamptz not null,
    finished_at         timestamptz,
    items_processed     integer default 0,
    items_added         integer default 0,
    items_updated       integer default 0,
    items_deleted       integer default 0,
    emails_subscribed   integer default 0,
    errors              integer default 0,
    cost_usd            numeric,
    total_web_searches  integer default 0,
    silent_search_count integer default 0,
    notes               text
);

alter table agent_runs add column if not exists mode text;
alter table agent_runs add column if not exists finished_at timestamptz;
alter table agent_runs add column if not exists items_processed integer default 0;
alter table agent_runs add column if not exists items_added integer default 0;
alter table agent_runs add column if not exists items_updated integer default 0;
alter table agent_runs add column if not exists items_deleted integer default 0;
alter table agent_runs add column if not exists emails_subscribed integer default 0;
alter table agent_runs add column if not exists errors integer default 0;
alter table agent_runs add column if not exists cost_usd numeric;
alter table agent_runs add column if not exists total_web_searches integer default 0;
alter table agent_runs add column if not exists silent_search_count integer default 0;
alter table agent_runs add column if not exists notes text;

alter table agent_runs enable row level security;
create index if not exists agent_runs_agent_started_idx on agent_runs (agent, started_at desc);


-- ---------------------------------------------------------------------------
-- db/deadline_check_log_schema.sql - S1-9. The file existed; the RLS line did
-- not.
-- ---------------------------------------------------------------------------
create table if not exists deadline_check_log (
    id             bigint generated always as identity primary key,
    opportunity_id text not null,
    checked_at     timestamptz not null,
    source         text not null,
    status         text,
    web_searches   integer,
    cost_usd       numeric,
    was_estimated  boolean,
    notes          text
);

create index if not exists deadline_check_log_opp_time
    on deadline_check_log (opportunity_id, checked_at desc);
create index if not exists deadline_check_log_source
    on deadline_check_log (source);

alter table deadline_check_log enable row level security;


-- ============================================================================
-- VERIFY. Run this after the above; all five rows should read rls = true.
--
-- Schema-qualified to 'public' on purpose. An earlier version of this query
-- read pg_class without joining pg_namespace, so it matched relations of the
-- same name in EVERY schema and returned "users" twice: once for public.users
-- (this app's) and once for auth.users (Supabase's own). Both happened to be
-- true, so the answer was right, but a verification query that cannot tell you
-- WHICH table it just cleared is not a verification query.
-- ============================================================================
select n.nspname            as schema,
       c.relname            as table_name,
       c.relrowsecurity     as rls
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname in ('conversations', 'agent_runs', 'deadline_check_log',
                    'promo_codes', 'users')
order by c.relname;
