# Frontend review — `frontend/` (Expo RN + RN-web), 2026-09-02

Scope: every file under `frontend/app`, `frontend/src`, `frontend/scripts`, plus `package.json`, `app.json`, `tsconfig.json`. 53 tracked files, ~11.5k lines. `frontend/dist` is **not** tracked (gitignored, `frontend/.gitignore:8`) but a 7.1 MB export from 2026-09-01 sits on disk and was used for bundle measurements. `npx tsc --noEmit` exits 0. No test files exist. All paths below are relative to `frontend/`.

## Summary table

| # | Sev | Area | Finding | Where |
|---|-----|------|---------|-------|
| 1 | **High** | Security/cost | Every AI system prompt ships in the bundle and the server forwards client-supplied `system`, `useWebSearch`, `maxTokens` verbatim; the Claude route adds `web_search` with no `max_uses`. Any bearer (7-day trial, no card) can replay/replace prompts with paid search on. | `src/lib/{profile,profileChat,profileTags,ranking,tracker}.ts`, `app/(app)/finder.tsx:164`; server `app/routes/ai.py:72-79,99-110` |
| 2 | **High** | Security | Access JWT placed in a URL query string for the Calendar connect flow (history, proxy/access logs, Referer). | `src/api/httpClient.ts:660-664` |
| 3 | Med | Security | Tokens + identity (name, email, plan) in `localStorage` on web — XSS-readable. Prod is single-origin so httpOnly cookies are feasible. | `src/api/tokenStore.ts:15-36,66-83` |
| 4 | Med | Auth | A transport failure during refresh is treated as "revoked": `refreshOnce` returns false on `catch`, and both boot paths then `forgetSession()`. Offline at launch = logged out with a valid access token. | `src/api/httpClient.ts:257-263,327-337` |
| 5 | Med | Auth | No request timeouts / `AbortController` anywhere; a hung fetch hangs the deadline-refresh loop forever. `withTimeout` on 3 chat calls only rejects the caller — the server still bills. | `src/api/httpClient.ts:236-242`, `src/lib/profileChat.ts:92-99` |
| 6 | Med | Cost | "Check for updates" guard is component state; expo-router remounts on each visit, so leave-and-return re-enables the button while a pass (N × ~$0.07) is still running → concurrent passes. | `app/(app)/tracker.tsx:98,220-226` |
| 7 | Med | Cost | Retry multiplication: parse-retry inside `callGeminiJSON` × outer retry = up to 4 paid calls per search and per tracker add; enrichment up to 4 rounds; no global budget. | `src/lib/aiJson.ts:32-36`, `app/(app)/finder.tsx:598-608,746-752,837-846`, `src/api/trackerAdd.ts:47-56`, `src/lib/profileTags.ts:153-164` |
| 8 | Med | Logic | Grade parser matches bare `junior`/`senior`/`middle school` → "junior varsity", "senior citizens", "tutor middle school kids" set a HARD grade filter (client `preFilter` and server `/api/match`). | `src/lib/grade.ts:23-25`; consumers `src/lib/ranking.ts:88-92`, `src/lib/profileDerived.ts:118` |
| 9 | Med | Logic | Add path sorts `importantDates`; refresh/sync path does not, yet carries `googleEventId` by **index** → after a refresh whose order differs, calendar event ids attach to the wrong date and `changed` flips true on order alone. | `src/api/trackerAdd.ts:105`, `app/(app)/finder.tsx:895` vs `src/api/trackerStore.ts:331-348` |
| 10 | Med | Logic | Synthesis failure silently persists raw transcript concatenated onto the profile as if the merge succeeded, then spends ~5 background calls deriving slots from it. | `app/(app)/profile.tsx:202-208,213-228` |
| 11 | Med | Logic | Malformed `date_iso` (only truthiness checked) makes `daysUntil` NaN → every comparison false → card reads **Happening Now**; calendar renders `NaN`. | `src/lib/status.ts:23-28,101-108`, `src/api/trackerStore.ts:332`, `app/(app)/tracker.tsx:774` |
| 12 | Med | Perf | Full catalog (~1.2 MB JSON, 1330 rows incl. summaries) fetched on **every** Finder mount and again by the Tracker search drawer; no client cache. | `app/(app)/finder.tsx:340-366`, `app/(app)/tracker.tsx:356-367` |
| 13 | Med | Perf | `@expo/vector-icons` barrel pulls every icon family's glyph map into JS (~320 KB, 13,033 entries measured) + 17 icon .ttf files (4.5 MB) into `dist`, and fetches `Ionicons.ttf` (390 KB) at runtime — for 4 glyphs. The app already has hand-authored SVG icons. | `src/ui/NavBar.tsx:1`, `app/landing.tsx:1`, `app/(app)/finder.tsx:1` (unused import) |
| 14 | Med | Dead code | Whole features ported but unreachable: behavioral `emitEvent` (never called), URL-intake path (`intakeExtractAndClassify` with `useWebSearch:true`, `submitUserOpportunity`, `slugifyTracker`), the profile-tag scoring facet in the finder (`setSelectedTag` only ever gets `null`), and a verbatim duplicate of `addCatalogOpportunity` inside `finder.tsx`. | see §4 |
| 15 | Low | Logic | `PROFILE_STALE_DAYS = 14` exported and unused; screen hardcodes `>= 30`. | `src/lib/profile.ts:16` vs `app/(app)/profile.tsx:477` |
| 16 | Low | Logic | Side effects (setState + paid Claude call) inside a `setHistory` updater. | `app/(app)/profile.tsx:353-365` |
| 17 | Low | Logic | Per-item add errors reported under "Already tracked: … (error)"; finder batch aborts mid-way and never marks the already-added ids. | `app/(app)/tracker.tsx:435-448`, `app/(app)/finder.tsx:917-947` |
| 18 | Low | Logic | Module singletons (`lastChecked`, `_lastCatalogStamp`, `sessionSearch`, `newlyAdded`) never reset on logout → next account on the same device sees the previous account's "Last checked" / cached results until reload. | `src/lib/lastChecked.ts`, `src/api/trackerStore.ts:383-387`, `app/(app)/finder.tsx:156` |
| 19 | Low | UX | "Continue with Google" is labelled COMING SOON but is live; landing footer Terms/Privacy are plain text; Enter does not submit the login form. | `app/login.tsx:92-97,141-142`, `app/landing.tsx:357-358` |
| 20 | Low | A11y | 5 `accessibilityLabel`s in the whole app; every `IconBtn` (refresh, sync, search, star, remove), the avatar, all ✕ close buttons, task delete/add, mic/voice are unlabelled; text-glyph checkboxes carry no role/state. | `src/ui/components.tsx:495-501`, `app/(app)/tracker.tsx:494-518,965-970`, `app/(app)/index.tsx:91-98,494-496` |
| 21 | Info | Tests | Zero tests. `scripts/verify.ts` is a live-backend smoke script (paid calls, hard-coded test account) excluded from tsc. | `package.json:33-38`, `tsconfig.json:15-17`, `scripts/verify.ts:76-80` |
| 22 | Info | Types | tsc clean, `strict: true`. 3 `any` (fullscreen shims), 4 `!`, 5 `as never` (route literals), 25 `as string` almost all caused by `Opportunity`'s `[key: string]: unknown`. | `src/api/types.ts:137` |
| 23 | Info | Deps | `@react-native-community/datetimepicker` in `package.json` + `app.json` plugins, imported nowhere. `npm audit`: moderate advisories only, all transitive via `@expo/config-plugins` → `xcode`, "fix" is a downgrade to expo 46 (ignore). | `package.json:10`, `app.json:33` |

---

## 1. Security on the client

### 1.1 Prompts and model instructions in the bundle — **High** (client half of a cost-abuse issue)
Every system prompt is a string literal in `src/lib` and is present verbatim in `dist/_expo/static/js/web/entry-*.js` (verified by grepping four distinctive phrases). Files/lines that build a `system` string and send it to `/api/messages` (Gemini) or `/api/messages-claude` (Claude):

| File:line | Function | Route | Live? |
|---|---|---|---|
| `src/lib/profile.ts:33` | `synthesizeProfile` (also `repairProfileText`) | Claude, `maxTokens` 4000/8000 | yes |
| `src/lib/profile.ts:101` | `assessProfileReadiness` | Gemini | **dead** (only `scripts/verify.ts`) |
| `src/lib/profileChat.ts:121` | `starterQuestionPoolFromAI` | Claude | yes |
| `src/lib/profileChat.ts:143` | `profileChatStarterQuestionsFromAI` | Claude | yes (Regenerate) |
| `src/lib/profileChat.ts:161` | `profileChatNextQuestion` | Claude | yes |
| `src/lib/profileTags.ts:96` | `enrichRequest` | Gemini | yes |
| `src/lib/profileTags.ts:203` | `extractTagsAndBasics` | Gemini, `maxTokens` 8000 | yes |
| `src/lib/ranking.ts:112` | `inferSubjects` | Gemini | yes |
| `src/lib/ranking.ts:145` | `rankCandidates` | Gemini, `maxTokens` 6000 | yes |
| `src/lib/ranking.ts:173` | `extractProfileBasics` | Gemini | **dead** |
| `src/lib/tracker.ts:274` | `extractTrackerInfo` | Gemini | yes |
| `src/lib/tracker.ts:309-321` | `intakeExtractAndClassify` | Gemini, **`useWebSearch: true`** | **dead** |
| `app/(app)/finder.tsx:164-179` | `scoreOpportunitiesForTag` | Gemini | **dead** |

Server side (read to confirm the exposure, not reviewed further): `app/routes/ai.py:72-79` passes `system`, `use_web_search` and a clamped `maxTokens` (ceiling 8000) straight to `call_gemini`; `app/routes/ai.py:99-110` does the same for Anthropic and attaches `web_search_20250305` **without `max_uses`** when the client sets `useWebSearch`. So the client-visible contract is "send any prompt, any input, search on, 8k output". A 7-day trial with no card is the only gate (and CLAUDE.md says the proxies must stay reachable signed-out for mock mode).

*Fix:* move prompts server-side behind feature ids — client posts `{feature: 'rank', inputs: {...}}`, server owns the text, `useWebSearch` and the token budget. Note this touches **MARQUEE M8/M9** (prompt text + paid-call code paths): approval and a dedicated commit. Interim mitigation that is client-only: drop the three dead prompts (§4) so the bundle stops advertising a web-search-on prompt shape.
*Tradeoff:* the pure, injectable `src/lib` design (prompts testable without a server) is lost for the prompt text; the model-call plumbing can stay injected.

### 1.2 Access token in a URL — **High**
`src/api/httpClient.ts:660-664` builds `/api/auth/google/calendar/start?token=<access JWT>&app_redirect=…` and `app/(app)/tracker.tsx:317` opens it top-level. The JWT lands in browser history, every proxy/access log on the path, and the Referer of the Google consent redirect chain. CLAUDE.md already refuses to carry `EMAIL_CRON_SECRET` in a query string for exactly this reason.
*Fix:* POST to mint a one-time, 60-second handoff token (the same shape `google_token` already uses on the way back) and put that in the URL; or POST and have the server 302.
*Tradeoff:* one extra round trip before the redirect.

### 1.3 Token storage — **Medium**
`src/api/tokenStore.ts:15-36` uses `localStorage` on web for access + refresh tokens, and `:66-83` also persists `wingman.session_user` (name, email, plan) there. Any XSS reads all three. Native uses SecureStore (fine).
*Fix:* production is ONE origin (app + API on `highschoolwingman.com`), so `httpOnly; Secure; SameSite=Lax` cookies for the refresh token are feasible with a CSRF token on POSTs; keep the access token in memory only.
*Tradeoff:* dev is cross-origin (`:8081` → `:8000`) and would need Metro's proxy or `credentials: 'include'` + CORS `Access-Control-Allow-Credentials`; native keeps SecureStore, so `tokenStore` grows a third branch.

### 1.4 Secrets / env in the bundle — OK
Only `EXPO_PUBLIC_API_BASE` (`src/api/httpClient.ts:22`). No Google client id, no API keys — OAuth is server-driven. Good.

### 1.5 OAuth handoff / open redirect / deep links — Low
- `src/auth/googleSignIn.ts:12-18` passes the app's own origin as `app_redirect`; the server allowlists (`GOOGLE_APP_REDIRECTS`, per comment at `:22`). No client-side redirect follows an attacker-controlled parameter.
- `app/google-auth.tsx:13-14` reads `google_token` from the URL. During the `pending` consent phase (`:41-42`) the one-time token stays in the address bar; a reload replays it. Low — strip it with `router.replace` once resolved.
- `app/(app)/subscription.tsx:111` assigns `location.href` from the server's `checkout_url` — trusts the server only. Fine.
- `Linking.openURL(opp.url)` / `item.url` (`app/(app)/finder.tsx:1507`, `app/(app)/tracker.tsx:975,1025`, `app/(app)/index.tsx:411,72`) — RN-web uses `window.open(url, target, 'noopener')` (verified in `node_modules/react-native-web/dist/exports/Linking/index.js:99`). No scheme check, but catalog URLs are curated and link-checked; user-submitted rows are inactive. Low.
- Deep links: scheme `wingman` (`app.json:5`); only `google-auth` and `tracker` are constructed as targets (`googleSignIn.ts:17,27`). No other deep-link handling exists.

### 1.6 DOM injection surfaces — Low
No `dangerouslySetInnerHTML`, no `WebView`, no `injectedJavaScript`, no `eval`. `app/landing.tsx:120-127` creates a raw `<iframe src=WALKTHROUGH_URL>` (fixed, same-origin in prod) and `:77-119` reaches into `contentDocument` to click its transport buttons — fragile but not injectable. `app/(app)/profile.tsx:405-427` creates a file `<input>` in the DOM; `:300-306` uses `SpeechSynthesisUtterance`. All fine.

---

## 2. Auth / session correctness (`src/api/httpClient.ts`)

**What is right:** refresh is single-flight (`:36`, `:247-266`); the 401 flow is refresh-once-then-retry-then-`AuthExpiredError` (`:271-281`); every 402 is mirrored once into the cached identity (`:57-63`, `:286`) so the paywall appears without a reload; `loadData` coalesces per microtask (`:113-125`); `saveData` writes through only after the server accepted (`:425-433`); identity cache is documented as a cache, not an authority (`tokenStore.ts:63-65`); the boot fast path still verifies server-side on the next call.

**Findings:**

- **Transport error == revoked (Medium).** `refreshOnce` returns `false` from its `catch` (`:259-261`) exactly as it does for a 401. `initAuth` then calls `forgetSession()` on both the fast path (`:328-330`, in the background, after the app already rendered) and the slow path (`:334-337`), and `request()` does too after a failed refresh (`:277-280`). Launching the app offline, or hitting a 502 from Render during a deploy, deletes tokens and bounces to `/login` with a perfectly valid access token in hand. *Fix:* return a tri-state from `refreshOnce` (`ok | revoked | unreachable`); only `revoked` forgets the session; `unreachable` keeps the tokens and lets the next request try again. *Tradeoff:* a genuinely revoked token now survives until the next successful round trip — which is the server's decision anyway.
- **Refresh-token rotation / multi-tab (Low).** Server mints a new pair per refresh and only invalidates by `token_version` (`app/routes/auth.py:3-9`, `app/auth/tokens.py:56-77`), so tab B's stale in-memory refresh token still works after tab A rotates — no lockout. But `forgetSession()` in one tab wipes `localStorage` for all tabs while the others keep in-memory tokens until reload; and a rotation in tab A is never picked up by tab B's memory (`_refresh` is loaded once at `:320-321`). Harmless today; worth a `storage` event listener if rotation ever becomes single-use.
- **No timeouts (Medium).** `rawFetch` (`:236-242`) never passes an `AbortSignal`. `refreshTrackerDeadlines` (`trackerStore.ts:476-518`) awaits each paid check serially; one hung request freezes the "Checking (3/12)…" label forever with the button disabled. The only timeouts are `withTimeout` in `profileChat.ts:92-99` (20 s on three calls), which rejects the caller but not the fetch — CLAUDE.md's "a client-side timeout still bills server-side" blind spot, reproduced. *Fix:* `signal: AbortSignal.timeout(ms)` in `rawFetch` with a per-class budget (data 15 s, AI 90 s, deadline check 120 s) and let `HttpError`/`AbortError` surface. *Tradeoff:* an aborted AI call still bills; pair with a server-side upstream timeout so both halves agree.
- **402 / 403 / 5xx / network.** 402 → `markSubscriptionBlocked()` + `HttpError` (`:286-287`) ✔. 403 → plain `HttpError`, no special case (there is no 403 path in the app today). 5xx → `HttpError`, no retry except the Finder catalog (2 quiet retries, `finder.tsx:66-69,351-357`). Network → the raw `TypeError` propagates (not `HttpError`); `getDeadlineCheckResult` maps it to `'error'` (`:569-580`) ✔; `syncTracker`/`getActionItems` swallow to `{}`/`null` (`:590-611`) ✔ by design.
- **Retry storms (Medium, cost).** `callGeminiJSON` retries the whole call once on a parse failure (`aiJson.ts:32-36`). The Finder wraps `rankCandidates` in a second retry (`finder.tsx:598-608` suggest path, `:746-752` form path) → up to 4 paid ranking calls per search when the model keeps returning unparsable JSON. `extractTrackerInfo` gets the same outer retry per item (`trackerAdd.ts:47-56`, duplicated at `finder.tsx:837-846`) → up to 4 calls per tracked item. Enrichment loops up to `ENRICH_MAX_ROUNDS = 4` (`profileTags.ts:153`). Nothing counts attempts across a user action. *Fix:* one retry total per user action, tracked in the call site; surface "AI ranking unavailable" after that.
- **Concurrent deadline passes (Medium, cost).** `refreshing` is component state (`tracker.tsx:98`); the Quest Log is remounted per visit, so leaving and returning re-enables the button while the first `refreshTrackerDeadlines` is still awaiting (`:232`). Two passes then each force a paid check per item. *Fix:* hold the in-flight promise in a module singleton (as `_refreshInFlight` does for auth) and rejoin it on remount; also restore the progress label from it.
- **`loadData` batching failure modes.** One transport failure rejects every key in the batch (`:106-111`) — deliberate and documented. A key the server omits resolves `null` and is cached as `null` (`:100-103`) ✔. Subtle race: a load that resolves after a `saveData` overwrote the cache (`:102` vs `:432`) rewinds `_dataCache` to the older value, so the next `peekData` paints stale until the next load. Low; guard with a per-key write sequence number.
- **Persisted `session_user` trust boundary.** `loadSession` does no shape validation (`tokenStore.ts:76-84`) and `initAuth` adopts it wholesale (`httpClient.ts:323-325`). A tampered `has_access:false` merely paywalls until the background refresh replaces it; `has_access` missing is treated as allowed (`app/(app)/_layout.tsx:27`) and the server 402s anyway. Acceptable. `subscriptionStatus()` casts `Record<string, unknown>` into `SubscriptionState` (`:508-512`) — add a minimal validator so a changed server shape cannot poison the cached identity.
- **Boot chatter (Info).** The fast path always POSTs `/api/auth/refresh` (`:328`), rotating tokens on every cold start even with hours of access-token life left. Harmless.

---

## 3. Logic gaps and bugs

### `src/lib`

- **`grade.ts:23-25` (Medium).** `\b(?:rising\s+)?(freshman|sophomore|junior|senior)\b` matches "junior varsity", "senior citizens", "senior project"; `middle school` matches "I tutor middle school kids" → 8. The result becomes a HARD filter: `ranking.ts:88-92` drops every row whose `grade_min/max` excludes it, and `profileDerived.ts:118` stores it in `filterValues.grade`, which `finder.tsx:539,689` forwards to `/api/match` as `grade`. A 9th-grader on JV soccer is filtered as an 11th-grader. *Fix:* require a school-year context (`\b(?:I'?m|I am|as) a (junior|senior)\b`, `(junior|senior) year`, `\brising (junior|senior)\b`), explicitly exclude `junior varsity|jv`, and drop the `middle school` catch-all. *Tradeoff:* fewer profiles yield a grade → more rows pass the filter (safer direction).
- **`status.ts:23-28,101-108` (Medium).** `daysUntil` on a malformed ISO string returns `NaN`; in `computeProgressStatus` both comparisons are false, so the item reads `in_progress` = **Happening Now**. Ingest only checks truthiness of `date_iso` (`trackerStore.ts:332`, `trackerAdd.ts:96`, `finder.tsx:886`). Calendar view then renders `NaN` for the day (`tracker.tsx:774`) and `undefined` for the month (`:798`). *Fix:* validate `/^\d{4}-\d{2}-\d{2}$/` and `!Number.isNaN(Date.parse(...))` at the three ingest points; treat invalid as absent.
- **`status.ts:62-75` (Low, design).** `cycleYearShift` projects a next cycle for `status: 'unknown'`/`undefined` too, so a one-off event with past dates that was never deadline-checked reads as recurring next year with a "Predicted dates from past cycle" banner (`tracker.tsx:981-985`). Documented as intended; flagging the false-positive class (one-time conferences, discontinued programs the checker has not reached).
- **`status.ts:38-44,62-75` — checked, correct.** `addYearsISO` clamps Feb 29; `cycleYearShift` starts at `max(1, Δyears)` and increments until the last date is in the future — no off-by-one found. `daysUntil` uses local midnight consistently with month-key computations (`:214-215,242,251`); DST is absorbed by `Math.round`.
- **`extractJSON.ts:6` (Low, cost).** `text.search(/[{[]/)` picks the first bracket even in prose ("[Note] …"), parses `[Note]`, throws, and `callGeminiJSON` pays for a second call. Prefer the earliest position where a full parse succeeds, or strip a leading `[word]` token. `:10-11` `void closeChar` is a parity leftover.
- **`profile.ts:16` vs `app/(app)/profile.tsx:477` (Low).** `PROFILE_STALE_DAYS = 14` is exported and never read; the screen hardcodes `days >= 30`. Two definitions of "stale".
- **`profileTags.ts:130-135` (Low).** Positional fallback `wanted[i]` when the echoed tag is not in `wanted` — a response that drops one tag mid-list shifts every later enrichment onto the wrong tag. The top-up loop repairs the *missing* ones but not the *misattributed* ones. Use position only when `arr.length === wanted.length`.
- **`profileDerived.ts:99-107` — checked, correct.** The shared-extract memo drops a rejected promise; in-flight dedupe keyed on text; `queueSlotWrite` serialises persist. Good.
- **`profileChat.ts:64-77,103-113` (Info).** Rotation indices are module-level and shared across accounts on one device — trivial.

### `src/api`

- **`trackerStore.ts:331-348` vs `trackerAdd.ts:94-105` / `finder.tsx:884-895` (Medium).** The add path sorts `importantDates` by `dateISO`; `applyDeadlineToTrackerItem` does not sort and carries `googleEventId` forward **by index** (`:345`) — the exact positional trap CLAUDE.md says the calendar sync already hit once. If the server's order differs from sorted order (the prompt *asks* for chronological but nothing enforces it), (a) `JSON.stringify(mapped) !== JSON.stringify(previous)` is true on ordering alone → "1 deadline updated" with no change, and (b) the next sync PATCHes event N with date M's label/date. *Fix:* sort in `applyDeadlineToTrackerItem` too, and carry `googleEventId` by a `(type,label)` key with index as fallback. *Tradeoff:* a genuinely relabelled date creates one new event once.
- **`calendarSync.ts:100-108` (Low).** `loadTrackerSaved()` swallows any error to `{}` (`trackerStore.ts:246-252`). If the saved-flags read fails while the tracker read succeeds, every saved-for-later item is treated as active and written to the student's calendar; the sweep keeps them. Use a `…Checked` variant like `loadTrackerDataChecked` and refuse to sweep.
- **`trackerStore.ts:383-387,428-433` + `src/lib/lastChecked.ts` (Low).** `_lastCatalogSyncAt`, `_lastCatalogStamp` and the "Last checked" label are module singletons never cleared by `forgetSession`. Account B signing in on the same device after A sees A's "Last checked: Sep 1 …" (`tracker.tsx:101`) and is throttled out of its first non-forced sync. Same for `sessionSearch` (`finder.tsx:156`) — A's match list is shown to B if B's profile text happens to be identical (empty profiles!): both have `profileKey === ''`, so `sessionSearch.profileKey !== text` is false and **B sees A's cached results**. *Fix:* register an `onSessionLost` listener in each module (or a `resetSessionState()` called from `forgetSession`).
- **`trackerStore.ts:398-451` + `AuthContext.tsx:61-64` + `index.tsx:183` (Low, perf).** On cold start the forced sync and Home Base's focus sync start in the same tick; the throttle stamps only after completion, so both run: two `/api/tracker/sync` GETs. Hold the in-flight promise and rejoin.
- **`httpClient.ts:486-499` `submitUserOpportunity`** — never called by any screen (the URL-intake feature was removed in P8). Dead API surface; see §4.

### Screens

- **`app/(app)/profile.tsx:202-208` (Medium).** When `synthesizeProfile` throws (truncated twice, timeout, 402, network), the catch builds `before + ' ' + fallbackText` and `persist`s it as the profile (`:213-218`), shows the highlight animation, and fires `refreshProfileDerived` (≈5 model calls) on that text. The student sees a "successful" merge whose tail is raw transcript. *Fix:* keep the transcript in state, show "Couldn't fold this into your profile — try again", do not persist or derive.
- **`app/(app)/profile.tsx:353-365` (Low).** `sendText` runs `setBusy('thinking')` and a paid `profileChatNextQuestion` **inside** the `setHistory` updater. Updaters must be pure; React can call them twice (StrictMode, concurrent re-renders) → duplicate Claude call and a duplicated bot bubble. Compute `next` from a ref and call outside.
- **`app/(app)/profile.tsx:161-164,46-55` (Low).** `persist()` spreads the `profile` state captured at mount; slots the background refresh wrote to the server since then are dropped on the next save. Today every save also changes `synthesized`, so the dropped slots were stale anyway — but a future save that does not change the text would silently wipe `filterTags`/`starterPool`. Re-read the record in `persist` (load-modify-save, like `queueSlotWrite`).
- **`app/(app)/index.tsx:128-140` (Low).** `cycleActionItem` writes the whole tracker from component `data` without load-modify-save, unlike every mutator in `trackerStore.ts:594-596`. A free sync that landed after this screen's `data` was captured is clobbered by the next pill tap. Route through a store mutator (`updateTrackerItem` exists and is unused).
- **`app/(app)/tracker.tsx:435-448` (Low, wording).** A thrown error inside `addSelected` is pushed into `duplicates` and reported as `Already tracked: <name> (<error message>)`. A network failure is thus reported as "already tracked".
- **`app/(app)/finder.tsx:917-947` (Low).** In `addSelectedToTracker` an exception from `addOneToTracker` escapes to the outer catch; `markNewlyAdded` and `setTrackedIds` never run for the ids already written → cards still say "Save Match" and the Quest Log shows no NEW badge for items that were added.
- **`app/(app)/finder.tsx:741-752` (Low, cost).** Form-path search calls `rankCandidates` even when `pool.length === 0` (empty or fully grade-filtered catalog) — a paid call with an empty candidate list that can only fail. Short-circuit before it.
- **`app/(app)/finder.tsx:1209-1211` (Low).** A `<Pressable>` wraps the description `TextArea`; on native this intercepts touches/focus and adds a button role around a text field.
- **State after unmount / stale closures.** Long awaits followed by `setState` with no alive guard: `index.tsx:139,147,151,155,162`; `tracker.tsx:235-276,299-351`; `profile.tsx:213-228`. React 19 does not warn and the terminal "Last checked" label is also written to the singleton, so no visible bug — but `tracker.tsx:232-234` progress ticks after unmount are wasted renders. `rerunThemeMatch` reads `selectedThemesRef` (good) but `homeState`/`grade`/`profileReady` from the closure at timer fire (`finder.tsx:629-651`) — acceptable.
- **Effects / repeated AI calls.** No dependency-array loop found. All per-mount derived-slot reads (`profile.tsx:147`, `finder.tsx:400-402`, `:539`, `:689`) go through the exact-text cache, so an unchanged profile costs 0 calls. The remaining per-mount cost is network, not AI (see §5).
- **Empty catalog.** Finder shows the retry card on fetch failure (`finder.tsx:1025-1038`) ✔; an empty *array* renders the hero normally and every search ends in "No matches" after the paid call above. Tracker search drawer handles `[]` ✔.
- **`app/login.tsx:92-97`** "COMING SOON" badge on a live, enabled Google button. **`app/landing.tsx:357-358`** footer "Terms"/"Privacy" are `<Text>` with no `onPress`. **`app/login.tsx:141-142`** no `onSubmitEditing` → Enter does not sign in on web.

---

## 4. Dead / leftover code

### Ported-but-unwired features
- **Behavioral event capture**: `httpClient.ts:128-155,648-657`, `ApiClient.ts:13-34,161-166` (`emitEvent`, `queueEvent`, `flushEvents`, `WingmanEventAction`, `EventInput`). `grep emitEvent app src` → zero call sites. The P-A telemetry the matcher's revealed-preference loop is meant to read is never written.
- **URL intake path**: `src/lib/tracker.ts:38-49` (`todayLabel`, `baseDomain`), `:290-341` (`IntakeInfo`, `intakeExtractAndClassify` — a full web-search-ON prompt, `slugifyTracker`), `httpClient.ts:486-499` + `ApiClient.ts:146-153,187-199` (`submitUserOpportunity`, `UserOpportunitySubmission`). Nothing calls them since P8.
- **Profile-tag scoring facet** in `finder.tsx`: `TagScore` (`:131-134`), `scoreOpportunitiesForTag` (`:160-192`), `tagKeywordMatch` (`:194-204`), state (`:326-329`), the scoring effect (`:436-466`), the filter branch (`:1003-1012`), the loading row (`:1404`), the `aiReasoning` card branch (`:1511-1518`), `activeFilterCount`/`clearAllFilters` references (`:963,968`). `setSelectedTag` is only ever called with `null` (`:968`) → unreachable, ~130 lines and one prompt.
- `src/lib/profile.ts:87-103` `assessProfileReadiness` / `ProfileReadiness` — only `scripts/verify.ts` (excluded from the build).
- `src/lib/ranking.ts:168-176` `extractProfileBasics` — superseded by `extractTagsAndBasics`.
- `src/lib/tracker.ts:127-145` `applyDeadlineCheckToInfo` — superseded by `trackerStore.applyDeadlineToTrackerItem`.
- `src/lib/status.ts:78-80,213-265` `hasProjectedDates`, `getUpcomingDeadlineItems`, `getBeyondDeadlineItems`.
- `src/api/trackerStore.ts:527-529,572-574,585-592` `countItems`, `addTrackerItem`, `updateTrackerItem`.
- `src/api/httpClient.ts:705-707` `currentUser()`.
- `src/ui/components.tsx:441-448` `Badge` ("kept for compatibility") — no users.
- `src/lib/kinds.ts:6-18` `comingSoon`, `source: 'web'`, `venueKind` — never set except via the always-true `ACTIVE_KINDS` filter.
- `src/lib/lastChecked.ts:23-26` `resetLastCheckedLabel` "only for tests" — no tests exist.
- Exported-but-only-internal (harmless, but each is a public seam nobody uses): `PROFILE_STALE_DAYS`, `profileIsSufficient`, `PROFILE_SYNTH_MAX_TOKENS*`, `REPAIR_ONLY_INPUT` (`profile.ts`); `withTimeout`, `PREDETERMINED_STARTER_QUESTIONS`, `STARTER_POOL_SIZE`, `STARTERS_PER_OPEN`, `getNextPredetermined*`, `isProfileInsufficientForAI` (`profileChat.ts`); `ENRICH_*`, `TAG_EXTRACT_MAX_TOKENS`, `dedupeTagStrings`, `enrichBudgetFor`, `mergedExtractBudget` (`profileTags.ts`); `tokenize`, `keywordScore`, `TYPE_FILTER_MIN_POOL`, `RANK_MAX_TOKENS`, `PROFILE_BASICS_FIELDS` (`ranking.ts`); `daysUntil`, `hashColor`, `UpcomingEntry` (`status.ts`); `ALL_SLOTS`, `profileDerivedIsFresh`, `SlotName`, `SlotRecord`, `FilterValuesSlot` (`profileDerived.ts`); `profileWriteInFlight`, `PROFILE_WRITE_WAIT_MS` (`profileWrites.ts`); `parseGradeLevel` (`grade.ts`); `taskKey`, `mergeActionItems`, `applyDeadlineToTrackerItem`, `applyTasksToTrackerItem`, `ActionItemBasis`, `TaskTrustTier`, `*Result` types (`trackerStore.ts`); `collectTrackedDeadlineEvents`, `CollectedEvent`, `SyncOutcome` (`calendarSync.ts`); `HttpError` (`httpClient.ts`); `googleRedirectUri` (`googleSignIn.ts`); `TokenPair` (`tokenStore.ts`); `AiRequest`, `AiTextBlock`, `SubscriptionState`, `MatchThemeInput`, `MatchResultRow` (`types.ts`); `KindConfig` (`kinds.ts`); `REVIEW_STATUS_META` (`components.tsx`).

### Duplicate helpers
- `app/(app)/finder.tsx:822-904` `addOneToTracker` is a verbatim copy of `src/api/trackerAdd.ts:35-114` `addCatalogOpportunity`, whose own comment (`:30-33`) says it was extracted "so Fresh Finds and the Quest Log cannot drift" — Fresh Finds still uses the original. They *will* drift (the finder copy already differs by the `resultKind` bucket rule at `:829`). Pass `bucket` in and delete the copy.
- `app/(app)/finder.tsx:75-82` `kindForOpp` = `src/lib/tracker.ts:29-36` `kindForOpp`.
- `app/login.tsx:172-178` `ConsentRow` ≈ `app/google-auth.tsx:120-129` `ConsentRow`.
- `app/(app)/profile.tsx:46-55` `StoredProfile` and `app/(app)/index.tsx:46-48` `StoredProfile` vs `src/lib/profileDerived.ts:58-67` `ProfileRecord`.
- `src/ui/components.tsx:347-365` `OppStatus`/`TaskStatus` vs `src/lib/status.ts:8-12` (acknowledged mirror; a single `src/lib/statusTypes.ts` would remove the "keep in sync" burden).
- `app/(app)/profile.tsx:57-61` `daysSince` vs `status.daysUntil`.

### Markers, noise, comments
- `TODO|FIXME|HACK|XXX`: none (all grep hits are `todo*` identifiers).
- Commented-out code blocks: none. Four `// eslint-disable-next-line react-hooks/exhaustive-deps` (`components.tsx:578`, `finder.tsx:416,465,484`) with **no ESLint config or script in the project** — they document intent but nothing enforces the rule.
- `console.*`: 16 `warn`/`error` (listed in the grep; all on error paths, none `console.log`). They ship un-gated to production; wrap in `if (__DEV__)` or a tiny logger.
- Unused imports: `app/(app)/finder.tsx:1` `Ionicons` (never rendered). `src/lib/extractJSON.ts:9-11` dead `closeChar`.

### Dependencies (`package.json`)
- `@react-native-community/datetimepicker` (`:10`, plugin `app.json:33`) — no import anywhere; remove both (it also drags a config plugin into every prebuild).
- `expo-constants` (`:12`) — no direct import; it is a dependency of `expo-router`, so it is not dead, but it is not something the app uses.
- No `lodash`/`moment`/`dayjs` (0 hits in the bundle).

### Files over 800 lines — suggested splits
- **`app/(app)/finder.tsx` (1786).** First delete the dead tag-scoring path (~130) and the `addOneToTracker`/`kindForOpp` duplicates (~95) → ~1560. Then: `finder/ResultCard.tsx` (card JSX `:1453-1541` + its hover/press state moved local, ~160); `finder/FilterBar.tsx` (theme facet, pool facets, backdrop, `toggleFacet` measurement `:277-294,1284-1399`, ~230); `finder/stages/{Home,Quiz,Form}.tsx` (`:1019-1253`, ~280); `src/lib/finderSearch.ts` (pure: `QUIZ_*`, `FILTER_FIELDS`, `facetValue`, `themeTagsFor`, `buildMatchBlob`, result mapping from `callMatchMapped`, `sessionSearch` cache, ~220); `finder/controls.tsx` (`LoadingRow`, `TextArea`, `SoftInput`, `SoftSelect`, `BackLink`, ~80). `finder.tsx` keeps state + `search`/`callMatchMapped`/add orchestration (~450).
- **`app/(app)/tracker.tsx` (1166).** `tracker/CalendarCard.tsx` (`:699-831` + lane styles, ~200); `tracker/ListCard.tsx` (`:834-1035` + card styles, ~260); `tracker/AddSearchDrawer.tsx` (drawer JSX `:611-693` + catalog/selection/`addSelected` state `:353-464`, ~220); `tracker/useCalendarSync.ts` (the four-state sync machine `:106-118,283-351`, ~90). Remaining screen ~350.
- **`app/(app)/profile.tsx` (861).** `profile/ChatDrawer.tsx` (drawer JSX `:635-704` + chat state/handlers `:274-379`, ~220); `profile/ImportModal.tsx` (`:399-466,706-761`, ~140); `src/lib/speech.ts` (the `SpeechRecognition`/TTS shims `:78-91,300-348`, ~70); `profile/StoryCard.tsx` (prose rendering + highlight `:480-633`, ~170). Remaining ~260 for load/merge/derived orchestration.
- `src/ui/components.tsx` (710) and `src/api/trackerStore.ts` (689) are cohesive; `trackerStore` could shed `taskMerge.ts` (`:107-148,594-689`) if it grows.

### Stale documentation (CLAUDE.md "Known gaps in the RN port")
`starterPool`/`drawStarterWindow` **are** wired (`profile.tsx:274-290`); the finder **does** regenerate `filterTags` (`finder.tsx:398-403`); Clear profile is **not** a visual stub (`profile.tsx:238-256`). Only "payments deferred" remains true.

---

## 5. Performance

### Bundle (measured on `dist/` from 2026-09-01)
- `entry-*.js`: **1,943,891 B raw, 543,552 B gzip**, 951 Metro modules, single chunk, no route-level code splitting (`app.json:22-26` static output; no `asyncRoutes`).
- **Icon fonts are the one avoidable driver.** `@expo/vector-icons` is imported via the barrel (`NavBar.tsx:1`, `landing.tsx:1`, `finder.tsx:1` unused) for four Ionicons glyphs (`settings-outline`, `play`, `expand`, `contract`). Metro does not tree-shake, so the glyph maps of **every** family ride in the JS: 13,033 `"name":codepoint` entries ≈ **320 KB uncompressed** (MaterialCommunityIcons alone is absent-by-name but FontAwesome6/Fontisto/etc. are present — verified by grepping `face-grin-tears`, `acrobat-reader`). `dist/assets` also carries 17 icon `.ttf` files (**4.5 MB**; MaterialCommunityIcons 1.3 MB) and the browser fetches `Ionicons.ttf` (390 KB) on first render of NavBar. The app already ships hand-authored SVG icons in `src/ui/icons.tsx`. *Fix:* four `<Path>`s in `icons.tsx`, drop the dependency. Expect roughly −300 KB raw JS, −390 KB runtime font, −4.5 MB dist.
- Everything else is baseline: `react-native-web` + `react-dom` + `expo-router`/`react-navigation` + `react-native-svg` + `Animated` (~800–900 KB together). No `lodash`, `moment`, `reanimated`, `gesture-handler`.
- Brand fonts: 7 files, ~650 KB, imported per-weight subpath (good); cached immutably per the server fix.

### Catalog fetching — **Medium**
`GET /api/opportunities` returns the full row set: `opportunities.json` (the diffable backup of the same table) is **1,192,950 B for 1,330 rows** — every summary, review summary, eligibility text, tags. It is fetched:
- on **every mount** of Fresh Finds (`finder.tsx:340-366`; expo-router remounts the screen on each tab visit; the server sends `no-store`, so the browser cannot cache it either);
- **again**, into unrelated state, the first time the Quest Log's search drawer opens (`tracker.tsx:356-367`), which needs only `id/name/org/type/url`.
There is no module-level catalog cache, unlike `sessionSearch`. *Fix:* a `catalogStore.ts` singleton with a TTL (server TTL is `OPPORTUNITIES_CACHE_TTL`; mirror it) shared by both screens; server-side, a `fields=` param or a slim list endpoint for the drawer. *Tradeoff:* `preFilter` scores on `summary`, so the Finder still needs the fat rows — one fat fetch per session rather than per visit is the win.

### Main-thread work
- `preFilter` (`ranking.ts:56-105`) tokenizes and scores ≤1,330 rows once per search — trivial. Not per keystroke.
- Quest Log name search filters 1,330 rows per keystroke inside `useMemo` (`tracker.tsx:408-412`) — sub-millisecond.
- Calendar lanes/colour map memoised (`tracker.tsx:706-736`). `getDisplayMilestones` runs per `ListCard` per render — cheap.

### Re-render hotspots
- **Finder result list**: cards are inline `.map()` JSX (`finder.tsx:1438-1543`) with hover/press state lifted to the screen (`hoveredCardId`, `hoveredSaveBtnId`, `pressedSaveBtnId`, `hoveredFacetKey`, `pressedFacetKey`, `:251-260`). Hovering any card re-renders the entire screen, including recomputing facet value sets for four fields (`:1348-1357`) and re-creating every inline closure. Extract a memoised `ResultCard` with local `usePopInteraction` — the pattern `tracker.tsx`'s `ListCard` already uses.
- No `FlatList`/virtualisation anywhere (`Screen` is a `ScrollView`, `components.tsx:61-69`). Fine at ≤50 tracked items and 10-at-a-time paging in the Finder (`visibleCount`, `:264`); revisit only if the Quest Log grows past ~100 cards.
- Home Base "All Your Tasks" modal renders every task of every item (`index.tsx:379-500`) — fine.

### Network calls on cold start (signed in, cached identity)
| Screen | Requests |
|---|---|
| boot | `POST /api/auth/refresh` (background) + AuthContext forced sync → `data/load(hs-tracker-data)` + `GET /api/tracker/sync` |
| Home Base | 1 batched `data/load` (3 keys — coalesces with the forced sync's load) + 1 more `tracker/sync` (throttle not in-flight-aware, §3) |
| Fresh Finds | `GET /api/opportunities` (1.2 MB) + `data/load(student-profile)` + `data/load(hs-tracker-data)` (**not** coalesced — the profile load awaits `awaitProfileWrites` first, `finder.tsx:373-374,409`) + 0 AI calls if slots are fresh |
| Quest Log | 1 batched `data/load` (2 keys) + `tracker/sync` (usually throttled) |
| My Vibe | `data/load` + `basics` slot (0 calls if fresh) |
| Manage Plan | `POST /api/subscription/status` |

### AI calls per user action (happy path → worst case)
| Action | Calls |
|---|---|
| Open the profile drawer | 0 (pool fresh) → 1 Claude (build pool of 10). Regenerate: 1 Claude. |
| Each chat turn | 1 Claude |
| Close the drawer with answers | 1 Claude synthesis (→2 if truncated) + background slot refresh: 1 Gemini `inferSubjects` + 1 Gemini merged tags/basics (→ +1–4 enrichment top-ups) + 1 Claude opener pool = **4 → 9** |
| Run a search (theme/suggest path) | 1 `/api/match` (server embedding) + 1 Gemini `rankCandidates` (→ 4 with parse+network retries); grade/subjects from cache. Each theme toggle in results repeats this after a 500 ms debounce. |
| Run a search (form/quiz path) | 1 Gemini `rankCandidates` (→ 4); 0 if the pool is empty *should* be, but is 1 today |
| Add N tracker items | per item: 1 Gemini `extractTrackerInfo` (→ 4) + 1 deadline check (free if cached, ~$0.07 if it triggers a fresh server check) + 1 action-items GET (may generate server-side, ~$0.002) — serial |
| Refresh deadlines | N × forced paid check (~$0.07 each) + N action-item GETs; **×2 if the button is re-pressed after a remount (§2)** |
| Quick-add résumé / LinkedIn | 1 Claude extraction (server) + the "close drawer" bundle above |

---

## 6. Accessibility and UX robustness (brief)
- **Labels.** `accessibilityLabel` appears 5 times in the app (`NavBar.tsx:109`, `components.tsx:328`, `landing.tsx:268,279`, `finder.tsx:1288`). `IconBtn` (`components.tsx:495-501`) takes no label prop, so the Quest Log's refresh/sync/search/star/remove buttons (`tracker.tsx:494-518,965-970`), the 👤 avatar (`NavBar.tsx:120-122`), every ✕ close (`NavBar.tsx:134`, `profile.tsx:649,712`, `tracker.tsx:620`, `index.tsx:375`), task delete/add (`index.tsx:91-98,494-496`) and the mic/voice toggles (`profile.tsx:645,695`) are announced as "button" or nothing. Add a `label` prop to `IconBtn` and `accessibilityRole="button"` throughout.
- **Checkbox/radio semantics.** Facet rows and theme rows draw ☐/☑ and ○/● as text (`finder.tsx:1345-1347,1329-1331,1380-1384`; `tracker.tsx:658-671`) with no `accessibilityRole="checkbox"|"radio"` or `accessibilityState`. `SoftSelect` (`finder.tsx:1609-1627`) is a `Pressable` list with no `combobox`/`listbox` role.
- **Keyboard on web.** Enter does not submit login (`login.tsx:141-142`; no `onSubmitEditing`). Facet panels close only via the invisible backdrop (`finder.tsx:1284-1290`) — no Escape. Chat input and add-task have `onSubmitEditing` ✔. Search drawer input `autoFocus` ✔ (`tracker.tsx:635`).
- **Focus traps.** All drawers/modals use RN `Modal` (`components.tsx:586`, `index.tsx:366`, `profile.tsx:707`); RN-web's `Modal` ships a focus trap and Escape→`onRequestClose` (verified `node_modules/react-native-web/dist/exports/Modal/ModalFocusTrap.js`, `ModalContent.js:26-34`). Covered.
- **Contrast/size.** Stat pill labels are 8.5 px orange-on-navy (`index.tsx:516`); `colors.muted` (#8A93A6) 10–12 px text on cream appears throughout (`theme.ts:49,104`). Below WCAG AA for small text.

---

## 7. Test coverage
**There are no tests.** No `*.test.*`/`__tests__`, no jest/vitest config, no `test` script (`package.json:33-38`). `scripts/verify.ts` is a live-backend smoke run (paid Gemini + Claude calls, a hard-coded test account `rn_browser_test_a1` / `s3cret-pass` at `:76-80`) and is excluded from `tsc` (`tsconfig.json:15-17`).

Pure, injectable modules with zero coverage — in rough order of risk:
1. `src/lib/status.ts` — `cycleYearShift` year-boundary + Feb 29, `computeProgressStatus` day-of boundaries, NaN behaviour (§3).
2. `src/api/trackerStore.ts` — `parseTrackerData` legacy migrations (`competitions`, `dismissed`), `mergeActionItems` tombstones + user tasks, `applyDeadlineToTrackerItem` source gate + `googleEventId` carry, `syncTrackerFromCatalog` throttle.
3. `src/api/httpClient.ts` — refresh single-flight, 401→refresh→retry→`AuthExpiredError`, 402 mirror, `loadData` coalescing and batch rejection (mock `fetch`).
4. `src/lib/grade.ts` — the JV/senior-citizens false positives.
5. `src/lib/extractJSON.ts` — truncation repair, strings containing braces, leading `[word]` prose.
6. `src/lib/profileDerived.ts` — exact-text freshness, in-flight dedupe, `queueSlotWrite` ordering, rejected shared extract.
7. `src/lib/profileTags.ts` — dedupe, wrapper-object tolerance, top-up loop termination, positional misattribution.
8. `src/lib/ranking.ts` `preFilter` — `strictEmpty`, `widened`, grade hard filter, 60/100 cut.
9. `src/lib/profileHighlight.ts` thresholds; `src/lib/profileChat.ts` rotation windows; `src/api/calendarSync.ts` `collectTrackedDeadlineEvents` (not_running/saved exclusion, year shift).

Everything above takes a `callGemini`/store function as a parameter, so Vitest with no RN mocks covers it (only `tokenStore`/`theme` touch `Platform`). Suggested first PR: vitest + ~40 cases over items 1–5.

---

## 8. Type safety
- `npx tsc --noEmit`: **exit 0**, `strict: true` (`tsconfig.json:4`), typed routes on (`app.json:36`).
- `any`: **3** — `app/landing.tsx:44,55,67` (Fullscreen API prefix shims). Acceptable; a 6-line `interface` would remove them.
- Non-null assertions: **4** — `app/(app)/tracker.tsx:716,882` (`Map.get()!` right after `has`/`set`), `src/lib/profileDerived.ts:227,250`. All locally justified.
- `as never`: **5** — `src/ui/NavBar.tsx:93,107,177` (typed routes reject the `'/(app)'` group literal; use `'/'`/`href` objects), `app/(app)/tracker.tsx:941` (`cardRef as never` to smuggle a DOM ref through `Pressable`).
- `as unknown as`: **6** — three web `boxShadow` styles in `theme.ts:116,125,134` (RN's `ViewStyle` lacks it; RN-web accepts it), `landing.tsx:161`, `finder.tsx:592`, `profile.tsx:331`.
- `as string`: **25** — nearly all read `price`, `location`, `season`, `state`, `status`, `review_status`, `review_summary` off `Opportunity`, whose index signature `[key: string]: unknown` (`src/api/types.ts:137`) makes every real column `unknown`. Declare those seven columns (they are in `OPPORTUNITIES_FIELDS`) and most casts disappear; keep the index signature for forward-compat.
- Mirrored shapes that can drift: `ImportantDate` snake_case API (`tracker.ts:51-66`) vs camelCase stored (`trackerStore.ts:12-29`) — intentional; `TrackerInfo.status` closed union vs `TrackerItem.status: … | string` (`trackerStore.ts:157`) — the `| string` defeats the union; `SubscriptionState` vs the `Record<string, unknown>` `subscriptionStatus()` returns.

---

## Appendix — verified facts used above
- `git ls-files frontend | wc -l` → 53; `git ls-files frontend/dist` → empty (untracked; `.gitignore:8`).
- `npm ls --depth=0` clean; `npm audit` → moderate only, all via `@expo/config-plugins → xcode`, "fix" = downgrade to expo 46 (do not).
- Bundle grep confirms the prompts ship: `You maintain a single, coherent running profile` (1), `You are Wingman, helping a student` (1), `You extract structured tracking data` (1), `You classify and extract structured tracking data` (1), `PROFILE THEMES` (1).
- Server forwards client `system`/`useWebSearch`/`maxTokens`: `app/routes/ai.py:72-79` (Gemini), `:99-110` (Anthropic, `web_search_20250305` with no `max_uses`).
- Server refresh: new pair per call, revocation only via `token_version` (`app/routes/auth.py:3-9`, `app/auth/tokens.py:56-77`).
- RN-web `Linking.openURL` → `window.open(url, target, 'noopener')`; RN-web `Modal` has `ModalFocusTrap` + Escape handling.
