# Highschool Wingman backend — performance & scalability review

Target: **50 RPS sustained, mixed across all endpoints including the AI proxies**, on Render
(`render.yaml`) with Supabase/PostgREST over `urllib`. Review date 2026-09-02, working tree
at `main` (HEAD = `aa766a2`). Static reading only — no server run, no network calls.

Files read completely: `app/main.py`, `app/config.py`, `app/deps.py`, `app/core.py`,
`app/auth/*.py`, every `app/routes/*.py`, every `app/services/*.py` (templates skimmed),
`supabase_common.py`, `gemini_common.py`, `claude_common.py`, `embed_common.py`,
`check_deadlines.py` (`call_claude`, `_search_round`, `research_deadlines`,
`find_program_sources`, `extract_deadlines`, `check_one`, `deadline_write_decision`),
`source_capture.py` (capture call), `sitemap_common.py` / `aggregators_common.py` (indexed),
`render.yaml`, `requirements.txt`, `frontend/src/api/httpClient.ts`,
`frontend/src/api/trackerStore.ts`, plus the screen call sites.

Installed versions (local): fastapi 0.141.1, starlette 1.6.0, uvicorn 0.52.4, anyio 4.14.2
(default thread limiter **40 tokens**, verified by querying it inside a loop), numpy 2.2.0,
Python 3.13.

---

## 0. One blocker that is not a performance issue

**`numpy` is imported by the shipped app but is not in `requirements.txt`.**

- `app/services/matching.py:34` — `import numpy as np`; `app/services/recall_query.py:259` — same.
- `app/main.py:22-25, 103-105` imports and mounts `app.routes.matching`, which imports both.
- `requirements.txt` (HEAD and `origin/main`, last committed 2026-08-25) lists fastapi, uvicorn,
  PyJWT, argon2-cffi, PyPDF2, python-docx — none of which depend on numpy (checked
  `pip show … | grep Requires`). Only the worktree
  `.claude/worktrees/opportunity-matching-improvement-fb6134/requirements.txt` has `numpy>=1.26`.
- `render.yaml:332` builds with `pip install -r requirements.txt`.

Unless Render's build cache already carries numpy from an earlier deploy, `uvicorn app.main:app`
raises `ModuleNotFoundError` at import and the service does not boot. Check the Render deploy
log first; the fix is one line (`numpy>=1.26`, ~30 MB RAM). Everything below assumes the
service boots.

---

## A. Per-endpoint table

Conventions. **Auth dep**: `cur` = `get_current_user` (JWT decode, **0 DB**); `opt` =
`get_optional_user` (0 DB); `req_sub` = `require_subscription` = `cur` + `subscription_block_reason`
→ `get_user_account` = **1 Supabase GET** (`app/deps.py:141-145`, `app/core.py:671-692`);
`opt_sub` = `optional_subscribed_user` = 0 GETs signed-out, **1 GET** when a bearer token is
present (`app/deps.py:148-153`). The RN client attaches the bearer to *every* `request()` call,
so in practice `opt_sub` costs 1 GET for every signed-in user. `bg` = work done on a spawned
daemon thread after the response (still real Supabase load).

Note: `require_subscription`/`optional_subscribed_user` are **sync `def` dependencies**, so
FastAPI runs them in the anyio threadpool — an authed request takes **two** threadpool
acquisitions (dependency, then handler), each a queue point under saturation.

| Route | Method | Handler | Auth dep | Supabase round trips / request (sync) | Upstream paid calls | In-process cache | Approx response | Notes |
|---|---|---|---|---|---|---|---|---|
| `/api/register` | POST | `def` | none (per-IP limiter) | 4: `get_user_account`, `get_user_by_email`, `create_user` POST, `get_user_account` (+bg welcome email: claim POST, Resend, PATCH) | none | — | <1 KB | argon2 hash: 64 MiB RAM, ~0.1-0.3 s CPU (`app/auth/passwords.py:107`) |
| `/api/login` | POST | `def` | none (per-IP limiter) | 1 typical (`get_user_account`); +1 PATCH on legacy-hash upgrade; +2 (`get_user` select=* incl. `data` blob, PATCH) when `trial_ends_at` is NULL | none | — | <1 KB | argon2 verify same cost as hash |
| `/api/auth/refresh` | POST | `def` | refresh JWT | 1 (`get_user_account`) | none | — | <1 KB | fired in background on every app open |
| `/api/auth/logout-all` | POST | `def` | `cur` | 2 (`get_user` select=*, PATCH) | none | — | tiny | |
| `/api/email/sweep` | POST | `def` | `X-Cron-Secret` | whole `users` roster paginated (N/1000 GETs, includes `data` blob for the deadline sweep) + per-due claim POST/PATCH | Resend per email | — | small | cron only; not on the hot path |
| `/api/email/unsubscribe` | GET | `def` | HMAC | 1 PATCH | none | — | HTML ~1 KB | |
| `/api/events` | POST | `def` | `opt` | **0** (buffered; one batch POST per 20 s per process) | none | buffer ≤5000 rows | tiny | the one endpoint already shaped for volume |
| `/api/messages` | POST | `def` | `opt` + inline `subscription_block_reason` | 1 GET (signed in); bg 4-6 (`agent_runs` GET+PATCH under a global lock, `user_costs` GET+PATCH under a second lock; first call of the day adds GET+POST each); +1 bg `conversations` POST for profile-chat turns | **1 Gemini** `generateContent` (`gemini-3.5-flash-lite`, up to 8000 output tokens), optional googleSearch | none | 1-10 KB | **`call_gemini` sleeps up to 5 s process-wide before every call** (`gemini_common.py:221-234, 447`); 120 s timeout; 429 → `sleep(5)` + 1 retry in-slot |
| `/api/messages-claude` | POST | `def` | same | same as above | **1 Anthropic** call (`claude-haiku-4-5`, ≤8000 tokens), optional web_search | none | 1-20 KB (raw Anthropic JSON passthrough) | **`urlopen(req)` with NO timeout** (`app/routes/ai.py:122`) |
| `/api/mailing-list/status` | GET | `def` | `opt_sub` | 1-3 (gate; `opportunity_signups` GET; `mailing_list_subscriptions` GET if signed in) | none | — | ~200 B/id | |
| `/api/mailing-list/subscriptions` | GET | `def` | `req_sub` | 3 (gate, subscriptions GET, opportunities `in.()` GET) | none | — | small | |
| `/api/opportunities/{id}/subscribe` | POST | `def` | `req_sub` | 5 (gate, opportunities GET, signups GET, `get_user` select=*, subscriptions upsert) | 1 third-party mailing-list POST | per-user hourly counter (dict, keys never pruned) | small | |
| `/api/match` | POST | `def` | `req_sub` | 1 (gate) + catalog cache read (0, or 2 × ~10 MB paginated GETs on refresh, under lock); bg 4-6 cost rollup | **1 Gemini embed** (`batchEmbedContents`, skipped on student-embed cache hit) + **1 Gemini `generateContent`** eligibility gate (≤100 candidate views, 8000 max tokens) | catalog (300 s); student embeddings (256 entries, no TTL) | up to 100 rows × ~1 KB ≈ **100 KB** | both Gemini calls pay the 5 s rate-limit sleep; numpy conversion of 1330×768 floats per request |
| `/api/match` `{prewarm}` | POST | `def` | `req_sub` | 1 | 1 Gemini embed (or 0 on hit) | student embeddings | tiny | |
| `/api/opportunities` | GET | `def` | `opt_sub` | 1 (signed in) + catalog cache (0 / 2 on refresh) | none | catalog, TTL 300 s | **~1.27 MB** JSON (1330 rows, ~952 B/row measured from `opportunities.json`); no gzip, no ETag, `Cache-Control: no-store` | per request: dict-strip over every row + `json.dumps` (6.6 ms on a full core, measured) |
| `/api/opportunities/{id}/deadline` (cached) | GET | `def` | `req_sub` | 3 (gate, row GET, `deadline_check_log` POST, 5 s timeout) | none | none in-process (cache is the DB row, 7 days) | ~1-3 KB | |
| `/api/opportunities/{id}/deadline` (fresh / `?refresh=1`) | GET | `def` | `req_sub` | 3 + PATCH (1) + bg `user_costs` 2-3; aggregator policy GET (300 s cache) | **3-10 Anthropic** calls: up to 4 rungs × up to 2 attempts (`_search_round`, web_search+web_fetch, 120 s timeout each) + phase-2 extract + `source_capture` capture call (want_requirements=True) | `_shared_capture_cache` (120 s TTL, **never evicted**) | ~1-3 KB | plus blocking `robots.txt`/sitemap fetches (≤25 child sitemaps × 5 MB) twice per check; a single request can hold a threadpool slot for minutes |
| `/api/tracker/sync` | GET | `def` | `req_sub` | 2 (gate, opportunities `in.()` GET, ≤300 ids) | none | — | ~1-3 KB per id | client-throttled to 1 per 5 min except on login |
| `/api/opportunities/{id}/action-items` (stored) | GET | `def` | `req_sub` | 2 (gate, row GET) | none | — | ~1-2 KB | |
| `/api/opportunities/{id}/action-items` (fresh) | GET | `def` | `req_sub` | 2 + PATCH; bg 2-3 | same ladder as the fresh deadline check (`process_one(full_capture=True)`) + 1 Claude extract | shared capture cache | ~1-2 KB | |
| `/api/extract-from-resume` | POST | `def` | `cur` + inline gate | 1; bg 4-6 | 1 Anthropic (`claude_common.call_claude`, 30 s timeout, 500 tokens) | — | ~1 KB | PyPDF2 parse CPU in-slot; unbounded multipart body |
| `/api/extract-from-linkedin` | POST | `def` | `cur` + inline gate | 1; bg 4-6 | 1 Anthropic (30 s) | — | ~1 KB | |
| `/api/user-submitted-opportunities` | POST | `def` | `opt_sub` | 1 (signed in) + **2 paginated GETs of the whole table** (id,name,url, ~1440 rows) + 1-3 insert attempts | none | — | small | O(n) `url_dedupe.find_duplicates` per submission |
| `/api/subscription/status` | POST | `def` | `cur` | 1 (+2 on first-ever trial stamp) | none | — | small | called on Manage Plan mount + focus |
| `/api/subscription/checkout` | POST | `def` | `cur` | 3 (`get_user_account`; `update_subscription` = `get_user` select=* + PATCH) | 2 Stripe (10 s) | — | small | Stripe unconfigured today |
| `/api/subscription/cancel` | POST | `def` | `cur` | 3; bg goodbye email | 1 Stripe | — | small | |
| `/api/subscription/redeem-promo` | POST | `def` | `cur` | 4 (`get_user_account`, `get_user`, PATCH, `get_user_account`) | none | — | small | |
| `/api/subscription/validate-promo` | POST | `def` | `opt` | 0-1 | none | — | small | |
| `/api/data/save` | POST | `def` | `req_sub` | **3** (gate; `get_user_data` = whole `data` jsonb; PATCH whole `data` jsonb back) | none | — | tiny | read-modify-write of the entire blob (37 KB+ tracker); lost-update race between concurrent saves |
| `/api/data/load` | POST | `def` | `req_sub` | 2 (gate, `get_user_data`) | none | — | requested keys, typically 40-80 KB (tracker JSON string + profile) | client batches keys per tick into one call |
| `/api/account/location` | POST | `def` | `req_sub` | 3 (gate, `get_user` select=*, PATCH) | none | — | tiny | |
| `/api/auth/google/start` | GET | `def` | none | 0 | none | per-process state dicts | 302 | |
| `/api/auth/google/callback` | GET | `def` | cookie state | 1-4 (`get_user_by_google_id`; on link: `get_user_by_email`, `get_user`, PATCH) | 2 Google (10 s each) | per-process handoff token dict | 302 | breaks with >1 worker |
| `/api/auth/google/session` | GET | `def` | handoff token | 1 (`get_user` select=*) + 0-2 | none | per-process | small | |
| `/api/auth/google/finish` | POST | `def` | handoff token | ≥5 | none | per-process | small | |
| `/api/auth/google/calendar/start` | GET | `def` | JWT in query | 1 (`get_user`) | none | per-process | 302 | |
| `/api/auth/google/calendar/callback` | GET | `def` | cookie state | 1 PATCH | 1 Google | per-process | 302 | |
| `/api/calendar/sync` | POST | `def` | `req_sub` | 3-5 (gate; `get_user` ×2 — once in `get_google_calendar_access_token`, again for `ensure_wingman_calendar`; PATCH on token refresh / calendar create) | 2 + 2N Google calls, **sequential**, 10 s each | — | ~200 B/event | tens of seconds for a 20-item Quest Log |
| `/{full_path}` (`serve_static`) | GET | `def` (threadpool) | none | 0 | none | none — 3-4 `os.path` stats per request; `walkthrough.html` (1.5 MB) read + `bytes.replace` per request | index.html shell ~KB; `entry-*.js` 1.9 MB (immutable) | HTML shells `no-store` |

**What the client fires per screen** (from `httpClient.ts`, `trackerStore.ts`, screens):

- **App open**: `initAuth` boots from the cached identity, `POST /api/auth/refresh` in the
  background (1 Supabase GET); `AuthContext` then `syncTrackerFromCatalog({force:true})` →
  `POST /api/data/load` (tracker key) + `GET /api/tracker/sync` (2 GETs); Home Base focus →
  `POST /api/data/load` with 3 keys batched into one request (2 GETs). ≈ 4 API calls,
  ≈ 8 Supabase round trips per open.
- **Fresh Finds**: `GET /api/opportunities` (1.27 MB, every mount — no client-side catalog
  cache), `/api/data/load` (profile), `POST /api/match` prewarm + real, several
  `/api/messages` (subject inference, ranking per kind, tag scoring), on add: `/deadline` +
  `/action-items` + `data/save`; impressions batched into `/api/events`.
- **Quest Log**: `/api/data/load` (2 keys batched), `/api/tracker/sync` (throttled 5 min),
  `GET /api/opportunities` when the search panel opens; **"Check for updates"** = per tracked
  item `GET …/deadline?refresh=1` (**paid, minutes**) + `GET …/action-items`.
- **My Vibe**: `/api/data/load`; one `/api/messages-claude` per chat turn; on drawer close:
  synthesis (`messages-claude`, 4-8 K output) then `refreshProfileDerived` = ~4 Gemini + 1
  Claude calls and **4 serialized `data/save`** (12 Supabase round trips).
- **Manage Plan**: `POST /api/subscription/status` on mount and on focus.

---

## B. Process model

- **Start command** (`render.yaml:333`): `uvicorn app.main:app --host 0.0.0.0 --port $PORT` —
  **one process, one event loop, no `--workers`, no `--limit-concurrency`, no
  `--timeout-keep-alive` override, no `--forwarded-allow-ips`.** `uvicorn[standard]` gives
  uvloop + httptools on Linux.
- **Plan** (`render.yaml:327`): `plan: free` — Render Free web services are **0.1 CPU / 512 MB**
  and **spin down after ~15 min idle** (cold start = a 30 s+ first request). Every CPU figure
  below must be multiplied by ~10 for this instance class.
- **Every route handler is `def`** (grep confirms: the only `async def`s are the two body
  dependencies in `app/deps.py:53-61`, the middleware, the exception handler, and the two
  token dependencies). So every handler runs via `anyio.to_thread.run_sync` under the
  **default `CapacityLimiter(40)`**. A 41st concurrent handler waits (unbounded queue, no
  timeout) on the event loop. Sync dependencies (`require_subscription`,
  `optional_subscribed_user`) also consume a token, so an authed request is two acquisitions.
- **No blocking I/O on the event loop today.** `no_cache` (`app/main.py:64-87`) is a
  `BaseHTTPMiddleware` — it never blocks, but it does wrap every response (including the 1.9 MB
  bundle and 1.27 MB catalog) in Starlette's streaming task-group machinery: a small fixed
  overhead per request and a well-known source of subtle issues; a pure-ASGI header
  middleware is cheaper. `json_body` parses request bodies on the loop
  (`app/deps.py:28-36`) — a 100 KB+ `data/save` body is a few ms of loop time, tolerable.
- **What consumes threadpool slots, and for how long**:
  - AI proxies: the entire upstream round trip (1-20 s), **plus up to 5 s of
    `time.sleep` in `gemini_common._enforce_rate_limit`** before every Gemini call, plus
    `time.sleep(5)` on a 429 retry — all in-slot.
  - Fresh `/deadline` and `/action-items`: 3-10 sequential Anthropic calls at 120 s timeout
    each + sitemap/robots fetches → **one request can hold a slot for 1-5+ minutes**.
  - `/api/calendar/sync`: 2+2N sequential Google calls.
  - Everything else: 100-500 ms of Supabase round trips.
- **Background threads (per process)**:
  - `_activity_flusher` (`app/core.py:986-1002`) and `_events_flusher` (`:1126-1142`): one
    each, started lazily, flush every 30 s / 20 s. Activity flush is a sequential
    read-modify-write per `(user, day)` (~150 ms each): at 1,000 active users/interval the
    flush takes ~5 min for a 30 s window and never catches up (counts merge, so memory stays
    bounded by active users; only `hits` accuracy suffers).
  - **One new daemon thread per AI call** for `record_interactive_cost`
    (`app/core.py:280-282`), each doing 2-3 Supabase round trips under `_interactive_lock`
    and then 2-3 more under `_user_costs_lock` (`:191-283, 427-543`). Service rate of the
    lock ≈ 2-3 calls/s; above that, threads pile up without bound.
  - One daemon thread per profile-chat turn (`log_conversation_async`), per signup/cancel
    (`send_lifecycle_email_async`). Fine at current volume; unbounded by design.
- **Module-level state that breaks or degrades under `--workers > 1` / multiple instances**:
  - `app/services/google_oauth.py:352-406` — Google sign-in handoff tokens, calendar states,
    app-redirect map are **process dicts**; with two workers the callback lands on a
    different worker than `/start` ~50% of the time → "invalid or expired request".
  - `gemini_common.py:240-340` — `.gemini_web_search.lock` file + `_lock_acquired` global: the
    second worker's first `useWebSearch` call finds a live-PID lock and **raises
    `RuntimeError` → 502, permanently, for that worker**.
  - `gemini_common._last_call_time`, `claude_common._last_call_time`,
    `check_deadlines._last_call_time` — unsynchronized globals (racy even in one process).
  - `app/core.py:192 _interactive_rollup`, `:428 _user_costs_rows` — per-process id caches
    over read-modify-write PATCHes: two workers lose each other's increments.
  - `app/core.py:956 _activity_buffer`, `:1064 _events_buffer` — per-process; activity's RMW
    flush races across workers (lost `hits`), events are append-only (safe).
  - `app/services/opportunities.py:15 _opportunities_cache` — each worker holds its own
    ~40 MB copy and refreshes independently (N × 20 MB fetches every 5 min).
  - `app/services/embeddings.py:138 _STUDENT_EMBED_CACHE` — a prewarm on worker A is a miss
    (and a second paid embed) on worker B.
  - `app/auth/ratelimit.py:197-198`, `app/services/mailing_list.py:52` — per-process limits
    multiply by worker count (documented in the file).
  - Latches (`_account_select`, `_match_vector_available`, `_user_costs_available`,
    `_activity_available`, `_events_available`) — per-process, harmless.

---

## C. HTTP client usage

- **Every outbound call is `urllib.request.urlopen` on a fresh `Request`** — Supabase
  (`app/core.py:597-605, 919-926, 1213-1216`, `app/services/opportunities.py:45-54`,
  `deadlines.py`, `action_items.py`, `mailing_list.py`, `email.py`), Gemini
  (`gemini_common.py:436-464, 572-588`), Anthropic (`app/routes/ai.py:111-122`,
  `check_deadlines.py:270-282`, `claude_common.py:470-481`, `source_capture.py:162-167`),
  Google, Stripe, Resend. **No connection pooling, no keep-alive** (urllib sends
  `Connection: close`), so each call pays DNS (`getaddrinfo` per call) + TCP handshake +
  TLS 1.3 handshake + the HTTP exchange. No HTTP/2.
- **Timeouts**: Supabase users 10 s (`core.py:603`), admin tables 15 s (`:921, :1214`),
  catalog 10 s per page (`opportunities.py:53`), deadline log 5 s, `conversations` 10 s;
  Gemini 120 s default (`GEMINI_TIMEOUT_SECS`, never set in `.env`/`render.yaml`);
  `check_deadlines.call_claude` 120 s; `claude_common` 120 s default, resume passes 30 s;
  Resend 20 s; Google/Stripe 10 s; **Anthropic proxy: none** (`app/routes/ai.py:122`) —
  a hung upstream holds a threadpool slot forever.
- **Retries**: Gemini and embed: on 429 only, `time.sleep(5)` then exactly one retry, in-slot
  (`gemini_common.py:457-464, 582-588`). `_search_round` re-rolls once on a silent search
  (a second full paid call). Nothing else retries; no backoff, no jitter, no circuit breaker,
  no `Retry-After` handling.
- **Latency floor per Supabase call**: 3 network round trips (TCP, TLS, request) + PostgREST
  + Postgres. CLAUDE.md measured **164 ms per `/api/data/load` call** in production; the
  design floor is ~70-90 ms same-region and ~200 ms+ cross-region, of which **two RTTs
  (40-140 ms) are pure connection setup** that a keep-alive pool removes. The TLS handshake
  also costs ~1-3 ms of CPU on a full core per call — **10-30 ms on a 0.1-CPU instance**;
  at 50 RPS × ~2.5 Supabase calls the handshakes alone exceed the instance's CPU budget.

---

## D. Caching

| What | Where | Size / TTL | Invalidation | Notes |
|---|---|---|---|---|
| Active catalog incl. `match_vector` | `app/services/opportunities.py:15, 73-109` | 1330 rows; vectors as **Python float lists ≈ 32.8 MB** + ~5 MB of row dicts/strings; refresh payload ≈ **20 MB JSON** (`~15 KB` of floats per row) in 2 paginated GETs; TTL 300 s | TTL only in the shipped app (ops console busts it locally) | Refresh is **synchronous, under `_opportunities_cache_lock`**: every `/api/opportunities` and `/api/match` caller blocks for the whole fetch+parse (json.loads of ~1 M floats ≈ 0.5-1 s on a full core → 5-10 s on 0.1 CPU). `supabase_common.py:40-45` itself warns a 1000-row page of jsonb vectors can exceed Supabase's ~8 s statement timeout — then the 10 s urllib timeout trips, stale is served, and **the next request retries the whole 20 MB fetch while holding the lock**, again and again. Peak memory during refresh ≈ old + new + raw string ≈ 80-100 MB. |
| Catalog endpoint output | none | — | — | **Re-serialized per request**: `[{k:v …} for row in data]` strip (2.5 ms) + `json.dumps` (6.6 ms) measured on this machine for 1.27 MB; no ETag, no gzip (`GZipMiddleware` not installed), `Cache-Control: no-store` forced by `app/main.py:84`. |
| Deadline / action-item answers | Supabase row (`dates_last_checked_at`, `action_items_checked_at`), 7 days | — | stamp columns | Not in-process: a "cached" deadline hit still costs **3 Supabase round trips** (gate + row + log POST). |
| User identity / subscription | **not cached** | — | — | The access token is stateless, but `require_subscription` re-reads the `users` row on every gated request, so the JWT saves nothing on the DB side. |
| Student theme embeddings | `app/services/embeddings.py:138-159` | ≤256 entries, no TTL | LRU-ish (insertion order) | Not lock-protected (benign races). Per process. |
| Shared program capture | `check_deadlines.py:846-847` | TTL 120 s, **never evicted** — holds captured page text per opportunity id | none | Grows with every distinct fresh check for the life of the process. |
| Aggregator policy | `aggregators_common.py:37, 162` | 300 s | explicit invalidate (ops) | One Supabase GET per 5 min. |
| Google OAuth handoff state | `app/services/google_oauth.py` | pruned on use | — | Per process (see B). |
| Static files | Starlette `FileResponse` | — | — | `/assets/`, `/_expo/static/` get `immutable`; HTML shells `no-store`; `walkthrough.html` re-read per request. |

**Catalog JSON size**: `opportunities.json` at the repo root is **1,192,950 bytes, 1,330 rows**;
re-shaped to exactly the client fields the endpoint ships (`id,name,org,summary,url,
subject_tags,type,price,state,location,intl,season,review_status,review_summary,grade_min,
grade_max,status,eligibility`) it is **1,266,942 bytes ≈ 952 B/row**. gzip would bring this to
roughly 200-260 KB. At 10 RPS the endpoint alone is ~12.7 MB/s ≈ 100 Mbit/s of egress and
would consume Render Free's 100 GB/month allowance in **~2.2 hours**.

---

## E. Matching / search path (`POST /api/match`)

Per request, in order (`app/routes/matching.py:273-346`):

1. `require_subscription` — **1 Supabase GET** (threadpool acquisition #1).
2. `fetch_opportunities()` — cache read under lock (0 GETs, or the 20 MB refresh).
3. `recall_pool` (`recall_query.py:342-357`):
   - `student_embed_texts` → **1 Gemini `batchEmbedContents`** call for all themes+projects
     (`gemini_common.call_gemini_embed`, `:544-599`) unless the exact text list is in the
     256-entry cache (prewarm makes this a hit in the common case). Pays
     `_enforce_rate_limit()` → **up to 5 s sleep**. ~$0.0002.
   - `recall()` (`matching.py:318-384`): O(n) Python filter over 1330 rows (6 predicates, two
     regexes on `eligibility` only when grade is below `grade_min`); then
     `_to_matrix([r["match_vector"] …])` converts **1330 × 768 Python floats to a float64
     numpy array on every request** (~8.2 MB allocation, ~40-80 ms on a full core — the
     conversion dominates, the matmul itself is sub-millisecond); sort 1330; floor at 0.1;
     top 100.
   - `_scores_by_id` re-embeds the ≤100 pool rows into a matrix and re-scores (cheap).
4. `gate_pool_eligibility` (`pool_eligibility.py:81-115`): regex scan of ≤100 rows; builds a
   prompt with up to 100 candidate views (16 fields each, ~30-60 KB ≈ 10-15 K input tokens);
   **1 Gemini `generateContent`** call, `max_tokens=8000`, thinking "low" — pays the 5 s
   sleep again; typical 3-10 s; `extract_json` over the reply; quote verification per verdict.
   Cost at the `gemini_common` constants ≈ $0.01-0.02/request (the constants are
   `gemini-3.6-flash` prices; real `flash-lite` billing is lower).
5. `attach_display` — ≤100 dict copies; response ≈ 100 KB.
6. `record_interactive_cost_async` — bg thread, 4-6 Supabase round trips.

**No Supabase RPC, no per-request catalog query** — good. **What dominates latency**: the
two upstream Gemini calls plus the two 5 s rate-limit sleeps (worst case ~10 s of pure
sleeping), then the eligibility model call (3-10 s). CPU per request ≈ 50-100 ms full-core,
i.e. **0.5-1 s on the free instance**. **No LLM call per row** — one per request — good.
Concurrency: each `/api/match` holds a slot for ~5-20 s, so **~4 concurrent matches per
second saturates the 40-slot pool by themselves**.

---

## F. AI proxy path (`/api/messages`, `/api/messages-claude`)

- **Concurrency limit**: none — bounded only by the 40-token threadpool shared with every
  other endpoint. No per-user rate limit (only login/register have limiters), no body size
  limit (`raw_body` reads any size), no queue, no shedding.
- **Timeouts**: Gemini 120 s (`gemini_common._default_timeout_secs`); **Anthropic none**.
- **Retry**: Gemini 429 → `time.sleep(5)` + one retry (slot held throughout); Anthropic 429/529
  are passed straight through to the client (`Response(content=e.read(), status_code=e.code)`),
  the client `request()` throws `HttpError`; `callGeminiJSON` retries only on JSON-parse
  failure, not on 429. No `Retry-After`, no backoff.
- **Streaming**: no — full upstream body buffered, then returned.
- **Rate-limit sleep**: `gemini_common.py:163` `DEFAULT_MIN_DELAY_SECS = 5`; `:183` reads
  `GEMINI_MIN_DELAY_SECS` (not set anywhere in `app/`, `render.yaml`, or `.env` keys);
  `:447` calls `_enforce_rate_limit()` inside `call_gemini`, i.e. **on every `/api/messages`
  and every `/api/match` call in production**. The global is unsynchronized, so it behaves as
  "all calls arriving within 5 s of the last stamp wake together at stamp+5 s": each call
  gains 0-5 s latency and holds a slot while sleeping. This was designed for the batch agents
  and is the single largest self-inflicted latency source in the app.
- **Web-search file lock**: `gemini_common.py:267-330` — `useWebSearch=true` calls take a
  repo-root lock file once per process; fine with 1 worker, fatal (502) with 2.
- **At 50 RPS mixed** (say 20 % AI ≈ 10 RPS): Little's law with ~2-4 s Gemini latency + up
  to 5 s sleep ≈ 6-8 s per call → **60-80 concurrent AI handlers wanted vs 40 slots**. The
  pool saturates within seconds; from then on `/api/data/load`, `/api/opportunities`, and
  even the SPA shell (`serve_static` is `def`) queue behind AI calls. A single hung Anthropic
  socket (no timeout) reduces the pool permanently by one.
- **Provider limits**: Gemini flash-lite RPM/TPM are tier-dependent; 600 RPM with 5-40 K-token
  ranking prompts is a TPM question, not RPM. Anthropic Haiku on an org's lowest tier is on
  the order of **50 RPM ≈ 0.8 RPS** — 10 RPS of profile chat needs a materially higher tier.
  Confirm both tiers in the consoles before load-testing.
- **Cost per call (estimated from the repo's own constants)**: Gemini (`/api/messages`) at
  the `gemini_common` rates: ~5 K in / 1 K out ≈ $0.007 (real flash-lite ≈ $0.001);
  Claude Haiku profile-chat turn ≈ $0.003, synthesis (4-8 K out) ≈ $0.03-0.05; fresh deadline
  check **~$0.07/row measured** (CLAUDE.md), action items ~$0.004/row measured.
  At 10 AI RPS ≈ 36 K calls/hour ≈ $50-250/hour depending on mix — the cost limiter is
  also absent.

---

## G. Rate limiter and accounting

- **`RateLimiter`** (`app/auth/ratelimit.py:166-198`): per-process, one `threading.Lock`,
  `deque` per key capped at `max_hits` (refused attempts are not appended), sweep when
  >4096 keys removes only idle keys — memory bounded to ~hundreds of KB. Lock hold time is
  microseconds; not a contention problem.
  **Correctness risk on Render**: uvicorn's `ProxyHeadersMiddleware` only trusts
  `X-Forwarded-For` from `--forwarded-allow-ips` (default `127.0.0.1`); Render's load balancer
  connects from a private address, so `request.client.host` is likely **the proxy's IP for
  every user** — making `login_limiter` a **global 10 logins / 5 min** and `register_limiter`
  10 signups/hour across the whole user base. The docstring's "on Render the app sees the
  real client" is an assumption; verify with one log line in prod. At 50 RPS this is a hard
  functional blocker for `/api/login`.
- **Activity** (`touch_user_activity`, `core.py:962-983`): in-memory, locked, O(1); flush
  falls behind at ~200+ active users per 30 s (sequential RMW). Multi-worker: lost `hits`.
- **Cost accounting** (`record_interactive_cost`, `core.py:195-283`; `record_user_cost`,
  `:431-533`): thread per call; two global locks; 4-6 sequential Supabase calls per AI call
  (~0.6-1 s); PATCH read-modify-write. Throughput ≈ 1-2 AI calls/s before the lock queue
  grows unboundedly (threads, memory, GIL contention, and a growing write load on
  `agent_runs`/`user_costs` — one PATCH per AI call). Multi-worker: lost increments.
- **Events** (`record_user_events`): bounded (5000 rows), append-only batch insert — the right
  pattern; the flush POST can be ~1 MB but is one request per 20 s.
- **`_subscribe_history`** (`mailing_list.py:52`): per-user list, values bounded to 10, keys
  never pruned — slow growth with distinct users; negligible.

---

## H. O(n) / pathological work on hot paths

1. `app/routes/opportunities.py:51-54` + `app/deps.py:13-20` — per request: strip
   comprehension over every row + `json.dumps` of 1.27 MB, no gzip/ETag. ~9 ms full-core,
   **~90 ms on 0.1 CPU** → ~10 RPS ceiling on this endpoint alone.
2. `app/services/opportunities.py:82-109` — 20 MB refresh parsed under a lock every 5 min;
   all catalog/match readers stall; retry storm on a failed page (see D).
3. `app/services/matching.py:363` — 1330×768 Python-float → numpy conversion per `/api/match`
   (~8 MB alloc, 40-80 ms). Should be a single matrix built once per catalog refresh.
4. `app/core.py:845-854` — `/api/data/save` reads and rewrites the entire `data` jsonb per key;
   `refreshProfileDerived` does this 4 × serially. Lost updates across devices/tabs.
5. `app/services/resume.py:261-284` — full-table read (2 GETs) + `url_dedupe.find_duplicates`
   fuzzy compare per user submission.
6. `app/routes/google_oauth.py:563-728` — sequential Google API calls per event (2 + 2N).
7. `app/main.py:208-230` — 3-4 `os.path` stats per static request; `walkthrough.html` 1.5 MB
   read + `bytes.replace` on every hit.
8. `check_deadlines.py:734-743`, `source_capture.py:173-190` — blocking sitemap discovery
   (robots + ≤25 child sitemaps × ≤5 MB) run **twice** per fresh check, inside the slot.
9. `app/auth/passwords.py:107` — argon2 defaults (`memory_cost=65536` KiB, `time_cost=3`,
   `parallelism=4`): **64 MiB and ~0.1-0.3 s CPU per login/register** (1-3 s on 0.1 CPU);
   8 concurrent logins ≈ 512 MB → OOM on the free instance.
10. Not problems: regexes are compiled at module import; `.env` is loaded once at import
    (`app/config.py:36`); `classify_feature` is ~20 substring tests; JSON bodies are parsed once.

---

## I. Ranked findings and RPS estimates

### Top 12

**1. `numpy` missing from `requirements.txt` — service may not boot.**
`app/services/matching.py:34`, `app/services/recall_query.py:259`, `app/main.py:103`,
`requirements.txt`. Impact: `ModuleNotFoundError` at import on a clean Render build → 0 RPS.
Fix: add `numpy>=1.26`. Risk: +~30 MB RSS. (Verify against the Render build log; if the
build cache hides it today, the next cache miss surfaces it.)

**2. Process-wide 5 s sleep before every Gemini call on the interactive path.**
`gemini_common.py:163, 183, 221-234, 447, 578`. Impact: 0-5 s added latency to every
`/api/messages` and both `/api/match` model calls; the sleeping thread holds a threadpool
slot, capping Gemini-backed throughput at roughly 40 slots ÷ ~8 s ≈ **5 RPS** and dragging
every other endpoint down with it. Fix: at app startup call `gemini_common.set_min_delay(0)`
(or set `GEMINI_MIN_DELAY_SECS=0` on Render) and keep the 5 s floor only in the batch
scripts' `--min-delay`; replace the in-slot 429 sleep with a fast 429/503 to the client plus
`Retry-After`. Tradeoff: bursts can 429 at Gemini — that is the provider's limiter doing its
job; handle it with backoff, not a global sleep. This changes no prompt and no spend
decision, but touches a shared M9 module — flag it as an operational change.

**3. Every authed request re-reads the `users` row for the subscription gate.**
`app/deps.py:98-153`, `app/core.py:671-692`. Impact: +1 Supabase round trip (~100-170 ms wall,
~10-30 ms CPU on this instance) on ~90 % of traffic; the JWT's whole point (no DB read) is
undone. Fix: per-process cache `userid → (has_access, status, checked_at)` with a 60 s TTL,
invalidated in-process by `redeem-promo`/`cancel`/`checkout`; or mint `has_access` + an
`access_until` claim into the access token (45 min) and only hit the DB when the claim is
about to lapse. Tradeoff: up to 60 s of lag for a lapse (already tolerated in the other
direction — the client receives 402 whenever the server says so) and cross-worker lag once
there are workers.

**4. No HTTP connection pooling; a TLS handshake per Supabase/provider call.**
`app/core.py:587-605, 902-926, 1195-1216`, `app/services/opportunities.py:45-54`, `deadlines.py`,
`action_items.py`, `mailing_list.py`, `email.py`, `gemini_common.py`, `app/routes/ai.py`.
Impact: ~2 RTTs + 1-3 ms CPU per call; at 50 RPS × 2.5 calls the handshakes alone are
100-300 % of a 0.1 CPU and ~50 % of the wall latency. Fix: one module-level
`urllib3.PoolManager` (or `httpx.Client`) with keep-alive, shared by a single
`supabase_request()` helper; uniform timeouts (connect 3 s / read 10 s) everywhere. Keep the
repo-root agents on urllib. Tradeoff: one new dependency in `requirements.txt`; the
`HTTPError.read()`-once idiom (`_error_body`) must be re-implemented against the new client.

**5. AI proxies hold a 40-slot shared threadpool for the full upstream call; Anthropic has no
timeout; no concurrency cap.**
`app/routes/ai.py:111-129, 148-175`, `gemini_common.py:454-464`, anyio default limiter (40).
Impact: ~10 concurrent AI calls stall the entire service; a hung Anthropic socket is a
permanent slot loss. Fix (staged): (a) startup:
`anyio.to_thread.current_default_thread_limiter().total_tokens = 200` and `timeout=60` on the
Anthropic `urlopen` — a one-line stopgap; (b) make the two proxies `async def` using
`httpx.AsyncClient` with an `asyncio.Semaphore(30)` and fail fast with 503 + `Retry-After`
when full, so AI never competes with data endpoints for threads; (c) `--limit-concurrency`
on uvicorn as a backstop. Tradeoff: (b) wraps rather than edits `call_gemini` (M9) — build the
request body with a pure helper and send it from `app/`; keeps the agents untouched.

**6. `/api/opportunities`: 1.27 MB serialized per request, no gzip, no ETag, `no-store`.**
`app/routes/opportunities.py:38-54`, `app/main.py:64-87`, `app/deps.py:13-20`. Impact: ~90 ms
CPU per request on this instance (≈10 RPS ceiling) and 100 GB/month of egress in ~2 h at
10 RPS. Fix: at cache refresh, pre-build the client-shaped list once, `json.dumps` once,
gzip once, hash once; serve bytes with `ETag` + `Cache-Control: private, max-age=300` and
answer `304` on `If-None-Match`; exempt this route from the `no-store` middleware; add
`GZipMiddleware(minimum_size=1024)` for everything else. Tradeoff: clients may see an
activation up to 5 min late — identical to today's server TTL.

**7. Catalog refresh is synchronous, lock-holding, 20 MB, and can retry-storm.**
`app/services/opportunities.py:36-59, 82-109`; `supabase_common.py:40-45` (statement-timeout
warning). Impact: a 2-10 s full stall of `/api/opportunities` and `/api/match` every 5 min;
if the vector page trips Supabase's ~8 s statement timeout, every subsequent request repeats
the 20 MB fetch under the lock. Fix: refresh in a background thread (stale-while-revalidate;
never hold the lock across the network); fetch `match_vector` as its own paginated select
with `page_size=200` into a single `np.ndarray` + id index kept beside the row list; keep the
row dicts vector-free. Tradeoff: two selects instead of one; ~35 MB RSS saved.

**8. Cost accounting: a thread per AI call, global locks, 4-6 sequential Supabase writes.**
`app/core.py:191-283, 427-543`. Impact: lock throughput ≈ 1-2 AI calls/s; beyond that threads
and memory grow without bound, and `agent_runs`/`user_costs` take one PATCH per call. Fix:
accumulate in memory keyed `(surface, day)` and `(user, day, surface, feature, model)` and
flush every 30 s from one thread — the pattern `user_activity` already uses. Tradeoff: a
crash loses ≤30 s of cost attribution (the same trade activity accepts); the console's
"latest day" figure lags by up to 30 s.

**9. argon2 defaults + a login limiter that is probably global behind Render's proxy.**
`app/auth/passwords.py:107`; `app/auth/ratelimit.py:166-198`; `render.yaml:333`. Impact:
64 MiB and 1-3 s CPU per login on the free instance; ~8 concurrent logins can OOM it; and if
`request.client.host` is the proxy address, **10 logins per 5 min for everyone**. Fix:
`PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)` (OWASP baseline;
`check_needs_rehash` migrates rows transparently on next login); add
`--forwarded-allow-ips='*'` to the start command (Render terminates TLS and sets
`X-Forwarded-For`) and log `client_ip()` once to confirm. Tradeoff: lighter hash parameters
(still standard); trusting forwarded headers is correct only behind Render's proxy.

**10. `/api/data/save` rewrites the whole `data` jsonb per key; lost updates.**
`app/core.py:845-854`, `app/routes/user_data.py:230-245`; client `queueSlotWrite` exists only
because of this. Impact: 3 round trips and 2 × blob transfer per save; 12 round trips per
profile synthesis; concurrent saves from two tabs/devices silently drop one. Fix: a
PostgREST RPC (`create function set_user_key(uid text, k text, v jsonb)` doing
`update users set data = jsonb_set(coalesce(data,'{}'), array[k], v)`) → 2 round trips,
atomic, no blob round-trip; accept `{values:{…}}` for multi-key saves. Tradeoff: one manual
DDL step (the repo's standing convention).

**11. Per-process state blocks `--workers > 1` and multi-instance scaling.**
`app/services/google_oauth.py:352-406`, `gemini_common.py:240-340`, `app/core.py:192, 428, 956,
1064`, `app/services/embeddings.py:138`, `app/auth/ratelimit.py`. Impact: the only scale-up
path today is a bigger single process; 50 RPS on one Python process with a threadpool is
marginal even after the fixes above. Fix: OAuth handoff → an HMAC-signed, 5-min token (the
`unsubscribe` link already uses this pattern) or a small Supabase table; drop the web-search
file lock on the interactive path (`_acquire_web_search_lock` only when
`WINGMAN_ENABLE_OPS`/batch); make the rollup caches idempotent (upsert-with-increment via an
RPC); then run `--workers 2-4` or 2 instances. Tradeoff: moderate refactor; the batch agents
keep their lock.

**12. Fresh deadline / action-item checks run minutes of sequential upstream work inside a
request slot, fanned out by "Check for updates".**
`app/routes/opportunities.py:104-113, 206-234`; `check_deadlines.py:688-716, 746-831, 860-913`;
`source_capture.py:146-218`; `sitemap_common.py:36-39, 92-99`; `_shared_capture_cache`
unbounded at `check_deadlines.py:846`. Impact: 3-10 Claude calls (120 s timeout each) plus
sitemap crawls per request; 10 students refreshing 10 items each want 100 slots for minutes.
Fix: a `threading.Semaphore(4)` around the fresh path that returns `stale-fallback`
immediately when full (the client already handles that source), or a job table + `202` and
client polling; bound the capture cache (e.g. 64 entries, evict on insert). Tradeoff: some
"Check for updates" presses answer "try again shortly" under load — better than stalling
the app.

Honourable mentions: `BaseHTTPMiddleware` for headers (`app/main.py:64-87`) → pure ASGI;
`serve_static` `walkthrough.html` re-read per hit → cache the patched bytes; the client
re-downloads the catalog on every Fresh Finds mount (`finder.tsx:348`) and again for the Quest
Log search panel (`tracker.tsx:361`) → a module-level client cache keyed on the ETag; Render
Free spin-down means the first request after 15 idle minutes takes 30 s+.

### Sustainable RPS today (Render Free: 1 uvicorn worker, 0.1 CPU, 512 MB, 40-slot pool)

| Class | Estimate | Reasoning |
|---|---|---|
| (i) Read endpoints | `/api/opportunities`: **~5-8 RPS**; `/api/tracker/sync`, cached `/deadline`, `/action-items`: **~10-20 RPS** | Catalog: ~9 ms full-core serialization + framework ≈ 90-120 ms CPU per request on 0.1 CPU, plus 1 users GET when signed in, plus 1.27 MB egress each. Other reads: 2-3 Supabase calls ≈ 300-500 ms wall, 20-60 ms CPU (TLS + JSON). Pool: 40 slots ÷ 0.4 s ≈ 100 RPS, not the binding limit. |
| (ii) Authed data endpoints (`data/load`, `data/save`, `subscription/status`, `refresh`) | **~10-15 RPS** at 300-600 ms p50 | 2-3 Supabase round trips with a fresh TLS handshake each ≈ 30-70 ms CPU per request on this instance; the 5 s-per-request budget is CPU, not the pool. Logins are additionally bound by argon2 (~1-3 s CPU each) and the possibly-global limiter. |
| (iii) AI proxies | **~2-4 RPS sustained**; Gemini path hard-capped near 5 RPS by (5 s sleep + upstream) × 40 slots; Claude path bounded by the org's Anthropic tier (lowest tiers ≈ 0.8 RPS) and by the cost-accounting lock (~1-2 calls/s before threads pile up) | Above ~4 AI RPS the pool fills and classes (i)/(ii) collapse with it. |
| **Mixed today** | **~10-15 RPS total** before p95 exceeds ~2 s; **50 RPS is not reachable** on this configuration regardless of code changes because 0.1 CPU cannot serialize/handshake at that rate. | |

Same code on a 1 vCPU / 2 GB instance, still 1 worker: (i) ~40-60 RPS (egress-bound on the
catalog), (ii) ~40-60 RPS, (iii) still ~5 RPS — the pool and the 5 s sleep, not CPU, bind.
Mixed ≈ 25-30 RPS with AI held under ~3 RPS.

### What it takes to reach 50 RPS mixed (e.g. 35 data/read + 10 AI + 5 static)

1. **Instance**: a paid plan with ≥1 vCPU and ≥2 GB (argon2 + the catalog cache + numpy),
   ideally 2 instances or `--workers 2-4` once finding 11 is done. Free tier is off the table.
2. **Fix 1** (numpy) — mandatory for boot.
3. **Fixes 2 + 5** — remove the interactive Gemini sleep, cap and isolate AI concurrency, put a
   timeout on Anthropic, raise the thread limiter. This alone lifts AI to provider-tier
   limits and stops AI from starving data endpoints.
4. **Fixes 3 + 4** — identity cache and a pooled HTTP client: cuts Supabase calls per authed
   request from ~2.5 to ~1.5 and per-call wall/CPU by ~50 %. With these, one 1-vCPU worker
   sustains ~100-150 RPS of data endpoints.
5. **Fixes 6 + 7** — pre-serialized, gzipped, ETag'd catalog served from a background-refreshed
   cache: the catalog endpoint becomes ~1 ms CPU and mostly `304`s; egress drops ~5×.
6. **Fix 8** — batched cost accounting so 10 AI RPS does not spawn 10 threads/s and 50
   Supabase writes/s.
7. **Fix 9** — forwarded-IP handling and lighter argon2 so 5-10 logins/s do not OOM or 429.
8. **Fix 12** — semaphore/queue on fresh deadline checks so a Quest Log refresh cannot
   monopolize the pool.
9. Provider tiers: confirm Anthropic ≥ tier 2-3 (≥1000 RPM) and Gemini TPM headroom for the
   ranking prompts; budget ~$50-250/hour at 10 AI RPS with today's prompt sizes.
10. Then load-test with the real mix (`k6`/`locust`) against a staging instance; the numbers
    above are static estimates, and the two unknowns that most affect them are Supabase's
    region relative to Render (RTT) and the org's provider tiers.

---

## Addendum — live investigation of the catalog fetch (2026-09-02)

Everything above section 10 was static reading. This addendum is from a **live** investigation
triggered by a real user report ("searched for a new profile theme → *Search failed: Could not
reach Supabase: HTTP Error 500*"), so the numbers here are measured against the running Supabase,
not estimated. It refines section E's line *"cache read under lock (0 GETs, or the 20 MB
refresh)"* — that refresh was not merely heavy, it was **intermittently failing**.

### F1. Confirmed bug: the catalog refresh times out on the embedding column

`fetch_opportunities()` (`app/services/opportunities.py`) pulls `OPPORTUNITIES_FIELDS` —
including the server-only 768-dim `match_vector` — for **all 1,686 active rows** (count grew from
the ~1,330 the static review assumed), paginated in **1,000-row pages**. Measured live:

- A page carrying `match_vector` takes ~4.5 s for 686 rows (~6.6 ms/row); the *same* rows without
  the vector return in ~0.5 s. A full 1,000-row vector page sits right at Supabase's per-statement
  timeout.
- Full pagination therefore **intermittently 500s** with PostgREST `{"code":"57014","message":
  "canceling statement due to statement timeout"}`. Reproduced directly; failure rate is
  load-dependent (some trials completed at ~3.7 s max/page, the next trial 500'd).
- That 500 had **no graceful path**: the module only degrades from the *400* it gets when
  `match_vector` is un-migrated. On a **cold cache** the timeout propagated out of the route as
  the `502 Could not reach Supabase: HTTP Error 500` the finder rendered as "Search failed".
  (With a warm cache it silently serves stale, which is why it looked intermittent.)

### F2. Shipped fix (low-risk, in place)

In `_paginated_catalog_fetch`:
- **Smaller pages** (`CATALOG_PAGE_SIZE = 250`) so a page's vector payload stays well under the
  statement timeout.
- **Retry the transient timeout** — a page that still 500s with `57014` is retried up to 4× with
  backoff; a 500 that is *not* a statement timeout is surfaced immediately.

Verified: 4/4 cold fetches now return all 1,686 rows with vectors where it previously flaked.
Regression tests added (`tests/unit/test_opportunities_cache.py`:
`test_statement_timeout_page_is_retried`, `test_non_timeout_500_is_not_retried`; file green).
**Latency is roughly unchanged** (~9-11 s on a cold refresh — the old *successful* path was
already ~11 s across its two big pages); the win is that the refresh is now **reliable** rather
than fast-but-flaky. Not yet committed (working tree carries unrelated dedupe changes from a
concurrent session).

### F3. Recommended follow-up — decouple the two caches (greenlight-when-ready)

The deeper inefficiency, and the operator's product framing around it:

- **`/api/opportunities` (catalog browsing) loads `match_vector` only to strip it**
  (`app/routes/opportunities.py:49-53`) — the browser never sees it. Only `/api/match` uses the
  vectors (`recall()` reads `row["match_vector"]`).
- Today both endpoints share **one 300 s-TTL cache** (`_opportunities_cache`), so the ~20 MB
  vector transfer repeats every 5 minutes and whichever request triggers the refresh eats the
  ~10 s stall under the lock.

Proposed shape:
1. **Split the cache.** A light, vector-free catalog cache for `/api/opportunities` (small
   payload, keep the short TTL so admin edits to names/summaries still appear quickly), and a
   separate vector-bearing cache used only by `/api/match`.
2. **Vectors refresh on a 24 h backstop, not 5 min** (operator decision, 2026-09-02). Rationale:
   a row's embedding changes only when the matching pipeline re-embeds it, never on an ordinary
   edit, so a 5-minute cadence is pure waste. The 24 h is a *backstop for a totally idle system*,
   not a "changes take a day to appear" rule — see next point.
3. **Keep instant-on-change.** The console already busts the catalog cache on
   activate/moderate (`ops/core.py` sets `fetched_at = 0.0` in 3 places); the split must bust
   **both** caches so a newly-activated opportunity is matchable immediately. Any decoupling
   work has to preserve this or it regresses into "new listing invisible for 24 h".
4. **Optional: pre-warm the vector cache on startup** (and just after the 24 h timer rolls). The
   product intent is for **matching to be the primary feature**; without a warm-up, the *first*
   match after a deploy or timer expiry is the unlucky request that pays the ~10 s cold load. Note
   this composes well: because the cache amortizes the load across every match request, feature
   popularity does **not** multiply the cost — the heavy pull happens ~once/24 h (or per deploy),
   not per search.

Known caveat to record: an **offline** re-embed run against production would not propagate to a
running instance for up to 24 h (vs 5 min today) unless the instance is nudged (restart, or the
same bust trigger). Acceptable given re-embeds are infrequent and manual; just flag it in the
runbook.

**Why paused:** the split touches shared plumbing (`ops/core.py`'s cache-bust sites and the
`_opportunities_cache` import) that a concurrent workstream is editing, plus the tested contract
in `test_opportunities_cache.py`. It is a "clean it up properly" task, not an emergency — the F2
fix already removes the user-facing failure. Also relevant to section D's egress math: a
vector-free catalog cache is what makes Fix 6/7 (pre-serialized, gzipped, ETag'd catalog) simplest
to implement.

