# HANDOFF — Highschool Wingman

## Project
Static vanilla-JS single-page app ("Highschool Wingman" — finds/tracks extracurricular
opportunities for high schoolers) at **`C:\Users\shama\Documents\wingman`**. No build step,
Tailwind via CDN. `CLAUDE.md` exists in the repo root with architecture notes — read it first,
it's kept current. This folder **is** a git repo (`origin` =
`https://github.com/bluefeather78/wingman.git`, branch `main`).

## Goal (current focus)
Migrate wingman's three **offline agent scripts** — `scrape_opportunities.py` (finds new
catalog rows), `check_deadlines.py` (verifies deadline/status freshness), `check_reviews.py`
(verifies org legitimacy/reputation) — off the Anthropic API onto Gemini, after a national-scope
scrape run burned ~$5-30 on client-timeout failures that still billed server-side. Root-cause,
migrate, and empirically validate the new pipeline before trusting it for real batch runs.

**Standing rule established this session, still in force**: never run anything that calls a
paid API (Gemini or Anthropic) without a fresh, explicit per-instance go-ahead from the user in
chat. This was established after an unexpected ~$30 spend and has been honored before every
single test run since — don't relax it without the user explicitly saying so.

## Current Progress / State

### Root cause of the original failure (Anthropic-era)
The national-mode scrape (16 hand-written "seeds" = category + search angle, one Gemini/Claude
call per seed) had 12/16 seeds fail. Root cause: broad national-scope seeds triggered many
searches per call, exceeding the client-side HTTP read timeout — but **a client-side timeout
does not stop server-side billing**, so failed/killed requests were still charged. Also found
(and reported) a data-loss gap: `claude_common.py`'s original `call_claude()` only extracted
`text`-type content blocks, silently discarding `server_tool_use`/`web_search_tool_result`
blocks — meaning the actual search queries run on any past request were never recoverable.

### Provider evaluation: DeepSeek vs. Gemini (assessed both, chose Gemini)
- **DeepSeek disqualified entirely** — confirmed via DeepSeek's own docs that its API has no
  hosted/server-executed search tool at all ("the model itself does not execute specific
  functions"). Would require standing up and hosting an entirely separate search backend.
- **Gemini chosen** — its `googleSearch` tool is architecturally similar to Anthropic's
  `web_search` (model autonomously decides how many searches to run), and tokens are ~4-10x
  cheaper.

### Migration completed
- **New file `gemini_common.py`** replaces `claude_common.py`'s role — deliberately shapes
  `call_gemini()`'s return value identically (`(text, usage)` with the same `usage` dict shape)
  so the three calling scripts only needed import lines / call-site names / env-var names
  swapped, not their logic. `claude_common.py` is left in place, unused, for rollback only.
- All three scripts (`scrape_opportunities.py`, `check_deadlines.py`, `check_reviews.py`)
  migrated: `ANTHROPIC_API_KEY` → `GEMINI_API_KEY`, `call_claude`→`call_gemini`, stale
  docstrings updated.
- Fixed a deprecated-model bug found live: `gemini-2.5-flash` 404'd ("no longer available to
  new users") — corrected to `gemini-3.6-flash` in `gemini_common.py`'s `MODEL` constant, with
  matching pricing constants (`$0.75`/`$3.75` per 1M input/output tokens, `$14`/1000 search
  requests after 5000 free/month).
- **Confirmed via two rounds of direct doc verification**: Gemini's `googleSearch` tool has
  **no server-enforced cap** on search count (no `max_uses` equivalent — the tool schema is
  genuinely just `{}`, and no `maximumRemoteCalls`/similar field exists in `GenerationConfig`
  for server-executed search). `gemini_common.py`'s `max_searches` param is therefore a SOFT,
  prompt-embedded budget only (folded into the system prompt), not a guarantee — documented
  at length in that file's module docstring.

### Major finding this session: "silent skip-search"
Gemini frequently answers entirely from training data and **never actually invokes
`googleSearch`**, with no error/warning distinguishing this from a real live-verified answer —
the only tell is `usage["server_tool_use"]["web_search_requests"] == 0`. Observed empirically
across ~7 live test runs today: real search only fired **once**; every other seed (including a
12-seed batch where ALL 12 showed zero searches) answered from memory. Confirmed this is not a
batching artifact — re-testing a seed in isolation reproduced the same zero-search result.

This is documented at length in `gemini_common.py`'s docstring with a risk assessment:
**LOW risk for `scrape_opportunities.py`** (worst case: a recalled candidate is already a
catalog dupe, or is real but not live-verified and gets human review before `is_active=true`).
**HIGHER risk for `check_deadlines.py`/`check_reviews.py`** — their entire premise is "current
state," and a silent skip could write back a stale/hallucinated status while `last_checked_at`/
`last_reviewed_at` still gets stamped as freshly verified, with no signal anything went wrong.

**Was mid-fix when this handoff was written** (see Next Steps #1 — this is the immediate
unfinished thread): user said "let's strengthen the prompt" to try to force Gemini to actually
search. I had just re-read `SYSTEM_BASE` in `scrape_opportunities.py` (lines 111-139) but had
**not yet made the edit** when the session was interrupted for this handoff.

### Prompt caching — investigated, deliberately NOT implemented
Before running more seeds, checked whether Gemini prompt caching could cut cost. Concluded not
viable: both explicit and implicit caching require ≥4096 tokens (Gemini 3.6 Flash) to engage at
all, but all three scripts' system prompts are only ~520-815 tokens — 5-8x under the minimum.
Also, `scrape_opportunities.py`'s prompt interpolates the per-seed `angle` near the very start
of the string, which would break prefix-based caching even if the token minimum were met. Not
worth the complexity for a low-volume monthly batch job. Do not re-attempt without prompt
lengths changing significantly.

### Telemetry added
Two new `agent_runs` columns — `total_web_searches`, `silent_search_count` — added via a
manual `ALTER TABLE` the user ran in the Supabase SQL editor (confirmed live via query). All
three scripts now compute and log these per run, alongside the existing `items_processed`/
`errors`/`cost_usd`. `check_deadlines.py`/`check_reviews.py`'s `check_one()` now returns
`(info, cost, searches)` instead of `(info, cost)` to support this.

### Bugs found and fixed live
- `scrape_opportunities.py`'s per-candidate loop called `.get()` on list items without checking
  they were dicts — Gemini occasionally returns non-dict entries in its JSON array, which
  crashed the whole seed mid-loop. Fixed with an `isinstance(candidate, dict)` guard before any
  `.get()` call; verified via a live re-run of the seed that had crashed (seed 9 — completed
  cleanly the second time, 8 candidates, 7 new rows, `errors: 0`).

### Live validation status (national mode, 16 seeds)
All 16 national seeds have now been run at least once under Gemini: seeds 0, 1, 3, 4, 9
individually, plus 2, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15 as one batch. All completed without the
original timeout/crash failure mode — that root cause is resolved. 85 total rows written today
under `source='scraper-national-20260818'`, all `is_active=false` pending manual review.

One background-run oddity: a 12-seed batch was launched via Bash and auto-backgrounded (exceeded
the ~120s default foreground timeout). The task tracker later reported status `"killed"`, which
was **misleading** — cross-checking Supabase (`agent_runs` id=8) showed it actually completed
successfully (`finished_at` populated, 36 rows added, cost $0.0532). A second, orphaned
`agent_runs` row (id=9) appeared concurrently with `items_processed: 0`, `finished_at: null`,
`cost_usd: null` — likely a dead/duplicate invocation that never reached a Gemini call (cost is
`null`, not `0`, suggesting it died before any billable request), flagged as a low-confidence,
unresolved oddity rather than a confirmed problem.

**Not yet validated live this session**: `check_deadlines.py` and `check_reviews.py` compile
cleanly and are migrated, but have **not been run even once** since the Gemini migration — only
`scrape_opportunities.py` has real-world test coverage so far. Seattle mode (`SEATTLE_SEEDS`, 8
seeds) has also not been touched under the Gemini migration — only national mode was tested.

## What Worked
- Rigorous, *repeated* doc verification via WebFetch before asserting technical claims (e.g.
  re-confirming Gemini has no hard search cap, re-confirming caching minimums) rather than
  trusting earlier summarized/remembered research — the user explicitly valued this and asked
  for it directly ("verify this rigorously") more than once.
- Minimal-diff migration design (`gemini_common.py` shaped to match `claude_common.py`'s return
  values exactly) kept the actual script diffs small and low-risk.
- Empirical validation via small, cheap, single-seed test runs rather than large committed runs
  — this is how both real bugs (deprecated model, non-dict-candidate crash) were caught cheaply
  before they could recur at scale in a big batch.
- Being transparent about accidental/ambiguous outcomes (the deprecated-model incident, the
  orphaned `agent_runs` row, the misleading "killed" background-task status) rather than
  omitting or downplaying them — consistently the right call with this user.

## What Didn't Work / Pitfalls to avoid
- **Assuming a client-side kill = no server-side cost is false.** A killed/timed-out client
  request can still complete and bill server-side. This was the root cause of the original
  overspend and must stay top-of-mind for any future long-running seed/item loop.
- Inferring which specific seeds failed in a run from category counts alone is imprecise
  (multiple seeds can share a category) — when precision matters, mine the actual run
  transcript/logs instead.
- Letting a Bash call run past its ~120s default foreground timeout without explicitly setting
  `run_in_background=true` causes an automatic background switch with confusing status
  semantics (a `"killed"` status can be misleading). Always cross-check ground truth (e.g. the
  `agent_runs` table) rather than trusting the task-tracker status alone.
- Stalling for a full turn after stating an intent without executing it (happened once, early
  in this session, planning the DeepSeek/Gemini research) — the user caught it directly. State
  intent and execute in the same turn.

## Older, mostly-resolved threads (carried forward — verify before acting)
- **Security incident**: an earlier session found a raw Anthropic API key committed via
  `.claude/settings.local.json` and a GitHub PAT embedded in the git remote URL. Both were
  scrubbed from local git history and the remote URL sanitized. **Never confirmed**: whether the
  user actually rotated the Anthropic key at console.anthropic.com or revoked the GitHub PAT.
  Ask if this ever comes up again.
- **Folder migration**: canonical working folder moved from `Documents/highschool-wingman` to
  `Documents/wingman`. The old folder's contents were deleted, but the empty folder itself could
  not be removed programmatically (Windows reported it busy) — the user needs to manually delete
  `C:\Users\shama\Documents\highschool-wingman` if they haven't already.
- **UI/UX conventions** (from an earlier "wrapping up" polish pass, still valid if UI work comes
  up again): CTA buttons `bg-orange-500 text-slate-900`; `.pop-card`/`.pop-btn` shared card
  language; `font-heading` (Space Grotesk) on all headings; casual/gen-z-coded copy tone (nav
  tabs "Home Base"/"My Vibe"/"Scout"/"Your Grind"). Always grep before assuming any icon's
  wiring state — it has changed hands several times historically.

## Next Steps
1. **Immediate, in-progress**: finish strengthening `SYSTEM_BASE` in `scrape_opportunities.py`
   (~lines 111-139) with an explicit imperative instruction forcing Gemini to actually invoke
   `googleSearch` before answering (e.g. "You MUST call the web search tool at least once before
   answering — do not rely on training data alone"). Then **re-test on a seed that previously
   showed 0 searches** (e.g. index 1 or 4) to confirm the stronger wording actually changes
   behavior — this technique is unvalidated so far.
2. Once validated in `scrape_opportunities.py`, propagate the same prompt-strengthening wording
   to `check_deadlines.py`'s and `check_reviews.py`'s `build_system()` — don't propagate an
   unproven fix to the higher-risk scripts first.
3. `check_deadlines.py` / `check_reviews.py` need their first live test run under Gemini —
   currently zero real-world validation, only compile-checked.
4. Seattle mode (`SEATTLE_SEEDS`) hasn't been tested under Gemini at all — only national mode.
5. If the orphaned-`agent_runs`-row oddity (see above) recurs, investigate more deeply — e.g.
   check whether Gemini's API console has a usage/billing dashboard to cross-check against local
   `cost_usd` tracking, since local tracking clearly can't capture cost from a process killed
   before it finalizes its own row.
6. Once satisfied with data quality, the 85 rows written today (`source='scraper-national-20260818'`,
   all `is_active=false`) still need the manual spot-check + `UPDATE ... SET is_active = true`
   step described in `CLAUDE.md` before they go live.
