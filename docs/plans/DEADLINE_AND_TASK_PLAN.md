# Deadline & Task creation — coverage and accuracy (MAIN PLAN)

**Status:** **P0–P6 + T6 SHIPPED (backend complete through the substrate).** P0–P4 (2026-08-25);
**P5** trusted-domain allowlist + rung 4 (proven live); **REPLAN** — decision 6 superseded, both
features unified onto ONE fetch+verify SUBSTRATE (§5a); **P6a** capture viability (T8 resolved);
**P6b** tasks on the substrate (THINK proven live — real PDF-derived verified tasks, no
fabrication); **P6c** date-verify analogue (per-date `verified`/`source_url`); **T6** shared
program-source finder — read-once + FAQ/sub-page discovery for tasks (proven live on THINK, a
tier-merge bug found & fixed in-session). A free tracker SYNC layer (`/api/tracker/sync`) sits on
top. **1045 pytest green, tsc clean, ~$0.70 of live proofs this session, zero DB writes.**

**ALL PHASES SHIPPED — P0–P11 COMPLETE (P10+P11 on 2026-08-26, commit e68a230).** The full
plan is live: substrate + trust tiers end-to-end, frontend gradient, producer collapse,
decoupled refresh, per-user task delete/user-added tasks, calendar "Open now" band — plus the
`not_running` evidence gate added from the live ec18599 case. Everything verified in-browser
(the paid E2E on THINK $0.199; the ec18599 corrective re-check $0.081; P10/P11 free against
real catalog sync data). **Remaining follow-ups are outside the phased plan:** task
TIMELINESS (page-verified tasks can carry past dates), the §13 coverage/measurement layers
(F1 change-detection/reminders, F2 accuracy harness), and the deliberately-deferred proactive
coverage (item 1).

**GAP-HUNT FOLLOW-UP SHIPPED 2026-08-28 (origin/main `14e0b81`).** The 2026-08-27 gap-hunt's fixes
are live: **G6a** (today-anchored deadline dates demoted to estimates) and the **sitemap-first
discovery series D0–D5** (`sitemap_common.py`: a free stdlib helper that reads a program's own
sitemap to reach its real steps/dates pages, wired into BOTH the task capture and the deadline
ladder behind the `web_search` fallback; plus a shallow-capture no-stamp guard). Validated live
read-only (D4 $0.178, D5 $0.236); 4 real task rows written live ($0.174). Code-only — no schema/env
changes. **Only two threads remain, both deferred + paid: G6b staleness detection, and a wider
proactive writing pass.** See the ✅ section immediately below and §9 D0–D5 / §3 G6.
**Owner:** _tbd_  **Started:** 2026-08-25  **Branches:** `P5-P7-deadline-and-task-tracker` (P0–P11),
`sitemap-discovery-g6a` → merged to main (G6a + D0–D5)

---

### ✅ GAP-HUNT FOLLOW-UP — SHIPPED TO MAIN 2026-08-28 (G6a + D0–D5)

A live gap-hunt on real rows surfaced three findings and one chosen build. **All of it is now BUILT,
VALIDATED, and MERGED TO MAIN (origin/main `14e0b81`) — Render deploys from main; no schema/env
changes needed.** G6a + the full sitemap-first discovery series (D0–D5) shipped; D4/D5 validated live
read-only; 4 real task rows were written live. **The ONLY remaining open items are the two
deliberately-deferred, paid, operator-approval-gated threads:** G6b staleness detection, and a wider
proactive WRITING pass (§13 item 1). 1315 pytest green on the merged result. Full detail in the
change log (2026-08-28 entries), the section anchors named, and §9 for the phased build.

| # | Finding (live row) | Where | Status |
|---|---|---|---|
| **G6a** | Today-anchored unverified `opens` reached the student (Tisch ec17543). | §3 **G6a** | ✅ **BUILT** — `verify_dates_against_capture` demotes an `estimated:false, verified:false` date equal to the check date to `estimated:true` + note. |
| **G6b** | `verified:true` cannot detect a STALE/pre-JS `web_fetch` capture, so it can be confidently wrong. | §3 **G6b** | OPEN — needs rendered re-fetch / thin-shell detection (paid or SPA-render); DEFERRED. |
| **G-task-1** | Rich steps page never discovered — Congressional Award (ec18244): 2 throwaway tasks while `/the-program/` carries a full verifiable step list. | §4 **G-task-1a/b/c** | ✅ **ADDRESSED** by D1/D2 (discovery) + D3 (no-stamp); durable fix pending PAID D4 validation on the real row. |
| **G-D1 / D0–D3** | **Sitemap-first page discovery** (shared free helper) + shallow-capture no-stamp. | §9 D0–D3 | ✅ **BUILT** — `sitemap_common.py` + fixtures/tests (D0/D1), wired into `fetch_and_capture` behind the `web_search` fallback (D2), furniture no-stamp signal (D3). |
| **D4** | PAID validation on 4 real rows. | §9 D4 | ✅ **DONE 2026-08-28 ($0.1781, read-only)** — proof row ec18244 fixed (furniture CTA → real $35-fee/Record-Book tasks); 0→3 page-backed on ec18676; no-sitemap control OK; SLIYS demotion-rate signal noted. A ranker bug was caught & fixed FREE first. |
| **D5** | Deadline-ladder adoption + optional backfill. | §9 D5 | ✅ **CODE DONE + VALIDATED 2026-08-28 ($0.2358, read-only)** — sitemap fires + reaches site on all 3, no regression; search-count drop did NOT show on off-season rows (climb to rung 2 for estimation regardless), so benefit here is recall not cost. Optional backfill still deferred. |

**Remaining open work (both PAID + operator-approval-gated; nothing else in this series is open):**
**(a)** a wider proactive WRITING pass to improve more of the catalog — D4/D5 validation was
read-only and only 4 task rows were written live, so most rows still serve old cached data until a
writing run or TTL lapse (this is the G-task-3 / §13 item 1 backfill, deferred until you un-defer
it); ~$0.044/row tasks, ~$0.079/row deadlines. **(b)** **G6b** — staleness detection (rendered
cross-fetch / thin-shell), orthogonal to everything above: D-series gets to the RIGHT page, G6b
handles a STALE `web_fetch` of it (the Tisch ec17543 confidently-wrong-date case).

---

> **This is the single source of truth for the deadline creator and the task creator.**
> It merges and supersedes `../archive/DEADLINE_CREATION_PLAN.md` and `../archive/ACTION_ITEMS_TRUST_PLAN.md` — both
> are retained only as history and carry a banner pointing here. Edit THIS doc going forward.

## HANDOFF — read this first in a new thread

Self-contained; you do not need the originating conversations. Two features share one design:
**deadlines** (dates → Quest-Log milestones + calendar events) and **tasks** (the application
checklist). Both are being moved to Claude, both are on-demand + 7-day cached, both draw on a
shared operator-controlled trusted-domain allowlist, and both were failing on the same live
row (THINK Scholars, a JS SPA). The work is phased at the bottom. **As of 2026-08-26 the two
features share ONE fetch+verify substrate (§5a / decision 6) — the spine of the design; read it
first.** The deadline gaps (G1–G4) and the task-trust tiers (aggregators) are fully designed and
**every operator decision is settled**; only technical-design unknowns (T1–T8) remain, resolved
at build time.

---

## 1. The unified architecture (the one-screen model)

Both features resolve the same way: **one authoritative, cached, on-demand endpoint per data
type, generated by Claude, verified where verification is possible, and self-healing on
failure.**

| Data | Authoritative endpoint (cached + on-demand) | Model | Verification |
|---|---|---|---|
| dates / status | `/api/opportunities/<id>/deadline` | **Claude Haiku 4.5** | web_search two-phase, escalation loop |
| action items | `/api/opportunities/<id>/action-items` | **Claude Haiku 4.5** | code-side quote check (`page_text`) |
| meta / fit | `extractTrackerInfo` (client) | Gemini (slimmed) | none needed (descriptive only) |
| apply url | static `opp.url` | — | link-checked by `check_links.py` |

**Architecture decisions that govern both features (confirmed 2026-08-25; decision 6
superseded 2026-08-26):**

1. **Claude (Haiku 4.5) owns both tasks and deadlines** — the product's two core surfaces,
   promoted off `gemini-3.5-flash-lite` for reasoning quality. Cost via
   `claude_common.estimate_cost`; per-user attribution flips these to Anthropic
   (`classify_feature` signature + model pin for `provider_for_model`).
2. **On-demand, cached, 7-day TTL, stamped only on a successful run.** A failed / unreachable /
   unparsed run writes **no stamp**, leaving the row due so the next view auto-retries. This is
   the deadline model; tasks now adopt it (they served a stored list forever before).
3. **Collapse the redundant client producer.** `extractTrackerInfo` (Gemini) used to re-derive
   BOTH dates and tasks that the two endpoints already produce. It is slimmed to `meta`/`fit`
   only; dates come from the deadline endpoint, tasks from the action-items endpoint,
   `apply_url` from the static `opp.url`. (This is what resolves deadline gap **G4** — with no
   client date estimate, there is nothing for a verified-empty result to wipe.)
4. **A shared, operator-controlled trusted-domain allowlist** (`trusted_aggregators` + console
   Sources tab) governs every off-domain source: the deadline loop's 4th search rung AND the
   task aggregator tier both draw only from it. Build once, shared.
5. **Verification ≠ trust, kept orthogonal.** Code proves *this source said this*
   (`claim_is_supported` / `quote_is_on_page` for tasks; grounding-resolved URLs for dates).
   The trust tier answers *how authoritative is this source* — an operator policy. An
   aggregator quote can verify perfectly and still be wrong, so aggregator content is verified
   the same way but carries a lower tier, is labelled by source, and **may back logistics,
   never eligibility** (see §5).
6. **One shared fetch+verify SUBSTRATE; two feature-specific EXTRACTS.** *(Revised 2026-08-26 —
   supersedes the earlier "two separate calls, task must not search". See §5a and the change
   log.)* Both features stand on ONE substrate, and only the extract differs:
   - **Shared discovery + fetch** — Claude `web_search` / `web_fetch` (the escalation
     "program source finder", §3/§5). This is the fetcher for BOTH, chosen because it reads
     PDFs, JS-rendered SPAs, and 403/TLS-walled pages that our stdlib `urllib` fetcher cannot
     — the exact pages tasks were falling back to generic on. THINK's guidelines PDF, proven
     live 2026-08-26, is read this way.
   - **Shared content capture** — the fetched TEXT of every `web_search_tool_result` /
     `web_fetch_tool_result` block, not just its URL. Today the deadline agent keeps only the
     URLs (`extract_source_urls`); the substrate additionally keeps the content, which is the
     "page text we hold locally" the verifier needs.
   - **Shared code-side verification** — the extracted fact must appear in the captured
     content: tasks via `page_text.quote_is_on_page` / `claim_is_supported`, dates via the
     same-shaped date analogue (new — brings deadlines UP to the task standard, rather than
     tasks down to none). Model and verifier see the SAME captured blocks, so `web_fetch`
     truncation causes no false demotion.
   - **Shared trust tiers** (§5) — every captured source is classified by domain (official /
     trusted / pending / blocked). This is what makes a shared search-fed fetch safe: a claim
     verified only against a non-official domain is tier-limited (logistics, never
     eligibility), so the third-party-quote risk is gated, not trusted.
   - **Per-feature EXTRACT only** — a search-OFF JSON pass over the captured content: dates
     for one, tasks for the other.

   **Why this replaces "task must not search".** The anti-fabrication guarantee was never
   search-OFF itself — it was CODE verification against text we hold. Capturing Claude's fetch
   content preserves that guarantee (an invented "Algebra 2" is not in the captured blocks, so
   `quote_is_on_page` still fails it) while using a fetcher strong enough to read the real
   source. Verification ≠ trust (decision 5) is what closes the remaining gap.

   **Still two extracts, two caches.** `dates_last_checked_at` vs `action_items_checked_at`
   stay independent. But ONE shared fetch pass populates both, so the combined cost is a single
   paid discovery/fetch plus two cheap search-OFF extracts — *cheaper* than two independent
   search passes, which is why unifying is efficient, not merely tidy. The client still fires
   both together on add / open / refresh.

   **Open build question (T6).** Whether the shared fetch is literally ONE pass whose
   stop-condition covers both date-bearing AND requirement-bearing pages, or two coordinated
   passes over shared machinery: a date-optimized early-exit (stop as soon as dates are found)
   can under-fetch the requirements page a task needs. Resolve at build time (§8, T6).

**The shared root cause that started both threads — and why the fix is the substrate.** THINK
Scholars (`ec17921`, `think.mit.edu`) is a client-side-rendered SPA: the server returns ~490
bytes (a `<title>` and a JS bundle), and its guidelines PDF link is JS-injected. **Two
different fetchers gave two different outcomes, and that IS the finding:**
- The **deadline** agent uses Claude's server-side `web_search` / `web_fetch`, which reads the
  static guidelines PDF fine (proven live 2026-08-26 — its dates were recovered from
  `THINK_Program_Guidelines_2026.pdf`). The deadline gap was never the SPA; it was **G1** (only
  one search round ran, so the prior-cycle PDF was never fetched), fixed by P2's escalation loop.
- The **task** agent uses our stdlib `page_text` / `urllib`, which rejects the SPA (~490 bytes
  → `empty-or-js`) AND the PDF (`Content-Type: application/pdf` → `not-html`) — so it reads
  **nothing** and falls back to a generic list, while the richest requirement content sits in a
  PDF Claude's fetcher already reads.

So the two features diverged only because they stood on two different fetchers of unequal
capability. **Decision 6 (revised 2026-08-26) unifies them onto Claude's fetcher** — the task
agent reads the same PDF the deadline agent does, and both verify in code against the captured
content. This makes **fetch fixes A (pypdf) and C (Playwright) largely moot for READING** (Claude
does it server-side); they survive only as a possible local fallback, and stay deferred.

---

## 2. How creation works today (the pipeline)

1. A student adds/opens a tracked opportunity. Today the client runs `extractTrackerInfo`
   (Gemini) for an immediate guess of everything (to be slimmed — decision 3).
2. **Dates:** `getDeadlineCheck(id)` → `/deadline` → `check_deadlines.check_one()` (Claude),
   7-day cached (`dates_last_checked_at`). `check_one` is **two-phase**: phase 1 prose,
   tools ON; phase 2 strict JSON, tools OFF — because demanding JSON collapses the search rate.
   `deadline_write_decision()` (shared by batch + interactive) decides whether to overwrite and
   whether to stamp.
3. **Tasks:** `getActionItems(id)` → `/action-items` → `resolve()` serves the stored list, else
   runs the batch pipeline once and caches. Tasks are page-fetched by us (`page_text`) and each
   task's quote is verified against that page in code; a task is `page` (verified specific
   claim) or `generic` (asserts nothing). `action_items_write_decision()` is the shared
   write gate.
4. The client overlays the authoritative results onto the tracker item stored per-user in
   `users.data` (`hs-tracker-data`).

---

## 3. Deadline creation — gaps & fixes

### Evidence (live, 2026-08-25) — three rows, three different failures, all zero dates

| Row | id | status | why empty |
|---|---|---|---|
| THINK Scholars (MIT) | ec17921 | running | recurring, dates knowable, but prior-cycle search couldn't run; own page is an SPA |
| Harvard Science Research Conf. | ec18392 | unknown | own site unreachable; gave up; empty then frozen 7 days |
| KCLS Teen Advisory Board | ec18286 | running | genuinely rolling/always-open; no deadline exists to find |

### Gaps

- **G1 — Estimation physically cannot run.** `MAX_SEARCHES=1` caps `web_search` at one query
  (`max_uses:1`, server-enforced), but the prompt mandates current-cycle **then** prior-cycle
  **then** FAQ searches. The prior-cycle search — the estimation basis — never runs. Compounded
  by SPA-unfetchability for THINK.
- **G2 — Unreachable site = give up + freeze.** No fallback when the site is down, and the
  empty `unknown` is stamped and cached 7 days.
- **G3 — "Always open" is unrepresentable.** Rolling programs have no dates → `computeProgressStatus`
  maps "no dates" → `not_started` forever; never "Happening Now", no calendar event.
- **G4 — Verified-empty wipes the client estimate.** (Resolved by architecture decision 3 — no
  client estimate is produced any more.)
- **G-cross — The write decision caches holes.** An empty verified result on a dateless row
  stamps + freezes exactly the rows most likely to be a search miss.

### Fixes (all DECIDED)

**G1 — orchestrated escalation loop.** `max_uses` is a ceiling, not a target; Anthropic bills
per search *performed* (~$0.01), so early-exit already means "pay only until found". Replace
phase 1's single call with a loop of up to N rounds, each `max_uses:1`, each injecting a
**distinct strategy**; read a cheap **found-signal** (a structured tail line
`FOUND_CONFIRMED_DATES` / `FOUND_PRIOR_CYCLE_BASIS`) and stop as soon as satisfied; run phase 2
once at the end over the union of fetched `sources`.

| Round | Injected strategy | Purpose |
|---|---|---|
| 1 | given URL + current/next cycle | normal case |
| 2 | **+ prior cycle** | estimation basis (fixes THINK) |
| 3 | + FAQ / key-dates / timeline | subpages |
| 4 | + third-party, **TRUSTED domains only** | last resort; dates forced `estimated:true` + source-noted |

Keep silent-search retry within a round; wire **prompt caching** (system prompt is identical
across rounds). Lives in `research_deadlines`/`check_one`, so batch + interactive inherit.

**G2 + G-cross — don't give up, don't freeze.** The ladder is the fallback (rung 4 recovers
Harvard's date from a trusted listing when its own site is down). Add a phase-1 `SITE_REACHED:
yes/no` signal and a revised `deadline_write_decision`:

| Phase-1 outcome | proposed |
|---|---|
| never searched | don't write/stamp (`unverified-fallback`) |
| searched, JSON unreadable | don't write/stamp (`unparsed-fallback`) |
| searched, empty, row HAS dates | keep existing (`kept-existing`) |
| **searched, empty, site NOT reached** | **don't stamp (`unreachable-fallback`)** — auto-retry next view |
| searched, empty, site reached, `unknown` | write + stamp, standard 7-day (real absence, not re-billed every view) |
| `not_running` / `rolling`, empty | write + stamp (real answer) |
| dates found | write + stamp |

**G3 — first-class `rolling` status.** Add `"rolling"` to `VALID_STATUS`; prompts emit it for
genuine continuous-admission programs (empty `important_dates` is then correct);
`deadline_write_decision` writes it even empty (same carve-out as `not_running`). Client:
`computeProgressStatus` maps `rolling` → `in_progress` (one line beside the `not_running`
case), a rolling badge ("Open now — apply anytime"), empty-state suppressed. **No** synthetic
today-dated "opens" entry (violates "never anchor a date to today"). **Ripple:** enumerate
every `status` reader (`cycleYearShift`, the three `get*DeadlineItems`, mocks) so `rolling`
doesn't fall through.

### G5 — a false `not_running` (found POST-plan, 2026-08-26, from a live user report) — FIXED

**The off-season of an annual program read as its death.** ec18599 (Impact Internships): its
first-ever check wrote `not_running` from the note *"2026 cycle closed; grace period ended
June 3, 2026. No 2027 dates posted yet"* — while the site still carried the whole 2026 cycle
(apply links, June 6–Aug 8 schedule) and the SAME shared capture's task extract quoted its
application-cycle language verbatim seconds later. One wrong word cascades: Past Event pill
(`computeProgressStatus` → completed), zero dates (the `EMPTY_IS_VALID_STATUS` carve-out lets
`not_running` write instantly), the item excluded from Home Base's task surface (its
page-verified tasks invisible) and from calendar sync. In late August — between cycles for a
large fraction of the catalog — this is the season this misread peaks.

Three mechanical causes, all fixed (commit 3bc43de; 1070 pytest green):
1. **Phase 2's own rule contained the conflation**: "suspended, discontinued or *not running
   this cycle* → not_running" — the exact case (a)/(b) distinction phase 1 guards against,
   written into the extract. Now: `not_running` = PERMANENTLY DISCONTINUED, nothing weaker;
   a closed/completed/unposted cycle is `running` with forward-dated estimates.
2. **A believed-dead verdict stopped the ladder**: `FOUND_CONFIRMED_DATES`'s "no application
   step" escape let a rung-1 discontinuation conclusion halt the prior-cycle rung — the one
   built to correct it. Now the signal must be "no" when the program is believed dead, so
   the ladder keeps climbing (corroborate the discontinuation OR recover the next cycle).
3. **Status was the only load-bearing claim with no evidence requirement** — dates have
   per-date `verified`, tasks need a code-checked quote, eligibility needs the official page,
   but "this program is dead" needed nothing. Now `verify_status_evidence()` in `check_one`
   (the status analogue of `verify_dates_against_capture`): phase 2 must emit a verbatim
   `status_evidence` quote with any `not_running`; code checks it against the captured pages
   via `quote_is_on_page`; missing/unfound → **downgrade to `unknown`**, which gets no
   empty-write carve-out (kept-existing protects any dates) and carries the caveat in the
   note. A verified quote is appended to the note with its source URL.

**Corrective re-run proven live ($0.081):** the fixed rung 1 found the FALL 2026 cycle —
applications open Aug 30, 2026, `verified:true` against the apply page — status `running`,
3 dates. The old verdict had been hiding a program whose application window opens within the
week. Residual: rows already carrying a wrong `not_running` from before the fix self-heal
only when re-checked; a targeted sweep of `not_running` rows is a cheap candidate follow-up.

---

### G6 — `verified:true` on WRONG dates: stale / pre-JS `web_fetch` content (found 2026-08-27, live debug) — OPEN

**Every date on a "verified" row was wrong, and three of the four carried `verified:true`.**
ec17543 (Tisch Future Artists, NYU — `www.nyu.edu/.../tisch-future-artists.html`), checked
today (`source: "fresh, real search"`, 1 search, `was_estimated:false`, cost $0.0707):

| field | stored (shown to student) | LIVE page (rendered in-browser 2026-08-27) |
|---|---|---|
| opens | 2026-08-**27** — `verified:false, estimated:false` (**= the check date**) | **2026-08-17** |
| deadline | 2026-11-**13** — **`verified:true`**, source_url = the nyu.edu landing page | **2026-11-09** |
| event_start | 2027-02-**03** — **`verified:true`** | **2027-01-30** |
| event_end | 2027-05-**11** — **`verified:true`** | **2027-05-08** |

The student sees "Nov 13 deadline (verified)". The real deadline is Nov 9 — a **4-day error on
the exact fact this product exists to get right**, wearing a confidence badge.

**Mechanism (confirmed, not inferred).** `date_is_on_page` matches the *exact* day, and the
live page contains **no "November 13" anywhere** (it says "November 9" twice, in body prose AND
a structured "Schedule and Deadlines" table). Yet the P6c verifier found "November 13" in the
captured text for that same URL and stamped `verified:true`. The only way both are true: **the
content Anthropic's `web_fetch` returned for that URL was a DIFFERENT (older/pre-JS) version of
the page than the live one.** Corroborating: the live "Opens August 17" and "Program Dates
January 30 – May 8" blocks are JS-rendered (our own `urllib`/WebFetch gets an empty 202 shell
for this host); the capture evidently lacked them, so the model could not find the opening date
and **anchored it to today (Aug 27)** — the documented today-anchoring failure, here landing on
`opens` and surviving into the row as `estimated:false`.

**Two distinct gaps, one cheap and one hard:**

- **G6a (cheap, code-fixable) — the today-anchored `opens` reached the student. ✅ BUILT
  2026-08-28.** `verify_dates_against_capture` correctly marked it `verified:false` but **never
  removed a date**, and the write path writes every date. A non-estimated date that is
  `verified:false` **and equals the check date** is the anchoring fingerprint (a genuinely-today
  real date would verify `true` against the page). **Implemented (demote, not drop):**
  `verify_dates_against_capture(info, captured, today=None)` now demotes any `estimated:false`
  date whose `date_iso == today AND verified == false` to `estimated:true` (never dropped, per
  T7) and appends a caveat to `important_date_note`. A real same-day date is unaffected because
  it verifies. Applies to any type but the `opens` case is why it matters (it flips "Happening
  Now"). The demoted date is no longer counted as a "confirmed date not found" quality signal.
  Code backstop for the extract prompt's "never anchor an estimate to today" rule. 3 unit tests
  in `test_check_deadlines_helpers.py`.

- **G6b (hard, strategic) — `verified:true` cannot detect staleness, so it can be confidently
  wrong.** P6c proves "this date string is in the text `web_fetch` returned", NOT "this is the
  current date on the live page". For JS-rendered / cache-served hosts (NYU is the canonical
  one — CLAUDE.md already notes nyu.edu answers our client with a 202/empty body) the fetched
  text can lag the live page, and verification then *manufactures* false confidence rather than
  catching error — strictly worse than leaving the date unverified. Candidate mitigations, none
  free: (i) a fresh independent re-fetch of the URL (rendered, e.g. the same path the app's own
  browser preview uses) to cross-check any `verified:true` date, downgrading to
  `verified:false`/`estimated` on disagreement; (ii) treat a capture whose text is a thin/pre-JS
  shell (detectable: our `page_text` fetch returns empty for the same URL that `web_fetch`
  "verified") as **not** a verification substrate; (iii) fingerprint the "status=running +
  deadline present + opens unverified-and-today" contradiction and force prior-cycle estimation
  instead of trusting the capture. This is the same family as §13 item 5 (Playwright/SPA) and
  item 4 (a provenance/recency surface must not overclaim). **Until mitigated, "(verified)" in
  the UI overstates certainty on JS-heavy hosts** — see §11.

**Immediate corrective:** ec17543's stored dates are live-wrong now; clearing
`dates_last_checked_at` only re-rolls the same stale capture. A durable fix needs G6a + at least
one G6b mitigation before a re-check helps.

---

## 4. Task creation — gaps & fixes

Task *accuracy* is already strong: the code-side quote verification (`claim_is_supported` /
`quote_is_on_page`) is what stopped the fabricated "Algebra 2 prerequisite". The problem is
**coverage** — everything that is neither the program's own readable page nor generic
boilerplate is invisible. **The 2026-08-26 replan (decision 6 / §5a) addresses both coverage
gaps at the root by moving tasks onto the shared substrate**, so this section's fixes are now
that substrate plus the trust tiers:

- **The official page may be unreadable by US** (SPA like THINK, or a PDF behind JS) — but
  Claude's `web_fetch` reads it. Under the substrate the task extract runs over the CAPTURED
  fetch content (the same PDF the deadline agent already reads), verified in code against that
  capture. The urllib-fetcher gap that produced THINK's generic list is gone; fetch fixes A/B/C
  are demoted to an optional local fallback (§5).
- **The richest description often sits on a third-party aggregator** (e.g.
  `lumiere-education.com`). The substrate's fetch may surface it and the trust tier decides
  whether it ships — the aggregator content is verified the same way, then tier-gated (below).

### G-task-1 — the steps page is never discovered; the pipeline settles for the homepage (found 2026-08-27, live debug) — OPEN

**A program with a rich, verifiable steps page got two throwaway tasks.** ec18244 (Congressional
Award), `action_items_source: page-verified`, checked 2026-08-24, stored list:
1. "Register for the program online" — `basis:generic`
2. "Sign up for emails from us" — `basis:page`, evidence "Sign Up for Emails from Us"

The stored `opp.url` is the **bare homepage** (`congressionalaward.org`) — marketing copy whose
only task-shaped string is the email-signup CTA. The actual application steps live on
**`/the-program/`**, and that page is **fully readable even by our free `urllib` fetch** (~5 KB,
reason `ok`): "Register and submit your one-time **$35.00 registration fee**… minimum age 13½",
"Sign up for a **Submittable account**", "Study the guidelines in the **Program Book**", "Select
a **Validator** for each goal", "complete all parts of your **Record Book** and ask your Advisor
to sign". Rich, program-specific, verbatim-quotable — everything the extract needs. The pipeline
started from `opp.url`, never reached `/the-program/`, and fell to homepage-furniture + a generic
top-up. (This row predates the substrate — generated 2026-08-24, pre-2026-08-26 — and is still
inside the 7-day TTL, so it is served from cache and has not re-run; see G-task-3.)

Three distinct gaps:

- **G-task-1a (primary) — sub-page discovery guesses page NAMES instead of reading the site's
  map.** `source_capture.FETCH_SYSTEM` and the shared finder hunt for pages named *How to Apply /
  Application / Requirements / Eligibility / FAQ / Key Dates / Timeline / Guidelines* via
  `web_search`. Programs routinely file the same content under *The Program / How It Works / Get
  Started / Participate / Overview / Steps / Participant Timeline*, so the guess misses and the
  hunt stops at the homepage. **CHOSEN FIX — sitemap-first discovery (operator direction
  2026-08-27), superseding "broaden the vocabulary".** Vocabulary-widening only trades one guess
  list for a longer one; enumerating the site's OWN pages removes the guess. See G-D1 below — a
  new shared free helper that both features call.

- **G-task-1b — a marketing-homepage capture still counts as `page-verified` and STAMPS.** The
  write decision cannot tell "read A page" from "read THE steps page": one recovered page-backed
  task (even a `Sign Up for Emails` CTA) → `page-verified` → stamped, suppressing re-discovery for
  7/90 days. There is no shallow-capture signal. **Candidate:** when the only page-backed tasks
  are navigation labels / CTAs (the run-62 furniture class — `page_text` could score this), OR
  when a `page-verified` result is `MIN_ITEMS` only because of the generic top-up, do not stamp —
  leave the row due so a broader discovery pass retries. Weaker than the deadline `site_reached`
  signal but the same idea: don't freeze a shallow read behind the TTL.

- **G-task-1c — the CTA task itself is low value.** "Sign up for emails from us" is a real,
  quoted phrase that passes both verifier gates yet is not an application step — exactly the
  navigation-label class the extract prompt names as non-steps (`generate_action_items.py:203`).
  On a marketing homepage the CTA is the *only* quotable "step", so it survives. Fixing G-task-1a
  (fetch the real steps page) mostly moots this; a stricter furniture filter is the backstop.

**Unlike G6, this row is FIXABLE by a re-run** — the content is there, readable, and verifiable —
**provided discovery reaches `/the-program/`.** The durable fix is G-D1. A forced re-check today,
on the current substrate, may or may not reach it; that uncertainty is exactly the gap.

### G-D1 — Sitemap-first page discovery (shared, free) — DESIGNED & VALIDATED 2026-08-27

**Principle: enumerate the site's real pages, then choose; never guess page names.** A new free,
stdlib-only helper (proposed `sitemap_common.py`) that both the deadline finder and the task
capture call BEFORE spending a `web_search`:

1. **Locate the sitemap (free HTTP).** Read `robots.txt` for `Sitemap:` lines; else probe
   `/sitemap.xml`, `/sitemap_index.xml`, `/wp-sitemap.xml` on the stored URL's host. Resolve a
   `<sitemapindex>` one level into its child sitemaps (bounded: ≤ N children, ≤ M total URLs,
   ≤ K bytes each, short timeout). Carry each URL's `<lastmod>` as a freshness signal.
2. **Scope to THIS program.** For a single-program host (`congressionalaward.org`) take all pages;
   for a multi-program host (`nyu.edu` hosting dozens) filter to URLs sharing the stored URL's
   **path prefix** or the opportunity's **name/slug tokens**, so ranking isn't over 50k pages.
3. **Rank by slug (free, deterministic).** Positive tokens (program, apply, application, register,
   how-to, prospective, participant, eligibility, requirements, deadline, dates, guidelines,
   admission, get-started, steps, timeline, overview, faq); negative chrome tokens (leadership,
   donor, giving, sponsor, news, blog, event, summit, alumni, staff, board, photos, podcast,
   job-posting); prefer shallow paths; boost name/slug-token overlap and recent `lastmod`.
4. **Fetch the top few.** Hand the shortlist to the existing Claude `web_fetch` step — either the
   heuristic top-3 directly, or the shortlist (top ~15, chrome stripped) for the model to pick the
   3 most relevant and fetch. Both extracts (dates + tasks) read that one capture (T6, unchanged).
5. **Fallback preserved.** No sitemap / empty (JS) sitemap / nothing survives scoping → today's
   `web_search` discovery, unchanged. So this can only ADD recall, never regress a working row.

**Why not just broaden the discovery vocabulary? (decided 2026-08-27, with the operator's
search-cap objection).** Vocabulary is not discarded — it becomes the ranker's token table and the
picker prompt. The question is only WHERE it is applied. Task discovery is capped at
`MAX_SEARCH = 2` non-deterministic, engine-ranked searches; broadening the prompt adds no search
budget and cannot change what the engine ranks, and the current prompt ALREADY lists 8 page-type
synonyms (How-to-Apply/Application/Requirements/Eligibility/FAQ/Key-Dates/Timeline/Guidelines) and
still missed `/the-program/`. So the same vocabulary applied to the site's real, complete page list
(one free GET) strictly dominates the same vocabulary applied to 2 capped searches. **Conclusion:
sitemap-first is PRIMARY; broadened vocabulary survives only as the ranker tokens + the search
FALLBACK's query terms for the ~37% of hosts with no sitemap.**

**Coverage measured 2026-08-27 (free, no API): 63% of hosts expose a usable sitemap.** Probed a
random 120-host sample of the 862 distinct hosts across 1,261 active rows (robots.txt `Sitemap:` +
`/sitemap.xml` `/sitemap_index.xml` `/wp-sitemap.xml`, 8 s timeout): **76/120 = 63%** had a
sitemap. This is a FLOOR — the probe skipped sibling subdomains (the `tisch.nyu.edu` case), longer
timeouts, and gzip variants. The other ~37% fall back to today's `web_search`, unchanged (no
regression). Necessary-but-not-sufficient: "has a sitemap" → "sitemap lists the useful deep page" →
"the ranker picks it" is the full chain — **D4 measures that conversion; 63% is the addressable
ceiling, not the guaranteed win.**

**Validated live 2026-08-27 (free, no API):** `congressionalaward.org` exposes a WordPress
`page-sitemap.xml` of 271 pages; a naive slug scorer ranked **`/prospective-participants/`** and
**`/the-program/`** as the top two (the steps page `web_search` never reached), with `/register/`
close behind — one job-application false positive, trivially filtered. Contrast the fallback case:
`www.nyu.edu/sitemap.xml` returns 202/empty, but `tisch.nyu.edu/sitemap.xml` is a clean sitemap
listing `.../graduate-application-requirements` — i.e. sitemap-first would also have surfaced the
Tisch program's real subdomain home, the same place its dates live (§3 G6). **This is discovery
only; it does not touch the G6 stale-`web_fetch` verification problem** — a page still has to be
fetched fresh and verified in code.

**Cost:** the sitemap probe + ranking is free HTTP + string work; it REPLACES ~1–2 `web_search`
calls ($0.01 each) with a fetch of pages we already know are right, so it is cost-neutral-to-
negative. Applies identically to the deadline ladder's own-site rungs (§3 G1 rungs 1–3), which
today issue `site:` searches that a sitemap makes unnecessary on hosts that publish one.

### G-task-3 — a pipeline upgrade does not backfill existing rows (noted 2026-08-27)

ec18244 was generated by the pre-substrate (urllib) pipeline on 2026-08-24 and will keep serving
its 2-item list until its TTL lapses (interactive 7 d; batch stamp 90 d), even though the
2026-08-26 substrate is strictly better at this row. There is no "regenerate rows written before
version X" mechanism. This is the same family as §13 item 1 (proactive coverage, deliberately
deferred) — recorded here so it is a known consequence of shipping accuracy fixes incrementally,
not a surprise: **improving the pipeline does not improve already-cached rows until they age out.**

### Fix — trusted-aggregator tiers (built P5/P6; operator decisions resolved in §8)

Add a **per-task trust tier**, highest→lowest, displayed as a confidence gradient:

1. **Official** — the program's own domain (page or its own PDF).
2. **Trusted aggregator** — a page on an operator-approved domain.
3. **Aggregator (pending)** — a domain not yet approved. **Withheld from students**; visible
   only to the operator as the approval queue (the flywheel).
4. **Generic** — type-derived, asserts nothing.

First principles (carry through every task decision):
- **Verification ≠ trust** (architecture decision 5).
- **Aggregators may back LOGISTICS, never ELIGIBILITY.** A false prerequisite is the original
  harm; an aggregator being wrong about one is that harm with a citation. A task stating a
  prerequisite/required course/test/score/GPA/age/grade/eligibility condition may be kept only
  at the **Official** tier; at aggregator tiers it is **dropped, not demoted**.
- **Third-party = lower tier, always; only the operator's allowlist promotes a domain.**
- **Enforced at BOTH generation and serve time** (defense in depth): generation tags tier; the
  serve path independently filters out `pending`/`blocked`, so a generation bug can't leak an
  un-approved source.
- **Every displayed task links to the specific document that backs it** (a second link,
  distinct from the step's action URL).

**Discovery of aggregator URLs — DECIDED (2026-08-25): reuse the deadline tracker's search
machinery.** The task pass has no web search by design; aggregator *discovery* is a separate
search step, and it is **the same shape as the deadline escalation loop** — a prose search-ON
phase that returns grounding-resolved, trusted-classified source URLs. So it reuses that
machinery rather than reimplementing D1 from scratch:
- **Shared helper.** The escalation loop built for deadlines (§3 G1) is generalized into a
  reusable "program source finder": prose phase-1, tools ON, per-round `max_uses:1`,
  grounding-resolved `sources`, trusted-domain classification. Deadlines call it to find
  date-bearing pages; task discovery calls it to find where the program is described.
- **Feature-specific extract stays search-OFF.** What differs is only the phase-2 step:
  deadlines extract dates; tasks fetch each surfaced page and run the existing code-side quote
  verification (`page_text`), tagging tier by domain. The "two Claude calls stay separate"
  rule holds — the shared part is the *search*, not the *extract*.
- **Different invocation, same code.** Deadline discovery runs interactively (student view,
  cached). Task aggregator discovery stays **operator-run (`--aggregators`), cost-quoted
  (`--preview`)**, never on a student's path — putting paid aggregator search on the
  interactive path is the unbounded-cost failure mode the repo avoids.
- **Synergy — share the RESULTS too.** When the deadline loop's rung 4 surfaces a *trusted*
  third-party page for a program, persist that URL; the task pass can reuse it as a known
  source instead of paying to re-discover it. One search can seed both features.
D2 (operator-supplied URLs) remains a free manual supplement; D3 (per-aggregator index crawl)
is a later optimization.

---

## 5. Shared infrastructure

### 5a. The unified fetch+verify substrate (the spine — decision 6, 2026-08-26)

This is now the CENTRE of the design: both features are the same pipeline with one differing
step. Build it once; deadlines and tasks are thin extracts on top.

```
  DISCOVER + FETCH   Claude web_search/web_fetch  ── the "program source finder" (§ below)
        │            reads PDFs, SPAs, 403/TLS-walled pages our urllib cannot
        ▼
  CAPTURE            the fetched TEXT of every result block (+ its resolved URL + domain)
        │            — this is the local text the verifier checks against
        ▼
  TIER-TAG           classify every captured source's domain via aggregators_common
        │            (official / trusted / pending / blocked)
        ▼
  EXTRACT  ◄──────── the ONE per-feature step: dates | tasks (search-OFF JSON over the capture)
        │
        ▼
  VERIFY (code)      the extracted fact must appear in the captured content:
        │            tasks → quote_is_on_page / claim_is_supported
        │            dates → the same-shaped date analogue (new)
        ▼
  DECIDE + WRITE     deadline_write_decision | action_items_write_decision (already parallel)
                     + eligibility gate: eligibility claims kept ONLY at official tier
```

- **Reuse is the point.** Discovery, fetch, capture, tier-tag, verify-mechanics, write-decision
  shape — all shared. Only the extract prompt and the cache column differ.
- **One paid fetch feeds both extracts.** A single discovery/fetch pass per row, then two cheap
  search-OFF extracts over the same capture. Cheaper than two independent search passes.
- **Capture is the new primitive.** `extract_source_urls` keeps only URLs today; the substrate
  keeps `{url, domain, text, tier}` per fetched block. Verification and tiering both read it.
- **Verification ≠ trust still holds (decision 5).** Code proves the source SAID it; the tier
  says how far to trust the source. A trusted-aggregator quote verifies the same way as an
  official one but is tier-limited (logistics, never eligibility).
- **Brings deadlines UP, not tasks down.** Deadlines gain a code-side "is this date on the
  captured page" check they never had; tasks gain a fetcher that reads the real source. Same
  standard both ways — the parity the 2026-08-26 replan asked for.
- **T6 (build-time):** one fetch pass vs. two coordinated passes — a date-optimized early-exit
  can under-fetch the requirements page a task needs. See §8.

### 5b. Trusted-domain allowlist

**Trusted-domain allowlist (`trusted_aggregators`).** One table + one shared lib
(`aggregators_common.py`: `normalize_domain`, `load_aggregator_policy`, `classify_source`) +
one console **Sources** tab. Serves **both** features:
- **Deadlines** use the read side only — the escalation loop's rung 4 keeps a date only if its
  domain is trusted.
- **Tasks** use the full **park-and-approve flywheel**: discovery may surface any domain;
  approved → tier 2 (shown), not-yet-approved → tier 3 (`pending`, stored but withheld, shown
  to the operator as "N parked tasks across M programs — approve?"), blocked → dropped.
  Approving a domain promotes its parked tasks live.

Modelled on the repo's existing discovery-vs-execution splits (mailing-list recipes at
`pending_review`; scraped rows at `is_active=false`). Degrade-not-break: if the table is
absent, the console shows the setup step, every aggregator domain is treated as pending
(nothing ships), and the deadline rung-4 filter simply keeps nothing off-domain.

**Fetch layer — Claude `web_fetch` is the shared fetcher; capture DECODES per media type
(2026-08-26, P6a-confirmed).** Both features fetch through Claude's server-side tools, which
read PDFs, SPAs and 403/TLS-walled pages our urllib cannot. The capture step then normalizes
what `web_fetch` returns to text (T8):
- **HTML** → `media_type text/plain`, clean markdown text → use directly.
- **PDF** → `media_type application/pdf`, base64 bytes → **base64-decode + pypdf extract** (this
  is fetch-fix A, revived as a capture DECODER — Claude still does the fetching/reaching).
- **`web_search` snippets are `encrypted_content` → unusable for verification.** The substrate
  must `web_fetch` any page it verifies against.

So B (same-domain link discovery) and C (Playwright) stay deferred/optional — Claude's fetcher
already reaches SPAs — but A is now a REQUIRED part of capture, not an optional local win.
`page_text`'s VERIFY half (`quote_is_on_page` / `claim_is_supported`, the generic-token list) is
retained and runs against the captured, decoded text.

**Shared search machinery ("program source finder").** The deadline escalation loop (§3 G1) is
built as a **reusable helper**, not a deadline-only function: prose phase-1, tools ON, per-round
`max_uses:1`, grounding-resolved `sources`, trusted-domain classification, cheap structured tail
signals (`FOUND_*`, `SITE_REACHED`). It is the DISCOVER+FETCH step of the substrate (§5a).
Deadlines call it to find date-bearing pages; the task pass calls the same helper for
requirement-bearing pages. The feature-specific *extract* (dates vs. tasks) is a search-OFF pass
over the shared **captured content** (not a separate urllib fetch), and each extract is verified
in code against that same capture. One fetch pass can feed both extracts; trusted rung-4 URLs
the deadline loop surfaces are persisted so a task-only pass can reuse them without re-paying.

---

## 6. Cross-cutting behaviours

### User-added opportunities (deadline + task)

The Quest Log "Add Opportunity" form posts to `/api/user-submitted-opportunities`, which runs
**inline** and returns a `catalogId` (dedup → existing id; new → `is_active=false` row + new
id; unresolvable → `id:null`). Both endpoints resolve by id and **ignore `is_active`**, so a
user-added opportunity uses the **identical Claude pipelines** as a Fresh Finds add. The one
gap — batch agents select `is_active=eq.true` and never re-touch inactive user-added rows — is
**closed by the 7-day TTL + stamp-on-success**: a failed attempt leaves the row due and
self-heals on the next view. `id:null` (unresolvable) → deadline 404 counts as *skipped* (not
failed), tasks fall to a static client-side generic checklist, until the row is resolved.

### "Check for updates" — refreshes deadlines AND tasks

**✅ SHIPPED with P9 (2026-08-25).** The two checks are decoupled in
`refreshTrackerDeadlines`: the task re-pull no longer hides behind a successful deadline check
(it runs on `ok`/`failed`; skipped only on `not-found` — no row serves either — and `blocked` —
the 402 gate covers both endpoints), the button forces the deadline check while the task
endpoint honours its own server-side 7-day TTL, and the result carries distinct
`deadlineUpdates`/`taskUpdates` counts ("N deadlines and M task lists updated"). Cost: a stale
item can trigger up to two Claude calls on refresh — accepted (the T6 read-once capture cache
means the two together still read the program once when fired in the same window).

### Per-user task delete & user-added tasks — ✅ SHIPPED with P10 (2026-08-26)

Both are **per-user**, live entirely in the tracker item (`users.data`), needed **no catalog
schema change**. The catalog `action_items` stays the shared, regenerated source;
`mergeActionItems` reconciles on **task text** via the shared `taskKey()` (positional ids are
unstable). The central constraint held exactly as predicted — the old merge mapped over
`incoming` only, so anything not in the catalog list was dropped on refresh — and both
features landed as **the merge extension**
(`mergeActionItems(existing, incoming, removedKeys)`):
- **Delete** = a per-user tombstone (`TrackerItem.removedTasks`, taskKey strings); the merge
  drops an incoming task whose key is tombstoned. A USER task is deleted by a real splice
  instead — nothing regenerates it, so a tombstone would be dead weight. Reversible: the
  modal shows "N removed tasks — restore" (`restoreRemovedTasks` clears the tombstones and
  forces a free sync so the tasks visibly return).
- **User-added** = `ActionItem.origin: 'catalog' | 'user'` (absent ⇒ catalog); random-suffix
  client id (stable while catalog `-tN` ids shuffle around it); never page-backed (`generic`,
  no tier — `user` is just another never-page-backed source); rendered in its own "Your own
  tasks" group with an "Added by you" chip; **the merge appends surviving `origin:'user'`
  tasks** instead of dropping them. Never written to the catalog. `addUserTask` refuses a
  duplicate text (text IS the merge identity, so a duplicate key would make
  state-preservation ambiguous) and lifts a matching tombstone on re-add; if a user task's
  text later matches a regenerated catalog line, the catalog copy wins and inherits the
  student's state.

Proven live against real data: the on-focus catalog sync re-merged THINK's cached task list
without resurrecting the tombstoned task and without dropping the user tasks, with ticked
state preserved.

---

## 7. Data model / schema

- **`trusted_aggregators`** (new `../../db/trusted_aggregators_schema.sql`): `domain` (pk), `status`
  (`trusted`|`blocked`; absent ⇒ pending), `label`, `notes`, `added_by`, timestamps.
  ALTER-then-CREATE convention (PostgREST 400s a whole write on one unknown key).
- **`action_items` item fields** (JSONB, no DDL): add `source_tier`
  (`official`|`trusted`|`pending`|null), `source_url`, `source_domain`. `basis` stays
  (page/generic). Back-compat: legacy `basis:'page'` with no tier reads as `official`; absent
  reads as generic. `action_items_source` (row-level) gains `aggregator-verified`.
- **`action_items_checked_at`** already exists — now drives the 7-day on-demand TTL.
- **Deadline columns** unchanged; `VALID_STATUS` gains `rolling`; a new write-decision source
  `unreachable-fallback`.
- **Per-date `source_url` + `verified` — ✅ SHIPPED with P6c (2026-08-26):** each verified
  `important_dates` entry now carries `verified` (bool) and, when found, `source_url` (the
  fetched page the date appears on) — additive JSONB, no DDL. The F2 provenance groundwork the
  plan recommended adding early; F2's student-facing recency/provenance surface renders it.
- **`status_evidence` (phase-2 transient, 2026-08-26):** the extract schema emits a verbatim
  discontinuation quote alongside a `not_running` status. It is CONSUMED by
  `verify_status_evidence()` and never written to the row — a verified quote is appended to
  `important_date_note` (with its source URL) so a reviewer can see why; an unproven
  `not_running` is downgraded to `unknown` before any write. No column, no DDL.
- **Per-user task fields — ✅ SHIPPED with P10 (2026-08-26), client-side only, no DDL:**
  `ActionItem.origin: 'catalog' | 'user'` (absent ⇒ catalog) and
  `TrackerItem.removedTasks: string[]` (taskKey tombstones), both living in the per-user
  tracker snapshot in `users.data` exactly as planned — the catalog row is untouched.

---

## 8. Decision log

### Resolved — deadline side (2026-08-25)

1. **Escalation ladder: 4 rungs, rung 4 TRUSTED-domains-only**, from the shared
   `trusted_aggregators` allowlist; rung-4 dates always `estimated:true` + source-noted.
2. **Third-party sourcing:** allowed, trusted-only, never confirmed (folded into #1).
3. **Genuine `unknown` TTL:** standard 7 days; only the unreachable case skips the stamp.
4. **Rolling:** first-class `rolling` status.
5. **Model:** Claude Haiku 4.5 for both tasks and deadlines.
6. **Prompt caching:** wire into the escalation loop (Phase 2).
7. **Task cache TTL:** 7 days on-demand (batch's 90-day stays a separate bulk knob).
8. **Playwright (fetch fix C):** deferred.
- Plus architecture decisions: on-demand + stamp-on-success; collapse the Gemini producer
  (resolves G4); `apply_url = opp.url`. *(The "two Claude calls stay separate" decision was
  SUPERSEDED 2026-08-26 — see the unified-substrate entry below.)*

### Resolved — unified fetch+verify substrate (2026-08-26)

- **Same standard, maximal reuse — DECIDED (2026-08-26): unify BOTH features onto one
  fetch+verify substrate (§5a), unified UPWARD onto Claude `web_fetch`.** Supersedes decision 6's
  "two separate calls, task must not search". Rationale: the two features diverged only because
  they stood on fetchers of unequal capability (Claude `web_fetch` vs. our urllib) — proven live
  2026-08-26, where the deadline agent read THINK's guidelines PDF that the task agent's urllib
  rejects outright. The fix is not a task-only local PDF patch; it is to share the fetcher and
  keep code-side verification for both by capturing the fetched content. Shared: discover+fetch,
  content capture, code-side verify, trust tiers, write-decision shape. Per-feature: the extract
  (dates vs tasks) only. The trust tiers (P5, already built) are what make a shared search-fed
  fetch safe — a non-official quote is tier-limited, never eligibility. (No longer open.)
- **Direction — unify UPWARD, not downward.** Downward (both on our urllib+pypdf fetcher) keeps
  hitting our client's 403/TLS/SPA ceiling and would REGRESS deadline coverage; upward reads the
  real source for both. Fetch fixes A/B/C demoted to optional local fallback. (No longer open.)

### Resolved — task-trust (aggregator) side (2026-08-25)

- **D1 discovery engine — DECIDED (2026-08-25):** reuse the deadline tracker's escalation-loop
  search machinery as a shared "program source finder"; task extract stays search-off and
  code-verified; operator-run (`--aggregators`), cost-quoted. Persist trusted rung-4 URLs the
  deadline loop surfaces so the task pass can reuse them. See §4. (No longer open.)
- **Eligibility carve-out — DECIDED (2026-08-25): Official tier ONLY.** An eligibility /
  prerequisite / required-course/test/score / GPA / age-grade claim is kept only when read on
  the program's own page; at trusted or pending aggregator tiers it is **dropped, not demoted**.
  Aggregators may back logistics, never eligibility. Enforced by the eligibility-claim detector
  (T3). (No longer open.)
- **Pending tasks — DECIDED (2026-08-25): PARK.** A task on a not-yet-approved domain is stored
  with `source_tier='pending'`, never served to students, and surfaced in the console's Sources
  tab as the approval queue ("N parked tasks across M programs — approve?"). Approving a domain
  promotes its parked tasks live; blocking drops them. Builds the evidence-based approval
  flywheel. (No longer open.)
- **Student display — DECIDED (2026-08-25): grouped headings + per-task source chips.** Group
  by tier ("From the program's own page" / "From a trusted guide · {domain}" / "Typical steps —
  confirm on the site") AND give each task a small tappable source chip (green Program page /
  blue Guide·{domain} / grey Typical step). Chip colour fixed per tier (not positional). The
  chip links the **evidence** `source_url` (where the claim came from); the existing trailing ↗
  stays the **step action** `url` — two different links. (No longer open.)
- **Seed allowlist — DECIDED (2026-08-25): seed `lumiere-education.com` as trusted on day one.**
  The `../../db/trusted_aggregators_schema.sql` seeds this one domain as `status='trusted'`, so its
  tasks (and THINK's) show from the first pass. All other discovered domains still start pending
  and go through the park-and-approve queue. (No longer open.)

### Technical unknowns (design, not policy)

- **T1** selecting "tracked rows regardless of `is_active`" — no opp→trackers index exists;
  lean toward running enrichment over active catalog + all `source='user-submitted'` rows.
- **T2** pending-domain aggregation at scale (JSONB scan; materialize later if slow).
- **T3** eligibility-claim detector (a new classifier, bias toward flagging; own test suite).
- **T4** one confidence vocabulary across dates (`(est.)`) and tasks (tier chips).
- **T5** copy that makes "the aggregator SAID it" vs "the program REQUIRES it" legible.
- **T6** (new 2026-08-26) — **RESOLVED / BUILT 2026-08-26.** The shared fetch is
  `check_deadlines.find_program_sources(want_dates, want_requirements)`: the date ladder
  (`research_deadlines`, UNCHANGED) fetches date-bearing pages; a requirements half
  (`source_capture.fetch_and_capture`, prompt widened to hunt How-to-Apply / FAQ / Key-Dates /
  Timeline / Guidelines-PDF) fetches requirement-bearing pages; the captures are merged. Rather
  than one loop with a combined stop-condition (which risked the proven date ladder), the two
  halves run and their captures union — deadlines read the date NOTES, tasks read the captured
  CONTENT. The **FULL (both) result is cached per-opportunity for 120s** (`_shared_capture_cache`),
  so the interactive deadline endpoint (`check_one(want_requirements=True)`) and the action-item
  endpoint (`process_one(full_capture=True)`) firing together read the program ONCE. Batches stay
  single-goal (deadline batch dates-only = unchanged; task batch requirements-only = cheaper), and
  single-goal calls never touch the cache.
- **T7** (new 2026-08-26) date verification analogue: the code-side check that a claimed date
  actually appears in the captured content (the dates' `quote_is_on_page`). Dates are formatted
  many ways ("Jan 15", "January 15, 2027", "15/01/27"), so this needs date-aware normalization,
  not the string match tasks use. Bias: verify the date is grounded, but do not DROP an
  estimated/projected date (which by definition is not on any page) — gate only NON-estimated
  dates, mark unverifiable ones rather than deleting.
- **T8** (new 2026-08-26) capture plumbing — **RESOLVED by the P6a live probe (2026-08-26,
  ~$0.09). The substrate is VIABLE.** Concrete block shapes on Haiku:
  - `web_fetch_tool_result.content.content.source` — for **HTML** it is
    `{type:'text', media_type:'text/plain', data:<clean markdown text>}` → **verifiable
    directly**. For a **PDF** it is `{type:'base64', media_type:'application/pdf', data:<b64>}`
    → the model reads it natively but WE get bytes, so capture must **base64-decode + extract
    text locally (pypdf)** before `quote_is_on_page`. This REVIVES fetch-fix A — not as a
    fetcher (Claude reaches the PDF, even off an SPA) but as a **capture DECODER**.
  - `web_search_tool_result` items carry **`encrypted_content`, opaque to us** — search
    snippets are NOT verifiable. **Consequence:** the substrate must `web_fetch` every page it
    verifies against; a search-only hit cannot back a task/date. The finder must fetch, not
    just search (ties into T6 — the fetch pass must actually fetch requirement pages).
  - Capture yields `{url, domain, media_type, text, tier}` per fetched block, deduped by url.

---

## 9. Unified phased plan

Each phase is shippable and inert-by-default. Foundation first, then the two feature tracks,
which are independent until the client consolidation.

**Foundation**
- **P0 — Tasks → Claude (Haiku 4.5). ✅ DONE 2026-08-25.** Swap `call_gemini`→`call_claude` in
  `generate_action_items.py`; cost via `claude_common`; verification untouched; gate
  `app/services/action_items.py` on `ANTHROPIC_API_KEY`; cost-attribution signature. No
  user-visible change beyond quality. *(Not yet run on real rows — a graded Claude sample
  would cost money; correctness is structural + unit-tested.)*
- **P1 — Task 7-day TTL + stamp-on-success. ✅ DONE 2026-08-25.** `resolve()` re-verifies past
  `action_items_checked_at` (`_is_fresh`); no stamp on failure. Closes the user-added-inactive
  gap; enables P8. Unit-tested.

**Deadline coverage & accuracy**
- **P2 — Escalation loop rungs 1–3 + prompt caching (G1). ✅ DONE 2026-08-25.** In
  `check_deadlines.py` (`RUNGS`/`ESCALATION_RUNGS`/`_search_round`/`_parse_signals`); batch +
  interactive inherit. Built as the reusable "program source finder" helper (§5) for P6. Loop
  orchestration unit-tested (monkeypatched). Verified live on the 3 traced rows
  (dry-run→committed): THINK→running+4 dates, KCLS→rolling, Harvard→running+6 dates, $0.1333.
- **P3 — Write-decision: `SITE_REACHED` / `unreachable-fallback` (G2, G-cross). ✅ DONE
  2026-08-25.** `check_one` returns 5-tuple w/ `site_reached`; full matrix unit-tested.
- **P4 — Rolling status (G3). ✅ DONE 2026-08-25.** `VALID_STATUS`+`EMPTY_IS_VALID_STATUS`,
  both prompts, write carve-out, client `computeProgressStatus`→in_progress, full ripple (4
  status accept-lists + `cycleYearShift`; date-based readers exclude rolling naturally), "Open
  now" badge. Verified live in-browser. **Plus** (beyond plan): the free tracker SYNC model —
  see the change-log entries below for the `/api/tracker/sync` endpoint, `syncTrackerFromCatalog`,
  its triggers, and the "Last checked" DB-timestamp stamp.

**Shared trust infrastructure**
- **P5 — `trusted_aggregators` + `aggregators_common` (read side) + console Sources tab.
  ✅ CODE-COMPLETE 2026-08-25** (branch `P5-P7-deadline-and-task-tracker`; 999 pytest green;
  console Sources tab browser-verified in the degraded table-absent state). Unlocks deadline
  rung 4 AND the task aggregator tier. Serve-path `pending`/`blocked` filter (safe no-op until
  aggregators exist) live. Deadline **rung 4** wired into the P2 loop. **DDL RUN + rung 4
  PROVEN LIVE 2026-08-26** ($0.146 total, no DB writes): gating verified on THINK ec17921 +
  Harvard ec18392 (own-site rungs 1-3 succeed → rung 4 correctly skipped); positive path
  verified directly on THINK — allowlist injected into the focus, model returned 9
  lumiere-education.com URLs + 1 Wikipedia, the Wikipedia hit **dropped by the trust filter**
  (only trusted sources reach phase 2), 3 dates emitted all `estimated=true` with a note
  citing the lumiere guide. P5 fully closed.

**Unified fetch+verify substrate + task coverage** *(replan 2026-08-26; decision 6 / §5a;
operator decisions resolved; T1–T8 at build time)*
- **P6 — Unified fetch+verify substrate.** The 2026-08-26 replan, in sub-steps so each ships
  and is provable on its own. Reworks shipped P0–P1 (tasks move off urllib) but leaves the
  student-visible contract the same until P7.
  - **P6a — Capture-viability probe (T8). ✅ DONE 2026-08-26 (~$0.09 live).** Confirmed on
    Haiku: `web_fetch` HTML → `text/plain` clean text (verify directly); `web_fetch` PDF →
    base64 (decode + pypdf); `web_search` → `encrypted_content` (unusable, must `web_fetch`).
    **Substrate viable.** Build target for the capture helper: return
    `{url, domain, media_type, text, tier}` per fetched block (`text` decoded per media type,
    `tier` from `aggregators_common`), deduped by url; verify only against fetched (not
    searched) content. Adds `pypdf` as a capture decoder.
  - **P6b — Task extract on the substrate. ✅ DONE + PROVEN LIVE 2026-08-26 (1033 pytest green;
    E2E on THINK $0.088, read-only).** End-to-end proof: THINK Scholars — a generic-only list
    under the urllib fetcher — now yields **4 page-backed OFFICIAL-tier tasks from its
    guidelines PDF**, each with a verbatim verified quote and a `source_url` to the PDF, plus 1
    generic; 1 demoted, 0 dropped, **no fabricated "algebra"**. The exact coverage win the
    substrate was built for. New `source_capture.py` (parse
    `web_fetch` blocks → `{url,domain,media_type,text,tier}`; HTML text/plain direct, PDF via
    PyPDF2; `web_search` ignored; tier via `aggregators_common`). `generate_action_items.py`:
    `process_one` fetches via `source_capture.fetch_and_capture` (Claude web_fetch) instead of
    `page_text`/urllib; `verify_items` runs the EXISTING `quote_is_on_page` /
    `claim_is_supported` against the captured content and tags each task's tier by the SOURCE
    that carried its quote; new `page_text.is_eligibility_claim` (T3) drops eligibility claims
    at non-official tiers (blocked sources dropped, pending parked). `app/services/action_items`
    inherits via the shared `process_one`. Item schema gains `source_tier`/`source_url`/
    `source_domain` on page-backed tasks. Tests: `test_source_capture.py` +
    eligibility/tier suites in `test_action_items.py`.
  - **P6c — Date verification analogue (T7). ✅ DONE 2026-08-26 (1039 pytest green; backend-only,
    additive, no extra API cost).** `page_text.date_is_on_page` (date-aware, multi-format: "Jan
    15", "January 15, 2027", "1/15/2027", ISO). The deadline loop now CAPTURES fetched content
    (`call_claude(return_captured=True)` → `source_capture.parse_captured_sources`, threaded
    through `research_deadlines`); `check_one` calls `verify_dates_against_capture` which MARKS
    each date `verified` + attaches `source_url`, IN PLACE (check_one's 5-tuple unchanged, so
    both call sites inherit; interactive route enriches its payload for free). NEVER deletes:
    an estimated/projected date is marked `verified:false` and not counted; only a NON-estimated
    date absent from every fetched page is counted as the quality signal (batch summary:
    "confirmed dates not found on any fetched page"). Rung-4 captured content is trust-filtered
    like its URLs. No auto-downgrade — watch the signal first (task-demotion-rate discipline).
  - **T6 (one pass vs two):** aim for one fetch pass feeding both extracts, its stop-condition
    covering date- AND requirement-bearing pages; persist discovered/ rung-4 URLs for reuse.
- **P7 — Frontend trust gradient. ✅ DONE 2026-08-25** (pure RN; tsc clean; verified live
  in-browser on seeded data — three tier groups, chips green/blue/grey by computed style, the
  per-date "✓ verified ↗" evidence link, "(estimated)", and the unmarked-unknown case). Built
  exactly to the contract below: `NormalizedActionItem`/`ActionItem` gained
  `sourceTier`/`sourceUrl`/`sourceDomain`; `taskTrustTier()` is the ONLY tier test (legacy
  page-backed-no-tier reads OFFICIAL — its urllib pipeline only ever read the program's own
  page, so provenance is known, not unknown; a `pending`/`blocked` tier is force-generic'd in
  the normalizer as defense in depth); `Milestone` carries `verified`/`sourceUrl` (forced
  false/null on client-projected dates). Surface the
  provenance the backend already emits. **Data contract (produced by P6b/P6c/T6):**
  - Each **task** the `/action-items` endpoint returns carries, when page-backed: `basis:"page"`,
    `source_tier` (`official`|`trusted`|`pending`), `source_url`, `source_domain`. Generic tasks
    have `basis:"generic"` and no tier. The serve path already withholds `pending`/`blocked`
    (`_servable`), so the client only ever sees `official`/`trusted`/generic.
  - Each **date** the `/deadline` endpoint returns carries `estimated` (bool) and, new, `verified`
    (bool) + `source_url` (present when verified). A projected/estimated date is `verified:false`
    by design.
  - **Build:** `frontend/src/api/trackerStore.ts` `ActionItem` gains `sourceTier`/`sourceUrl`/
    `sourceDomain`; a `taskTrustTier` helper; grouped headings ("From the program's own page" /
    "From a trusted guide · {domain}" / "Typical steps — confirm on the site"); per-task source
    chips (colour fixed per tier — green Program page / blue Guide·{domain} / grey Typical step;
    chip links the evidence `source_url`, the trailing ↗ stays the step-action `url`). Dates: a
    per-date "verified"/"(est.)" marker sharing one visual language with the tier chips (T4).
  - **Legacy/back-compat:** tasks written before P6b have no `source_tier` → render as generic
    (unknown provenance is not evidence of provenance). Existing normalizers
    (`normalizeUnverifiedActionItems` forces client-produced items to generic;
    `isPageBackedTask` is the only test) already handle this — P7 extends the VERIFIED-path
    normalizer (`normalizeVerifiedActionItems`) to read the new tier fields.

**Client consolidation & refresh**
- **P8 — Collapse the Gemini producer. ✅ DONE 2026-08-25.** `extractTrackerInfo` slimmed to
  `meta`/`fit` ONLY, search OFF (descriptive fields need no source; the prompt states it has no
  web access and forbids dates outright — the opening line is preserved verbatim as the
  `tracker_extract` mock + cost-attribution signature). The finder's add is now three
  INDEPENDENT sources, each failing alone degrading only its slice: meta/fit (slim Gemini),
  dates/status/note (deadline endpoint — the ONLY date producer, **G4 moot**), tasks
  (action-items endpoint, else `staticGenericChecklist` — the client twin of GENERIC_DEFAULT),
  `applyUrl = opp.url`. `intakeExtractAndClassify` keeps classifying + extracting dates (it
  seeds the review-queue submission) but no longer asks for action items; its unresolvable
  (`id:null`) fallback is the static generic list. `ACTION_ITEM_RULES` and
  `normalizeUnverifiedActionItems` are DELETED — no client path asks a model for tasks any
  more, so the failure mode is removed rather than fenced.
- **P9 — "Check for updates" refreshes both. ✅ DONE 2026-08-25.** Deadline and task pulls
  decoupled in `refreshTrackerDeadlines`: the task re-pull no longer hides behind a successful
  deadline check (it runs even on a failed one; skipped on `not-found` — no row serves either —
  and on `blocked` — the 402 gate covers both endpoints, don't pay a second refusal). The
  button still FORCES the deadline check while the task endpoint honours its own server-side
  7-day TTL. Result carries distinct `deadlineUpdates`/`taskUpdates`; the Quest Log label reads
  "N deadlines and M task lists updated". The `not-found` skip path verified live in-browser
  ($0): one 404'd deadline call, NO task call, honest "1 added by URL can't be auto-checked".

**Future**
- **P10 — Per-user task delete + user-added tasks. ✅ DONE 2026-08-26** (commit e68a230; tsc
  clean; merge + UI verified live in-browser against a REAL catalog sync). `taskKey()` is the
  shared text identity; `mergeActionItems(existing, incoming, removedKeys)` drops tombstoned
  catalog tasks and APPENDS surviving `origin:'user'` tasks (when a user task's text later
  matches a regenerated catalog line, the catalog copy wins and inherits the state). Item
  gains `removedTasks: string[]`; `ActionItem.origin` absent ⇒ catalog. Store helpers:
  `deleteTrackerTask` (tombstone for catalog, splice for user), `restoreRemovedTasks`
  (clears tombstones + the UI forces a free sync so the tasks visibly return), `addUserTask`
  (never page-backed, duplicate text refused — text IS the merge identity — and re-adding
  lifts a matching tombstone). UI in the Home Base modal: ✕ per row, "Your own tasks" group
  + "Added by you" chip, per-item add input, "N removed tasks — restore" undo line. Live
  proof: THINK's cached catalog list re-merged on focus sync WITHOUT resurrecting the
  tombstoned task or dropping the user tasks; in_progress state preserved; restore returned
  the task instantly.
- **P11 — Calendar: surface programs with ONGOING submissions (LAST phase). ✅ DONE
  2026-08-26** (same commit; verified live — KCLS `rolling` renders in the band and nowhere
  else on the calendar). Green "OPEN NOW — APPLY ANYTIME" band above the month lanes in
  `CalendarCard`, listing `status === 'rolling'` entries as tappable pills (→ the list card,
  same jump the lane entries use). Outside the date sort; no placeholder dates; saved/
  not_running excluded (upstream/by definition); dated currently-open programs deliberately
  NOT duplicated into the band — they already sit on a month lane with their real dates.
  The empty-state now only shows when both lanes AND the band are empty. *(Original
  rationale: a `rolling` program (G3) carries no `important_dates`, so the month-swimlane
  Calendar view — which places items by date — never showed it, and a student scanning "what
  can I apply to right now" missed every always-open program. The "consider also
  currently-open dated programs" question was decided against — no duplication.)* Client-only,
  in `frontend/app/(app)/tracker.tsx`'s `CalendarCard`; no backend, no new data.

**Deferred / optional:** fetch fixes A (PDF) + B (link discovery) survive only as a possible
LOCAL fallback now that Claude `web_fetch` is the shared fetcher (§5a); C (Playwright) deferred.

**Order:** ~~P0 → P1 → P2 → P3 → P4 → P5 → P6 (P6a → P6b → P6c) → T6 → P7 → P8 → P9 → P10 →
P11~~ ✅ **ALL DONE (2026-08-26).**

**THE PHASED PLAN IS COMPLETE.** Trust tiers and per-date provenance render in the app; the
redundant Gemini producer is gone; refresh reports deadlines and tasks separately; students
can remove and add their own tasks (surviving every refresh); rolling programs surface on the
calendar; and a `not_running` verdict now requires proof. Next work items live in §13/§13a
(F1 reminders, F2 accuracy harness, proactive coverage) plus the task-timeliness follow-up.

**Loose ends carried out of the P0–P4 session (none blocking):**
- P0 tasks-on-Claude never run on real rows — a graded sample costs money; do one when
  convenient to confirm demotion/cost on Haiku (watch `generate_action_items.py`'s summary).
- P2 interactive latency scales with rung count on hard rows (up to 3 sequential searched
  calls). If it bites real users, cap the interactive path to 2 rungs (one-line change).
- P2 prompt caching may no-op if the system prompt is under Haiku's 2048-token cache minimum
  — harmless, but confirm a cache hit on a live 2-round row before claiming the saving.
- A stale-row silent loop can make up to `2 × ESCALATION_RUNGS` phase-1 calls (token-only,
  bounded, rare); caching mitigates. Acceptable.
- Cosmetic: `deadlines.mock_deadline_check_payload` note still says "set GEMINI_API_KEY" though
  the gate is `ANTHROPIC_API_KEY` (pre-existing, out of P0–P4 scope).

### Sitemap-first discovery — phased build (G-D1, planned 2026-08-27)

New phase series **D0–D5**, deliberately numbered apart from the completed P0–P11 so it reads as
the post-plan follow-up it is. The design and its live validation are in §4 **G-D1**; this is the
build order. **The whole free core (D0–D2 logic) can be built and tested with zero API spend**;
only D4/D5 touch paid calls, and each is gated on a free `--preview` first. Guiding rule: every
phase must **degrade to today's `web_search` discovery**, so no phase can regress a row that works
now — sitemap-first only ADDS recall.

| Phase | In plain English | Cost |
|---|---|---|
| **D0** | Save real sitemaps from a handful of programs as test data, so the logic can be built and checked offline without calling anything. | free |
| **D1** | Write the helper that reads a site's own page list, throws out the junk (staff bios, galas, news), and ranks which pages most likely hold the apply-steps and dates. | free |
| **D2** | Plug that helper in so the app looks at the real page list FIRST, and only falls back to blind web search when a site has no usable map. | free logic |
| **D3** | Stop the app from "locking in" a weak result — if all it found was a homepage button, don't mark the program done; let a later run try again. | free |
| **D4** | Try it for real on Congressional Award and a few other programs; confirm it reaches the right pages and isn't more expensive than today. | paid, tiny |
| **D5** | Use the same page-list trick for deadline-hunting too, and — only if you approve — refresh old programs that were done the pre-sitemap way. | paid |

- **D0 — Fixtures & offline harness (free, no API). ✅ DONE 2026-08-28.** Sitemaps captured as
  static fixtures in `tests/fixtures/sitemaps/`: a WordPress sitemap **index** + two children
  (`congressionalaward_*.xml`, the proof row — page child carries `/the-program/` &
  `/prospective-participants/`, post child is chrome), a flat `<urlset>` with **CDATA-wrapped
  `<loc>`** (`tisch_sitemap.xml`), a **gzipped** `gzhost_sitemap.xml.gz`, and the no-sitemap /
  garbage / large-multi-program cases built in-test. Deliverable landed:
  `tests/fixtures/sitemaps/*` + `tests/unit/test_sitemap_common.py` (17 tests).

- **D1 — `sitemap_common.py` core (free, stdlib only). ✅ DONE 2026-08-28.** The whole discovery
  brain, no network in tests (fixtures injected via a fake `fetch`). Public surface:
  `discover_candidate_pages(opp, fetch=default_fetch, top_n=5) -> list[Candidate{url, score,
  lastmod}]`, returning `[]` cleanly whenever nothing usable is found (the fallback trigger).
  Internals, each unit-tested:
  - **locate:** `robots.txt` `Sitemap:` lines → else probe `/sitemap.xml`, `/sitemap_index.xml`,
    `/wp-sitemap.xml` on the stored URL's host; short timeout, bounded bytes.
  - **parse:** `<sitemapindex>` recursion **one level** into child sitemaps; handle gzip
    (`.xml.gz`), CDATA-wrapped `<loc>`, and `<lastmod>`. Hard caps: ≤ N child sitemaps, ≤ M total
    URLs, ≤ K bytes/file — a 50k-URL host must not blow time or memory (truncate + log, never hang).
  - **scope:** single-program host → keep all; multi-program host → filter to URLs sharing the
    stored URL's **path prefix** OR the opportunity's **name/slug tokens** (this is what stops
    nyu.edu-scale hosts ranking their whole tree).
  - **rank:** the slug scorer validated in §4 G-D1 — POS tokens (program/apply/register/deadline/
    dates/how-to/eligibility/requirements/…), NEG chrome tokens (leadership/donor/news/event/
    staff/job-posting/…), shallow-depth bonus, name-token-overlap bonus, recent-`lastmod` bonus.
  - **politeness:** obey the existing inter-call throttle for any fetch; honour an explicit
    `Disallow` on a candidate path; we only READ pages a student's browser would. (Note the
    content-signal `ai-train=no` some hosts set — we do not train, so it does not apply; record
    the reasoning so it is not re-litigated.)

- **D2 — Wire into the shared capture, behind the fallback (free logic; a live run costs the
  same as today). ✅ DONE 2026-08-28.** `source_capture.fetch_and_capture` consults
  `discover_candidate_pages` FIRST (injectable `discover` param) and injects the ranked candidate
  URLs into the model's `user_content` so `web_fetch` retrieves the real page. `web_search` stays
  enabled as the in-call fallback, and a `[]` result (no sitemap) or a crashing helper degrades to
  a **byte-identical** pre-sitemap prompt. Logs `discovery_source=sitemap|search` as a stat line.
  3 tests in `test_source_capture.py`. **Scope note:** D2 wired the TASK capture only; the deadline
  ladder's own-site `site:` rungs are D5 (paid). **No behaviour change on a no-sitemap host.**

- **D3 — Shallow-capture no-stamp signal (G-task-1b), complementary. ✅ DONE 2026-08-28.** Makes
  D2 safe against re-freezing a thin read: `action_items_write_decision` does NOT stamp a
  `page-verified` result when **every** page-backed task is navigation furniture/CTA
  (`is_furniture_task` — distinctive tokens vs a furniture vocabulary; ec18244's "Sign up for
  emails from us"). The list is still written (better than nothing); the row stays due so a
  later, better-discovered pass retries. A single substantive task stamps normally. Batch +
  interactive both already gate on `decision.stamp`. 3 tests in `test_action_items.py`.

- **D4 — Live validation on real rows. ✅ DONE 2026-08-28 ($0.1781, 4 rows, READ-ONLY).**
  Operator-approved. Free discovery on the real ec18244 sitemap first exposed and fixed a ranker
  bug (see the change log) BEFORE any spend. Then `process_one` (read-only) on 4 rows spanning the
  cases:
  - **ec18244 Congressional Award (proof row) — WIN.** Discovery reached `/register/` +
    `/participants/` (the pages `web_search` never found). The furniture CTA "Sign up for emails
    from us" → real page-verified OFFICIAL tasks: "Register and pay the one-time $35 registration
    fee", "Submit your completed Record Book for approval". 2 page-backed, $0.0525.
  - **ec18676 Chicago Summer Business — WIN.** 0 tasks → 3 page-backed OFFICIAL from `/how-to-apply/`
    ("Upload official transcript, resume, essay, income…"). $0.0258.
  - **ec18691 Red Cross (no-sitemap control) — NO REGRESSION.** Correctly fell back to `search`,
    still got 4 page-backed OFFICIAL tasks from the apply page. $0.0399.
  - **ec18687 SLIYS — MIXED (the demotion-rate signal).** Discovery reached `/sliys-requirements`
    + `/sliys-how-apply`, but the extract's quotes didn't match strictly → all 4 demoted to
    generic (0 page-backed, not fabrication). A verifier-strictness case, not a discovery miss.
    $0.0598.
  **Cost:** ~$0.044/row — slightly ABOVE the old ~$0.002-0.004 baseline (NOT the hoped
  cost-neutral), because sitemap-first fetches ~5 pages vs 1. We pay a little more to get real
  tasks instead of generic filler; the no-sitemap row cost the same, so multi-page fetch (not the
  sitemap probe) is the cost driver. **Read-only: the improved lists were NOT written — the tested
  rows still serve their cached lists to students until a live (writing) pass or TTL lapse.**

- **D5 — Deadline-ladder adoption. ✅ CODE DONE + VALIDATED 2026-08-28 ($0.2358, 3 rows,
  READ-ONLY).** Sitemap-first now feeds the deadline ladder's own-site rungs 1–3
  (`research_deadlines(discover=…)`, wired at the `find_program_sources` entry point; `web_search`
  stays the in-round fallback; rung 4 stays off-site). `discover` defaults OFF so the ladder's unit
  tests never touch the network. **Live read-only validation:**
  - Sitemap discovery FIRED on all 3 rows (`[discovery] sitemap: 5 candidate page(s)`), all
    reached the site (`site_reached=True`), no regression: ec18676 & ec18687 went 0 → full
    estimated date sets (`running`), ec18244 correctly stayed `rolling`/no-dates. G6a held (no
    today-anchored dates).
  - **The hoped-for search-count DROP did NOT materialize here — still 2 searches/row.** All three
    are OFF-SEASON rows (current cycle unposted), so the ladder climbs to rung 2 for prior-cycle
    estimation regardless of having the sitemap pages, and each rung still spends its one search.
    On these rows the benefit is **recall** (the model fetches the right pages), not cost. The
    search drop would show on a row whose CURRENT cycle is posted (rung 1 fetches the key-dates
    page and stops early) — these three weren't that case. ~$0.079/row, in line with the deadline
    baseline. A larger sample spanning in-season rows would be needed to measure the search drop.
  - **Optional backfill** of rows generated before D2 (the G-task-3 backfill) is proactive
    coverage, which §13 item 1 defers, so it stays gated on the operator un-deferring it.

**Order:** D0 → D1 → D2 → D3 → (free to here) → D4 → D5. D3 can land in parallel with D1/D2 (it
touches the write decision, not discovery). Nothing here is a schema change — `sitemap_common.py`
is new, additive, and stdlib-only, exactly like `page_text.py`.

---

## 10. Touch list

- `check_deadlines.py` — escalation loop; per-round `max_uses:1` + ladder; `FOUND_*` /
  `SITE_REACHED` tails; prompt caching; `VALID_STATUS`+`rolling`; `deadline_write_decision`
  + `SOURCE_UNREACHED`; rung-4 trusted filter.
- **`source_capture.py` (new, DONE):** the substrate CAPTURE layer — `parse_captured_sources`
  (`web_fetch` blocks → `CapturedSource{url,domain,media_type,text,tier}`; HTML text/plain direct,
  PDF base64 → PyPDF2; `web_search` ignored), `tier_for`, `fetch_and_capture` (Claude web_fetch),
  widened FETCH_SYSTEM (hunts how-to-apply/FAQ/key-dates/timeline/PDF).
- **`sitemap_common.py` (new, PLANNED — G-D1 / phases D0–D5):** free stdlib discovery helper
  `discover_candidate_pages(opp)` — locate (robots→common paths) → parse (index recursion, gzip,
  caps) → scope (path-prefix / name tokens) → rank (slug scorer) → top-N URLs, `[]` on nothing.
  Consumed by `source_capture.fetch_and_capture` and `check_deadlines.find_program_sources` ahead
  of `web_search`, which stays as the fallback. Tests in `tests/test_sitemap_common.py` +
  `tests/fixtures/sitemaps/*`.
- `check_deadlines.py` — **DONE:** `find_program_sources(want_dates, want_requirements)` (T6
  shared finder + `_shared_capture_cache` read-once); `call_claude(return_captured=True)`;
  `verify_dates_against_capture` (P6c) enriches each date with `verified`/`source_url`;
  `check_one(want_requirements=...)`. *(escalation loop, rung-4 filter, rolling, write-decision
  already done in P2–P5; date ladder UNCHANGED.)*
- `generate_action_items.py` — **DONE:** `process_one(full_capture=...)` fetches via the shared
  `find_program_sources` (not urllib); `verify_items(raw, opp, sources)` runs the UNCHANGED
  `quote_is_on_page`/`claim_is_supported` against captured content, tags each task's tier by the
  source holding its quote; page-backed items gain `source_tier`/`source_url`/`source_domain`.
- `page_text.py` — **DONE:** `is_eligibility_claim` (T3), `date_is_on_page` (P6c); the urllib
  `fetch_page_text` is now an optional local fallback, its verify half retained.
- `app/services/action_items.py` — **DONE:** interactive twin calls `process_one(full_capture=True)`;
  7-day TTL + serve-path `pending`/`blocked` filter (`_servable`) live.
- `app/routes/opportunities.py` — **DONE:** deadline endpoint calls
  `check_one(want_requirements=True)` (read-once).
- `app/routes/opportunities.py` — inherits `check_one`; new-outcome stamp handling.
- `aggregators_common.py` (new), `../../db/trusted_aggregators_schema.sql` (new).
- `ops/core.py` + `ops/admin.py` + `ops/admin_console.html` — Sources tab (approve/block/park).
- `frontend/src/lib/status.ts` — `rolling` in `computeProgressStatus` + list readers.
- `frontend/src/lib/tracker.ts` — **DONE (P7/P8):** tier/verified fields in the raw types +
  normalizer; `extractTrackerInfo` slimmed to meta/fit (search OFF, signature line preserved);
  intake keeps classifying + dates but no longer asks for tasks; `ACTION_ITEM_RULES` and
  `normalizeUnverifiedActionItems` DELETED; new `staticGenericChecklist`.
- `frontend/src/api/trackerStore.ts` — **DONE (P7/P9/P10):** `ActionItem` tier + `origin`
  fields; `taskTrustTier`; `taskKey`; `mergeActionItems(existing, incoming, removedKeys)`
  honours tombstones + appends user tasks; `deleteTrackerTask`/`restoreRemovedTasks`/
  `addUserTask`; `refreshTrackerDeadlines` decoupled with distinct counts;
  `applyDeadlineToTrackerItem` carries per-date `verified`/`sourceUrl`.
- `frontend/app/(app)/finder.tsx` + `tracker.tsx` + `index.tsx` — **DONE (P7-P11):**
  dates←deadline, tasks←action-items, `applyUrl=opp.url`; refresh reports both counts;
  trust-gradient rendering (3 tier groups + source chips + per-date verified marker); rolling
  badge; P10 task delete/add/restore UI; P11 calendar "Open now" band (`CalendarCard`).
- `check_deadlines.py` — **DONE (2026-08-26, post-plan):** `verify_status_evidence()` + the
  not_running prompt fixes in both phases + the `FOUND_CONFIRMED_DATES` believed-dead guard
  (§3 G5).
- cost attribution — `classify_feature` signature + Claude model pin for `provider_for_model`.
- tests — `test_check_deadlines_helpers.py` (write-decision matrix, date-verify, status
  evidence gate), `test_action_items.py` (Claude path + generic-token suite), status logic,
  mocks.

---

## 11. Risks & non-goals

- **Fabrication risk rises with source breadth — mitigated by trusted-only + estimated-noting.**
  Off-domain dates come only from the allowlist and are always `estimated:true`; aggregator
  tasks can never carry eligibility.
- **Non-determinism remains.** The loop makes the strategy *sequence* deterministic; the model
  still decides whether to search within a round — keep silent-search retry.
- **Status ripple (rolling).** Enumerate every `status` reader before shipping P4. *(Done in
  P4; the same enumeration is why a false `not_running` cascades so far — see §3 G5.)*
- **Merge fragility (P10) — RESOLVED 2026-08-26.** Preserving user tasks + tombstones was the
  one real code risk; keyed on text (`taskKey`), never positional id, and proven live against
  a real catalog sync (tombstone held, user tasks survived, ticked state preserved).
- **A false `not_running` buries a live program — MITIGATED 2026-08-26 (§3 G5).** The
  evidence gate makes it require a code-verified quote; the residual is rows written wrong
  BEFORE the gate, which only self-heal on their next check.
- **Task TIMELINESS (open follow-up).** A page-verified task can carry an already-past date
  ("Submit your application by June 3rd, 2026" — true, quoted, stale). Verification checks
  truth, not currency; a date-aware demotion/drop for past-dated tasks is unbuilt.
- **`verified:true` overclaims on stale/pre-JS captures (§3 G6, OPEN).** P6c proves a date is
  in the text `web_fetch` returned, not that it matches the live page. On JS-rendered / cached
  hosts (NYU) the capture can lag, so a wrong date gets a confidence badge (ec17543: "Nov 13
  (verified)" vs live Nov 9). Verification here manufactures false confidence instead of
  catching error — the one case where marking a date verified is worse than not. G6a (drop a
  today-anchored unverified date) is the cheap backstop; G6b (staleness detection / rendered
  cross-check) is unbuilt. Until then the UI "(verified)" marker overstates certainty on these
  hosts.
- **Substrate capture plumbing (T8) — RESOLVED 2026-08-26 (P6a), substrate viable.** `web_fetch`
  HTML returns clean `text/plain` (verify directly), PDF returns base64 (decode + pypdf),
  `web_search` returns `encrypted_content` (unusable — the substrate MUST `web_fetch` any page
  it verifies against, never rely on a search snippet). The residual risk is narrow: a page
  Claude fetches but returns as an unexpected media type — capture must degrade to "no text →
  no verifiable claim from this source", never crash.
- **Task cost rises on the substrate.** Tasks move from an ~free local fetch to a paid
  `web_fetch`/search pass. Mitigated by ONE shared fetch feeding both extracts (T6) and by the
  interactive 7-day cache, but confirm per-row cost on a live P6b sample before a full pass.
- **Verification strength must not regress in the move.** The captured content must be exactly
  what the model saw (same blocks), or a real quote could fail `quote_is_on_page`. Test that the
  "Algebra 2" fabrication still fails under substrate verification, not just under urllib.
- **Non-goal:** recurring event-date extraction for rolling programs (e.g. monthly meetings).
- **Non-goal:** confirmed-delivery / open-tracking anywhere (privacy).

---

## 12. Testing

- Unit-test the full `deadline_write_decision` matrix (every §3 row) and
  `action_items_write_decision`.
- Re-run the three traced rows (`--preview` first): THINK gains estimated dates (rungs 1–3 +
  trusted third-party), HSRC recovers a date off a trusted listing or is left due (not frozen),
  KCLS reads `rolling`/open-now.
- Watch escalation-depth + silent-search counters (cost guard: confirm early-exit).
- Every built-in generic checklist line runs through the verifier against an EMPTY page
  (existing `test_action_items.py` discipline) so no generic line smuggles a claim.
- Per-tier task rendering (official/trusted/generic) and the serve-path pending filter.
- **Substrate (P6):** capture parsing (`{url,domain,text,tier}` from fetch/search result
  blocks, deduped, truncation-bounded) unit-tested against recorded block fixtures;
  `quote_is_on_page`/`claim_is_supported` re-run against captured content (the "Algebra 2"
  fabrication must still fail); tier-tagging by domain via `aggregators_common`; the eligibility
  detector (T3) with its own adversarial suite. **P6a live probe** confirms Haiku's blocks carry
  text before P6b builds on it. Date-verify analogue (T7) with date-format-normalization cases,
  incl. that an estimated/projected date is never dropped for being absent from the page.
- **Status-evidence gate (G5):** unit suite in `test_check_deadlines_helpers.py` — verified
  quote keeps `not_running` + records evidence in the note; missing quote, quote-not-on-page,
  and no-capture all downgrade to `unknown`; other statuses untouched with a stray
  `status_evidence` stripped; `{}`/`None` outcomes pass through. 1070 pytest green.
- **Client verification record (P7–P11; no frontend unit framework — tsc + live browser
  against this session's own servers, API :8002 / Metro :8083, dev test account):**
  - P7 seeded-data pass: three tier groups, chip colours by computed style, "✓ verified ↗"
    and "(estimated)" markers, unmarked-unknown case, zero console errors.
  - Paid E2E ($0.199, operator-approved): real intake of think.mit.edu deduped into ec17921;
    task extract generated fresh on the substrate from the guidelines PDF (3 official-tier
    tasks, verbatim quotes, source_url); P9 forced refresh → "1 deadline updated" with
    distinct counts; fields proven intact catalog→endpoint→normalizer→users.data.
  - P9 skip path (free): one 404'd deadline call, NO task call, honest can't-auto-check label.
  - G5 corrective re-check ($0.081): ec18599 `running` + Fall-2026 dates, opens date
    `verified:true` against the apply page.
  - P10/P11 (free, real catalog data): focus-sync merge held the tombstone, kept the user
    tasks and the ticked state; restore returned the task instantly; add/delete/restore all
    persisted server-side; KCLS rendered in the "Open now" band and nowhere else.

---

## 13. Toward ONE source of truth — strategic gaps beyond this plan (EM review, 2026-08-25)

This plan makes the per-row **check** accurate. A live baseline (2026-08-25, 1,292 active rows)
shows the product's bigger gap is **coverage**, which this plan does not address:

| | Deadlines | Tasks |
|---|---|---|
| have any data | **40 / 1292 (3%)** | 52 / 1292 (4%) |
| **never checked** | **1236 (96%)** | 1249 (97%) |
| quality of what exists | 13% of dates estimated | 43% of tasks page-backed |

The freshness model is **entirely on-demand + pull**: a row is only populated when a student
tracks/opens it, and the 7-day TTL only re-bills rows that get viewed. So a student *browsing*
the catalog sees dates/tasks on 3–4% of rows — that is a directory, not a source of truth. The
accuracy work is a **prerequisite** (never mass-populate wrong data), but it is **not
sufficient**. Seven things a source-of-truth product needs, roughly prioritized:

1. **Fund + schedule proactive coverage.** _(Rough budget: a full both-pass ≈ **~$100–120** —
   deadlines ~$0.068/row × 1292 ≈ $88 with the escalation loop; tasks on Haiku ~$15–30; monthly
   ≈ ~$100/mo.)_ **DISPOSITION (2026-08-25): DELIBERATELY DEFERRED by the operator** until the
   deadline/task logic is finalized and in a stable state. This is an intentional sequencing
   choice, not a gap — do NOT build proactive/scheduled runs, and do not un-pause the crons,
   until the logic is proven stable. Revisit as the *first* thing once it is.
2. **Confidence- & proximity-aware freshness, not a flat 7-day TTL.** TTL = f(confidence,
   proximity): an **estimated** date on a deadline **3 days out** re-checks ~daily; a confirmed
   date 6 months out weekly is fine. Cheap (only imminent/estimated rows churn) and it is the
   real mechanism behind "never miss a deadline." **DISPOSITION (2026-08-25): PARKED IDEA** —
   promising but adds complex per-date variable-TTL logic; revisit once the system is in a
   stable state. Until then the flat 7-day TTL stands.
3. **Proactive change detection + notification.** _(a)_ detect when a date/requirement
   **changed** since the student last saw it and tell them ("moved Jan 1 → Dec 15"); _(b)_
   **push deadline reminders** via the existing Resend lifecycle-email infra, which today sends
   only welcome/trial/goodbye — there is no deadline reminder. A calendar the student must
   remember to check is not "never miss." **DISPOSITION (2026-08-25): COMMITTED future
   standalone feature** — see §13a.
4. **Measure accuracy — a ground-truth harness.** Precision/recall of dates & tasks vs a
   sampled human audit (the `grade_mailing_lists.py` precedent) — you cannot claim a source of
   truth you cannot measure — plus a **student-facing provenance/recency surface** (one
   confidence+provenance vocabulary across dates and tasks, "last verified N days ago", a
   per-date **source link** like tasks already carry; this is T4 elevated). **DISPOSITION
   (2026-08-25): COMMITTED future standalone feature** — see §13a.
5. **Reconsider the Playwright deferral, informed by data.** The official page is the truth;
   recovering dates from prior-cycle/third-party is a workaround. Measure SPA prevalence across
   the catalog first, then decide — deferral may be right, but it should be an evidence-based
   choice, not a default.
6. **Broaden from opportunity-facts to the application journey (future).** Eligibility as a
   verified first-class surface (today it is official-only, so an unreadable page = no
   eligibility — a real hole for the decision a student makes first), and **application-state
   tracking** (applied / accepted / waitlisted). "Source of truth for *applying*" is more than
   dates + tasks.
7. **Catalog integrity.** Dedup / canonicalization (HSRC already has a duplicate row). Two
   entries for one program erodes trust faster than a missing date.

**What this plan does well (not to lose):** verification-in-code (the anti-fabrication
guarantee), honest failure handling (no-stamp / auto-retry), shared search machinery, the
trust-tier model, and collapsing the redundant producer. The critique is not that the plan is
wrong — it is that it optimizes *accuracy of the 3% that gets checked* while the source-of-truth
goal needs *coverage of the 97%*, plus freshness, change-notification, and measurement layers
this plan is silent on. Sequencing is still defensible: land accuracy (Phases 0–9), keep proactive coverage (item 1)
deliberately deferred until the logic is stable, and build the notification + measurement
layers (items 3–4) as their own features below.

## 13a. Committed future standalone features

Not part of the Phase 0–10 core (which is deadline/task *creation* accuracy). These are
separately-scoped, separately-shippable, and each becomes its own plan when picked up. Recorded
here so the core is built with them in mind (e.g. dates should carry per-date provenance from
day one so #4 has something to render).

### F1 — Change detection & proactive reminders (item 3)

*Goal:* the app tells the student rather than waiting to be asked — the heart of "never miss a
deadline."
- **Change detection.** On every deadline/task refresh, diff the new result against what the
  student last saw and record a per-item "changed" event (date moved, requirement added,
  status flipped to `not_running`). Surface it in-app ("2 things changed since you last looked")
  and in the reminder email. Needs a small per-user "last seen" snapshot or a change log — the
  repo has no event log today (see the user-metrics notes), so this is net-new.
- **Deadline reminders.** Extend the existing Resend lifecycle system (`app/services/email.py`,
  currently welcome/trial/goodbye only) with a **deadline-approaching** email keyed off the
  tracker's `importantDates` (e.g. T-14 / T-3 / T-1). Reuse the `email_sends` claim-table
  dedupe so a reminder is sent once. Honour `lifecycle_email_optout`. Respect the same
  disarmed-cron caution — this needs a scheduled trigger, which ties to the deferred item 1.
- **Push (later):** native push once the reminder content model exists.
- *Dependencies:* benefits from per-date provenance (F2) and is the main consumer that makes
  proactive coverage (deferred item 1) worth funding.

### F2 — Accuracy measurement & provenance surface (item 4)

*Goal:* be a source of truth you can prove, and show the student why to believe each fact.
- **Ground-truth harness.** A `grade_deadlines.py` / `grade_action_items.py` (mirroring
  `grade_mailing_lists.py`): a deterministic adversarial sample, a human worksheet, and a
  `--score` computing **precision/recall** of dates and tasks against reality. Recall needs a
  human (the check cannot report what it missed). Free; the thing it protects is trust.
- **Provenance & recency, student-facing.** One confidence+provenance vocabulary across dates
  and tasks (this is T4 elevated to a feature): "last verified N days ago", the estimated/
  confirmed marker (dates) and the tier chip (tasks) sharing one visual language, and a
  **per-date source link** — dates should carry the URL they came from, exactly as tasks carry
  `source_url`. *Build the per-date `source_url` field into the core now* (cheap, additive
  JSONB) so this feature has data to render later.

## Change log

- **2026-08-28 (later still)** — **D5 built + validated ($0.2358, 3 rows, READ-ONLY); 4 tested
  task rows written LIVE ($0.174, agent_runs id=72).** D5: sitemap-first wired into the deadline
  ladder's own-site rungs (`research_deadlines(discover=…)` default OFF for network-free tests;
  `find_program_sources` passes the real helper; rung 4 stays off-site). Validation: discovery
  FIRED + `site_reached` on all 3, no regression (ec18676/ec18687 0→estimated date sets, ec18244
  stayed `rolling`), **but the search-count drop did NOT show — all 3 are off-season rows that
  climb to rung 2 for prior-cycle estimation regardless; benefit here is recall, not cost.** Also:
  after D4's read-only proof, the operator approved WRITING the 4 tested task rows live —
  ec18244's furniture CTA is now replaced in the catalog by the real "$35 registration fee" /
  "Record Book" tasks; ec18676 went 0→page-backed. 1171 pytest green.
- **2026-08-28 (later)** — **D4 live validation DONE ($0.1781, 4 rows, READ-ONLY) + a ranker bug
  fixed FREE before spending.** The free discovery half on ec18244's real 414-page sitemap caught
  the ranker dropping `/the-program/` & `/prospective-participants/` entirely: the old 400-page
  scope threshold tripped multi-program filtering, which kept name-matching chrome (news/gala:
  "congressional-award-…") and dropped the real content pages (which don't repeat the org name).
  Fixed in `sitemap_common.py`: scope decides single-/multi-program by the stored URL's PATH not
  page count (bare homepage → keep all); `name_tokens` drops host-derived tokens; `score_slug`
  gains an exact-nav-slug boost (+3 for the-program/apply/eligibility/…), a long-"sentence"-slug
  penalty (news headlines), and fundraiser NEG tokens (golf/poker/tournament/gala). Then the paid
  read-only run: **ec18244 proof row WIN** (furniture "Sign up for emails" → real "$35 registration
  fee" + "Record Book" from `/register/`+`/participants/`), **ec18676 WIN** (0→3 page-backed from
  `/how-to-apply/`), **ec18691 no-sitemap control** (search fallback, 4 page-backed, no regression),
  **ec18687 SLIYS mixed** (reached the requirement pages but all 4 demoted to generic — the
  verifier-strictness / demotion-rate signal, not a discovery miss). Cost ~$0.044/row, slightly
  ABOVE the old baseline (multi-page fetch, not the sitemap probe, is the driver). Read-only —
  nothing written. Details in §9 D4. Remaining: D5 (deadline-ladder adoption + optional backfill)
  and G6b, both deferred.
- **2026-08-28** — **FREE CORE of the gap-hunt follow-up BUILT — G6a + D0–D3** (branch
  `sitemap-discovery-g6a`, off `main`; 1166 pytest green; zero API spend; DDL-free/stdlib-only).
  - **G6a** (`check_deadlines.verify_dates_against_capture`, +`today` param): demotes a
    `estimated:false, verified:false` date equal to the check date to `estimated:true` + a note
    (never drops, per T7). The today-anchoring fingerprint (ec17543's `opens` → HAPPENING NOW);
    a real same-day date verifies and is untouched. 3 tests.
  - **D0/D1** (`sitemap_common.py` NEW + `tests/fixtures/sitemaps/*` + 17 tests): free,
    stdlib-only sitemap-first discovery — `discover_candidate_pages(opp, fetch)` locates
    (robots→common paths), parses (sitemapindex recursion one level, gzip, CDATA, lastmod; hard
    caps), scopes (single-program keeps all / multi-program filters by path-prefix + name tokens),
    ranks (slug scorer). Returns `[]` = the `web_search` fallback trigger, so it only ADDS recall.
  - **D2** (`source_capture.fetch_and_capture`, +`discover` param): consults the sitemap FIRST and
    injects ranked candidate URLs into the `web_fetch` prompt; no-sitemap / crashing-helper →
    byte-identical pre-sitemap prompt. Logs `discovery_source=sitemap|search`. Wired the TASK
    capture only; the deadline ladder's `site:` rungs stay for D5. 3 tests.
  - **D3** (`generate_action_items.action_items_write_decision`, +`is_furniture_task`): a
    `page-verified` result whose page-backed tasks are ALL navigation furniture/CTAs (ec18244)
    writes but does NOT stamp — leaves the row due for a better-discovered retry. 3 tests.
  - **Deferred (need operator approval, PAID):** D4 (`--preview`-first live validation on ec18244
    + spanning hosts, cost-delta vs `web_search`), D5 (deadline-ladder adoption + optional
    backfill), and **G6b** (staleness detection: rendered re-fetch / thin-shell — the hard half of
    §3 G6; G6a is only the cheap backstop).
- **2026-08-27** — **G-task-1 recorded (gap-hunt example 2): rich steps page never discovered.**
  ec18244 (Congressional Award) served 2 throwaway tasks ("Register…" generic + "Sign up for
  emails" CTA) while `/the-program/` carries a full, verbatim-quotable step list ($35 registration,
  Submittable account, Program Book, Validator, Record Book) — readable even by our free urllib
  fetch. Root cause: the pipeline starts from `opp.url` (the bare homepage) and the sub-page
  discovery vocabulary (How-to-Apply/FAQ/Requirements) misses programs that file their steps under
  "The Program"/"How It Works"/"Participant Timeline". Filed under §4 as **G-task-1a** (broaden
  discovery + hunt harder when the landing page is thin), **G-task-1b** (a marketing-homepage
  capture still stamps `page-verified`, suppressing re-discovery — add a shallow-capture no-stamp
  signal), **G-task-1c** (the CTA "step" is furniture that passed verification). Plus **G-task-3**:
  a pipeline upgrade doesn't backfill cached rows. Fixable by re-run IF discovery reaches the page
  — that "if" is the gap. No code change yet; logged for the operator.
- **2026-08-27** — **G-D1 chosen: sitemap-first page discovery (operator direction), superseding
  "expand the discovery vocabulary".** Enumerate the site's own pages (robots→sitemap→child
  sitemaps, free HTTP), scope to the program, rank slugs, fetch the top few; fall back to the
  current `web_search` when no usable sitemap. Validated free on real data: over
  congressionalaward.org's 271-page sitemap a naive slug scorer put `/the-program/` and
  `/prospective-participants/` on top (the pages web_search missed); www.nyu.edu has no sitemap
  (202) but tisch.nyu.edu does. Shared free helper for BOTH features; cost-neutral-to-negative
  (replaces paid searches). Designed under §4 G-D1; **phased as D0–D5 in §9** (free core D0–D2,
  paid validation D4/D5, each `--preview`-gated). Not yet built. **Coverage measured: 63% of hosts
  (76/120 sample of 862 distinct hosts) expose a usable sitemap — a floor; the rest fall back to
  web_search unchanged.** Vocabulary-broadening rejected as PRIMARY (search is capped at 2,
  non-deterministic; the prompt already lists 8 synonyms and still missed the page) but retained as
  the ranker tokens + fallback query terms.
- **2026-08-27** — **G6 recorded (live debug, example 1 of a gap-hunt session): `verified:true`
  on WRONG dates.** ec17543 (Tisch Future Artists) written today with all four dates live-wrong
  and three marked `verified:true` (deadline stored Nov 13, real Nov 9; opens today-anchored to
  the check date). Root cause confirmed in-browser: Anthropic `web_fetch` returned stale/pre-JS
  content for this JS-rendered NYU host, and P6c "verified" it against that stale capture. Two
  gaps filed under §3 G6: **G6a** (drop/demote a `estimated:false, verified:false` date that
  equals the check date — the today-anchoring fingerprint; cheap code backstop) and **G6b**
  (verification cannot detect capture staleness → false confidence on JS/cached hosts; needs a
  rendered cross-check or thin-shell detection). §11 risk added. No code change yet — logged for
  the operator; ec17543 will not self-heal on a plain re-check (same stale capture).
- **2026-08-26** — **P10 + P11 SHIPPED — the phased plan is COMPLETE** (commit e68a230, tsc
  clean, verified live in-browser at $0 against real catalog data). P10: `taskKey` text
  identity; `mergeActionItems` honours per-user tombstones (`TrackerItem.removedTasks`) and
  appends surviving `origin:'user'` tasks; store helpers `deleteTrackerTask` /
  `restoreRemovedTasks` / `addUserTask`; Home Base modal gains ✕ per row, a "Your own tasks"
  group ("Added by you" chip), an add-task input and a restore-undo line. Live proof: the
  focus sync re-merged THINK's cached catalog list without resurrecting the tombstoned task
  or dropping user tasks (state preserved); restore returned the task instantly. P11: green
  "OPEN NOW — APPLY ANYTIME" band above the calendar's month lanes listing rolling programs
  (tappable pills → list card); outside the date sort, no invented dates, no duplication of
  dated open programs; KCLS verified in the band and nowhere else.
- **2026-08-26** — **`not_running` now requires PROOF (status-evidence gate), from a live
  user-reported case.** ec18599 (Impact Internships, an annual program between cycles) was
  written `not_running` from "2026 cycle closed... No 2027 dates posted yet" — the off-season
  read as death. One wrong word cascades: Past Event pill (`computeProgressStatus` →
  completed), zero dates (the empty-write carve-out let it write instantly), item excluded
  from Home Base's task surface (its 2 page-verified tasks invisible) and from calendar sync.
  Root causes fixed in `check_deadlines.py` (commit 3bc43de, 1070 pytest green):
  (1) phase 2's own rule said "not running this cycle → not_running" — the exact conflation
  phase 1 guards against, now removed; not_running = permanently discontinued, nothing weaker;
  (2) phase 1: a closed cycle is never by itself discontinuation evidence, and a
  discontinuation claim must be quoted VERBATIM from a fetched page; FOUND_CONFIRMED_DATES may
  never be "yes" because the program is believed dead (the ladder loophole that stopped the
  prior-cycle rung from running); (3) NEW `verify_status_evidence()` in `check_one` — the
  status analogue of `verify_dates_against_capture`: phase 2 emits `status_evidence`, code
  checks it via `quote_is_on_page` against the capture, and an unproven not_running downgrades
  to `unknown` (no empty-write carve-out → cannot wipe dates) with the caveat in the note.
  "This program is dead" was the only load-bearing claim with no evidence requirement; now it
  meets the same standard as dates and tasks. **Corrective re-run proven live ($0.081):** the
  fixed rung 1 found the FALL 2026 cycle — applications open Aug 30, 2026 (five days out),
  `verified:true` against the apply page — status `running`, 3 dates. The old verdict was
  hiding a program whose window opens this week. Residual (accepted): the row's stored tasks
  still carry a page-verified but PAST June 3 task until their TTL re-verifies (~7 days);
  task timeliness (drop/demote past-dated tasks) remains an open follow-up.
- **2026-08-25 (later session)** — **P7 + P8 + P9 SHIPPED (all client, tsc clean, $0).**
  - **P7 (trust gradient):** `NormalizedActionItem`/`ActionItem` gained `sourceTier`/
    `sourceUrl`/`sourceDomain`; `taskTrustTier()` in trackerStore is the only tier test
    (page-backed + no tier ⇒ OFFICIAL — legacy items were verified against the program's own
    page by construction; `pending`/`blocked` force-generic'd in the normalizer, mirroring
    `_servable`). Home Base's task modal renders THREE groups ("From the program's own page" /
    "From a trusted guide · {domain}" / "Typical steps — confirm on the site") with per-task
    chips (green Program page / blue Guide·{domain} / grey Typical step; chip links the
    evidence `sourceUrl`, the trailing ↗ stays the step-action url). Dates: `ImportantDate`/
    `Milestone` carry `verified`/`sourceUrl` end-to-end (store merge, free sync, finder add,
    intake add); the Quest Log date row renders "✓ verified ↗" (green, tappable evidence link)
    beside the existing "(estimated)"; a client-projected date is forced verified:false. All
    verified in-browser on seeded data (chips confirmed by computed style; no console errors).
  - **P8 (collapse producer):** `extractTrackerInfo` → meta/fit only, search OFF, mock/cost
    signature line preserved; finder add rebuilt as three independent sources (a Gemini outage
    no longer degrades the whole add to a stub); `staticGenericChecklist()` added (client twin
    of GENERIC_DEFAULT) for endpoint-less rows; intake prompt no longer asks for action items;
    `ACTION_ITEM_RULES` + `normalizeUnverifiedActionItems` deleted (no client path asks a model
    for tasks — removing the ask removes the fabrication failure mode). G4 moot.
  - **P9 (decoupled refresh):** task re-pull independent of the deadline outcome (runs on
    `ok`/`failed`; skipped on `not-found`/`blocked`); distinct `deadlineUpdates`/`taskUpdates`
    counts; label "N deadlines and M task lists updated". Skip path proven live: one 404
    deadline call, no task call, honest can't-auto-check message.
  - Verification setup for later sessions: `.claude/launch.json` gained `wingman-api-8002`
    (PORT=8002, explicit WindowsApps python — the bare `python` on PATH lacks uvicorn) and
    `wingman-web-8083` (Metro, EXPO_PUBLIC_API_BASE→:8002), so this session's servers never
    collide with 8000/8001/8081/8082 in use by others. Seeded via dev_test_account.py +
    /api/data/save; seed cleared after.
  - **PAID E2E PROVEN LIVE same session ($0.199, operator-approved).** Real flow, real THINK
    row (ec17921), through the browser UI: Quest Log intake with `https://think.mit.edu/` →
    slim Gemini classify (~$0.002) → user submission DEDUPED into ec17921 (no new queue row) →
    deadline endpoint (cached hit from the 08-26 stamp) → action-items endpoint generated
    FRESH on the substrate (~$0.192 — it read the guidelines PDF): **3 page-backed
    OFFICIAL-tier tasks, verbatim quotes, source_url to the PDF**, rendered under "From the
    program's own page" with linked green chips. Then the P9 forced refresh (~$0.006, rung 1):
    fresh check found the 2025-26 cycle ended → rolled all 5 dates forward, every date
    `estimated:true, verified:false` — the honest answer, rendered as "(estimated)" with no
    verified marks — and the label read **"1 deadline updated"** (distinct counts live; tasks
    unchanged so no task count). Snapshot inspection confirmed tier + verified/sourceUrl
    fields flow catalog→endpoint→normalizer→users.data intact. The `verified:true` render
    path was proven on seeded data (a live one needs a program with posted current-cycle
    dates; P6c proved the backend emits it).
- **2026-08-26** — **T6 BUILT — shared program-source finder (read-once + FAQ for tasks).**
  Prompted by the question "shouldn't tasks discover pages the same thorough way deadlines do,
  so FAQ/how-to-apply pages get checked for tasks too?". Yes — it's an ACCURACY win, not just
  efficiency (those sub-pages carry both dates and requirements). Built
  `check_deadlines.find_program_sources(want_dates, want_requirements)`: the date ladder stays
  `research_deadlines` (UNCHANGED, proven); a requirements half reuses `fetch_and_capture` with a
  widened prompt (How-to-Apply / FAQ / Key-Dates / Timeline / Guidelines-PDF); captures merge.
  The FULL result is cached per-opportunity 120s so the two interactive endpoints firing together
  read the program ONCE (`check_one(want_requirements=True)` + `process_one(full_capture=True)`).
  Batches stay single-goal (deadline dates-only unchanged; task requirements-only). 1045 pytest
  green. **PROVEN LIVE 2026-08-26 on THINK ($0.115 both steps, read-only):** deadline check
  fetched+cached the program; the task check REUSED it (fetch cost $0.0106 — just the extract),
  so the program was read ONCE; tasks came back `page/official` from the guidelines PDF; deadline
  dates unregressed (2 verified against the PDF via P6c); no fabrication. **Tier-merge bug found
  and fixed in the same session:** the date half captures pages untiered (pending), and first-wins
  merge let a date-fetched copy of the program's OWN page shadow the requirements half's `official`
  — which the serve filter would have WITHHELD from students. Fixed by re-tiering every captured
  page against the opportunity's own domain in `find_program_sources` (tier is now a pure function
  of url+own_domain+policy, order-independent); regression-tested. Task-batch note: interactive
  reads once, but a program viewed for deadlines only still fetches requirement pages (the cost of
  "one read feeds both" — accepted, client fires both together).
- **2026-08-26** — **P6b PROVEN LIVE + P6c DONE.** P6b E2E on THINK ($0.088, read-only): 4
  page-backed OFFICIAL-tier tasks from its guidelines PDF, verbatim verified quotes, no
  fabricated Algebra 2. **P6c** (date verification analogue T7, 1039 pytest green, backend-only,
  no extra API cost): `page_text.date_is_on_page` (multi-format); the deadline loop captures
  fetched content (`return_captured` → `parse_captured_sources`, threaded through
  `research_deadlines`); `check_one` → `verify_dates_against_capture` marks each date `verified`
  + `source_url` IN PLACE (5-tuple unchanged; interactive route inherits); never deletes
  (estimated dates marked not-counted); batch summary counts confirmed dates not found on any
  fetched page (quality signal, no auto-downgrade). Per-date `source_url`/`verified` = F2
  provenance groundwork, shipped early.
- **2026-08-26** — **P6a DONE — capture-viability probe (T8 resolved, ~$0.09 live, no writes).**
  The hard prerequisite for the substrate passed. Live on Haiku:
  `web_fetch_tool_result.content.content.source` is `{text, text/plain, <clean markdown>}` for
  HTML (verifiable directly) and `{base64, application/pdf, <bytes>}` for a PDF (decode + pypdf);
  `web_search_tool_result` items carry `encrypted_content`, opaque, so search snippets are NOT
  verifiable and the substrate must `web_fetch` any page it verifies against. **Substrate
  viable.** Refinements folded into the plan: capture returns `{url, domain, media_type, text,
  tier}` per fetched block; fetch-fix A (`pypdf`) is REVIVED as a capture DECODER (not a fetcher);
  verification is `web_fetch`-only. Next: **P6b** (task extract on the substrate).
- **2026-08-26** — **REPLAN: unified fetch+verify substrate** (decision 6 superseded; §5a new).
  Triggered by the P5 rung-4 proof, which exposed that deadlines and tasks stood on two fetchers
  of unequal power: Claude `web_fetch` read THINK's guidelines PDF (deadlines) while our urllib
  `page_text` rejects both the SPA (`empty-or-js`) and the PDF (`not-html`), so tasks fell to
  generic. Operator direction: **same standard for both features, maximal logic reuse.** Decision:
  unify BOTH onto ONE substrate (discover+fetch via Claude `web_fetch` → capture the fetched
  content → tier-tag by domain → per-feature extract → code-side verify against the capture →
  write-decision), unified **upward** (Claude's fetcher, not our urllib) so coverage rises for
  both and neither is stricter. The trust tiers (P5, built) make a shared search-fed fetch safe
  (non-official quotes are tier-limited). Supersedes "two separate calls, task must not search":
  still two extracts + two caches, but one shared fetch pass feeds both (cheaper). Fetch fixes
  A/B/C demoted to optional local fallback. Re-scoped: **P6 = the substrate** (P6a capture-probe
  T8 → P6b task extract on substrate → P6c date-verify analogue T7), P7 unchanged. New unknowns
  T6 (one pass vs two), T7 (date-on-page check), T8 (capture plumbing). **Plan only — no code
  yet;** P5 code unchanged and still valid (the substrate builds ON P2's finder + P5's tiers).
- **2026-08-25** — **P5 IMPLEMENTED** (code-complete; branch `P5-P7-deadline-and-task-tracker`,
  dev server on :8001). Shared trust infrastructure, all five deliverables:
  - **`../../db/trusted_aggregators_schema.sql`** (new) — domain-pk allowlist, `status`
    trusted/blocked (absent row ⇒ pending), ALTER-then-CREATE, seeds `lumiere-education.com`
    as trusted. **Manual DDL step — not yet run in Supabase.**
  - **`aggregators_common.py`** (new, stdlib-only, repo root) — the ONE read side both
    features share: `normalize_domain` (scheme-aware, subdomain-safe), `AggregatorPolicy`
    (`.classify` → trusted/blocked/pending, blocked wins, subdomain-suffix match),
    `domain_matches`, `load_aggregator_policy` (never raises; missing table ⇒ present=False,
    everything pending), `get_policy` cached + `invalidate_policy_cache`.
  - **Deadline rung 4** wired into `check_deadlines.py`'s escalation loop: `RUNGS` gains
    "trusted third-party" (focus filled with the allowlist at round time), `ESCALATION_RUNGS`
    4, reached only when rungs 1-3 fail AND the allowlist is non-empty, its sources
    trust-filtered before phase 2 (own-site rungs unfiltered — recall unchanged), dates
    forced estimated via the focus. `_load_trusted_domains()` lazy-loads (cached) so neither
    call site changed. Empty allowlist ⇒ rung 4 no-op ⇒ pre-P5 behaviour preserved.
  - **Serve-path tier filter** in `app/services/action_items.py`: `payload()`/`_servable()`
    drop `source_tier` pending/blocked (defense in depth; no-op until P6 tags tiers; legacy
    untagged items pass).
  - **Console Sources tab** (`ops/core.py` CRUD + `ops/admin.py` 3 routes +
    `ops/admin_console.html` view): list/trust/block/remove domains, counts, degrade-to-setup
    notice. Writes invalidate the policy cache (same process). Browser-verified.
  - Tests: new `tests/unit/test_aggregators_common.py` (normalize/classify/policy/cache +
    serve filter); rung-4 loop tests + updated own-site-rung count in
    `test_check_deadlines_helpers.py`. **999 pytest green.**
  - **Rung 4 PROVEN LIVE 2026-08-26** ($0.146, no DB writes). Gating: THINK ec17921 rungs
    1-2 found the prior-cycle basis via the guidelines PDFs and stopped → rung 4 skipped;
    Harvard ec18392 rung 1 found its REAL site (`harvardresearch.org`, though the catalog
    url `hcura.org` is stale) → rung 4 skipped. Both confirm rung 4 is the last resort.
    Positive path (direct rung-4 invocation on THINK): focus carried `lumiere-education.com`,
    the model returned 9 lumiere URLs + 1 Wikipedia, the Wikipedia hit was **dropped by the
    trust filter** so only trusted sources reached phase 2, and phase 2 emitted 3 dates all
    `estimated=true` noting the lumiere guide. Both traced "hard" rows are now handled by the
    escalation loop's own-site rungs — rung 4 is genuinely rare, which is the intended shape.
  - **Not done (as scoped — checkpoint before P6/P7):** the task aggregator discovery (P6)
    and frontend trust gradient (P7).
- **2026-08-25** — Created as the merged main plan from `../archive/DEADLINE_CREATION_PLAN.md` and
  `../archive/ACTION_ITEMS_TRUST_PLAN.md`. Deadline decisions all resolved; task-trust decisions carried
  forward as open. Nothing implemented.
- **2026-08-25** — **P0–P4 IMPLEMENTED** (operator go-ahead; model decision: Haiku everywhere,
  no A/B; paid-run policy: ask each time). All verified via unit tests + tsc; NO paid run yet.
  - **P0** — `generate_action_items.py` runs on **Claude Haiku 4.5** (`call_gemini`→`call_claude`,
    cost via `claude_common`, `ANTHROPIC_API_KEY`); `app/services/action_items.py` gated on it;
    route records under surface `claude`/Haiku model so `provider_for_model` → Anthropic.
    Console `api` label updated. Verification layer untouched.
  - **P1** — task **7-day on-demand TTL** in `action_items.resolve()` via `_is_fresh` on
    `action_items_checked_at` (never-stamped/failed rows read stale and self-heal; verified
    lists served free 7 days). New tests in `test_action_items.py`.
  - **P2** — **escalation loop** in `check_deadlines.py`: `RUNGS` (current→prior→subpages),
    `ESCALATION_RUNGS=3`, per-round `max_uses:1`, `_search_round` + `_parse_signals`
    (SITE_REACHED / FOUND_CONFIRMED_DATES / FOUND_PRIOR_CYCLE_BASIS tail lines), early-exit,
    union-of-sources into phase 2, **prompt caching** (`cache_system`). Built as the reusable
    "program source finder" seam for P6. `FOUND_CONFIRMED_DATES` requires the opening date so a
    deadline-only row still climbs to the prior-cycle rung. Rung 4 (trusted third-party) left
    for P5.
  - **P3** — `check_one` now returns `site_reached`; `deadline_write_decision(..., site_reached)`
    gained `unreachable-fallback` (empty + own page never reached → no write/stamp, auto-retry).
    Both call sites (batch + interactive route) updated. Full matrix unit-tested.
  - **P4** — first-class **`rolling`** status: `VALID_STATUS`+`EMPTY_IS_VALID_STATUS`, prompt
    guidance (both phases), write carve-out (writes empty like `not_running`); client
    `computeProgressStatus`→`in_progress`, `cycleYearShift` guard, all 4 status accept-lists,
    an "Open now" badge + "apply anytime" note on the Quest Log card. tsc clean.
- **2026-08-25** — **Tracker freshness / SYNC model** (new; extends §6 "Check for updates").
  Root cause of "old data on :8081": the per-user tracked item in `users.data` is a snapshot
  frozen at add-time, and nothing propagated a catalog update to an already-tracking user
  except the PAID "Check for updates" button. Fix = separate **SYNC (free, mirror catalog
  cache)** from **VERIFY (paid, re-run the check)**:
  - New free read-only batch endpoint **`GET /api/tracker/sync?ids=`** (`get_cached_tracker_data`
    in `app/services/deadlines.py`): one PostgREST read, NO model call, returns each row's
    cached status/dates/tasks. `source=cached` when `dates_last_checked_at` set (verified — an
    empty list may clear a stale snapshot, e.g. rolling), else `unverified-cache` (won't wipe).
    Id list sanitised (`_safe_ids`, injection guard) + capped (`MAX_SYNC_IDS=300`).
  - Client **`syncTrackerFromCatalog()`** (`trackerStore.ts`): throttled (5 min; login forces),
    merges via the extracted shared helpers `applyDeadlineToTrackerItem` /
    `applyTasksToTrackerItem` (now also used by the paid `refreshTrackerDeadlines`), preserves
    per-user state + Google Calendar ids, saves back only on change.
  - **Triggers:** app-open/login (AuthContext, keyed on `userid` so it fires once per real
    login, not every token refresh) + Quest Log focus + Home Base focus. No interval (rejected).
    "Update now" stays the paid VERIFY path.
  - **"Last checked" line now stamped by the free sync** from the catalog's freshest
    `dates_last_checked_at` (not the sync wall-clock) — `TrackerInfo.dates_last_checked_at`
    added; `syncTrackerFromCatalog` returns `lastCheckedAt`; the Quest Log renders it in local
    time. So the line reads "Last checked: <when the DATA was verified>" instead of "never" on
    a fresh load of already-verified data. `_lastCatalogStamp` module singleton lets a
    throttled call still supply the stamp.
  - Verified LIVE in-browser (dev account seeded STALE, sync corrected on load): KCLS shows
    "OPEN NOW"/Happening Now, Harvard "Happening Now", THINK dates recovered, and the header
    reads "Last checked: Aug 25, 2026, 6:51 PM". tsc clean; 971 pytest green (+4 sanitizer
    tests). Deferred knob (option 3): auto-verify the most-stale N items on sync — ties to the
    plan's deferred freshness item 2.
- **2026-08-25** — Added **P11 (LAST phase): calendar UI for ongoing/rolling submissions**
  (§9 Future). A `rolling` program has no dates, so it never appears on the month-swimlane
  Calendar view — only in List view. P11 adds an "Open now — apply anytime" band to the
  calendar. Client-only, depends only on the shipped `rolling` status. Also recorded the
  "Last checked" decision rule: `syncTrackerFromCatalog` shows the **freshest** (max)
  `dates_last_checked_at` across tracked items (consistent with the paid button; can understate
  per-item staleness — per-item recency is the deferred F2 provenance surface).
  - **Not done this session (as scoped):** P5–P9 (aggregators, frontend trust gradient, Gemini
    producer collapse, refresh-both), P10. Backend deadline changes are LIVE-code but unproven
    on real rows — a paid `--ids ec17921 ec18286 ec18392 --preview` then live run is the next
    step, pending per-run approval. Interactive-path latency now scales with rung count on hard
    rows (up to 3 sequential searched calls); watch it on the first live check.
