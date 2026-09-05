# Opportunity Matching — improvement plan

*Started 2026-08-26. Status: INVESTIGATION / DESIGN. Nothing here is built or approved yet.
This document records the current matching pipeline as it actually exists in code, the
measured state of the data it depends on, the gaps found so far, and the candidate
directions. It is self-contained: a fresh session can pick it up with no other context.*

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

2. **`inferSubjects` matches against only 17 subjects, ~80% hard-STEM.** The list:
   `Mixed, STEM, Medicine, Humanities, Art, Business, Engineering, Computer Science,
   Mathematics, Astronomy, Physics, Chemistry, Biology, Leadership, Law, Logic, Education`.
   No Debate, Speech, Journalism, Creative Writing, Theater, Music, Economics, Psychology,
   Environmental Science, Political Science, Linguistics, History, Languages. A debate/MUN/
   journalism/theater student gets no useful subject signal — the semantic layer that is
   supposed to catch what keywords miss speaks only STEM. (Caveat: it's a +3 nudge, not a
   hard filter, so keyword overlap can still surface an on-topic row if the literal word
   appears — but the ranking degrades to noisy keyword matching for them.)

3. **Grade gating only fires when the student's grade is known.** If the profile never
   states grade and the form dropdown is left at "Prefer not to say," no grade filter runs.
   Worth measuring how reliably the profile captures grade.

4. **No behavioral signal.** There is no event log — saves, clicks, dismisses, applies are
   not recorded anywhere (confirmed against `user_activity`, which is a daily rollup for
   metrics, deliberately NOT an event log). So no learn-from-behavior loop is possible yet.

5. **The ranker is tuned for precision, against exploration.** The prompt explicitly says
   "not just anything thematically adjacent," which actively suppresses the serendipitous
   cross-domain matches the north star wants.

---

## Measured data state (live catalog, read-only query, 2026-08-26)

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
text at all are a `agents/refresh_opportunities.py` coverage job, runnable in parallel.

*(Note: the git-tracked `opportunities.json` is an Aug-20 snapshot that predates the
`eligibility` column entirely — do not measure data state from it. Query the live table.)*

---

## Candidate directions

### A. Eligibility → structured gates + first-search clarification flow

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
| geo *residency* restriction | 36 (4%) |

This distribution drives the whole design — do NOT build a 5-question wall:

- **Only GRADE justifies interrupting every user.** The other four gate 4-7% each; a wall
  for all of them makes most students answer questions that change nothing for them.
- **Location is a graded FEASIBILITY filter (corrected 2026-08-26).** Earlier framing
  ("only a preference, 4% residency-locked") UNDERCOUNTED it. The real signal is FORMAT:
  measured 84% of the catalog is in-person (851 In-Person + 214 In-Person&Remote; only 175
  Remote / 14%). So location determines physical feasibility of most rows regardless of any
  residency rule in the text. Data we have: `state` = program's state (90% populated),
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
- **Prereqs (7%): probably NOT a prompt** — too rare and too soft; better as a per-card
  flag ("requires Algebra II — confirm") than a gate.
- Answers write back to the profile (extend `PROFILE_BASICS_FIELDS` / the profile record)
  so it deepens the profile once and never asks again.
- **Open question (next investigation):** how cleanly does the 885-row eligibility prose
  parse into gate flags — and specifically hard-vs-soft demographic? That decides A's effort.

### B. Semantic interest matching → replaces the 17-subject boost (bigger lift)

Drop the fixed 17-subject vocabulary AS THE MATCHING MECHANISM. Enrichment agent tags each
row with **free-form topic tags or an embedding**; match student interests by **semantic
similarity**, so "debate" matches "Model UN / mock trial / policy / public forum" with none
predefined. This is the interest-axis half of the vectorization idea below.

- Band-aid alternative (expand to ~40 subjects) is explicitly rejected: any fixed list has
  the same failure next week ("marine biology," "game design," …).

### C. Vectorization (the umbrella that A + B both feed into)

Represent student and opportunity as structured vectors. **Not** "replace the LLM with a dot
product" — a hybrid:
- **Opportunity vectors** precomputed once per row by a background enrichment agent (LLM
  reads the row → structured axis scores → jsonb column, e.g. `match_vector`). Same shape as
  `agents/generate_action_items.py`: page/row in, JSON out.
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

**The decision-axis whitelist (initial draft — the tagging is the anti-over-weed guard):**

| axis | filter / preference | source | notes |
|---|---|---|---|
| grade | **filter** (hard gate) | `grade_min/max` + ask | must-ask; gates 68%; not sensitive |
| location / travel | **filter only the clearly-infeasible**, else preference | `state` + `location`(format) + ask | in-person + far + can't-travel → cut; else RANK. Residency-locked + not-local → cut. Remote → neutral. (Per curation model: mostly a weight.) |
| cost / ability to pay | **filter** (student-declared hard budget) | `price` + ask | must offer relax (T3) — many can't pay, pool collapses easily |
| citizenship | **filter** (hard, when program requires) | eligibility flag + ask | sensitive; optional/skippable tier; skip ≠ ineligible |
| hard demographic (gender-exclusive) | **filter** (hard) | eligibility flag + profile gender | applied from profile, not usually asked; soft demographic NEVER cuts |
| time commitment / availability | **filter** on genuine unavailability | `season` + ask | e.g. "only during summer" vs school-year |
| prereq (coursework/GPA) | **preference / flag**, rarely filter | eligibility flag | too soft/rare to hard-cut; surface as a card flag |
| subject / interest area | **preference** (this is FIT) | `subject_tags` / vector | the semantic layer; ranks, never cuts |
| activity type (build/research/compete/help/organize/create) | **preference** | behavioral answer / vector | from E's behavioral questions |
| work style (independent↔collaborative, structured↔open) | **preference** | behavioral answer | ranks |
| selectivity / competitiveness | **preference** | derived | ranks |

Sensitive axes (citizenship, demographic) stay in the optional/skippable tier and are
applied from the profile where already known rather than asked cold. `filter` axes cut;
`preference` axes flow into stage-3 curation as weights.

---

## PERSISTENCE + SURFACING — store the criteria, recompute the results (operator directive 2026-08-26)

Two operator thoughts that define the persistence layer and the anti-lock-in design. They
resolve into ONE principle, below.

### 1. Filters and preferences persist DIFFERENTLY, and permanently

Whenever a student gives new information, route it by its whitelist tag into one of two
durable stores on the profile — and the tag fixes how it is treated **forever**:

- **`hard_constraints`** (the `filter` axes) — grade, citizenship, gender-exclusivity,
  availability window, declared cost/location limits. **Gate now, gate in future.**
- **`preferences`** (the `preference` axes) — subjects, activity type, work style,
  selectivity. **Stored, but only ever RANK — now and in future.** Because they only rank,
  they can never HIDE anything (this is the whole safety of the split).

Four refinements without which this goes wrong:
- **Filter hardness is a spectrum.** `immutable` (grade, citizenship, gender-exclusive — the
  student can't act on them, gating is correct) vs `relaxable` (cost, location — a standing
  fact the student can lift for the right thing). Store `relaxable: true|false`. Load-bearing
  for surfacing (below): a relaxable-filter violation is never a silent permanent death.
- **Freshness.** Every hard constraint carries `captured_at` + a volatility. **Grade is wrong
  within a year** and must re-confirm each cycle; a hard filter trusted forever silently
  becomes a wrong filter. Preferences decay/update from behavior, never harden.
- **Provenance.** `set_by: asked | inferred`. An explicitly stated constraint outranks an
  inferred one; an inferred constraint is held loosely and is overridable — never gates
  permanently.
- **Durable vs session.** A one-search narrowing ("free only, just competitions, right now")
  is EPHEMERAL — it must NOT harden into a permanent profile filter, or the funnel itself
  becomes the lock-in machine. Only standing facts persist; momentary "show me fewer" stays
  in the session.

Sketch:
```
profile.criteria = {
  hard_constraints: { grade:{value, set_by, captured_at, volatility:'per-cycle', relaxable:false},
                      cost_ceiling:{value, set_by, relaxable:true}, citizenship:{...}, ... },
  preferences:      { subjects:[...], activity_types:[...], work_style:{...},
                      set_by, updated_at, decay },   // ranking weights only
}
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
become a re-evaluation trigger (a fourth scraper touchpoint — see below); the two-store
`criteria` schema lands in P2 (not the old flat profile basics).

---

## Interaction with the scraper v2 work (../archive/SCRAPER_IMPROVEMENT_PLAN.md, other session)

Mostly decoupled. Two touchpoints to reserve NOW so that session doesn't retrofit:
1. **`match_vector` (and eligibility gate flags) are downstream enrichment columns**, owned
   by their own agent — NOT produced by the scraper. Keep the scraper's job (find/verify/
   dedupe) clean.
2. **Phase 3's best-copy-wins merge must add these columns to its "fields owned by other
   agents, never touch" list**, and must INVALIDATE the vector when it changes name/URL/
   summary (a re-find that upgrades identity makes the old vector stale). Cheap to note now,
   annoying to retrofit. → send this one line to the scraper session.
3. **Timing:** don't vectorize until scraper Phases 2 (URL truth) + 3 (merge/naming) land —
   vectors computed from a hub page, wrong-page URL, or junk name bake identity errors in
   permanently. Vectorization CONSUMES URL-truth + correct names.
4. **Bonus:** the vectorizer doubles as scraper QA — a row whose vector is all-zeros or
   contradicts its `subject_tags` is a review flag, feeding the same reviewer loop.
5. **New-row re-evaluation trigger (from the surfacing design above):** when the scraper
   activates new rows, that is the signal to invalidate affected students' cached views and
   push the new opportunities through the funnel/curation ("new for you"). The scraper need
   not know about students — it just needs the activation to be observable (a timestamp /
   event the matcher can poll or subscribe to). Cheap to expose now.

---

## Phases of implementation (PROPOSED — not approved, nothing built)

Sequenced by dependency, not by ease. The spine is **recall → funnel → curation**; each
phase states what it delivers, what it depends on, its gate, and its cost. Two tracks run in
PARALLEL to the spine (events, backfill) because they have their own clocks. Paid agent runs
need fresh per-run approval (the ~$30-overspend rule); funnel/curation logic is free code,
model CALLS at query time cost the usual interactive per-call fee.

### Phase 0 — Data spikes (free, read-only, no code shipped)

Resolve the open questions that decide schema and effort BEFORE building. Deliverables are
findings, not features.
- Eligibility parse-quality: pull the 885 values; measure how cleanly they split into
  grade / citizenship / **hard-vs-soft demographic** / prereq. The hard-vs-soft call is the
  risky one — sample and hand-check.
- Grade-capture rate: how often does the profile already carry grade (→ how often the flow
  must ask).
- `subject_tags` vocabulary breadth (→ how urgent B is; if tags are also ~17 buckets, the
  semantic rework moves up).
- Location/residential inference feasibility (keywords) — for ranking only, not blocking.
**Gate:** a one-page findings note appended here; each later phase's schema references it.

#### Phase 0 FINDINGS (2026-08-28, read-only live pull; full report + verbatim ground-truth samples in [PHASE0_ELIGIBILITY_FINDINGS.md](../archive/PHASE0_ELIGIBILITY_FINDINGS.md))

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

### Phase 1 — Feasibility inputs, OPPORTUNITY side (paid agent pass; gated)

Parse eligibility prose into structured gate flags on each row: `citizenship`,
`demographic_hard` (with the exclusion-vs-encouragement MODALITY, not just the group),
`prereq`, plus the existing `grade_min/max`, `state`, `location`(format). New enrichment
agent (shape of `agents/generate_action_items.py`: row in, JSON out) + schema columns with the
degrade-until-migrated + CREATE/ALTER convention.
- **Depends on:** Phase 0 (schema).
- **Delivers:** every row carries machine-readable feasibility flags.
- **Gate:** on a labeled sample, hard-vs-soft demographic classified correctly ≥ target;
  ZERO open programs wrongly marked demographically exclusive (the backfire case). No row
  hard-flagged on soft language.
- **Cost:** one Gemini pass, ~catalog-sized; needs per-run approval.

### Phase 2 — Feasibility inputs, STUDENT side + apply as curation input (free code)

The minimum that makes the feasibility win live WITHOUT the adaptive funnel yet: extend the
profile record with grade/location (+ optional citizenship), a simple **first-search
2-question ask** (grade + location, only if unknown), write-back to profile, and wire the
Phase-1 flags into the candidate pipeline as **curation weighting** — infeasible rows are
deprioritized / not selected, per the curation model (no mechanical hard-exclude except true
residency/gender/citizenship gates; unknown ≠ ineligible).
- **Depends on:** Phase 1 (flags exist).
- **Delivers:** grade + location asked once, stored, and actually shaping results.
- **Gate:** a lapsed-eligibility opportunity (wrong grade / gender-exclusive) no longer
  appears for a student it excludes; a Seattle student stops seeing Boston-only in-person
  day programs at the top; skipping the sensitive ask never hides anything.

### Phase 3 — Final cross-kind CURATION pass (dir G) (query-time model cost)

Replace the 7-kind fan-out merge (40-70 rows) with a single curation pass selecting the best
**≤10 overall** — fit + feasibility (Phase 2) + a little diversity — each with a "why you"
reason. This is where the curated-≤10 OUTPUT MODEL actually ships.
- **Depends on:** Phase 2 (feasibility inputs available). Benefits from B/C recall but does
  not require it.
- **Delivers:** the ≤10 curated list; the product stops feeling like search.
- **Gate:** result count ≤10; every slot passes feasibility; manual review of N students'
  lists judges each slot a genuine fit; no regression vs today's list on obvious matches.

### Phase 4 — The progressive elicitation funnel (free code; the brainstormed UX)

Upgrade Phase 2's static 2-question ask into the adaptive funnel: info-gain question
selection over the whitelist, `filter`-vs-`preference` tagging enforced in code (T1), live
counts + relax + back-up (T3), stopping rule, whitelist-only questions (T2). Deprecate the
taxonomy quiz (E) here. Funnel output feeds the Phase-3 curator.
- **Depends on:** Phase 2 (profile fields, feasibility flags) + Phase 3 (curation as the
  handoff target).
- **Delivers:** the full "100 → 60 → … → shortlist" experience; the taxonomy quiz retired.
- **Gate:** funnel never cuts on a `preference` axis (unit-tested); never dead-ends
  (count-guard + relax); never asks a non-whitelisted or already-known question; ≤5 rungs.

### Phase 5 — Semantic recall, B/C (paid enrichment; sequenced after scraper)

Replace the 17-subject boost with free-form topic tags / embeddings; cosine-similarity
pre-filter; the `match_vector` column. Fixes the debate/humanities blind spot and raises the
recall that Phases 3-4 depend on.
- **Depends on:** scraper v2 Phases 2 (URL truth) + 3 (merge/naming) — do NOT vectorize dirty
  identity. Reserve `match_vector` in the scraper's merge "never-touch" list NOW.
- **Delivers:** semantic matching; a better top-of-funnel; near-free offline eval.
- **Gate:** a debate/journalism/theater profile surfaces on-topic programs it misses today;
  vector QA (all-zero / contradicts-tags) flags bad scraper rows.

### Parallel track P-A — Event plumbing (F) — START ANYTIME, low cost

`user_events` capture shipped EARLY (has a real deadline: unlogged clicks are unrecoverable).
Consumer (revealed-preference weighting into the funnel/curator, "not interested" re-rank)
comes later once data accrues. Independent of the spine; can begin before Phase 1.

### Parallel track P-B — Eligibility text backfill — in parallel with everything

The ~30% of rows with no eligibility text: a `agents/refresh_opportunities.py` coverage run (paid,
gated). Improves Phase-1 coverage but does not block it.

### Deferred — Fit+Growth+Novelty weighting, revealed-preference model

The weighted-formula / behavioral-learning vision waits until events (P-A) have accrued real
data to tune against. Hand-picked weights are a v1 guess only; exploration slots (D) can ship
inside Phase 3 curation without them.

---

## Open questions / next investigation

- [ ] Parse-quality spot-check: pull the 885 eligibility values, see how cleanly they fall
      into citizenship / geo / demographic / prereq — and especially HARD-vs-SOFT
      demographic ("Female-identifying only" = gate; "particularly underrepresented" =
      encouragement, open to all). (Decides A's schema + effort.)
- [ ] Residential-vs-commuter: now a RANKING nice-to-have only (location never hard-gates,
      per the 2026-08-26 simplification), not a gate decision. Infer from
      "residential"/"overnight"/"commuter" keywords if/when we want to rank "far + residential"
      above "far + commuter". Not blocking.
- [ ] How reliably does the profile capture the student's grade? (Determines how often the
      existing grade gate actually fires.)
- [ ] What do the catalog's `subject_tags` actually contain, and how narrow is their
      vocabulary? (If they're also 17-ish buckets, the +3 boost is doubly blunt and B is
      more urgent.)
- [ ] Default posture when eligibility is unknown/blocked-fetch (don't hard-exclude on
      absence, but don't silently assume "open to all" either).
- [ ] Weighting/formula for Fit+Growth+Novelty — deferred until there's behavioral data to
      tune against; hand-picked weights are a v1 guess only.
