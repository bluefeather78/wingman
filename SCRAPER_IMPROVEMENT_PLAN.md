# Scraper improvement plan — precision, dedupe, and new discovery channels

*Drafted 2026-08-25 from the first full human grading of scraper output: the 378 review-queue
decisions and ~46 direct DB deletions made 2026-08-23/24.*

## What this hopes to achieve, in plain English

Right now, roughly one in three rows the scraper finds is thrown away by a person — not
because the program isn't real, but because the row arrived broken in a way the pipeline
could have caught or fixed on its own. The three big ways it arrives broken:

1. **The URL points at somebody writing *about* the program** (a blog listicle, a Reddit
   thread, a YouTube video) instead of the program's own page. The program is real and
   worth listing; the row dies anyway, and a real opportunity is lost to students.
2. **We already have it.** The scraper re-finds programs that are already in the catalog —
   or that another seed found ten minutes earlier in the same run — and a person has to
   recognize the twin, pick a survivor, and clean up by hand.
3. **The catalog quietly rots.** When a program's page moves, the link checker deactivates
   the row, the reviewer rejects it, and a flagship program (AMC 8, USAMO, Boys State)
   simply vanishes from the app with no path back.

The goal of this plan is that **a scrape run produces rows a reviewer can approve at a
glance, and almost nothing they have to untangle**: URLs that are provably the program's
own page, no twins of things we already list, stale catalog URLs upgraded instead of
duplicated, and dead flagship programs re-found instead of lost. Secondary goal: two new
discovery channels (hub-page mining, resurrection of dead rows) that find *valid, unique*
opportunities more cheaply than another pass over saturated search angles.

### The gap, measured

Grading of the 2026-08-23 batch (166 inserted rows) against the human decisions:

| Outcome | n | Dominant causes |
|---|---|---|
| Approved | 115 (69%) | — |
| Rejected | 44 (27%) | ~28 duplicates of existing/active rows; ~11 third-party URLs; rest wrong-page-on-right-site |
| Deleted by hand | 7 (4%) | listicle URLs, a rules PDF, a professor's personal page, intra-run twins |

Plus, outside the batch: **~19 existing catalog rows deleted by hand** to consolidate
pre-existing duplicates the batch's dup-candidates surfaced (Conrad Challenge ×2 from
www/slash URL variants, Girls Who Code ×2 under one URL, The Concord Review ×3), and
**~200 dead-link rows rejected** — including programs that certainly still exist — with no
mechanism to re-find them.

Human decision rules inferred from those actions (the spec this plan implements):

- A third-party URL is rejected essentially 100% of the time — but the listicle itself
  links to the real page, so the fix is retrieval, not rejection.
- Between duplicate copies, **the row with the best canonical program-page URL survives,
  regardless of age**: new copy rejected when the old row is well-linked; old row deleted
  when its URL is a bare root domain or junk (Wikipedia, `tcr.org/page-1826185`).
- 403 / TLS / timeout flags and "homepage of a dedicated program site" were approved
  freely — those signals are calibrated correctly today and must not be tightened.
- Same-site siblings that are genuinely different programs (Berklee's programs, YoungArts
  disciplines) were approved — so no name-similarity auto-reject, ever (reconfirming the
  existing url_dedupe measurement: 257 of 264 pairs at ratio ≥0.85 are distinct programs).

### Non-negotiables carried over from existing findings

- Never auto-activate. Everything still lands `is_active = false`.
- Never auto-reject on name similarity or shared domain alone (url_dedupe's measurements).
- No new private dedupe rules in the scraper — everything goes through `url_dedupe`.
- The three `normalize_url()` copies stay untouched; only `url_dedupe.match_key()` grows.
- Paid runs still need fresh explicit approval; everything in Workstreams 0–2 is free
  (plain HTTP, no model calls).
- The review snapshot shape (`{"inserted": [...], "rejected": [...]}`) is read by
  `dryrun_common.py` — additions are new keys, never shape changes.

---

## Workstream 0 — the measuring instrument, and two free repairs (do first)

### 0.1 A labeled regression harness: `grade_scraper_batch.py`

We now have something the scraper never had: **ground truth**. 166 rows with a human
verdict on each (approve / reject / delete), plus the per-seed logs and snapshots that
produced them. Encode it before changing anything:

- New script `grade_scraper_batch.py` (free, stdlib): takes a snapshot file + a verdicts
  JSON (committed fixture built from the 2026-08-23 grading), replays any candidate-level
  decision function over the snapshot, and reports: how many human-rejected rows would now
  be suppressed/converted, and — the number that matters more — **how many human-approved
  rows would have been blocked** (must stay at 0).
- Every change in Workstreams 1–2 lands with a before/after from this harness. This is the
  same discipline `url_repair.py` used (72 → 13 proposals, measured at each gate).
- Fixture: `tests/fixtures/scraper_grading_20260823.json` — row id → verdict, built from
  the DB pull + the deletion list (already reconstructed in this session).

### 0.2 Tombstones: stop deleted rows from coming back

Deleting a row punches a hole in dedupe memory: `find_duplicates()` matches against the
table, so a deleted URL no longer blocks re-insertion. `conradchallenge.org` and
`girlswhocode.com/programs/summer-immersion-program` currently exist nowhere in the table;
the next run over business/CS angles will re-insert them as new pending rows.

- New git-tracked file `scraper_tombstones.json`: a list of `{url, name, note}` entries
  for rows deliberately deleted from the DB (seeded with the ~46 deletions from 08-23/24,
  names/URLs recovered from the snapshots and `opportunities.json` backup).
- `scrape_opportunities.main()` loads it and appends the entries to the `existing` dedupe
  pool (they need only `id`/`name`/`url` shape; use ids like `tomb-0001`). An exact match
  against a tombstone is skipped with reason `"previously deleted by operator"` in the
  snapshot's `rejected` list — visible, never silent.
- Going forward the console path is preferred over deletion (see 2.4); the tombstone file
  is the retrofit for rows already gone.

### 0.3 Catalog self-dup sweep (report only)

The Conrad pair proves the live catalog still holds normalization-variant duplicates from
the pre-fix era, and Concord Review ×3 suggests more. One free script pass:

- For all rows (active + inactive): group by `url_dedupe.match_key(url)`; report groups
  with >1 row. Second cut: same `registrable_domain` + `name_similarity ≥ 0.9`, reported
  as *hints only* (that threshold is known to be wrong often — it goes to a human list,
  never to an action).
- Output is a report for console review, not writes. Feeds 2.4's merge tooling.

### 0.4 Verify the dup-candidate id mismatch

Rejected row `ec18686` (NACLO) recorded dup candidate `ec18517` with reason "name 100%
similar", but `ec18517` was NYU Tisch's Filmmakers Workshop per the 08-20 snapshot. Either
the reconstruction is misleading or `find_duplicates()` misaligns ids and reasons when
building candidate lists. One targeted unit test against `find_duplicates()` with a
synthetic pool settles it; fix if real.

### Workstream 0 status — SHIPPED 2026-08-25, all read-only

- **0.1** `grade_scraper_batch.py` + `tests/fixtures/scraper_grading_20260823.json`
  (166 verdicts: 115 approved / 44 rejected / 7 deleted, pinned by test). First real run
  already produced two findings: the batch predates `FLAG_OFFSITE`, so W1 policies must
  RECOMPUTE signals over snapshot rows rather than read stored flags; and a naive
  suppress-on-strong-dup would have cost **18 approved rows** — most of whose dup targets
  were the pending twins the operator deleted, confirming W2.1 must rank survivors by
  target status, never suppress the newcomer outright.
- **0.2** `scraper_tombstones.json` (54 entries, every one verified absent from the DB)
  + `load_tombstones()` wired into `scrape_opportunities.main()`'s dedupe pool with its
  own skip reason. Matching is URL+name like live dedupe, so a program re-found at a NEW
  url is never blocked.
- **0.3** `find_catalog_dups.py` — first run: **48 identical-normalized-URL groups**
  (3 same-name near-certain dup pairs, all-active; several same-program-renamed pairs
  where an 08-23 approval duplicated an older active row, e.g. `ec18681`/`ec18345`
  Harvard AI Bootcamp, `ec18536`/`ec17087` CNI-X, `ec18600`/`ec17095` SIMR) plus 17
  name-similarity hint pairs. Report-only; consolidation stays a console decision.
- **0.4 RESOLVED — not a code bug.** `find_duplicates()` alignment is pinned by test.
  The observed NACLO/`ec18517` mismatch was the date-only snapshot-overwrite bug: the
  on-disk 08-20 snapshot is a different run than the one that minted those DB ids. DB
  `ec18517` really was NACLO (`nacloweb.org`), proven by the dup-candidate copies in the
  reviewed rows. Consequence handled: the `ec18420`–`ec18518` tombstone entries were
  rebuilt from authoritative sources (backup + dup-candidate copies), never from that
  snapshot. **Treat the 08-20 snapshot's id→row mapping as unusable.**

---

## Workstream 1 — URL truth (every fix here is free HTTP)

### 1.1 Rescue offsite URLs by extracting the primary link from the secondary page

**Gap:** ~11 of 44 rejections + 4 of 7 deletions were third-party URLs. The programs were
real; every one of those listicles/posts links to the official page in its body.

**Change** — in the `main()` per-candidate loop, when `domain_matches_org()` fails for the
chosen URL (i.e. where `FLAG_OFFSITE` is attached today), run an escalation ladder:

1. **Another resolved grounding URL that passes `domain_matches_org()`** — prefer a
   span-attributed one, else any same-run resolved URL that also passes
   `url_repair.title_proves()` for this candidate. Free, already in hand.
2. **Fetch the offsite page and harvest its outbound links** (new
   `url_repair.extract_primary_link(page, name, org)` — reuses the existing `_fetch`,
   `_LINK_RE`, `identity_words`, `title_proves`). Accept an outbound link only if it
   passes BOTH existing tests: `domain_matches_org()` (it is on the org's own domain) and
   the title proof on a fetch of the target. Both gates are required: SEO mills' most
   prominent outbound links are their own signup funnels (Ladder, Lumiere), and the title
   proof is what stops a link to the org's homepage-of-the-wrong-program. Anchor-text
   match on `identity_words` picks the candidate links to try (cap at ~5 fetches).
3. **Nothing passed** → keep today's behavior exactly: store the model/grounding URL with
   `FLAG_OFFSITE`. No paid retry search in this phase (revisit only if the harness shows a
   meaningful residual — the on-page link should cover most cases).

A successful rescue replaces the URL and adds a new flag naming the provenance:
`FLAG_URL_RESCUED = "URL taken from the primary link on <secondary-domain> — verified by title"`.

**Path-aware content-mill denylist** — small git-tracked list (lumiere-education.com,
admissionsight.com, scholarships360.org, nshss.org blog paths, borderless.so, aralia.com,
indigoresearch.org, ladderinternships.com, opportunitiesforyouth.org,
immerse.education/knowledge-base/*, youtube.com, reddit.com, en.wikipedia.org…). A URL
matching it can never be the *stored* URL (rung 3 for these stores no URL-of-record —
insert with the offsite flag and the denylisted URL preserved in `quality_flags` text so
the reviewer still has the lead). Path-aware because immerse.education is also a
legitimate provider (approved at `/summer-schools/`); only its `/knowledge-base/` path is
a mill.

**Acceptance (harness):** ≥8 of the 11 offsite-rejected rows in the fixture come out with
an org-domain, title-proven URL; 0 approved rows change URL to something failing the title
proof.

### 1.2 Title-proof every stored URL

**Gap:** wrong-page-on-right-site rows pass every current check (Cooper Union's makerspace
facilities page, Cornell's outreach page for NACLO, FIT's costs page, a rules PDF, a
professor's personal page).

**Change:** after URL resolution, fetch each candidate's final URL (the liveness check in
`check_urls()` already touches it; extend it to return the body or re-fetch — one page per
candidate, bounded by `PAGE_BYTES`) and run `url_repair.title_proves(title, name, org)`.

- Pass → no flag.
- Fail, and a sibling grounding URL on the same org domain passes → swap to the sibling,
  keep `FLAG_URL_REPLACED`.
- Fail with no better sibling → new flag
  `FLAG_TITLE_UNPROVEN = "page title does not name this program — confirm it is the program's own page"`.
  Never a rejection: title proof has known false negatives ("Algebra II" vs "Algebra 2"
  class of problem), and 403/blocked pages can't be fetched at all (skip the test, keep
  the existing blocked flag — the reviewer approves those freely today and that behavior
  is correct).
- PDFs and non-HTML content types auto-fail the proof (they have no usable title) → same
  flag; catches the Cooper Hewitt rules-PDF case.

### 1.3 Prefer the canonical program page over its own subpages

**Gap:** deep junk got stored when a parent page was available (`/admissions`, `/rules/`,
`/costs/`, `/faq`). The reviewer approved subpage URLs when nothing better existed, so
this is a preference, never a gate.

**Change:** when multiple same-org-domain URLs are available for one candidate (span URLs
+ resolved URLs + rescued links), rank them: title-proof pass first, then non-low-value
path (`url_dedupe.is_low_value_path`, extended with `admissions`, `costs`, `rules`,
`register`, file extensions `.pdf`), then shortest path depth. Store the winner; demote
today's implicit "first span URL wins".

---

## Workstream 2 — catalog-aware dedupe and merge

### 2.1 Re-found active rows: suppress the twin, propose the upgrade

**Gap:** ~28 of 44 rejections were re-finds of active rows; separately ~19 old rows were
deleted by hand because the *new* copy had the better URL. Both directions of the same
event, currently handled entirely by a human.

**Change:** in the insert loop, after `find_duplicates()` returns, add one decision for
candidates matching an **active** row with same `registrable_domain` AND
`name_similarity ≥ 0.9` (deliberately above the 0.82/0.88 hint thresholds; AND-ed with
same-domain; active rows only):

- Existing row's URL passes the title proof (or equals the candidate's) → **do not
  insert.** Record in the snapshot as `re_found` with the existing id — and use the free
  side effect: the candidate URL's liveness check just confirmed the program is alive,
  which the snapshot notes.
- Existing row's URL is a bare root domain or fails the title proof while the candidate's
  passes → **do not insert a row; emit a URL-upgrade proposal instead.** New snapshot
  section `url_upgrades: [{id, old_url, new_url, evidence}]` and a matching console list
  (2.4) where a person applies them one-click. Automatic overwrite of an active row's URL
  stays off the table — that is `check_links.py`/console territory, and a wrong swap on a
  live row is the worst failure available here.
- Below the similarity bar, or inactive/rejected target → today's behavior (insert with
  dup candidates as hints). Same-site distinct programs (YoungArts disciplines, Berklee)
  sit below the bar or differ in name enough to insert — harness must show 0 approved
  rows suppressed.

### 2.2 Pending-row twins: merge instead of inserting a sibling

**Gap:** the 08-23 run re-found much of the 08-20 queue; the reviewer approved the new
copy and deleted 27 old ones by hand.

**Change:** candidate matches a **pending** (`is_active=false`, not rejected) row by the
2.1 rule → PATCH that row in place (URL if the new one wins the 1.3 ranking, plus
refreshed summary/eligibility fields), append a `quality_flags` note
(`"updated by scraper run <stamp>; previous url <old>"`), and do not insert. The queue
keeps one row per program that improves across runs instead of accreting siblings.
Dry-run mode records the would-be merge in the snapshot without writing, like every other
withheld write.

### 2.3 Cross-seed, same-run name dedupe

**Gap:** NACLO from seeds 6 and 38, Civic Innovators from 9 and 12, Cooper Hewitt and
LaunchX twice — same run, different URLs, so the existing per-URL pool append missed them.

**Change:** the `existing.append(...)` in-run pool already handles exact URL matches;
extend the in-run check (only for rows minted *this run*) to same-registrable-domain +
`name_similarity ≥ 0.9` → keep the copy whose URL wins the 1.3 ranking, fold the loser
into the snapshot's `rejected` with reason `intra-run duplicate of <id>`. In-run rows
only: this looser rule never touches the real catalog, where it would be wrong.

### 2.4 Console: merge action and upgrade queue (ops/, local-only)

- **Merge into survivor:** on any queue row with dup candidates — pick survivor; losers
  get `moderation_status='duplicate'` + `duplicate_of=<survivor>` via the existing
  moderate endpoint (which keeps their URLs in the dedupe pool — the whole reason to
  prefer this over SQL DELETE), with an optional "carry this URL to the survivor" step
  when the loser's URL wins the title-proof comparison. Carrying a URL onto an *active*
  survivor is a new, deliberate capability: gated behind its own confirm, logged into the
  survivor's `quality_flags` with the old URL (hand-reversible, same convention as
  `--repair-flagged`).
- **URL upgrades tab:** renders 2.1's `url_upgrades` proposals with side-by-side old/new
  and the title evidence; apply is per-row, explicit.
- **Self-dup report:** renders 0.3's output with the same merge action.

---

## Workstream 3 — new discovery channels (model spend; needs run approval)

### 3.1 Resurrection mode: `--refind`

**Gap:** ~200 rejected dead-link rows include flagship programs that still exist (AMC 8,
USAMO, Boys State, Jackson Lab, SMYSP, Wharton Investment Competition, YSPA, CMU CS
Scholars, Brave New Voices). Rejection was correct — the row was dead — but nothing ever
looks for the moved page.

**Change:** `scrape_opportunities.py --refind [--limit N]` selects rejected rows whose
`quality_flags` carry this pipeline's own dead-link flag, and runs one narrow phase-1
search per row: *"current official page for «name» by «org»"* — grounding-resolved URL,
title proof, then the normal insert path (which dedupe-checks against the tombstones and
the old row's URL naturally, since the new URL differs). A program found to be genuinely
discontinued (Siemens) simply yields no qualifying page and writes nothing — record it in
the snapshot as `not_refound` so the same row isn't re-paid for next pass (stamp a
`refind_attempted` note into its `quality_flags`).

Cost: ~1 search/row → ballpark $0.02–0.05/row, ≈ $5–10 for the whole backlog, batchable
with `--limit`. Every run needs the standard fresh approval.

### 3.2 Hub-page mining

**Gap and opportunity:** pages that *list many programs* currently produce one flagged row
at best — yet a university's own pre-college index (e.g.
`expandedlearning.ceismc.gatech.edu/summer-programs/sessions/high-school`) is a denser,
truer source than another web search, and even rejected listicles name programs the
search angles never surface.

**Change:** new script `mine_hub_pages.py` (kept out of the per-seed hot path):

- **Input:** explicit hub URLs (operator-provided or collected by the scraper when a page
  fails the title proof but contains ≥3 program-looking links — those get written to the
  snapshot as `hub_candidates`, mined only by a later explicit run).
- **Link harvest:** free, regex-first (`_LINK_RE`), same philosophy as
  `find_mailing_lists.py`. Direction rule: for an institutional hub (org-domain page),
  follow only same-registrable-domain links; for a listicle, follow only *off*-domain
  links. Cap links per hub (~25) after `html_to_text`-style main-region stripping.
- **Dedupe first, spend second:** every harvested link goes through
  `url_dedupe.find_duplicates()` + tombstones before any model call — the whole point is
  that most links on a good hub are either new or already ours, and only the new ones
  cost anything.
- **Extraction:** for survivors, fetch the target page and run a single no-search model
  call per page to extract the catalog fields (the URL is real by construction — we
  followed it — so the entire grounding apparatus is unnecessary; this is the
  `generate_action_items.py` shape: page in, JSON out, `page_text.py` plumbing). Rows
  land `is_active=false`, `source="hub-<domain>-<date>"`, with a
  `found via hub: <hub url>` flag so the review queue shows siblings together.
- Cost: ~$0.002–0.005 per candidate page (no search fee anywhere) — an order of magnitude
  cheaper per row than seed search.

### 3.3 Saturation-aware angle management

**Gap:** approval by seed was bimodal — original broad angles (seeds 1–13) 0–60%, the
newer niche angles (dance, culinary, theology, sports science, film, debate, cyber,
environmental) **100%**. The catalog has absorbed the head of the distribution; broad
angles now mostly re-buy duplicates.

**Change (mostly free, uses existing plumbing):**

- `record_seed_result()` already credits `found/added/dupes/cost`. Add the post-review
  truth: a small script (or console action) that, after a batch is adjudicated, writes
  each seed's approved/rejected counts back to `scraper_seeds` (needs two columns —
  extend `scraper_seeds_schema.sql` with the usual CREATE+ALTER pairing). The console's
  seed grid then ranks angles by *approved rows per dollar*, not inserted rows.
- Console surfaces a "saturated" hint (high dup share across last N runs) so retiring an
  angle is an informed one-click, not archaeology.
- Guidance for new angles, written into the seeds doc: specificity wins — the 100%-approval
  angles were all niche; Workstream 2 makes broad angles cheap to keep (their re-finds
  become suppressions and URL upgrades rather than review work), so retirement is about
  spend, not queue noise.

---

## Sequencing and effort

| Order | Item | Cost to run | Depends on |
|---|---|---|---|
| 1 | 0.1 harness + fixture | free | — |
| 2 | 0.2 tombstones, 0.3 self-dup report, 0.4 dup-candidate bug check | free | — |
| 3 | 1.1 offsite rescue + denylist | free | 0.1 (to grade) |
| 4 | 1.2 title proof, 1.3 canonical ranking | free | 1.1 |
| 5 | 2.1 re-found suppression + upgrade proposals | free | 1.2 |
| 6 | 2.3 cross-seed dedupe, 2.2 pending merge | free | 2.1 |
| 7 | 2.4 console merge/upgrade UI | free | 2.1, 0.3 |
| 8 | 3.1 `--refind` backlog pass | ~$5–10 total, needs approval | 1.1–1.3 |
| 9 | 3.2 hub mining (CEISMC first as pilot) | ~$0.05–0.15 per hub, needs approval | 1.2, 2.x |
| 10 | 3.3 seed outcome writeback + console ranking | free (one small DDL) | a graded batch |

Everything through step 7 is free and verifiable offline against the 08-23 fixture before
any paid run. The next real scrape run is the live test: target is a batch where the
reviewer's reject rate drops from ~31% (44 rejected + 7 deleted of 166) to under 10%, with
zero previously-approvable rows lost — the harness proves the second half before the run
pays for the first.
