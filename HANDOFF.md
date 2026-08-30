# HANDOFF — Scraper dedupe + page-classifier gate

## ⚠️ START HERE — which branch / where to work

**The work is on local branch `claude/opportunity-scraper-logic-a509f1`, in a git worktree at
`C:\Users\shama\Documents\wingman\.claude\worktrees\opportunity-scraper-logic-a509f1`.**

- **NOT pushed. NOT on `main`.** The two commits below exist ONLY on this branch/worktree:
  - `2c6a8ae` — the whole dedupe + classifier gate (7 modules + 6 tests)
  - `e7b402f` — MARQUEE M8 prompt sharpening + `classify_queue.py`
- **Work in that worktree directory** (it is the cwd for this session). If you start elsewhere,
  `git checkout claude/opportunity-scraper-logic-a509f1` first, or you will not see any of this.
- `.env` is already copied into the worktree (gitignored). The catalog embedding index
  `catalog_embeddings.jsonl` is built and sits in the worktree (gitignored, ~1509 rows). If you
  ever work from a fresh checkout, both must be recreated (`.env` copied in;
  `python build_catalog_embeddings.py --commit` to rebuild the index, ~$0.03, gated).
- Read `CLAUDE.md` (repo root) and `SCRAPER_IMPROVEMENT_PLAN.md` (the "Session 2026-08-30" section
  at the top is the authoritative design record). This file is the short version.

---

## Goal

The review queue fills faster than the operator can clear it. Build the discovery-side gate that
moves the operator toward **spot-checking**, on two axes, both from ONE page fetch:

1. **Page classifier** — read the full page, label it `program` / `first_party_hub` /
   `third_party_hub` / `none` (+ deterministic staleness). Program → a queued row (pre-labelled);
   hubs → discovery leads (fed to the hub-mining queue); none → flagged; stale → dropped.
2. **Content-embedding dedupe** — catch the SAME program at a DIFFERENT URL (which URL+name miss),
   as a tiered-confidence signal that can eventually AUTO-merge the safe cases.

**Also combines the new-opportunity scraper with `refresh_opportunities`' metadata extraction**
(one page read does both). Deadlines, action items, reviews stay as SEPARATE standalone agents.
`refresh_opportunities.py` is KEPT standalone (not retired). Nothing auto-activates.

## Current progress — BUILT, TESTED, COMMITTED (full unit suite 1755+ green)

Modules (all model/embedding calls injected → hermetic tests):
- `classify_page.py` — classifier + M8 prompt (CTA signal + purpose-based hub/program boundary) +
  deterministic staleness gate (drop program if newest page date ≤ year−3; undated = KEEP).
- `combined_reader.py` — fetch-once orchestrator: classify → (if program) refresh-metadata +
  dedupe hint. Reuses `refresh_opportunities.build_system`/`clean_update_dict` read-only (no M1 edit).
- `embed_common.py` — Gemini `gemini-embedding-001`, pure cosine/nearest (no numpy), jsonl index.
- `build_catalog_embeddings.py` — fields-rep index backfill (RAN: 1509 rows).
- `dedupe_confidence.py` — tiers PROOF/CONFIDENT/ADJUDICATE/SIBLING/HINT/NONE + two guards:
  acronym tie-breaker (shared `(CSSI)` softens CONFLICT→SUBSET) + context guard (CONFIDENT needs
  same domain OR agreeing org).
- `dedupe_eval.py` — measurement harness (`--run` / `--signals` / `--tiers`).
- `dedupe_queue.py` — read-only dedupe dry-run over the live review queue.
- `classify_queue.py` — read-only classifier dry-run over the queue; FEEDS classified hubs into the
  `discovered_leads` hub-mining queue (first_party→same-domain, third_party→off-domain).

## What worked (measured, keep)

- **Fields representation beats page text for dedupe** — cleaner high-cosine band AND needs no
  fetch (index is cheap/robust, covers dead-page rows). `combined_reader.default_representation`.
- **Dedupe is a HINT, not auto-suppress, on a single score** — no clean threshold (same-org
  siblings overlap true dups). But the TIERS make auto-merge safe: eval CONFIDENT **14/14 correct**,
  live queue CONFIDENT **4/4 correct, 0 FP**.
- **The two guards** fixed the two live-queue failure modes (abbreviation miss recovered;
  cross-org generic-name collision blocked) with 0 new FP.
- **Classifier works live** — 30-row pilot ($0.04): pulled out real junk (`none` = a dead "no
  results" page, a wrong 2012 page), unreadables correctly got no verdict (M1), hub pipe fed.
- **M8 prompt fix verified** — "lists SEVERAL programs = never program" was too blunt; replaced
  with a PURPOSE test. Re-pilot: 2 false hubs flipped to program, genuine indexes stayed hubs.

## What didn't work / gotchas (do NOT repeat)

- **`dedupe_queue`/`build_catalog_embeddings` need the GEMINI key, not the Supabase key** — they
  are different creds (fixed; `_gemini_key()`). Supabase read is free; embeddings/classify are paid.
- **Restrict dedupe eval to the ACTIVE catalog** — pending hub-batch rows are full of unresolved
  dupes that pollute the "distinct" label bucket and make separation read worse than it is.
- **The `--tiers` "precision 0.12" number is misleading** — it scores vs an incomplete label set
  (real dupes marked "distinct"). Trust the sorted cosine list, not that aggregate.
- **cp1252 console** — avoid em-dashes/unicode in `print()` strings (crashed other agents; here it
  only mojibake'd). Page-content text you can't control; your own strings use ASCII.
- **Classifier is rate-limited to one call / 5s** (Gemini floor). Full 279-row queue ≈ $1.12 / ~24m.

## Next steps (all gated — each PAID run needs fresh chat approval, the ~$30-overspend rule)

1. **Full-queue classifier triage** — `python classify_queue.py` (no flag) over all 279 pending
   rows (~$1.12, ~24 min): drops junk/stale, feeds every real hub into mining. Drains the backlog.
   (Pilot with `--limit N` first; `--preview` is free.)
2. **Wire `combined_reader` into `scrape_opportunities.py`'s candidate loop** — free to build; the
   discovery gate then runs at scrape time so future rows arrive classified + deduped + hub-routed.
   Grade with `python grade_scraper_batch.py` (0 regressions required).
3. **Decide auto-merge** — flip CONFIDENT/PROOF tiers to actual (reversible, audited P3) auto-merge,
   backed by the 14/14 + 4/4 measurement; re-confirm FP as operator verdicts accumulate.
4. **Console** — surface class/confidence + the dedupe dup-hint/tier in the review queue UI
   (`ops/` — localhost-only; note a concurrent session may edit `ops/*`, stage only your files).

## Key commands

```
python -m pytest tests/unit -q                 # full suite (1755+ green; hermetic)
python dedupe_eval.py --tiers                   # FREE: dedupe tiers over the 90 curated pairs
python dedupe_queue.py --preview                # FREE: queue dedupe cost preview
python classify_queue.py --preview              # FREE: classifier cost preview
python classify_queue.py --limit 30             # PAID pilot (~$0.12): classify 30 queue rows
python discovered_leads.py --list               # the hub-mining work-list (5 hub leads queued)
```

## Marquee (never change without explicit chat yes + a DEDICATED commit that names the entry)

- **M8** — the classifier prompt `classify_page.CLASSIFY_SYSTEM` (and the eval-only
  `dedupe_eval.DESCRIPTOR_SYSTEM`). Any wording change is M8.
- **M9** — the paid calls: one no-search classify call per page, and the embeddings. Toggling
  spend / model pins is M9.
- **M1** — `refresh_opportunities.py` reads the LIVE page, never memory. `combined_reader` only
  CALLS its public helpers read-only; do not edit that file.
