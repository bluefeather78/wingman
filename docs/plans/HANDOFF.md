# HANDOFF — Discovery gate, review-queue surfacing, and console queue-tools

## ⚠️ START HERE — where to work

- **Branch:** `claude/opportunity-scraper-logic-a509f1`
- **Worktree:** `C:\Users\shama\Documents\wingman\.claude\worktrees\opportunity-scraper-logic-a509f1`
- **`.env`** and the embedding index **`catalog_embeddings.jsonl`** (1,676 rows, active catalog) both
  live in this worktree, gitignored. If you ever work from a fresh checkout, recreate both
  (`.env` copied in; `python build_catalog_embeddings.py --commit` to rebuild the index).
- **The dev server on `http://localhost:8000` runs from THIS worktree** (last PID 74508). Restart it
  with `restart_server.ps1` from this worktree — never Bash `&`. Each git worktree has its OWN
  gitignored `discovered_leads.jsonl`, so the console's lead counts follow whichever worktree the
  server runs from (this caused a "leads count dropped" scare — it was just the serving worktree
  changing, not data loss).
- Read `CLAUDE.md` (marquee rules — M8 prompts, M9 paid calls) and `../archive/SCRAPER_IMPROVEMENT_PLAN.md`.
  The **Scraper Logic Map** artifact is the plain-English picture:
  https://claude.ai/code/artifact/5a5c5614-e561-4f28-9f0f-cac1c30ada99 (updated this session; WebFetch
  it before republishing — a republish overwrites, it does not merge).

## Goal

Close the review-queue loop for scraped/mined opportunities: every candidate should arrive
**classified** (program / hub / none / stale), **enriched** (page metadata), and **deduped**
(name/URL **and** content-embedding), land in the review queue **labelled**, and be drainable in a
few clicks — with nothing auto-activated.

## Current progress — DONE, verified, committed

Full unit suite green throughout. Commits on this branch, oldest first:

| commit | pushed? | what |
|---|---|---|
| `01cd554` | ✅ pushed | Console surfacing: classify pills + tier-coloured dedupe back-links + class filter in the review queue |
| `51d8d3a` | ✅ pushed | **MARQUEE M9** — discovery gate wired always-on into `scrape_opportunities.py` (classify + metadata enrich + embedding dedupe per candidate) |
| `e5b029a` | ✅ pushed | `triage_queue.py` — bulk-reject the queue by classifier verdict (FREE, reversible) |
| `67e3596` | ✅ pushed | `--gate-observe` — label-only gate mode (keep everything in the queue for review vs act=drop/divert) |
| `da6910b` | ✅ pushed | **MARQUEE M9** — activation embeds the row into the dedupe index (keeps it current on its own) |
| `7fe2746` | ❌ **UNPUSHED** | Console: gate-mode toggle (Observe/Act) on the scraper card |
| `21c3278` | ❌ **UNPUSHED** | Console: Queue-tools cards (Classify / Dedupe / Triage / Refresh-index) |

**➡️ First action for the next session: `git push origin claude/opportunity-scraper-logic-a509f1`**
to ship `7fe2746` + `21c3278` (the user asked to push; it was the last open loop).

### The new pieces (all live)

- **Discovery gate** (`combined_reader.py` + `classify_page.py` + `dedupe_confidence.py` +
  `embed_common.py`), wired into the scraper's candidate loop. Per candidate: fetch page once →
  classify → (if program) enrich metadata + embedding-dedupe hint. Pure shaping helpers
  (`gate_metadata_overlay`, `gate_dup_candidates` in `scrape_opportunities.py`) are unit-tested.
- **Queue tools** — surfaced in the console via the existing `MAINTENANCE_TOOLS` registry (NOT the
  agent schema, so cost/history invariants are untouched). Under **Run → New Opportunities → Queue
  tools**: `classify_queue.py`, `dedupe_queue.py`, `triage_queue.py`, `build_catalog_embeddings.py`,
  each preview-first. `build_tool_args` has a branch per tool.
- **Gate-mode toggle** on the scraper card — console default is **Observe** (review-first); CLI
  default stays **Act**.

### Runs executed this session (measured)

- Full-queue **classify** (`classify_queue.py --write`): 279 rows, **$0.4631** → 159 program, 38+18
  hub, 20 none, 44 unreadable, 11 stale.
- Full-queue **dedupe** (`dedupe_queue.py --write`): 279 rows, **$0.0036** → 4 confident, 4 adjudicate,
  10 hints.
- **5-angle observe scrape** (seeds 6,10,15,16,22, `--gate-observe`): 94 candidates → **21 rows,
  $0.4552**, all labelled, nothing dropped. Live in the review queue (`source scraper-national-20260830`).
- Embedding index refreshed to **1,676** active rows ($0.0022).

## What worked (keep)

- **Reusing existing seams instead of new machinery**: the console tool cards went through
  `MAINTENANCE_TOOLS`/`build_tool_args`/`run_tool_subprocess` (already there) — no new endpoints, and
  the cards auto-render from the registry via `TOOL_SLOTS`.
- **`quality_flags` / `dup_candidates` as the surfacing channel** — already flow to the review queue,
  so no schema migration was needed to show class pills and dedupe back-links.
- **Observe mode** for a review-first scrape — the operator sees hubs/stale in the queue rather than
  having them dropped/diverted silently.
- **Verifying claims against code + data** before answering (the "leads got replaced" scare was a
  worktree/server artifact; first/third-party hubs ARE both queued — 38 same-domain + 21 off-domain).

## What didn't work / gotchas (do NOT repeat)

- **The auto-mode permission classifier blocks paid script runs** from Bash/PowerShell even with chat
  approval. Background runs sometimes pass; otherwise the **user runs the paid command themselves**.
- **`tee` to a non-existent `agent_logs/`** silently broke a backgrounded run; use `python -u` and the
  task's own `.output` file, no `tee`.
- **cp1252 console**: set `PYTHONIOENCODING=utf-8` for any run that prints program names (en-dashes).
- **Hub mining is a SEPARATE pipeline from the gate.** `mine_hub_pages.py` does NOT run
  `combined_reader`/`classify_page` — it has its own URL-picker + URL-dedupe + `build_row`. So mined
  rows arrive **URL-deduped and field-populated but NOT class-pilled or embedding-deduped**. The
  intended way to finish them is the **queue-tools workflow**, not a miner rewrite (decided with the
  user this session — do NOT rewrite the miner).

## Next steps — the user's active task

The user is **draining the queues from the admin dashboard**, in this order (all console cards,
preview-first):

1. **Mine Hub Pages** (Run → New Opportunities) — leave URL blank, set **"take N from the discovery
   queue"** (59 leads waiting: 38 first-party same-domain, 21 third-party off-domain). PAID.
2. **Classify the Review Queue** → 3. **Dedupe the Review Queue** (tick "only rows already
   classified") → 4. **Triage the Review Queue** (reject hubs/none/stale) → 5. review & activate.

**Open decision the user was about to make:** `classify_queue.py --write` re-classifies (re-pays for)
the WHOLE queue each run. The user was offered a small non-marquee enhancement — an
**"only-unclassified" guard** so the mine→classify loop skips already-stamped rows — and had not yet
said yes/no. Offer it again if they mine repeatedly.

### Deferred / open (not this session's task)

- **Grade the M9 gate** with `grade_scraper_batch.py` (0 regressions) before a large-scale scrape. Paid, user-triggered.
- **Auto-merge** the CONFIDENT/PROOF dedupe tiers (reversible, audited) once FP rate is measured — 4
  confident pairs exist to start the count. Currently label-only by design.
- **Rotate the GitHub PAT** embedded in the `origin` URL (flagged; user's call).
- The concurrent **scraper-v2** session owns `mine_hub_pages.py` on `origin/main`; coordinate before
  editing hub-mining files (merge-conflict risk).

## Marquee reminders

- **M8** — `classify_page.CLASSIFY_SYSTEM` and any model prompt. Wording change = approval + dedicated commit.
- **M9** — paid calls: the gate's per-candidate classify/metadata/embedding, and the activation
  embedding. Toggling spend / model pins = approval + dedicated commit.
- **M1** — `refresh_opportunities.py` reads the live page; `combined_reader` only calls its public
  helpers read-only. Do not edit that file.
