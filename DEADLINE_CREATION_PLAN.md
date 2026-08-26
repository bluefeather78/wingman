# Deadline creation — gap analysis and fix plan

**Status:** planning (do NOT implement yet — this doc is being reviewed).
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
- **Staleness.** That plan uses **90-day** batch staleness and serve-forever on-demand,
  reasoning "requirements don't move weekly." This decision introduces a **7-day** on-demand
  TTL. As written they now disagree (7-day on-demand vs 90-day batch). Options to resolve:
  (i) adopt 7-day everywhere; (ii) keep tasks on a longer TTL than deadlines (they change far
  less) and adopt only the *stamp-on-success / no-stamp-on-failure* half of this decision —
  which is the clearly-correct part regardless of TTL length. **Flagged for the operator; the
  failure-handling rule is adopted now, the exact TTL length is an open decision (below).**

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
  nothing off the official page at all. The escalation loop partly routes around this — its
  prior-cycle and third-party rounds land on *other*, server-rendered pages — but the only
  fix that reads THINK's own page/PDF is the shared **Playwright headless render (fetch fix
  C)**. See Open decisions / that doc's decision 6.
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
| 4 | + broader / third-party (`"<name>" deadline <year>`) | last resort (see risk) |

**Found-signal (cheap, no extra call):** each phase-1 prose round ends with a small
structured tail, e.g. `FOUND_CONFIRMED_DATES: yes/no` and `FOUND_PRIOR_CYCLE_BASIS: yes/no`.
The loop reads that line to decide whether to escalate — no extra API call, no phase-2 run
per round.

**Cost shape** (early-exit + per-search billing → average barely moves):
- dates on the page → 1 search (~today's cost).
- recurring, needs prior cycle (THINK) → 2 searches (+~$0.01).
- genuinely hard row → up to 4 (+~$0.03), only those rows.
- The one real added cost is **repeated system-prompt tokens per round** — mitigate with
  Anthropic **prompt caching** (system prompt is byte-identical across rounds).

**Consistency to preserve:** keep prose-phase-1 (searches) + single JSON-phase-2 (no tools)
at the end; accumulate `sources` (actually-fetched URLs) across all rounds and pass the
union to phase 2 so it copies real URLs. Keep the existing silent-search retry semantics
within a round. The loop must live in `research_deadlines`/`check_one` so BOTH the batch
path and the interactive endpoint inherit it (they share `check_one`).

---

## Fix G2 — don't give up, don't freeze (PROPOSAL)

Two parts, and the escalation loop from G1 already does half the work.

**(a) Stop giving up.** The escalation ladder *is* the fallback. If round 1 can't reach the
primary site, rounds 2–4 (prior cycle, FAQ, third-party) still run — an annual conference
like HSRC surfaces its spring date from an off-site search even when its own site is down.
Anything sourced only off-domain is written `estimated: true` with the basis in the note,
never as a confirmed date.

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

## Fix G3 — represent "always open" honestly (PROPOSAL)

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
| searched, empty, site reached, `unknown` | write+stamp | write+stamp (real absence) — _or_ short TTL (open) |
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

## Open decisions (need input)

1. **Ladder depth / N.** Cap escalation at 3 (own-domain only, safest) or 4 (include
   third-party, more coverage, higher fabrication risk)?
2. **Third-party sourcing.** Allow round-4 off-domain dates at all? If yes, force
   `estimated:true` + source note, never confirmed. (This repo has been burned by
   model-typed / off-site URLs before.)
3. **Unknown TTL.** For a written `unknown` (site reached, real absence), keep the 7-day
   cache, or re-check sooner (e.g. 1 day)? Needs a way to express a per-status TTL.
4. **`rolling` as a first-class status** vs. a client-only rendering heuristic off the note.
   First-class is cleaner but ripples through every status reader.
5. **(G4 — now has a proposed fix above.)** Confirm the status-aware overwrite: keep the
   client estimate on `running`/`unknown` + verified-empty, clear only on
   `not_running`/`rolling`. Alternative if you'd rather not preserve unverified guesses at
   all: keep today's behaviour and rely solely on G1 to stop the authoritative check from
   returning empty.
6. **Prompt caching** for the escalation loop — wire it in now or defer?
7. **Task TTL length.** The stamp-on-success / no-stamp-on-failure rule is adopted. The TTL
   *length* is open: 7 days (matches deadlines, re-bills weekly) vs a longer window (tasks
   change far less than dates — the `ACTION_ITEMS_TRUST_PLAN.md` 90-day rationale). Must be
   reconciled with that plan's batch staleness so on-demand and batch don't disagree.
8. **Fetch fix C (Playwright).** Shared with `ACTION_ITEMS_TRUST_PLAN.md` (its decision 6).
   The only fix that reads SPA-only official pages (THINK's own page + dates). Heavy dep,
   departs from stdlib-only agents. Build or not?

## Risks & non-goals

- **Fabrication risk rises with ladder depth.** Off-domain dates are the classic failure
  mode; keep them estimated-and-noted, never confirmed.
- **Non-determinism remains.** The loop makes the *strategy sequence* deterministic, but the
  model still decides whether to actually search within a round; keep silent-search retry.
- **Status ripple (G3).** A new status value touches many client readers; enumerate before
  building.
- **Non-goal:** action-item creation is working for all three rows and is out of scope for
  this pass.
- **Non-goal:** recurring event-date extraction for rolling programs.

## Tentative touch list (subject to plan approval)

- `check_deadlines.py` — escalation loop in `research_deadlines`/`check_one`; `MAX_SEARCHES`
  → per-round budget + ladder; phase-1 signal tail; `VALID_STATUS`; `deadline_write_decision`
  + `SOURCE_UNREACHED`; phase-2 prompt (rolling).
- `app/routes/opportunities.py` — inherits `check_one`; confirm the `source`/stamp handling
  for the new outcomes; TTL if decision 3 changes it.
- `frontend/src/lib/status.ts` — `rolling` in `computeProgressStatus` + the deadline-list
  readers.
- `frontend/src/lib/tracker.ts` — `extractTrackerInfo` / `intakeExtractAndClassify` prompts
  (rolling); possibly the empty-verified overwrite guard (decision 5).
- rendering (Quest Log / Home Base) — rolling badge + empty-state suppression.
- tests — `test_check_deadlines_helpers.py` (write-decision matrix), status logic, mocks.

## Testing considerations

- Unit-test the revised `deadline_write_decision` matrix (every row of the table above).
- Re-run the three traced rows (`--ids ec17921 ec18286 ec18392`, `--preview` first) and
  confirm: THINK gains estimated dates, HSRC either recovers off-site dates or is left due
  (not frozen), KCLS reads rolling/open-now.
- Watch the escalation depth + silent-search counters in the run summary to confirm early
  exit is actually happening (cost guard).
