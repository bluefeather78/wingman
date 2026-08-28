# Angle strategy — how a search angle becomes queries, and where new angles come from

Working document, opened 2026-08-27. Scope: the **angle layer only** — the thing that decides
*what the scraper goes looking for* and *how that intent reaches a search engine*. Everything
downstream of a returned page (URL truth, dedupe, merge, review) is
[SCRAPER_IMPROVEMENT_PLAN.md](SCRAPER_IMPROVEMENT_PLAN.md)'s territory and is not re-litigated here.

This file starts as a **survey plus measurements**, not a proposal. Section 5 is the open-questions
list; nothing in it is decided.

---

## 1. What an angle is today

An **angle** (stored as a `scraper_seeds` row, called a "seed" everywhere in the code) is one
free-text English phrase describing a class of opportunity to go and find. It is the *only* thing
that varies between one paid scraper call and the next.

    scraper_seeds(id, mode, angle, is_enabled, sort_order,
                  total_runs, total_found, total_added, total_dupes, total_cost,
                  last_run_at, disabled_reason, disabled_at, category*)

`* category` is dead weight: `not null` so writes supply `SEED_CATEGORY_PLACEHOLDER`, and nothing
reads it. It was dropped as a concept 2026-08-23 because it was never interpolated into a prompt
and its one live use (a `type` fallback) measured 27% wrong overall, 65% for Research angles.

Two `mode` values exist: `national` (58 enabled) and `seattle` (8 enabled). Mode selects which
addendum is appended to the research prompt and is the console's National/Seattle switch.

**An angle is a prompt fragment, not a query.** It is interpolated verbatim into the phase-1 system
prompt (`RESEARCH_SYSTEM`) and once into the user turn. Nothing in this repo writes a search query
for the scraper. See section 3.

Loading, selection and crediting live in [seeds_common.py](seeds_common.py):
`load_seeds()` (Supabase, falling back loudly to the `NATIONAL_SEEDS` / `SEATTLE_SEEDS` literals in
[scrape_opportunities.py](scrape_opportunities.py)), `select_seeds()` (`--seed-ids` stable,
`--seed-indices` deprecated), `record_seed_result()` (read-modify-write of the lifetime counters,
re-read immediately before crediting so a mid-run console edit is not clobbered).

---

## 2. Where every angle-relevant prompt lives (inventory)

| Prompt | File | Tools | What the angle does to it |
|---|---|---|---|
| `RESEARCH_SYSTEM` (phase 1) | `scrape_opportunities.py` | googleSearch ON | `Search thoroughly with web_search for: {angle}` — the whole steering mechanism |
| user turn, phase 1 | `scrape_opportunities.py:research_seed` | — | `Search now and write up what you find for: {angle}` |
| `SEATTLE_ADDENDUM` | `scrape_opportunities.py` | — | appended for `mode=seattle`; reframes the task as creative reasoning about non-obvious local orgs |
| forced-search nudge | `gemini_common.py:call_gemini` | — | appended to EVERY search-enabled system prompt: "You MUST call the googleSearch tool at least once..." |
| soft search budget | `gemini_common.py:call_gemini` | — | appended when `max_searches` is set: "use at most {n} web searches total" |
| `EXTRACT_SYSTEM` (phase 2) | `scrape_opportunities.py` | none | the angle is NOT present — extraction never sees what was being looked for |
| `_NAME_SYSTEM` | `harvest_names.py` | none | reads program NAMES off a fetched page; no angle involved |
| refind query | `refind_dead_links.py` | search | `current official page for <name> by <org>` — the one place this repo composes a query string itself |

Three properties of the phase-1 prompt worth stating plainly, because they constrain any redesign:

- **It asks for prose, deliberately.** Measured A/B (2026-08-23, one row, arms alternated):
  prose 4/4 calls searched, JSON 0/4; 34 grounding chunks against 0. Do not add an output format.
- **The angle is the only variable.** Same rules, same schema, same quality bar for every angle;
  the phrase carries 100% of the differentiation.
- **Quality-over-quantity is stated but unquantified.** "If you found 2 excellent ones, write up 2"
  — there is no target count, no floor, no ceiling on candidates returned.

---

## 3. The angle-to-query path — nobody in this repo writes the query

    angle (English phrase)
      -> interpolated into RESEARCH_SYSTEM + the user turn
        -> Gemini decides, autonomously and non-deterministically, WHETHER to search
          -> and then decides WHICH queries to issue, and how many
            -> queries come back only as telemetry: usage.server_tool_use.web_search_queries

Consequences already established elsewhere and load-bearing here:

- **The search decision cannot be forced.** `gemini_common.py`'s THIRD finding, still standing:
  there is no `toolConfig` equivalent of Anthropic's forced tool choice for `googleSearch`. The only
  mitigation is detect-and-re-roll (`research_seed` retries once on a zero-search response, which is
  cheap because a silent call pays no per-search fee).
- **The search budget is a request, not a cap.** `--max-searches` (default 10) is folded into the
  prompt as English. Observed runs stop well short of it.
- **Queries are printed and logged, never used.** `research_seed` prints each query; `main()` writes
  them to `agent_logs/scraper_<stamp>_seed<id>.json`. No code reads that field back — no dedupe of
  repeated queries, no per-query attribution, no query-level yield.

### 3a. What the model actually searched (measured this session, free)

Corpus: every seed log in this checkout — the 2026-08-23 run, **40 seeds, 213 queries**.
There are no seed logs from the 08-27 runs (`agent_runs` 71 and 73): those ran in the
`wingman-scraper-v2` worktree, which has since been removed. **The query telemetry for the two most
recent paid runs is gone.** That is itself a finding — see 5.6.

    queries per seed      min 2   median 6   max 8   mean 5.33     (against a soft cap of 10)
    seeds that went silent           0 of 40
    seeds that needed the retry      2 of 40
    exact duplicate queries          0 across the whole corpus

Query shapes, by hand-checkable pattern. These were the first cut, by literal quote-mark and
keyword; §7's classifier (which also catches an UNQUOTED proper noun — "Johnson Wales University
high school culinary summer camp") puts the narrow share considerably higher, at **68%**:

| Pattern | Count | Share |
|---|---|---|
| contains a quoted proper name (`"MassArt" "Summer Intensives" ...`) | 105 | 49% |
| contains an audience word (high school / teen / pre-college) | 162 | 76% |
| asks for METADATA (cost / eligibility / tuition / stipend / contact email) | 101 | 47% |
| uses a site or domain hint | 4 | 2% |

Read together these say something the design did not anticipate: **roughly half the paid search
budget is not discovery at all.** The model finds a name in a broad query, then spends a second,
third and fourth *fee-bearing* search confirming that one program's cost, eligibility and contact
email — because `RESEARCH_SYSTEM` asks it to cover all of those per opportunity. Example
(seed 27, psychology/neuroscience, 7 searches): 2 broad discovery queries, 5 named-entity
confirmations of programs already found.

That enrichment is exactly the primitive `harvest_names.py` (4N) and `refind_dead_links.py`
formalise — one narrow search per name, grounding-resolved, **title-proven** — except here it
happens inside the broad call, at the same per-search fee, with no title proof and no record of
which query produced which row.

Actual discovery queries are simple and close to the angle text, e.g.
`high school arts intensive summer portfolio program conservatory`,
`urban design high school program competition`,
`psychology neuroscience high school research programs internships`.

---

## 4. How new angles are discovered today

Three mechanisms exist; only the first two are built, and neither has produced an angle that has
run twice.

### 4a. Hand-written (the real source of every angle in use)

40 `NATIONAL_SEEDS` + 8 `SEATTLE_SEEDS` literals, migrated into the table
(`migrate_seeds_to_supabase.py`). Every angle currently enabled traces to a person typing it.

### 4b. `propose_angles.py` — coverage-gap analysis (FREE)

Reads active catalog rows and proposes an angle string for every **thin cell**:

- an under-served `type` (fewer than `--min-per-cell`, default 4, active rows),
- an under-served `(type, season)` pair, and
- an under-served **subject**, ranked thinnest-first, against a curated 38-item `CORE_SUBJECTS`
  list. Deliberately not the catalog's own `subject_tags`: those are hyper-specific
  ("Pastry Arts", "Typography"), so nearly all appear a handful of times and would mint hundreds
  of noise angles.

Templates are fixed strings:

    "{scope} {type} opportunities for high school students (grades 9-12)"
    "{scope} {season} {type} programs for high schoolers"
    "{scope} high school {subject} programs, competitions and research opportunities"

`--commit` writes them as **disabled** seeds; nothing ever runs on its own. Seed ids 54-70 (the 18
"gap angles" enabled and run 2026-08-27) came from here.

**The `--siblings` model call the docstring advertises does not exist.** The argparse has only
`--mode`, `--min-per-cell`, `--preview`, `--commit`. Phase 4 of the improvement plan specifies
"one cheap siblings-of-winners model call"; it was never built. So today, **new angles are generated
by templates over catalog gaps, and by nothing else.**

### 4c. `seed_ledger.py` — retirement, the other half of the loop

The catalog IS the ledger: every scraped row carries `seed_id`, and the reviewer's verdict
(`moderation_status` + `moderation_reason`) lands on that same row, so an angle's funnel is a live
GROUP BY, never a drifting counter. `build_seed_funnels` -> `diagnose` -> `should_auto_disable`,
with `auto_disable_mined_seeds()` running the sweep at the end of every scrape.

Diagnoses: `healthy` | `mined_out` | `mis_aimed` | `pipeline_limited` | `thin` |
`insufficient_sample`. **Only `mined_out` and `thin` auto-disable** — a mis-aimed or
pipeline-limited angle has its fix elsewhere, and silencing it would hide the problem.

Sample guard: `MIN_FOUND = 10`, `MIN_RUNS = 2`, `MIN_ADJUDICATED = 5`.

### 4d. What the other discovery channels do and do NOT feed

`mine_hub_pages.py` (hub links), `harvest_names.py` (4N, names on a page), `discovered_leads.py`
(4F, capture the pages a search already paid to consult), `refind_dead_links.py` (resurrect moved
programs). **All four produce catalog rows or page leads. None of them proposes an angle.** The
compounding loop closes through *rows*, never through *intent*.

---

## 5. Measured state and open questions

Everything in 5.1-5.3 was read live and read-only this session (free; no model call, no write).

### 5.1 The retirement half of the loop has never fired

66 seeds, all enabled, **0 auto-disabled**. Every seed has `total_runs = 1`, so `MIN_RUNS = 2`
alone makes every angle `insufficient_sample`. 205 attributed rows across 66 angles is ~3 rows per
angle against a `MIN_FOUND` of 10.

    diagnoses: insufficient_sample x 66      auto_disable candidates: 0

**The ledger is correct and inert.** Nothing is broken; there is simply not enough evidence yet.
Open question: is a second full pass the way to arm it, or is the guard calibrated for a run
cadence this project does not have?

### 5.2 Reasons are missing on the majority of rejects

205 attributed rows: **132 approved, 69 rejected, 1 duplicate, 3 pending.**

    reason codes present:  third-party-url 13, wrong-page 4, duplicate 4, other 4, not-a-fit 2

That is 27 codes against 70 negatives — **~60% of negative verdicts carry no code**. `diagnose()`
handles this honestly (an uncodeable plurality falls to `pipeline_limited`, which never
auto-disables), but the effect is that even at 2+ runs most angles would decline to diagnose.
Of the codes that DO exist, 17 of 27 are `third-party-url` / `wrong-page` — i.e. the angle found a
real program and the URL layer mishandled it. **No angle has yet been rejected for being a bad
angle.**

### 5.3 Yield per angle is suspiciously uniform

Across 58 run angles, `total_found` clusters at 4-7 and `total_added` at 4-6, regardless of how
broad or narrow the angle is: "remote/virtual internships" (very broad) and "national high school
Theater programs" (narrow gap-fill) both returned 5.

Working hypothesis, untested: **the angle text steers *what* is returned but not *how much*.** The
volume is set by the prompt's posture and the model's stopping behaviour, not by the angle. If true,
broadening an angle buys different results, never more of them, and "one angle = one call = ~5
candidates" is the real unit of production. That has direct consequences for coverage planning and
for cost per approved row.

### 5.4 Angle granularity is unexamined

Angle length in the current set ranges from ~10 words ("national Year-Long research programs for
high schoolers") to ~45 words with parenthetical carve-outs and explicit "distinct from X" clauses.
Several were written specifically to avoid overlapping an earlier angle ("distinct from creative
writing competitions", "distinct from theoretical AI ethics programs") — i.e. the operator is
hand-managing MECE-ness across 66 phrases with no tooling, and with no measurement of whether the
disambiguators change what actually gets searched.

### 5.5 Half the search fee is spent on enrichment, not discovery (from 3a)

47% of queries ask for cost / eligibility / contact email on a program already named. Open question:
should phase 1's brief narrow to *find and identify*, with metadata resolved by a free page fetch
(`page_text`) or by the existing metadata agent, so the per-search fee buys only discovery?
Counter-consideration: those enrichment queries are also what produce grounding chunks for the
program's own page, and grounding is what makes the stored URL trustworthy — so naively removing
them could re-open the 26%-dead-link failure. **Do not act on this without measuring the effect on
grounding coverage.**

### 5.6 Query telemetry is written and then lost

Queries exist only in `agent_logs/scraper_<stamp>_seed<id>.json`, which is untracked local disk. The
two most recent paid runs' logs are gone with the deleted worktree. Nothing reads them back, so
today there is no way to ask: which queries repeat across angles, which query shape yields approved
rows, whether two angles collapsed to the same three searches.

### 5.7 New-angle generation is template-only

`propose_angles.py` can only propose what its three format strings can say, over a fixed
`CORE_SUBJECTS` list. It cannot propose an angle for a *shape* of opportunity nobody has named (the
"structured skilled volunteering, not one-off volunteering" distinction, or the "international
residential 10-21 day" angle) — every one of those was hand-written. The siblings-of-winners call
that would learn from what actually got approved is unbuilt (4b).

---

## 6. Takeaways so far (nothing decided)

1. **The angle is a prompt fragment; the query layer belongs entirely to the model.** Any refinement
   is either (a) changing the phrase, (b) changing the prompt around the phrase, or (c) taking query
   generation in-house. Only (c) is a new capability, and it trades away the grounding-chunk
   mechanism phase 2's URL truth depends on.
2. **Discovery and enrichment are billed at the same per-search rate and mixed in one call.** That is
   the largest identified cost lever in the angle layer. §8.1 sharpens it to a division-of-labour
   fact: `refresh_opportunities.py` already owns every field phase 2 extracts EXCEPT `url`, and
   `url` is the only one it will never write — so phase 1 is paying search fees for fifteen fields a
   free agent re-derives, and its real product is the program's home page.
3. **The self-learning loop is built end-to-end but starved.** One run per angle, and reason codes on
   40% of negatives, means the diagnosis layer has never spoken. It needs evidence before it needs
   changes.
4. **No angle has failed for being a bad angle yet.** Every codeable negative so far points at the
   URL/pipeline layer. Retiring angles may be solving a problem the data has not shown.
5. **Angle *coverage* is measured (thin cells); angle *quality* and *overlap* are not.** There is no
   measure of two angles searching the same thing, and 0 duplicate queries in the corpus is weak
   evidence that they do not.
6. **New angles come from templates over catalog gaps.** The catalog can only reveal gaps in
   vocabulary it already contains, so this is structurally incapable of finding a category nobody has
   thought of.

---

## 7. Built 2026-08-27 — query visibility in the admin console

`Agents -> New Opportunities -> What it searches -> **What each angle searched**`, backed by
`GET /api/seeds/queries` (localhost-gated like every ops route). Free: it reads local files, makes no
model call and writes nothing.

- **[query_telemetry.py](query_telemetry.py)** — pure, stdlib-only, the judgement half.
  `classify_query` / `summarize_queries` / `summarize_seed` / `summarize_run`. 17 unit tests, every
  case a real query string out of the logs.
- **`ops/core.list_seed_query_runs()`** — the disk half: scans `agent_logs/scraper_<stamp>_seed<id>.json`,
  groups by run, newest first, with a run picker.
- The angle text shown is the one **recorded in the log**, not the current `scraper_seeds` value: an
  angle edited since the run must not be displayed as if it were what ran.

Each query is classified into one of three shapes, and the first is the one that matters:

    broad      names no program — it describes a CLASS. The only shape that can surface something
               nobody has named yet.
    named      carries a specific program or org (quoted, or a capitalised proper noun / acronym).
               Ceiling: one program, and one the model already had in mind before searching.
    metadata   names no program but asks cost / eligibility / contact / deadline — enrichment.

`named` wins over `metadata` when a query is both, because "this search could only ever return one
program" is the more important fact about it. Subject acronyms (AI, UX, STEM, CTF) are stopworded —
without that, the broadest queries in the corpus classify as the narrowest.

**It reports what it does not have.** `missing_runs` lists live scraper runs in `agent_runs` NEWER
than the newest log on disk. `agent_logs/` is local and untracked, so a run executed in a git worktree
writes its telemetry there and removing the worktree deletes it — which is what happened to runs 71
and 73 (2026-08-27, $1.44 between them). A query view that silently showed the newest run it happened
to hold would read as "this is the latest run".

### What it says about the 2026-08-23 run (28 angles, the newest run with telemetry)

    153 searches over 28 angles (5.46 each)   26% broad · 73% named · 2% metadata
    0 silent · 0 retried · 0 repeated queries across angles

So **roughly three in four paid searches could not have discovered anything** — they confirmed a
program the model had already produced. That leaves ~1.4 discovery searches per angle. The full
40-seed corpus reads the same: 30.5% broad, 68.1% named.

---

## 8. Prompt proposal (2026-08-27, revised after operator pivots) — NOT BUILT

Two operator corrections reshaped this section. **(1) Phase 1's product is a program's IDENTITY and
its HOME PAGE, not a profile** — downstream agents crawl from that URL and derive everything else.
**(2) Local is not "institutions"** — private companies run geographically-bounded programs too, and
an institution-only frame misses them.

### 8.1 Phase 1 is buying the one field nothing else can give it, and paying for fifteen it already has

`refresh_opportunities.py` states its own scope: *name, org, summary, type, price, state, location,
intl, season, category, eligibility, grade_min, grade_max, cost, subject_tags, contact_email.* That is
**every field the scraper's phase 2 extracts, except `url`** — and it refuses to write `url`
deliberately, because a no-search model writes URLs from memory (the 26%-dead-link mechanism).

So the division of labour is already decided by the code, and the prompt contradicts it:

    the ONLY field phase 1 is uniquely able to get right   ->  url   (it has grounding; nothing else does)
    every other field                                      ->  owned by refresh_opportunities.py (no search, ~free),
                                                               check_deadlines.py (dates), generate_action_items.py
                                                               (requirements), sitemap_common.py (the site's own pages)

Meanwhile `RESEARCH_SYSTEM` orders a ten-field write-up per opportunity under "never guess", which is
the mechanism behind the measured 73% named-query rate (§7): the model cannot clear that bar without
searching each name it finds. **It is paying a per-search fee to buy data a free agent re-derives
anyway.**

Be precise about what to cut. The cost is in phase 1's SEARCHES, not in phase 2's fields — phase 2 is
a no-search call over notes that already exist, so extracting a field there is free. The rule is:

    phase 1 may not GO LOOKING for anything except programs and their pages
    phase 2 may EXTRACT whatever the notes happen to contain, and null everything else

That keeps the review queue readable (a reviewer still sees name / org / one-line summary / type) at
zero extra cost, while removing the reason the model searches sixteen times for five programs.

### 8.2 What "home page" has to mean, and why the code already agrees

The stored URL is a **crawl root**, not a citation. `sitemap_common.discover_candidate_pages()` walks
the site's own page list from it; `resolve_url_truth()` already trades a low-value sub-page
(apply/FAQ/rules/PDF) UP to a proven canonical landing page whenever grounding offers one. That
trade-up can only fire when a landing URL is *among the retrieved pages* — so a prompt that makes the
model surface landing pages is feeding a mechanism that exists and is currently starved.

The definition to put in the prompt, matching the checks that will judge it (`domain_matches_org`,
`is_content_mill`, `is_low_value_path`, `is_bare_domain`, `title_proves`):

> A program's own page is on the provider's own website — not a round-up, directory or aggregator —
> and is the page about THAT program: not the provider's homepage when it runs many, not the
> application form, not a PDF, not an FAQ. If the program has its own dedicated site, that site's root
> is correct. Copy it verbatim from a page you actually retrieved; never construct one.

### 8.3 The structural change: discovery and URL-resolution become separate, budgeted stages

The honest problem with "just stop searching by name": a broad query's grounding chunks are the pages
that query consulted — listicles and a handful of program pages — so many discovered names would have
**no URL of their own**. Today's confirmation searches are what produce those URLs. So the narrow
search is not removed, it is **repurposed and budgeted**:

    1a  DISCOVERY      N broad, deliberately varied searches   ->  many program NAMES (+ any URLs
                                                                   grounding already resolved, free)
    1b  RESOLUTION     one narrow search per UNRESOLVED name,  ->  the program's own page, title-proven
                       hard-capped by a loop counter
    2   EXTRACTION     no search, notes in, JSON out           ->  whatever fields the notes carry

**1b is already written.** It is `harvest_names.best_resolved_url` (4N) and `refind_dead_links`: one
search, grounding-resolved URL, `url_repair.title_proves` as the evidence bar. Reuse it rather than
rebuild it. Take its free gates too — especially `is_known_name`, which refuses to *pay* to resolve a
name the catalog already has. Today dedupe happens after extraction, so we currently research
duplicates at full price.

Three things this buys that the current single-stage shape cannot:

- **Coverage stops being capped by one SERP.** Today discovery is effectively one query and everything
  after it is enrichment, which is why a very broad angle and a narrow gap-fill angle both return five
  (§5.3). Names scale with 1a; URLs scale with 1b.
- **The search budget becomes real.** `--max-searches` is English in a prompt and has never bound
  (5.33 against a cap of 10). A 1b loop counter is enforced in code, like `harvest_names`' caps.
- **Unresolved names stop being losses.** A name we decline to resolve within budget is written out as
  a 4N lead, and a round-up page consulted along the way is already captured by 4F.

**Modelled, not measured** — flagged as an estimate because that is what it is:

    today      ~5 searches -> ~5 programs      ~$0.09/angle    ~$0.018 per program
    proposed   5 broad + <=10 resolution        ~$0.25/angle    ~$0.012 per program at ~20 programs

Cost per angle roughly triples; cost per program falls and coverage per angle roughly quadruples. If
that ratio does not hold in an A/B, the change does not ship.

### 8.4 National prompt — draft

Objective (replaces the ten-field brief):

> Your job is to find as many DISTINCT programs as possible matching the angle, and for each one to
> identify its own official page. You are not writing a profile. Later agents read each program's own
> page and take eligibility, cost, dates and requirements from it — so do not spend searches
> confirming those, and do not omit a program because you could not establish them.

Query policy (the part that fixes the 73%):

> Run {n} DIFFERENT broad searches before you write anything. Each must describe a CLASS of
> opportunity — never a specific program by name. Vary them deliberately: swap the program noun
> (program / institute / academy / intensive / fellowship / challenge / competition), swap the audience
> phrasing (high school students / teens / grades 9-12 / pre-college), swap the host type (university /
> national lab / museum / professional society / nonprofit), and include at least one list-shaped query
> ("list of ... for high school students 2027", "... directory"). Round-up and directory pages are
> valuable to us: take every program name they list.

Output shape (terse lines, not paragraphs — the paragraph is what implied the model had to know that
much):

> One line per program: **Name** — organisation — one sentence on what it is — the URL of its own page
> if one of the pages you retrieved IS that program's page, otherwise "no url". A name with no URL is
> still worth writing down.

The invention ban stays verbatim. What is added is the permission that removes the pressure behind the
narrow searches: *not knowing eligibility, cost or dates is expected and is not a defect.*

### 8.5 Local prompt — draft (pivot 2: provider type is irrelevant, geography is the whole point)

The earlier "enumerate institutions" framing was too narrow, and the operator is right about why:
a coding academy, an art or music studio, a dance company, a makerspace, a clinic, a family business
offering an apprenticeship, or a startup taking a high-school intern are all real local opportunities
and none of them is an institution.

**What actually makes something local is that its audience is geographically bounded** — you have to be
able to get there, or it is only offered to students from that area. Provider type is not the
definition and must not be the filter.

The measured failure of search-first local (2026-08-18, and the reason 4L was pinned) has a specific
cause worth naming: **a generic description-shaped query is won by national content marketing with the
city appended.** A small local provider does not out-rank an SEO listicle for "summer program Seattle".
Two levers answer that directly:

- **Anchor on the SMALLER place names, not the metro.** Neighbourhoods, suburbs, the county, the school
  district (Ballard, Capitol Hill, Bellevue, Redmond, Issaquah, King County, Lake Washington School
  District). National listicles do not rank for "Redmond teen coding academy".
- **Search where local programs are INDEXED, not only the programs themselves.** Parks-and-recreation
  activity catalogues, school-district community-education bulletins, library event calendars, regional
  parenting and camp guides (the local equivalent of a listicle — ParentMap-shaped), chambers of
  commerce, community-centre schedules. Each lists dozens of local programs, and each is exactly the
  feedstock 4F and 4N consume. **This is the local analogue of "ask for round-ups", and it is probably
  worth more locally than nationally.**

Draft objective:

> Find programs a high schooler living in {place} could actually take part in — programs whose audience
> is bounded to that area. The provider can be ANY kind: a city or county department, a library, a
> museum, a school district, a university, a hospital, a nonprofit, or a private business — a coding
> academy, an art or music studio, a dance company, a makerspace, a lab, a local employer's teen
> programme, or a startup willing to take a high-school intern. A commercial provider is fine; price is
> not your concern. Do not skip a program because the provider is small or its website is thin.

Draft query policy:

> Generic searches will be won by national programs with the city's name appended, so: anchor EVERY
> query to a place name, and prefer the smaller ones — {sub_places} — over "{place}" alone. Spend at
> least half your searches on pages that LIST local programs rather than on individual programs: parks
> and recreation activity catalogues, school district community-education bulletins, library event
> calendars, regional parenting or summer-camp guides, chamber of commerce member directories,
> community centre schedules.

**Implementation note, no DDL required.** `mode` is currently `national` | `seattle`. Rather than a new
`place` column, keep mode as the locale key and add a small `LOCALES` dict in `scrape_opportunities.py`
holding each locale's display name and its `sub_places` vocabulary. Adding a second city then means
adding a dict entry, not a migration.

**Risk specific to local, not yet measured:** the free gates were calibrated on national programs.
`title_proves` needs two identity words in a page's `<title>`, and a small provider's title is often
"Home | Studio Name". Local resolution may therefore fail proof far more often than national — so
measure the proof rate on a local arm BEFORE concluding the channel does not work, or the gates will be
blamed for the geography and vice versa.

**4L is still PINNED.** Nothing here gets built for local without the operator lifting that.

### 8.6 How to decide it cheaply

One angle, two arms, identical everything else, ~$0.20-0.40 — the shape of the prose-vs-JSON A/B that
settled the two-phase design. The console card (§7) reads out the first two with no new tooling:

    breadth %                      26% today  ->  target >70%
    distinct programs per angle    ~5 today   ->  target 15+
    cost per NEW program           the number that actually decides it
    URL resolution rate            share of named programs that end with a title-proven own-domain
                                   page — this is the deliverable now, so it is the acceptance metric
    FLAG_TITLE_UNPROVEN rate       must not rise; it is the risk in 8.3

Run the local arm separately with its own baseline. A single blended number would hide whichever half
is failing.

Do not ship the prompt on argument. Ship it on that table.

---

## 9. Not in scope for this document

URL truth and title-proof, dedupe/merge, the review queue and its reason codes as a *UI*, hub mining
and name harvest as *channels*, Phase 4L local strategy (pinned by the operator). Each is covered in
[SCRAPER_IMPROVEMENT_PLAN.md](SCRAPER_IMPROVEMENT_PLAN.md); this file touches them only where they
constrain the angle layer.
