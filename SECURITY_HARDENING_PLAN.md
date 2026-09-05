# Security Hardening Plan — Phases S0 and S1

Standalone, self-contained security workstream extracted from
[PRODUCTION_READINESS_PLAN.md](PRODUCTION_READINESS_PLAN.md) (2026-09-02 review).
Written 2026-09-04 to be picked up in a fresh session with no prior conversation context.

**Source of record for every finding id below:**
[docs/review-2026-09-02/security_report.md](docs/review-2026-09-02/security_report.md) — it
carries the exploit walkthrough, the code excerpt and the file:line for each. Frontend-side
detail is in `frontend_report.md`; the live red-team of the AI proxy is in
PRODUCTION_READINESS_PLAN.md § "Live verification (2026-09-03)".

Line numbers are from the working tree on 2026-09-02. **Re-grep before editing** — they will
have drifted.

---

## 0. Status (updated 2026-09-04)

**Phase S0 is COMPLETE and shipped**, plus S1-8 (pulled forward because hazard H3 requires it
to land before S0-7). One commit per item; the five M9 items were approved by Shama in chat
before any editing and each names M9 in its subject.

| | |
|---|---|
| Tag | **`phase-S0`** (annotated), pushed to `origin` |
| Commits | `efcb6f7..8acab94` on `codecleanup`, 10 commits, pushed |
| Diff | 27 files, +2006 / −62 |
| Tests | 6 new files, 82 new test functions; suite green at **2080 passing** (see the `EMAIL_POSTAL_ADDRESS` note at the end of this section) |

New test files, so the next session knows where the coverage already is:
`tests/unit/test_ai_request_caps.py` (S0-2/S0-3/S0-4), `test_spend_caps.py` (S0-5),
`test_ops_gate.py` (S1-8), `test_login_ratelimit.py` (S0-7),
`test_google_oauth_security.py` (S0-8/S0-9), `test_requirements_pins.py` (S0-11), plus
new cases in `test_subscription_gate.py` (S0-1).

| Item | State | Commit |
|---|---|---|
| S0-1 gate the live AI proxy `[C1]` `[M9]` | done | `MARQUEE M9 (S0-1)` |
| S0-2 throttle + size cap `[D1,D4,M4]` `[M9]` | done | `MARQUEE M9 (S0-2)` |
| S0-3 pin tool use `[D3]` `[M9]` | done, **minus the M8 half** | `MARQUEE M9 (S0-3)` |
| S0-4 timeout + `max_uses` `[C1.4,M4]` `[M9]` | done | `MARQUEE M9 (S0-4)` |
| S0-5 spend caps `[H4]` `[M9]` | done | `MARQUEE M9 (S0-5)` |
| S0-6 static allow-list `[H5]` | **already done** before this pass | `25de538` |
| S0-7 forwarded IPs + login key `[H3]` | done | `S0-7:` |
| S0-8 `email_verified` `[H1]` | done | `S0-8 + S0-9:` |
| S0-9 exact-origin redirects `[H2]` | done; fragment move closed later | `S0-8 + S0-9:`, `S0-9 (second half)` |
| S0-10 repo hygiene | **already done**; no PAT in the remote | — |
| S0-11 deploy prerequisites | done, **minus the free tier** | `S0-11:` |
| S1-8 ops token + Render refusal | done, ahead of S0-7 per H3 | `S1-8:` |

**Three things S0 deliberately did NOT close.** Two of them have since been closed (S1-1
and the `S0-9 (second half)` commit); only the free tier is still open.

1. ~~**Deleting `intakeExtractAndClassify`**~~ — **CLOSED by S1-1**, which deleted it along
   with the other two genuinely-dead prompts.
2. ~~**Moving the Google token into the URL fragment**~~ — **CLOSED**, commit
   `S0-9 (second half)`. The fragment is used for http(s) destinations (where the redirect
   hits our own origin and a query string is an access-log entry) and the query string is
   kept for custom schemes (`wingman://`, `exp://`), where the OS resolves the redirect and
   no HTTP server ever sees it — so the change is applied exactly where the leak exists,
   rather than gambling on fragment handling across a custom-scheme redirect. The same
   commit moved `/api/auth/google/session` from a GET with the token in the query string to
   a POST with it in the body.
3. **Moving off the Render free tier** (part of S0-11). A billing decision, not a code change;
   `render.yaml` still says `plan: free`.

**One thing S0-7 cannot finish from a laptop.** `--forwarded-allow-ips` is set in
`render.yaml` to loopback + the private ranges, deliberately **not** `*` — reading the
installed `uvicorn/middleware/proxy_headers.py` settles the plan's open question: with
`always_trust` uvicorn returns `x_forwarded_for_hosts[0]`, the **leftmost** entry, which is
whatever the client sent, because proxies append. `*` would have made `request.client.host`
attacker-controlled. The chosen list is fail-safe (worst case it changes nothing), but
**confirm it actually helped**: `app/main.py` logs the first request's resolved client IP and
raw `X-Forwarded-For` once at `[client-ip]`. Read it off the Render log after deploy — a
resolved address still in `10.x` means the trusted list does not cover Render's LB and the
limiters are still sharing one bucket.

**Open decisions still waiting on Shama** (§6 below): the real per-user daily allowance
(S0-5 shipped a conservative $0.50 placeholder behind `USER_DAILY_BUDGET_USD`, not the
measured 5x-median the plan asks for) and password reset. Answered since: q5 (Anthropic
`max_uses`) by S0-4 = 1; q1 (M8 for S1-1) approved and shipped; q3 (`conversations`) decided
as "keep writing it, drop `client_ip`" and shipped — see §0b.

**Unrelated config gap found while running the suite.** `EMAIL_POSTAL_ADDRESS` is unset
locally, so `test_lifecycle_email.py` and `test_deadline_alert_template.py` fail on the
CAN-SPAM placeholder reaching a rendered email. Not caused by this work (it fails on the
commit before it) and not a code bug — but confirm the value IS set in the Render dashboard,
or every lifecycle email ships with `[SET EMAIL_POSTAL_ADDRESS IN .env]` in its footer. With
it set, the suite is fully green.

### Environment variables S0 introduced

All have working defaults, so nothing breaks unset — **except `WINGMAN_OPS_TOKEN`, which
fails closed on purpose.** Every budget/limit value is disabled by setting it `<= 0`, which
is the operator's off switch.

| Variable | Default | What it does |
|---|---|---|
| `WINGMAN_OPS_TOKEN` | *(none — ops API routes 503)* | S1-8. `X-Ops-Token` header on every ops API route. `python server.py` mints and prints one when `.env` has none; put it in `.env` to keep it stable |
| `USER_DAILY_BUDGET_USD` | `0.50` | S0-5 layer 1. **A conservative placeholder, not the measured number** — see the open decisions above |
| `BUDGET_EXEMPT_USERIDS` | *(empty)* | S0-5. Comma-separated userids that bypass the per-user cap — the operator override |
| `GLOBAL_DAILY_BUDGET_USD` | `25.0` | S0-5 layer 3. Above this every paid branch degrades to cached/mock |
| `FORCED_RECHECK_WINDOW_SECONDS` / `_MAX_PER_WINDOW` | `3600` / `1` | S0-5 layer 2. The `refresh=1` cooldown, per (user, row) |
| `BUDGET_CACHE_TTL_SECONDS` | `60` | How long a spend total read from `user_costs` is trusted |
| `AI_MAX_BODY_BYTES` | `524288` | S0-2. Largest legitimate payload measured ~100 KB |
| `RESUME_MAX_BODY_BYTES` | `10485760` | S0-2. The resume upload parses wholly in memory |
| `AI_RATE_LIMIT_PER_USER` / `_PER_IP` / `_WINDOW_SECONDS` | `30` / `120` / `60` | S0-2. Per-IP is loose on purpose — a school NAT |
| `AI_UPSTREAM_TIMEOUT_SECONDS` | `60` | S0-4 |
| `ANTHROPIC_MAX_WEB_SEARCH_USES` | `1` | S0-4. Moot while S0-3's pin holds; the defence-in-depth layer |

`render.yaml` also changed: `startCommand` gained `--forwarded-allow-ips` (S0-7) — read the
note above on why it is **not** `*`.

---

## 0b. Phase S1 status (updated 2026-09-04)

**Phase S1 is COMPLETE.** All fourteen remaining items shipped, one commit per item, in the
cheapest-first order §0b previously recommended. S1-8 was already done in the S0 pass.

| | |
|---|---|
| Commits | `4c0b866..HEAD` on `codecleanup`, 15 commits (14 S1 items + the S0-9 leftover) |
| Tests | 12 new test files, ~247 new test functions; suite green at **2348 passing** |
| Migrations to run | `db/auth_schema.sql` (S1-2), `db/promo_codes_schema.sql` (S1-10), `db/conversations_schema.sql` + `db/agent_runs_schema.sql` + `db/deadline_check_log_schema.sql` (S1-9) |

| Item | State | Commit |
|---|---|---|
| S1-1 prompts server-side `[C1.2]` `[M8]` | done | `MARQUEE M8 (S1-1)` |
| S1-2 refresh rotation + reuse detection `[M2]` | done | `S1-2:` |
| S1-3 calendar handoff nonce `[M3]` | done | `S1-3 + S1-14:` |
| S1-4 `url_is_public()` + auth on submission `[M1,M10]` | done | `S1-4:` |
| S1-5 body limits, headers, `Secure` cookies `[M4,M11]` | done | `S1-5:` |
| S1-6 conditional promo PATCH `[M6]` | done | `S1-6:` |
| S1-7 one login-failure message `[M7]` | done | `S1-7:` |
| S1-8 ops token + Render refusal | done in S0 | `S1-8:` |
| S1-9 `conversations` RLS + no `client_ip` | done | `S1-9:` |
| S1-10 promo codes into a table `[L3]` | done | `S1-10:` |
| S1-11 wrap legacy password hashes `[L1]` | done | `S1-11:` |
| S1-12 `compare_digest` on non-ASCII `[L2]` | done | `S1-12:` |
| S1-13 stop relaying upstream error bodies `[L5]` | done | `S1-13:` |
| S1-14 calendar sync input handling `[L6]` | done | `S1-3 + S1-14:` |
| S1-15 narrow the row reads `[L10]` | done | `S1-15:` |

**The M8 approval and how it was honoured.** Shama approved the whole plan in chat before
any editing. S1-1 is its own dedicated commit naming M8. The prompt text was moved
VERBATIM, and that was verified rather than asserted: a script extracted each original
template literal from git, substituted its interpolations, and compared character by
character against what `app/services/prompts.py` builds. All ten system prompts match byte
for byte, as do both conditional branches and every user-content template.

### Two decisions taken during the pass, recorded because they differ from the text above

1. **`conversations`: Shama chose "keep writing it, drop `client_ip` outright"** (§6 q3),
   rather than hashing the IP. `db/conversations_schema.sql` drops the column.
2. **S1-7's preferred fix could not ship as written.** The plan asks register to answer the
   success shape for a taken email and mail the existing account. `/api/register` hands
   back tokens inline (the client goes straight to `showApp()`), so there is no 200 to
   return that is not either a lie the client chokes on or somebody else's session. The
   plan's own stated fallback shipped instead: a per-address sign-up rate limit.

### Three corrections to the plan's own text, found while implementing

1. **`scoreOpportunitiesForTag` is NOT dead.** S1-1's inventory lists it as having no
   caller; `frontend/app/(app)/finder.tsx` calls it from a live effect whenever a profile
   tag is selected. It was ported as `tag_suggestions`, not deleted. The other three dead
   prompts were confirmed dead and deleted.
2. **S1-6's `not.cs.{CODE}` filter alone is not enough.** It closes the compound-one-code
   exploit but leaves the two-different-codes case: two grant codes redeemed concurrently
   both pass their own filter, both apply, and last-write-wins drops one from the array —
   so the dropped one can be redeemed again. The shipped filter is a full compare-and-swap
   on the array. It also has to name `is.null` explicitly, because every array operator
   against NULL evaluates to NULL and a row predating the column's DEFAULT could otherwise
   never redeem anything.
3. **S1-11's "consider a server pepper" was deliberately not done.** Adding one changes the
   argon2 input, so every already-wrapped row stops verifying without a second migration and
   a dual-verify path. It belongs with the Phase 2 argon2 parameter work, done once.

### What still needs a human

- ~~**Run [db/RUN_ME_S1.sql](db/RUN_ME_S1.sql)**~~ — **DONE 2026-09-04, and VERIFIED.** The
  verification query returned `rls = true` for `conversations`, `agent_runs`,
  `deadline_check_log`, `promo_codes` and `users`. That confirmation is what actually closes
  S1-9: the schema files could only make the ALTER available to run, and could not
  retroactively secure tables created by hand from a code comment. The columns and tables
  exist ahead of the deploy, which is the right order — the code that uses them is still on
  `codecleanup`, so until it ships they sit unused. Original instructions kept below.
- ~~Run it~~ in the Supabase SQL editor — one paste
  covering all five files, idempotent, with a verification query at the end that should
  return `rls = true` for all five tables. (The individual files stay the source of record
  and the place to read why each statement exists; that one is a convenience copy.)
  Everything degrades rather than breaking until it runs — rotation is simply off, promo
  codes fall back to the built-in table — but nothing is actually fixed until it does.
- ~~**Confirm RLS**~~ — **DONE 2026-09-04**, see above. All five tables report
  `rls = true`. S1-9 is closed in fact, not just in a file.

- **Watch the first sign-ins after the deploy.** S1-2's rotation goes live the moment the
  code ships against the migrated database. Every refresh token minted before it carries no
  `jti`, and the route adopts those once and rotates them in rather than treating them as
  reuse — that path is tested, but it is the one change here that could sign the whole user
  base out if it is wrong, and the symptom would be a spike of 401s on `/api/auth/refresh`.
- **Run `cd frontend && npx tsc --noEmit`.** There was no node toolchain in the environment
  this work was done in, so the frontend changes in S1-1, S1-2 and S1-3 are unverified by
  the compiler. They are mechanical (signature changes and call-site updates), but that is
  exactly the class of change tsc catches.
- **Read the `[client-ip]` line off the Render log** after deploy — still open from S0-7.
- **Turn on `CSP_ENFORCE`** only after reading the report-only violations against a real
  `expo export` bundle (S1-5).
- Still open from S0: the Render free tier, `EMAIL_POSTAL_ADDRESS` in the dashboard, and the
  real per-user daily allowance (§6 q2 — `USER_DAILY_BUDGET_USD` is still the conservative
  $0.50 placeholder).

### Deliberately NOT done, with reasons

**Nothing in the plan is left undone.** The Google-token fragment move — deferred by the S0
pass and again at the end of the S1 pass, both times on "untestable without a device" — was
closed on Shama's instruction in the `S0-9 (second half)` commit, which records how the risk
was contained rather than accepted. What remains below is only what the plan itself scopes
out of S0/S1.

- **M5** (the process-wide 5s Gemini sleep) and the argon2 parameters stay in Phase 2, and
  L9 in Phase 4, exactly as §1 says. Finishing S1 means no High or Medium open EXCEPT M5 —
  that is now true.

---

## 1. Scope

This document covers **Phase 0 and Phase 1** of the production readiness plan, i.e. the
security work. Two adjustments were made when extracting it:

**Pulled IN from Phase 0 even though they are not security** (they gate everything else
shipping, so they belong in the same batch):
- `numpy` missing from `requirements.txt` — a clean Render build does not boot.
- Exact dependency pins (every pin is `>=` today).
- Move off the Render free tier.

**Security findings deliberately NOT in this plan** (they live in later phases; listed here
so they are not lost):

| Finding | Where it lives | Why it is not here |
|---|---|---|
| M5 — process-wide 5 s Gemini sleep (a DoS lever) | Phase 2 | Its fix is entangled with the async AI lane; C1's auth gate removes the anonymous half of the exploit |
| argon2 params (64 MiB / 1-3 s per login → OOM at ~8 concurrent) | Phase 2 | Capacity-driven; L1's legacy-hash migration IS here |
| L9 — lost updates on `users.data` | Phase 4 | Needs the `jsonb_set` RPC that phase introduces |
| L6 — calendar `googleEventId` quoting, malformed `dateISO` → 500 | S1-14 (kept here, it is 20 minutes) | — |
| L4 — Stripe webhook route | Phase 6 | Dormant until Stripe is configured |
| L8 — PyPDF2 EOL → pypdf | Phase 6 | Exact pins ARE here (S0-11) |

**Effort:** S0 ≈ 2 days, S1 ≈ 5 days, one engineer AI-assisted.

---

## 2. Approval gates — read before touching anything

Per [MARQUEE_DECISIONS.md](MARQUEE_DECISIONS.md) and CLAUDE.md:

- **M9 — any code path that makes a paid API call.** Covers `use_web_search` / `max_searches`
  / `max_uses` toggles, model pins, per-row model calls, provider swaps. **S0-1, S0-2, S0-3,
  S0-4, S0-5 all touch M9 seams.** `gemini_common.call_gemini`'s own docstring says so.
- **M8 — any prompt sent to a model** (adding, changing or removing prompt text).
  **S1-1 is M8** — it physically moves every system prompt from the bundle into `app/`.

Rules that follow:
1. Get an explicit "yes" from Shama in chat **before** editing, naming the entry.
2. A marquee change is **always its own dedicated commit** naming the entry. Never bundled.
3. Group the M9 work so it is one approval conversation, not five — but still separate commits.

**Nothing in S0-6 through S0-11, or S1-2 through S1-15, is marquee.** Those can proceed on
normal judgement.

---

## 3. Sequencing hazards — the three ways this goes wrong

Read these before picking an order. Each is a case where fixing one finding *creates* another.

### H3 + M8: the `FORWARDED_ALLOW_IPS` trap
S0-7 fixes the login limiter by trusting `X-Forwarded-For`. But the ops console's **only**
protection is `request.client.host in ("127.0.0.1", "::1", ...)` (`ops/admin.py:18-27`). If
ops were ever mounted on Render with `FORWARDED_ALLOW_IPS=*`, an attacker sending
`X-Forwarded-For: 127.0.0.1` gets the entire console: subprocess launches that spend money,
the roster of minors' names and emails, catalog activation, test email sends to arbitrary
addresses.

**Therefore S0-7 and S1-8 must ship together, or S1-8 first.** Do not set
`FORWARDED_ALLOW_IPS` until the ops mount refuses to come up when `RENDER` is set *and* an
`WINGMAN_OPS_TOKEN` header check is in place.

### C1 + mock mode: do not lock out offline dev
CLAUDE.md's standing constraint is that the app stays fully click-through-able with no API
keys. The auth gate in S0-1 must key on **whether a live key is configured**, not on the route:

```
if not GEMINI_API_KEY:  -> mock branch, stays reachable signed-out
else:                   -> live branch, requires authenticated + subscribed caller
```

Gating the whole route breaks offline development and the signed-out demo path.

### S1-1 + the cost classifier
`classify_feature()` / `_FEATURE_SIGNATURES` (`app/core.py:371-401`) identifies which feature
a call was by **substring-matching the client's prompt**. When S1-1 moves prompts server-side,
the feature id becomes explicit and that whole substring table should be retired in the same
commit — but note `test_classify_feature.py` compares the source list against its case list
**in order**, so it must be updated together or the suite fails. Also note CLAUDE.md's warning
that `profile_extract`'s signature is load-bearing: rewording it silently moves spend to
`other`.

---

## 4. Phase S0 — Stop the bleeding (~2 days)

> **COMPLETE 2026-09-04, tag `phase-S0`.** Kept in full below as the record of what each item
> was and why — the per-item status, and the three things deliberately left open, are in § 0.

Ordered by dependency. S0-1 through S0-5 are one M9 approval conversation.

---

### S0-1 — Gate the live AI proxy behind auth + subscription  `[C1]` `[M9]`

**Files:** `app/routes/ai.py:148-175`; `app/deps.py:106-108`;
`app/auth/dependencies.py:229-238`

**Mechanism.** `handle_messages` uses `get_optional_user`, which never 401s. `userid` is then
`None`, and `subscription_block_reason(None)` returns `None` because of an early
`if not userid: return None`. So a request with **no `Authorization` header at all** falls
straight through to `_proxy_to_gemini` / `_proxy_to_anthropic`.

**Verified live 2026-09-03** (probe D5): anonymous POST → `200`, real billed call. Spend
attributed to nobody.

**Fix.**
- On the live branch (key configured), depend on `require_subscription` — `401` for no/invalid
  token, `402` for a lapsed account (body = `subscription_block_reason()`'s message).
- Leave the mock branch (`if not GEMINI_API_KEY:` / `if not ANTHROPIC_API_KEY:`) reachable
  signed-out.
- Apply to **both** `/api/messages` and `/api/messages-claude` — one shared handler shape.

**Verify.** Signed-out POST to a key-configured instance → `401`. Lapsed account → `402`.
Key-absent instance, signed out → `200` mock. Add all three to
`tests/unit/test_subscription_gate.py`, which already asserts the gate route-by-route
(including the routes that must stay UNGATED — do not break those).

---

### S0-2 — Throttle and size-cap both proxies  `[D1, D4, M4]` `[M9]`

**Files:** `app/routes/ai.py`; `app/auth/ratelimit.py`; `app/deps.py:58-61` (`raw_body`)

**Mechanism.** No limiter on either route; no body-size limit anywhere in the app. `raw_body`
reads the entire request into memory unbounded.

**Verified live 2026-09-03:** 12 rapid POSTs → `200 x12`, **zero** `429`. A 41,040-byte body →
`200` with **9,703 input tokens billed**.

**Fix.**
- A limiter keyed **per-IP and per-user** on both proxy routes → `429` with `Retry-After`.
- A max body / `userContent` length → `413`, evaluated **before** the upstream call. Set the
  JSON cap above the largest legitimate payload (CLAUDE.md cites a 37 KB `hs-tracker-data` as
  ordinary; 1 MB is a safe ceiling). The resume route needs its own higher cap (10 MB).

**Note.** The body cap originally sat in Phase 2. It is pulled forward because D4 is a direct
billing lever with no auth in front of it.

**Verify.** N+1 rapid POSTs → the last is `429` with `Retry-After`. An over-limit body →
`413`, and provider usage for that request is zero.

---

### S0-3 — Pin tool use server-side  `[D3]` `[M9]`

**Files:** `app/routes/ai.py:72-82` (Gemini), `:99-110` (Anthropic)

**Mechanism.** `use_web_search = bool(payload.get("useWebSearch"))` — the **client** decides
whether the server performs paid web searches. Verified live: `useWebSearch: true` on
`/api/messages-claude` produced `web_search_requests=1` and +2,240 billed input tokens.

**Finding that simplifies this (verified 2026-09-04).** Grep the whole frontend for a `true`
in the `useWebSearch` position: the **only** hit is `src/lib/tracker.ts:326`
(`intakeExtractAndClassify`), and that function has **zero callers anywhere in the repo** —
`frontend_report.md` §14 lists it under dead code. Every live feature on both routes passes
`false`.

**So: hard-pin `use_web_search=False` on both routes.** No feature-gating is needed today. Do
not build the feature-derived variant speculatively; when a feature genuinely needs search,
derive it server-side from the S1-1 feature id, never from a client flag.

**Also delete** `intakeExtractAndClassify` from `src/lib/tracker.ts` in the same change — while
it sits in the bundle it advertises the exploit shape. (Deleting prompt text is **M8**; fold it
into the S1-1 commit if you prefer to keep S0 free of M8, or take a separate approval.)

**Verify.** A `useWebSearch: true` body on either route performs **0** web searches
(`usage.server_tool_use` / `groundingMetadata.webSearchQueries` empty).

---

### S0-4 — Anthropic timeout and `max_uses`  `[C1.4, M4]` `[M9]`

**Files:** `app/routes/ai.py:99-110` (tool block), `:171` (`urlopen`)

**Mechanism.** Two separate omissions on the Anthropic path:
- The `web_search_20250305` tool is attached with **no `max_uses`** — contrast
  `agents/check_deadlines.py:256-260`, which caps it. Anthropic enforces `max_uses` server-side, so
  this is a real ceiling, unlike Gemini's prompt-level `max_searches`.
- `urllib.request.urlopen(req)` has **no `timeout`**. A hung socket permanently loses one of
  the 40 anyio threadpool slots — capacity that never returns until restart.

**Fix.** Add `max_uses` to the tool block (moot once S0-3 pins search off, but it is the
defence-in-depth layer — keep both). Add an explicit `timeout=` to the `urlopen`. While there,
confirm the Gemini path's timeout is explicit rather than inherited from `gemini_common`'s
120 s default.

---

### S0-5 — Per-user spend cap, forced-recheck cooldown, circuit breaker  `[H4]` `[M9]`

**Files:** `app/routes/opportunities.py:91-113` (deadline `refresh=1`), `:206-234`
(action-items), `app/routes/matching.py:281-349` (`/api/match`), both AI proxies;
`app/core.py:195-277`, `:431-533` (`record_user_cost` / `record_interactive_cost`)

**Mechanism.** Nothing anywhere reads spend back to refuse a call. The rollups only *record*.

**Exploit.** One 7-day trial account (which costs $0) loops
`GET /api/opportunities/<id>/deadline?refresh=1` across the catalog. `refresh=1` bypasses the
7-day cache unconditionally, and each verified check measures ≈ **$0.07**. 1,300 rows ≈ **$90
per pass, repeatable**. `/api/match` is a few cents per call, also unbounded.

**Fix — three independent layers, all needed:**
1. **Per-user daily budget**, checked before each paid branch, backed by the existing
   `user_costs` rollup (it already computes the number — read it back). Needs a UI message and
   an operator override.
2. **Per-user per-row cooldown on `refresh=1`** (e.g. one forced re-check per opportunity per
   hour). The cache bypass is the amplifier; the budget alone still allows a fast burn.
3. **Global daily circuit breaker** on total spend that flips the paid branches to
   cached/mock. Turns a billing incident into a degraded app, which is the correct failure
   direction.

**Decision needed from Shama:** the ceiling. The plan recommends ~5x the measured median daily
per-user spend (read it off the Cost per user tab), with an operator override.

**Note.** `app/services/action_items.py` has the same shape — user-submitted rows are never
stamped with `action_items_checked_at`, so **every** call on such a row pays.

---

### S0-6 — Replace the static deny-list with an allow-list  `[H5]`

**Files:** `app/main.py:128-129` (`_DENY_EXT` / `_DENY_NAMES`), `:175-190` (`_resolve_static`),
`:208-230` (`serve_static`, mounted on `/{full_path:path}`)

**Mechanism.** The catch-all serves **any** file under `REPO_ROOT` that is not explicitly
denied. Traversal is correctly blocked (verified in the report's "checked — no finding"
section) — the problem is the policy, not the path handling.

**Currently servable in production:** `ops/admin_console.html` (the full operator console UI,
naming every `/api/agents/*` endpoint), `ops/logic_map.html`, `opportunities.json` **and
`Opportunities.xlsx`** (the entire 1,330-row catalog as a bulk download — the product's core
asset), `review_check_dry_run_*.json`, `scrape_review_*.json`, `eval/golden_profiles.json`,
`eval/golden_scorecard.html`, `frontend/package-lock.json`, `render.yaml`, `pyproject.toml`,
and after `npm ci`, every non-dot file under `frontend/node_modules/**` (an exact dependency
inventory). Locally, `python server.py` binds `0.0.0.0`, so `discovered_leads.jsonl` and every
`*_dry_run_*.json` are served to the LAN.

Nothing exposes credentials or student PII **today** — that is luck. Only `.env` and
`agent_settings.json` are denied by name; a `users_db.json` at the root would be served.

**Fix.** Allow exactly: `public/terms.html`, `public/privacy.html`, `public/about.html`, `public/walkthrough.html`,
`public/styles.css`, `public/favicon.svg`, plus the `frontend/dist` bundle. Everything else 404s.

**Preserve** the documented resolution order in `serve_static()` — dist file → exported route
html → repo-root page → dist index.html fallback. The repo-root step sits **before** the
fallback deliberately so `/terms.html` can never be shadowed by the app shell.

**Verify.** `/ops/admin_console.html` → 404. `/opportunities.json` → 404. `/terms.html` → 200.
Add a test pinning the allow-list.

---

### S0-7 — Forwarded-IP handling and a per-(ip,user) login key  `[H3]`

**Files:** `app/auth/ratelimit.py:302-350`, `app/deps.py:79-80` (`request.client.host`),
`app/routes/account.py:88`, `:164`, `render.yaml:18` (start command)

**READ § 3 FIRST — this must ship with or after S1-8.**

**Mechanism.** uvicorn 0.52 defaults to `proxy_headers=True` but `forwarded_allow_ips=None` →
effectively `127.0.0.1`. Render's load balancer connects from the private network, not
loopback, so `X-Forwarded-For` is **ignored** and `request.client.host` is the proxy's address
for every visitor on earth.

**Consequence as deployed.** `login_limiter = RateLimiter(10, 5 * 60)` is **one bucket for the
entire user base**. Ten `POST /api/login` bodies lock every student out of sign-in for five
minutes, for free, repeatably. Ten registrations per hour is the site-wide signup capacity.

**Fix.**
- Add `--forwarded-allow-ips` to the Render start command (Render terminates TLS and sets
  `X-Forwarded-For`). **Verify empirically** by logging `client_ip()` once from a real request
  before relying on it — and confirm Render *overwrites* rather than appends a client-supplied
  header, or `*` is itself a bypass.
- Key the login limiter on `(client_ip, userid)` so one IP cannot lock out other users.
- The limiter is per-process; note that in the code for the eventual multi-worker move
  (Phase 4). The file already documents this.

---

### S0-8 — Require `email_verified` on Google account linking  `[H1]`

**Files:** `app/routes/google_oauth.py:166-171` (profile fields read), `:174-182` (the link)

**Mechanism.** When no account matches the `google_id`, the code looks up by email and links:

```python
by_email = get_user_by_email(email)
if by_email:
    # Google has verified this address, so link it to the existing account.
    record = get_user(by_email["userid"])
    _users_request("PATCH", query_patch, data={"google_id": google_id})
```

The comment asserts verification. **`profile.get("email_verified")` is never read** — only
`sub`, `email`, `given_name`, `family_name`.

**Exploit.** Google issues `email_verified: false` for addresses that were never confirmed
(non-Gmail addresses added to a Google account; some Workspace configurations). An attacker
creates a Google account carrying the victim's email unverified, signs in with Google, and
their `google_id` is linked to the victim's password account — returning a full session for
it. The victim's profile, tracker and calendar are now the attacker's, **and the victim's
password still works**, so nothing looks wrong from either side.

**Fix.** Refuse the link and the pending-signup path unless `profile.get("email_verified") is
True` — `json_error(400, ...)` rather than falling through. Optionally require the user to be
already signed in (or re-enter their password) before attaching a Google identity to an
existing account.

---

### S0-9 — Exact-origin redirect matching  `[H2]`

**Files:** `app/routes/google_oauth.py:86-92` (`_is_allowed_app_redirect`), `:199-204` (the
token redirect), `:319-322` / `:404-407` (the calendar variant)

**Mechanism.**

```python
def _is_allowed_app_redirect(uri: str) -> bool:
    return bool(uri) and any(uri.startswith(prefix) for prefix in _ALLOWED_APP_REDIRECTS)
```

A **string prefix match**. With the configured value `https://highschoolwingman.com`, both
`https://highschoolwingman.com.evil.tld/` and `https://highschoolwingman.com@evil.tld/` pass.
The defaults are affected too: `http://localhost:8081` matches
`http://localhost:8081.evil.tld`, and `exp://` accepts any host.

**Exploit.** Mail a student
`https://highschoolwingman.com/api/auth/google/start?app_redirect=https://highschoolwingman.com.evil.tld/`.
They see the **real** Google consent screen, sign in, and are 302'd to `evil.tld` carrying
`?google_token=…`. The attacker calls `GET /api/auth/google/session?token=…` within 5 minutes
and receives the student's access **and refresh** tokens.

**Fix.**
- Parse with `urllib.parse.urlsplit` and compare `(scheme, host, port)` **exactly** against the
  allowlist. Reject any value containing `@`. For custom schemes (`exp://`) compare the scheme
  only when the value has no network location an attacker could redirect.
- Better, and cheap: move the token from the query string into the **URL fragment**
  (`#google_token=`) so it never reaches a server log or a `Referer` header. Keep the exact
  check regardless.
- Apply to the calendar `app_redirect` too — it only leaks `calendar_connected=1`, but it is
  the same function.

**Cost.** Every dev port and the production origin must now be listed explicitly in
`GOOGLE_APP_REDIRECTS`. One line in the Render dashboard.

---

### S0-10 — Repo hygiene: tracked artefacts and the embedded PAT

- **Delete the tracked logs/dumps** that S0-6 stops serving: `server_debug.txt`,
  `server_output.txt`, `server_full_output.log`, `server_stderr.log`,
  `find_contact_emails_full_run.log`, `refresh_run.log`, plus the stray Render CLI
  `README`/`CHANGELOG`. The report confirms the four `server_*` files contain only a startup
  banner and `find_contact_emails_full_run.log` holds **program** contact addresses, not user
  ones — so this is hygiene, not an incident. `.gitignore` already covers the patterns; they
  predate it. Remember `.gitignore` only prevents *future* tracking — `git rm --cached` is
  required.
- **Rotate the GitHub PAT embedded in the git remote URL** (`git remote -v`). Local config, not
  in the tree.
- Check `git ls-files public/walkthrough.html` still prints it — CLAUDE.md notes production breaks
  silently if that file ever becomes untracked.

---

### S0-11 — Deploy prerequisites (not security, but nothing ships without them)

- **`numpy>=1.26` into `requirements.txt`.** Imported at module top in
  `app/services/matching.py:34` and `app/services/recall_query.py:259`; `app/main.py:103`
  mounts the matching router unconditionally. None of the six current deps pull it
  transitively. Production survives only on Render's build cache — the next cache miss is a
  `ModuleNotFoundError` at import and a hard outage. Check the Render build log to confirm.
- **Exact pins.** Every requirement is `>=`, so a deploy picks up whatever is newest that day.
- **Move off the free tier** (0.1 CPU, 512 MB, spins down after 15 idle minutes). Required
  before S0-5's budget accounting and S1-11's argon2 work behave sanely.

---

### S0 exit test

1. Signed-out POST to `/api/messages` on a key-configured instance → **401**.
2. Same instance with no key → **200** mock (offline dev still works).
3. `useWebSearch: true` on `/api/messages-claude` → **0** web searches performed.
4. Over-limit body → **413**, zero provider usage recorded.
5. N+1 rapid proxy POSTs → **429** with `Retry-After`.
6. `/ops/admin_console.html` → **404**. `/opportunities.json` → **404**. `/terms.html` → **200**.
7. A clean Render build (cache cleared) boots.
8. `pytest` green, `cd frontend && npx tsc --noEmit` clean.

---

## 5. Phase S1 — Security hardening (~5 days)

> **COMPLETE as of 2026-09-04.** Every item below shipped; § 0b carries the per-item commit
> table, the three corrections to this section's own text that implementing it turned up,
> and what still needs a human. The text below is kept UNCHANGED as the record of what each
> finding was and why — the commits reference it.

---

### S1-1 — Move every system prompt server-side, keyed by feature id  `[C1.2]` `[M8]`

**The largest item in this plan. Its own dedicated commit, M8 approval first.**

**Files:** all of `frontend/src/lib/{profile,profileChat,profileTags,ranking,tracker}.ts` and
`frontend/app/(app)/finder.tsx:164`; server side `app/routes/ai.py:72-79`, `:99-110`;
`app/core.py:371-401` (`_FEATURE_SIGNATURES`); `tests/unit/test_classify_feature.py`

**Mechanism.** Every system prompt is a string literal in `src/lib` and ships verbatim in the
web bundle (the report verified this by grepping four distinctive phrases out of
`dist/_expo/static/js/web/entry-*.js`). The server is a dumb pipe: it forwards `system`,
`useWebSearch` and `maxTokens` from the client. So the client-visible contract is *"send any
prompt, any input, search on, 8k output"*, and every product guardrail authored in a prompt —
the profile chat rules, "never invent a date", the eligibility guard — is bypassable by any
account holder.

**The prompt inventory** (from `frontend_report.md` §1.1; `dead` = no caller):

| Function | File | Route | Live? |
|---|---|---|---|
| `synthesizeProfile` / `repairProfileText` | `profile.ts:33` | Claude | yes |
| `assessProfileReadiness` | `profile.ts:101` | Gemini | **dead** |
| `starterQuestionPoolFromAI` | `profileChat.ts:121` | Claude | yes |
| `profileChatStarterQuestionsFromAI` | `profileChat.ts:143` | Claude | yes |
| `profileChatNextQuestion` | `profileChat.ts:161` | Claude | yes |
| `enrichRequest` | `profileTags.ts:96` | Gemini | yes |
| `extractTagsAndBasics` | `profileTags.ts:203` | Gemini | yes |
| `inferSubjects` | `ranking.ts:112` | Gemini | yes |
| `rankCandidates` | `ranking.ts:145` | Gemini | yes |
| `extractProfileBasics` | `ranking.ts:173` | Gemini | **dead** |
| `extractTrackerInfo` | `tracker.ts:274` | Gemini | yes |
| `intakeExtractAndClassify` | `tracker.ts:309-321` | Gemini, `useWebSearch: true` | **dead** |
| `scoreOpportunitiesForTag` | `finder.tsx:164-179` | Gemini | **dead** |

**Fix.**
- Client posts `{feature: 'rank', inputs: {...}}`. Server owns the prompt text, the tool
  config and the token budget, and **refuses an unknown feature**.
- **Delete the four dead prompts** rather than porting them.
- Retire `_FEATURE_SIGNATURES` in the same commit — the feature id makes classification exact
  instead of a substring guess, which also closes the "trivially gamed classifier" note in C1.
  `test_classify_feature.py` compares source list against case list **in order**; update both.
- `app/routes/matching.py:99` is already the target shape (server-owned prompt,
  `use_web_search=False`, server-owned `max_tokens`) — use it as the pattern.

**Cost, stated honestly.** `src/lib/*` is currently pure and dependency-injected precisely so
prompts are testable without a server; that property is lost for the prompt *text* (the
model-call plumbing can stay injected). And prompt edits start shipping with the backend rather
than the bundle.

---

### S1-2 — Refresh-token rotation with reuse detection  `[M2]`

**Files:** `app/auth/tokens.py:407-431`, `app/routes/auth.py:26-53`,
`frontend/src/api/httpClient.ts:375-377` (`logout`)

**Mechanism.** A 30-day refresh token, no rotation. `/api/auth/refresh` checks `ver` against
`users.token_version`, returns a **new** pair — and the presented token stays valid until its
own `exp`. The client's `logout()` only calls `forgetSession()` locally; there is no server
call. `logout-all` exists but is opt-in.

**Consequence.** A refresh token copied from `localStorage` on a shared school computer, from
a proxy log, or from a compromised device keeps minting access tokens **for 30 days, including
after the student presses "log out"**. Nothing signals that two parties are refreshing the same
lineage.

**Fix.** Give each refresh token a random `jti`; store the current one per user (one column,
`users.refresh_jti`); accept only that value. Presentation of a **superseded** `jti` is
evidence of theft → bump `token_version`, killing the lineage. Make the client's `logout()`
call a revoke endpoint.

**Cost.** One DB write per refresh (~every 45 min per active user) and one column migration.
Storing a small **set** rather than a single value lets two devices hold separate lineages.

**Note.** New columns follow this repo's convention: a `db/*.sql` file with `create table if
not exists` **plus an ALTER block**, run by hand in the Supabase SQL editor, and the code must
degrade rather than break when the migration has not been run.

---

### S1-3 — Calendar handoff nonce instead of a JWT in the URL  `[M3]`

**Files:** `frontend/src/api/httpClient.ts:659-664`, `app/routes/google_oauth.py:296-303`

**Mechanism.** The client builds `/api/auth/google/calendar/start?token=<access JWT>`. The
full 45-minute bearer lands in Render's access logs, the browser history, and any school or
corporate proxy log.

**Fix.** Mint a single-use, 60-second handoff nonce server-side
(`POST /api/auth/google/calendar/handoff` with the bearer in the **header** → `{nonce}`) and
put the nonce in the URL. The sign-in flow already does exactly this with `_mint_google_token`
(`app/services/google_oauth.py:33-47`) — copy that pattern. Costs one extra round trip.

---

### S1-4 — `url_is_public()` and auth on opportunity submission  `[M1, M10]`

**Files:** `app/routes/resume.py:94-96` (`optional_subscribed_user`),
`app/services/resume.py:169-258`, `:261-284`; `wingman/sitemap_common.py:92-106`, `:133-139`;
`page_text._fetch_urllib`; `url_repair._fetch`; `mailing_list_common.fetch_page`;
`app/routes/opportunities.py:77`

**Two findings, one fix, because they chain.**

**M10 — unauthenticated catalog write.** Anyone with no token can insert a row with
attacker-controlled `name`, `url`, `summary`, `important_dates`, `category`. Each call also
reads the **whole catalog** for dedupe (~1,400 rows across two pages) — a cheap amplification
against a free-tier instance. A script fills the review queue with thousands of rows, burying
real submissions, and the stored `name`/`summary` render in the admin console.

**M1 — blind SSRF, reachable through it.** `get_opportunity_for_deadline_check` has no
`is_active` filter (by design, documented), so any subscriber can trigger a check on an
attacker-submitted row. That runs `sitemap_common.default_fetch`, a plain `urlopen` against
`origin + "/robots.txt"` and the probed sitemap paths, with **no private-IP filter and
redirects followed**. Submit `{"url": "http://10.0.0.5:8080/"}` unauthenticated, then call
`GET /api/opportunities/<id>/deadline?refresh=1` from a trial account, and the server probes
inside Render's network. Responses are not echoed, but timing and the resulting status leak
reachability, and a redirect on the attacker's host steers follow-up GETs.

Worse, the operator's **free** agents (`agents/check_links.py`, `wingman/url_repair.py`,
`agents/find_mailing_lists.py`) later fetch those URLs **from the operator's laptop** — an SSRF
against your own LAN.

**Fix.**
- `require_subscription` on the submission route (the client only calls it from the authed
  Quest Log anyway), a per-user daily submission cap, and length limits on the text fields.
- One shared `url_is_public(url)` helper: resolve the host; reject loopback, RFC1918,
  link-local, ULA, `0.0.0.0/8` and cloud metadata ranges; **re-check after every redirect**
  via a custom opener. Apply at submission time **and** at every fetch site listed above.

**Cost.** Legitimate programs never live on private addresses, so false positives are nil; one
DNS lookup per fetch. The route's "soft auth for provenance" rationale is lost — acceptable,
since a signed-out user cannot use the Quest Log either.

---

### S1-5 — Body limits, security headers, `Secure` cookies  `[M4, M11]`

**Files:** `app/main.py:37-47` (CORS), `:64-87` (the only middleware, cache-control);
`app/deps.py:28-36`, `:58-61`; `app/core.py:1078-1118` (`context`), `:845-854` (`data` value);
`app/routes/resume.py:36`; `app/routes/google_oauth.py:124-125`, `:336-337` (cookies)

**Body limits (M4, the half not covered by S0-2).** Extend the cap to every route, not just the
proxies: `context` dicts into the 5,000-entry events buffer, `/api/data/save` values into the
row's jsonb, and the resume upload (parsed wholly in memory). Add `timeout=` to **every**
`urlopen` in `app/`.

**Security headers (M11).** There are none today — the only middleware adds cache-control. Add
`Strict-Transport-Security`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`, and a CSP.

Three things to get right:
- **CSP `report-only` first.** `expo export` inlines `@font-face` rules and preload tags into
  the document head; the policy needs one iteration against a real exported bundle.
- **`public/walkthrough.html` is iframed by the landing page** — give it `frame-ancestors 'self'`
  rather than `DENY`.
- Use **pure ASGI middleware, not `BaseHTTPMiddleware`** (the perf report flags the existing
  one; adding a second of the same kind compounds it).

**CORS.** `allow_origins=["*"]` unless `CORS_ALLOW_ORIGINS` is set, and `render.yaml` does not
set it. Not exploitable on its own (auth is a bearer header and `allow_credentials=False`) but
unnecessary in prod, where app and API share an origin. Set it to the exact origins.

**Cookies.** `google_oauth_state` and `google_calendar_oauth_state` are `httponly=True` but
lack `secure=True`. SameSite defaults to `lax`, which is adequate for a top-level redirect. Set
`secure=True` when the scheme is https.

---

### S1-6 — Conditional promo PATCH  `[M6]`

**File:** `app/routes/subscription.py:143-161`

**Mechanism.** Read-check-write across two round trips with no conditional write:

```python
used = list(record.get("promo_codes_used") or [])
if code in used: return json_error(400, ...)
...
update_subscription(userid, {..., "promo_codes_used": used})
```

**Exploit.** Fire N parallel `POST /api/subscription/redeem-promo {"promo_code":"BETAUSER"}`.
All read `used == []`; interleavings where a later reader sees an earlier writer's
`subscription_end_at` **compound** the grant (7 → 14 → …). Free access indefinitely from a
7-day code.

**Fix.** Make the PATCH conditional using PostgREST's contains operator:
`PATCH users?userid=eq.X&promo_codes_used=not.cs.{CODE}`, and treat **zero rows updated** as
"already used". One filter on an existing request; no migration.

---

### S1-7 — One login-failure message  `[M7]`

**File:** `app/routes/account.py:113-121`, `:174-182`

`404 "No account found with that user ID."` vs `401 "Incorrect password."` enumerates valid
userids; register returns distinct 409s for a taken userid vs a taken email. Given the
population is largely minors, a list of "these emails have accounts" is itself sensitive.

**Fix.** One message for both login failures ("Incorrect user ID or password"). Keep the userid
conflict on register (the user chose it and it is visible), but respond to a taken **email**
with the same 200 the success path returns plus a "you already have an account" email — or at
minimum rate-limit per email.

---

### S1-8 — Ops console: a token, and refuse to mount on Render  `[M8-finding]`

> **DONE 2026-09-04** (commit `S1-8:`), shipped ahead of S0-7 exactly as § 3 requires.
> Implemented as written, with one addition the plan did not specify: the four
> browser-navigable HTML shells (`/admin`, `/admin/logic-map`, `/evals`, `/evals/scorecard`)
> are exempt from the header check, because a page navigation cannot set a header. They carry
> no credential and no data — the console prompts for the token and sends it on every API
> call — so a tunnel reaches a page asking for a secret, not a console. `/evals/data` is NOT
> exempt: nothing navigates to it, so it is tooling, and tooling can send a header.
> `server.py` mints and prints a token when `.env` has none, so `python server.py` still
> works out of the box without weakening the fail-closed rule.

**Files:** `ops/admin.py:18-30`, `server.py:32-33`, `app/main.py:109-114`

**READ § 3 — this gates S0-7.**

**Mechanism.** The only protection is `request.client.host in ("127.0.0.1", "::1", ...)`. That
is defeated by **any** localhost tunnel — ngrok, VS Code port forwarding, Cloudflare tunnel —
because the tunnel's peer *is* 127.0.0.1. And if `FORWARDED_ALLOW_IPS=*` is ever set (the
plausible "fix" for H3), `client.host` becomes an attacker-controlled `X-Forwarded-For`.

What is behind it: subprocess launches that spend real money (`/api/agents/run`,
`/api/agents/tools/run`), the roster with names/emails/plan status of minors
(`/api/agents/metrics`, `/api/agents/user-costs`), catalog activation, and test email sends to
arbitrary addresses (`/api/agents/emails/test`).

**Fix.**
- A random `WINGMAN_OPS_TOKEN` checked in a header on **every** ops route — the same pattern
  `EMAIL_CRON_SECRET` already uses (header, never a query string; `compare_digest`; fails
  **closed** when unset). The console's own fetch calls must send it.
- **Refuse to mount ops when `RENDER` is set, regardless of `WINGMAN_ENABLE_OPS`.** Today
  `server.py` only *declines to set* the flag; make the mount itself refuse.
- S0-6 already removes `ops/admin_console.html` and `ops/logic_map.html` from the public route.

---

### S1-9 — `conversations`: RLS, or stop writing it  `[M9-finding]`

**Files:** `app/core.py:129-151` (the schema-as-a-comment), `:545-571` (`log_conversation`),
`:573-584`; `app/services/email.py:382,398,612,659`; `app/services/mailing_list.py:160`;
`app/routes/account.py:126`

**Mechanism.** `log_conversation` writes `userid`, `client_ip`, the bot's question and the
student's **free-text answer** to a `conversations` table whose only definition is a code
comment — **no `*_schema.sql`, so no `enable row level security`**. Every other user table in
this repo enables RLS with no policies; this is the one that may be readable with the anon key
if it was created from that comment. It stores the most sensitive free text in the product — a
minor describing themselves — duplicated outside the RLS-protected `users` row.

Separately, `userid` and full **email addresses** are printed to stdout in five places
(`[MOCK EMAIL] {kind} -> {email}`, etc.), and Render retains stdout. Anyone with log access
gets a timeline of which minors did what and from where.

**Fix.**
- Add `db/conversations_schema.sql` with RLS on and no policies, **and confirm the live
  table's RLS state in the Supabase dashboard** — the file does not retroactively secure a
  table created from the comment.
- Hash or drop `client_ip`.
- Drop userids and email addresses from log lines — log the `email_sends.id` instead; the row
  carries the address.
- Consider whether `conversations` is needed at all now that `user_activity` / `user_events`
  exist. **This is decision 5 in PRODUCTION_READINESS_PLAN.md and needs Shama's call.**

**Also unverified (re-checked 2026-09-04, and the tree has moved since the report):**
`db/deadline_check_log_schema.sql` now exists but contains **no `enable row level security`**
statement; `agent_runs` still has **no schema file at all**. Neither table's live RLS state is
knowable from the repo — confirm both in the Supabase dashboard and add the RLS line to the
deadline file. Note schema files live in **`db/`** now, not the repo root as CLAUDE.md's repo
map still says.

---

### S1-10 — Promo codes into a table  `[L3]`

`wingman/subscription_common.py:203-212` hard-codes `BETAUSER` (7 days), `FREEMONTH`, `WELCOME10`.
Anyone who can read the repository gets free access — and with M6, repeatedly. Move to a table
with per-code redemption counts and expiry. (Ship **after** S1-6, or the race outlives the
move.)

---

### S1-11 — Wrap legacy password hashes in argon2  `[L1]`

**File:** `app/auth/passwords.py:262-295`, `app/routes/account.py:95-97`

Rows that have not logged in since Phase 2 still hold the bare client SHA-256 (`_SHA256_HEX`).
The client sends `sha256(password)` unsalted (`httpClient.ts:342`), so that stored value is
**password-equivalent on the wire** — a DB read lets an attacker sign in as those users with no
cracking at all.

**Fix.** A one-off migration wrapping every legacy value: `argon2(sha256hex)` verifies
identically through the existing path, so nothing else changes. Consider a server pepper.
Separately, validate that `passwordHash` is 64 hex characters server-side and add a client-side
minimum length — neither exists today.

**Note.** There is **no password-reset flow** anywhere in `app/`. A student who forgets their
password has no recovery path except Google linking — which S0-8 is about to tighten. Worth
raising with Shama as a product gap, not a security bug.

---

### S1-12 — `compare_digest` on non-ASCII  `[L2]`

`app/routes/email.py:201-202` and `app/services/email.py:105-109` pass `str` operands to
`hmac.compare_digest`, which raises `TypeError` on non-ASCII input — so `X-Cron-Secret: é` or
`?t=é` returns an unhandled **500** instead of 403/400. Compare `.encode("utf-8")` bytes.

---

### S1-13 — Stop relaying upstream error bodies  `[L5]`

`app/routes/ai.py:84,127` relay the provider's error JSON verbatim (quota messages, model
names); `app/routes/resume.py:62,91` return `str(e)`; `app/routes/account.py:117,142` and many
others return `f"Could not reach Supabase: {e}"`; `app/routes/matching.py:345` returns
`f"Matching failed: {e}"`. None include keys, but they name infrastructure and library
internals. Log the detail with a correlation id; return a generic message.

---

### S1-14 — Calendar sync input handling  `[L6]`

`app/routes/google_oauth.py:646` interpolates the client's `googleEventId` **unquoted** into
the Google API path; a value containing `/` or `?` re-targets the request (scoped to the user's
own calendar token, so no cross-user impact — quote it anyway). `:626-627` does
`year, month, day = date_iso.split("-")`, which raises on a malformed `dateISO` and 500s the
whole sync.

---

### S1-15 — Narrow the row reads  `[L10]`

`get_user` (`app/core.py:651-654`, `select=*`) is used by `update_user_location`,
`update_subscription`, `bump_token_version`, the calendar sync (`google_oauth.py:587`) and the
mailing-list subscribe (`mailing_list.py:203`). Each pulls `password_hash` and
`google_calendar_refresh_token` into memory to answer a question about other columns;
`ops/core._fetch_all_accounts` does it for the whole roster. Nothing leaks today, but every new
consumer of `record` is one `json.dumps` away from it. Use `get_user_account` / narrow selects,
and never pass a full `record` to code that only needs a `userid`.

---

### S1 exit test

1. No **High** or **Medium** finding from `security_report.md` left open (S0 + S1 together
   close C1, H1-H5, M1-M4, M6-M11; **M5 is explicitly deferred to Phase 2** — record that).
2. A replayed refresh token revokes its whole lineage.
3. Every prompt-bearing request is refused unless it names a known feature id; no `system`
   string is accepted from a client.
4. Ops routes 403 without the token, and do not mount at all when `RENDER` is set.
5. CSP in report-only produces a clean report against a real exported bundle.
6. `pytest` green; `cd frontend && npx tsc --noEmit` clean.

---

## 6. Open questions for Shama

Blocking, or near enough:

1. ~~**Prompts server-side (S1-1)?**~~ **ANSWERED — approved and shipped.** See §0b; the
   prompt text was moved verbatim and that was verified character by character.
2. **The daily per-user AI allowance (S0-5).** Recommended ~5x measured median daily spend,
   with an operator override. Read the median off the Cost per user tab.
3. ~~**`conversations` (S1-9)**~~ **ANSWERED** — keep writing it, add RLS, and drop
   `client_ip` outright rather than hashing it. Shipped.
4. **Password reset (S1-11 note).** There is no recovery path today. Product decision.
5. ~~**Anthropic `max_uses` value (S0-4).**~~ **ANSWERED** — S0-4 set it to **1**. The layer
   only matters if S0-3's pin is lifted by accident, so the blast radius should be one search,
   not three. Tunable via `ANTHROPIC_MAX_WEB_SEARCH_USES`.

---

## 7. What this plan deliberately does NOT do

- **Does not touch `gemini_common`'s 5 s delay.** That is M5 / Phase 2, entangled with the
  async AI lane. S0-1's auth gate removes the anonymous half of its DoS story.
- **Does not change argon2 parameters.** Phase 2, capacity-driven. S1-11's legacy-hash
  migration is independent of it.
- **Does not merge or delete any branch.** Phase 3. Note `opportunity-matching` must never be
  merged — it would land four unapproved M8/M9 changes with no conflict marker.
- **Does not add a password-reset flow, an ops SSO, or 2FA.** Out of scope; raise separately.
- **Does not touch the batch agents' own security posture.** They run only on the operator's
  laptop and are never mounted on Render.
