# Merge-risk audit — wingman repo, 2026-09-02

Read-only audit (git 2.49, `merge-tree --write-tree`). **`main` is at `2718301`** (one commit past the `aa766a2` named in the brief: "Golden set: extend to 50 profiles"). 316 commits reachable from all refs. The main worktree is clean.

## Headline

- **34 local branches besides main. 29 are pure ancestors of main (zero unique commits). Only 5 carry anything unique, and 3 of those are one lineage** (`claude/opportunity-matching-improvement-fb6134` is a strict ancestor of `wingman/search-feature`, which shares 54 of its 57 commits with `opportunity-matching`).
- **Exactly one branch is safe to merge: `local-discovery-engine`** (clean, net +2 new files; its M8/M9 hunks are already on main).
- **`opportunity-matching` must not be `git merge`d.** It is a competing Fresh Finds implementation (progressive funnel + LLM curation) against main's recall grid, which `../plans/RECALL_GRID_MERGE_PLAN.md` records as a deliberate interim product decision ("SEARCH not CURATION — a conscious product-stance reversal, interim"). Git reports only 6 textual conflicts, but the *clean* part of the merge would (1) delete `inferSubjects`, `VALID_SUBJECTS`, the `filterValues` slot and `getProfileFilterValues`, which main's `finder.tsx` (lines 25, 686-718) and `profileDerived.ts` still use, and (2) land four unapproved M8/M9 prompt/cost changes with no conflict marker to stop anyone. That is the M1 failure shape.
- **No evidence the M1-style silent reversal has recurred on main**: none of main's 7 merge commits hand-resolved a hunk in an M8/M9 file, and no `MARQUEE` sentinel line was removed by any merge.
- The `ab7ab03` / `05f88d6` tips are both main commits (M1 headless-browser fallback and M9 contact-email-to-Gemini, both 2026-08-28). Every branch parked there is fully merged.
- Both stashes are superseded by committed work on main. One untracked one-off script (`cleanup_subject_tags.py`, 139 lines) exists only in the fb6134 worktree and would be lost by `worktree remove --force`.

## (a) Master table

Ahead/behind are vs `main`. "Files" = `git diff --name-only main...B` (vs merge-base). Conflicts = `merge-tree --write-tree main B`. M8/M9 = touches a prompt/paid-call file from the governed list.

| Branch | Tip | Ahead | Behind | Last commit | Merged? | Files | Conflicts with main | M8/M9? | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| P5-P7-deadline-and-task-tracker | 6a7e186 | 0 | 152 | 2026-08-26 | yes (ancestor) | 0 | none | – | delete |
| add-unit-test-suite | 8ea0c08 | 0 | 195 | 2026-08-23 | yes | 0 | none | – | delete |
| admin-console-redesign | d12edee | 0 | 134 | 2026-08-27 | yes (merge 6408993) | 0 | none | – | delete |
| angle-query-strategy | 9826ee9 | 0 | 65 | 2026-08-28 | yes (merge 5d5db17) | 0 | none | – | delete |
| claude/admin-console-layout-46b4ef | ab7ab03 | 0 | 50 | 2026-08-28 | yes (tip is a main commit) | 0 | none | – | delete |
| claude/admin-console-layout-e8be72 | a7e1213 | 0 | 37 | 2026-08-30 | yes | 0 | none | – | delete |
| claude/database-health-check-agent-ca257d | ebea17c | 0 | 28 | 2026-08-30 | yes (tip is main's health-check commit) | 0 | none | – | delete |
| claude/isolated-tree-status-946c2e | 05f88d6 | 0 | 55 | 2026-08-28 | yes (tip is a main commit) | 0 | none | – | delete |
| claude/local-directory-cleanup-1459e6 | ab7ab03 | 0 | 50 | 2026-08-28 | yes | 0 | none | – | delete |
| claude/opportunity-matching-0fd2c7 | ab7ab03 | 0 | 50 | 2026-08-28 | yes | 0 | none | – | delete |
| **claude/opportunity-matching-improvement-fb6134** (worktree) | af32736 | 47 | 50 | 2026-08-30 | **no** | 43 | 12 files: ../plans/HANDOFF.md, app/core.py, app/routes/opportunities.py, curation.py, embeddings.py, matching.py, finder.tsx, ApiClient.ts, httpClient.ts, matching_eval.py, test_matching.py, test_matching_eval.py | **yes** (6 files) | **delete** — strict ancestor of both `opportunity-matching` and `wingman/search-feature`, 0 commits of its own. Rescue the untracked script first, then remove the worktree |
| claude/opportunity-matching-plan-review-0336da | ab7ab03 | 0 | 50 | 2026-08-28 | yes | 0 | none | – | delete |
| claude/opportunity-scraper-logic-04272c | ab7ab03 | 0 | 50 | 2026-08-28 | yes | 0 | none | – | delete |
| claude/opportunity-scraper-logic-5b0f58 | ab7ab03 | 0 | 50 | 2026-08-28 | yes | 0 | none | – | delete |
| claude/opportunity-scraper-logic-a509f1 | c39a5e2 | 0 | 33 | 2026-08-30 | yes (tip is main's reconcile merge) | 0 | none | – | delete |
| claude/pm-self-assessment-c39876 | ab7ab03 | 0 | 50 | 2026-08-28 | yes | 0 | none | – | delete |
| claude/rebuild-opportunity-matching-0c1504 | 05f88d6 | 0 | 55 | 2026-08-28 | yes | 0 | none | – | delete |
| claude/reject-broken-it-intern-3a0ad8 | ab7ab03 | 0 | 50 | 2026-08-28 | yes | 0 | none | – | delete |
| deadline-email-alerts | 6f9ab2f | 0 | 151 | 2026-08-26 | yes | 0 | none | – | delete |
| hub-social-audience-fix | 9b78d74 | 0 | 124 | 2026-08-27 | yes (merge c54fc41) | 0 | none | – | delete |
| **local-discovery-engine** | 8648d15 | 5 (2 real; 3 already cherry-picked to main) | 5 | 2026-09-02 | **no** | 21 (net effect of merge: **+2 files**) | **none** | touches scrape_opportunities.py + mine_hub_pages.py, **but both hunks are already on main**; adds `local_org_discovery.py`, a new Gemini-calling script | **MERGE** (first; dedicated commit naming the new paid script) |
| **opportunity-matching** | f3c5cd7 | 58 | 50 | 2026-08-31 | **no** | 45 | 6 files: ../plans/HANDOFF.md, app/core.py, app/routes/opportunities.py, finder.tsx, ApiClient.ts, httpClient.ts | **yes** — scrape_opportunities.py, refresh_opportunities.py, app/services/ai.py, app/services/resume.py (prompt + max_tokens); matching/curation/gemini_common are byte-identical to main | **DO NOT MERGE.** Tag as archive; extract per decision in dedicated MARQUEE commits (section g) |
| phase-2-auth | bf6e4bf | 0 | 199 | 2026-08-23 | yes | 0 | none | – | delete |
| phase-3-expo-frontend | 24ceacd | 0 | 197 | 2026-08-23 | yes | 0 | none | – | delete |
| phase-3-parity-2 | e5c506a | 0 | 193 | 2026-08-23 | yes | 0 | none | – | delete |
| phase-3-ui | bb0de67 | 0 | 196 | 2026-08-23 | yes | 0 | none | – | delete |
| phase-3-ui-parity | 1d8a1e8 | 0 | 194 | 2026-08-23 | yes | 0 | none | – | delete |
| **rearchitecture** | 0f2e58b | 2 | 203 | 2026-08-23 | **no** — but `git cherry` shows both commits are patch-equivalent to main's dcca585 + 217f571 | 32 | **24 files, all add/add** (entire app/ + ops/ tree, CLAUDE.md, render.yaml, requirements.txt) | app/services/ai.py (stale original copy) | **DELETE, never merge** (tag `archive/rearchitecture` if you want a marker) |
| recall-grid-matching | abe854a | 0 | 15 | 2026-09-01 | yes (merge 77c5ce3) | 0 | none | – | delete |
| scraper-v2 | 4bad443 | 0 | 141 | 2026-08-26 | yes | 0 | none | – | delete |
| sitemap-discovery-g6a | e643a0e | 0 | 140 | 2026-08-26 | yes (merge 14e0b81) | 0 | none | – | delete |
| ui-polish-and-search-perf | 4473d52 | 0 | 219 | 2026-08-20 | yes | 0 | none | – | delete |
| **wingman/search-feature** | a3c2135 | 57 | 50 | 2026-08-31 | **no** | 48 | 11 files (the 6 above + curation.py, matching.py, matching_eval.py, test_matching.py, test_matching_eval.py — its copies are OLDER than main's) | **yes** (same 6 as opportunity-matching, older) | **delete after tag** — its 3 own commits are on main byte-identical as d277aa3; everything else is in opportunity-matching |
| worktree-admin-dashboard-improvements (worktree) | 2342060 | 0 | 13 | 2026-09-01 | yes (tip IS a main commit) | 0 | none | – | delete + remove worktree |

## (b) Per-branch summaries (branches with unique commits)

### `local-discovery-engine` (5 ahead / 5 behind, merge-base 82f1c9f)
Two genuinely new commits: `beac206` "Local-org discovery feeder: prototype + signal expansion + stage 2 (leads)" and `a72267c` "Feeder: net-new vs already-in-catalog comparison instrumentation". Together they add `local_org_discovery.py` (421 lines: natural-language region → archetype × Gemini search → hub leads; imports `url_dedupe`, `url_validate`, `supabase_common`, `agent_common`, all present on main) and 126 lines to `DISCOVERY_ENGINE_PLAN.md`. The other three commits (`ea8f7fa`, `aa1bb96`, `8648d15`) were already cherry-picked onto main as `06fc20e`, `dea4c8b`, `2718301` — `git cherry` confirms patch-equivalence. The branch's only touches on governed files are the two `include_weak=False` call-site lines in `scrape_opportunities.py` / `mine_hub_pages.py`, and both are already on main, so the merge leaves those files at main's version. **M9 note:** `local_org_discovery.py` calls `call_gemini` (with a `max_searches` path) — it is a new paid code path, not wired into the console or any agent. The merge commit should say so.

### `opportunity-matching` (58 ahead / 50 behind, merge-base ab7ab03)
The full "progressive funnel + curation" matching build (Phases 0–7, 2026-08-28 → 08-31): server-side recall + eligibility guard, `/api/match` orchestration with a funnel mode (`funnel.py`, `match_pipeline.py`), behavioral vibe questions, cost/time/interest as pre-recall filters, a curation shortlist with "why it fits", a swipeable setup card stack, retirement of the 17-bucket subject vocabulary everywhere (Phase 6), profile-prompt alignment for grade/GPA/location, and `user_events` plumbing. **Main already absorbed the backend half by file copy**: `30d239d` (Recall PR1) took `matching.py`, `curation.py`, `eligibility.py`, `embeddings.py`, `gemini_common.py`, `backfill_match_vectors.py`, `../../db/match_vector_schema.sql`, `matching_eval.py` and their tests at this branch's tip — they are byte-identical to main today, including the M8 curation-prompt commits `ccb2efd`/`f3c5cd7`. Also already on main in identical form: the contact-email-to-Gemini M9 change (`05f88d6`), the activation-to-refresh-queue console work, `user_events`, Phase 0 findings. What remains unique is the funnel/curation **product surface** (`finder.tsx`, `ShortlistView.tsx`, `funnel.py`, `match_pipeline.py`, the funnel-mode route in `app/routes/opportunities.py` / `app/core.py`), the Phase 6 deletions, and the four unapproved prompt/cost edits listed in section (f). `../plans/RECALL_GRID_MERGE_PLAN.md` on main explicitly listed `ai.py`, `resume.py`, `finder.tsx`, `match_pipeline.py`, `funnel.py` as "Leave behind". 25 of its 58 commits are tagged MARQUEE M8/M9.

### `wingman/search-feature` (57 ahead / 50 behind)
Forked from `opportunity-matching` at `9291ea6`. Its 3 own commits — the Quest Log catalog-name search drawer with multi-select add (`b3d4e71`, `52efe48`) and the My Vibe Gender-box removal (`a3c2135`) — **are already on main as `d277aa3`**: `tracker.tsx`, `trackerAdd.ts`, `tracker.ts`, `profile.tsx` are byte-identical between the branch tip and main. It lacks the 4 latest `opportunity-matching` commits (recall floor, curation shortlist/eligibility prompts, EVAL_PLAN), which is why its `curation.py`/`matching.py` copies conflict with main where `opportunity-matching`'s do not. Nothing to merge.

### `claude/opportunity-matching-improvement-fb6134` (47 ahead / 50 behind, checked out in a worktree)
Tip `af32736` is the merge of origin/opportunity-matching into itself; it is a strict ancestor of both branches above (0 commits of its own relative to either). Zero unique content in git. Its worktree holds one untracked file worth keeping: `cleanup_subject_tags.py` (139 lines, 2026-08-29) — a `--dry-run`/`--yes-really` one-off that scrubs the retired 17-bucket words out of `opportunities.subject_tags`, case-insensitively. It exists in no commit on any branch.

### `rearchitecture` (2 ahead / 203 behind)
`d5612c4` "Phase 1: decompose server.py into FastAPI app/ + local-only ops/" and `0f2e58b` "Shim: never enable ops on Render". `git cherry` reports both as patch-equivalent to main's `dcca585` and `217f571`. It is the original Phase 1 branch, re-landed on main under new hashes on 2026-08-23. A merge would raise 24 add/add conflicts across the whole `app/` and `ops/` tree — it is a stale duplicate, not divergent work.

## (c) Pairwise overlap and cross-branch conflicts

| Pair | Overlapping files | merge-tree between them |
|---|---|---|
| fb6134 × opportunity-matching | 43 (everything) | clean — ancestor |
| fb6134 × search-feature | 43 (everything) | clean — ancestor |
| opportunity-matching × search-feature | 44 | **clean** (diverge 4 vs 3 commits; the 3 are on main) |
| opportunity-matching × local-discovery-engine | 2: `app/services/resume.py`, `scrape_opportunities.py` | conflicts in the same 6 files as vs main — because LDE contains main's recall-grid commits; not LDE-specific |
| search-feature × local-discovery-engine | 2 (same) | 11 conflicts, same reason plus its older curation/matching copies |
| fb6134 × local-discovery-engine | 1: `scrape_opportunities.py` | (below the 2-file threshold) |
| rearchitecture × each matching branch | 6–7 (`app/config.py`, `app/core.py`, `app/routes/opportunities.py`, `app/services/ai.py`, `app/services/opportunities.py`, `app/services/resume.py`, `requirements.txt`) | 24 add/add conflicts — the whole app/ tree |
| rearchitecture × local-discovery-engine | 3: `app/services/resume.py`, `ops/admin_console.html`, `ops/core.py` | 24 add/add conflicts |

Once the matching lineage is collapsed to one branch and `rearchitecture` is deleted, the only real cross-branch overlap is `opportunity-matching` × `local-discovery-engine` on two files, and LDE's side of both is already on main — so there is no live cross-branch conflict.

## (d) Hotspot files (touched by 3+ distinct unmerged branches)

Raw count across the 5 unmerged branches: 44 files hit 3+, but 3 of the 5 branches are one lineage, so most of these are the same change counted three times. The honest hotspots, i.e. files touched by **more than one lineage**:

- `scrape_opportunities.py` — matching lineage (subject_tags prompt, M8) + local-discovery-engine (`include_weak=False`, already on main). 4 branches.
- `app/services/resume.py` — matching lineage (resume-import prompt + max_tokens, M8/M9) + LDE + rearchitecture. 4 branches.
- `app/config.py`, `app/core.py`, `app/routes/opportunities.py`, `app/services/ai.py`, `app/services/opportunities.py`, `requirements.txt` — matching lineage + rearchitecture (stale add/add). 4 branches each.
- `ops/admin_console.html`, `ops/core.py` — LDE (already on main) + rearchitecture.

Every other 3-count file (`app/services/matching.py`, `curation.py`, `embeddings.py`, `finder.tsx`, `httpClient.ts`, the `tests/unit/test_*matching*` set, `../plans/HANDOFF.md`, ...) is the matching lineage alone.

## (e) Worktrees and stashes

### Worktrees
| Path | Branch | State |
|---|---|---|
| `.claude/worktrees/admin-dashboard-improvements` | `worktree-admin-dashboard-improvements` @ 2342060 | **clean**; tip is main's own commit "MARQUEE M9: embed recall match_vector at activation time". Safe to `worktree remove` and `branch -d`. |
| `.claude/worktrees/opportunity-matching-improvement-fb6134` | `claude/opportunity-matching-improvement-fb6134` @ af32736 | 0 modified, **3 untracked**: `cleanup_subject_tags.py` (real, unsaved, 139 lines — see section b), `frontend/.expo/`, `frontend/node_modules/` (noise). `git worktree remove` will refuse because of the untracked script; `--force` would delete it. **Copy it out first.** |

### Stashes
**`stash@{0}` "On opportunity-matching: search-feature WIP (safety)"** — base `edda804` (not on main). Touches `finder.tsx` (-95), `tracker.tsx` (293 lines), `tracker.ts` (+11), plus untracked `frontend/src/api/trackerAdd.ts`. This is the earlier *dropdown* design of the Quest Log catalog search; the committed *drawer* design (`b3d4e71`/`52efe48`, on main as `d277aa3`) superseded it. `tracker.ts` and `trackerAdd.ts` in the stash are byte-identical to main; `tracker.tsx` is the abandoned draft (185+/121- vs what shipped). All three tracked files have since changed on main (finder.tsx by 405+/632-). Applying it anywhere would conflict and would resurrect the discarded design. **Drop.**

**`stash@{1}` "On scraper-v2: wip: refind_dead_links, phase4 test, opp-matching plan, deadline plan gap-hunt notes"** — base `2e6f1a0` (on main, 2026-08-26). Touches `../plans/DEADLINE_AND_TASK_PLAN.md` (+326), `refind_dead_links.py` (+59), `tests/unit/test_phase4_selection.py` (+48), plus untracked `../plans/OPPORTUNITY_MATCHING_PLAN.md`. All of it landed on main and then evolved: `2da8422`/`f2d0bbc` (refind gate — main is a superset: it has `is_content_mill(sib) or _is_editorial_url(sib)` and one extra test), `83effca`, `5dfa04e`; main's plan docs are 149-223 lines longer. The only residue is 2 lines of `refind_dead_links.py` that main rewrote. **Drop.**

## (f) Silent-clobber candidates (both sides changed since merge-base, no textual conflict)

Method: for every file changed on both `main` and the branch since their merge-base, I diffed `main` against the `merge-tree --write-tree` result to see exactly what a merge would do to main's copy.

**Literal resurrection of old code: none found.** On every governed file the 3-way merge preserves main's later additions — e.g. `refresh_opportunities.py` keeps main's `0e9d82c` re-embed block (MARQUEE M9) and only adds the branch's subject_tags hunks; `scrape_opportunities.py` keeps main's `5647568` `running` field (MARQUEE M8) and the dedupe-vector plumbing; `gemini_common.py`, `matching.py`, `curation.py`, `embeddings.py`, `eligibility.py`, `backfill_match_vectors.py`, `../../db/match_vector_schema.sql`, `matching_eval.py`, `app/config.py`, `app/services/opportunities.py` come out identical to main. `local-discovery-engine`'s merge changes nothing on main except adding two new files.

**Semantic clobber from `opportunity-matching` (and `wingman/search-feature`, which carries the same commits): real, and it is the M1 shape.** These hunks apply with **no conflict marker**:

| File on main | What the "clean" merge does | Why it matters |
|---|---|---|
| `frontend/src/lib/ranking.ts` | Deletes `inferSubjects()`; drops the `subjectHints` parameter from `preFilter` (6 args → 5); removes the +3 subject-tag nudge | main's `finder.tsx` **line 25 imports `inferSubjects`** and **line 718 calls `preFilter` with 6 args** (recall-grid path; `ff27cc4` MARQUEE M8 and `24f4e70` both touched this file on main). `finder.tsx` itself is a conflict file, so whoever resolves it toward main's version gets a tree that does not type-check; toward the branch's version loses main's recall grid. |
| `frontend/src/lib/constants.ts`, `profileDerived.ts`, `profile.ts` | Deletes `VALID_SUBJECTS`, the `filterValues` slot, `FilterValuesSlot`, `getProfileFilterValues` | main's `profileDerived.ts` lines 4/115/118/240 and `finder.tsx` 686-690 (`fv.subjects`) still use them; `frontend/scripts/verify.ts` on main calls `inferSubjects`. |
| `app/services/ai.py` | Deletes `VALID_SUBJECTS`, `mock_infer_subjects`, and its `generate_mock_text` branch | Mock mode for the subject-inference prompt silently falls through to another mock. `tests/unit/test_mock_ai.py` is taken from the branch, so the suite would not notice. |
| `scrape_opportunities.py` (**M8**) | Rewrites the `subject_tags` line of `EXTRACT_SYSTEM` (free-form tags, no fixed list) and drops the `{subjects}` format arg | A prompt change riding in untagged (branch commit `d07d978` has no MARQUEE label). `../plans/RECALL_GRID_MERGE_PLAN.md` deliberately left this behind. |
| `refresh_opportunities.py` (**M8**) | Same prompt rewrite; `clean_update_dict` stops filtering tags to the 17 words and caps at `MAX_SUBJECT_TAGS = 8` | Changes what the paid refresher writes to every live row it touches. Same untagged commit. |
| `app/services/resume.py` (**M8 + M9**) | Replaces the resume/LinkedIn import prompt with the "capture grade/GPA/location/personality" version and raises `max_tokens` 500 → 4000 | Prompt change and an 8x output ceiling on a paid Claude call (branch commit `2fce24b`, labelled "M8" but not approved on main). Also changes the `resume_import` signature (`test_classify_feature.py` needle changes), so `_FEATURE_SIGNATURES` in `app/core.py` must move with it or resume spend lands in `other`. |
| `frontend/src/api/types.ts` | +76 lines of funnel types | Additive, harmless. |

`rearchitecture`: every one of its 32 files is "both changed", but all 24 conflicts are add/add against the whole `app/` + `ops/` tree — it cannot be merged at all, so there is no silent path.

## (g) Recommended order and disposition

**Step 0 — rescue, then housekeeping (no merges, no risk).**
1. Copy `.claude/worktrees/opportunity-matching-improvement-fb6134/cleanup_subject_tags.py` somewhere tracked (it belongs with a future Phase 6 data scrub; it is the DB half of the subject_tags retirement).
2. `git worktree remove` both worktrees (fb6134 after step 1; admin-dashboard-improvements is clean).
3. `git branch -d` the 29 ancestor branches (git will accept `-d` for every one — all are `--merged`). Optional: `git tag archive/rearchitecture 0f2e58b` then `git branch -D rearchitecture`; it is a stale duplicate of Phase 1 and must never be merged.
4. `git stash drop` both stashes (both superseded; section e).

**Step 1 — merge `local-discovery-engine`.** Clean; net effect `+DISCOVERY_ENGINE_PLAN.md`, `+local_org_discovery.py`. Use a dedicated merge commit whose message states it adds a new Gemini-calling script (`local_org_discovery.py`, unwired, `max_searches`-capped) so the M9 disclosure is in history. Then delete the branch. The 3 cherry-picked-to-main commits mean the eval/ and dedupe files auto-resolve to main's versions.

**Step 2 — retire `wingman/search-feature` and `fb6134`.** `git tag archive/search-feature a3c2135`, then `branch -D` both. Nothing in them is absent from main plus `opportunity-matching`.

**Step 3 — `opportunity-matching`: do not merge; extract by decision.** Tag it `archive/opportunity-matching-funnel` (or keep the branch) and, only with Shama's explicit yes per item, cherry-pick into dedicated MARQUEE commits on main:
- (M8) free-form `subject_tags` prompts in `scrape_opportunities.py` + `refresh_opportunities.py` (`d07d978`), paired with running `cleanup_subject_tags.py` so data and prompt move together.
- (M8+M9) resume-import prompt + `max_tokens` 4000 (`2fce24b`, `app/services/resume.py`) **together with** the `_FEATURE_SIGNATURES` / `test_classify_feature.py` needle change in the same commit.
- (frontend) Phase 6 retirement of `inferSubjects` / `filterValues` / `VALID_SUBJECTS` — only after main's recall-grid `finder.tsx` (lines 25, 686-718), `profileDerived.ts` and `scripts/verify.ts` stop depending on them; otherwise `tsc --noEmit` breaks.
- (product) the funnel + curation shortlist (`funnel.py`, `match_pipeline.py`, `ShortlistView.tsx`, funnel-mode `/api/match`, the finder UI). This is the "SEARCH not CURATION — interim, revisit" decision in `../plans/RECALL_GRID_MERGE_PLAN.md`; it needs a product decision and a rebuild on top of the recall grid, not a merge. Its M8 curation-prompt commits (`ccb2efd`, `f3c5cd7`) are already on main inside `curation.py`.

**Hygiene note for main itself** (not a branch risk, but the owner asked about silent marquee changes): five non-merge commits on main since 2026-08-28 touched governed files with no MARQUEE/M8/M9 tag in the subject: `30d239d` (adds the `CURATION_SYSTEM` prompt + the Gemini embedding call in `gemini_common.py` — the plan doc records that Shama waived M8 pre-signoff on 08-31 "review tomorrow"; confirm that review happened), `2c6a8ae` (`classify_page.py` + `embed_common.py` — a classifier prompt and embedding calls), `67e3596` (`--gate-observe` scraper flag), `06fc20e` and `e5e3a05` (dedupe plumbing in `scrape_opportunities.py`/`mine_hub_pages.py`, no prompt text). None of the 7 merge commits on main carries a hand-resolved hunk in a governed file, and no `MARQUEE` sentinel line was removed by any of them.
