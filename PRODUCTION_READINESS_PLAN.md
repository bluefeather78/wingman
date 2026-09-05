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
Phase 2 (capacity) is the next unstarted phase and is where this document picks up again.

The blow-by-blow — every finding, what was done, what was deliberately not done, and the
three places this plan's own text turned out to be wrong — is in
[SECURITY_HARDENING_PLAN.md](SECURITY_HARDENING_PLAN.md), sections 0 and 0b. Read that
before touching anything security-adjacent; this section is only the summary.

| | |
|---|---|
| Phase 0 | complete, tag `phase-S0` |
| Phase 1 | complete, 15 items (14 + the S0-9 leftover) |
| Findings closed | C1, C2, H1–H5, M1–M4, M6–M11, L1, L2, L3, L5, L6, L10 |
| Still open by design | **M5** (process-wide 5 s Gemini sleep) → Phase 2; argon2 parameters → Phase 2; L9 (lost updates on `users.data`) → Phase 4; L4 (Stripe webhook) → Phase 6; L8 (PyPDF2 → pypdf) → Phase 6 |
| Tests | 2349 passing (was 2080 at the end of Phase 0); 12 new test files |
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

### Still needs a human (none of it blocks Phase 2)

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
5. **Decisions 2 and 3 below are still open** — the real per-user daily allowance
   (`USER_DAILY_BUDGET_USD` is still the conservative $0.50 placeholder, not the measured
   5x-median this plan asks for) and the paid Render tier.

---

## Headline

| | |
|---|---|
| Critical | 2 — **both CLOSED** (Phase 0). Open, unmetered AI proxy (`app/routes/ai.py`) — live-verified 2026-09-03, see below; `numpy` missing from `requirements.txt` |
| High | 9 — **7 CLOSED** (Phases 0–1): `email_verified`, the prefix-match open redirect, the global login bucket, the per-user spend cap, the catch-all static route, and both pipeline items are done. **2 remain**, and neither is security: AI calls stalling the shared 40-thread pool is M5/Phase 2; `opportunity-matching` is Phase 3 (never merge it) |
| Capacity today | ~10–15 mixed rps on Render free (0.1 CPU, sleeps). Laptop measurement: catalog 70 rps ceiling, authed data 27–95 rps, ~150 ms Supabase gate read per signed-in request |
| Tests | backend suite green at **2349** (was 2080 after Phase 0, ~1900 at review time), `tsc --noEmit` clean, still zero frontend tests (Phase 5) |
| Branches | 34 local; 29 fully merged; merge only `local-discovery-engine`; never merge `opportunity-matching`; rescue `cleanup_subject_tags.py` from the fb6134 worktree |
| Spend by this review | $0 — no paid agent was run; the load probe ran with AI keys withheld |

## Decisions needed from Shama

1. ~~Move AI prompts server-side (M8)?~~ **ANSWERED yes; shipped 2026-09-04** as S1-1, its own
   dedicated M8 commit. The prompt text was moved verbatim and that was verified character by
   character against the originals.
2. **STILL OPEN — daily AI allowance per student.** `USER_DAILY_BUDGET_USD` shipped at a
   conservative $0.50 placeholder. Read the median off the console's Cost-per-user tab and set
   it to ~5x that, with the `BUDGET_EXEMPT_USERIDS` override.
3. **STILL OPEN — hosting.** `render.yaml` still says `plan: free`.
4. Retire `opportunity-matching` as a branch (archive tag, extract per decision)? Recommended yes.
5. ~~Keep logging chat turns + IP to `conversations`?~~ **ANSWERED (Shama, 2026-09-04): keep
   the turns, drop the IP.** Shipped as S1-9 — `client_ip` is no longer written and the column
   is dropped, RLS is on and confirmed live, and userids/emails no longer go to stdout.
6. Split the catalog cache and refresh embeddings on a **24 h** backstop instead of 5 min?
   **Decided yes (Shama, 2026-09-02).** See "Live finding" below.

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
- **Planned cleanup (Phase 2, greenlight-when-ready).** `/api/opportunities` (browsing) loads the
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
| **2 NEXT** Capacity — no Gemini sleep on web path; async AI lane (httpx, semaphore 30, timeouts, 503+Retry-After); 60 s identity cache; pooled HTTP; pre-serialized gzip+ETag catalog with background refresh + client cache; batched cost accounting; OWASP argon2; semaphore 4 on fresh deadline/checklist; `/healthz` + structured logs + alerts; k6 load test on staging; confirm provider tiers | Wk 2–4 | 7 d | M9 flag (sleep) | 50 rps × 10 min on staging, p95 < 1 s data routes, AI sheds not stalls |
| 3 Pipeline + repo — insert ladder degrades only on missing column; one URL key, no re-stamp on commit, all snapshot families committable; DB-sequence ids + run lock in `agent_runs`; bank cost before parse; merges → review queue; discontinued needs page evidence; reject unsourced URLs; ordered pagination; service key required; branch cleanup + merge `local-discovery-engine`; CI marquee-tag check; move one-offs/eval out of root; `scrape_common.py`; tests for untested paid paths | Wk 3–5 | 6 d | M8 if prompt text moves; decision 4 | two agents at once refuse to overlap; simulated insert timeout fails loudly; snapshot commit inserts 0 dupes |
| 4 Shared state — shared cache or signed handoff tokens; lock file batch-only; idempotent rollups via RPC; `jsonb_set` RPC for saves; leads + snapshots in tables; scheduled worker (free agents first, paid behind toggle + dollar ceiling); optional direct Postgres for hot queries | Wk 5–7 | 6 d | M3 per scheduled paid run | two instances pass the 50 rps test; second machine sees same lead queue |
| 5 Product accuracy — grade parser context; date validation; sort-on-refresh + calendar ids by label; synthesis failure keeps transcript; unreachable ≠ revoked; reset singletons on logout; one retry per action; client timeouts; drop icon fonts + dead prompts/code; Vitest ~40 cases; a11y labels; split big screens | Wk 6–8 | 5 d | none | frontend tests in CI; golden-set score holds; bundle −300 KB |
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

Assumptions to confirm: Render plan actually in use; Supabase region vs Render; Anthropic/Gemini org
tiers. ~~RLS state of `conversations`, `agent_runs`, `deadline_check_log` (no schema file in the
tree)~~ — **RESOLVED 2026-09-04**: all three now have schema files, RLS is enabled, and it was
confirmed against the live database rather than assumed (all report `rls = true`).
