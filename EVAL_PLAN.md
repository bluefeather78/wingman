# Eval pipeline — plan

*Started 2026-08-30. Status: **DESIGN / NOT BUILT**. This is a planning document only — no
`evals/` package exists yet. It is self-contained: a fresh session can pick it up with no other
context.*

*The one-line thesis: **wingman already runs evals by hand — this systematizes them.** The
goal is NOT to adopt a framework. It is to unify the four eval-shaped things already in the
repo (`matching_eval.py` + three `grade_*.py` scripts) onto one skeleton, add golden datasets
as first-class assets, cover the untested paid surfaces, and gate the free layers in CI —
without sending any student data to a third party.*

---

## Decisions locked before any building — READ THIS FIRST

Three questions get asked every time someone says "let's add evals," and for wingman they are
already answered by the repo's existing posture. Do not re-litigate them without a reason.

1. **No LangChain.** LangChain is an *orchestration* framework; this repo talks to Gemini /
   Anthropic over raw HTTP with no SDK, on purpose (the stdlib-only philosophy). Evaluating
   production through a *different* call path than production uses would invalidate the eval.
   The eval harness MUST call the real `gemini_common.call_gemini` / `claude_common` code path,
   never a re-implementation. This is the same rule the existing `grade_url_truth.py` follows
   ("run the REAL `scrape_opportunities.resolve_url_truth`").

2. **No hosted eval platform (LangSmith / Braintrust / hosted promptfoo).** They are the best
   dashboards and the worst privacy fit. Their model is: ship traces — which here contain
   **student profiles and prompts, for a user base that is largely minors** — to a third party.
   That contradicts the posture the whole app is built on (why Mailchimp/Loops were rejected
   for lifecycle email; why `/api/agents/*` and `/api/metrics` are localhost-only; why
   `SUPABASE_SERVICE_KEY` never reaches the client). If a dashboard is ever wanted, it is
   **another admin-console tab reading local JSON reports** — the same muscle the agents card
   already uses — not an external service.

3. **Every eval is FREE by default; the paid `--run` is opt-in and cost-quoted.** Same three
   tiers the agents already use (`--preview` / `--dry-run` / live). Scoring is pure and free;
   only the live-model arm costs money, and it names its cost before spending, exactly like
   `estimate_agent_cost()`. `matching_eval.py` already does this (`--list` free, `--run` paid).

**promptfoo is the one external tool that MIGHT earn a place — later, narrowly.** It runs
locally (no trace upload), is provider-agnostic, and can shell out to a Python provider so it
still calls the real `call_gemini`. It is genuinely good for *matrix prompt A/B* ("does prompt
v2 beat v1 across 30 cases, side by side"). Adopt it ONLY if that specific need appears; it is
never the backbone. Everything below is in-house.

---

## What already exists (the pipeline is 60% built and doesn't know it)

The plan is mostly "generalize these four, then extend." Naming them so nothing gets rebuilt:

| Asset | What it is | Layer (below) | Cost |
|---|---|---|---|
| `matching_eval.py` | Phase-7 eligibility eval: 9 crafted labeled cases, pure `score_eligibility()`, asymmetric over/under-exclusion scoring, `--list` free / `--run` paid | 3 + 4 | paid arm opt-in |
| `grade_scraper_batch.py` | replays a scraper decision policy vs. frozen human verdicts; scores wins vs. REGRESSIONS (bar = 0) | 2 | free |
| `grade_url_truth.py` | live-HTTP validation of URL-truth resolution vs. the frozen 08-23 batch | 2 (live-HTTP) | free |
| `grade_mailing_lists.py` | precision/recall of mailing-list discovery; `--sample`/`--worksheet`/`--score`/`--verify` | 3 | `--verify` sends real mail |
| `tests/fixtures/scraper_grading_20260823.json` | 166 rows, every one hand-adjudicated — the first frozen golden set | dataset | — |
| `tests/fixtures/pair_resolution_20260826.json` | frozen dedupe-pair labels | dataset | — |
| `tests/unit/test_matching_eval.py`, `test_grade_scraper_batch.py` | pin the pure scorers hermetically | 1 | free |
| `tests/conftest.py` | autouse network-block — the hermeticity guarantee the free layers need | infra | free |

Deterministic verifiers that already do in *code* what most teams pay an LLM-judge for, and so
need only Layer-1 pinning, never a judge: `page_text.claim_is_supported`,
`page_text.quote_is_on_page`, `url_validate.domain_matches_org`, `deadline_write_decision`,
`action_items_write_decision`.

**The gap is not capability — it is that the four share a skeleton nobody factored out**, there
is no dataset directory, no CI gate on the free layers, and the paid live-model surfaces (search
rate, deadline accuracy, ranking relevance) are measured only in ad-hoc chat sessions, never on
demand.

---

## The taxonomy this plan uses (four layers, different tools each)

| Layer | Question | wingman example | Cost | Runs |
|---|---|---|---|---|
| **1. Verifier / unit** | Does the deterministic guard behave? | `claim_is_supported("Algebra 2", nyu_page)` → drop | free, hermetic | CI, every push |
| **2. Golden-dataset regression** | Would a prompt/policy change lose good outputs? | replay policy vs. `scraper_grading_20260823.json` | free (frozen) | CI, every push |
| **3. Metric tracking (live-model)** | Is the *model* still doing its job? | search-rate (prose vs JSON), demotion rate, over/under-exclusion | paid | operator-triggered |
| **4. LLM-as-judge** | Grade quality where there is no ground truth | "does this ranking's *why-it-fits* actually fit?" | paid + must self-validate | operator-triggered |

Layers 1–2 are the CI gate and must stay green. Layer 3 is a deliberate paid run, like an agent.
Layer 4 is the one to be most skeptical of — see Phase 3.

---

## Phase 0 — Unify (FREE, no model calls, fully reversible)

Create an `evals/` package and port the four existing evals onto one skeleton **without changing
their logic**. Pure refactor; the existing tests must stay green throughout.

- `evals/common.py` — shared plumbing mirroring `agent_common.py`:
  - dataset load/validate (JSONL: `input`, `gold`, `provenance`, `labeled_at`)
  - a report shape (`{overall, per_dimension, cases:[{id, gold, pred, correct}]}`) — the shape
    `score_eligibility()` already returns, generalized
  - a CLI harness with `--list` / `--preview` / `--run`, so every eval speaks the agents' dialect
  - a cost quote for `--run`, averaged from real history the way `preview_agent()` does
- Port `matching_eval.py` → `evals/eligibility.py` (keep the module name as a shim if anything
  imports it; check first).
- Port the three `grade_*.py` → `evals/scraper_decision.py`, `evals/url_truth.py`,
  `evals/mailing_lists.py`. Keep the root scripts as thin shims if the admin console or memory
  notes reference them by name (they do — verify before moving).
- Wrap every frozen-fixture scorer as pytest under `tests/unit/` so Layer-2 gates CI.

**Deliverable:** one skeleton, four evals on it, `cd . && python -m pytest` green, no behavior
change. **Cost: $0.**

---

## Phase 1 — Golden datasets as first-class assets (FREE)

- `evals/datasets/*.jsonl` — one file per surface. Seed from what is already frozen
  (`scraper_grading_20260823.json`, `pair_resolution_20260826.json`, the 9 crafted eligibility
  cases).
- **The append-only rule, stated once:** a labeled case is **never deleted, only added to** —
  same discipline as the snapshot files and the `email_sends` claim table. A case that stops
  passing is a signal, not a case to remove.
- Every case carries provenance (`crafted` vs `catalog:<row_id>`) and a `labeled_at` date, so a
  future reader can tell a synthetic control from a real adjudicated row.

**Deliverable:** `evals/datasets/` with seed data and a documented labeling convention. **$0.**

---

## Phase 2 — Cover the untested paid surfaces (Layer 3, PAID `--run`)

Each of these is a number already reasoned about informally in CLAUDE.md; this turns it into a
command. Build in priority order once Shama picks the surface that hurts most.

- **Search-rate eval** — the prose-vs-JSON finding (`gemini_common.py`'s SEVENTH finding) as a
  repeatable searches/row per agent, so a prompt edit can't silently kill search. This is the
  single highest-value metric because a silent-search regression is invisible and expensive.
- **Action-item demotion / drop rate** — the run-62/63 hand-grade (31% → 3% demotions) as
  `python -m evals.action_items --run --sample N`. Watches the exact signal that told you the
  first shipment was bad even though verification passed.
- **Deadline extraction** — extracted dates vs. a labeled set; the today-anchoring bug and the
  missing-opens gap as explicit assertions.
- **Ranking / curation relevance** — golden profiles → expected top-K opportunities; grows the
  `matching_eval.py` seed set from 9 crafted cases toward real-catalog labeled rows.

**Deliverable:** per-surface `evals/<surface>.py` with `--preview` cost quote and JSON report.
**Cost: per-run, quoted, operator-triggered — never in CI.**

---

## Phase 3 — LLM-as-judge, carefully (Layer 4, PAID)

Only for genuinely open-ended quality with no ground truth (ranking's "why it fits" prose;
profile-synthesis quality). **The rule that makes this safe:** a judge is itself a model call
and **must be validated against human labels before it is trusted** — measure the judge's own
agreement with a human on a labeled sample, and report it, before using its verdicts. An
unaudited paid judge is exactly the "model asserting a fact nothing checked" failure the
action-items subsystem exists to prevent. If the judge can't beat a coin flip against human
labels, it doesn't ship. Prefer extending a *deterministic* verifier over adding a judge
wherever the ground truth is checkable in code.

**Deliverable:** at most one validated judge, with its human-agreement number published beside
every score it produces. **Do not build speculatively.**

---

## Phase 4 — CI gate + admin console card (mostly FREE)

- **CI:** Layers 1–2 run on every push (free, must stay green). A regression in a frozen golden
  set fails the build — the bar is zero, matching `grade_scraper_batch.py`'s existing stance.
- **Console:** a Layer-3 eval gets a card like the agents — `--preview` cost estimate, a
  deliberate trigger, JSON report on disk, **no auto-run**. It reads local report files; it does
  not call a hosted service.

**Deliverable:** green CI gate + one console card reading local JSON. **CI is $0; console runs
are the same quoted paid runs as Phase 2.**

---

## Open decisions for Shama

1. **Which surface does Phase 2 lead with** — search-rate, action-item honesty, deadline
   accuracy, or ranking relevance? (Recommendation: search-rate first — invisible regression,
   highest blast radius, cheapest to measure.)
2. **Is promptfoo wanted at all**, or stay fully in-house? (Recommendation: in-house until a
   concrete matrix-A/B need appears.)
3. **Does this become its own branch + eventual merge**, or live alongside the
   opportunity-matching work? (Phase 0 touches no `app/` / `frontend/` / `render.yaml` files, so
   it is a safe standalone branch — a deploy no-op.)

---

## Guardrails, restated (the two that this whole plan hangs on)

- **The eval calls the real production code path.** Never a re-implementation, or the eval and
  production drift and the number lies.
- **Free by default; paid arm opt-in and cost-quoted.** Scoring is pure. Only `--run` spends,
  and it quotes first.
