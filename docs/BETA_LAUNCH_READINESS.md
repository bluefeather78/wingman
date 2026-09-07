# Beta Launch Readiness — findings

_Compiled 2026-09-07. A sweep of every planning doc, the CLAUDE notes, `MARQUEE_DECISIONS.md`,
the 2026-09-02 production audit, `SECURITY_HARDENING_PLAN.md`, and the live code, to surface what
remains before onboarding the first real beta users (who are largely minors)._

> **How to read this.** Items are graded **BLOCKER** (do before any real user signs up),
> **DECISION** (a choice only you can make), **SHOULD-DO** (quality/trust, not strictly blocking),
> and **DEFERRED** (safe to leave for after beta). Anything already shipped is noted so you don't
> re-audit it. Every claim carries a `file:line` so you can verify.

---

## TL;DR — the state of things

- **Security is in good shape.** The 2026-09-02 audit found 1 Critical + 5 High + 21 M/L. Since then
  the full **S0 and S1 hardening program shipped** (`docs/plans/SECURITY_HARDENING_PLAN.md:20,119`):
  the unauthenticated arbitrary-prompt proxy (**C1**), Google-login takeover (**H1**), open-redirect
  token leak (**H2**), spend caps (**H4**), the catch-all static route (**H5**) — **all closed**.
  `db/RUN_ME_S1.sql` was run and RLS verified on 2026-09-04. Treat the old audit as history, not a
  to-do list. The one security item still open by design is **M5** (a process-wide throughput cap,
  not a user risk — see Deferred).
- **The app itself is feature-complete for beta.** Auth (argon2, JWT fail-closed), subscription
  gating, parental-consent capture, matching, tracker, calendar, lifecycle email — all built.
- **What actually remains is mostly operational + a few decisions**, not code: confirm the Render
  environment matches your local `.env`, decide the beta economics (free-tier gate + Stripe), and
  resolve one legal contradiction. Details below.

---

## 1. BLOCKERS — do these before real users

### 1a. Confirm production (Render dashboard) env parity
Your **local `.env` is fully populated**, but production is the Render dashboard, which is set
separately (`render.yaml` declares the keys as `sync: false` — it never carries values). Several of
these **fail closed or silently degrade to mock** if missing in prod:

- `JWT_SECRET` — unset ⇒ **auth layer 503s, nobody can log in** (`app/auth/tokens.py:42`, fail-closed by design).
- `SUPABASE_URL` / `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_KEY` — unset ⇒ no accounts, no data, no catalog (`app/config.py:311,373`).
- `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` — unset ⇒ **AI silently runs in MOCK mode** (fabricated matches/deadlines) (`CLAUDE.md`, `app/services/ai.py:237`).
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` **and `GOOGLE_APP_REDIRECTS`** — the redirect allowlist default is **local-dev only**, so Google sign-in from the deployed site won't complete until this is set to the web origin (`render.yaml:66`).
- `STRIPE_API_KEY` / `STRIPE_PRICE_ID` / `STRIPE_WEBHOOK_SECRET` — see Decision 2b. `STRIPE_PRICE_ID` must be the new **$4.99/mo** Price object.
- `EMAIL_POSTAL_ADDRESS` — unset ⇒ the literal placeholder `[SET EMAIL_POSTAL_ADDRESS IN .env]` renders in **every email footer** (CAN-SPAM) (`app/config.py:548`). _(Your local value is a real address — mirror it to Render.)_
- After deploy, **read the `[client-ip]` line off the Render log** to confirm `--forwarded-allow-ips` is resolving real client IPs — otherwise every rate-limit bucket collapses to one (`render.yaml:38`; still-open item from S0-7, `SECURITY_HARDENING_PLAN.md:209`).

### 1b. Confirm the required DB migrations are applied to the production Supabase
> **✅ VERIFIED 2026-09-07** by probing the live PostgREST schema directly (project
> `gxxhsgbkdhuefbdtbgjh.supabase.co`, read-only, no SQL run). **All** signup-critical,
> feature-degrading, and day-one migrations below are applied. Two notes: (1)
> `two_tier_free_migration.sql` has no table/column to check, but the live account
> distribution is `{free:10, canceled:2, beta:1}` with **zero `trial`/NULL**, which confirms
> it ran; (2) `users_email_unique_schema.sql` creates only a functional unique index, which
> PostgREST's schema API does not expose — it is the **one item not confirmable remotely**
> (low risk: the app also checks email uniqueness in code; the index is only the race
> backstop). **Remaining human step:** confirm Render's `SUPABASE_URL` points at this same
> project (`gxxhsgbkdhuefbdtbgjh`), or the check applies to the wrong database.

There are 38 `db/*.sql` files, all applied by hand in the Supabase SQL editor (PostgREST has no DDL,
so **nothing in code applies them**). `db/RUN_ME_S1.sql` (the security bundle) was run 2026-09-04.
The **signup-critical** ones — if any is missing, the app is broken for real users:

| Migration | If missing |
|---|---|
| `db/subscription_schema.sql` | **Registration is DOWN** — every insert rejected, `/api/register` 503s (`app/core.py:1029`). _(Docs say run on prod 2026-08-21 — verify.)_ |
| `db/two_tier_free_migration.sql` | New/existing accounts stay `trial` instead of the current `free` model; "run before the client ships" (`two_tier_free_migration.sql:1`). |
| `db/google_auth_schema.sql` | Google Sign-In signup/login fails (`app/core.py:1040`). |
| `db/users_email_unique_schema.sql` | Duplicate-email accounts possible under a signup race (`app/core.py:1089`). |
| `db/user_data_rpc.sql` | **Lost updates** — a student's tracker/profile silently overwritten across tabs/devices (`app/core.py:1229`). |
| `db/auth_schema.sql` | "Log out everywhere" / session-revocation is a silent no-op (`app/routes/auth.py:71`). |
| `db/auth_handoffs_schema.sql` | Google sign-in breaks across multiple workers/instances (`app/services/handoff_store.py:12`). |

**Feature-degrading** (app runs; that feature is off): `email_schema.sql` (no lifecycle email at all),
`match_vector_schema.sql` (finder falls back to thin matching), `action_items_schema.sql`,
`mailing_list_schema.sql`, `google_calendar_schema.sql`, `user_submissions_schema.sql`,
`promo_codes_schema.sql`, `user_costs_schema.sql` (+ re-run for the `model` column), `seo_pages_schema.sql`,
`account_deletions_schema.sql` (deletion works but writes no audit tombstone).

**Run on day one even though nothing reads them for weeks** (the data is otherwise permanently
unrecoverable): `db/user_metrics_daily_schema.sql`, `db/user_events_schema.sql`,
`db/user_activity_schema.sql`, `db/api_errors_schema.sql` (`docs/CLAUDE-ops.md:560`).

### 1c. Resolve the Terms §3 contradiction (legal)
> **✅ DONE / already resolved (confirmed 2026-09-07).** The old "provided free of charge …
> notice before charging you" language is **no longer in `legal/terms.md`** — §3 now describes
> the permanent Free plan + $4.99 Wingman Unlimited, "no free trial," and only promises notice
> before a "material change to pricing" (`legal/terms.md:50-52`). The built HTML users see
> (`public/terms.html`) already matches: re-running `python -m agents.build_legal` on
> 2026-09-07 produced **no diff**, and no old-pricing/`$9.99`/`free trial` string survives in
> either the markdown or the HTML. `TERMS_VERSION` is `2026-09-07` (`app/config.py:362`). No
> code change or commit required. The flagged `docs/SUBSCRIPTION_SETUP.md:307` /
> `docs/CLAUDE-app.md:172` citations were **stale references** predating the terms update.

_Still advisory (not a blocker): consider a privacy-attorney read of the minors framing
(§10/§14) before onboarding minors at scale (`docs/plans/DATA_DELETION_EXPORT_PLAN.md:362`)._

---

## 2. DECISIONS — only you can make these

### 2a. Enforce the free-tier AI cap, or run global-breaker-only?
> **✅ RESOLVED 2026-09-07 — gate ENFORCED in production.** `FREE_TIER_AI_GATE_ENFORCED=1` is set
> on Render, so the per-user daily action cap is live: a Free user who would start a new AI action
> beyond their limit gets a visible, structured `429` (banner + greyed AI buttons, app-wide via
> `AiLimitBanner`/`useAiGate`), not silent mock content, and only *their own* account is blocked.
> Limits kept at defaults for beta: **10 actions/day, 20 on signup day**, pooled across all AI
> features, same-class calls within 5 min collapse to one action. Paid/Unlimited + `BUDGET_EXEMPT_USERIDS`
> are unlimited. The `$25` global breaker still backstops total spend on top.
> **Known soft spot (not a beta blocker):** the per-user action counter is **in-process/in-memory**
> (`app/services/budget.py:324`), so it resets on any process restart (Render Free idle spin-down,
> redeploy, crash) — the limit is soft, failing in the user's favour, which is fine for abuse
> prevention. **Trigger to fix:** before ever running `--workers > 1` or enabling
> autoscaling/instances > 1, move this counter to a shared store (Redis/Supabase) — each process
> keeps its own ledger otherwise and the limit multiplies. The `$25` breaker is DB-backed and is
> unaffected by worker/instance count. Value `10` is a beta anchor; tune later from the console
> per-user histogram (env var, no code change).

`FREE_TIER_AI_GATE_ENFORCED` defaults **off** (`app/config.py:302`), so `FREE_TIER_DAILY_AI_ACTIONS = 10`
is **displayed but never blocks anyone** (`app/services/budget.py:393`, `app/routes/ai.py:394`). Today the
**only** real spend backstop is the **global** `GLOBAL_DAILY_BUDGET_USD = 25` breaker — and when it trips
it degrades the app to mock/cached **for everyone** (`app/services/budget.py:207`). There is no per-user
dollar cap (removed in favour of the actions model, `app/config.py:258`).
- **Implication for beta:** one heavy/abusive account can push toward $25 and flip AI off for all beta
  users. Per my earlier cost model a normal user runs ~$0.15–0.30, so 10 actions/day is generous headroom.
- **Recommendation:** flip `FREE_TIER_AI_GATE_ENFORCED=1` for beta (it's the intended eventual state,
  `TWO_TIER_AI_PLAN.md:26`), or consciously accept global-breaker-only and watch the console.

### 2b. Beta economics — Stripe + the Upgrade path
With Stripe unconfigured, `/api/subscription/checkout` errors and Upgrade surfaces that gracefully
(`frontend/app/(app)/subscription.tsx:227`); the **only** way to become Paid is a `grant` promo code
(`BETAUSER`). Combined with 2a (gate off), that means **effectively everyone is unlimited-free during
beta**. Decide: (i) intentional free beta (leave as-is, hand out `BETAUSER`), or (ii) real payments —
then set the three `STRIPE_*` vars in Render and confirm the Price is $4.99/mo recurring.

### 2c. Arm the deadline/trial reminder emails?
The lifecycle-email **cron is DISARMED** (`.github/workflows/lifecycle-emails.yml` is `workflow_dispatch`
only, `docs/CLAUDE-app.md:604`). Welcome + goodbye are event-driven and fire regardless; only the
**trial-ending / deadline reminders** are held. To arm: run `email_schema.sql`, set `EMAIL_CRON_SECRET`
(Render + GitHub Actions) and `WINGMAN_API_BASE`, uncomment the schedule. _(Deadline-alert P5 is
"ready, held until beta ship" — `docs/plans/DEADLINE_EMAIL_ALERTS_PLAN.md:384`.)_

---

## 3. SHOULD-DO — quality & trust (not strict blockers)

- ~~**Grade-parser false positives set a HARD match filter.** "junior varsity" → grade 11.~~
  **✅ Already fixed (confirmed 2026-09-07).** `parseGradeFromText` is context-required: a grade WORD
  only counts inside a self-referential frame ("I'm a…", "rising…", "…year", "high school…"), so
  "junior varsity"/"senior citizens"/"tutor middle school kids" no longer set a grade
  (`frontend/src/lib/grade.ts:44-67`). A false negative filters nothing, and the finder asks grade
  explicitly (a mandatory one-time picker) before the first suggest-search when it still can't tell
  (`frontend/app/(app)/finder.tsx:827`). The flagged `frontend_report.md:108` predates the fix.
- ~~**Eligibility is invisible to matching.**~~ **✅ Addressed 2026-09-07.** The theme path was already
  eligibility-gated inside `/api/match`; the form/quiz path (`preFilter`+`rankCandidates`) was not, so
  it could surface citizenship/geography/prereq-ineligible rows (grade was already filtered there).
  New `POST /api/match/eligibility` runs the SAME `gate_pool_eligibility` (M8 prompt, quote-verified)
  over the form path's chosen candidates and drops the verified-ineligible ones; the client keeps
  everything not excluded, so any degrade leaves the list untouched. **MARQUEE M9** (new paid-call
  path), costed/gated like `/api/match`, not metered as a second Free-tier action
  (`app/routes/matching.py`, `frontend/app/(app)/finder.tsx` `gateFormEligibility`).
- **"(verified)" deadlines can be confidently wrong on JS-heavy sites** — a stale pre-JS page fetch
  reads as verified (`docs/plans/DEADLINE_AND_TASK_PLAN.md:360`, G6b, open). Trust risk for a deadline
  product; consider softening the "verified" label copy until mitigated.
- **Matching recall decay:** new catalog rows only become matchable after a **manual `match_vector`
  backfill** — there is **no activation re-embed hook** (`docs/plans/RECALL_GRID_MERGE_PLAN.md:210`).
  Confirm the 222-row backfill was run and decide whether to wire the hook, or recall silently decays
  as you add opportunities. Also calibrate `WINGMAN_STRONG_MATCH_MIN` (defaults provisional, `:65`).
- **Synthesis failure persists the raw chat transcript as the profile** (`frontend_report.md` Med #10);
  malformed date can make a card read "Happening Now" (Med #11).
- **School-domain email blind spot:** a student on a locked school domain silently receives no mail;
  there's no editable alert-address field (`docs/CLAUDE-app.md:685`).
- **Frontend has zero automated tests** and only ~5 accessibility labels app-wide
  (`frontend_report.md:247,239`) — relevant if any beta user relies on assistive tech.
- **Cosmetic doc drift:** `docs/CLAUDE-app.md:508` still calls clear-profile a "visual stub" — it's
  actually fully implemented (`frontend/app/(app)/profile.tsx:294`). No code action; fix the note.

---

## 4. SCALING — Render Free is the binding constraint

Not a blocker for a small beta, but know the ceiling:
- **Render Free = 0.1 CPU, single worker** (`render.yaml`). `AI_MAX_CONCURRENCY = 12` of 40 threadpool
  slots — deliberately conservative; comment: "raise once the tier is known and the host is paid — a
  launch-gate task" (`app/config.py:150`). A classroom opening Home Base together can hit shedding (503s).
- The 2026-09-02 perf review concluded **50 RPS is unreachable on Free regardless of code**
  (`docs/review-2026-09-02/perf_report.md:496`). Plan a paid instance before any real load / a class demo.
- **M5:** a process-wide 5s sleep before every interactive Gemini call caps throughput ~5 RPS
  (`perf_report.md:372`; the only security/perf item deliberately left for Phase 2,
  `SECURITY_HARDENING_PLAN.md:246`). Fine for a trickle of beta users; revisit before scale.
- If you ever add `--workers > 1`, the in-process rate limiters and budget ledger multiply per worker
  (`app/auth/ratelimit.py:6`) — coordinate that with a shared store first.

---

## 5. DEFERRED — safe to leave for after beta

- **Catalog pipeline / scraper** work (`docs/plans/HANDOFF.md`): the review-queue drain workflow, the
  discovery gate, hub-mining — ops-side catalog quality, doesn't gate signup. Note two **unpushed
  commits** on branch `claude/opportunity-scraper-logic-a509f1` (a worktree) and a flagged **GitHub PAT
  rotation** (`HANDOFF.md:112`).
- **Matching v2** (curated Fit+Growth recommender, `OPPORTUNITY_MATCHING_PLAN.md`) — design only, unbuilt.
- **Discovery engine** (`DISCOVERY_ENGINE_PLAN.md`) — mostly unbuilt; P7 crawling individuals carries a
  minors-safety flag, kept out of student view until vetted.
- **Dedupe simplification** phases 4–5, **angle strategy** prompt redesign — admin-side catalog hygiene.
- **Data export/delete**: shipped, but confirm `account_deletions_schema.sql` is run and complete the
  Play/App-Store deletion-URL listing step (`DATA_DELETION_EXPORT_PLAN.md:347`).

---

## Minimal go-live order (condensed)

1. **Verify prod env parity** on Render (§1a) — especially `JWT_SECRET`, Supabase keys, both AI keys,
   `GOOGLE_APP_REDIRECTS`, `EMAIL_POSTAL_ADDRESS`, and the $4.99 `STRIPE_PRICE_ID`.
2. ~~**Verify the signup-critical + day-one DB migrations** are applied to prod Supabase (§1b).~~
   **✅ DONE 2026-09-07** — all applied; only open sub-step is confirming Render's `SUPABASE_URL`
   matches project `gxxhsgbkdhuefbdtbgjh`.
3. ~~**Fix Terms §3**, re-run `build_legal.py` (§1c).~~ **✅ DONE** — already resolved; rebuild is a no-op.
4. **Decide** the free-tier gate (§2a), Stripe/economics (§2b), and email cron (§2c).
5. Read the `[client-ip]` log line post-deploy; set `CSP_ENFORCE=1` once you've checked the CSP report.
6. (Optional, quality) grade-parser fix + eligibility-in-matching + `match_vector` backfill/hook (§3).

_Security S0+S1: complete. Boot blocker (numpy): fixed. Nothing in this doc is a code emergency — it's
config, decisions, and polish._
