# CLAUDE.md — the student-facing app (`frontend/` + `app/`)

Split out of [CLAUDE.md](../CLAUDE.md) on 2026-09-04. **Read [CLAUDE.md](../CLAUDE.md) first**
— it carries the marquee rules (M8 covers ANY prompt sent to a model, M9 ANY code path that
makes a paid API call; both need approval first and a dedicated commit), the repo map, and how
to run things. Prose is verbatim from the original; the `##` headings are new — this content ran
as one unbroken 500-line block there, which is part of why the file grew unreadable.

Sibling: [CLAUDE-ops.md](CLAUDE-ops.md) — the agents, the pipeline, and the admin console.

---

## Backend API endpoints

**Backend (`server.py`)** is a `ThreadingHTTPServer` with a `GET /api/opportunities` route
plus five POST endpoints:
> **S1-1 (2026-09-04) replaced `/api/messages` and `/api/messages-claude` with one route,
> `POST /api/ai`, taking `{feature, inputs}`.** Every reference to those two endpoints below
> is historical: the model pins, the mock behaviour, the token clamps and the cost
> accounting all still apply — but the client no longer sends `system`, `userContent`,
> `useWebSearch` or `maxTokens`, and the provider is chosen from the server-side feature id.
> The prompts themselves live in [app/services/prompts.py](../app/services/prompts.py), which
> is **marquee M8**. `_FEATURE_SIGNATURES`/`classify_feature` are gone with them: cost
> attribution reads the feature id, so it is exact rather than a substring guess.

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

## Subscription, trial, and signup consent

**Subscription, trial, and signup consent.** Every account starts a **7-day free trial**
that converts to a **$9.99/month** Stripe plan. `wingman/subscription_common.py` talks to Stripe
over raw HTTP (no SDK, matching the stdlib-only philosophy) and holds the `PROMO_CODES`
dict; four POST endpoints (`/api/subscription/status|checkout|cancel|validate-promo`) sit
in `server.py`.

- The `users` table needs the columns in **[subscription_schema.sql](../db/subscription_schema.sql)**
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
  past it; the unique index in **[users_email_unique_schema.sql](../db/users_email_unique_schema.sql)**
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

## Client persistence — tokens vs server state

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

## App-open latency

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

## The load-time font flash

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

## AI call flow

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

## The profile chat, and what is cached vs live

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

## App screens and the branded tab names

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

**The landing page's walkthrough film** is [public/walkthrough.html](../public/walkthrough.html) at the repo
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
([email_schema.sql](../db/email_schema.sql) — another one-time manual DDL step, and it also adds
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
  [.github/workflows/lifecycle-emails.yml](../.github/workflows/lifecycle-emails.yml).
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
1. Run [email_schema.sql](../db/email_schema.sql) in the Supabase SQL editor.
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

