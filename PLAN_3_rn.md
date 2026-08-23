## Phase 2 result (completed 2026-08-23)

Phase 2 (`PLAN_2_auth.md`) is done: a real JWT session layer is in, the IDOR is closed, and
the old `script.js` client is updated to use it (verified in a browser — login, reload-
persist, refresh-on-401, data save/load round-trip, expired-session bounce). This is the
**exact contract the RN app must satisfy.** Everything below is live on `app/` and shipped to
Render from `main`.

**Token model chosen: access + refresh + a `token_version` revocation counter.**
- **Access JWT** (`token`), HS256, **45-min** TTL. Verified statelessly on every gated
  request — no DB hit. Claims: `sub` (userid, lowercased), `type:"access"`, `ver`, `iat`,
  `exp`.
- **Refresh JWT** (`refresh_token`), HS256, **30-day** TTL. Same claim shape,
  `type:"refresh"`. Presented ONLY to `POST /api/auth/refresh`. That route is the sole place
  `ver` is compared against the account's current `users.token_version`; a mismatch → 401,
  re-login required.
- **Revocation:** `POST /api/auth/logout-all` (bearer) bumps `users.token_version`,
  invalidating every outstanding refresh token → all sessions die within one access-token
  lifetime (≤45 min). No per-request DB read. There is **no UI wired to logout-all yet** —
  the mechanism exists for phase 3 / support use.
- Signing secret: **`JWT_SECRET`** env var (in `.env` locally; add to Render env + already in
  `render.yaml` as `sync:false`). Unset ⇒ auth fails closed (503, not 401).
- Config: `JWT_SECRET`, `JWT_ALGORITHM="HS256"`, `ACCESS_TOKEN_TTL_SECONDS`,
  `REFRESH_TOKEN_TTL_SECONDS` in `app/config.py`. Mint/verify in `app/auth/tokens.py`.

**Login request/response shape (unchanged endpoints, tokens ADDED to the response).**
The one response builder is `login_response(record)` in **`app/deps.py`** (wraps
`app.core._login_payload` and adds tokens), returned by all three login-completion paths, so
password and Google converge exactly as required:
- `POST /api/login` — body `{userid, passwordHash}` → `200 {ok, userid, firstName, lastName,
  email, location, subscription, token, refresh_token, token_type:"Bearer", expires_in}`.
- `POST /api/register` — body unchanged (`{firstName,lastName,email,userid,location,
  passwordHash,isAdult,parentalConsent,acceptedTerms}`) → same payload as login (register
  **auto-logs-in**; it used to return just `{ok:true}`).
- Google: `GET /api/auth/google/session?token=<handoff>` (returning login) and
  `POST /api/auth/google/finish` (new-account) both return the same payload. The redirect
  handoff (`?google_token=`) is unchanged.
- `POST /api/auth/refresh` — body `{refresh_token}` → the same login payload with a fresh
  token pair (and current subscription block). 401 on expired/invalid/revoked.

**How the token is sent:** `Authorization: Bearer <access token>` header on every gated
request. Never in the body or query (one exception: `GET /api/auth/google/calendar/start`
takes `?token=<access>` because a top-level browser redirect can't set a header; the server
still derives userid from the verified token, not from the URL).

**`get_current_user` contract (the RN client must satisfy this):** send the bearer access
token. Missing/invalid/expired → **401 `{"error": "..."}`**; unconfigured server secret →
**503**. On a 401 the client should call `/api/auth/refresh` once with the stored refresh
token, store the returned pair, and retry; if refresh also 401s, send the user to sign-in.
(The old client implements exactly this in `authFetch` — mirror its behavior.) There is a
soft variant, `get_optional_user`, used by attribution-only routes — see below.

**Gated routes:**
- **Hard (401 without a valid token; identity is `user.id`, body/query userid ignored):**
  `/api/data/save`, `/api/data/load`, `/api/account/location`, `/api/calendar/sync`,
  `/api/auth/google/calendar/start` (token via query), `/api/opportunities/<id>/deadline`,
  `/api/extract-from-resume`, `/api/extract-from-linkedin`,
  `/api/mailing-list/subscriptions`, `/api/opportunities/<id>/subscribe`,
  `/api/subscription/status|checkout|cancel|redeem-promo`, `/api/auth/logout-all`.
- **Soft (`get_optional_user`: use token identity if present, never 401 — preserves signed-
  out / mock mode):** `/api/messages`, `/api/messages-claude`, `/api/mailing-list/status`,
  `/api/user-submitted-opportunities` (userid is provenance only),
  `/api/subscription/validate-promo`.
- **Public (unchanged):** `/api/opportunities`, `/api/register`, `/api/login`, the Google
  OAuth start/callback pair. Ops routes (`ops/admin.py`) are **not** token-gated — localhost-
  gated only, never mounted in prod. `get_current_user` is an `app/` concern only.

**Password migration approach: argon2 OVER the existing client SHA-256, no client change.**
The client still SHA-256s and sends `passwordHash`; the server now stores
`argon2(passwordHash)` (`app/auth/passwords.py`). Legacy rows (bare 64-char hex SHA-256) are
detected on login, verified by constant-time compare, and **transparently rewritten to
argon2 on that successful login** — no lockout, no pre-migration, no client change. Verified
end-to-end. New deps: `PyJWT`, `argon2-cffi` (in `requirements.txt`; Linux wheels, Render
installs them).

**One manual Supabase step (revocation only):** run **`auth_schema.sql`** in the Supabase SQL
editor — it adds `users.token_version integer not null default 0`. Auth works fully without
it (code reads a missing value as 0, so login/refresh/IDOR-fix all work); only session
revocation is inert until it runs. `create_user` deliberately does NOT write the column
(keeps registration working pre-migration). **As of this writing it has NOT been run** —
confirm with the user before relying on logout-all.

**Rate limiting:** in-process sliding window on `/api/login` (10/IP/5min) and `/api/register`
(10/IP/hr) — `app/auth/ratelimit.py`. Per-worker (documented); a shared store is future work.

**New/changed files:** `app/auth/` (tokens, passwords, dependencies, ratelimit, `__init__`),
`app/routes/auth.py` (refresh + logout-all), `auth_schema.sql`; edits to `app/config.py`,
`app/core.py` (`bump_token_version`, `update_password_hash`), `app/deps.py` (`login_response`),
`app/main.py` (auth router + HTTPException→`{"error"}` handler), every gated router, and
`script.js` (token storage in **localStorage**, `authFetch`, all gated fetch sites). Built and
tested on branch **`phase-2-auth`** (not yet merged to `main`).

---

# Plan 3 of 3 — Rebuild the frontend in Expo (React Native + web)

> **Read this first.** Final phase of a 3-phase migration (see `PLAN_1_decompose.md` for the
> full picture and locked decisions). Read `CLAUDE.md`, then this file's "Phase 2 result"
> section (appended by the phase-2 session — it pins the exact auth contract this frontend
> must satisfy), then the rest of this file.

## What phases 1 & 2 left behind (assumed starting state)

- **Phase 1:** `server.py` is now a FastAPI app (`app/`) with domain routers; offline agents
  live in a non-shipped `ops/` package. See `PLAN_1_decompose.md` `## Phase 1 result`.
- **Phase 2:** there is a real **JWT session-token** auth layer. Login (password + Google)
  returns a token; every user-data route derives `userid` from the verified token; the IDOR is
  closed. The exact token model, login request/response shape, header format, and gated-route
  list are in the `## Phase 2 result` section at the **top of this file** — that is the
  contract the RN app codes against.

> If `## Phase 2 result` is missing, phase 2 did not complete — stop and confirm with the user.
> This phase must not be built against the old insecure contract.

The **API is now stable and authed**, which is the whole reason this phase comes last: the
frontend is built **once**, against the final shape, with auth as day-one behavior rather than
a retrofit.

## Phase 3 goal

Replace the vanilla-JS SPA (`index.html` + `script.js` 5,969 lines + `styles.css`) with an
**Expo** app that targets iOS, Android, and web from one codebase. The RN rewrite **is** the
frontend modularization — the global-functions-plus-inline-`onclick` blob becomes screens,
components, and reusable logic modules.

## Locked decisions from the design session

- **Expo** (with `expo-router`) + **React Native Web** so one codebase covers web + native.
  Web build → **Render Static Site**; native builds → **EAS**, distributed via the app stores
  (never Render). The three clients all hit the same Render-hosted FastAPI API.
- **Payments deferred.** Do not build subscription/paywall UI. The backend gate is open; the
  app should assume access. Leave a clean seam to add it later, nothing more.
- Auth token lives in **`expo-secure-store`** on native (proper secure storage — something the
  old web `window.storage` couldn't do), and the web equivalent on RN-web. One API-client
  interceptor attaches `Authorization: Bearer <token>` to every request per the phase-2
  contract.

## Open decision to make at the start of this session (and record it)

**One codebase for all three platforms, or keep a separate web app and add native?** The
design session left this open. Default recommendation: **one Expo codebase (RN + RN-web)** —
it's why Expo was chosen. But if `script.js`'s web-specific pieces (e.g. the vendored
`walkthrough.html` self-extracting film, see `CLAUDE.md`) prove painful under RN-web, a
split-web option is the fallback. Decide up front; it drives how much logic is shared vs.
platform-specific.

## Decisions recorded at session start (2026-08-23)

- **One Expo codebase (RN + RN-web)** for all three platforms — the default recommendation.
  Web-only assets (the `walkthrough.html` film, Tailwind CDN) get native-appropriate
  equivalents or are gated web-only; they do not block the shared codebase.
- **The Expo app lives in `frontend/`, NOT the repo root.** The FastAPI package already
  owns `app/`, and expo-router also defaults to an `app/` routes directory — scaffolding
  Expo at the root would collide. Web build output (`frontend/dist`) is what deploys to the
  Render Static Site.
- **Built in parallel with Phase 2.** Auth-independent work (Expo scaffold, salvaged pure-TS
  logic modules, UI screen shells against a stubbed `ApiClient` interface) proceeds now.
  Auth-dependent work (login/register wiring, the Bearer/401 interceptor, final
  OpenAPI-generated client) is held behind that single `ApiClient` seam until the Phase 2
  contract lands, then filled in without touching the screens or salvage modules.

## Progress (session 2026-08-23, built parallel to Phase 2)

Done, all auth-independent, `tsc --noEmit` clean and `expo export -p web` produces `dist/`:
- **Scaffold**: Expo SDK 57 + TS in `frontend/`, converted to expo-router + RN-web,
  `expo-secure-store` installed. Routes: `app/_layout.tsx`, `app/index.tsx` (auth-gate
  placeholder → `/(app)`), `app/login.tsx` (placeholder), `app/(app)/_layout.tsx` (tabs) +
  Home/Finder/Tracker/Profile screen shells.
- **The `ApiClient` seam**: `src/api/ApiClient.ts` (interface), `src/api/httpClient.ts`
  (impl — Phase-1 methods `getOpportunities`, `getDeadlineCheck`, `callGemini`,
  `callClaude`/`callClaudeDetailed` work today incl. mock mode; auth methods are stubs; the
  Bearer header + 401 handling are isolated to `setAuthToken`/`authHeaders`/`request`).
- **Salvaged pure-TS logic** (`src/lib/`): `extractJSON`, `constants`
  (`ALL_BUCKETS`/`PROFILE_SUFFICIENT_LENGTH`/`VALID_SUBJECTS`), `grade`, `kinds`
  (`KIND_CONFIG`/`ACTIVE_KINDS`), `ranking` (`preFilter`/`inferSubjects`/`rankCandidates`/
  `extractProfileBasics`), `profile` (`synthesizeProfile`/`repairProfileText`/
  `profileHasTruncatedTail`/`assessProfileReadiness`), `profileChat` (openers-cached /
  follow-ups-live behavior preserved), `tracker` (`extractTrackerInfo`/`findBucketForKind`/
  `applyDeadlineCheckToInfo`). All model access is dependency-injected → pure & testable.

**Auth layer wired against the Phase 2 contract (2026-08-23, verified in-browser).**
- Token storage: `src/api/tokenStore.ts` — expo-secure-store (native) / localStorage (web).
- Password: `src/api/hash.ts` — expo-crypto SHA-256 hex (matches the contract's client hash).
- `src/api/httpClient.ts` now implements `initAuth`/`login`/`register`/`logout`/`loadData`/
  `saveData` and the **401 → single shared refresh → retry → AuthExpiredError** flow (Bearer
  header, `{"error"}` parsing). `src/auth/AuthContext.tsx` + the `app/index.tsx` gate,
  `(app)/_layout.tsx` guard, real `app/login.tsx` (login/register + consent), Home authed
  round-trip, Profile logout.
- **Backend change:** added `CORSMiddleware` to `app/main.py` (env `CORS_ALLOW_ORIGINS`,
  default `*`; bearer auth ⇒ credentials off). The RN-web client is a separate origin from the
  API in dev AND on Render (Static Site → Web Service), so this was required.
- Verified end-to-end in the in-app browser against a live `uvicorn app.main:app`:
  register→auto-login→app, UI login, reload-persistence, Bearer data save/load round-trip,
  garbage-access-token→refresh-rotates-and-recovers, both-tokens-garbage→bounce-to-login.
  Also confirmed the raw API contract by script (register/login/refresh/data/401s all match).
  Test accounts created in Supabase: `rn_browser_test_a1`, `rn_contract_test_*`.

**Screens wired to the salvaged logic (2026-08-23, verified live).**
- **Finder** (`app/(app)/finder.tsx`): kind picker → free-text → inferSubjects → preFilter →
  rankCandidates → ranked cards (tier badge, reason, Open link, Add to tracker). Degrades to
  keyword-only pre-filter if the AI rank fails.
- **Tracker** (`app/(app)/tracker.tsx`): items grouped by `ALL_BUCKETS`, reload-on-focus,
  remove, and the on-demand cross-user deadline check. Backed by `src/api/trackerStore.ts`
  (server-persisted via the data key/value store).
- **Profile** (`app/(app)/profile.tsx`): profile card + inline chat (openers batch, live
  follow-ups, transcript incl. bot lines), synthesis on finish, "Tidy it up" repair, logout.
- Verified two ways: (a) full auth UI click-through on Metro earlier; (b) a deterministic
  `frontend/scripts/verify.ts` (`npx tsx`) that runs the exact ported modules against the live
  backend — getOpportunities(1239) → inferSubjects → preFilter → rankCandidates(10, e.g. MIT
  BWSI with grounded reasons) → assessProfileReadiness → synthesizeProfile → deadline check.
  ALL PASSED. Finder also confirmed in-browser loading 1239 real opps with `/api/messages` 200.
- **Backend note (not a Phase-3 client bug):** `GET /api/opportunities/<id>/deadline` returns
  **502** live (the on-demand deadline check errors server-side; every other Supabase call is
  200). The RN client catches it and shows "No deadline info available". Worth an ops look.
- **Preview flakiness observed:** Metro's HMR websocket in the in-app browser repeatedly
  full-reloaded the app ("Disconnected from Metro 1006"), resetting state mid-search. The
  static export (`expo export -p web`, the actual Render Static Site artifact) has no such
  websocket; verification used it + the Node script.

**Google sign-in wired (2026-08-23).**
- **Backend change (`app/routes/google_oauth.py` + `app/services/google_oauth.py`):** the
  sign-in `/callback` historically redirected to the backend-root SPA (`/?google_token=`).
  It now accepts an allowlisted `app_redirect` at `/start` (kept in a state-keyed map) and
  sends the one-time token to the app instead — web origin or native `wingman://` scheme.
  Allowlist defaults to the native scheme + local dev origins; override in prod with
  `GOOGLE_APP_REDIRECTS`. An un-allowlisted redirect is silently ignored (falls back to the
  SPA root) so this can't become an open redirect. `/start` still always proceeds to Google.
- **Client:** `src/auth/googleSignIn.ts` (web = full-page redirect, native =
  `WebBrowser.openAuthSessionAsync`), `googleStartUrl`/`googleSession`/`googleFinish` on the
  ApiClient + AuthContext, a "Continue with Google" button on `login.tsx`, and the
  `app/google-auth.tsx` completion screen (resolves the handoff → enters app, or collects
  consent + location for a new account → `/finish`). Installed `expo-web-browser`.
- Verified: backend `/start` → `accounts.google.com` (302) with the app_redirect allowlist
  gating the callback target; `/session` & `/finish` return `400 {"error"}` on a bad token;
  the login screen shows the Google button; and `/google-auth?google_token=bogus` drives the
  completion screen through resolve → backend 400 → the "sign-in link has expired" error UI.
  tsc clean; static export includes `/google-auth`.
- **Not verifiable here (needs config, not code):** the actual Google account round-trip.
  Google must have the backend host's `…/api/auth/google/callback` registered as an authorized
  redirect URI (same requirement the old SPA already had — the redirect_uri is derived from
  the request Host). The `app_redirect` is NOT a Google redirect_uri, so it needs no console
  entry. On Render, set `GOOGLE_APP_REDIRECTS` to the static-site origin.

**Full UX rebuild + Render deploy config (2026-08-23).**
- **Design system** `src/ui/` faithfully ports the original app's "BENTO & POP" identity —
  cream canvas, navy ink/borders, hard offset "pop" shadows, accents (lime/orange/purple),
  Space Grotesk (display) + Plus Jakarta Sans (body) via `@expo-google-fonts`. `theme.ts`
  (tokens + `popShadow`/`softShadow`) and `components.tsx` (`Screen`, `Txt`, `PopCard`,
  `PopButton`, `Chip`, `Badge`, `Field`). **Font import gotcha:** import each weight from its
  subpath (`@expo-google-fonts/plus-jakarta-sans/400Regular`), NOT the package barrel — the
  barrel bundles all 14 weights and fails to resolve.
- **Screens rebuilt** on the system: styled auth (`login.tsx` hero + form + Google),
  Dashboard (greeting, stat tiles, profile/CTA cards), Finder (kind grid → describe →
  ranked results, staged), Tracker (List + **Calendar** month view fed by saved deadline
  dates), Profile (card + chat + tidy-up), google-auth completion, and an icon tab bar.
  `trackerStore` now persists deadline dates onto items for the calendar.
- **Render (monorepo, two services):** `render.yaml` adds a `runtime: static` site
  (`wingman-web`) that runs `expo export -p web` and serves `frontend/dist` with an
  index.html rewrite; `EXPO_PUBLIC_API_BASE` is wired from the API service host (client
  prepends https:// to the bare host). Set `GOOGLE_APP_REDIRECTS` on the API to the static
  origin once live. Native still ships via EAS.
- Verified: production `expo export` builds all routes clean; login/dashboard/finder render
  in-browser with the new styling; tsc clean throughout.

Not started: a native target run (EAS/simulator), OpenAPI-generated client, and retiring the
old frontend (backend still serves it at the root) after full parity.

**Note for the merge:** Phase 2 is editing `script.js` in place, so its line numbers move —
port by grepping function names, not line numbers. The salvaged logic above is not what
auth touches, so its content was stable.

## Salvage vs. discard (the modularization)

`script.js` is ~6k lines but **well over half is DOM glue that does not exist in a component
model** (the `showPage()` toggling, inline-handler plumbing, manual DOM building). Do **not**
port it line-by-line. Split it:

**Salvage — port to plain TypeScript modules, reused across screens:**
- Ranking / candidate logic: `rankCandidates`, `inferSubjects`, `preFilter`, `KIND_CONFIG`.
- `extractJSON` (brace/bracket-depth JSON extraction from model output) and the AI-call
  wrappers' *response-parsing* logic.
- Profile logic: `synthesizeProfile` orchestration, `assessProfileReadiness`,
  `profileHasTruncatedTail`/`repairProfile`, `PROFILE_SUFFICIENT_LENGTH` (keep this in sync
  with the server's copy — `CLAUDE.md` calls out that they must not diverge).
- Bucket/tracker model: `ALL_BUCKETS`, tracker classification, calendar date logic.
- The profile-chat flow rules (openers cached / follow-ups live; transcript includes bot
  lines) — see `CLAUDE.md`'s long note; preserve the behavior, not the imperative code.

**Discard — rewritten as components/navigation:**
- `showPage()` + `#page-*` section toggling → `expo-router` routes/screens.
- Inline `onclick=`/`onsubmit=` handlers → component event handlers.
- Manual DOM construction and `window.storage` guards → React state + `expo-secure-store` /
  async storage, and server-persisted data via the API client.
- `#page-login` / `#page-locked` full-screen replacements → an auth stack (login screen +
  an authed app stack); no paywall screen for now.

## Screen breakdown (from the app's page model)

- **Auth stack:** Login / Register (password + Google), per the phase-2 login contract.
- **Home / Dashboard** — progress, todo counts.
- **Finder / Wizard** — quiz or free-text profile → `runSearch()` / `runProfileSuggestSearch()`
  → ranked results → build tracker.
- **Tracker** — calendar + list views across `ALL_BUCKETS` (summerPrograms, internships,
  researchCompetitions, pureCompetitions, conferences, journals).
- **Profile** — the profile card + profile-chat drawer (openers/follow-ups behavior preserved).
- Landing/marketing — decide whether this stays web-only (it's desktop-authored; the
  `walkthrough.html` film is a heavy vendored web bundle that won't port to native as-is).

## API client

Generate a **typed client from the backend's OpenAPI spec** (FastAPI produces it for free).
This replaces every hand-rolled `fetch` and the `callGemini`/`callClaude` helpers. One
interceptor injects the auth header and handles 401 (refresh or bounce to login) per the
phase-2 token model. Do not hand-write request types the spec can generate.

## Cautions

- **RN-web parity for web-only assets.** The `walkthrough.html` film and any Tailwind-CDN
  styling in `index.html` don't cross to native. Plan native-appropriate equivalents or make
  them web-only.
- **Mock mode.** The backend still fabricates AI responses with no API key (`CLAUDE.md`).
  Keep the app usable in that state — don't assume live keys.
- **Keep `PROFILE_SUFFICIENT_LENGTH` and any other shared constant identical to the server's.**
  `CLAUDE.md` warns the console and app must agree on these; same applies to the RN app.
- **Retire the old frontend only after parity.** Keep `server.py`'s static serving (or the old
  Render static assets) until the Expo web build reaches feature parity, then cut over.

## Exit test

1. Expo app runs on web (Render Static Site build) and at least one native target (EAS dev
   build or simulator).
2. Full flow works against the authed API: register/login (password + Google), token stored in
   secure storage and sent on every request, profile & tracker load/save, finder search,
   deadline check, mailing-list subscribe, resume import.
3. A signed-out user can't reach user data; a 401 bounces to login.
4. Runs in mock mode (no AI keys) without crashing.
5. Old vanilla-JS frontend can be retired (feature parity confirmed).

## Hand-off / close

When done, update `CLAUDE.md` to describe the new architecture (FastAPI `app/`, `ops/` local
agents, JWT auth, Expo frontend, Render Web Service + Static Site + EAS) and remove/repoint the
now-stale "three-file frontend" description. Tell the user the migration is complete.
