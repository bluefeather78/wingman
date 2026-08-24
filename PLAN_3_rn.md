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

## Session handoff — pixel-parity pass IN PROGRESS (2026-08-23)

Everything below is committed on `main` (last commit `e5c506a`). The RN app in `frontend/`
now reproduces the live web app's design ("BENTO & POP") and is wired to the same backend
data. A side-by-side parity pass against `:8000` got the **authed screens** matching; the
**landing page and a wide-screen width bug are the main things still off**.

### How to run + verify
- Backend (serves old app at `:8000` AND the API the RN app calls):
  `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
- RN web (Metro): from `frontend/`,
  `EXPO_PUBLIC_API_BASE=http://127.0.0.1:8000 npx expo start --web --port 8081`
  (add `--clear` after installing deps; new npm deps need a Metro restart, plain edits
  hot-reload). Then compare `localhost:8081` (RN) vs `localhost:8000` (real).
- Test account: **userid `shamabildikar`** (real account with a populated profile + 5 tracked
  items — the password is the user's, not stored here). Its data is what to compare against.
- `cd frontend && npx tsc --noEmit` must stay clean.

### Data sharing (the key integration — DONE)
The RN app reads/writes the **same backend data keys and shapes** as the web app, so both
frontends share state (required for cutover):
- **Profile**: key `student-profile` → `{synthesized, updatedAt, chatRounds}`.
- **Tracker**: key `hs-tracker-data` → a JSON **string** of a 6-bucket object
  (`{summerPrograms, internships, researchCompetitions, pureCompetitions, conferences,
  journals}`), each item carrying `id/name/type/status/reviewStatus/meta/importantDates[]/
  wasEstimated/applyUrl/actionItems/...` (see `src/api/trackerStore.ts` and the shape at
  `script.js` ~line 5552). Verified: Shama's 5 items appear in the RN app.

### Design system (`frontend/src/ui/`)
- `theme.ts` — tokens matched to `styles.css` + screenshots: cream `#FBF8F3`, navy `#1D4E89`,
  ORANGE primary `#F79256`, lavender inputs `#EEF0FB`, teal `#00B2CA`, `popShadow`/`softShadow`.
  Fonts Space Grotesk + Plus Jakarta Sans (import **per-weight subpath**, not the barrel).
- `components.tsx` — `Screen`, `Txt`, `SoftCard` (content), `PopCard` (bordered), `PopButton`
  (orange primary, pop shadow), `Chip`, `Badge`, `Field` (lavender), `ProgressBar`.
- `NavBar.tsx` — branded top nav; `(app)/_layout.tsx` uses it + `<Slot>`. **Branded tab names
  are load-bearing: Home Base / My Vibe / Fresh Finds / Quest Log** (not Home/Profile/…).

### Screens — parity status (verified in-browser at 1280px)
- **Login** (`app/login.tsx`): MATCHES (centered card, BETA badge, notice, Google, form).
- **Home Base** (`app/(app)/index.tsx`): MATCHES — story card when profile exists, "What
  You're Chasing" legend (Happening Now / Future Event) + Look for Fresh Finds, 3 status
  pills + "and beyond".
- **Fresh Finds** (`app/(app)/finder.tsx`): MATCHES core — auto-suggest on entry ("Finding
  your matches…"), rich result cards (category + ⚡STRONG FIT + ✓WELL REVIEWED badges, WHY IT
  FITS, meta pills), "Deepen your story" banner, "In Quest Log" state. Also has kind grid,
  branching quiz, filtered form (grade/state/cost/format).
- **Quest Log** (`app/(app)/tracker.tsx`): MATCHES — per-bucket horizontal month-timeline
  calendar with type-coloured event cards; rich list cards (predicted-dates banner,
  two-column date table, FUTURE EVENT pill + Apply).
- **My Vibe** (`app/(app)/profile.tsx`): MATCHES — "story is ready" banner, basics tiles
  (grade/state/gender via `extractProfileBasics`), INTERESTS & EXPERIENCE prose, chat.

### KNOWN GAPS — RESOLVED in the 2026-08-23 pixel-parity session (commit 35d8a9e)

All four gaps above were closed, verified with a headless Playwright harness that logs both
frontends into the same account and screenshots every screen at 1280 and 1920 (harness lives
in the session scratchpad; the approach: inject the RN token pair into localStorage via
`wingman.access_token`/`wingman.refresh_token`, grow the viewport to the RN inner scroll
height for full-page captures):

1. **Width bug fixed** — nav pill + content share a centered `APP_MAX_WIDTH = 896` column
   (max-w-4xl, what `:8000` actually uses — not 1140; the landing uses 1100 and got
   `LANDING_MAX_WIDTH`). NavBar is the live app's floating pill (sticky, blue glow,
   drawn-favicon logo, teal 👤 badge opening an account drawer with Log out).
2. **Landing rebuilt** section-for-section (one-line 48px hero, centered yellow badge,
   3px-navy-bordered audience pop-cards, film poster frame, feature cards, gradient CTA,
   founder card, footer).
3. **Fresh Finds**: filter row (Only untracked + Type/Cost/Season/Format facet dropdowns)
   and the old ⭐ Save Match selection model + fixed bottom "Add to my tracker →" bar;
   result cards match resultCardHTML (border-4 slate-900 rounded-3xl, violet kind pill,
   yellow Strong Fit, WHY IT FITS left-bar block, indigo-200 meta pills, In Quest Log tag).
   NOT ported: the AI "Your Profile" tag facet (needs the profile-tag extraction slot).
4. **Quest Log list**: ⭐ save / ✕ delete icon-btns, Saved for Later section, year-tag +
   two-column date rows with "(cont.)", Show details expander, predicted/stale banners,
   status-then-date sort. Calendar: month-cards (#F8FAFC/#CBD5E1, indigo current month)
   with the exact assignCalendarColors palette — colors assigned from RAW bucket order
   (sorting first flips them; the calendar deliberately uses unsorted entries).

**Logic parity fixed alongside pixels** — `src/lib/status.ts` is a verbatim port of
script.js computeProgressStatus (incl. running+wasEstimated → Future Event),
getDisplayMilestones, earliestUpcoming, computeStats (saved-for-later excluded via the
shared `hs-tracker-saved` key), upcoming/beyond selection. Before this the RN home showed
"6 tracked / 2 Happening Now" against the old app's "0 Happening Now" (no-date items were
misclassified).

**Backend fix found by the parity run (commit 2a0bbf3):** the on-demand deadline endpoint's
502 was the Phase-1 extraction selecting/patching a nonexistent `last_checked_at` column
(the real column is `dates_last_checked_at` — check_deadlines.py and script.js both use it).
Fixed in `app/services/deadlines.py` + `app/routes/opportunities.py`; verified 200.

**Round 2 (same day, commit 150d11d)** — moved from screenshot eyeballing to a Playwright
**computed-style diff** (`compare/cssdiff.js` in the session scratchpad: matched elements
in both DOMs, getComputedStyle side-by-side; note the noise classes — RN reports
fontWeight 400 because weight is baked into the font file, and 9999px vs 999px radii are
identical). It caught: buttons are 16px/24 weight-700 (48px tall), square orange buttons
KEEP the 2px navy border (only pill CTAs set border:none inline), card h2s are slate-900
(the body tag's Tailwind class beats styles.css's navy), and several missing line-heights.
Also: the account drawer is now the full #profilePanel port (location save via new
`ApiClient.saveLocation`, live subscription line, Legal/Contact/About), logout lands on
/landing (navigate BEFORE clearing the session or the (app) guard's /login redirect wins),
the tab is titled "Wingman" with the real favicon (expo-router Head + favicon.png rendered
from favicon.svg), and Home gained the See-all-tasks modal with persisted status cycling.

**Round 3 (same day, commit ec2b890)** — user-reported details: `src/ui/icons.tsx` copies
the live app's inline stroke SVGs path-for-path via **react-native-svg** (new dep — Metro
restart needed; icon approximations from Ionicons read wrong, the Quest Log calendar
especially); the Logo is redrawn from favicon.svg's REAL geometry (**four** bars + glow-halo
dot, #F97316/#FACC15 — the 3-bar version came from the hero's inline variant); My Vibe
prose is **PlusJakartaSans_600SemiBold** (`.vibe-value.vibe-body` = 600, new font face
loaded); the finder's **"Your Profile" facet** is implemented — it reads the
`filterTags.enrichedTags` the old app caches on the shared student-profile record (free),
scores visible results with the same batch-scoring Gemini prompt (cached per tag+ids), and
renders "PROFILE MATCH • RANK #N" with the indigo bar (WHY IT FITS bar is yellow); and both
drawers **slide in from the right** via the new `RightDrawer` (account 300ms, story 250ms).

Remaining, still open: profile Quick-add (resume/LinkedIn import modal) and Clear-profile
are visual stubs; "Manage Plan" in the account drawer is a stub (payments deferred);
landing "See how it works" doesn't scroll to the film section; the walkthrough film itself
stays a poster (user is producing the video). Google button visually mirrors the live
app's COMING SOON treatment but stays functional.

### Other live notes
- Deadline endpoint `GET /api/opportunities/<id>/deadline` returns **502** server-side
  (backend/ops issue) — the RN client degrades gracefully.
- Google sign-in is wired (web redirect / native WebBrowser + `/google-auth`); the real
  round-trip needs the backend host's callback URL registered in Google console, and
  `GOOGLE_APP_REDIRECTS` set on the Render API service to the static-site origin. NOTE: the
  live web app's login shows Google as "COMING SOON"; the RN build makes it functional.
- Deploy: `render.yaml` defines the API web service + `wingman-web` static site
  (`expo export -p web` → `frontend/dist`). Native ships via EAS, not Render.
- Paywall + subscription screens remain **deferred** by the plan (assume access).

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
