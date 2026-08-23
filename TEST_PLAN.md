# Test Plan — Highschool Wingman

> Status: **no automated tests exist yet.** This document records that finding and proposes
> a proportionate suite to close the gap. Written 2026-08-23 after the Phase 2 auth work
> (`PLAN_2_auth.md`) shipped to prod with only manual verification. Do the build-out in its
> own session.

## 1. Current state (audited 2026-08-23)

There is **no test suite, no CI, and nothing gates deployment** beyond a dependency install.

- **No tests of our own.** The only `test_*.py` at the repo root is `test_agent.py`, a fake
  demo (see §5) — not a test. Every other `test`/`tests` path lives inside
  `frontend/node_modules` (dependencies' own tests).
- **No test runner** — no `pytest.ini`, `pyproject.toml`, `tox.ini`, or jest config.
- **No CI** — no `.github/workflows/`, GitLab, or CircleCI config.
- **No git hooks** — only the default samples in `.git/hooks/`.
- **Render deploy runs only** `pip install -r requirements.txt` (`render.yaml` `buildCommand`).
  No test step, no `preDeployCommand`.

**So the deploy pipeline today is:** push to `main` → Render installs deps → if it imports
and boots, it ships to highschoolwingman.com.

### The risk
A *build/import* failure is safe — Render keeps serving the previous deploy. But a **logic
bug that still imports goes straight to real users** (largely minors). Phase 2 (auth + the
IDOR fix) was verified entirely by hand: ad-hoc E2E scripts plus a browser walkthrough. Those
scripts live in a temp scratchpad, not the repo, so they don't run again on their own. The
next regression in auth, gating, or subscription logic has nothing to catch it before prod.

## 2. Goals (in priority order)

1. **Lock down the security-critical invariants** so they can never silently regress:
   the IDOR stays closed, every owned-data route stays token-gated, and the subscription
   gate keeps blocking lapsed accounts. This is the highest-value coverage in the repo.
2. **Unit-cover the pure logic** that's easy to break and hard to notice: token
   mint/verify, password hashing + legacy upgrade, cost/feature classification, URL
   dedupe/validate/repair, subscription-state math.
3. **Gate deployment on a green suite** via CI, so a red test blocks the push from shipping.

Non-goal: exhaustive coverage of the offline agents' live API calls (those cost money and
are non-deterministic — assert on their pure helpers, not on real Gemini/Claude responses).

## 3. Proposed structure

```
tests/
  conftest.py             # fixtures: FastAPI TestClient, a signed-in token, a 2nd user
  unit/
    test_tokens.py        # app/auth/tokens.py
    test_passwords.py     # app/auth/passwords.py
    test_ratelimit.py     # app/auth/ratelimit.py
    test_subscription_state.py  # app/core.subscription_state / subscription_block_reason
    test_classify_feature.py    # app/core.classify_feature / provider_for_model
    test_url_dedupe.py    # url_dedupe.py (pure)
    test_url_validate.py  # url_validate.py (DNS/dead vs unverified classification, pure)
  integration/
    test_auth_flow.py     # register→login→refresh→logout-all, 401/503 paths
    test_idor.py          # THE regression guard: no-token & wrong-token cannot read data
    test_route_gating.py  # every hard route 401s w/o token; soft/public routes don't
```

Runner: **pytest**. Add `pytest` (+ `httpx` for FastAPI's `TestClient`) to a new
`requirements-dev.txt`. Add a minimal `pyproject.toml` `[tool.pytest.ini_options]` with
`testpaths = ["tests"]`.

### The one real design decision: the Supabase dependency
There is **one Supabase project, shared between local dev and prod** — the same `users` and
`opportunities` tables back both. Integration tests must not scribble on prod data. Options,
cheapest first:

- **A. Mock the Supabase seam.** `app/core._users_request` / `_supabase_request` are the
  choke points; patch them (or `get_user`/`create_user`/`update_user_data`) with an in-memory
  fake. Fast, hermetic, no network, safe for CI. **Recommended for the IDOR/gating tests** —
  they're about *the auth layer's behavior*, not about Postgres, so a fake user store is
  enough. This is also the only option that works in GitHub Actions without leaking the
  service key.
- **B. A separate Supabase test project** (or schema) with its own keys. Truer integration,
  but another project to maintain and secrets to wire into CI.
- **C. Throwaway prefixed rows on the real DB with guaranteed cleanup** (what this session
  did manually, `_phase2test_*`). Do **not** put this in CI — a failed run leaks rows into
  prod, and it needs the prod service key in the CI env. Fine for a local smoke script only.

Recommendation: **A for CI**, keep a small **C-style live smoke script** (adapted from the
scratchpad E2E, below) for manual pre/post-deploy checks against real prod.

## 4. What to cover (mapped to the actual code)

Security-critical (write these first — they encode the Phase 2 guarantees):

- **IDOR closed** (`app/routes/user_data.py`): `/api/data/load` with no token → 401 and no
  data; with user B's valid token but user A's `userid` in the body → returns B's own data,
  never A's; with a garbage token → 401. (This is the exact scenario the manual E2E proved —
  make it permanent.)
- **Route gating** (`app/auth/dependencies.py` wiring): every hard route
  (`data/save|load`, `account/location`, `calendar/sync`, `opportunities/<id>/deadline`,
  `extract-from-resume`, `extract-from-linkedin`, `mailing-list/subscriptions`,
  `opportunities/<id>/subscribe`, `subscription/status|checkout|cancel|redeem-promo`,
  `auth/logout-all`) → 401 without a token; soft routes (`messages`, `messages-claude`,
  `mailing-list/status`, `user-submitted-opportunities`, `subscription/validate-promo`) →
  not 401; public (`opportunities`, `register`, `login`) → reachable.
- **Auth lifecycle** (`app/auth/tokens.py`, `app/routes/auth.py`): access verifies; refresh
  verifies and rejects an access token presented as refresh (and vice versa); tampered/expired
  → `AuthError`; `refresh` rejects a token whose `ver` ≠ `users.token_version` (revocation);
  unset `JWT_SECRET` → `AuthConfigError` → 503, not 401.
- **Password path** (`app/auth/passwords.py`): argon2 round-trips; a legacy bare-SHA-256 row
  verifies by direct compare and reports `needs_upgrade=True`; wrong password fails on both
  legacy and argon2; a `None` hash (Google-only account) never matches.
- **Subscription gate** (`app/core.subscription_state`, `app/deps.subscription_block_reason`):
  trial/active/beta/canceled/past_due access matrix; NULL `trial_ends_at` reads as
  not-expired; missing userid fails open (None).

Pure helpers (cheap, high-value unit tests, no server needed):

- `app/core.classify_feature` / `provider_for_model` — signature matching, ordering.
- `url_dedupe.find_duplicates` / `match_key` — the "same URL + similar name only" rule.
- `url_validate` — dead (404/410/NXDOMAIN) vs unverified (403/429/TLS) classification.
- `url_repair` — the three acceptance tests (title proof, own-name, no-lost-identity-word).
- `app/auth/ratelimit.RateLimiter` — window rollover, limit enforcement.

## 5. CI (what makes tests actually gate a deploy)

Add `.github/workflows/ci.yml`: on push/PR to `main`, `pip install -r requirements.txt
-r requirements-dev.txt` then `pytest`. With the mock-Supabase approach (§3-A) no secrets are
needed, so this runs anywhere.

Then, in the **Render dashboard → Settings**, enable *"Wait for CI to pass before deploying"*
(Render reads GitHub commit statuses). That's the step that turns a green suite into an actual
deploy gate — without it, CI is advisory and Render still deploys a red commit.

Optional belt-and-suspenders: a `pre-push` git hook running `pytest -q` for fast local
feedback before the push even leaves the machine.

## 6. Starting material

The manual Phase 2 E2E scripts (`e2e_auth.py`, `e2e_auth2.py`) were written to the session
scratchpad and are **not in the repo** — they'll be cleaned up with the temp dir. They're a
ready-made basis for `tests/integration/test_auth_flow.py` and `test_idor.py`: they already
exercise register→login→save→load→IDOR→refresh→wrong-password and the argon2/legacy-upgrade
path against a live server. Salvage their assertions and re-point them at a `TestClient` +
mocked user store before they're gone.

---

### Appendix: what `test_agent.py` is

`test_agent.py` is **not a test** despite the name. It's a ~54-line stub that prints canned
`[INFO]/[SUCCESS]` log lines with `time.sleep()` pauses and random numbers, branching on an
`agent_type` argv (`refresh`/`scraper`/`deadline`). It was a stand-in used while building the
admin console's live log-streaming viewer — something to stream fake output into the console
without triggering a real, paid agent run. It is **not imported or referenced anywhere** in
the app or ops code (grep finds it only inside `frontend/node_modules`). It can be deleted, or
kept as a zero-cost fixture for exercising the console's log tail — but rename it (e.g.
`fake_agent_demo.py`) so it isn't mistaken for a real test and swept up by `pytest`.
