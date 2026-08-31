# Opportunity Matching — improvement plan

*Started 2026-08-26. Status: **BUILT (2026-08-30)** on branch
`claude/opportunity-matching-improvement-fb6134`. The design below (finalized 2026-08-29) is
now implemented as a **backend-orchestrated + embeddings** pipeline, wearing the polished
Fresh Finds UI ported from the parallel `opportunity-matching` branch. See "Implementation
status" immediately below for exactly what shipped and what remains; the rest of this document
is the design rationale and measured data, which still stand. It is self-contained: a fresh
session can pick it up with no other context.*

*The 2026-08-29 review settled seven open forks, each marked "(decision 2026-08-29)" at its
section: loosened grade filter at recall (not zero, not strict); a two-direction Phase-1 gate
(the wrong-INCLUSION direction is graded, not just the over-exclusion the substring guard
catches); Phase 5 decoupled from scraper v2 (embed the vetted catalog early); Gemini as the
pinned embedding model both sides; a per-rung classification contract (instant counter +
deterministic cuts); the widened quote check kept as-is with the marketing residual accepted
and watched by the eval; sensitive attributes kept session-only. Phase 7 (validation/eval) is
now written in — it runs concurrent with / ahead of Phase 1, not after Phase 6.*

*Eligibility gets parsed by LIVE reasoning over raw catalog text at query time, never by a
batch agent writing structured flag columns — see the architecture note under Phase 1.
Directions A and B below are kept for their measured data and worked examples; their "how"
sections are superseded by that note.*

---

## Implementation status (2026-08-30) — READ THIS FIRST

**Two parallel implementations existed; this branch is the reconciled base.** A separate
session built branch `opportunity-matching` — a fully **client-side** matcher (keyword + IDF
recall, browser-side funnel/curation, and a **batch** eligibility parser `parse_eligibility.py`
writing an `eligibility_flags` column) with a polished 12-screen Fresh Finds UI behind
`EXPO_PUBLIC_NEW_FINDER`. This branch (`…-improvement-fb6134`) forked from `ab7ab03` — before
that work existed — and independently built the **backend-orchestrated + embeddings** design
this document specifies (i.e. the P5 that the other branch explicitly deferred), plus a live
quote-verified eligibility guard instead of the batch column.

**Shama's decision (2026-08-30), after a full side-by-side:** keep THIS branch's
backend + embeddings pipeline as the base, and **port the `opportunity-matching` UI onto it.
The BACKEND owns the funnel** (the UI is a renderer). Neither branch is merged to `main` yet;
picking which becomes `main` (and retiring/reconciling the other) is the open decision.

**What is BUILT and live-verified on this branch** (dev server + unit suite; the in-app
Browser pane can't dispatch synthetic clicks to RN-web, so interactions were verified via real
JS-dispatched DOM clicks and at the API level):

| Phase (below) | Status | Where |
|---|---|---|
| P1 live feasibility + fit reasoning | ✅ | `app/services/eligibility.py` (quote-verified guard), `curation.py` |
| P2 student-blob assembly | ✅ | `finder.tsx buildStudentBlob` (grade/location/`profile_themes`/`highlight_projects`) |
| P3 cross-kind curation ≤10 | ✅ | `app/services/curation.py`, `match_pipeline.curate_pool` |
| P4 progressive funnel | ✅ | `app/services/funnel.py` (filter axes **+ behavioral vibe rungs**), backend-owned |
| P5 semantic recall via embeddings | ✅ | `app/services/matching.py` (gemini-embedding-001@768d + numpy cosine), `embeddings.py`, `match_vector_schema.sql` (run), `backfill_match_vectors.py` (ran live, 1509 rows) |
| P6 retire the 17-bucket subject list | ✅ | commit `3e7a58c` (inferSubjects/VALID_SUBJECTS/filterValues/quiz deleted) |
| P7 eval harness | ✅ (eligibility) | `matching_eval.py` (9 seed cases); ranking/curation eval still to grow |

**The endpoint:** `POST /api/match` (`app/routes/opportunities.py`). One union response:
a funnel **rung** (`done:false` — axis/question/options+live counts/classification/pool_ids,
`kind:"vibe"` for rerank-only rungs) or the curated **shortlist** (`done!==false`). Modes:
default (recall→curate), `funnel:true` (progressive), `curate_now:true` (curate current pool —
now unused by the client, see below). Recall runs once on rung 0; later rungs carry
client-narrowed `pool_ids` so nothing re-embeds.

**The UI** (ported from `opportunity-matching`, re-typed to neutral props, driven by the
endpoint): `frontend/src/features/freshFinds/ShortlistView.tsx` — `RungStep` (one question per
screen, live per-option counts, NARROWS-THE-LIST / vibe badge, Back/Skip, "Show my matches
now"), `ShortlistView`+`ShortlistCard` (type/tier/review badges, WHY IT FITS, hover-expand,
sticky "Add N to Quest Log"), `FullListView` (the full remaining pool, best-fit-first,
paginated 10/page, client-side/free), `NotInterestedModal`, `ReviewDrawer`. `finder.tsx`
renders these for the suggest/funnel path; the legacy browse grid + facets is kept for the
browse path (`suggestMode===false`).

**Vibe questions (MARQUEE M8).** The behavioral rerank axes + prompts were ported **verbatim**
from `opportunity-matching`'s Shama-approved M8 prompts (per Shama's 2026-08-30 directive),
moved server-side. A vibe rung never filters (empty classification); its answer folds into
curation as a soft `preferences` phrase that reorders, never excludes.

### Funnel redesign — the elicitation dimensions (2026-08-30)

The funnel was reshaped around the student's *intent*, in this order:

1. **Pre-recall SETUP screen** (`SearchSetup`, client) — asked BEFORE the vector match:
   - **Interest** — the profile's themes AND its passion/research **projects**, as selectable
     chips. The picks become the recall query (blank = all). *Projects are boosted*
     (`PROJECT_MATCH_BOOST=1.2` in matching.py): a row matching a project out-ranks one matching
     only a theme at the same cosine — the student's most distinctive signal wins. Themes +
     projects are embedded in one call; recall scores `max(theme cosines, boosted project cosines)`.
   - **Budget** (`price`) and **Timing** (`season`) — carried in `funnel_answers` and applied as
     **recall filters** (`recall_cost_ok`/`recall_time_ok` in matching.py), so the top-100 is
     already affordable + available. Unknown price/season is never cut.
2. **Recall** → top-100 from the interest/cost/time-constrained catalog.
3. **In-funnel rungs**, in order: **engagement** (dim 2, a LOCAL pool-derived FILTER on `type`,
   exact counts, + a free-text "Something else" that reranks) → **outcome** (dim 3, a pool-derived
   RERANK question, M8 prompt) → **eligibility** filters (citizenship/hard_demographic, the only
   axes still classified by the model) → **vibe** rerank axes. Filters gate on `CURATE_AT`;
   rerank questions gate on `POOL_FLOOR` so they fire right up to the shortlist.
   - **Project-focus branch:** when the student picked a passion/research PROJECT in setup
     (`project_focus`), the first rung is a **project-goal** RERANK question (M8,
     `PROJECT_GOAL_QUESTION_SYSTEM`, pool-derived, given the project text — "what do you want to
     do with your project") that REPLACES both engagement and the generic outcome question.
4. **Curation** → ≤10, each "why you" reason **contextual to the whole journey** (M8): it may
   cite a profile specific and/or the student's choices (what they enjoy, want, budget/timing).
   `collect_preferences` (vibe/outcome/free-text) + `describe_funnel_choices` (engagement/cost/
   timing) are passed to curation as `preferences`.

Filter-vs-rerank summary — **filters (cut):** interest (focuses recall), engagement, cost, time,
citizenship, hard_demographic (+ recall's grade/geo/status). **rerank (reorder):** outcome,
selectivity, residential, collaboration, structure, intensity. The old binary `output` vibe axis
was retired (outcome supersedes it).

**Key config knobs:** `RECALL_POOL_SIZE=100` (matching.py — top-N after cosine; raising it trades
a wider net against bigger engagement/eligibility classification), `PROJECT_MATCH_BOOST=1.2`,
`CURATE_AT=15` (filter stop), `POOL_FLOOR=5` (rerank stop), `MAX_RUNGS=5`, `CURATED_LIMIT=10`,
`FUNNEL_MAX_TOKENS=8000` (the eligibility classification call needs ~3k output tokens; 2000
truncated it and silently skipped questions).

**Commits (this branch, newest last):** `b67178f` funnel+shortlist UI · `e2b6e14` curate_now
escape · `9ab0cd9` **M8** vibe questions · `22bf299` funnel axis-dedup guard · `f82bf09` funnel
token-budget fix · `0350ea9` full paginated list + restart loading · `ca57c83` interest-before-
recall · `2a5feec` engagement filter · `660c8db` **M8** outcome rerank + sequencing fix ·
`13d36cd` pool-size header fix · `3d955cb` local cost/time filters (fix inverted counts) ·
`a10bee2` **M8** cost/time become pre-recall filters · `acddde4` projects feed recall (boosted) ·
`61fa750` **M8** contextual "why you" · `2848abc` **M8** project-goal question (replaces
engagement when a project is picked) · `1f1af43` review drawer shows setup choices + project-focus
wiring.

**REMAINING / open:**
- Full-list ("Show my matches now") cards use free local reasons, not the journey-aware curation
  reason (deliberate — no model call on that path). Could add journey-aware annotation later.
- Eligibility caveats on cards — DEFERRED (would be another M8 curation-prompt change).
- Eligibility caveats on cards — DEFERRED (would be another M8 curation-prompt change).
- Phase C polish — `EntryScreen` is ported in ShortlistView.tsx but not wired (finder uses its
  own home hero); `ManageCriteria` (grade/location editing in My Vibe) not ported.
- `curate_now` backend capability is now unused by the client (the full-list escape replaced
  it) — remove or repurpose during cleanup.
- Decide which branch becomes `main`; retire/reconcile the other. Nothing merged to `main`.

*(The "Phases of implementation" section near the end predates all of this — it is the
original proposal. Read it for the dependency reasoning, not for status; status is the table
above.)*

---

## North star

Help a high schooler **discover the kinds of opportunities they'd genuinely enjoy** — not
just the ones that keyword-match what they typed — and never show them something they are
categorically ineligible for. Matching should express **Fit + Growth + Motivation +
Feasibility**, with a deliberate **exploration** signal so the result isn't "you like
coding → 20 coding internships." The product is a recommendation system for exploration,
**not** a personality classifier. Students can disagree ("not interested"), and that
disagreement is signal.

**OUTPUT MODEL — CURATION, not search (operator directive 2026-08-26, load-bearing).**
The end state is a **highly curated list of ≤10 results, each a fantastic fit for THIS
student and one they can actually do.** NOT a long flagged list the student wades through
and filters. This reframes everything below:
- **Feasibility (grade / location / eligibility) is a CURATION INPUT, not a user-facing
  filter or flag.** A program the student can't do isn't a "fantastic fit," so it simply
  doesn't earn one of the 10 slots — the curator declines to pick it, the same way it skips
  a wrong-subject program. We do NOT default to show-and-flag (that is the search instinct).
- **"Err toward showing" (e.g. MIT PRIMES) applies only at the true margins** — genuine
  location UNCERTAINTY, or a fit so exceptional it's worth a slot despite distance. The
  clarification flow removes most uncertainty by ASKING, so with location known the curator
  can confidently decline to spend a slot on something unreachable (Boston-only → not in a
  Seattle student's 10; is in a Boston student's 10, high).
- **This raises the bar in two ways.** (1) Recall matters MORE: with only 10 slots, the one
  perfect program the pre-filter accidentally drops never gets a chance → semantic recall is
  critical. (2) Feasibility inputs must be RIGHT: a slot wasted on an ineligible/unreachable
  row is a bigger loss out of 10 than out of 70.
- **Architectural implication:** today's 7-kind fan-out returns 10-12 PER kind → 40-70 rows
  sorted by tier (the search paradigm). Curated-≤10 needs a **final cross-kind curation
  pass** selecting the single best ≤10 overall (fit + feasibility + a little diversity/
  exploration), not 10-per-kind.

---

## How matching works TODAY (as read from code, 2026-08-26)

Two-part pipeline per search. There is **no single match score** — cheap local code does a
coarse cut, then one LLM call does the fine ranking.

**Entry points** (`frontend/app/(app)/finder.tsx`, `search()`):
- **"Suggest for me"** — the whole synthesized profile is the query; no single type
  requested; **fans out across all 7 kinds concurrently**, one ranking call each, then
  merges + dedupes (first/best-tier wins).
- **Form/quiz path** — student picks one kind (Summer Program, Internship, Conference,
  Journal, Research Competition, Volunteering, Academic Competition) + types a description.

**Step 1 — pre-filter** (`frontend/src/lib/ranking.ts`, `preFilter`): pure code, free.
- Tokenize description, drop stopwords / <3 chars.
- **Type filter** with a size guard: if a requested type has <15 rows (`TYPE_FILTER_MIN_POOL`),
  the type filter is abandoned and the whole catalog is searched (`widened`), UNLESS the kind
  is `strictType` (Conference/Journal), which never widens (empty pool + a note instead).
- **Grade filter** (`isGradeEligible`, `grade.ts`): HARD filter on `grade_min`/`grade_max`,
  but only when the student's grade is known. Rows with no bounds pass for everyone.
- **Score** each row: +1 per query keyword found in `name+org+summary+subject_tags`, **+3**
  if the row's `subject_tags` intersect the AI-inferred subjects. Sort, keep top 100.

**Step 2 — rank** (`rankCandidates`): one Gemini call over the ≤100-row pool.
- Prompt selects "ONLY genuinely good matches… leave out weak or generic fits," best 10-12.
  (Strict kinds use `requireAll`: return everything, tiny real pool.)
- Writes a specific <15-word reason addressed to the student ("you"/"your") + a tier
  (`strong` / `look`).
- Sends the LLM a **compact** row shape: `{id, name, org, summary, subject_tags, type,
  price, location, season}`.

**Supporting AI calls:**
- `inferSubjects` — one Gemini call mapping the profile to **2-5 of a fixed 17-subject list**
  (`VALID_SUBJECTS`). Output is only the +3 scoring nudge above. **Cached on the profile.**
- Profile-tag facet on results — a separate Gemini call re-scoring visible results against
  one selected profile tag (`scoreOpportunitiesForTag`), keyword fallback on failure.

**Fallbacks:** AI ranking failure → keyword-ordered top 12 with a "ranking unavailable"
note. Searches are session-cached, keyed on profile text.

---

## What the matcher does NOT use (the gaps)

1. **The `eligibility` free-text field is invisible to matching.** It is served to the
   client (`OPPORTUNITIES_FIELDS`, `app/config.py`) but `preFilter` never reads it and
   `rankCandidates` never sends it. The only eligibility-type gate is the structured
   `grade_min`/`grade_max`. So **citizenship, geography, demographic, and prerequisite
   restrictions are never enforced** — a student can be ranked against programs they cannot
   join. (`eligibility` IS used downstream, post-match, in `extractTrackerInfo` as context
   only — never as a gate.)
   → **Addressed (2026-08-29, designed not built): Phase 1.** The text gets read live, at
   query time, by the curation/funnel-question calls — no waiting on a batch pass, no
   separate representation to fall out of sync. Every exclusion needs a quote verified in
   code against that row's own text, closing the gap without opening the hallucination risk
   a naive "just read it" version would.

2. **`inferSubjects` matches against only 17 subjects, ~80% hard-STEM.** The list:
   `Mixed, STEM, Medicine, Humanities, Art, Business, Engineering, Computer Science,
   Mathematics, Astronomy, Physics, Chemistry, Biology, Leadership, Law, Logic, Education`.
   No Debate, Speech, Journalism, Creative Writing, Theater, Music, Economics, Psychology,
   Environmental Science, Political Science, Linguistics, History, Languages. A debate/MUN/
   journalism/theater student gets no useful subject signal — the semantic layer that is
   supposed to catch what keywords miss speaks only STEM. (Caveat: it's a +3 nudge, not a
   hard filter, so keyword overlap can still surface an on-topic row if the literal word
   appears — but the ranking degrades to noisy keyword matching for them.)
   → **Addressed (2026-08-29, designed not built): Phase 5.** `inferSubjects` retires
   outright (grep-confirmed one consumer) in favor of per-theme embeddings compared by cosine
   similarity against a per-row catalog embedding — no shared vocabulary on either side, so
   there's no fixed list to be narrow. This also folds in the catalog's own 929-value dirty
   `subject_tags` problem: those values become an ingredient in the row's embedded text
   instead of something matched by string equality.

3. **Grade gating only fires when the student's grade is known.** If the profile never
   states grade and the form dropdown is left at "Prefer not to say," no grade filter runs.
   Worth measuring how reliably the profile captures grade.
   → **Partially addressed.** Phase 2's first-search ask ("grade + location, only if
   unknown") makes grade known far more often than an unprompted profile would. What did NOT
   change: unknown still means unfiltered by design ("unknown ≠ ineligible" stays the rule),
   and a second finding sharpened the gate itself — "rising Nth grader" phrasing (a grade-9
   student reading "for rising 10th graders" IS eligible) is exactly the kind of thing a
   numeric `grade_min`/`grade_max` comparison gets backwards, which Phase 1's live text
   reasoning catches and a structured-field comparison could not.

4. **No behavioral signal.** There is no event log — saves, clicks, dismisses, applies are
   not recorded anywhere (confirmed against `user_activity`, which is a daily rollup for
   metrics, deliberately NOT an event log). So no learn-from-behavior loop is possible yet.
   → **Unchanged by this conversation.** Still Parallel track P-A (event capture already
   shipped, in progress per the commits already on this branch; the consumer side is still
   deferred until real data accrues).

5. **The ranker is tuned for precision, against exploration.** The prompt explicitly says
   "not just anything thematically adjacent," which actively suppresses the serendipitous
   cross-domain matches the north star wants.
   → **Unchanged by this conversation.** Still Direction D / Phase 3's reserved exploration
   slots. Worth noting embeddings give this a sharper definition than it had before: "outside
   the student's lane" can now mean "eligible, but low similarity to every one of the
   student's themes" — a checkable condition — rather than a vibe.

**New gap surfaced by this conversation, not in the original five:** recall has no
geographic scoping step. This wasn't a problem while the catalog was all national/remote, but
the first local rows (Seattle Parks, YMCA — see the scraper-v2 work) make it one: without a
state/metro pre-filter ahead of the semantic similarity score, a Boston-local row can compete
for a Seattle student's recall purely on topical similarity. Folded into Phase 5's recall
design, not deferred as a separate phase — see below.

---

## Measured data state (live catalog, read-only query, 2026-08-26)

*(The catalog has grown since this pull — re-verify exact counts at implementation time.
Treat the percentages below as directional; the 18% geo-residency correction noted below the
table is the one specific number known to have moved.)*

1,261 active rows:

| signal | populated |
|---|---|
| `eligibility` text present | **885 (70%)** |
| any grade bound set | **860 (68%)** |
| of rows WITH eligibility text, also have grade bounds | **859 (97%)** |
| eligibility mentions grade/age but grade cols NULL | **only 16** |

**Read:** collection is not the main problem for grade — where eligibility text exists,
structured grade bounds are set 97% of the time and the extractor almost never misses a
grade mention. Sample values are clean:
- `"Open to US-based young women, genderqueer, and non-binary students in grades 9-12." → 9,12`
- `"Arizona high school students, typically rising sophomores and juniors." → 10,11`

So the eligibility priority is **consumption, not collection**: 885 rows carry citizenship/
geo/demographic/prereq prose the matcher never reads. The 376 rows (30%) with no eligibility
text at all are a `refresh_opportunities.py` coverage job, runnable in parallel.

*(Note: the git-tracked `opportunities.json` is an Aug-20 snapshot that predates the
`eligibility` column entirely — do not measure data state from it. Query the live table.)*

---

## Candidate directions

### A. Eligibility → structured gates + first-search clarification flow

*(No structured gate columns get written — the measured dimensions and the clarification-flow
UX below still stand; see the architecture note under Phase 1 for how eligibility actually
gets read.)*

Two halves: parse the OPPORTUNITY side into gate flags, and capture the STUDENT side via a
first-search clarification flow. Feed both into `preFilter` as gates, exactly as
`isGradeEligible` already works — the hard-filter mechanism exists; it just has one input
today (grade) instead of several.

- **Feasibility must be a GATE, never a soft weight.** A 0.9 interest score must not buy a
  9th grader into a seniors-only program. This is why it does not belong in a weighted sum.

**MEASURED how much each dimension actually gates (live catalog, 2026-08-26, 1261 rows):**

| dimension | rows restricted |
|---|---|
| **grade** | **860 (68%)** — dominant |
| prereq / coursework / GPA | 61 (7%) |
| demographic / gender | 55 (6%) |
| citizenship / US-based | 40 (5%) |
| geo *residency* restriction | 36 (4%), later remeasured at **18%** of eligibility rows carrying ANY geographic mention — see the Architecture note under Phase 1. The 4% here undercounted by measuring only a narrow "residency-locked" phrasing; 18% is the accurate figure for what actually needs the residency-vs-location distinction. |

This distribution drives the whole design — do NOT build a 5-question wall:

- **Only GRADE justifies interrupting every user.** The other four gate 4-7% each; a wall
  for all of them makes most students answer questions that change nothing for them.
- **Location is a graded FEASIBILITY filter (corrected 2026-08-26).** Earlier framing
  ("only a preference, 4% residency-locked") UNDERCOUNTED it. The real signal is FORMAT:
  measured 84% of the catalog is in-person (851 In-Person + 214 In-Person&Remote; only 175
  Remote / 14%) — later remeasured at 73% in-person-capable / 13% remote-only / 14% null
  format, with `state` at 86% populated. Directionally the same story either way: most of the
  catalog is in-person, so location determines physical feasibility of most rows regardless of
  any residency rule in the text. Data we have: `state` = program's state (90% populated,
  later 86%),
  `location` column = FORMAT not place (In-Person / Remote / In-Person and Remote). Data we
  LACK cleanly: residential-vs-commuter.
  **REFRAMED 2026-08-26 under the curation output model (supersedes the earlier
  show-and-flag simplification, which was a search instinct).** Location is a CURATION
  INPUT, not a hard filter and not a user-facing flag:
    * A program the student can't reach is not a "fantastic fit" → it doesn't earn one of
      the 10 slots (the curator declines it — no mechanical exclude, no flag-and-show).
    * Because location is a must-ask in the clarification flow, the curator usually KNOWS:
      Boston-only → out of a Seattle student's 10, in a Boston student's 10.
    * "Err toward showing" only at the margins — true location uncertainty, or a fit
      exceptional enough to spend a slot despite distance.
    * Remote (14%) is location-neutral and always eligible for a slot on fit alone.
  Location is a must-ask (NOT sensitive) alongside grade; its consumption is curation
  weighting, and the residential-vs-commuter gap is a ranking nice-to-have, never a blocker.
- **Much demographic language is SOFT — hard-gating it backfires.** Measured samples:
  "*particularly* from underrepresented communities," "*typically* first-generation,"
  "*particularly* underrepresented and economically disadvantaged" — these are
  encouragement/priority, the program is OPEN to all. Only unambiguous language
  ("Female-identifying and non-binary high school students," "US citizens required") is a
  true gate. The hard part of this dimension is the parser distinguishing hard exclusion
  from soft priority, NOT extraction.
- **Unknown ≠ ineligible, always.** Skipped clarifier / blocked fetch / missing text must
  NEVER hard-exclude — it surfaces the option WITH the restriction flagged ("show with a
  caveat"), never silently drops it. Sensitive fields (citizenship, gender) are PII for a
  minor user base — the clarifier is skippable and skip-safe by construction.

**The first-search clarification flow (operator directive 2026-08-26):**
- On the first find-matches, if gating fields are unknown from the profile, take the user
  through a short series of prompts to clarify, THEN use the answers both to deepen the
  profile and to show the right matches.
- It FIRST reads what the profile already has — grade/state/gender are already extracted
  into profile basics (`PROFILE_BASICS_FIELDS`, `extractProfileBasics`). Only prompt for
  genuine gaps; never re-ask what the profile already states.
- **Grade + location: always resolve if missing** — both not sensitive, both high-impact
  (grade gates 68%, location affects the 84% in-person). Grade is a quick tap; location is
  the student's metro/city + state, used for the graded feasibility model above.
- **Citizenship + hard demographic: ONE optional, clearly-skippable step**, framed as "so
  we can filter out things you're not eligible for," not a demographic survey. Consider
  making it LAZY — surface only when a restricted program would otherwise be affected —
  rather than up front.
  *(SUPERSEDED: this static up-front step is not the final design. The decision-axis
  whitelist and persistence sections below settled on the LAZY option described here as the
  ONLY option — citizenship/hard demographic are asked live by the funnel, only when a
  candidate in the current pool actually requires it, never up front, never stored. Nothing
  here should be built as a standalone clarification-flow step.)*
- **Prereqs (7%): probably NOT a prompt** — too rare and too soft; better as a per-card
  flag ("requires Algebra II — confirm") than a gate.
- Answers write back to the profile (extend `PROFILE_BASICS_FIELDS` / the profile record)
  so it deepens the profile once and never asks again.

### B. Semantic interest matching → replaces the 17-subject boost (bigger lift)

*(See Phase 5 for the embedding architecture this becomes. `inferSubjects` is retirable
outright: grep shows exactly one consumer in the whole codebase.)*

Drop the fixed 17-subject vocabulary AS THE MATCHING MECHANISM. Enrichment agent tags each
row with **free-form topic tags or an embedding**; match student interests by **semantic
similarity**, so "debate" matches "Model UN / mock trial / policy / public forum" with none
predefined. This is the interest-axis half of the vectorization idea below.

- Band-aid alternative (expand to ~40 subjects) is explicitly rejected: any fixed list has
  the same failure next week ("marine biology," "game design," …).

### C. Vectorization (the umbrella that A + B both feed into)

*(Superseded on "how": the "background enrichment agent producing structured axis scores"
mechanism below is NOT what got built — see Phase 5. The actual design is a plain text
embedding per row, computed from raw fields, compared by cosine similarity — no LLM call
per row, no structured axis scores, no `match_vector` shaped as anything other than a plain
vector. The motivating ideas below (Growth/Novelty computability, near-free offline eval,
hard gates staying out of the vector) still stand; only the production mechanism changed.)*

Represent student and opportunity as structured vectors. **Not** "replace the LLM with a dot
product" — a hybrid:
- **Opportunity vectors** precomputed once per row by a background enrichment agent (LLM
  reads the row → structured axis scores → jsonb column, e.g. `match_vector`). Same shape as
  `generate_action_items.py`: page/row in, JSON out.
- **Student vector** from the profile axes, later **weighted by revealed preference** from
  the event log (recent saves up, dismisses down).
- **Three uses:** (1) sharper pre-filter via cosine similarity instead of substring keyword
  overlap; (2) **Fit + Growth + Novelty become computable** — Growth = student below the
  opportunity's level on an axis they want to develop; Novelty = high similarity on some
  axes but low on the most-saved axis (the "surprising fit" generator); (3) near-free
  offline eval (score the whole catalog with no LLM call → makes the exploration tier and
  per-visit re-ranking affordable).
- **Hard gates stay OUT of the vector** (grade/cost/eligibility are filters, not axes).
- **The LLM keeps the final ranking + the "why this fits you" reason** on the top survivors.
  Vectors do the coarse cut + novelty math; the LLM does nuance + human-readable reasons.
- Weighted linear sums (`0.35·interest + 0.25·goal + …`) are a fine v1 but the weights are
  guesses with no data to tune them yet (~15 users). Don't oversell their precision.

### D. Exploration / novelty tier (differentiator)

Within the curated ≤10, a couple of slots are deliberately **exploratory** — a surprising
cross-domain fit, not just the obvious on-subject picks — but they must still be excellent
AND feasible (an exploratory pick that wastes a slot is the same failure as any other). The
`strong`/`look` tier split exists to build on. NOTE: under the curation model (see North
star) this is NOT a separate "Worth exploring" search section the user wades through; it is
2-3 of the 10 curated slots earmarked for serendipity.

### G. Final cross-kind curation pass (the output-model change)

The piece that turns 40-70 fanned-out results into the curated ≤10. Takes the merged
candidate set (after feasibility inputs have shaped it) and selects the single best ≤10
OVERALL — fit + feasibility + a little diversity/exploration — instead of 10-12 per kind.
This is where "each one a fantastic fit" is actually enforced. Likely an LLM curation call
over the top candidates, producing the final ordered short list + the per-item "why you"
reason. Depends on good recall upstream (B/C) and correct feasibility inputs (A), because a
tight list punishes both a missed perfect match and a wasted slot.

### E. Behavioral quiz (replaces the taxonomy-router quiz — AGREED to deprecate)

The current quiz (`QUIZ_ROOT`/`QUIZ_SUB`) is a taxonomy router ("I have a finished paper" →
journal), not a preference instrument. Replace with behavioral questions ("Which Saturday
sounds better: debugging an experiment / teaching someone / building from scratch /
organizing people") whose answers become additional matching signal. No engine change — the
answers feed the same ranker/vector. **Decision recorded: deprecate taxonomy-based browsing
in favor of behavioral questions.**

### F. Event plumbing (prerequisite for any learn-from-behavior work)

A NEW append-only `user_events` table (distinct from `user_activity`, which is a daily
rollup and deliberately not an event log). Per-event grain: `userid, ts, action, opportunity_id,
context jsonb`. Actions capture the preference gradient weakest→strongest: `impression` (with
rank/tier/kind shown) → `open` → `save` → `track` → `apply_click`, with `dismiss`/`untrack`
as explicit negatives, plus `search` (query text) and `tag_filter`.
- Reuse every proven pattern from `user_activity`: buffered writes + background flush,
  RLS-with-no-policies, service-role only, degrade-until-migrated, fail-open (never blocks
  UI). Client `emitEvent()` batches per tick like `httpClient.loadData` already does; POSTs
  to a new `POST /api/events`.
- **Ship capture EARLY even though nothing reads it for weeks** — clicks cannot be
  reconstructed retroactively (same logic as the metrics daily-snapshot: every unlogged day
  is permanently missing).
- Privacy: user base is largely minors; search text is the sensitive field; table stays
  service-role-only, never reachable from the browser.
- **Consumer comes later:** a revealed-preference rollup per user (feeds C's student vector)
  and a "not interested → re-rank" loop (D can use it even without ML).

---

## THE INTERACTION MODEL — progressive elicitation funnel (operator-favored, 2026-08-26)

This is the UX spine that ties A + E + G together and replaces both the static clarification
flow (A's first draft) and the taxonomy quiz (E). It is how the student gets from the catalog
to their curated ≤10.

**The idea:** ask the student only the questions that actually DISCRIMINATE among their real
remaining candidates, progressively narrowing a pool. Start ~100 (recall), narrow to ~60 on
grade + location, then ask what distinguishes the survivors (e.g. "needs summer travel"),
narrow again, and so on — each question more selective than the last — until a tight,
exactly-fitted set remains. Questions are chosen from the live pool, so none is wasted (never
ask about summer travel if every survivor is remote).

**Three stages, DIFFERENT JOBS — do not collapse them:**
1. **Recall** → ~100 candidate pool (semantic + keyword, dir B/C). Everything rides on this:
   a perfect match not in the 100 can never be funneled back in. Curation makes recall MORE
   important, not less.
2. **Progressive elicitation funnel** → narrows to a feasible pool (~15-30) by asking only
   discriminating, decision-relevant questions. Answers deepen the profile (write-back), so
   the student is funneled once, not every visit.
3. **Fit curation (dir G)** → picks the fantastic ≤10 out of the survivors, exploration slots
   included, each with a "why you" reason. **The last mile is FIT, not more filtering** — do
   NOT funnel all the way to 10 by questions, or you get 10 feasible-but-mediocre matches.

**The three traps this design must enforce (in CODE, not prompt):**

- **T1 — Filter vs preference is the load-bearing distinction.** A question may CUT the pool
  only if it is a genuine can't/won't **constraint**. A **preference** must RANK (reweight),
  never cut. Filtering on "these split research vs competition → pick one" deletes a
  competition the student would have loved. Every whitelisted axis is tagged `filter` or
  `preference`; the funnel physically cannot cut on a `preference` axis.
- **T2 — Discriminating ≠ worth asking.** The sharpest statistical split of the pool may be
  useless to a student ("45 university-run, 15 nonprofit-run"). Questions come ONLY from a
  curated whitelist of decision-relevant axes; information gain picks AMONG those, never from
  arbitrary catalog columns.
- **T3 — Never a dead end.** Show live counts ("this leaves 5 — relax a filter?"), let the
  student back up, and stop before the pool collapses. A hard "free only" against a mostly-
  paid pool must offer to relax, not wall the student.

**Question-selection rule (per rung):** ask an axis only when it is (a) on the whitelist,
(b) NOT already known from the profile, (c) discriminating above an info-gain threshold over
the CURRENT pool, and (d) decision-relevant. **Stopping rule:** stop when the pool is small
enough to curate (~≤25), OR no whitelisted axis meaningfully splits it, OR after ~4-5
questions (a longer funnel is a chore). Then hand off to stage 3.

**The decision-axis whitelist.** Only grade and location are ever persisted — every other
filter axis is **session-only** (asked live when relevant, used for that search, discarded)
and every preference axis is **derived live** from the profile text, never a separate stored
value. Location/travel also splits in two below — *residency* (who may apply, a
program-stated fact) and *travel distance* (can the student realistically get there, a
ranking question) were one conflated row before, and they behave too differently to share it.

| axis | filter / preference | source | notes |
|---|---|---|---|
| grade | **filter** (persisted) | stored `grade` (Phase 2) + live text reasoning | the ONLY persisted filter fact besides location; live reasoning catches "rising Nth grader" phrasing a numeric range comparison gets backwards |
| location / travel distance | **preference** (mostly ranks) | stored `location` (Phase 2) + `state` + `location`(format) | the other persisted fact; feeds both this ranking axis and the residency filter below |
| residency (program-required) | **filter** (derived, no ask needed) | stored `location` (Phase 2) + live eligibility-text reasoning | derived automatically from the one stored location fact — "Boston Public Schools students only" vs. a Seattle student's stored location. No separate ask, no separate store — the only "storage" involved is the location the student already gave for Phase 2. |
| cost / ability to pay | **filter, session-only — never stored** | ask + live text reasoning, each time it's relevant | re-asked every time it matters, same as citizenship below; must offer relax (T3) — pool collapses easily |
| citizenship | **filter, session-only — never stored** | live eligibility-text reasoning + an ephemeral funnel question, asked only when a candidate in the CURRENT pool actually requires it | re-asked whenever it's relevant rather than persisted (data-minimization call, minors-heavy user base); skip ≠ ineligible |
| hard demographic (gender-exclusive) | **filter, session-only — never stored** | live eligibility-text reasoning + an ephemeral funnel question, same mechanism as citizenship | soft demographic language NEVER cuts, regardless of the answer |
| time commitment / availability | **filter, session-only — never stored** on genuine unavailability | ask, each time it's relevant | e.g. "only during summer" vs school-year; same never-persisted rule as cost |
| prereq (coursework/GPA) | **preference / flag**, rarely filter | live eligibility text | too soft/rare to hard-cut; surface as a card flag |
| subject / interest area | **preference** (this is FIT) | per-theme student embedding vs. per-row catalog embedding, cosine similarity (Phase 5) | derived fresh from the profile text every time — nothing is stored beyond the profile itself |
| activity type (build/research/compete/help/organize/create) | **preference**, not yet built | Direction E's behavioral quiz, IF built — folded into profile text, not a separate store | ranks; no separate persistence decision needed if it just becomes part of the profile like passion projects |
| work style (independent↔collaborative, structured↔open) | **preference**, not yet built | same as activity type | ranks |
| selectivity / competitiveness | **preference** | derived | ranks |

Only grade and location are ever written to the profile as matching criteria. Every other
filter — cost, time commitment, citizenship, hard demographic — is session-only: asked live
by the funnel only when it's actually relevant to the current pool, used for that search,
discarded when the session ends, and re-asked next time only if it comes up again. `filter`
axes (persisted or session-only) cut; `preference` axes flow into stage-3 curation as weights,
derived fresh from the profile text each time rather than stored separately.

---

## PERSISTENCE + SURFACING — store the criteria, recompute the results (operator directive 2026-08-26)

Two operator thoughts that define the persistence layer and the anti-lock-in design. They
resolve into ONE principle, below.

### 1. Only grade and location are persisted. Everything else is re-derived or session-only.

**No separate `preferences` store.** Subject/interest signal needs no persistence decision at
all — it comes from the profile's own themes, recomputed live whenever the profile text
changes (Phase 5), so it always reflects whatever the profile currently says with nothing
extra written anywhere. Activity type / work style / selectivity (Direction E's behavioral
quiz, not yet built) default to living **inside the profile text itself** if built at all —
picked up the same way passion projects already are, never as a standalone cache the student
never sees. There is no third persisted bucket for "preferences."

Two stores, not three:

- **`hard_constraints`** (persisted) — grade and location ONLY. **Gate now, gate in future.**
- **Session-only** (citizenship, hard demographic) — **never written to the profile record at
  all.** Real gates, asked live by the funnel only when a candidate in the CURRENT pool
  actually requires an answer, used for that search's curation call, discarded when the
  session ends. Re-asked next time only if relevant again — a deliberate data-minimization
  choice for a largely-minor user base: these axes are rare enough in the catalog (~8% and
  ~2% of eligibility-bearing rows) that re-asking costs a few seconds occasionally, not a
  repeated wall, and that's a smaller cost than a lasting sensitive-attribute record most
  searches will never even touch.

Three refinements without which `hard_constraints` goes wrong:
- **Filter hardness is a spectrum.** `immutable` (grade — the student can't act on it,
  gating is correct) vs `relaxable` (location — a standing fact the student can lift for the
  right thing). Store `relaxable: true|false`. Load-bearing for surfacing (below): a
  relaxable-filter violation is never a silent permanent death.
- **Freshness.** Every hard constraint carries `captured_at` + a volatility. **Grade is wrong
  within a year** and must re-confirm each cycle; a hard filter trusted forever silently
  becomes a wrong filter.
- **Provenance.** `set_by: asked | inferred`. An explicitly stated constraint outranks an
  inferred one; an inferred constraint is held loosely and is overridable — never gates
  permanently.

Sketch:
```
profile.criteria = {
  hard_constraints: { grade:{value, set_by, captured_at, volatility:'per-cycle', relaxable:false},
                      location:{value, set_by, relaxable:true} },
                      // nothing else lives here — no cost, no citizenship, no preferences
}
// Subject/interest signal is never stored as "criteria" — it's derived fresh from
// profile.synthesized (the profile text itself) every time via the theme embeddings.
// Citizenship / hard-demographic answers live only in the current search's request state,
// never in profile.criteria — they don't survive past the session that asked for them.
```

### 2. New opportunities must never be locked out by old criteria

**THE PRINCIPLE (answers both this and "how do results stay current"):**
> **Store the CRITERIA, never the result set. The match is always `(live catalog) ×
> (criteria)`, recomputed — never a cached shortlist.**

Same philosophy as the scraper plan's "the catalog IS the ledger — a live GROUP BY, never a
writeback job." Persist the criteria, recompute against the current catalog, and **new
opportunities are evaluated by construction** — nothing is gated by a frozen list, because
there is no frozen list. That alone removes most lock-in. On top of it, the filter/preference
split does the rest:

- **A new row can only be HIDDEN by a hard filter** — and that is correct only when the
  filter is `immutable` and fresh (a 9th grader still can't do a new seniors-only program).
  The failure mode is a STALE or MIS-HARD filter, which §1 handles (freshness + `relaxable`).
  A `relaxable`-filter violation surfaces in an "outside your filters" channel, never dies
  silently.
- **A new row can NEVER be hidden by a preference** (preferences only rank) — but a
  preference can BURY it below the top 10 (the echo-chamber risk). Three mechanisms stop
  that:
  1. **Reserved exploration slots (dir D)** that BYPASS preference ranking but still honor
     hard filters. 2-3 of the 10 are for high-fit opportunities OUTSIDE the student's
     expressed lane — a new debate program reaches a mostly-STEM student here.
  2. **A novelty signal on recently-added rows**, so a brand-new great fit isn't buried under
     older equally-good ones the student has already seen/dismissed.
  3. **Re-evaluation on catalog growth** — when the scraper adds rows, invalidate affected
     students' cached views (cheap semantic pre-filter vs their profile vector) and surface a
     "new for you" nudge. New rows are PUSHED through the funnel, not left waiting to be
     searched.
- **Distinguish "hard-ineligible" (correctly hidden, and we can say why) from "just not
  top-10" (eligible; gets an exploration/near-miss path).** Collapsing these two is exactly
  how a student never discovers anything new.
- **Feedback (dir F) updates preference weights but a single dismiss must NOT kill a
  category.** Preferences decay and update; they don't harden. One "not interested" is a
  nudge, not a permanent wall.

**Consequences for the phases:** never materialize the ≤10 as ground truth (P3 recomputes);
reserved exploration slots are part of P3, not an afterthought; the scraper's new-row inserts
become a re-evaluation trigger (a fourth scraper touchpoint — see below); the minimal
`criteria` schema (grade + location only, plus session-only sensitive answers that never
touch it) lands in P2 (not the old flat profile basics).

---

## Interaction with the scraper v2 work (SCRAPER_IMPROVEMENT_PLAN.md, other session)

Mostly decoupled. Two touchpoints to reserve NOW so that session doesn't retrofit:
1. **`match_vector` is a downstream enrichment column**, computed by the activation-gated hook
   described in Phase 5 — NOT produced by the scraper. Keep the scraper's job (find/verify/
   dedupe) clean. (There are no separate eligibility gate flags to reserve alongside it —
   eligibility is read live from existing fields at query time, never a stored flag.)
2. **The SCRAPER's own Phase 3 (best-copy-wins merge — not this document's Phase 3) must add
   these columns to its "fields owned by other agents, never touch" list**, and must
   INVALIDATE the vector when it changes name/URL/summary (a re-find that upgrades identity
   makes the old vector stale). Cheap to note now, annoying to retrofit. → send this one line
   to the scraper session.
3. **Timing (corrected 2026-08-29 — the coupling is softer than this bullet first claimed):**
   the ~1,300 already-curated, human-activated rows can be embedded NOW — `url` is not an
   embedding input, so a wrong-page URL can't corrupt a vector, and these rows' names/summaries
   are already vetted. Only FRESH SCRAPED rows carry the "junk identity" risk, and they already
   embed at *activation* (human-vetted), never at scrape-time — plus the content-hash recompute
   self-heals a later merge that upgrades identity. So Phase 5's recall win is NOT gated behind
   scraper Phases 2+3; see Phase 5's "Depends on" for the full argument.
4. **Bonus:** the vectorizer doubles as scraper QA — a row whose vector is all-zeros or
   contradicts its `subject_tags` is a review flag, feeding the same reviewer loop.
5. **New-row re-evaluation trigger (from the surfacing design above):** when the scraper
   activates new rows, that is the signal to invalidate affected students' cached views and
   push the new opportunities through the funnel/curation ("new for you"). The scraper need
   not know about students — it just needs the activation to be observable (a timestamp /
   event the matcher can poll or subscribe to). Cheap to expose now.

---

## Phases of implementation (ORIGINAL PROPOSAL — now BUILT; see "Implementation status" up top)

> **This section is the original sequencing proposal and its dependency reasoning. Phases 1–6
> are now implemented (backend-orchestrated, with the `opportunity-matching` UI ported and the
> backend owning the funnel) — for current status read the "Implementation status (2026-08-30)"
> table near the top of this document, not the "nothing built" language below.**


Sequenced by dependency, not by ease. The spine is **recall → funnel → curation**; each
phase states what it delivers, what it depends on, its gate, and its cost. Two tracks run in
PARALLEL to the spine (events, backfill) because they have their own clocks. Paid agent runs
need fresh per-run approval (the ~$30-overspend rule); funnel/curation logic is free code,
model CALLS at query time cost the usual interactive per-call fee.

**Build order (resolves the "where to start" question; confirm before writing code).** The
phase numbers are dependency order, not the order to BUILD:
- **Start with Phase 2 (student-blob assembly) + Phase 7 (eval tooling), concurrently.** Phase
  2 is the only spine phase with zero dependencies and is pure free code; Phase 7's labeled
  samples + scorer are what every later gate is graded against, so they cannot come last. These
  two unblock everything else.
- **Phase 5's existing-catalog embed can also start early** now that it's decoupled from
  scraper v2 (embed the ~1,300 vetted rows; new rows still wait for their own activation) —
  this is what gives Phases 3–4 good recall at launch.
- **Phase 1's two prompts (curation, funnel-question) are M8 + M9 and need EXPLICIT sign-off
  as their own dedicated commit** — general "ready to build" enthusiasm is not that sign-off
  (the marquee rule). The already-committed `refresh_opportunities`/`scrape_opportunities` tag
  fix (`d07d978`) got its sign-off; these two have not.
- Phases 3 → 4 follow once 1/2/5/7 are in place; Phase 6 (retirement) is worked incrementally,
  each item gated on ITS own replacement being live.

### Architecture note — live blob reasoning, not batch enrichment (context for Phase 1)

**Operator directive: no new precomputed enrichment columns.** A periodic agent writing
structured gate flags (`citizenship`, `demographic_hard`, `geo_scope`, …) onto the catalog has
the same failure class Direction B already convicted the 17-subject taxonomy of: a fixed
representation of something that keeps changing underneath it. Every time
`refresh_opportunities.py` or the scraper touches a row's `eligibility`/`summary` text, a
derived flag is stale until the next scheduled pass. **Phase 1 as originally scoped (a new
paid enrichment agent) is DROPPED.** Eligibility parsing moves from a batch write to a live
read, at the two points in the pipeline that actually need an answer.

#### Phase 0 FINDINGS (2026-08-28, read-only live pull; full report + verbatim ground-truth samples in [PHASE0_ELIGIBILITY_FINDINGS.md](PHASE0_ELIGIBILITY_FINDINGS.md))

**Catalog has grown: 1488 active rows** (was ~1261 on 2026-08-26). Eligibility text present on
**1073 (72%)**. Numbers below supersede the "Measured data state" table above where they differ.

1. **Eligibility parse-quality** (keyword buckets over the 1073, a row can hit several):
   grade/age **96%**, geographic **18%**, prereq **11%**, citizenship **8%**, demographic **8%**.
   **~35% of eligibility rows carry more than one restriction type** — the parser must extract
   MULTIPLE gates per row, not classify a row into one axis. Top overlaps: geo+grade, grade+prereq,
   citizenship+grade, demographic+grade.

2. **Hard-vs-soft demographic is the real risk, and it fails in BOTH directions** (n=86 demographic
   rows). A naive "only / must / required" keyword rule tags just **6** hard and misses the rest:
   - **16 rows are HARD-BY-SCOPE** — the demographic term IS the eligible population with no "only"
     ("Female-identifying and non-binary high school students", "BIPOC students aged 15-19", "Native
     American or Indigenous rising juniors"). A verb-only classifier reads these as non-restrictive
     and would show a girls-only program to a boy. So **hard total ≈ 22**, soft **31**, unclear **33**.
   - **The mirror failure:** a gender-scoped (hard) program can contain a stray inclusive verb —
     `ec18636` Girls Who Code SIP ("female, non-binary… Beginners **welcome**") is HARD but reads
     soft; `ec18889` Stanford Next Gen Women in Physics ("Students of **any gender are welcome**") is
     genuinely SOFT. **Same "Women"-titled framing, opposite verdicts, decided by one phrase — Phase 1
     MUST be shown this exact contrast pair as its worked example** (house style: teach with do/don't
     examples, [[feedback_prompts_use_examples]]).
   - **33 "unclear" residual = income / first-gen / "underrepresented" with no verb and no gender/race
     scope.** These split hard (a NUMERIC/AMI income threshold — "household income under $80,000",
     "income ≤ 80% AMI") vs soft (unquantified "low-income background" as priority). The surface text
     alone cannot separate them — this is where the paid LLM earns its cost over keywords, and where
     hand-labeled ground truth is most needed. Ground-truth row ids captured in the scratchpad report:
     6 hard, 16 hard-scope, 31 soft, 33 unclear — a ready labeled sample for the Phase-1 gate metric.
   - Parser false-positive to guard: `ec18818` "passionate about **Asian community**" is a topical
     interest, not an ethnicity gate; several YoungArts rows are citizenship gates the income regex
     mis-swept into demographic.

3. **Grade-capture: 68–71% have a grade bound. Extractor-miss = 38 rows** (double the ~16 assumed) —
   almost all **age-only phrasings the grade extractor never maps** ("Youth age 13-19", "Ages 12–17",
   "15-18 years old") plus enrollment-scoped rows ("High school researchers", "enrolled in Durham
   Public Schools"). **Phase 1's grade step should map age→grade (age 14 ≈ grade 9)** to recover most.

4. **`subject_tags` is 929 distinct FREE-FORM values (99.9% coverage), NOT a 17-bucket vocabulary** —
   so the catalog *data* is not the bottleneck, but two structural problems make B/C urgent anyway:
   (a) the top layer is coarse + STEM-weighted — "STEM" alone tags ~45% of rows, Biology/Eng/Med/CS/
   Physics pile on, humanities/arts/social-science spread thin across an 869-tag tail; (b) the tags are
   **dirty** — case dupes ("Leadership" 213 vs "leadership" 15; "Art" vs "Arts"), synonyms (CS/Coding/
   Programming, Medicine/Healthcare/Public Health, AI/ML/Data Science). Exact-string matching over-recalls
   STEM and under-recalls everything else. This is the CATALOG side; the profile side (`inferSubjects`,
   17 STEM-only buckets) is separately narrow. **Both halves need the semantic rework** — neither exact
   tags nor 17-bucket inference bridges a humanities profile to humanities opportunities today.

5. **Location shape confirmed:** `location` is a FORMAT enum — In-Person **58.5%**, In-Person+Remote
   **14.8%**, Remote **13%**, null **13.7%**. `state` carries the place, populated **86%**. Gate geography
   on `state` (+ eligibility geo text); use `location` only for the delivery-format facet. Treat null
   format (13.7%) and null state (14%) as **unknown, never excluded**.

**Net effect on the phase plan:** Phase 1's schema needs a demographic MODALITY field (hard / hard-scope /
soft) with the exclusion-vs-encouragement examples baked into the prompt, an age→grade mapping step, and
a numeric-income-threshold vs unquantified-priority distinction. Phase 5 (B/C) is confirmed urgent for
BOTH catalog-tag normalization and profile-side breadth. The 33 unclear demographic rows are the labeled
gate sample for Phase 1's "zero open programs wrongly marked exclusive" acceptance test.

#### Two live call sites, not a background agent

1. **Funnel-question design** (live, ONE CALL PER RUNG, not a single upfront batch) — each
   call takes the pool as narrowed by every prior rung's answer, plus the student blob, and
   decides the single next most-discriminating question for the CURRENT pool (or signals
   nothing's left worth asking, triggering the stopping rule). Up to Phase 4's ~4-5 rung cap,
   fewer if the stopping rule fires early. Adaptive and sequential by construction, matching
   Phase 4's design — never one call producing the whole question set at once.
   - **Per-rung contract (decision 2026-08-29): the call returns the pool's classification for
     the axis it asks about, not just the question.** For a filter axis it returns, per
     candidate, whether that candidate is cut / kept / caveat-shown under each possible answer
     (with the `exclusion_quote` + `exclusion_source_field` where it cuts). Two things fall out
     of this for free, and both are load-bearing:
     - **The post-answer cut is deterministic code, not a second model call.** The rung already
       reasoned over the pool to pick the question; it hands back the reasoning, so applying the
       student's answer is a filter over a returned map — no re-reasoning the ~100 rows per
       answer.
     - **The live "matches" counter stays instant.** The UX requires "~60 → ~30" to move the
       moment a narrowing answer is tapped; that count is now free arithmetic over the returned
       classification, even for the eligibility-reasoned axes (citizenship, demographic,
       residency) that are NOT cheap `price`/`grade` comparisons. Without this contract those
       counts would each need their own model pass and the counter could not stay live.
2. **Curation / matching** (live, ONE call, at the end) — the funnel's survivors' full raw
   text + student blob produce the final ≤10, each with a "why you" reason and an eligibility
   verdict.

Neither call site depends on a precomputed flag column. The only fields either one reads are
ones already maintained by the existing regular metadata-refresh cadence — nothing new is
ever written to `opportunities`.

#### Zero-hallucination guard, moved from batch-time to request-time

The rule this repo already enforces for action items (`page_text.py`'s
`claim_is_supported()`/`quote_is_on_page()`) has to move with the call, not get lost in the
move:
- Every `eligible: false` verdict must carry a verbatim `exclusion_quote` AND an
  `exclusion_source_field` naming which of `name`/`org`/`summary`/`eligibility` it came from.
  A restriction can be stated in `summary` ("designed exclusively for NYC high schoolers")
  when the dedicated `eligibility` column is silent, so the check is **not `eligibility`-only**.
- **Code checks the quote is a real substring** — of the named `exclusion_source_field`
  FIRST, and only if that fails, of every other text field on the row before discarding (a
  benign field-mislabel must not throw away a real, verifiable quote). A quote that verifies
  nowhere is discarded — the candidate reverts to eligible, per the standing "unknown ≠
  ineligible" rule. No fuzzy matching, same principle `url_repair.py` already lives by.
- **This guard is ONE-DIRECTIONAL by construction** — it catches over-exclusion (a
  hallucinated exclusion), NOT under-exclusion (a real hard-scope gate the model reads as
  open — "female-identifying students," no "only," shown to a boy). Under-exclusion is the
  worse product harm (a rejected application, not a missed opportunity) and has no substring
  guard available, because there is no quote to verify when the model *fails* to exclude. It
  is covered instead by the wrong-inclusion metric in the Phase 1 gate + Phase 7, graded on
  the labeled hard-scope sample. State this plainly rather than letting the substring guard
  read as full protection.
- **Accepted residual (decision 2026-08-29: keep the any-field design, no marketing guard).**
  Widening past `eligibility` lets marketing hyperbole in `summary` ("exclusively designed to
  challenge top students") read as an exclusion. Not guarded in code; instead the
  over-exclusion metric in the Phase 1 gate watches for it on the labeled sample, and the
  prompt's worked examples distinguish a marketing "exclusively" from an eligibility gate.
- This is what keeps a live call auditable: every `(quote, source_field, verified?)` triple
  can be logged, building an ongoing production eval instead of a one-time gate.

#### Worked examples the prompt carries (house style: examples, not adjectives)

- **Residency vs. location** — "Open only to Boston Public Schools students" is a hard gate;
  "Hosted at Northeastern University in Boston" is not — it says where the program runs, not
  who may apply. Measured at 18% of eligibility rows carrying a geographic mention — bigger
  than a residency restriction might sound, because most of that 18% is this distinction, not
  a rare edge case.
- **Demographic modality** — the Girls Who Code SIP ("female, non-binary… beginners welcome")
  vs. Stanford Next Gen Women in Physics ("students of any gender are welcome") contrast pair:
  same "for women" framing, opposite verdicts, decided by one phrase.
- **"Rising Nth grader"** — means a student CURRENTLY FINISHING grade N-1. A grade-9 student
  reading "for rising 10th graders" is eligible; a naive `grade_min=10` numeric comparison
  would wrongly exclude them. The strongest argument in this whole revision for reading text
  instead of comparing extracted numbers: the extraction itself can encode the wrong meaning.

#### Input shapes

```json
// student blob
{
  "grade": 9,
  "location": {"city": "Seattle", "state": "WA"},
  "profile_themes": [{"theme": "...", "intent": "...", "next_steps": "..."}],
  "highlight_projects": [
    "Passion Project: Built a robotics team that competed at states.",
    "Research Project: Studying grapheme-to-phoneme error rates in Finno-Ugric languages."
  ],
  "funnel_answers": {}
}
```
`highlight_projects` is extracted **in code, for free** from the already-labeled paragraph
structure `synthesizeProfile`'s own prompt already enforces (`"Passion Project: "` /
`"Research Project: "` prefixes, `profile.ts`) — a plain paragraph split, no new model call,
no staleness risk. The general (unlabeled) paragraphs of the profile are deliberately NOT
sent — they mostly restate what `profile_themes` already summarizes at the altitude curation
needs; a case that later needs more is a signal to add a targeted field, not to default back
to the whole blob.

```json
// candidate blob (one per row in the pool)
{
  "id": "ec18636", "name": "...", "org": "...", "type": "Summer Program",
  "summary": "...", "eligibility": "...", "grade_text": "Grades 9-12",
  "subject_tags": ["Computer Science", "Coding"], "price": "Free",
  "location": "Remote", "state": null, "intl": true, "season": "Summer",
  "review_status": "verified", "review_summary": "..."
}
```
Full pass over `OPPORTUNITIES_FIELDS` (`app/config.py`): `type` and `intl` were missing from
the first draft and are added (real fit/eligibility signal). `status` (running/not_running) is
deliberately NOT sent to the model — a discontinued program is filtered at the cheap
code-level recall stage, same tier as the existing grade/type filters, free and deterministic.
`review_status`/`review_summary` ride along as a trust signal, not a fit gate — surfaced on
the card, never used to suppress a slot, since review coverage isn't complete catalog-wide yet
and a missing review must not read as "suspicious" (same unknown≠ineligible principle). `url`
carries no fit signal and is dropped.

#### Cost, honestly

- No batch enrichment spend — removes the "~catalog-sized Gemini pass" Phase 1 used to budget.
- Cost is now purely query-time, scaling with **searches**, not catalog size. Trivial at ~15
  users; needs re-costing if the user base grows, since this doesn't amortize the way a
  once-per-row-lifetime enrichment cost did.
- Prompt size grows (each of ~100 candidates now carries a paragraph of `eligibility` text) —
  fits one context window at this catalog size; measure before shipping, don't assume.
- **Per-search shape, stated honestly:** one "find matches" is up to ~5 sequential per-rung
  question calls PLUS one curation call — ~6 large-context model calls, more than today's
  per-kind ranking. Two things keep this from being a latency wall: (1) the per-rung contract
  above means the pool is reasoned ONCE per rung to design the question, never re-reasoned to
  apply the answer; (2) the rungs are naturally interleaved with the student's own thinking
  time (they answer between calls). The residual cost is real — a model call still runs
  between an answer and the next question appearing — so Phase 4's ~4-5 rung cap is a latency
  budget, not only a "don't make it a chore" budget. Measure the per-rung round-trip on the
  real prompt before shipping; if it's slow, the lever is fewer rungs or a smaller
  per-rung pool, not removing the contract.
- **M8** (the worked examples above become prompt content) and **M9** (a new/changed paid
  call shape) — both need explicit sign-off before implementation, each its own dedicated
  commit, per the marquee rule.

---

### Phase 1 — Live feasibility + fit reasoning (free code + query-time model cost)

No new enrichment agent, no new columns. The two live call sites above (funnel-question
design, curation) read `eligibility`/`summary`/`grade_min`/`grade_max`/`subject_tags` directly
at query time, with the code-side quote-verification guard.
- **Depends on:** nothing — this is the first phase on the spine.
- **Delivers:** eligibility reasoning that is never more than one write behind the live
  catalog, because there is no separate representation to fall behind.
- **Gate (BOTH directions — the single most important correction to this phase):**
  - (a) **ZERO open programs wrongly EXCLUDED** (over-exclusion — the quote-verification
    guard's direction). Every exclusion's quote verifies against its named field.
  - (b) **ZERO ineligible-only programs wrongly SHOWN** (under-exclusion — a hard-scope
    program like "female-identifying students" surfaced to a boy). Measured explicitly on the
    22 hard/hard-scope rows in the Phase-0 labeled sample. This direction has no code guard
    (there is no quote to verify when the model *fails* to exclude), so it is graded, not
    assumed — and it is the WORSE harm.
  - Plus: hard-vs-soft demographic and residency classified correctly.
  Needs the labeled samples (Phase 7 owns them) before this phase can be graded.
- **Cost:** query-time only (see above); M8 + M9, needs sign-off.

### Phase 2 — Student blob assembly + first-search ask (free code)

Builds the `student` blob Phase 1 consumes: grade + location via the existing first-search
2-question ask (only if unknown), `profile_themes` from the existing `filterTags` slot, and
`highlight_projects` extracted in code from the profile's own labeled paragraphs.
- **Depends on:** nothing structurally (there are no flags to wait on anymore) — this phase IS
  the direct input to Phase 1's two live calls.
- **The `filterTags` promotion is the real work here, not a footnote.** Today `filterTags` is
  deliberately the ONE slot kept OFF the search-critical path — `profileDerived.ts`'s own
  comment calls it "the slowest answer of the set" and says folding it in "would make a
  cold-cache search block on tag enrichment it never reads"; `filterValues`/`inferSubjects`
  was the slot built to be fast and blocking instead. Making `profile_themes` a required
  curation input reverses that division of labor, so it needs explicit handling:
  - **Steady state:** `refreshProfileDerived()` already pre-warms every slot in the background
    after each synthesis, so a student who isn't searching in the same instant they finish an
    edit pays nothing extra.
  - **Cold case** (new student, or a search fired seconds after an edit): block the funnel
    call on the slot's own freshness check and show an explicit "personalizing your matches"
    state — never silently ship stale or half-computed themes.
  - **Thin/empty profile:** zero themes is a legitimate answer (the slot already treats it
    that way, not as "not computed yet") — proceed with `profile_themes: []` rather than
    blocking indefinitely. Same "unknown ≠ ineligible" posture applied to signal, not just
    eligibility.
- **Delivers:** grade + location asked once and stored; the student blob Phase 1 needs,
  assembled without paying for the whole profile text.
- **Gate:** a lapsed-eligibility opportunity (wrong grade / gender-exclusive) no longer
  appears for a student it excludes; a Seattle student stops seeing Boston-only day programs
  at the top; skipping the sensitive ask never hides anything; a brand-new student with an
  empty profile still gets a search, degraded but not blocked.

### Phase 3 — Final cross-kind CURATION pass (dir G) (query-time model cost)

Replace the 7-kind fan-out merge (40-70 rows) with a single curation pass selecting the best
**≤10 overall** — fit + feasibility (Phase 2) + a little diversity — each with a "why you"
reason. This is where the curated-≤10 OUTPUT MODEL actually ships.
- **Depends on:** Phase 2 (feasibility inputs available). Benefits from B/C recall but does
  not require it.
- **Delivers:** the ≤10 curated list; the product stops feeling like search.
- **Gate:** result count ≤10; every slot passes feasibility; manual review of N students'
  lists judges each slot a genuine fit; no regression vs today's list on obvious matches. AND,
  because the 2–3 exploration slots are the signature moment (dir D), grade them SEPARATELY —
  an exploration slot must be both a genuine stretch (outside the student's expressed lane,
  which embeddings make checkable: low similarity to every one of the student's themes) AND
  still excellent + feasible. A stretch pick that's actually a bad fit is a wasted slot, the
  same failure as any other; measuring the list's average fit alone hides it.

### Phase 4 — The progressive elicitation funnel (free code; the brainstormed UX)

Upgrade Phase 2's static 2-question ask into the adaptive funnel: info-gain question
selection over the whitelist, `filter`-vs-`preference` tagging enforced in code (T1), live
counts + relax + back-up (T3), stopping rule, whitelist-only questions (T2). Deprecate the
taxonomy quiz (E) here. Funnel output feeds the Phase-3 curator.
- **Depends on:** Phase 2 (the student blob) + Phase 3 (curation as the handoff target).
- **Delivers:** the full "100 → 60 → … → shortlist" experience; the taxonomy quiz retired.
- **Gate:** funnel never cuts on a `preference` axis (unit-tested); never dead-ends
  (count-guard + relax); never asks a non-whitelisted or already-known question; ≤5 rungs.

### Phase 5 — Semantic recall via embeddings; retires the 17-bucket subject list EVERYWHERE (query-time + inline cache; existing catalog embedded EARLY, new rows at activation)

Replaces BOTH halves of the fixed-vocabulary problem at once: the catalog's 929 dirty
free-form `subject_tags` values (Direction B) and the profile's 17-bucket `inferSubjects` call.

**Retirement scope is bigger than one consumer — a full grep found the 17-item list embedded
in five live places, not one, and one of them is an active, ongoing bug independent of this
phase shipping:**
- `frontend/src/lib/ranking.ts` (`inferSubjects` + `preFilter`'s `subjectHints` scoring),
  `profileDerived.ts` (the `filterValues` slot caching it), `constants.ts` (the list itself),
  `app/services/ai.py` (`mock_infer_subjects`, the offline-mode stand-in) — the profile-side
  consumer originally scoped here.
- **`refresh_opportunities.py` — HIGHER PRIORITY, independent of this phase.** Its prompt asks
  for tags "from the list: {17 items}", and its code then **discards any tag the model
  proposes that isn't in that list** (`valid_tags = [t for t in tags if t in VALID_SUBJECTS]`).
  This agent periodically re-touches the entire ACTIVE catalog, so every refresh pass silently
  strips any free-form tag a row previously had — including ones the scraper legitimately
  captured — back toward the 17-word vocabulary. This is actively narrowing the live catalog
  today, not a historical artifact, and is worth fixing on its own schedule rather than waiting
  for the full embeddings buildout below.
- `scrape_opportunities.py` — its Phase 2 extraction prompt forces every NEW row's first tag
  to come from the same 17-item list, perpetuating the STEM skew at insertion time.
- `backfill_subject_tags.py` references the list too, but it's a completed one-off script that
  won't run again — no ongoing risk, no action needed.

Full retirement means deleting `inferSubjects`, the `filterValues` slot, `mock_infer_subjects`,
and rewriting both `refresh_opportunities.py`'s and `scrape_opportunities.py`'s extraction
prompts to stop constraining/filtering `subject_tags` to that list at all.

**Delete the `VALID_SUBJECTS` constant definitions themselves, in every file that has one —
not just their call sites.** Stopping at "nothing calls `inferSubjects` anymore" leaves five
separate hardcoded copies of the list sitting in the codebase, any of which a future change
could silently start reading from again with no signal anything regressed. Deleting the
literal array definitions turns that into a hard failure — a stray reference becomes a
`NameError`/`ImportError` at the point of the mistake, not a silent narrow-vocabulary
regression discovered months later. This is a one-line-per-file cleanup, cheap to do, and
worth doing as its own step within this phase rather than trusting that every call site was
found and removed.

**The same "delete, don't just stop calling" rule applies to where the DATA is stored — but
the two sides are asymmetric, and treating them the same would break Phase 5's own design:**
- **Profile side: delete the actual stored slot.** `filterValues` (`student-profile.
  filterValues.subjects`/`.grade`) is the real persisted artifact of `inferSubjects` — delete
  the `FilterValuesSlot` interface and its key from `ProfileRecord` entirely, not just stop
  computing it. Any remaining reference then fails at compile time (TypeScript), not silently
  returns `undefined`. Precision on what "fails loudly" can mean here: this lives in a
  schemaless jsonb blob, so there's no database-level enforcement possible — the guarantee is
  code-level (won't compile), not data-level. Old stored `filterValues` keys already sitting
  in existing users' profile records become inert once nothing reads them; scrubbing them out
  is a one-time hygiene pass, not required for correctness.
- **Catalog side: do NOT delete `subject_tags` — it's still load-bearing.** There is no
  separate column holding the 17-bucket classification; the constraint lives *inside*
  `opportunities.subject_tags`, the same free-form column this very phase's embedding needs as
  one of its five input fields. Deleting that column would break the embedding design above,
  not just retire the 17-bucket problem. What actually needs to happen is narrower: stop
  WRITING the constrained value (the prompt rewrites above already cover this) — the column
  stays, because it's still doing real work. The legitimate analog to "delete and fail loudly"
  here is a data-quality backfill, not a schema change: flag historical rows whose
  `subject_tags` is JUST a bare 17-bucket tag (e.g. `["STEM"]` alone, no specific tags) as
  candidates for a future re-tagging pass, the same shape as the completed
  `backfill_subject_tags.py` one-off.
  - **[cleanup_subject_tags.py](cleanup_subject_tags.py) does this scrub.** Free (no API
    calls — pure data transformation), case-insensitive removal of the 17 words from every
    row's `subject_tags` (active and inactive alike), `--dry-run` first per the usual
    convention. A row whose entire `subject_tags` was bucket words only goes to `[]` rather
    than being skipped — the `+3` nudge that word was protecting is itself being retired from
    the matching code, so preserving it just to cushion a transition window isn't worth
    special-casing; these rows are still reported separately as the backfill candidates above.
    Delete this script once it's been run — its own copy of the 17-word list should not
    survive as a sixth place the vocabulary lives.

**Embedding input fields — confirmed sufficient, and two exclusions are deliberate, not
oversights:** `name + org + summary + subject_tags + type`. `eligibility`, `price`, `season`,
`location`, and `state` are explicitly EXCLUDED — mixing restriction/logistics language into a
FIT signal risks the embedding keying on who's excluded or where a program runs rather than
what it's about. `review_summary` is a **deferred, future consideration**, not adopted now —
it's third-party legitimacy text, not the program's own description, and shouldn't be added
without evidence it's needed.
- **Embedding model (decision 2026-08-29): Gemini embeddings** (e.g. `text-embedding-004` /
  `gemini-embedding-*`) — same provider already wired for every other Gemini call, so cost
  attribution flows through the existing `provider_for_model()` path with no third provider
  surface (Anthropic has no embeddings API, so Haiku was never an option here). **One pinned
  model, used on BOTH sides** — the catalog row vector and the student theme vector MUST come
  from the same model at the same dimensionality, or cosine similarity between them is
  meaningless. Pin it in one place the way `MESSAGES_MODEL`/`CLAUDE_MODEL` already are, and add
  a feature signature to `_FEATURE_SIGNATURES` (`match_embed`, split catalog-write vs
  student-search if worth distinguishing) so real spend surfaces in the Cost-per-user
  dashboard automatically. Payload note: at 768-dim, ~1,500 rows is ~9MB of float text read
  into `_opportunities_cache` per TTL — fine; re-check if the pinned model is higher-dim.
- **Catalog-side:** [match_vector_schema.sql](match_vector_schema.sql) adds `match_vector`
  (the embedding, plain `jsonb` — no `pgvector` extension needed at this catalog size),
  `match_vector_hash` (the content hash gating recomputation), and
  `match_vector_computed_at`. One-time manual DDL step, same convention as every other
  `*_schema.sql` file — until it's run, Phase 5 has nowhere to write or read a vector, so it
  simply isn't buildable against real data yet; today's matching pipeline is unaffected
  either way since it never reads these columns. One embedding vector per row, cached inline
  **keyed by a content hash of those five fields**. **Refresh rule, generalized from a hook
  that only fires at activation:
  fire whenever a write leaves the row's `is_active` as true** — whether it was already true
  (a `refresh_opportunities.py` pass on an already-live row) or is becoming true right now
  (the activation endpoint). Skip it whenever the row is inactive and stays inactive — a
  scraped or console-edited candidate that's still pending review may never activate, and
  computing its embedding early is often wasted cost. One rule, checked against row state, not
  four different rules per agent:
  - Scraper insert → row lands inactive → skipped.
  - Console manual edit (`ops/core.py`, already scoped to inactive rows only) → skipped.
  - **Activation** (`POST /api/agents/pending/activate`) → row becomes active → fires, once,
    here — the single, low-volume, already-existing call site to hook.
  - `refresh_opportunities.py` → row was already active → fires on its normal write, since
    there's no future activation event to defer to.
  - Scraper merge (Phase 3, re-found duplicate) → fires if the merge target is already active;
    skipped if merging two still-pending candidates (fires later at that row's activation).
- **Student-side:** one embedding **per theme** (not one blob for the whole profile), stored
  on the existing `filterTags` slot record, computed the same moment the theme text is and
  invalidated by the same freshness rule the slot already uses. No new staleness surface, and
  no per-agent write-path problem at all — there's exactly one producer (`extractTagsAndBasics`)
  already gated by that freshness check.
- **Matching:** cosine similarity between a row's embedding and each of the student's theme
  embeddings, best match wins. `subject_tags` stops being matched by string equality against
  anything — it becomes one ingredient folded into the row's own embedded meaning.
- **Infra:** brute-force cosine over ~1,500 rows is milliseconds in plain code — **no vector
  DB / pgvector / ANN index** at this catalog size (real crossover is tens of thousands of
  rows+). The caveat is implementation, not row count: this only holds if the similarity math
  is vectorized (numpy/BLAS or equivalent) — a naive scalar loop over the same row count could
  take seconds instead of milliseconds regardless of catalog size.
- **Two in-process caches, not one:** the DB column above is the durable cache. The app also
  reuses the EXISTING `_opportunities_cache` (same `OPPORTUNITIES_CACHE_TTL`, no parallel
  structure) with `match_vector` added as one more selected column — refreshed on the same TTL
  and explicitly busted wherever a batch agent run completes, same convention already used for
  the activate/reject endpoints. Worst case: a background run's embedding update is visible
  within one TTL window, not immediately — acceptable, since these are periodic batch jobs,
  not something a user is watching happen live.
- **Recall's filter set, corrected — `status != not_running` and `is_active = true`
  (inherited from how the catalog is fetched, not a new filter) always exclude, plus a
  deliberately LOOSENED grade filter. Type does not hard-filter here** (except in the separate
  form path, below):
  - **Grade uses a LOOSENED recall filter, not zero influence and not a strict numeric cut
    (decision 2026-08-29).** The original "zero grade influence" framing was a false dichotomy
    against a strict `grade_min`/`grade_max` cut, and both extremes are wrong: a strict cut
    unrecoverably drops "rising Nth grader" and age-phrased rows the LLM would have kept, while
    zero influence *dilutes the ~100-row pool* — grade gates 68% of the catalog, so for a 9th
    grader a large share of the top-100 *semantic* matches can be older-only rows, pushing the
    one perfect grade-eligible match past rank 100 where it never enters the pool. That is the
    recall failure curation makes matter MOST. The loosened filter keeps a row when ANY of:
    `grade_max >= student_grade`, OR grade bounds are null/absent, OR the `eligibility`/grade
    text contains a "rising"/age phrasing the numeric bounds may have encoded backwards. It
    only ever drops rows that are *unambiguously* above the student's grade by their own
    structured bounds — so it protects the edge cases (which fall through the null/rising/age
    escape hatches) while sparing the pool from 68% dilution. Grade's final, exact call still
    belongs to Phase 1's live text reasoning; recall's job is only to stop a clearly-too-old
    row from crowding out an eligible one.
  - **Type only hard-filters in the separate, explicit form/quiz path** (student picks one
    kind before typing a description) — there it's a stated constraint, same as any other
    filter axis. It does NOT filter in "Suggest for me": Phase 3's whole point is one curated
    list across kinds, and there's no stated type preference to filter on in that flow — type
    isn't even on the funnel's decision-axis whitelist.
- **Geo-scoping pre-filter, needed the moment local rows exist, not deferred to a future
  scale problem.** Local opportunities (Seattle Parks, YMCA — the scraper-v2 work's first
  local rows) change the catalog's *shape*, not just its size: without a state/metro filter
  applied before the similarity score, a Boston-local row can compete for a Seattle student's
  recall purely on topical similarity. Same tier as the status/is_active filters above — an
  objective fact about the row (is it local-only, and to where), not a judgment about the
  student — scope local rows to the student's stored location (Phase 2's ask), pass
  national/remote rows through untouched.
- **The LLM-calling stages stay insulated from catalog growth.** Phase 1's live calls (up to
  ~4-5 funnel-question calls, one per rung, plus one curation call — see Phase 4) only ever
  see recall's output, which is fixed at ~100 rows by design regardless of whether the catalog
  is 1,500 or 50,000 rows — so per-search model cost doesn't scale with catalog size. Only the
  free arithmetic does.
- **Depends on: NOTHING hard — the scraper-v2 coupling is softer than first written
  (decision 2026-08-29).** The original "don't vectorize until scraper Phases 2+3 land" rested
  on "don't embed dirty identity," but three facts defang it: (1) `url` is NOT an embedding
  input — a wrong-page URL can't corrupt the vector, only a junk name/summary can, and those
  are a property of *fresh scraped rows*, not the ~1,300 already-curated, human-activated
  catalog rows; (2) new scraped rows embed at **activation**, not at scrape-time (the
  activation-gated hook), and activation is exactly when a human has vetted identity; (3) a
  later scraper merge that upgrades a live row's name/summary self-heals via the content-hash
  recompute rule. **So: embed the existing vetted catalog EARLY** (the decision was "embed
  existing rows only"), giving Phases 3–4 good recall at launch instead of the keyword+17-bucket
  layer this plan itself calls broken for humanities/debate. New scraped rows keep waiting for
  their own activation — which they already do. The ONLY thing still owed to the scraper session
  is the cheap coordination note: reserve the embedding column in its merge "never-touch" list.
- **Delivers:** semantic matching that needs no shared vocabulary on either side; retires the
  17-bucket subject list everywhere it lives (not just `inferSubjects`); a better top-of-funnel
  feeding Phase 1's recall stage — available EARLY, not gated behind all of scraper v2.
- **Gate:** a debate/journalism/theater profile surfaces on-topic programs it misses today; a
  personalized theme ("Organizing student clubs and enrichment events") surfaces a
  lexically-unrelated but semantically-matching row (e.g. Model UN) that keyword overlap would
  score zero; vector QA (all-zero / contradicts-tags) flags bad scraper rows.
- **Cost:** embedding calls are far cheaper than generation calls (no reasoning/output
  tokens), but this is still a new paid call path on both the catalog-write and student-search
  sides — M9, needs sign-off, own commit.

### Phase 6 — Retire the old logic (cleanup; each item gated on ITS replacement, not all of Phases 1-5)

A dedicated decommissioning pass. Nothing here ships new capability — it removes what the new
logic replaced, once that replacement has actually proven itself. Deliberately last and
deliberately gated, not bundled into the phases that build the replacements: ripping out old
logic the moment its replacement merges leaves no window to compare the two or roll back if
the new path has a problem nobody caught yet. This is a checklist worked through incrementally
as each phase lands, not one atomic step — each item below depends on its OWN corresponding
phase, not on all five collectively.

- **The 7-kind fan-out + merge** (`finder.tsx`'s "Suggest for me" path) — the
  `Promise.all(ACTIVE_KINDS.map(...))` loop and the "first/best-tier wins" merge/dedupe logic
  it feeds. Per the discussion above: retiring "Suggest for me" and shipping Phase 3's
  cross-kind curation pass are the same event, not two — this is that retirement's concrete
  checklist. **Depends on: Phase 3, live and validated.**
- **The 17-bucket subject vocabulary, everywhere it lives** — restated here as the actual
  deletion checklist (already scoped in Phase 5): `inferSubjects()` + `subjectHints` scoring
  in `ranking.ts`; the `filterValues` slot (interface + compute entry) in `profileDerived.ts`;
  the `VALID_SUBJECTS` constant in `constants.ts`; the dead `inferSubjects` import in
  `finder.tsx`; `mock_infer_subjects()` in `app/services/ai.py`; the prompt constraint + the
  tag-discarding filter in `refresh_opportunities.py` (**rewritten, not deleted** — it still
  needs to propose `subject_tags`, just unconstrained); the equivalent prompt constraint in
  `scrape_opportunities.py` (same, rewritten not deleted); and `cleanup_subject_tags.py`
  itself, deleted once it's been run, per its own docstring. **Depends on: Phase 5, live and
  validated.**
- **The taxonomy quiz** (`QUIZ_ROOT`/`QUIZ_SUB`, Direction E) — already noted inside Phase 4's
  own description as something it replaces; restated here as the actual removal, gated on
  Phase 4 being LIVE, not merely designed. **Depends on: Phase 4, live and validated.**
- **`preFilter`'s old grade+type hard-filtering, for whichever flow doesn't keep it.** Once
  Phase 5 ships (the curation flow uses the LOOSENED grade recall filter + `status`/`is_active`,
  not the old strict `grade_min`/`grade_max` cut; type only hard-filters in the separate
  form/quiz path), decide whether `preFilter` forks into two functions — one for the form path
  keeping the strict hard filters, one for the curation flow using the loosened set — or
  stays one function with a mode parameter. This phase is where that decision gets made
  concrete and whichever shape isn't chosen gets deleted, not left as dead code "just in
  case." **Depends on: Phase 5, live and validated.**
- **The `filterValues.grade` vs. `basics.grade` duplication** (still an open question, not yet
  resolved) — once decided, delete whichever of the regex-based profile-text parsing or
  `basics.grade` isn't kept as the source of truth for the fallback chain. **Depends on:** the
  open question being resolved first — see Open Questions.
- **Gate:** for every item above, the replacement must be live and validated BEFORE its old
  counterpart is removed — this phase never runs ahead of what it's replacing. Nothing here is
  a new capability; it's pure removal, and removal-before-validation is exactly the risk this
  phase exists to prevent.
- **Cost:** free — pure code deletion — except the `refresh_opportunities.py` /
  `scrape_opportunities.py` prompt rewrites, which are M8 and need sign-off as their own
  dedicated commit, same as any other prompt change.

### Phase 7 — Validation & eval framework (free; runs CONCURRENT WITH / slightly AHEAD of Phase 1, not after Phase 6)

Not a final phase despite the number — it is the measuring apparatus the other phases' gates
are written against, so its tooling has to exist *before* Phase 1 can be graded. Numbered last
only because it spans all of them. Everything here reuses infra this repo already has
(`grade_mailing_lists.py`'s shape, the pytest suite, `_FEATURE_SIGNATURES`, the Cost-per-user
dashboard) — no new eval system is built.

- **Labeled datasets as durable files, not scratchpad notes.**
  - *Demographic* (hard / hard-scope / soft): the 22/31/33 split from the Phase-0
    investigation already exists as row-ids — save it as a real committed file if it isn't one.
  - *Geographic* (residency gate vs. soft framing vs. "just says where it runs"): does NOT
    exist yet. Per the resolved sequencing call, write Phase 1's prompt FIRST and pull this
    sample to validate against, rather than blocking the prompt on the sample.
  - Both double as the prompt's worked examples (house style: examples, not adjectives,
    [[feedback_prompts_use_examples]]).
- **A scoring/runner script**, modeled on `grade_mailing_lists.py` (`--score` → precision +
  recall against a labeled set, repeatable every time a prompt changes — not a one-off
  measurement). It must score BOTH error directions, because they are not symmetric and the
  worse one has no code guard:
  - **over-exclusion** — an open program marked ineligible (the substring guard's direction,
    plus the accepted marketing-hyperbole residual from the widened quote check — this is where
    a `summary` "exclusively designed to challenge…" false positive would show up).
  - **under-exclusion** — a hard-scope program (girls-only, citizenship-gated) shown to an
    ineligible student. Graded on the 22 hard/hard-scope rows; the Phase-1 gate's direction (b).
- **Automated unit tests** (into the existing pytest suite, no new infra; deterministic,
  fixture-based, no live model call per test):
  - the quote-verification logic — named-field-first then any-field fallback, discard on no
    match, the marketing residual case as a known-accepted fixture.
  - the funnel's T1/T2/T3 code-enforced invariants — a `preference` axis literally cannot
    appear in a cut path; every question comes from the whitelist; the funnel never dead-ends
    without offering a relax.
  - the per-rung classification contract — the returned map, applied as a filter, yields the
    same pool the counter displayed (the counter can never disagree with the actual cut).
  - the embedding refresh hook's row-state logic — fires iff `is_active` is/becomes true;
    skipped for insert-inactive and console-edit-inactive.
- **A lightweight qualitative review workflow for Phase 3's curation quality** — a
  worksheet-style process (same shape as `grade_mailing_lists.py --worksheet`), not full
  automation, given the ~15-user base: a fixed N profiles, a human scores each slot's genuine
  fit, repeatable structure rather than ad hoc. **This is also where the exploration slots get
  their separate grade** (stretch AND excellent AND feasible — see Phase 3's gate), since
  average-fit scoring hides a wasted stretch slot.
- **Cost tracking wired into EXISTING infra:** add feature signatures for Phase 1's calls
  (curation, funnel-question) and Phase 5's embedding calls to `_FEATURE_SIGNATURES`, so real
  measured spend surfaces automatically in the Cost-per-user dashboard rather than landing in
  `other`. (`test_classify_feature.py` compares the signature list against its case list in
  order — add each at the same position in both.)
- **Depends on:** nothing — it is the first thing that should exist, because Phase 1's gate
  already reads "needs a labeled sample before this phase can be graded." Phase 7 IS that
  dependency.
- **Delivers:** the labeled samples, the repeatable scorer (both error directions), the unit
  guards, the curation worksheet, and cost visibility — i.e. the ability to actually grade
  every other phase's gate.
- **Gate:** the scorer runs green on the committed labeled sets; the unit guards are in the
  suite and fail closed on a T1/T2/T3 or hook-state regression.
- **Cost:** free — no model in the eval harness itself; the calls it grades are the ones the
  graded phases already budget.

### Parallel track P-A — Event plumbing (F) — low cost

`user_events` capture has a real deadline: unlogged clicks are unrecoverable, so this should
not wait on the rest of the spine. The remaining work is the consumer side — revealed-
preference weighting into the funnel/curator, "not interested" re-rank — once data accrues.
Independent of the spine; can begin before Phase 1.

### Parallel track P-B — Eligibility text backfill — in parallel with everything

The ~28% of rows with no eligibility text: a `refresh_opportunities.py` coverage run (paid,
gated). Still matters under live-blob
reasoning — Phase 1 can only reason about text that exists — but doesn't block it; a row
with no eligibility text just reasons as "no stated restriction" (unknown ≠ ineligible).

### Deferred — Fit+Growth+Novelty weighting, revealed-preference model

The weighted-formula / behavioral-learning vision waits until events (P-A) have accrued real
data to tune against. Hand-picked weights are a v1 guess only; exploration slots (D) can ship
inside Phase 3 curation without them.

---

## Open questions / next investigation

- [ ] Pull labeled samples for Phase 1's gate metric: demographic (hard / hard-scope / soft)
      and geographic (residency gate vs. soft framing vs. "just says where it runs"). Both are
      needed before Phase 1 can be graded, and both double as worked examples for its prompt.
- [ ] Residential-vs-commuter: a RANKING nice-to-have only (location never hard-gates), not a
      gate decision. Infer from "residential"/"overnight"/"commuter" keywords if/when we want
      to rank "far + residential" above "far + commuter". Not blocking.
- [ ] Default posture when eligibility is unknown/blocked-fetch — don't hard-exclude on
      absence, but don't silently assume "open to all" either. Needs a concrete rule in the
      Phase 1 prompt, not just a principle.
- [ ] Weighting/formula for Fit+Growth+Novelty — deferred until there's behavioral data to
      tune against; hand-picked weights are a v1 guess only.
- [x] **Grade-source duplication — RESOLVED: consolidate onto `basics.grade`.**
      `parseGradeFromText`'s regex has real, common gaps `basics.grade`'s LLM extraction
      doesn't: "I'm a 10th grader" fails (the regex requires the literal word "grade" as its
      own word — "grader" doesn't match that boundary), and a raw age statement ("I'm 15")
      fails completely since there's no age-to-grade mapping in the regex at all. (Explicit
      class-year words like "freshman"/"sophomore" ARE already matched by the regex — that
      specific case isn't a gap, but the two above are.) `basics.grade` becomes the one
      canonical source Phase 2 checks for "is grade known." `parseGradeLevel` (the same
      function, used to parse the form DROPDOWN's own labels) is unaffected — that's a
      different, narrower, controlled input domain and stays exactly as is. See Phase 6 for
      the retirement of `filterValues.grade`'s profile-prose-parsing role specifically.
