# Discovery Engine Plan

**Thesis.** The valuable problem is not "search for internships." It is: *discover opportunities
that were never designed to be found by high schoolers.* Seattle proves it — Seattle Children's
research training, Allen Institute programs, Port of Seattle internships, tiny-nonprofit youth
councils — all real, all local, none of them findable by searching `"high school internship
Seattle"`, because they live on unrelated sites and speak a different language ("youth council,"
"career quest," "research training," "student researchers"). 

> Google searches *pages*. Wingman searches the *local opportunity ecosystem*.

Wingman should be an **opportunity-discovery engine, not a search engine**, and the differentiator
is not *more listings* (a Seattle competitor already claims 900+) — it is **better discovery +
honest confidence + genuinely obscure local finds.**

---

## What already exists (so this is an extension, not a rebuild)

Roughly 40% of the vision is already built and load-bearing:

- **Org-first discovery** — `local_org_discovery.py` (this branch): NL request → region brief →
  archetype×region Gemini search → grounded, deduped local-org candidates. Bay Area → 19 real orgs
  across 5 archetypes for ~$0.27, application-based-only.
- **Web-graph exploration** — `walk_up_hubs.py` + `mine_hub_pages.py` + `discovered_leads.jsonl`.
  The system already walks row→parent-index→children and hub→programs, with **deliberate caps** so
  it "can never wander into crawling the open web."
- **Dead-opportunity / recurrence** — `check_deadlines.py` projects a prior cycle's dates onto the
  current one (`was_estimated`, the opens→deadline interval ladder), and the app labels them
  "Predicted dates from past cycle." Seattle Children's "check back for 2027" is exactly this.
- **Opportunity fingerprint** — the catalog schema already carries type, eligibility, grade, price,
  dates, subject_tags. The real hole is **geo/distance** (tracked in the matching plan).
- **Never-auto-activate + review queue** — every discovered thing lands `is_active=false` pending a
  human yes. This is correct and is the true throughput bottleneck (see Guardrails).

---

## Priority ranking (opinionated)

### Do now — high leverage, low cost, low risk
- **P1 — Signal expansion.** Teach discovery/classify the *hidden* vocabulary ("youth council,"
  "career quest," "research training," "pre-college," "student researchers," "open to secondary
  students," "student fellows," "work-based learning") plus opportunity *verbs* (mentor, shadow,
  research, volunteer). Cheapest, highest-recall win; directly attacks the language mismatch that
  hides Seattle Children's / Allen Institute. **Safe to be greedy** because the refusal gate is the
  precision backstop — broadening recall just sends more candidates to a gate that says no cheaply.
  Constraint held: still **application-based programs only** (operator decision 2026-09-01).
- **P2 — Stage-2 hub resolution → leads.** Turn org candidates into miner input: resolve each org
  to its teen/programs *index* (free structural first), emit a `discovered_leads` KIND_HUB /
  SCOPE_SAME_DOMAIN row. The miner already drains these. This is what turns "1 org" into "N rows"
  (mining a single program page yields one row; mining the index yields the whole family).
- **P3 — Hiddenness score (v1, free signals).** The best novel ranking signal *and* a review-queue
  triage signal. v1 from free signals only: URL path depth / buriedness, has-its-own-landing-page,
  search-rank, aggregator presence. Surfaces "🔎 Hidden Gem" to students AND auto-prioritizes the
  reviewer. (Paid domain-authority data is a later refinement.)
- **P4 — Recurrence intelligence.** Extend the existing date projection into an explicit
  "🟡 likely returning — applications historically open ~January" state. Cheap, and it makes expired
  rows valuable instead of discarded.

### Next — real value, watch cost/precision
- **P5 — Partner-graph expansion, BOUNDED.** "…partnered with X nonprofit…" → X's site → its
  programs. A natural extension of the leads table (off-domain / names lead), but gated by a
  **value-of-information** check (only spend to expand a node whose archetype/yield history predicts
  payoff — the seed-yield-credit machinery already exists). Graph exploration yes; open-web crawl no.
- **P6 — Geo.** City/distance on rows. Prerequisite for the "3.7 miles away" radar; already the
  matching plan's gap.

### Later — high WOW, high risk; defer until the safe core proves out
- **P7 — Crawl people (professors/researchers) & informal opportunities** ("email the professor").
  Highest WOW, but two hard problems: **safety** (users are minors; surfacing "contact this
  individual adult" is a different liability than an institutional program) and **verifiability** (an
  informal opportunity has nothing to verify against and changes weekly — the opposite of this repo's
  "verify against the page" culture). Mitigation: the three-tier confidence
  🟢 formal / 🟡 informal / 🔵 discovery, with **🔵 kept out of what students see** until a human vets it.
- **P8 — Continuous per-org monitoring.** As stated ("every org, daily agent") it is unrealistic
  here: **there is no scheduler** (monthly cron paused, agents run manually) and per-org daily paid
  crawls are a large recurring bill for content that barely changes. Reframe: a **bounded watchlist**
  of high-value orgs re-checked **around their known cycle windows** (which P4 tells us) — 10x cheaper
  and smarter than polling. Opportunities are seasonal, not daily.

---

## Guardrails (non-negotiable, inherited from this repo)

1. **Three cost tiers everywhere** — `--preview` (free, zero calls), `--dry-run` (pays, no writes),
   live. Discovery search and mining are PAID (MARQUEE M9): fresh explicit approval per run, with a
   printed cost estimate. Structural resolution (fetch + regex) is FREE and should be preferred.
2. **Never auto-activate.** Orgs → hubs → rows all land as leads / `is_active=false`. A human gates
   each hop. The review queue is the real throughput limit, so P3's scoring must double as reviewer
   triage (high-confidence formal → normal queue; informal/discovery → a low-priority bucket).
3. **Verify, don't trust.** A model-typed URL is untrusted anywhere in this repo — URLs come from
   grounding. Extraction is verified against the fetched page. The obscure/informal end is where
   verification is weakest, so confidence must be **surfaced honestly** ("why you're seeing this"),
   and a 🔵 hunch must never wear a 🟢 listing's clothes.
4. **Bounded graph.** Keep the miner's recursion/scope caps. Expansion is gated by value-of-information,
   never an open crawl. Every fetched page that clears the gate costs ~$0.004; every search ~$0.014.
5. **Safety for minors.** Anything that routes a student to contact an individual person is treated as
   a safety decision, not just a data one — human-vetted before surfacing, and never auto-sent.
6. **Marquee.** New model prompts are M8 (approval + dedicated commit, house-style examples). New paid
   call paths are M9. This plan's prompt work is small but still marquee.

---

## Phased build

- **P0 — Prototype (DONE, this branch).** `local_org_discovery.py`: NL → region brief → org
  candidates. Free preview + `--live`. Bay Area validated.
- **P1 — Signal expansion (this branch).** Broaden the discovery/extract prompts to the hidden
  vocabulary; hold application-based-only.
- **P2 — Hub resolution + leads (this branch).** `resolve_hub_url()` (free structural: promote a
  program URL to the org's programs index; keep an index as-is) → `append_leads()` KIND_HUB. Then the
  existing miner drains them.
- **P3 — Hiddenness score v1** (free signals) — student surface + reviewer triage.
- **P4 — Recurrence state** — extend date projection into "likely returning."
- **P5 — Geo** — city/distance on rows (with the matching plan).
- **P6+ (later, gated)** — partner-graph expansion, people/informal (with safety tiers), bounded
  watchlist monitoring.

---

## Open decisions
- **Archetype breadth vs precision** — how greedy P1 gets (more hidden signals = more recall = more
  paid extraction on the refusal gate). Start greedy, measure the demotion/refusal rate.
- **Hiddenness inputs** — free-only for v1, or budget for domain-authority data?
- **Where 🔵 discovery-tier finds live** — internal-only vs a clearly-labeled student surface.
- **Watchlist size** for P8 monitoring, once we get there.
