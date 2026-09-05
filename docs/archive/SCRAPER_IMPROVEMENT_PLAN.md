# Opportunity Scraper v2 — self-learning pipeline plan

*Rewritten 2026-08-26. Self-contained: a fresh session can pick this up with no other
context. Design APPROVED by the operator (tenets + decisions below). **All phases P1-P5
BUILT and P4 code shipped 2026-08-27 — see the Implementation Status section immediately
below before reading the phase specs, which are now the historical design record.***

---

## ⭐ START HERE — session of 2026-08-27 (evening). NEW SESSION PICK-UP.

**`main` is well ahead of `origin/main` and NOT PUSHED** (count it — `git rev-list
--count origin/main..main` — rather than trusting a number written here, which every commit
since has made stale). Gate on every commit:
**1484 tests green, `grade_scraper_batch` url-dup 0 regressions, SAFE.** (The
`[suppress-all] … UNSAFE` line is the harness's own self-test probe, not a failure.)

### The system, in one screen

```
search angle -> search -> URLs
  |- the program's own page ------------------> catalog row      (unchanged)
  '- a third-party page about programs -------> ROUTER, never discarded
       |- LINKS them  -> hub mining    (free to follow, ~$0.0015/page to read)
       '- NAMES them  -> name harvest  (PAID, ~$0.019/name)   [operator-pointed only]

a row we already activated -> its PARENT url -> does the parent link it back?
       '- yes -> a same-domain hub lead   (wingman/walk_up_hubs.py, FREE, no classifier)
```
Second feed: **rejecting a row as a round-up in the console queues it automatically.**
Third feed: **the catalog itself** — see the walk-up below, which is where hubs now come from.

**The router's whole rule** (`discovered_leads.classify_page`, FREE):
```
0 anchors in the html?              -> we never received the page. no verdict.
title promises many opportunities?  -> no: not a lead   (site name stripped first)
>= 6 distinct OFF-domain domains    -> hub lead
>= 2000 chars of HS-audience prose  -> names lead
```
It opens **every** candidate concurrently — 17 pages in 1.4s — so nothing is rationed.

### The operator's re-framing, and how kind B was closed

The operator's position, which the measurements support: the useful split is not links-vs-no-
links, it is **(A) pages linking programs on OTHER domains** (third-party round-ups) versus
**(B) pages linking programs on their OWN domain** (Stanford/Berkeley pre-college indexes).

- **Kind A works.** Off-domain links are rare on a normal page, so >= 6 distinct off-domain
  domains separates cleanly (round-ups 9-20, ordinary pages 1-3).
- **Kind B is NOT detectable from link structure, measured three ways.** Over 6 real seeds
  (109 candidates): raw same-domain count calls **42% of everything** an index — FIT's *costs*
  page scores 401, a PR release 318, Berkeley's *FAQ* 93; nav-subtraction overlaps (good 0-57
  vs bad 0-35); and links-under-own-path gives **0 for 3 of 6 real indexes** (Georgetown,
  Ringling, LIM keep programs at a different path than the index). Cause is structural:
  same-domain links on any page are dominated by the shared nav.

**BUILT 2026-08-28 — `wingman/walk_up_hubs.py` derives kind B instead of detecting it.** When a search
returns `ced.berkeley.edu/academics/summer-programs/**summer-institute**` and it becomes a row,
its **parent** is very likely the index of its siblings. So walk UP from a program we already
trust and make the parent **prove** it:

    the proof is the BACK-LINK. a parent that does not link the child is a shorter
    URL, not an index -- which is exactly why the 42% false-positive rate above is
    irrelevant here: a costs page does not link the program pages, so it cannot pass.

- Only **activated** rows are walked up from (the strongest trust signal here — nothing in this
  repo activates anything on its own), never up to a **root homepage** (business.wisc.edu's root:
  40 links, 2 gems), never to a parent the router already calls impossible (a `/blog/` post's
  parent is a blog index), and never a parent that redirects onto the child or links nothing
  beyond it.
- **First live sweep, FREE (plain HTTP, no model call anywhere): 1325 active rows -> 612 distinct
  parents -> 584 looked at -> 244 proven indexes**, including `ced.berkeley.edu/academics/
  summer-programs/` (this plan's own worked example), `cmu.edu/pre-college/academic-programs/`
  (12 of our rows under it, 25 more programs on it), Brown, BU, Vanderbilt, Tufts, UCSF SEP.
  171 parents were unreadable and 171 judged not an index — counted **separately**, because an
  unreadable page is a fact about our HTTP client, never about the institution.
- All 244 are **queued and unmined**. Queuing is free; reading them is the paid step.
- Curation (`hub_pilot_national.json`, 5 hand-picked hubs) is no longer the only route to these
  pages — which mattered, because it had produced the only successful hub-mining run.

**`scope` now travels ON a hub lead** (`off-domain` / `same-domain`), because a round-up must be
mined off-domain and one of these same-domain, and only whatever qualified the page knows which.
`mine_hub_pages --from-leads` had a flat `True` for every lead and a flat `False` before that —
each correct for one kind of lead and wrong for the other. A lead with no `scope` reads as
off-domain, since every lead already on file came from the router.

### What shipped this session

- **Phase 4N `agents/harvest_names.py`** — names off a page, resolved via the refind primitive. Three
  free gates; ranking before the cap (**A/B'd live: 1 row -> 3 rows at identical cost**, self-
  promotion 1-of-1 -> 0-of-3); a **score floor** replaced the count cap.
- **Phase 4F `wingman/discovered_leads.py`** — the router. Structural, no host list, no budget.
- **Live runs (PAID $0.55 total):** hub mining 19 rows, name harvest 12 rows across 3 runs.
  **31 rows await review.**
- **Console:** *Harvest Names From a Page* joins *Mine Hub Pages*. Free preview by default.
- **Reject hook:** the reason `third-party-roundup` (split out from the old lumped
  "article / listicle / video") queues the page automatically, fire-and-forget.

### Bugs the live runs and the operator found — all fixed, all worth not repeating
1. Hub extract prompt never listed the legal `type` values — **19 of 19 rows** needed a human.
2. Hub miner did not collapse in-run twins (`/accelerated-learning-program/` + `/alp/`).
3. A **U+2011 in model output crashed a run** after its paid call returned (cp1252 console).
4. `FLAG_SELF_PROMOTED` — the first name-harvest run returned 3 rows, **all 3 the source
   site's own products**, because a round-up names its own products canonically and everyone
   else's descriptively.
5. `dup_candidates` was hardcoded `None` — a row went in on the SAME URL as ec17751, unflagged.
6. One page names one program twice (`…(BWSI)` and without) — paid twice, inserted twins.
7. Title test matched the **site's own name** in the suffix (`… - Opportunities for Youth`).
8. **A page with 0 anchors is a page we never received, not one without links.** A Wix round-up
   returned 400KB and 0 `<a>` tags; it was routed to PAID name harvest where the same fetch
   finds the same nothing. **10 of the 11 queued name leads were this.**
9. **`--from-leads` mined hub leads SAME-domain while the router qualifies them OFF-domain** —
   all 25 queued leads would have been mined for their own nav.

### Costs, fitted to real runs (reconcile to $0.0001)
| step | per | cost |
|---|---|---|
| hub mining — read a page | page | **$0.0015** |
| name harvest — read the names | page | $0.0038 |
| name harvest — search one name | name | **$0.0167** (84% is the flat search fee) |
| name harvest — make a row | row | $0.0047 |
| re-find a dead link | row | $0.02–0.05 |
| walk-up: catalog -> proven index | — | **$0.00** (one free fetch per parent) |
| hub mining, measured over 300 pages | page | **$0.00096** ($0.2884 / 300, incl. refusals) |
| router, filters, dedupe, review, loop | — | **$0.00** |

### OPEN / do next
1. ~~Push `main`~~ **PUSHED 2026-08-28.** Verified before pushing: the batch touches no `app/`,
   `frontend/`, `render.yaml`, `requirements.txt`, `server.py` or workflow file, so the Render
   deploy is a genuine no-op for the web service.
2. ~~Mine a walk-up lead as a pilot~~ **DONE — and then a 43-hub batch. See the session block
   below.** 158 rows now await review. **240 walk-up leads remain queued**; drain them in
   batches with a `--max-pages` ceiling, and expect the ~50% refusal rate that run measured.
3. ~~Review the 31 pending rows~~ **DONE by the operator.** Verified live 2026-08-28: of the 35
   rows from hub mining / name harvest / refind, **32 are approved and ACTIVE and 3 rejected**.
   The queue holds **3 rows**, all from `scraper-national-20260826` (NSLI-Y twice, SCA). Do not
   trust a pending count written in this file — read it from the table.
4. Queue today: **277 hub leads (244 same-domain from the walk-up, 33 off-domain round-ups),
   5 names leads.** Each is now mined the way it qualified; mining is PAID and gated.
5. ~~Catch-up for the pre-hook rejected backlog~~ **DONE 2026-08-28, free**: `--from-rejects
   --any-reason --commit` over the whole pile — **349 rejected rows -> 12 leads** (8 hub, 4
   names), 9 ignorable, and **323 remembered as not-a-round-up** so no later sweep re-fetches
   them. The 93% no-verdict rate is the expected shape: most rejects are ordinary program pages
   rejected for wrong-page/dead-link/duplicate, which say nothing about being a round-up.
6. **~22% of candidates cannot be read at all** (403/PDF/JS). They are dropped with no verdict.
   That bucket — and only that bucket — is where grounding footnotes or an LLM classifier could
   help. Deferred by the operator.
7. **4L (local) stays PINNED.** Two designs measured, neither worked.
8. Two files from a concurrent session sit untracked and were deliberately left alone:
   `../plans/ANGLE_STRATEGY_PLAN.md`, `../MATCHING_UX_REQUIREMENTS.md`. That session is also editing
   `ops/admin.py`, `ops/admin_console.html` and `ops/core.py` in this working tree (angle
   query telemetry) — do not stage those with scraper work.
9. **The plain-English picture of all of this is the published *Scraper Logic Map* artifact**
   — <https://claude.ai/code/artifact/5a5c5614-e561-4f28-9f0f-cac1c30ada99>. It carries the
   same rules, the fitted cost table, and the kind-B gap. Update it in place (pass that URL)
   when the pipeline changes; do not publish a second copy.

### Session 2026-08-28 — kind B closed, two live bugs fixed
- **`wingman/walk_up_hubs.py` built + swept** (above). 1516 tests green (1533 collected locally, 17 of
  them a concurrent session's untracked `test_query_telemetry.py`); `grade_scraper_batch` url-dup
  **0 regressions, SAFE**. 244 leads written.
- **`wingman/discovered_leads.py --list` crashed on the live file.** A remembered NO carries `kind=None`
  and the listing formatted it with a width spec (`f"{None:5}"` raises); 10 such rows are on
  disk, so the command this handoff tells the next session to run did not work at all. The
  default listing is now the actionable work-list; `--all` shows the whole file.
- **A Hawaiian okina (U+02BB) in a row name killed the first full sweep** on a cp1252 console —
  after every fetch had completed, the same crash class the hub miner hit on a model's U+2011.
  `safe_console()`, and leads are now **written before they are printed**: rendering must never
  be able to discard finished work. (Free here, but the same shape as losing a paid call's
  result.)
- **NOT done, deliberately:** no console tile for the walk-up. `ops/` is being edited by a
  concurrent session in this shared tree, and the plan's own rule is to stage only scraper files.
  Wiring *Walk Up From The Catalog* beside *Mine Hub Pages* is a clean follow-up.

### Session 2026-08-28b — both extractors exercised live, five bugs found by doing it

**PAID: $0.3280 total** ($0.0396 CMU pilot + $0.2884 the 43-hub batch). 1 agent_runs row each.

**The batch:** 33 off-domain round-ups + the 10 densest walk-up leads -> 346 new candidates ->
300 extracted (ceiling) -> **144 rows**, 0 errors. Both halves work. Off-domain yielded real
programs at real institutions (UCLA CSSI, Toronto DEEP, UChicago Data Science Summer Lab,
Syracuse, Stanford AI4ALL, NYU Shanghai, NIMH SIP, Columbia BRAINStorm); the walk-up half
produced the first LOCAL rows the project has ever had (Seattle Parks teen life centres, Teen
Summer Musical, YMCA BOLD & GOLD).

**MEASURED: the extractor refuses 52% of pages** (300 read, 144 rows). That is the answer to the
open question from 4N — the `{"name": null}` refusal is doing the chaff filtering, not the free
slug list, which must therefore NOT grow into one.

**Five bugs, each found only by running it:**
1. **The pre-spend catalog check suppressed NOTHING.** `find_duplicates(u, "")` — its exact rule
   is "same URL AND similar name" by design, so an empty name always fell through to a hint the
   caller ignored, while the comment claimed the catalog was checked. **12 of the 14 CMU rows
   duplicated rows we already had**, and the walk-up lead had predicted it in as many words
   ("links 12 program(s) we already have"). `fresh_candidates()` is URL-only, which is right here
   and would be wrong at the scraper's insert layer where one portal backs six programs.
2. **The link cap truncated by POSITION IN THE PAGE.** `filter_hub_links` broke at 25 survivors,
   and a page's first links are its chrome — so on seattle.gov (974 links) the cap filled with
   navigation and every real teen program was never judged. The operator found this by pointing
   at a program we had missed. Now every link is judged, the cap applies last, and `over_cap` is
   reported (907 on that page).
3. **The run ceiling starved whichever hubs came last** — all 46 skipped candidates were walk-up
   leads, so Brown/BU/Vanderbilt/UCSF reported zero rows because they NEVER RAN. `allocate_budget()`
   is round-robin. Same failure as (2), one level up.
4. **A candidate's redirect was invisible.** Nine CMU `/student-affairs/...` links all 302 to the
   index; deduping on the requested URL made them nine candidates. `page_text.fetch_page_text_resolved`
   returns the landing URL (two-value form unchanged, like `call_gemini(return_grounding=True)`).
5. **`FLAG_OFFSITE` was wrong 94% of the time on hub rows** — 16 of 17. It asks "did a model type
   somebody else's URL?", and a followed link is real by construction. Suppressed for hub-mined
   rows; a content mill still flags. NOT fixed by loosening `domain_matches_org`, which also gates
   url_repair's acceptance of a re-found link.

**Also built:** `--max-pages` (spend ceiling, spread evenly, and a truncated hub stays queued),
`--give-up-after` (abandon a hub after N refusals — non-trivial.org spent 17 extractions for
zero rows), per-hub yield in the run summary, shortener/booking/form hosts dropped (never
`docs.google.com` — it collapses to google.com and would take "Doodle for Google" with it), and
a **Discovery leads card in the console** plus "take N from the queue" on Mine Hub Pages. The
queue had no surface at all before: the console wrote to it and never showed it.

**Dedupe analysis, done and DEFERRED by the operator.** Every same-site pair in the catalog with
a different URL and name similarity >= 0.85 was resolved: **96 pairs, 5 are true aliases**
(`/alp/` 301s to `/accelerated-learning-program/`), **91 are genuinely different programs**. So
suppressing on name similarity would destroy 91 rows to catch 5 — the existing "suppress only on
proof" rule is measured-correct and must not move. A redirect-equality proof tier was designed
and NOT built; the 5 known aliases are ec18774, ec18771, ec18856, ec18918, ec18865.

### Rejected ideas, with the reason (do not re-propose without new evidence)
- **An LLM classifier at the search-results step.** Costed at **$0.02–0.07 per 30-seed run**, so
  cost is NOT the objection. It was rejected on evidence: at that point we hold the URL string
  and phase 1's prose, **not the pages**, so it would infer from a slug what a free fetch reads
  from the page. Folding it into phase 2 is additionally unsafe — that call is capped at 6000
  tokens and `extractJSON` silently REPAIRS a truncated array, so competing for its budget can
  lose candidates invisibly.
  **(2026-08-30: REVISITED — see "the page-classifier gate" section below. The objection was
  "not the pages"; the new design FETCHES the full page first and classifies from its text,
  which is exactly the missing premise. It is a SEPARATE no-search call, not folded into phase
  2, so the token-budget objection also does not apply.)**
- **A grounding-footnote prioritiser.** Sound idea (a URL cited across many program spans is a
  round-up), but it existed to rank a 12-page budget that no longer exists.
- **Same-domain link counting for kind B.** See the three measurements above.

---

## Session 2026-08-30 — PLANNED (design AGREED with the operator, NOT yet built)

**The page-classifier gate + content-embedding dedupe.** Two new signals, both computed from the
FULL fetched page and both riding ONE fetch, aimed at one goal: **the review queue fills faster
than the operator can clear it, so move the operator from in-the-loop toward spot-checking.** The
gate does not replace review — it pre-sorts and pre-justifies it, and starts routing the
extractor's discards into discovery leads instead of losing them.

**Conservative by explicit operator choice:** in v1 the MODEL's judgment never drops a would-be
program row and nothing auto-activates. The only new DROP is a deterministic date rule the
operator asked for (below). Everything else is a label, a reroute, or a hint.

**Two marquee asks stand, un-ratified until the prompt/paid path are approved per M8/M9:** the
classifier PROMPT (M8) and the new PAID calls — one classify call per candidate + embeddings
(M9). Building and unit-testing the modules is FREE; money only lands on the eval, the backfill,
and a live run, each separately gated per the ~$30-overspend rule.

### The shared gate — ONE fetch does discovery + refresh's job at once (operator scope, 2026-08-30)

```
fetch the candidate page ONCE (reuses the per-candidate fetch the staging loop already does)
  → classify        → program | first_party_hub | third_party_hub | none   (1 no-search call)
  → if "program" and not stale:
        metadata     → read the SAME page for name/org/summary/eligibility/…   (refresh's own
                       M1 prompt + validation, reused verbatim on the text already in hand)
        staleness gate (deterministic, code)   → drop if the page's LATEST date is stale
        embed → cosine vs the catalog index     → attach a duplicate HINT if a near match exists
```
Only `program`-class pages are enriched — hubs become leads, `none` is flagged, stale drops.

**This collapses the daisy chain where it was REDUNDANT (the discovery path): the combined reader
does what the new-opportunity scraper AND `refresh_opportunities` both do — read the page, pull the
fields — so a NEW row lands fully formed for a 5-second review.** Nothing is retired:

- **`agents/refresh_opportunities.py` stays STANDALONE**, the lightweight existing-row updater. The reader
  only CALLS its public `build_system`/`clean_update_dict` (read-only, no M1 edit); M1 holds because
  metadata is extracted only when the fetch succeeded — a failed fetch routes to `unreadable` and
  never reaches the model, so we never answer from memory.
- **Action items and deadlines stay STANDALONE too** (operator, 2026-08-30) — out of scope here,
  followed up later. Keeping tasks out also keeps this build clear of the action-item verification
  machinery. Reviews stay separate as always.
- Built as `wingman/combined_reader.py` (fetch-once orchestrator, every call injected, hermetically tested).

### Axis 1 — the page classifier (M8 prompt, M9 paid; search scraper ONLY for v1)

One no-search Gemini call per candidate, reading the chrome-stripped full page text. Returns
strict JSON `{"class","confidence","evidence","why"}`. It is a SEPARATE call from phase-2 extract
(the plan's own rejected-ideas note forbids folding a classifier into that 6000-token call).

The four classes and their routing — **conservative policy**:

| class | what it is | v1 action (nothing auto-drops on model judgment) |
|---|---|---|
| `program` | one opportunity's own page | build the row, **labelled** class+confidence+evidence → 5-sec review |
| `first_party_hub` | an institution listing MANY of its OWN programs | **not a row** → same-domain hub lead (`discovered_leads`, scope=same-domain) |
| `third_party_hub` | a blog/listicle/directory naming OTHERS' programs | **not a row** → off-domain hub / names lead |
| `none` | a non-opportunity page, or unreadable | **stays queued, flagged** (NOT dropped in v1 — this is the precision measurement that must precede granting it drop authority) |

- **The apply/deadline CTA is the strongest program signal, and it is in the prompt.** A single
  clear **Apply / Register / Enroll** action, an application **deadline**, or "applications
  open/close" language means this is one program's own page. A hub has no apply action of its own
  — it lists many programs, each with its OWN separate "Apply"/"Learn more" link. This is what
  the operator wants the classifier to key on to split a program home page from a hub.
- **first_party vs third_party = WHOSE programs they are** (the site's own vs other orgs'). This
  is the "kind B" split the plan says is undetectable structurally — a model reading the page
  content is exactly what decides it.
- **Unreadable page (403/JS-shell/PDF) → NO verdict**, keep today's behaviour (queue with the
  existing blocked flag). A blocked fetch is a fact about our HTTP client, never about the page.
  (A headless-Chromium retry via `page_text.fetch_page_text(..., allow_browser=True)` — the M1
  fallback — is a deferred option, not v1.)
- **`evidence` must be a verbatim page substring.** If the model cannot quote the page, the class
  is `none` — the same "quote or it didn't happen" bar `quote_is_on_page` already enforces.

### The staleness drop (deterministic CODE, operator-authorised, program-class only)

Dates are where models fabricate (the entire deadline-checker history), so this is NOT a prompt
instruction — it is a regex over the fetched text.

- Extract every year/date from the page, take the **latest**. If it is `<= current_year - 3`
  (today 2026 → drop when the newest date on the page is **2023 or earlier**), **drop the
  candidate** and log it to the run snapshot with the latest year found. Never silent (tenet 12).
- **A page with NO detectable date is KEPT** (operator decision 2026-08-30) — it cannot be proven
  stale, and many evergreen program pages print no year. The rule fires only when there IS a
  latest date and it is old.
- Future/current dates keep the page; a "© 2026" footer biases toward KEEPING — so the rule errs
  toward not losing a live program, the safe direction for a drop.
- This is a hard date FACT the operator chose to trust, deliberately distinct from the
  classifier-confidence `none` drop, which stays deferred until its precision is measured.

### Axis 2 — content-embedding dedupe (M9 paid embeddings; HINT only, never auto-reject)

**The gap it fills:** today dedupe suppresses only on same-URL + similar-name (tenet 10). The
measured hard case it misses is **the same program at a DIFFERENT URL** (`/alp/` vs
`/accelerated-learning-program/`, reorganised paths, cross-domain reposts). Name-similarity
cannot fill it: **96 same-site/diff-URL/name-similar pairs → only 5 are true aliases, 91 are
genuinely different programs.** So the bar is separating those 5 aliases from those 91 siblings —
which page CONTENT might do and URL/name cannot.

- **The make-or-break risk is institutional boilerplate** (two different CMU programs share the
  chrome, the apply block, the footer), which a naive full-page embedding can read as a
  duplicate. That is exactly the 96-pair population, so it is a direct stress test.
- **Measure BEFORE building (go/no-go gate).** `../../eval/dedupe_eval.py` reconstructs the 96-pair labeled
  set (same-site, diff-URL, name-sim ≥ 0.85; the 5 known aliases —
  `ec18774, ec18771, ec18856, ec18918, ec18865` — positive, the rest negative) and scores three
  representations: **(1) stripped page text, (2) structured fields (name+org+summary+eligibility+
  type), (3) a model canonical descriptor.** Pick the representation + cosine threshold that best
  separates aliases from siblings. **If none separates cleanly, the dedupe axis stops here** —
  learned for ~$0.10 instead of after shipping.
- **HINT, never a reject** (tenet 10 + the conservative choice): a near match populates the
  EXISTING `dup_candidates` field with `{id, score, reason}`, which the console review queue
  already renders inline with confidence colouring — turning a duplicate call into one click.
  Only after the 96-pair precision is measured would a top tier (≈ ≥0.97 AND same registrable
  domain, i.e. an alias) even be PROPOSED for auto-suppress, and that stays the operator's call.
- **Storage needs no DDL.** ~1300 rows × a ~768-float vector ≈ 4 MB — load and cosine-compare
  in-process with numpy, no pgvector, no SQL RPC (which this repo cannot run). MVP = a repo-root
  sidecar `catalog_embeddings.jsonl` (`id → vector + embedded_at + source`), the same
  "file now, table later" pattern `wingman/discovered_leads.py` uses. Dead-page rows fall back to
  field-embedding. Provider: Gemini `gemini-embedding-001`.

### Build order (both axes)

| # | step | cost | gate |
|---|---|---|---|
| 1 | `wingman/classify_page.py` (CTA + staleness) + `wingman/embed_common.py` + `../../eval/dedupe_eval.py` + `wingman/combined_reader.py` (classify + refresh-metadata + dedupe, fetch-once) + unit tests | **FREE ✅ BUILT** | pytest green (59 new tests); `../../eval/grade_scraper_batch.py` 0 regressions |
| 2 | run `../../eval/dedupe_eval.py` — pick representation + threshold | ~$0.04 spent ✅ RAN 2026-08-30 | **verdict below** |
| 3 | `agents/build_catalog_embeddings.py` — backfill the sidecar index (fields rep, incremental, `--commit`) | **built ✅; free preview: 1509 rows, ~$0.027 to embed** | run gated |
| 4 | wire `combined_reader` into `agents/scrape_opportunities.py`'s candidate loop | paid per run, gated | grade harness 0 regressions |
| 5 | console: show class/confidence + the embedding dup-hint in the review queue | FREE | — |

### Dedupe eval RESULT (RAN 2026-08-30, $0.038 total, active catalog, 90 same-site name-similar pairs)

**Verdict: GO for a HINT, NO for auto-suppress — which is exactly the conservative design.**

- **No clean separating threshold exists** (both reps overlap: fields clean-gap −0.138, page −0.172).
  The cause is the make-or-break risk, now CONFIRMED: same-org SIBLINGS overlap true duplicates —
  YoungArts category competitions (0.83–0.93), Badger *Music* vs *Arts* Clinic, Stanford sibling
  programs all sit in the true-dup band. So embeddings **cannot auto-reject** (tenet 10 holds); they
  attach a `dup_candidates` HINT and the reviewer decides.
- **As a hint they are STRONG.** The printed "precision 0.12" is against an incomplete label set (it
  marked many real dupes "distinct"); the **sorted cosine list is the truth**, and the fields
  representation's **≥0.95 band is almost purely genuine duplicates**.
- **FIELDS beats page text** (name+org+type+summary+eligibility): cleaner top band, and — decisive —
  **needs no page fetch**, so the catalog index is cheap/robust to build and covers dead-page rows.
  Page-rep lost 25 of 90 pairs to fetch failures. `combined_reader.default_representation` uses fields;
  `DEFAULT_DUP_THRESHOLD` set to **0.93** (recall-leaning; a hint is dismissible in a glance).
- **BONUS: the run surfaced ~17 REAL duplicates sitting ACTIVE in the catalog right now** (fields
  cosine ≥0.95, none of them the 5 known aliases): SEES ×2, Neubauer Phoenix STEM ×2, Stanford
  PINGWI ≡ "Inspiring the Next Generation of Women", Science Without Borders ×2, Annenberg Youth
  Academy ×2, Princeton Ten-Minute Play ×2, Stanford Math Camp ×2, Urban Journalism Workshop ×2,
  Sport Mgmt & Leadership ×2, Genes in Space ×2, NYC Ladders ≡ Ladders for Leaders, Davidson Fellows
  ≡ Fellows Scholarship, NYU GSTEM ×2, Automation-Robotics ×2, Coding for Game Design ×2, Applied
  Research (Sci&Eng) ×2, Badger Music Clinic ≡ Summer Music Clinic. Actionable console cleanup,
  independent of the scraper — the method demonstrably finds duplicates.
- **Consequence for build order:** step 3 (`agents/build_catalog_embeddings.py`) embeds ROW FIELDS, not
  pages — no catalog-wide fetch, ~$0.20, robust. The dedupe axis proceeds as a hint.

### Dedupe CONFIDENCE — the multi-tiered design (raise auto-action, shrink the human tail)

**The problem this answers (operator, 2026-08-30):** a single similarity score will always need a
human, because content similarity cannot tell a DUPLICATE from a same-institution SIBLING — the eval
proved it (YoungArts categories, Badger Music vs Arts, Stanford siblings all overlap true dupes). As
the catalog grows, naive review of every similar pair returns to today's bottleneck.

**The reframe: confidence comes from INDEPENDENT signals AGREEING, not a better score.** A duplicate
is only "confident" when several orthogonal tests all say "same program". Tiers, strongest first:

1. **Deterministic PROOF (auto-merge, certain, FREE):**
   - **Redirect equality** — both URLs follow their redirects to the SAME final page → literally one
     page. (`page_text.fetch_page_text_resolved` already returns the final URL.)
   - **Canonical-tag equality** — both pages declare the same `<link rel="canonical">` → the site
     itself says they are one page.
2. **DISCRIMINATORS — the signals that SEE the sibling difference (FREE, from data we already have):**
   - **Identity-name tokens** — subtract the shared org/structure words and compare what is left.
     Siblings differ here exactly: **Music** vs **Arts**, **Mini** vs full, **Design** vs **Visual**.
     `name_relation` → SAME / SUBSET / CONFLICT / UNKNOWN.
   - **Hard structured fields** — a real dup does not differ on grade range, season, or type. A
     mismatch on any is a sibling tell. `field_relation` → AGREE / CONFLICT / UNKNOWN.
     The embedding sees shared boilerplate; these see the difference it misses.
3. **Pairwise LLM ADJUDICATOR (PAID, only on the ambiguous band):** one call sees both programs'
   facts and answers "same program or different?" with a verbatim distinguishing quote; take a
   majority of 2-3 votes. Cost tracks AMBIGUITY, not catalog size, because it runs only where the
   cheap signals could not settle it.
4. **LEARN from every verdict (FREE, compounding):** each resolved duplicate / dismissed hint is a
   labeled pair (the `build_fixture` shape). Accumulate them, CALIBRATE where the auto line sits, and
   the auto-tier widens as the human tail teaches it — supervision given reduces supervision needed.

**The tier map** (`dedupe_confidence.classify_pair`):
```
redirect / canonical equal ............................... TIER_PROOF      -> auto-merge (certain)
high cosine + name SAME + no field conflict .............. TIER_CONFIDENT  -> auto-merge (after measured FP=0)
high cosine + (name CONFLICT or field conflict) .......... TIER_SIBLING    -> NOT a dup (discriminator overrides the embedding)
high cosine + name SUBSET + no conflict .................. TIER_ADJUDICATE -> LLM judge -> auto or hint
moderate cosine, no conflict ............................. TIER_HINT       -> human (small, ~constant tail)
low cosine ............................................... TIER_NONE
```

**Why this defuses the growth fear:** the duplicate FLOOD as the catalog grows is overwhelmingly the
SAME program re-discovered (exact/near-exact) — those hit TIER_PROOF/CONFIDENT and auto-consolidate,
ideally **blocked at insert so they never reach the queue**. The hard cases are same-institution
siblings, a SMALL, roughly CONSTANT tail — not something that scales with catalog size. The human
tail stays flat and shrinks as tier 4 learns.

**Two things make auto-acting safe enough to actually do:** a merge here is **reversible + audited**
(P3 merge preserves the incumbent, writes old values into `quality_flags`, never deletes — very
different from auto-delete), and **we measure the false-positive rate on accumulated verdicts before
turning on any auto-tier**. Same discipline as the rest of the pipeline. Honest caveat: the ambiguous
tail never reaches zero human touch without accepting SOME bounded, reversible, measured auto-merge
error.

**Build status (2026-08-30):** `wingman/dedupe_confidence.py` (identity-token + hard-field discriminators,
proof helpers, `classify_pair` tier logic — all pure) BUILT + tested. `agents/build_catalog_embeddings.py`
RAN (`--commit`, $0.0269, 1509 active rows → `catalog_embeddings.jsonl`). `../../eval/dedupe_eval.py --signals`
(discriminators, free) and `--tiers` (full pipeline: index cosine + discriminators, free) are the
measurements.

**MEASURED — full tier pipeline over the 90 active pairs (`--tiers`, FREE):** confident **14**,
adjudicate 6, sibling 3, hint 17, none 50. **All 14 CONFIDENT pairs are genuine duplicates — 0 false
positives** (SEES, Neubauer, Science Without Borders, Princeton Ten-Minute Play, Annenberg, Stanford
Math/PINGWI, Urban Journalism, Coding for Game Design, the 3 aliases, plus Davidson Fellows≡Fellows
Scholarship and NYC Ladders≡Ladders for Leaders — the org-token subtraction correctly matched those).
This is the evidence that gates auto-merge: on this set the CONFIDENT tier is safe. Sample is small
(14) and the label set imperfect, so the standing rule is **re-confirm as operator verdicts
accumulate** before widening. AUTO-MERGE is still wired OFF in v1 (tiers LABEL the hint and route the
ambiguous band); flipping the CONFIDENT/PROOF tiers to auto is the next decision, backed by this 0-FP
measurement + the reversible/audited merge.

**Validated on the REAL review queue + two guards it exposed (`agents/dedupe_queue.py`, read-only, ~$0.004).**
Ran the logic over the 279 pending rows against the active index and each other: **4 CONFIDENT (all
verified true duplicates — Fred Hutch SHIP, NYU SPARC, FBINAA Youth Leadership, Stanford CNI-X; two
arrive on a WORSE url — a Facebook page, an Empowerly listing — and would fold into the real
institutional row), 4 adjudicate (incl. an intra-queue pair, both pending), 3-4 sibling, ~10 hint,
258 genuinely new.** The live queue surfaced two weaknesses the curated eval could not, both now
fixed FREE in `wingman/dedupe_confidence.py`:
- **Abbreviations** — "Google Computer Science Summer Institute (CSSI)" vs "Google CS Summer Institute
  (CSSI)" read as a name CONFLICT and was wrongly kept distinct. `shared_acronym` (a shared
  PARENTHETICAL acronym) now softens CONFLICT→SUBSET, recovering it (SIBLING→HINT here; the CS/CS
  wording keeps cosine below the auto bar).
- **Generic names across orgs** — "Youth Leadership Program" at two different orgs would auto-merge on
  name+cosine alone. `same_context` (`org_agrees` OR `same_registrable_domain`) now downgrades a
  cross-institution CONFIDENT to ADJUDICATE. Org, not just domain, because a real dup often arrives on
  a third-party/social URL whose domain differs while the org matches — exactly the CNI-X/Youth
  Leadership cases above. After the guards: CONFIDENT stayed 4/4 correct, 0 new false positives.

### Cost, fitted to the existing measurements

- classify: ~$0.003–0.006 per candidate (no-search, thinking low) → **~$0.60–1.20 per national run**.
- embedding: ~$0.0005 per candidate; eval ~$0.10 one-time; catalog backfill ~$0.60 one-time.

### Decisions on record (operator, 2026-08-30) — do not re-litigate
- **Conservative v1:** label + reroute hubs; do NOT drop `none` on model judgment yet.
- **Search scraper only** for v1 (hub miner and name-harvest are a later pass).
- **Dedupe representation chosen by measurement**, not picked up front.
- **Undated pages are KEPT** by the staleness rule.
- **The staleness drop IS wanted** (a deterministic date rule, ≤ year−3), logged not silent.
- **Metadata IS folded in** (the reader does refresh's page-extraction for a new row) — but
  **`agents/refresh_opportunities.py` is KEPT standalone** as the existing-row updater; nothing retired,
  no M1 code edited (reader calls its public helpers read-only).
- **Action items + deadlines are DEFERRED to their standalone agents** — out of scope for the
  combined reader; follow-up later. Reviews stay separate.

---

## Implementation status — pick-up-here (last worked 2026-08-27)

**Branch `scraper-v2`** (operator directive: NOT deadline-email-alerts). Branched off main
(== origin/main == 6a7e186). Commits, newest last, ALL atop the email session's commit
`6f9ab2f` which shares this working tree:
- `63979dd` P1-P3 (attribution ledger, URL truth, uniqueness+merge)
- `afb7551` P5 (compounding loop)
- `ebcd50e` P4 discovery-channel code (paid runs deferred)
- `f5d1521` propose_angles fix (697 noise proposals → 18 curated)
- `38ae6b7` P4 live-preview fixes (fetch_page_text unpack; refind ec-id minting)

**Nothing merged to main.** The working tree is SHARED with an active deadline-email
session (app/services/email*.py, wingman/send_lifecycle_emails.py, app/config.py) — when
committing, stage ONLY scraper files. The email commit `6f9ab2f` sits in scraper-v2's
history; reorganize branches later if you want them fully separate.

**State: all phases built, 1263 tests green, grading harness SAFE. `0 regressions` on
`python ../../eval/grade_scraper_batch.py` is the merge bar for any future change.**

### DDL run by the operator (live): `../../db/scraper_attribution_schema.sql`, `scraper_seeds` ALTER (disabled_reason/at). `moderation_reason` was already live.

### What actually RAN this session (live, on real data)
- **seed_id backfill DONE**: `../../scripts/one-off/backfill_seed_attribution.py` stamped **143/159** Aug-23 rows
  (16 honest `(no seed)`: 14 unmatched, 2 ambiguous). Idempotent; match is same-run-date +
  unambiguous. → the console seed funnel is now populated with real Appr/$-per-approved.
- **Console verified live** at /admin → Scraper angles: funnel columns (Appr/Rej/Dup/Waste %/
  $/appr/Diagnosis) render; every angle shows `small sample` (each has 1 run; needs ≥2 + ≥10
  found to diagnose or auto-disable). "Recent merges" card wired ("No merges yet").
- **18 gap angles written as DISABLED seeds** (`agents/propose_angles.py --commit`). Review + enable
  the worthwhile ones in the console.
- **Refind pilot RAN (PAID, $0.4091)**: `agents/refind_dead_links.py --limit 20`. 20 searched, **4
  re-found** → review queue (is_active=false): Kenyon Review ✅, Red Cross Youth Council ✅,
  Immerse "Academic Insights" ⚠ (mill product), UVA Creative Writing ❌ (matched
  northern.virginia.edu — a WRONG-institution false positive). 16 found nothing (wrote
  nothing). All 20 old rows stamped `refind_attempted 20260827` (never re-paid).

### Measured findings that refined the plan (each is a real, accepted result)
- **P2 free offsite rescue = 0/27 on Aug-23.** Those rows are true search-misses (the model
  wrote a listicle URL because its search never surfaced the real page); nothing to rescue TO
  for free → they are refind targets. P2's real value is PREVENTION (mills never stored,
  title-proof, canonical page), not repairing this batch.
- **P3 match_key merge is NOT unconditionally safe** — it would drop 2 approved rows. Safe
  rule = **bare-domain vs dedicated-page** + treating a merge as PRESERVE (program survives in
  the incumbent), never a drop. build_fixture independently CONFIRMED the merge: Harvard AI
  (auto-merged) was later marked `duplicate` by the operator.
- **P3 name-replace only when the incumbent is junk** (<2 identity words). Strict title-proof
  ("Mathematics" vs "Math") must not trade a good specific name for a shorter one.
- **P4 refind precision ~50-75%** on found rows (below the 80% target): `domain_matches_org`
  substring-matches too loosely (northern.virginia.edu ⊃ "virginia") and title-proof is weak
  on generic names ("Creative Writing"). Operator review catches it; TIGHTEN before a full run.
- **P4 hub mining needs a fetchable PROGRAM-INDEX page.** Org roots yield chaff (CEISMC root:
  62 links → 12 mostly-nav candidates) and the real index (`ceismc.gatech.edu/programs`) 403s
  our client — the same partial-block the original pilot hit on medicine.illinois.

### Session 2026-08-27b (worktree): items 1-3 DONE. Commits on `scraper-v2` (2e6f1a0..f2d0bbc)
Worked in an ISOLATED git worktree at `C:\Users\shama\Documents\wingman-scraper-v2` (a
concurrent session was switching branches in the main tree and clobbered edits mid-session;
the worktree is immune). `.env` copied in (gitignored) so tests/scripts run there.
- **Item 1 (review 4 refound rows) — recommendations delivered, operator applies in console.**
  Kenyon `ec18693` → approve+activate (kenyonreview.org/event/young-writers-...); Red Cross
  `ec18691` → approve+activate (redcross.org/red-cross-youth/national-youth-council.html);
  UVA `ec18690` → REJECT `wrong-page` (re-found northern.virginia.edu/**blog**/... — wrong
  institution AND editorial); Immerse `ec18692` → **left pending** (operator decision).
- **Item 2 (tighten refind) — DONE, commits 2da8422 + f2d0bbc.** `best_refound_url` now:
  (1) requires the re-found URL on the **same registrable domain as the dead URL** (not the
  substring `domain_matches_org`), (2) requires `title_proves` AND `keeps_identity` (test 3),
  (3) rejects same-domain **editorial** (/blog//news/) siblings — the live UVA class. +6 tests.
- **Item 3 (hub miner at real indexes) — DONE, commit e62f061.** `hub_pilot_national.json`
  carries the 5 named targets; free `--preview` surfaces the gems (USNA Summer STEM+Seminar,
  Wisconsin BEL, ~13 UW pre-college programs, +recursion: engineering/pharmacy/arboretum/union)
  and new `is_nonprogram_link()` drops nav/PDF/image/editorial/degree chaff (46→29 candidates,
  all gems retained). CEISMC + medicine.illinois 403 our client (client fact, per plan). +12 tests.
- Gate: full unit suite green; `grade_scraper_batch` url-dup **0 regressions, SAFE**.

### OPEN / do next
1. **Apply the 4 refind verdicts** in the console (recommendations above; Immerse still pending).
2. **Refind is now tightened — resume draining the 167-row backlog** in small PAID batches
   (~$4-8 total), reviewing as you go. Precision should be higher than the Aug-27 ~50-75%.
3. **Hub extraction is PAID and still gated.** `python -m agents.mine_hub_pages --hubs-file
   hub_pilot_national.json` (no --preview) extracts the 29 candidates (~$0.09); rows land
   is_active=false for review. Consider trimming to fewer hubs to hit the ≤10-call pilot bar.
   NOTE: mine_hub_pages `main()` proves+prices extraction but does NOT insert — wiring the
   insert is a separate approved step (see its closing comment).
4. **Exercise the full live pipeline**: enable a few of the 18 disabled angles and run the
   scraper (PAID, per-run approval). A 2nd run per angle gives the funnel a real diagnosis;
   same-URL re-finds populate the merges card; a mined-out angle can auto-disable.
5. **Merge scraper-v2 → main** when ready (coordinate re: the email commit in its history).
   Then `git worktree remove C:\Users\shama\Documents\wingman-scraper-v2`.

### Every script, and its money tier (all `--preview` is FREE)
- `python ../../eval/grade_scraper_batch.py` — FREE gate. `0 regressions` required to ship any change.
- `python ../../eval/build_fixture.py --batch B --snapshot F1 F2 --out tests/fixtures/X.json` — FREE.
- `python ../../scripts/one-off/backfill_seed_attribution.py [--commit]` — FREE (done; idempotent).
- `python -m agents.propose_angles [--commit]` — FREE (18 written).
- `python -m wingman.walk_up_hubs [--limit N] [--commit]` — **FREE at every tier** (plain HTTP +
  a Supabase read, no model call anywhere). Derives an institution's own program index by
  walking UP from an active row and requiring the parent to LINK that row. Queues
  `scope=same-domain` hub leads; mining them is the paid step.
- `python -m agents.mine_hub_pages --hubs URL --preview` FREE / live = PAID.
- `python -m agents.refind_dead_links --preview` FREE / `--limit N` = PAID search.
- `python -m agents.harvest_names --hubs URL --preview` FREE (prices the run over fetchable pages) / without `--preview` = PAID (1 naming call + up to `--max-names` searches).
- `python -m agents.scrape_opportunities --mode national --seed-ids IDS` — PAID (preview via console).
- **Every paid run needs fresh explicit chat approval (the ~$30-overspend rule).**

## North star

**Every real, currently-offered opportunity a high schooler could pursue appears in the
catalog exactly once, under the name they'd search for, at its own dedicated page — and
nothing else does.** The scraper's job is to make the reviewer's approval take five
seconds with evidence attached, and to learn from every verdict the reviewer records.

---

## The thirteen tenets (operator-approved, with their two corrections applied)

Enforcement note: a prompt is neither a floor nor a ceiling in this repo (measured
repeatedly — see CLAUDE.md's action-items and scraper sections). Tenets marked [code]
must be enforced in the pipeline; [prompt] ones are phrased into the phase-1/phase-2
scraper prompts; [both] need both.

**A. Where to hunt (recall)**
1. **Hunt at the frontier, not the head.** [prompt + seed mgmt] Broad angles re-buy
   duplicates (measured: original seeds 0-60% approval, niche seeds 100%). Retirement is
   measured over EVERYTHING an angle found — internal discards (dupes, invalid,
   no-URL) count against it, not just what reached the review queue. (Operator
   correction #1: waste rate = internal discards + human rejects + human dups, over
   found.)
2. **A real program is never lost silently.** [code] A dead link is a fact about a URL,
   not the program. Dead-link rejects are the `--refind` work-list, not garbage.
3. **Pages that list many programs are sources, not rejects.** [code] Hub pages
   (university pre-college indexes, even SEO listicles) get their links harvested — a
   followed URL is real by construction and costs no search fee.

**B. What is true (identity)**
4. **The URL of record is the program's own page, retrieved — never remembered, never
   secondhand.** [code] Model-typed URLs measured 26% dead. A page ABOUT the program
   (listicle/blog/video/Wikipedia) is never the row's URL — but it usually links to the
   real page: follow it, verify, take that.
5. **The page must prove the program.** [code] Title/content must name this specific
   program (separates the program page from facilities/costs pages, professors'
   homepages, sibling programs). Prefer canonical landing page over /faq /apply /rules
   /admissions and PDFs. Proof over similarity, always.
6. **Each row is exactly one opportunity, at its most specific page.** [both] (Operator
   correction #2.) A page listing several programs is a hub — a discovery source, never
   a row. Follow each program to its dedicated page; that page is the row. Only a
   program with genuinely no page of its own may share a URL with a sibling, and then
   its name carries the full distinction. Two rows sharing one URL is always a defect
   signal: one is a duplicate, or both need their dedicated pages found.

**C. What a row owes a student (quality)**
7. **Name it the way a student would search for it.** [prompt + merge] Full official
   program name, org attached where it disambiguates: "MSU OsteoCHAMPS", never
   "Michigan State University". A name that could belong to ten programs belongs to
   none. ("Better name" is decided by page-title evidence, never by length or model
   opinion — see merge design.)
8. **Every claim carries evidence or a flag.** [both] Fields come from the page and the
   search, not from what's typical. Unverifiable ≠ false: flag honestly. Estimated is
   labeled estimated.

**D. Uniqueness (precision)**
9. **Never insert what we already have — improve it instead.** [code] Check candidates
   against the whole table (rejected rows included) before writing. A re-found program
   is an upgrade opportunity: better URL/name flows onto the existing row.
10. **Suppress only on proof; hint on suspicion.** [code] Same normalized URL + same
    name = skip. Anything weaker is a hint for a human, never an auto-reject: 257 of
    264 name-similar catalog pairs at ratio ≥0.85 are genuinely DISTINCT programs; 73%
    of rows share a domain with a different opportunity.
11. **If the search didn't happen, say nothing.** [code, already live] Retry a silent
    search once; still silent → write nothing.
12. **Discard nothing silently; explain everything.** [code] Every skip/demotion/
    rejection lands in the run snapshot with its reason. Reviewer verdicts + reasons are
    the grading set: no pipeline change ships without proving zero human-approved rows
    lost.
13. **Nothing reaches students without a human yes.** [code, already live] Every row
    lands `is_active=false`. Only the operator activates, in the console.

---

## Operator decisions on record (do not re-litigate)

- **No real users yet → no backward-compatibility constraints.** Prefer the simplest
  elegant design. (Row-id stability for student trackers is NOT currently a constraint.)
- **Never SQL-DELETE an `opportunities` row, ever.** Operator commitment 2026-08-26.
  Console Reject/Duplicate is the only way a row dies; a rejected row blocks its URL
  forever for free. The tombstone shim was retired the same day: all 56 previously
  deleted rows were re-inserted under their ORIGINAL ids as rejected rows
  (`source='tombstone-backfill'`, reason names the why + survivor). The table is the
  ONLY dedupe memory.
- **Merges auto-apply everywhere, active rows included.** Audit trail (old values into
  `quality_flags`), not an approval queue. Review gate stays where it matters: nothing
  NEW activates without the operator.
- **Angles auto-disable** when diagnosed mined-out or thin (sample guard: ≥10 found
  across ≥2 runs) — visible in the console with the reason and one-click re-enable.
  Mis-aimed and pipeline-limited angles NEVER auto-disable (their fix is elsewhere).
- **Rejects require a reason; approvals stay one-click.** An approved row with no
  reason IS the label "good row". Reason codes: `duplicate`, `third-party-url`,
  `wrong-page`, `dead-link`, `not-a-fit`, `low-quality`, `other` (note required for
  other), stored in `opportunities.moderation_reason` as `code` or `code: note`.
  The column is LIVE (schema re-run 2026-08-26). The Duplicate button auto-fills its
  reason from the survivor; Restore clears it.
- **Paid agent runs still need fresh explicit approval in chat, per run.** (The ~$30
  overspend rule.) Everything in Phases 1-3 and 5 is free; Phase 4's runs are gated.
- **Hub-mining pilot targets are chosen**: the operator's CEISMC example plus the three
  umbrella pages from the collapsed pairs (USNA /Admissions/Programs/,
  business.wisc.edu→/precollege/, medicine.illinois.edu SpHEREs page).

---

## Evidence base (all measured, 2026-08-23 → 08-26)

**Round 1 — first full human grading of scraper output** (378 console decisions + 46
SQL deletions): the post-rewrite 08-23 batch ran 115 approved / 44 rejected / 7 deleted
(69% approval). Rejection causes: ~28 duplicates of existing rows, ~11 third-party URLs
(lumiere-education, admissionsight, scholarships360, nshss, borderless.so, YouTube,
Reddit — rejected ~100% of the time), rest wrong-page-on-right-site. ~200 dead-link
rows rejected including flagship programs (AMC 8, USAMO, Boys State, Jackson Lab,
SMYSP, YSPA, CMU CS Scholars) — all still exist, all refind targets.

**Round 2 — 30 identical-URL pair resolutions** (2026-08-26, queue emptied, 123
decisions): **the incumbent row won 27 of 28 resolved pairs**, including every pair
where the newer copy had the better name. Unified survivor rule: better URL wins; equal
URLs, incumbent wins. Side effect: ~5 surviving rows have junk names ("Columbia",
"Michigan State University", "Pre-College") — the better name was on the loser. The
operator flagged this as tool-learning noise, NOT preference: best practice (tenet 7)
overrides; fix via merge in Phase 3.

**Hub pilot** (2026-08-26, free, no writes): USNA umbrella yielded Summer STEM's
DEDICATED page (the re-split tenet 6 requires) + 2 more candidates, and correctly
recognized Summer Seminar as already cataloged. CEISMC yielded real candidates plus
elementary/middle-school chaff. business.wisc.edu (root domain) yielded 40 links with
exactly 2 gems (Precollege Programs sub-hub + BEL Scholarship at its dedicated page) —
proving root homepages need the two-stage filter and one-level sub-hub recursion.
medicine.illinois.edu 403'd our client (falls to manual pile; ~10-20% of sites refuse
non-browser clients — a fact about our HTTP client, never about the program).

**Verdict stability:** 65 batch rows re-stamped in round 2 kept identical verdicts —
the frozen fixtures are stable ground truth.

**Fixtures on disk** (`tests/fixtures/`):
- `scraper_grading_20260823.json` — 166 verdicts (115/44/7) over the two 08-23
  snapshots. Harness: `../../eval/grade_scraper_batch.py`. Probe already run: suppress-on-strong-dup
  would lose 18 approved rows → never a live rule.
- `pair_resolution_20260826.json` — the 30 pair outcomes (survivor + losers + notes).

---

## Current state (shipped and verified)

- `../../eval/grade_scraper_batch.py` — replay/scoring harness. Hard gate for every phase:
  **zero human-approved rows suppressed**.
- `wingman/find_catalog_dups.py` — read-only self-dup sweep (48 identical-URL groups found;
  the 30 pair-shaped ones are resolved; multi-row portal groups deliberately left).
- Reject-reason capture live end-to-end (console modal → `moderation_reason` column).
- Tombstones retired; 56 backfill rows in table (note: `opportunities.type` is NOT
  NULL — backfill rows carry a 'Program' placeholder).
- Review queue: **158 rows as of 2026-08-28** (144 from the 43-hub batch, 14 from the CMU pilot
  of which 12 are duplicates created before the dedupe fix — keep ec18794/ec18795). Was 3 rows (all `scraper-national-20260826`). Catalog **1325
  active**, 1704 total; the 379 inactive = 349 rejected + 27 duplicate + 3 queued. Counts in this
  file go stale as soon as anyone reviews anything — read them from the table.
- Loose end: 13 rows sit `is_active=true` + `moderation_status='pending_review'` — the
  url_repair-restored rows from 08-23. Harmless (queue filters on inactive), tidy to
  'approved' whenever convenient.
- Related but separate systems already live: silent-search retry, seeds in
  `scraper_seeds` (yield counters found/added/dupes/cost via `record_seed_result`),
  review snapshots `{"inserted": [...], "rejected": [...]}` (shape read by
  `wingman/dryrun_common.py` — additions OK, shape changes not), per-seed debug logs in
  `agent_logs/scraper_<stamp>_seed<id>.json`.

---

## Phases

Grading rule for every phase: run `../../eval/grade_scraper_batch.py` (and the pair fixture where
relevant) before merging; **0 regressions** is the gate. `cd frontend && npx tsc
--noEmit` untouched (no frontend work here). Full pytest suite stays green.

### Phase 1 — Attribution + the self-learning ledger (free)

The catalog IS the ledger: verdict counts per angle are a live GROUP BY, never a
writeback job (no counters that can drift, retroactively correct when a verdict
changes).

Build:
- DDL (one-time manual Supabase SQL, CREATE+ALTER convention like every schema file
  here): `opportunities.seed_id int`, `opportunities.found_via text`;
  `scraper_seeds.disabled_reason text`, `scraper_seeds.disabled_at timestamptz`.
  Degrade gracefully until run (retry writes without the column, report a ready flag —
  the `moderation_reason` pattern in `ops/core.py` is the template).
- `agents/scrape_opportunities.py`: stamp `seed_id` on every inserted row (`build_row` or the
  insert loop); carry it in the snapshot.
- `ops/core.get_seed_yield()`: per-seed funnel = live GROUP BY over `opportunities`
  (seed_id × moderation_status × reason-code prefix, where the code is
  `moderation_reason.split(':')[0]`) joined with `scraper_seeds` counters
  (found/dupes/cost). Pending rows shown but EXCLUDED from rates.
- Pure `diagnose(funnel)` → `healthy | mined_out | mis_aimed | pipeline_limited |
  thin | insufficient_sample`, from the reason mix: duplicate-dominated → mined_out;
  not-a-fit-dominated → mis_aimed; third-party-url/wrong-page-dominated →
  pipeline_limited (do NOT punish the angle); low-quality-dominated → thin.
- Auto-disable at scrape-run end for seeds that just ran: mined_out/thin with ≥10 found
  across ≥2 runs → `enabled=false` + `disabled_reason="auto: mined out — N found, N
  approved, N dupes"`. Never touches manually disabled seeds; never deletes.
- Console seed grid: funnel columns, $/approved (the sort), diagnosis badge,
  auto-disabled badge with one-click re-enable.

Success criteria:
1. Every row from the next scrape run carries `seed_id`; hub rows carry `found_via`.
2. Console shows per-angle funnel incl. reason mix with NO new stored counters (verify:
   change a verdict in the console, funnel updates on refresh).
3. `diagnose()` unit-tested on synthetic funnels incl. the guard cases (small sample →
   insufficient_sample; pipeline_limited never auto-disables).
4. Auto-disable fires in a simulated run-end and the grid shows reason + re-enable.
5. $/approved computed for the 08-23 batch retroactively (seed attribution backfilled
   from the per-seed logs by URL match where possible; unattributable rows count in a
   `(no seed)` bucket, never dropped).

### Phase 2 — URL truth (free HTTP; biggest reject-rate lever)

Build:
- `url_validate.is_content_mill(url)` + path-aware `CONTENT_MILL_PATTERNS` (domains
  measured in round 1: lumiere-education.com, admissionsight.com, scholarships360.org,
  nshss.org, borderless.so, aralia.com, indigoresearch.org, ladderinternships.com,
  opportunitiesforyouth.org, immerse.education/knowledge-base/* — path-aware because
  immerse.education is ALSO a legit provider at /summer-schools/ — plus youtube.com,
  reddit.com, en.wikipedia.org, lithub.com). A mill URL can never be the stored URL.
- `url_repair.extract_primary_link(html, name, org)`: harvest `<a>` links (`_LINK_RE`),
  rank by anchor-overlap with `identity_words()`, fetch top ≤5, accept only what passes
  BOTH `url_validate.domain_matches_org()` AND `title_proves()`. (Both gates required:
  SEO mills' most prominent outbound links are their own signup funnels.)
- Candidate-loop rescue ladder where `domain_matches_org` fails today (the
  FLAG_OFFSITE site): (a) another same-run grounding URL that passes domain+title, (b)
  `extract_primary_link` on the offsite page, (c) keep + flag as today. Success adds
  `FLAG_URL_RESCUED` naming the secondary domain. No paid retry search in this phase.
- Title proof on EVERY stored URL (one extra free fetch per candidate; skip when the
  site 403s — blocked stays approvable, that calibration is measured-correct):
  fail → try sibling grounding URL → else new `FLAG_TITLE_UNPROVEN`. Never a
  rejection (false negatives like "Algebra II" vs "Algebra 2" are the accepted cost).
  PDFs/non-HTML auto-fail the proof.
- Canonical ranking when several org-domain URLs exist for one candidate: title-proof
  pass > not-low-value-path (extend `LOW_VALUE_SEGMENTS` with admissions, costs, rules,
  register; treat .pdf as low value) > shallowest path.

Success criteria:
1. Harness on the 08-23 fixture: **≥8 of the 11 offsite-rejected rows come out with an
   org-domain, title-proven URL; 0 approved rows suppressed or given a
   title-proof-failing URL.**
2. The 7 deleted listicle/PDF rows would have been rescued or flagged
   `FLAG_TITLE_UNPROVEN` (spot-check in harness output).
3. immerse.education both-ways unit test: /knowledge-base/* is a mill,
   /summer-schools/ is not.
4. Wall-time bounded: ≤2 extra fetches per candidate, existing timeouts.

### Phase 3 — Uniqueness + merge (free)

Build:
- Insert-layer rule: candidate whose `match_key` equals ANY existing row's → **never
  insert** (today only same-URL+same-name skips; same-URL+different-name inserted with
  a strong flag — that flow is what cost 30 manual pair resolutions). Instead run
  best-copy-wins merge against the existing row.
- Best-copy-wins merge (one function): fetch the page, `title_proves` decides the name
  (evidence, never length); most-specific title-proven URL wins; other scraper-owned
  fields (org, summary, eligibility, grade range, subject_tags, contact_email)
  fill-if-empty. NEVER touches fields owned by other agents: `important_dates/status/
  was_estimated/dates_last_checked_at` (deadline checker), `review_*` (review checker),
  `link_*` (link checker), provenance (`id/source/created_at`). Auto-applies (operator
  decision), old values appended to `quality_flags`
  (`"merged 2026-08-XX: name was 'Columbia'"`) — hand-reversible. Snapshot gains a
  `merged` list; small console card shows recent merges (audit, not approval).
- Cross-seed in-run dedupe: rows minted THIS run matching same-domain +
  `name_similarity ≥ 0.9` collapse to the copy whose URL wins the Phase-2 ranking
  (loser → snapshot `rejected` with reason `intra-run duplicate of <id>`). In-run rows
  only — this looser rule never touches the real catalog.
- One-off script: run the merge over the 27 `duplicate_of` pairs from
  `pair_resolution_20260826.json` to repair the junk-named survivors ("Columbia" →
  Lamont's full name, "Michigan State University" → MSU OsteoCHAMPS, "Pre-College" →
  UCSB's full name, etc.), each rename backed by the page title, logged, reversible.

Success criteria:
1. Replay over the 08-23 snapshots: every rejected-as-duplicate row with an identical-
   URL target becomes suppress-or-merge; **0 approved rows suppressed** (the 18-row
   strong-dup trap from the harness probe must NOT reappear — the rule is match_key
   equality, never name similarity).
2. Pair-fixture replay: for the 28 resolved pairs, the automatic rule reaches the same
   end state (one surviving row per URL) — survivor CHOICE may differ from the human's
   where title evidence favors the other copy; that is tenet 7 overriding, expected.
3. The ~5 junk-named survivors are renamed with title evidence recorded; before/after
   list shown to operator.
4. NACLO/Civic-Innovators-style intra-run twins collapse in a replay of the 08-23 run.
5. All merges visible in the console audit card and reversible from `quality_flags`.

### Phase 4 — Discovery channels (PAID — each run needs fresh operator approval)

Build:
- `agents/mine_hub_pages.py`: harvest (regex, free) → two-stage filter proven by the pilot:
  (a) anchor-level audience filter (drop elementary/middle/graduate/MBA/PhD/faculty/
  admitted-student links — cuts Wisconsin's 40 links to ~4), (b) fetch each surviving
  target and require high-school-audience words (free). One-level sub-hub recursion
  (an anchor like "Precollege Programs" is a hub, recurse once; hard caps: ~25 links
  per hub, same-registrable-domain links for institutional hubs, OFF-domain links for
  listicle hubs — following the wrong kind is how you crawl the internet). Dedupe +
  reason-checked against the catalog BEFORE any model call. Extraction = one no-search
  model call per surviving page (~$0.003; the URL is real by construction, no
  grounding needed — the `agents/generate_action_items.py` shape: page in, JSON out). Rows
  land `is_active=false`, `source='hub-<domain>-<date>'`, `found_via=<hub url>`.
- `--refind` on the scraper: selects REJECTED rows whose reason/flags say dead-link;
  one narrow search per row ("current official page for <name> by <org>"),
  grounding-resolved + title-proven URL, normal insert path. Genuinely discontinued →
  no qualifying page → nothing written; stamp `refind_attempted` into the old row's
  `quality_flags` so it isn't re-paid next pass. ~$0.02-0.05/row; ~200-row backlog.
- Umbrella re-splits ride the hub miner: the collapsed pairs' shared URLs are hubs;
  sub-programs (USNA Summer STEM at STEM.php, Wisconsin BEL) insert at their dedicated
  pages per tenet 6.
- Angle generation: free coverage-gap analysis (catalog crossed by type × subject_tags
  × season; thin cells become angle candidates) + one cheap siblings-of-winners model
  call. Proposals land as DISABLED seeds in `scraper_seeds` for the operator to enable.

Success criteria (pilot-sized, then scale):
1. Hub pilot run on the four chosen hubs: ≤10 extraction calls, ≤$0.10 total;
   **USNA Summer STEM and Wisconsin BEL Scholarship land in the review queue at their
   dedicated pages** with `found_via` set; zero wrong-audience rows inserted.
2. Refind pilot on 20 rows: ≥50% re-found with title-proven URLs, <$1.50; discontinued
   programs produce no row; operator grades the batch — approval rate of refound rows
   ≥80%.
3. Angle proposals: ≥10 proposed, all disabled, none duplicating enabled/mined-out
   angles; operator judges ≥half worth enabling.
4. Every run pre-approved in chat; every run's snapshot names everything discarded.

### Phase 4L — Local opportunities (**PINNED 2026-08-27 — do not build**)

**The operator has pinned local discovery to rethink the strategy from scratch.** Everything below is the MEASURED RECORD of why the first two designs did not work — keep it, it is what any new strategy has to answer to — but none of it is a work item, and 4F is not automatically its replacement. Ask before resuming.

**Current state: dormant, and REDESIGNED 2026-08-27 after a measured Seattle preview
overturned the original premise.** `--mode seattle` exists (hyperlocal `SEATTLE_SEEDS` +
`SEATTLE_ADDENDUM` in agents/scrape_opportunities.py, console National/Seattle switch, `mode`
column on `scraper_seeds`), ran once 2026-08-18, ~nothing survived review. Search-first is
dead for local (small orgs, no SEO, link rot). The obvious replacement — "hub-first, every
local institution publishes a program index" — was **tested and found only half true.**

**MEASURED (2026-08-27, `hubs_seattle.json`, 8 fetchable Seattle hubs, free preview):**
`agents/mine_hub_pages.py` harvested/filtered to **54 candidates → 42 after new local chaff
filters → still ~75% chaff.** The chaff was structural, not incidental: **civic/nonprofit
sites are built differently from university program indexes.** A university links to a
dedicated page *per program* (`STEM.php`, `badger-summer-scholars`); a library/museum/city
dept links to **branch locations and service categories** — SPL yielded 23
`/hours-and-locations/<branch>` pages, then `donatebutton`/`whats-popular`/research
databases; KCLS yielded `/ebooks/`, `/volunteer/`, `learnenglish`, and a literal `{{url}}`
template artifact. Their actual teen programs live in **event calendars and prose, which
link-harvest cannot see.** The ONLY real yield (~10 programs) came from the two orgs that
happen to structure programs as linked pages — **YMCA camps** (`bold-gold`,
`overnight-camp`) and **WSU 4-H** (`4-h-clubs`, `4-h-stem`). Libraries and museums returned
~0 programs by link-harvest. Also **7 of 16 candidate hub URLs 403'd/404'd our client** —
the ~40% civic-site block rate is much higher than the national ~10-20%.

**So local discovery needs THREE parts, and only the first two exist:**

1. **Curated per-metro hub registry** — `hubs_seattle.json`-shaped (SPL/KCLS, Pacific
   Science Center, Seattle Aquarium, MoPOP, YMCA, WSU 4-H King County, city youth services,
   + Parks & Rec / UW outreach / district CTE where fetchable). This is the local analogue
   of "angles". Curation is heavy and must point at the real program-list page; expect ~40%
   of civic sites to block our client or render in JS.
2. **Local chaff filters** — SHIPPED 2026-08-27 in `mine_hub_pages.is_nonprogram_link`:
   `_CIVIC_PATH_SEGMENTS` (hours-and-locations/locations/branches/ebooks/databases/donate/…)
   and an unrendered-template drop (`{{ }}`). Necessary but NOT sufficient — it removes
   noise, it does not find programs (proved: 54→42 was almost all still service/nav).
3. **`name-harvest → search` — THE LOAD-BEARING, UNBUILT piece.** Because local programs are
   *named in prose/calendars, not linked*, the only way to reach them is: pull the program
   NAMES from the hub page's text (one cheap no-search model call), dedup against the
   catalog, then search-resolve each name to its own page via the refind primitive
   (grounding-resolved + title-proven). This is the SAME feature national JS-listicles need
   (College Transitions' 70 named-but-unlinked competitions) — **it is the common unlock,
   and for local it is not optional.** Without it, "local hub mining" returns branch pages.

- **Strategy: depth in ONE metro as the template.** Seattle first (where the users are);
  replicate per metro only when account locations warrant it (`/api/account/location`).
- Rows carry `state`/`location`; the finder already filters on them. Hubs carry the metro so
  the Phase-1 ledger diagnoses local yield separately (a `(metro)` cut).
- **The creative-reasoning addendum stays quarantined** (operator-confirmed 2026-08-26):
  **the program must exist on a page (title-proven); the PITCH may be as creative as it
  likes.** A farmers market that actually hosts an "Emerging Entrepreneurs" event is in
  scope — its events page goes in the registry, the row title-proves against it, the summary
  may say "beta-test your product on real customers." Inventing rows for opportunities no
  page describes stays out — unverifiable by construction, the invented-Algebra-2 family.

Success criteria (revised): (1) `name-harvest → search` built and unit-tested; (2) a Seattle
sweep = registry → hub mine (link-harvest for the YMCA/4-H-shaped orgs) + name-harvest→search
(for the library/museum/calendar orgs) lands ≥15 REAL local programs in the review queue at
their own title-proven pages, `found_via` set, operator approval ≥60%; (3) the `(metro)` cut
appears in the seed grid so local yield is diagnosable like any angle. The old "≤$0.30, hubs
alone" target is retired — hubs alone cannot hit it on civic sites, and name-harvest is paid
per name.

### Phase 4N — Name-harvest → search (**BUILT + RUN PAID 2026-08-27 as `agents/harvest_names.py`**; operator-pointed)

*The spec below is the design record. Two things about what shipped are NOT in it. (a) It has run
paid — 12 rows across 3 runs — and the ranking A/B, the score floor and `FLAG_SELF_PROMOTED` all
came out of those runs; see START HERE. (b) **It is a console tool the operator aims, never fed by
the router** (2026-08-27, `52c6a56`): of the 11 name leads the router had queued, 10 were pages we
never actually received, and paying a search per name off a page we did not get is the one way this
tool wastes money outright. The genuine cases are recognisable by eye — College Transitions'
competitions table has 28 anchors, 0 off-domain links and ~70 canonical names in its text — so a
queue was not worth its failure mode. Free `--preview` by default; the name cap doubles as the
spend ceiling; the set of tools allowed to spend is pinned in a test. The remaining difference from
the spec: the per-name resolver is `harvest_names.best_resolved_url`, not `refind_dead_links.best_refound_url` directly — refind holds a candidate to the same registrable domain as the DEAD url, and a harvested name has no prior url to hold it to, so `title_proves` carries the whole weight there. That is exactly why free gate 2 refuses an unprovable name before any search is paid for. See the START-HERE block for what the build measured.*

**The problem it solves:** link-harvest (hub mining) only works when programs are actual
`<a>` links. Two large classes of pages NAME many programs but don't link them: national
**JS-rendered directories** (College Transitions' Dataverse — measured: 0 harvestable program
links, but `page_text` returns 14,355 chars naming "70 academic competitions") and **local
civic calendars** (library/museum teen programs live in event widgets/prose). For these, the
names are in the TEXT; the URLs are not.

**Mechanism** (reuses the refind primitive end-to-end):
1. `page_text.fetch_page_text(hub_url)` (free) → one **no-search** model call: "list every
   program/competition this page NAMES" → JSON list of names. ~$0.001/page.
2. Dedup names against the catalog (free — skip what we already have).
3. For each fresh name: one narrow search "official page for `<name>`", take the
   grounding-resolved + **title-proven** URL (`refind_dead_links.best_refound_url`, same
   evidence bar), insert `is_active=false`/`pending_review`. ~$0.02-0.05/name (PAID, per-search
   fee — gated per run, with a per-page name cap).

**Why higher precision than a broad search angle:** the page already vouched the program exists
and is HS-relevant, so this only resolves name→URL, it doesn't discover from scratch.

Build as a new mode on `agents/mine_hub_pages.py` (`--resolve-names`) or a small `harvest_and_resolve.py`;
free to build + unit-test, paid only when run. Confirmed feasible on the real College Transitions
page (names ARE in the server text). Residual limit: a FULLY client-rendered page whose text is
also empty still needs a headless browser — rarer than expected; flag, don't solve now.

Success criteria: (1) name extraction + dedup + per-name resolve built and unit-tested against a
fixture (fake page text in, resolved rows out); (2) a gated run over College Transitions'
Dataverse lands ≥15 real competitions at title-proven pages, operator approval ≥70%; (3)
per-name cost logged, capped, and attributed to the source page.

### Phase 4F — Feed-forward (**BUILT 2026-08-27 as `wingman/discovered_leads.py`**; router is structural)

*Spec below is the design record. **This status line has been wrong once already — read it against
the START HERE block, which is authoritative.** An earlier version said the hub half did not work
and the names half carried the feature; the router was rebuilt structural and it is the other way
round.*

- **The hub half is the working one.** A lead qualifies on **>= 6 distinct OFF-domain domains**
  (round-ups 9-20, ordinary pages 1-3), and `--from-leads` mines it **off-domain to match** — the
  fix in `52c6a56`, without which all 25 queued leads would have been mined for their own nav.
- **The names half is recorded, not consumed.** `agents/harvest_names.py` is operator-pointed (4N above).
- **Rule 0 sits in front of both** (`01cd374`): a page with **zero anchors was never received**, and
  gets no verdict rather than a guess. That alone was 10 of the 11 queued name leads.
- What free link-counting genuinely cannot do is spot **an institution's own index** — the kind-B
  gap in START HERE. Its fix is the walk-up-from-a-program idea, not a better threshold: raw
  same-domain counting calls 42% of all pages an index, nav-subtraction overlaps, and
  links-under-own-path scores 0 for 3 of 6 real indexes.

**The gap:** the search scraper, hub mining, and name-harvest are three disconnected channels.
But WHILE searching, the scraper constantly hits hub indexes and listicles and then throws them
away. **Measured (08-23 run, 28 seeds):** 660 grounding pages consulted → 126 became program
rows → **71 discarded content-mill/listicle URLs** (aralia "research journals for high school",
immerse "15 summer writing camps", lumiere "10 conferences") — each naming 7-15 programs, i.e.
hundreds of leads discarded per run. Those listicles are exactly name-harvest/hub feedstock.

**Mechanism — capture, don't inline-process** (keeps the free search run decoupled from paid
extraction, per the repo's cost discipline):
- In the scraper, for each resolved grounding URL that did NOT become a program row, classify
  (free): **content-mill/listicle** (`url_validate.is_content_mill` — pure, no fetch) →
  **name-harvest lead**; **non-mill page that LINKS ≥N HS programs** (one free
  `mine_hub_pages.harvest_links`/`filter_hub_links` check, capped per seed) → **hub-mining
  lead**. Everything else ignored.
- Write leads with their **source seed/angle** (so the ledger can later credit "angle X
  surfaced N good hubs"), deduped by URL against the catalog + prior leads.
- Hub mining / name-harvest CONSUME the queue in their own gated (paid) step.

**Storage — two tiers:**
- **MVP (no migration):** scraper appends to `discovered_leads.jsonl` + a `leads` list in the
  run snapshot; `agents/mine_hub_pages.py` gains `--from-leads`. Hub leads processable NOW (insert is
  wired); name-harvest leads wait on Phase 4N.
- **Mature:** a `discovered_leads` table (url, kind, source_seed_id, signal, status) reviewable
  in the console, same shape as `scraper_seeds` — with the degrade-and-retry pattern.

**Why high-leverage:** the scraper already PAID to consult those 660 pages; harvesting the
hub/listicle ones is nearly free and multiplies yield. It also means the hub registry no longer
has to be hand-curated — search *discovers* hubs. Reframes search as a hub-DISCOVERY engine.

Success criteria: (1) leads captured from a live scrape's grounding, classified + deduped +
attributed, at ~zero added cost/wall-time (capped per seed); (2) `mine_hub_pages --from-leads`
processes the hub leads into pending rows; (3) the run summary reports leads captured by kind so
the operator can see the new yield channel.

### Phase 5 — The compounding loop (free)

Build:
- `../../eval/build_fixture.py`: any adjudicated batch → grading fixture automatically (verdict +
  reason from the table, snapshot as row source). Ground truth grows with every review
  session.
- Harness deciders that call the REAL Phase-2/3 functions over snapshots (not
  reimplementations), so future changes are graded against all accumulated fixtures.
- Document the loop in this file: scrape → review (reasons) → fixture → diagnosis →
  angles retire/spawn → next scrape.

Success criteria:
1. `../../eval/build_fixture.py` regenerates the 08-23 fixture and matches the hand-built one.
2. A deliberately-broken decider (suppress-all) fails the gate loudly.
3. The next real batch's review produces a fixture with zero manual steps.

### The compounding loop — how it runs (shipped 2026-08-27)

    scrape ──▶ review (verdict + reason code land on each row in the catalog)
      ▲                                   │
      │                                   ▼
    next scrape ◀── angles retire/spawn ◀── diagnose (seed_ledger funnel per angle)
      ▲                                   │
      └──────── gate: grade_scraper_batch ◀── ../../eval/build_fixture.py (verdicts → frozen fixture)

- **One rule, graded automatically.** `classify_same_url` (the Phase-3 same-URL disposition) is
  imported by BOTH the live scraper and `grade_scraper_batch.decide_url_dup` — a change to the
  rule is re-graded against every accumulated fixture with no reimplementation to drift.
- **`../../eval/build_fixture.py`** turns any reviewed batch into a fixture from the live
  `moderation_status`/`moderation_reason`, so ground truth grows per review session at zero manual
  effort. Verified 2026-08-27 on the 08-23 batch: it reproduces a **grading-equivalent** fixture;
  the 12 id-level differences from the hand-built one are all real ground-truth evolution (7
  tombstone deleted→rejected relabels, plus 5 approved→duplicate/rejected re-moderations from
  Round 2 — including ec18681 Harvard AI, which the operator later marked duplicate, independently
  confirming Phase 3's auto-merge of that row). The hand-built 08-23 fixture stays frozen; new
  batches add fixtures, never overwrite.
- **The gate has teeth.** The `suppress-all` decider drops every approved row and the harness
  reports it UNSAFE (110–115 regressions) — proving a broken policy cannot pass. `0 regressions`
  remains the merge bar for every future pipeline change.

---

## Capability diff (before → after, for review)

| Capability | Now | After |
|---|---|---|
| URL of record | grounding-resolved else model URL + flag; listicles stored and die in review | mills never stored; offsite auto-rescued via on-page primary link; every URL title-proven; canonical page preferred |
| Page verification | liveness only | liveness + identity (page names the program) |
| Duplicates | exact URL+name skips; same-URL-diff-name inserts with flag (cost 30 manual resolutions) | same-URL never inserts; re-finds upgrade the existing row; in-run twins collapse |
| Names | whatever the model said ("Columbia") | student-searchable official names, title-evidence enforced |
| Angles | static list, no verdict feedback | live funnel + reason-mix diagnosis; $/approved ranking; mined-out auto-disable w/ console badge |
| New angles | hand-written | proposed from coverage gaps + siblings-of-winners, landing disabled |
| Channels | seed search only | + hub mining, + refind resurrection, + umbrella re-splits |
| Learning from reviewer | none (this analysis was manual archaeology) | every verdict+reason flows to angles, fixtures, and work-lists automatically |
| Change safety | judgment | harness gate: zero approved rows lost, ground truth auto-growing |
| Review burden | ~31% rejects, causes untracked | target <10%; twins pre-merged; every reject one labeled click |

---

## Gotchas for the implementing session (hard-won; do not re-derive)

- **The six paid agents (and any new model-calling script) need fresh explicit chat
  approval per run.** Preview tiers are free; `--dry-run` still pays.
- **Dev service runs on port 8000 only, via `restart_server.ps1`** (operator directive
  2026-08-26) — never Bash `&`, never the 8002/8004 launch.json alternates.
- **`supabase_common.supabase_get` paginates internally via Range headers** — adding a
  limit/offset loop on top 416s past the end of the table.
- **PostgREST 400s an entire write on one unknown column** → every new column needs the
  degrade-and-retry pattern (`moderation_reason` in `ops/core.moderate_opportunities`
  is the template) and must go in BOTH the CREATE and ALTER blocks of its schema file.
- **`opportunities.type` is NOT NULL**; `state` = US state code; `review_status` =
  check_reviews' verdict; don't reuse those names.
- **The 08-20 snapshot's id→row mapping is unusable** (date-only filename overwritten
  by a same-day run). Authoritative content of old rows lives in `dup_candidates`
  copies inside reviewed rows, or `opportunities.json` (a git-tracked backup, not
  runtime data).
- **Review snapshots are read by `wingman/dryrun_common.py`** — add keys, never change shape.
  `_patch_updates()` there must mirror any change to an agent's live PATCH columns.
- **The queue filter must spell out NULL** (`moderation_status.is.null` in the `or=`) —
  `NULL NOT IN (...)` is NULL in SQL.
- **`find_duplicates` measured limits**: name-sim ≥0.85 matches 257 genuinely distinct
  pairs; 73% of rows share a domain — neither may ever auto-reject. Shared portals are
  real (spicestanford.smapply.io = 6 programs).
- **Silent search**: retry identical prompt once, never prompt harder; still-silent →
  flag everything (`FLAG_NOT_SEARCHED`). Gemini's search decision is
  non-deterministic; there is no forcing mechanism.
- **~10-20% of program sites refuse our HTTP client** (403/TLS) while loading fine in a
  browser — a blocked fetch is never evidence about the program; skip the title proof,
  keep the blocked flag, stay approvable.
- **5s min-delay between Gemini calls is a floor** (429 history); cost is banked per
  attempt BEFORE any parse that can raise.
- **Console UI conventions**: `.vtab` swaps pages, `.tab` filters within a card; pills
  truncate at 90 chars with full text in `title=`; setup steps (missing migration)
  render as hints, never errors.
- Unit tests are hermetic (sockets blocked in `tests/conftest.py`); anything touching
  Supabase/HTTP gets mocked or extracted into pure helpers (`_moderation_updates`
  pattern).
