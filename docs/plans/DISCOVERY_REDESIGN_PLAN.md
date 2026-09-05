# Discovery redesign — phase 1 buys programs and their home pages

Opened 2026-08-27 on branch `angle-query-strategy`. **Nothing in here is built.** Every paid run
needs fresh explicit approval in chat, as with every other agent in this repo.

Companion to [ANGLE_STRATEGY_PLAN.md](ANGLE_STRATEGY_PLAN.md), which is the survey and the
measurements. This file is the change: what would move, what breaks downstream when it does, in what
order to build it, and what has to be decided first.

---

## 1. The change, in one page

Three deltas, in dependency order.

**D1 — Phase 1's brief becomes identity + home page.** `RESEARCH_SYSTEM` currently orders a ten-field
write-up per opportunity under "never guess". `refresh_opportunities.py` declares its own scope as
*name, org, summary, type, price, state, location, intl, season, category, eligibility, grade_min,
grade_max, cost, subject_tags, contact_email* — every one of those fields — and it refuses to write
`url` on principle. So phase 1 is paying a per-search fee for fifteen fields a free agent re-derives,
and the one field only it can get right (grounding is what makes a URL real) is a by-product. The new
brief: **name, org, one line, and the URL of the program's own page.** Before/after text is in §9.

**D2 — Discovery and URL-resolution become separate, budgeted stages.**

    1a  DISCOVERY    N broad, deliberately varied searches   -> program NAMES (+ any URL grounding
                                                                already resolved, free)
    1b  RESOLUTION   one narrow search per UNRESOLVED name,  -> that program's own page, title-proven
                     capped per angle AND per run
    2   EXTRACTION   no search, notes in, JSON out           -> whatever fields the notes carry

1b is not new code: it is `harvest_names.best_resolved_url` (4N) and the same shape as
`refind_dead_links`. Its free gates come too — above all `is_known_name`, which refuses to *pay* to
resolve a name the catalog already holds. Today dedupe runs after extraction, so we research
duplicates at full price.

**D3 — Local becomes a geography, not an institution list.** What makes an opportunity local is that
its audience is geographically bounded; the provider's type is irrelevant, and private businesses
(coding academies, studios, makerspaces, labs, local employers) are in scope. Queries anchor on the
SMALLER place names and spend half their budget on the pages that INDEX local programs (parks-and-rec
catalogues, district bulletins, library calendars, regional camp guides, chamber directories).

**Phase 4L is PINNED.** D3 is written down here because D1/D2 change the prompt it would build on.
It is not a work item until the operator lifts the pin.

---

## 2. Why this is a pipeline change, not a prompt edit

The prompt is one of eleven things that move. The rest of this document is the eleven.

    scrape_opportunities.main()
      |- research_seed          D1 prompt, D2 stage 1a          -> §3.6 telemetry, §3.5 wall-clock
      |- [NEW] resolve_names    D2 stage 1b                     -> §3.4 cost, §3.8 dedupe
      |- extract_candidates     unchanged call, thinner notes   -> §3.1 completeness
      |- build_row              drops URL-less rows today       -> §3.8
      |- insert_rows            unchanged
      |- record_seed_result     `found` semantics shift         -> §3.3 THE LEDGER HAZARD
      |- auto_disable_mined_seeds                               -> §3.3
      |- discovered_leads.capture  more pages consulted          -> §3.9
      `- snapshot write         shape gains unresolved names    -> §3.10

---

## 3. Downstream implications

> **Update 2026-08-28 — the memory half of this blocker is FIXED (MARQUEE M1).**
> `refresh_opportunities.py` now READS THE LIVE PAGE (a free plain-HTTP GET) and extracts
> fields from it; a fetch failure skips the row rather than answering from memory. It was
> discovered that the agent had been silently flipped to memory-only (`use_web_search=False`,
> "YOU HAVE NO WEB ACCESS") inside an unrelated commit — see [MARQUEE_DECISIONS.md](../../MARQUEE_DECISIONS.md)
> M1. What REMAINS of this blocker: the agent still selects `is_active = true` rows only, so it
> cannot yet be pointed at queued rows — the `--ids` / `--pending` mode below is still to build.

### 3.1 BLOCKER — a queued row can never be enriched, because the refresher only reads ACTIVE rows

`refresh_opportunities.py` fetches with `"is_active": "eq.true"`. Scraped rows land
`is_active=false`. So the agent that is supposed to own the fifteen fields phase 1 stops collecting
**cannot see the rows that need them**, and the sequence D1 assumes —

    scrape (name/org/url) -> refresh (metadata) -> review -> activate

— is not runnable today. Without a fix, D1 ships thin rows into the review queue and a reviewer
activates a row that shows a student a name and nothing else.

Three ways out, in order of preference:

- **(a) Give `refresh_opportunities.py` a pending mode.** `--ids ID...` and `--pending` (rows with
  `is_active=false` and `moderation_status` pending), ignoring the active filter. This mirrors
  `check_deadlines.py --ids/--missing-opens`, which exists for exactly this reason: to re-check a
  known set for cents instead of paying for a full pass. Cheapest, most in keeping with the repo.
  It is a **no-search** agent, so a 200-row pending pass is roughly $0.2, not $20.
- **(b) Refresh on activation.** The console's activate endpoint fires a refresh for the activated
  ids. Ties spend to a UI click, which this repo deliberately avoids elsewhere; also makes activation
  slow and failure-prone.
- **(c) Accept thin rows and refresh on the next full pass.** Rejected: it makes every newly activated
  row student-visible in a thin state for an unbounded window.

**Recommendation: (a), and it is a prerequisite for D1, not a follow-up.** D1 must not merge before
it.

Second-order: `refresh_opportunities.py` is the only writer of `subject_tags`, and
`propose_angles.py` measures subject coverage from `subject_tags + name + summary`. If new rows carry
no tags, the coverage-gap angle generator reads the catalog as thinner than it is and proposes angles
for subjects that are actually covered — a self-reinforcing error in the one place new angles come
from.

### 3.2 The review queue gets more rows, each carrying less

A reviewer judges a queued row from `name / org / summary / type / url / quality_flags`. After D1,
`summary` is one line and `type` may be null (`FLAG_NO_TYPE`), while row volume rises ~4x. Both
directions hurt: less to read per row, more rows to read.

What actually makes a thin row judgeable is the **URL**, and D1/D2 improve exactly that — a
title-proven, own-domain landing page is a reviewer's fastest possible check. So the queue should
lean on it:

- Show the URL's proof state per row (`FLAG_TITLE_UNPROVEN` present or not) rather than leaving it in
  the flags pill soup.
- Sort or group by proof state, so proven rows can be swept quickly and unproven ones get attention.
- If (a) above runs before review, the row is not thin at all — which is another reason (a) is a
  prerequisite rather than a nicety.

### 3.3 LEDGER HAZARD — `found` changes meaning, and it can auto-retire good angles

`seed_found = len(candidates)` today: what phase 2 extracted. `seed_ledger._finalize` then computes

    effective_found   = max(found, queue_total)
    internal_discards = effective_found - reached_queue
    waste_rate        = (internal_discards + rejected + duplicate) / effective_found

If D2's discovered-but-not-resolved names are counted in `found`, every name the run **chose not to
pay to resolve** becomes an "internal discard" and inflates waste. An angle that discovers 25 names
and resolves 10 would read as 60% waste — and `mined_out`/`thin` are exactly the diagnoses that
**auto-disable**. The better an angle is at discovery, the more likely it would be retired. That is
the loop running backwards.

Rules to hold:

- **`found` keeps meaning "candidates we paid to pursue"** — names that reached extraction. A
  budget-declined name is not waste; nobody spent anything on it and it was not judged.
- Discovered-but-unresolved names are recorded **separately** (snapshot + the 4N lead file, §3.9), so
  an angle's discovery power stays visible without polluting the rate that retires it.
- Optional, later, and degrade-if-absent like every migration here: `scraper_seeds.total_names`
  (`alter table ... add column if not exists`) to show discovery volume in the console beside yield.
- **Re-check `MIN_FOUND = 10` after the first run under the new shape.** It was calibrated when an
  angle returned ~5; if candidates per angle rise, the sample guard arms sooner and diagnoses start
  firing on a population whose reason-code coverage is still 40% (ANGLE_STRATEGY_PLAN §5.2). Arming
  the retirement loop and changing what it measures in the same change is how a good angle gets
  retired for a reason nobody can reconstruct.

### 3.4 Cost — per angle it roughly triples; the multiplier lands downstream, not here

Modelled, and labelled as modelled:

    today       ~2-3 calls, ~5 searches, ~5 candidates          ~$0.09/angle    ~$0.018/program
    proposed    1a (6 searches) + <=10 x 1b + phase 2           ~$0.25-0.30     ~$0.012/program
                = ~12 calls, ~16 searches, ~20 candidates       at 40 angles: ~$10-12/pass vs ~$3.60

Cost per program falls; **cost per pass rises about 3x** and needs explicit approval before any live
run. The bigger number is not this agent at all:

    a 4x row yield means 4x the rows flowing into every downstream agent that is priced per row
    check_reviews.py         ~$0.0166/row   800 new approved rows -> ~$13
    generate_action_items.py ~$0.0016/row                          -> ~$1.3
    check_deadlines.py       ~$0.0676/row   on-demand, so it lands as student traffic, not a batch

**State this to the operator before building, not after the first pass.** Discovery is the cheap end
of this pipeline; admitting rows is what costs money, and D1/D2 are a request to admit far more of
them.

### 3.5 Wall-clock and rate limits — the constraint that actually binds

`gemini_common` enforces a **5s minimum delay** between calls (the fix for this pipeline's repeated
429s — treat it as a floor) and takes a **process-wide web-search lock**, so search calls serialise.

    today       40 angles x ~3 calls  = ~120 calls   ~35 min
    proposed    40 angles x ~12 calls = ~480 calls   5s delay + ~15s latency ≈ 2.5-3 hours

That is a different kind of run: long enough to hit an interrupted session, and long enough that a
mid-run failure wastes real money. Mitigations, all of which already exist in the repo:

- **A run-level resolution ceiling, not just a per-angle one** — the exact lesson of `mine_hub_pages`
  (`ebd5431`, "a spend ceiling for the whole run, not just per hub"). Reuse the pattern.
- **Resolve in priority order** so a truncated run spent its budget on the best names: unknown names
  first (`is_known_name`), then those whose name is provable (`name_is_resolvable`), then by rank.
- Cost banked **per call**, as the two-phase agents already do, so an exception cannot discard spend
  already billed.
- Names not reached become 4N leads (§3.9). A truncated run degrades to fewer resolutions, never to
  lost discovery.

### 3.6 The query-telemetry card breaks unless stage is recorded

This is a consequence of the feature built earlier today. `query_telemetry` classifies a query
broad / named / metadata, and the whole point of `breadth %` is that broad queries are the ones that
can discover something. **Stage 1b queries are named BY DESIGN** — "official page for X by Y" is a
resolution, not a discovery failure. Mixed into one bucket, breadth would read ~30% after the change
just as it does now, and the metric that is supposed to prove the change worked would be blind to it.

So the per-seed log gains a `stage` per query (`discovery` | `resolution`), and the console reports
**breadth over stage-1a queries only**, with resolution counted separately as its own column
(searches spent, names resolved, proof rate). `summarize_run` should also stop counting 1b queries in
`distinct_queries`, or the cross-angle overlap signal drowns.

Backwards compatibility: existing logs have no `stage`. Treat a missing stage as `discovery` — that is
what those runs were — and never as unknown, or every historical run drops out of the chart.

### 3.7 The search budget stops being a wish

`--max-searches` is English folded into the prompt and has never bound (5.33 against a cap of 10).
After D2:

- Phase 1a keeps the soft prompt cap and can come **down** (~6), since it no longer pays for
  confirmations.
- Phase 1b is a **loop counter in code** — genuinely enforced, per angle and per run.

That is the first real spend control this agent has had, and it is worth saying plainly: the
prompt-level cap was never one.

### 3.8 Dedupe, URL truth, and rows with no URL

- `build_row()` returns None when there is no URL — the row has no identity. Under D2 that is the
  correct behaviour for a name we could not resolve, but the name must not vanish: it goes to the
  lead file, not to the floor. Today it is dropped silently.
- `url_dedupe.find_duplicates` needs a URL. A name-only match must **never** auto-reject — measured:
  the 0.85 name-similarity threshold matched 264 pairs of which 257 were genuinely distinct. The
  cheap pre-check for 1b is `harvest_names.is_known_name`, strict identity-set equality, which
  decides *whether to pay*, never whether a row is a duplicate.
- `collapse_intra_run_twins` sees more rows per run and more same-domain siblings. Its ranking
  (title-proven > not-low-value > shallower path) is unchanged and is exactly right for a
  landing-page-first design.
- `resolve_url_truth`'s step 2 — trading a low-value sub-page UP to a proven landing page — currently
  fires only when grounding happens to offer a landing URL. D1 asks for landing pages directly, so
  this mechanism should fire more often and more usefully. **Watch `FLAG_URL_RESCUED` and
  `FLAG_TITLE_UNPROVEN` rates as the acceptance signal.**

### 3.9 Feed-forward gets busier, and that is the point

More broad and list-shaped searches mean more round-up pages consulted, and `discovered_leads` (4F)
captures them for free. Two consequences:

- The lead queue grows faster than it is worked. It is already 277 hub + 5 name leads. Acting on a
  lead is PAID, so growth is not spend — but a queue nobody drains is not a compounding loop either.
- Unresolved names from 1b are a **new lead kind** with a natural home: `KIND_NAMES` already means "a
  page that names programs". A bare name with no page is not that. Either add a third kind, or write
  unresolved names to the run snapshot only and let a later pass pick them up. **Open question,
  §6.**

### 3.10 Snapshots and dry-run commit

`dryrun_common` reads `data.get("inserted")` and ignores unknown keys, so adding
`"unresolved_names"` to the snapshot is safe with no reader change. `_patch_updates()` must keep
mirroring the live write column-for-column — D1 changes which columns are typically populated (many
now null), and **a null must not be written over a value a later refresh already filled in.** Check
this explicitly when the commit path is next touched; a snapshot committed after a refresh pass could
otherwise blank good data.

### 3.11 Local surface area (D3, gated on the 4L pin)

`mode` is hardcoded to `national | seattle` in four places: the console's angle tabs, the seed modal,
the run modal, and `propose_angles.py --mode` choices plus its `scope` string. Generalising to
locales means those become data-driven off a `LOCALES` dict (display name + `sub_places`), keyed by
the same `mode` value — **no migration**, since `mode` is already a free-text column.

Risk that must be measured before judging the channel: the free gates were calibrated on national
programs. `url_repair.title_proves` needs two identity words in a page's `<title>`, and a small local
provider's title is routinely "Home | Studio Name". Local resolution may fail proof far more often for
reasons that have nothing to do with geography. **Measure the proof rate on a local arm before
concluding local discovery does not work**, or the gates get blamed for the place and the place for
the gates.

### 3.12 Tests and the grading gate

`grade_scraper_batch` imports the real deciders (`classify_same_url`), so the gate keeps its teeth and
old fixtures keep grading old batches. Touched suites: `test_scrape_insert`, `test_scraper_urls`,
`test_funnel`, `test_seed_ledger` (if `found` semantics move), `test_query_telemetry` (stage field),
`test_name_harvest` (if 1b is refactored for reuse). `0 regressions` stays the merge bar.

---

## 4. Build order

Each step is independently mergeable and independently useful. Free unless marked.

| # | Step | Cost | Gate to the next step |
|---|---|---|---|
| P0a | **DONE 2026-08-28 (MARQUEE M1)** — `refresh_opportunities.py` reads the live page (free HTTP) instead of memory | fetch is free; only the Gemini extract costs, ~fraction of a ¢/fetched row | metadata is filled from the real page, not invented |
| P0b | **DONE 2026-08-28** — `refresh_opportunities.py --ids / --pending` point it at queued/new rows | free | a queued or just-activated row can now be enriched (--pending found 174 queued rows live) |
| P1 | **DONE 2026-08-28** — resolution queries kept OUT of `queries`, so breadth is discovery-only by construction; resolution reported on its own axis | free | the A/B in P4 is measurable |
| P2 | **DONE 2026-08-28 (M5/M8)** — `DISCOVERY_SYSTEM` split from `RESOLVE_SYSTEM`; opportunity + broad/named queries defined with examples | free to build | breadth% rises on one angle (verify in P4) |
| P3 | **DONE 2026-08-28 (M8/M9)** — `resolve_missing_url` (free gates + 1 search + best_resolved_url), `--resolve-per-angle/-per-run` ceilings, `--no-resolve` | free to build | resolution rate + proof rate (verify in P4) |
| P4 | **A/B on ONE angle, both arms** | PAID, ~$0.20-0.40, needs approval | the table in §5 |
| P5 | Ledger semantics: `found` excludes budget-declined names; re-check `MIN_FOUND` (§3.3) | free | no angle auto-disabled for discovering well |
| P6 | Review-queue proof-state column/sort (§3.2) | free | reviewer can sweep proven rows |
| P7 | D3 local — LOCALES dict, prompt block, console tabs data-driven | free to build | **BLOCKED on the 4L pin** |
| P8 | Local A/B with its own baseline (§3.11) | PAID, needs approval | scored separately, never blended |

P0 before P2 is not negotiable (§3.1). P1 before P4 or the measurement is blind.

---

## 5. Acceptance — what would make this ship, and what would kill it

One angle, two arms, identical everything else. Score national and local separately; a blended number
hides whichever half is failing.

    metric                        today    target      kills it if
    breadth % (stage 1a only)      26%     >70%        below ~50% — the prompt did not take
    distinct programs per angle     ~5      15+         below 10 — coverage did not move
    URL resolution rate              —      >60%        the deliverable; below ~40% the design fails
    FLAG_TITLE_UNPROVEN rate     baseline  <= baseline  a rise means we traded truth for volume
    cost per NEW program         ~$0.018   <$0.015     above today's — more expensive AND more work
    reviewer approval rate         64%     >= 60%      a real fall means thin rows are unjudgeable

The last row is the one to watch hardest: D1 deliberately gives the reviewer less prose. If approval
falls while everything else improves, the fix is P0/P6 (enrich and surface proof), not a retreat on
the prompt.

Do not ship on argument. Ship on that table.

---

## 6a. Deferred — silent-search retry budget (operator: revisit another time, 2026-08-28)

`research_seed` re-rolls a zero-search call **once** (attempt loop `(1, 2)`). Measured in the P4
runs: ~1/3 of discovery calls on model-saturated domains (neuroscience, culinary) still went silent
through *both* attempts and answered from memory (homepage/memory URLs, all flagged). A silent call
pays no per-search fee, so raising the budget to ~3 attempts is nearly free and — if the silent
decision is independent per call — would cut memory-answers from ~1/3 to ~1/25. **Deferred by the
operator.** When revisited: make the attempt count configurable, and MEASURE whether extra re-rolls
actually break a silent streak or just repeat it (if the streak is angle-correlated, the real lever
is running discovery on less-saturated angles, not more retries). Forcing a search is impossible
(no `toolConfig: ANY` for `googleSearch` — gemini_common's THIRD finding); this only raises the odds.
It is an M9 change (alters the API-call loop) — own commit, approval first.

## 6. Open questions for the operator

1. **Unresolved names (§3.9)** — a third lead kind, or snapshot-only until a later pass? A third kind
   makes them workable; snapshot-only keeps the lead queue from growing faster than it is drained.
2. **Per-run resolution ceiling** — what is the number? It sets both the pass cost (§3.4) and the wall
   clock (§3.5). ~10/angle and ~150/run is the modelled starting point.
3. **The downstream multiplier (§3.4)** — 4x rows means ~$13 of review spend per pass and a much
   longer review queue. Is the intent to admit more rows per pass, or the same number more cheaply?
   The design serves both, but the budget answer differs.
4. **`scraper_seeds.total_names`** — worth a small ALTER to keep discovery volume visible in the
   console, or leave discovery counted only in snapshots?
5. **4L pin** — D3 is drafted and blocked. Lift, or leave pinned and ship national-only?

---

## 7. Deliberately not doing

- **Not writing queries in code.** Grounding chunks are the only place a retrieved URL exists, and
  they come from the model's own searches. Composing queries ourselves would trade away the mechanism
  phase 2's URL truth depends on.
- **Not making phase 1 return JSON.** Measured: prose 4/4 calls searched, JSON 0/4.
- **Not merging phase 1 and phase 2.** Same finding.
- **Not auto-activating anything.** Unchanged: a person activates, always.
- **Not loosening `title_proves`** to raise the local resolution rate. If local proof is genuinely
  harder, that is a measurement to act on deliberately, not a threshold to quietly relax — the three
  tests in `url_repair` cost 59 proposals to keep 13 honest, and that trade was made on evidence.

---

## 8. Reversibility

D1 and D3 are prompt constants; reverting is one edit and one commit. D2 adds a stage — reverting
means setting its per-run ceiling to 0, which degrades to today's single-stage behaviour with a
slightly broader prompt. **The one thing that is not reversible is rows admitted into the catalog on a
bad pass**, which is why P4 is a one-angle A/B and not a full pass, and why nothing here changes the
rule that a person activates every row.

---

## 9. The prompt, before and after

Kept here so the change is reviewable without reading the diff. `EXTRACT_SYSTEM` (phase 2) is
unchanged — it already permits a null URL, and extracting fields there is free. `gemini_common`'s
forced-search nudge and soft budget line are unchanged.

### 9.1 `RESEARCH_SYSTEM` — before

    You are a meticulous researcher helping build a high-quality catalog of extracurricular
    opportunities (programs, internships, research, competitions, volunteer roles, journals,
    conferences) for high school students, for the app "Wingman". Today's date is {today}.

    Search thoroughly with web_search for: {angle}

    Hard rules — quality over quantity matters far more than hitting any target count:
    - Only write up real opportunities you found actual evidence for via web_search. Never invent
    one, never pad the list to reach a certain size. If you found 2 excellent ones, write up 2 — do
    not add weak or speculative filler to look more thorough.
    - Confirm (as best you can from what you found) that the opportunity is currently real/active —
    skip anything that looks defunct, or where you can't tell if it's still running.
    - Reason about eligibility, grade range, and cost from what you actually find on the page —
    never guess.

    Write your findings as notes, one opportunity at a time, each starting with its name in bold.
    For each, cover: the running organization, what it actually is (2-4 sentences of real detail),
    who is eligible (grades, international vs domestic), cost, whether it is in-person or remote,
    what time of year it runs, the US state if it is location-specific, the subject areas it covers,
    and a contact email if you found a real one on the site. Say plainly when you could not
    establish something rather than filling it in.

### 9.2 `RESEARCH_SYSTEM` — after (proposed)

    You are a meticulous researcher helping build a high-quality catalog of extracurricular
    opportunities (programs, internships, research, competitions, volunteer roles, journals,
    conferences) for high school students, for the app "Wingman". Today's date is {today}.

    Find as many DISTINCT opportunities as you can for: {angle}

    WHAT YOU ARE PRODUCING. For each opportunity: its name, who runs it, one line on what it is,
    and — the part that matters most — the URL of its own page. You are not writing a profile.
    Other agents read each opportunity's own page afterwards and take eligibility, grade range,
    cost, dates and application steps from it. So you do not need to establish any of those, and
    you must not spend searches on them. Not knowing them is expected, not a gap.

    WHAT COUNTS AS ITS OWN PAGE. A page on the running organization's own website — not a round-up
    article, a directory, a blog or an aggregator — and the page about THAT opportunity: not the
    organization's homepage when it runs many programs, not the application form, not an FAQ, not a
    PDF. If the opportunity has its own dedicated site, that site's root is the right page. Copy the
    URL character for character from a page you actually retrieved. NEVER construct, complete or
    guess one — a plausible wrong URL is worse than no URL, because it looks correct to a reviewer.

    HOW TO SEARCH. Run at least {n} DIFFERENT searches before you write anything, and make every one
    of them describe a CLASS of opportunity rather than naming a specific program. Vary them
    deliberately:
    - swap the noun: program / institute / academy / intensive / fellowship / challenge /
      competition / internship
    - swap the audience wording: high school students / teens / grades 9-12 / rising juniors /
      pre-college
    - swap the kind of host: university, national lab, hospital, museum, professional society,
      nonprofit, company
    - include at least one list-shaped search: "list of ... for high school students",
      "... directory", "best ... 2027"
    Round-up articles, directories and "N programs for high schoolers" listings are USEFUL to you:
    read them and take every opportunity name they list. Do NOT search for a specific opportunity by
    name — a search that names one program can only ever return that one program, and finding
    programs nobody has told us about is the entire job.

    HARD RULES:
    - Only write up opportunities you found actual evidence for in a search result. Never invent
    one, and never pad the list with weak or speculative filler to look thorough.
    - Skip anything that looks defunct, or where nothing you found suggests it still runs.
    - Write down an opportunity even when you could not find its own page: give the name and say
    "no url". A name on its own is still useful to us.

    FORMAT. One opportunity per line, no paragraphs:
    **Name** — organization — one sentence on what it is — URL of its own page, or "no url"

### 9.3 `SEATTLE_ADDENDUM` — before

    This is a hyperlocal, creative-reasoning sweep: the org itself may not describe this as a "high
    school opportunity" at all. Your job is to actively reason about whether a motivated high
    schooler could turn it into one — e.g. a student building an app could use a local farmers
    market booth to get beta customers. That's just one example, not a template — do not force every
    result into that same framing. In your notes, concretely explain *why and how* a high schooler
    could actually use this one, specific to what you found, not generic filler. If you can't come
    up with a genuinely concrete, specific reason, leave it out.

### 9.4 `LOCAL_ADDENDUM` — after (proposed, blocked on the 4L pin)

    THIS IS A LOCAL SWEEP for {place}. Everything above still holds — you are still producing a
    name, an organization, one line, and the opportunity's own page — but where you look changes.

    What makes something local here is that its audience is geographically bounded: a student has to
    be able to get there, or it is only open to students from that area. The PROVIDER can be any
    kind, and the kind does not matter: a city or county department, a library, a school district, a
    museum, a university, a hospital, a nonprofit — or a private business, such as a coding academy,
    an art or music studio, a dance company, a makerspace, a lab, a local employer running a teen
    program, or a small company willing to take a high school intern. A commercial provider is
    perfectly acceptable; price is not your concern. Do not skip an opportunity because the provider
    is small, or because its website is thin or amateurish — that is normal for a genuinely local
    program and is not evidence against it.

    A generic search will be won by national programs with the city's name appended to them, so:
    - Anchor EVERY search to a place, and prefer the SMALLER places — {sub_places} — over "{place}"
      on its own.
    - Spend at least half your searches on pages that LIST local opportunities rather than on
      individual ones: parks and recreation activity catalogs, school district community education
      bulletins, library event calendars, regional parenting or summer-camp guides, community center
      schedules, chamber of commerce member directories. One such page can name dozens of local
      programs, and it is the most valuable thing you can find in this sweep.
    - A national organization's local chapter, branch or site counts, as long as what you found is
      the local one.

**The notable deletion** is the creative-reasoning framing — the farmers-market-booth instruction.
That is the 4L experiment: it ran once, almost nothing survived review, and it asks the model to
*invent* an opportunity out of a business that is not offering one. D3 replaces invention with a wider
net over providers that really are offering something.

### 9.5 User turn

    before:  Search now and write up what you find for: {angle}
    after:   Search now, then list every opportunity you found, one per line, with its own-page URL
             where you have one, for: {angle}
