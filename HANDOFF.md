# HANDOFF — Highschool Wingman

## Project
Static vanilla-JS single-page app ("Highschool Wingman" — finds/tracks extracurricular
opportunities for high schoolers) at **`C:\Users\shama\Documents\wingman`**. No build step,
Tailwind via CDN. `CLAUDE.md` exists in the repo root with architecture notes — read it first,
it's kept current. This folder **is** a git repo (`origin` =
`https://github.com/bluefeather78/wingman.git`, branch `main`).

---

# CURRENT THREAD: Opportunity matching — FULL PORT BUILT + LIVE (2026-08-30)

**This thread runs in a separate worktree**: `opportunity-matching-improvement-fb6134`
(branch `claude/opportunity-matching-improvement-fb6134`). Check which worktree a fresh session
opens in before assuming file state. **This worktree NOW HAS `node_modules` AND `.env`** (an
earlier version of this note said it didn't — that is no longer true): `npx tsc --noEmit` runs
clean here, the pytest suite is green, and the dev server hits live Gemini/Supabase. The API
server on **:8000** is run from THIS worktree (`./restart_server.ps1`); Metro serves the new
frontend on **:8091** (NOT :8081 — that's the separate `opportunity-matching` checkout with the
old code). The in-app Browser pane can't dispatch synthetic clicks to RN-web, so UI
interactions are verified via real JS-dispatched DOM clicks and at the API level.

## STATUS (2026-08-30) — the reconciled base: backend+embeddings pipeline wearing the ported UI

**Two parallel matchers existed.** A separate session built branch `opportunity-matching` — a
**client-side** matcher (keyword+IDF recall, browser funnel/curation, a **batch**
`parse_eligibility.py` → `eligibility_flags` column) with a polished Fresh Finds UI behind
`EXPO_PUBLIC_NEW_FINDER`. THIS branch forked from `ab7ab03` (before that work) and independently
built the **backend-orchestrated + embeddings** design (the P5 that branch deferred) + a live
quote-verified eligibility guard. **Shama decided (2026-08-30):** keep THIS branch's
backend+embeddings as the base, **port the `opportunity-matching` UI onto it, backend owns the
funnel.** Neither branch is merged to `main`; which one becomes `main` (and retiring the other)
is the open decision. Full status table: **`OPPORTUNITY_MATCHING_PLAN.md` → "Implementation
status (2026-08-30)"** (that doc was reconciled in the same pass as this one).

**What's live-verified now:** a pre-recall SETUP screen (interest + budget + timing) → recall
(embeddings, interest-focused + cost/time-filtered) → funnel (engagement filter → outcome rerank
→ eligibility → vibe, backend-owned) → curated ≤10 with journey-contextual "why you", PLUS "Show
my matches now" → the full remaining pool paginated 10/page (client-side, free). Ported UI in
`frontend/src/features/freshFinds/ShortlistView.tsx` (SearchSetup, RungStep, ShortlistView/Card,
FullListView, NotInterestedModal, ReviewDrawer), wired in `finder.tsx`; legacy browse grid kept
for the browse path. 1,509 rows embedded; eligibility eval 9/9; all funnel dimensions verified on
:8091 (see the funnel section in OPPORTUNITY_MATCHING_PLAN.md's Implementation status for the
filter-vs-rerank map + config knobs).

**Funnel dimensions (2026-08-30), the shape a fresh session most needs to know:**
- **Pre-recall SearchSetup** asks interest + budget + timing before the vector match. Interest =
  themes AND passion/research **projects** (selectable; projects boosted ×1.2 in recall). Budget
  (`price`) + timing (`season`) are recall FILTERS (`recall_cost_ok`/`recall_time_ok`), so the
  100 is already affordable + available.
- **In-funnel order:** engagement (LOCAL pool-derived FILTER on `type`, + free-text "Something
  else" that reranks) → outcome (pool-derived RERANK) → eligibility (citizenship/hard_demographic,
  the ONLY model-classified axes now) → vibe. Filters gate on CURATE_AT; rerank on POOL_FLOOR.
  **If a PROJECT was picked in setup (`project_focus`), a project-goal RERANK question (M8) replaces
  engagement + outcome** ("what do you want to do with your project", pool-derived, project-framed).
- **Review drawer** shows ABOUT YOU (grade/location) + YOUR SEARCH (interests/budget/timing from
  `setupChoiceRef`) + YOUR ANSWERS (in-funnel picks).
- **"Why you" is contextual to the whole journey** — curation gets `collect_preferences` +
  `describe_funnel_choices` and the prompt (M8) requires the reason to cite a profile specific
  and/or the student's choices (enjoy/want/budget/timing).
- Retired: the binary `output` vibe axis; cost/time as post-recall funnel rungs (moved pre-recall).

**Port/UX + funnel commits (newest last), on top of the backend-spine commits below:**
- `b67178f` funnel+shortlist UI · `e2b6e14` curate_now escape · `9ab0cd9` **M8** vibe questions ·
  `22bf299` axis-dedup guard · `f82bf09` FUNNEL_MAX_TOKENS fix · `0350ea9` full paginated list +
  restart loading · `ca57c83` interest-before-recall · `2a5feec` engagement filter · `660c8db`
  **M8** outcome rerank + sequencing fix · `13d36cd` pool-size header fix · `3d955cb` local
  cost/time filters · `a10bee2` **M8** cost/time pre-recall · `acddde4` projects feed recall
  (boosted) · `61fa750` **M8** contextual "why you" · `2848abc` **M8** project-goal question ·
  `1f1af43` review drawer shows setup choices + project-focus wiring.

**Remaining:** full-list ("Show my matches now") cards use free local reasons, not the
journey-aware curation reason (deliberate — no model call on that path); eligibility caveats on
cards (DEFERRED — another M8 curation-prompt change); Phase C polish (EntryScreen ported but not
wired — finder uses its own hero; ManageCriteria not ported); remove the now-unused `curate_now`;
decide which branch becomes `main`.

### Earlier: backend spine (2026-08-29)

Recall + curation run SERVER-SIDE (the embedding vectors live server-side — shipping ~9MB/load
to clients would regress mobile). Backend-spine commits (branch
`claude/opportunity-matching-improvement-fb6134`):
- `d07d978` — retire the 17-bucket subject_tags vocabulary from both catalog agents (M8/M9).
- `0916f81` — recall core (`app/services/matching.py`: numpy cosine, LOOSENED grade_min filter
  w/ rising/age escapes, geo-scope, content hash, `recall()`) + eligibility quote-verification
  guard (`app/services/eligibility.py`). match_vector_schema.sql; numpy in requirements.txt.
- `cbf48bb` — M9 Gemini embedding call (`gemini_common.call_gemini_embed`, EMBED_MODEL=
  gemini-embedding-001 @ 768d) + activation-gated hook (`app/services/embeddings.py`).
- `21e04ae` — M8 funnel + curation PROMPTS + code guards (`app/services/funnel.py`,
  `app/services/curation.py`).
- `8803e53` — M9 `POST /api/match` + `run_match` (`app/services/match_pipeline.py`);
  match_vector into the cache + STRIPPED from the client `/api/opportunities` payload.
- `c9ccae8` — cost feature signatures (match_curation / match_funnel).
- `370f95f` — Phase 7 eligibility eval scorer + 9 seeded labeled cases (`matching_eval.py`).
- `95c2c82` — degrade the catalog fetch when match_vector isn't migrated (found live).
- `93b50ee` — **Phase 3 FRONTEND**: `finder.tsx` "Suggest for me" → `/api/match`, student-blob
  assembly (grade+location from basics slot, themes from filterTags slot, highlight_projects
  from `extractHighlightProjects`); httpClient.match(); geo state-name↔code normalization.

**To re-run / operate:** `python backfill_match_vectors.py --dry-run|--yes-really` (idempotent;
re-run after activating/refreshing rows so new/edited rows get embedded — there is no inline
activation hook yet). `python matching_eval.py --run` grades eligibility (watch under-exclusions).
The frontend change shows on Metro (`:8081`), NOT on `:8000` (which serves the prebuilt bundle).

**NOW BUILT (were "not built" as of the 2026-08-29 spine):**
- **PHASE 4 — progressive funnel: DONE**, and extended 2026-08-30 with backend-owned
  **behavioral vibe rungs** (`9ab0cd9`, M8) + the "Show my matches now" full paginated list
  (`0350ea9`). `POST /api/match` `funnel:true` runs the stateless rung flow; `finder.tsx`
  renders it via the ported `RungStep`.
- **PHASE 6 — retire the 17-bucket logic: DONE** (`3e7a58c`) — `inferSubjects`, `VALID_SUBJECTS`
  (all copies), the `filterValues` slot, `mock_infer_subjects`, and the taxonomy quiz
  (`QUIZ_ROOT`/`QUIZ_SUB`) are deleted; the catalog-agent prompts were retired earlier
  (`d07d978`). The SUGGEST path uses `/api/match`; the FORM/browse path keeps `preFilter`
  (keyword + type + grade) deliberately.

**Still open:**
- Eligibility caveats on cards (DEFERRED — another M8 curation-prompt change).
- Phase C polish: `EntryScreen` ported but not wired (finder uses its own hero); `ManageCriteria`
  not ported.
- Remove the now-unused `curate_now` backend capability (the full-list view replaced it).
- Inline activation-hook wiring (immediacy; `backfill_match_vectors.py` covers correctness).
- Phase 7 real-catalog labeled samples (the 9 crafted cases run today; catalog cases are additive).
- Decide which branch becomes `main` (`opportunity-matching` vs this one); retire/reconcile the
  other. Nothing merged to `main`.

**Deferred (your calls):** `cleanup_subject_tags.py` run; embed-cost recording (sub-cent gap,
documented in c9ccae8).

## PHASE 4 — the progressive elicitation funnel (design — NOW BUILT; kept for the rationale)

> Superseded by the shipped funnel (see the status block above). The design notes below record
> WHY the stateless pool-ids rung contract was chosen; the funnel (filter axes + vibe rungs) is
> live in `app/services/funnel.py` + `finder.tsx`.


**Goal:** between recall (~100) and curation (≤10), ask the student 1-2 discriminating
questions to narrow the pool — the "100 → 60 → 30 → curate" experience — instead of curating
the full recall pool immediately as the Phase-3 MVP does. Each answer also deepens matching
for THIS search only (session-only per the whitelist; grade/location already persisted in P2).

**What already exists (committed, tested):** `app/services/funnel.py` — `FUNNEL_QUESTION_SYSTEM`
(one call per rung; whitelist = cost/time_commitment/citizenship/hard_demographic),
`apply_rung_answer` (deterministic narrowing, T1/T2/T3 enforced, quote-verified cuts revert),
`count_after`, `POOL_FLOOR`. 9 tests in `test_funnel_apply.py`.

**What's needed:**
1. **Endpoint(s).** Recommended shape — keep it STATELESS (no server session), client holds
   only the answers dict:
   - `POST /api/match` gains a `funnel_answers` object (already in the student blob) and a
     `mode`: when `mode="funnel"` and the pool is still large / rungs remain, return the NEXT
     question instead of curating; when answers exhaust the useful axes or hit the rung cap,
     curate and return the ≤10 (the current behavior).
   - Response for a rung: `{done:false, axis, question, options:[{label,value,count}], current_count}`.
     The per-option `count` is the live counter — computed server-side with `count_after` so
     the client shows "→ 30" beside each option with NO extra round trip.
   - Each answer round-trips (`POST /api/match` again with the answer appended to
     `funnel_answers`); the server re-runs recall+apply (all cheap, server-side) and returns
     the next rung or the final list.
   - **Decide: embed caching across rungs.** Re-running recall each rung re-embeds the student
     themes (a paid embed call per rung). Options: cache the theme vectors per (userid, profile
     hash) in-process for a few minutes, OR have the client send back the pool ids so the
     server skips recall entirely on rungs 2+ (it only needs the funnel model call + apply).
     The pool-ids approach is simplest and avoids re-embedding — recall runs once (rung 0),
     the client carries the surviving ids, each rung narrows them.
2. **`apply_rung_answer` is Python (server-side)** — so answer application stays on the server
   (it needs the eligibility guard for citizenship/demographic cuts). The client never applies
   cuts itself; it only displays the counts the server returns.
3. **Frontend (finder.tsx):** a funnel stage between the suggest trigger and the results —
   render the question + options with live counts, a "this leaves N — relax?" affordance when
   `current_count` nears `POOL_FLOOR` (T3), and a back-up control. On the last rung / stop,
   transition to the existing results rendering (unchanged). Deprecate the taxonomy quiz
   (`QUIZ_ROOT`/`QUIZ_SUB`) here (Phase 6 item, gated on this shipping).
4. **Cost:** each rung is one funnel-question model call (already signature-tagged
   `match_funnel`); the per-search shape becomes ~4-5 rung calls + 1 curation call. The rung
   cap (`POOL_FLOOR` + ≤5 rungs) is the latency/cost budget — measure the per-rung round-trip.

**Build order for Phase 4:** endpoint first (backend, unit-testable with stubbed model — the
apply logic already is), then the finder UI. Gate: funnel never cuts on a preference axis
(already unit-tested in `test_funnel_apply.py`), never dead-ends (count-guard + relax), ≤5 rungs.

---
*(Sections below this line are HISTORICAL — the design-phase wrap-up and the older link-health/
scraper threads. Superseded by the status above; kept for provenance.)*

## Goal
Redesign how a student gets matched to opportunities — from a 7-kind fan-out that dumps
40-70 results (a search experience) to a curated ≤10 list (a curation experience), with
eligibility read live from raw text instead of a batch-enrichment agent, and semantic
recall replacing a fixed 17-subject vocabulary. Two living documents carry the design:
- **[OPPORTUNITY_MATCHING_PLAN.md](OPPORTUNITY_MATCHING_PLAN.md)** — the source of truth.
- **[Wingman Match Funnel](https://claude.ai/code/artifact/5e03d570-cf56-42bd-9e26-382bbaee644e)**
  — the same design as a visual primer/artifact, kept in sync with the plan doc.

**Status: design is extensive and mostly captured in both documents, BUT the last few turns
of this conversation agreed real changes in chat that are NOT YET written into either
document. Read "Decisions agreed in chat but not yet in the docs" below FIRST — reconciling
those is the actual next step, before writing any implementation code.**

## What's committed
One commit, `d07d978`, on branch `claude/opportunity-matching-improvement-fb6134`:
"Retire the 17-bucket subject_tags vocabulary from both catalog-writing agents" —
`refresh_opportunities.py` and `scrape_opportunities.py` only. This was flagged as an
**active, ongoing bug independent of the rest of the redesign**:
`refresh_opportunities.py`'s `clean_update_dict()` was silently discarding any `subject_tags`
value outside a hardcoded 17-item list on every routine refresh of the ALREADY-LIVE catalog
— stripping specific tags back toward broad categories every time a row was touched (why
"STEM" alone tagged ~45% of the catalog). Both agents' prompts now ask for free-form,
specific tags; the discarding filter is gone, replaced with plain shape validation.

## What's written but NOT committed / NOT run
- **`match_vector_schema.sql`** (new, repo root) — the one-time manual DDL for Phase 5's
  catalog-side embedding columns (`match_vector`, `match_vector_hash`,
  `match_vector_computed_at`), matching this repo's `*_schema.sql` convention exactly.
  **RUN — user confirmed this at the end of the session.** (Run from outside this worktree,
  since there is no `.env` here — gitignored, doesn't carry over into a new worktree — so
  nothing in this worktree could have reached Supabase to run it.) Not yet verified in this
  conversation which columns actually landed or whether the index created cleanly — worth a
  quick confirmation read (`select column_name from information_schema.columns where
  table_name='opportunities' and column_name like 'match_vector%'`) before Phase 5 code
  starts relying on it.
- **`cleanup_subject_tags.py`** (new, repo root) — one-time scrub of the retired 17-bucket
  words out of existing `subject_tags` data (case-insensitive, active+inactive rows, `[]` for
  rows that were bucket-words-only rather than skipping them). `--dry-run` / `--yes-really`,
  same convention as `clear_deadline_cache.py`. **User explicitly said "I will not run the
  script just yet"** — held pending, not blocked on anything specific, just not run yet.
  Delete this script once it has been run (its docstring says so — it carries its own 6th
  copy of the retired word list).
- Both plan-doc edits (the whole design conversation) and the artifact are saved/published,
  but see the reconciliation gap below.

## Decisions agreed in chat but NOT YET written into the docs — DO THIS FIRST

1. **Quote-verification widened to any candidate field, not just `eligibility`.** Agreed:
   a restriction can be stated in `summary` too (a program whose description says "designed
   exclusively for NYC high schoolers" with nothing in the dedicated eligibility column).
   The curation prompt's schema needs a new field, `exclusion_source_field` (naming which of
   `name`/`org`/`summary`/`eligibility` the quote came from), and the verification code
   should check the quote against that named field FIRST, falling back to checking every
   other text field on the row before discarding — so a benign field-mislabel doesn't wrongly
   throw away a real, verifiable quote. **Not yet reflected** in Phase 1's "Zero-hallucination
   guard" section or the candidate-blob JSON example in the plan doc, nor in the artifact.
2. **The funnel-question prompt stays whitelist-only — user aligned with keeping it as
   designed**, after pushback that relaxing it to "anything that makes sense" reopens exactly
   the failure T2 was built to prevent (a statistically sharp but decision-irrelevant split,
   e.g. "45 university-run, 15 nonprofit-run"). **No doc change needed here** — this confirms
   the existing text is correct, it was a real question worth asking, not left in limbo.
3. **Grade-source duplication is RESOLVED** (consolidate onto `basics.grade`, the
   LLM-extracted field, over the regex-based `filterValues.grade`) — **this one IS already
   written** into the Open Questions section of the plan doc, marked `[x]` resolved, with the
   reasoning (the regex misses "I'm a 10th grader" — "grader" doesn't match `\bgrade\b` — and
   raw age statements like "I'm 15" entirely; explicit class-year words like "freshman" ARE
   already handled by the regex, so that specific example the user first raised wasn't
   actually a gap, but the underlying instinct was right for other real phrasings).
4. **A new "Phase 7 — Validation & eval framework" was requested and sketched in
   conversation, but NEVER WRITTEN to either document.** This is the biggest gap — capturing
   the sketch here so it isn't lost:
   - **Labeled datasets as durable files, not scratchpad notes.** The demographic
     hard/hard-scope/soft sample (22/31/33 split) exists from the earlier Phase 0
     investigation but needs saving as a real file if it isn't already one. The geographic/
     residency sample (residency-gate vs. soft-framing vs. "just says where it runs")
     doesn't exist yet — per the resolved Q2 above, the plan is to write Phase 1's prompt
     FIRST and validate against this sample once it's pulled, not block on pulling it first.
   - **A scoring/runner script**, modeled on this repo's own `grade_mailing_lists.py`
     (`--score` computing precision/recall against a labeled set, repeatable any time the
     prompt changes — not a one-off measurement).
   - **Automated unit tests** (added to the existing pytest suite, not new infra) for: the
     quote-verification logic (deterministic, fixture-based, no live model call needed per
     test run), the funnel's T1/T2/T3 code-enforced invariants (a preference axis literally
     cannot appear in a cut path; every question comes from the whitelist; the funnel never
     dead-ends without offering a relax), and the embedding refresh hook's row-state logic
     (fires iff `is_active` is/becomes true).
   - **A lightweight qualitative review workflow for Phase 3's curation quality** — a
     worksheet-style process (same shape as `grade_mailing_lists.py --worksheet`), not full
     automation, given the small current user base (~15 users): a fixed N profiles, a human
     scores each slot's genuine fit, repeatable structure rather than ad hoc.
   - **Cost tracking wired into EXISTING infra**, not a new system: add feature signatures
     for Phase 1's and Phase 5's new calls to `_FEATURE_SIGNATURES` so real measured cost
     surfaces automatically in the existing Cost-per-user dashboard.
   - **Sequencing note:** this needs to run concurrently with / slightly ahead of Phase 1,
     not strictly after Phase 6 — Phase 1's own gate already says "needs a labeled sample
     pulled... before this phase can be graded," so Phase 7's tooling is what that gate
     actually depends on, not a phase that waits for 1-6 to finish first.
   **Next step: write this up as an actual Phase 7 section in the plan doc (right analog to
   Phases 1-6's format: depends-on/delivers/gate/cost) and add it to the artifact's phase
   spine, THEN revisit whether it changes the sequencing note on Phases 1/5's own gates.**

## Explicitly deferred, unresolved when the session ended
Two questions the user said "let's talk about next" and then the conversation moved to other
things before circling back — **still open, pick these up first in the new session**:
- **Q3 — where to start building.** Phase 2 (student blob assembly) has zero dependencies
  and is pure free code — the only phase buildable today with nothing blocking it. Phase 1
  can be built in parallel but can't be graded until Phase 7's labeled samples exist. Never
  got an explicit confirmation of the starting point.
- **Q4 — explicit M8/M9 sign-off for Phase 1's actual prompts.** The `refresh_opportunities`/
  `scrape_opportunities` fix (committed, `d07d978`) DID get explicit sign-off ("commit now")
  — that one's done. Phase 1's bigger new prompts (the curation call, the funnel-question
  call) have been reviewed and refined in chat (items 1 and 2 above) but **have not been
  explicitly approved for implementation** as their own dedicated commit yet. Per the
  marquee rule, general "ready to build" enthusiasm earlier in the conversation doesn't
  count as that sign-off — ask explicitly before writing and shipping these two prompts.

## Full prompt before/after review (already shown to the user in chat, not yet saved as a doc appendix)
Every prompt that changes across Phases 1-5, with real current text pulled from the code
(not paraphrased) — offer to save this as an appendix in the plan doc if useful going
forward, since it was assembled once already and shouldn't need re-deriving:
1. **Curation prompt** replaces `rankCandidates`'s system prompt (`ranking.ts:141`, quoted
   verbatim in the conversation) — collapses 7 per-kind calls into 1 cross-kind call, adds
   `eligibility` to the payload, adds the quote-verification requirement (now widened per
   item 1 above).
2. **Funnel-question prompt** — genuinely new, no predecessor; corrected mid-conversation
   from "one call producing 1-2 questions" to "one call per rung, one question per call,"
   since the funnel is adaptive/sequential by design (Phase 4).
3. **`inferSubjects`'s prompt** (`ranking.ts:112`, quoted verbatim) — deleted outright, no
   replacement prompt (an embedding call takes raw text, not an instruction).
4. **`refresh_opportunities.py`'s `subject_tags` field description** — before/after shown,
   **already implemented and committed** (`d07d978`).
5. **`scrape_opportunities.py`'s `subject_tags` field description** — before/after shown,
   **already implemented and committed** (`d07d978`).

## Everything else — the full design, already correctly captured in the docs
The plan doc and artifact already correctly reflect (verified in a dedicated consistency
pass this session, which also fixed several stale cross-references left over from an earlier
edit — see the doc's own history if curious): the curated-≤10 output model; the three-stage
recall→funnel→curation spine; live eligibility reasoning replacing batch enrichment (Phase 1);
the student-blob assembly incl. the `filterTags` critical-path promotion (Phase 2); the
cross-kind curation pass retiring the 7-kind fan-out (Phase 3); the adaptive per-rung funnel
with T1/T2/T3 code-enforced traps (Phase 4); embedding-based semantic recall with the
activation-gated refresh hook, the corrected recall filter set (grade has ZERO influence at
recall, type only filters in the separate form/quiz path, only `status`/`is_active` ever
exclude a row), and the full 17-bucket-list retirement scope, including the profile/catalog
storage asymmetry (delete the `filterValues` slot outright; do NOT delete the
`subject_tags` column, it's still load-bearing for the embedding) (Phase 5); the gated
old-logic retirement checklist (Phase 6); the two-bucket persistence model (only grade +
location ever stored; citizenship/hard-demographic/cost/time-commitment are session-only,
asked live, never persisted — a real correction from an earlier draft that also
had a third "preferences" store, which got removed).

## Next steps, in order
1. Reconcile the "agreed but not written" gaps above into both documents — especially
   writing the actual Phase 7 section, and updating Phase 1's guard mechanism for the
   any-field quote verification.
2. Resolve Q3 (starting phase) and Q4 (explicit prompt sign-off) with the user.
3. Once Phase 7 exists on paper, decide whether to pull the geographic labeled sample before
   or alongside writing Phase 1's actual prompt code (the user's Q2 answer was "prompt first,
   validate once the sample exists" — Phase 7's tooling is what that validation runs on).
4. `match_vector_schema.sql` is already RUN (confirmed by the user at session end) — worth a
   quick confirmation read of the new columns before Phase 5 code starts relying on them.
   `cleanup_subject_tags.py` is still NOT run (user said "not just yet") — run whenever the
   user is ready; needs an environment with real Supabase credentials, which this worktree
   does not have.
5. Do not touch anything under "Marquee decisions" (M8/M9) without the explicit sign-off
   pattern already used for the one committed fix — review, then an explicit go-ahead, then
   its own dedicated commit.

---

# PRIOR THREAD: Catalog link health + transferring the scraper's lessons (2026-08-23)

## Goal
Two things the user asked for:
1. Take what the scraper rewrite learned and apply it to the **other agents'** accuracy.
2. **Check every existing opportunity's URL**, flag the broken ones for review, and
   **deactivate them until a person re-activates them.**

3. Then, on top of (2): **attempt to find the right URL before flagging**, run that over the
   already-flagged rows, and **restore the ones that pass**.

4. Then: an A/B to settle WHY the other agents never search, and **make check_reviews and
   check_deadlines two-phase** like the scraper, at `MAX_SEARCHES = 1`.

**Status: all four done, verified against the live database. Live runs: 148 rows deactivated
for dead links, 13 repaired and RESTORED to active (catalog 1374 -> 1239), plus a $0.11 A/B
and one verification row through each rebuilt agent. Nothing is committed.**

## The measurement that drove everything (free — HTTP only, no API calls)
All 1374 active rows, checked live:

| | rows | |
|---|---|---|
| live | 1029 | |
| **dead** | **137 (10.0%)** | 135 x 404, 2 x 410 |
| unverified | 208 | 112 x 403, 41 TLS, timeouts, resets, 8 DNS |

One catalog row in ten sent a student to a page that is not there — and they are **real
programs with rotted links**, not junk: `smysp.stanford.edu`,
`jkcf.org/our-programs/young-artist-award/`, `training.nih.gov/.../aip_hs/`. That is why
the answer is "deactivate and queue for review", never "delete".

A second measurement, on `review_sources` (the citations `check_reviews.py` shows students
as evidence for a legitimacy verdict): **199 of 1469 URLs (13.5%) are dead.**

## Part 1 — `check_links.py` (new agent, the sixth) — DONE AND RUN

**It is FREE.** Plain HTTP, no model, no key beyond Supabase. The only agent that can be
run without cost approval, and the only one whose `--dry-run` genuinely costs nothing.

**The rule, and it is the whole design: only EVIDENCE OF ABSENCE deactivates.**

    deactivate   404, 410, malformed URL, hostname does not resolve (NXDOMAIN)
    flag only    403, 429, TLS failure, timeout, connection reset  -> row stays LIVE

403 is ~9% of this catalog and TLS failures another 41 rows. Those sites refuse *our*
client; a student's browser has a different root store and loads them fine. Treating
"connection failed" as "page is gone" would have deactivated ~150 working rows on the first
run. `url_validate._is_dns_failure()` is what tells a real NXDOMAIN (8 rows, all retired
university subdomains — `smysp.stanford.edu`, `bri.ucla.edu`,
`globalyouthprogram.wharton.upenn.edu`) apart from the 41 TLS/timeout failures that arrive
wearing the same `URLError` class. **Do not collapse them.**

**Two passes, always.** Anything that looks dead is re-checked before a write. Free, and the
only thing between a CDN hiccup and a deactivated row. Measured on the 137: **135 unchanged,
and 2 rows moved INTO dead** — it corrects in both directions.

### The live run
`agent_runs` id=53, `mode=all`, 1374 rows in 126s, **$0.00**.

- **148 deactivated** (137 x 404, 8 x NXDOMAIN, 3 x 410) — `is_active=false`,
  `moderation_status='pending_review'`, each carrying a `dead link (<code>)` flag.
- 207 flagged and left active. 355 rows written, 0 errors.
- Catalog went **1374 -> 1226 active**. Review queue went **120 -> 268**.
- Verified in the console: all 148 appear in the queue with their flags rendered.
- Full report: `agent_logs/link_check_20260823-030348.json` (every row, its code, its
  action). Written on live runs too, not just dry runs — the console's log ring buffer is
  500 lines and a full pass prints ~1400, so the deactivations scroll out of reach.

**To undo any of it:** the console's Review queue, Activate button. Nothing was deleted and
nothing was rejected — a rotted link is not a verdict on the program.
`reviewed_by`/`reviewed_at` were deliberately **left alone**, so the queue can say
"a person approved this on 08-23 and the link has died since", which is a different
situation from a row nobody has ever looked at.

### Two checks tried here and REJECTED on measured noise — do not re-add
Both live in `url_validate` and both earn their place in `scrape_opportunities.py`, where a
fresh candidate has the opposite base rate. Against the **curated** catalog:

- `is_bare_domain()` — fires on **161 of 1029 live rows (16%), and they are correct**.
  `jshs.org`, `congressionalaward.org`, `precollege.wisc.edu` are dedicated program sites
  whose homepage IS the program page.
- `domain_matches_org()` — fires on **88 (9%), about one in seven of them real**. The rest
  are university domain abbreviations no rule derives: `umd.edu`, `udel.edu`, `unc.edu`,
  `tamu.edu`, `gatech.edu`, `ucsd.edu`.

What replaced them is **`FLAG_SOFT_404`**: a deep link that redirects to a bare homepage,
i.e. the program page deleted behind a 200. Fires on **10 rows (1.0%) at ~50% precision**
(`feinberg.northwestern.edu/diversity/programs/health-professions...` and
`louisville.edu/medicine/cancer-research/.../summer` are genuine losses; `web.mit.edu/wtp/`
-> `wtp.mit.edu/` is a benign move). Ten rows at one-in-two beats eighty-eight at
one-in-seven.

### `domain_matches_org()` did get one real fix
Two-letter initialisms were missing, which was the **largest single group of false
"unrelated" flags**: `uh.edu` (University of Houston), `bu.edu` (Boston University),
`wm.edu` (College of William & Mary) all read as third-party sites. `_initial_forms()` now
builds both the every-capitalised-word shape ("bu") and the generic-words-dropped shape
("wm") — neither alone covers both — and buckets by length: 3+ chars stay
substring-matchable, **exactly 2 must equal a whole domain label**. At two characters a
substring rule fires on `edinburgh`, `columbus`, `bulldogs`. Fire rate 11% -> 9% with every
known scraper case still passing.

## Part 1b — URL repair, and 13 rows restored to active — DONE AND RUN

Asked for after the first pass landed: **try to find the right URL before flagging**, run it
over the rows already flagged, and **put back the ones that pass**.

### Proposing is cheap; accepting is the whole feature
Taking the best-scoring link on the dead URL's parent page "repairs" **72 of 148 (49%)** —
and a large share point at a **different program at the same institution**:

    ll.mit.edu/outreach/summer-high-school-internships  ->  middle-school-stem-program
    training.nih.gov/research-training/hs/aip_hs/       ->  .../pb/sip/     (AIP != SIP)
    medschool.vanderbilt.edu/imsd/high-school-summer... ->  /md/

A wrong repair is **worse than no repair**: it is a live link, so every other check passes
it, and it silently sends a student somewhere the row does not promise. So [url_repair.py](url_repair.py)
accepts nothing on similarity. **Three independent tests, each forced by a measured failure:**

1. **Title proof** — fetch the candidate, require every distinctive word of the program name
   in its `<title>`. A similarity ratio was tried and REJECTED: at >= 0.72 it accepted "Bay
   Area Entrepreneurship" -> "BootCamp Entrepreneurship" (0.76), "Summer Research Immersion"
   -> "First-year Research Immersion", "VEX Robotics Competition" -> "RECF Robotics
   Competition", and a **UC Berkeley** course -> the same provider's **Yale** one. The shared
   word is always the CATEGORY, the differing word the IDENTITY — backwards for a ratio.
2. **The name must be its own** — distinctive words are the name's **minus the org's**, so a
   match on the institution cannot stand in for a match on the program. Without it,
   "University of Notre Dame" verified against every page on nd.edu, "Jackson Laboratory
   Summer Student Program" against "Careers at The Jackson Laboratory", "Doodle for Google"
   against "Google Doodles".
3. **No lost identity word** — if the OLD url used a word to identify the program and the new
   url and its title both lack it, we landed on a sibling. This is what catches a row whose
   **name and org are swapped in the catalog** (`name='University of Notre Dame'`,
   `org='Global Scholars Program'`, `global-scholars` -> `summer-scholars`), which passes
   tests 1 and 2 and is still wrong.

**72 -> 34 -> 18 -> 13 accepted**, and the 13 are right. Losing 59 proposals to keep 13
honest is the intended trade.

### The live run — `agent_runs` id=55, `mode=flagged`, 148 rows, 110s, $0.00

**13 rows went back to active.** Catalog **1226 -> 1239**, review queue **268 -> 255**.

| | |
|---|---|
| Honors Summer Math Camp | `.../camps/Summer-Math-Camps-Information/hsmc.html` -> `.../mathworks-camps/hsmc.html` |
| Physical Therapy Summer Academy | `.../academies/pt` -> `.../academies/physical-therapy` |
| Jack Kent Cooke Young Artist Award | `/our-programs/...` -> `/our-grants/young-artist-award/` |
| Discovery to Cure Program | `obgyn/discovery/education/internships/` -> `obgyn/education/discovery-to-cure/` |
| Young Scholars Program | `jindal.utdallas.edu/external-relations/.../high-school/` -> `/ysp/` |
| Upward Bound · Sejong Korea Scholars · Pathways to Science Summer Scholars · Summer Youth Science Fellowship · ACS Project SEED · Cyber Patriot · NSLI-Y · Stanford SASI | same shape |

Verified in the database: 13 rows carry the repair flag, all 13 active, each flag naming the
**old URL**. A further **47 rows gained a `possible replacement found but NOT verified`
suggestion** — candidates that failed the tests, kept so a reviewer opens the queue with a
lead rather than a bare "dead link". That was a bonus, not the ask.

### is_active = true from code — why this is not the rule being bent
`--repair-flagged` is the ONLY code path in this repo that sets `is_active = true`. The
standing rule protects rows **no person has ever vetted** — a scraper's guess. These rows
were in the live catalog because a person put them there; a machine removed them over a
link, and the same machine has now proven the link. Restoring puts back what the automated
check took out. Bounded accordingly:

- Only rows carrying **this agent's own `dead link (` flag** — never a row a person rejected,
  never one that was never active.
- Each restored row keeps a flag naming its old URL, so the edit is auditable and reversible
  by hand.
- A row whose ORIGINAL url simply comes back to life on its own is still **not** restored —
  that stays a person's call in the console.

### Also in this pass
- **Repair runs on the normal path too**, before anything is condemned (`--no-repair` opts
  out). A future full run repairs what moved instead of deactivating it.
- `build_update` no longer re-asserts `is_active=False`/`moderation_status` on rows that are
  already inactive — in `--repair-flagged` that was 134 no-op writes bumping `updated_at`,
  which makes rows look freshly touched everywhere else in the console.
- Summary wording is **scope-aware**: in `--repair-flagged` every row is already inactive, so
  "deactivated: 134" and "flagged (left active)" would both have been false statements about
  what the run did. It says "stayed deactivated" there.
- Console: a third Scope option (*Retry rows already flagged as dead links*) and a
  *Skip the repair attempt* checkbox; argv round-trip verified in the browser.

## Part 2 — the scraper's lessons, applied to the other agents — DONE

**A model-typed URL is not trustworthy anywhere.** Three enforcement points added:

1. **`refresh_opportunities.py` no longer writes `url` at all.** This was the live hazard.
   It calls Gemini with `use_web_search=False` and wrote whatever `url` came back onto a
   **live, student-facing catalog row** — the exact mechanism behind the scraper's 26%
   dead-link rate, except overwriting curated data instead of creating new. The scraper's
   fix (take the URL from `groundingChunks`) is unavailable without search, so the field is
   simply not written; `check_links.py` owns link health and a replacement is a human edit.
   Its prompt also opened with *"YOU MUST use web_search and web_fetch"* while search was
   **off** — an instruction the model could only satisfy by answering from memory in the
   voice of a lookup. Rewritten to say it has no web access and that null is expected.
   *(Only one run exists in this agent's history and it was a dry run, so no damage has
   been done yet — which is exactly why now was the time.)*
2. **`check_reviews.py` verifies `review_sources` for free** (`clean_sources()`): kept if
   the search actually retrieved the URL (resolved from `groundingChunks`), or if an HTTP
   check finds it live; dropped otherwise, with what was dropped preserved in the dry-run
   snapshot so the loss is visible. 403/timeout is **kept** — a site blocking the checker is
   not evidence of a fabricated citation.
3. **Silent-search retry in both search-enabled agents.** Re-send the *identical* prompt
   once; do not prompt harder (`gemini_common.py`'s THIRD finding is correct). A silent call
   pays no per-search fee, so the retry is cheap.
   - `check_reviews.py`: **a still-silent call now writes NOTHING and does not stamp
     `last_reviewed_at`.** The old behaviour did double damage — a memory-derived
     `insufficient_data`, textually identical to a real search finding nothing (the file's
     own comment said so), *plus* a 30-day suppression of the re-check that would have
     corrected it. Skipping leaves the row due and the next pass re-rolls.
   - `check_deadlines.py`: retry is on **by default including the interactive path**. That
     costs a user one extra round-trip and is worth it because `server.py` caches a deadline
     answer for **7 days** — one silent, invented set of dates is served to every student who
     opens that opportunity for a week. `check_one()` now returns a **4-tuple**
     `(info, cost, searches, attempts)`; both call sites were updated.
   - **Cost is banked per attempt** in both, so an exception on the retry cannot discard what
     the first call already spent. Same fix the scraper's two phases needed.

**`find_mailing_lists.py` was deliberately left alone.** Its URLs come from regex over pages
it actually fetched, never from a model, so it has no fabrication surface.

## Console integration (verified in the browser)
Sixth card, `links`. Preview through the console resolves 1374 rows / **$0.00** / 1.4m and
builds correct argv; every checkbox (`--force`, `--flag-only`, `--sample`) round-trips.

`AGENT_CONFIGS_SCHEMA["links"]["free"] = True` is read by `estimate_agent_cost()` and the
console. Two things that had to change because of it, both about not training an operator to
ignore warnings:
- A free agent's `$0.00` must not render as *"no successful run to estimate from"*. It is a
  fact about the design, not missing history, and `provisional` is false.
- Its confirm dialog must not say the run *"spends real money on the None — plain HTTP API"*.
  Its real warning is unrelated to cost and is now said in its own words: this run sets
  `is_active = false` on rows, removing them from what students see.

## Migration still pending
**[link_health_schema.sql](link_health_schema.sql) has NOT been run** — it is a one-time
manual step in the Supabase SQL editor (`link_status`, `link_status_code`,
`link_checked_at`, `link_dead_since`). The live run above **worked without it**: the agent
detected the missing columns, warned once, dropped them from its writes, and still
deactivated all 148. What is lost until it runs is the 7-day staleness filter, so every run
re-checks the whole catalog — free, so this degrades to *slower*, not *broken*.

## Known gap (stated, not fixed)
A flag on a row that stays **active** is written to `quality_flags` but has nowhere to show:
the console's Review queue lists `is_active = false` rows only. So the 207 flagged-but-live
rows are readable only in `agent_logs/link_check_<stamp>.json`. The run summary says this
explicitly rather than leaving it a mystery. A "Link health" console card would close it.

## Verification done
- `scratchpad/test_matcher.py` rebuilt (the previous session's copy was in a scratchpad that
  is gone): **30/30**, covering every case named in this handoff plus the new two-letter
  ones and the over-match guards. Exits non-zero on regression. Also prints live-catalog
  fire rates.
- Retry logic for **both** agents unit-tested offline with a stubbed API — silent-then-search
  (2 calls), always-silent (2 calls, no write), no-retry flag (1 call), search-first
  (1 call, must not retry). No money spent.
- `classify()` / `merge_flags()` / `build_update()` exercised on every branch, including
  `link_dead_since` being preserved across repeat-dead passes and cleared when a URL
  recovers, and `merge_flags` keeping another agent's flags while replacing its own.
- `--preview` clean on `check_links`, `check_reviews`, `check_deadlines`,
  `refresh_opportunities`. Confirmed `refresh_opportunities.clean_update_dict()` drops a
  `url` key even when the model returns one.
- Console re-checked after a server restart: six cards, no console errors, run history shows
  the live pass at $0.0000.

## Part 2b — the A/B, and both search agents made two-phase — DONE (one row each, live)

### The A/B: asking for JSON is what stopped these agents searching
One row (`ec17455`, TASS/Telluride), identical research instructions, identical model and
token budget, arms **alternated** so drift could not confound them. Only the closing
paragraph differed — "respond with ONLY a raw JSON object" vs "write up what you find in
plain prose":

| arm | searched | searches | grounding chunks | cost |
|---|---|---|---|---|
| **prose** | **4/4** | 7 | 34 | $0.1053 |
| **JSON** | **0/4** | 0 | 0 | $0.0054 |

Total $0.1107, inside the $0.25 cap. It matches the history exactly: `check_reviews` had
made **22 searches across 3089 row-checks** and `check_deadlines` **59 across 1218**, both on
single JSON calls, against the scraper's prose phase 1 at 5.3 searches/seed.

**STATE THE CLAIM CAREFULLY** — a previous session over-claimed this and had to retract, and
I checked whether this contradicts that retraction. It does not. The seed-51 pair the
retraction rested on (`agent_runs` id=32 and id=33) are **both from 08-21, before the
rewrite**, so both were the old JSON prompt — and id=33 fired 6 searches. Post-rewrite id=48
was a *prose* call that fired none. So:

    CORRECT   "a JSON-shaped answer format collapses the PROBABILITY of a search"
    WRONG     "a JSON-only prompt suppresses search" (deterministic — id=33 refutes it)

Written up as the **SEVENTH finding in `gemini_common.py`** with both counterexamples named,
so this does not get flip-flopped a third time. The THIRD finding stands unchanged: there is
still no way to *force* a search, only to stop discouraging one.

### What was built
Both agents now mirror the scraper:

    Phase 1 (research)  prose out, tools on   -> keeps grounding / retrieved source URLs
    Phase 2 (extract)   notes + REAL urls in  -> strict JSON out, no tools

- `check_reviews.py` — `build_research_system()` / `EXTRACT_SYSTEM`, `research_reviews()` /
  `extract_review()`. Phase 2 gets the URLs resolved from phase 1's `groundingChunks` and is
  told to copy them verbatim.
- `check_deadlines.py` — same shape, plus **`extract_source_urls()`, which is Claude's
  answer to `groundingChunks`**: `web_search_tool_result` / `web_fetch_tool_result` blocks
  carry the URLs actually retrieved, already resolved, no redirect hop needed.
- **`MAX_SEARCHES = 1`** in both, per the user's call. Soft budget on Gemini (prompt-folded);
  a real server-enforced ceiling on Anthropic (`max_uses`).
- **`web_fetch` is deliberately NOT capped alongside `web_search`** in `check_deadlines`. It
  has no per-call fee and it is the tool that reaches the FAQ/key-dates subpages the dates
  actually live on — the prompt's whole estimation logic depends on it.
- **Phase 2 is skipped when phase 1 stayed silent** in both. Notes written without looking
  are not worth converting, nothing is written either way, and skipping keeps a fully silent
  row near the old per-row price.

### A bug I introduced and then fixed — worth knowing about
Making `check_one()` return an **empty** info on a silent call turned the existing
write-through in `server.py`'s interactive deadline endpoint into a **data-destroying** path:
it would have PATCHed empty `status`/`important_dates` over good values **and** stamped
`last_checked_at`, so the row would lose its dates and be unable to recover them for the
7-day TTL. Both paths now skip the write; the interactive one returns
`cached_deadline_payload(opp, "unverified-fallback")` and deliberately does **not** stamp, so
the next request re-rolls the search decision.

### Verified live, one row each
- `check_reviews --sample 1 --dry-run` → searched first try, **$0.0166**, and both citations
  came back **`retrieved=True`** — i.e. pages the search really returned, which is what the
  old single-call design could never establish.
- `check_deadlines --sample 1 --dry-run` → searched first try, **$0.0676**.
- Retry/skip logic for both unit-tested offline against a stubbed API: prose-then-JSON call
  order, `max_searches` propagation, silent-retry, phase-2-skip-on-silent, and the no-retry
  flag.

### Cost, measured rather than guessed

| | before (single JSON call) | after (two-phase, MAX_SEARCHES=1) |
|---|---|---|
| `check_reviews.py` | $0.0014/row, **~0 searches** | **$0.0166/row** → ~$20 per 1226-row pass, ~$4 per staleness tranche |
| `check_deadlines.py` | $0.0010/row when silent | **$0.0676/row** → ~$84 for a full `--all` pass |

**Do not react to the $84 without the next sentence.** Interactive deadline checks that
*really searched* have always cost a **median $0.0790** (36 of them in `deadline_check_log`),
so the two-phase version is **cheaper per verified check** than what the on-demand endpoint
was already paying — the search cap went 3 → 1. The old sub-cent `agent_runs` figures (id=14,
id=16) are the price of **not looking**, not a cheaper way of looking; comparing against them
is how this decision gets made wrongly.

`check_deadlines --all` is still not the primary mechanism (on-demand per-row checking
replaced it in 08-18) — but if anyone does run a full pass, it is ~$84 now, not ~$1.27.

## Next steps
1. ~~Run `link_health_schema.sql`~~ — **DONE 2026-08-23.** All four columns live; backfill
   pass `agent_runs` id=58 recorded all 1239 active rows (1042 live, 198 unverified, 0 dead —
   the earlier cleanup holds). The 7-day staleness filter works and correctly skipped the 13
   just-repaired rows.
   - **Discovered doing it: `opportunities.updated_at` is stamped by an ON-UPDATE TRIGGER**,
     not by the code, contrary to what CLAUDE.md used to say. So a link pass moves it on
     every active row. That broke `check_refresh_progress.py`, which read "1236/1236
     opportunities updated" with the refresher having touched none; it now excludes
     link-only writes and reports a floor. Never add a reader of that column meaning "the
     opportunity's content changed" — it cannot mean that.
2. **Work the review queue — it is now 255 rows**, 135 of them dead links. Each carries the
   HTTP code, and **47 carry a `possible replacement found but NOT verified` suggestion** —
   start there, since a candidate is already named and only needs a human to confirm or
   reject it. For many of the rest the fix is still a URL edit rather than a rejection.
3. Consider a **Link health card** in the console for the 207 flagged-but-active rows.
4. Nothing is committed, and the working tree still carries **three** threads' work — this
   one, the scraper rewrite, and the pre-existing user-metrics feature. Stage by hunk.
   This thread's files: `check_links.py` (new), `url_repair.py` (new),
   `link_health_schema.sql` (new),
   `url_validate.py`, `refresh_opportunities.py`, `check_reviews.py`, `check_deadlines.py`,
   `gemini_common.py` (SEVENTH finding),
   `server.py` (AGENT_CONFIGS_SCHEMA + build_agent_args + estimate_agent_cost + the
   deadline endpoint's 4-tuple and its unverified-fallback), `admin_console.html` (scope
   fields, colour var, free-agent copy), `CLAUDE.md`, `HANDOFF.md`.
5. Not done, deliberately: per-item diagnostic JSON logs for `check_reviews`/
   `check_deadlines` (the scraper writes one per seed; these would help the same way).
7. **Neither rebuilt agent has had a real batch run** — one verification row each, both
   dry-run. `check_reviews` has 47 rows due (~$0.78); `check_deadlines --all` is ~$84 and
   is NOT the primary mechanism, so leave it to the on-demand endpoint unless a backfill is
   actually wanted. Both need fresh approval.
8. `refresh_opportunities.py` was NOT made two-phase and should not be: it runs with
   `use_web_search=False` deliberately, so there is no search for the format to suppress.
   `find_mailing_lists.py` likewise — regex first, one attribution call, no search.
6. Possible next step on repair: the 47 suggestions could get a one-click **Accept this
   URL** button in the review queue, which is the cheapest way to convert the proposals the
   three tests deliberately refuse to auto-accept. Do NOT instead loosen the tests — the
   72 -> 13 funnel in Part 1b is what each one is buying.

---

# PRIOR THREAD: Scraper rewrite — grounding-based URLs (2026-08-23)

## Goal
Make `scrape_opportunities.py` accurate and efficient. It was producing rows with
**fabricated URLs**: an HTTP check of all 116 rows in
`scrape_review_national_20260820.json` found **30 hard 404s (26%)**.

**Status: rewritten and validated live across ALL 40 angles. Both runs finished cleanly
(`agent_runs` id=49 and id=50). A follow-up audit then found a SECOND failure the
headline numbers hide - live-but-wrong URLs - now fixed in code but NOT reflected in
the 166 rows already in the queue. Nothing is committed.**

Full design + measurements: **`SCRAPER_PLAN.md`** (repo root). Read that before changing
any of this; it carries the numbers behind every decision.

## The root cause (this is the load-bearing finding)
**Gemini decides per call whether to search, non-deterministically. It cannot be forced.**
Proof in `agent_logs/`: seed 51 run twice with an *identical* command returned
`0 search(es)` once and `6 search(es)` the next time.

When it does not search, it writes URLs **from memory** — right host, path off by one
segment (`juilliard.edu/music/pre-college` for
`.../music/preparatory-division/juilliard-pre-college`). Every one of the 30 dead URLs was a
constructed deep path; **none was a bare domain**.

And `gemini_common.call_gemini` was throwing away the fix: it read `groundingMetadata` only
as `len(webSearchQueries)`. The discarded fields are exactly what repairs this:
- `groundingChunks[].web.uri` is a `vertexaisearch.cloud.google.com/grounding-api-redirect/…`
  link that **resolves to the exact real page in one free HTTP hop**. (`web.title` is only a
  bare domain; `web.domain` does not exist on `v1beta/generateContent`.)
- `groundingSupports[]` gives `segment{startIndex,endIndex,text}` + `groundingChunkIndices`,
  i.e. per-opportunity attribution.
- Measured head-to-head: **4/4 model-typed URLs 404, 4/4 grounding-resolved URLs 200.**

## What was built
- **`url_validate.py`** (new, free — no API calls). `resolve_grounding_chunks()`,
  `support_urls_by_span()`, `check_urls()` (separates **dead** 404/410 from **unverified**
  403/429/timeout — 403 is ~9% on the existing catalog, so treating it as death bins good
  rows), `is_bare_domain()`, `same_host()`.
- **Two-phase call per seed** in `scrape_opportunities.py`:
  `research_seed()` = prose + search (keeps grounding), then `extract_candidates()` = strict
  JSON, no search, **with the resolved URLs supplied** so the model copies rather than
  recalls. Do not collapse these back into one call.
- **Silent-search retry**: `research_seed()` re-sends the **identical** prompt once if
  `searches == 0`. Not a sterner prompt — the decision is a coin flip, so re-rolling is the
  mitigation. Still-silent output is flagged, not discarded.
- **Seed category dropped end to end.** It was never sent to the model, nothing
  student-facing reads `opportunities.category`, and its only use was a `type` fallback that
  measurement showed wrong 27% of the time (65% for Research seeds).
- **Dedupe swapped to `url_dedupe.find_duplicates()`**. The old private rule rejected on URL
  alone and on bare name similarity >= 0.85 — which matches **264 catalog pairs, 257 of them
  genuinely distinct**.
- **"Discard almost nothing, explain everything."** Only an exact duplicate (same normalized
  URL *and* matching name) and a candidate with no URL are ever withheld, and both go to the
  snapshot with their raw JSON. Everything else inserts `is_active=false`,
  `moderation_status='pending_review'`, with `FLAG_*` reasons saying what to check.
- **Console: duplicate back-links.** `dupeBackLinks()` renders each `dup_candidate` inline —
  confidence, the suspected row as a **clickable link to its own page**, its id, and the
  reason. Previously the queue showed only a `2 possible duplicates` count.
- **Diagnostics**: `webSearchQueries` strings now exposed in `usage`; raw notes/queries/
  resolved URLs/candidates saved to `agent_logs/scraper_<stamp>_seed<id>.json`; every
  rejection logged with its reason.

## Validation (live, real money)
All 40 angles, `agent_runs` id=49 + id=50, **$3.607, 166 rows**, vs the full 116-row
08-20 batch:

| metric | 08-20 (old) | 08-23 (rewrite) |
|---|---|---|
| rows | 116 | 166 |
| dead links | 30 (**26%**) | **0 (0%)** |
| confirmed live | 64 (55%) | 146 (**88%**) |
| bare root domains | 46 (40%) | 13 (8%) |

Of 30 dead old rows, 9 were re-found on the same site and **9/9 came back live**
(`tisch.nyu.edu/…/summer-filmmakers-workshop` -> `…/filmmakers-workshop`;
`med.stanford.edu/psychiatry/education/CNIX.html` -> `…/highschool.html`).
Silent retry fired on 2/40 seeds and **succeeded both times** (final 0/40 silent).
Rates: **~36s/seed, $0.090/seed, $0.022/row.**

## Both runs COMPLETED (no longer in flight)
- `agent_runs` id=49 — 12 angles, $1.0517, 62 rows.
- `agent_runs` id=50 — remaining 28 angles, $2.5557, 104 rows, 0 errors, 0 silent seeds.

Combined across all 40 angles: **166 rows, $3.607**, 192 raw candidates, 26 rejected (all
exact duplicates), 0 invalid, 0 errors, **0 dead links**. Logs are in the session scratchpad
(`rerun12.log`, `rerun28.log`); per-seed raw responses are in `agent_logs/`.

**The user began triaging while run 2 was going**: of the 166 rows, 44 are already
`approved` + activated, 2 rejected, 120 still `pending_review`.

## What worked
- **Measuring before theorising.** HTTP-checking the actual catalog turned "the scraper
  seems off" into "26% of URLs are 404 and every one is a constructed deep path", which
  pointed straight at the mechanism.
- **Probing cheaply.** Three tiny Gemini calls (**$0.032 total**) settled what the docs
  could not. Probe scripts are in the session scratchpad, not the repo.
- **Replaying saved API responses through new code**, so the whole pipeline was verified
  offline before any paid run.
- Reusing `url_dedupe.py` instead of writing a second matching rule.

## What did NOT work (do not repeat)
- **Do not claim the JSON-only prompt suppresses search.** I asserted this from a 3-sample
  probe (JSON -> 0 searches, prose -> 2). Seed 51's identical-command 0-then-6 split shows
  the noise floor is far too large for that. The two-phase design is justified by *"a
  JSON-only call cannot carry grounding data"*, **not** by that claim. An earlier version of
  the memory entry and `SCRAPER_PLAN.md` said otherwise and were corrected.
- **`gemini_common.py`'s "THIRD finding" is CORRECT** — no reliable way to force search.
  I briefly concluded it had missed a lever; it had not. Do not "fix" that docstring.
- **Do not match "same program" on loose word overlap.** My first comparison paired NIH SIP
  with an NYU URL and AI4ALL with Stanford Psychiatry. Use same registrable domain **and**
  `url_dedupe.name_similarity` >= 0.60.
- **Do not backfill or re-run the pre-2026-08-23 scraper.** The monthly cron
  (`~/.claude/scheduled-tasks/monthly-national-scrape`) reuses these angles — verify its
  command matches the new flags before it next fires on the 1st.

## Traps hit (worth not re-learning)
- **`dryrun_common._load()` returned `[]` for any non-list.** The new snapshot shape is
  `{"inserted": [...], "rejected": [...]}`, so every future scraper snapshot would have
  listed as "0 entries" and committed nothing — silently, no error. Fixed to read both
  shapes; all 7 historical snapshots verified still readable.
- **Phase 1's cost was only banked after phase 2 returned**, so an exception in phase 2
  discarded money already spent. Each phase is banked as it completes.
- `scraper_seeds.category` is **`not null`** and this repo cannot run DDL, so `create_seed()`
  writes `SEED_CATEGORY_PLACEHOLDER`. `opportunities.category` is nullable (already NULL on
  1139/1440 rows) so the scraper simply stopped writing it.
- Console `quality_flags` pills truncate at 90 chars — flags must stay short; a `title=`
  tooltip now carries the full text.

## Migration / DB state, checked live
- `user_submissions_schema.sql` **has been run** (`moderation_ready: true`), so flags and
  `dup_candidates` land correctly. No pending DDL for this thread.
- The **110 rows from the 08-20 batch were rejected by the user via the admin console** at
  `2026-08-23T08:09Z` (`reviewed_by='admin-console'`). They are recoverable from the
  Rejected tab; rejecting never deletes.
- 62 rows from `source='scraper-national-20260823'` are in the queue awaiting triage, plus
  whatever the in-flight 28-angle run adds.

## !! The working tree contains TWO threads' work !!
`git status` is **not** all scraper work. A `git add -A` would bundle two unrelated features.

- **This thread:** `scrape_opportunities.py`, `url_validate.py` (new), `gemini_common.py`,
  `seeds_common.py`, `dryrun_common.py`, `migrate_seeds_to_supabase.py`,
  `SCRAPER_PLAN.md` (new).
- **A different, pre-existing thread (NOT mine):** a user-metrics/activity feature —
  `USER_METRICS_PLAN.md`, `user_activity_schema.sql`, `user_metrics_daily_schema.sql`, a
  **+719-line block at `get_user_costs` in `server.py`**, and a **+370-line
  `renderUserCosts` block in `admin_console.html`**.
- **`server.py`, `admin_console.html` and `CLAUDE.md` are MIXED** — they carry hunks from
  both. My `server.py` change is only `SEED_FIELDS` / `SEED_CATEGORY_PLACEHOLDER` /
  `create_seed()`; my console changes are the seed-category removal, the flag `title=`
  tooltip, and `dupeBackLinks()`.

Stage by hunk, not by file, if these are to be committed separately.

## Next steps
1. **Triage the review queue** — 120 of the 166 rows are still `pending_review`. Start from
   `scratchpad/triage.json` (below): it already says, per row, whether the page's own title
   corroborates the row. The 15 MISMATCH rows are where the bad ones are.
2. **Decide about the 10 listicle rows** (`scratchpad/listicles.json`). None reached the live
   catalog. The `reconcile_url` fix would have repaired 5 of them for free; the other 5 need
   a URL by hand or a reject. They are the strongest argument for a small re-run of just
   those seeds, but that costs money and needs approval.
3. Decide whether to commit, and if so **separate the two threads' hunks**.
4. ~~The monthly cron~~ **DONE** — paused, and its auto-activate step removed. All agents
   are run manually now. See "Cron" below before re-enabling anything.
5. Consider whether the 110 rejected 08-20 rows are worth re-scraping now that URLs resolve
   — several were real programs killed only by a bad link.
6. Optional, deliberately not done: feeding the catalog into the prompt to cut the ~40%
   duplicate rate (needs its own paid A/B); splitting seed `angle` from an explicit
   `queries` field (see `SCRAPER_PLAN.md` "Change 4").

---

## SESSION 2026-08-23 (later): post-run audit, and the second failure mode

**Nothing was spent this session.** Every check below is HTTP-only or reads local logs.

### Verified the handoff's own claims independently
Re-measured all 166 rows against the 116-row 08-20 batch: dead links **26% -> 0%**, bare
domains **40% -> 8%**, confirmed live **53% -> 88%**. Matches what `SCRAPER_PLAN.md` claims.
(The plan says 55% live for the old batch where I measured 53%; that is 403/timeout
flakiness between runs, not a discrepancy that matters.)

### Reconciled a 44-row gap that looked like a bug and was not
166 rows were inserted but only 122 carried `source='scraper-national-20260823'` in the
inactive set. By row id: **44 of run 1's rows are `is_active = true`**. They were not
auto-activated — `reviewed_by='admin-console'`, `reviewed_at` 09:04-09:11Z, i.e. a person
triaged them in the console while run 2 was still going. 44 approved, 2 rejected, 120 left
pending. Everything reconciles; no rows are missing.

### THE FINDING: fixing dead links exposed a second failure
Fetched every row's page and compared its title to the row. **10 of 166 (6%) store a
third-party SEO round-up instead of the program's own page** — `ladderinternships.com`
for Stanford AIMI, `indigoresearch.org/blog/` for NASA OSTEM, `futureforward.app/blog/`
for NHSJS. **None of them reached the live catalog.**

Every existing check passes these: HTTP 200 (so `check_urls` is happy), deep path (so
`is_bare_domain` is happy), not `/faq/` (so `is_low_value_path` is happy). A live-and-wrong
link is worse for a student than an obviously dead one, and the "0% dead links" headline
cannot see it.

**Half of them were `reconcile_url`'s own doing.** With a grounding span present it returned
`span_urls[0]` without first asking whether the model's URL was *itself* a retrieved page.
It was, in 33 of 166 rows — `aimi.stanford.edu/education/summer-research-internship`,
`stemgateway.nasa.gov/.../high-school-internships`, `nhsjs.com/submit-your-work/` all thrown
away for a blog.

### What was changed (code)
- **`scrape_opportunities.py` `reconcile_url()`** — hoisted `model_url in resolved_urls`
  above the span fallback. Replayed over the whole batch: **changes 33 rows, improves 5
  measurably, worsens 0**; ~20 more of the "neutral" changes are better by eye
  (`naclo.clsp.jhu.edu` over `linguistics.cornell.edu/outreach`).
- **`url_validate.domain_matches_org()`** (new) + **`FLAG_OFFSITE`** — catches the 5 the
  model typed itself, which no ranking can repair. **16% of the batch flagged, 10/10 known
  cases caught**, ~25 of the 27 flagged rows genuinely off-site.

**These changes are NOT reflected in the 166 rows already in the queue** — those were written
by the code as it stood during the runs. A future run gets the benefit; today's queue does not.

### Traps hit while building the matcher
- **Whole-token matching does not work on domain labels.** A label is words run together, so
  an exact-token rule called `idyllwildarts.org`, `tellurideassociation.org` and
  `artandwriting.org` unrelated to their own owners — **58% fire rate**. Substring matching
  against tokens of >= 4 chars, with generic words removed, gives 16% at the same recall.
- Abbreviation must work **both directions**: `colum.edu` (label shorter than the org word)
  and the `umich`/`upenn` shape both read as third-party sites with containment one way only.
- Initials must be taken with **parentheticals stripped**: "Fermi National Accelerator
  Laboratory (Fermilab)" otherwise yields `fnalf` and misses `fnal.gov`.
- **Heredocs in this environment collapse backslashes.** Patching `url_validate.py` through a
  `python - <<PY` heredoc wrote a regex word-boundary escape as a literal **backspace byte
  (0x08)**, so the acronym regex silently matched nothing — and the file still parsed and
  imported cleanly. If a regex mysteriously matches nothing right after an edit, check for
  control characters before rewriting the logic. Patch by line index, or build escapes with
  `chr(92)`.

### Verification
- `scratchpad/test_matcher.py` — **39/39** unit cases, plus the batch and live-catalog fire
  rates. Exits non-zero on regression.
- `reconcile_url` branch tests: 6/6, covering every return path.
- `python scrape_opportunities.py --mode national --preview` — clean, 43 seeds, free tier.
- No live agent run. The fix is validated by **offline replay of the saved batch**, the same
  method the original rewrite used.

### Cron: PAUSED, and its instructions rewritten
`monthly-national-scrape` (`~/.claude/scheduled-tasks/monthly-national-scrape/SKILL.md`).

**The user's decision, 2026-08-23: all agents are run manually for now.** The task is set
`enabled: false` (paused, not deleted — the prompt and schedule survive, `nextRunAt` is
gone). It would otherwise have fired 2026-09-01.

Its instructions used to end with *"After review, activate with... PATCH to set
is_active=true"*, on an unattended run with nobody reviewing. That step is gone. The prompt
now opens with the rule instead: **never activate an opportunity, ever** — every row lands
`is_active = false` / `moderation_status = 'pending_review'` and waits for a person in the
console, and no flag count or clean run substitutes for that. It also states the ~$3.60 /
~110-minute cost up front, points at `--preview` as the free way to check, and fixes the
snapshot filename pattern (stamps carry seconds now).

**Audited: no code path can violate this.** `scrape_opportunities.py:415` and
`dryrun_common.py:308` hardcode `is_active: False`; every other `is_active` reference in the
repo is an `eq.true` *read* filter, except `migrate_to_supabase.py` (the one-off historical
import). The single write path is `activate_opportunities()` (`server.py:3438`) — localhost
only, explicit id list, no "activate everything matching", stamps `reviewed_by`/`reviewed_at`.

To resume automatic runs later: `update_scheduled_task` with `enabled: true`. Ask first.

### Scratchpad artefacts (this session, all free to re-run)
`compare_all.py` (40-angle comparison), `triage_check.py` -> `triage.json` (per-row
page-title corroboration), `aggregator_check.py` -> `listicles.json`, `would_fix.py` and
`impact.py` (replay of the fix over the batch), `test_matcher.py` (regression suite).

---

## Files changed this thread
`scrape_opportunities.py` (rewritten), `url_validate.py` (new), `gemini_common.py`
(`return_grounding`, `web_search_queries`, FIFTH/SIXTH findings), `seeds_common.py`,
`dryrun_common.py` (`_load` both shapes), `migrate_seeds_to_supabase.py`,
`server.py` (seed CRUD only), `admin_console.html` (seed category removal, flag tooltip,
`dupeBackLinks`), `CLAUDE.md`, `SCRAPER_PLAN.md` (new).

Added in the later 2026-08-23 session: `url_validate.py` gains `domain_matches_org()` /
`_org_tokens()` / `_acronyms()`; `scrape_opportunities.py` gains `FLAG_OFFSITE` and the
`reconcile_url()` hoist. `CLAUDE.md` and `SCRAPER_PLAN.md` document both.

---

# PRIOR THREAD: Review-queue actions, snapshot stamps, Haiku pin (2026-08-22)

## Goal
Close the gap the admin-console thread left open: the review queue could only *activate*.
Junk rows could be left inactive but never adjudicated, so they came back round every time
the queue was opened and the queue only ever grew — and a row that was nearly right could
not be fixed at all without a database console.

**Status: built, round-tripped against the live database, catalog left unchanged (113 rows
still pending, 0 rejected). Nothing is committed.**

## What was added
- `POST /api/agents/pending/moderate` `{ids, status}` — writes `moderation_status`,
  `reviewed_by = "admin-console"`, `reviewed_at`. Valid statuses mirror the CHECK constraint
  in `user_submissions_schema.sql`. `rejected`/`duplicate` also force `is_active = false`
  and bust `_opportunities_cache`.
- `GET /api/agents/pending?status=queue|rejected|all`. The console fetches `all` once and
  splits client-side, so the Queue/Rejected pills cost no round-trip.
- Console: Queue / Rejected `.tab` pills in the card head, a red **Reject selected**, and
  **Restore to queue** in place of it on the Rejected tab. Queued rows now show provenance
  badges — `from <userid>`, duplicate-candidate count, and each `quality_flags` entry —
  which is the whole point of the columns for user submissions.
- `activate_opportunities()` now stamps `moderation_status = "approved"` too. Approving does
  NOT activate; activating does approve.
- `POST /api/agents/pending/update` `{id, fields}` + a per-row **Edit** modal — 16 editable
  fields (name, org, type, url, summary, eligibility, grades, price/cost, location/state,
  intl, season, category, subject_tags), each validated server-side with a message the modal
  shows verbatim. `_BASE_PENDING_SELECT` was widened to carry them so the modal prefills from
  the list already in memory; the client sends only changed fields.
- A per-row **Duplicate** modal, plus `GET /api/agents/opportunities/search?q=&limit=` to
  find the surviving row (id-exact first, then name/org/url ilike; active and inactive
  alike). The modal offers the row's stored `dup_candidates` — with the reason and
  confidence `url_dedupe` recorded at submission time — before the free search.
  `duplicate_of` is now validated: required for `duplicate`, refused for every other status,
  cannot be the row itself, and cannot point at a row that is itself rejected/duplicate.
  It is cleared on restore/reject rather than left stale. `list_pending_opportunities`
  returns a `duplicate_targets` map so the queue names the survivor, not a bare id.

### Timestamped snapshot filenames (separate from the queue work)
Snapshot filenames were date-only, so a second run of the same agent on the same day
silently overwrote the first one's file — and a `--dry-run` has already paid the API in
full by the time it writes, so that destroyed work that cost real money. There is physical
evidence of the problem in the repo: someone had hand-renamed
`scrape_review_national_20260818.run1-backup.json` to save one.

- `agent_common.snapshot_stamp()` is the new shared helper; all four agents call it and now
  write `YYYYMMDD-HHMMSS`. It lives in `agent_common` for the same reason the flags do —
  four writers, one reader (`dryrun_common`), one contract.
- **Both shapes are read.** `dryrun_common._run_date()` takes `(\d{8})(?:-(\d{6}))?`, so
  every pre-existing date-only file still lists and still commits; those resolve to midnight,
  which is exactly what the old code returned, so their staleness and ordering are unchanged.
- The console prints a time **only** for filenames that carry one — a date-only snapshot
  showing `00:00` would read as a real run time rather than "unknown which run".
- Sorting moved from `run_date` to the full `ran_at`; same-day files would otherwise order by
  filename, i.e. alphabetically by agent instead of newest-first.
- The stamp is local time (matching `agent_logs/<agent>_<stamp>.log`) but parsed/served as
  UTC so snapshots stay comparable; the console reads it back with `getUTC*` so the printed
  digits match the filename's.
- **The scraper's `source` value stays date-only** (`scraper-national-20260820`). A whole
  day's scrape groups under one source and 113 existing rows carry those values — only the
  filename gained seconds.

### One Anthropic model everywhere: Haiku (also separate from the queue work)
`claude_common.MODEL` was still `claude-sonnet-4-6` while `server.py`'s `CLAUDE_MODEL` and
`check_deadlines.py`'s local pin were both `claude-haiku-4-5-20251001`. Asked to make it
consistent, and Haiku is now the single answer. Chasing it turned up two costing bugs that
were the real damage:

1. **`claude_common`'s price constants are imported by `server.py`** to cost every
   `interactive_claude` call — and those calls run on `CLAUDE_MODEL` (Haiku). With the file
   on Sonnet the constants were Sonnet's $3/$15 against Haiku's actual $1/$5, so interactive
   Claude spend was estimated at **3x** what it cost. Now $1/$5.
2. **`check_deadlines.py` imported `estimate_cost` from `gemini_common`** and used it on
   calls its own local `call_claude()` made to Anthropic — pricing a Claude call at Gemini's
   $0.75/$3.75 + $0.014/search. Now imports it from `claude_common` (which also counts the
   cache-token fields Anthropic returns and Gemini's version does not).

Also worth knowing: the resume/LinkedIn import calls `claude_common.call_claude` but records
its cost under `CLAUDE_MODEL`, so before this change `user_costs.model` named Haiku for a
call that had actually run on Sonnet. One pin fixes that too.

**Neither correction is retroactive.** `agent_runs`, `user_costs` and `deadline_check_log`
rows written before 2026-08-22 carry the old rates. Nothing recomputes them, and the
overstatement is not uniform (interactive Claude was too high, deadline checks were priced
on a different provider's curve entirely), so do not apply a blanket multiplier.

`WEB_SEARCH_PRICE_PER_SEARCH` stayed at $0.01 — web search is billed per search at a flat
rate, not per model. The date-suffixed id `claude-haiku-4-5-20251001` was kept rather than
the bare `claude-haiku-4-5`, to match the two pins that already existed and work in
production; introducing a third spelling was the opposite of the point.

### check_reviews.py's TEMP filter: removed
`"review_status": "is.null"` is gone from the row selection; `"order": "id"` stays (stable
resume ordering — never actually temporary, just mislabelled).

**The reason recorded for keeping it was already wrong.** The old note said the filter was
why the agent previews as 0 rows due. Measured against the live catalog: 1327 active rows,
**0** with a NULL `review_status`, **0** never-reviewed-or-stale. Preview was 0 because of
the staleness filter alone, and removing the TEMP filter changed today's count not at all
(verified: `--preview` still reports 0).

What it was actually doing was freezing the agent permanently. With no NULL rows left it
matched nothing, so `STALE_AFTER_DAYS = 182` was dead code and no row could ever be
re-checked. It also sat **outside** the `if not args.force` branch, so `--force` — which
documents itself as ignoring the staleness filter — returned 0 rows as well. `--force` now
resolves the full 1327 (verified through both the CLI and the console's preview endpoint:
$0.70, ~6635s estimated).

The 605 rows (46%) sitting at `insufficient_data` are the ones most worth a re-check, and
were exactly what the filter blocked.

**`STALE_AFTER_DAYS` lowered 182 -> 30** in the same pass, at the user's call. At 182 an
ad-hoc run did nothing for half a year, so `--force` (the entire catalog) was the only way
to make this agent act at all between passes — all-or-nothing. At 30 the catalog comes due
in five batches from **2026-09-17** (825 rows) through 2026-09-21 instead of one 1327-row
wall. The threshold was kept rather than removed so a plain run stays idempotent: rows
checked yesterday are not re-paid for, which is what protects an accidental run — or a
future scheduler — from re-spending ~$0.70 and 110 minutes every firing.

Console help text for the Force checkbox was updated to match (it said "182-day") and now
also says Force means the full catalog, not just what has aged. Verified: `--preview`
reports 0 rows due today under the new threshold.

## Traps hit (worth not re-learning)
- **`NULL NOT IN (...)` is NULL, not true.** Every scraper row has a NULL
  `moderation_status`, so filtering the queue with `moderation_status=not.in.(rejected,
  duplicate)` empties it completely. The filter spells the null case out with `or=`.
- **PostgREST 400s the whole select on one unknown column**, so a single wide select would
  take the queue down on a database missing the migration. The read is a two-step ladder
  (wide → base) and reports `moderation_ready`.
- `togglePending()` re-renders the table, so a scripted `cbs[0].click(); cbs[1].click()`
  only registers the first — the second element is detached by then. Not a bug for a human.
- **The edit endpoint is scoped to `is_active = false` and reads the row to check.** Without
  that it is a general catalog editor reachable by id, with no confirmation and no audit
  trail — reachable from a page whose other two buttons both confirm.
- **`type` is a clean 7-value enum in the data; `category` is not** ('COMPETITION',
  'SUMMER_PROGRAM', mixed case, 1139 NULLs). So type is validated and category is free text.
  Validating category would refuse most of the catalog's own values.
- **PostgREST's `or=()` filter is comma-separated and paren-delimited**, so a comma or paren
  typed into the survivor search would be parsed as syntax. They are stripped from the term
  rather than quoted — it is a search box, and a slightly broader match is harmless.

## Migration state, checked live this thread
`user_submissions_schema.sql` was found half-applied — eight of nine columns, no
`submission_payload`, so submissions would have inserted via step 2 of the ladder in
`_insert_opportunity_row()` and silently lost the AI-extracted extras (apply_url,
requirements, meta, note). It was **re-run mid-thread and all nine columns now exist**.
There are **0 user-submitted rows** so far, so nothing was lost.

User submissions have always shown up in this queue — `list_pending_opportunities()` filters
on `is_active = false` only, with no source restriction, and the submission path writes
exactly that. The Edit modal and the provenance badges are what make them actually
*reviewable* rather than merely present.

## Verification done
Reject → restore round-tripped through the UI on `ec18523`; edit round-tripped on the same
row (org, subject_tags array, grade_min changed and then reverted); duplicate round-tripped
too — searched, picked `ec17468` as survivor, the row left the queue with the survivor's
*name* rendered on it, then Restore put it back **and cleared `duplicate_of`**. Every
validation path exercised: unknown field, bad type, blank name, schemeless url, 3-letter
state, inverted grade range, non-numeric grade, missing id, empty payload, a live row
refused, duplicate with no target, duplicate of itself, duplicate of a nonexistent id,
`duplicate_of` on a non-duplicate status, and the chain guard (pointing at a row already
marked duplicate). Server errors surface inside the modal rather than silently.
**The catalog is unchanged: 113 rows pending, 0 rejected.** The one residue is that
`ec18523` now carries `moderation_status = 'pending_review'` instead of NULL — semantically
the same queue state.

Snapshots: two synthetic same-day files (`…20260822-091500` / `…-164200`) listed as separate
rows with their times, newest first, while the seven real date-only files kept their exact
previous display; a timestamped file resolved and preview-committed cleanly (2 entries, no
writes) and `../server.py` was still refused. Both test files deleted afterwards — the
snapshot directory holds the same seven files it started with.

## Next steps
1. ~~Re-run `user_submissions_schema.sql`~~ — **done**, `submission_payload` now exists.
2. Work the 113-row queue — rejecting is now possible.
3. ~~`duplicate` is reachable only via the API~~ — **done**, it has its own picker modal.
   Still no *merge*: marking a duplicate discards the losing row's data rather than folding
   anything into the survivor. That is the right default, but if the loser ever has better
   metadata, the fix today is Edit the survivor by hand first.
4. Editing a row's **URL does not re-run dedupe**, so a hand-edited URL can collide with an
   existing row. Deliberate (the check needs a full paginated catalog read per save); the
   duplicate would be caught by a human in the same queue.
5. Snapshot filenames now carry seconds, but **nothing links a snapshot to its `agent_runs`
   row** — that is still guesswork by timestamp. A run id in the filename would close it.
6. The **first list's items 4, 6 and 7 are deprioritized by the user** (scheduling, working
   the 113-row queue, and the 38%-failed-runs investigation). Item 8 (`check_reviews.py`'s
   TEMP filter) and item 10 (`user_costs` retention) were not raised either way.
7. `check_reviews.py`'s `--force` now reaches all 1327 active rows where it used to return
   0. Nothing was run — but the console's Live Run button on that agent is no longer a
   guaranteed no-op, so read the preview before pressing it.
8. Still uncommitted, along with everything from the prior threads.

## Files changed this thread
Modified: `server.py` (`moderate_opportunities()`, `update_pending_opportunity()`,
`_coerce_field()`, `_is_missing_column_error()`, status-filtered
`list_pending_opportunities()` + widened `_BASE_PENDING_SELECT`, approve-stamp in
`activate_opportunities()`, `search_opportunities()`, `_duplicate_targets()`,
`/api/agents/pending/moderate`, `/update` and `/api/agents/opportunities/search`),
`admin_console.html` (status pills, reject/restore, provenance badges, edit modal +
`OPP_FIELDS`, duplicate picker modal + `.dup-opt` styles,
`updateActivateBtn` → `updateReviewButtons`, snapshot time column + `clock()`),
`agent_common.py` (`snapshot_stamp()`), `dryrun_common.py` (both filename shapes, `ran_at`
/`has_time`, sort by instant), `refresh_opportunities.py`, `check_reviews.py`,
`check_deadlines.py`, `scrape_opportunities.py` (all four now stamp to the second),
`claude_common.py` (MODEL -> Haiku + matching prices), `check_deadlines.py`
(`estimate_cost` from claude_common), `check_reviews.py` (TEMP filter removed),
`CLAUDE.md`.

---

# PRIOR THREAD: Registration consent + trial/paid subscription

## Goal
Finish the subscription feature the previous thread left broken, and add the signup
consent the product needs to be lawful: an age checkbox, a parental-permission checkbox
for under-18s, a Terms/Privacy acceptance checkbox, and the two legal documents attached
in-app.

**Status: complete, migrated, and verified end to end against the live database.**
Registration works again. Nothing is committed.

## Migration: done

[subscription_schema.sql](subscription_schema.sql) was run in the Supabase SQL editor on
2026-08-21; all eleven columns are live. Re-running it is harmless (every statement is
`IF NOT EXISTS`), and it is still the file to point at if the table is ever rebuilt.

Verified after the migration: all 15 pre-existing accounts kept access (13 of them have
`trial_ends_at` NULL, read as "clock not started"), `ensure_trial_started()` stamps a real
7-day window on first sign-in, registration succeeds for both an 18+ and an under-18
account with the consent fields correctly recorded, a backdated trial 402s the AI endpoints
and swaps the live tab to the paywall without a reload, flipping the row to `active`
restores access, and signing back in as a lapsed account lands on the paywall without the
app ever becoming visible.

The previous thread's missing-column theory was **confirmed** before the fix with the literal
error: a `select=subscription_status` probe returns `42703, column
"users.subscription_status" does not exist`, and none of the six columns exist. Writes
report the same condition differently — `PGRST204, Could not find the 'subscription_status'
column of 'users' in the schema cache` — which is why `_is_missing_column_error()` checks
for both codes. Checking only 42703 (the obvious one, from the read probe) silently misses
every insert.

## What was built

**Consent at signup.** Three checkboxes on the register form: 18-or-older; if that is
unticked, a parental-permission box that also asserts 13-or-older (Terms §2 sets the floor
at 13 and requires guardian permission under 18); and Terms + Privacy acceptance with links
to the documents. The parental row hides *and clears itself* when someone ticks 18+, so a
box ticked before correcting an age can't still be submitted. `handle_register()` re-checks
all three and refuses the account — the browser half is the explanation, not the control.
Five new columns record what was accepted, stamped with `TERMS_VERSION`.

**The legal documents.** `legal/terms.md` and `legal/privacy.md` are the source of record;
`build_legal.py` renders them into `terms.html` / `privacy.html`. Linked from the register
form, the account drawer, and the paywall. The generated HTML must not be hand-edited.

**Trial enforcement**, which the previous thread flagged as a gap. `subscription_state()`
is now the single source of truth, and both halves derive from it: the client swaps in
`#page-locked` before the app shell is ever unhidden, and `Handler._subscription_blocks()`
returns 402 from the four endpoints that spend real money per call.

**Bugs found and fixed in the previous thread's code:**
- `create_checkout_session()` returned the session *id*, and the endpoint pasted it into
  `https://checkout.stripe.com/pay/{id}` — a retired URL form that 404s. It now returns
  the `url` Stripe actually gives back.
- `cancel_subscription()` DELETEd the subscription, revoking access immediately, while the
  confirmation dialog promised access "until the end of your billing period". Now
  cancel-at-period-end, with `current_period_end` recorded so the gate honors it.
- `days_until_trial_end()` floored, so a 7-day trial read "6 days left" one second in.
- A `trial` row with NULL `trial_ends_at` — i.e. **every account that predates the
  migration** — would have been read as expired and paywalled the instant the migration
  ran. Now read as "clock not started", with `ensure_trial_started()` stamping a real
  window on first sign-in. This one is worth remembering: it would have locked out the
  entire existing user base and looked like a subscription bug, not a migration bug.

**`BETAUSER` promo code + real redemption.** The promo system previously only *validated*
codes — nothing ever applied one, and nothing ever wrote `promo_codes_used`, so
"one-time use per account" was unenforced. Added `POST /api/subscription/redeem-promo`,
which writes: sets `subscription_status`, extends `subscription_end_at`, and records the
code. `PROMO_CODES` entries now carry a `kind`:

- **`grant`** — `BETAUSER`, status `beta`, +7 days. Applied server-side against the user's
  own row, no Stripe involved, so **it works right now with Stripe unconfigured**. This is
  the code to hand beta testers.
- **`checkout`** — `FREEMONTH` / `WELCOME10`, unchanged: discounts that only mean something
  at Stripe checkout. Redeeming one through the grant endpoint is refused rather than
  burning the code for nothing.

Grants extend from `max(now, current end)` so they **add** to a running trial (3 days left
+ BETAUSER = 10 days, not 7). Refused for `active` subscribers, since handing a payer a
7-day window is a downgrade. `beta` expires exactly like a trial, with its own copy on the
paywall and in the 402.

**Unique user ID + email at registration.** User IDs were already unique (primary key,
lowercased on every read/write). Emails were not enforced at all — the table had two
addresses shared across five accounts. `handle_register()` now checks both before
inserting and names whichever clashed, plus a basic email shape check (there was no
server-side email validation at all before). Case-insensitive by normalization rather than
`ILIKE`, because `_` is both a legal email character and an ILIKE wildcard.

The pre-insert check can still lose a race with a simultaneous signup;
[users_email_unique_schema.sql](users_email_unique_schema.sql) adds the unique index on
`lower(email)` that actually closes it. **That index cannot be created until the existing
duplicates are resolved** — CREATE INDEX fails outright while they exist. The file leads
with the query that finds them.

## What is still open

1. **Stripe is not configured.** `STRIPE_API_KEY` / `STRIPE_PRICE_ID` are still absent from
   `.env`, so `upgradeSubscription()` cannot complete a real checkout — untested against a
   live Stripe account across both threads now. It fails cleanly rather than crashing
   ("Failed to create Stripe customer: Stripe API key not configured"). Everything upstream
   of the payment (trial, gating, promo validation, cancel bookkeeping) works without it.
   **This is the blocking item for actually taking money.**
2. **No webhook.** Nothing flips `subscription_status` to `active` after a successful
   payment; `SUBSCRIPTION_SETUP.md` §2.4 documents the endpoint but it isn't implemented.
   Until it is, a paid subscription won't actually unlock the app.
3. **One conflict in the legal text**, flagged to the user, not changed — the
   documents are theirs to edit:
   - Terms §3 says the beta "is currently provided free of charge" and promises notice
     before charging. The $9.99 plan contradicts that as written.
   Editing it means re-running `build_legal.py` **and** bumping `TERMS_VERSION`.
4. Existing accounts have never accepted anything — `terms_accepted_at IS NULL` finds them.
   There is no re-consent prompt for existing users; the gate only runs at registration.
5. **Test accounts left in the `users` table** from this thread's verification:
   `adult1787351240`, `minor1787351240`, `uiflow1787351329353` (that last one has a
   deliberately backdated trial, so it always shows the paywall — handy for re-testing).
   `debugtest1` predates this thread; its status/trial were mutated during testing and
   restored afterwards. Delete them whenever.
6. **Unrelated pre-existing breakage spotted:** the Firebase block in `index.html`
   (~line 839) loads the v10 ES-module builds as classic scripts, so `firebase` is never
   defined and analytics silently never fires. Throws on every page load. Uncommitted work
   from another thread; not touched here.
7. Nothing is committed. Review the full diff before staging; do not blanket-stage.

## Files changed this thread
New: `subscription_schema.sql`, `users_email_unique_schema.sql`, `build_legal.py`, `legal/terms.md`, `legal/privacy.md`,
`terms.html`, `privacy.html`.
Modified: `server.py` (consent capture + enforcement, `subscription_state()` incl. the
`beta` status, `handle_redeem_promo()`,
`ensure_trial_started()`, `_subscription_blocks()` on four endpoints, `MissingUserColumns`,
login returns the subscription block), `subscription_common.py` (checkout URL, cancel
semantics, day rounding, `BETAUSER` + promo `kind` + `extend_from()`), `index.html` (consent checkboxes, `#page-locked`, legal links,
cache-bust to `?v=consent1`), `script.js` (consent validation, paywall, 402 handling,
promo/upgrade parameterized for reuse, promo redemption + `beta` rendering), `CLAUDE.md`, `SUBSCRIPTION_SETUP.md`.

---

# PRIOR THREAD: Snapshot commit + opportunity activation

## Goal
Two admin-console additions, both about acting on work that was already done and paid for:
1. **Commit a dry-run snapshot to the database** instead of re-running the agent live.
2. **Activate scraped opportunities** — flip `is_active` false → true from the console.

**Status: built, verified, nothing committed to git.** Neither feature has been *used* on real
data yet; that is the operator's call, not something to do unprompted.

## Why feature 1 matters
`--dry-run` calls the paid API at full cost and only skips the database writes. Until now the
only way to act on its answer was to run the whole agent again and pay a second time. Every
agent already dumps what it *would* have written to a local JSON file — committing that file
applies those writes for free.

**There is real money sitting on disk right now**: `review_check_dry_run_20260819.json` holds
303 already-paid review results, all of which would change a row.

## How it works
[dryrun_common.py](dryrun_common.py) discovers snapshots and replays them; server.py injects
the Supabase plumbing (`_commit_patch` / `_commit_insert` / `_existing_opportunity_urls`) so
the module has no database dependency and is testable on its own.

- `_patch_updates()` mirrors each agent's live PATCH **column for column**. If an agent's live
  write changes, this must change with it.
- **Two-step commit**: `preview: true` resolves real post-dedupe counts and writes nothing;
  the console shows those in the confirm dialog, so the operator sees "insert 96, skip 20"
  rather than the raw file count.
- A commit logs an `agent_runs` row with `mode = "snapshot-commit"`, `cost_usd = 0`. It pairs
  with the original `-dryrun` row: dry run has the cost and no row counts, commit has the row
  counts and no cost. Neither figure double-counts.

## Traps found while building this
- **The scraper's snapshot is written on live runs too**, not just dry runs — so it can name
  rows that already exist. Every insert is deduped by normalized URL against the whole table.
- **The dedupe set must paginate.** A single `limit=5000` request returns 1000 rows (PostgREST's
  cap), which would have silently let ~390 rows' worth of duplicates through. Verified: the
  paginated helper returns 1390 URLs, the naive request returns 1000.
- **Snapshot filenames carry a date, not a timestamp**, so a same-day rerun overwrites the
  file. Discovered concretely: `scrape_review_national_20260820.json` and the 113 DB rows
  tagged `scraper-national-20260820` overlap by only 20 — they are different runs from the
  same day. Do not assume a snapshot maps to a particular `agent_runs` row. Not fixed (it
  would mean touching all four agents' filenames); called out in the console's own copy.
- File mtime is useless for snapshot age here — a git checkout rewrites every mtime to "now".
  Staleness is judged from the `YYYYMMDD` in the filename instead.

## Feature 2 — Review queue
New third tab. Lists `is_active = false` rows with source filter, select-all, per-row link out,
and an "Activate N selected" button behind a confirm that says the rows become visible to
students immediately. 113 rows are waiting, all from `scraper-national-20260820`.

- **Nothing in this repo ever activates a row automatically** and that stays true: activation
  takes an explicit id list, there is no "activate all matching" endpoint.
- `active: false` reverses a mistake without needing a database console.
- Both paths bust `_opportunities_cache`, or the operator activates a row and then cannot see
  it in the app for the cache TTL.

## Verification done
- Snapshot discovery, preview counts, and path-traversal rejection (`../server.py`,
  `C:/Windows/win.ini`, bare `server.py` all refused).
- Preview against three snapshots of both kinds — no writes.
- Activation **round-tripped on one real row** (`ec18523` → active → back to inactive) to prove
  the write path. The catalog is unchanged: still 113 pending.
- Console: all three tabs, snapshot table, selection wiring, confirm copy, no console errors.
- **No snapshot has been committed.** That would insert 96 rows and is the operator's call.

## Next steps
1. Decide whether to commit `review_check_dry_run_20260819.json` (303 already-paid updates).
2. Work the 113-row review queue.
3. Consider adding a timestamp to snapshot filenames so same-day runs stop overwriting.
4. ~~The review queue has no reject/delete~~ — **done 2026-08-22**, see the current thread.
   Reject is now available (non-destructive and reversible); delete still is not.
5. Still uncommitted, along with everything from the prior threads.

## Files changed this thread
New: `dryrun_common.py`.
Modified: `server.py` (commit + activation helpers, four endpoints), `admin_console.html`
(snapshot card, Review queue tab, confirm copy), `CLAUDE.md`.

---

# PRIOR THREAD: Per-user cost attribution in the admin console

## Goal
Extend the admin dashboard so spend can be read **per user** — what each account's own
interaction with the product costs in AI, next to the $9.99/month plan it is supposed to be
covered by.

**Status: built and verified, blocked on one manual DDL step.** Nothing is committed.

## The one thing left to do
Run [user_costs_schema.sql](user_costs_schema.sql) once in the Supabase SQL editor, then
restart with `restart_server.ps1`. Until then the server logs one warning, attribution is
off, and the console's card shows the setup step instead of numbers — everything else on the
page is unaffected. Nothing else is pending.

## The central invariant — do not break this
`user_costs` is a **breakdown of interactive spend, not a second ledger.** Every dollar in it
is already counted in `agent_runs`' `interactive_gemini` / `interactive_claude` rollups, or in
`deadline_check_log`. `record_interactive_cost()` computes the cost **once** and writes both,
specifically so the aggregate and the per-user split cannot drift. Never add the two together
— this repo has already been burned twice by double-counting.

The residual (`interactive_total - attributed`) is real and is reported as **unattributed**:
spend from calls that arrived with no userid (signed-out, pre-login). It is shown with an
attribution rate rather than spread across users, on the same principle as showing interrupted
runs as `unknown` instead of `$0`.

## What was built

**Storage** — `user_costs`, one row per `(userid, UTC day, surface, feature)`. RLS enabled with
no policies, same posture as `users`: per-user spend is operator data and must never be
reachable from the browser. Read-then-PATCH, not a PostgREST upsert — upsert *replaces* a
conflicting row and these counters must *add*.

**Feature classification is server-side** (`classify_feature()` / `_FEATURE_SIGNATURES` in
server.py), pattern-matching the system prompt with the same signatures `generate_mock_text()`
already uses. This was chosen over having the client pass a label so that **no script.js call
site had to change**. Verified against every `const system = ...` literal in script.js: all 13
classify to a named feature, none fall into `other`. Two prompts needed signatures that the
mock list didn't have (`tag_intent`, `tag_suggestions`), and the ranking prompt needed its
opening line as a signature because `Rank the best 10-12 matches` only appears on one of its
two `selectionRule` branches. **Adding a new AI feature means adding a signature here, or its
spend silently lands in `other`.**

**Endpoint** — `GET /api/agents/user-costs?days=&limit=`, localhost-guarded with the rest of
`/api/agents/*`. That guard matters more on this route than the others: the response carries
every account's name, email and subscription status next to what they cost.

**Console tab** — the console gained a top-level view nav (`.viewtabs` / `showView()`): *Agents*
holds everything that was already there, *Cost per user* is the new view. It shows attributed
vs unattributed with an attribution rate, active users, avg and worst cost per user, a count of
accounts whose AI cost exceeds the plan price, a stacked by-feature bar, and a sortable
per-user table (cost, cost/call, % of $9.99, last active) with an expandable per-user
surface/feature breakdown. Accounts over the plan price are highlighted in red. The tab itself
carries the attributed total as a badge (blank, not `$0.0000`, before anything is attributed).

`.vtab` is styled deliberately unlike `.tab`: `.tab` is the inline pill inside a card head that
filters one table, `.vtab` swaps the whole page, and they should not read as interchangeable.
Copy that said "estimated spend **above**" was updated — nothing is above it any more.

**Two gaps found and closed along the way** (both were under-reporting, not cosmetic):
1. **Resume / LinkedIn import was entirely uncosted.** `_extract_profile_from_text()` called
   `call_claude()` and discarded the usage block, so those were real Anthropic charges that
   appeared in **no figure anywhere** on the console. Now rolled into `interactive_claude`.
2. Cached / mock / stale-fallback deadline checks are deliberately **not** attributed — they
   make no API call. Only the request that actually paid to populate the cache is charged.

**Two endpoints gained a `userid`**, passed on the query string because one is a GET and the
other is multipart: `/api/opportunities/<id>/deadline?userid=` and
`/api/extract-from-resume?userid=`. Both dispatch branches had to move off exact-`self.path`
comparison to survive it — the same trap the `/api/agents/*` routes already carry a comment
about. script.js got one shared `costAttributionQS()` helper next to `currentUser`.

## What worked
- **Asking what "user cost" meant before building.** The obvious reading (an end-user-facing
  usage page) was not what was wanted; one question saved building the wrong thing.
- **Deriving both figures from a single cost computation** rather than costing twice. It makes
  the aggregate/breakdown consistency structural instead of something to remember.
- **Testing the classifier against the real prompts in script.js** rather than trusting the
  signature list. That is how the three `other` cases surfaced.
- **Verifying the populated UI by injecting a fixture into `S.userCosts` in the browser** and
  re-rendering — full coverage of the table, expansion, sorting and the over-plan highlight
  without needing the table to exist or spending a cent.

## Traps hit
- `_qs()` takes a **parsed** `parse_qs` dict, not a raw query string.
- `_users_request()` **raises** on failure (unlike `_supabase_request()`, which returns None).
  The account-decoration lookup is wrapped so losing it degrades the card instead of failing it.
- Adding a query string to a route matched by exact `self.path` equality silently 404s it.
- `money()` keyed its precision off the raw value, so a negative margin rendered as
  `$-1.4300`. Now keyed off magnitude, printing `-$1.43`.
- Screenshots of the Browser pane fail when the pane isn't displayed; DOM reads via
  `javascript_tool` verified everything instead.

## Next steps
1. Run the SQL, restart, then use the app signed in for a few minutes and confirm rows appear
   and the attribution rate is sensible.
2. Watch the `other` feature slice — if it grows, a call site is missing a signature.
3. `user_costs` has no retention policy. It is a daily rollup so growth is slow, but at some
   point old days should be trimmed or aggregated.
4. Still uncommitted, together with everything from the prior thread.

## Files changed this thread
Modified: `server.py` (attribution recorder, feature classifier, `get_user_costs()`,
`/api/agents/user-costs`, resume/LinkedIn costing, two route-matching fixes),
`admin_console.html` (Cost per user card + renderers, `money()` fix), `script.js`
(`costAttributionQS()` + four call sites), `CLAUDE.md`.
New: `user_costs_schema.sql`.

---

# PRIOR THREAD: Admin console for the four background agents

## Goal
An operations dashboard at `/admin` to run and monitor the four offline agent scripts —
cost, rows updated, rows created, errors per run — with per-run configurability (sample,
dry run, zero-cost preview), editable timing, and editable scraper search angles.

**Status: complete and verified against live data.** Everything below works end to end in
the browser. Nothing is committed yet.

## Guardrails (carried forward, still in force)

- **Never run an agent without fresh explicit per-instance approval in chat.** All four
  scripts hit paid APIs. Building a button is not authorization to press it. `--preview` is
  the only free path and is safe to run unprompted.
- **Restart the dev server only via `restart_server.ps1`.** Never Bash `&`. See the
  server-restart section below — this has burned two sessions now.
- **Never copy `admin_console.html` back from the scratchpad** — that once silently reverted
  real wired-up code to an older mock version.
- Verify a script's real `argparse` before wiring any flag; guessed flags previously made
  every run fail.

---

## What exists now

### The four agents (authoritative mapping — also in CLAUDE.md)

| UI key | Script | `agent_runs.agent` | Display name | Web search |
|---|---|---|---|---|
| `scraper` | `scrape_opportunities.py` | `scraper` | New Opportunity Scout | Gemini |
| `metadata` | `refresh_opportunities.py` | `metadata_refresher` | Update Opportunity | no |
| `deadline` | `check_deadlines.py` | `deadline_checker` | Deadline Checker | Claude |
| `reviews` | `check_reviews.py` | `review_checker` | Review Checker | Gemini |

**The bug that shaped this thread:** the previous console had three cards, and the one
labelled "Refresh Agent — Update metadata" actually executed `check_reviews.py` (the
*reputation* checker) and read `review_checker` rows. The real metadata refresher,
`refresh_opportunities.py`, was wired in nowhere and **wrote nothing to `agent_runs`** — no
cost, no history, no metrics. Now fixed.

Two traps that remain live in the data:
- **The scraper's `items_processed` counts SEEDS, not rows.** Never sum it with the other
  three agents' row counts. `AGENT_CONFIGS_SCHEMA[key]["unit"]` encodes this.
- **`name` is a display label only**; the dict key and `db_agent` are the real identifiers.
  **Dict order in `AGENT_CONFIGS_SCHEMA` IS the UI order** — it drives cards, chart legend
  and stacking, timing table, and history filter together.

### Three run tiers — only one is free
- **`--preview`** — resolves scope, prints a `PREVIEW_JSON:` line, exits before the first
  API call. Zero cost, zero writes. Shared plumbing in `agent_common.py`.
- **`--dry-run`** — **still calls the paid API at full cost**; skips DB writes only.
- neither — full run.

### Editable timing
`--min-delay` / `--timeout` on all four agents, three layers: module default → env var →
flag. Overrides are edited inline in the console's timing table and persist to a gitignored
`agent_settings.json` (chosen over a Supabase table: these are per-machine operational knobs
that must be readable before any network call). Everything reads through `agent_defaults()`
in server.py, so an edit reaches the config endpoint, the argv builder, and the duration
estimator at once. Sub-5s values save but raise a "429 risk" flag.

### Editable scraper angles
Live in Supabase `scraper_seeds` (**table created and populated — 48 seeds: 40 national, 8
Seattle**). `seeds_common.py` loads them, falling back to the hardcoded
`NATIONAL_SEEDS`/`SEATTLE_SEEDS` literals if the table is unreachable, logging loudly which
it used. Per-angle lifetime yield totals accumulate (`total_added`, `total_cost`, …) so
angles can be ranked by **added-per-dollar** and retired. Select with `--seed-ids` (stable);
`--seed-indices` is deprecated because positions shift on add/delete.

### Cost accounting — the honest picture
The user correctly challenged an early "$9.41 spend" figure. It was an undercount. Now:

- **Dry runs are logged and counted as spend.** They previously skipped the `agent_runs`
  insert entirely while paying full API cost — the biggest hole. Marked by a `-dryrun`
  suffix on `agent_runs.mode` (deliberately chosen over a new column so no migration was
  needed). Cost counts; would-have-been row counts don't.
- **Interactive app calls are costed.** `/api/messages` and `/api/messages-claude` discarded
  their usage block. Now rolled into one `agent_runs` row per surface per UTC day
  (`interactive_gemini` / `interactive_claude`), priced with the correct provider's rates.
- **`deadline_check_log` is folded in** via `fetch_deadline_check_cost()` — that was $2.86
  invisible. Deliberately NOT also rolled into `agent_runs` (would double-count).
- **Interrupted runs show `unknown`, never `$0`.** Five such runs exist in history.
- KPI relabelled **"Estimated spend"** with an agents-vs-app split. 7-day total went
  $9.41 → **$12.27** once the gaps closed.

**Still uncaptured, and stated in the UI rather than hidden:** local estimation isn't
provider billing; a client-side timeout bills server-side and is never seen; runs that die
before reporting contribute $0.

### Billed-spend reconciliation (researched against live docs, not memory)
- **Anthropic: possible but needs a different key.** `GET /v1/organizations/cost_report`
  returns real billed cost. Needs an **Admin key** (`sk-ant-admin…`, read from
  `ANTHROPIC_ADMIN_KEY` in `.env`) or an `org:admin` OAuth token — a regular `sk-ant-api`
  key returns 401, and **the Admin API is unavailable to individual accounts** (an
  organization must be set up in Console first). Amounts return in **cents** as decimal
  strings (easy 100× bug — handled). Integration is built: drop the key in `.env` and the
  card switches from a note to live figures, no code change.
- **Google: not possible.** The Gemini API exposes no billing endpoint at all; the separate
  Cloud Billing API can't be authenticated with an AI Studio key. Gets a dashboard link.
- Dashboard links: `https://platform.claude.com/cost` and `https://aistudio.google.com/spend`.

### Other server work
- **Live streaming output.** `run_agent_subprocess()` used `proc.communicate()`, which
  blocks until exit — for a 100-minute pass the console showed nothing, then discarded the
  output. Now read line by line into a ring buffer plus `agent_logs/<agent>_<stamp>.log`,
  served incrementally by `GET /api/agents/log?agent=&since=`.
- **Localhost guard** (`_require_local()`) on `/admin`, `/api/agents/*`, `/api/seeds` — the
  server binds all interfaces and these routes spend money. Verified: blocked over LAN IP,
  main app still serves.
- **Batched status.** `/api/agents/status` made one Supabase round-trip *per agent per poll*
  on a 3s forever poll. Now one cached query for all four; poll backs off to 20s when idle.
- Query-string routes must use `urlparse(self.path).path` — the dispatch chain compares
  `self.path` by exact equality, so `?agent=…` would otherwise 404.

---

## What worked

- **Auditing the data before trusting a number.** The user's "are you sure the costs are
  correct?" was right, and querying `agent_runs` / `deadline_check_log` / `conversations`
  directly found four distinct blind spots. Don't defend a figure — go check it.
- **Verifying provider capabilities against live docs** rather than answering from memory.
  The Admin-API-key requirement and the "unavailable for individual accounts" restriction
  are both things a confident wrong answer would have missed.
- **Using `agent_runs.mode` suffixes and daily rollup rows** to add dry-run and interactive
  cost tracking with **zero schema migrations** — the user was already blocked on one manual
  DDL step, and adding more would have stalled the work.
- **`--preview` as a free verification path.** Every agent could be exercised end to end
  without spending anything, which is what made iterating on this safe.
- Reading each script's real `argparse` before wiring flags (the prior session's lesson held).

## What didn't work / traps to avoid

- **The server-restart problem, which is worse than it looks.** Three separate failure modes,
  all hit this session:
  1. Git Bash `pkill` **cannot see native Windows python processes** — they're `python3.13`,
     not `python`. Kills silently match nothing and the old process keeps port 8000, serving
     stale code while every "restart" appears to succeed. A zombie from a *previous* session
     (PID 26788, ~8h old) was still running at the start of this one.
  2. The WindowsApps `python.exe` is an **alias shim**: the PID `Start-Process` returns is
     the launcher; a *child* holds the socket. Killing the recorded PID kills the wrong one.
  3. **PowerShell scalar/array trap**: `Get-ListenerPids` returns a bare int for a single
     listener, so `$targets += [int]$recorded` did integer *addition* (7960 + 7960 = 15920)
     and the script tried to kill a PID that never existed, reporting no error. **The `@()`
     around that call in `restart_server.ps1` is load-bearing — do not remove it.**
  `restart_server.ps1` now handles all three and is verified idempotent across consecutive
  restarts. Use it.
- **`curl` on Windows sends CP1252, not UTF-8.** A seed angle containing an em-dash failed
  via curl but works fine from the browser. Don't conclude a bug from a curl-only failure —
  but do check the error message is truthful (this one wasn't; fixed).
- **Don't trust a test's own expectation over the data.** A preview check "failed" (9 seeds
  instead of 8) because a leftover test row was still enabled — the code was right.

---

## Next steps

1. **Nothing is committed.** The working tree holds all of this plus pre-existing
   uncommitted `index.html`/`script.js` resume-import work and untracked
   `SUBSCRIPTION_*.md` / `subscription_common.py` from another thread. Review before staging
   — do not blanket `git add`.
2. **Data-quality issue the new dashboard surfaced:** 2,136 errors across 29 runs — **38% of
   runs failed**, concentrated in the Review Checker (1,524 errors / 11 runs). This was
   invisible before. Worth investigating what those errors actually are before spending more
   on that agent.
3. ~~**`check_reviews.py` carries a TEMP filter**~~ — **removed 2026-08-22**. The stated
   reason (that it caused the 0-row preview) turned out to be wrong; see the current thread.
4. **Yield columns are all zero** until the scraper runs for real. After the first run, sort
   the angles table by added-per-dollar to find dead weight.
5. ~~`claude_common.py`'s `MODEL` is `claude-sonnet-4-6` while `check_deadlines.py` pins
   `claude-haiku-4-5-20251001`~~ — **resolved 2026-08-22**: Haiku everywhere. It was not
   cosmetic; see the current thread for the two costing bugs it was hiding.
6. **Scheduling remains deferred** by choice. The run pipeline was built so a scheduler can
   call the same `run_agent_subprocess()` path later without rework.
7. **SECURITY, unrelated but unresolved:** the git remote URL still contains a live GitHub
   PAT in plaintext (`https://bluefeather78:github_pat_11BNM7JAA0…@github.com/...`).
   `HANDOFF.md` claimed this was sanitized in an earlier session; it was not. Revoke at
   github.com/settings/tokens and reset the remote to the bare HTTPS URL.

## Files changed this thread

Modified: `server.py` (largest — agent schema, preview/log/summary/billed/seeds endpoints,
streaming runner, timing overrides, localhost guard), `admin_console.html` (full rebuild),
`gemini_common.py`, `claude_common.py`, `check_deadlines.py`, `check_reviews.py`,
`refresh_opportunities.py`, `scrape_opportunities.py`, `.gitignore`, `CLAUDE.md`.

New: `agent_common.py` (preview/timing CLI plumbing), `seeds_common.py`,
`migrate_seeds_to_supabase.py`, `scraper_seeds_schema.sql`, `restart_server.ps1`.
Gitignored runtime files: `agent_settings.json`, `agent_logs/`.

---

# PRIOR THREAD: Gemini migration for the 3 offline agent scripts (2026-08-18 to 2026-08-19)

*(Preserved from before — still has open next steps, e.g. `check_deadlines.py`/
`check_reviews.py` had not been live-tested under Gemini as of this writing. Not touched
during the admin-dashboard session above.)*

## Goal (at the time)
Migrate wingman's three **offline agent scripts** — `scrape_opportunities.py` (finds new
catalog rows), `check_deadlines.py` (verifies deadline/status freshness), `check_reviews.py`
(verifies org legitimacy/reputation) — off the Anthropic API onto Gemini, after a national-scope
scrape run burned ~$5-30 on client-timeout failures that still billed server-side. Root-cause,
migrate, and empirically validate the new pipeline before trusting it for real batch runs.

**Standing rule established this session, still in force**: never run anything that calls a
paid API (Gemini or Anthropic) without a fresh, explicit per-instance go-ahead from the user in
chat. This was established after an unexpected ~$30 spend and has been honored before every
single test run since — don't relax it without the user explicitly saying so.

## Current Progress / State

### Root cause of the original failure (Anthropic-era)
The national-mode scrape (16 hand-written "seeds" = category + search angle, one Gemini/Claude
call per seed) had 12/16 seeds fail. Root cause: broad national-scope seeds triggered many
searches per call, exceeding the client-side HTTP read timeout — but **a client-side timeout
does not stop server-side billing**, so failed/killed requests were still charged. Also found
(and reported) a data-loss gap: `claude_common.py`'s original `call_claude()` only extracted
`text`-type content blocks, silently discarding `server_tool_use`/`web_search_tool_result`
blocks — meaning the actual search queries run on any past request were never recoverable.

### Provider evaluation: DeepSeek vs. Gemini (assessed both, chose Gemini)
- **DeepSeek disqualified entirely** — confirmed via DeepSeek's own docs that its API has no
  hosted/server-executed search tool at all ("the model itself does not execute specific
  functions"). Would require standing up and hosting an entirely separate search backend.
- **Gemini chosen** — its `googleSearch` tool is architecturally similar to Anthropic's
  `web_search` (model autonomously decides how many searches to run), and tokens are ~4-10x
  cheaper.

### Migration completed
- **New file `gemini_common.py`** replaces `claude_common.py`'s role — deliberately shapes
  `call_gemini()`'s return value identically (`(text, usage)` with the same `usage` dict shape)
  so the three calling scripts only needed import lines / call-site names / env-var names
  swapped, not their logic. `claude_common.py` is left in place, unused, for rollback only.
- All three scripts (`scrape_opportunities.py`, `check_deadlines.py`, `check_reviews.py`)
  migrated: `ANTHROPIC_API_KEY` → `GEMINI_API_KEY`, `call_claude`→`call_gemini`, stale
  docstrings updated.
- Fixed a deprecated-model bug found live: `gemini-2.5-flash` 404'd ("no longer available to
  new users") — corrected to `gemini-3.6-flash` in `gemini_common.py`'s `MODEL` constant, with
  matching pricing constants (`$0.75`/`$3.75` per 1M input/output tokens, `$14`/1000 search
  requests after 5000 free/month).
- **Confirmed via two rounds of direct doc verification**: Gemini's `googleSearch` tool has
  **no server-enforced cap** on search count (no `max_uses` equivalent — the tool schema is
  genuinely just `{}`, and no `maximumRemoteCalls`/similar field exists in `GenerationConfig`
  for server-executed search). `gemini_common.py`'s `max_searches` param is therefore a SOFT,
  prompt-embedded budget only (folded into the system prompt), not a guarantee — documented
  at length in that file's module docstring.

### Major finding this session: "silent skip-search"
Gemini frequently answers entirely from training data and **never actually invokes
`googleSearch`**, with no error/warning distinguishing this from a real live-verified answer —
the only tell is `usage["server_tool_use"]["web_search_requests"] == 0`. Observed empirically
across ~7 live test runs today: real search only fired **once**; every other seed (including a
12-seed batch where ALL 12 showed zero searches) answered from memory. Confirmed this is not a
batching artifact — re-testing a seed in isolation reproduced the same zero-search result.

This is documented at length in `gemini_common.py`'s docstring with a risk assessment:
**LOW risk for `scrape_opportunities.py`** (worst case: a recalled candidate is already a
catalog dupe, or is real but not live-verified and gets human review before `is_active=true`).
**HIGHER risk for `check_deadlines.py`/`check_reviews.py`** — their entire premise is "current
state," and a silent skip could write back a stale/hallucinated status while `last_checked_at`/
`last_reviewed_at` still gets stamped as freshly verified, with no signal anything went wrong.

### Prompt caching — investigated, deliberately NOT implemented
Before running more seeds, checked whether Gemini prompt caching could cut cost. Concluded not
viable: both explicit and implicit caching require ≥4096 tokens (Gemini 3.6 Flash) to engage at
all, but all three scripts' system prompts are only ~520-815 tokens — 5-8x under the minimum.
Also, `scrape_opportunities.py`'s prompt interpolates the per-seed `angle` near the very start
of the string, which would break prefix-based caching even if the token minimum were met. Not
worth the complexity for a low-volume monthly batch job. Do not re-attempt without prompt
lengths changing significantly.

### Telemetry added
Two new `agent_runs` columns — `total_web_searches`, `silent_search_count` — added via a
manual `ALTER TABLE` the user ran in the Supabase SQL editor (confirmed live via query). All
three scripts now compute and log these per run, alongside the existing `items_processed`/
`errors`/`cost_usd`. `check_deadlines.py`/`check_reviews.py`'s `check_one()` now returns
`(info, cost, searches)` instead of `(info, cost)` to support this.

### Bugs found and fixed live
- `scrape_opportunities.py`'s per-candidate loop called `.get()` on list items without checking
  they were dicts — Gemini occasionally returns non-dict entries in its JSON array, which
  crashed the whole seed mid-loop. Fixed with an `isinstance(candidate, dict)` guard before any
  `.get()` call; verified via a live re-run of the seed that had crashed (seed 9 — completed
  cleanly the second time, 8 candidates, 7 new rows, `errors: 0`).

### Live validation status (national mode, 16 seeds)
All 16 national seeds have now been run at least once under Gemini: seeds 0, 1, 3, 4, 9
individually, plus 2, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15 as one batch. All completed without the
original timeout/crash failure mode — that root cause is resolved. 85 total rows written today
under `source='scraper-national-20260818'`, all `is_active=false` pending manual review.

One background-run oddity: a 12-seed batch was launched via Bash and auto-backgrounded (exceeded
the ~120s default foreground timeout). The task tracker later reported status `"killed"`, which
was **misleading** — cross-checking Supabase (`agent_runs` id=8) showed it actually completed
successfully (`finished_at` populated, 36 rows added, cost $0.0532). A second, orphaned
`agent_runs` row (id=9) appeared concurrently with `items_processed: 0`, `finished_at: null`,
`cost_usd: null` — likely a dead/duplicate invocation that never reached a Gemini call (cost is
`null`, not `0`, suggesting it died before any billable request), flagged as a low-confidence,
unresolved oddity rather than a confirmed problem.

**Not yet validated live this session**: `check_deadlines.py` and `check_reviews.py` compile
cleanly and are migrated, but have **not been run even once** since the Gemini migration — only
`scrape_opportunities.py` has real-world test coverage so far. Seattle mode (`SEATTLE_SEEDS`, 8
seeds) has also not been touched under the Gemini migration — only national mode was tested.

## What Worked
- Rigorous, *repeated* doc verification via WebFetch before asserting technical claims (e.g.
  re-confirming Gemini has no hard search cap, re-confirming caching minimums) rather than
  trusting earlier summarized/remembered research — the user explicitly valued this and asked
  for it directly ("verify this rigorously") more than once.
- Minimal-diff migration design (`gemini_common.py` shaped to match `claude_common.py`'s return
  values exactly) kept the actual script diffs small and low-risk.
- Empirical validation via small, cheap, single-seed test runs rather than large committed runs
  — this is how both real bugs (deprecated model, non-dict-candidate crash) were caught cheaply
  before they could recur at scale in a big batch.
- Being transparent about accidental/ambiguous outcomes (the deprecated-model incident, the
  orphaned `agent_runs` row, the misleading "killed" background-task status) rather than
  omitting or downplaying them — consistently the right call with this user.

## What Didn't Work / Pitfalls to avoid
- **Assuming a client-side kill = no server-side cost is false.** A killed/timed-out client
  request can still complete and bill server-side. This was the root cause of the original
  overspend and must stay top-of-mind for any future long-running seed/item loop.
- Inferring which specific seeds failed in a run from category counts alone is imprecise
  (multiple seeds can share a category) — when precision matters, mine the actual run
  transcript/logs instead.
- Letting a Bash call run past its ~120s default foreground timeout without explicitly setting
  `run_in_background=true` causes an automatic background switch with confusing status
  semantics (a `"killed"` status can be misleading). Always cross-check ground truth (e.g. the
  `agent_runs` table) rather than trusting the task-tracker status alone.
- Stalling for a full turn after stating an intent without executing it (happened once, early
  in this session, planning the DeepSeek/Gemini research) — the user caught it directly. State
  intent and execute in the same turn.

## Older, mostly-resolved threads (carried forward — verify before acting)
- **Security incident**: an earlier session found a raw Anthropic API key committed via
  `.claude/settings.local.json` and a GitHub PAT embedded in the git remote URL. Both were
  scrubbed from local git history and the remote URL sanitized. **Never confirmed**: whether the
  user actually rotated the Anthropic key at console.anthropic.com or revoked the GitHub PAT.
  Ask if this ever comes up again.
- **Folder migration**: canonical working folder moved from `Documents/highschool-wingman` to
  `Documents/wingman`. The old folder's contents were deleted, but the empty folder itself could
  not be removed programmatically (Windows reported it busy) — the user needs to manually delete
  `C:\Users\shama\Documents\highschool-wingman` if they haven't already.
- **UI/UX conventions** (from an earlier "wrapping up" polish pass, still valid if UI work comes
  up again): CTA buttons `bg-orange-500 text-slate-900`; `.pop-card`/`.pop-btn` shared card
  language; `font-heading` (Space Grotesk) on all headings; casual/gen-z-coded copy tone (nav
  tabs "Home Base"/"My Vibe"/"Scout"/"Your Grind"). Always grep before assuming any icon's
  wiring state — it has changed hands several times historically.

## Next Steps (Gemini migration thread)
1. **Immediate, in-progress at the time**: finish strengthening `SYSTEM_BASE` in
   `scrape_opportunities.py` (~lines 111-139) with an explicit imperative instruction forcing
   Gemini to actually invoke `googleSearch` before answering (e.g. "You MUST call the web
   search tool at least once before answering — do not rely on training data alone"). Then
   **re-test on a seed that previously showed 0 searches** (e.g. index 1 or 4) to confirm the
   stronger wording actually changes behavior — this technique is unvalidated so far.
2. Once validated in `scrape_opportunities.py`, propagate the same prompt-strengthening wording
   to `check_deadlines.py`'s and `check_reviews.py`'s `build_system()` — don't propagate an
   unproven fix to the higher-risk scripts first.
3. `check_deadlines.py` / `check_reviews.py` need their first live test run under Gemini —
   currently zero real-world validation, only compile-checked.
4. Seattle mode (`SEATTLE_SEEDS`) hasn't been tested under Gemini at all — only national mode.
5. If the orphaned-`agent_runs`-row oddity (see above) recurs, investigate more deeply — e.g.
   check whether Gemini's API console has a usage/billing dashboard to cross-check against local
   `cost_usd` tracking, since local tracking clearly can't capture cost from a process killed
   before it finalizes its own row.
6. Once satisfied with data quality, the 85 rows written today (`source='scraper-national-20260818'`,
   all `is_active=false`) still need the manual spot-check + `UPDATE ... SET is_active = true`
   step described in `CLAUDE.md` before they go live.

## Rate limiting & 429 error handling & parallel execution prevention (2026-08-19, v2)
**Comprehensive quota management** for web-search scripts (was hitting HTTP 429 errors):
- **Enforced 5-second minimum delay between ALL Gemini API calls** (per Gemini's documented policy).
  Implemented via module-level rate limit enforcer in `gemini_common.py` — all callers get this
  automatically with zero code changes.
- **Simplified 429 error strategy**: on rate limit error, retry exactly once then abort. No more
  exponential backoff with 5 retries — calling scripts now see a clear "this item failed, continue
  to next" signal. Failed items are logged and the batch continues.
- **Parallel execution prevention**: lockfile mechanism ensures only one web-search-enabled script can run
  at a time (they share Google Search quota). On first `call_gemini(..., use_web_search=True)`, an
  exclusive lock is acquired; fails fast if another instance is running. Lock auto-releases on exit or
  after 24 hours if stale.
- **Removed loop-level throttles** from `check_reviews.py` and `check_deadlines.py`. Rate limiting is
  now enforced at the API call level, making per-item explicit sleeps redundant.

## Future optimization: consider model swap for batch scripts
**Evaluated 2026-08-19** (deferred, keeping as-is for now): Currently using `gemini-3.6-flash`
(reasoning model with internal thinking tokens) for all three batch scripts (`scrape_opportunities`,
`check_deadlines`, `check_reviews`). Investigated whether `gemini-3.5-flash-lite` (already in use
for interactive UI calls) could be a viable, cheaper alternative. **Key finding**:
`gemini-3.6-flash-lite` does not yet exist (confirmed against live API 2026-08-18). If/when it
ships, it should be the first target to test. Before then, **could consider**:
- **`check_deadlines.py`**: Most straightforward task (structured extraction: status, deadlines, dates).
  Already uses similar extraction logic in UI via `gemini-3.5-flash-lite` successfully. **Candidate
  for trial swap** to save ~$0.01/item across 1200+ rows (~$12/full pass).
- **`scrape_opportunities.py`**: Higher judgment/reasoning (relevance + legitimacy assessment). Likely
  benefits from 3.6's reasoning tokens. **Keep as-is**.
- **`check_reviews.py`**: Borderline (search + reputation judgment). Could test but worth keeping 3.6
  if 3.6-lite ships. **Keep as-is for now**.
Not implemented yet — only run the swap if you see unnecessary token overhead or cost pressures in 3.6
runs, or once 3.6-flash-lite becomes available.
