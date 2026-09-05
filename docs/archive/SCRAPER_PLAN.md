# Scraper rework plan

Status: **implemented and validated live, 2026-08-23.**

Validated across **all 40 angles** from the 2026-08-20 batch, live
(`agent_runs` id=49 + id=50, `source='scraper-national-20260823'`): **166 rows, $3.607**.

| metric | 08-20 (old scraper) | 08-23 (rewrite) |
|---|---|---|
| rows | 116 | 166 |
| dead links (404/410) | 30 (**26%**) | **0 (0%)** |
| confirmed live | 64 (55%) | 146 (**88%**) |
| bare root domains | 46 (40%) | 13 (8%) |
| live but a third-party article | not measured | 10 (6%) — see below |

**A live link is not yet a correct one.** Auditing the batch page-by-page afterwards found a
second failure the headline numbers hide: 10 rows pointed at an SEO round-up that merely
mentions the program. `reconcile_url` and a tenth review flag address it — *"The second
failure mode"* below. That fix is **not** reflected in the 166 rows above; they were written
by the code as it stood during the runs.

Of the 30 dead rows in the old batch, 9 were re-found on the same site by the rerun and
**9 of 9 came back with a live URL** — including
`tisch.nyu.edu/…/summer-filmmakers-workshop` (dead) -> `…/filmmakers-workshop` (live) and
`med.stanford.edu/psychiatry/education/CNIX.html` (dead) -> `…/highschool.html` (live). The
other 21 were simply not returned again: the model searched fresh, so this is "same angles,
new results", not a row-for-row repair pass.

Across both runs: 192 raw candidates -> 166 inserted, **26 rejected (all exact duplicates),
0 invalid, 0 errors**. 60 rows carry review flags. The silent-search retry fired on 2 of 40
seeds and **succeeded both times** (final: 0/40 silent). Phase 2 costs ~$0.008 against phase
1's ~$0.08, so the two-phase split does not materially change the bill:
**$0.090/seed, $0.022/row, ~36s/seed.**

## Root cause

**Gemini decides per call whether to search, and that decision is stochastic.** It cannot be
forced. When it does not search, the model writes URLs from memory: right host, path off by
one segment.

The decisive evidence is `agent_logs/`: **seed 51 was run twice with an identical command and
prompt** and behaved differently each time —

```
--seed-ids 51 ... --dry-run  ->  0 search(es) [SILENT]  $0.0062
--seed-ids 51 ... --dry-run  ->  6 search(es)           $0.0900
```

There is a **bias toward answering from memory when the topic feels familiar**:

| seed / probe | topic | searched? |
|---|---|---|
| 53 — "pre college programs hosted in US universities" | very mainstream | 0 |
| probe v1 — Juilliard pre-college | very mainstream | 0 |
| probe v2 — NASA/NIH deadlines | mainstream orgs | 0 |
| 51 — "conferences accepting high school papers" | middling | **0, then 6** |
| 52 — "reddit /summerprogramresults" | obscure | 6 |

A 2026-08-23 probe also varied the output format (JSON-only vs prose) and saw 0/0/2
searches. That *may* add bias on top of familiarity — both JSON runs opened their output
with `[` while the prose run opened with prose — but **seed 51's split shows the noise floor
is far too large for three samples to establish it.** Do not treat the format as a cause.

Ruled out: **it is not the thinking budget.** Probe v2 spent 162 thinking tokens and did not
search; v3 spent 107 and did.

`wingman/gemini_common.py`'s "THIRD finding" docstring — that no reliable way exists to force
search — **is correct and should stand.**

Consequences of a silent seed:

- `scrape_review_national_20260820.json`: **30 of 116 URLs are hard 404s (26%)**. Every 404
  is a constructed deep path; none is a bare domain.
- Activated scraper rows are only 5% dead (6/128), vs 10% for the hand-curated
  `wingman-seed` baseline. The failure is **per-run**, not inherent to the scraper.
- Across all 15 runs, cost/seed spans **$0.0044 to $0.6227** — and the cheap seeds are the
  silent ones. `agent_runs` id=8 ran 12 seeds, all silent, for $0.053, and **36 of those rows
  are live to students today**.

**Because search cannot be forced, the mitigation is to detect and retry, not to prompt
harder.** Seed 51 going 0-then-6 on identical input is direct evidence a retry works, and a
silent seed costs only ~$0.006-0.01 — so retrying whenever `searches == 0` is nearly free and
likely to succeed. If the retry is still silent, the batch is memory-recalled and the
reviewer must be told (flag 6).

## The discarded evidence is the fix

`gemini_common.call_gemini` reads `groundingMetadata` only as `len(webSearchQueries)` and
returns text alone. Everything else is dropped. Confirmed by probe:

- **`groundingChunks[].web.uri`** is a `vertexaisearch.cloud.google.com/grounding-api-redirect/…`
  URL that **resolves to the exact real page in one free HTTP hop**. (`web.title` is only a
  bare domain string; `web.domain` does not exist on `v1beta/generateContent`.)
- **`groundingSupports[]`** gives `segment{startIndex, endIndex, text}` plus
  `groundingChunkIndices` — per-opportunity attribution, not just whole-response.
- **`webSearchQueries`** holds the actual query strings, e.g.
  `'summer 2027 high school research program application deadline'`. The angles translate
  fine; they often just were not searched.

Decisive comparison — 4/4 memory-typed URLs dead, 4/4 grounding-resolved URLs live:

| program | model typed | grounding-resolved |
|---|---|---|
| Sanford PROMISE | 404 | **200** |
| NASA internships | 404 | **200** |
| GTRI STEM | 404 | **200** |
| NIH SIP | 404 | **200** |

The NIH row is the catalog's existing dead link: stored
`training.nih.gov/research-training/sip/` (404) vs grounding's
`training.nih.gov/research-training/pb/sip/` (200).

---

## Design

### The contract

```
seed = (mode, angle)
  -> Gemini turns the angle into its own search terms
  -> returns opportunities, each typed from the enum
  -> we resolve real URLs from grounding, validate them, and flag anything imperfect
```

The seed says *where to look*. The model says *what it found*. We verify and explain.

### Two-phase call

Justified by *"we need the grounding data and a JSON-only call cannot carry it"* — **not** by
any claim that JSON-only causes silence. See the root-cause section.

- **Phase 1 — research.** Prose output, `googleSearch` on. **Retry once if `searches == 0`.**
  Harvest `groundingChunks`, resolve the redirects (free HTTP), use `groundingSupports` to
  bind sources to spans.
- **Phase 2 — extract.** Feed phase 1's prose + the resolved URLs back in; ask for the JSON
  array with `type` from the enum. No search needed, so JSON-only is harmless here.

If phase 1 is still silent after the retry, its output is memory-recalled: proceed, but flag
every row from that seed with flag 6.

Phase 2 is token-only and small. Net effect: standardise on today's *searching* seed price
(~$0.03-0.09) instead of oscillating between $0.005 seeds that fabricate and $0.09 seeds
that work.

### Guiding rule: discard almost nothing, explain everything

Insert anything that could be real, at `is_active = false` with
`moderation_status = 'pending_review'` and a **short reason the reviewer can act on**.

**Hard reject — one case only:** `url_dedupe.find_duplicates()` returns an exact duplicate
(same normalized URL *and* matching name). Genuinely already in the catalog.

**Cannot insert:** no URL at all — the URL is the row's identity. Goes to the snapshot with
its raw JSON and is counted.

**Everything else: insert + flag.**

### Flag taxonomy

`quality_flags` is an existing `jsonb` column of human-readable strings
(`../../db/user_submissions_schema.sql`), already rendered by the console as warn pills. **Pills
truncate at 90 characters** (`admin_console.html:1308`) — keep every reason under that, and
add a `title=` attribute carrying the untruncated text.

| # | flag text | trigger |
|---|---|---|
| 1 | `dead link (404) — program may be real; find the correct URL` | HTTP 404/410 |
| 2 | `link unverifiable (403) — site blocks checks; open it manually` | HTTP 403/429 |
| 3 | `link timed out — could not reach the site; open it manually` | timeout / DNS |
| 4 | `URL is a site homepage, not a program page` | bare root domain (was 40% of the 08-20 batch) |
| 5 | `URL is a sub-page (faq/about/apply), not the main page` | `url_dedupe.is_low_value_path()` |
| 6 | `not search-verified — model answered from memory; check every field` | seed ran 0 searches |
| 7 | `URL not among the pages actually searched — may be from memory` | model URL matches no resolved chunk |
| 8 | `URL replaced with the page the search returned — confirm it matches` | we substituted a grounding URL |
| 9 | `no valid type returned — set one before activating` | `type` not in `VALID_TYPES` |
| 10 | `URL is on an unrelated site — may be an article about it, not its own page` | `url_validate.domain_matches_org()` is false |

Possible duplicates keep using the existing separate `dup_candidates` column and its own pill.

Flag 6 is the important new one: it marks a whole seed's output as memory-recalled, which is
exactly the condition that produced the 08-20 batch.

Flag 10 was added **2026-08-23, after the batch**, and covers the failure the other nine
miss — see the section below.

---

## The second failure mode: live, and still the wrong page

Fixing dead links did not finish the job, it changed the shape of the remaining problem. Auditing all
166 rows by fetching each page and comparing its `<title>` to the row: **10 rows (6%) stored
a third-party SEO round-up** rather than the program's own page —
`ladderinternships.com/…/19-selective-internships…` for the Stanford AIMI internship,
`indigoresearch.org/blog/stem-internships…` for NASA OSTEM,
`futureforward.app/blog/journals…` for NHSJS.

**Every other check passes these.** They return HTTP 200, so `check_urls` is happy; they have
a deep path, so `is_bare_domain` is happy; the path is not `/faq/`, so `is_low_value_path` is
happy. A link that is live and wrong is worse for a student than one that is obviously dead.

Two distinct causes, and they need different answers:

1. **`reconcile_url` discarded a verified URL for an unverified one.** When grounding
   attributed a span, the function took `span_urls[0]` without first asking whether the
   model's own URL was *itself* one of the retrieved pages. It was, in 33 of the 166 rows —
   including `aimi.stanford.edu/education/summer-research-internship`,
   `stemgateway.nasa.gov/…/high-school-internships` and `nhsjs.com/submit-your-work/`, each
   thrown away for a blog. Replaying the whole batch, hoisting that check changes 33 rows,
   improves 5 measurably and **worsens none**; by eye ~20 more of the "neutral" changes are
   also better (`naclo.clsp.jhu.edu` over `linguistics.cornell.edu/outreach`,
   `speechanddebate.org/national-tournament-2026` over an `aralia.com` article).
2. **The model simply typed a listicle URL**, which no ranking can repair — 5 of the 10.
   These get flag 10 instead.

`domain_matches_org()` is deliberately generous, since a wrong "unrelated" flags a good row.
Matching is by **substring against each domain label**, because a domain label is words run
together: an exact-token rule called `idyllwildarts.org`, `tellurideassociation.org` and
`artandwriting.org` unrelated to their own owners and fired on **58%** of the batch.
Abbreviations count in both directions (`colum.edu` for Columbia College, `umich`/`upenn`
for those universities), as do acronyms and initials — with parentheticals stripped first,
or "Fermi National Accelerator Laboratory (Fermilab)" yields `fnalf` and misses `fnal.gov`.

Final: **16% of the batch flagged, all 10 known cases caught**, and of the 27 flagged rows
about 25 are genuine off-site links. Regression suite: `scratchpad/test_matcher.py`, 39 cases.

---

## Changes

### 1. Drop the seed category

The category is **never sent to the model** — `SYSTEM_BASE.format()` interpolates only
`today`, `angle`, `subjects`. It is used only after the response, as a `type` fallback and a
provenance column.

Measured: the model's type disagrees with the seed category on **65 of 238 rows (27%)**, and
those rows are all in the catalog with 34 live — nothing is discarded on type today. Per
category the seed is a poor predictor: Research seeds are overridden **65%** of the time,
Internship **56%**.

The fallback goes away. Invalid type -> flag 9, never a guess.

| file | change |
|---|---|
| `agents/scrape_opportunities.py` | `NATIONAL_SEEDS`/`SEATTLE_SEEDS` tuples -> strings; `build_row()` drops `seed_category`; lines 234, 250, 308, 349, 351, 396 |
| `wingman/seeds_common.py` | `SEED_SELECT`, fallback dict shape, docstring |
| `server.py` | `SEED_FIELDS` (:4683), `SEED_SELECT` (:4684), `create_seed()` validation (:4744) |
| `admin_console.html` | remove category picker, pill column (:2778), payload (:2815), dropdown label (:2938) |
| `../../scripts/one-off/migrate_seeds_to_supabase.py` | one-off, already run — update for consistency |

Two schema facts make this cheap:
- `opportunities.category` is **nullable and already NULL on 1139/1440 rows**. Just stop
  writing it. No DDL, no backfill.
- `scraper_seeds.category` is **`not null`** and this repo cannot run DDL, so `create_seed()`
  writes a placeholder while the console stops asking. Optional cleanup DDL later; nothing
  waits on it.

### 2. Stop discarding the evidence

- Log `webSearchQueries` — the real query strings, already paid for, currently reduced to
  `len()`. This is the control surface for tuning angles.
- Save the raw response per seed to `agent_logs/`. No run has ever done this, which is why
  seed 52's $0.18 (5 candidates, all rejected, twice) is unexplainable.
- Log every rejection with its reason, so the ~40% discard rate (71 of 176 candidates across
  all runs) becomes auditable.

### 3. Swap the dedupe rule

Replace `is_duplicate()` with `url_dedupe.find_duplicates()` — already written, already used
by user submissions. The current rule rejects on URL alone (breaks shared portals: one
SMApply portal backs six programs) and on bare name similarity >= 0.85, which matches **264
pairs in the catalog, 257 of them genuinely different opportunities** (`'Summer Internship'`
collides with everything).

### 4. Split angle from queries (optional, after #2)

One field currently does two jobs. Seed 26 reads *"robotics, maker spaces, … **(distinct from
general CS/engineering — hands-on building, robotics competitions, invention)**"* — the
bolded half is guidance for the *judge*, sitting inside the sentence we tell the model to
*search for*. A separate `queries` field would let each be tuned for its actual job. Do this
only once #2 makes the effect measurable.

---

## Order

| step | spend | note |
|---|---|---|
| 2. Visibility | none | do first; unblocks judging everything else |
| 1. Drop category | none | mechanical, no DDL |
| 3. Dedupe swap | none | reuses existing tested code |
| Two-phase call + flags | one seed to verify | the core change |
| 4. Split queries | one seed | optional |

Steps 1-3 are buildable and verifiable without spending anything.

## Not doing

- **Feeding the catalog into the prompt** to cut the ~40% duplicate rate — parked; needs its
  own paid A/B.
- **Auto-rejecting anything on name similarity or shared domain** — `wingman/url_dedupe.py`'s
  measurements forbid it.
- **Backfilling the 110 untriaged 08-20 rows** — left alone by decision.
