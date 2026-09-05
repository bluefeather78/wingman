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
  the AI features backed by that provider (`POST /api/ai` picks the provider from the
  server-side feature id; see `app/services/prompts.py`) run in **MOCK
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
  `app/main.py` additionally **refuses the mount outright when `RENDER` is set**, whatever
  the flag says. Every ops API route also requires **`WINGMAN_OPS_TOKEN` in an `X-Ops-Token`
  header** and fails closed without one — localhost-gating alone is defeated by any tunnel,
  whose peer *is* 127.0.0.1. The browser-navigable page shells (`/admin`,
  `/admin/logic-map`, `/evals`, `/evals/scorecard`) are exempt because a navigation cannot
  set a header; the console prompts for the token and sends it on every call. `python
  server.py` mints and prints one when `.env` has none — put it in `.env` to keep it stable.
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

## Which file do I need?

CLAUDE.md is loaded into **every** session, so it holds only what is true regardless of what
you are working on: the marquee rules, the layout, how to run things, and the security notes.
The two domain files below are **not** auto-loaded — say which one you are working in at the
start of a session and read it then. They were split out of this file on 2026-09-04 (it had
reached 177KB); the text in them is unchanged, only relocated.

| Working on | Read | Covers |
|---|---|---|
| the student-facing app — `frontend/`, `app/` | [docs/CLAUDE-app.md](docs/CLAUDE-app.md) | the API endpoints, auth, subscription/trial/consent, the profile chat and its caching, AI call flow, app screens, app-open latency, the font-flash fix, lifecycle email |
| the catalog pipeline or the console — `agents/`, `wingman/`, `ops/` | [docs/CLAUDE-ops.md](docs/CLAUDE-ops.md) | the seven agents and what each costs, dry-run/preview/commit tiers, cost accounting, the Metrics and Cost-per-user views, link health, URL repair, action-item generation, the scraper, dedupe and the review queue |

If you are touching both — a feature that adds a column the agents write and the app reads —
read both. **Read the relevant one BEFORE editing**, not after: most of what is in them is a
record of something that was already tried and went wrong, and the cost of not knowing is
repeating it.

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

