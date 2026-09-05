# TWO_TIER_AI_PLAN.md

> **IMPLEMENTATION STATUS (branch `two-tiersystem`, 2026-09-05).** Being built in sequence:
> - **Step 1 — instrumentation (done, `962e9f0`):** `ai_tier()`, `budget.user_requests_today()`,
>   config anchors (`FREE_TIER_DAILY_AI_ACTIONS=10`, `FIRST_DAY_AI_ACTIONS=20`), the console
>   "AI tiers" card (tier counts, allowance histogram, `ai_limit_hit` plumbing). Read-only.
> - **Step 2 — retire the trial (done, `aacf416`, MARQUEE):** `subscription_state()` reworked —
>   `has_access` always True, `in_paid_period` drives the tier, no lockout; `free` is the default;
>   `db/two_tier_free_migration.sql` backfills trial→free.
> - **Step 3 — set numbers:** anchors in place (10 / 20 first day); tune from the console later.
> - **Step 4 — the gate (done, `2d4fcb9`, MARQUEE M9 + new M11):** `budget.ai_allowance_state()`
>   (actions/day, time-bucket collapse by class, first-day boost, dollar backstop + circuit
>   underneath), structured 429 + `meta.allowance` echo, wired into `/api/ai`, the deadline check,
>   resume import, action items; `record_user_cost` counts every call (M11). **Ships in OBSERVE
>   mode — `FREE_TIER_AI_GATE_ENFORCED` off — so nobody is blocked by the action cap yet.**
> - **Step 5 — client UI:** in progress (Home Base banner, meter chip, cap card, Manage Plan
>   compare, My Vibe badge, 429-with-allowance handling, remove trial countdown).
> - **Step 6 — legal + Stripe + email:** pending (Terms edit + `TERMS_VERSION` bump; repurpose the
>   `trial_ending` email to a limit-hit nudge; configure Stripe before promoting the upsell).
>
> The §§ below are the original design; Q0/Q0b resolved, plus §14 Q1 (first-day: boosted allowance)
> and the action-counting mechanism (time-bucket collapse by class) resolved by Shama 2026-09-05.

Planning document — **not yet approved, no code written**. Author: Claude, 2026-09-05, at
Shama's request; **Q0 + Q0b resolved by Shama 2026-09-05** (see §0). This designs a two-tier
AI model: a **permanent Free tier** metered to a daily AI-request allowance, and a **Paid
tier** with unlimited AI. **The 7-day trial is removed entirely.** It covers the data model,
the gating logic, per-tier and limit-hit tracking, the admin dashboard, the user-facing UI
(including a Home Base upsell banner), the My Vibe / profile changes, and every fallback path.

> **MARQUEE WARNING — read before implementing.** Almost everything here touches **M9 — any
> code path that makes a paid API call** (the spend caps in `app/services/budget.py`, the AI
> gate in `app/routes/ai.py`, model/provider wiring). Several parts also touch the paywall
> and `subscription_state()`, which is the single source of truth the whole access model
> rests on. **Shama has ratified the two product decisions in §0**, but no *code* is ratified:
> each M9-touching change below is still its own approved, dedicated commit per the rules in
> [MARQUEE_DECISIONS.md](../../MARQUEE_DECISIONS.md). This plan proposes a **new marquee entry
> (§0.2)** — the daily-allowance counter — that Shama has asked for; its text lands in
> `MARQUEE_DECISIONS.md` at implementation time.

---

## 0. Resolved decisions (Shama, 2026-09-05)

### 0.1 — Q0: Remove the trial. Free tier is permanent.

**Decision:** There is **no trial period**. A brand-new account lands directly on a permanent
**Free tier** and stays there as long as it likes, subject to the daily AI-request allowance
(the "daily guards"). The only other state is the **Paid** subscription (unlimited AI). This
is **Model A (freemium)**, taken to its conclusion — the trial concept is deleted, not merely
bypassed.

Consequences that ripple through existing code (all detailed in §2b):

- The **7-day-trial machinery is retired**: `ensure_trial_started()`, `trial_ends_at`, the
  trial countdown, `is_trial_expired()`, and the `trial_ending` lifecycle email all become
  dead or repurposed. New signups no longer start a clock.
- The **hard paywall lockout** in `frontend/app/(app)/_layout.tsx` is **removed** for the Free
  tier — a Free user always has app access; only *live AI calls past the daily allowance* are
  gated. (`has_access` effectively becomes always-true except for explicit account problems;
  see §2b for the exact `subscription_state()` rework.)
- Two visual **upsell** surfaces are added (Shama's explicit ask):
  1. a **banner on Home Base** for every Free-tier user, spelling out the incentives to
     upgrade (see §7.1);
  2. an **upgrade CTA inside the "you've used today's AI allowance" message** (see §7, state 3).

### 0.2 — Q0b: Meter in **number of AI requests**, and make the counter a marquee guard.

**Decision:** The allowance is denominated in **# of AI requests made to any AI provider —
now or in the future** (Gemini, Anthropic, and any provider added later). Dollars stay only as
an invisible global backstop (the $25/day circuit breaker); the **per-user** limit users see
and hit is a request count.

**New marquee entry (proposed — Shama asked for this explicitly):**

> **M10 (proposed) — the daily AI-request allowance counter.** Every code path that makes a
> call to *any* AI provider MUST route through the allowance check-and-count seam
> (`ai_allowance_state` / the request counter, §4). Adding, moving, or changing a provider
> call is a marquee event: it must be re-examined against this entry, because a new provider
> call that skips the seam is a **hole in the free-tier cap** — a Free user gets unmetered
> paid calls through the unguarded path, and the cap silently under-counts. This is the
> request-count sibling of **M9** (M9 guards *that* a call is paid; M10 guards *that a paid
> call is counted against the daily allowance*). A `# MARQUEE M10:` sentinel sits on the
> counting seam and on every provider call site.

This is recorded here as a *proposal* per the marquee rules (Claude proposes, Shama ratifies).
Shama's message ratifies the intent; the actual `MARQUEE_DECISIONS.md` entry + sentinel
comments are written in the implementation commit. Until then, treat M10 as governing this
work.

**Why a marquee guard is the right tool here:** the failure mode is invisible and delayed.
Today there are exactly **two** paid interactive surfaces — `POST /api/ai` and the on-demand
deadline check. The whole free-tier cap rests on *both* being counted. The day someone adds a
third AI-backed feature (a résumé rewriter, a new chat surface, a summariser) and wires it
straight to a provider the way the old `/api/messages` routes did, a Free user gets an
uncounted path and the cap leaks — exactly the class of bug M9 already exists to prevent, one
level over.

---

## 1. Goals & non-goals

**Goals**
- A durable Free tier metered to a **daily AI allowance**, resetting at **midnight UTC**
  (reuse the existing reset boundary — the message copy already promises it).
- A Paid tier with **unlimited** AI (subject only to the *global* circuit breaker and the
  per-IP/per-user rate limiter, which stay as anti-abuse, not as product limits).
- Observability: **how many users are on each tier**, **how many hit their daily cap**, and
  **how close the rest are** — on the admin console.
- Honest, non-punitive **user-facing** communication of remaining allowance, approaching the
  cap, and hitting it — consistent with the existing budget copy's deliberately
  non-accusatory tone (`app/services/budget.py:152`).
- Clean **fallback** everywhere: a capped student still has a fully working app; a Supabase
  blip still fails *open* (never locks out a paying user), exactly as the caps do today.

**Non-goals (this pass)**
- No new payment mechanics beyond what Stripe already does. (Stripe is still unconfigured per
  CLAUDE.md — see §11; the free tier is shippable *without* Stripe, the paid upgrade is not.)
- No per-feature pricing, no rollover/banking of unused allowance, no annual plan.
- No change to what any agent or prompt does (that's all M8/M9 and out of scope here).

---

## 2. How this maps onto what already exists

Good news: the spine is already built. This feature is mostly **re-pointing an abuse guard at
a product concept**, plus tracking and UX.

| Need | Already exists | What changes |
|---|---|---|
| Per-user daily spend, read back before a call | `over_user_budget()` / `user_spend_today()` (`app/services/budget.py`) | Make it **tier-aware**: skip for Paid; enforce a *user-facing* allowance for Free |
| The gate in the request path | `_live_branch()` → returns 429 with the allowance message (`app/routes/ai.py:316`) | Branch on tier; return a richer, structured payload the client can render |
| Who is paid vs not | `subscription_state()` → `status` / `has_access` (`app/core.py:41`) | Derive an `ai_tier` ∈ {`free`,`paid`} from it (§3) |
| Exemptions | `BUDGET_EXEMPT_USERIDS` (`app/config.py:193`) | Keep as the operator override; Paid tier becomes the *general* exemption |
| Global backstop | `circuit_open()` ($25/day) | Unchanged — still degrades *everyone* to mock on a billing incident |
| Per-user cost ledger | `user_costs` table + `record_user_cost()` (`app/core.py:418`) | Source of the "spend today" number; add a **limit-hit** signal alongside it |
| Metrics surface | Ops console Metrics + Cost-per-user views | Add tier counts + limit-hit counts (§6) |

The key reframing: **`over_user_budget()` stops being "is this account abusing us" and becomes
"has this Free-tier student used today's allowance."** That is an M9 change to a spend-cap path.

---

## 2b. Retiring the trial — the `subscription_state()` rework

Removing the trial (§0.1) is the highest-blast-radius part of this plan, because
`subscription_state()` (`app/core.py:41`) is *the* single source of truth for access, read by
both the client paywall and the server gate. Do this as its **own dedicated commit**, ahead of
the allowance work, with a full `test_subscription_gate.py` rewrite.

**New status model.** Replace the five-state trial model with:

| Status | Meaning | App access | AI tier |
|---|---|---|---|
| `free` | the default for every new account | **yes, always** | free (metered) |
| `active` | paying Stripe subscriber | yes | paid (unlimited) |
| `beta` | comped grant (`BETAUSER`) | yes, until `subscription_end_at` | paid |
| `canceled` | cancel-at-period-end | yes, until `subscription_end_at`, **then → `free`** | paid until period end |
| `past_due` | Stripe dunning | yes (as free) | free |

- **`free` is the new default** in `create_user()` (replaces `"trial"` at `app/core.py:813`)
  and the `subscription_status` column default. `trial_ends_at` is no longer written.
- **`has_access` is now true for `free`.** In today's `subscription_state()`, `trial` gates on
  `trial_ends_at`; the new `free` branch just returns `has_access=True`. The only `has_access
  == false` cases left are explicit account problems you *choose* to lock on (there may be
  none at launch — a `past_due` user staying on free-with-cap is friendlier and still applies
  pressure to fix their card). **Decide explicitly whether anything locks the app at all
  anymore** (open question §14).
- **A `canceled` subscription lapses to `free`, not to lockout.** When `subscription_end_at`
  passes, the account becomes a normal metered Free user — no lockout screen, ever.
- **Migration for existing accounts.** Everyone currently `trial` (including dateless
  pre-migration rows and expired trials) maps to `free`. Everyone `active`/`beta`/`canceled`
  is unchanged. This is a one-time `db/*.sql` step + a backfill of `subscription_status`; it
  **loosens** access (nobody who had access loses it), so it's safe to run before the client
  ships. Follows the house rule: `alter if not exists`, and the same create/alter discipline
  every schema file here documents.

**What gets deleted / repurposed (each noted so nobody re-derives it later):**

- `ensure_trial_started()` (`app/core.py:111`) — **deleted**. No clock to start.
- `is_trial_expired()` / `days_until_trial_end()` / `trial_ends_at_iso()` — deleted or reduced
  to whatever the `beta`/`canceled` countdown still needs (those run off `subscription_end_at`,
  not `trial_ends_at`, so the trial helpers go entirely).
- The client **trial countdown UI** and any "N days left in trial" copy — removed.
- **Lifecycle email — repurposed (Shama, Q5):** the `trial_ending` message (CLAUDE.md's
  three-email system) loses its trigger, and is **repurposed into a "you hit your daily limit"
  nudge** → go Unlimited. New `kind` (e.g. `limit_hit`), new `dedupe_key` (the UTC day the cap
  was hit, so a student who hits it repeatedly hears from us at a sane cadence — reuse the
  date-keyed dedupe pattern the trial email already used for extended trials). Fires off the
  `ai_limit_hit` signal (§3.3), throttled (e.g. at most once every few days, and honour the
  existing opt-out). `welcome`/`goodbye` unaffected. **The GitHub Actions sweep is already
  disarmed** (CLAUDE.md), so nothing auto-fires until re-armed — low-risk to build. Its own
  follow-up, not part of the core gate.

- **The first-session wall — the one Q4 risk to design around.** Everyone lands on Free with
  10 actions, and a new user's natural first flow burns them fast: build profile (1) → find
  matches (1) → add 3 opportunities (3) → check deadlines (1) = **6 of 10 before they've really
  started**, more if they chatted a lot. Hitting the cap *during onboarding* is the worst
  possible moment. **Recommendation: give the first day a higher allowance (or don't count the
  initial profile build).** Cheapest form: a `FIRST_DAY_AI_ACTIONS` (e.g. 20) applied when the
  account's `created_at` is today. This is an open decision (§14 Q1). Sybil exposure (permanent
  free × N throwaway accounts) is bounded in practice by the existing per-IP/per-email register
  limiters and the $25/day global breaker — acceptable pre-revenue, noted so it's a conscious yes.
- **Metrics:** "trial-to-paid conversion" becomes **"free-to-paid conversion"** (denominator =
  all free accounts, or free accounts that have hit the cap ≥ once — see §5). The "came back
  after signup" and activation-funnel metrics are unaffected (they never depended on the trial).
- **Terms/legal:** removing the trial and offering a permanent free tier is a material change
  to the offering — `legal/*.md` edit → `agents/build_legal.py` re-run → `TERMS_VERSION` bump.
  (CLAUDE.md notes Terms §3 *already* contradicts the paid plan by calling the beta free; this
  is the moment to fix that too.)

---

## 3. Data model & the `ai_tier` derivation

### 3.1 Deriving the tier (no new column needed for the tier itself)

Add a pure function next to `subscription_state()`:

```
ai_tier(record) -> "paid" | "free"
    paid  if subscription_state(record)["status"] in PAID_STATUSES
          (active; beta; canceled-but-still-in-paid-period)
    free  otherwise (free; past_due; a lapsed canceled row; unknown)
```

- **Do not store the tier** — derive it, for the same reason `provider_for_model()` derives
  provider instead of storing it (CLAUDE.md: a stored copy drifts out of step with the source
  of truth after one bad write). `subscription_state()` stays the single source of truth.
- `beta` grants count as **paid** (they're a comped paid experience) — matches how the metrics
  already treat beta as "converted-adjacent."
- With the trial gone (§2b) there is no `trial`/`expired-trial` case; the default account is
  `free` and resolves to the free tier directly.

### 3.2 The allowance: 10 pooled ACTIONS/day (Shama, 2026-09-05)

The user-facing allowance is **10 "actions" per day, pooled** across all AI-backed things a
student does. The count shown and gated is **actions**, not raw provider calls — but internally
every provider call is still counted (M10), and an action that fans out to several calls maps
to **1 shown/gated unit**. The dollar figure stays only as the invisible global backstop.

- `FREE_TIER_DAILY_AI_ACTIONS = 10` (new config, env-overridable; **anchor value, pre-revenue —
  will be tuned from §12 once there's real usage**). This is the number users see and hit.
- **`over_user_budget()` / `USER_DAILY_BUDGET_USD` is retired as the per-user gate.** The action
  count replaces it. The dollar idea survives only at the global level (`circuit_open()`,
  $25/day). Paid tier is exempt from the action cap; everyone stays under the global breaker.

**The metered actions (the pool).** Every one of these is a user-initiated thing that triggers
at least one billed provider call:

| Action | Underlying call(s) | Counts as |
|---|---|---|
| **Profile** (chat session + its closing synthesis) | `profileChatNextQuestion` per turn + `synthesizeProfile` + the derived-slot refresh (subjects, tags, enrichment, basics) — up to ~6 calls | **1** per completed profile update |
| **Find matches** | `rankCandidates` + `inferSubjects` | **1** per run |
| **Add opportunity to tracker** | `extractTrackerInfo` (per opportunity) | **1 each** |
| **Deadline "Check for updates"** (Quest Log refresh) | `check_one` fanned across tracked items | **1** per tap, regardless of item count |
| **Resume / LinkedIn import** | `_extract_profile_from_text` | **1** per import |

- **Shama's original list was profile-synthesis / find-matches / add-to-tracker.** The three
  added rows — the **chat conversation** (each bot turn is a live Claude call, not just the
  closing synthesis), the **deadline refresh** (one of the two paid interactive surfaces, and
  it fans out across the whole Quest Log), and **resume import** — were missing and would each
  be an uncounted or under-counted paid path. Folding chat+synthesis into one "profile" action
  and one refresh tap into one action keeps the count friendly while M10 still counts the calls.
- **What does NOT decrement:** anything served from cache or mock — a deadline check that hits
  the 7-day cache, cached profile-derived slots, opener questions served from the pool,
  mock-mode calls, and signed-out calls. Same exclusion list `user_costs.calls` documents. This
  is what keeps ordinary browsing and repeat visits free.
- **Regenerate openers / "Tidy it up" profile repair** are rare live calls — fold into the
  "profile" action (or count as 1); decide at build time, low volume either way.

**Counting source — and why it makes M10 robust.** We already write one `user_costs` row per
`(userid, day, surface, feature, model)` with a `calls` counter (`record_user_cost()`,
`app/core.py:418`). Provider calls are summed there; the **action count** is a light mapping on
top (fan-out actions collapse to 1). Add `user_actions_today(userid)` to `budget.py` mirroring
`user_spend_today()` (same cache, same fail-open, same midnight-UTC reset key). Whether the
action mapping lives server-side (preferred — one authority) or is derived from `feature` ids is
a build-time detail; the gate reads actions, the ledger keeps calls.

The important structural point: **the M10 counting seam *is* the existing cost-recording seam.**
Every paid provider call already must route its cost through `record_user_cost` /
`record_interactive_cost` — that is exactly what M9 already requires and what CLAUDE.md's cost
accounting is built on. So "count every provider call" reduces to "every provider call is
costed," which is already law here. M10 makes that law load-bearing for a *second* reason (the
free cap, not just the ledger) and puts a sentinel on it. A call that is costed is counted; a
call that isn't costed is the bug both M9 and M10 exist to prevent.

**Counting source — and why it makes M10 robust.** We already write one `user_costs` row per
`(userid, day, surface, feature, model)` with a `calls` counter (`record_user_cost()`,
`app/core.py:418`). The allowance is **`sum(calls)` for `(userid, today-UTC)`** — the
request-count analogue of `user_spend_today()`. Add `user_requests_today(userid)` to
`budget.py` mirroring `user_spend_today()` (same cache, same fail-open, same midnight-UTC reset
key). **No schema change required for counting.**

The important structural point: **the M10 counting seam *is* the existing cost-recording seam.**
Every paid provider call already must route its cost through `record_user_cost` /
`record_interactive_cost` — that is exactly what M9 already requires and what CLAUDE.md's cost
accounting is built on. So "count every provider call against the allowance" reduces to "every
provider call is costed," which is already law here. M10 makes that law load-bearing for a
*second* reason (the free cap, not just the ledger), and puts a sentinel on it so a future call
that skips `record_user_cost` is caught as the marquee violation it is. A call that is costed is
counted; a call that isn't costed is the bug both M9 and M10 exist to prevent.

### 3.3 New persistence: the limit-hit signal

Nothing today records "a student hit their cap" — `over_user_budget()` only logs a WARN and
returns a string. To answer "how many users are hitting their daily limits" we need a durable
signal. Two options:

- **Option 1 (recommended): reuse `user_activity`.** It already takes every authenticated
  request, buffers in memory, flushes every 30s (CLAUDE.md, `touch_user_activity`). Add a
  surface `ai_limit_hit`. A limit-hit becomes just another activity surface — DAU/limit-hit
  counts fall out of the same query, no new table, no new migration. Cost: it's approximate
  (a crash between flushes loses ≤ one interval), which is fine for a *count of who hit the
  cap* — the same tolerance the activity table already documents.
- **Option 2: a dedicated `ai_limit_events` table** (`userid`, `day`, `tier`, `requests_used`,
  `first_hit_at`). Exact and queryable, but it's another one-time manual DDL step (the pattern
  every schema file here follows) and another fail-open path. Overkill for v1.

Go with **Option 1** for v1; revisit Option 2 only if we later want per-hit timing.

> **Migration note.** If any counter or column *is* added, it follows the house rule every
> `*_schema.sql` here follows: `create/alter if not exists`, and **a column added to a CREATE
> must also be added to the ALTER block**, or PostgREST 400s the whole insert and the feature
> reads as "nobody used it" instead of "every write failed."

---

## 4. Backend: the gating logic

**All of §4 is MARQUEE M9.** It changes what happens on a paid-call path.

### 4.1 Tier-aware `over_user_budget` / a new `ai_allowance_state`

Replace the single dollar check with an allowance resolver that the route can both **gate on**
and **report** (the client needs "X of Y left" even on a *successful* call, to render the
counter). Proposed shape in `budget.py`:

```
ai_allowance_state(userid, record) -> {
    tier: "free" | "paid",
    unlimited: bool,               # true for paid
    used: int, limit: int,         # requests today / daily allowance (free only)
    remaining: int,
    over: bool,                    # used >= limit
    dollar_backstop_hit: bool,     # free-tier $ ceiling tripped (rare)
    reason: str | None,            # the message to show if blocked
}
```

- **Paid:** `unlimited=true`, `over=false`, always. Never reads `user_costs` for a gate (one
  fewer Supabase round-trip on the hot path for paying users — a nice side effect).
- **Free:** reads `user_requests_today()` (cached, fail-open) and compares to the allowance;
  also consults the dollar backstop. `over` if *either* trips.
- **Fail-open preserved:** if the count can't be read (`None`), treat as **not over** —
  identical to today's `_sum_cost() is None → don't block`. A DB blip must not lock a Free
  student out mid-session, and *must not* wrongly lock out anyone. Documented deliberately.
- **Operator override:** `BUDGET_EXEMPT_USERIDS` still forces `unlimited` (demos, support).

### 4.2 The request-path change in `app/routes/ai.py`

`_live_branch()` (`app/routes/ai.py:316`) currently: circuit → per-user budget → live. New
order, same spirit:

```
if not key_configured: return mock
if budget.circuit_open(): return mock            # global backstop, unchanged
allowance = budget.ai_allowance_state(userid, record)
if allowance.over:
    return refuse(429, allowance.reason, meta=allowance)   # structured, not a bare string
return live   # and the response echoes `allowance` so the client can update its counter
```

- **The 429 stays a 429** but its **body gains structure** (`tier`, `used`, `limit`,
  `remaining`, `reset_at`) so the client can render a real "you've hit today's limit, resets
  in 6h, upgrade for unlimited" state instead of parsing a sentence. Keep the existing
  human string as `reason` for backward-compat with any client that only reads text.
- **Successful calls also carry the allowance** in the response envelope (a new optional
  `meta.allowance` block; `_envelope()` at `app/routes/ai.py:133`). This is what lets the
  counter tick down live without a second request. Backward-compatible: old clients ignore it.
- **The deadline endpoint** (`app/routes/opportunities.py`) is the *other* paid interactive
  surface and must consult the same `ai_allowance_state` — otherwise a capped Free student
  still burns money on forced deadline re-checks. It already has the `forced_recheck` cooldown;
  the allowance sits **in front** of it. A cached/unverified deadline hit costs nothing and
  does **not** decrement (it makes no API call — same rule as everywhere).
- **Record the limit-hit** (`touch_user_activity(userid, "ai_limit_hit")`) at the moment
  `allowance.over` first blocks a call, so §6's counts have a source. Fire it at most once per
  user per day (the activity buffer dedups naturally by surface+day if we keep it a boolean
  "hit today").

### 4.3 What stays exactly as-is

- The **auth gate** `_ai_access_error` (`app/routes/ai.py:275`) **changes**: with no trial and
  no lockout, a signed-in Free user is never "lapsed," so the **402 path largely disappears** —
  a signed-in account falls through to the Free allowance (429 when over), and 401 still covers
  a live-key call with no token. This is the most important behavioral flip and needs its own
  careful commit + a `test_subscription_gate.py` rewrite (it asserts the current trial/lapsed
  wiring route-by-route). Confirm which routes, if any, still 402 at all after §2b.
- The **global circuit breaker** ($25/day) and the **per-IP / per-user rate limiter**
  (30/min) are anti-abuse, not product tiers — unchanged, and they apply to Paid too.
- **Mock mode** stays fully reachable signed-out and offline — the app stays
  click-through-able with no keys (a standing CLAUDE.md constraint). The allowance only ever
  gates the *live* branch.

---

## 5. Tracking — how many users on each tier, how many hit the cap

Everything here is **read-side** and localhost-only (the metrics routes already are; the
payload is a roster of minors — CLAUDE.md is emphatic: no export button, don't expose the route).

- **Users per tier (state metric).** `ai_tier(record)` over the current `users` table. This is
  a *point-in-time* count and needs **no migration** — it's computed from state, like the rest
  of the activation funnel. Bucket: `paid` (active/beta/canceled-in-period) vs `free`
  (trial / expired-trial / past_due). Report alongside the existing plan-status breakdown so
  they reconcile.
- **Daily-cap hits (event-ish metric).** From `user_activity` surface `ai_limit_hit` (§3.3):
  **count of distinct Free users who hit the cap on `latest_day`**, plus a small time series.
  Use `latest_day` — the most recent day *with* activity — never "today", for the exact reason
  the Cost-per-user tab already does (UTC day rolls at 5pm Pacific; a "today" figure reads
  `$0`/`0` every evening and looks like a dead pipeline).
- **Approaching the cap (leading indicator).** From `user_costs.calls` summed per user for
  `latest_day`: histogram of Free users by **% of allowance used** (e.g. buckets 0-50 /
  50-80 / 80-99 / 100%). This is the number that tells you whether the allowance is set right
  *before* people start hitting it — the single most useful tuning signal.
- **Conversion pressure.** Cross-tab: of Free users who hit the cap ≥ N days in the window,
  how many upgraded? This is the freemium funnel's money question and reuses the existing
  trial-to-paid plumbing (`stripe_subscription_id` present = converted).

All of these are **decompositions of data we already write** (`users`, `user_activity`,
`user_costs`) — no new ledger, consistent with the rule that "two places computing the same
dollars is how the two drift."

---

## 6. Admin dashboard changes (`ops/admin_console.html`)

The console has five top-level views (`showView()`); this adds to two and possibly a card.

- **Metrics view (default):** a new **"AI tiers"** card, sitting beside the activation funnel:
  - Two big tiles: **Free** count and **Paid** count (with the `n/d` raw fraction the page
    favors over bare percentages at this account size).
  - **"Hit their daily cap today"**: count + a 14-day sparkline (from `ai_limit_hit`).
  - **Allowance-usage histogram** for Free users (the 0-50/50-80/80-99/100% buckets).
  - Each tile is **click-through to the roster** it represents (`missing_userids`-style), so
    "who are the 4 people hammering the cap" is one click — the page's established pattern.
  - Colors **fixed per tier** (paid = accent, free = neutral), never positional — same rule
    the provider/plan-status colors already follow, or a tier overtaking the other swaps hues
    mid-session and the chart reads backwards.
- **Cost-per-user view:** add a **tier column / filter** to the per-user table (it's already
  seeded from the `users` roster, so every account appears even at $0 spend). Lets you answer
  "which Paid users cost more than $9.99" (the loss-per-head question that tab exists for) and
  "which Free users are pinned at the cap" separately.
- **A tuning readout:** show the current `FREE_TIER_DAILY_AI_REQUESTS` and the measured
  **median/p90 requests-per-active-Free-user** right next to it, so setting the allowance is a
  data decision, not a guess — mirrors the `USER_DAILY_BUDGET_USD` "tune to ~5× median" note.
- **New endpoint:** extend `GET /api/agents/metrics` (or a sibling
  `GET /api/agents/ai-tiers`) with the tier counts, cap-hit counts, and the usage histogram.
  Localhost + `X-Ops-Token`-gated like every other `/api/agents/*` route; degrades to a setup
  notice if `user_activity` isn't migrated (same pattern as `activity_ready`/`snapshots_ready`).

---

## 7. User-facing UI — informing users

Tone rule, inherited: **non-punitive.** The existing cap copy deliberately reads as "you used
the app hard," never "you're an abuser" (`app/services/budget.py:152`). Keep that everywhere.

### 7.1 The Home Base upsell banner (Shama's explicit ask)

A persistent but dismissible **banner on Home Base** (`frontend/app/(app)/index.tsx`), shown to
**every Free-tier user**, that spells out the incentives to upgrade — not a nag, a value pitch:

- **Content:** the concrete deltas, e.g. *"You're on the Free plan — 15 AI actions a day.
  Wingman Unlimited: unlimited profile chats, unlimited match-finding, unlimited deadline
  re-checks. $9.99/mo."* Name the *specific* AI things they do most (profile chat, Fresh Finds
  matching, deadline checks), because those are what the cap actually touches — a generic
  "upgrade for more" converts far worse than "the thing you just did, without limits."
- **Behavior:** dismissible for the session, but returns on a later visit (a Free user is a
  standing upsell target); **escalates** as they hit the cap more — a first-time Free user sees
  a soft version, someone who's hit the cap ≥ N days sees a stronger "you keep running out"
  variant (drive off the `ai_limit_hit` history from §3.3). Hidden entirely for Paid.
- **CTA** routes to Manage Plan (§8). Reuses BENTO & POP banner styling — no one-off component.
- **State source:** the `ai_tier` + allowance snapshot already on the login/refresh payload
  (§8), so the banner needs no extra request to know which variant to show.

### 7.2 The four in-flow allowance states

All driven by the `meta.allowance` block the API now returns:

1. **Ambient remaining-allowance indicator (Free only).** A small, quiet chip — e.g. near the
   chat/AI entry points on My Vibe and Fresh Finds: **"12 of 15 AI actions left today."** Not a
   scary meter; a light counter that only draws attention as it gets low. Hidden entirely for
   Paid (no meter = the *feeling* of unlimited).
2. **Approaching the cap (≤ ~20% left).** The chip warms up (color shift, not a modal) and
   gains a one-line "resets at midnight UTC · [Go unlimited]". No interruption — they can still
   use their remaining actions.
3. **Cap reached.** The AI action is blocked *gracefully at the point of use* (the chat send /
   the "find matches" button), with an inline card:
   - what happened ("You've used today's AI actions"),
   - **when it resets** (render `reset_at` as a live "resets in 6h", not just "midnight UTC" —
     a countdown is far less frustrating than a bare timezone),
   - **what still works** (browse, track, calendar, everything non-AI — see §9),
   - **the upgrade CTA** ("Go unlimited — $9.99/mo").
   - Reuse the existing `reason` string as the fallback text.
4. **Paid confirmation.** After upgrade, a lightweight "You're on Wingman Unlimited — no daily
   AI cap" once, then the meter simply never appears again.

Client plumbing:
- `httpClient` already centralizes 402→paywall handling in one place; add **429-with-allowance
  handling** in the same spot — flip a cached `allowanceState`, fire an `onAllowanceChanged`
  event, and let a context (like `AuthContext`) turn it into UI. The counter updates without a
  reload, exactly as the 402 paywall flip does today.
- The block must arrive **without a reload** and lift **without a reload** the moment the
  student upgrades (upgrade → `subscription_status: active` → `ai_tier: paid` →
  `subscriptionStatus()` refresh clears the block through the same notify path that lifts the
  402 today).
- **Design source of truth:** the "BENTO & POP" system in `frontend/src/ui/` (`theme.ts`
  tokens, `components.tsx`). New chips/cards use existing tokens — no new one-off styling.

---

## 8. Profile / My Vibe dashboard changes

"My Vibe" (`frontend/app/(app)/profile.tsx`) and Manage Plan
(`subscription.tsx`) are where tier lives for the user:

- **A tier badge** on My Vibe: **"Free plan"** / **"Wingman Unlimited"**, with the daily
  allowance and today's usage for Free ("12 of 15 AI actions used today · resets in 6h").
- **Manage Plan** becomes a real comparison, not just a trial countdown:
  - A **two-column Free vs Unlimited** table — Free: *N AI actions/day, full catalog, tracking,
    calendar*; Unlimited: *everything, no daily AI cap* — so the value of paying is legible.
  - With the trial gone, this screen replaces today's dead-end "your trial ended, and Stripe
    is unconfigured so you can't pay" (CLAUDE.md's "one sharp edge") with a **permanent working
    free experience** + an upgrade path. That's a strict improvement even before Stripe is wired.
  - Keep the **`grant` promo path** (`BETAUSER`) — it already flips a user to `beta` = Paid
    tier, so it "just works" as a comp-to-unlimited lever with no new code.
- **`subscription_state()` payload** already reaches the client on every login/refresh; extend
  `_login_payload()` (`app/core.py:93`) to include `ai_tier` and (for Free) the allowance
  snapshot, so the profile can render tier without an extra call. Additive, backward-compatible.

---

## 9. Fallback paths (the "what still works / what if X fails" matrix)

The whole design is built so **no failure ever produces a locked or lying app.**

| Situation | Behavior |
|---|---|
| Free user hits daily cap | AI actions blocked *at point of use* with reset countdown + upgrade CTA. **Everything non-AI keeps working** — browse catalog, track/save, Quest Log, calendar sync, deadlines *from cache*, mailing-list signup. |
| Cached AI result available while capped | Serve it. Cached deadline checks, cached profile-derived slots, and mock-safe paths cost nothing and must **not** decrement the allowance or be blocked. |
| Supabase unreadable (can't read `user_costs`) | **Fail open** — treat as under cap, allow the call. Identical to today's `_sum_cost() is None → don't block`. A DB blip must never lock out a paying user *or* a Free user mid-session. |
| Global circuit breaker open ($25/day) | **Everyone** (Free *and* Paid) degrades to mock, no error — a billing incident yields a dumber-but-working app. Unchanged. Paid users see a soft "AI is briefly unavailable" note, not a cap message. |
| No API key configured (offline/dev) | Mock mode for all; allowance never gates the mock branch; app fully click-through-able. Unchanged CLAUDE.md guarantee. |
| Stripe unconfigured (today's reality) | Free tier ships and works with **zero** Stripe. Upgrade CTA surfaces whatever the unconfigured backend answers (or is hidden until Stripe is live). Grant promo (`BETAUSER`) is the working path to Paid meanwhile. |
| `user_activity` not migrated | Limit-hit tracking degrades to a setup notice on the console (like `activity_ready`); gating still works (it reads `user_costs`, not activity). |
| Clock/reset boundary | Allowance resets at **midnight UTC**, reusing the existing cache-day key that already re-reads from zero after midnight (`_cached_total`'s day-stamped entry). The user-facing copy already promises exactly this. |
| A `canceled` subscription lapses | Account becomes a normal Free user — metered, never locked out. No trial, no paywall wall. |

---

## 10. Edge cases & failure modes to get right

- **Double-decrement / racing at the boundary.** Two AI calls firing near the cap must not
  both slip through *and* must not both count as a hit. The allowance read is cached +
  `note_spend()`-bumped in-process (already), and the per-user rate limiter (30/min) bounds
  overshoot inside a cache window — same bound the dollar cap relies on today. Accept a *small*
  overshoot (fail-open philosophy) rather than a hard atomic gate; document it, as the budget
  module already documents its overshoot bound.
- **A single "action" that makes multiple provider calls — the sharp edge of Q0b.** Your
  decision meters "# of AI requests to any provider," and by M10 every provider call is counted.
  But one *user action* can be several provider calls: a profile synthesis is 3-5 (CLAUDE.md's
  profile-chat flow), and one profile update is ~5 (subjects + tags + enrichment + basics +
  opener). If the allowance is literally provider-calls, **one "send" could eat a third of a
  15/day allowance**, which reads as broken to the student. Two ways to honor "count every
  provider call" without that: (a) keep the internal counter as provider-calls (M10-clean) but
  **display and gate on user-actions** (map a fan-out action to 1 shown unit) — friendliest;
  (b) set the allowance high enough that provider-call granularity feels generous. **This is now
  the top open question (§14 Q1)** — it's the one thing your two answers left genuinely
  ambiguous, and it changes both the number and the copy.
- **Mid-session upgrade / downgrade.** Upgrade lifts the cap without reload (§7). A
  cancel-at-period-end user is Paid until the period ends (`subscription_state` already), then
  lapses to Free — metered, never locked out. A clean downgrade.
- **Signed-out / unattributed calls.** Never decrement anyone's allowance (no userid), same
  residual the cost attribution already reports as "unattributed."
- **Copy that can't lie.** "Resets in 6h" must be computed from the real UTC boundary, not a
  fixed string; a wrong countdown is worse than none.

---

## 11. Product / monetization implications

- **The funnel is now browse-forever → hit-cap → pay.** The old trial→pay-or-leave funnel is
  gone. The **cap level is the price lever**: too high and nobody upgrades, too low and it feels
  hostile to a student who legitimately explored hard on a Sunday. The Home Base banner (§7.1)
  and the cap-message CTA are the two conversion surfaces; §6's usage histogram tells you
  whether the cap is set right *before* people start hitting it.
- **Cost exposure goes up** — Free users now have a *permanent* (if capped) paid-call budget,
  where today an expired trial cost $0. The global circuit breaker ($25/day) and the Free
  per-user allowance bound the total; model the worst case (`free_users × allowance ×
  per-request cost`) against the circuit breaker before setting the allowance. **This is the
  main risk of removing the trial** and the reason step 1 of §13 (measure before enforcing)
  matters — a permanent free tier with a too-generous cap is a standing bill.
- **Stripe is still the gating dependency for revenue.** The free tier + the whole upsell UX is
  shippable now with zero Stripe; the *upgrade itself* is not real until
  `STRIPE_API_KEY`/`STRIPE_PRICE_ID` are set (CLAUDE.md). Until then the `BETAUSER` grant is the
  only working path to Paid — fine for testing the tiers, not for revenue. **Configure Stripe
  before promoting the upsell**, or the banner leads to a dead button.
- **Terms/legal — now required, not optional.** Removing the trial and offering a permanent
  free tier is a material change to the offering: `legal/*.md` edit → `agents/build_legal.py`
  re-run → `TERMS_VERSION` bump (the repo's standing rule). Also fixes the existing
  contradiction CLAUDE.md flags (Terms §3 still calls the beta free).

---

## 12. What must be measured before setting numbers

Do **not** hardcode the allowance from intuition — the repo's own history ($0.50 placeholder,
under-quoted preview costs) is a warning about guessed cost numbers. Before launch, read off
the Cost-per-user tab:

- **Median and p90 billed AI requests/day** per active user today (that's the allowance
  candidate — set the free cap near the median so typical use rarely hits it, and let heavy
  users feel the pull to upgrade).
- **Mean $/request** across Gemini and Claude (they differ), to translate the request
  allowance into a dollar backstop and to model aggregate exposure.
- **Current distribution of `sum(calls)` per user/day** — this *is* the histogram §6 draws;
  building it first tells you where to put the line.

Put the chosen `FREE_TIER_DAILY_AI_REQUESTS` in `app/config.py` as an env-overridable constant
(every cap here already is), so tuning is an env change, not a deploy.

---

## 13. Suggested sequencing (each M9/M10 step its own approved commit)

1. **Instrumentation first (no gating, no risk).** Add `ai_tier()`, `user_requests_today()`,
   the console tier counts + usage histogram, and the `ai_limit_hit` surface — but **do not
   enforce** anything yet. Watch real usage for a week. *(Read-only; touches the metrics payload
   — low risk, and it's how you set the number in step 3.)*
2. **Retire the trial — `subscription_state()` rework (§2b), dedicated commit.** New `free`
   default, `has_access` always-true for free, migrate existing `trial` rows to `free`, delete
   the trial helpers, rewrite `test_subscription_gate.py`. This *loosens* access (nobody loses
   it), so it's safe to land before enforcement. Removes the paywall lockout.
3. **Set the numbers** from step 1's data (§12) — and resolve Q1 (action vs provider-call unit).
4. **Tier-aware gate — M9 + the new M10, dedicated commit.** `ai_allowance_state()` + the
   `_live_branch()` / deadline-endpoint changes + structured 429 + the M10 sentinel on the
   counting seam and every provider call site. Add allowance tests. Ship **behind a flag / high
   allowance** first.
5. **User-facing UI.** Home Base upsell banner (§7.1), meter chip, cap card with upgrade CTA,
   Manage Plan Free-vs-Unlimited comparison, My Vibe badge.
6. **Legal + Stripe.** Terms update (remove trial, describe free tier) + `TERMS_VERSION` bump;
   configure Stripe so the upgrade CTA is real; decide the `trial_ending` email's fate (§2b).

Steps 1 and 2 are safe and high-value on their own; if the rest stalled you'd still have shipped
the tier/limit observability you asked for *and* fixed the trial-lockout dead-end.

---

## 14. Open questions for Shama

**Resolved (2026-09-05):** unit = **user-actions**, 10/day pooled (Q1); anchor = **10**, tune
later (Q2); **AI-only** (Q3); **no trial, everyone starts Free** (Q4); `trial_ending` email
**repurposed to a limit-hit nudge** (Q5); permanent-free exposure **accepted** (Q6).

**Still open:**

1. **First-day allowance?** The one thing Q4 leaves unsettled (§2b). A new user can burn ~6 of
   10 actions in their first onboarding pass, hitting the wall at the worst moment. Recommend a
   `FIRST_DAY_AI_ACTIONS` boost (e.g. 20 on the signup day) or exempting the initial profile
   build. Yes/no, and the number.
2. **Chat/repair granularity confirm.** Plan folds a whole profile-chat *session* (every bot
   turn + closing synthesis) into **1** action, and one Quest Log "Check for updates" tap into
   **1** action however many items it refreshes. Confirm that's the intent (it's the friendly
   reading; the alternative — counting each chat turn / each item — is harsher and I don't
   recommend it).
3. **Stripe before promoting the banner.** With everyone on Free, "hit cap → Go Unlimited" is
   the default path and it's a dead button until `STRIPE_API_KEY`/`STRIPE_PRICE_ID` are set.
   Ship the banner/CTA gated (or `BETAUSER`-only) until Stripe is live?

---

*No code has been written. Q0 (remove the trial, permanent free tier) and Q0b (meter by AI-request
count; guard it as marquee M10) are resolved by Shama, 2026-09-05. Everything else is a discussion
starting point for the MARQUEE M9 / M10 / paywall changes it describes; per repo rules,
implementation waits on an explicit "yes" and each paid-call-path change lands as its own
dedicated, named commit — and M10's `MARQUEE_DECISIONS.md` entry + sentinels are written in the
commit that first ships the allowance counter.*
