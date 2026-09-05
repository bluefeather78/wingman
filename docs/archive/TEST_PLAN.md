# Test Plan — Highschool Wingman

> Status: **no automated tests exist yet.** This document records that finding and proposes
> a proportionate suite to close the gap. Written 2026-08-23 after the Phase 2 auth work
> (`PLAN_2_auth.md`) shipped to prod with only manual verification. **Revised 2026-08-23** to
> cover the *whole* codebase, not just the auth layer — the first draft scoped coverage to
> Phase 2 and missed the large body of pure business logic that carries most of the app's
> real risk (URL health, cost accounting, the mock-AI dispatcher, provider recipe parsing,
> snapshot commit, metrics/funnel math, and the frontend). Do the build-out in its own session.

## 1. Current state (audited 2026-08-23)

There is **no test suite, no CI, and nothing gates deployment** beyond a dependency install.

- **No tests of our own.** `test_agent.py` (a fake log-streaming demo, not a test) has already
  been removed. Every other `test`/`tests` path lives inside `frontend/node_modules`.
- **No test runner** — no `pytest.ini`, `pyproject.toml`, `tox.ini`, or jest config.
- **No CI** — no `.github/workflows/`, GitLab, or CircleCI config.
- **No git hooks** — only the default samples in `.git/hooks/`.
- **Render deploy runs only** `pip install -r requirements.txt` (`render.yaml` `buildCommand`).
  No test step, no `preDeployCommand`.

**So the deploy pipeline today is:** push to `main` → Render installs deps → if it imports
and boots, it ships to highschoolwingman.com.

### The risk
A *build/import* failure is safe — Render keeps serving the previous deploy. But a **logic
bug that still imports goes straight to real users** (largely minors). The risk is not just
auth: a wrong cost estimate under-quotes a paid agent run before someone authorises it; a
loosened URL-repair test silently redirects a student to a sibling program; a broken mock
dispatcher makes the whole app unusable offline; a drifted price constant estimates spend at
3× reality. All of that still imports and boots.

## 2. Goals (in priority order)

1. **Lock down the security-critical invariants** so they can never silently regress: the
   IDOR stays closed, every owned-data route stays token-gated, the subscription gate keeps
   blocking lapsed accounts, and the static server keeps rejecting path traversal.
2. **Unit-cover the pure logic across the whole repo** — this is the bulk of the work and the
   highest value-per-test, because almost none of it needs a server or a network:
   - **Auth/account math**: token mint/verify, argon2 + legacy-SHA upgrade, rate limiting,
     `subscription_state`, `subscription_block_reason`, signup-consent, email/uniqueness.
   - **Cost accounting**: `estimate_cost` in *both* provider libs, `provider_for_model`,
     `classify_feature`, the console's per-user/per-model rollup and untracked-bucket folding,
     `estimate_agent_cost` (the number people authorise a paid run against).
   - **URL health** (the agents' whole reason to exist): `url_dedupe.find_duplicates`,
     `url_validate` (DNS-failure / dead-vs-unverified / offsite detection / grounding spans),
     `url_repair`'s three acceptance tests, the scraper's `reconcile_url`.
   - **The offline-mode surface**: `app/services/ai.py` (mock dispatcher + every parser) and
     `deadlines` cache helpers — this is what keeps the app click-through-able with no keys.
   - **Provider recipes**: `mailing_list_common` form/provider detection and field resolution.
   - **Snapshot commit + seeds**: `dryrun_common`, `seeds_common.select_seeds`.
   - **Metrics/funnel math**: the funnel stage predicates, cumulative-drop, conversion
     denominator, retention cohorts.
3. **Pin the cross-file invariants** the codebase's own comments warn about (§4.9).
4. **Gate deployment on a green suite** via CI, so a red test blocks the push from shipping.

Non-goal: exhaustive coverage of the offline agents' *live* API calls (they cost money and
are non-deterministic — assert on their pure helpers, not on real Gemini/Claude responses).
Also non-goal: pixel/DOM testing of the frontend renderers (low value); the frontend target
is its pure logic only (§4.8).

## 3. Proposed structure

```
tests/
  conftest.py                      # fixtures: TestClient, signed-in token, 2nd user, in-memory user store
  unit/
    # --- auth / account ---
    test_tokens.py                 # app/auth/tokens.py
    test_passwords.py              # app/auth/passwords.py
    test_ratelimit.py              # app/auth/ratelimit.py
    test_subscription_state.py     # app/core.subscription_state, deps.subscription_block_reason
    test_consent_and_email.py      # core._check_signup_consent, normalize_email, _is_email_conflict, config.EMAIL_RE
    # --- cost accounting ---
    test_classify_feature.py       # core.classify_feature, provider_for_model  (+ sync check vs ai.generate_mock_text)
    test_estimate_cost.py          # gemini_common.estimate_cost AND claude_common.estimate_cost (separately)
    test_estimate_agent_cost.py    # ops/core.estimate_agent_cost, _group_untracked_models
    test_user_costs_rollup.py      # ops/core.get_user_costs core aggregation (seams stubbed)
    # --- URL health ---
    test_url_dedupe.py             # wingman/url_dedupe.py (find_duplicates + helpers)
    test_url_validate.py           # url_validate: _is_dns_failure, domain_matches_org, is_bare_domain, support_urls_by_span
    test_url_repair.py             # url_repair: title_proves, keeps_identity, identity_words
    test_scraper_urls.py           # scrape_opportunities: reconcile_url, spans_for_name, build_row, next_id_generator
    test_check_links.py            # check_links: classify, merge_flags, build_update
    # --- offline / mock surface ---
    test_mock_ai.py                # app/services/ai.py (generate_mock_text dispatcher + every parser)
    test_deadlines.py              # app/services/deadlines cache helpers
    test_extract_json.py           # gemini_common.extract_json AND claude_common.extract_json (separately)
    # --- providers / snapshots / seeds ---
    test_mailing_list_common.py    # detect_provider, extract_forms, resolve_fields, _loads, provider classifiers
    test_dryrun_commit.py          # dryrun_common.commit_snapshot (injected callables), _run_date, resolve
    test_seeds.py                  # seeds_common.select_seeds
    test_subscription_common.py    # extend_from, trial math, validate_promo_code, promo_kind
    # --- ops console math ---
    test_ops_shaping.py            # _run_status, _shape_run, build_agent_args, _coerce_field, _qs_int
    test_funnel.py                 # _profile_facts, _tracker_facts, _stage_flags, _cumulative_stage
    # --- misc pure ---
    test_resume_multipart.py       # resume.extract_multipart_file, fallback_extract_text
    test_static_resolve.py         # main._resolve_static path-safety
    test_build_legal.py            # build_legal.render / inline
    test_dedupe_seam_import.py     # regression: mailing_list.py uses re without importing it (§5 finding)
  integration/
    test_auth_flow.py              # register→login→refresh→logout-all, 401/503 paths
    test_idor.py                   # THE regression guard: no-token & wrong-token cannot read data
    test_route_gating.py           # every hard route 401s w/o token; soft/public routes don't
    test_subscription_gate.py      # lapsed account → 402 from the four paid endpoints
```

Runner: **pytest**. Add `pytest` (+ `httpx` for FastAPI's `TestClient`) to a new
`requirements-dev.txt`. Add a minimal `pyproject.toml` `[tool.pytest.ini_options]` with
`testpaths = ["tests"]`. The offline agents are stdlib-only, so their unit tests need no extra
deps beyond pytest itself.

### The one real design decision: the Supabase dependency
There is **one Supabase project, shared between local dev and prod** — the same `users` and
`opportunities` tables back both. Integration tests must not scribble on prod data. Options,
cheapest first:

- **A. Mock the Supabase seam.** The choke points are `app/core._users_request` /
  `_supabase_request` / `_supabase_request_strict` (and the raw `urllib.urlopen` calls in
  `services/deadlines.py`, `services/opportunities.py`, `services/google_oauth.py`); patch them
  with an in-memory fake. For the offline agents, the seam is `supabase_common.*` and each
  agent's paid call (`call_gemini` / `call_claude` / `check_deadlines.call_claude`). Fast,
  hermetic, safe for CI, and the only option that works in GitHub Actions without leaking the
  service key. **Recommended default.**
- **B. A separate Supabase test project** (or schema) with its own keys. Truer integration,
  but another project to maintain and secrets to wire into CI.
- **C. Throwaway prefixed rows on the real DB with guaranteed cleanup** (what Phase 2 did
  manually, `_phase2test_*`). Do **not** put this in CI — a failed run leaks rows into prod and
  needs the prod service key in the CI env. Fine for a local smoke script only.

Recommendation: **A for CI**, keep a small **C-style live smoke script** (adapted from the
scratchpad E2E, below) for manual pre/post-deploy checks against real prod.

**A note on global state.** Many modules cache in process-level globals — `app/core`
(`_runs_cache`, `_interactive_rollup`, `_user_costs_rows`, `_activity_buffer`, the
`_user_costs_available`/`_has_model` latches), `app/services/opportunities` (`_opportunities_cache`),
`ratelimit` singletons, `google_oauth` token stores, `mailing_list._subscribe_history`,
`ops/core` (`_runs_cache`, `_metrics_snapshot_available`, the log ring buffer),
`script.js` (`starterWindowIndex`, `OPPORTUNITIES`). Tests must reset these between cases — add
autouse fixtures. This is the single biggest source of order-dependent flakiness to design out
up front.

## 4. What to cover (mapped to the actual code)

Ordered by value. §4.1 is the security floor; §4.2–4.5 are the bulk of the pure-logic wins;
§4.6–4.8 broaden coverage; §4.9 pins the cross-file contracts.

### 4.1 Security-critical (write these first — they encode the Phase 2 guarantees)

- **IDOR closed** (`app/routes/user_data.py`): `/api/data/load` with no token → 401 and no
  data; with user B's valid token but user A's `userid` in the body → returns B's own data,
  never A's; garbage token → 401. Make the exact manual-E2E scenario permanent.
- **Route gating** (`app/auth/dependencies.py` wiring). Hard (→401 without a token):
  `data/save|load`, `account/location`, `calendar/sync`, `opportunities/<id>/deadline`,
  `opportunities/<id>/subscribe`, `extract-from-resume`, `extract-from-linkedin`,
  `mailing-list/subscriptions`, `subscription/status|checkout|cancel|redeem-promo`,
  `auth/logout-all`. Soft (not 401): `messages`, `messages-claude`, `mailing-list/status`,
  `user-submitted-opportunities`, `subscription/validate-promo`. Public (reachable):
  `opportunities`, `register`, `login`, `auth/refresh`, the Google OAuth start/callback/session.
- **Auth lifecycle** (`app/auth/tokens.py`, `app/routes/auth.py`): access verifies; refresh
  verifies and **rejects an access token presented as refresh (and vice versa)**;
  tampered/expired → `AuthError`; `refresh` rejects a token whose `ver` ≠ `users.token_version`
  (revocation via `logout-all`); unset `JWT_SECRET` → `AuthConfigError` → **503, not 401**.
- **Password path** (`app/auth/passwords.py`): argon2 round-trips; a legacy bare-SHA-256 row
  verifies by constant-time compare and reports `needs_upgrade=True`; wrong password fails on
  both legacy and argon2; a `None`/empty hash (Google-only account) never matches.
- **Subscription gate** (`app/core.subscription_state`, `app/deps.subscription_block_reason`):
  trial/active/beta/canceled/past_due access matrix; NULL `trial_ends_at` reads as
  not-expired (don't paywall pre-migration accounts); missing userid fails open (None); the
  four paid endpoints return **402** when access has lapsed.
- **Static path-safety** (`app/main._resolve_static`): rejects dotfiles/dotdirs, `agent_logs`,
  `..` traversal outside `REPO_ROOT`, `_DENY_EXT`/`_DENY_NAMES`; directory → `index.html`.
- **Localhost gate** (`ops/admin.require_local`): non-local host → 403; the IPv6 forms
  (`::1`, `::ffff:127.0.0.1`) and `request.client is None`.

### 4.2 Cost accounting (a wrong number here spends real money or misreports it)

- **`gemini_common.estimate_cost` AND `claude_common.estimate_cost`, tested separately** —
  they have different price constants and Claude adds cache-token pricing + a different
  per-search fee. The Sonnet/Haiku price drift documented in CLAUDE.md is exactly a
  constant-vs-MODEL mismatch a test would have caught.
- **`app/core.classify_feature`** — ordered substring dispatch; unrecognized/None → `"other"`.
  **Order-sensitive** (the two `tracker_extract` sigs, `chat_starters` before `profile_chat`,
  the two `ranking` sigs). Table-test every signature.
- **`app/core.provider_for_model`** — prefix map (`claude`→anthropic, `gemini`→google),
  `_SURFACE_PROVIDERS` fallback, else `"unknown"`.
- **`ops/core.estimate_agent_cost`** — the figure an operator authorises a paid run against.
  Pin: the `free` short-circuit ($0.00, `provisional:False`) reads as a *fact* not an absence;
  filters to successful/finished/non-`snapshot-commit`/`items_processed>0`/`cost_usd not None`
  rows (the documented under-quote-by-half bug came from *not* excluding failures + commits);
  low/high spread; `provisional = n<3`; empty history → all-None.
- **`ops/core._group_untracked_models` / `_group_untracked_feature_models`** — blank/"(before
  model tracking)" fold into one "Other" bucket sorted last; divide-by-zero guard on
  `cost_per_call`; `users` = max not sum.
- **`ops/core.get_user_costs`** aggregation core (seams stubbed): UTC-day bucketing
  (`recent_day` = latest active day, not "today"); every account gets a row even at zero spend;
  `attributed + unattributed == interactive_total` invariant; `pre_attribution` vs `signed_out`
  split; `attribution_rate` over the *attributable* total; `pct_of_plan`.
- **`ops/core.get_agents_summary` unit-aware rollup**: scraper `items_processed` = SEEDS,
  never summed into row counts; interactive rows skipped from run counts; `-dryrun` cost counts
  but row counts don't; `unknown_cost_runs`.
- **`app/core.record_interactive_cost`** arithmetic (inject usage + pricing; the rollup upsert
  is a seam).

### 4.3 URL health (the agents' whole job; a wrong verdict removes a real program or ships a dead link)

- **`url_dedupe.find_duplicates`** + helpers (`split_url`, `_clean_query`, `match_key`,
  `registrable_domain`, `normalize_name` incl. year-stripping, `name_similarity` with
  `GENERIC_NAMES`/short-name → 0.0, `is_low_value_path`, `_prefix_relation` bare-root guard).
  The governing rule: **only same-normalized-URL + similar-name is a duplicate**; shared-portal
  URLs (`spicestanford.smapply.io` backs six programs) must *not* collapse. Pure over injected
  `existing_rows`.
- **`url_validate`**: `_is_dns_failure` (NXDOMAIN-dead vs TLS/timeout-unverified — construct
  nested `URLError.reason` chains; "do not collapse"); `domain_matches_org`
  (substring-against-label, acronyms/initials, deliberately generous → the documented measured
  cases); `is_bare_domain`; `support_urls_by_span` (grounding span→URL attribution over fixture
  dicts); the DEAD (404/410) vs UNVERIFIED (403/429/TLS) split in `check_url` (mock the opener).
- **`url_repair` three acceptance tests** — the accuracy core: `title_proves` (Test 1+2: every
  identity word in the title; `<2` words → unverifiable), `keeps_identity` (Test 3:
  sibling-program + name/org-swap catch), `identity_words` (name minus org). Feed titles/URLs,
  assert accept/reject. **Do not loosen** — the documented sibling failures (`aip_hs → pb/sip`,
  Notre Dame/Global Scholars swap) are what these catch.
- **`scrape_opportunities`**: `reconcile_url` (the anti-hallucination preference ladder:
  span-attributed > model-url-if-retrieved > same-host-retrieved > flagged-unsourced),
  `spans_for_name`, `build_row` (placeholder-type parking, None on no name/url),
  `next_id_generator`, `clean_value`, `_name_key`.
- **`check_links`**: `classify` (DEAD→repair/deactivate, UNVERIFIED→flag blocked-vs-unreachable,
  LIVE→soft-404 flag), `merge_flags` (replace only this agent's `_OWNED_PREFIXES`, idempotent),
  `build_update` (`link_dead_since` first-seen-wins; `is_active` True only on repair-restore /
  False on deactivate; the deliberate `updated_at` withholding; None when nothing changed).

### 4.4 The offline / mock surface (keeps the app usable with no API keys)

- **`app/services/ai.py`** — the richest pure target in `app/`. `generate_mock_text` dispatcher
  (ordered substring match on `system`; must stay in sync with `classify_feature`) plus every
  parser: `extract_ids`, `extract_candidates`, `mock_rank_candidates` (tiering + fallback),
  `mock_infer_subjects` (`<2` matches → `['STEM','Mixed']`), `mock_synthesize_profile`,
  `guess_section` (order-sensitive keyword buckets), `parse_opp_fields` (two-tier),
  `mock_profile_chat_question` (turn-count modulo), `mock_tracker_extract` (140-char truncation).
  Seed `random`, freeze `date.today()`. Flag `mock_deadline_iso`'s process-salted `hash()`.
- **`app/services/deadlines`**: `deadline_cache_is_fresh` (7-day TTL, None/parse-error → False),
  `cached_deadline_payload` (key mapping + defaults), `mock_deadline_check_payload`.
- **`extract_json` in `gemini_common` AND `claude_common`, separately** (duplicate ports):
  string/escape-aware brace scan, truncation repair (close open string/array/object, trim
  dangling comma), `strict=False` fallback. The `script.js` `extractJSON` is a third copy —
  test it too if a JS harness lands (§4.8).

### 4.5 Providers, snapshots, seeds, subscription math

- **`mailing_list_common`**: `detect_provider` (Mailchimp requires `u`+`id` → `post-json`
  rewrite; ConvertKit/Kit, MailerLite, Substack), `extract_forms` (`&amp;amp;`-unescape of
  action — the documented "amp;id" bug), `resolve_fields` (`$placeholder` sub, drop empties,
  keep literals), `_loads` (bare-JSON vs JSONP), the four provider classifiers (`_mailchimp`
  etc.) with `_post` mocked, `_ALREADY_RE`/success wording, `candidate_urls`.
- **`dryrun_common`**: `commit_snapshot` (dependency-injected patch/insert/existing-urls
  callables — designed to be testable without a DB; dedupe against live+snapshot, always
  `is_active=false`), `_run_date` (date-only vs `YYYYMMDD-HHMMSS`, date-only→midnight UTC),
  `resolve` (path-traversal guard — security-relevant), `_load` (bare-list vs `{inserted,
  rejected}`), `_pending_count`, `normalize_url`.
- **`seeds_common.select_seeds`** — select by stable id vs deprecated positional index,
  missing-id warnings; fallback seeds carry `id=None`.
- **`subscription_common`**: `extend_from` (additive grant from `max(now, current)` — "don't
  take back unused trial"), `trial_ends_at_iso`/`is_trial_expired`/`days_until_trial_end`
  (`math.ceil` rounding — the "2 days left one second in" bug; inject/patch now),
  `validate_promo_code`, `promo_kind` (missing → "checkout"),
  `verify_stripe_webhook_signature` (pin current behavior — looks fragile).

### 4.6 Ops console shaping / funnel math

- **Run shaping**: `_run_status` (failed/success/running/**interrupted** split on
  `AGENT_RUN_TIMEOUT_SECS` — freeze the clock), `_shape_run` (interactive branch, `-dryrun`
  mode, duration only when both timestamps parse), `_parse_iso` (`Z` swap), `_agent_key_for`.
- **`build_agent_args`** — the best argv unit-test target (branch-heavy): per-agent scope flags,
  `links` `--repair-flagged` excludes `--force`, timing precedence, `--preview` vs `--dry-run`
  mutual exclusion, default sample sizes.
- **`_coerce_field`** (int None/""/"—"→None, range 1–13, junk raises; list comma-split, empties
  dropped), `_int_or_none`, `_qs_int` (clamp order), `create_seed` validation branch.
- **Funnel**: `_json_obj` (dict-or-JSON-string coercion), `_profile_facts` (mirrors
  `PROFILE_SUFFICIENT_WORDS=20`), `_tracker_facts` (excludes saved-for-later; started-action
  predicate), `_stage_flags` (`ran_search` = proxy OR implication; `_rich_profile` gate),
  `_cumulative_stage` (stop at first false — the cumulative-drop invariant), `_week_start`.
- **`estimate_agent_cost` / `get_user_costs` / `get_user_metrics`** aggregation with seams
  stubbed (conversion denominator = only trials that ended; `beta` excluded; churned-with-Stripe
  = converted; cohort maturity → dash not 0%; DAU/WAU/MAU guards). `annotate_committed_snapshots`,
  `seed_yield_state`, the log ring buffer (`_append_log`/`get_agent_log` `dropped` accounting).

### 4.7 Misc pure helpers (cheap, no server)

`resume.extract_multipart_file` (two `filename=` forms, `\r\n\r\n` vs `\n\n` split, malformed
cases) + `fallback_extract_text`; `agent_common.clean_email`/`snapshot_stamp`/`emit_preview`;
`check_deadlines.extract_source_urls` (Claude's grounding equivalent, over a response dict);
`build_legal.render`/`inline` (a self-contained mini-markdown parser — very unit-testable);
`contact_email_common.extract_emails`/`_is_generic`/`_visible_text`/`candidate_urls`;
`supabase_common.load_dotenv` + `config.load_dotenv` (quote strip, don't-override-existing);
`deps.json_response`/`json_error`/`read_json_body_strict`; `ai_route._clamped_max_tokens`;
`google_oauth._mint/_take/_prune` token-store trio (freeze `time.time`).

### 4.8 Frontend (`script.js`) — pure logic only, if/when a JS harness is added

No JS test runner exists today. **Recommendation: defer, but track it** — the Python side is
where deploy risk concentrates. If added (node + jsdom or bun, `crypto.subtle` available for
`hashPassword`), the pure targets are: `extractJSON` (the third copy of the JSON repair logic),
`preFilter` (inject the `OPPORTUNITIES` global), `keywordScore`/`tokenize`,
`parseGradeFromText`/`isGradeEligible`, `countProfileWords`, `profileHasTruncatedTail`,
`drawStarterWindow` (resets `starterWindowIndex`), `computeProgressStatus` (freeze clock),
`computeStats` (saved-for-later exclusion), `profileDerivedIsFresh`. The renderers and
storage/DOM-coupled functions are out of scope.

### 4.9 Cross-file invariant contracts (the code's own comments demand these)

These are the "if the bar moves, move it in both places" points. Even without a shared JS+Py
harness, assert the Python side of each and leave a comment pointing at the JS line:

1. `PROFILE_SUFFICIENT_LENGTH` (script.js) == `PROFILE_SUFFICIENT_WORDS=20` (`ops/core`) ==
   `meaningful_profile` gate.
2. `ALL_BUCKETS` (script.js) == `TRACKER_BUCKETS` (`ops/core`).
3. Saved-for-later exclusion identical in `computeStats` (js) and `_tracker_facts` (`ops/core`).
4. `classify_feature` signatures (`app/core`) stay in sync with `generate_mock_text`'s dispatch
   order (`app/services/ai.py`) — a single test importing both and asserting the same system
   prompt routes to the matching feature/mock.
5. `CLAUDE_MODEL` and its price constants agree across `app/config`, `agents/check_deadlines.py`, and
   `wingman/claude_common.py` (the documented Sonnet-drift-3×-overcharge bug).

## 5. A correctness bug the audit surfaced (fix alongside the tests)

`app/services/mailing_list.py` calls `re.sub(...)` in three functions but **never imports
`re`** — it works only because `from app.config import *` happens to re-export the `re` module.
Add an explicit `import re` and a regression test (`test_dedupe_seam_import.py`) so a future
tidy of `config`'s imports can't silently break mailing-list id-sanitization in prod.

## 6. CI (what makes tests actually gate a deploy)

Add `.github/workflows/ci.yml`: on push/PR to `main`, `pip install -r requirements.txt
-r requirements-dev.txt` then `pytest`. With the mock-Supabase approach (§3-A) no secrets are
needed, so this runs anywhere.

Then, in the **Render dashboard → Settings**, enable *"Wait for CI to pass before deploying"*
(Render reads GitHub commit statuses). That's the step that turns a green suite into an actual
deploy gate — without it, CI is advisory and Render still deploys a red commit.

Optional belt-and-suspenders: a `pre-push` git hook running `pytest -q` for fast local feedback
before the push even leaves the machine.

## 7. Suggested phasing (so the first session ships something that gates)

The full §4 surface is large; do it in waves, each independently mergeable and CI-gating:

- **Wave 1 — security floor + CI skeleton**: §4.1 (IDOR, gating, auth lifecycle, passwords,
  subscription gate, static/localhost) + `pyproject.toml` + `requirements-dev.txt` + the CI
  workflow. This alone makes the next auth/gating regression impossible to ship silently.
- **Wave 2 — money + URLs**: §4.2 (cost accounting) and §4.3 (URL health). Highest
  value-per-test outside auth; all pure, no server.
- **Wave 3 — offline surface + providers**: §4.4, §4.5. Protects the no-keys experience and the
  student-facing mailing-list/snapshot flows.
- **Wave 4 — ops math + misc + invariants**: §4.6, §4.7, §4.9, and the §5 fix.
- **Wave 5 (optional/deferred)**: §4.8 frontend harness.

## 8. Starting material

The manual Phase 2 E2E scripts (`e2e_auth.py`, `e2e_auth2.py`) were written to the session
scratchpad and are **not in the repo** — they'll be cleaned up with the temp dir. They're a
ready-made basis for `tests/integration/test_auth_flow.py` and `test_idor.py`: they already
exercise register→login→save→load→IDOR→refresh→wrong-password and the argon2/legacy-upgrade
path against a live server. Salvage their assertions and re-point them at a `TestClient` +
mocked user store before they're gone.
