# Opportunity Scraper v2 — self-learning pipeline plan

*Rewritten 2026-08-26. Self-contained: a fresh session can pick this up with no other
context. Design APPROVED by the operator (tenets + decisions below). **All phases P1-P5
BUILT and P4 code shipped 2026-08-27 — see the Implementation Status section immediately
below before reading the phase specs, which are now the historical design record.***

---

## ⭐ START HERE — session of 2026-08-27 (latest) — NEW SESSION PICK-UP

**Branch `hub-social-audience-fix` is MERGED into local `main` (`c54fc41`), and Phase 4N is
BUILT on top of it (`83effca`). Neither is PUSHED yet — `main` is 2 commits ahead of
`origin/main` (`6408993`).** Gates on the merge and on 4N alike: **1392 tests green,
`grade_scraper_batch` url-dup 0 regressions, SAFE**. (The harness also prints a
`[suppress-all] … UNSAFE` line — that is its own self-test probe proving it can detect a
regression, not a failure.)

What landed in the merge (previously stranded on the branch):
- hub miner chaff filters: social/commerce/list-signup hosts (`_NONPROGRAM_HOSTS`),
  wrong-audience named only in the URL, WordPress internals.
- **the scraper resolves each new row's contact email** via `contact_email_common.
  resolve_contact_email` (regex-first, ~free); `find_contact_emails.py` is now a backfill for
  OLD rows only.
- civic chaff filters (`_CIVIC_PATH_SEGMENTS`, template `{{}}` drop) + `hubs_seattle.json`.
- **hub miner insert step WIRED**: `mine_hub_pages.py` writes real `is_active=false`/
  `pending_review` rows (`agent_runs agent='hub_miner'`, snapshot, `--dry-run`). Extraction is
  still PAID and gated.

### Phase 4N — BUILT this session (`harvest_names.py`, +53 tests). NOT YET RUN PAID.

Reads the program NAMES off a page and resolves each to its own page through the refind
primitive (one narrow search → grounding-resolved → title-proven). Three FREE gates run
before a single search is paid for — `name_is_on_page`, `name_is_resolvable`,
`is_known_name`; see the module docstring, which carries the reasoning for each.

**Validated FREE against the real target and 1673 live catalog rows** (no model call, no
spend). The College Transitions competitions table is at
`https://www.collegetransitions.com/dataverse/academic-competitions-and-contests/` — fetches
to **14,355 chars**, matching the figure this plan measured, and lists ~70 named competitions
in a table with 0 harvestable program links. Feeding 35 of its names through the gates:
**24 would be searched, 6 correctly matched to rows we already have** (Congressional App
Challenge = ec18605, National Science Bowl = ec17913, …), and a planted off-page control
("Telluride Association Summer Seminar") was rejected by gate 1.

Two things that measurement changed, both worth not re-deriving:
- **`is_known_name` carries the DIGITS `identity_words` discards, and NOT short alphabetic
  tokens.** "1-Week" and "3-Week Medical Academy" both reduce to {week, medical} — the digit
  is the whole difference, which is CLAUDE.md's measured 0.95-ratio collision. But treating
  short ALPHA tokens as marks made "Academic Decathlon" miss the row we already hold ("US
  Academic Decathlon", ec17937) on the "us" alone, i.e. re-paying for a program we have. A
  digit says WHICH program; "US" says where.
- **Known recall gap, reported per run rather than hidden: single-token brand names cannot be
  title-proven.** `title_proves` needs two identity words, so CyberPatriot, DECA, iGEM and
  Model UN are dropped as `unprovable`. Three of those four are already in the catalog; **iGEM
  is a genuine miss.** The gate is not wrong — it refuses to pay for a name whose answer
  could never be verified — but the class is real, so every dropped name is printed in full
  (never a head slice) for a person to pick up.

`--preview` is free, prices the run over the **fetchable** pages only, and prints an excerpt so
a cookie banner and a program table are distinguishable. Measured on the national registry:
3 of 5 hubs fetchable (CEISMC 403s, medicine.illinois URLErrors — the client fact this plan
already records).

### Session 2026-08-27 (late) — 4F built, both channels LIVE-TESTED (PAID $0.24)

**Phase 4F BUILT** (`discovered_leads.py`, +46 tests). Free capture of the pages a search
already paid to consult; acting on a lead stays separately gated. Two measured findings:
- **Not every content mill is a listicle.** Fetching one of each: lumiere/immerse/aralia
  19.5-24k chars of real listicle prose, en.wikipedia 7.7k of real prose, **youtube 24k chars
  of `ytcfg` JS config** (billable junk), **reddit 0** (refuses our client). YouTube was 20 of
  109 mill hits. youtube/youtu.be/reddit excluded; wikipedia kept.
- **Free link-counting CANNOT identify a hub page — automatic hub capture ships OFF**
  (`HUB_PROBE_PER_SEED = 0`). At budget 8 it called **204 of 273 probed pages (75%)** hubs,
  including /faq/, /apply/, /contact/, a PR release and a job posting. Two discriminators
  measured on 6 known indexes vs 7 known non-indexes: **raw count good 11-94 vs bad 7-53**;
  **nav-subtracted good 0-57 vs bad 0-35** — both fully overlapping. Same-domain links on any
  page are dominated by shared nav, and nav subtraction cannot fix it (`precollege.wisc.edu`,
  a good hub, scores 0 because it IS the site root). The machinery is kept and tested;
  `capture(probe_budget=N)` re-enables it if a real discriminator is ever found.
- The names half needs no probe and works: replaying the 40 archived seed logs queued **70
  clean listicle leads**. `mine_hub_pages --from-leads` / `harvest_names --from-leads` consume.

**HUB MINING — first live run ever (PAID $0.0461).** 3 hubs → 30 candidates → **19 rows**,
0 errors. Real programs (USNA Summer STEM + Summer Seminar, UW-Madison BEL, Badger Summer
Scholars, Summer Music Clinic, Engineering/Pharmacy summer programs). Rows ec18756-ec18774,
`is_active=false`, **awaiting operator review.** Known residual: several rows are sub-pages of
one program (Music Clinic senior/mini/auditions) and "Candidate Visit Weekend" is an admissions
visit, not an extracurricular — reviewer calls, not code bugs.

**NAME HARVEST — first live run ever (PAID $0.1926).** 3 listicle leads → 24 names → 10
searched → **3 rows** (ec18775-ec18777). **The result is a negative finding and matters more
than the rows: ALL THREE are Immerse Education products**, two from Immerse's own listicle,
while every independent program the same pages named (Parsons, Otis, Drexel, NYU Tisch,
Columbia, MAD) came back UNPROVEN.
- **Cause: a listicle heading is a DESCRIPTION, not a canonical name.** "Drawing: Eye and Idea
  Pre-College Course at Columbia University" is not what Columbia calls it, so `title_proves`
  can never match — whereas a company names its OWN products canonically in its own article.
  **The evidence bar therefore selects self-promotion.** `FLAG_SELF_PROMOTED` now marks a row
  that resolved back onto the site that named it (flag, never reject — a provider can host a
  real program, per the operator's Immerse ruling).
- **So 4N works on canonical-name DIRECTORIES, not on marketing listicles.** The free
  validation on College Transitions' competition table produced 24 clean canonical names
  ("Academic Decathlon", "BEST Robotics Competition"); the listicles produced descriptions.
  **That directly changes what 4F should feed 4N** — the 70 queued leads are mostly marketing
  listicles, i.e. the weak case. Before draining them, consider: ask the naming call for the
  program's canonical name AND host org as separate fields, and search on org+name.

**Four bugs the live runs found and fixed** (commit `2601577`): the hub extract prompt never
listed the legal `type` values (**19 of 19 rows** carried FLAG_NO_TYPE); the hub miner did not
collapse in-run twins (one index links a program and its sub-pages — `/accelerated-learning-
program/` + `/alp/`); `agent_common.safe_console()` — a **U+2011 in model output crashed a run
after its paid call had returned** (cp1252 console); and the self-promotion flag above.

### OPEN / do next
1. **Push `main`** — now 11 commits ahead of `origin/main` (the merge brought 5 branch commits with it). Scraper-only (no `app/`,
   `render.yaml`, `requirements.txt`, `server.py`), so a Render deploy is a no-op for the web
   service.
2. **Review the 22 new pending rows** in the console: ec18756-ec18774 (hub-mined, mostly good)
   and ec18775-ec18777 (name-harvested, all three self-promotion — the honest verdict is
   probably to keep the two real Immerse program pages and reject the `/pathways/career`
   marketing page).
3. **Decide 4N's next input before spending more on it.** Point it at the College Transitions
   table (24 canonical names, ~$0.75 at `--max-names 10`) rather than at more listicles, OR
   build the canonical-name + org change first.
4. **4L (local) stays PINNED** — rethinking from scratch. 4F does not replace it.
5. Standing backlog: ~167-row refind (~$4-8 in small PAID batches), gated hub extraction on
   more registry hubs.

**The visual map of all of this (live/built/unbuilt, colour-coded) is a published artifact —
ask the operator for the "Scraper Logic Map" link if you need it.** It predates Phase 4N being
built and shows it as unbuilt.

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
session (app/services/email*.py, send_lifecycle_emails.py, app/config.py) — when
committing, stage ONLY scraper files. The email commit `6f9ab2f` sits in scraper-v2's
history; reorganize branches later if you want them fully separate.

**State: all phases built, 1263 tests green, grading harness SAFE. `0 regressions` on
`python grade_scraper_batch.py` is the merge bar for any future change.**

### DDL run by the operator (live): `scraper_attribution_schema.sql`, `scraper_seeds` ALTER (disabled_reason/at). `moderation_reason` was already live.

### What actually RAN this session (live, on real data)
- **seed_id backfill DONE**: `backfill_seed_attribution.py` stamped **143/159** Aug-23 rows
  (16 honest `(no seed)`: 14 unmatched, 2 ambiguous). Idempotent; match is same-run-date +
  unambiguous. → the console seed funnel is now populated with real Appr/$-per-approved.
- **Console verified live** at /admin → Scraper angles: funnel columns (Appr/Rej/Dup/Waste %/
  $/appr/Diagnosis) render; every angle shows `small sample` (each has 1 run; needs ≥2 + ≥10
  found to diagnose or auto-disable). "Recent merges" card wired ("No merges yet").
- **18 gap angles written as DISABLED seeds** (`propose_angles.py --commit`). Review + enable
  the worthwhile ones in the console.
- **Refind pilot RAN (PAID, $0.4091)**: `refind_dead_links.py --limit 20`. 20 searched, **4
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
3. **Hub extraction is PAID and still gated.** `python mine_hub_pages.py --hubs-file
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
- `python grade_scraper_batch.py` — FREE gate. `0 regressions` required to ship any change.
- `python build_fixture.py --batch B --snapshot F1 F2 --out tests/fixtures/X.json` — FREE.
- `python backfill_seed_attribution.py [--commit]` — FREE (done; idempotent).
- `python propose_angles.py [--commit]` — FREE (18 written).
- `python mine_hub_pages.py --hubs URL --preview` FREE / live = PAID.
- `python refind_dead_links.py --preview` FREE / `--limit N` = PAID search.
- `python harvest_names.py --hubs URL --preview` FREE (prices the run over fetchable pages) / without `--preview` = PAID (1 naming call + up to `--max-names` searches).
- `python scrape_opportunities.py --mode national --seed-ids IDS` — PAID (preview via console).
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

### Phase 4L — Local opportunities (**PINNED 2026-08-27 — do not build**)

**The operator has pinned local discovery to rethink the strategy from scratch.** Everything below is the MEASURED RECORD of why the first two designs did not work — keep it, it is what any new strategy has to answer to — but none of it is a work item, and 4F is not automatically its replacement. Ask before resuming.

**Current state: dormant, and REDESIGNED 2026-08-27 after a measured Seattle preview
overturned the original premise.** `--mode seattle` exists (hyperlocal `SEATTLE_SEEDS` +
`SEATTLE_ADDENDUM` in scrape_opportunities.py, console National/Seattle switch, `mode`
column on `scraper_seeds`), ran once 2026-08-18, ~nothing survived review. Search-first is
dead for local (small orgs, no SEO, link rot). The obvious replacement — "hub-first, every
local institution publishes a program index" — was **tested and found only half true.**

**MEASURED (2026-08-27, `hubs_seattle.json`, 8 fetchable Seattle hubs, free preview):**
`mine_hub_pages.py` harvested/filtered to **54 candidates → 42 after new local chaff
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

### Phase 4N — Name-harvest → search (**BUILT 2026-08-27 as `harvest_names.py`**; not yet run paid)

*The spec below is the design record. What shipped follows it in every respect except one: the per-name resolver is `harvest_names.best_resolved_url`, not `refind_dead_links.best_refound_url` directly — refind holds a candidate to the same registrable domain as the DEAD url, and a harvested name has no prior url to hold it to, so `title_proves` carries the whole weight there. That is exactly why free gate 2 refuses an unprovable name before any search is paid for. See the START-HERE block for what the build measured.*

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

Build as a new mode on `mine_hub_pages.py` (`--resolve-names`) or a small `harvest_and_resolve.py`;
free to build + unit-test, paid only when run. Confirmed feasible on the real College Transitions
page (names ARE in the server text). Residual limit: a FULLY client-rendered page whose text is
also empty still needs a headless browser — rarer than expected; flag, don't solve now.

Success criteria: (1) name extraction + dedup + per-name resolve built and unit-tested against a
fixture (fake page text in, resolved rows out); (2) a gated run over College Transitions'
Dataverse lands ≥15 real competitions at title-proven pages, operator approval ≥70%; (3)
per-name cost logged, capped, and attributed to the source page.

### Phase 4F — Feed-forward (**BUILT 2026-08-27 as `discovered_leads.py`**; hub half OFF)

*Spec below is the design record. What shipped differs in one measured respect: the hub-lead half does not work and is disabled by default — free link-counting cannot tell an index from a page with a big nav (both discriminators measured, fully overlapping). The names half shipped and queued 70 leads. See the session block above.*

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
  run snapshot; `mine_hub_pages.py` gains `--from-leads`. Hub leads processable NOW (insert is
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

### The compounding loop — how it runs (shipped 2026-08-27)

    scrape ──▶ review (verdict + reason code land on each row in the catalog)
      ▲                                   │
      │                                   ▼
    next scrape ◀── angles retire/spawn ◀── diagnose (seed_ledger funnel per angle)
      ▲                                   │
      └──────── gate: grade_scraper_batch ◀── build_fixture.py (verdicts → frozen fixture)

- **One rule, graded automatically.** `classify_same_url` (the Phase-3 same-URL disposition) is
  imported by BOTH the live scraper and `grade_scraper_batch.decide_url_dup` — a change to the
  rule is re-graded against every accumulated fixture with no reimplementation to drift.
- **`build_fixture.py`** turns any reviewed batch into a fixture from the live
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
