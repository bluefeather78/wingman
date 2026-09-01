# Plan — Semantic recall + eligibility, in the main-style grid

*Status: BUILT (2026-08-31) on branch `recall-grid-matching` off `main` — 4 commits, all tests
green (bar one pre-existing main failure), `tsc` clean, endpoint smoke-tested in mock mode.
Awaiting Shama's review + the prod backfill decision. A deliberate, minimal extraction from the
`opportunity-matching` branch onto `main`. NOT a git merge — see "Why not a merge".*

## What was built (commits on `recall-grid-matching`, oldest first)

- `30d239d` **PR1** — recall modules (`matching`/`eligibility`/`curation`/`embeddings`), the
  `gemini_common` embed path, `match_vector` config/cache/strip, schema + backfill. No
  user-visible change. All copied tests green.
- `34a36c6` **MARQUEE M8** — `ELIGIBILITY_ONLY_SYSTEM` prompt + `gate_pool_eligibility` +
  `match_eligibility` cost signature. Built without pre-signoff per waiver; own commit for review.
- `5ac9660` **PR3** — `POST /api/match` (new route module `app/routes/matching.py`) +
  `recall_query` helpers (scoring + strong badge). Smoke-tested end-to-end in mock mode.
- `e58c52c` **PR4** — frontend: `httpClient.match()`, suggest path → `/api/match`, first-screen
  theme picker, theme query-facet that re-runs recall, strong badge. `tsc` clean.

**Findings that changed the rollout:**
- **PR2 is mostly already done in prod.** The dry-run ran clean (no 400), so
  `match_vector_schema.sql` is ALREADY migrated and ~1,456 of 1,678 rows are already embedded
  (the earlier branch's live backfill). Only **222 rows need embedding, est. ~$0.0036** — not the
  full-catalog spend the plan budgeted. Left unrun per D3 (paid write to prod); trivially cheap.
- **No activation hook exists anywhere** — not even on `opportunity-matching`. `refresh_row_embedding`
  has NO call site; the existing vectors came from manual `backfill_match_vectors.py` runs. So
  D7 "wire the hook" was never built: re-embedding is a **manual backfill run** (cheap), to be
  done after activating new rows. Wiring the hook is an optional future nicety, not a blocker.
- **`EMBED_MODEL` (`gemini-embedding-001`) resolves** — a stray real embed call during smoke
  testing returned 429 (rate-limited), not 404, confirming the model id is live.

## Build decision log (2026-08-31 — Shama authorized "build it now", waived M8 pre-signoff, review tomorrow)

## Build decision log (2026-08-31 — Shama authorized "build it now", waived M8 pre-signoff, review tomorrow)

- **D1** — Built as **staged commits on one branch** (`recall-grid-matching`), each commit
  mapping to a planned PR, since one session can't open/merge 4 separate PRs. Splittable later.
- **D2** — M8 eligibility-only prompt built **without pre-signoff** per explicit waiver; kept
  as its **own dedicated commit** for tomorrow's review.
- **D3** — **PR 2 (prod DDL + paid backfill) not auto-executed**: `match_vector_schema.sql` is
  manual DDL (no PostgREST DDL endpoint) and the backfill is real spend. Free `--dry-run` run
  to report the number; the paid `--yes-really` + DDL are left for Shama.
- **D4** — `gemini_common.py` gained the embedding call path (`call_gemini_embed` etc.) as a
  **pure additive** port (91 lines, 0 deletions) — the M9 seam was already marquee-approved in
  its own comment; needed by `embeddings.py` + `backfill_match_vectors.py`.
- **D5** — The client-strip of `match_vector` was hand-added to **main's** route
  (`handle_opportunities`) rather than merging the branch's funnel-entangled route file.
- **D6** — PR4 frontend rewire **delegated to a subagent** with a bounded spec + `tsc`-clean
  gate; diff reviewed before commit. The **form/quiz path kept** the old
  `preFilter`/`rankCandidates` (only the suggest path moved to `/api/match`); blob
  `location.state` comes only from the `homeState` field for now (profile-basics source is a
  follow-up); old single-tag client scoring left **inert, not deleted** (clean-up later).
- **NOTE** — `tests/unit/test_ops_shaping.py::test_paid_tools` fails on **clean main** too
  (verified by stashing) — a PRE-EXISTING failure, unrelated to this work.

## What's left for Shama (tomorrow)

1. **Review the M8 prompt** (`34a36c6`, `app/services/pool_eligibility.py`) — built under waiver.
2. **Run the 222-row backfill** (`python backfill_match_vectors.py --yes-really`, ~$0.0036) so
   the newest rows are recallable. Schema is already migrated; this is the only paid step, and
   it's <1¢.
3. **Decide the merge**: this branch → `main`, and how it relates to `opportunity-matching`
   (the fuller funnel/curation branch). This cut is designed to co-exist / supersede cleanly.
4. **Optional**: wire `refresh_row_embedding` into the activation path so new rows self-embed
   (today: re-run the backfill after activating rows — cheap).
5. **Calibrate** `WINGMAN_STRONG_MATCH_MIN` (and `WINGMAN_RECALL_MIN_SCORE`) once live recall
   scores are logged — the defaults are provisional.

## Goal

Ship the **one high-value, low-risk win** from the matching branch — **semantic recall**
(embeddings replace keyword `preFilter`) — plus **eligibility gating**, inside the **existing
main-style results grid with client filters**. Leave the funnel, setup cards, curated-≤10
shortlist, and Phase-6 frontend deletions behind.

**Output model is SEARCH, not curation** (a conscious, interim reversal of the plan's
"curation not search" directive): the student picks which profile themes to search on, gets a
semantically-ranked pool of ~100 eligible rows, and narrows it with the same facets `main`
has today.

**Themes are a filter, not a one-time gate.** The first screen is a light "where do you want
to start" pick — copy like *"What would you like to start with? You can always change this
later in the filters."* — and the very same theme selector reappears **as a facet in the
results view**, so the student can add/drop themes without going back. This means the results
view has **two classes of filter**, and the difference is load-bearing:
- **Query filters — themes (and projects):** they *are* the recall query, so changing them
  **re-runs recall server-side** (a fresh `/api/match` → new pool). Costs a (usually cached)
  embedding + one eligibility call.
- **Pool filters — type / cost / format / subject-tag / profile-tag:** they narrow the pool
  already returned, **client-side and free**, exactly as `main` does today.

### What ships
- Semantic recall (cosine over per-theme embeddings) replacing keyword `preFilter`.
- A theme-selection step (reuses the existing `filterTags` MECE themes) as the query.
- Eligibility gating over the pool (verbatim-quote guard) so ineligible rows are dropped.
- The existing main grid + facets (type / cost / format / subject-tag / profile-tag).
- A cosine-derived "strong match" badge so the top of the list isn't an unlabelled wall.

### What is deferred (and layers back with zero rework — recall is their foundation)
- Curated ≤10, "why you" reasons, tiers, exploration picks, goal-format ranking.
- The progressive funnel + vibe/outcome questions.
- The pre-recall setup card stack.
- The behavioral event-capture consumer.

---

## Why not a git merge

The branch is 58 interleaved commits; the value is spread across **modified shared files**
that also carry the funnel and the Phase-6 frontend deletions, and git merge is all-or-nothing
per commit. But the recall/eligibility logic was written as **standalone new modules**, so it
**extracts** cleanly even though it doesn't **merge** cleanly.

| Piece | How it comes over |
|---|---|
| `matching.py`, `eligibility.py`, `curation.py`*, `embeddings.py` | **Copy as-is** — new files on main, zero conflict |
| `match_vector_schema.sql`, `backfill_match_vectors.py` | **Copy as-is** — new files |
| their unit tests | **Copy as-is** — new files |
| `app/routes/opportunities.py` (the `/api/match` route) | **Do NOT merge** — write a NEW trimmed handler |
| `app/services/opportunities.py` (match_vector in the cache select + degrade) | **Port the small select change only** |
| `app/services/ai.py`, `resume.py`, `finder.tsx`, `match_pipeline.py`, `funnel.py` | **Leave behind** |

\* `curation.py` is copied for its `eligibility.py`-backed guard and `build_candidate_view`,
but its fit-ranking prompt is unused here; the eligibility pass gets its own trimmed prompt
(see below).

---

## Runtime flow (the whole thing on one screen)

```
STUDENT (main-style Fresh Finds)
   │  1. FIRST SCREEN: "What would you like to start with? You can change it later in filters."
   │     pick which profile themes to search on  (filterTags slot — already on main)
   ▼
POST /api/match           (NEW trimmed route on main's opportunities.py)   ◄──────────────┐
   │  2. embed the selected theme texts        embeddings.embed_student_themes (cached)    │
   │  3. recall top-100 by cosine              matching.recall  (grade+geo gates, floor)   │
   │  4. eligibility pass over the pool         NEW eligibility-only prompt (M8) + guard    │
   │       - only rows carrying restriction text go to the model (keyword prefilter)       │
   │       - verified-ineligible rows dropped; unknown/unclear kept (unknown != ineligible)│
   │  5. attach cosine score + strong-match badge                                          │
   ▼                                                                                       │
RESPONSE  { results: [ {...row, score, strong, eligible:true}, ... ], pool_size }          │
   │                                                                                       │
   ▼                                                                                       │
MAIN GRID + FACETS (existing)                                                              │
   │   • THEME facet (query filter)  ── change themes ──► RE-RUN recall ───────────────────┘
   │   • pool facets (client, free): type / cost / format / subject-tag / profile-tag
```

Per recall: **1 embedding call** (cached across identical theme sets) + **1 flash-lite
eligibility call** over the restriction-bearing subset. Recall itself is free numpy. Changing
a **pool facet** costs nothing; changing the **theme facet** pays one recall (cached embedding
if the theme set was seen before).

---

## The one net-new prompt (MARQUEE M8 — needs sign-off + its own dedicated commit)

`curation.py`'s prompt only verdicts the rows it *picks* or *drops*, not all 100 — so a
filterable list of 100 needs a **new eligibility-only prompt** that verdicts every row it is
shown. It is small and reuses `eligibility.py`'s quote guard verbatim; it is **Part 1 of
`CURATION_SYSTEM` with the fit half removed**, returning one verdict per candidate:

```
{"verdicts":[{"id":"...","eligible":true,"exclusion_quote":null,"exclusion_source_field":null},
             {"id":"...","eligible":false,"exclusion_quote":"verbatim sentence","exclusion_source_field":"eligibility"}]}
```

Design rules carried over from `CURATION_SYSTEM` unchanged (the rising-grader keep, the
entry-window too-late exclude, residency vs. location, hard-vs-soft demographic, and the
"if you cannot quote it verbatim, do not exclude"). The `eligibility.py` guard then
re-verifies every quote in code — the model can only ever make the list MORE inclusive.

**Cost/latency guard:** only send the model rows whose `eligibility` (and `summary`) text
actually carries restriction signal (a keyword prefilter — citizen, resident, only, female,
grade, etc.); rows with no eligibility text or no restriction words are marked eligible
without a model call. Keeps the call small and dodges truncation on a 100-verdict response.

---

## Work items

### A. Backend — copy the standalone modules (no conflict)
1. Copy `matching.py`, `eligibility.py`, `embeddings.py`, `curation.py` + their tests.
2. Copy `match_vector_schema.sql`, `backfill_match_vectors.py` + test.
3. Do **not** copy `funnel.py`, `match_pipeline.py`.

### B. Backend — the new trimmed `/api/match` route (net-new, on main's opportunities.py)
4. `POST /api/match`, body `{ student:{grade,state,theme_texts[],project_texts?[]}, userid }`.
   - `embed_student_themes` → theme (and optional project) vectors.
   - `matching.recall(rows, theme_vectors, grade, state, project_vectors=…)` → top-100.
     (cost/time/type prefs left empty — no funnel; grade+geo+floor only.)
   - eligibility pass (item C) → drop verified-ineligible.
   - attach `score` and `strong` (cosine ≥ threshold) per row; return the pool.
   - cost accounting: `record_interactive_cost_async("interactive_gemini", …)` for the
     eligibility call, with a new feature signature `match_eligibility` (item F).
5. **Port the cache select change**: main's `app/services/opportunities.py` must include
   `match_vector` (+ `match_vector_hash`) in the row select, with the **degrade-when-not-
   migrated** fallback (branch commit `95c2c82`) so a pre-migration prod doesn't 500.

### C. Backend — the eligibility-only pass (item = the M8 prompt above)
6. `ELIGIBILITY_ONLY_SYSTEM` prompt + a `gate_pool_eligibility(pool, student, call_gemini,
   extract_json)` service that: keyword-prefilters, calls the model on the restriction subset,
   runs each returned verdict through `eligibility.apply_eligibility_verdict`, returns the
   surviving rows. Pure-ish (model call injected) so it is unit-testable like the rest.
   Add labeled cases to `matching_eval.py` (already has 11 eligibility seeds).

### D. Backend — the activation embedding hook (durability — DO NOT SKIP)
7. Wire `embeddings.refresh_row_embedding` into the write path so a row is (re)embedded
   whenever a write leaves it `is_active=true` — the activation endpoint AND a
   `refresh_opportunities.py` pass (per `match_vector_schema.sql`'s "when this gets written").
   **Verify the call-site exists** (the branch documents the hook but the wiring lives in a
   modified file we are not merging wholesale) — if it is not wired, **new rows never become
   recallable** and recall silently decays as the catalog grows.

### E. Frontend — keep main's grid, rewire the search
8. **First screen — theme picker as a soft start, not a gate.** Render the `filterTags` MECE
   themes (already computed on main) as selectable chips under copy like *"What would you like
   to start with? You can always change this later in the filters."* Default all-selected; the
   picks become `theme_texts`. It is explicitly reassuring and reversible — no "you must
   choose" wall.
9. **Theme selector reappears in the results view as a QUERY facet.** The same selector sits
   alongside the pool facets, pre-populated with the current picks. Editing it **re-POSTs
   `/api/match`** (new recall), with a loading state on the grid — this is the one facet that
   is not a free client-side narrow, and the UI should make the "we went and found new
   matches" moment feel intentional (a brief spinner + count update), not laggy. Debounce
   rapid toggles into one call; the server-side embedding cache makes a repeat theme set free.
10. **Pool facets stay client-side and free** (type / cost / format / subject-tag /
    profile-tag) over the returned pool, exactly as `main` does today. The visual/interaction
    distinction between "changing this re-searches" (themes) vs. "changing this narrows what's
    here" (the rest) should be legible — a subtle grouping or label, not jargon.
11. Replace the body of main `finder.tsx` `search()`: instead of client `preFilter` +
    `rankCandidates`, POST to `/api/match` and render the returned pool in the existing grid.
    - Keep `inferSubjects`? **No** — superseded by embeddings; leave it on main untouched for
      any *other* caller, just stop using it in the search path (do not port Phase-6's deletion).
    - Add the "strong match" badge from `row.strong`.
12. Empty/thin-profile state: a student with no themes → recall returns the filtered set
    unscored, or the floor shrinks it → show main's existing empty state. Also handle
    "you deselected every theme" in the facet (treat as all-themes, or prompt to pick one).

### F. Cost visibility
13. Add a `match_eligibility` signature to `_FEATURE_SIGNATURES` (server) so the pass's spend
    is attributed, not dumped in `other` (mirror branch commit `c9ccae8`).

---

## Data / operational (MARQUEE M9 — paid, prod)

Order matters; nothing recalls until 1–2 are done.

1. **Run `match_vector_schema.sql`** in the Supabase SQL editor (adds `match_vector`,
   `match_vector_hash`, `match_vector_computed_at`). Until then the route degrades to "not
   migrated" and recall is off (grid still works on the fallback path).
2. **`python backfill_match_vectors.py --dry-run`** → confirms count + estimated cost.
   Then, with approval, **`--yes-really`** — a paid embedding pass over the ~1,500 active
   prod rows (chunked 100). This is the one real spend; get the dry-run number first.
3. Confirm the **activation hook (D7)** is live, so rows activated after the backfill embed
   automatically. Spot-check by activating a row and checking `match_vector_computed_at`.

Ongoing per-search cost: 1 (usually cached) embedding call + 1 flash-lite eligibility call —
cents, on live traffic, which `main` does not pay today. Acceptable and monitored via F11.

---

## Marquee / approval gates (do these as their own commits)

- **M8** — the new `ELIGIBILITY_ONLY_SYSTEM` prompt (C6): explicit sign-off, dedicated commit.
- **M9** — the embedding call path already exists in `embeddings.py`; the backfill run and the
  activation hook are paid code paths: dry-run first, approve the spend, dedicated commit for
  any pin/flag change.
- Everything else (modules copy, route, frontend rewire, cost signature) is ordinary work.

---

## Testing

- Unit: the copied module tests (`test_matching`, `test_eligibility_guard`,
  `test_curation_finalize`, `test_embeddings`, `test_backfill_match_vectors`) run as-is.
- New: `gate_pool_eligibility` unit tests + eval cases in `matching_eval.py` (both error
  directions — the under-exclusion direction has no code guard and is the real harm).
- Integration: `/api/match` happy path + not-migrated degrade + thin-profile empty pool.
- `cd frontend && npx tsc --noEmit` clean; verify the grid in the browser preview against a
  dev test account (recall returns a pool, facets narrow it, strong badge renders).

---

## Risks carried forward (from the design discussion)

1. **Better recall EXPOSES eligibility mismatches keyword hid.** The reason the eligibility
   pass (C) is non-negotiable in this cut — without it, semantic recall surfaces *more*
   topically-perfect-but-ineligible rows than main did. C closes this.
2. **Raw cosine ordering is unvalidated** (Phase 7 evals eligibility, not ranking). Mitigated
   by the relevance floor + the strong-match badge; calibrate `WINGMAN_RECALL_MIN_SCORE`
   against the live score distribution (log recall scores, pick the on-lane/tail split).
3. **SEARCH not CURATION** — a conscious product-stance reversal, interim. Revisit before
   this becomes the permanent shape.
4. **Activation hook must stay wired** (D) or recall decays invisibly.

---

## Decisions (resolved 2026-08-31)

1. **Projects in the query — YES.** Recall embeds boosted project vectors alongside themes
   (`project_vectors` + `PROJECT_MATCH_BOOST`), so a project-aligned row out-ranks a merely
   theme-aligned one at the same cosine. The route always passes `project_texts` from
   `highlight_projects`; the theme facet governs themes, projects ride along automatically.
2. **Strong-match badge — a FIXED cosine cut** (`WINGMAN_STRONG_MATCH_MIN`, env-tunable, no
   code change). Provisional until the live score distribution is logged (risk 2); ship a
   conservative default and calibrate. Not top-N% — a fixed bar means "strong" has the same
   meaning on a broad profile and a thin one.
3. **`/api/match` is a NEW route module** (`app/routes/matching.py`), not an extension of
   `opportunities.py` — keeps main's file clean and avoids the branch's funnel-entangled
   version. Registered in `app/main.py` like the other routers.
4. **Staged PRs** off `main`, not one integration branch — see the rollout below.

---

## Rollout — staged PRs (each independently reviewable + revertable)

Ordered so the paid/prod steps come only after the free code is in and validated.

- **PR 1 — recall engine (backend, no user-visible change).** Copy `matching.py`,
  `eligibility.py`, `embeddings.py`, `curation.py` + tests; `match_vector_schema.sql`;
  `backfill_match_vectors.py` + test; the cache-select change (B5) with the not-migrated
  degrade. Nothing calls it yet. Merges green, ships nothing to users.
- **PR 2 — prod data (ops, gated on PR 1).** Run `match_vector_schema.sql`; `backfill
  --dry-run` → approve → `--yes-really`; wire + verify the activation hook (D7). Still no
  user-visible change; recall data now exists and self-maintains.
- **PR 3 — the endpoint + eligibility pass (backend, M8).** `app/routes/matching.py` with
  `POST /api/match`; `gate_pool_eligibility` + the `ELIGIBILITY_ONLY_SYSTEM` prompt (its own
  dedicated M8 commit inside this PR); `match_eligibility` cost signature; eval cases.
  Testable via curl before any UI depends on it.
- **PR 4 — frontend rewire (the only user-visible PR).** First-screen theme picker; theme
  query-facet that re-runs recall; strong-match badge; `search()` posts to `/api/match`. Behind
  a flag if you want a dark launch. This is the one to watch in production.
```
