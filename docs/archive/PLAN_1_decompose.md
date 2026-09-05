# Plan 1 of 3 — Decompose the Python backend

> **Read this first.** This is phase 1 of a 3-phase migration. Each phase is executed in its
> own Claude Code session to keep context small. Read `CLAUDE.md` for the app's architecture,
> then this file. Phases 2 and 3 are `PLAN_2_auth.md` and `PLAN_3_rn.md` — **do not start them
> here.** The whole migration was designed in a session on 2026-08-23; these files are the
> record of those decisions so a fresh session can execute without re-deriving them.

## The migration in one picture

Highschool Wingman is today a static vanilla-JS SPA (`index.html`, `script.js`, `public/styles.css`)
served by a 6,956-line stdlib `http.server` monolith (`server.py`), plus ~34 Python files of
offline agent/utility code, all talking to Supabase. The three phases:

1. **(this file) Decompose `server.py`** into a FastAPI app with domain routers, and split the
   offline agents into their own package the web deploy does not ship. *Old frontend keeps
   working unchanged throughout.*
2. **Auth** (`PLAN_2_auth.md`) — JWT session tokens, close a live IDOR on the data endpoints.
   *Old frontend still the client; small change to verify.*
3. **RN frontend** (`PLAN_3_rn.md`) — rebuild the frontend in Expo (React Native + web) against
   the now-stable, authed API. Salvage pure logic from `script.js`, discard DOM glue.

**Locked decisions (do not relitigate in this session):**
- Backend stays **Python** — all the value (6 AI agents, scraper, cost accounting) is Python
  and works. Only the *web layer* is being restructured, not the language.
- Order is decompose → auth → RN. Auth before RN so the frontend is built once against the
  final contract, and so the IDOR is closed early rather than left open across the RN build.
- **Payments are deferred.** The Stripe/trial/subscription plumbing stays in the code but
  dormant; leave the trial gate open so nothing paywalls. Do not remove it, do not build on it.
- Hosting stays **Render** (backend = Render Web Service; later the Expo web build = Render
  Static Site; native builds go through EAS, never Render). Offline agents run **locally**
  against production Supabase and are never invoked on the deployed server.

---

## Phase 1 goal

Turn `server.py` from one 6,956-line `if/elif` request router into a FastAPI application split
by domain, **and** physically separate the offline tooling so only user-facing code ships.
These are the *same operation* — the boundary between "web API" and "offline agents" is drawn
once here.

**This phase changes structure only. No behavior changes, no new features, no auth yet.** The
exit test is that the existing `script.js` frontend still works end-to-end against the new
backend with zero frontend changes (same URLs, same request/response JSON).

## Non-goals for phase 1 (explicitly out of scope — later phases)

- No auth / JWT / token work — that is phase 2. The `userid`-in-body IDOR stays open one more
  phase; do not fix it here, it would muddy the "structure only" diff.
- No frontend changes.
- No touching the offline agents' internal logic — they move, they don't change.
- No payment removal.

## Current state (verified 2026-08-23, starting point for this session)

- `server.py` — 6,956 lines. `do_GET`/`do_POST` (around lines 4870 and 4968) are one big
  `if/elif` chain matching ~50 `/api/*` routes, some by exact `self.path`, some by regex
  (`DEADLINE_PATH_RE`, `SUBSCRIBE_PATH_RE`, `SEED_PATH_RE`, etc. near line 1424).
- Route groups already visible in the chain:
  - Public app API: `/api/messages`, `/api/messages-claude`, `/api/opportunities`,
    `/api/opportunities/<id>/deadline`, `/api/opportunities/<id>/subscribe`,
    `/api/register`, `/api/login`, `/api/data/save`, `/api/data/load`,
    `/api/account/location`, `/api/extract-from-resume`, `/api/extract-from-linkedin`,
    `/api/user-submitted-opportunities`, `/api/mailing-list/*`.
  - Google OAuth: `/api/auth/google/*`, `/api/calendar/sync`.
  - Subscription (dormant): `/api/subscription/*`.
  - **Localhost-only ops** (gated by `_require_local()`): all `/api/agents/*` and `/api/seeds`,
    plus the `/admin` console page.
  - Static file serving (the SPA).
- Offline tooling (already fairly modular, ~34 files): the 6 agents (`agents/scrape_opportunities.py`,
  `agents/check_reviews.py`, `agents/check_deadlines.py`, `agents/refresh_opportunities.py`, `agents/find_mailing_lists.py`,
  `agents/check_links.py`) plus shared libs (`wingman/gemini_common.py`, `wingman/claude_common.py`, `wingman/agent_common.py`,
  `wingman/dryrun_common.py`, `wingman/url_validate.py`, `wingman/url_repair.py`, `wingman/url_dedupe.py`, `wingman/seeds_common.py`,
  `wingman/supabase_common.py`, `wingman/subscription_common.py`, `wingman/mailing_list_common.py`, `wingman/contact_email_common.py`,
  the `migrate_*` one-offs, `admin_console.html`, `agents/grade_mailing_lists.py`, etc.).
- The monolith is the problem; the utility files are mostly fine and get *reused*, not rewritten.

## Target layout

```
app/                          # ships to Render — the public web service
  main.py                     # FastAPI app; mounts routers; serves SPA static files (for now)
  deps.py                     # shared dependencies (get_supabase, request helpers)
  routes/
    opportunities.py          # /api/opportunities, /deadline, /subscribe
    user_data.py              # /api/data/save|load, /api/account/location
    ai.py                     # /api/messages, /api/messages-claude
    account.py                # /api/register, /api/login
    google_oauth.py           # /api/auth/google/*, /api/calendar/sync
    mailing_list.py           # /api/mailing-list/*
    subscription.py           # /api/subscription/*  (dormant, gate open)
    resume.py                 # /api/extract-from-resume, /extract-from-linkedin, /user-submitted-opportunities
  services/                   # existing domain code, imported as-is where possible
    (gemini_common, claude_common, supabase_common, subscription_common,
     mailing_list_common, contact_email_common, ... move or re-export here)

ops/                          # DOES NOT ship to Render — local-only agent toolkit
  admin_server.py OR admin/   # the /api/agents/* + /api/seeds routes + _require_local gate
  admin_console.html
  agents/                     # the 6 agents + agent_common, dryrun_common, seeds_common,
                              # url_validate, url_repair, url_dedupe, grade_mailing_lists, migrate_*
```

Exact folder names are the implementer's call; what matters is the **two-package split**
(`app/` ships, `ops/` does not) and routers grouped by domain.

## Key decisions / cautions for this phase

- **FastAPI + uvicorn.** Render start command becomes `uvicorn app.main:app --host 0.0.0.0
  --port $PORT`. Add `render.yaml` (or set it in the dashboard). Keep `requirements.txt`
  minimal — stdlib philosophy is fine to relax now that FastAPI earns its place.
- **Preserve every route's exact path and JSON shape.** The old frontend is the regression
  test. If a response envelope changes, the old app breaks and you've failed the exit test.
- **The localhost-only guard must survive the move.** `_require_local()` currently protects
  `/api/agents/*`, `/api/seeds`, and `/admin`. After the split those routes ideally aren't in
  the shipped `app/` at all (they live in `ops/`), which is *better* than the guard — but if
  any stay in `app/` for convenience, they MUST keep the guard. Confirm the shipped service
  exposes **no** agent/seed/admin route.
- **Static file serving.** `app/main.py` keeps serving `index.html`/`script.js`/`public/styles.css`
  for now (FastAPI `StaticFiles`), because the old frontend is still the client this phase.
  Phase 3 later moves the frontend to a Render Static Site and this can be dropped.
- **The `SUPABASE_SERVICE_KEY` must only be in `app/`'s and `ops/`'s environments, never in
  any client.** Nothing changes here, just don't leak it into a new config during the move.
- **Google OAuth in-process token stores** (`_google_session_tokens` near line 289) are
  process-local dicts. They keep working under one uvicorn worker. If you configure multiple
  workers, note this becomes shared-state-sensitive — flag it, don't solve it here.
- Regex routes (`/api/opportunities/<id>/deadline`, `/subscribe`, `/api/seeds/<n>`,
  `/api/agents/settings/<agent>`) become FastAPI path params — straightforward, but make sure
  the query-string-carrying ones (`/deadline?userid=`, `/extract-from-resume?userid=`) still
  parse; the old code has comments about them breaking exact-path matching.

## Exit test (must pass before this session ends)

1. `uvicorn app.main:app` starts clean.
2. The existing `script.js` SPA, unchanged, loads and works: sign in, load/save profile &
   tracker, run a search over `/api/opportunities`, an AI call to `/api/messages`, a deadline
   check. (Mock mode is fine if no keys — the point is the routes respond with the same shape.)
3. The shipped `app/` exposes **no** `/api/agents/*`, `/api/seeds`, or `/admin` route
   (confirm they 404 on the web service — they now live only in `ops/`, run locally).
4. Offline agents still run locally (spot-check one free one, e.g. `agents/check_links.py --preview`)
   against Supabase, importing from wherever their shared libs now live.

## Hand-off to phase 2

When done, append a short "## Phase 1 result" section to the TOP of `PLAN_2_auth.md`
recording: the final folder layout actually used, the FastAPI entrypoint path, where the
data routes (`/api/data/save|load`) now live (phase 2 gates exactly those), and anything that
deviated from this plan. Then tell the user the path and stop. Do not start phase 2.
