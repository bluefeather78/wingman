## Phase 1 result (completed 2026-08-23)

Phase 1 (`PLAN_1_decompose.md`) is done and all four exit tests pass. What you are building
on:

**Entrypoint / how to run.** `uvicorn app.main:app --host 0.0.0.0 --port 8000`. `server.py`
is now a ~40-line shim that calls `uvicorn.run("app.main:app", ...)` with
`WINGMAN_ENABLE_OPS=1`, so `python server.py`, `.claude/launch.json`, and
`restart_server.ps1` all still work and still serve `/admin` locally. `requirements.txt` now
lists `fastapi` + `uvicorn` (+ optional `PyPDF2`/`python-docx`); `render.yaml` was added
(start command `uvicorn app.main:app`, ops NOT enabled).

**Final layout actually used** (a single-module-per-domain split; `app/` ships, `ops/` is
local-only):

```
app/
  config.py            env + shared constants (loads .env on import)
  core.py              THE SEAM: Supabase plumbing (_supabase_request/_users_request/...),
                       account CRUD (get_user/create_user/update_user_data/...), cost +
                       activity accounting, subscription_state / _login_payload /
                       ensure_trial_started. Imported by both app/ and ops/.
  deps.py              json_response / json_error / read_json_body[_strict] /
                       subscription_block_reason / client_ip   <-- request+response helpers
  main.py              FastAPI app; includes routers; serves the SPA (deny-listed static);
                       mounts ops ONLY if WINGMAN_ENABLE_OPS is set
  services/            opportunities, deadlines, ai (mocks), mailing_list, google_oauth, resume
  routes/              ai, opportunities, account, user_data, google_oauth, mailing_list,
                       subscription, resume     <-- one APIRouter per file
ops/
  core.py              agent orchestration, metrics, user-costs, seeds, snapshots, review-queue
  admin.py             the /api/agents/*, /api/seeds, /admin router (every route localhost-gated)
  admin_console.html   (moved here from the repo root)
```

**Where the data routes phase 2 gates now live:** `/api/data/save` and `/api/data/load` are
`handle_data_save` / `handle_data_load` in **`app/routes/user_data.py`** (also
`/api/account/location`). They still read `userid` **from the JSON body** — the IDOR is
untouched, exactly as this phase requires. The account/session code you will build token auth
around is in `app/routes/account.py` (`handle_register`, `handle_login`) and
`app/routes/google_oauth.py`; `_login_payload` and the user CRUD are in `app/core.py`. The
per-request subscription gate is `subscription_block_reason(userid)` in `app/deps.py` (returns
a 402 reason string or None; routes turn it into a `json_error(402, ...)`) — a natural place
to also hang token verification, since every money-spending route already calls it.

**Deviations from `PLAN_1_decompose.md`, and why** (the plan said to record these):

1. **The offline agents and their shared libs were NOT physically moved into `ops/agents/`.**
   They stay at the repo root as the shared layer both `app/` and `ops/` import. Reason: the
   web app imports the agent module `check_deadlines` directly (the deadline endpoint reuses
   `check_one`), plus `gemini_common`/`claude_common`/`agent_common`/`supabase_common`/
   `mailing_list_common`/`url_dedupe`/`subscription_common`/`dryrun_common`; the agents import
   each other by bare name; and their `__file__`-anchored `agent_logs/` + snapshot writers
   assume the repo root. Moving them would break bare imports and that file I/O, and the plan
   forbids changing agent internals ("they move, they don't change"). Net effect is the same
   as intended: the **shipped service exposes no agent/seed/admin route and never runs an
   agent** — that is enforced by not mounting `ops/` (see #2), not by physical location.
   `ops/core.py` anchors agent paths via an explicit `REPO_ROOT` because it sits one directory
   below where `server.py` used to.
2. **Ops is mounted by an env flag, not physically absent from a separate process.**
   `app/main.py` mounts `ops/admin.py` only when `WINGMAN_ENABLE_OPS` is set (and imports
   `ops` lazily inside that branch, so the shipped app never even imports it). Chosen over a
   separate ops process to preserve exact in-process behavior locally (the runs-cache
   invalidation and the 30s activity-flush the console reads both share state with the app).
   On Render the flag is unset → `/admin`, `/api/agents/*`, `/api/seeds` all 404.
3. Ops-only helper functions live in `ops/core.py`; the genuinely shared seam
   (`_supabase_request`, the cost/activity recorders, `subscription_state`, ...) is in
   `app/core.py`, which `ops/` imports. `_missing_table_error` and the `USER_ACTIVITY/METRICS`
   SQL-filename constants ended up in `app/core.py` / `app/config.py` because the shared
   activity path needs them.

**Verified (exit tests):** uvicorn boots clean; the unchanged `script.js` SPA loads and its
`/api/opportunities` fetch returns 200 (1239 rows); `/api/login`, `/api/data/load`,
`/api/subscription/validate-promo` return the same JSON shapes; the no-cache headers are
present; source/secrets are deny-listed (`/server.py`, `/.env`, `/app/config.py` → 404); with
ops off `/admin` + `/api/agents/*` + `/api/seeds` → 404, with ops on they → 200; and
`agents/check_links.py --preview` still runs (both directly and launched via the console).

---

# Plan 2 of 3 — Token auth + close the IDOR

> **Read this first.** Phase 2 of a 3-phase migration (see `PLAN_1_decompose.md` for the full
> picture and locked decisions). Read `CLAUDE.md`, then `PLAN_1_decompose.md`'s "Phase 1 result"
> section (appended by the phase-1 session — it records the actual backend layout you're
> building on), then this file. Phase 3 is `PLAN_3_rn.md` — do not start it here.

## What phase 1 left behind (assumed starting state)

Phase 1 turned `server.py` into a FastAPI app (`app/`) with domain routers and moved the
offline agents into a non-shipped `ops/` package. **The old `script.js` frontend still works
unchanged** against the new backend, and it is still the client you verify against in this
phase. The data endpoints `/api/data/save` and `/api/data/load` now live in a router (phase 1
placed them — see `## Phase 1 result` at the top of this file for the exact module). **Their
behavior is still the pre-migration behavior, including the security hole this phase fixes.**

> If the `## Phase 1 result` section is missing, phase 1 did not complete — stop and confirm
> with the user before proceeding.

## The problem this phase fixes (verified 2026-08-23)

There is **no real auth**. Login just returns a user object that the client caches
(`currentUser` in `window.storage`). The server has no session concept. Critically, the data
endpoints take the account id straight from the request body and return that account's data
with no check that the caller is that user:

```python
# handle_data_load (was server.py:5653)
userid = (body.get("userid") or "").strip().lower()
record = get_user(userid)
value = (record.get("data") or {}).get(key)      # returns ANY user's data
# handle_data_save (was server.py:5636) has the same shape — writes ANY user's data
```

This is a live **IDOR**: `curl` with `{"userid":"someone","key":"studentProfile"}` returns that
student's profile. The user base is **largely minors**, and the data is personal (names,
school, essays). This is the single most important thing in the whole migration to fix, and it
is deliberately fixed **before** the RN build so the frontend is built against the secure
contract from day one and the hole isn't left open across the longest phase.

Google login already has *some* real token machinery (`_google_session_tokens`, single-use
handoff tokens, phase-1 module for google oauth) — but it's a one-time redirect handoff, not a
credential carried on later requests. After that handoff, Google-authed requests are back to
raw `userid`-in-body, identical to password requests. So the fix is login-method-agnostic.

## Phase 2 goal

Add a **login-method-agnostic session-token layer**:

1. Login (password **and** Google) converge on one step: "identity verified → mint a JWT for
   this userid → return it to the client."
2. Every request that touches user-owned data carries `Authorization: Bearer <jwt>`.
3. The server **derives `userid` from the verified token**, never from the request body. This
   is what closes the IDOR — identity comes from something the caller can't forge.
4. Move password hashing server-side (argon2/bcrypt); add basic login rate limiting.
5. Update the **old `script.js` frontend** to store the token and send the header, and verify
   the whole existing app still works. (Small, contained — the old app has one login path.)

## Locked decisions from the design session

- **JWT, not opaque tokens** for the hot path. Recommended shape: **short-lived access JWT
  (~30–60 min) + a longer-lived refresh token** so you get statelessness *and* a revocation
  story ("log out everywhere", account kill). If you want to ship the beta faster, a single
  medium-lived JWT (~7 days) with no refresh is *acceptable to start* — but write down that
  instant revocation doesn't work yet, and leave room to add refresh without reworking clients.
  **Make this call explicitly at the top of the session and record it.**
- **The identity travels in the token, not the body — no exceptions.** Every data route reads
  `userid` from `verify(token)`.
- Password login and Google login **share the same token-minting code.** Do not build two
  session mechanisms. Unify the Google path onto the same JWT model rather than leaving it as
  a weaker twin.
- Server-side password hashing (argon2 or bcrypt). Today the client SHA-256s and the server
  stores that hash — so the stored value is password-equivalent. Real hashing is server-side.
  **Migration concern:** existing rows hold the old client-SHA-256 value. Plan a transition
  (e.g. re-hash on next successful login, or a one-off migration) — do not lock out existing
  accounts. Record the approach chosen.
- Still no payments. Auth is independent of subscription state; do not couple them.

## Which routes get gated

Anything that reads or writes user-owned data or acts on a user's behalf:
- `/api/data/save`, `/api/data/load` — **the IDOR; top priority.**
- `/api/account/location`
- `/api/opportunities/<id>/subscribe` (mailing-list signup for a real user)
- `/api/opportunities/<id>/deadline?userid=` and `/api/extract-from-resume?userid=` — these
  currently carry `userid` for cost attribution; switch them to derive it from the token too.
- `/api/subscription/*` (dormant, but they mutate the account — gate them anyway).
- `/api/calendar/sync`, and anything else that takes a `userid`.

**Not gated** (stay public): `/api/opportunities` (public catalog read), `/api/register`,
`/api/login`, the Google OAuth start/callback pair, and the AI proxy routes `/api/messages`/
`/api/messages-claude` *if* they carry no user identity today (confirm — if they attribute
cost per user, decide whether to gate; don't break mock mode / signed-out usage).

> **Caution:** `CLAUDE.md` notes several endpoints treat "no userid" as a valid signed-out
> case (cost attribution reports it as "unattributed"). Gating must not turn a legitimate
> signed-out call into a 401 where the app expects to work signed-out. Gate user-**owned-data**
> routes hard; for attribution-only `userid`, prefer "use token identity if present."

## Implementation shape (FastAPI)

- One dependency, `get_current_user`, that verifies the bearer token and returns the userid /
  user record. Protected routes declare `user = Depends(get_current_user)` and use `user.id` —
  the body's `userid` is ignored (and can be removed from those request models).
- Token mint/verify + refresh in `app/auth/`. Keep the signing secret in env (`.env`, Render
  env var), never in client or repo.
- Rate-limit `/api/login` (and `/api/register`) — even a simple in-process counter is better
  than today's unlimited guessing; note it's per-worker if you scale workers.

## Old-frontend changes (part of this phase — keep them minimal)

In `script.js`: on login, store the returned token (still via the guarded `window.storage`);
attach `Authorization: Bearer <token>` to the data/save, data/load, and other gated calls;
drop `userid` from those bodies. Handle a 401 by bouncing to the login gate. This is a handful
of `fetch` sites — the old app already funnels through a small set. **This is throwaway work**
(the frontend is replaced in phase 3) but it's how you prove the auth layer is correct against
a client you already trust before building a new one.

## Exit test (must pass before this session ends)

1. Password login and Google login both return a JWT; the old app stores and sends it.
2. Full existing-app flow works signed in: load/save profile & tracker, subscribe, deadline
   check, resume import.
3. **IDOR is closed:** a request to `/api/data/load` with someone else's `userid` in the body
   (and either no token or the *wrong* user's token) does **not** return that user's data.
   A request with a valid token returns only that token's own data. Demonstrate both.
4. Password hashing is server-side; existing accounts can still log in (migration path works).
5. Signed-out paths that are supposed to work (public catalog, mock-mode AI) still work.

## Hand-off to phase 3

When done, append a "## Phase 2 result" section to the TOP of `PLAN_3_rn.md` recording: the
token model chosen (single JWT vs access+refresh, and lifetimes), the exact login
request/response shape (endpoints, fields, where the token comes back), how the token is sent
(`Authorization: Bearer`), which routes are gated, the header/`get_current_user` contract the
RN client must satisfy, and the password-migration approach. Then tell the user the path and
stop. Do not start phase 3.

---

## Addendum — field notes from the Phase 1 session (read before starting)

Concrete specifics found while actually building Phase 1, to save Phase 2 the rediscovery.
Nothing here overrides the plan above; it makes the "where exactly" answerable.

**1. There is ONE place to mint the token for all three login paths.** `_login_payload(record)`
in **`app/core.py`** is the single response builder returned by every login-completion handler:
`handle_login` (`app/routes/account.py`, password), and `handle_google_session` +
`handle_google_finish` (`app/routes/google_oauth.py`, Google). Add the JWT to what
`_login_payload` returns (or wrap it) and all three converge for free — this is exactly the
"password and Google converge on one step" the plan asks for, already unified. Each path calls
`ensure_trial_started(...)` before building the payload; keep that order. `_login_payload`
already returns `userid` — keep returning it (the client still uses it for display); just add
the token alongside.

**2. Exact inventory of where `userid` enters today** (what you're switching to token-derived):
- **Body `userid`:** `/api/data/save`, `/api/data/load`, `/api/account/location`,
  `/api/calendar/sync`, `/api/subscription/*`, `/api/opportunities/<id>/subscribe`,
  `/api/messages`, `/api/messages-claude`, `/api/extract-from-linkedin`,
  `/api/user-submitted-opportunities`.
- **Query `userid`:** `/api/opportunities/<id>/deadline?userid=`,
  `/api/extract-from-resume?userid=` (multipart, so it uses the query string),
  `/api/auth/google/calendar/start?userid=`.
Confirmed: **`/api/messages` and `/api/messages-claude` DO attribute cost per user AND gate on
subscription** (they call `subscription_block_reason(userid)` and
`record_interactive_cost_async(..., userid=...)`), so they are the plan's "attribution-only,
use token if present" case — do **not** hard-401 them or you break mock mode and signed-out
usage.

**3. The subscription gate is your ready-made choke point.**
`subscription_block_reason(userid)` in **`app/deps.py`** is already called at the top of every
money-spending route (messages, messages-claude, deadline, subscribe, resume). It currently
**fails OPEN on missing userid** (signed-out allowed). Compose `get_current_user` with it: for
**owned-data** routes require the token (hard 401) and pass `user.id` into everything that took
`userid`; for **attribution-only** routes use the token's userid when present but keep the
signed-out path. Feed the token userid into `record_interactive_cost_async` /
`record_user_cost_async` (both in `app/core.py`) so per-user cost accounting keeps working.

**4. Password migration — you can avoid changing the client contract entirely.** Today the
**client** SHA-256s the password (`hashPassword` in `script.js`) and sends `passwordHash`; the
server NEVER sees plaintext and stores that SHA-256 as `password_hash`. So "server-side
hashing" here cannot mean hashing a plaintext — you don't have one. Cleanest path that keeps
the old frontend working unchanged: **argon2/bcrypt OVER the client SHA-256**, i.e. store
`argon2(passwordHash)`. Migration with no lockout and no client change: on login, if the stored
value is a legacy raw SHA-256 (not an argon2/bcrypt string), compare it directly to the
incoming `passwordHash`; on match, overwrite it with `argon2(passwordHash)`. Existing accounts
upgrade transparently on next login. (If you insist on hashing the *real* password server-side,
you must change `script.js` register + login to send plaintext over TLS instead of
`passwordHash` — bigger, client-touching change. Record whichever you choose.)

**5. Client token persistence is UNVERIFIED — check it before trusting `window.storage`.**
`script.js` persists the session only via `window.storage` (10 sites, **zero `localStorage`**),
and its own comment (`script.js:40-46`) says `window.storage` **silently no-ops when it doesn't
exist** (e.g. a plain browser tab). It is unclear whether `window.storage` is even present on
the deployed site, so a token stored there may not survive a page reload — logging the user out
every visit. Verify in a real browser on **highschoolwingman.com** (the live site). If it
no-ops, store the token in `localStorage` (small, contained change) so login persists. Do not
assume the plan's "store via window.storage" works in production without checking.

**6. New deps + secrets, and they ship the moment you touch `main`.**
- Add a JWT lib (`PyJWT`) and a password-hash lib (`argon2-cffi` or `bcrypt`) to
  `requirements.txt` — Render installs on deploy (both have Linux wheels).
- Add `JWT_SECRET` (and any pepper) to `.env` locally **and** to Render's env, and add it to
  `render.yaml`'s `envVars` as `sync: false`. If it's unset, fail closed.
- **The backend is now LIVE on highschoolwingman.com (Render auto-deploys from `main`).** The
  old `script.js` is the live client until Phase 3, so a broken auth change on `main` breaks
  real users (largely minors). Build/test on a branch; keep a rollback point (a tag, or Render's
  previous deploy). Phase 1 verified prod is serving correctly (`/` and `/api/opportunities`
  200, `/admin` + `/api/agents/*` + `/api/seeds` + `/server.py` + `/.env` all 404).

**7. Do NOT gate the ops console.** `get_current_user` belongs only on `app/routes/*`. The ops
routes (`ops/admin.py`) are localhost-gated by `require_local` and not mounted in production —
leave them tokenless. Auth is an `app/` concern only.

**8. Bonus foresight for Phase 3.** The `Authorization: Bearer` model (vs cookies) is
CORS-friendly — exactly what Phase 3 needs when the Expo web build moves to a separate Render
Static Site origin. No cookie/SameSite headaches; keep it header-based. (Same-origin today, so
there is no CORS middleware yet — you'll add one in Phase 3, not here.)
