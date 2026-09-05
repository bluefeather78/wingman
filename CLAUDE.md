# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Marquee decisions — STOP before changing these

[MARQUEE_DECISIONS.md](MARQUEE_DECISIONS.md) lists load-bearing decisions that must **never** be
changed, reverted, or quietly softened without an explicit, extensive discussion with Shama in chat
and a clear "yes." This overrides any default, inference, or "plausible improvement."

- **Before editing code or config a marquee entry protects, STOP** — name the entry, say what you'd
  change and why, and get an explicit yes first. Never proceed on a default.
- Protected sites carry a `# MARQUEE M<n>:` sentinel comment. Seeing one is the trigger to stop and
  read MARQUEE_DECISIONS.md.
- A marquee change is **always its own dedicated commit** that names the entry — never bundled into an
  unrelated feature commit. (A marquee decision was once reversed silently inside an unrelated commit;
  that is the failure this rule prevents — see M1 in that file.)
- Only Shama decides what is on the list. Claude may *propose* entries but may not treat a proposal as
  ratified until Shama confirms.

Two entries are broad enough that they touch most substantive work, so know them without opening the
file: **M8 — any prompt sent to a model** (changing/adding/removing prompt text) and **M9 — any code
path that makes a paid API call** (toggling `use_web_search`/`max_searches`/`max_uses`, model pins,
per-row model calls, provider swaps) are both marquee. Approval first, dedicated commit. The M1
reversal was both at once: a paid call turned off and its prompt rewritten to hide it.

**Prompt-writing convention (operator directive, 2026-08-28): always use concrete examples in
prompts.** Models follow do/don't *examples* far better than adjectives — "broad", "distinct",
"high quality" alone don't steer behaviour; a good/bad example pair does. When writing or editing any
prompt, define the key terms with examples (see `DISCOVERY_SYSTEM` in `agents/scrape_opportunities.py`: it
defines "opportunity" structurally with counter-examples and "broad vs named search" with good/bad
query pairs). This is measured house style here (it fixed the 73% named-query rate), not a preference.

## Project

"Highschool Wingman" — an app that helps high schoolers find and track extracurricular
opportunities (summer programs, internships, research competitions, academic competitions,
conferences, journals). The frontend is an **Expo (React Native + RN-web) app in
`frontend/`** targeting web + iOS + Android from one codebase; the backend is a FastAPI
service in `app/`.

**The old vanilla-JS SPA (index.html / script.js / public/styles.css / public/walkthrough.html) was
retired 2026-08-23 at git tag `workingwithauth`** — check out that tag to see it. Every
`script.js`/`index.html` reference in this file is historical: the *behavioral rationale*
(profile-chat caching, tracker classification, mock mode, etc.) was ported verbatim into
`frontend/src/lib/*` and still applies; the DOM-glue descriptions do not. `public/styles.css` and
`public/favicon.svg` survive at the repo root only because terms/privacy/about pages use them.

## Running the app

Backend (API + the static terms/privacy/about pages, on `http://localhost:8000`):

```
python server.py
```

or, equivalently, `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

Frontend (RN-web dev server on `http://localhost:8081`, from `frontend/`):

```
EXPO_PUBLIC_API_BASE=http://127.0.0.1:8000 npx expo start --web --port 8081
```

**Never start Metro with `CI=1`** — CI mode disables the file watcher, so edits silently
never rebundle. `cd frontend && npx tsc --noEmit` must stay clean. Web deploys via
`expo export -p web` → `frontend/dist` (Render Static Site, see `render.yaml`); native
ships via EAS. The API's root path returns a JSON status (or redirects when the
`WEB_APP_URL` env var is set to the web origin).

**As of the Phase 1 rearchitecture (2026-08-23, `docs/archive/PLAN_1_decompose.md`), the web layer is a
FastAPI app under `app/`, not the old `http.server` monolith.** `server.py` is now a thin
shim that boots uvicorn (`app.main:app`) with the local ops console enabled — so
`python server.py` still works and still serves `/admin`. See the **Web layer** section
below for the module map. `requirements.txt` now installs `fastapi` + `uvicorn`; the offline
agents at the repo root remain stdlib-only.

- `.env` (gitignored) holds `GEMINI_API_KEY` and `ANTHROPIC_API_KEY`. If either is unset,
  that endpoint (`/api/messages` or `/api/messages-claude` respectively) runs in **MOCK
  mode**, fabricating plausible pattern-matched responses (see `generate_mock_text` in
  [app/services/ai.py](app/services/ai.py)) so the app is fully click-through-able offline.
- Never pass an API key inline on the command line (e.g.
  `GEMINI_API_KEY=... python server.py`) — it gets recorded in shell history / Claude Code's
  local settings allowlist and has leaked before. Always put it in `.env`.

## Web layer (`app/` ships, `ops/` is local-only)

Phase 1 sliced the former 6,956-line `server.py` into two packages. **The old `server.py`
line-number references elsewhere in this file are historical** — the code moved but did not
change (it was extracted verbatim), so the rationale still applies; look for the same
function name in its new home.

- **`app/`** — the FastAPI web service, the only thing deployed to Render
  (`render.yaml`, start command `uvicorn app.main:app`). `app/config.py` (env + constants),
  `app/core.py` (the shared seam: Supabase plumbing, account CRUD, cost/activity accounting,
  `subscription_state`), `app/services/*.py` (opportunities, deadlines, ai mocks,
  mailing_list, google_oauth, resume), `app/routes/*.py` (one router per domain),
  `app/main.py` (the app; a deny-listed static route serves ONLY the surviving root-level
  static pages — terms/privacy/about + public/styles.css/favicon.svg — the SPA itself is gone).
- **`ops/`** — the local-only operations console: `ops/core.py` (agent orchestration,
  metrics, user-costs, seeds, snapshots, review-queue moderation — everything that backed
  `/api/agents/*` and `/api/seeds`) and `ops/admin.py` (the router, every route
  localhost-gated), plus `ops/admin_console.html` (moved from the repo root). **This is
  mounted only when `WINGMAN_ENABLE_OPS` is set** (the `server.py` shim sets it; Render does
  not), so the shipped service exposes no agent/seed/admin route — they 404 there.
- **Shared offline layer stays at the repo root.** The 6 agents and their libs
  (`gemini_common`, `claude_common`, `agent_common`, `supabase_common`,
  `subscription_common`, `mailing_list_common`, `url_dedupe`, `dryrun_common`,
  `check_deadlines`, ...) were NOT moved: `app/` imports several of them (the deadline
  endpoint reuses `check_deadlines.check_one`), the agents import each other by bare name,
  and their `__file__`-anchored log/snapshot I/O assumes the repo root. Moving them would
  break both, and the plan forbids changing agent internals — so they remain the shared layer
  both `app/` and `ops/` import. `ops/core.py` anchors agent paths via an explicit
  `REPO_ROOT` (it sits one level down from where `server.py` used to).

## Architecture

### Repo map — where to look first

Read this before grepping; it is the whole layout in one screen.

```
frontend/                 THE web+native app (Expo, expo-router). See "Frontend" below.
  app/                    routes: landing, login, google-auth, +html.tsx, (app)/{index,
                          finder,tracker,profile,subscription}.tsx + (app)/_layout.tsx
  src/api/                ApiClient seam: httpClient (bearer+refresh), tokenStore,
                          trackerStore, hash, types
  src/lib/                ported pure logic: ranking, profile, profileChat, profileHighlight,
                          tracker, status, kinds, grade, constants, extractJSON, aiJson
  src/ui/                 design system: theme, components, NavBar, icons
  src/auth/               AuthContext, googleSignIn
app/                      FastAPI service (the only Python thing deployed)
  main.py                 app + CORS + static/dist serving       routes/*.py  one per domain
  core.py                 Supabase plumbing, accounts, cost/activity, subscription_state
  config.py               env + model pins                       services/*.py  domain logic
  auth/                   JWT tokens, argon2 passwords, deps, ratelimit
ops/                      LOCAL-ONLY console (WINGMAN_ENABLE_OPS): core.py, admin.py,
                          admin_console.html — never mounted on Render
wingman/                  THE SHARED LAYER — 33 modules app/, ops/, agents/, tests/ and the
                          one-off scripts all import: gemini_common, claude_common,
                          supabase_common, url_validate, url_dedupe, page_text,
                          agent_common, embed_common, dedupe_*, queue_flags, seeds_common …
                          First-party, NOT in requirements.txt: it resolves because the repo
                          root is the cwd of every entry point, exactly as `app` does.
                          Exports REPO_ROOT — the one definition of "the repo root".
agents/                   the 21 offline catalog agents + maintenance tools. ops/core.py runs
                          them as `python -m agents.<name>` with cwd=REPO_ROOT. The `-m` is
                          load-bearing: `python agents/x.py` would put agents/ on sys.path[0]
                          instead of the repo root and every `from wingman import …` fails.
                          SIX OF THE SEVEN CATALOG AGENTS COST REAL MONEY PER RUN.
server.py                 the only .py at the repo root — the `python server.py` dev shim.
scripts/                  leaf scripts nothing imports: one-off/ (9 migrations+backfills
                          already run) · dev/ · backfill/. Each carries a ROOT sys.path
                          shim because `python scripts/x/y.py` puts x/ on the path, not
                          the repo root.
eval/                     grading + golden-set harness (matching_eval, dedupe_eval,
                          grade_scraper_batch, build_fixture, the golden CSVs, evals_hub).
db/                       one-time manual DDL, run by hand in the Supabase SQL editor.
                          Never opened by code; the filenames appear in ~130 setup and
                          503 messages, so keep the basenames stable.
docs/                     plans/ (unbuilt or part-shipped) · archive/ (shipped, superseded,
                          dated snapshots) · review-2026-09-02/ (the production audit) ·
                          SUBSCRIPTION_SETUP.md + MATCHING_UX_REQUIREMENTS.md (live refs)
data/                     Opportunities.xlsx, the diffable opportunities.json snapshot,
                          the two hand-curated hub registries. Not read at runtime.
legal/*.md                source of record -> agents/build_legal.py -> public/terms.html/privacy.html
tests/                    pytest suite for the backend (945 tests, all green)
public/                   the ONLY directory the static route serves, and the only thing on
                          this host reachable without auth: terms/privacy/about.html,
                          walkthrough.html (the landing film, ~1.5MB vendored bundle),
                          styles.css, favicon.svg. URLs stay unprefixed — /terms.html —
                          so links in already-delivered emails still resolve.
```

**Adding a module: does it go in `wingman/` or `agents/`?** `agents/` is for something an
operator RUNS (it has a `main()`/argparse and an `ops/core.py` entry); `wingman/` is for
something other code IMPORTS. A file that is both — `check_deadlines` and
`generate_action_items` are, since `app/` reuses their functions — lives in `agents/`, and
`app/` imports it from there. Never compute the repo root with `dirname(__file__)`; import
`REPO_ROOT` from `wingman`. That mistake broke 17 sites at once during this move and 16 of
them failed silently.

**`app/main.py`'s static route resolves inside `public/` and nowhere else** (`PUBLIC_DIR`).
Until 2026-09-04 it resolved against the whole repo and defended with deny-lists, so a file
was served unless something remembered to exclude it — and one was not: **`GET
/logic_map.html` returned 200 in production**, publishing the ops console's internal
pipeline map. It now lives in `ops/`, which is never mounted on Render.

Serving is **opt-in: to publish a file, put it in `public/`.** Nothing else on disk is
reachable, whatever its extension — which closes the gap that `_DENY_EXT`, a file-TYPE
list with no `.json`/`.xlsx`/`.docx`, could never close on its own. Those deny-lists are
kept as a second line, not the only one. This is
[PRODUCTION_READINESS_PLAN.md](PRODUCTION_READINESS_PLAN.md) High #5 ("catch-all static
route serves the repo"), closed. `agents/build_legal.py` writes into `public/`.

Retired at tag `workingwithauth`: `index.html`, `script.js`, the old icon SVGs. Every
`script.js` / `index.html` reference below is **historical** — the reasoning was ported
into `frontend/src/lib/*` and still holds; the DOM mechanics described do not exist.

**Frontend: the Expo app in `frontend/`** (see `docs/archive/PLAN_3_rn.md` for the full build log).
Routes in `frontend/app/` (expo-router: landing, login, google-auth, and the authed
`(app)/` group — Home Base / My Vibe / Fresh Finds / Quest Log / subscription); ported
pure logic in `frontend/src/lib/` (ranking, profile, profileChat, tracker, status,
extractJSON, profileHighlight — dependency-injected model access, no DOM); the API seam
in `frontend/src/api/` (`httpClient` implements the Phase-2 bearer/refresh contract,
`trackerStore` reads/writes the same `hs-tracker-data`/`hs-tracker-saved`/`student-profile`
keys the old app used); the "BENTO & POP" design system in `frontend/src/ui/` (`theme.ts`
tokens, `components.tsx`, `NavBar.tsx`, `icons.tsx` — exact SVG ports). Pixel parity with
the retired SPA was verified against tag `workingwithauth` via computed-style diffs; the
design source of truth is public/styles.css (kept for legal pages) plus the Claude Design
"Wingman Design System" project.

**How the two halves are served.** In dev they are two origins (Metro `:8081` -> API
`:8000`, which is why `app/main.py` carries CORS). In production they are ONE: the Render
web service's build runs `expo export -p web` and `SERVE_WEB_DIST=1` makes `app/main.py`
serve `frontend/dist` at the root, so `highschoolwingman.com` is app + API together — no
prod CORS, and the Google OAuth callback host never moves. Resolution order in
`serve_static()` is **dist file -> exported route html -> repo-root page -> dist
index.html fallback**; the repo-root step sits before the fallback deliberately, so
`/terms.html` can never be shadowed by the app shell. `render.yaml` also still defines a
standalone `wingman-web` static site as an alternative path; it is not what the domain
uses today.

**Opportunity data**: the opportunity catalog (1200+ rows) lives in a Supabase (hosted
Postgres) `opportunities` table, not a static file — `/api/opportunities`
proxies to it (PostgREST, anon key, RLS-restricted to `is_active=true` rows, paginated past
PostgREST's 1000-row cap, cached in-process for `OPPORTUNITIES_CACHE_TTL` seconds) and
the client fetches that endpoint once on load.
[opportunities.json](data/opportunities.json) still exists git-tracked as a diffable backup
snapshot only — regenerate it with `agents/export_json.py` after editing the DB, it is **not**
fetched at runtime anymore. It moved to `data/` on 2026-09-04; `agents/export_json.py`'s `OUT_PATH`
writes there, and the static route now blocks the directory (it was downloadable from the
production domain before, since `.json` is not in `_DENY_EXT`). `scripts/one-off/migrate_to_supabase.py` was the one-off script that populated the
table (from this file plus a sibling `opportunity finder/` project's seed data); not part of
the regular dev loop.

## The seven background agents and the admin console

Seven offline Python scripts maintain the catalog. **Six of the seven cost real money per run**
(Gemini or Anthropic, most with web search). **Never run one of those six without fresh
explicit approval in chat** — this rule exists because of an unplanned ~$30 spend, and
building UI for a run is not authorization to trigger it. `agents/check_links.py` is the exception
and is genuinely free; see its own section below for the one thing it *does* need care about.

| Console key | Script | `agent_runs.agent` | Job | Web search |
|---|---|---|---|---|
| `metadata` | `agents/refresh_opportunities.py` | `metadata_refresher` | Core metadata: name, org, summary, eligibility, pricing | no |
| `reviews` | `agents/check_reviews.py` | `review_checker` | Org legitimacy / reputation from independent sources | Gemini |
| `scraper` | `agents/scrape_opportunities.py` | `scraper` | Find NEW opportunities (only agent that INSERTs) | Gemini |
| `deadline` | `agents/check_deadlines.py` | `deadline_checker` | Deadlines + running/not-running status | Claude |
| `mailinglist` | `agents/find_mailing_lists.py` | `mailing_list_finder` | Find each program's mailing-list signup form, store a replayable recipe | no |
| `links` | `agents/check_links.py` | `link_checker` | Verify every catalog URL; repair what moved, **queue the rest for human review** (never deactivates on its own, as of 2026-09-02) | no — **free, plain HTTP** |
| `tasks` | `agents/generate_action_items.py` | `action_item_generator` | The application checklist per program, verified against the program's own page | no — page fetched by us, no search |

Watch out for two things that have caused real bugs here:
- The **scraper's `items_processed` counts SEEDS, not rows** — never sum it with the other
  three agents' row counts. `AGENT_CONFIGS_SCHEMA[key]["unit"]` encodes this.
- `agents/check_reviews.py` is the *review* checker, not the metadata refresher. An earlier console
  card labelled "Refresh Agent" actually executed `agents/check_reviews.py`; the keys in the table
  above are the corrected mapping. Don't rename the `db_agent` literals — the scripts write
  them and there is existing history under each.

**A model-typed URL is not trustworthy anywhere in this repo.** This is the scraper rewrite's
central finding generalised, and as of 2026-08-23 it is enforced in three more places:

- `agents/refresh_opportunities.py` **no longer writes `url` at all.** It calls Gemini with
  `use_web_search=False` and used to write whatever `url` came back onto a live catalog row —
  the exact mechanism behind the scraper's 26% dead-link rate, except overwriting curated
  data rather than creating new. The scraper's fix (take the URL from `groundingChunks`) is
  unavailable without search, so the field is simply not written; `agents/check_links.py` owns link
  health and a replacement URL is a human edit in the console. Its prompt also used to open
  with *"YOU MUST use web_search and web_fetch"* while search was off, which left the model
  no way to comply except to answer from memory in the voice of a lookup. It now says it has
  no web access and that null is the expected answer.
- `agents/check_reviews.py` **takes `review_sources` from the search, not from memory**: phase 2 is
  handed the URLs resolved from phase 1's grounding chunks and told to copy them, and
  `clean_sources()` marks each kept source `retrieved: true/false` and drops any unretrieved
  one an HTTP check finds dead. Measured 2026-08-23: **199 of the 1469 source URLs already in
  the catalog (13.5%) were dead**, and these are shown to students as the evidence behind a
  legitimacy verdict. The grounding half matters more than the HTTP half — **61% of those
  URLs sit on hosts (Reddit 654, College Confidential 158) that never answer a checker at
  all**, so `retrieved: true` is the only real proof obtainable for them.
- Both search-enabled agents are now **two-phase and retry once on a silent search**, and
  both **write nothing if still silent** — see the next section.

**Asking for JSON is what stops an agent searching — all three search agents are now
two-phase.** This is the scraper's design generalised, and it turned out to have a second,
bigger justification than the one it was built on. Measured 2026-08-23 in a controlled A/B
(one row, identical research instructions, arms alternated, only the closing paragraph
differing): **prose 4/4 calls searched, JSON 0/4**, 34 grounding chunks against 0. It matches
the history — `agents/check_reviews.py` had made **22 searches across 3089 row-checks** and
`agents/check_deadlines.py` **59 across 1218**, both on single JSON calls, against the scraper's
prose phase 1 at 5.3 searches/seed.

    Phase 1 (research)  prose out, tools on   -> keeps grounding / retrieved source URLs
    Phase 2 (extract)   notes + REAL urls in  -> strict JSON out, no tools

State the claim carefully — a previous session over-claimed it and had to retract. It is a
large shift in **probability**, not a gate: run id=33 (JSON) fired 6 searches and run id=48
(prose) fired none. `wingman/gemini_common.py`'s SEVENTH finding carries both counterexamples. The
THIRD finding still stands: there is no way to *force* a search, only to stop discouraging
one. Do not collapse any of the three back into a single call.

- **`MAX_SEARCHES = 1`** in both new two-phase agents. On Gemini this is a soft budget folded
  into the prompt; on Anthropic it is `max_uses`, enforced server-side. It is the dominant
  cost lever, because the fee is per search, not per token.
- **Claude's `extract_source_urls()` is its `groundingChunks`.** `web_search_tool_result` /
  `web_fetch_tool_result` blocks carry the URLs actually retrieved — already resolved, with
  no redirect hop. Phase 2 gets them for the same reason the scraper's does: so the model
  copies a URL that was really fetched instead of recalling one that merely looks right.
- **`web_fetch` is deliberately NOT capped alongside `web_search`** in `agents/check_deadlines.py`.
  It carries no per-call fee and it is the tool that actually reaches the FAQ/key-dates
  subpages the dates live on; the prompt's whole estimation logic depends on it.

**Silent search: detect and re-roll, never prompt harder.** Both providers still decide per
call whether to search. The mitigation is to re-send the *identical* prompt once — a silent
call pays no per-search fee — and it is in all three agents.

- In `agents/check_reviews.py` a **still-silent call writes nothing and does not stamp
  `last_reviewed_at`**. That column is the staleness filter, so the old behaviour did double
  damage: a memory-derived `insufficient_data` (textually identical to a real search finding
  nothing — the file's own comment said so) *and* a 30-day suppression of any re-check that
  would have corrected it. Skipping leaves the row due, and the next pass re-rolls.
- In `agents/check_deadlines.py` the retry is on **by default including the interactive path**
  (`retry_on_silent=True`). That costs a user one extra round-trip, and it is worth it
  because `server.py` caches a deadline answer for 7 days: one silent, invented set of dates
  is served to every student who opens that opportunity for a week. `check_one()` returns a
  4-tuple `(info, cost, searches, attempts)` — **both** call sites (this script's `main()`
  and the on-demand endpoint in `app/routes/opportunities.py`) unpack it. **`info` is
  tri-state** as of 2026-08-24: `{}` when phase 1 never searched, `None` when phase 1 DID
  search but phase 2's JSON could not be parsed, a dict otherwise. Neither caller may
  re-derive what to do with that — both call `deadline_write_decision()`.
- **A still-silent deadline check writes nothing, in both paths, and that is now load-bearing
  rather than merely cautious.** `check_one()` returns an *empty* info when the search never
  fired, so writing it would blank the row's real `status`/`important_dates` **and** stamp
  `last_checked_at` — destroying good data and then hiding the damage behind the 7-day TTL.
  The interactive endpoint falls back to `cached_deadline_payload(opp, "unverified-fallback")`
  and deliberately does **not** stamp, so the next request re-rolls the search decision
  instead of being served the hole.
- **`deadline_write_decision(info, searches, existing_dates)` is the single place that
  decides whether a check may overwrite a row**, shared by the batch loop and the interactive
  endpoint so they cannot drift. FOUR outcomes, three of which write nothing AND do not stamp
  (`source` on the response names which):
  - `unverified-fallback` — phase 1 never searched. The original guard, above.
  - `unparsed-fallback` — phase 1 searched but phase 2's JSON was unreadable. **This used to
    collapse into `{}` and be written as an authoritative `status=unknown` with no dates**:
    one garbled response wiped a row's real deadlines, and the stamp then served that hole to
    every student for 7 days. "We looked but cannot read the answer" is not "there is nothing
    to find", and the silent-search guard never covered it because a search *did* happen.
  - `kept-existing` — verified, but found no dates while the row already has some. A verified
    empty result is far more often a search miss than a program withdrawing its dates; a
    genuinely dead program comes back `not_running`, which still writes.
  - `fresh, real search` — write and stamp.
  Two deliberate exceptions to `kept-existing`: **`not_running` always writes** even with zero
  dates (an empty `important_dates` is the *correct* answer for a discontinued program), and a
  row with **no existing dates** is written and stamped, because there is nothing to lose and
  not stamping would re-bill that row on every view forever.
- **Nulling `dates_last_checked_at` is the only way to force a re-check inside the TTL**, and
  until 2026-08-24 there was no working way to do it: `wingman/clear_deadline_cache.py` PATCHed
  `last_checked_at`, a name that only ever existed in `agents/check_deadlines.py`'s DDL comment, so
  PostgREST rejected every write. That script now takes ids or `--all` (with `--dry-run` and a
  `--yes-really` guard), `agents/check_opp_data.py` inspects the same columns, and the console's
  deadline card carries a **Force re-check** button over
  `POST /api/agents/deadline/clear-cache`. All three quote the **queued** spend (~$0.07/row,
  paid when a student next opens the row) rather than a $0.00 that would read as free, and the
  whole-catalog read paginates past PostgREST's 1000-row cap — unpaginated it silently cleared
  the first 1000 rows and reported a short count as if it were complete.
- **A missing registration-OPENS date silently downgrades an opportunity**, and it is now
  counted rather than shrugged at (`missing_opens_date()`, logged per interactive check and
  totalled in the batch summary). `computeProgressStatus` marks an item **Happening Now** the
  moment its FIRST date has passed — so a row whose only date is a deadline reads "Coming Up"
  right until it flips to "Past" and can never say Happening Now, which is backwards for a
  student who could be applying today. Measured 2026-08-24: **13 of the 34 active rows that
  carry any dates (38%) had no `opens` entry**. All four prompts (both `agents/check_deadlines.py`
  phases, `extractTrackerInfo`, `intakeExtractAndClassify`) now require an opens entry
  whenever there is an application step, require projecting the prior cycle's when the current
  one is unposted, and require an explicit reason in the note when there genuinely is none.
  Note the app deliberately does **not** assume "no opens date means applications are open" —
  that would be wrong for a program opening months from now. Estimation is **best-effort from
  the previous cycle's search data**, which is an explicitly accepted trade: a well-founded
  estimate carrying `was_estimated` beats an empty field, and the app labels it "Predicted
  dates from past cycle" wherever it shows.
  - Ladder step **b2 is the one that actually recovers opening dates**: when the current cycle
    posts a deadline but no opening and a prior cycle posted both, apply the prior cycle's
    **opens-to-deadline INTERVAL** to the current deadline rather than rolling last year's
    opening forward a year. When a cycle shifts, the interval survives and the calendar date
    does not. Present in all three search prompts.
  - **An estimated date must never be today's date.** Observed live on 2026-08-24: a row was
    given an opening of that very day while its own note read "~10-11 weeks before a
    2027-01-10 deadline" — which computes to late October, not today. The model stated the
    right method and substituted "now" for the arithmetic, and that alone made the program
    read HAPPENING NOW. All four prompts now require an estimate to be what its stated basis
    computes to, and to be omitted rather than anchored to the current date. This is the
    "never invent a date with no basis" rule failing in a new way: the basis was real and the
    arithmetic was not.
  - **`important_dates[].estimated` is per-DATE**, and the card renders "(estimated)" beside
    each one it applies to. The row-level `was_estimated` stays as the roll-up but cannot do
    this job: a row routinely mixes a confirmed deadline with a projected opening, so one
    card-level banner either implies the real deadline is a guess or lets the guessed opening
    pass as fact — and the opening is exactly the date that decides whether the card reads
    "Happening Now". Before this the marker existed only when the model happened to type
    "(estimated)" into the free-text label, which it did for IEEE and would not for the next
    row. All four prompts now set the field and are told NOT to put it in the label;
    `getDisplayMilestones` ORs it with `projected`, since a client-projected date is an
    estimate by construction. Absent on rows written before 2026-08-24 — treated as unknown,
    never as confirmed, and the renderer suppresses a duplicate when the label already says it.
  - `agents/check_deadlines.py` gained **`--ids ID...`** and **`--missing-opens`** so a known gap can
    be re-checked for cents instead of paying ~$84 for a full pass to fix a handful of rows.
    Both ignore the 7-day cache (that staleness filter belongs to the interactive endpoint,
    not to a deliberate operator re-check), and both work with `--preview`, which is free.
    `--missing-opens` selects on `missing_opens_date()`, so it needs a deadline present — a
    row with no dates at all is a different problem and is not swept up by it.
- **Measured backfill, 2026-08-24** (`agent_runs` id=61, `--missing-opens`, 8 rows, **$0.3788**,
  8/8 searched, 0 silent, 0 unreadable): **5 of 8 gained an opens date** and 2 rows moved from
  "Coming Up" to "Happening Now" — one correctly (Congressional App Challenge really is open),
  one via the today-anchoring bug above. The 3 that still have none now carry an explicit
  reason in `important_date_note` instead of a silent absence. One row hit `kept-existing`,
  i.e. the empty-result guard firing on live data as designed.
  - The run also exposed a limit worth knowing: **`kept-existing` protects against an EMPTY
    result, not a THINNER one.** UChicago's Summer Language Institute went 7 dates -> 3 on a
    verified check and the write was allowed, correctly — a verified correction must be able
    to remove dates. There is no guard against a verified answer simply being worse, and
    adding one would block legitimate corrections.
- **The client may only DELETE dates on a verified `source`.** The deadline response carries
  `source`, and `isVerifiedDeadlineSource()` in `frontend/src/lib/tracker.ts` accepts only
  `cached` and `fresh, real search`. Both readers (`applyDeadlineCheckToInfo` and
  `refreshTrackerDeadlines`) gate on it. The guard used to be a bare `.length`, so a verified
  "discontinued, no dates" could never clear the dates `extractTrackerInfo` had guessed — the
  card ended up reading `status: unknown` beside confident-looking dates nothing had ever
  confirmed, and students read the dates, not the status. Widening it to clear on **any** empty
  payload would be worse: `mock`, `stale-fallback`, `unverified-fallback` and `kept-existing`
  all echo an empty array and would wipe good data. Add a source to the endpoint and it must be
  classified in that list.
- **The Quest Log's refresh counts four outcomes, not two.** `refreshTrackerDeadlines` returns
  `checked / updated / skipped / blocked / failed / total` off `getDeadlineCheckResult`, which
  carries the HTTP status through `HttpError`. A 404 is **not** a failure — it is a tracked
  item with no catalog row, which can never be auto-checked — and a 402 is the paywall. All
  three used to collapse into a bare `null` and be reported as "no changes found".
- Cost is banked **per attempt** in both, so an exception on the retry cannot discard what
  the first call already spent. Same fix the scraper's two phases needed.
- **The Quest Log's "Last checked" line survives navigation** (`src/lib/lastChecked.ts`, a
  module singleton like `newlyAdded.ts`). It was component state, so switching tabs reset it
  to "Last checked: never" — the check had really run, and the app then said it never had,
  which reads as the refresh having failed. Progress ticks ("Checking 3/12…") are deliberately
  NOT remembered: navigating away mid-run would freeze one on screen forever. Not persisted
  either — a stamp from three days ago greeting a fresh load is staler than saying nothing.
- **Google Calendar sync**, which is downstream of all of this and had two matching bugs:
  a refresh rebuilt `importantDates` and dropped each entry's `googleEventId`, so the next
  sync POSTed a **new** event while the old one — still carrying the same index-based
  `wingmanId` — was by definition not stale and survived the sweep, giving the student a
  duplicate on their real calendar per refresh. Fixed on both sides: the client carries the id
  forward by index (the marker IS `${item.id}::${index}`, so slot N's event is slot N's date),
  and the server maps `wingmanId -> event id` before upserting, adopts the existing event when
  the client has no id, and deletes true duplicates, reporting them as `deduped`. Separately,
  `collectTrackedDeadlineEvents` now **skips `not_running`** like every reader in `status.ts`:
  `cycleYearShift` deliberately does not project a next cycle for a discontinued program, so
  those carry REAL past dates and were putting dead deadlines on a student's calendar.
- **Events go to a dedicated "Highschool Wingman" calendar and CANNOT go anywhere else.** The
  `calendar.app.created` scope grants access only to calendars the app itself created, which
  is what guarantees Wingman can never read or write a student's own calendars. The cost is
  that "it isn't syncing" is almost always "I was looking at my primary calendar": the sync
  result therefore NAMES the calendar and returns a `calendarLink` (taken from a written
  event's `htmlLink`, which is far more reliable than constructing a URL from a secondary
  calendar id). **Do not try to force the calendar visible** — `PATCH
  /users/me/calendarList/<id>` returns **401** under this scope, verified 2026-08-24 with a
  token that reads and writes events on that same calendar. The scope covers events on
  app-created calendars, not the calendar list.
- **The calendar MIRRORS the Quest Log — the sweep now deletes unmarked events too.** This
  reversed a deliberate earlier choice, and the reversal is the more important half of the
  fix. The marker only started being written 2026-08-22, so sparing unmarked events left
  every older one permanently unsweepable: measured on the first real account, **45 events on
  the calendar for 17 tracked dates — 22 unremovable orphans plus 6 duplicates**, including
  deadlines for opportunities deleted from the app weeks earlier. The marker's job is now
  only to identify WHICH tracked date an event is, so a sync PATCHes rather than duplicates.
  State the consequence plainly rather than discovering it later: **anything a student adds
  to that calendar by hand is removed on the next sync.**
- **Phase 2 is skipped when phase 1 stayed silent** in both agents. Notes written without
  looking are not worth converting, nothing gets written either way, and skipping keeps a
  fully silent row at roughly the old per-row price.

**What the two-phase change costs, measured rather than guessed** (2026-08-23, one row each):

| | before (single JSON call) | after (two-phase, MAX_SEARCHES=1) |
|---|---|---|
| `agents/check_reviews.py` | $0.0014/row, **~0 searches** | **$0.0166/row** → ~$20 per 1226-row pass, ~$4 per staleness tranche |
| `agents/check_deadlines.py` | $0.0010/row when silent | **$0.0676/row** → ~$84 for a full `--all` pass |

Read the deadline row carefully before reacting to $84: the interactive checks that *really
searched* have always cost a **median $0.0790** (36 of them in `deadline_check_log`), so the
two-phase version is **cheaper per verified check** than what the on-demand endpoint was
already paying — `MAX_SEARCHES` capped `web_search` at 1 where it allowed 3. The old
sub-cent `agent_runs` figures (id=14, id=16) are the price of *not looking*, not a cheaper
way of looking, and comparing against them is how this decision gets made wrongly.

`agents/find_mailing_lists.py` is the odd one out on cost, and deliberately so: it fetches each
row's page with `urllib` and finds provider embeds by **regex**, and only calls the model
when a form was actually found — to answer the one question a regex cannot, *is this form
THIS program's list or the host institution's?* Rows with no form resolve for **free**, no
API call at all. Do not "improve" it by handing the whole page to a model: a hallucinated
Mailchimp endpoint is a recipe that fails silently for every student who ever taps the
button, where a regex cannot invent one.

`agents/check_reviews.py` selects rows on **staleness only** (`STALE_AFTER_DAYS = 30`, i.e. never
reviewed or last reviewed >30 days ago), and `--force` ignores that entirely and re-checks
every active row. The threshold was 182 (~6 months) until 2026-08-22; at that value an
ad-hoc run did nothing at all for half a year, so the only way to make the agent act
between passes was `--force` — the whole catalog or nothing. 30 days keeps a plain run
idempotent (rows checked yesterday are not re-paid for) while letting an ad-hoc run pick up
whatever has genuinely aged. Reputation moves on the scale of months, so don't drop it much
further; the console's **Force recheck** checkbox is the escape hatch for "re-check
everything now". A `review_status:
is.null` filter used to sit alongside it, added to protect the August 2026 backlog from
being re-paid for on every resume; it was removed 2026-08-22 once that backlog finished.
Do not put it back. With every active row now carrying a verdict it matched nothing, which
made the staleness threshold dead code, meant the agent could never re-check anything, and
— because the filter sat *outside* the `if not args.force` branch — made `--force` return 0
rows too, the opposite of what it documents. Today's selection is 0 rows (everything was checked in
August). At 30 days the catalog comes due in five batches from **2026-09-17** (825 rows)
through 2026-09-21, rather than all at once; a full 1327-row pass is roughly $1.42 and
~110 minutes at the 5s delay floor.

## Cost accounting — what the numbers do and don't include

Every cost figure in this repo is **estimated locally from token counts** against the price
constants in `wingman/gemini_common.py` / `wingman/claude_common.py`. Nothing reads provider billing, so
treat totals as a floor. Known blind spots, all of them deliberate and surfaced in the
console's "Estimated vs billed" card rather than hidden:

- **A client-side timeout still bills server-side.** Killed requests complete and are
  charged; the local estimate never sees them. This caused the original ~$30 overspend.
- **Runs that die before their closing PATCH** leave `cost_usd` NULL. The console shows
  those as `unknown`, never as `$0` — five such runs exist in current history.
- **Dry runs ARE logged and ARE counted as spend.** `--dry-run` skips database writes, not
  API calls, so it costs the same as a live run. Runs are marked with a `-dryrun` suffix on
  `agent_runs.mode` (chosen over a new column so no migration was needed); the console
  counts their cost but excludes their would-have-been row counts.
- **Interactive app calls** (`/api/messages`, `/api/messages-claude`) are costed into
  **one rolled-up `agent_runs` row per surface per UTC day** (`interactive_gemini` /
  `interactive_claude`) rather than a row per call. See `record_interactive_cost()`.
- **On-demand deadline checks** write costed rows to their own `deadline_check_log` table;
  `fetch_deadline_check_cost()` folds them into the summary. They are deliberately NOT
  also rolled into `agent_runs` — that would double-count them.
- **Resume / LinkedIn import** (`_extract_profile_from_text`) used to throw its usage block
  away, so those Claude calls appeared in no figure anywhere. It now records into the same
  `interactive_claude` rollup as every other app call.
- **Every Anthropic call in this repo runs on Haiku 4.5** (`claude-haiku-4-5-20251001`),
  pinned in three places that must agree: `server.py`'s `CLAUDE_MODEL`,
  `agents/check_deadlines.py`'s local `CLAUDE_MODEL`, and `wingman/claude_common.py`'s `MODEL`. The last
  of those was left on `claude-sonnet-4-6` when the other two moved, which meant the
  model depended on which entry point you came through — the resume/LinkedIn import went
  through `claude_common` and so actually ran on Sonnet while recording its cost under
  `CLAUDE_MODEL`, i.e. `user_costs.model` named a model that had not served the call.
- **`claude_common`'s price constants must track its `MODEL`.** `server.py` imports them to
  cost every `interactive_claude` call, and those run on Haiku — so while that file said
  Sonnet, the constants were Sonnet's $3/$15 against Haiku's actual $1/$5 and interactive
  Claude spend was estimated at **3x** what it cost.
- **`agents/check_deadlines.py` costed its Claude calls with `gemini_common.estimate_cost`** —
  Gemini's $0.75/$3.75 per MTok plus $0.014/search — against Anthropic calls made by its
  own local `call_claude()`. It now imports `estimate_cost` from `claude_common`.
- Both corrections landed 2026-08-22 and are **not applied retroactively**: `agent_runs`,
  `user_costs` and `deadline_check_log` rows written before then carry the old rates.

**Per-user cost attribution** lives in a Supabase `user_costs` table
([user_costs_schema.sql](db/user_costs_schema.sql) — a one-time manual DDL step in the Supabase
SQL editor; until it is run, `server.py` logs one warning, attribution is off, and the console
shows the setup step instead of an error). Read it as a **breakdown of interactive spend, never
as extra spend**: every dollar in `user_costs` is already counted in the `interactive_*`
`agent_runs` rollups or in `deadline_check_log`. `record_interactive_cost()` computes the cost
once and writes both, so the two can't drift.

- Grain is one row per `(userid, UTC day, surface, feature)` — a rollup, for the same reason
  the interactive daily rows are.
- **Feature is classified server-side from the system prompt** (`classify_feature()` /
  `_FEATURE_SIGNATURES`), reusing the same signatures `generate_mock_text()` matches on, so no
  script.js call site had to change. Adding a new AI feature means adding a signature here too,
  or its spend lands in `other`.
- `user_costs.calls` counts **billed, attributable calls only** — it is not app traffic. It
  excludes mock-mode calls, signed-out calls, cached/mock/stale-fallback deadline checks, and
  calls that errored before returning usage. That last exclusion is the familiar blind spot:
  an errored or timed-out call still bills server-side, so the count is a floor like every
  other figure here. The UI labels it "billed calls" and carries the exclusions in a tooltip.
- Attribution is best-effort: calls with no `userid` (signed-out, pre-login) are not
  attributed. The console reports that residual as **unattributed** with an attribution rate
  rather than distributing it across users.
- Cached/mock/stale-fallback deadline checks are **not** charged to anyone — they make no API
  call. Only the user whose request actually paid to populate the cache is billed for it.
- Spend is broken out by **provider and model**, not just by surface. `user_costs.model`
  stores the exact model id that was billed (`MESSAGES_MODEL` / `CLAUDE_MODEL` /
  `check_deadlines.CLAUDE_MODEL`, imported rather than re-declared so a bump there can't
  leave the breakout naming a stale model). Provider is **derived** from that id by
  `provider_for_model()` — never a stored column, which could drift out of step with
  `model` after one bad write. Do **not** split by `surface` instead: `gemini`→Google and
  `claude`/`deadline_check`→Anthropic line up today only because of the current wiring,
  and the profile chat is already a deliberate Anthropic holdout inside an otherwise
  Gemini app — one feature moving provider would silently make a surface-based split wrong.
  Surface is still reported separately; it answers *where in the app*, not *who is billing*.
- The `model` column arrived after the table did, so **`db/user_costs_schema.sql` must be
  re-run** (it is idempotent — `add column if not exists` plus a constraint swap). Until
  then the console degrades rather than breaking: reads retry with a narrower select,
  writes retry without the column, `model_ready: false` comes back, and every model reads
  as `(before model tracking)` while the provider split still works off the surface
  fallback in `_SURFACE_PROVIDERS`. Totals are correct in both states.
- The grain constraint **includes `model`** and the column is `not null default ''`, not
  nullable: Postgres treats NULLs as distinct in a unique constraint, so a nullable model
  would make every single call insert a fresh row instead of accumulating into one.
- `GET /api/agents/user-costs?days=&limit=` (localhost-only like the rest of `/api/agents/*`,
  which matters more here — the response carries names, emails and plan status) backs the
  console's **Cost per user** tab: attributed vs unattributed, spend by provider, by model
  (with each model's feature split), by feature, and a per-user table showing cost against
  the $9.99 plan price.
  - The table is seeded from the **`users` table**, not from `user_costs`. Built from the
    cost rows alone it was a spend ledger wearing a roster's name: an account with no
    billed call has no rows at all, so 9 of 15 accounts — every recent signup among them —
    could not appear at any range setting. A trial that costs $0 is not missing data, it
    is the most important thing this page can say. Zero-spend rows carry a **never used
    AI** pill, sort below every spender, and are not expandable.
  - The headline day figure is **`latest_day` — the most recent day with spend — never
    "today"**. Rows are bucketed on the UTC day, which rolls at 5pm Pacific, so a "today"
    figure reads `$0.00` every evening on a day that cost real money, which is
    indistinguishable from the pipeline having stopped. A rolling 24h window was
    considered and rejected: the daily grain cannot say how much of yesterday's single
    rollup row falls inside it, so it would have to estimate. `latest_day` is exact.
  - **The unattributed residual is split in two.** `pre_attribution_cost_usd` is spend
    billed before the first `user_costs` row ever existed (`_attribution_start()`) and
    could never have been attributed to anybody; `signed_out_cost_usd` is the real
    no-userid traffic. Reported as one number the first swamps the second — it read as
    "$2.92 from calls with no signed-in user" when the true signed-out figure was ~$0.06
    — and pinned the rate at 37% until those rows aged out of the window. `attribution_rate`
    is measured against `attributable_total_usd`, so it describes how well attribution
    works rather than how old the window is. Expanding a user row shows their own provider and model breakdown.
  Provider colours in the console are **fixed per provider**, unlike the positional feature
  palette — otherwise a provider overtaking the other swaps colours mid-session and the bar
  reads backwards.
- The tab's body is **one always-visible frame plus three paged cuts of the same money**.
  The frame is the KPI tiles, the per-day attributed-spend chart and the provider split —
  provider is the frame, not a fourth cut. Under it a `.tab` strip (`showUserPanel()`)
  pages between **By model**, **By feature** (each feature carrying the model(s) that
  serve it, since "what costs most" and "what model is it on" are one decision) and
  **Users**. They page rather than stack because reading them as one column meant
  scrolling past two tables to reach the one you wanted. The sort `<select>` is hidden
  unless the Users panel is showing — it means nothing against the other two.
- Rows with a blank `user_costs.model` collapse into a single **"Other"** bucket, in the
  by-model list and inside every feature's model list alike (`_group_untracked_models()` /
  `_group_untracked_feature_models()`, keyed `UNTRACKED_MODEL_KEY`). They are all from one
  ~5-hour window on 2026-08-21, between attribution going live (18:59:39, the first
  `user_costs` row) and `db/user_costs_schema.sql` being re-run to add the column (before
  00:16:41 on 08-22, the first row carrying one): 13 rows, $0.19, a set that can never
  grow. Only the **model id** is unknown in them — cost, calls, tokens, user, surface and
  feature are exact and fully counted, and the provider still resolves through
  `provider_for_model()`'s surface fallback, which is why the provider split stays complete
  while the model table does not.
  - **Do not backfill them** from the model pins in `server.py`, even though the answer is
    inferable. This column means *what was actually billed*, and the Sonnet/Haiku drift
    above is what happens when a plausible guess is written into it. Unknown is honest.
  - They collapse rather than sit among the real models because a blank id is not a peer
    of `gemini-3.5-flash-lite` — it is an absence, and ranking an absence by cost next to
    real models invites reading it as a third model. The bucket sorts **last regardless of
    cost**, carries a flat neutral colour (never a provider hue), and keeps the providers
    it covers in `providers` for the row tooltip. A footnote under both tables carries
    `UNTRACKED_MODEL_NOTE`. It ages out of a 30-day window on its own around 2026-09-20.
- The **By model** table deliberately has no per-model feature detail row: that cut is the
  By feature panel, which states it properly with the model attached. Repeating it under
  every model doubled each row's height to say the same thing worse.
- The console has **five top-level views** (`.viewtabs` / `showView()` in
  [admin_console.html](admin_console.html)): *Metrics* (the default — see the User metrics
  section below), *Agents* (everything about the five background agents, including the
  dry-run snapshot list), *Review queue* (pending activations), *Mailing lists* (recipe
  review), and *Cost per user*. Note the distinct `.vtab` vs `.tab` styling — `.tab` is the
  inline pill inside a card head (the scraper's National/Seattle switch) and filters one
  table; `.vtab` swaps the whole page. Keeping them visually different is deliberate.
- Two endpoints gained a `userid` so their spend can be attributed:
  `GET /api/opportunities/<id>/deadline?userid=` and `POST /api/extract-from-resume?userid=`
  (query string because one is a GET and the other is multipart). Both routes had to move off
  exact-`self.path` matching to survive the query string — the same trap the `/api/agents/*`
  routes already carry a comment about.

## User metrics — the Metrics view

The console's default view ([admin_console.html](admin_console.html), `#view-metrics`),
backed by `GET /api/agents/metrics?days=&limit=` (`get_user_metrics()` in `server.py`,
localhost-only like every `/api/agents/*` route — and it matters more here than anywhere
else on that router: the payload is a roster of names, emails, plan status and per-account
behaviour for a user base that is largely minors. **Do not add an export button, and do
not expose this route.**)

**Cost per user asks what an account costs; this asks whether it got anywhere and whether
it paid.** Same roster, deliberately separate views — a page computing both computes
neither well. This one never reports spend totals; that decomposition belongs to the other
tab, and two places computing the same dollars is how the two drift.

**The blocker this feature was built around: there is no event log.** Everything the repo
knows about a user is current state (the `users` row and its `data` jsonb) or a cost
rollup. Two obvious substitutes are both wrong, and both were checked:

- **`users.updated_at` is a trap.** Declared `default now()` in
  `scripts/one-off/migrate_users_to_supabase.py` with **no trigger**, and `update_user_data()` never
  writes it — it equals `created_at` on practically every row. A "last active" metric
  built on it looks plausible and is fiction. (Contrast `opportunities.updated_at`, which
  moves on every write — see below. Same column name, opposite meaning, and **neither**
  means what you would guess.)
- **`opportunities.updated_at` is stamped by an ON-UPDATE TRIGGER, not by the code.**
  Verified 2026-08-23 by PATCHing a column back to its own value and watching the timestamp
  move. An earlier version of this file said it was "stamped explicitly by `server.py`" —
  the explicit writes exist, but they are not what makes it move, so **every** agent write
  moves it and no reader can attribute a change to a particular agent. The scripts that set
  it by hand are harmless but redundant.
  - This became a real bug the moment `agents/check_links.py` landed: a link-health pass writes
    `link_status`/`link_checked_at` to every active row, so `agents/check_refresh_progress.py` —
    which counts `updated_at > cutoff` — reported **"1236/1236 opportunities updated"** with
    the metadata refresher having touched none of them. It now excludes rows whose
    `link_checked_at` also falls in the window and reports its count as a floor.
  - `agents/check_links.py`'s `build_update()` still withholds `updated_at` for a telemetry-only
    write. That is currently a no-op against the trigger, and it is kept deliberately: it
    states the intent, and it is what makes the behaviour correct if the trigger is ever
    dropped. Do not "simplify" it away on the grounds that it changes nothing today.
  - **Do not add a new reader of `opportunities.updated_at` that means "the opportunity's
    content changed."** It cannot mean that. If an agent needs to know when it last touched
    a row, it needs its own column — the way `last_reviewed_at`, `dates_last_checked_at`
    and `link_checked_at` already do.
- **`user_costs` only sees billed AI calls.** A student who opens the app daily and works
  their tracker costs $0 and reads as inactive; in mock mode the signal vanishes entirely.

So the view splits in two, and **the state half needs no migration at all**.

**The activation funnel** (`FUNNEL_STAGES`) is **cumulative and strictly ordered** — an
account counts at stage N only if it satisfies stages 1..N, which is what makes the
step-over-step percentages mean anything. Rules that follow, several of them got wrong on
the first pass:

- Every predicate must be genuinely implied by the ones after it, or the cumulative rule
  silently drops people who are *further* along than the chain can see. That is why
  `ran_search` accepts "has anything in the tracker" as evidence alongside its billed-call
  proxy — the proxy is incomplete, the implication is not. It renders with a **proxy**
  pill for the same reason: mock mode bills nothing, so with no API key the proxy half
  reads zero for everybody.
- **"Came back after signup day" is NOT in the chain**, even though it reads like stage 2.
  Someone can build a profile and track five things the day they join and never return;
  chaining it would score them as a total failure. It sits beside the funnel with the
  other side metrics (rich profile, calendar, mailing list, Google signup, consent gaps).
- **`meaningful_profile` reuses `PROFILE_SUFFICIENT_LENGTH = 20`** from
  [script.js](script.js) — the bar the app itself already gates on and shows students. A
  second definition here would let the console and the app disagree about the same
  student. Move it in both places or in neither.
- **`tracked_1`/`tracked_3` exclude saved-for-later.** `hs-tracker-saved[id] === true`
  means the student explicitly parked it, and `script.js` already refuses to count those
  as actively tracked. Counting them here would inflate the most important number on the
  page.
- Each stage carries `missing_userids`, so clicking a bar names exactly who it lost with
  no second request. At this account count **that is the point of the page**: a percentage
  is a decoration on a fraction until the roster is in the hundreds, so every tile shows
  its raw `n/d` and the funnel names people. Same call the Cost per user tab made when it
  seeded its table from the roster rather than from the cost rows.

**Trial-to-paid conversion's denominator is trials that have actually ENDED**, never all
accounts. With a 7-day trial, dividing by everyone scores every signup from the last
week as a failure before they have had a chance to decide. `beta` grants are excluded
from both halves and reported separately — they never reached the choice. A `canceled` or
`past_due` account carrying a `stripe_subscription_id` counts as **converted**: cancelling
later is churn, not a failure to convert, and folding the two together would make the rate
fall every time a paying customer leaves. Every access gate derives from
`subscription_state()`, never from re-reading the columns, for the reason that function
exists.

**Two migrations gate the time-series half**, and both degrade to a setup notice rather
than an error:

- **[user_activity_schema.sql](db/user_activity_schema.sql)** → `activity_ready`. Unlocks
  DAU/WAU/MAU, the retention cohorts, and the "came back" side metric.
  `touch_user_activity(userid, surface)` is called from the nine handlers that carry a
  userid (login, data save/load, both AI surfaces, deadline check, subscription status,
  resume import, mailing-list subscribe). It **buffers in memory and a background thread
  flushes every 30s**: this table takes *every* authenticated request, not just billed
  ones, and PostgREST cannot do `SET hits = hits + n` without a stored function, so it is
  read-modify-write either way and batching turns a round trip per request into one per
  user per interval. A process that dies between flushes loses at most one interval of
  counts; **DAU/WAU/retention only need the row to exist**, so only `hits`/`surfaces` are
  ever approximate. `get_user_metrics()` flushes before reading, or the console trails the
  buffer and a quiet day looks like a stalled pipeline. A missing table latches the whole
  path off after one warning; a transient failure deliberately does **not** — the same
  distinction `record_user_cost` carries, for the same reason.
- **[user_metrics_daily_schema.sql](db/user_metrics_daily_schema.sql)** → `snapshots_ready`.
  Every state metric is computed from the *current* `users` table, and the `data` jsonb
  holds one profile rather than a history of one — so "how many users had a meaningful
  profile on 2026-08-01" is not merely unqueried, it is **unrecoverable**. The snapshot is
  written from the read path (throttled to one write per 5 minutes) rather than on a
  schedule, because there is no scheduler here and computing it twice is how a chart and a
  tile come to disagree. `dau`/`wau`/`mau` are **NULL, not 0**, when activity is not set
  up: a zero is indistinguishable from a genuinely dead day, and these rows can never be
  recomputed. **Run it on day one even though nothing reads it for weeks** — every day it
  is not running is a day permanently missing from every trend line this will ever draw.

Both files end with an **ALTER block** for the same reason `db/mailing_list_schema.sql` does:
`create table if not exists` is a no-op against a table that already exists in an older
shape, and PostgREST 400s an entire insert on one unknown key — so a single missing column
means *nothing* is ever recorded and the view reads as "nobody used the app" rather than
"every write failed". Add a column to a CREATE there and you must add it to the ALTER too.

Smaller things the view is careful about, each for a reason already documented elsewhere
in this file: the DAU line is **never drawn back past `activity_since`** (a flat zero
through a period nobody was measuring reads as "the app was dead", not "we weren't looking
yet"); a retention cell for a cohort too young to have reached that column shows a dash,
**not 0%**, which would read as churn that never happened; plan-status colours are **fixed
per status, not positional**, exactly like the provider colours; and `money()` is
deliberately not used for MRR, because its magnitude-keyed precision renders `$0.0000`,
which looks like a rounding artefact rather than nobody paying yet.

See [USER_METRICS_PLAN.md](docs/archive/USER_METRICS_PLAN.md) for the design rationale and the one
phase still unbuilt (event-level funnel timing — *how long* from signup to first tracked
opportunity, which needs real events and is not worth it at this account count).

**Pulling real billed spend:** Anthropic exposes `GET /v1/organizations/cost_report`, which
needs an **Admin key** (`sk-ant-admin…`, set as `ANTHROPIC_ADMIN_KEY` in `.env`) or an
`org:admin` OAuth token — a regular `sk-ant-api` key returns 401, and the Admin API is
unavailable to individual (non-organization) accounts. Amounts come back in **cents** as
decimal strings. Google has **no equivalent**: the Gemini API exposes no billing endpoint at
all, and the Cloud Billing API can't be authenticated with an AI Studio key — so Gemini gets
a dashboard link instead of a live figure. See `fetch_anthropic_billed_cost()`.

**Three run tiers**, and only one of them is free:
- `--preview` — resolves which rows/seeds would be processed, prints a `PREVIEW_JSON:` line,
  exits before the first API call. **Zero cost, zero writes.** Shared plumbing lives in
  `wingman/agent_common.py`; `server.py`'s `preview_agent()` parses that line and pairs the count with
  a per-item cost averaged from that agent's real `agent_runs` history.
  - That average takes **successful runs only, and never `mode = "snapshot-commit"`**.
    Both exclusions matter and both were missing until 2026-08-22: a *failed* run counted
    every row it touched but errored out of most of them before paying for them, so it
    lands in the sample as an implausibly cheap per-item rate, and a snapshot commit
    carries real item counts against `cost_usd = 0` **by construction** (the dry run that
    produced the file already paid). With 6 of `review_checker`'s 10 qualifying runs being
    failures and a 7th a commit, the estimate came out at $0.000528/row against the
    ~$0.00091 its clean runs measure — i.e. the one feature whose whole job is to price a
    run before you authorise it was under-quoting by about half. The old $0.70 full-pass
    figure quoted above for `agents/check_reviews.py` was this same skewed number; it is $1.42 now.
  - `est_cost_low_usd`/`est_cost_high_usd` carry the **spread** across the sample, and
    `provisional` is set below three clean runs. A lone mean reads as a precision the
    number does not have — the scraper's own history spans three orders of magnitude per
    seed depending on how much web search a run triggered.
- `--dry-run` — **still calls the paid API at full cost**; only skips DB writes and dumps a
  local JSON snapshot instead. That snapshot can be **committed** later from the console
  rather than paying to re-run the agent live — see below.
- neither flag — full run, writes to Supabase.

**Timing** is configurable per run via `--min-delay` and `--timeout` on every agent (three
layers: module default → env var → flag). The 5-second inter-call delay is what fixed this
pipeline's repeated HTTP 429s — treat it as a floor. It also dominates wall time: ~1330 rows at
5s is ~110 minutes regardless of API speed. `gemini_common` and `claude_common` each expose
`set_min_delay()`/`set_default_timeout()`. Note `agents/check_deadlines.py` has its **own** local
`call_claude()` (not `claude_common`'s) and its own delay knob defaulting to 0, because
`check_one()` is shared with server.py's interactive on-demand deadline endpoint, where a
process-wide delay would make one user's request block on another's; batch mode raises it to 5.

**Committing a dry-run snapshot** ([dryrun_common.py](wingman/dryrun_common.py)) replays a snapshot's
withheld writes into Supabase. It makes **no API calls and costs nothing** — the money was
already spent by the run that produced the file. `GET /api/agents/snapshots` lists what is on
disk; `POST /api/agents/snapshots/commit` with `preview: true` resolves the real post-dedupe
counts without writing, and without it applies them.

- `_patch_updates()` mirrors each agent's live (non-dry) PATCH column-for-column. **If an
  agent's live write changes, change this too**, or a committed snapshot writes a different
  shape than a live run of the same agent.
- The **scraper's snapshot is written on live runs too**, not only dry runs, so it can name
  rows that already exist. Inserts are deduped by normalized URL against the *whole* table
  (active and inactive), so committing the same file twice inserts nothing the second time.
  The dedupe set paginates past PostgREST's 1000-row cap — a single unpaginated request
  silently truncates it and lets duplicates through.
- Committed rows always land `is_active = false`. A commit never activates anything.
- A real commit writes an `agent_runs` row with `mode = "snapshot-commit"` and `cost_usd = 0`.
  That pairs with the `-dryrun` row the original run wrote: the dry run carries the cost and
  no row counts, the commit carries the row counts and no cost, so neither is double-counted.
- **Snapshot filenames are stamped `YYYYMMDD-HHMMSS`** by `agent_common.snapshot_stamp()`,
  shared by all four agents. Seconds, not just the date: they were date-only until
  2026-08-22, so a second run on the same day silently overwrote the first one's file —
  and a `--dry-run` has already paid the API in full by the time it writes, so that
  destroyed work that cost real money. It also made this list a lie, since the file said
  `20260819` with no way to tell which of that day's runs it held.
  - Two shapes exist on disk and **both are read**. Files written before 2026-08-22 keep
    their date-only names and stay committable; `dryrun_common._run_date()` resolves those
    to midnight, exactly what the old code returned, so their staleness and ordering are
    unchanged. The console only prints a time for filenames that actually carry one — a
    date-only snapshot showing `00:00` would read as a real run time.
  - The stamp is **local time** (matching `agent_logs/<agent>_<stamp>.log`) but is parsed
    and served as UTC so every snapshot stays comparable; the console reads it back with
    `getUTC*` so the digits it prints match the digits in the filename.
  - The scraper's `source` value stays **date-only** (`scraper-national-20260820`) — a whole
    day's scrape groups under one source, and existing rows carry those values.
  - Snapshots are still sorted by full instant, not by date: same-day files would otherwise
    order by filename, i.e. alphabetically by agent rather than newest-first.
  - A snapshot still does not correspond to a particular `agent_runs` row — nothing links
    them — but two same-day runs no longer collapse into one file.

## Link health — `agents/check_links.py`

Fixing the scraper's fabricated URLs fixed only NEW rows. The catalog they join had never
been checked. Measured over all 1374 active rows on 2026-08-23: **1029 live, 137 dead
(10.0%), 208 unverified.** One row in ten sent a student to a page that is not there, and
they were real programs with rotted links (`smysp.stanford.edu`,
`jkcf.org/our-programs/young-artist-award/`, `training.nih.gov/.../aip_hs/`), not junk.

**As of 2026-09-02 the agent QUEUES findings for a person; it deactivates NOTHING on its
own.** Every finding sets `link_review_status = 'pending'` (a new column in
`db/link_health_schema.sql`) and shows up on the console's **Links** tab, where a person
multi-selects rows and either **Clears** them (dismiss the finding, row stays live) or
**Deactivates** them (`is_active=false` + `moderation_status='pending_review'`). The
classification below still names the *severity* of each finding — it now decides how the
queue row reads, not whether the agent pulls the row. See "The Links tab" below.

**Only evidence of absence is queued as a dead link.** This is the whole design, and the
numbers force it:

- **dead-link finding** (queued, strongest) — 404, 410, a malformed URL, or a hostname that
  does not resolve.
- **flagged finding** (queued, softer) — 403, 429, TLS failures, timeouts, connection resets.

403 alone is ~9% of this catalog (112 rows) and TLS failures another 41. Those sites are
refusing *our* client — a student's browser carries a different root store and loads them
fine — so reading "the connection failed" as "the page is gone" would have pulled ~150
working opportunities out of the catalog on the first run. `url_validate._is_dns_failure()`
is what separates a genuine NXDOMAIN (8 rows, all retired university subdomains) from the
41 TLS/timeout failures wearing the same `URLError` class; **do not collapse them**.

- **Two passes, always.** Anything that looks dead is re-checked before a write. Free, and
  it is the only thing between a CDN hiccup and a queued dead-link finding. Measured: 135 of
  137 were unchanged on the second pass and 2 rows moved *into* dead, so it corrects both ways.
- **Repair before queuing** — [url_repair.py](wingman/url_repair.py), free, on by default
  (`--no-repair` opts out). Programs get reorganised far more often than they are cancelled:
  of the 30 dead rows in the 08-23 audit, 9 were re-found on the same site and 9 of 9 came
  back live. See the next section — the accuracy bar there is the whole feature.
- A queued finding sets `link_review_status = 'pending'` (only over a NULL — a re-run never
  overturns a human `'cleared'`/`'deactivated'`) plus a `quality_flags` entry naming the
  code. It does **not** touch `is_active` or `moderation_status`. When a person then
  **Deactivates** from the Links tab, that human action writes `is_active=false` +
  `moderation_status='pending_review'` and `link_review_status='deactivated'`;
  **`reviewed_by`/`reviewed_at` are left alone** — most of these were approved by a person
  once, and "approved 08-23, link has died since" is a different situation from a row nobody
  has ever seen.
- **It never rejects, and never deactivates.** A rotted link is not a verdict on the program,
  and the verdict is a person's to make.

**The Links tab.** The console's `#view-links` view (top-level nav, between Run and Money)
holds the Link Checker agent card, the Re-find (repair) tool, and the review queue.
`GET /api/agents/link-queue` (`core.list_link_queue()`) returns TWO lists: `opportunities`
(`link_review_status='pending'` — the checker's open findings) and `repaired`
(`link_review_status='repaired'` — inactive rows the repair pass proved a new URL for, awaiting
manual activation). `POST /api/agents/link-queue/resolve` (`core.resolve_link_queue(ids, action)`)
applies `clear` / `deactivate` / `activate` to a multi-selected set — `activate` (the repaired
list's action) routes through `activate_opportunities` (moderation stamp + embeddings + cache
bust, MARQUEE M9) and then clears the `repaired` flag. Both localhost-gated like the rest of
`/api/agents/*`. The tab degrades to a setup notice if `db/link_health_schema.sql`'s
`link_review_status` column is absent (deactivate still works via a stripped write; clear
needs the column). This replaced the old behaviour where a dead link surfaced only in the
New-Opportunities review queue after the agent had already deactivated the row — and it
fixes the old "known gap" where a flag on a still-active row had nowhere to show.

**`--repair-flagged` scope (broadened 2026-09-02) and its NO-auto-activation rule.** It walks
inactive rows carrying any repairable link flag — `dead link (`, `link unverifiable (`,
`link unreachable (`, or the soft-404 flag (`_REPAIRABLE_PREFIXES` in `agents/check_links.py`), not
dead-link-only — and attempts a repair on any that re-check as dead **or** unverifiable (a
403/timeout row is often genuinely gone; a repair attempt is free). A proven repair no longer
sets `is_active=true`: it writes the new URL and parks the row at `link_review_status='repaired'`
for a person to verify and Activate on the Links tab. See M2 — as of 2026-09-02 **no** code path
in this repo auto-activates a catalog row.

## URL repair — `wingman/url_repair.py` (proves a moved link; never activates, as of 2026-09-02)

**Proposing a replacement URL is cheap and worthless on its own; accepting one is the
feature.** Measured over the 148 rows the first pass deactivated, taking the best-scoring
link on the parent page "repaired" **72 (49%)** — and a large share pointed at a *different
program at the same institution*: `ll.mit.edu/outreach/summer-high-school-internships` →
`middle-school-stem-program`, NIH's `aip_hs` → `pb/sip` (AIP ≠ SIP),
`medschool.vanderbilt.edu/imsd/...` → `/md/`. **A wrong repair is worse than no repair**: it
is a live link, so every other check passes it, and it silently sends a student somewhere the
row does not promise.

So nothing is accepted on similarity. **Three independent tests must all pass**, and each one
exists because of a specific measured failure:

1. **Title proof.** Fetch the candidate; every distinctive word of the program's name must
   appear in its `<title>`. A similarity ratio was tried and rejected: at ≥ 0.72 it accepted
   "Bay Area Entrepreneurship" → "BootCamp Entrepreneurship" (0.76), "Summer Research
   Immersion" → "First-year Research Immersion", "VEX Robotics Competition" → "RECF Robotics
   Competition", and a UC Berkeley course → the same provider's Yale one. The shared word is
   always the *category* and the differing word the *identity* — backwards for a ratio.
2. **The name must be its own.** Distinctive words are the name's **minus the org's**, so a
   match on the institution cannot stand in for a match on the program. Without it,
   "University of Notre Dame" verified against every page on `nd.edu`, "Jackson Laboratory
   Summer Student Program" against "Careers at The Jackson Laboratory", "Doodle for Google"
   against "Google Doodles". Fewer than two words left → unverifiable, leave it alone.
3. **No lost identity word.** If the OLD url used a word to identify this program and the new
   url and its title both lack it, we landed on a sibling. This is what catches a row whose
   **name and org are swapped in the catalog** (`name='University of Notre Dame'`,
   `org='Global Scholars Program'`, old slug `global-scholars` → `summer-scholars`), which
   passes tests 1 and 2 and is still wrong.

End to end on the same 148: **72 → 34 → 18 → 13 accepted**, and the 13 are right. Losing 59
proposals to keep the 13 honest is the intended trade — the unrepaired rows are not lost,
they keep their dead-link flag plus, where a candidate was found, an explicit
`possible replacement found but NOT verified` suggestion (47 rows got one), which is strictly
more than a reviewer had before.

**`--repair-flagged` no longer activates anything (changed 2026-09-02 by operator decision;
MARQUEE M2 was amended to match).** It used to be the one code path in the repo that set
`is_active = true` — it restored a deactivated row when it proved a moved link. That path is
gone: a proven repair now **writes the new URL onto the still-inactive row and parks it at
`link_review_status='repaired'`** for a person to verify and Activate on the Links tab. So
there is now **no** code path anywhere that auto-activates a catalog row. The repair is still
bounded to rows carrying one of this agent's repairable link flags (`_REPAIRABLE_PREFIXES`:
dead link / unverifiable / unreachable / soft-404 — broadened 2026-09-02 from dead-link-only,
since some "unverifiable" rows are genuinely gone), never a row a person rejected or one that
was never active, and each repaired row keeps a flag naming its **old URL** so the edit is
auditable and hand-reversible. A row whose original URL simply comes back to life on its own is
still *not* touched — that is a person's call in the console.

Ran 2026-08-23 (`agent_runs` id=55, under the old auto-restore behaviour): 148 flagged rows,
**13 restored**, catalog 1226 → 1239 active, queue 268 → 255, 47 rows gained a suggestion.
$0.00. (Post-2026-09-02 an equivalent run would leave those 13 at `link_review_status='repaired'`
for manual activation rather than restoring them itself.)
- **Two url_validate checks were tried here and rejected on measured noise**, and the
  reasoning is worth not re-deriving: `is_bare_domain()` fires on 16% of live rows and they
  are *correct* (`jshs.org`, `precollege.wisc.edu` — dedicated program sites whose homepage
  IS the program page), and `domain_matches_org()` on 9%, roughly one in seven of them real
  (the rest are university domain abbreviations no rule derives — `umd.edu`, `tamu.edu`,
  `gatech.edu`). Both earn their place in `agents/scrape_opportunities.py`, where a fresh candidate
  has the opposite base rate. What replaced them is `FLAG_SOFT_404` — a deep link that
  redirects to a bare homepage, i.e. the program page deleted behind a 200. It fires on 10
  rows (1.0%) at about one-in-two precision. Ten rows at one-in-two beats eighty-eight at
  one-in-seven.
- **[link_health_schema.sql](db/link_health_schema.sql)** — **RUN 2026-08-23; re-run 2026-09-02**
  to add `link_review_status`. Five columns now (`link_status`, `link_status_code`,
  `link_checked_at`, `link_dead_since`, `link_review_status`), all live. It keeps the same
  ALTER block for the same reason as `db/mailing_list_schema.sql` — **add a column to a CREATE
  there and you must add it to the ALTER too**. Without the migration the agent still runs and
  still records what it can: it drops those columns from its writes, losing the 7-day
  staleness filter (every run re-checks everything — free, so *slower* not *broken*) and, when
  it is specifically `link_review_status` that is missing, the queue routing. `link_dead_since`
  is not derivable from `link_checked_at` (stamped every pass): without it, a link broken in
  March and one broken this morning look identical.
- **The old "known gap" is closed.** A flag on a still-active row used to have nowhere to show
  (the Review queue lists `is_active=false` rows only), so the run report in
  `agent_logs/link_check_<stamp>.json` was the only place to read it. Now every finding —
  active or not — is queued via `link_review_status='pending'` and shows on the Links tab.
- `AGENT_CONFIGS_SCHEMA["links"]["free"] = True` is read by `estimate_agent_cost()` and by
  the console. A free agent's `$0.00` is a fact about its design, not a "no history yet"
  fallback, and the two must not render alike. Since 2026-09-02 the run no longer takes rows
  away from students at all — it only queues findings — so its confirm dialog carries no
  scary warning; deactivation is now a separate, deliberate human action on the Links tab.

## Action items — the Quest Log's checklist, and the one place a task may claim a fact

**A student tracking NYU's User Experience Design summer program was shown "Review
prerequisite requirements (Algebra 2)".** No such prerequisite is on the program's page or
in its catalog row. That one line is what this subsystem exists to make impossible, and
every choice below traces back to it.

The old design generated tasks in the BROWSER, per student, per add, from a single Gemini
call. Three properties made a fabrication inevitable:

- **It could not read the page.** `/api/messages` attaches exactly one tool, `googleSearch`
  — no `web_fetch` (that lives only on `agents/check_deadlines.py`'s Anthropic path) and no
  `urlContext`. The prompt meanwhile said *"YOU MUST use web_search"* and *"Fetch this
  URL"*. Neither tool existed in the call, so the model could not comply except by
  answering from memory in the voice of a lookup — the identical failure
  `agents/refresh_opportunities.py` already carries a note about.
- **The prompt licensed invention outright**: *"Infer these from the requirements you find
  AND FROM WHAT'S TYPICAL FOR THIS TYPE OF OPPORTUNITY."* "Algebra 2" is precisely what a
  STEM summer program typically requires. The dates in the same response had a never-invent
  rule, a SELF-CHECK block and a server-side write guard; the tasks had none.
- **Nothing could check the answer, and nobody could see it.** `_proxy_to_gemini` returns
  only the response text — grounding and `web_search_requests` are discarded — so no caller
  could tell a researched answer from a recalled one. And a per-student list is seen by
  nobody else, which is why this surfaced as a user report rather than as a metric.

It was also **permanent**: `refreshTrackerDeadlines` and `applyDeadlineCheckToInfo` never
touched `actionItems`, so no code path could ever replace a wrong one. And it rendered as
flat authoritative text, with no equivalent of the dates' `(est.)` marker.

**Tasks are now catalog data**, generated by `agents/generate_action_items.py` and stored on the
opportunity row ([action_items_schema.sql](db/action_items_schema.sql) — another one-time
manual DDL step; until it runs the agent aborts naming it and the app keeps its old
per-student behaviour). They were never personalised — the prompt has always forbidden
anything about the student's own project — so every student was paying for an identical
answer and getting a slightly different one. Moving them makes an add instant, makes the
list consistent, and, the actual reason, **makes it reviewable**.

**The guarantee is in code, not in the prompt** — [page_text.py](wingman/page_text.py), free,
stdlib-only, shared by the batch agent and the on-demand endpoint. Two tests, and there are
two for a reason:

- **`claim_is_supported()`** — every DISTINCTIVE word of the task must appear on the page we
  fetched. Strip the generic application vocabulary and the program's own name (the same
  subtraction `wingman/url_repair.py` makes, for the same reason) and "algebra" is what is left; it
  is not on nyu.edu, so the task cannot be kept. **This runs on EVERY task regardless of
  what the model labelled it.** Checking only the tasks the model *called* page-backed
  leaves the loophole wide open — relabelling an invented prerequisite "generic" would walk
  straight through, and that is the likeliest way this fails once the prompt asks for labels.
- **`quote_is_on_page()`** — a page-backed task must supply a verbatim quote that really is
  on the page. Test 1 says the words exist somewhere; this says the model read a sentence
  rather than assembling a plausible one out of scattered words.

**Failing test 1 DROPS a task; failing test 2 only DEMOTES it to generic.** A task whose
words all check out asserts nothing unsupported — it merely did not prove a specific
sentence — and the card labels it accordingly. Demote where demoting is honest; drop only
what is unsupportable.

- **No fuzzy matching, ever.** `wingman/url_repair.py` measured what a similarity ratio does to
  exactly this judgement (at >= 0.72 it accepted "Summer Research Immersion" as proof of
  "First-year Research Immersion"): the shared words are the category and the differing word
  is the identity, which is backwards for a ratio. Normalizing curly quotes and dashes is
  not fuzzy matching — those characters carry no information — but stemming and similarity
  are. The cost of strictness is false negatives (the page says "Algebra II", the model
  wrote "Algebra 2", so a real task is demoted). That is the right direction to be wrong in.
- **`GENERIC_TOKENS` is the list of words that carry no claim.** Keep it generous, but
  nothing that could be the SUBSTANCE of a requirement may enter it — "algebra", "sat",
  "gpa", "citizen", a digit. `test_action_items.py` runs every line of the built-in generic
  checklists through the verifier against an EMPTY page, so a checklist line that smuggles
  in a claim fails the suite. That test already caught two: "or a coach" and "if one is
  needed" were reworded rather than buying those words a pass.

**A row whose page we cannot fetch costs NOTHING — there is no model call at all**, because
there would be nothing to verify the answer against. Those rows get a per-type generic
checklist built locally. This is not a degradation to apologise for: **the NYU row that
started all this is one of them** (nyu.edu answers our client with a 202 and an empty body),
so the outcome for that exact opportunity is now a free, honest, generic checklist and no
possible Algebra 2. Roughly one page in ten refuses us — `agents/check_links.py` measured ~9% 403s
plus 41 TLS failures on pages a student's browser loads fine — so a fetch failure is a fact
about our HTTP client, **never** about the program.

**`action_items_write_decision()` is the single place that decides what may be written**,
shared by the batch loop and `app/services/action_items.py` so the two cannot drift —
exactly the role `deadline_write_decision()` plays for dates. Four outcomes, and
`action_items_source` on the row names which:

- `page-verified` — page read, model ran, something survived. Writes and **stamps**.
- `page-empty` — page read, nothing program-specific survived. Generic checklist, and it
  **stamps**: a page that states no requirements is a real finding, and not stamping would
  re-bill that row on every run forever.
- `generic-fallback` — page unfetchable. Writes a generic checklist but **does not stamp**,
  so the row stays due and a later run retries; retrying is free, and stamping a transient
  403 would freeze the row for 90 days. It also **refuses to overwrite an existing
  page-verified list** — without that guard one bad-network run replaces every verified
  checklist in the catalog.
- `unparsed` — page read, model output unreadable. Keeps whatever the row has, does not stamp.

**Single call, not two phases, and this is a deliberate divergence from the other agents.**
`agents/check_reviews.py` and `agents/check_deadlines.py` are two-phase because demanding JSON collapses
the SEARCH rate (prose 4/4, JSON 0/4). That reasoning does not transfer: this agent never
searches, it is handed the page. With no search to suppress, a second phase buys nothing and
doubles the per-row cost. Watch the **demotion rate** in the run summary — that is the signal
that would justify adding a prose phase. Do not add it speculatively.

**The two graded samples, and why the first one failed even though verification worked.**
This is the most useful thing in this section: the checks below all passed on run 1 and the
feature was still bad.

| | run 62 (first) | run 63 (after the fix) |
|---|---|---|
| tasks proposed / 20 rows | 37 | 48 |
| kept page-backed | 22 | 30 |
| DROPPED as unsupported | 2 | 3 |
| **demoted (quote not on page)** | **10 (31%)** | **1 (3%)** |
| cost | $0.0355 | $0.0313 |

Run 62 let nothing false through — and produced *"Complete the following Google form."* as a
student's entire checklist, plus "Read frequently asked questions" and "Add course offering
to shopping cart". Those are all **true**: they are on the page. Verification cannot catch
them, because the problem is not truth, it is that they are navigation labels rather than
application steps. Three causes, all fixed:

- **87% of the lines reaching the model were site furniture** (measured on stsci.edu). Told
  to quote verbatim, a model reaches for the most quotable strings available, and on an
  unstripped page those are link labels. `html_to_text` now prefers the page's own
  `<main>`/`<article>` region, strips nav/header/footer/aside/form, dedupes, and drops short
  lines with no digit or colon. Same page: **17,564 chars -> 4,507, 520 lines -> 20, short
  lines 87% -> 15%.** That single change is what took demotions from 31% to 3% — the model
  had been quoting junk, not paraphrasing badly.
- **The prompt conflated the step with the quote**, so the task text came back as a copy of
  whatever was quotable. It now says explicitly that the step is the student's instruction in
  the model's own words and the quote is merely its evidence, and it names link labels,
  headings and button captions as things that are not steps.
- **The safety rules made the model too conservative to be useful** — under two tasks a row,
  several rows with exactly one. `MIN_ITEMS = 3` and `top_up()` guarantee a floor in code,
  padding with generic steps (which assert nothing, so they cannot reintroduce the original
  problem). Verified steps always sort first. **A prompt cannot be relied on for a floor any
  more than for a ceiling.**

`GENERIC_BY_TYPE`/`GENERIC_DEFAULT` are ordered most-universal-first, because `top_up()`
takes from the front. Run 63 put "Draft your personal statement" on an IEEE *conference* row
whose catalog `type` is not `Conference`, so it fell through to the default list — anything
assuming a particular KIND of application belongs at the back, where only a row with nothing
else reaches it.

Cost settled at **~$0.0016/row, ~$2 for a full pass** — a quarter of the first estimate,
because stripping chrome cut input tokens roughly fourfold and unfetchable rows cost nothing.
Unfetchable was **5/20 in run 63** (403, empty-or-JS, not-html), higher than check_links'
~9%; treat one row in five to four as generic-only.

**Measured 2026-08-24, one real row** (UW Madison ALP, `precollege.wisc.edu/alp/`):
**$0.0044**, 3 tasks proposed, 3 page-backed with real quotes, 0 dropped, 0 demoted. That
projects to roughly **$5 for a full catalog pass** — well under the ~$10-15 planned, because
unfetchable rows cost nothing and there is no per-search fee anywhere in this agent.

**Two bugs this shipped with, both caught by an end-to-end run and worth not repeating.**
`estimate_cost()` takes the **usage dict**, not three positional numbers — and a single
`try` wrapping both the API call and the parse turned that plain `TypeError` into a report
of "the model produced unreadable output", *and* discarded the cost of a call that had
already been billed. Cost is now banked immediately after the call, before anything that can
raise, and the parse has its own `except` that names the exception.

**Client side.** `basis` is honoured in exactly one place — `normalizeVerifiedActionItems`,
fed from `GET /api/opportunities/<id>/action-items`. Everything a model produces in the
browser goes through `normalizeUnverifiedActionItems`, which **forces every item to
`generic`** however confidently it was labelled, because nothing on that path has ever seen
the page. A claim is page-backed because a page backed it, never because a model said so.
`isPageBackedTask()` is the only test anywhere, and an item with **no** `basis` (everything
written before 2026-08-24) reads as generic — unknown provenance is not evidence of
provenance, the same rule `ImportantDate.estimated` follows.

Home Base renders the two groups under separate headings ("From the program page" /
"Typical steps — confirm on the site"). Existing students' lists all fall into the second
group on first load, which looks like a downgrade and is not: it is the app stating what it
always was. `dismissed` is a flag rather than a splice — the list is shared and regenerated,
so a deleted task would reappear on the next refresh — and `mergeActionItems` keys on task
TEXT, not id, because the ids are positional and a regenerated list that drops one task
would otherwise hand slot 2's completion to slot 3's task. Same positional-id trap the
Google Calendar sync hit with `importantDates`.

**`eligibility` is now in `OPPORTUNITIES_FIELDS`.** It is maintained by
`agents/refresh_opportunities.py` and was the only curated record of a program's entry requirements
anywhere in the repo, yet it never left the database — so the prompt that was inventing
prerequisites could not see the column that knows them. It reaches the prompt as **context,
never as proof**: both prompts state explicitly that it is our own note rather than the
page, and that it may never be quoted as evidence.

**Activating scraped opportunities** — `GET /api/agents/pending` lists `is_active = false`
rows and `POST /api/agents/pending/activate` (`{ids, active}`) flips them, backing the
console's **Review queue** tab. Nothing in this repo ever sets `is_active = true`
automatically; a scrape always writes inactive and stays that way until a person activates it
here, because the scraper does return plausible-looking rows that are wrong and the catalog is
what students see. Activation takes an explicit id list — there is deliberately no
"activate everything matching" path. `active: false` reverses a mistake without a DB console.
Both paths bust `_opportunities_cache`, or the operator activates a row and then cannot find
it in the app for `OPPORTUNITIES_CACHE_TTL` seconds.

**Rejecting a queued row** — `POST /api/agents/pending/moderate` (`{ids, status}`) writes
`moderation_status` + `reviewed_by`/`reviewed_at`, backing the queue's **Queue / Rejected**
pills. It is deliberately *not* the same call as activate: `is_active` alone cannot say "a
human looked and declined", so without it a junk row sat inactive forever and was re-triaged
every time the queue was opened — the queue only ever grew.

- **Rejecting never deletes.** The row stays in the table so its URL keeps blocking
  re-submission through `url_dedupe`, and moderating it back to `pending_review` undoes the
  decision from the Rejected tab. Deleting is destructive and is still not offered anywhere.
- Rejecting/duplicating also forces `is_active = false`. **Approving does not activate** —
  that stays the separate, explicit Activate button. Activating *does* stamp
  `moderation_status = "approved"`, or an activated row keeps a NULL status and comes back
  round the queue.
- `GET /api/agents/pending?status=` is `queue` (default) / `rejected` / `all`. The queue
  filter must spell out the NULL case (`or=(moderation_status.is.null,…in.(…))`): every
  scraper row has a NULL `moderation_status`, and `NULL NOT IN (…)` is NULL in SQL, so a
  plain `not.in` would empty the queue outright.
- The moderation endpoints degrade if
  [user_submissions_schema.sql](db/user_submissions_schema.sql) has not been run — the list
  falls back to the base select (`moderation_ready: false`, the console hides Reject and
  shows the setup line), and activate drops the approve stamp rather than the whole write.

**Marking a row a duplicate** — the `duplicate` status is its own per-row **Duplicate**
modal, not a variant of the Reject button, because it is the one verdict that needs a
*target*.

- **`duplicate_of` is required for `duplicate` and refused for every other status.** A
  duplicate with no survivor is a rejection wearing a misleading label, and there is then no
  row for a reader to follow to.
- The target is checked: it must exist, must not be the row itself, and **must not itself be
  `rejected`/`duplicate`** — a chain the queue cannot follow, which quietly loses the real
  survivor.
- `duplicate_of` is **always written, never merely set**: restoring or rejecting a row that
  was previously a duplicate clears it, or it keeps naming a survivor for a relationship
  that no longer exists.
- `GET /api/agents/opportunities/search?q=&limit=` finds the survivor. It searches **active
  and inactive rows alike** — the survivor of two queued scrapes is itself still queued —
  by id (exact, tried first) then `name`/`org`/`url` ilike. Commas, parens, `*` and `%` are
  stripped from the term: PostgREST's `or=()` list is comma-separated and paren-delimited,
  so those would parse as syntax rather than as text.
- The modal offers each row's stored `dup_candidates` first, with the reason and confidence
  `url_dedupe` recorded at submission time — that is the whole reason the column is stored.
- **The queue itself also renders them inline** (`dupeBackLinks()`), one line per candidate:
  confidence, the suspected row's name as a link to its own page, its id, and the reason.
  Before that the queue showed only a `2 possible duplicates` count, so finding out *what* a
  row might duplicate meant opening the modal on every row in turn. Confidence drives the
  colour (strong = danger, weak = warn) because the two mean genuinely different things here —
  a strong match is usually a real duplicate and a weak one usually is not, and `url_dedupe`
  emits far more weak ones by design.
- `list_pending_opportunities` returns a `duplicate_targets` map (one extra request, only
  when something is actually marked duplicate) so the queue can name the survivor instead of
  showing a bare id.

**Editing a queued row** — `POST /api/agents/pending/update` `{id, fields}` fixes a row
before anyone activates it, backing the queue's per-row **Edit** modal.

- **Only `is_active = false` rows.** The endpoint reads the row first and refuses a live one.
  Without that guard it is a general catalog editor reachable by id, with no confirmation and
  no audit trail — not what the Activate/Reject buttons around it lead an operator to expect.
- `EDITABLE_OPPORTUNITY_FIELDS` is a **whitelist**, and an unknown key is refused *by name*
  rather than dropped (PostgREST would 400 the whole PATCH on it anyway). Not editable:
  `is_active`/`moderation_status` (those are the buttons), `id`/`source`/`created_at`
  (provenance), `review_*` (`agents/check_reviews.py` owns them), and
  `status`/`important_dates`/`was_estimated`/`dates_last_checked_at` (`agents/check_deadlines.py`
  owns them — a hand-typed date would be overwritten by the next check).
- `type` is validated against `OPPORTUNITY_TYPES`; a typo there makes the row invisible to
  the finder's `KIND_CONFIG` lookup rather than merely ugly. `category` is deliberately *not*
  validated — it is legacy free text holding `COMPETITION`, `SUMMER_PROGRAM`, mixed case.
- `_BASE_PENDING_SELECT` carries every editable column so the modal prefills from the list
  the console already has. **Add a field to the whitelist and it must go in that select too**,
  or the modal opens with it blank and saving writes the blank.
- The client sends **only changed fields**; a no-op save would still bump `updated_at` and
  make the row look freshly touched everywhere else.

**User-submitted opportunities** — the Quest Log's "Add Opportunity" form posts to
`/api/user-submitted-opportunities`, which writes an `is_active = false`,
`source = "user-submitted"` row into the same review queue the scraper feeds.

**That endpoint runs INLINE and returns the resolved catalog id** (2026-08-24). It used to
hand the work to a background thread and answer `{"status": "queued"}` with no id, and the
Quest Log fired it off *after* creating the item — so a hand-added opportunity had nothing to
link to. It carried a local `slugifyTracker()` slug, `/api/opportunities/<slug>/deadline`
404'd forever, and "Check for updates" skipped it while still reporting **"no changes
found"** — the worst possible answer, because it is the one that stops a student checking
themselves. The tracker item is now created under the returned id, so a custom add uses the
same shared, cached deadline check a Fresh Finds add does, on add and on every later refresh.
- An **exact URL duplicate returns the existing row's id** rather than nothing: that row is
  the right thing to track, and it may already carry a verified, cached answer (a free hit).
- The row still lands `is_active = false`. **Being addressable is not being published** — the
  deadline endpoint reads a row by id with no `is_active` filter, while `/api/opportunities`
  still serves only active rows. Activation remains the manual console step.
- Failure is still never the student's problem: an unresolvable submission returns 200 with
  `id: null`, the item stays in the Quest Log under a slug, and the refresh says plainly that
  it cannot be auto-checked instead of claiming it checked it.
Matching lives in **[url_dedupe.py](wingman/url_dedupe.py)**, kept separate from the `normalize_url()`
that `agents/scrape_opportunities.py` / `wingman/dryrun_common.py` / `scripts/one-off/migrate_to_supabase.py` each carry —
those three are deliberately identical to each other and are **not** to be changed to match
this one.

The governing rule, and the catalog measurements that force it:

- **Only "same normalized URL *and* similar name" is ever auto-rejected.** Not URL alone:
  `spicestanford.smapply.io` is the application portal for **six** distinct programs
  (Stanford E-Japan, Sejong Korea Scholars, …) and Girls Who Code's immersion URL backs two.
  Rejecting on URL alone makes those permanently unsubmittable. URL matches but name differs
  → insert with a **strong** flag instead. A wrongly-flagged row costs a reviewer seconds; a
  wrongly-rejected one is lost silently.
- **Never auto-reject on shared domain.** 969 of 1330 rows (73%) sit on a domain shared with
  a *different* opportunity — `nyu.edu` alone hosts 36. Bare "same site" is only emitted as a
  hint when the domain has ≤ `SAME_SITE_MAX_PEERS` existing rows, or it buries the reviewer.
- **Never auto-reject on name similarity.** The scraper used to, at `DEDUP_RATIO = 0.85`.
  Measured against the current catalog that threshold matches **264 pairs, 257 of which have
  different URLs** and are genuinely distinct opportunities — `'Summer Internship'` collides
  with everything, and `'1-Week Medical Academy'` vs `'3-Week Medical Academy'` scores 0.95.
  It was suppressing real opportunities silently and unlogged. `agents/scrape_opportunities.py` now
  calls `find_duplicates()` here like everything else; do not reintroduce a private rule.
- **The stored `url` is never normalized.** 100 catalog rows have case-sensitive paths
  (`…/CNIX.html`) that 404 once folded. Normalization happens only in a throwaway
  `match_key()`. The bug this replaces lowercased the *needle* and compared it with PostgREST
  `eq.` against un-normalized stored values, so dedupe silently failed for the ~44% of rows
  holding an uppercase character or trailing slash — visible in the catalog today as
  `'Clinical Summer Internship'` twice and JSHS three times under three name variants.
- Tracking params (`utm_*`, `fbclid`, …) are stripped but **all other query params are
  preserved** — sites keying programs off `?id=` would otherwise collapse into one row.
- The dedupe read paginates past PostgREST's 1000-row cap (the table is ~1440 rows including
  inactive ones), and if the catalog can't be read it **refuses to insert** rather than
  inserting blind.
- **`apply_url`, `apply_label`, `meta`, `requirements`, `description` and `deadline` are NOT
  columns on `opportunities`.** PostgREST rejects an entire insert on one unknown key, so an
  earlier row builder that set them meant *no user submission ever wrote a row* — the empty
  review queue read as "nobody submitted", not "every insert 400'd". Selecting a nonexistent
  column 400s reads the same way. Confirm a column exists before adding it here; the AI
  extraction returns a wider shape than the catalog stores, and the surplus goes in
  `submission_payload`.

Review-queue columns (`moderation_status`, `submitted_by`, `dup_candidates`, `quality_flags`,
…) come from **[user_submissions_schema.sql](db/user_submissions_schema.sql)** — another one-time
manual DDL step in the Supabase SQL editor. Until it runs the insert is retried with the base
columns only and logs one warning naming the file; submissions still land inactive.
`moderation_status` is **separate from `is_active`** on purpose: the boolean alone cannot say
"a human looked at this and said no", so a rejected row would sit at `is_active = false`
forever and be re-triaged every time the queue is opened. Do **not** name it `state` (that is
the 2-letter US state code) or `review_status` (that is `agents/check_reviews.py`'s org-legitimacy
verdict, already shown to students). The normalized-URL index there is deliberately **not
unique**, for the shared-portal reason above.

**Mailing-list signup** lets a student join one program's mailing list with one tap. It is
split into two halves that are deliberately far apart in trust, and the split *is* the
accuracy design:

- **Discovery** (`agents/find_mailing_lists.py`) writes one **recipe** per opportunity into
  `opportunity_signups` — how to POST a signup for that program — always at
  `status = 'pending_review'`. It cannot verify its own work.
- **Execution** (`subscribe_user_to_list()` in server.py, `POST
  /api/opportunities/<id>/subscribe`) replays a recipe for one real user and **refuses
  anything not promoted to `verified`** by a person in the console's *Mailing lists* tab.
  No AI, no cost, fully replayable. Same shape as `is_active` on scraped rows, for the
  same reason — except here a wrong answer lands in a student's inbox.

- **The success state is `submitted`, never `subscribed`** — in
  [mailing_list_common.py](wingman/mailing_list_common.py), in the `state` column, and in the
  button label. Every supported provider double opt-ins, and we sign the student up with
  **their own address** (there is no wingman-owned relay to read), so nothing in this repo
  can observe the confirmation link being clicked. Claiming otherwise is the exact silent
  failure the feature is measured against; do not tidy the wording up.
- **Four providers only**: Mailchimp, Substack, Kit/ConvertKit, MailerLite — the ones with
  a documented, key-free public endpoint that answers with a readable success/failure.
  Deliberately excluded: **beehiiv** (its embed carries a bot-check token), **bare HTML
  forms** (no success signal; the failure mode is a 200 that did nothing), **portals**
  (that is account creation) and **anything CAPTCHA-guarded**. Those all degrade to the
  "Open signup page" handoff, which is the honest answer. `mailto:` "email us to join"
  programs were considered and dropped — they need a sending identity we do not have.
- **`scope_evidence` is the field that matters** on a recipe: the quote proving the form
  belongs to *this program*. 73% of the catalog shares a domain with a different
  opportunity and one SMApply portal backs six programs, so "a newsletter form exists on
  this page" proves nothing. The console shows it as a wide column and confidence as a
  narrow one on purpose — confidence is the model grading its own work, which is the thing
  being checked. There is deliberately **no** "verify everything above confidence X".
- **Consent is re-checked server-side**, like the signup checkboxes in `handle_register`:
  a per-list tap, an explicitly confirmed email address, and a ticked box. There is no
  bulk "subscribe me to all results" path and there must not be one — most users here are
  minors, and bulk sending is also how the outbound address gets blacklisted.
  `legal/terms.md` §14A and `legal/privacy.md` §6A cover it (re-run `agents/build_legal.py`);
  `TERMS_VERSION` moved to `2026-08-22` for it.
- The email is **prefilled from the account but editable**. A Google signup often carries a
  school address that blocks outside mail, and making the address an explicit choice is
  better consent hygiene than silently using whatever is on file. The address actually used
  is stored per attempt — "which address did you sign up with" is the first question when a
  student says the mail never arrived.
- Attempts are one row per `(userid, opportunity_id)` in `mailing_list_subscriptions`, and
  a repeat tap **updates** it. The honest question is "did this student sign up", not "how
  many times did they press the button". A per-user throttle of 10/hour sits on top — not
  for our cost (a subscribe is free) but so a stuck button does not look like an attack to
  someone else's mail provider.
- If the attempt cannot be recorded the response carries **`recorded: false`**: the signup
  went out, but the button will reset and the Quest Log cannot list it. Surfaced rather
  than hidden, because it reads as a mystery otherwise.
- **[mailing_list_schema.sql](db/mailing_list_schema.sql)** is another one-time manual DDL
  step. Unlike the other schema files it ends with an **ALTER block**, because
  `create table if not exists` is a no-op against a table that already exists in an older
  shape — and PostgREST 400s an entire insert on one unknown key, so a single missing
  column means the finder writes *nothing* and the queue reads as "the agent found
  nothing" rather than "every insert failed". **Add a column to a CREATE there and you
  must add it to the ALTER block too.** Until the file is (re-)run, discovery aborts
  naming it, the console tab shows the setup step, and every opportunity degrades to the
  handoff — which is the correct behaviour with no verified recipe.
- **[grade_mailing_lists.py](agents/grade_mailing_lists.py)** is the measuring instrument, and is
  free. `--sample` picks a deterministic, deliberately adversarial 10-row stratified
  sample (it includes shared-portal rows on purpose); `--worksheet` turns the finder's
  output into a form a human fills in by opening each page; `--score` computes
  **precision** (correct / proposed) and **recall** (correct / lists a human found).
  Recall can only be measured by a person, because the finder cannot report the lists it
  missed. `--verify` additionally POSTs each recipe for real using
  `MAILING_LIST_TEST_EMAIL`, which measures execution separately from discovery. There is
  **no confirmed-delivery metric and there cannot be one** under the current design — do
  not add a fourth number that quietly assumes it.

**The console's spend chart draws every key in the series, not the five with cards.**
`get_agents_summary()` returns `series_keys` (each labelled and grouped `agent`/`app`/
`other`) and folds on-demand deadline spend in via `fetch_deadline_check_cost_by_day()`.
Iterating the card list instead dropped the interactive rollups, the deadline checks (which
are in `deadline_check_log`, not `agent_runs`, so they were never in the series at all) and
any agent run from a script with no card — $4.84 of a $14.24 KPI printed ten pixels above
the bars.

The series is banded, not one key per `agent_runs.agent` literal. Each of the five console
agents keeps its own band because each is something an operator deliberately runs.
Everything else collapses into two, because splitting them answered a question nobody asks
of this chart:
- **`end_user` — "End User Initiated"**: both `interactive_*` rollups plus on-demand
  deadline checks. Spend the users caused, which nobody starts from this console. The
  per-user decomposition of exactly this money is the Cost per user tab.
- **`other` — "Other"**: standalone scripts with no card — `scripts/one-off/backfill_subject_tags.py` (a
  completed one-off) and `agents/find_contact_emails.py` (a full-catalog pass; an ordinary
  `agents/refresh_opportunities.py` run already resolves `contact_email` per row, so this is only
  for an initial backfill or a `--force` re-check).

Each band carries a `note` the console prints under the legend, and each per-day entry
carries `parts` so a collapsed band still names its components in the bar tooltip —
grouping hides nothing, it just stops the legend reading like an implementation detail. The chart hint now
states the charted total and flags any gap rather than letting two adjacent figures
disagree in silence. Note `fetch_deadline_check_cost(start, end)` **accepted and ignored
`end`** until 2026-08-22, which made the summary's previous-period deadline cost include
the current period too, so every KPI delta touching deadline spend was wrong.

**A committed snapshot is marked as committed.** `dryrun_common._pending_count()` is
computed purely from the file on disk and is never compared against the database, so a
snapshot read identically before and after being applied. `annotate_committed_snapshots()`
recovers the link from the `notes` string the commit already writes (`Committed dry-run
snapshot <file>`) rather than adding a column — no migration, and every historical commit
is recognised retroactively. The button becomes **Commit again…** and says when it was
applied. This is harmless for the scraper (inserts dedupe on normalized URL) but not for
the three PATCH agents, where a second commit re-applies days-old field values over
whatever has changed since.

**The scraper is a TWO-PHASE call per seed, and the split is the accuracy design.**

    Phase 1 (research)  prose out, googleSearch on  -> keeps groundingChunks/groundingSupports
    Phase 2 (extract)   phase 1 notes + RESOLVED urls in -> strict JSON out, no search

Do not collapse these back into one call. Gemini does not put a retrieved URL in its answer
text: `groundingChunks[].web.uri` is the only place the real URL exists, and a JSON-only
answer does not carry it back. When the model answers without searching it writes URLs from
memory, and they come out with the **right host and a path off by one segment** — measured,
**30 of 116 URLs in the 2026-08-20 batch were hard 404s (26%)**, every one a constructed deep
path and never a bare domain. Head to head, 4/4 model-typed URLs 404'd where 4/4
grounding-resolved URLs returned 200, including the catalog's own dead
`training.nih.gov/research-training/sip/` against the real `…/research-training/pb/sip/`.
Phase 2 needs no search, so a strict output format is free there.

- **`wingman/url_validate.py`** does both halves and is entirely free — no API calls, no keys.
  `resolve_grounding_chunks()` follows the one redirect hop from
  `vertexaisearch.cloud.google.com/grounding-api-redirect/…` to the real page (`web.title` is
  only a bare domain and `web.domain` does not exist on `v1beta/generateContent`, so the hop
  is the only way). `support_urls_by_span()` uses `groundingSupports` to tie a source to one
  opportunity's own span — without it you only know which pages were consulted for the whole
  answer, which cannot say which URL belongs to which of eight results. `check_urls()`
  separates **dead** (404/410) from **unverified** (403/429/timeout); 403 is ~9% on the
  existing catalog, so treating it as death would throw away good rows.
- `spans_for_name()` matches on significant words, not exact substring: a candidate named
  "NASA Internship Programs (Summer 2027)" whose span reads "NASA Internship Programs:"
  otherwise keeps its remembered URL while the retrieved one sits unused.
- **`call_gemini(..., return_grounding=True)`** returns a third element; the default stays a
  2-tuple so the five other call sites are untouched. `usage["server_tool_use"]` now also
  carries **`web_search_queries`** — the actual query strings, which were reduced to a
  `len()` until 2026-08-23, meaning nobody could see or tune what was being searched.
- Every seed's raw notes, queries, resolved URLs and candidates are written to
  `agent_logs/scraper_<stamp>_seed<id>.json`. No run before this kept any, which is why past
  failures ("5 candidates, all invalid, $0.09", twice) were undiagnosable afterwards.

**Silent search cannot be fixed by prompting — retry instead.** Gemini decides per call
whether to search, non-deterministically: seed 51 was run twice with an *identical* command
and returned 0 searches once and 6 the next time. Phase 1 therefore retries once on a
zero-search response (cheap — a silent call pays no $0.014/search fee) and flags whatever is
still silent. See `wingman/gemini_common.py`'s FIFTH and SIXTH findings. Do not try to force it; the
THIRD finding's conclusion that no reliable forcing mechanism exists is **correct**.

**Discard almost nothing, explain everything.** Every row lands `is_active=false` with
`moderation_status='pending_review'` and short `quality_flags` saying *what to go and check*.
Only two things never reach the table: an **exact duplicate** (same normalized URL *and*
matching name, via `url_dedupe.find_duplicates()`), and a candidate with **no URL** — the URL
is the row's identity. Both are written to the review snapshot with their raw JSON, so
nothing vanishes silently. The snapshot is now `{"inserted": [...], "rejected": [...]}` rather
than a bare list; `wingman/dryrun_common.py` reads these files, so **check it if you change that
shape**. Flags are the `FLAG_*` constants in `agents/scrape_opportunities.py` and must stay short —
the console renders each as a pill truncated at 90 characters (with the full text in a
`title=` tooltip).

**A live URL is not a correct URL — the second failure mode.** Fixing the 26% dead-link rate
did not finish the job, it changed the shape of the problem. Auditing all 166 rows of the
2026-08-23 batch by fetching each page and comparing its `<title>` to the row found **10 (6%)
whose URL was a third-party SEO round-up** that merely mentions the program
(`ladderinternships.com/…/19-selective-internships…` stored for Stanford AIMI,
`indigoresearch.org/blog/…` for NASA OSTEM). **Every other check passes those**: they return
200, so `check_urls` is happy; they have a deep path, so `is_bare_domain` is happy; the path
is not `/faq/`, so `is_low_value_path` is happy. `url_validate.domain_matches_org()` is the
only signal that catches them, and `FLAG_OFFSITE` is what it writes.

Half of them were `reconcile_url`'s own doing: when grounding attributed a span it took
`span_urls[0]` **without first asking whether the model's URL was itself one of the retrieved
pages**. It was, in 33 of the 166 rows — `aimi.stanford.edu/education/summer-research-internship`,
`stemgateway.nasa.gov/…/high-school-internships` and `nhsjs.com/submit-your-work/` were each
discarded for a blog. That check is now hoisted above the span fallback: a URL the search
actually returned is verified no matter which sentence cited it. Replayed over the whole
batch the hoist changes 33 rows, improves 5 measurably and **worsens none**. The other half
the model simply typed itself, which no ranking can repair — those get the flag.

`domain_matches_org()` matches by **substring against each domain label**, not by whole
tokens, because a domain label is words run together with no separator: an exact-token rule
read `idyllwildarts.org`, `tellurideassociation.org` and `artandwriting.org` as unrelated to
their own owners and fired on **58%** of the batch against 16% for the substring rule (which
still catches 10/10). Abbreviations count in both directions (`colum.edu` for Columbia
College, the `umich`/`upenn` shape), as do acronyms and initials — and initials are taken
with parentheticals stripped, or "Fermi National Accelerator Laboratory (Fermilab)" yields
`fnalf` and misses `fnal.gov`. Being generous is the point: a wrong "unrelated" flags a good
row, and this is a review hint, never a rejection.

**A seed is an ANGLE — nothing else.** It used to be a `(category, angle)` pair; the category
was dropped 2026-08-23. It was never interpolated into either prompt, so it never influenced
the search; nothing in the student-facing app reads the `opportunities.category` column it was
written to (nullable, already NULL on 1139 of 1440 rows, and `preFilter` keys off `type`); and
its one live use was a silent `type` fallback. Measured across 238 scraper rows, the model's
type disagreed with the seed's category **27% of the time overall and 65% for Research seeds**
— so the fallback fired a guess that was usually wrong, at exactly the moment (a malformed
response) when guessing is least defensible. An invalid type is now a review flag.
`scraper_seeds.category` still exists because it is `not null` and dropping it needs DDL this
repo cannot run: `create_seed()` writes `SEED_CATEGORY_PLACEHOLDER` to satisfy the constraint,
`SEED_SELECT` does not read it, and the console no longer offers it.

**Scraper search angles** ("seeds") live in a Supabase `scraper_seeds` table
([scraper_seeds_schema.sql](db/scraper_seeds_schema.sql)), editable from the admin console, with
lifetime per-angle yield totals (`total_added`, `total_cost`, …) so unproductive angles can be
found and retired. `wingman/seeds_common.py` loads them and falls back to the hardcoded
`NATIONAL_SEEDS`/`SEATTLE_SEEDS` literals in `agents/scrape_opportunities.py` if the table is empty or
unreachable — it logs loudly which source it used.
Yield totals are credited by `record_seed_result()`, which **re-reads the seed immediately
before adding** rather than adding to the copy loaded when the run began: PostgREST cannot
do `SET total = total + n` without a stored function (DDL this repo cannot run), so it
stays read-modify-write, but the window shrinks from the length of a 110-minute pass to one
round trip — the stale-snapshot version silently discarded any console edit made mid-run.
**Dry runs now credit `found`/`dupes`/`cost` but never `added`.** This is a deliberate
exception to "--dry-run skips DB writes", of the same kind `agent_runs` already makes: a dry
run spends real money, so its cost belongs against the angle that spent it. Crediting nothing
at all is why every angle still read zero after a month — the whole retire-bad-angles feature
had no data. `added` stays 0 because nothing was actually inserted. Fallback angles have no id
and still credit nothing; `seed_yield_state()` on `GET /api/seeds` says which case a grid of
zeros is, because it otherwise reads as a dead feature. Select angles with `--seed-ids` (stable);
`--seed-indices` is deprecated because positions shift whenever a seed is added or deleted.

**Admin console** is [admin_console.html](admin_console.html), served at `/admin`. It and every
`/api/agents/*` and `/api/seeds` route are **restricted to localhost** (`_require_local()`), since
the server binds all interfaces and these routes spend money. Agent output is streamed line by
line to a ring buffer and to `agent_logs/<agent>_<stamp>.log`, readable via
`GET /api/agents/log?agent=&since=`.

**Restarting the dev server**: use [restart_server.ps1](restart_server.ps1), never Bash `&`.
It kills whoever actually owns port 8000 (via `Get-NetTCPConnection`, not `pkill` — Git Bash
cannot see native Windows python processes), verifies the port is free, and records the *listening*
PID. The WindowsApps `python.exe` is an alias shim whose child holds the socket, so the PID
`Start-Process` returns is not the server. Getting this wrong once left 26 zombie processes
serving stale code for a whole session.

**Backend (`server.py`)** is a `ThreadingHTTPServer` with a `GET /api/opportunities` route
plus five POST endpoints:
- `/api/messages` — proxies to the real Gemini API (model `gemini-3.5-flash-lite`, pinned
  as `MESSAGES_MODEL` in server.py, see `callGemini()` in script.js) when `GEMINI_API_KEY`
  is set, otherwise fabricates a mock response by pattern-matching the `system` prompt text
  (`generate_mock_text`). Client sends a plain `{system, userContent, useWebSearch}` body
  (not Anthropic's content-block/messages envelope); server.py reuses `gemini_common.
  call_gemini()` — the same request-building, forced-search nudge, and thinking-budget
  handling used by the offline batch scripts (`agents/check_deadlines.py`/`agents/check_reviews.py`) —
  and re-wraps the result into a `{content:[{type:"text",text:...}]}` envelope so both live
  and mock responses parse the same way client-side. When adding a new AI-backed feature,
  add a matching mock branch here so the app stays usable without a live key.
- `/api/messages-claude` — the one deliberate holdout from the Gemini migration: the
  profile chat's `profileChatNextQuestion`/`profileChatStarterQuestionsFromAI` (see
  `callClaude()` in script.js) still run on the real Anthropic API (model
  `claude-haiku-4-5-20251001`, pinned as `CLAUDE_MODEL` in server.py) when
  `ANTHROPIC_API_KEY` is set. Client sends the same plain `{system, userContent,
  useWebSearch}` shape as `callGemini()`; `proxy_to_anthropic()` translates that into
  Anthropic's content-block/messages envelope server-side, so the client stays
  backend-agnostic regardless of which endpoint it's calling. The client may also send
  `maxTokens`, clamped server-side into `[CLAUDE_MAX_TOKENS, CLAUDE_MAX_TOKENS_CEILING]`
  by `_clamped_max_tokens()`. That exists for **profile synthesis**, which rewrites the
  whole profile on every merge and so produces a longer answer as the profile grows — at
  the flat 1000-token default it was silently cut off mid-sentence, and Anthropic hands
  back the partial text looking like a normal, complete response. `callClaudeDetailed()`
  in script.js surfaces `stop_reason` so `synthesizeProfile()` can retry at the ceiling
  and, if it is *still* truncated, throw rather than save a fragment over a complete
  profile. There is deliberately no word limit on the profile in the prompt, in storage,
  or in the display. Note **which end of the profile a truncation eats**: the prompt
  emits general paragraphs first, then `Passion Project: ` paragraphs, then
  `Research Project: ` ones, so a response that ran out of budget always lost its tail —
  i.e. the projects — which is how this surfaced ("passion projects cut off") rather than
  as a visibly half-written profile. A profile damaged that way does **not** heal on its
  own: `existing` is handed to the next merge as ground truth under "do not drop details
  from the current profile", so the fragment is copied forward indefinitely, and the card
  is read-only apart from Clear profile. The system prompt therefore carries a repair
  clause (finish the thought only if the rest of the profile makes it unambiguous,
  otherwise drop it, never invent), which fixes it on the next ordinary merge, and
  `profileHasTruncatedTail()` / `repairProfile()` back a **Tidy it up** button that runs
  that pass on its own for students who don't chat again.
- `/api/register`, `/api/login`, `/api/data/save`, `/api/data/load` — backed by a Supabase
  `users` table (`get_user`/`create_user`/`update_user_data` in server.py), queried with the
  `SUPABASE_SERVICE_KEY` (service_role — bypasses RLS). That table has RLS **enabled with no
  policies at all**, so the anon key gets zero access to it; only `server.py`'s service-role
  calls can read/write it, unlike the public read-only `opportunities` table. Client hashes
  passwords with SHA-256 (`crypto.subtle.digest`) before sending; the server only ever
  stores/sees the hash — no salting, no HTTPS enforcement, no rate limiting (fine for a
  prototype, not production-grade). `scripts/one-off/migrate_users_to_supabase.py` was the one-off script that
  moved the old flat-file `users_db.json` into this table — logic/shape is otherwise
  unchanged, this was a storage-backend swap only.

**Subscription, trial, and signup consent.** Every account starts a **7-day free trial**
that converts to a **$9.99/month** Stripe plan. `wingman/subscription_common.py` talks to Stripe
over raw HTTP (no SDK, matching the stdlib-only philosophy) and holds the `PROMO_CODES`
dict; four POST endpoints (`/api/subscription/status|checkout|cancel|validate-promo`) sit
in `server.py`.

- The `users` table needs the columns in **[subscription_schema.sql](db/subscription_schema.sql)**
  — a one-time manual DDL step in the Supabase SQL editor, same as `db/user_costs_schema.sql`.
  PostgREST has no DDL endpoint, so nothing in this repo can run it. **Until it runs,
  registration is down**: `create_user()` writes all of those columns and Postgres rejects
  the insert entirely if one is missing. `/api/register` detects that case (PostgREST
  reports it as `42703` on reads but `PGRST204` on writes — both are checked) and returns a
  **503 naming the file**, rather than the bare `502 Could not reach Supabase` that cost a
  session of debugging.
- **`subscription_state(record)` in `server.py` is the single source of truth** for whether
  an account may use the app. The client paywall and the server-side gate both derive from
  it, so they cannot disagree. `has_access` is: `active` → yes; `trial` → yes until
  `trial_ends_at`; `beta` → yes until `subscription_end_at`; `canceled` → yes until
  `subscription_end_at` (cancelling is cancel-at-period-end, they paid for that time);
  anything else → no.
- **Promo codes come in two incompatible kinds**, keyed by `kind` in `PROMO_CODES`.
  A **`grant`** code (`BETAUSER` → status `beta`, +7 days) is redeemed immediately against
  the user's row via `POST /api/subscription/redeem-promo`; it touches no Stripe and works
  with Stripe unconfigured. A **`checkout`** code (`FREEMONTH`, `WELCOME10`) is a discount
  that only exists once Stripe is involved and is passed to the Checkout Session. Redeeming
  a checkout code through the grant endpoint is refused — it would burn the code for
  nothing. `validate-promo` returns `kind` so the client knows which path to take.
  Grants extend from `max(now, current end)`, so they **add** to a running trial rather
  than replacing it, and `GRANTABLE_STATUSES` stops a typo'd status from writing a value
  `subscription_state()` has no branch for (which would read as no access and lock out the
  user who just redeemed).
- `promo_codes_used` is what makes a code one-per-account. Before the redeem endpoint
  existed nothing ever wrote that column, so "one-time use" was unenforced.
- **A `trial` row with a NULL `trial_ends_at` means "clock not started", not "expired".**
  That is every account predating the migration. `ensure_trial_started()` stamps a real
  window on first sign-in. Reading NULL as expired — which `is_trial_expired(None)` does if
  you take it literally — would paywall every existing user the moment the migration lands.
- **Enforcement is in both halves, and as of 2026-08-24 it covers the whole app rather
  than only the calls that cost money.** It used to be the four paid endpoints, on the
  reasoning that spend was what needed protecting — but a lapsed account could still open
  Home Base, read and write its profile and Quest Log, and browse the catalog, so "your
  trial has ended" meant nothing it could see. Both halves derive from the same
  `subscription_state()`, so they cannot disagree about who is blocked.
  - **Server (`app/deps.py`) is the real control.** `require_subscription` wraps
    `get_current_user` (missing token → 401, lapsed account → **402** whose body is
    `subscription_block_reason()`'s message); `optional_subscribed_user` is the soft form
    for routes legitimately reachable signed-out — an unidentified caller is never blocked
    (the same residual the cost attribution reports as unattributed), a caller who
    identifies as a lapsed account is. Gated: `/api/data/{save,load}`,
    `/api/account/location`, `/api/opportunities` (soft) and its deadline check,
    `/api/mailing-list/*`, `/api/calendar/sync`, `/api/user-submitted-opportunities`
    (soft). The AI proxies and the resume/LinkedIn imports keep their **inline**
    `subscription_block_reason()` call — the proxies must stay reachable signed-out for
    mock mode. `test_subscription_gate.py` asserts the wiring route by route, including
    the routes that must stay UNGATED: every `/api/subscription/*` path, login/register
    and the token lifecycle. **A paywall you cannot pay through is a lockout.**
  - **Client**: `(app)/_layout.tsx` is the paywall. `has_access === false` (an explicit
    false — an older cached session leaves it undefined and must not be locked out)
    redirects every route to Manage Plan, and `NavBar locked` hides the four tabs, which
    would otherwise each bounce straight back and read as the app being broken. The
    account drawer stays: signing out must always be reachable.
  - **The block arrives without a reload.** `httpClient` treats **every 402 as the same
    thing** in one place — it flips the cached identity to `has_access:false`, persists it
    and fires `onUserChanged`, which `AuthContext` turns into `setUser`. So a trial that
    lapses mid-session moves the student to the paywall instead of leaving them on a
    screen whose every request now fails. `applyTokens` (i.e. every background refresh)
    and `subscriptionStatus()` notify through the same path, which is what **lifts** the
    block the moment a grant promo code is redeemed.
  - **Nothing is deleted and nothing is charged for the block.** A gated route refuses
    before touching Supabase, so the row is untouched and comes straight back on
    resubscribe; the paywall copy says so, because "has your data been deleted" is the
    first thing a student assumes.
  - A Supabase failure still **fails open** rather than locking out every paying user, and
    calls with no identified user are still not blocked.
- **Both `userid` and `email` must be unique across all accounts**, case-insensitively.
  `users` has no is_active/deleted column, so every row is a live account and any match is
  a real conflict. `handle_register()` checks both up front and names the field that
  clashed — Postgres alone returns a bare 409 that can't say which. Uniqueness is by
  **normalization, not `ILIKE`**: `userid` is lowercased everywhere already, and
  `normalize_email()` lowercases/trims on write so an exact `eq.` match *is* the
  case-insensitive lookup. Don't switch these to `ilike` — `_` is a legitimate email
  character and an ILIKE wildcard, so it would over-match and refuse valid signups.
  The check and the INSERT are two round-trips, so simultaneous signups can still race
  past it; the unique index in **[users_email_unique_schema.sql](db/users_email_unique_schema.sql)**
  is what actually closes that, and `EMAIL_UNIQUE_INDEX` in `server.py` must keep matching
  the index name there or an email collision gets reported as a userid collision.
- **Signup consent**: three checkboxes (18-or-older; if not, parent/guardian permission
  per Terms §2; and accepting the Terms + Privacy Policy). `handle_register()` re-checks all
  three server-side and refuses the account otherwise. What was accepted is stamped on the
  row (`is_adult`, `parental_consent`, `terms_accepted_at`, `privacy_accepted_at`,
  `terms_version`). **Bump `TERMS_VERSION` in `server.py` whenever `legal/*.md` changes
  materially** or old and new acceptances become indistinguishable.
- **The legal documents are generated.** `legal/terms.md` and `legal/privacy.md` are the
  source of record; `public/terms.html` / `public/privacy.html` are built from them by
  **`agents/build_legal.py`** and must not be hand-edited — re-run it after any edit under
  `legal/`. Note Terms §3 still states the beta is free of charge, which the $9.99 plan
  contradicts.
- Stripe is **not configured**: `STRIPE_API_KEY`/`STRIPE_PRICE_ID` are absent from `.env`,
  so `/api/subscription/checkout` errors and the subscription screen's Upgrade button
  surfaces that answer rather than redirecting. Everything upstream of the payment itself
  (trial, gating, promo validation + redemption, cancel bookkeeping) works without it.

**Two persistence layers on the client**, easy to conflate:
1. **Tokens only** — `frontend/src/api/tokenStore.ts`: `expo-secure-store` on native,
   `localStorage` on web, under `wingman.access_token` / `wingman.refresh_token`. Nothing
   else is cached client-side. (The old app's `window.storage` shim is gone; it was never
   defined in this repo and silently no-opped outside its host runtime.)
2. **Everything else is server state** — the Supabase `users` table via `/api/register`,
   `/api/login`, `/api/data/save`, `/api/data/load`. Three keys carry the whole app:
   `student-profile` (`{synthesized, updatedAt, chatRounds, basics, filterTags,
   starterPool, filterValues}`), `hs-tracker-data` (a JSON **string** of the 6-bucket
   object), `hs-tracker-saved` (`{id: bool}` saved-for-later flags). These key names and
   shapes were kept byte-identical through the RN rewrite so a student's data survived the
   cutover — do not "clean them up".

**App-open latency: four serialized round trips, now one.** Home Base measured 5-6s to
first paint in production. None of it was computation — it was the shape of the boot.
`initAuth` awaited `/api/auth/refresh`, and only then did the screen fire
`hs-tracker-data`, `hs-tracker-saved` and `student-profile` as three separate
`/api/data/load` calls. Four causes compounded, and all four are fixed (2026-08-24):

- **Blocking Supabase IO inside `async def` handlers meant the server did one thing at a
  time.** `app/core._users_request` is blocking `urllib`, and FastAPI runs an `async def`
  endpoint ON the event loop — so each Supabase read froze the whole process, including
  requests already in flight. Measured: three `/api/data/load` calls that take 164ms each
  alone took **660ms wall** when issued together. Every route that touches Supabase is now
  a plain `def`, which FastAPI runs in a threadpool; the same three now take **223ms**.
  The only thing forcing them async was `await request.body()`, so that moved into the
  `json_body` / `raw_body` **dependencies** in `app/deps.py`. **A handler that awaits
  anything in its own body must stay `async def`** — that is the line, and adding a new
  Supabase-touching route as `async def` silently reintroduces this.
- **One row, fetched once per key.** Every stored key lives in the same `data` jsonb on
  the same row, so three keys cost three full-row reads. `/api/data/load` now also accepts
  `{keys: [...]}` and answers `{values: {...}}`. The single-key `{key}` → `{value}` form is
  unchanged and must stay — a browser running a stale bundle still uses it.
- **The client batches, so no screen had to change.** `httpClient.loadData` coalesces every
  call made in one tick into a single `{keys}` request (a microtask flush, not a timer —
  an async function runs to its first `await` synchronously, so Home Base's three, the
  Quest Log's two and the calendar sweep's two already all land in the same tick). Put the
  batching here rather than at the call sites and there is nothing to remember to do.
- **The identity is cached, so refresh left the critical path.** `SessionUser` only ever
  came back in a login/refresh response, which is why startup had to await one before it
  could even choose a screen. It is now persisted beside the tokens (`wingman.session_user`)
  and `initAuth` boots from it when the stored access token's `exp` has not passed,
  revalidating in the background. **This grants nothing** — the token is still verified
  server-side on the next call — and a failed revalidation calls `forgetSession`, which
  fires `onSessionLost`, which `AuthContext` turns into a redirect to `/login`.
- **`peekData` / `peekTrackerData` are render accelerators, never a source of truth.**
  expo-router remounts a screen on every visit, so Home Base showed a full-screen spinner
  on each tab switch for a round trip it had already paid for. It now seeds its state from
  the last loaded values and still runs the fetch. `saveData` writes through only *after*
  the server accepts, so a failed save cannot seed the cache; `forgetSession` clears it so
  the next account starts clean.
- **Reads select what they need.** `get_user_account()` is every column except `data`
  (identity/token/subscription — what refresh and the paywall gate read) and
  `get_user_data()` is only `data`. `get_user()` (`select=*`) remains for the paths that
  genuinely want both. PostgREST has no "everything but X", so the account list is explicit
  and **falls back to `*` once and latches** if a column has not been migrated in — the
  same 400-on-unknown-column trap `db/mailing_list_schema.sql` carries, except here it would
  break sign-in.

Measured end to end on `:8000` (warm, exported bundle): first paint of Home Base went from
**781ms across 4 serialized round trips to 353ms across 1** (refresh now overlaps rather
than blocking). The remaining fixed cost is the 1.9MB unsplit `entry-*.js`, which is a
separate problem.

**The load-time font flash: `no-store` on content-hashed assets, and a gate that never
gated.** The app painted its first text in Times New Roman and snapped to Space Grotesk /
Plus Jakarta Sans a beat later, on every visit. Three things had to be true at once, and
two of them looked correct:

- **`app/main.py`'s `no_cache` middleware applied `no-store` to EVERYTHING**, inherited from
  the SPA days when `script.js` carried no content hash. `expo export -p web` writes seven
  `<link rel="preload" as="font" crossorigin>` tags into the document head and inlines the
  matching `@font-face` rules — that part was already right — but **`no-store` forbids the
  browser from KEEPING the response**, so the preload could not be reused and the real
  @font-face fetch started over from scratch once the 1.9MB bundle had evaluated. Measured
  on production 2026-08-24: preloads complete by 566ms, the same four files re-downloaded
  1234ms->1466ms at ~43KB each, i.e. ~600KB of font paid for twice and the fonts arriving
  *after* the text they were for. And because nothing was cached across loads either, it
  reproduced on every single visit rather than only the first.
  `_is_immutable_asset()` now exempts `/assets/` + `/_expo/static/` — but **only with a
  content hash present** (`.<hash>.` for assets, `-<hash>.` for the bundle, `@2x` allowed
  after): `_expo/.routes.json` sits under the same root without one, and a year-long
  `immutable` on a reusable URL is not recoverable server-side. HTML shells stay `no-store`
  — the shell is what names the current bundle hash, so a stale shell pins a stale
  everything. `test_cache_headers.py` pins the split.
- **`useFonts` is not a load gate on web**, so the root layout's spinner never did what its
  comment claimed. expo-font's web `isLoaded()` asks only whether the @font-face RULE
  exists (`build/ExpoFontLoader.web.js` -> `getFontFaceRulesMatchingResource`), and
  `output: "static"` ships all seven rules inline in the head — so expo-font's own
  `useState(isMapLoaded(map))` is already `true` on the first client render, before a byte
  of any .ttf is fetched. A font file is fetched only when rendered text matches it, so the
  fetch does not even *start* until after that first paint.
  - **Holding the render on `document.fonts.load()` was tried and reverted.** It works, and
    it costs a hydration mismatch: static pre-rendering emits the app (or the auth-gate
    spinner) into the HTML, while a font-gated client would render the root spinner —
    different trees. Verified 2026-08-24 by building both ways: the gated bundle logs
    **React error #418** on every cold load and React discards the server HTML, which on a
    pre-rendered route like `/landing` is a worse flash than the one being fixed. Seeding
    the state from `document.fonts.check()` does not save it — a preloaded font is in the
    HTTP cache but its FontFace is still `unloaded`, so `check()` is false exactly when the
    gate would fire. **Do not re-add a font gate to `app/_layout.tsx`.**
- **RN-web writes `fontFamily` into CSS verbatim, and the names had no fallback stack** —
  `font-family: SpaceGrotesk_700Bold` alone, so unresolved text fell to the browser's
  default *serif*. That is why the flash was Times New Roman rather than merely a different
  sans, and it is the half that made it obvious. `fonts` in `src/ui/theme.ts` now appends
  `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` **on web only** (a
  comma-separated stack is not a valid native `fontFamily`). Each weight is its own family
  here, so every one of the seven needs its own stack.

Measured after, on a warm load: **all seven fonts serve from cache at 0 bytes / 0ms and the
CSS-initiated re-fetch is gone entirely**; every visible text node under `#root` computes to
a brand family (the residual Times New Roman on `<html>`/`<head>`/`<title>` is the UA default
on elements that render no text). Do not "simplify" the theme stacks away on the grounds
that the caching fix already closes the window — the stack is what keeps a slow-network
residual unremarkable instead of glaring.

**AI call flow**: `httpClient.callGemini(system, userContent, useWebSearch)` POSTs to
`/api/messages` and returns cleaned text; `extractJSON()` (`src/lib/extractJSON.ts`) pulls a
JSON value out of it by brace/bracket-depth scanning (tolerates trailing commentary, repairs
truncated responses), and `callGeminiJSON` (`src/lib/aiJson.ts`) retries the whole call once
on a parse failure. Every model-touching module takes the call function as a **parameter**
rather than importing the client — that is what keeps `src/lib/*` pure and testable, so pass
`httpClient.callGemini` at the call site. Callers: `inferSubjects`, `rankCandidates`,
`extractProfileBasics` (ranking.ts), `extractTrackerInfo` (tracker.ts), and the finder's
profile-tag scorer. The profile chat is the one Anthropic holdout —
`profileChatNextQuestion` / `profileChatStarterQuestionsFromAI` / `synthesizeProfile` /
`repairProfileText` use `callClaude`/`callClaudeDetailed` against `/api/messages-claude`
(`claude-haiku-4-5-20251001`); `callClaudeDetailed` additionally surfaces `stop_reason` so
synthesis can detect truncation and retry at the higher ceiling.

**The profile chat's two halves are cached asymmetrically, and the asymmetry is the point.**
Openers are cached; follow-ups are deliberately not. Both are `callClaude()`, so the
difference is not visible from the call sites — only from what each one depends on.

- **Openers** (the 3 questions offered when the drawer opens) depend on the profile text and
  nothing else. There is no conversation yet for them to react to, which is exactly what
  makes them safe to cache. They live in a `starterPool` slot in `PROFILE_DERIVED_SLOTS`
  alongside `filterValues`/`filterTags`/`basics`: **10** questions generated per profile
  "version", from which each drawer open serves a rotating window of **3**
  (`drawStarterWindow`) — three clean trios, then the fourth wraps and reuses two, since 10
  is not a multiple of 3. Being a slot also means `refreshProfileDerived()` pre-warms the
  pool right after every merge — it walks every slot — so the drawer opens on a warm cache
  (measured: ~3.8s cold, 0ms warm), and regeneration is tied to the same freshness rule every
  other slot uses rather than a second threshold meaning the same thing. **Regenerate stays a
  live call**: that button is the explicit "these don't suit me", which is the one place
  paying is clearly warranted.
- **Tags, tag enrichment and the basics tiles are ONE model call** (`extractTagsAndBasics`
  in `src/lib/profileTags.ts`, returning `{basics, tags}` with each tag already carrying its
  intent and next steps). They were three calls that each uploaded and re-read the same
  profile text to ask a different question about it, and they always run together because
  every synthesis invalidates every slot at once — so the split bought nothing and cost two
  extra round trips plus two extra copies of the input. A full profile update is now **3
  model calls, down from 5**.
  - `filterTags` and `basics` remain **separate slots** sharing one call: each keeps its own
    copy of the text it was computed from, so one can be missing or mid-refresh without
    saying anything about the other. The call is memoized by exact text (`sharedExtract`) so
    the two slots requesting it together pay once, and it is **held rather than cleared on
    settle** so a later lone reader reuses it — but a REJECTED promise is dropped
    immediately, or one failure would be cached as the permanent answer.
  - **Each half of the response is salvaged on its own.** A garbled `basics` must not empty
    the tag facet, and vice versa; that isolation is the entire reason collapsing three calls
    into one is acceptable. Tags that come back as bare strings (a common slip against the
    object shape asked for) are topped up through the existing enrichment call rather than
    left un-enriched.
  - **`inferSubjects` is deliberately NOT merged in**, though it would fit. It is the one
    derived value on the search critical path (`preFilter` cannot narrow the catalog without
    it) and the merged answer is the slowest of the set because it carries every tag, so
    folding it in would make a cold-cache search block on tag enrichment it never reads. That
    is the same reasoning that made these independent slots in the first place; it still
    holds for this one and stopped holding for the other three.
  - **The chat opener pool is not merged either, for an unrelated reason**: it runs on
    Anthropic. Moving it into a Gemini call would silently change the feature's provider and
    mis-attribute its cost — the exact failure `provider_for_model()` exists to prevent.
  - Its spend classifies as **`profile_extract`**, and that signature is tested **first** in
    `_FEATURE_SIGNATURES` because the merged prompt necessarily contains the wording of the
    two single-purpose prompts it replaced. `test_classify_feature.py` compares the source
    list against its case list **in order**, so a new signature must be added to both at the
    same position.
- **Slot freshness is EXACT profile-text identity, and deliberately has no tolerance.**
  `profileDerivedIsFresh` compares `slot.profile === synthesized`, so every synthesis pass
  (a chat merge, a resume/LinkedIn import, or a **Tidy it up** repair) invalidates all four
  slots and `refreshProfileDerived` rebuilds them in the background. There was a
  `PROFILE_FILTER_REFRESH_WORDS = 10` tolerance here until 2026-08-24 — an edit moving the
  word count by fewer than 10 words counted as a touch-up and kept the stored values. It was
  a word-COUNT test, not a content test, which is what made it wrong: swapping "robotics" for
  "chemistry" is a zero-word delta, so the tags, subjects and basics went on describing a
  profile the student no longer had, with no way to notice and no expiry. The cost is
  accepted and is **~5 model calls per synthesis** (4 Gemini — subjects, tag extraction, tag
  enrichment, basics — plus 1 Claude for the opener pool, which is skipped free below 50
  words). Reads are unaffected: unchanged text still matches exactly, so ordinary page visits
  and repeat searches still cost nothing.
- **Profile filter tags are MECE THEMES, not one tag per line of the profile.** The prompt
  used to ask for "one entry for every distinct interest, goal, project or pursuit it
  mentions" and explicitly forbade merging two different pursuits, so a 24-line profile
  produced 24 dropdown rows — two separate volunteering placements, three separate clubs, a
  row for one trivia night. A facet at that altitude cannot filter anything: each row matched
  one program or none, and the student scrolled a copy of their own résumé looking for a
  search term. Since 2026-08-24 it asks for a **mutually exclusive, collectively exhaustive**
  set instead, pitched at the level a program is described ("Volunteering with organizations
  that serve children", never "Volunteering with Kids Coming Together"; "Organizing student
  clubs and enrichment events", never "Organized school Trivia Night").
  - **Ties are broken by where the OPPORTUNITIES live**, which is the question the facet
    exists to answer — a chatbot built to learn AI groups with studying AI, a chatbot being
    sold groups with building products; a USAPhO score groups with competitions, not with
    physics as a subject. Stating the tie-break matters: told only "mutually exclusive", the
    model files the ambiguous item under both.
  - **Grouping is not shortening.** Nothing may be dropped for being small, old or
    unimpressive; an item that fits no theme joins the nearest one. The prompt gives a
    granularity test (a theme covers 2+ profile items, or is a standing interest several
    different programs could serve) and 6-12 as guidance, both to stop it collapsing into
    "STEM" / "The arts", which would match most of the catalog.
  - `intent` and `nextSteps` are now **area-level**, in the merged pass and in the top-up
    `enrichRequest` alike. The finder's scorer feeds all three into its ranking call, and
    "milestones for *Organized school Trivia Night*" was close to meaningless.
  - The legacy `buildProfileFilterTags`/`extractProfileTagStrings` pair was **deleted** in
    the same change. Nothing imported it and it carried the old per-item prompt verbatim,
    which is precisely how the retired rule would come back.
  - The prompt's opening line (`pulling out everything an opportunity-matching app needs`) is
    unchanged **on purpose** — it is the `profile_extract` signature in `_FEATURE_SIGNATURES`,
    so rewording it would silently move this feature's spend into `other`.
- **The tag facet has NO CAP on tag count** — `extractTagsAndBasics` returns as many themes
  as it takes to cover what the profile actually says, and the 6-12 above is guidance to the
  model, never a `.slice()`. Grouping reduces the count for a reason a reader can see;
  truncation does not. There was a `PROFILE_TAG_LIMIT = 10`
  until 2026-08-24, applied both in the prompt and as a `.slice()`; it dropped whatever fell
  off the end, and it fell off by the model's own ordering of "most important", so a student
  with a broad profile lost their less-central interests from the dropdown with nothing on
  screen to say so. Two things a cap was implicitly protecting are now handled directly, and
  removing it without them would be cosmetic:
  - **Both calls send their own output budget, and enrichment TOPS UP.** The thing a tag cap
    was really standing in for was `MESSAGES_MAX_TOKENS = 2000`: extraction returns one tag
    per thing the profile mentions and enrichment one object per tag, so both answers grow
    with the student, and a broad profile truncated. It truncated **invisibly** — `extractJSON`
    repairs a truncated array rather than failing, so a short answer and a complete one are
    indistinguishable, which is precisely how a token ceiling became a limit on how many
    interests a student was allowed to have.
    - `/api/messages` now accepts a client `maxTokens`, clamped by `_clamped_gemini_max_tokens`
      into `[MESSAGES_MAX_TOKENS, MESSAGES_MAX_TOKENS_CEILING]` exactly as the Claude route
      already did. It can only ever RAISE headroom, so every existing call site is untouched.
      Unused budget is free (billing is on tokens produced) — the same reasoning profile
      synthesis uses. Remember Gemini 3.x **thinking tokens draw from this same budget**.
    - Extraction asks for the ceiling (`TAG_EXTRACT_MAX_TOKENS`), because the tag count is by
      definition unknown before it runs. Enrichment sizes its budget from the count
      (`enrichBudgetFor`), so a broader profile asks for more rather than the same.
    - Enrichment is **one request for every tag**, then re-asks for exactly the ones that did
      not come back, up to `ENRICH_MAX_ROUNDS`. That constant is a **non-termination guard,
      not a size limit** — every round asks for all remaining tags, so the shortfall shrinks
      fast (measured against a fake that hard-limits 20 objects per response: 50 -> 30 -> 10,
      fully enriched in 3 rounds). A round that adds nothing breaks immediately, so a dead
      enrichment endpoint costs one attempt, not `ENRICH_MAX_ROUNDS` of them.
    - A batched version of this existed briefly and was replaced: a fixed batch size is just
      the same ceiling one level down, and it made a short list pay for the plumbing. The one
      thing worth carrying forward from it is that the positional fallback in `enrichRequest`
      is **relative to the list THAT request was given** — on a top-up round the indexes mean
      nothing against the original ordering, and reusing them hands tags another tag's intent.
  - **The dropdown scrolls** (`facetScroll`, `maxHeight: 320`). The panel is absolutely
    positioned, so an over-long list ran off the bottom of the viewport with the tags below
    the fold unreachable. `None` sits outside the scroller so clearing the filter is always
    visible.
  - Extracted tags are **deduped case-insensitively**. Without a cap a repeat is likelier,
    and a duplicate collides twice: `enrichBatch` keys results by tag string, and the facet
    uses the tag as its React key.
- **Slot WRITES are serialized** (`queueSlotWrite`), because `/api/data/save` replaces the
  whole value at a key — so persisting one slot is a load-modify-save of the entire
  `student-profile` record. `refreshProfileDerived` fires all four at once, and without the
  queue two slots finishing within one load round-trip of each other both read the pre-write
  record and the second save silently dropped the first slot. Only the persist step queues;
  the model calls still run concurrently, so wall time is unchanged.
- **Follow-ups** (`profileChatNextQuestion`) are one live call per bot turn, and must stay
  that way. A follow-up's whole job is to react to what the student just said, and a
  pre-generated question cannot. This was tried and reverted: with pooled follow-ups, a
  student who answered "I'm writing a paper on grapheme-to-phoneme error rates in
  Finno-Ugric languages with two friends from a summer camp" got a generic non-sequitur
  back, because that detail did not exist when the pool was built and does not reach the
  profile until the drawer closes and synthesis runs. **Do not "optimize" this into a pool.**
- The transcript sent with a follow-up includes the **bot lines, not just the student's
  answers**. Answers are routinely meaningless alone ("Yes." says nothing), and the bot lines
  are also what stop the model re-asking what it already asked.
- Both question prompts carry two style rules worth preserving: **one short sentence, never a
  run-on or two questions joined by "and"/"or"**, and **at most 2-3 profile details per
  question** — chaining four or more produces the elaborate connect-the-dots questions this
  replaced.
- Cost note: this is roughly a **wash in dollars**, and was never mainly a cost change — it
  spends where responsiveness is bought and caches where it cannot be. The per-turn question
  is not the expensive call in this flow; **synthesis on drawer close is** (it rewrites the
  whole profile at a 4-8k output budget, ~6x a follow-up turn). If real savings are ever
  wanted, that is where to look. Both opener paths classify as `chat_starters` in
  `_FEATURE_SIGNATURES` and follow-ups as `profile_chat`, so the console can tell them apart.
- **Closing the drawer always ends the session** (`resetProfileChatSession()` clears the
  transcript, the starters, and any unsent input). Synthesis still runs only when the student
  actually answered something — an empty transcript would pay the most expensive call in the
  flow to rewrite an unchanged profile. Before that reset existed, a starter question the
  student read but never answered stayed in `profileChatHistory`, and reopening rendered that
  stale bubble instead of a fresh set of starters.

**App screens** (expo-router; the file path IS the route). Outside the authed group:
`landing.tsx` (signed-out marketing page), `login.tsx` (sign-in + register + consent),
`google-auth.tsx` (OAuth handoff completion), `index.tsx` (the auth gate — redirects to
`/(app)` or `/landing`), `+html.tsx` (the web document shell; it exists so the favicon
`<link>` is present in dev, which `expo export` otherwise injects only at build time).
Inside `(app)/` (guarded by `_layout.tsx`, which renders `NavBar` + `<Slot>` and bounces
signed-out users to `/login`): `index.tsx` Home Base, `finder.tsx` Fresh Finds,
`tracker.tsx` Quest Log, `profile.tsx` My Vibe, `subscription.tsx` Manage Plan.

**The branded tab names are load-bearing** — Home Base / My Vibe / Fresh Finds / Quest Log,
never Home/Profile/Search/Tracker. The finder is a staged single screen (`home` →
`quiz` | `form` → `results`), not four routes. The tracker's six buckets are `ALL_BUCKETS`
in `src/lib/constants.ts`: summerPrograms, internships, researchCompetitions,
pureCompetitions, conferences, journals.

**Known gaps in the RN port** (deliberate or unfinished, so nobody re-derives them):
`starterQuestionPoolFromAI`/`drawStarterWindow` are ported in `src/lib/profileChat.ts` but
**not wired** — the drawer calls the live 3-question path on every open, so the old
`starterPool` cache is currently unused. The finder **reads** `filterTags.enrichedTags`
off the stored profile but never regenerates them (no writer for that slot in RN).
Clear-profile is a visual stub. Payments are deferred by the plan: the subscription screen
shows status and runs the promo flow, and Upgrade surfaces whatever the unconfigured
Stripe backend answers — which is the one sharp edge of the access gate above. **A lapsed
account is now correctly locked out of the app and, with Stripe unconfigured, has no way
to pay its way back in**; the only route out today is a `grant` promo code
(`BETAUSER`), which the paywall screen does accept. Configure `STRIPE_API_KEY` /
`STRIPE_PRICE_ID` before the first real trial expires.

**The landing page's walkthrough film** is [public/walkthrough.html](public/walkthrough.html) at the repo
root — a **vendored, self-extracting ~1.5MB bundle** exported from a design canvas, carrying
its own React runtime, the composition source and every webfont in one file. **Do not
hand-edit it**: the real source is a `<script type="__bundler/manifest">` block of gzipped,
base64'd assets, so every apparent line is machine-written. Re-export and replace the whole
file to change the film.

It is served by `app/main.py`'s repo-root static route (NOT from `frontend/dist`), and
`frontend/app/landing.tsx` points at it via `backendUrl('/walkthrough.html')`. Because it is
heavy and autoplays once, it is **not** embedded eagerly: the poster frame mounts the iframe
only when someone presses play (or "See how it works", which scrolls to the section and
starts it). On native there is no webview dependency, so the same press hands off to the
system browser.

**It must stay git-tracked or production breaks silently.** The file lives at the repo root,
which is mostly gitignored build/log noise, and it was untracked at one point after the SPA
cutover — the landing page then iframes a URL that 404s on Render while working perfectly
against a local checkout. If you touch the film, confirm `git ls-files public/walkthrough.html`
prints it.

## Lifecycle email — three messages, Resend, and the claim table

Three transactional emails around the account lifecycle: **welcome** at signup,
**trial_ending** a couple of days before the free trial expires, and **goodbye** when a
subscription is cancelled. `app/services/email_templates.py` owns what they say;
`app/services/email.py` owns whether they are sent at all.

**This is not a marketing system, and the distinction is the design.** There is no list, no
segments, no campaign composer, and deliberately no code path anywhere in this repo that
mails everybody at once. A marketing platform (Mailchimp, Loops, Customer.io) was rejected
for one reason: it requires continuously syncing the roster — names, emails, plan status —
to a third party, for a user base that is largely minors. That contradicts
`legal/privacy.md`, and switching it on would need a privacy edit, a `agents/build_legal.py`
re-run and a `TERMS_VERSION` bump first. What IS outsourced is the pipe: Resend gets one
address at a time, at the moment of sending. What a provider buys and a repo cannot rebuild
is SPF/DKIM/DMARC on the sending domain, a warmed IP, bounce/complaint handling and a
suppression list — without which mail from a cold domain to Gmail and school Google
Workspace accounts goes to spam, and school MXes are the least forgiving recipients there
are.

**`email_sends` is a CLAIM table, not a log**
([email_schema.sql](db/email_schema.sql) — another one-time manual DDL step, and it also adds
`users.lifecycle_email_optout`). The row is written **before** Resend is called, and its
`unique (userid, kind, dedupe_key)` is what makes a repeated sweep safe: the second attempt
loses the insert, sees 23505, and skips. A log written *after* the send cannot do this — the
window between "Resend accepted it" and "we recorded it" is exactly where a crash puts a
second copy in a real student's inbox. State the cost plainly: a send that crashes mid-flight
leaves a row stuck at `sending` and is **never retried automatically**. That is the intended
direction — a stuck row is visible in the console and clearable by hand, a duplicate cannot
be un-sent.

- **Failing to claim means not sending, including when the table is absent.** Until
  `db/email_schema.sql` runs, every claim fails and nothing is ever sent; the console shows the
  setup step. That reads as the feature being switched off, which is correct — the
  alternative is sending with no record of having sent, i.e. a daily sweep that mails the
  same student every morning.
- **`dedupe_key` is why the constraint is three columns, not two.** A trial can be extended
  (a `grant` promo code adds days to `trial_ends_at`), so keying `trial_ending` on
  `(userid, kind)` alone would mean a student who redeems `BETAUSER` and gets a second trial
  window never hears from us again. The key is the trial's end **date** — date, not
  timestamp, so a grant shifting the end by hours does not mint a second send while one
  adding days correctly does. `welcome`/`goodbye` use `''`, **not NULL**: Postgres treats
  NULLs as distinct in a unique constraint, which would silently make every insert a fresh
  row and defeat the whole table. Same trap `user_costs.model` documents.
- **The pre-send guards release their claim** (`release_claim`). Opted-out and
  no-address are decided *after* the row exists; leaving it behind would permanently
  suppress a legitimate later send, so an account that opts back in would never get another
  reminder. Never release after Resend has been handed the message.
- **Mock mode writes NO claim row.** With no `RESEND_API_KEY` the whole path runs offline
  (the convention `GEMINI_API_KEY`/`ANTHROPIC_API_KEY` already set) — but a claim would
  suppress the real send once a key is configured, so developing offline would silently cost
  real users their welcome email.
- `_claim` classifies from an **already-read** error body rather than calling
  `_missing_table_error(e)`: `_error_body` consumes the response stream and is readable
  exactly once, so a second call gets `{}` and reports a missing table as a generic error —
  precisely the case that most needs to name the .sql file.

**The scheduler is NOT in the admin console, and cannot be.** `ops/` is localhost-gated and
never mounted on Render, so a button there would only fire the trial sweep on days somebody's
laptop is on — and the student whose trial ends tomorrow is exactly the one who will not open
the app to trigger it for us. So:

- **`app/` (shipped)** owns the triggers and the two production endpoints. `welcome` fires in
  `handle_register` **and** in the Google signup completion (`create_user` is called from two
  places; the claim means adding it in both cannot double up). `goodbye` fires in
  `handle_subscription_cancel`, from `{**record, **updates}` rather than a re-read, because
  the email's most important sentence is the date access ends and `get_user_account()` there
  can still return the pre-PATCH row. All three go out through `send_lifecycle_email_async` —
  a signup must not fail or hang because a mail provider is having a bad day.
- **`POST /api/email/sweep`** (`app/routes/email.py`) is the daily trigger, called by
  [.github/workflows/lifecycle-emails.yml](.github/workflows/lifecycle-emails.yml).
  **The schedule is DISARMED as of 2026-08-24** — the workflow carries `workflow_dispatch`
  only, so the trial reminder does NOT go out automatically and someone must press "Run
  workflow". Welcome and goodbye are unaffected: they are event-driven from `app/routes/`,
  not from here. Re-arming is uncommenting two lines and setting the two repository secrets;
  the commented cron is 15:00 UTC, deliberately not midnight (GitHub's scheduler is heavily
  oversubscribed on the hour and 00:00 runs are routinely delayed or dropped, and a 3am
  reminder is the least likely to be acted on). Guarded by `EMAIL_CRON_SECRET` in an
  **`X-Cron-Secret` header, never a query string** — a URL carrying a credential is written to every proxy log on the way. Unset
  secret **fails closed with a 503**, the same choice `JWT_SECRET` makes. The per-account
  detail list is dropped unless `verbose`, so a roster of minors' addresses never lands in an
  Actions log.
- **`GET /api/email/unsubscribe`** answers **HTML, not JSON** — it is opened in a browser by a
  person, and a raw JSON blob reads as the link having failed, the one impression an
  unsubscribe link must never give. It carries an HMAC of the userid under `JWT_SECRET` (not
  a JWT: an unsubscribe link sits in a mailbox for years and must not expire), so nobody can
  opt somebody else out by guessing an id. A failed write says so rather than claiming
  success.
- **The opt-out is honoured for all three kinds**, including the two that are defensibly
  transactional and could legally ignore it. Most of this user base are minors, an
  unsubscribe that quietly keeps sending is the exact silent failure the mailing-list feature
  is measured against, and there is no volume here that makes the distinction worth the trust
  cost.
- `wingman/send_lifecycle_emails.py` at the repo root is the **local** runner (`--preview`,
  `--dry-run`, `--days`, `--json`) for a manual catch-up. Unlike the six catalog agents **all
  three tiers are free** — there is no model in this path. What `--preview` protects is not
  money, it is a student's inbox.

**The console's Emails tab** (`ops/admin_console.html`, `#view-emails`, backed by
`GET /api/agents/emails`) owns the *review* half: the send log, per-kind counts, a stuck
count, who is currently due, template previews, and a test send.

- Preview and the real send both come from `render_for()`, so what an operator reviews is
  byte-identical to what a student receives — a preview built from its own copy of the
  templates is a preview of something that is not being sent. It renders into an
  `iframe srcdoc` with an empty `sandbox`, so the email is rendered, not executed.
- **The test send is deliberately NOT deduped and writes nothing.** A test is something you
  repeat while editing copy, and a claim would both block the second attempt and — far worse
  — consume the real user's one send, so previewing the welcome email against your own
  account would mean that account never gets one. Its subject is prefixed `[TEST]`.
- `configured` (no API key) and `table_ready` (no table) are reported **separately**: they
  are different problems with different fixes, and one "not working" would hide which you
  have. **Mock is a KPI tile**, not a footnote — with no key everything else on the page
  reads as a working pipeline that has simply had nothing to do, which is the most
  misleading way this page can fail.
- `due_error` is reported rather than collapsing to zero, for the reason every count in this
  repo states: an empty list and a failed read look identical, and one of them means the
  reminder is not going out.

**Templates**: table-based layout, inline styles, **no external CSS and no images** — Outlook
renders through Word's HTML engine and remote images are blocked by default in most clients,
so a logo would be a broken-image box on first open. **Every email ships a text/plain part**
(a missing one is among the strongest spam signals there is). **No tracking pixel and no
click-wrapping** — `legal/privacy.md` does not describe open tracking, so adding it is a
privacy change and a `TERMS_VERSION` bump, not a template edit. `EMAIL_POSTAL_ADDRESS` is in
every footer unconditionally: all three are arguably CAN-SPAM-exempt as transactional, but
the exemption is a legal argument and losing it costs more than a line of text. The goodbye
email carries **no win-back offer, deliberately** — the same email with a coupon in it is
commercial, and the exemption is not worth trading for one.

**Setup, in order** (nothing sends until all of it is done):
1. Run [email_schema.sql](db/email_schema.sql) in the Supabase SQL editor.
2. Create a Resend account, verify `highschoolwingman.com` as a sending domain (SPF/DKIM),
   and put `RESEND_API_KEY` in `.env` and in the Render dashboard. A 403 whose message
   mentions the domain is an unverified sender, not a bad key — `_resend_post` says so.
3. Set `EMAIL_POSTAL_ADDRESS` — the default is a deliberately obvious placeholder
   (`[SET EMAIL_POSTAL_ADDRESS IN .env]`) so it cannot ship unnoticed, and it renders in
   every footer. `EMAIL_FROM`/`EMAIL_REPLY_TO` already default to
   `contactus@highschoolwingman.com` and need overriding only if that changes.
4. Only when re-arming the schedule: `EMAIL_CRON_SECRET` on Render **and** as a GitHub
   Actions secret, plus `WINGMAN_API_BASE`.

**`EMAIL_APP_URL` refuses to inherit a loopback `WEB_APP_URL`.** The fallback chain is
`EMAIL_APP_URL` → `WEB_APP_URL` (only if not loopback) → the production origin. In dev
`WEB_APP_URL` is legitimately `http://localhost:8081`, and inheriting it put
`Keep my account: http://localhost:8081/subscription` into a real trial-ending email — a
link that resolves to the RECIPIENT's own machine, so it fails for them and works when you
test it, which is the worst possible way for this to be wrong. Setting `EMAIL_APP_URL`
explicitly still overrides, loopback included: an explicit value is a decision, an inherited
one is an accident.

**Known gap, stated rather than discovered later:** the address on file may be a **school
account that blocks outside mail**. The mailing-list feature already hit this and made the
address editable for that reason; there is no editable field here, so a student on a locked
school domain silently gets nothing. `email_sends.email` records the address actually used,
which is the first thing to check when somebody says nothing arrived.

## Security notes for this repo

- `.env` (holds `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
  `SUPABASE_SERVICE_KEY`), `.claude/settings.local.json`, and `server.log` are gitignored —
  never `git add -f` them. `.gitignore` only prevents future tracking; if a secret is ever
  committed, `git rm --cached` is also required. `SUPABASE_SERVICE_KEY` in particular must
  never reach the browser/client code — it bypasses RLS and is only read by `server.py` and
  the one-off `migrate_*_to_supabase.py` scripts.
- If a secret is ever committed locally but not yet pushed, prefer `git reset --soft` to
  before the leaking commit + a fresh commit, then `git reflog expire --expire=now --all &&
  git gc --prune=now --aggressive` to purge the orphaned blob, verifying after with
  `git fsck --full --unreachable`. Do not rewrite history that's already been pushed without
  explicit user confirmation.
