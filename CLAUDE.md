# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

"Highschool Wingman" — a static vanilla-JS single-page app that helps high schoolers find and
track extracurricular opportunities (summer programs, internships, research competitions,
academic competitions, conferences, journals). No build step, no framework, no bundler.
Tailwind CSS is loaded via CDN in [index.html](index.html).

## Running the app

```
python server.py
```

Serves the static site and API on `http://localhost:8000`. `server.py` is a Python
stdlib-only (`http.server`) dev server — no dependencies to install, no build/lint/test
tooling exists in this repo.

- `.env` (gitignored) holds `ANTHROPIC_API_KEY`. If unset, the server runs in **MOCK mode**,
  fabricating plausible pattern-matched responses (see `generate_mock_text` in
  [server.py](server.py)) so the app is fully click-through-able offline.
- Never pass the API key inline on the command line (e.g.
  `ANTHROPIC_API_KEY=... python server.py`) — it gets recorded in shell history / Claude Code's
  local settings allowlist and has leaked before. Always put it in `.env`.

## Architecture

**Three-file frontend, no modules:** [index.html](index.html) (markup/layout),
[script.js](script.js) (~2500+ lines, all app logic, loaded as one non-module script),
[styles.css](styles.css). Everything is global — functions and state (`let`/`const` at top
level of script.js) are called directly from inline `onclick="..."`/`onsubmit="..."`
attributes in the HTML. When adding a function that's invoked from HTML, it must stay a
plain global function (not wrapped in a module or IIFE).

**Opportunity data**: the opportunity catalog (1200+ rows) lives in a Supabase (hosted
Postgres) `opportunities` table, not a static file — `server.py`'s `/api/opportunities`
proxies to it (PostgREST, anon key, RLS-restricted to `is_active=true` rows, paginated past
PostgREST's 1000-row cap, cached in-process for `OPPORTUNITIES_CACHE_TTL` seconds) and
`script.js` fetches that endpoint into the global `OPPORTUNITIES` array on load.
[opportunities.json](opportunities.json) still exists git-tracked as a diffable backup
snapshot only — regenerate it with `export_json.py` after editing the DB, it is **not**
fetched at runtime anymore. `migrate_to_supabase.py` was the one-off script that populated the
table (from this file plus a sibling `opportunity finder/` project's seed data); not part of
the regular dev loop.

**Backend (`server.py`)** is a `ThreadingHTTPServer` with a `GET /api/opportunities` route
plus four POST endpoints:
- `/api/messages` — proxies to the real Anthropic Messages API (model
  `claude-sonnet-4-6`, see `callClaude()` in script.js) when `ANTHROPIC_API_KEY` is set,
  otherwise fabricates a mock response by pattern-matching the `system` prompt text
  (`generate_mock_text`). When adding a new AI-backed feature, add a matching mock branch
  here so the app stays usable without a live key.
- `/api/register`, `/api/login`, `/api/data/save`, `/api/data/load` — backed by a Supabase
  `users` table (`get_user`/`create_user`/`update_user_data` in server.py), queried with the
  `SUPABASE_SERVICE_KEY` (service_role — bypasses RLS). That table has RLS **enabled with no
  policies at all**, so the anon key gets zero access to it; only `server.py`'s service-role
  calls can read/write it, unlike the public read-only `opportunities` table. Client hashes
  passwords with SHA-256 (`crypto.subtle.digest`) before sending; the server only ever
  stores/sees the hash — no salting, no HTTPS enforcement, no rate limiting (fine for a
  prototype, not production-grade). `migrate_users_to_supabase.py` was the one-off script that
  moved the old flat-file `users_db.json` into this table — logic/shape is otherwise
  unchanged, this was a storage-backend swap only.

**Two persistence layers on the client**, easy to conflate:
1. `window.storage` (get/set, async) — used for `currentUser` session cache, `studentProfile`,
   `trackerData`, `trackerSavedState`. This API is **not defined anywhere in this repo**; it's
   presumably injected by whatever runtime hosts the live preview, and calls are always
   guarded with `if(window.storage){...}` + try/catch. Running `python server.py` and opening
   a plain browser tab means these silently no-op — data won't persist across reloads in that
   environment.
2. The Supabase `users` table via `/api/register`/`/api/login`/`/api/data/save`/`/api/data/load`
   — this is the only storage that actually persists accounts and per-user data (profile,
   tracker) across server restarts and different browsers/devices.

**AI call flow**: all AI features funnel through `callClaude(system, userContent, useWebSearch)`
in script.js, which POSTs to `/api/messages` and returns cleaned text; `extractJSON()` then
pulls a JSON value out of that text via brace/bracket-depth scanning (handles trailing
commentary and attempts best-effort repair of truncated/token-limited responses). Callers:
`inferSubjects`, `rankCandidates`, `findVenuesViaWeb`, `synthesizeProfile`,
`assessProfileReadiness`, `extractTrackerInfo`/tracker classification. Conferences/journals
aren't in the local `opportunities.json` dataset, so those two "kinds" search the live web
(`useWebSearch: true`) instead of ranking local candidates.

**App pages** (single-page, no router — `showPage(name)` toggles `#page-*` sections):
Home/Dashboard (progress bars, todo counts), Wizard/Finder (quiz or free-text profile →
`runSearch()`/`runProfileSuggestSearch()` → ranked results → `buildTracker()`), Tracker
(calendar + list views across buckets in `ALL_BUCKETS`: summerPrograms, internships,
researchCompetitions, pureCompetitions, conferences, journals).

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
