# Production Readiness Review and Plan — 2026-09-02

Full review of `main` (commit 2718301), every unmerged branch, a measured load probe, and a
phased plan to a production-grade service at 50 requests/second across all endpoints.

**The full, PM-readable document with flowcharts is the published artifact:**
https://claude.ai/code/artifact/dfcad0e6-d2d8-4589-a344-d2b2cf8bb906

The five specialist reports it summarises, with file:line for every finding, are in
[docs/review-2026-09-02/](docs/review-2026-09-02/): `security_report.md`, `perf_report.md`,
`agents_report.md` (includes the node-by-node control flow of every agent), `frontend_report.md`,
`merge_report.md`, plus `load_results.json` and the `load_probe.py` that produced it.

## Headline

| | |
|---|---|
| Critical | 2 — open, unmetered AI proxy (`app/routes/ai.py`); `numpy` missing from `requirements.txt` (prod survives on Render's build cache) |
| High | 9 — Google link without `email_verified`; prefix-match open redirect leaks the login token; login limiter is one global bucket behind Render's proxy; no per-user spend cap; catch-all static route serves the repo; AI calls stall the shared 40-thread pool; `opportunity-matching` would silently land unapproved M8/M9 changes; insert ladder strips review data on any error; snapshot commit uses a weaker dedupe key and re-stamps dates |
| Capacity today | ~10–15 mixed rps on Render free (0.1 CPU, sleeps). Laptop measurement: catalog 70 rps ceiling, authed data 27–95 rps, ~150 ms Supabase gate read per signed-in request |
| Tests | backend suite green, `tsc --noEmit` clean, zero frontend tests |
| Branches | 34 local; 29 fully merged; merge only `local-discovery-engine`; never merge `opportunity-matching`; rescue `cleanup_subject_tags.py` from the fb6134 worktree |
| Spend by this review | $0 — no paid agent was run; the load probe ran with AI keys withheld |

## Decisions needed from Shama

1. Move AI prompts server-side (M8)? Recommended yes.
2. Daily AI allowance per student? Recommended ~5x measured median daily spend, with operator override.
3. Hosting: recommended stay on Render + Supabase, move to a paid tier, two instances after Phase 4.
4. Retire `opportunity-matching` as a branch (archive tag, extract per decision)? Recommended yes.
5. Keep logging chat turns + IP to `conversations`? Recommended stop, or hash IP + add RLS.

## Phase plan (one engineer, AI-assisted; ~31 engineer-days over ~8 weeks)

| Phase | When | Effort | Approvals | Exit test |
|---|---|---|---|---|
| 0 Stop the bleeding — numpy + exact pins; proxy requires subscribed caller on live path; Anthropic timeout + `max_uses`; per-user daily budget + forced-recheck cooldown + circuit breaker; static allow-list; `FORWARDED_ALLOW_IPS` + login key (ip,user); `email_verified` + exact redirect host; paid tier; delete tracked logs/dumps/stray Render CLI README+CHANGELOG; rotate the PAT in the git remote | Days 1–3 | 2 d | M9 (proxy) | signed-out proxy POST → 401; clean Render build passes; `/ops/admin_console.html` → 404 |
| 1 Security — prompts server-side by feature id; refresh-token rotation; calendar handoff nonce; `url_is_public()` + auth on submissions; body limits + security headers (CSP report-only) + Secure cookies; conditional promo PATCH; single login-failure message; ops token; `conversations` RLS or stop; promo table; argon2-wrap legacy rows | Wk 1–2 | 5 d | M8 | no High/Medium open; replayed refresh token revokes lineage |
| 2 Capacity — no Gemini sleep on web path; async AI lane (httpx, semaphore 30, timeouts, 503+Retry-After); 60 s identity cache; pooled HTTP; pre-serialized gzip+ETag catalog with background refresh + client cache; batched cost accounting; OWASP argon2; semaphore 4 on fresh deadline/checklist; `/healthz` + structured logs + alerts; k6 load test on staging; confirm provider tiers | Wk 2–4 | 7 d | M9 flag (sleep) | 50 rps × 10 min on staging, p95 < 1 s data routes, AI sheds not stalls |
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
tiers; RLS state of `conversations`, `agent_runs`, `deadline_check_log` (no schema file in the tree).
