# Opportunity Scraper v2 — self-learning pipeline plan

*Rewritten 2026-08-26 (supersedes the 08-25 draft in this file's history). Self-contained:
a fresh session can pick this up with no other context. Status: design APPROVED by the
operator through the tenets and decisions below; Phase 0 shipped; Phases 1-5 await
review of this document.*

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
  snapshots. Harness: `grade_scraper_batch.py`. Probe already run: suppress-on-strong-dup
  would lose 18 approved rows → never a live rule.
- `pair_resolution_20260826.json` — the 30 pair outcomes (survivor + losers + notes).

---

## Current state (shipped and verified)

- `grade_scraper_batch.py` — replay/scoring harness. Hard gate for every phase:
  **zero human-approved rows suppressed**.
- `find_catalog_dups.py` — read-only self-dup sweep (48 identical-URL groups found;
  the 30 pair-shaped ones are resolved; multi-row portal groups deliberately left).
- Reject-reason capture live end-to-end (console modal → `moderation_reason` column).
- Tombstones retired; 56 backfill rows in table (note: `opportunities.type` is NOT
  NULL — backfill rows carry a 'Program' placeholder).
- Review queue: EMPTY as of 2026-08-26. Catalog ~1261 active / ~1607 total rows.
- Loose end: 13 rows sit `is_active=true` + `moderation_status='pending_review'` — the
  url_repair-restored rows from 08-23. Harmless (queue filters on inactive), tidy to
  'approved' whenever convenient.
- Related but separate systems already live: silent-search retry, seeds in
  `scraper_seeds` (yield counters found/added/dupes/cost via `record_seed_result`),
  review snapshots `{"inserted": [...], "rejected": [...]}` (shape read by
  `dryrun_common.py` — additions OK, shape changes not), per-seed debug logs in
  `agent_logs/scraper_<stamp>_seed<id>.json`.

---

## Phases

Grading rule for every phase: run `grade_scraper_batch.py` (and the pair fixture where
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
- `scrape_opportunities.py`: stamp `seed_id` on every inserted row (`build_row` or the
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
- `mine_hub_pages.py`: harvest (regex, free) → two-stage filter proven by the pilot:
  (a) anchor-level audience filter (drop elementary/middle/graduate/MBA/PhD/faculty/
  admitted-student links — cuts Wisconsin's 40 links to ~4), (b) fetch each surviving
  target and require high-school-audience words (free). One-level sub-hub recursion
  (an anchor like "Precollege Programs" is a hub, recurse once; hard caps: ~25 links
  per hub, same-registrable-domain links for institutional hubs, OFF-domain links for
  listicle hubs — following the wrong kind is how you crawl the internet). Dedupe +
  reason-checked against the catalog BEFORE any model call. Extraction = one no-search
  model call per surviving page (~$0.003; the URL is real by construction, no
  grounding needed — the `generate_action_items.py` shape: page in, JSON out). Rows
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

### Phase 4L — Local opportunities, Seattle-first (rides Phase 4; paid runs gated)

**Current state: dormant.** `--mode seattle` exists (hyperlocal `SEATTLE_SEEDS` +
`SEATTLE_ADDENDUM` in scrape_opportunities.py, console National/Seattle switch, `mode`
column on `scraper_seeds`), ran once 2026-08-18, ~nothing survived review. Local is the
worst case for search-first discovery (small orgs, no SEO, link rot) and the BEST case
for hub-first discovery — every local institution publishes a program index that never
ranks in search.

- **Strategy: depth in ONE metro as the template, not shallow local coverage
  everywhere.** Seattle first (where the users are); replicate the pattern per metro
  only when account locations warrant it (`/api/account/location` data exists).
- **A curated hub registry per metro** (~20-30 pages: Parks & Rec teen programs,
  SPL/KCLS, Pacific Science Center, Woodland Park Zoo, Seattle Aquarium, Seattle
  Children's youth volunteering, UW outreach/pre-college, district CTE, YMCAs, MoPOP,
  city youth commissions) fed to `mine_hub_pages.py`. A full metro sweep is ~$0.10-0.30
  and re-runnable seasonally. Search-mode seattle angles stay only for what hubs can't
  reach (regional competition rounds, county youth boards).
- Rows carry `state`/`location` as today; the finder already filters on them. Angles
  and hubs carry the metro so the Phase-1 ledger diagnoses local yield separately.
- **The creative-reasoning addendum ("a student could set up a farmers-market booth")
  is quarantined, not deleted** — and the boundary is precise (operator-confirmed
  2026-08-26): **the program must exist on a page; the PITCH for why it matters may be
  as creative as it likes.** A farmers market that actually hosts an "Emerging
  Entrepreneurs" event IS in scope — the market's events/programs page belongs in the
  hub registry, the row title-proves against that page, and its summary can say "sell
  to real customers, beta-test your product." What stays quarantined is inventing rows
  for opportunities no page describes ("a student *could* ask for a booth" where no
  program exists) — unverifiable by construction, same failure family as the invented
  Algebra-2 prerequisite. If run at all, that mode runs as a clearly labeled experiment
  whose rows say so — never mixed into the verified local sweep.

Success criteria: pilot sweep over ~10 Seattle hubs lands ≥15 local candidates in the
review queue at their own pages with `found_via` set, ≤$0.30, wrong-audience chaff
filtered; operator approval rate on the batch ≥60%; a `(metro)` cut appears in the seed
grid so local yield is diagnosable like any angle.

### Phase 5 — The compounding loop (free)

Build:
- `build_fixture.py`: any adjudicated batch → grading fixture automatically (verdict +
  reason from the table, snapshot as row source). Ground truth grows with every review
  session.
- Harness deciders that call the REAL Phase-2/3 functions over snapshots (not
  reimplementations), so future changes are graded against all accumulated fixtures.
- Document the loop in this file: scrape → review (reasons) → fixture → diagnosis →
  angles retire/spawn → next scrape.

Success criteria:
1. `build_fixture.py` regenerates the 08-23 fixture and matches the hand-built one.
2. A deliberately-broken decider (suppress-all) fails the gate loudly.
3. The next real batch's review produces a fixture with zero manual steps.

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
- **Review snapshots are read by `dryrun_common.py`** — add keys, never change shape.
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
