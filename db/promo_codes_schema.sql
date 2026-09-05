-- promo_codes — the promo table, replacing the hard-coded dict in
-- wingman/subscription_common.py. SECURITY_HARDENING_PLAN.md S1-10, finding L3.
--
-- WHY THIS EXISTS. The three codes (BETAUSER, FREEMONTH, WELCOME10) were literals in a
-- source file, so anybody who could read the repository — every contractor, every fork,
-- anybody who ever saw a screen share — had free access. There was also no way to expire a
-- code, cap how many times it could be handed out, or retire one without a deploy.
--
-- MUST BE RUN AFTER the S1-6 conditional PATCH is deployed. S1-6 makes the per-user "have
-- you already used this?" check a compare-and-swap on users.promo_codes_used; without it,
-- moving codes into a table just relocates a race that grants unlimited free access.
--
-- Run this once in the Supabase SQL editor. Until it exists the code falls back to the
-- built-in table and logs one warning per process, so nothing breaks — see
-- wingman/subscription_common.load_promo_codes(). Safe to run more than once.

create table if not exists promo_codes (
    code             text        primary key,
    -- 'grant'    — redeemed against the user's own row, server-side, no Stripe, no card.
    --              Sets `status` and extends access by `grant_days`.
    -- 'checkout' — a discount that only means anything once Stripe is in the picture.
    -- The two are NOT interchangeable and neither can be redeemed through the other's
    -- endpoint; see promo_kind() and handle_redeem_promo.
    kind             text        not null default 'checkout',
    status           text,                  -- grant only: the subscription_status to set
    grant_days       integer,               -- grant only: days of access to add
    discount_months  integer,               -- checkout only
    discount_percent integer,               -- checkout only
    description      text,                  -- shown to the student on redemption
    is_active        boolean     not null default true,
    -- NULL means "never expires" / "unlimited". Both are deliberately nullable rather than
    -- sentinel-valued: a 0 or a 1970 date would read as a live constraint to anyone
    -- eyeballing the table.
    expires_at       timestamptz,
    max_redemptions  integer,
    redemption_count integer     not null default 0,
    created_at       timestamptz not null default now()
);

-- No policies, deliberately: every read and write goes through the service key from the
-- server. A promo table readable with the anon key is the original problem with extra
-- steps — anyone could enumerate the live codes from the browser.
alter table promo_codes enable row level security;

-- Seed the three codes that were hard-coded, so redemption behaviour is unchanged the
-- moment this runs. ON CONFLICT DO NOTHING, so re-running never overwrites an edit made in
-- the dashboard (an expiry set, a code retired).
insert into promo_codes (code, kind, status, grant_days, discount_months, discount_percent,
                         description)
values
    ('BETAUSER',  'grant',    'beta', 7,    null, null, 'Beta access for 1 more week'),
    ('FREEMONTH', 'checkout', null,   null, 1,    null, '1 free month'),
    ('WELCOME10', 'checkout', null,   null, null, 10,   '10% off first month')
on conflict (code) do nothing;


-- ---------------------------------------------------------------------------
-- ALTER block — for a table that already exists in an older shape. Every statement is
-- idempotent, so this file can be pasted whole whatever state the database is in.
-- ---------------------------------------------------------------------------
alter table promo_codes add column if not exists kind             text    not null default 'checkout';
alter table promo_codes add column if not exists status           text;
alter table promo_codes add column if not exists grant_days       integer;
alter table promo_codes add column if not exists discount_months  integer;
alter table promo_codes add column if not exists discount_percent integer;
alter table promo_codes add column if not exists description      text;
alter table promo_codes add column if not exists is_active        boolean not null default true;
alter table promo_codes add column if not exists expires_at       timestamptz;
alter table promo_codes add column if not exists max_redemptions  integer;
alter table promo_codes add column if not exists redemption_count integer not null default 0;
alter table promo_codes add column if not exists created_at       timestamptz not null default now();
alter table promo_codes enable row level security;

create index if not exists promo_codes_active_idx on promo_codes (is_active);
