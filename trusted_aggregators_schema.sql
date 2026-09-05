-- trusted_aggregators — the operator-controlled trusted-domain allowlist (P5).
--
-- ONE table shared by BOTH features (see docs/plans/DEADLINE_AND_TASK_PLAN.md §5):
--   * DEADLINES use the READ side only: the escalation loop's 4th rung keeps an off-domain
--     date only if the page's domain is trusted here, and forces it estimated=true.
--   * TASKS (P6) use the full park-and-approve flywheel: discovery may surface any domain;
--     approved -> shown, not-yet-approved -> parked/withheld, blocked -> dropped.
--
-- STATUS SEMANTICS — matches aggregators_common.AggregatorPolicy.classify():
--   'trusted'  -> tier 2: shown to students, may back logistics (never eligibility).
--   'blocked'  -> dropped everywhere.
--   NO ROW     -> pending: parked, withheld from students, shown to the operator as the
--                 approval queue. There is deliberately no 'pending' row — a domain is
--                 pending PRECISELY when it has no row here, so the flywheel needs no
--                 backfill and an unknown domain is safe-by-default (withheld).
--
-- DEGRADE-NOT-BREAK: if this table is absent, aggregators_common returns an empty policy
-- with present=False — every domain classifies as pending (nothing off-domain ships), the
-- deadline rung-4 filter keeps nothing, and the console Sources tab shows the setup step.
--
-- ALTER-THEN-CREATE, like mailing_list_schema.sql / email_schema.sql: `create table if not
-- exists` is a no-op against a table that already exists in an older shape, and PostgREST
-- 400s an entire write on one unknown key — so a single missing column means EVERY write
-- fails and the feature reads as "found nothing" rather than "every write failed". If you
-- add a column to the CREATE, you MUST add it to the ALTER block too.

create table if not exists trusted_aggregators (
    domain      text primary key,
    status      text not null default 'trusted' check (status in ('trusted', 'blocked')),
    label       text,
    notes       text,
    added_by    text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

alter table trusted_aggregators add column if not exists status     text not null default 'trusted';
alter table trusted_aggregators add column if not exists label      text;
alter table trusted_aggregators add column if not exists notes      text;
alter table trusted_aggregators add column if not exists added_by   text;
alter table trusted_aggregators add column if not exists created_at timestamptz not null default now();
alter table trusted_aggregators add column if not exists updated_at timestamptz not null default now();

-- Seed lumiere-education.com as trusted on day one (docs/plans/DEADLINE_AND_TASK_PLAN.md decision,
-- 2026-08-25) so its tasks — and THINK Scholars', which lumiere describes — show from the
-- first pass. EVERY other discovered domain still starts pending and goes through the
-- park-and-approve queue. `do nothing` on conflict so re-running this file never clobbers a
-- later operator edit (e.g. if they blocked it).
insert into trusted_aggregators (domain, status, label, added_by)
values ('lumiere-education.com', 'trusted', 'Lumiere Education', 'seed')
on conflict (domain) do nothing;
