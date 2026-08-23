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
