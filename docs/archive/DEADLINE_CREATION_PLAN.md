# Deadline creation — gap analysis and fix plan

> **⛔ SUPERSEDED (2026-08-25) — do not edit. Merged into
> [`../plans/DEADLINE_AND_TASK_PLAN.md`](../plans/DEADLINE_AND_TASK_PLAN.md), the single main plan for the
> deadline and task creators.** This file is kept only as history / detailed rationale for the
> deadline gaps (G1–G4). All decisions and the unified phased plan now live in the merged doc.

**Status:** planning — **all open decisions resolved 2026-08-25**; ready to implement on the
operator's go-ahead (phased plan below). Do NOT start coding until told to.
**Owner:** _tbd_  **Started:** 2026-08-25

> **See also `ACTION_ITEMS_TRUST_PLAN.md`.** THINK Scholars (`ec17921`) appears in both
> plans for the SAME underlying reason: `think.mit.edu` is a JS-rendered SPA whose served
> HTML is 490 bytes, so our fetcher reads nothing — which produces empty deadlines here AND
> a generic task list there. The fetch-layer fixes (PDF support, same-domain link discovery,
> Playwright headless render) are **shared infrastructure — build once, not twice.** If task
> work has drifted into this thread, that doc's "Shared root cause" and "Open issues &
> decisions" sections are the handoff.

## Purpose

Three tracked opportunities produced **zero** `important_dates` (and therefore no
Quest-Log milestones and no calendar events), each for a *different* reason. This document
traces what actually happened, names the gaps, and proposes fixes for all of them. The goal
is that a recurring, dated program should almost never end up with an empty date field, and
that the rare genuinely-empty cases are represented honestly rather than silently frozen.

## How deadline creation works today (the pipeline)

1. A student adds/opens a tracked opportunity. The client may run `extractTrackerInfo`
   (`frontend/src/lib/tracker.ts`, Gemini) for an immediate *guess*.
2. The authoritative dates come from the on-demand endpoint
   (`app/routes/opportunities.py` → `check_deadlines.check_one()`, Claude), cached per
   opportunity for 7 days (`dates_last_checked_at`).
3. `check_one()` is **two-phase**: phase 1 = prose, tools ON (search + fetch); phase 2 =
   strict JSON, tools OFF. The split exists because demanding JSON collapses the search
   rate.
4. `deadline_write_decision()` decides whether the result may overwrite the row and whether
   to stamp `dates_last_checked_at`. It is shared by the batch loop and the interactive
   endpoint so they cannot drift.
5. The client overlays the authoritative result. An **empty** `important_dates` overwrites
   the client guess only when the source is verified (`fresh, real search`).

## Architecture decision (2026-08-25): Claude owns tasks + deadlines; collapse the redundant Gemini producer

Tracing the three rows surfaced a structural redundancy: **three** model producers write
date/task data, two of them redundantly.

| Data | Authoritative endpoint (cached + on-demand) | was | now |
|---|---|---|---|
| dates/status | `/api/opportunities/<id>/deadline` | Claude | **Claude** (unchanged) |
| action items | `/api/opportunities/<id>/action-items` | Gemini (`gemini-3.5-flash-lite`) | **Claude (Haiku 4.5)** |
| meta / fit | `extractTrackerInfo` (client, Gemini) | Gemini | **Gemini** (kept, slimmed) |
| apply url | `extractTrackerInfo` invents `apply_url` | model-typed | **static `opp.url`** |

The redundancy was that `extractTrackerInfo` (the monolithic client Gemini pass) re-derived
BOTH dates and tasks that the two dedicated endpoints already produce — better, verified, and
cached/shared across users. Both endpoints are already "serve cache if valid, else generate
on-demand and cache."

**Decisions (confirmed 2026-08-25):**

1. **Claude owns both tasks and deadlines** — these are the product's core, and warrant the
   higher-reasoning model over `gemini-3.5-flash-lite`. Model: **Haiku 4.5** for both (the
   existing interactive-Claude / deadline pin; a large step up from flash-lite). Both are
   **on-demand, cached 7 days, stamped only on a successful run** — a failed/unreachable run
   writes no stamp, leaving the row due so the next view auto-retries. (This is already the
   deadline model; tasks adopt it.)
2. **Tasks move off Gemini onto Claude**, keeping the page fetch (`page_text`) and the
   **code-side quote verification** (`claim_is_supported` / `quote_is_on_page`) exactly as-is —
   that verification is model-agnostic and remains the real guarantee (it is what stopped the
   "Algebra 2" fabrication). Only the model call swaps (`call_gemini` → `call_claude`), and
   cost must switch to `claude_common.estimate_cost` (the same trap `check_deadlines` hit).
3. **Task caching gains a 7-day TTL** — today the action-items endpoint serves a stored list
   *forever* (never re-checks). It will now re-verify on view past 7 days, mirroring
   deadlines, via `action_items_checked_at`. (Accepted cost: re-bills a Claude task call per
   opportunity per week it is viewed.)
4. **Gemini stays for `meta`/`fit` only** — `extractTrackerInfo` is slimmed so its date and
   task outputs are dropped/ignored. The client takes dates from the deadline endpoint and
   tasks from the action-items endpoint.
5. **`apply_url` = static `opp.url`** (link-checked by `check_links.py`) + a static label. No
   model-typed apply URL; per-step action-item URLs cover the deep-link need.
6. **The two Claude calls stay separate** — the deadline check needs `web_search` (current +
   prior cycle), the task check must NOT search (page fetched by us, quotes verified against
   it). Merging would break the quote-verification guarantee and mix search/no-search modes.

**Consequence — G4 is resolved by this, not by the status-aware overwrite.** With no Gemini
date estimate produced at all, there is nothing for a verified-empty Claude result to wipe.
The overlay simply uses the Claude dates. (The Fix-G4 section below is retained for the record
but is superseded — no `applyDeadlineCheckToInfo` status-gating is needed if Gemini never
writes dates.)

**Behaviour changes to handle carefully:**
- The action-items endpoint (`app/services/action_items.py:resolve`) gates on
  `GEMINI_API_KEY`; it moves to `ANTHROPIC_API_KEY`. The free `generic-fallback` (no key /
  fetch failed) path stays.
- `resolve()` must add the 7-day staleness check (currently absent) against
  `action_items_checked_at`, reusing the deadline endpoint's `DEADLINE_STALE_DAYS` shape.
- Per-user cost attribution: action-item spend becomes Anthropic, not Google —
  `classify_feature` needs a signature and the Claude model pin must be imported so
  `provider_for_model` attributes it correctly.
- Cost delta: Haiku (~$1/$5 per MTok) vs flash-lite for tasks is a per-row increase, plus the
  7-day re-bill. Accepted deliberately for product quality on the two core features.

**⚠ Reconcile with `ACTION_ITEMS_TRUST_PLAN.md` — two conflicts this decision creates:**
- **Model.** That plan is written on Gemini (`generate_action_items.py`, `call_gemini`) and
  quotes Gemini task costs throughout. Moving tasks to Claude Haiku supersedes its model
  choice. The two compose cleanly — the trust-tier / aggregator work is model-agnostic and
  the code-side quote verification is unchanged — but that doc's cost figures, `call_gemini`
  references, and the D1 discovery-phase design need updating to Claude. **Neither doc should
  claim a model the other contradicts.**
- **Staleness — DECIDED (2026-08-25): on-demand task TTL = 7 days, matching deadlines.** The
  `resolve()` view path re-verifies a task list older than 7 days, exactly like the deadline
  endpoint (`DEADLINE_STALE_DAYS`). The batch agent's own staleness window in
  `ACTION_ITEMS_TRUST_PLAN.md` (90-day) is a **separate operator knob** for bulk pre-warming
  and does not need to match — for deadlines the batch likewise ignores the interactive TTL.
  They don't conflict: the view path is the freshness driver, the batch is a bulk backstop.

## Evidence — the three traced rows (real data, checked 2026-08-25)

All three: phase 1 searched, phase 2 parsed, dates came back empty, and — because the rows
had no existing dates ("nothing to lose") — `deadline_write_decision` **wrote the empty
result and stamped**, caching the hole for 7 days.

| Row | id | status | note | real situation |
|---|---|---|---|---|
| THINK Scholars (MIT, think.mit.edu) | ec17921 | running | "active … but specific dates for the current cycle are not available in the fetched pages" | recurring program with a well-known annual deadline (~Jan 1); estimation should have fired |
| Harvard Science Research Conference (hcura.org) | ec18392 | unknown | "could not access hcura.org or locate cycle information" | real annual spring conference; primary site was unreachable |
| KCLS Teen Advisory Board | ec18286 | running | "Rolling admissions with no published deadlines" | genuinely always-open volunteer board; no deadline exists |

(Aside: HSRC also has a duplicate inactive row ec18620 pointing at a spam
`thedatascientist.com` URL — data-hygiene cleanup, not a creation gap.)

## Gap catalog

- **G1 — Estimation physically cannot run.** `MAX_SEARCHES = 1` (`check_deadlines.py:140`)
  caps `web_search` at one query, enforced server-side by Anthropic (`max_uses: 1`). But the
  phase-1 prompt *mandates* a multi-query discipline (current cycle, then "ALWAYS ALSO" the
  prior cycle, then FAQ/key-dates). The single search lands on the evergreen page that omits
  dates; the mandatory prior-cycle search — the estimation basis — never runs. `web_fetch`
  (cap 5, free) can't rescue it because fetch only reaches URLs a search already surfaced.
  → THINK. **Compounding cause (shared with `ACTION_ITEMS_TRUST_PLAN.md`):** `think.mit.edu`
  is a client-side-rendered SPA whose served HTML is ~490 bytes, so a plain fetch reads
  nothing off the official page at all. **Playwright (fetch fix C) is DEFERRED (decision 8)**,
  so THINK's own page stays unreadable; its dates are instead recovered via the escalation
  loop's prior-cycle and trusted-third-party rungs, which land on *other*, server-rendered
  pages. If SPA-only sites later prove common, revisit fetch fix C.
- **G2 — Unreachable site = give up + freeze.** When the program's own site is inaccessible,
  the agent gives up (`status=unknown`, empty) with no fallback (prior cycle, third-party,
  general knowledge). Worse, that empty `unknown` is treated as a confident verified write —
  stamped and cached 7 days — even though "we couldn't reach the site" is closer to
  "unparsed" than to a real answer. → Harvard HSRC.
- **G3 — "Always open" is unrepresentable.** A rolling/always-open program honestly has no
  deadline, so it produces zero dates. But `computeProgressStatus` (`status.ts:92`) maps
  "no dates" → `not_started`, so the program can never read **Happening Now**, generates no
  calendar event, and is effectively invisible to the app's action model — even though the
  student can act today. → KCLS TAB.
- **G4 — A verified-but-empty result wipes a real client estimate.** On add, the fast
  client-side `extractTrackerInfo` (Gemini) can produce estimated dates. The authoritative
  check then overlays via `applyDeadlineCheckToInfo` (`tracker.ts:111-113`), whose guard
  `(verified || length)` lets an **empty** array overwrite when the source is verified — so
  the estimate is wiped. Combined with G1/G2 (verified-empty is common for recurring /
  unreachable programs), the student sees dates appear on add and then vanish. The guard was
  widened from a bare `.length` so a verified "discontinued, no dates" could clear a bogus
  guess — correct for `not_running`, but it can't distinguish "discontinued → clear" from
  "search miss / unreachable → keep". → contributes to THINK & Harvard showing nothing.
- **G-cross — The write decision caches holes.** An empty *verified* result on a dateless
  row writes + stamps, locking in a 7-day miss for exactly the rows most likely to be a
  search miss rather than a true absence.

---

## Fix G1 — Option B: orchestrated escalation loop (DECIDED)

**Key billing fact:** `max_uses` is a ceiling, not a target. Anthropic bills per search
*actually performed* (~$0.01 each), so raising the cap does not raise the floor — it only
removes the block. Early-exit therefore already means "pay only until the info arrives."

**Design:** replace phase 1's single call with a loop of up to N rounds. Each round is one
phase-1 call with `max_uses: 1` and a **distinct strategy injected by us** (not left to the
model, whose search behaviour is non-deterministic and cannot be forced). After each round,
read a **found-signal**; stop as soon as it's satisfied, then run phase 2 once.

Strategy ladder (each rung = one round = one new search angle):

| Round | Injected strategy | Purpose |
|---|---|---|
| 1 | given URL + current/next cycle (`site:root <year>`) | the normal case |
| 2 | **+ prior cycle** (`site:root <lastyear> deadline`) | estimation basis (fixes THINK) |
| 3 | + FAQ / "key dates" / timeline subpages | dates hidden on subpages |
| 4 | + third-party, **TRUSTED domains only** (`"<name>" deadline <year>`) | last resort (DECIDED) |

**Rung 4 is restricted to operator-approved trusted domains (DECIDED 2026-08-25).** It draws
only from a domain the operator has approved, using the **same `trusted_aggregators` allowlist
and admin-console Sources tab that `ACTION_ITEMS_TRUST_PLAN.md` defines** — this is now shared
infrastructure across both plans, like the fetch layer. A date sourced only from a rung-4
domain is written `estimated: true` with the source domain named in the note, never as a
confirmed date. A third-party result on a domain NOT in the allowlist is ignored for dates
(it may still be *parked* for the task/aggregator flow, but it never sets a deadline). This
resolves open decision 2 (third-party sourcing): allowed, but trusted-only and always
estimated-and-noted.

**Found-signal (cheap, no extra call):** each phase-1 prose round ends with a small
structured tail, e.g. `FOUND_CONFIRMED_DATES: yes/no` and `FOUND_PRIOR_CYCLE_BASIS: yes/no`.
The loop reads that line to decide whether to escalate — no extra API call, no phase-2 run
per round.

**Cost shape** (early-exit + per-search billing → average barely moves):
- dates on the page → 1 search (~today's cost).
- recurring, needs prior cycle (THINK) → 2 searches (+~$0.01).
- genuinely hard row → up to 4 (+~$0.03), only those rows.
- The one real added cost is **repeated system-prompt tokens per round** — mitigated with
  Anthropic **prompt caching** (system prompt is byte-identical across rounds), wired in with
  Phase 2 (decision 6).

**Consistency to preserve:** keep prose-phase-1 (searches) + single JSON-phase-2 (no tools)
at the end; accumulate `sources` (actually-fetched URLs) across all rounds and pass the
union to phase 2 so it copies real URLs. Keep the existing silent-search retry semantics
within a round. The loop must live in `research_deadlines`/`check_one` so BOTH the batch
path and the interactive endpoint inherit it (they share `check_one`).

---

## Fix G2 — don't give up, don't freeze (PROPOSAL)

Two parts, and the escalation loop from G1 already does half the work.

**(a) Stop giving up.** The escalation ladder *is* the fallback. If round 1 can't reach the
primary site, rounds 2–4 (prior cycle, FAQ, **trusted** third-party) still run — an annual
conference like HSRC surfaces its spring date from a trusted off-site listing even when its own
site is down. Per decision 1, rung 4 draws only from operator-approved trusted domains
(`trusted_aggregators`), and anything sourced only off-domain is written `estimated: true` with
the source domain in the note, never as a confirmed date.

**(b) Stop freezing the hole.** Add a phase-1 signal `SITE_REACHED: yes/no` (did any
`web_fetch`/search of the program's own domain succeed?) and thread it into
`deadline_write_decision`:

- **site reached + genuinely nothing found** → `status=unknown`, empty → this is a *real*
  answer; write + stamp (don't re-bill a true absence). Unchanged from today.
- **site NOT reached, still nothing after escalation** → new outcome
  `SOURCE_UNREACHED = "unreachable-fallback"`: **do not stamp**, leave the row due so the
  next view re-rolls — mirroring the existing `unverified-fallback` / `unparsed-fallback`
  philosophy. This turns a transient network failure into an auto-retry instead of a 7-day
  cached hole.

Open sub-question: whether to also shorten the TTL for a written `unknown` (e.g. re-check
after 1 day instead of 7) rather than the current all-or-nothing stamp. Deferred — see Open
decisions.

---

## Fix G3 — represent "always open" honestly (DECIDED: first-class `rolling` status)

Introduce an explicit rolling/always-open status rather than inventing a fake deadline.

**Server / catalog:**
- Add `"rolling"` to `VALID_STATUS` (`check_deadlines.py:114`). Semantics: the program is
  genuinely always-open with no cycle boundaries (distinct from `running`, which has a cycle
  and a deadline).
- Teach all three prompts (check_deadlines phase-2 extract, `extractTrackerInfo`,
  `intakeExtractAndClassify`) to emit `status=rolling` when the evidence is rolling/continuous
  admission with no published cycle dates — and, in that case, to legitimately return empty
  `important_dates` with a one-line note, instead of forcing an "opens" entry.
- `deadline_write_decision`: `rolling` writes even with zero dates (same carve-out that
  `not_running` already has) — it's a real, student-visible answer.

**Client:**
- `computeProgressStatus` (`status.ts`): map `rolling` → `in_progress` regardless of dates
  (one line at the top, next to the existing `not_running` special-case), so the card reads
  **Happening Now / Open now**.
- Render a rolling badge ("Open now — apply anytime") and suppress the "no dates yet"
  empty-state for rolling items.
- Calendar: rolling has no dated event, so nothing syncs — correct (there is no deadline to
  miss). Do NOT synthesize a today-dated "opens" entry (violates the "never anchor a date to
  today" rule and would drop a junk event on the calendar).

**Ripple to enumerate before building:** every reader of `status` — `cycleYearShift`,
`getUpcomingDeadlineItems`/`getBeyondDeadlineItems`/`getAllDeadlineItems` (which special-case
`not_running`), the mock generators, and any place that switches on the three-value status —
must be checked so `rolling` doesn't fall through to a wrong default.

**Secondary (out of scope for now):** rolling programs may still have recurring *event*
dates (e.g. monthly meetings). Not attempted here; the core fix is representing "open now".

---

## Fix G4 — status-aware overwrite (SUPERSEDED — see Architecture decision above)

**Superseded by the architecture decision:** once Gemini no longer produces dates at all,
there is no client estimate for a verified-empty Claude result to wipe, so this status-gating
is unnecessary. Retained below only for the record / in case the (a) scope is revisited.

Mirror the server's `kept-existing` rule on the client: a verified-but-empty result clears
the client's estimate **only when the status makes emptiness correct**.

- `not_running` (discontinued) → clear (real "no future dates").
- `rolling` (after G3) → clear (genuinely dateless; card reads "open now" anyway).
- `running` / `unknown` + empty → **keep the client estimate** — a verified empty here is far
  more often a search miss than a withdrawal (same reasoning as `deadline_write_decision`'s
  `kept-existing`).

```js
const statusJustifiesEmpty =
  deadlineInfo.status === 'not_running' || deadlineInfo.status === 'rolling';
if (Array.isArray(deadlineInfo.important_dates)
    && (deadlineInfo.important_dates.length || (verified && statusJustifiesEmpty))) {
  info.important_dates = deadlineInfo.important_dates;
}
```

Riders: add `rolling` to the status-accept list at `tracker.ts:106-110`; apply the same
change to the sibling reader `refreshTrackerDeadlines` so the two don't drift. G1 reduces how
often G4 fires (the authoritative check will usually estimate dates rather than return empty),
but the fix is cheap and defensively keeps an honest estimate over a blank field — the app's
own "an estimate beats an empty field" stance.

## Fix G-cross — write-decision redesign (PROPOSAL)

Fold G2(b) into a single revised `deadline_write_decision` so all "empty result" paths are
decided in one place:

| Phase-1 outcome | dates | today | proposed |
|---|---|---|---|
| never searched | — | don't write/stamp (`unverified-fallback`) | unchanged |
| searched, JSON unreadable | — | don't write/stamp (`unparsed-fallback`) | unchanged |
| searched, empty, row HAS dates | keep existing (`kept-existing`) | unchanged |
| **searched, empty, site NOT reached** | write+stamp | **don't stamp (`unreachable-fallback`)** |
| searched, empty, site reached, `unknown` | write+stamp | write+stamp, standard 7-day (DECIDED: real absence, not re-billed every view) |
| `not_running` / `rolling`, empty | write+stamp | unchanged (real answer) |
| dates found | write+stamp | unchanged |

After G1, `status=running` + empty on a reached site should become rare (estimation fills
it), so the main behavioural change here is the `unreachable-fallback` branch.

---

## Unifying mechanism: phase-1 signals

G1 and G2 both hinge on cheap structured signals emitted at the tail of phase-1 prose (no
extra calls):
- `FOUND_CONFIRMED_DATES: yes/no` — drives escalation / early-exit (G1).
- `FOUND_PRIOR_CYCLE_BASIS: yes/no` — whether estimation is possible (G1).
- `SITE_REACHED: yes/no` — drives the `unreachable-fallback` write branch (G2).

## User-added opportunities — deadline & task creation

How a hand-added opportunity gets dates and tasks, and why the architecture decision makes it
identical to a Fresh Finds add.

**Flow today.** The Quest Log "Add Opportunity" form posts to
`/api/user-submitted-opportunities`, which runs **inline** and returns a resolved
`catalogId`:
- **Deduped into an existing row** → returns that row's id (may already carry a verified,
  cached answer — a free hit).
- **Genuinely new** → writes an `is_active=false`, `source='user-submitted'` row and returns
  its new id.
- **Unresolvable** → returns `id: null`.

The tracker item is created under the returned id, so both endpoints resolve by id (**neither
filters on `is_active`**, unlike `/api/opportunities`): `getDeadlineCheck(id)` runs the same
cached/on-demand Claude deadline check, and `getActionItems(id)` runs the same cached/on-demand
Claude task check. **So a user-added opportunity uses the identical Claude pipelines as a
catalog add** — this is the whole reason the submission endpoint was made to return an id.

**The one gap, and how the TTL closes it.** The batch agents select `is_active=eq.true`, so
they **never re-touch** an inactive user-added row. Today that means a user-added row gets
exactly one on-demand attempt ever; a transient `generic-fallback` / unreachable result is
frozen until an operator activates the row. The **7-day TTL on both endpoints** (stamp only on
success) closes this: a failed attempt leaves the row due, so the next "Check for updates" or
re-open re-rolls it. This is the same failure-handling rule the deadline path already uses,
now extended to tasks — it is what makes user-added rows self-heal without operator action.

**`id: null` (unresolvable).** No catalog row exists, so `getDeadlineCheck`/`getActionItems`
404. Handled honestly: the deadline refresh counts a 404 as *skipped* (not failed — there is
nothing to check), and tasks fall back to a **static per-type generic checklist built
client-side** (no model call) after the Tier-2 drop. Such a row shows no confirmed dates and
only generic tasks until it is resolved/activated — correct, because nothing has read a page.

## "Check for updates" — now refreshes deadlines AND tasks

**Requirement:** the Quest Log's "Check for updates" button must refresh both deadlines and
tasks. It is **partly built already** and partly blocked by the missing task TTL.

**Today** (`refreshTrackerDeadlines`, `trackerStore.ts`): per tracked item it runs the deadline
check, and *then* re-pulls the shared checklist (`getActionItems`) and `mergeActionItems` —
so tasks DO refresh. But two things stop it being a real task re-check:
1. **Coupled to the deadline check.** The task re-pull sits after the deadline check's
   `continue`, so if the deadline cache is fresh (skip), tasks are not re-pulled at all — even
   if the task list is stale.
2. **No fresh verification.** `getActionItems` → `resolve()` serves the stored list *forever*
   (no TTL), so the re-pull only ever surfaces changes the **batch agent** already made; it
   never triggers a fresh on-demand task re-verification.

**Change (depends on the task 7-day TTL):**
- **Decouple** the two checks in the refresh loop: run the deadline check and the task check
  independently, each honouring **its own** staleness window, so a fresh deadline no longer
  suppresses a stale task re-check (and vice versa).
- Once `resolve()` has a TTL, a stale item's `getActionItems` triggers a **fresh Claude task
  verification** — which is exactly "check for task updates".
- **Report them distinctly.** Extend the result counts so the UI can say "N deadlines updated,
  M tasks updated" instead of one blended `updated`. The four-outcome accounting
  (checked/updated/skipped/blocked/failed) applies to each stream.
- **Cost note:** a stale item now triggers up to **two** Claude calls (deadline + task) on a
  refresh. Consistent with the "Claude owns both core features" decision; surfaced here so it
  is not a surprise.

## Future: per-user task delete & user-added tasks (design, not this pass)

Both are **per-user** (this student only, never the shared catalog), and both live entirely in
the per-user tracker item (`users.data` → `hs-tracker-data` → `item.actionItems`) — **no
catalog schema change**. The catalog `opportunities.action_items` stays the shared, regenerated
source of truth; the per-user item is the source of truth for what the student has *done* with
it. `mergeActionItems` already reconciles the two on the **task text** key (ids are positional
and unstable). The two features extend that reconciliation.

**The central constraint:** `mergeActionItems` today maps over `incoming` (the catalog list)
only, so **anything not in the incoming list is dropped on refresh**. That is fine for
completion state but fatal for both features below — a user-added task or a per-user deletion
must *survive* regeneration. So both hinge on extending the merge, not on new storage.

**Per-user task delete.** There is precedent: the retired `dismissed` flag became the
`not_needed` **state** (visible, reversible, excluded from the progress bar). A true *delete*
(hide entirely) needs a **tombstone** so the regenerated catalog list doesn't re-add it:
- Record removed tasks by text-key on the item (a `removedTaskKeys` set, or a `removed` state).
- `mergeActionItems` drops an incoming catalog task whose key is tombstoned.
- Keyed on text for the same reason completion is — positional ids can't be trusted.
- Reversible (un-delete = clear the tombstone), per the same "listened-to" principle that
  turned dismiss into `not_needed`.

**User-added tasks.** A task the student writes themselves:
- New field `origin?: 'catalog' | 'user'` on `ActionItem` (absent ⇒ `catalog`, the back-compat
  rule). A stable client-generated id, `basis` absent (never page-backed — nothing verified
  it), rendered in its own group ("Your own tasks").
- **`mergeActionItems` must be extended** to append surviving `origin:'user'` tasks from
  `existing` that have no catalog match, instead of dropping them. This is the one real code
  change; everything else is UI + the field.
- Never written back to the catalog row (it is this student's, not everyone's) and never sent
  to the calendar unless the student gives it a date (out of scope for v1).

Both compose cleanly with the trust tiers in `ACTION_ITEMS_TRUST_PLAN.md`: `origin:'user'` is
simply another source that is never page-backed, sorted below verified/trusted and above
nothing. Neither feature is in the current pass; captured here so the merge change is designed
with them in mind rather than retrofitted.

## Decisions (ALL RESOLVED 2026-08-25)

1. **Ladder depth — DECIDED (2026-08-25): 4 rungs, rung 4 TRUSTED-domains-only.** Off-domain
   escalation is allowed but only from operator-approved domains in the shared
   `trusted_aggregators` allowlist, maintained in the admin-console Sources tab (shared with
   `ACTION_ITEMS_TRUST_PLAN.md`). Rung-4 dates are always `estimated:true` + source-noted.
2. **Third-party sourcing — DECIDED (folded into decision 1):** allowed, trusted-only, always
   estimated-and-noted, never confirmed. A non-trusted domain never sets a deadline.
3. **Unknown TTL — DECIDED (2026-08-25): standard 7 days.** A genuine `unknown` (site reached,
   all rungs searched, nothing found) is treated like any verified answer — write + stamp,
   7-day cache. No per-status TTL. Only the *unreachable* case (network failure) skips the
   stamp and auto-retries; a real absence does not, to avoid re-billing a dateless row on
   every view.
4. **`rolling` status — DECIDED (2026-08-25): first-class status.** Add `"rolling"` to
   `VALID_STATUS`, prompts emit it, `computeProgressStatus` maps it to `in_progress`. The Fix
   G3 section is the spec; the status-reader ripple list there MUST be enumerated during build.
5. **(G4 — now has a proposed fix above.)** Confirm the status-aware overwrite: keep the
   client estimate on `running`/`unknown` + verified-empty, clear only on
   `not_running`/`rolling`. Alternative if you'd rather not preserve unverified guesses at
   all: keep today's behaviour and rely solely on G1 to stop the authoritative check from
   returning empty.
6. **Prompt caching — DECIDED (2026-08-25): wire in with Phase 2.** Add `cache_control` to the
   escalation loop's system prompt so repeated rounds pay minimal token cost.
7. **Task TTL length — DECIDED (2026-08-25): 7 days on-demand**, matching deadlines. The
   batch's 90-day staleness stays a separate bulk-pre-warm knob (see the reconcile note above).
8. **Fetch fix C (Playwright) — DECIDED (2026-08-25): DEFER.** Do not build Playwright now.
   SPA official pages (THINK's own page) stay unreadable; their dates are recovered instead via
   the escalation loop's prior-cycle and trusted-third-party rungs (decision 1). Revisit only
   if SPA-only sites prove common. Also resolves that doc's decision 6 the same way. Lightweight
   fetch fixes A (PDF) + B (same-domain link discovery) are NOT part of this decision and were
   not requested — treat them as optional low-cost future wins, not current scope.

## Risks & non-goals

- **Fabrication risk rises with ladder depth — mitigated by the trusted-only rule (decision
  1).** Off-domain dates are the classic failure mode; rung 4 draws only from the operator's
  `trusted_aggregators` allowlist and its dates are always estimated-and-noted, never
  confirmed. A non-trusted domain never sets a deadline.
- **Non-determinism remains.** The loop makes the *strategy sequence* deterministic, but the
  model still decides whether to actually search within a round; keep silent-search retry.
- **Status ripple (G3).** A new status value touches many client readers; enumerate before
  building.
- **Non-goal:** action-item creation is working for all three rows and is out of scope for
  this pass.
- **Non-goal:** recurring event-date extraction for rolling programs.

## Tentative touch list (subject to plan approval)

Deadlines / status (G1–G3, G-cross):
- `check_deadlines.py` — escalation loop in `research_deadlines`/`check_one`; `MAX_SEARCHES`
  → per-round budget + ladder; phase-1 signal tail; `VALID_STATUS` (+`rolling`);
  `deadline_write_decision` + `SOURCE_UNREACHED`; phase-2 prompt (rolling).
- `app/routes/opportunities.py` — inherits `check_one`; confirm the `source`/stamp handling
  for the new outcomes; TTL if decision 3 changes it.
- `frontend/src/lib/status.ts` — `rolling` in `computeProgressStatus` + the deadline-list
  readers.

Tasks → Claude (architecture decision):
- `generate_action_items.py` — swap `call_gemini`→`call_claude` (Haiku 4.5 pin); cost via
  `claude_common.estimate_cost`; keep `page_text` fetch + `claim_is_supported`/
  `quote_is_on_page` verification unchanged.
- `app/services/action_items.py` — gate on `ANTHROPIC_API_KEY`; add the 7-day (or agreed)
  staleness check on `action_items_checked_at`.
- cost attribution — `classify_feature` signature for action items; import the Claude model
  pin so `provider_for_model` attributes Anthropic.

Client — collapse the redundant Gemini producer:
- `frontend/src/lib/tracker.ts` — slim `extractTrackerInfo` to `meta`/`fit` (drop its date &
  task outputs); `intakeExtractAndClassify` still classifies custom adds; `rolling` in the
  status-accept list; the empty-verified overwrite guard becomes moot (G4 superseded).
- `frontend/app/(app)/finder.tsx` + `tracker.tsx` — dates from `getDeadlineCheck`, tasks from
  `getActionItems`, `applyUrl = opp.url` (static label); stop trusting Gemini dates/tasks.
- rendering (Quest Log / Home Base) — rolling badge + empty-state suppression.
- tests — `test_check_deadlines_helpers.py` (write-decision matrix), `test_action_items.py`
  (Claude path), status logic, mocks.

**Cross-doc:** several of these files also appear in `ACTION_ITEMS_TRUST_PLAN.md`
(`generate_action_items.py`, `app/services/action_items.py`, `tracker.ts`). Sequence the two
so the Gemini→Claude move lands before/with the aggregator work, not against it.

## Phased implementation plan

Each phase is shippable on its own and ordered so dependencies land first. Phases 0–1 are the
task-engine foundation; 2–4 are deadline quality; 5–6 collapse the redundant producer and wire
the refresh; 7 is a future per-user layer. The shared fetch-layer track (A/B/C) runs alongside.

**Phase 0 — Tasks move to Claude (foundation).** _(build-order step 0, shared with
`ACTION_ITEMS_TRUST_PLAN.md`)_
- `generate_action_items.py`: `call_gemini` → `call_claude` (Haiku 4.5 pin); cost via
  `claude_common.estimate_cost`. Keep `page_text` fetch + `claim_is_supported`/
  `quote_is_on_page` verification untouched.
- `app/services/action_items.py`: gate on `ANTHROPIC_API_KEY`; keep the free `generic-fallback`.
- Cost attribution: `classify_feature` signature for action items; import the Claude model pin
  for `provider_for_model`.
- **User-visible change:** none beyond model quality. **Tests:** `test_action_items.py` on the
  Claude path.

**Phase 1 — Task caching TTL + failure handling.**
- `resolve()`: add a staleness check on `action_items_checked_at` (length per decision 7);
  **stamp only on a successful run, no stamp on failure** (leave the row due to auto-retry).
- Closes the "user-added inactive rows never retried" gap; enables real task re-checks in
  Phase 6.

**Phase 2 — Deadline estimation: G1 escalation loop.**
- `check_deadlines.py`: escalation loop in `research_deadlines`/`check_one`; per-round
  `max_uses:1` + strategy ladder; `FOUND_*` signal tail; accumulate `sources` across rounds;
  prompt caching (decision 6). Batch + interactive inherit via `check_one`.
- **Rung 4 (trusted third-party) depends on the shared `trusted_aggregators` allowlist** (see
  Parallel track). Ship rungs 1–3 first; gate rung 4 on the allowlist existing, so a filter
  reads the operator-approved domains and drops any off-domain date not on it. Until the
  allowlist exists, the loop simply runs rungs 1–3 (degrade-not-break).
- **Tests:** re-run `--ids ec17921 ec18286 ec18392 --preview`; confirm THINK gains estimated
  dates; watch escalation-depth / silent-search counters for early exit.

**Phase 3 — Deadline write-decision: G2 + G-cross.**
- `SITE_REACHED` signal; `SOURCE_UNREACHED` outcome (don't stamp); revised
  `deadline_write_decision` matrix; per-status unknown TTL if decision 3 changes it.
- **Tests:** the full write-decision matrix in `test_check_deadlines_helpers.py`.

**Phase 4 — Rolling status: G3.**
- `VALID_STATUS` + `rolling`; teach the three prompts; write-decision carve-out (rolling writes
  empty). Client: `computeProgressStatus` maps `rolling` → `in_progress`; enumerate every
  `status` reader (ripple list above); rolling badge + empty-state suppression.

**Phase 5 — Collapse the redundant Gemini producer (client).**
- Slim `extractTrackerInfo` to `meta`/`fit` (drop its date & task outputs); `applyUrl = opp.url`
  + static label. `finder.tsx`/`tracker.tsx`: dates from `getDeadlineCheck`, tasks from
  `getActionItems`. `id:null` → static client generic checklist. **G4 becomes moot** here.

**Phase 6 — "Check for updates" refreshes both, independently.**
- Decouple the deadline and task checks in `refreshTrackerDeadlines` so each honours its own
  TTL; report deadline vs task updates as distinct counts; surface "N deadlines, M tasks
  updated". Depends on Phase 1 (task TTL) and Phase 5 (single source per stream).

**Phase 7 — Per-user task delete & user-added tasks (FUTURE, separate).**
- `ActionItem.origin`; extend `mergeActionItems` to preserve `origin:'user'` tasks and honour
  per-user tombstones; removal state/undo; add/delete UI. No catalog schema change.

**Parallel track — shared infrastructure (with `ACTION_ITEMS_TRUST_PLAN.md`).**
- **Trusted-domain allowlist** — `trusted_aggregators` table + `aggregators_common.py` +
  admin-console Sources tab (approve/block/park). Defined in `ACTION_ITEMS_TRUST_PLAN.md`;
  now **also gates the deadline loop's rung 4** (decision 1). Build once, shared. Deadline
  rung 4 needs only the read side (which domains are trusted); the task side needs the full
  park-and-approve flywheel.
- **Fetch layer** — **C (Playwright SPA render): DEFERRED (decision 8).** SPA official pages
  stay unreadable; dates come from the escalation loop's trusted third-party rung instead.
  **A (PDF)** + **B (same-domain link discovery)** are optional low-cost future wins, not
  current scope (not requested in decision 8).

**Dependency order:** 0 → 1 → 2 → 3 → 4 → 5 → 6, then 7 later. A/B parallel from Phase 1; C
gated. Deadline quality (2–4) and the task foundation (0–1) are independent until Phase 5,
so they can proceed in parallel if two people are working.

## Testing considerations

- Unit-test the revised `deadline_write_decision` matrix (every row of the table above).
- Re-run the three traced rows (`--ids ec17921 ec18286 ec18392`, `--preview` first) and
  confirm: THINK gains estimated dates, HSRC either recovers off-site dates or is left due
  (not frozen), KCLS reads rolling/open-now.
- Watch the escalation depth + silent-search counters in the run summary to confirm early
  exit is actually happening (cost guard).
