# User Metrics Dashboard — planning doc

Written 2026-08-22. **Phases 0–2 built 2026-08-23**; see §7 for what landed and what
did not. The design rationale below is the record of *why* it is shaped this way — the
implementation notes that matter day to day are in [CLAUDE.md](../../CLAUDE.md) under
"User metrics — the Metrics view".

A fifth admin-console view answering *"is anyone actually using this, and are they
converting?"* — the question the existing **Cost per user** tab deliberately does not
answer. That tab measures dollars out; this one measures usage and revenue in. They share
the `users` roster and cross-link, but they must not merge: one is a spend ledger, the
other is a product funnel, and collapsing them produces a page that answers neither.

---

## 1. The blocker, stated up front

**This repo has no event log.** Everything it knows about a user is either

- **current state** — the `users` row and its `data` jsonb (profile, tracker, saved state), or
- **cost rollups** — `user_costs`, at a `(userid, day, surface, feature, model)` grain.

Nothing anywhere records *"user X did thing Y at time T"*. Three consequences drive the
whole phasing below:

1. **DAU/WAU/retention cannot be computed today.** A sign-in writes nothing
   (`handle_login` only calls `ensure_trial_started`, which is a no-op after the first
   time). `/api/data/save` PATCHes `data` and nothing else.
2. **`users.updated_at` is a trap.** It is declared `default now()` in
   `../../scripts/one-off/migrate_users_to_supabase.py` with **no trigger**, and `update_user_data()` never
   writes it. It therefore equals `created_at` for practically every row. Do **not** build
   a "last active" metric on it — it will look plausible and be wrong. (Contrast
   `opportunities.updated_at`, which *is* explicitly stamped by `server.py`. Same column
   name, opposite meaning.)
3. **State metrics are only ever "as of now".** The `data` jsonb holds one profile, not a
   history of it. "How many users had a meaningful profile on 2026-08-01" is
   **unrecoverable** — there is no version of that fact stored anywhere. The only fix is
   to start snapshotting daily; every historical state chart therefore begins at the day
   Phase 2 ships, and the UI must say so rather than drawing a line back to zero that
   reads as "nobody had a profile before then".

The nearest existing proxies for activity, and why each is insufficient on its own:

| Source | Covers | Why it isn't activity |
|---|---|---|
| `user_costs.day` / `first_at` / `last_at` | billed AI calls | A student who browsed their tracker every day all week costs $0 and reads as inactive. It measures *AI usage*, not *usage*. Also excludes mock mode, so with no API key the whole signal vanishes. |
| `conversations.created_at` | profile-chat Q&A turns only | One feature, and `userid` is nullable (the table predates attribution). |
| `mailing_list_subscriptions.attempted_at` | one button | Narrow by construction. |
| `deadline_check_log` | on-demand deadline checks | Cached 7 days, so a repeat visit leaves no row. |
| `users.created_at` | signups | **Exact and usable today** — the one real time series we already have. |

---

## 2. Metric catalogue

`OK` = computable today, no migration. `P1` = needs Phase 1 (activity table).
`P2` = needs Phase 2 (daily snapshots) for the *trend*; the *current value* is `OK`.

### 2.1 Acquisition

| Metric | Definition | Source |
|---|---|---|
| Total accounts | `count(users)` | OK — `users` |
| Signups per day | bucket `created_at` on the UTC day | OK — `users.created_at` |
| Signup method | `google_id is not null` vs password | OK — `users.google_id` |
| Under-18 share | `is_adult = false` (`parental_consent` should then be true) | OK |
| Consent gap | `terms_accepted_at is null` — accounts predating consent capture | OK |
| Stale terms | `terms_version <> TERMS_VERSION` — who needs re-consent | OK |

### 2.2 Activation funnel — the centrepiece

Strictly ordered, each stage a subset of the one above it. Percentages are shown both
*of all accounts* and *step-over-step*, because the step-over-step number is what names
the leak.

> **As-drafted list — the shipped chain is shorter.** Stages 2, 6 and 11 below turned out
> not to be subsets of their neighbours, which under a cumulative funnel would have scored
> people *below* where they actually got. They became side metrics instead. `FUNNEL_STAGES`
> in `server.py` is the built list; §7 records each change and why.

| # | Stage | Exact test | |
|---|---|---|---|
| 1 | Signed up | row exists | OK |
| 2 | Returned after signup day | any activity day > `date(created_at)` | P1 |
| 3 | Has any saved data | `data <> '{}'` | OK |
| 4 | Has a profile | `data->'student-profile'->>'synthesized'` non-empty | OK |
| 5 | **Meaningful profile** | that text has at least **20** words | OK |
| 6 | Rich profile | at least 3 `chatRounds`, or at least 100 words | OK |
| 7 | Ran a search | a `user_costs` row with feature `ranking`; or `student-profile.filterValues` present | OK (proxy) |
| 8 | **At least 1 tracked opportunity** | any bucket in `hs-tracker-data` has an item whose id is **not** truthy in `hs-tracker-saved` | OK |
| 9 | At least 3 tracked | same, count >= 3 | OK |
| 10 | Working the list | any tracked item has a checked `actionItems` entry | OK |
| 11 | Deep engagement | Google Calendar connected, or at least one `mailing_list_subscriptions` row | OK |

**Stage 5's bar is `PROFILE_SUFFICIENT_LENGTH = 20`, imported, not re-typed.** The client
already gates its own UI on that constant ([script.js:1549](script.js:1549)); a second
threshold in the dashboard meaning "meaningful" would let the admin page and the app
disagree about the same student. If the definition of meaningful should change, change it
in one place.

**Stage 8 must exclude saved-for-later.** `trackerSavedState[id] === true` means the
student explicitly parked it — [script.js:4369](script.js:4369) already refuses to count
those as "actively tracked", and counting them here would inflate the most important
number on the page.

### 2.3 Engagement & retention

| Metric | Definition | |
|---|---|---|
| DAU / WAU / MAU | distinct userids with at least one activity day in the trailing 1 / 7 / 30 days | P1 |
| Stickiness | DAU / MAU | P1 |
| Active days in first 7 | per signup cohort — the best early predictor at this scale | P1 |
| D1 / D7 / D30 retention | cohort by `date(created_at)`, active on/after day N | P1 |
| AI-active users | distinct userids in `user_costs` in the window | OK (partial — billed calls only) |
| Profile chat rounds | `student-profile.chatRounds`, summed and distributed | OK |

### 2.4 Monetization

| Metric | Definition | |
|---|---|---|
| Plan mix | count by `subscription_status` (`trial`/`active`/`beta`/`canceled`/`past_due`) | OK |
| **Trial to paid conversion** | of accounts whose trial has *ended*, the share now `active` or `canceled`-but-paid | OK / P2 for the trend |
| Time to convert | `subscription_end_at` bookkeeping vs `created_at` | OK (approximate) |
| Trials ending within 48h | `status='trial' and trial_ends_at` within 2 days | OK |
| At-risk | trial ending within 48h **and** funnel stage below 5 — the email list | OK |
| Churn | `status='canceled'`, and how many still have access | OK |
| Promo redemptions | unnest `promo_codes_used`, count per code, split grant vs checkout | OK |
| MRR | `count(status='active') * $9.99` | OK |
| Gross margin per user | `$9.99 - user_costs.cost_usd` — **already computed** as `margin_usd` | OK |
| Cost per activated user | window AI spend / users at stage 5+ | OK |

**Conversion's denominator is accounts whose trial has *expired*, never all accounts.**
With a 7-day trial and 9 accounts, dividing by everyone puts every signup from the last
week in the denominator as a failure. The tile must show `n/d` alongside the
percentage so a 1-of-2 reads as 1-of-2.

**`has_access` is not a status.** Derive every gate from `subscription_state(record)`
([server.py:149](../../server.py:149)) rather than re-reading the columns, for the same reason
the client paywall does — two implementations of "may this account use the app" will
eventually disagree.

### 2.5 The honest caveat about N

There are **9 accounts** (CLAUDE.md's "15" was stale). At that size a percentage is a decoration on a fraction, and a
retention curve is noise. So the dashboard's first job is to **name accounts, not plot
rates** — the same call the Cost per user tab already made when it seeded its table from
the roster instead of from the cost rows. Every funnel bar is clickable and lists the
actual people who did and didn't clear that stage. Rates stay on the page, always with
their raw `n/d`, and become the primary reading later on their own.

---

## 3. Data plan

### Phase 0 — ship the whole state half with no migration

`GET /api/agents/metrics?days=30` reads the `users` table once (select the scalar columns
plus `data`), computes stages 1 and 3–11 in Python, and returns funnel, plan mix,
conversion, signups-per-day and the per-user roster. Folds in `user_costs` for stage 7 and
the margin columns.

This is genuinely most of the dashboard, and it is available today. Do this first.

- Paginate the read past PostgREST's 1000-row cap — the existing user-costs read uses a
  flat `limit=5000`, which is fine at 9 rows and silently wrong later.
- `data` jsonb is the heavy part of the row. At current size fetching it whole is fine;
  past a few thousand accounts, move the funnel to a Postgres view.

### Phase 1 — `user_activity`, the one new write path

```sql
-- ../../db/user_activity_schema.sql  (one-time manual DDL, same pattern as ../../db/user_costs_schema.sql)
create table if not exists user_activity (
    userid      text not null,
    day         date not null,
    hits        integer not null default 0,
    first_at    timestamptz,
    last_at     timestamptz,
    surfaces    jsonb not null default '{}'::jsonb,  -- {"login":3,"data_save":11,...}
    primary key (userid, day)
);
create index if not exists user_activity_day_idx on user_activity (day desc);
alter table user_activity enable row level security;  -- no policies, service-role only
```

**Daily rollup, not an event log** — the same decision `record_interactive_cost()` made,
for the same reason: a row per request grows without bound for data only ever read in
aggregate, and this table would take *every* authenticated request, not just billed ones.

`touch_user_activity(userid, surface)` fires-and-forgets on a background thread (copy
`record_user_cost_async`) from the handlers that carry a userid: `/api/login`,
`/api/data/save`, `/api/data/load`, `/api/messages*`, `/api/subscription/status`,
`/api/opportunities/<id>/deadline`, `/api/extract-from-resume`, the subscribe route.

Three things to get right:

- **It must never break a request.** Same swallow-and-log posture as `log_conversation()`.
- **One upsert per userid per day, not per request.** Keep an in-process
  `set[(userid, day)]` and skip the round trip once seen — otherwise an app that polls
  `/api/subscription/status` turns into a write per poll.
- **`/api/data/load` counts, `/api/data/save` counts double.** A load is "opened the app";
  a save is "changed something". Storing the per-surface counts in `surfaces` keeps that
  distinction available without a second table.

Unlocks stage 2, DAU/WAU/MAU, retention. **History starts the day it ships** — no
backfill is possible, and the chart must say so.

### Phase 2 — `user_metrics_daily` snapshots

One row per UTC day holding the funnel counts and plan mix computed that day. Written by
the metrics endpoint itself on first call of a new day (cheap, no scheduler needed), or by
a tiny script. This is the only way state metrics ever get a trend line. Ship it early even
if nothing reads it for weeks — every day it isn't running is a day permanently missing.

### Phase 3 — event-level funnel timing

*How long* from signup to first tracked opportunity. Needs real events. Not worth it at 15
accounts; noted so it isn't accidentally designed out.

---

## 4. UI design

### 4.1 Placement

A fifth `.vtab` in `#viewTabs` ([admin_console.html:367](admin_console.html:367)), first
in the strip and the **default view**:

```
[ Metrics ] [ Agents ] [ Review queue 12 ] [ Mailing lists 4 ] [ Cost per user ]
```

Tradeoff, stated plainly: this demotes Agents from the landing view, and the console is
today an agent-ops tool. The counter-argument is that agent runs are things you go looking
for, whereas "how's the product doing" is what you want on arrival. If that trades badly
in practice, move Metrics to second and leave Agents default — a one-line change.

`VIEW_SUBTITLE.metrics`: *"Who signs up, how far they get, and whether they convert"*.
The Cost per user subtitle already reads as the money half; keeping both sentences on
screen is what stops the two tabs blurring together.

The `.vtab` count chip shows **WAU**, not total accounts — a number that changes is worth
a chip; a number that only goes up isn't.

### 4.2 Frame + panels, matching the existing idiom

Same structure as the Cost per user tab: an always-visible frame, then a `.tab` pill strip
paging between cuts of the same data. They page rather than stack because three tables in
one column means scrolling past two to reach the one you want.

```
+------------------------------------------------------------------------------+
|  Metrics   Agents   Review queue   Mailing lists   Cost per user             |
|  Who signs up, how far they get, and whether they convert                    |
|                                                    [ 7d ][ 30d ][ 90d ][All] |
+------------------------------------------------------------------------------+
|  ACCOUNTS      WAU          ACTIVATED      PAYING       MRR      ENDING <48h |
|     15          6            5 (33%)        1/4         $9.99        2       |
|  +3 this wk   40% of all    20+ word       trials      1 active   ! 1 with   |
|               accounts       profile       ended                    no prof. |
+------------------------------------------------------------------------------+
|  Activity                                          --- DAU   - - WAU         |
|   8 |                                     ,-,                                |
|   4 |          ,--,      ,----,   ,-------' '--                              |
|   0 +----------'  '------'    '---'                                          |
|     Aug 1        Aug 8       Aug 15      Aug 22                              |
|   Tracking began 2026-08-24. Nothing before that was recorded.               |
+------------------------------------------------------------------------------+
|  [ Funnel ]  [ Retention ]  [ Subscriptions ]  [ Users ]      [sort v]       |
+------------------------------------------------------------------------------+
|  Signed up            #######################################  15            |
|  Returned after day 1 ############################             11   73% v    |
|  Saved anything       #######################                   9   82% v    |
|  Has a profile        ####################                      8   89% v    |
|  Meaningful (20+ wds) ############                              5   63% v  ! |
|  Ran a search         ###########                               4   80% v    |
|  Tracked >= 1         ########                                  3   75% v    |
|  Tracked >= 3         ####                                      2   67% v    |
|  Checked an action    ##                                        1   50% v    |
|                                                                              |
|  Biggest drop: profile -> meaningful profile (-3). Click any bar to see who. |
+------------------------------------------------------------------------------+
```

Notes on the frame:

- **The step column is step-over-step, and it is the one people should read.**
  Percent-of-all is shown on hover. The largest single drop gets the warning marker and is
  called out in a sentence under the bars — a funnel that makes you compute the diff
  yourself is a table.
- **Clicking a bar** expands a roster of exactly the accounts who did *not* clear that
  step, with name, plan, days left, and a link to their Cost per user row. This is the
  feature that makes the page useful at this account count.
- **KPI tiles carry `n/d`, never a bare percentage.** `1/4 trials ended` says something
  `25%` does not.
- **The activity chart shows its start date as a caption**, and draws nothing to the left
  of it — no line running along zero into the past.

### 4.3 The four panels

**Funnel** (above) — plus a small "where users are right now" strip showing each account's
furthest stage, so a single reading tells you the shape of the population rather than a
set of nested counts.

**Retention** — cohort triangle by signup week, cells shaded by percent, raw counts
printed. With 9 accounts this is a handful of rows, which is fine; it is honest about being
small rather than smoothed into a curve.

```
Cohort      n    D1    D3    D7   D14
Aug 4-10    4   75%   50%   50%    -
Aug 11-17   6   67%   50%    -     -
Aug 18-24   5   80%    -     -     -
```

**Subscriptions** — plan-mix donut; conversion tile with denominator spelled out; a
`trial_ends_at` timeline for the next 14 days; promo-code table (code, kind, redemptions,
`grant`/`checkout` labelled distinctly since they work through different endpoints); MRR
and total margin.

**Users** — the roster, one row per account. Columns: name/email, signed up, plan +
days left, furthest funnel stage (as a mini progress pip strip), tracked count, profile
words, last active, AI cost, margin. Expandable into the same per-user detail the Cost per
user tab already renders — **reuse that component, don't fork it**.

The sort `select` is hidden unless the Users panel is showing, exactly as
`showUserPanel()` already does — it means nothing against a funnel or a cohort grid.

### 4.4 Visual conventions to inherit

- Funnel stage colours: a single hue ramping darker down the funnel. **Not** a categorical
  palette — the stages are ordered, and a rainbow implies they aren't.
- Plan-status colours **fixed per status**, not positional — the same rule the provider
  colours follow. `active` green, `trial` blue, `beta` violet, `canceled` grey, `past_due`
  amber. A positional palette would swap green and grey the moment one status overtakes
  another.
- Zero-state rows get a pill, like the existing **never used AI** pill, and sort last.
- Everything degrades to a setup notice, never an error, when a migration hasn't run:
  `activity_ready: false` hides the DAU chart and stages 2 and retention,
  `snapshots_ready: false` hides trend lines. Phase 0's numbers stand alone in all cases.

---

## 5. Endpoint

`GET /api/agents/metrics?days=30` — **localhost-only via `_require_local()`**, like every
other `/api/agents/*` route. That matters more here than anywhere else in the console:
the response carries names, emails, plan status, and profile-derived counts **for
accounts belonging largely to minors**.

Response sketch:

```jsonc
{
  "days": 30,
  "generated_at": "...",
  "activity_ready": false,        // user_activity table present?
  "snapshots_ready": false,       // user_metrics_daily table present?
  "activity_since": null,         // first day ever recorded; null until Phase 1
  "totals": { "accounts": 15, "dau": 3, "wau": 6, "mau": 11 },
  "funnel": [ { "key": "meaningful_profile", "label": "...", "count": 5,
                "of_all": 0.33, "step": 0.63, "userids": ["..."] } ],
  "signups_by_day": [ { "day": "2026-08-20", "count": 2 } ],
  "activity_by_day": [ { "day": "2026-08-20", "dau": 3 } ],
  "cohorts":  [ { "cohort": "2026-08-11", "n": 6, "d1": 4, "d3": 3, "d7": null } ],
  "subscriptions": { "by_status": {}, "mrr_usd": 9.99,
                     "conversion": { "converted": 1, "eligible": 4 },
                     "ending_soon": [], "promos": [] },
  "users": [ { "userid": "...", "stage": 5, "tracked": 3, "profile_words": 84,
               "last_active": "2026-08-22", "cost_usd": 0.31, "margin_usd": 9.68 } ]
}
```

`userids` ride inside each funnel stage so the click-to-expand costs no second request —
at 9 accounts the whole payload is a few KB. Past a few hundred, move the roster behind
a `?stage=` filter and paginate.

---

## 6. Traps

1. **`users.updated_at` is not a last-modified timestamp** (section 1). Do not use it.
2. **The UTC day rolls at 5pm Pacific.** Already burned once — that is why the Cost per
   user tab reports `latest_day` rather than "today". DAU carries the same hazard: a
   "today" tile reads 0 every evening on a day with real traffic. Label the DAU tile with
   its actual date, and prefer WAU as the headline number.
3. **Mock mode makes `user_costs` empty.** Stage 7's proxy silently reads 0 with no API
   key. Prefer the `filterValues` test when present, and mark the stage as a proxy in the
   UI rather than letting a config state read as a product failure.
4. **Three userids carry attributed spend with no `users` row** (leftover test ids). The
   funnel is built from the roster, so they are correctly absent — but any join back to
   `user_costs` must not resurrect them as accounts. This exact bug already made the
   Accounts tile read 6/9 against a true 3/12.
5. **Don't add spend figures here.** Cost per user owns that decomposition; a second
   place computing the same dollars is how the two drift.
6. **No CSV export, no sharing this page.** It is a roster of minors' names and emails on
   a localhost-only route, and it should stay one.
7. **Only non-empty `data` proves anything about `window.storage`.** When the app runs
   under a host that injects `window.storage`, `AppStorage.set` short-circuits and
   **never calls `/api/data/save`** ([script.js:66](script.js:66)) — that user's profile
   and tracker never reach Supabase at all, and every state metric reads them as inert.
   Worth checking which environment real users are on before trusting stage 3+ as a
   product signal rather than a storage-path artefact.

---

## 7. What was built

**Done (2026-08-23) — phases 0, 1 and 2.**

- `GET /api/agents/metrics?days=&limit=` (`get_user_metrics()` in `server.py`),
  localhost-only. Funnel, side metrics, plan mix, conversion, ending-soon, promos,
  signups-per-day, DAU series, retention cohorts, per-account roster.
- The **Metrics** view in [admin_console.html](admin_console.html) — first in the tab
  strip and the default view, with KPI tiles, the signups/DAU chart, and the four paged
  panels (Funnel, Retention, Subscriptions, Users). Funnel bars expand to name the
  accounts each step lost.
- [user_activity_schema.sql](../../db/user_activity_schema.sql) + `touch_user_activity()`, wired
  into the nine handlers that carry a userid, buffered in memory and flushed every 30s.
- [user_metrics_daily_schema.sql](../../db/user_metrics_daily_schema.sql) + `record_metrics_snapshot()`,
  written from the read path and throttled to one write per 5 minutes.

**Both .sql files still need running once in the Supabase SQL editor** — PostgREST exposes
no DDL, so nothing in this repo can apply them. Until then the view reports
`activity_ready: false` / `snapshots_ready: false` and hides the DAU line and the
Retention panel; everything else works, because the state half needs no migration.

**Changes made to the plan during implementation, and why:**

- **"Returned after signup day" was pulled out of the funnel chain.** As drafted it was
  stage 2, but the funnel is cumulative — so an account that built a profile and tracked
  five things on the day it signed up and never came back would have scored as a total
  failure at every stage below. It is a side metric now.
- **`rich_profile` and `deep_engagement` moved out of the chain too**, for the same
  reason: a student can search and track on a 20-word profile, so a "rich profile" gate
  between `meaningful_profile` and `ran_search` would have dropped people who are further
  along.
- **`ran_search` accepts tracked items as evidence**, not just the billed-call proxy.
  Under the cumulative rule the proxy's known weakness (mock mode bills nothing) would
  have deflated every stage below it rather than just its own.
- **Conversion counts `canceled`/`past_due`-with-a-Stripe-subscription as converted.**
  Left as drafted, the rate would have fallen every time a paying customer churned.
- Added, not in the original list: **consent gaps** — accounts on an older `terms_version`
  and accounts with no `terms_accepted_at` at all. The second is the more serious of the
  two and had no home anywhere else in the console. Three accounts are in that state.

**Still unbuilt — Phase 3, event-level funnel timing.** *How long* from signup to first
tracked opportunity. Needs real per-event rows rather than a daily rollup, and it is not
worth the write volume at this account count. Revisit alongside the rates-over-names call
in §2.5, once the roster is in the hundreds.
