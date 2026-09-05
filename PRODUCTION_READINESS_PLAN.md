# Production Readiness Review and Plan — 2026-09-02

Full review of `main` (commit 2718301), every unmerged branch, a measured load probe, and a
phased plan to a production-grade service at 50 requests/second across all endpoints.

**The full, PM-readable document with flowcharts is the published artifact:**
https://claude.ai/code/artifact/dfcad0e6-d2d8-4589-a344-d2b2cf8bb906

The five specialist reports it summarises, with file:line for every finding, are in
[docs/review-2026-09-02/](docs/review-2026-09-02/): `security_report.md`, `perf_report.md`,
`agents_report.md` (includes the node-by-node control flow of every agent), `frontend_report.md`,
`merge_report.md`, plus `load_results.json` and the `load_probe.py` that produced it.

---

## STATUS: Phases 0 and 1 are DONE and deployed (2026-09-04)

**All security work in this plan is complete.** Phase 0 (stop the bleeding) and Phase 1
(security hardening) shipped as 31 commits on `codecleanup`, one per finding, and are pushed.
**Phase 2 (capacity) is also complete now** — see the second STATUS section below; Phase 3
(pipeline + repo) is where this document picks up.

The blow-by-blow — every finding, what was done, what was deliberately not done, and the
three places this plan's own text turned out to be wrong — is in
[docs/plans/SECURITY_HARDENING_PLAN.md](docs/plans/SECURITY_HARDENING_PLAN.md), sections 0
and 0b. Read that before touching anything security-adjacent; this section is only the
summary.

| | |
|---|---|
| Phase 0 | complete, tag `phase-S0` |
| Phase 1 | complete, 15 items (14 + the S0-9 leftover) |
| Findings closed | C1, C2, H1–H5, M1–M4, M6–M11, L1, L2, L3, L5, L6, L10 |
| Still open by design | ~~**M5** (process-wide 5 s Gemini sleep) → Phase 2; argon2 parameters → Phase 2~~ **both CLOSED in Phase 2** (items 1 and 7); L9 (lost updates on `users.data`) → Phase 4; L4 (Stripe webhook) → Phase 6; L8 (PyPDF2 → pypdf) → Phase 6 |
| Tests | 2349 passing (was 2080 at the end of Phase 0); 12 new test files. **Now 2447** after Phase 2 |
| Verified against a real production build | `tsc --noEmit` exit 0 · `expo export -p web` succeeds · **no prompt text left in the shipped bundle**, checked with the same grep the security report used to prove the vulnerability |
| Database | all migrations run and RLS confirmed live — `conversations`, `agent_runs`, `deadline_check_log`, `promo_codes`, `users` all report `rls = true` |

### What changed that Phase 2 has to know about

- **The two AI proxies are gone.** `/api/messages` and `/api/messages-claude` are replaced by
  ONE route, `POST /api/ai`, taking `{feature, inputs}`. Phase 2's async AI lane therefore
  targets a single handler (`app.routes.ai.handle_ai`) rather than two near-duplicates —
  simpler than this plan assumed. The prompts live in `app/services/prompts.py`, which is
  **marquee M8**: editing anything between the triple quotes needs approval first.
- **`classify_feature` and `_FEATURE_SIGNATURES` no longer exist.** Cost attribution reads the
  server-side feature id, so it is exact. Adding an AI feature now means adding it to
  `app/services/prompts.py` AND to `FEATURE_LABELS` in `app/core.py`.
- **Use pure ASGI middleware, not `BaseHTTPMiddleware`.** Phase 1 added the security-headers
  middleware that way deliberately, because the perf report flags the existing
  `BaseHTTPMiddleware`-based one and a second of the same kind compounds it. `SecurityHeaders`
  in `app/main.py` is the pattern to copy.
- **Body caps already exist and are on the shared dependency.** `app.deps.json_body` is capped
  at `JSON_MAX_BODY_BYTES`; `capped_raw_body(n)` is there for a route that needs its own
  ceiling. A new route is bounded by default — don't re-solve this.
- **`select_user()` / `user_exists()` exist** (`app/core.py`) and should be preferred over
  `get_user()`, which pulls the whole row including `password_hash` and the `data` blob.
  Phase 2's 60 s identity cache should cache the narrow read, not the wide one.
- **Errors go through `app.deps.opaque_error()`** — a correlation ref to the caller, the
  detail to `api_errors`. Don't interpolate an exception into a client-facing message; there
  is a test that greps `app/routes/` for exactly that.
- **`wingman/url_guard.py` is the one SSRF answer.** Any new outbound fetch of a
  catalog/user-supplied URL must go through `safe_urlopen`, not `urllib.request.urlopen`.
  A test asserts the five existing sinks still do.

### Still needs a human (blocks neither Phase 2 nor Phase 3 — but all four are STILL OPEN as of 2026-09-05)

1. **Read the `[client-ip]` line off the Render log** after this deploy. `app/main.py` prints
   it once, on the first request. A resolved address still in `10.x` means
   `--forwarded-allow-ips` does not cover Render's LB and the rate limiters are still sharing
   one bucket — which is the H3 finding not actually fixed. This is the single most valuable
   five seconds of verification left.
2. **Watch `/api/auth/refresh` for a burst of 401s** on the first deploy. S1-2's rotation goes
   live the moment this code meets the migrated database. Tokens minted before it carry no
   `jti` and are adopted once rather than read as theft; that path is tested, but it is the
   one change here that could sign the whole user base out.
3. **Set `EMAIL_POSTAL_ADDRESS`** in the Render dashboard. Nothing crashes without it —
   verified by rendering an email with it genuinely unset — but every lifecycle email ships
   with `[SET EMAIL_POSTAL_ADDRESS IN .env]` where a CAN-SPAM-required physical address
   belongs.
4. **Leave `CSP_ENFORCE` unset** until the report-only violations have been read against a
   real exported bundle. Turning it on blind can white-screen the app.
5. ~~Decisions 2 and 3 below are still open.~~ **Both ANSWERED 2026-09-05** — the $0.50/day
   allowance stays as-is and Render stays on the free plan until launch. Neither is now a
   blocker or a to-do. See "Phase 2 approvals and scope" immediately below.

### Phase 2 approvals and scope (logged 2026-09-05, before any Phase 2 code was written)

Shama reviewed the ten Phase 2 items and gave the five rulings below. They are recorded here **before**
implementation so the phase is executed against the scope that was actually approved, not the
scope this document originally proposed.

1. **M9 APPROVED — Phase 2 items 1 and 2 may proceed.** Removing the process-wide Gemini
   `time.sleep` from the web path (finding M5, `wingman/gemini_common.py`) and rebuilding the AI
   call path as an async lane (`app/routes/ai.py`) both sit on code that makes paid API calls, so
   both are **marquee M9**. Shama said yes on 2026-09-05. Standing conditions from
   MARQUEE_DECISIONS.md still apply: **each lands as its own dedicated commit naming M9**, never
   bundled into another change. **No prompt text moves in either — this is NOT an M8 change**, and
   if an implementation turns out to need prompt edits after all, that is a fresh approval, not a
   consequence of this one.

2. **Hosting stays free until launch — so Phase 2's exit test is intentionally NOT met.** The
   original exit test ("50 rps x 10 min on staging, p95 < 1 s") assumed a paid tier. Render Free
   is 0.1 CPU and sleeps when idle; **50 rps is not physically reachable on it**, and Shama has
   decided that is fine because no students have access yet. The revised bar for calling Phase 2
   done is therefore: *the code changes are in and verified correct, and the laptop probe shows
   the shape of the fix* — **not** an absolute throughput number. Bump the plan and re-run for
   real before opening to students; that re-run is Phase 6 / launch-gate work, not Phase 2.

3. **No staging environment; the load test runs on the laptop.** Same method the original review
   used — boot a copy of the service on a spare port with AI keys withheld and probe at
   concurrency 1/8/32 (`docs/review-2026-09-02/load_probe.py`, results in `load_results.json`).
   These numbers are **indicative, not proof**: a laptop is not Render, so report them as
   before/after deltas on the same machine and never as a production capacity claim. The k6
   -on-staging line in the phase table below is deferred, not cancelled.

4. **Item 9 (observability) is DROPPED, 2026-09-05.** No `/healthz`, no structured-logging
   rework, no alerting. Shama will use Datadog's free tier when traffic warrants it. See
   decision 7 below.

5. **Item 8's shedding behaviour is APPROVED (M9), 2026-09-05.** Capping fresh deadline/checklist
   work at 4 in flight means the 5th concurrent caller is **turned away rather than triggering a
   paid check**. Shama: "ok to turn student away for now... we will revisit when I buy hosting on
   Render." So the shed is the intended behaviour pre-launch, and revisiting it is a launch-gate
   task, not a bug report.

**Net effect on the ten items: NINE get built, not ten.** Item 9 is out. Only how the rest are
*verified* changed.

---

## STATUS: Phase 2 is DONE on branch `phase2-capacity` (2026-09-05)

**All nine buildable items are built, committed and green.** Item 9 was dropped (decision 7)
and item 10 is deferred (decision 8), so the phase is closed on its unit tests plus per-change
measurements. The branch is **pushed but NOT merged to `main`** — Shama is testing it. Phase 3
must not assume `main` contains any of this yet.

| | |
|---|---|
| Branch | `phase2-capacity` — pushed, **not merged**, twelve commits including this documentation one |
| Items | 1–8 built; **9 DROPPED** (decision 7); **10 DEFERRED** (decision 8) |
| Marquee | three dedicated M9 commits, each naming the entry: `MARQUEE M9 (Phase 2 item 1)`, `(item 2)`, `(item 8)`. **No prompt text moved, so no M8 was needed or taken** |
| Tests | **2349 → 2447**, exit 0; every item added its own tests |
| Measurement | per-change only. **This phase has no system-level before/after number** — see decision 8 |

| Item | State |
|---|---|
| 1 no Gemini sleep on the web path | **DONE** — `MARQUEE M9 (Phase 2 item 1)` |
| 2 async AI lane | **DONE** — `MARQUEE M9 (Phase 2 item 2)` |
| 3 60s identity cache | **DONE** |
| 4 pooled HTTP (Supabase only) | **DONE** |
| 5 split catalog/vector caches, 24h backstop, gzip+ETag | **DONE** |
| 6 batched cost accounting | **DONE** |
| 7 OWASP argon2 | **DONE** |
| 8 paid deadline/checklist lane | **DONE** — `MARQUEE M9 (Phase 2 item 8)` |
| 9 observability | **DROPPED** (decision 7 — Datadog free tier later) |
| 10 laptop probe | **DEFERRED** (decision 8 — "we'll come to perf tests later") |

**Five things measured or found while building this, worth keeping:**

1. **/api/match was sleeping ~10s per request, not ~5.** The Gemini throttle is taken once to
   embed the student's themes and once for the eligibility gate. The plan described M5 as one
   5s sleep; it was two, on the route matching is built around. Measured after: 5.0s -> 0.000s
   per pair of calls in the web process, with batch agents still at 5.0s.
2. **OWASP's argon2 numbers are LIGHTER than argon2-cffi's defaults**, not heavier
   (19 MiB/t=2/p=1 against 64 MiB/t=3/p=4). Item 7 therefore made sign-in ~2x faster and cut
   its memory ~70%. At the old default, **eight concurrent sign-ins is all 512 MB of a free
   instance — an OOM reachable from an unauthenticated endpoint.** That was live.
3. **AI_MAX_CONCURRENCY shipped at 12, not the plan's 30.** 30 of the shared 40-slot anyio
   threadpool leaves 10 for every other route, which is not meaningfully better than the
   starvation it is meant to prevent.
4. **httpx was imported but never declared.** It was reachable only as a transitive dependency
   of the test client — the identical shape to this plan's Critical #2 (numpy missing from
   `requirements.txt` while the code imported it). Now pinned at `httpx==0.28.1`.
5. **Cost accounting was serialising every paid call.** Attribution spawned a thread per call
   and held one global lock across three or four Supabase round trips inside it, so concurrent
   AI calls queued behind each other's bookkeeping. Item 6 removed both.

**Pre-warming the catalog at startup was deliberately not done.** Render Free sleeps when
idle, so it would add ~10s to every cold start — paid often to help rarely. It belongs with
the paid-tier move, not here.

### What changed that Phase 3 has to know about

Phase 2 left eight seams — seven in `app/`, one in `wingman/`. Each exists to remove a specific
trap, and each has a way of being re-introduced by a well-meaning later change, which is what
this list is for.

- **Two concurrency primitives on purpose, not one.** `app/services/lanes.py` defines
  `PaidLane`, a non-blocking `threading.BoundedSemaphore` that bounds the paid
  deadline/checklist branch (used from `app/routes/opportunities.py`, where those two handlers
  live); `app/routes/ai.py` defines a separate `_AiLane`, a **plain int with no lock**. They are not duplicates. `_AiLane` is safe unlocked *only* because it is
  touched from the single-threaded event loop, where nothing can interleave between reading
  `in_flight` and incrementing it; `PaidLane` is touched from N anyio worker threads at once
  and needs a real semaphore. **Use `PaidLane` for anything reached from a `def` handler; use
  the `_AiLane` shape only on the loop.** Merging them would give the loop-side lane a lock it
  can never contend and would leave one comment explaining when the other applies. Both
  acquire non-blocking and **shed** (`503` + `Retry-After`) — waiting is the behaviour being
  removed, since a queued caller still holds its connection.
- **`app/routes/ai.py` is split, and the split is load-bearing.** `handle_ai` is now
  `async def` and **must stay `async def` and must stay non-blocking**. Being on the event
  loop is what makes a shed free: the lane is full, the caller gets a 503, and no threadpool
  slot is ever taken. Were it plain `def` again, every request would have to win a threadpool
  slot just to learn it should be shed — the flood would drain the pool it is meant to be kept
  out of. All blocking work (both limiters, the subscription read, the budget lookups, the
  provider call) lives in `_serve_ai`, behind `to_thread.run_sync`. Adding one blocking call to
  `handle_ai` stalls the whole process.
- **`wingman/gemini_common.set_interactive_process()` has exactly one permitted caller:
  `app/main.py`.** It is a process-level switch that turns off the 5-second batch throttle.
  `tests/unit/test_gemini_interactive_throttle.py::test_only_the_web_app_flips_the_switch`
  greps `app/ agents/ ops/ wingman/ scripts/ eval/` and asserts the caller list is exactly
  `["app/main.py"]`. An **agent** calling it would lose the delay that keeps a long paid run
  under Gemini's rate limit. Every batch agent still measures 5.0s between calls; item 1
  turned the sleep off in the web process only, under the M9 approval logged above, and that
  is safe precisely because the web process runs no agent in-process. If that ever changes,
  this is the decision that has to change with it — and **M6 in MARQUEE_DECISIONS.md is the
  entry to read first**.
- **`app/http_pool.pooled_urlopen` is how this service talks to Supabase.** It is a shim, not
  a rewrite: it takes the same `urllib.request.Request` the eleven call sites already build
  and raises the same `urllib.error.HTTPError` they already catch, so the catalog's `57014`
  retry and the two `SELECT *` degrades keep working. **The Anthropic call in `app/routes/ai.py`
  deliberately does NOT use it** (Shama, 2026-09-05 — a reused handshake saves under 1% of a
  10-30s request and pooling it would be an M9 change);
  `test_http_pool.py::test_the_anthropic_call_is_not_pooled` pins that `_anthropic_call` still
  says `urllib.request.urlopen`. `httpx` is now a declared dependency in `requirements.txt`.
- **The subscription gate reads `app.core.get_user_subscription()`, not `get_user_account`.**
  It caches five narrow columns per userid for `IDENTITY_CACHE_TTL_SECONDS`; caching the wide
  row instead would hold every account's `password_hash` and calendar refresh token in process
  memory. Staleness is one-directional — a lapse can enforce up to a minute late, a payment
  never can — because **invalidation sits at the `_users_request` choke point** in
  `app/core.py`, not at the ~14 call sites. That placement is the reason it can be trusted: a
  new write path cannot forget to invalidate, because it cannot reach the `users` table
  without going through there. Keep new writes on that seam.
- **Cost accounting is batched; the spend caps are not.** `record_user_cost` buffers a delta
  under the rollup key, `flush_user_costs()` drains it on an interval (and via an `atexit`
  hook), and `record_user_cost_async` no longer spawns a thread — it is now a thin alias kept
  for its callers. **`budget.note_spend()` still fires SYNCHRONOUSLY, first, ahead of any
  buffering**, so the per-user daily budget and the global circuit breaker see every dollar the
  instant it is spent. Only the console's Cost-per-user view lags, by at most
  `COST_FLUSH_INTERVAL_SECONDS`. Moving `note_spend` behind the buffer would turn a latency fix
  into a hole in the spend caps.
- **`app/services/opportunities.py` now has five entry points, and browsing no longer pays for
  vectors.** `fetch_opportunities()` is the vector-free catalog on the short TTL;
  `fetch_vectors()` is `{id: match_vector}` on the 24 h backstop; `fetch_opportunities_with_vectors()`
  joins them for `/api/match`; `catalog_payload()` returns the pre-serialised `(body, gzip, etag)`
  for `/api/opportunities`; **`bust_catalog_cache()` clears BOTH caches.** `ops/core.py` calls
  `bust_catalog_cache()` at four sites and **must never reach into a cache dict again** —
  busting only the catalog would leave a newly-activated listing looking perfectly fine in the
  browser while being **un-matchable for a day**. That is the failure this API shape exists to
  make impossible; a third cache added later is covered for free.
- **Eight new env knobs, all in `app/config.py` with the reasoning above each.** Defaults:
  `AI_MAX_CONCURRENCY` **12**, `AI_SHED_RETRY_AFTER_SECONDS` **5**,
  `PAID_CHECK_MAX_CONCURRENCY` **4**, `PAID_CHECK_SHED_RETRY_AFTER_SECONDS` **10**,
  `IDENTITY_CACHE_TTL_SECONDS` **60**, `COST_FLUSH_INTERVAL_SECONDS` **5**,
  `COST_FLUSH_MAX_KEYS` **200**, `CATALOG_VECTOR_CACHE_TTL` **86400**. None of the eight appears
  in `render.yaml`, so unless one has been set in the Render dashboard, production runs these
  defaults. Tune from the dashboard, not by editing the code.

### Still open going into Phase 3

Kept in one place so none of it scrolls out of sight. None of it blocks starting Phase 3.

1. ~~Anthropic/Gemini provider tiers are still unconfirmed.~~ **CLOSED (Shama, 2026-09-05).**
   `AI_MAX_CONCURRENCY` stays at **12** and the tier question is settled — do not re-open it,
   do not re-raise it in a status section, and do not propose confirming it as follow-up work.
2. ~~Decision 4 below (retire `opportunity-matching` as a branch) is still unanswered.~~
   **ANSWERED yes and DONE in Phase 3** — archived as the tag `archive/opportunity-matching`,
   then deleted from `origin`.
3. **The `cleanup_subject_tags.py` rescue in the Headline is dead, found 2026-09-05.** The
   fb6134 worktree is gone (`git worktree list` shows one worktree), commit `fb6134` is no
   longer a valid object in this repo, and no commit reachable from any local or remote ref
   ever added a file by that name. The most likely cause is the history rewrite + `gc` in
   9ead8270 (scrubbing `frontend/node_modules`), which would have pruned an orphaned worktree
   commit. **Phase 3 should treat that file as lost and rewrite it if it is still wanted**,
   rather than spending time hunting for it.
4. **The four deploy-time items in "Still needs a human" above are still outstanding**, carried
   forward verbatim: read the `[client-ip]` line off the Render log; watch `/api/auth/refresh`
   for a burst of 401s; set `EMAIL_POSTAL_ADDRESS`; leave `CSP_ENFORCE` unset until the
   report-only violations have been read.
5. **Render stays `plan: free` and `USER_DAILY_BUDGET_USD` stays $0.50 until launch** —
   decisions 3 and 2, both answered, both deliberate. A Phase 3 session must not "helpfully"
   bump either.
6. **`docs/review-2026-09-02/load_probe.py` is stale and must be fixed before it is next run** —
   see decision 8.

---

## STATUS: Phase 3 is DONE on branch `phase3-pipeline` (2026-09-05)

Pipeline + repo. Branched off `origin/main` (which already carried the Phase 2 merge, so the
`ops/core.py` collision the Phase 2 handoff warned about never arose). **Pushed but not merged
— Shama is reviewing.** Phase 4 picks up from here.

| | |
|---|---|
| Exit test | **all three met**, each pinned by a named test: two agents at once refuse to overlap (`test_two_agents_at_once_refuse_to_overlap_db` / `_file`), a simulated insert timeout fails loudly (`test_statement_timeout_raises_and_never_narrows`), a snapshot commit inserts 0 dupes (`test_snapshot_commit_inserts_zero_dupes`) |
| Tests | **2626 passing**, up from 2447 at the end of Phase 2. 7 new test files |
| Verified how | unit suite + `tsc` + a **live smoke test**: the real server booted, the real console route exercised. That smoke test found two run-lock bugs every mocked test had missed (see below) — worth repeating in Phase 4 rather than trusting green units alone |
| Marquee | **none taken.** No prompt text moved and no paid call changed. The one item that would have been M8+M9 is deliberately left undone and **parked, not pending** — see below |
| Approvals used | decision 4 (retire `opportunity-matching`) answered yes; new decision 9 (do not touch the merge logic) recorded |

### What shipped

| Item | Where |
|---|---|
| 4.4 insert ladder narrows only for a pending migration | `agents/scrape_opportunities.py` |
| 4.13 service key required wherever a job reads inactive rows | `wingman/supabase_common.require_service_key` + 7 agents |
| 4.2 catalog run lock + sequence-backed ids | `wingman/run_lock.py`, `db/agent_locks_schema.sql`, 4 agents, the console |
| 4.3 cost survives the exception that follows it | 7 sites across 5 agents + `app/routes/opportunities.py` |
| 4.9 a discontinued verdict needs page evidence | `agents/check_links.py` |
| 4.14 every paged read ordered | `wingman/supabase_common.supabase_get` (one fix, 48 call sites) |
| 4.5 one URL key, no re-stamp, all ten snapshot families | `wingman/dryrun_common.py`, `ops/core.py`, `ops/admin.py` |
| 4.1 unsourced URLs rejected, not stored flagged | `agents/scrape_opportunities.py`, `agents/refind_dead_links.py` |
| CI marquee-tag check (+ M4/M6/M7 sentinels) | `scripts/ci/check_marquee_commits.py`, `.github/workflows/ci.yml` |
| `scrape_common.py` | `wingman/scrape_common.py` |
| tests for the untested paid paths | `tests/unit/test_paid_call_plumbing.py` |
| branch cleanup | 13 merged branches deleted from `origin`; `opportunity-matching` archived + deleted |

### Parked, not pending — the one thing not done, and why

**`local_org_discovery.py` from `local-discovery-engine` is NOT ported, and that is settled
rather than outstanding.** Its own header declares both marquee entries — three prompts sent to
a model (M8) and a paid Gemini path (M9) — so porting it needs approval first. Asked and
answered: **Shama, 2026-09-05, "leave it as is, I will decide later."** Nothing is blocked on
it and no future session should re-raise it as an open question; it is a thing to pick up if
and when the local-discovery work is wanted, not a loose end.

Merging the branch would also have done harm, which is why "merge `local-discovery-engine`" was
not executed as written. It predates the 2026-09-04 reorganisation, so it edits
`check_links.py` / `harvest_names.py` / `scrape_opportunities.py` / `url_dedupe.py` at the repo
ROOT and imports by bare name. Compared file by file it is BEHIND main rather than ahead:
the url_dedupe weak-tier suppression, the check_links discontinuation detection and the eval
golden set all reached main by other routes in a newer form — its `ops/core.py` still carries
the looser discontinuation description Phase 3 has just tightened. A `git merge` would have
reverted the whole `wingman/` layout to gain nothing.

Only three things were unique to it. [`DISCOVERY_ENGINE_PLAN.md`](plans/DISCOVERY_ENGINE_PLAN.md)
is ported (with a status note). The prototype and its console card are not. **The branch is
deliberately left on `origin`** so they stay recoverable.

If the answer is yes, the port is more than a copy: bare-name imports, a move under `agents/`,
and the Phase 3 rules every inserting agent now follows — the catalog run lock,
`require_service_key()`, ordered pagination.

### What the live smoke test caught, and the lesson

The unit suite was green and all three exit-test conditions passed before the service was ever
started. Booting it and launching an agent through the real console route then failed on the
first try, twice over:

1. **`current_holder` reported the lock free while a run held it.** With Supabase creds set but
   `db/agent_locks_schema.sql` not yet run, the 42P01 from the DB read hit a `try/except`
   wrapped around both reads and jumped past the file fallback to `return None`. The console
   returned 202 and **started a second paid agent** on top of a run holding the file lock. The
   agent's own guard would still have refused it — the two layers are not redundant by accident
   — but the console layer was simply broken.
2. **A lock left by a killed process wedged the pipeline for the full 900s lease.**

Both fixed, both pinned by regression tests. The lesson is the one worth carrying into Phase 4:
**every unit test here mocked `agent_locks` as present, so the entire class of "the migration
has not been run yet" was untested — which is the state of every checkout, including the one
this will next be pulled into.** Green units did not mean working software; six seconds of
running the real thing did.

### Two things that need a human at the database, not in the code

1. ~~`db/agent_locks_schema.sql` has NOT been run.~~ **RUN by Shama, 2026-09-05, and verified
   live.** The lock now takes the `db` backend: acquire writes an `agent_locks` row, a second
   agent is refused by name, `current_holder` sees the holder, the heartbeat advances the lease,
   a wrong token cannot release and the real one can, and the console answers **409** through
   the real HTTP route without starting anything. `next_opportunity_id` is seeded correctly at
   **ec19575**, one past the live maximum of ec19574, so the sequence can never re-issue an
   existing id.

   **Running it immediately exposed a bug nothing else could have.** `_db_current` read the lock
   table without `order_by`, inheriting this phase's `order=id` default — and `agent_locks` is
   keyed on `name`. Every read of the lock 400'd, so acquiring worked (an INSERT) while the
   REFUSAL path blew up. It is the exact failure `test_non_id_keyed_reads_name_their_key`
   exists to catch, and the table was missing from that test's list because it was added in a
   different commit. Fixed and pinned. Worth carrying forward: **every run_lock unit test mocks
   `supabase_get` away, so no query string in that module was ever exercised against a real
   PostgREST until the table existed to reject it.**
2. **The four deploy-time items from Phase 1 are still outstanding**, carried forward again:
   read the `[client-ip]` line off the Render log; watch `/api/auth/refresh` for a burst of
   401s; set `EMAIL_POSTAL_ADDRESS`; leave `CSP_ENFORCE` unset until the report-only violations
   have been read.

### Deliberate departures from the phase row — three, each recorded where it bites

- **The run lock lives in a new `agent_locks` table, not in `agent_runs`** as the phase row
  says. `agent_runs` is an append-only history: one row per run, no uniqueness on `agent`, and
  a run row is written some way INTO the run (after the catalog fetch and the embedding index
  load) rather than before it. A lock built on it could only be "select rows where finished_at
  is null, and if none, insert" — a read-then-write with a window between the two, which is
  precisely the race being closed. `agent_locks` makes `name` the primary key, so acquisition
  is a single INSERT and the loser is rejected by Postgres with no window at all. Adding a
  unique constraint to `agent_runs` instead would have broken its history (an agent has run
  many times) and coupled the mutex to the audit log. Same file, one extra table.

- **`merges → review queue` — STRUCK.** Decision 9 (Shama, 2026-09-05): do not touch the merge
  logic at all, neither the approval queue nor the page-verification alternative. The
  measurement it was decided on is in that decision.
- **`all snapshot families committable` — 8 of 10, not 10.** `link_check` and `mailing_list` are
  now REGISTERED (so the console lists them, where before they were invisible) but refuse a
  commit with a stated reason. check_links' `build_update` derives `link_status`,
  `link_dead_since`, `link_review_status` and the flag merge from the LIVE row as well as the
  result ("first seen dead wins"; never overturn a human verdict), which a snapshot does not
  carry — so replaying one would write a different result than the live run did, which is the
  very defect 4.5 is about. It is also the one agent that is free to re-run. `find_mailing_lists`
  writes `opportunity_signups`, and `commit_snapshot`'s injected `patch_fn` is bound to the
  catalog table.

### Still open going into Phase 4

1. ~~Anthropic/Gemini provider tiers are still unconfirmed.~~ **CLOSED (Shama, 2026-09-05) —
   settled, not outstanding.** `AI_MAX_CONCURRENCY` stays at 12. This is not to be re-raised.
2. **Render stays `plan: free` and `USER_DAILY_BUDGET_USD` stays $0.50 until launch** —
   decisions 3 and 2. A Phase 4 session must not "helpfully" bump either.
3. **`docs/review-2026-09-02/load_probe.py` is still stale** and must be fixed before it is next
   run (decision 8).
4. **Audit 4.11 is pinned, not fixed.** In BOTH providers the count that decides "did it
   search?" and the count the per-search fee is estimated from are the same derived number.
   Grounding chunks with an empty `webSearchQueries` read as silent, get retried (paying twice)
   and are billed as zero. Fixing it needs live evidence of when each provider omits the field,
   which this repo does not have. `tests/unit/test_paid_call_plumbing.py` records the behaviour
   so the next reader meets it as documentation rather than rediscovering it.
5. **Four unmerged branches remain on `origin`**, none deleted because each holds real work:
   `local-discovery-engine` (above), `docs/ollama-local-analysis-plan` (1 commit, a proposal
   doc), `rearchitecture` (2 commits, superseded by the shipped Phase 1), and
   `wingman/search-feature` (**57 commits** — by far the largest unmerged thing in the repo and
   worth a decision of its own). `backup/codecleanup-pre-scrub` is kept locally as the safety
   copy from the history rewrite.

---

### What changed that Phase 4 has to know about

Phase 3 left seven seams Phase 4 will land directly on top of. Each already exists — the risk
is rebuilding one, or adding a second thing that does the same job badly.

1. **There is already a cross-process mutex, and it is not the file lock.**
   `wingman/run_lock.py` + `db/agent_locks_schema.sql` give a real one: `name` is the primary
   key, so acquisition is an INSERT and the loser is rejected by Postgres with no read-then-write
   window. It leases (900s), heartbeats, guards release on a per-acquisition token, and takes
   over a lock whose holder pid is provably dead on the same host. **Phase 4's "lock file
   batch-only" item is about a DIFFERENT lock** — `gemini_common`'s `.gemini_web_search.lock`,
   which is still a local file guarding the shared googleSearch quota. The obvious move is to
   put that one on `RunLock` too (a second lock name, not a second mechanism); the thing not to
   do is invent a third locking scheme. **The scheduled worker Phase 4 wants MUST take
   `CATALOG_INSERT` before it inserts**, or it reintroduces exactly the collision 4.2 closed.

2. **The RPC helpers Phase 4 needs already exist.** `supabase_common.supabase_rpc(url, fn,
   payload, key)` was added for `next_opportunity_id` and is generic — it is what the
   "idempotent rollups via RPC" and "`jsonb_set` RPC for saves" items should call.
   `supabase_delete(url, table, params, key)` also exists and REFUSES an unfiltered call
   (PostgREST would happily empty a table for an empty filter set).

3. **`supabase_get` now applies `order` itself, defaulting to `id`.** Any table Phase 4 adds
   that is NOT keyed on `id` must pass `order_by=`, or every read of it 400s. This is not
   hypothetical: `agent_locks` shipped without it, every read of the lock 400'd, and nothing
   caught it because all the unit tests mock `supabase_get` away — it only surfaced when the
   table existed to reject the query. **Add the new table to `_NON_ID_KEYED` in
   `tests/unit/test_ordered_pagination.py` in the SAME commit that creates it.**

4. **Money that has been spent survives an exception now, and new paid paths must opt in.**
   `agent_common.bank_onto_exception(exc, cost)` stamps the amount onto the exception;
   `agent_common.banked_cost(exc)` recovers it in the handler. Any paid call Phase 4 adds — the
   scheduled worker especially, since nobody is watching it — needs both halves, or its failures
   are free as far as every total is concerned.

5. **`wingman/scrape_common.py` is where row-building lives.** `build_row`, `next_id_generator`,
   `insert_rows`, `VALID_*` and the `FLAG_*` constants moved there out of the runnable agent. A
   new inserting agent imports from `wingman/`, never from `agents/scrape_opportunities.py`.
   The agent re-exports them, so old import sites still work — but a monkeypatch has to target
   the module the function actually resolves its globals from.

6. **Snapshots: ten families are registered and staleness is now enforced.** Phase 4's "leads +
   snapshots in tables" item must carry three things across, not just the storage: the dedupe
   key is `url_dedupe.match_key` on BOTH sides of the comparison; a patch commit stamps the
   RUN's time (from the filename) rather than `now`, because those columns are staleness clocks;
   and a patch snapshot older than `STALE_DAYS` is refused unless `allow_stale`. `link_check`
   and `mailing_list` are registered as not-committable with their reasons in the spec — moving
   snapshots into a table must not quietly make them committable.

7. **CI now fails a commit that edits marquee-protected code without naming the entry.**
   `scripts/ci/check_marquee_commits.py`, wired into `.github/workflows/ci.yml` (which needs
   `fetch-depth: 0` to resolve a range). It does NOT replace approval — rule 1 is still a human
   gate in chat. Phase 4's scheduled paid runs are M3 territory (approval moves from per-run to
   per-schedule) and the toggle + dollar ceiling the phase row asks for is what makes that safe.

## STATUS: Phase 5 is DONE on branch `phase5-product` (2026-09-05)

Product accuracy. Branched off `phase4-shared-state`. **Pushed but not merged — Shama is
reviewing.**

| | |
|---|---|
| Exit test | **two of three met, the third deferred by decision 16.** *Frontend tests in CI* — a `frontend` job runs `npm ci` → `tsc --noEmit` → `vitest run`. *Bundle −300 KB* — **−453 KiB of JS and −3.89 MiB of fonts, measured**. *Golden-set score holds* — the paid run is deferred; M10 is unaffected |
| Tests | frontend **0 → 132** (8 files); backend unchanged at **2724**, still green |
| Verified how | `tsc --noEmit` clean · 132 vitest cases · a real `expo export -p web` · a Metro dev bundle (973 modules, **zero require cycles**, down from one) |
| Marquee | **one dedicated M9 commit**, approved in advance (decision 13). No prompt text moved — there is none left in the bundle |

| Item | State |
|---|---|
| grade parser context | **DONE** — split into `parseGradeLevel` (explicit) / `parseGradeFromText` (prose) |
| date validation | **DONE** — `src/lib/dateISO.ts`, applied at all four extraction points |
| sort-on-refresh + calendar ids by label | **DONE** |
| synthesis failure keeps transcript | **DONE** |
| unreachable ≠ revoked | **DONE** |
| reset singletons on logout | **DONE** — `src/lib/sessionScope.ts` |
| one retry per action | **DONE** — `MARQUEE M9 (Phase 5)` |
| client timeouts | **DONE** — same M9 commit |
| drop icon fonts + dead code | **DONE** — measured below |
| Vitest ~40 cases | **DONE — 132**, plus the CI job |
| a11y labels | **DONE** — `IconBtn`'s label is now a required prop |
| split big screens | **DONE for `tracker.tsx` (1,228 → 780)**; `finder.tsx` is largely unchanged — see below |

### The measured bundle number

Exported a real production bundle from this phase's parent commit in a worktree and from the
tree, on the same machine:

| | before | after | |
|---|---|---|---|
| entry JS | 1,918,461 B | 1,454,318 B | **−453 KiB (−24%)** |
| `.ttf` files | 26 | 7 | −19 |
| `.ttf` bytes | 4,723,920 B | 647,080 B | **−3.89 MiB** |
| `dist` total | 7,216 KB | 2,744 KB | **−62%** |

The seven surviving fonts are the two Google families the design system uses. The exit test
asked for −300 KB.

### Three things worth carrying forward

1. **Making a prop REQUIRED beats adding a label.** `IconBtn`'s `label` is `string`, not
   `string?`, so the next unlabelled icon button is a build error. Adding it surfaced all five
   existing call sites immediately. An optional prop gets left off and there is no way to
   notice from looking at the screen — which is how there came to be five labels in the whole
   app.
2. **Running Metro caught what `tsc` and the tests could not.** The date validators first
   lived in `status.ts`, which already imports `trackerStore` — so `trackerStore` importing
   them back made a **require cycle**. Metro warns these "can result in uninitialized values".
   Both uses are inside functions so it was benign, but a benign cycle is one edit from a
   module-init `undefined` that fails at runtime and nowhere else. `src/lib/dateISO.ts` is a
   leaf and cannot participate in one. This is Phase 3's lesson again in a different medium:
   green units did not mean a clean bundle.
3. **The compiler can make a refactor safe.** Splitting a 136-key shared `StyleSheet` across
   three files is normally where a silent regression hides — a missed key becomes `undefined`
   and renders as nothing. `StyleSheet.create` returns a TYPED object, so every misplaced key
   was a build error, in both directions, along with eleven imports the split made dead.

### Deliberately NOT done, each needing a fresh approval

Both are real, both are in `frontend_report`, and **neither is covered by decision 13** — that
approval named the retry caps and the client timeouts and nothing else. They are recorded here
rather than done quietly, which is exactly what MARQUEE_DECISIONS.md rule 1 is for.

1. **Finding 6 — "Check for updates" can run concurrent PAID passes.** The guard is component
   state, and expo-router remounts the screen on every visit, so leaving the Quest Log and
   coming back re-enables the button while a pass (N × ~$0.07) is still running. The fix is
   the same shape as finding 18's: move the in-flight flag to a module singleton. **M9** —
   it is the guard on a paid path.
2. **Finding 14 — the dead tag-scoring path, and the duplicated add.** `scoreOpportunitiesForTag`
   is unreachable (`setSelectedTag` only ever receives `null`), and `addOneToTracker` in
   `finder.tsx` duplicates `addCatalogOpportunity` in `src/api/trackerAdd.ts`. Deleting the
   first and collapsing the second both **remove a paid call path**, which is M9 territory
   whatever the path's reachability. The `kindForOpp` duplicate WAS removed — it is pure and
   makes no call.

`finder.tsx` is therefore still 1,987 lines. Its recommended split leans on deleting those two
paths first (the audit says so explicitly: *"First delete the dead tag-scoring path (~130) and
the `addOneToTracker`/`kindForOpp` duplicates (~95)"*), so the rest is best done in the same
pass as the approval above. Its pure data did move out, to `src/lib/finderSearch.ts`.

### Needs a human

Nothing new. Phase 4's four migrations are still outstanding and are unaffected by anything
here; Phase 1's four deploy-time items are still outstanding.

---

## STATUS: Phase 4 is DONE on branch `phase4-shared-state` (2026-09-05)

Shared state. Five items, built as five commits plus one marquee commit. **Pushed but not
merged — Shama is reviewing.** Phase 5 picks up from here.

| | |
|---|---|
| Exit test | **the testable half is met.** *A second machine sees the same lead queue* is pinned by `test_a_second_machine_sees_the_same_queue` and `test_marking_processed_on_one_machine_stops_the_other_re_paying`. *Two instances pass the 50 rps test* is **deferred with every other throughput bar** (decisions 3 and 8) — Render Free cannot reach 50 rps and that is a choice, not an oversight |
| Tests | **2706 → 2724**, exit 0. 5 new test files, 89 new tests |
| Verified how | unit suite **plus a live smoke test against the real Supabase** — see below. Phase 3's lesson was that green units did not mean working software, and it held again |
| Marquee | **one dedicated M9 commit**, approved in advance (decision 13). No prompt text moved, so no M8 |
| Migrations | **four new .sql files, none of them run yet.** See "Needs a human at the database" below |

| Item | State |
|---|---|
| handoff tokens survive a second worker | **DONE** — `app/services/handoff_store.py`, `db/auth_handoffs_schema.sql` |
| lock file batch-only | **DONE** — `MARQUEE M9 (Phase 4)` |
| idempotent rollups via RPC | **DONE** — `db/cost_rollup_rpc.sql` |
| `jsonb_set` RPC for saves (finding **L9**) | **DONE** — `db/user_data_rpc.sql` |
| leads + snapshots in tables | **DONE** — `db/discovered_leads_schema.sql`, `db/agent_snapshots_schema.sql` |
| scheduled worker | **DROPPED** (decision 14) |
| optional direct Postgres | **SKIPPED** (decision 15) |

### The pattern every one of these follows, and why

**Two backends, DB preferred, local fallback, warned once, naming the `.sql` file.** It is
`wingman/run_lock.py`'s shape from Phase 3, reused four more times rather than reinvented, and
it exists because of Phase 3's own lesson: *every unit test mocked the table as present, so the
entire class of "the migration has not been run yet" was untested — which is the state of every
checkout.* The fallback is not politeness; it is the only reason a fresh clone still works.

A **real** failure raises instead of falling back, everywhere. That distinction is the one worth
holding onto: an outage answered with a local file reads as success and quietly does the wrong
thing — re-mining a hub the shared queue had already processed, or committing a stale snapshot.

### What the live smoke test found

The unit suite was green before the service was ever started. Booting it and driving the real
routes against the real (un-migrated) Supabase then confirmed all four fallbacks — and produced
the four warnings verbatim, each naming its `.sql` file:

- `GET /api/agents/leads` → `200`, `"backend": "file"`, warning names `db/discovered_leads_schema.sql`
- `GET /api/agents/snapshots` → `200`, `"backend": "local"`, warning names `db/agent_snapshots_schema.sql`
- `GET /api/auth/google/start` → `302` to Google with a state cookie, warning names `db/auth_handoffs_schema.sql`
- `update_user_data()` on a non-existent account → `False`, warning names `db/user_data_rpc.sql`

One thing it showed that no test could: **this checkout has no `discovered_leads.jsonl` and no
snapshot files at all.** The 208 KB queue finding 4.20 is about lives on a different machine.
That is the finding, demonstrated rather than argued.

### Needs a human at the database — FOUR migrations, none run

None of these blocks anything: every code path falls back and warns. But until they are run,
**Phase 4 has changed nothing about the actual behaviour** — it has only made the shared
version available.

1. **`db/auth_handoffs_schema.sql`** — until this runs, Google sign-in nonces stay in process
   memory, so this service must keep running ONE worker. This is the migration that unblocks
   `--workers > 1`.
2. **`db/user_data_rpc.sql`** — until this runs, `/api/data/save` keeps the read-modify-write
   that loses concurrent updates (**L9**, open since Phase 1). This is the one with a
   user-visible failure today: two tabs, or a phone and a laptop, silently drop one save.
3. **`db/cost_rollup_rpc.sql`** — until this runs, cost accounting keeps its read-then-PATCH.
   Note this file also MERGES pre-existing duplicate rollup rows before creating its partial
   index; it prints a `notice` for each. Read those.
4. **`db/discovered_leads_schema.sql`** and **`db/agent_snapshots_schema.sql`** — until these
   run, the lead queue and the dry-run snapshots stay on whichever laptop produced them.

Run them in that order (they are independent, but 1 and 2 are the ones with live impact).
**Then re-run the smoke test**: each of the four warnings above should stop appearing, and
`/api/agents/leads` should report `"backend": "supabase"`.

### Deliberate departures from the phase row — three

- **The handoff tokens are a TABLE, not the HMAC-signed token the phase row and perf_report
  name.** A signed token needs no storage but is **replayable until it expires**, and single-use
  is the entire point of these nonces — S1-3 made the calendar handoff single-use precisely so a
  URL sitting in browser history or leaking through a `Referer` is inert on the second click.
  Consumption is now one `DELETE ... Prefer: return=representation`, so Postgres serialises two
  concurrent spenders and exactly one gets the payload. The nonce is stored as a **sha256**,
  never in the clear. A future session must not "simplify" this into a JWT.
- **`||`, not `jsonb_set`.** For one key they are equivalent, but `jsonb_set` takes a single
  path, so a multi-key save would need one call per key and be back to N statements. `||` merges
  every top-level key in one go, which is what makes `/api/data/save`'s new `{values: {...}}`
  form worth having.
- **The `agent_runs` unique index is PARTIAL.** That table is an append-only history — an agent
  has run many times — so a plain unique index on `(agent, mode)` would break every real agent
  immediately. Only `interactive_gemini` and `interactive_claude` genuinely have one row per
  `(agent, day)`, and the index says exactly that. `bump_interactive_run` refuses any other
  agent by name, because outside the partial index the `ON CONFLICT` never fires and every call
  would append a row to somebody's audit log.

### What changed that Phase 5 has to know about

Phase 5 is almost entirely frontend, so most of this does not touch it. Three things do.

1. **`/api/data/save` now accepts `{values: {key: value}}`** as well as `{key, value}`, and the
   multi-key form is ATOMIC. The client still sends one key at a time; wiring `saveData` to
   batch is a natural Phase 5 job (the profile synthesis writes four keys serially). The M4
   per-value size cap is checked per value, not on the batch — do not "simplify" it into one
   check on the whole body.
2. **The M9 approval Phase 5 carries (decision 13) covers the retry caps and the client
   timeouts, and nothing else.** If a Phase 5 change turns out to need a prompt edit, that is a
   fresh approval — and there is no dead prompt text left in the bundle to delete, which was
   checked.
3. **The paid golden run is deferred (decision 16), but M10 is not relaxed.**
   `eval/run_golden_matching.mjs` imports `frontend/src/lib/ranking.ts` directly, so it inherits
   a ranking change rather than drifting from it. A change to `finder.tsx`'s `callMatchMapped`
   curation/sort/slice still has to be mirrored in the harness in the same commit.

---

### Phase 4 approvals and scope (logged 2026-09-05, before any Phase 4 code was written)

Recorded **before** implementation, so the phase is executed against the scope that was actually
approved rather than the scope this document originally proposed. Full text in decisions 13–16
above; the short version:

| Phase-4 item | Ruling |
|---|---|
| handoff tokens survive a second worker | build |
| lock file batch-only | build — **M9 approved**, its own dedicated commit |
| idempotent rollups via RPC | build |
| `jsonb_set` RPC for saves | build — this is finding **L9**, open since Phase 1 |
| leads + snapshots in tables | build — this is the half of the exit test that is testable today |
| scheduled worker | **DROPPED** (decision 14). No M3 is needed or taken |
| optional direct Postgres | **SKIPPED** (decision 15), deferred to the launch gate |

**Five items, one marquee commit, no paid call made or scheduled by this phase.**

### Picking Phase 4 up cold

Phase 4 is **shared state** — making the pipeline correct when more than one process, or more
than one machine, is running it. Its row in the phase plan carries the item list and its exit
test (*two instances pass the 50 rps test; a second machine sees the same lead queue*).

Three orientation notes.

**First, the 50 rps half of that exit test cannot be met on the current hosting and that is
deliberate.** Render stays `plan: free` until the app opens to students (decision 3), and the
throughput bar was already deferred once for Phase 2 on exactly that basis (decision 8). Do not
bump `render.yaml`; do not treat the deferral as an oversight to fix. The *second machine sees
the same lead queue* half is testable today and is the real work.

**Second, read [docs/CLAUDE-ops.md](CLAUDE-ops.md) before editing anything under `agents/`,
`wingman/` or `ops/`** — most of it is a record of something that already went wrong once, and
six of the seven catalog agents cost real money per run.

**Third, and learned the hard way today: run the thing before believing the tests.** Phase 3's
unit suite was green and all three exit-test conditions passed before the service was ever
started. Booting it and driving the real console route then failed on the first attempt, twice
— once because a status probe swallowed a missing-table error and reported a held lock as free
(so the console launched a second paid agent), and once because a read inherited an `order=id`
default against a table keyed on `name`. Both were invisible to the unit tests, because those
mock the database seam away entirely. Phase 4 is *entirely* about shared state, which is the
category unit tests are worst at. Budget for a live two-process test, not just green pytest.

---

### Picking Phase 3 up cold (HISTORICAL — Phase 3 is done; kept for the record)

Phase 3 is **pipeline + repo** — the agents, `agent_runs`, the review queue, the URL rules and
the branch cleanup. Its row in the phase plan below carries the full item list, its approvals
(**M8 if any prompt text moves** — approval first, then its own dedicated commit; plus
**decision 4**, answered yes on 2026-09-05, which gated the branch work) and its exit test (*two agents at
once refuse to overlap; a simulated insert timeout fails loudly; a snapshot commit inserts 0
dupes*).

Two orientation notes for that reader. First, **read [docs/CLAUDE-ops.md](docs/CLAUDE-ops.md)
before editing anything under `agents/`, `wingman/` or `ops/`** — that is where the seven agents,
what each costs, and the dry-run/preview/commit tiers are written down, and most of it is a
record of something that already went wrong once. Second, **`ops/core.py` is the one file where
Phase 2 and Phase 3 collide, and the collision is invisible until merge time.** On `main` it
still imports `_opportunities_cache` / `_opportunities_cache_lock` from
`app/services/opportunities.py` and sets `["fetched_at"] = 0.0` at four sites; on
`phase2-capacity` those four sites are `bust_catalog_cache()` calls and the two module-level
names no longer exist under those spellings. **Branch Phase 3 off `phase2-capacity`, or expect
to resolve `ops/core.py` by hand** — and resolve it toward `bust_catalog_cache()`, because the
old spelling clears only the catalog and would leave a newly-activated listing un-matchable for
a day.

---

## Headline

| | |
|---|---|
| Critical | 2 — **both CLOSED** (Phase 0). Open, unmetered AI proxy (`app/routes/ai.py`) — live-verified 2026-09-03, see below; `numpy` missing from `requirements.txt` |
| High | 9 — **8 CLOSED** (Phases 0–2): `email_verified`, the prefix-match open redirect, the global login bucket, the per-user spend cap, the catch-all static route, both pipeline items, and — in Phase 2 — AI calls stalling the shared 40-thread pool (M5, items 1 and 2). **1 remains** and it is not security: `opportunity-matching` — **CLOSED in Phase 3**: archived as tag `archive/opportunity-matching` and deleted from `origin` (decision 4, answered yes 2026-09-05). Still never to be merged; the tag is how it stays recoverable |
| Capacity today | ~10–15 mixed rps on Render free (0.1 CPU, sleeps). Laptop measurement: catalog 70 rps ceiling, authed data 27–95 rps, ~150 ms Supabase gate read per signed-in request. **Phase 2's per-change fixes are not reflected in these numbers** — they are the pre-Phase-2 probe, and no post-Phase-2 probe was run (decision 8) |
| Tests | backend suite green at **2617** (2447 after Phase 2) (2349 after Phase 1, 2080 after Phase 0, ~1900 at review time), `tsc --noEmit` clean, still zero frontend tests (Phase 5) |
| Branches | at review time: 34 local, 29 fully merged. **Phase 3 cleaned this up: 13 fully-merged branches deleted from `origin`, 4 unmerged ones deliberately kept (see the Phase 3 status section). Earlier note: only 4 local remained** (`main`, `codecleanup`, `phase2-capacity`, `backup/codecleanup-pre-scrub`) — the rest survive on `origin` only, so most of Phase 3's branch cleanup is already accounted for. Still true: merge only `local-discovery-engine`; **never** merge `opportunity-matching`. **The `cleanup_subject_tags.py` rescue is no longer possible** — see "Still open going into Phase 3" |
| Spend by this review | $0 — no paid agent was run; the load probe ran with AI keys withheld |

## Decisions needed from Shama

*All nine below are ANSWERED as of 2026-09-05. Kept in full because several say "do not change
this" and the reasoning is the point — a future session that re-opens one without reading it is
the failure this list exists to prevent. Nothing here is waiting on Shama.*

1. ~~Move AI prompts server-side (M8)?~~ **ANSWERED yes; shipped 2026-09-04** as S1-1, its own
   dedicated M8 commit. The prompt text was moved verbatim and that was verified character by
   character against the originals.
2. ~~Daily AI allowance per student.~~ **ANSWERED (Shama, 2026-09-05): keep the $0.50/day
   placeholder; do not tune it now.** The measured-5x-median exercise is deliberately deferred —
   there is no student traffic yet, so the Cost-per-user tab has nothing meaningful to take a
   median of. Shama will raise `USER_DAILY_BUDGET_USD` directly in the Render dashboard when it
   starts biting. **No code change is wanted here**; the env var is already the knob. A future
   session must not "helpfully" re-tune this default.
3. ~~Hosting.~~ **ANSWERED (Shama, 2026-09-05): stay on `plan: free` until the app actually opens
   to students.** This is a deliberate, informed choice, not an oversight — see "Phase 2 approvals
   and scope" above for what it means for Phase 2's exit test. Do not open a PR bumping the plan;
   `render.yaml` saying `plan: free` is the intended state today.
4. ~~Retire `opportunity-matching` as a branch (archive tag, extract per decision)?~~
   **ANSWERED yes (Shama, 2026-09-05); done in Phase 3.** Tagged `archive/opportunity-matching`
   at its tip `f3c5cd73` and pushed, then the branch was deleted from `origin`. The tag is how
   it stays recoverable; it is still never to be merged.
5. ~~Keep logging chat turns + IP to `conversations`?~~ **ANSWERED (Shama, 2026-09-04): keep
   the turns, drop the IP.** Shipped as S1-9 — `client_ip` is no longer written and the column
   is dropped, RLS is on and confirmed live, and userids/emails no longer go to stdout.
6. Split the catalog cache and refresh embeddings on a **24 h** backstop instead of 5 min?
   **Decided yes (Shama, 2026-09-02).** See "Live finding" below.
7. Observability — `/healthz`, structured logs, alerting (Phase 2 item 9)?
   **ANSWERED (Shama, 2026-09-05): DROPPED from Phase 2 entirely — do not build it.** Shama
   intends to use **Datadog's free tier** for logs and alerting, and does not want it before
   there is real traffic. This supersedes the earlier in-session suggestion to ship `/healthz`
   plus structured logs now and defer only the alert wiring: **none of item 9 is in scope.** A
   future session must not add a health endpoint, a logging framework, or an alert integration
   under the banner of "finishing Phase 2" — Phase 2 is complete without it. Revisit when
   traffic justifies it, alongside the paid Render tier (decision 3).
8. ~~Run Phase 2's laptop before/after load probe (item 10)?~~ **ANSWERED (Shama, 2026-09-05):
   DEFERRED — "we'll come to perf tests later."** Phase 2 is closed on its unit tests plus the
   per-change measurements recorded above, and **that means the phase has no system-level
   before/after number**: nothing measured the service end to end with items 1, 2, 3, 4, 5, 6,
   7 and 8 all in at once. The per-item figures (5.0s → 0.000s on the Gemini throttle, ~2x
   faster sign-in, one buffered write instead of three round trips per paid call) are real but
   they are measurements of parts. This is a known, accepted gap, not an oversight — and it
   compounds decision 3's already-deferred *50 rps on staging* bar. Both come due at the same
   moment: bump Render off free, then measure, before students get access.

   **Prerequisite for whenever perf testing resumes:
   [`docs/review-2026-09-02/load_probe.py`](docs/review-2026-09-02/load_probe.py) is STALE and
   must be fixed before it is run again.** Two of its six scenarios (`ai_messages_mock`,
   `ai_claude_mock`) POST to `/api/messages` and `/api/messages-claude` with a client-supplied
   `system`/`userContent`/`useWebSearch` body. **Those routes were deleted in Phase 1** and
   replaced by the single `POST /api/ai` taking `{feature, inputs}`, so both scenarios now
   record nothing but 404s. It also has **no `/api/match` scenario at all** — which is where
   Phase 2's largest measured win is (that route was paying ~10s of Gemini sleep per request).
   The danger is not that it fails; it is that it *succeeds*: anyone running it unmodified gets
   a confident-looking latency table that says nothing whatsoever about items 1, 2 or 8. Port
   the two AI scenarios onto `/api/ai` and add an `/api/match` one **before** trusting a single
   number out of it.

9. Route the scraper's auto-merges through the review queue (Phase 3, from finding 4.6)?
   **ANSWERED (Shama, 2026-09-05): NO — leave the merge logic exactly as it is.** Not the
   approval queue the phase row asked for, and not the page-verification alternative offered
   alongside it: **do not touch `classify_same_url`, `merge_row` or `apply_merge` at all.**

   The measurement behind the ruling, taken live on 2026-09-05 before it was made: across the
   whole catalog only **22 rows carry a merge, 59 merge events**, from two scraper runs
   (8 on 2026-08-26, 51 on 2026-08-28). **Every one of the 59 is a `filled <field>` note** —
   a merge has never yet overwritten anything, only populated a field that was empty on the
   survivor. Finding 4.6's "no visible record" premise is also **out of date**: the console
   already carries a *Recent merges* panel over `GET /api/agents/merges`
   (`ops/core.list_recent_merges`), added after the audit was written, and every merge stays
   hand-reversible from the survivor's `quality_flags`.

   Two things a future session must NOT do under the banner of finishing Phase 3: add an
   approval gate in front of merges, or "harden" them by putting merged fields through the
   page-metadata overlay that inserted rows get. The second one is a real and still-true
   observation — merged fields come from phase-2 search notes rather than the page — and it
   was put to Shama with that caveat stated. The answer was still no. Log it here and leave
   it; do not re-raise it as a new finding.

   **Phase 3's `merges → review queue` item is therefore struck from the phase row below.**

10. ~~Port `local_org_discovery.py` (the `local-discovery-engine` prototype) onto main?~~
    **ANSWERED (Shama, 2026-09-05): leave it as is, decide later.** It is an M8 + M9 item by its
    own header — three prompts and a paid Gemini path — so it cannot be ported without a fresh
    yes. Parked, not pending: nothing is blocked on it and it should not be re-raised as an open
    question. The branch stays on `origin` so the prototype remains recoverable.

11. ~~Clean up the remaining unmerged branches?~~ **ANSWERED (Shama, 2026-09-05): no — leave them
    alone.** `wingman/search-feature`, `local-discovery-engine`, `rearchitecture` and
    `docs/ollama-local-analysis-plan` all stay on `origin` untouched. Only fully-merged branches
    were deleted. Do not revisit this.

12. ~~Confirm the Anthropic/Gemini org tiers behind `AI_MAX_CONCURRENCY`?~~ **ANSWERED
    (Shama, 2026-09-05): closed. `AI_MAX_CONCURRENCY` stays at 12.** It was the last surviving
    entry in the Method section's "assumptions to confirm" and had been carried forward through
    Phases 2 and 3. It is now retired from that list and from every "still open" section.
    **A future session must not re-raise it** — not as an open question, not as a risk note, and
    not as suggested follow-up work.

13. **Marquee approvals for Phases 4 and 5 (Shama, 2026-09-05): TWO M9 items approved, a third
    withdrawn.** Approved: (a) making the Gemini web-search lock **batch-only** in
    `wingman/gemini_common.py`, and (b) the frontend **retry caps + client timeouts** on the paid
    AI paths (`aiJson.ts`, `finder.tsx`, `trackerAdd.ts`, `profileTags.ts`, `httpClient.ts`). Both
    are M9 because they edit code inside a paid call path — neither increases what anything spends
    and (b) strictly reduces it. **Each lands as its own dedicated commit naming M9.** No prompt
    text moves in either, so **no M8 was asked for or taken**. The third item put to Shama — a
    scheduled worker, which would have been M9 + M3 — was **not** approved; see decision 14.

    Also settled while asking: **there is no dead prompt text left in the frontend bundle.** The
    phase-5 row's "dead prompts/code" was written before S1-1 moved every prompt server-side;
    `AiRequest.system` in `frontend/src/api/types.ts` is a dead *type field*, not a prompt.
    Verified by grep 2026-09-05. **Phase 5 is therefore not an M8 phase** — if a future session
    thinks it has found a prompt to delete there, it should look again before asking.

14. **The scheduled worker is DROPPED — Shama, 2026-09-05: "I don't want to build a scheduler. I
    have dropped that idea."** Not deferred, not parked pending a hosting decision: dropped. So
    Phase 4 builds **five** items, not seven, and takes **no M3 approval** (M3 governs paid runs,
    and nothing here launches one). Every catalog agent stays operator-triggered from the console
    or the CLI, exactly as today.

    Two consequences a later session must not "fix": the catalog's freshness remains a function of
    somebody pressing a button (audit §5.1 is an accepted state, not an open bug), and the note in
    "What changed that Phase 4 has to know about" that *"the scheduled worker MUST take
    `CATALOG_INSERT` before it inserts"* is now moot — there is no scheduled worker to take it.
    `wingman/run_lock.py` is untouched by this phase.

15. **"Optional direct Postgres for hot queries" is SKIPPED — Shama, 2026-09-05.** Deferred to the
    launch gate, alongside the other performance work (decisions 3 and 8). It would add a psycopg
    dependency, a database password to manage, and a **second way to reach the same database**
    beside PostgREST — permanently, for every future reader — to buy throughput that cannot be
    used on Render Free. Revisit when the plan is bumped, not before.

16. **The paid golden-set run at the end of Phase 5 is DEFERRED — Shama, 2026-09-05.**
    `eval/run_golden_matching.mjs` puts 50 profiles through the live `/api/match` plus the Claude
    reranker; that is real money and M3 needs a fresh yes, which was not given. Phase 5 therefore
    closes on its unit tests plus `tsc`, and **the phase row's "golden-set score holds" bar is
    unmet by choice** — the same treatment decision 8 gave Phase 2's load probe.

    **This does NOT relax M10.** The one Phase 5 change that touches match quality is the grade
    parser, and `eval/run_golden_matching.mjs` imports `frontend/src/lib/ranking.ts` directly, so
    it inherits the change rather than drifting from it. If a Phase 5 change turns out to need a
    harness edit to stay byte-aligned with `finder.tsx`, that edit is still mandatory — M10's
    obligation is to keep the two in sync, and it is independent of whether anyone pays to run it.

## Live finding (2026-09-02): catalog fetch statement-timeout + cache decoupling

Surfaced by a real user report ("search a profile theme → *Search failed: Could not reach
Supabase: HTTP Error 500*"), investigated live. Full detail in
[perf_report.md](docs/review-2026-09-02/perf_report.md) §F.

- **Bug (confirmed live).** `fetch_opportunities()` pulls the server-only 768-dim `match_vector`
  for all **1,686** active rows in 1,000-row pages; a full vector page sits at Supabase's
  per-statement timeout and **intermittently 500s** (`57014 canceling statement due to statement
  timeout`). No graceful path (the code only degrades from the *400* of an un-migrated column), so
  a cold cache surfaced it as a 502 = "Search failed".
- **Fix (shipped, low-risk, uncommitted).** Smaller pages (`CATALOG_PAGE_SIZE = 250`) + retry the
  transient `57014` with backoff, in `app/services/opportunities.py`. 4/4 cold fetches now succeed;
  regression tests added. Latency unchanged (~10 s cold), but reliable. Removes the user-facing
  failure now.
- **Planned cleanup — SHIPPED as Phase 2 item 5 (2026-09-05), except (d).** (a), (b) and (c) are
  in `app/services/opportunities.py`: the caches are split, the vector cache sits on
  `CATALOG_VECTOR_CACHE_TTL` (24 h), and `bust_catalog_cache()` clears **both**, which is what
  keeps an activation instantly matchable. **(d) pre-warm on startup was deliberately not
  done** — Render Free sleeps when idle, so it would add ~10 s to every cold start; it belongs
  with the paid-tier move. The original text follows unchanged. `/api/opportunities` (browsing) loads the
  vector only to strip it; only `/api/match` uses it, and vectors change only on a re-embed. So:
  (a) split into a light vector-free catalog cache (keep short TTL — admin edits still appear fast)
  and a vector cache used only by matching; (b) refresh the vector cache on a **24 h backstop**
  (decision 6) since a 5-min cadence on rarely-changing embeddings is pure waste; (c) **keep
  instant-on-change** — `ops/core.py` busts the cache on activate/moderate and the split must bust
  both, or a new listing goes un-matchable for a day; (d) optionally **pre-warm on startup** so the
  first match after a deploy/timer doesn't eat the ~10 s cold load (matching is intended to be the
  primary feature, and the cache amortizes the load across all searches, so cost does not scale
  with popularity). **Paused** because it touches `ops/core.py` + the tested cache contract that a
  concurrent workstream is editing; it also makes Phase-2 Fix 6/7 (gzip+ETag catalog) simplest.

## Live verification (2026-09-03): AI proxy exposure red-teamed

Critical #1 ("open, unmetered AI proxy") was **reproduced live** against the running dev service
with the real Anthropic key configured — the D-series (infra) of a red-team of the
profile-gatherer chat. Worksheet + full A–F suite:
https://claude.ai/code/artifact/5ef2ad4a-6c14-4e7e-948e-f2f760601fb3
Spend for this verification: **~$0.024** (12,125 input + 433 output tokens + 1 web search; keys
were live, so these were real calls). Findings sit in
[app/routes/ai.py](app/routes/ai.py) and apply to **both** `/api/messages-claude` and
`/api/messages` — one shared handler shape.

| Probe | Sent | Observed | Meaning |
|---|---|---|---|
| D5 no auth | POST, no bearer | `200` (not `401`), real billed call | `subscription_block_reason(None)` fails open — anonymous callers reach the live model |
| D1 flood | 12 rapid POSTs | `200×12`, **0× 429** | no rate limit on the route |
| D4 oversized | 41,040-byte body | `200` (not `413`), **9,703 input tokens billed** | no request-size guard; input billed in full |
| D3 search flip | client `useWebSearch:true` | **`web_search_requests=1`**, +2,240 billed input tok | client controls tool use; the profile chat never sets it |
| D2 clamp | `maxTokens` `999999` / `500` | `→8000` / `→1000` | clamp works, one-directional; only note is the 8000 ceiling is always reachable |

**The risk is the composition, not any single probe:** the endpoint is unauthenticated (D5) +
unthrottled (D1) + unbounded-input (D4) + web-search-capable on the client's say-so (D3), all
against the live key with spend attributed to nobody. A loop of oversized, search-enabled
anonymous POSTs is a direct, unmetered drain on the Anthropic/Gemini keys. D2 is the one clean
result.

### Remediation (three fixes)

All three are **proxy-layer** changes in `app/routes/ai.py` — no prompt text moves, so **not
M8**; they gate the paid path, so they land under the **M9** approval Phase 0 already carries.

1. **Gate the live path (D5).** When a real key is configured, require an authenticated,
   subscribed caller → `401` anon / `402` lapsed. Keep the **mock** path (no key) reachable
   signed-out so offline dev still works (CLAUDE.md's standing constraint). Already named in
   Phase 0 ("proxy requires subscribed caller on live path"); this confirms it is load-bearing,
   not theoretical.
2. **Throttle + size-cap both routes (D1, D4).** A per-IP *and* per-user limiter → `429` +
   `Retry-After`, and a max body / `userContent` length → `413` **before** the upstream call.
   The per-user budget/circuit-breaker is already in Phase 0; the **body cap currently sits only
   in Phase 2 — pull it forward to Phase 0**, since D4 is a direct billing lever with no auth in
   front of it today.
3. **Pin tool use server-side (D3) — new item, add to Phase 0.** Stop honoring the client
   `useWebSearch` on the Claude route: hard-pin it `false` for the profile chat (no interactive
   Claude feature uses search). On the Gemini route `useWebSearch` *is* used by real features, so
   there it must be **feature-gated server-side** (derived from the request's feature id, not the
   client flag), never blanket-off.

D2 needs no fix; optionally feature-gate the reachable 8000 `maxTokens` ceiling later (low
priority). Suggested additions to the Phase 0 exit test: a signed-out live POST → `401`; a
`useWebSearch:true` body on `/api/messages-claude` performs **0** web searches; an over-limit
body → `413`.

## Phase plan (one engineer, AI-assisted; ~31 engineer-days over ~8 weeks)

| Phase | When | Effort | Approvals | Exit test |
|---|---|---|---|---|
| **0 DONE** Stop the bleeding — numpy + exact pins; proxy requires subscribed caller on live path; Anthropic timeout + `max_uses`; per-user daily budget + forced-recheck cooldown + circuit breaker; static allow-list; `FORWARDED_ALLOW_IPS` + login key (ip,user); `email_verified` + exact redirect host; paid tier; delete tracked logs/dumps/stray Render CLI README+CHANGELOG; rotate the PAT in the git remote | Days 1–3 | 2 d | M9 (proxy) | signed-out proxy POST → 401; clean Render build passes; `/ops/admin_console.html` → 404 |
| **1 DONE** Security — prompts server-side by feature id; refresh-token rotation; calendar handoff nonce; `url_is_public()` + auth on submissions; body limits + security headers (CSP report-only) + Secure cookies; conditional promo PATCH; single login-failure message; ops token; `conversations` RLS or stop; promo table; argon2-wrap legacy rows | Wk 1–2 | 5 d | M8 | no High/Medium open; replayed refresh token revokes lineage |
| **2 DONE** Capacity — no Gemini sleep on web path; async AI lane (semaphore 12, timeouts, 503+Retry-After); 60 s identity cache; pooled HTTP (Supabase only); pre-serialized gzip+ETag catalog + split vector cache on a 24 h backstop; batched cost accounting; OWASP argon2; semaphore 4 on fresh deadline/checklist; ~~`/healthz` + structured logs + alerts~~ **DROPPED 2026-09-05 (Datadog free tier later, decision 7)**; ~~k6 load test on staging~~ ~~laptop probe~~ **DEFERRED 2026-09-05 (decision 8)**; confirm provider tiers — **still open** | Wk 2–4 | 7 d | **M9 granted 2026-09-05**; three dedicated commits | **CLOSED on unit tests + per-change measurements** (2349 → 2447, exit 0). Both throughput bars — laptop before/after (decision 8) and *50 rps on staging* (decision 3) — are **deferred to launch**, so the phase shipped with **no system-level number** |
| **3 DONE** Pipeline + repo — insert ladder degrades only on missing column; one URL key, no re-stamp on commit, ~~all snapshot families committable~~ **8 of 10; `link_check` and `mailing_list` are registered
but refuse, each with its reason**; DB-sequence ids + run lock ~~in `agent_runs`~~ **in a new `agent_locks` table — see the Phase 3
departures section for why `agent_runs` cannot hold a lock**; bank cost before parse; ~~merges → review queue~~ **STRUCK — decision 9 (Shama, 2026-09-05): do not touch the merge logic at all**; discontinued needs page evidence; reject unsourced URLs; ordered pagination; service key required; branch cleanup **(done: 13 merged branches deleted, `opportunity-matching` archived)** +
~~merge `local-discovery-engine`~~ **NOT merged — it is behind main on every shared file and a
merge would revert the `wingman/` reorg; its plan doc was ported and the prototype is parked as
an M8+M9 item**; CI marquee-tag check; ~~move one-offs/eval out of root~~ **already done — `scripts/one-off/` and `eval/` exist and `server.py` is the only `.py` left at the root (verified 2026-09-05)**; `scrape_common.py` **(now `wingman/scrape_common.py`)**; tests for untested paid paths | Wk 3–5 | 6 d | **none taken — no prompt text moved and no paid call changed**; decision 4 answered yes | **ALL THREE MET**, each pinned by a named test, and the lock verified live against the real `agent_locks` table |
| **4 DONE** Shared state — handoff tokens survive a second worker; lock file batch-only; idempotent rollups via RPC; `jsonb_set` RPC for saves; leads + snapshots in tables; ~~scheduled worker (free agents first, paid behind toggle + dollar ceiling)~~ **DROPPED 2026-09-05 (decision 14 — Shama has dropped the idea; do not build it)**; ~~optional direct Postgres for hot queries~~ **SKIPPED 2026-09-05 (decision 15 — deferred to the launch gate)** | Wk 5–7 | 6 d | ~~M3 per scheduled paid run~~ **not needed — no scheduler.** M9 granted for the Gemini lock change (decision 13) | ~~two instances pass the 50 rps test~~ **deferred with every other throughput bar (decisions 3 and 8)**; second machine sees same lead queue |
| **5 DONE** Product accuracy — grade parser context; date validation; sort-on-refresh + calendar ids by label; synthesis failure keeps transcript; unreachable ≠ revoked; reset singletons on logout; one retry per action; client timeouts; drop icon fonts + dead ~~prompts/~~code (**no dead prompt text is left in the bundle — S1-1 removed it; verified 2026-09-05, so this is NOT an M8 item**); Vitest ~40 cases; a11y labels; split big screens | Wk 6–8 | 5 d | ~~none~~ **M9 granted for the retry caps + client timeouts (decision 13)** | frontend tests in CI; ~~golden-set score holds~~ **the paid golden run is DEFERRED (decision 16); the M10 harness is still kept in sync**; bundle −300 KB |
| 6 Operate — dashboards, dependency bumps, key rotation, runbook, Stripe webhook route, re-arm trial cron | Wk 8+ | ongoing | none | "is it up / fast / what did it cost" on one screen |

## Trade-offs worth weighing

- Prompts server-side: prompt edits ship with the backend. Skip it and anyone can run arbitrary prompts on your keys.
- Per-user budget: a heavy legitimate user is stopped once a day. Skip it and one account can spend ~$90 a pass.
- Async AI lane: under a burst some AI calls answer "try again shortly". Skip it and ~10 concurrent AI calls freeze the app.
- Identity cache: a lapse enforces up to 60 s late. Skip it and every signed-in request pays a DB read.
- Catalog cache headers: an activation shows up to 5 min late (server TTL already 5 min).
- Split catalog/vector caches + 24 h embedding backstop: browsing gets snappier and the ~20 MB
  vector pull drops from every 5 min to ~once/day; cost is that an *offline* re-embed on prod lags
  up to 24 h unless the instance is nudged (activations still refresh instantly via the console bust).
- Never merge `opportunity-matching`: the funnel stays unshipped unless rebuilt on the recall grid.
- Scheduled paid agents: M3 moves from a yes-per-run to a yes-per-schedule; keep behind a toggle + dollar ceiling.
- Stay on Render/Supabase: revisit only past ~500 rps; a move now is churn for no measured gain.

## Method

Read all of `app/`, `ops/admin.py`, the request-facing `ops/core.py`, all 69 root scripts, every schema
SQL, the whole Expo app, deploy/CI config. Ran pytest and tsc. Booted a copy of the service on :8765
with only Supabase creds + JWT secret (no AI/email keys → mock mode) and load-probed six scenarios at
concurrency 1/8/32 for 12 s each; stopped it afterwards. Read-only git analysis of all branches,
worktrees and stashes. Production was only pinged read-only (root + catalog headers).

Assumptions to confirm: ~~Render plan actually in use~~ — **RESOLVED 2026-09-05**: `plan: free`,
and that is the deliberate intended state until launch (decision 3). Supabase region vs Render;
~~Anthropic/Gemini org tiers.~~ **CLOSED (Shama, 2026-09-05):** `AI_MAX_CONCURRENCY` stays at
12 and this assumption is retired from the list. Not to be re-raised. ~~RLS state of `conversations`,
`agent_runs`, `deadline_check_log` (no schema file in the tree)~~ — **RESOLVED 2026-09-04**: all
three now have schema files, RLS is enabled, and it was confirmed against the live database
rather than assumed (all report `rls = true`).

**The load probe described above is no longer runnable as written** — two of its six scenarios
target routes Phase 1 deleted, and it has no `/api/match` scenario. See decision 8 before
reusing this method.
