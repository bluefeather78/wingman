# Action-item source trust — trusted aggregators & confidence tiers

> **⛔ SUPERSEDED (2026-08-25) — do not edit. Merged into
> [`DEADLINE_AND_TASK_PLAN.md`](DEADLINE_AND_TASK_PLAN.md), the single main plan for the
> deadline and task creators.** This file is kept only as history / detailed rationale for the
> trust-tier + aggregator design. Open task-trust decisions and the unified phased plan now
> live in the merged doc.

**Status:** planning (do NOT implement yet — nothing in this plan is built). Five decisions
are open (see "Open issues & decisions"); build starts only after they are answered.
**Owner:** _tbd_  **Started:** 2026-08-25
**Related code:** `generate_action_items.py`, `page_text.py`, `app/services/action_items.py`,
`app/routes/opportunities.py`, `action_items_schema.sql`, `frontend/src/lib/tracker.ts`,
`frontend/src/api/trackerStore.ts`, `frontend/app/(app)/index.tsx`,
`frontend/app/(app)/tracker.tsx`, `ops/admin.py`, `ops/core.py`, `ops/admin_console.html`.
**Related docs/memory:** `DEADLINE_CREATION_PLAN.md` (shares the THINK Scholars root cause —
see "Shared root cause"), memories `project_action_items_spa_pdf_gap.md`,
`project_action_item_verification.md`, `project_scraper_offsite_urls.md`.

## HANDOFF — read this first if you are picking this up in a new thread

This document is self-contained; you do not need the originating conversation. It was written
across one planning session (2026-08-25) that:
1. Diagnosed why THINK Scholars Program got a generic task list (root cause below, verified
   against live data — not theory).
2. Designed a trusted-aggregator + confidence-tier system to fix it (the bulk of this doc).
3. Worked out how task generation is timed and how user-added opportunities flow through it.

**Nothing has been implemented.** No schema run, no code changed. The only artifacts are this
doc and the two memory notes above.

**To continue:** get the operator (repo owner) to answer the five open decisions at the
bottom, then implement in the order given in "Build order". If you are the deadline thread
that drifted into tasks: the **"Shared root cause"** section is what you want — THINK's empty
deadlines and its generic tasks are the *same* SPA-unfetchability problem, and the fetch
fixes (A/B/C) should be built once and shared, not twice.

## Shared root cause with the deadline work (why both threads meet here)

THINK Scholars Program (`ec17921`, `https://think.mit.edu/`) appears in BOTH this plan and
`DEADLINE_CREATION_PLAN.md`, for one underlying reason, confirmed live 2026-08-25:

- `think.mit.edu` is a **client-side-rendered SPA**. The server returns **490 bytes** of
  HTML: just `<title>Home | MIT THINK Scholars Program</title>` and a `/assets/index-*.js`
  bundle. No content, and the official guidelines PDF link
  (`/static_files/THINK_Program_Guidelines_2026.pdf`) is **injected by JavaScript**, so it is
  not even in the served HTML to discover.
- `page_text.fetch_page_text()` (stdlib `urllib`, no JS) gets 33 chars of text → hits the
  `< 200 chars → empty-or-js` guard → returns `None`.
- Consequence for **tasks**: no page → no model call → `generic-fallback` → the generic
  "Competition" checklist, every task URL pointing at the bare homepage. (Stored: `ec17921`,
  `action_items_source='generic-fallback'`, `action_items_checked_at=null`.)
- Consequence for **deadlines**: the same unreadable page is why the deadline pass produced
  no current-cycle dates (see `DEADLINE_CREATION_PLAN.md`).
- The guidelines PDF is also rejected independently: `fetch_page_text` gates on content-type
  and drops `application/pdf` as `not-html`.

Three fetch-layer fixes address the *official* side of this (documented in memory
`project_action_items_spa_pdf_gap.md`), and they are **shared infrastructure the deadline and
task work should build once**:
- **A — PDF support:** accept `application/pdf`, extract text via `PyPDF2` (already a declared
  dep, used by resume import in `app/services/resume.py`), verify identically. Low effort.
  Does NOT fix THINK alone (its PDF link is JS-injected).
- **B — same-domain link discovery:** from the fetched page, follow ≤2–3 requirement-bearing
  same-domain links/PDFs. Does NOT fix THINK alone (raw HTML has no links).
- **C — headless render (Playwright):** render `empty-or-js` pages. The ONLY fix that reaches
  THINK's content and its JS-injected PDF link. Heavy dep; departs from the stdlib-only-agents
  convention — this is the operator's call.

**How aggregators relate to A/B/C:** an aggregator write-up (e.g. `lumiere-education.com`) is
plain server-rendered HTML our current fetcher reads fine, so aggregators partly *route
around* the SPA problem for **logistics** — but NOT for eligibility (see principle 2), and not
for the deadline pass, which needs the official source. So aggregators and C are
**complementary, not substitutes**: C is still the only way to read official eligibility/dates
off an SPA. Do not treat "we added aggregators" as "we no longer need Playwright."

## Reconciliation with `DEADLINE_CREATION_PLAN.md` (Claude-owns-tasks decision, 2026-08-25)

The deadline thread has **confirmed a decision that changes the task engine this plan builds
on** — read its "Architecture decision (2026-08-25)" section. This plan is written against the
CURRENT code (Gemini); the deadline thread is moving tasks to Claude. Both are compatible, but
whoever implements must build on the Claude version. The deltas:

- **Task model: Gemini `gemini-3.5-flash-lite` → Claude Haiku 4.5.** So in this plan,
  `generate_action_items.MODEL`, `call_gemini`→`call_claude`, `GEMINI_API_KEY`→
  `ANTHROPIC_API_KEY` (in `app/services/action_items.py`'s gate), and cost via
  `claude_common.estimate_cost` not `gemini_common`. Every "Gemini" reference in this doc's
  backend section is really "the task model", which is becoming Claude.
- **Aggregator discovery (D1) search phase is therefore a CLAUDE web-search phase**, not a
  Gemini googleSearch phase — `web_search` with a server-side `max_uses` cap (the
  `check_deadlines` shape), and `extract_source_urls()` grounding, not Gemini
  `groundingChunks`. The two-phase prose→JSON split still applies.
- **Action items gain a 7-day TTL** (deadline thread decision 3) — `resolve()` will re-verify
  on view past `action_items_checked_at`, mirroring deadlines, instead of serving a stored
  list forever. This plan's serve-path pending filter and tier tagging must live alongside
  that TTL check. (Updated in "Generation timing" below.)
- **Verification is unchanged and remains model-agnostic** — `page_text.claim_is_supported` /
  `quote_is_on_page` are the guarantee regardless of which model proposed the task, so the
  entire tier/allowlist design is unaffected by the model swap.
- **Core task pass stays search-OFF, but the reason changes.** The "no web search because
  JSON-suppresses-search" rationale was Gemini-specific. On Claude the core pass is search-off
  *by design* (page fetched by us, quotes verified against it); D1 discovery is the deliberate
  search-ON exception, operator-run and cost-quoted.
- **`extractTrackerInfo`'s browser-side task guess (Tier 2) is dropped.** The client no longer
  produces its own tasks — they come only from the `/action-items` endpoint. This bites only
  the unresolvable-submission (`id:null`) path; see the note in *Generation timing* step 3.
- **TTL: the failure-handling half is settled, the length is not.** Adopt now the
  **stamp-only-on-success / no-stamp-on-failure** rule (a failed/unfetchable run leaves the row
  due to auto-retry instead of freezing a `generic-fallback` for 90 days — which also fixes the
  "user-added inactive rows never retried" gap in *Generation timing*). The TTL **length** (7
  days vs this plan's 90-day rationale) is open and must be the same for on-demand and batch —
  see `DEADLINE_CREATION_PLAN.md` open decision 7.
- **Reverse dependency:** this plan's fetch fixes A/B/C are shared infrastructure the deadline
  pass also needs; Playwright (C) is **DECIDED: deferred** in both docs (2026-08-25).
- **The `trusted_aggregators` allowlist is now shared with deadlines (2026-08-25 decision).**
  The deadline escalation loop's 4th (off-domain) rung draws dates ONLY from operator-approved
  domains in this same table — so `trusted_aggregators` + `aggregators_common.py` (read side)
  + the console Sources tab are joint infrastructure, not task-only. The deadline side needs
  only the read/classify path; this plan still owns the full park-and-approve flywheel. Build
  the table + read side once; whichever plan lands first creates it, the other consumes it.
- **Sequencing:** the Gemini→Claude task migration should land FIRST (it is simpler and the
  deadline thread owns it); the aggregator/tier work builds on top. Do not do both in one
  change. Tracked as build-order step 0 below.

## Purpose

Today an action item is one of exactly two kinds: `page` (a claim verified against the
program's OWN page, quote checked in code) or `generic` (type-derived boilerplate that
asserts nothing). Everything else — third-party guides, official PDFs behind JS, aggregator
write-ups — is invisible. THINK Scholars (`ec17921`, `think.mit.edu`, a JS SPA) is the
worst case: the official page is unreadable, the official guidelines PDF link is JS-injected,
and the richest available description of the program sits on a third-party aggregator
(`lumiere-education.com`) the agent is forbidden to use.

This plan adds:

- **(a) A trusted-aggregator allowlist the operator controls.** An aggregator domain
  contributes tasks to students ONLY once the operator has approved it. Until then its
  discovered tasks are *parked*, not shown.
- **(b) A per-task trust tier, displayed to the student as a confidence gradient**, ordered
  highest→lowest:
  1. **Official** — the program's own domain (page or its own PDF).
  2. **Trusted aggregator** — a page on an operator-approved aggregator domain.
  3. **Aggregator (pending)** — a page on a domain not yet approved. *Withheld from
     students*; visible only to the operator as the approval queue. (This is where (a) and
     (b) meet — see "Reconciling (a) and (b)".)
  4. **Generic** — type-derived, asserts nothing program-specific.

## First principles (carry these through every decision)

1. **Verification ≠ trust, and they stay orthogonal.** The two code tests
   (`page_text.claim_is_supported`, `quote_is_on_page`) prove *this source actually said
   this* — a code guarantee. The trust tier answers *how authoritative is this source* — an
   operator policy. An aggregator quote can pass verification perfectly and still be wrong
   about the program, because the aggregator itself is wrong. So aggregator tasks are
   verified the same way but carry a lower tier and are labelled by source.
2. **Aggregator sources may back LOGISTICS, never ELIGIBILITY.** The original harm — NYU's
   fabricated "Algebra 2 prerequisite" — is a false *eligibility/prerequisite* claim that
   stops a student applying. An aggregator being wrong about a prerequisite is exactly that
   harm with a citation attached. Therefore: a task that states a prerequisite, required
   course/test/score, GPA, age/grade limit, or eligibility condition may be kept **only at
   the Official tier**. At the aggregator tiers such a task is dropped (not demoted). This
   preserves the whole reason the verification layer exists while letting aggregators fill
   in logistics (deadlines, what to submit, how to register).
3. **Third-party = lower tier, always, never silently promoted.** A model saying a source is
   authoritative is worth nothing (the lesson of `basis:"page"` and of scraper-typed URLs).
   Only the operator's allowlist promotes a domain.
4. **The allowlist is enforced at BOTH generation and serve time.** Defense in depth, same
   shape as the subscription gate: generation tags tier; the serve path in
   `app/services/action_items.py` independently filters out pending/blocked tiers, so a bug
   in generation can never leak an un-approved source to a student.
5. **Every displayed task links to the specific document that backs it** — the "source of
   truth that made the model believe it", per the original ask. That is a *second* link,
   distinct from the step's action URL.

## Reconciling (a) and (b) — the approval flywheel

The tension: (a) says un-approved aggregators are "not considered for tasks", while (b)
lists "other aggregators not yet on the trusted list" as tier 3. Resolution, modelled on the
repo's existing **discovery-vs-execution** splits (mailing-list recipes at
`status='pending_review'`; scraped rows at `is_active=false` awaiting activation):

- Discovery MAY surface candidate tasks from any aggregator domain.
- A candidate whose domain is **approved** → tier 2, shown to students.
- A candidate whose domain is **not yet approved** → tier 3, `source_tier='pending'`,
  **stored on the row but never served to students.** It surfaces only in the console's
  Sources tab as *"lumiere-education.com — 14 parked tasks across 9 programs. Approve?"*.
- A candidate whose domain is **blocked** → dropped entirely, never stored.

So tier 3 is real and complete in the data model (satisfying (b)'s ladder) while never
reaching a student (satisfying (a)). Approving a domain promotes its parked tasks to tier 2
live. The operator thus sees *which* aggregators keep proving useful and decides with
evidence in front of them, instead of guessing an allowlist up front.

## The hard part: how aggregator URLs are discovered

The action-item agent deliberately has **no web search** (cost, and the JSON-suppresses-
search finding). To consider an aggregator we must first find its URL for a given program.
Three options; this plan recommends **D1**, gated.

- **D1 — a discovery phase with web search (recommended).** A prose, search-ON phase 1 (the
  same two-phase shape `check_deadlines`/`check_reviews` use) asks "where is this program
  described?", keeps the grounding-resolved URLs, and phase 2 extracts tasks per source with
  tools off. This reintroduces a per-search fee, so it is **opt-in** (`--aggregators`),
  **cost-quoted by `--preview`**, and **operator-run** like every paid agent. The allowlist
  then decides which discovered domains are usable; discovery itself is domain-blind.
- **D2 — operator-supplied URLs (free, manual).** When approving a domain the operator can
  paste specific aggregator URLs per opportunity. Precise and free but labor-heavy; good as a
  supplement, not the engine.
- **D3 — per-aggregator index crawl.** For one known aggregator (e.g. the Lumiere blog),
  crawl its program-guide index once and map program→URL by name match. Efficient per
  aggregator, more to build; a later optimization.

Note the pleasing inversion: the official page is often the UNREADABLE one (SPA), while the
aggregator write-up is plain server-rendered HTML that our existing fetcher reads fine. So
aggregators partly *route around* the SPA/PDF gap even before the A/B/C fetch fixes land.

## Data model

### New table — `trusted_aggregators` (new `trusted_aggregators_schema.sql`)

```
create table if not exists trusted_aggregators (
  domain      text primary key,                 -- normalized host, e.g. 'lumiere-education.com'
  status      text not null default 'trusted',  -- 'trusted' | 'blocked'
  label       text,                             -- human name, e.g. 'Lumiere Education'
  notes       text,
  added_by    text,
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);
-- ALTER-then-CREATE convention (see mailing_list_schema.sql): every future column is an
-- `add column if not exists`, because PostgREST 400s an entire write on one unknown key.
```

`status='blocked'` is a deny entry — its tasks are dropped, never parked. A domain absent
from the table is *pending* (parked, shown to operator). Present + `trusted` is tier 2.

### `action_items` element — additive fields (JSONB, no DDL)

`action_items` is `jsonb`, so new per-item keys need no migration. Current shape plus:

```
{ "text": "...", "url": "step action URL or null",
  "basis": "page" | "generic",            -- UNCHANGED (is this a specific claim or boilerplate)
  "evidence": "verbatim quote or null",
  "source_tier":   "official" | "trusted" | "pending" | null,   -- null == generic
  "source_url":    "https://.../the exact doc the quote came from, or null",
  "source_domain": "lumiere-education.com | null" }
```

- `basis` stays "is this a program-specific claim". `source_tier` adds "whose page proved
  it". The student-facing gradient is derived: official → trusted → (pending never ships) →
  generic.
- **Back-compat:** a legacy `basis:'page'` item with no `source_tier` is read as
  **`official`** — every such task predates aggregators and was a program-domain quote.
  Legacy generic stays generic. Same "absent means the safe reading" rule as
  `ImportantDate.estimated` and today's absent-`basis`.

### `action_items_source` (row-level) — one new value

Add `'aggregator-verified'` for a row whose kept tasks include a trusted-aggregator source.
Existing values (`page-verified`, `page-empty`, `generic-fallback`, `unparsed`) unchanged.

## Backend

### New shared lib — `aggregators_common.py` (repo root)

Mirrors `seeds_common.py`: stdlib-only, imported by the agent, `ops/core.py`, and
`app/services`. Provides `normalize_domain(url) -> host`, `load_aggregator_policy(supabase…)
-> {domain: status}`, and `classify_source(url, official_domain, policy) -> 'official' |
'trusted' | 'pending' | 'blocked'`. One definition, so the agent, the console and the serve
path cannot disagree about what a domain is — the same reason `deadline_write_decision` and
`action_items_write_decision` are single-sourced.

### `page_text.py`

No change to fetch mechanics (it already fetches any URL). Add nothing here beyond a domain
helper if not reused from `url_validate`. The A/B/C fetch fixes (PDF, link-following,
SPA-render) from `project_action_items_spa_pdf_gap.md` are **complementary and out of scope
for this plan** — they widen what the *official* tier can read; this plan widens which
*sources* count. They compose but ship independently.

### `generate_action_items.py`

- New `--aggregators` flag turns on the D1 discovery phase (search ON, cost-quoted, off by
  default). Without it the agent behaves exactly as today (official + generic only), so this
  change is inert until deliberately run.
- For each resolved source document (official page, official PDF, each aggregator page), run
  the existing verify pipeline. Tag each kept task via
  `aggregators_common.classify_source(...)`.
- **Eligibility carve-out (principle 2):** before keeping an aggregator-tier task, run it
  through an eligibility-claim detector (reuse/extend `GENERIC_TOKENS` logic in `page_text`:
  a task whose distinctive tokens include a prerequisite/eligibility marker is
  official-only). At `trusted`/`pending` tier such a task is dropped; at `official` it is
  kept as today.
- `action_items_write_decision(...)` gains a source dimension: official + trusted tasks are
  written and servable; pending tasks are written with `source_tier='pending'` (withheld);
  blocked dropped. Still the single shared decision point.
- Run summary counts a new line: tasks by tier (official / trusted / pending / generic) and
  parked-domain tally, so a run says how much it discovered vs how much is awaiting approval.

### `app/services/action_items.py` (serve path)

- After `resolve()` produces the stored list, **filter out any task with
  `source_tier in ('pending','blocked')`** before returning to the student. This is the
  independent enforcement of (a) — even a mis-tagged row cannot leak an un-approved source.
- If filtering empties a row that has stored tasks, top up to `MIN_ITEMS` with generic items
  (reuse `top_up`) so a student never sees a suspiciously short list.

### `ops/core.py` + `ops/admin.py` (local-only console API)

New functions in `ops/core.py`, new routes in `ops/admin.py` (localhost-gated like the rest):

- `GET /api/aggregators` → list trusted/blocked domains (from `trusted_aggregators`).
- `POST /api/aggregators` `{domain, status, label, notes}` → add/upsert. `normalize_domain`
  first; reject a bare TLD or the program's own hosts.
- `DELETE /api/aggregators/{domain}` → remove (reverts it to pending, not blocked).
- `GET /api/aggregators/pending` → computed from the catalog: scan `action_items` for
  `source_tier='pending'`, group by `source_domain`, return `{domain, task_count,
  program_count, sample_tasks:[{text, evidence, source_url, opp}]}`. (Compute from JSONB;
  if slow at scale, a later materialized view — not now.)
- `POST /api/aggregators/{domain}/approve` → upsert status `trusted` AND promote: PATCH every
  parked task on that domain from `pending`→`trusted` (bust `_opportunities_cache`).
- `POST /api/aggregators/{domain}/block` → status `blocked` AND strip its tasks from every
  row.

Degrade-not-break: if `trusted_aggregators` is absent, the console shows the setup step
(name the .sql), the agent treats every aggregator domain as pending (nothing ships), and the
serve filter still works (pending is withheld). Same shape as every other schema-gated
feature here.

## Frontend (student-facing)

### `frontend/src/api/trackerStore.ts`

- Extend `ActionItem`: add `sourceTier?: 'official' | 'trusted' | 'generic'` (note: `pending`
  is never sent to the client — the serve path already filtered it), `sourceUrl?: string |
  null`, `sourceDomain?: string | null`.
- Add `taskTrustTier(ai): 'official' | 'trusted' | 'generic'` — the single place trust is
  read (the `isPageBackedTask` role, generalized). Legacy `basis:'page'`+no tier → `official`;
  generic/absent → `generic`. Keep `isPageBackedTask` as a thin `tier !== 'generic'` shim so
  nothing else has to change at once.

### `frontend/src/lib/tracker.ts`

- `shapeActionItems` maps the three new fields, and — crucially — honours `source_tier` ONLY
  on the verified endpoint path (`normalizeVerifiedActionItems`). `normalizeUnverifiedAction
  Items` (browser-generated tasks) still forces `generic`, because nothing on that path saw
  a page. Unchanged trust rule, one more field.

### `frontend/app/(app)/index.tsx` (Home Base task modal)

Replace the two-group split with the confidence gradient. Recommended presentation (both a
grouped heading AND a per-task source chip, so the ladder reads at a glance and each task
still names its own proof):

- **Group headings** by tier, in order:
  - "From the program's own page" (official)
  - "From a trusted guide" (trusted) — subtitle shows the domain(s)
  - "Typical steps — confirm on the site" (generic)
- **Per-task source chip** (small pill, tappable → `sourceUrl`):
  - Official → green chip "Program page"
  - Trusted → blue chip "Guide · {domain}"
  - Generic → grey chip "Typical step" (no link)
  - Chip colour is FIXED per tier (like the console's provider/plan colours), never
    positional, so the gradient reads the same on every card.
- The chip's link is the **evidence** `sourceUrl` (where the claim came from); the existing
  trailing `↗` stays the **step action** `url` (where to go do it). Two different links,
  deliberately — the whole point of the ask.
- Legacy tasks (no tier) render as official or generic per the back-compat rule; existing
  cards therefore look exactly as they do today until regenerated.

## UI — admin console (`ops/admin_console.html`)

New `.vtab` **Sources** (`view-sources`), between Mailing lists and Cost per user:

- **Trusted aggregators** table — domain, label, status, task count in catalog, added_by,
  actions (Block / Remove). An "Add domain" row.
- **Awaiting approval** (the flywheel) — one card per pending domain, sorted by task count:
  domain, parked-task count, program count, and 2–3 sample tasks each showing the task text,
  its verbatim evidence quote, and a link to the exact `source_url`. Two buttons: **Approve
  domain** (promotes live) / **Block domain**. This is where the operator actually exercises
  control (a).
- KPI tiles: trusted domains, blocked domains, pending domains, parked tasks. `table_ready`
  reported separately (setup step if the schema is unrun), same as every other tab.
- Colours fixed per status (`trusted`/`pending`/`blocked`), not positional.

## Migration / back-compat summary

- Additive only. `trusted_aggregators_schema.sql` is a new one-time DDL step; the item-level
  fields are JSONB and need no migration.
- Every legacy row renders and behaves identically until an aggregator run touches it:
  legacy `page`→official, legacy generic→generic, no pending anywhere.
- Inert-by-default: nothing discovers aggregators until `--aggregators` is run, and nothing
  ships to students until a domain is approved. A deploy with an empty allowlist ==
  today's behaviour.

## Generation timing & user-added opportunities

**Today.** Tasks are generated on two paths sharing one cache and one logic:
- *Batch* (`generate_action_items.py`): sweeps the **active** catalog (`is_active=eq.true`),
  90-day staleness, **operator-run, not scheduled** (no cron; monthly job paused). Pre-warms
  rows so the on-demand path is a free cache hit.
- *On-demand* (`app/services/action_items.resolve`, `GET
  /api/opportunities/{id}/action-items`): serves the stored list if present, else runs the
  identical pipeline once and caches. Fired when a student adds/opens a tracked opp.
  **Note (per `DEADLINE_CREATION_PLAN.md`): this is gaining a 7-day TTL** — it currently
  serves a stored list forever; it will re-verify on view past `action_items_checked_at`,
  mirroring the deadline endpoint. The pending-tier serve filter runs on top of that.

**User-added opps.** The intake (`tracker.tsx`) resolves the submission to a `catalogId`:
1. Deduped into an existing row → already-generated (often free) verified list.
2. Genuinely new row → created `is_active=false`, id returned; `resolve()` generates ONCE
   on-demand at add time and caches.
3. Unresolvable (`id:null`) → browser model output, forced all-generic.
   **Changes under the Claude decision:** `extractTrackerInfo`'s browser task guess (Tier 2)
   is dropped, so there is no model output to force-generic here. Replacement: an
   unresolvable add shows a **static per-type generic checklist built client-side** (no model
   call) — honest and free, and the same content the server's `generic_items()` would produce.
   It carries no page-backed items by construction, which is correct: nothing read a page.
**Pre-existing gap:** the batch selects `is_active=eq.true`, so it NEVER re-touches
user-added inactive rows. They get exactly one on-demand attempt; a `generic-fallback` from
an unfetchable page is never retried until an operator activates the row.

**How this direction changes it.** Aggregator discovery uses **web search — paid**, and the
repo never puts paid search on a student's interactive path. So:
- **Interactive `resolve()` is unchanged in cost profile:** official + generic only, no
  aggregator search, plus the serve-path pending filter. A student's add stays instant.
- **Aggregator tiers are a deferred, operator-run second wave** (`--aggregators` batch) —
  which is exactly what the park-and-approve flywheel wants.
- **Close the user-added gap here:** the enrichment pass selects **tracked rows regardless
  of `is_active`** (active catalog OR tracked-by-≥1-student), so user-added opps ride the
  same aggregator sourcing instead of being permanently invisible to it.
- **Explicitly ruled out:** auto-running aggregator web search per add (async background).
  Unbounded per-add spend on a largely-minor user base adding arbitrary URLs — the cost
  failure mode the repo exists to avoid. Aggregator search stays operator-gated and
  `--preview`-costed.

Resulting user-added lifecycle: `add → on-demand official+generic → operator --aggregators
pass (now incl. tracked inactive rows) → trusted shown / pending parked → operator activates
→ future batches refresh it like any catalog row`.

## CLAUDE.md

CLAUDE.md documents *shipped* behaviour, so it is updated at implementation time, not now.
The action-items section will need: the trust-tier model, the eligibility carve-out
(principle 2), the allowlist's dual enforcement, and the aggregator discovery cost note.

## Open issues & decisions

Two kinds: **operator decisions** (need the repo owner's call — policy/product) and
**technical unknowns** (need design work, some can be resolved by whoever implements). All
are unresolved as of 2026-08-25.

### Operator decisions (need the owner's answer before build)

1. **Discovery engine:** D1 web-search phase (recommended, cost-gated) vs D2 operator-
   supplied URLs vs both. Drives cost and scope. — _undecided_
2. **Eligibility carve-out (principle 2):** confirm aggregator tiers may NEVER back an
   eligibility/prerequisite claim, only logistics. (Strongly recommended.) — _undecided_
3. **Pending tasks: park or discard?** Parking builds the approval flywheel (recommended);
   discarding is simpler but the operator never learns which aggregators are worth trusting.
   — _undecided_
4. **Student display:** grouped headings + per-task chips (recommended) vs chips only vs
   headings only. — _undecided_
5. **Seed `lumiere-education.com` as trusted on day one**, or start empty and populate the
   allowlist from the pending queue after the first run? — _undecided_
6. **Fetch fix C (Playwright) — DECIDED (2026-08-25): DEFER.** Not built now. SPA-only
   official sources (THINK's own page) stay unreadable; deadlines recover dates via the
   escalation loop's prior-cycle + trusted-third-party rungs. Consequence for THIS plan:
   THINK's official *task requirements* also stay unreadable, so its checklist relies on the
   aggregator tier (trusted third-party) — which is exactly what this plan adds. Revisit if
   SPA-only sites prove common. (Resolved identically in `DEADLINE_CREATION_PLAN.md`.)

### Technical unknowns (need design, not policy)

T1. **How to select "tracked rows regardless of `is_active`."** There is **no opp→trackers
   index** in this repo — tracker data lives per-user in `users.data` jsonb
   (`hs-tracker-data`), so "which opportunities does anyone track" is not directly queryable.
   Options: (i) scan all users' tracker data server-side to build the set (works now, O(users),
   localhost/ops only); (ii) add a lightweight `tracked_opportunities` table written on
   add/remove; (iii) just run the enrichment pass over active catalog + all
   `source='user-submitted'` rows regardless of whether currently tracked (simplest, slightly
   over-broad). Leaning (iii) for v1. — _needs decision at build time_

T2. **Pending-domain aggregation at scale.** `GET /api/aggregators/pending` scans
   `action_items` jsonb across the catalog to group parked tasks by domain. Fine at ~1.4k
   rows; if it gets slow, a materialized view or a side table. Not a v1 blocker. — _defer_

T3. **Eligibility-claim detector.** Principle 2 needs a code test that flags a task as an
   eligibility/prerequisite claim (to drop it at aggregator tiers). Sketch: reuse
   `page_text` tokenization; a task carrying a prerequisite/eligibility marker token
   (course names, "prerequisite", "GPA", "grade", test names, an age/grade number) is
   official-only. This is a NEW classifier and needs its own small test suite (extend
   `test_action_items.py`), analogous to how `GENERIC_TOKENS` is tested against an empty
   page. Risk: false negatives let an eligibility claim through at tier 2 — so bias the
   detector toward flagging, since the cost of a false positive is only that a logistics task
   gets dropped from an aggregator. — _needs design_

T4. **Trust-marker consistency across dates and tasks.** Deadlines already show `(est.)` /
   "Predicted dates from past cycle" for low-confidence dates; tasks will show tier chips.
   These are two trust vocabularies on the same card. Decide whether they should share
   styling/wording so a student reads one confidence language, not two. — _needs design_
   (touches `DEADLINE_CREATION_PLAN.md`)

T5. **Does the aggregator quote prove the PROGRAM's requirement or the AGGREGATOR's claim?**
   Verification proves the aggregator *said* it. The tier label must make the distinction
   legible to a student ("According to {domain}'s guide…"), and the wording is not yet
   drafted. — _needs copy_

## Build order (once decisions land)

Suggested sequence, each step shippable and inert-by-default:
0. **Gemini→Claude task migration** (owned by `DEADLINE_CREATION_PLAN.md`, decisions already
   confirmed there) — model swap, `ANTHROPIC_API_KEY` gate, 7-day TTL, cost attribution. This
   plan builds on the result; do not fold it into the aggregator change.
1. **Fetch fixes A (PDF) + B (link discovery)** — shared with deadline work, low risk, widen
   the official tier immediately. (C/Playwright gated on decision 6.)
2. **Schema** — `trusted_aggregators_schema.sql` + the additive `action_items` item fields
   (JSONB, no DDL). Console/agent degrade-not-break until run.
3. **`aggregators_common.py`** — shared classify/normalize lib.
4. **Serve-path pending filter** — `app/services/action_items.py` withholds `pending`/
   `blocked`. Safe even before any aggregator exists (no-op).
5. **Agent `--aggregators` discovery pass** (engine per decision 1) + eligibility detector
   (T3) + tier tagging + write decision. Operator-run, `--preview`-costed.
6. **Console Sources tab** + `ops` routes — the approval flywheel.
7. **Frontend** — `ActionItem` fields, `taskTrustTier`, Home Base rendering (per decision 4).
8. **CLAUDE.md** — document shipped behaviour.

## Change log

- **2026-08-25** — Doc created. Diagnosed THINK Scholars (SPA root cause, live-verified),
  designed the trusted-aggregator + tier system, mapped generation timing and user-added
  flow, made the doc a self-contained handoff and enumerated open issues T1–T5 + decisions
  1–6. Nothing implemented.
- **2026-08-25 (same day, later)** — Reconciled with `DEADLINE_CREATION_PLAN.md`'s confirmed
  "Claude owns tasks + deadlines" decision: task engine becomes Claude Haiku 4.5 (not Gemini),
  aggregator discovery becomes a Claude web-search phase, action items gain a 7-day TTL, cost
  attribution moves to Anthropic. Added build-order step 0 (the migration lands first) and a
  Reconciliation section. Verification/tier design unaffected (model-agnostic).
