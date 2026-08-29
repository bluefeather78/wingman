# Local Ollama for page-analysis — plan & approach

**Status:** proposed, not built. Discussion captured 2026-08-29 for a future session.
**Goal (in the operator's words):** dedup is unreliable and generates too much manual
review; fetching+analyzing pages costs Gemini budget. Move the local, no-search analysis
calls to a local Ollama model to (a) make dedup content-aware and reliable, and (b) trim
the cheap analysis spend.

---

## The core idea

Wherever a model call is **(a) analyzing content we fetched locally, AND (b) needs no web
search, AND (c) runs in the offline agents (not on Render)** — move it from Gemini to a
local Ollama model. All three conditions must hold.

- **Gemini keeps: search.** Every search-driven path stays Gemini — the search is the thing
  that cannot be done locally.
- **Claude keeps: everything it does now** — profile chat, resume/LinkedIn import, the
  interactive `/api/messages*` endpoints. These run in production and can't reach a local
  Ollama anyway.
- **Ollama takes:** hub-mining classify/read, action-items extraction, mailing-list form
  check, and the new content-based dedup adjudication.

---

## Dedup: the key design (block → match)

Dedup today is **URL-based** (`url_dedupe`: only "same normalized URL AND similar name" is
auto-rejected; everything else becomes a human flag). That's what drives the manual-review
load. The fix is **not to replace URL matching** — it's to add a content stage after it:

- **URL match = the blocker.** Keep `url_dedupe` as the cheap first pass that narrows ~1,325
  catalog rows down to a handful of suspects. You cannot content-compare a candidate against
  the whole catalog.
- **Content analysis = the decider.** Fetch the suspect pages and let Ollama (or an
  embedding) decide same-program-or-not — the judgment `url_dedupe` refuses to make and
  currently dumps on the operator as a flag.

**Two invariants to hold:**
1. **Use it to clear false flags, not to auto-merge.** The repo's rule is "a wrong
   reject/merge is worse than a wrong flag" (SMApply portal backs 6 programs; "1-Week" vs
   "3-Week Medical Academy" scores 0.95). Auto-*dismissing* a non-dup is safe;
   auto-*merging* two real programs is silent loss. Let Ollama clear noise and pre-rank the
   true merges; keep the human on the merge itself.
2. **Embeddings may beat a chat call for the match.** "Is this the same program" is a
   similarity judgment — a local embedding (name + canonical page text → cosine) is cheaper,
   deterministic, and testable in a way a generative verdict isn't. Consider
   `nomic-embed-text` for the match, `qwen2.5:7b` only where real reasoning is needed.

---

## Where Ollama helps (and where it doesn't)

**Ollama-able (local, no search, fetch-and-analyze):**
- `mine_hub_pages.py` / `sitemap_hub.py` — hub-mining classify (~$0.001/hub) + read-a-page
  (~$0.00096/page). No search → ~100% Ollama-able. **The best cost target after nothing.**
- `generate_action_items.py` — fetches page via `page_text.py`, one no-search Gemini call to
  extract the checklist. ~$0.0016/row, ~$2/full pass. Clean fetch-and-analyze.
- `find_mailing_lists.py` — model call only to answer "is this form THIS program's list?"
  on a fetched page. Low volume.
- New **content-based dedup adjudication** (see above).
- `find_contact_emails.py` — full-catalog fetch-and-extract backfill (one-time).

**Watch / higher risk:**
- `refresh_opportunities.py` — no-search, so it technically qualifies, but it shapes
  *curated metadata from model knowledge* rather than reading a fetched page. Grade it
  hardest, or leave it on Gemini.

**Cannot move (search is the whole point):** `scrape_opportunities.py` search angles,
name-harvest's per-name search step, `refind_dead_links.py`, `check_reviews.py`,
`check_deadlines.py` (also interactive/production).

**Off-limits (production on Render, can't reach local Ollama):** the interactive
`/api/messages` and `/api/messages-claude` endpoints, the on-demand deadline check.

**No savings (already free, pure code):** `check_links.py`, `url_validate/repair/dedupe`,
`page_text.py`.

---

## Honest cost expectation

This is **not a cost play** — dollar savings are modest and concentrated in agents that
were already cheap (analysis calls are ~$0.001 each). The real payoff is **dedup
reliability / lower manual-review load.**

| Ollama-able | current | after | recurring saving |
|---|---|---|---|
| Hub mining, full drain of 234 queued hubs | ~$1.5–2 | ~$0 | ~$1.5–2 |
| Action items, full catalog pass (~1,300 rows) | ~$2 | ~$0 | ~$2 |
| Name-harvest read gate | pennies | ~$0 | negligible |
| Content dedup | already $0 | $0 | $0 (saves review *time*, not money) |
| Contact-email backfill | ~$1–2 | ~$0 | one-time |

≈ **$4–6 per full sweep** of the Ollama-able agents, plus a one-time couple dollars.

**Unchanged — the actual bills**, all search-fee-bound and untouchable by Ollama:
- `check_deadlines.py` full pass — **~$84** (the real dollar lever; a caching/frequency
  question, not an Ollama one)
- `check_reviews.py` full pass — ~$1.42
- search angles / name-harvest searches — variable
- interactive Claude/Gemini — production

---

## Will a local model match Gemini quality?

Plausibly **yes on these specific tasks** — and it's measurable, not a guess:

- The bar is `gemini-3.5-flash-lite`, a small/fast model — not a frontier model.
- The Ollama-able tasks are **narrow, evidence-in-hand judgments** (is this URL a program
  page? are these two pages the same program? extract the checklist from this text). Small
  models are strong at classify/extract *with the answer on the page*; weak at
  recall-from-memory and open-ended reasoning — none of which these tasks are.
- **Validate with the existing grader.** The learning loop already froze every recorded
  verdict into a test at the bar "zero approved rows lost" (`grade_scraper_batch.py`, seed
  ledger). Point Ollama at those; if a model holds the bar it's good enough by definition,
  and finding out costs $0.
- Keep the **verify-in-code contract**: whatever Ollama says, code still checks it (title
  really names the program, URL really retrieved). We change *who suggests*, never *what is
  trusted*.

---

## Setup (macOS, this machine: 16GB M4)

Ollama is a native server, **not** a pip package in the venv. Install the app system-wide,
pull models, then `pip install ollama` (the thin client) into the venv.

```bash
# 1. Install the Ollama server (system-wide, not in the venv)
brew install ollama

# 2. Run it as a background service
brew services start ollama          # or: ollama serve  (foreground)

# 3. Pull the models
ollama pull qwen2.5:7b              # ~5GB — hub classify, action items, dedup adjudication
ollama pull nomic-embed-text       # ~275MB — embeddings for the dedup blocker→match

# 4. Install the Python client INTO the venv (the only pip part; activate venv first)
pip install ollama

# 5. Sanity check
ollama run qwen2.5:7b "reply with the single word: ok"
```

**16GB memory note:** keep **one** model resident at a time. Running chat + embeddings
back-to-back in the same script makes Ollama swap them in/out of memory (slow). For dedup
that's fine — embeddings are a separate batch step; just don't interleave chat and embed
call-by-call. Sequence the flow as **block → embed (batch) → adjudicate (chat)** to avoid
thrash. On 16GB, 7–8B is the ceiling; a 14B runs but starves the OS + Python + fetches.

---

## Repo constraints to respect

- **M8 (marquee): any prompt sent to a model is protected.** Ollama prompts are still M8 —
  approval first, dedicated commit, concrete-examples house style.
- **M9 (marquee): paid API call paths.** Ollama is free, so M9 doesn't bite — but a
  provider/model swap on a decision path is a big change; treat with care.
- The offline agents are local-only and stdlib-flavored; adding an Ollama client dependency
  is fine for them but must not leak into `app/` (the only thing deployed to Render).

---

## Suggested order of work (when picked up)

1. Setup + `ollama` client smoke test.
2. **Content dedup adjudicator** first — it's the pain the operator led with (review load),
   and it's $0 today so there's no spend risk, only quality to validate. Build as
   block(`url_dedupe`) → embed(`nomic-embed-text`) shortlist → adjudicate(`qwen2.5:7b`),
   clearing false flags only. Grade against frozen verdicts.
3. **Hub-mining classify** on Ollama — biggest clean cost win, no search, gradeable.
4. **Action items** extraction on Ollama — real recurring spend, no search.
5. Mailing-list form check + contact-email backfill as follow-ons.
6. Leave `refresh_opportunities.py` last / optional (quality risk).
