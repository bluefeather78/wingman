# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

"Highschool Wingman" — a static vanilla-JS single-page app that helps high schoolers find and
track extracurricular opportunities (summer programs, internships, research competitions,
academic competitions, conferences, journals). No build step, no framework, no bundler.
Tailwind CSS is loaded via CDN in [index.html](index.html).

## Running the app

```
python server.py
```

Serves the static site and API on `http://localhost:8000`. `server.py` is a Python
stdlib-only (`http.server`) dev server — no dependencies to install, no build/lint/test
tooling exists in this repo.

- `.env` (gitignored) holds `GEMINI_API_KEY` and `ANTHROPIC_API_KEY`. If either is unset,
  that endpoint (`/api/messages` or `/api/messages-claude` respectively) runs in **MOCK
  mode**, fabricating plausible pattern-matched responses (see `generate_mock_text` in
  [server.py](server.py)) so the app is fully click-through-able offline.
- Never pass an API key inline on the command line (e.g.
  `GEMINI_API_KEY=... python server.py`) — it gets recorded in shell history / Claude Code's
  local settings allowlist and has leaked before. Always put it in `.env`.

## Architecture

**Three-file frontend, no modules:** [index.html](index.html) (markup/layout),
[script.js](script.js) (~2500+ lines, all app logic, loaded as one non-module script),
[styles.css](styles.css). Everything is global — functions and state (`let`/`const` at top
level of script.js) are called directly from inline `onclick="..."`/`onsubmit="..."`
attributes in the HTML. When adding a function that's invoked from HTML, it must stay a
plain global function (not wrapped in a module or IIFE).

**Opportunity data**: the opportunity catalog (1200+ rows) lives in a Supabase (hosted
Postgres) `opportunities` table, not a static file — `server.py`'s `/api/opportunities`
proxies to it (PostgREST, anon key, RLS-restricted to `is_active=true` rows, paginated past
PostgREST's 1000-row cap, cached in-process for `OPPORTUNITIES_CACHE_TTL` seconds) and
`script.js` fetches that endpoint into the global `OPPORTUNITIES` array on load.
[opportunities.json](opportunities.json) still exists git-tracked as a diffable backup
snapshot only — regenerate it with `export_json.py` after editing the DB, it is **not**
fetched at runtime anymore. `migrate_to_supabase.py` was the one-off script that populated the
table (from this file plus a sibling `opportunity finder/` project's seed data); not part of
the regular dev loop.

## The six background agents and the admin console

Six offline Python scripts maintain the catalog. **Five of the six cost real money per run**
(Gemini or Anthropic, most with web search). **Never run one of those five without fresh
explicit approval in chat** — this rule exists because of an unplanned ~$30 spend, and
building UI for a run is not authorization to trigger it. `check_links.py` is the exception
and is genuinely free; see its own section below for the one thing it *does* need care about.

| Console key | Script | `agent_runs.agent` | Job | Web search |
|---|---|---|---|---|
| `metadata` | `refresh_opportunities.py` | `metadata_refresher` | Core metadata: name, org, summary, eligibility, pricing | no |
| `reviews` | `check_reviews.py` | `review_checker` | Org legitimacy / reputation from independent sources | Gemini |
| `scraper` | `scrape_opportunities.py` | `scraper` | Find NEW opportunities (only agent that INSERTs) | Gemini |
| `deadline` | `check_deadlines.py` | `deadline_checker` | Deadlines + running/not-running status | Claude |
| `mailinglist` | `find_mailing_lists.py` | `mailing_list_finder` | Find each program's mailing-list signup form, store a replayable recipe | no |
| `links` | `check_links.py` | `link_checker` | Verify every catalog URL; repair what moved, deactivate what is gone | no — **free, plain HTTP** |

Watch out for two things that have caused real bugs here:
- The **scraper's `items_processed` counts SEEDS, not rows** — never sum it with the other
  three agents' row counts. `AGENT_CONFIGS_SCHEMA[key]["unit"]` encodes this.
- `check_reviews.py` is the *review* checker, not the metadata refresher. An earlier console
  card labelled "Refresh Agent" actually executed `check_reviews.py`; the keys in the table
  above are the corrected mapping. Don't rename the `db_agent` literals — the scripts write
  them and there is existing history under each.

**A model-typed URL is not trustworthy anywhere in this repo.** This is the scraper rewrite's
central finding generalised, and as of 2026-08-23 it is enforced in three more places:

- `refresh_opportunities.py` **no longer writes `url` at all.** It calls Gemini with
  `use_web_search=False` and used to write whatever `url` came back onto a live catalog row —
  the exact mechanism behind the scraper's 26% dead-link rate, except overwriting curated
  data rather than creating new. The scraper's fix (take the URL from `groundingChunks`) is
  unavailable without search, so the field is simply not written; `check_links.py` owns link
  health and a replacement URL is a human edit in the console. Its prompt also used to open
  with *"YOU MUST use web_search and web_fetch"* while search was off, which left the model
  no way to comply except to answer from memory in the voice of a lookup. It now says it has
  no web access and that null is the expected answer.
- `check_reviews.py` **takes `review_sources` from the search, not from memory**: phase 2 is
  handed the URLs resolved from phase 1's grounding chunks and told to copy them, and
  `clean_sources()` marks each kept source `retrieved: true/false` and drops any unretrieved
  one an HTTP check finds dead. Measured 2026-08-23: **199 of the 1469 source URLs already in
  the catalog (13.5%) were dead**, and these are shown to students as the evidence behind a
  legitimacy verdict. The grounding half matters more than the HTTP half — **61% of those
  URLs sit on hosts (Reddit 654, College Confidential 158) that never answer a checker at
  all**, so `retrieved: true` is the only real proof obtainable for them.
- Both search-enabled agents are now **two-phase and retry once on a silent search**, and
  both **write nothing if still silent** — see the next section.

**Asking for JSON is what stops an agent searching — all three search agents are now
two-phase.** This is the scraper's design generalised, and it turned out to have a second,
bigger justification than the one it was built on. Measured 2026-08-23 in a controlled A/B
(one row, identical research instructions, arms alternated, only the closing paragraph
differing): **prose 4/4 calls searched, JSON 0/4**, 34 grounding chunks against 0. It matches
the history — `check_reviews.py` had made **22 searches across 3089 row-checks** and
`check_deadlines.py` **59 across 1218**, both on single JSON calls, against the scraper's
prose phase 1 at 5.3 searches/seed.

    Phase 1 (research)  prose out, tools on   -> keeps grounding / retrieved source URLs
    Phase 2 (extract)   notes + REAL urls in  -> strict JSON out, no tools

State the claim carefully — a previous session over-claimed it and had to retract. It is a
large shift in **probability**, not a gate: run id=33 (JSON) fired 6 searches and run id=48
(prose) fired none. `gemini_common.py`'s SEVENTH finding carries both counterexamples. The
THIRD finding still stands: there is no way to *force* a search, only to stop discouraging
one. Do not collapse any of the three back into a single call.

- **`MAX_SEARCHES = 1`** in both new two-phase agents. On Gemini this is a soft budget folded
  into the prompt; on Anthropic it is `max_uses`, enforced server-side. It is the dominant
  cost lever, because the fee is per search, not per token.
- **Claude's `extract_source_urls()` is its `groundingChunks`.** `web_search_tool_result` /
  `web_fetch_tool_result` blocks carry the URLs actually retrieved — already resolved, with
  no redirect hop. Phase 2 gets them for the same reason the scraper's does: so the model
  copies a URL that was really fetched instead of recalling one that merely looks right.
- **`web_fetch` is deliberately NOT capped alongside `web_search`** in `check_deadlines.py`.
  It carries no per-call fee and it is the tool that actually reaches the FAQ/key-dates
  subpages the dates live on; the prompt's whole estimation logic depends on it.

**Silent search: detect and re-roll, never prompt harder.** Both providers still decide per
call whether to search. The mitigation is to re-send the *identical* prompt once — a silent
call pays no per-search fee — and it is in all three agents.

- In `check_reviews.py` a **still-silent call writes nothing and does not stamp
  `last_reviewed_at`**. That column is the staleness filter, so the old behaviour did double
  damage: a memory-derived `insufficient_data` (textually identical to a real search finding
  nothing — the file's own comment said so) *and* a 30-day suppression of any re-check that
  would have corrected it. Skipping leaves the row due, and the next pass re-rolls.
- In `check_deadlines.py` the retry is on **by default including the interactive path**
  (`retry_on_silent=True`). That costs a user one extra round-trip, and it is worth it
  because `server.py` caches a deadline answer for 7 days: one silent, invented set of dates
  is served to every student who opens that opportunity for a week. `check_one()` returns a
  4-tuple `(info, cost, searches, attempts)` — **both** call sites (this script's `main()`
  and `server.py`'s on-demand endpoint) unpack it.
- **A still-silent deadline check writes nothing, in both paths, and that is now load-bearing
  rather than merely cautious.** `check_one()` returns an *empty* info when the search never
  fired, so writing it would blank the row's real `status`/`important_dates` **and** stamp
  `last_checked_at` — destroying good data and then hiding the damage behind the 7-day TTL.
  The interactive endpoint falls back to `cached_deadline_payload(opp, "unverified-fallback")`
  and deliberately does **not** stamp, so the next request re-rolls the search decision
  instead of being served the hole.
- Cost is banked **per attempt** in both, so an exception on the retry cannot discard what
  the first call already spent. Same fix the scraper's two phases needed.
- **Phase 2 is skipped when phase 1 stayed silent** in both agents. Notes written without
  looking are not worth converting, nothing gets written either way, and skipping keeps a
  fully silent row at roughly the old per-row price.

**What the two-phase change costs, measured rather than guessed** (2026-08-23, one row each):

| | before (single JSON call) | after (two-phase, MAX_SEARCHES=1) |
|---|---|---|
| `check_reviews.py` | $0.0014/row, **~0 searches** | **$0.0166/row** → ~$20 per 1226-row pass, ~$4 per staleness tranche |
| `check_deadlines.py` | $0.0010/row when silent | **$0.0676/row** → ~$84 for a full `--all` pass |

Read the deadline row carefully before reacting to $84: the interactive checks that *really
searched* have always cost a **median $0.0790** (36 of them in `deadline_check_log`), so the
two-phase version is **cheaper per verified check** than what the on-demand endpoint was
already paying — `MAX_SEARCHES` capped `web_search` at 1 where it allowed 3. The old
sub-cent `agent_runs` figures (id=14, id=16) are the price of *not looking*, not a cheaper
way of looking, and comparing against them is how this decision gets made wrongly.

`find_mailing_lists.py` is the odd one out on cost, and deliberately so: it fetches each
row's page with `urllib` and finds provider embeds by **regex**, and only calls the model
when a form was actually found — to answer the one question a regex cannot, *is this form
THIS program's list or the host institution's?* Rows with no form resolve for **free**, no
API call at all. Do not "improve" it by handing the whole page to a model: a hallucinated
Mailchimp endpoint is a recipe that fails silently for every student who ever taps the
button, where a regex cannot invent one.

`check_reviews.py` selects rows on **staleness only** (`STALE_AFTER_DAYS = 30`, i.e. never
reviewed or last reviewed >30 days ago), and `--force` ignores that entirely and re-checks
every active row. The threshold was 182 (~6 months) until 2026-08-22; at that value an
ad-hoc run did nothing at all for half a year, so the only way to make the agent act
between passes was `--force` — the whole catalog or nothing. 30 days keeps a plain run
idempotent (rows checked yesterday are not re-paid for) while letting an ad-hoc run pick up
whatever has genuinely aged. Reputation moves on the scale of months, so don't drop it much
further; the console's **Force recheck** checkbox is the escape hatch for "re-check
everything now". A `review_status:
is.null` filter used to sit alongside it, added to protect the August 2026 backlog from
being re-paid for on every resume; it was removed 2026-08-22 once that backlog finished.
Do not put it back. With every active row now carrying a verdict it matched nothing, which
made the staleness threshold dead code, meant the agent could never re-check anything, and
— because the filter sat *outside* the `if not args.force` branch — made `--force` return 0
rows too, the opposite of what it documents. Today's selection is 0 rows (everything was checked in
August). At 30 days the catalog comes due in five batches from **2026-09-17** (825 rows)
through 2026-09-21, rather than all at once; a full 1327-row pass is roughly $1.42 and
~110 minutes at the 5s delay floor.

## Cost accounting — what the numbers do and don't include

Every cost figure in this repo is **estimated locally from token counts** against the price
constants in `gemini_common.py` / `claude_common.py`. Nothing reads provider billing, so
treat totals as a floor. Known blind spots, all of them deliberate and surfaced in the
console's "Estimated vs billed" card rather than hidden:

- **A client-side timeout still bills server-side.** Killed requests complete and are
  charged; the local estimate never sees them. This caused the original ~$30 overspend.
- **Runs that die before their closing PATCH** leave `cost_usd` NULL. The console shows
  those as `unknown`, never as `$0` — five such runs exist in current history.
- **Dry runs ARE logged and ARE counted as spend.** `--dry-run` skips database writes, not
  API calls, so it costs the same as a live run. Runs are marked with a `-dryrun` suffix on
  `agent_runs.mode` (chosen over a new column so no migration was needed); the console
  counts their cost but excludes their would-have-been row counts.
- **Interactive app calls** (`/api/messages`, `/api/messages-claude`) are costed into
  **one rolled-up `agent_runs` row per surface per UTC day** (`interactive_gemini` /
  `interactive_claude`) rather than a row per call. See `record_interactive_cost()`.
- **On-demand deadline checks** write costed rows to their own `deadline_check_log` table;
  `fetch_deadline_check_cost()` folds them into the summary. They are deliberately NOT
  also rolled into `agent_runs` — that would double-count them.
- **Resume / LinkedIn import** (`_extract_profile_from_text`) used to throw its usage block
  away, so those Claude calls appeared in no figure anywhere. It now records into the same
  `interactive_claude` rollup as every other app call.
- **Every Anthropic call in this repo runs on Haiku 4.5** (`claude-haiku-4-5-20251001`),
  pinned in three places that must agree: `server.py`'s `CLAUDE_MODEL`,
  `check_deadlines.py`'s local `CLAUDE_MODEL`, and `claude_common.py`'s `MODEL`. The last
  of those was left on `claude-sonnet-4-6` when the other two moved, which meant the
  model depended on which entry point you came through — the resume/LinkedIn import went
  through `claude_common` and so actually ran on Sonnet while recording its cost under
  `CLAUDE_MODEL`, i.e. `user_costs.model` named a model that had not served the call.
- **`claude_common`'s price constants must track its `MODEL`.** `server.py` imports them to
  cost every `interactive_claude` call, and those run on Haiku — so while that file said
  Sonnet, the constants were Sonnet's $3/$15 against Haiku's actual $1/$5 and interactive
  Claude spend was estimated at **3x** what it cost.
- **`check_deadlines.py` costed its Claude calls with `gemini_common.estimate_cost`** —
  Gemini's $0.75/$3.75 per MTok plus $0.014/search — against Anthropic calls made by its
  own local `call_claude()`. It now imports `estimate_cost` from `claude_common`.
- Both corrections landed 2026-08-22 and are **not applied retroactively**: `agent_runs`,
  `user_costs` and `deadline_check_log` rows written before then carry the old rates.

**Per-user cost attribution** lives in a Supabase `user_costs` table
([user_costs_schema.sql](user_costs_schema.sql) — a one-time manual DDL step in the Supabase
SQL editor; until it is run, `server.py` logs one warning, attribution is off, and the console
shows the setup step instead of an error). Read it as a **breakdown of interactive spend, never
as extra spend**: every dollar in `user_costs` is already counted in the `interactive_*`
`agent_runs` rollups or in `deadline_check_log`. `record_interactive_cost()` computes the cost
once and writes both, so the two can't drift.

- Grain is one row per `(userid, UTC day, surface, feature)` — a rollup, for the same reason
  the interactive daily rows are.
- **Feature is classified server-side from the system prompt** (`classify_feature()` /
  `_FEATURE_SIGNATURES`), reusing the same signatures `generate_mock_text()` matches on, so no
  script.js call site had to change. Adding a new AI feature means adding a signature here too,
  or its spend lands in `other`.
- `user_costs.calls` counts **billed, attributable calls only** — it is not app traffic. It
  excludes mock-mode calls, signed-out calls, cached/mock/stale-fallback deadline checks, and
  calls that errored before returning usage. That last exclusion is the familiar blind spot:
  an errored or timed-out call still bills server-side, so the count is a floor like every
  other figure here. The UI labels it "billed calls" and carries the exclusions in a tooltip.
- Attribution is best-effort: calls with no `userid` (signed-out, pre-login) are not
  attributed. The console reports that residual as **unattributed** with an attribution rate
  rather than distributing it across users.
- Cached/mock/stale-fallback deadline checks are **not** charged to anyone — they make no API
  call. Only the user whose request actually paid to populate the cache is billed for it.
- Spend is broken out by **provider and model**, not just by surface. `user_costs.model`
  stores the exact model id that was billed (`MESSAGES_MODEL` / `CLAUDE_MODEL` /
  `check_deadlines.CLAUDE_MODEL`, imported rather than re-declared so a bump there can't
  leave the breakout naming a stale model). Provider is **derived** from that id by
  `provider_for_model()` — never a stored column, which could drift out of step with
  `model` after one bad write. Do **not** split by `surface` instead: `gemini`→Google and
  `claude`/`deadline_check`→Anthropic line up today only because of the current wiring,
  and the profile chat is already a deliberate Anthropic holdout inside an otherwise
  Gemini app — one feature moving provider would silently make a surface-based split wrong.
  Surface is still reported separately; it answers *where in the app*, not *who is billing*.
- The `model` column arrived after the table did, so **`user_costs_schema.sql` must be
  re-run** (it is idempotent — `add column if not exists` plus a constraint swap). Until
  then the console degrades rather than breaking: reads retry with a narrower select,
  writes retry without the column, `model_ready: false` comes back, and every model reads
  as `(before model tracking)` while the provider split still works off the surface
  fallback in `_SURFACE_PROVIDERS`. Totals are correct in both states.
- The grain constraint **includes `model`** and the column is `not null default ''`, not
  nullable: Postgres treats NULLs as distinct in a unique constraint, so a nullable model
  would make every single call insert a fresh row instead of accumulating into one.
- `GET /api/agents/user-costs?days=&limit=` (localhost-only like the rest of `/api/agents/*`,
  which matters more here — the response carries names, emails and plan status) backs the
  console's **Cost per user** tab: attributed vs unattributed, spend by provider, by model
  (with each model's feature split), by feature, and a per-user table showing cost against
  the $9.99 plan price.
  - The table is seeded from the **`users` table**, not from `user_costs`. Built from the
    cost rows alone it was a spend ledger wearing a roster's name: an account with no
    billed call has no rows at all, so 9 of 15 accounts — every recent signup among them —
    could not appear at any range setting. A trial that costs $0 is not missing data, it
    is the most important thing this page can say. Zero-spend rows carry a **never used
    AI** pill, sort below every spender, and are not expandable.
  - The headline day figure is **`latest_day` — the most recent day with spend — never
    "today"**. Rows are bucketed on the UTC day, which rolls at 5pm Pacific, so a "today"
    figure reads `$0.00` every evening on a day that cost real money, which is
    indistinguishable from the pipeline having stopped. A rolling 24h window was
    considered and rejected: the daily grain cannot say how much of yesterday's single
    rollup row falls inside it, so it would have to estimate. `latest_day` is exact.
  - **The unattributed residual is split in two.** `pre_attribution_cost_usd` is spend
    billed before the first `user_costs` row ever existed (`_attribution_start()`) and
    could never have been attributed to anybody; `signed_out_cost_usd` is the real
    no-userid traffic. Reported as one number the first swamps the second — it read as
    "$2.92 from calls with no signed-in user" when the true signed-out figure was ~$0.06
    — and pinned the rate at 37% until those rows aged out of the window. `attribution_rate`
    is measured against `attributable_total_usd`, so it describes how well attribution
    works rather than how old the window is. Expanding a user row shows their own provider and model breakdown.
  Provider colours in the console are **fixed per provider**, unlike the positional feature
  palette — otherwise a provider overtaking the other swaps colours mid-session and the bar
  reads backwards.
- The tab's body is **one always-visible frame plus three paged cuts of the same money**.
  The frame is the KPI tiles, the per-day attributed-spend chart and the provider split —
  provider is the frame, not a fourth cut. Under it a `.tab` strip (`showUserPanel()`)
  pages between **By model**, **By feature** (each feature carrying the model(s) that
  serve it, since "what costs most" and "what model is it on" are one decision) and
  **Users**. They page rather than stack because reading them as one column meant
  scrolling past two tables to reach the one you wanted. The sort `<select>` is hidden
  unless the Users panel is showing — it means nothing against the other two.
- Rows with a blank `user_costs.model` collapse into a single **"Other"** bucket, in the
  by-model list and inside every feature's model list alike (`_group_untracked_models()` /
  `_group_untracked_feature_models()`, keyed `UNTRACKED_MODEL_KEY`). They are all from one
  ~5-hour window on 2026-08-21, between attribution going live (18:59:39, the first
  `user_costs` row) and `user_costs_schema.sql` being re-run to add the column (before
  00:16:41 on 08-22, the first row carrying one): 13 rows, $0.19, a set that can never
  grow. Only the **model id** is unknown in them — cost, calls, tokens, user, surface and
  feature are exact and fully counted, and the provider still resolves through
  `provider_for_model()`'s surface fallback, which is why the provider split stays complete
  while the model table does not.
  - **Do not backfill them** from the model pins in `server.py`, even though the answer is
    inferable. This column means *what was actually billed*, and the Sonnet/Haiku drift
    above is what happens when a plausible guess is written into it. Unknown is honest.
  - They collapse rather than sit among the real models because a blank id is not a peer
    of `gemini-3.5-flash-lite` — it is an absence, and ranking an absence by cost next to
    real models invites reading it as a third model. The bucket sorts **last regardless of
    cost**, carries a flat neutral colour (never a provider hue), and keeps the providers
    it covers in `providers` for the row tooltip. A footnote under both tables carries
    `UNTRACKED_MODEL_NOTE`. It ages out of a 30-day window on its own around 2026-09-20.
- The **By model** table deliberately has no per-model feature detail row: that cut is the
  By feature panel, which states it properly with the model attached. Repeating it under
  every model doubled each row's height to say the same thing worse.
- The console has **five top-level views** (`.viewtabs` / `showView()` in
  [admin_console.html](admin_console.html)): *Metrics* (the default — see the User metrics
  section below), *Agents* (everything about the five background agents, including the
  dry-run snapshot list), *Review queue* (pending activations), *Mailing lists* (recipe
  review), and *Cost per user*. Note the distinct `.vtab` vs `.tab` styling — `.tab` is the
  inline pill inside a card head (the scraper's National/Seattle switch) and filters one
  table; `.vtab` swaps the whole page. Keeping them visually different is deliberate.
- Two endpoints gained a `userid` so their spend can be attributed:
  `GET /api/opportunities/<id>/deadline?userid=` and `POST /api/extract-from-resume?userid=`
  (query string because one is a GET and the other is multipart). Both routes had to move off
  exact-`self.path` matching to survive the query string — the same trap the `/api/agents/*`
  routes already carry a comment about.

## User metrics — the Metrics view

The console's default view ([admin_console.html](admin_console.html), `#view-metrics`),
backed by `GET /api/agents/metrics?days=&limit=` (`get_user_metrics()` in `server.py`,
localhost-only like every `/api/agents/*` route — and it matters more here than anywhere
else on that router: the payload is a roster of names, emails, plan status and per-account
behaviour for a user base that is largely minors. **Do not add an export button, and do
not expose this route.**)

**Cost per user asks what an account costs; this asks whether it got anywhere and whether
it paid.** Same roster, deliberately separate views — a page computing both computes
neither well. This one never reports spend totals; that decomposition belongs to the other
tab, and two places computing the same dollars is how the two drift.

**The blocker this feature was built around: there is no event log.** Everything the repo
knows about a user is current state (the `users` row and its `data` jsonb) or a cost
rollup. Two obvious substitutes are both wrong, and both were checked:

- **`users.updated_at` is a trap.** Declared `default now()` in
  `migrate_users_to_supabase.py` with **no trigger**, and `update_user_data()` never
  writes it — it equals `created_at` on practically every row. A "last active" metric
  built on it looks plausible and is fiction. (Contrast `opportunities.updated_at`, which
  moves on every write — see below. Same column name, opposite meaning, and **neither**
  means what you would guess.)
- **`opportunities.updated_at` is stamped by an ON-UPDATE TRIGGER, not by the code.**
  Verified 2026-08-23 by PATCHing a column back to its own value and watching the timestamp
  move. An earlier version of this file said it was "stamped explicitly by `server.py`" —
  the explicit writes exist, but they are not what makes it move, so **every** agent write
  moves it and no reader can attribute a change to a particular agent. The scripts that set
  it by hand are harmless but redundant.
  - This became a real bug the moment `check_links.py` landed: a link-health pass writes
    `link_status`/`link_checked_at` to every active row, so `check_refresh_progress.py` —
    which counts `updated_at > cutoff` — reported **"1236/1236 opportunities updated"** with
    the metadata refresher having touched none of them. It now excludes rows whose
    `link_checked_at` also falls in the window and reports its count as a floor.
  - `check_links.py`'s `build_update()` still withholds `updated_at` for a telemetry-only
    write. That is currently a no-op against the trigger, and it is kept deliberately: it
    states the intent, and it is what makes the behaviour correct if the trigger is ever
    dropped. Do not "simplify" it away on the grounds that it changes nothing today.
  - **Do not add a new reader of `opportunities.updated_at` that means "the opportunity's
    content changed."** It cannot mean that. If an agent needs to know when it last touched
    a row, it needs its own column — the way `last_reviewed_at`, `dates_last_checked_at`
    and `link_checked_at` already do.
- **`user_costs` only sees billed AI calls.** A student who opens the app daily and works
  their tracker costs $0 and reads as inactive; in mock mode the signal vanishes entirely.

So the view splits in two, and **the state half needs no migration at all**.

**The activation funnel** (`FUNNEL_STAGES`) is **cumulative and strictly ordered** — an
account counts at stage N only if it satisfies stages 1..N, which is what makes the
step-over-step percentages mean anything. Rules that follow, several of them got wrong on
the first pass:

- Every predicate must be genuinely implied by the ones after it, or the cumulative rule
  silently drops people who are *further* along than the chain can see. That is why
  `ran_search` accepts "has anything in the tracker" as evidence alongside its billed-call
  proxy — the proxy is incomplete, the implication is not. It renders with a **proxy**
  pill for the same reason: mock mode bills nothing, so with no API key the proxy half
  reads zero for everybody.
- **"Came back after signup day" is NOT in the chain**, even though it reads like stage 2.
  Someone can build a profile and track five things the day they join and never return;
  chaining it would score them as a total failure. It sits beside the funnel with the
  other side metrics (rich profile, calendar, mailing list, Google signup, consent gaps).
- **`meaningful_profile` reuses `PROFILE_SUFFICIENT_LENGTH = 20`** from
  [script.js](script.js) — the bar the app itself already gates on and shows students. A
  second definition here would let the console and the app disagree about the same
  student. Move it in both places or in neither.
- **`tracked_1`/`tracked_3` exclude saved-for-later.** `hs-tracker-saved[id] === true`
  means the student explicitly parked it, and `script.js` already refuses to count those
  as actively tracked. Counting them here would inflate the most important number on the
  page.
- Each stage carries `missing_userids`, so clicking a bar names exactly who it lost with
  no second request. At this account count **that is the point of the page**: a percentage
  is a decoration on a fraction until the roster is in the hundreds, so every tile shows
  its raw `n/d` and the funnel names people. Same call the Cost per user tab made when it
  seeded its table from the roster rather than from the cost rows.

**Trial-to-paid conversion's denominator is trials that have actually ENDED**, never all
accounts. With a 3-day trial, dividing by everyone scores every signup from the last 72
hours as a failure before they have had a chance to decide. `beta` grants are excluded
from both halves and reported separately — they never reached the choice. A `canceled` or
`past_due` account carrying a `stripe_subscription_id` counts as **converted**: cancelling
later is churn, not a failure to convert, and folding the two together would make the rate
fall every time a paying customer leaves. Every access gate derives from
`subscription_state()`, never from re-reading the columns, for the reason that function
exists.

**Two migrations gate the time-series half**, and both degrade to a setup notice rather
than an error:

- **[user_activity_schema.sql](user_activity_schema.sql)** → `activity_ready`. Unlocks
  DAU/WAU/MAU, the retention cohorts, and the "came back" side metric.
  `touch_user_activity(userid, surface)` is called from the nine handlers that carry a
  userid (login, data save/load, both AI surfaces, deadline check, subscription status,
  resume import, mailing-list subscribe). It **buffers in memory and a background thread
  flushes every 30s**: this table takes *every* authenticated request, not just billed
  ones, and PostgREST cannot do `SET hits = hits + n` without a stored function, so it is
  read-modify-write either way and batching turns a round trip per request into one per
  user per interval. A process that dies between flushes loses at most one interval of
  counts; **DAU/WAU/retention only need the row to exist**, so only `hits`/`surfaces` are
  ever approximate. `get_user_metrics()` flushes before reading, or the console trails the
  buffer and a quiet day looks like a stalled pipeline. A missing table latches the whole
  path off after one warning; a transient failure deliberately does **not** — the same
  distinction `record_user_cost` carries, for the same reason.
- **[user_metrics_daily_schema.sql](user_metrics_daily_schema.sql)** → `snapshots_ready`.
  Every state metric is computed from the *current* `users` table, and the `data` jsonb
  holds one profile rather than a history of one — so "how many users had a meaningful
  profile on 2026-08-01" is not merely unqueried, it is **unrecoverable**. The snapshot is
  written from the read path (throttled to one write per 5 minutes) rather than on a
  schedule, because there is no scheduler here and computing it twice is how a chart and a
  tile come to disagree. `dau`/`wau`/`mau` are **NULL, not 0**, when activity is not set
  up: a zero is indistinguishable from a genuinely dead day, and these rows can never be
  recomputed. **Run it on day one even though nothing reads it for weeks** — every day it
  is not running is a day permanently missing from every trend line this will ever draw.

Both files end with an **ALTER block** for the same reason `mailing_list_schema.sql` does:
`create table if not exists` is a no-op against a table that already exists in an older
shape, and PostgREST 400s an entire insert on one unknown key — so a single missing column
means *nothing* is ever recorded and the view reads as "nobody used the app" rather than
"every write failed". Add a column to a CREATE there and you must add it to the ALTER too.

Smaller things the view is careful about, each for a reason already documented elsewhere
in this file: the DAU line is **never drawn back past `activity_since`** (a flat zero
through a period nobody was measuring reads as "the app was dead", not "we weren't looking
yet"); a retention cell for a cohort too young to have reached that column shows a dash,
**not 0%**, which would read as churn that never happened; plan-status colours are **fixed
per status, not positional**, exactly like the provider colours; and `money()` is
deliberately not used for MRR, because its magnitude-keyed precision renders `$0.0000`,
which looks like a rounding artefact rather than nobody paying yet.

See [USER_METRICS_PLAN.md](USER_METRICS_PLAN.md) for the design rationale and the one
phase still unbuilt (event-level funnel timing — *how long* from signup to first tracked
opportunity, which needs real events and is not worth it at this account count).

**Pulling real billed spend:** Anthropic exposes `GET /v1/organizations/cost_report`, which
needs an **Admin key** (`sk-ant-admin…`, set as `ANTHROPIC_ADMIN_KEY` in `.env`) or an
`org:admin` OAuth token — a regular `sk-ant-api` key returns 401, and the Admin API is
unavailable to individual (non-organization) accounts. Amounts come back in **cents** as
decimal strings. Google has **no equivalent**: the Gemini API exposes no billing endpoint at
all, and the Cloud Billing API can't be authenticated with an AI Studio key — so Gemini gets
a dashboard link instead of a live figure. See `fetch_anthropic_billed_cost()`.

**Three run tiers**, and only one of them is free:
- `--preview` — resolves which rows/seeds would be processed, prints a `PREVIEW_JSON:` line,
  exits before the first API call. **Zero cost, zero writes.** Shared plumbing lives in
  `agent_common.py`; `server.py`'s `preview_agent()` parses that line and pairs the count with
  a per-item cost averaged from that agent's real `agent_runs` history.
  - That average takes **successful runs only, and never `mode = "snapshot-commit"`**.
    Both exclusions matter and both were missing until 2026-08-22: a *failed* run counted
    every row it touched but errored out of most of them before paying for them, so it
    lands in the sample as an implausibly cheap per-item rate, and a snapshot commit
    carries real item counts against `cost_usd = 0` **by construction** (the dry run that
    produced the file already paid). With 6 of `review_checker`'s 10 qualifying runs being
    failures and a 7th a commit, the estimate came out at $0.000528/row against the
    ~$0.00091 its clean runs measure — i.e. the one feature whose whole job is to price a
    run before you authorise it was under-quoting by about half. The old $0.70 full-pass
    figure quoted above for `check_reviews.py` was this same skewed number; it is $1.42 now.
  - `est_cost_low_usd`/`est_cost_high_usd` carry the **spread** across the sample, and
    `provisional` is set below three clean runs. A lone mean reads as a precision the
    number does not have — the scraper's own history spans three orders of magnitude per
    seed depending on how much web search a run triggered.
- `--dry-run` — **still calls the paid API at full cost**; only skips DB writes and dumps a
  local JSON snapshot instead. That snapshot can be **committed** later from the console
  rather than paying to re-run the agent live — see below.
- neither flag — full run, writes to Supabase.

**Timing** is configurable per run via `--min-delay` and `--timeout` on every agent (three
layers: module default → env var → flag). The 5-second inter-call delay is what fixed this
pipeline's repeated HTTP 429s — treat it as a floor. It also dominates wall time: ~1330 rows at
5s is ~110 minutes regardless of API speed. `gemini_common` and `claude_common` each expose
`set_min_delay()`/`set_default_timeout()`. Note `check_deadlines.py` has its **own** local
`call_claude()` (not `claude_common`'s) and its own delay knob defaulting to 0, because
`check_one()` is shared with server.py's interactive on-demand deadline endpoint, where a
process-wide delay would make one user's request block on another's; batch mode raises it to 5.

**Committing a dry-run snapshot** ([dryrun_common.py](dryrun_common.py)) replays a snapshot's
withheld writes into Supabase. It makes **no API calls and costs nothing** — the money was
already spent by the run that produced the file. `GET /api/agents/snapshots` lists what is on
disk; `POST /api/agents/snapshots/commit` with `preview: true` resolves the real post-dedupe
counts without writing, and without it applies them.

- `_patch_updates()` mirrors each agent's live (non-dry) PATCH column-for-column. **If an
  agent's live write changes, change this too**, or a committed snapshot writes a different
  shape than a live run of the same agent.
- The **scraper's snapshot is written on live runs too**, not only dry runs, so it can name
  rows that already exist. Inserts are deduped by normalized URL against the *whole* table
  (active and inactive), so committing the same file twice inserts nothing the second time.
  The dedupe set paginates past PostgREST's 1000-row cap — a single unpaginated request
  silently truncates it and lets duplicates through.
- Committed rows always land `is_active = false`. A commit never activates anything.
- A real commit writes an `agent_runs` row with `mode = "snapshot-commit"` and `cost_usd = 0`.
  That pairs with the `-dryrun` row the original run wrote: the dry run carries the cost and
  no row counts, the commit carries the row counts and no cost, so neither is double-counted.
- **Snapshot filenames are stamped `YYYYMMDD-HHMMSS`** by `agent_common.snapshot_stamp()`,
  shared by all four agents. Seconds, not just the date: they were date-only until
  2026-08-22, so a second run on the same day silently overwrote the first one's file —
  and a `--dry-run` has already paid the API in full by the time it writes, so that
  destroyed work that cost real money. It also made this list a lie, since the file said
  `20260819` with no way to tell which of that day's runs it held.
  - Two shapes exist on disk and **both are read**. Files written before 2026-08-22 keep
    their date-only names and stay committable; `dryrun_common._run_date()` resolves those
    to midnight, exactly what the old code returned, so their staleness and ordering are
    unchanged. The console only prints a time for filenames that actually carry one — a
    date-only snapshot showing `00:00` would read as a real run time.
  - The stamp is **local time** (matching `agent_logs/<agent>_<stamp>.log`) but is parsed
    and served as UTC so every snapshot stays comparable; the console reads it back with
    `getUTC*` so the digits it prints match the digits in the filename.
  - The scraper's `source` value stays **date-only** (`scraper-national-20260820`) — a whole
    day's scrape groups under one source, and existing rows carry those values.
  - Snapshots are still sorted by full instant, not by date: same-day files would otherwise
    order by filename, i.e. alphabetically by agent rather than newest-first.
  - A snapshot still does not correspond to a particular `agent_runs` row — nothing links
    them — but two same-day runs no longer collapse into one file.

## Link health — `check_links.py`

Fixing the scraper's fabricated URLs fixed only NEW rows. The catalog they join had never
been checked. Measured over all 1374 active rows on 2026-08-23: **1029 live, 137 dead
(10.0%), 208 unverified.** One row in ten sent a student to a page that is not there, and
they were real programs with rotted links (`smysp.stanford.edu`,
`jkcf.org/our-programs/young-artist-award/`, `training.nih.gov/.../aip_hs/`), not junk.

**Only evidence of absence deactivates a row.** This is the whole design, and the numbers
force it:

- **deactivate** — 404, 410, a malformed URL, or a hostname that does not resolve.
- **flag only, row stays live** — 403, 429, TLS failures, timeouts, connection resets.

403 alone is ~9% of this catalog (112 rows) and TLS failures another 41. Those sites are
refusing *our* client — a student's browser carries a different root store and loads them
fine — so reading "the connection failed" as "the page is gone" would have pulled ~150
working opportunities out of the catalog on the first run. `url_validate._is_dns_failure()`
is what separates a genuine NXDOMAIN (8 rows, all retired university subdomains) from the
41 TLS/timeout failures wearing the same `URLError` class; **do not collapse them**.

- **Two passes, always.** Anything that looks dead is re-checked before a write. Free, and
  it is the only thing between a CDN hiccup and a deactivated row. Measured: 135 of 137 were
  unchanged on the second pass and 2 rows moved *into* dead, so it corrects both ways.
- **Repair before condemning** — [url_repair.py](url_repair.py), free, on by default
  (`--no-repair` opts out). Programs get reorganised far more often than they are cancelled:
  of the 30 dead rows in the 08-23 audit, 9 were re-found on the same site and 9 of 9 came
  back live. See the next section — the accuracy bar there is the whole feature.
- A deactivated row goes to `is_active = false` + `moderation_status = 'pending_review'`
  with a `quality_flags` entry naming the code. **`reviewed_by`/`reviewed_at` are left
  alone** — most of these were approved by a person once, and the queue saying "approved
  08-23, link has died since" is a different situation from a row nobody has ever seen.
- **It never rejects.** A rotted link is not a verdict on the program.

## URL repair — `url_repair.py`, and the one place `is_active = true` is written by code

**Proposing a replacement URL is cheap and worthless on its own; accepting one is the
feature.** Measured over the 148 rows the first pass deactivated, taking the best-scoring
link on the parent page "repaired" **72 (49%)** — and a large share pointed at a *different
program at the same institution*: `ll.mit.edu/outreach/summer-high-school-internships` →
`middle-school-stem-program`, NIH's `aip_hs` → `pb/sip` (AIP ≠ SIP),
`medschool.vanderbilt.edu/imsd/...` → `/md/`. **A wrong repair is worse than no repair**: it
is a live link, so every other check passes it, and it silently sends a student somewhere the
row does not promise.

So nothing is accepted on similarity. **Three independent tests must all pass**, and each one
exists because of a specific measured failure:

1. **Title proof.** Fetch the candidate; every distinctive word of the program's name must
   appear in its `<title>`. A similarity ratio was tried and rejected: at ≥ 0.72 it accepted
   "Bay Area Entrepreneurship" → "BootCamp Entrepreneurship" (0.76), "Summer Research
   Immersion" → "First-year Research Immersion", "VEX Robotics Competition" → "RECF Robotics
   Competition", and a UC Berkeley course → the same provider's Yale one. The shared word is
   always the *category* and the differing word the *identity* — backwards for a ratio.
2. **The name must be its own.** Distinctive words are the name's **minus the org's**, so a
   match on the institution cannot stand in for a match on the program. Without it,
   "University of Notre Dame" verified against every page on `nd.edu`, "Jackson Laboratory
   Summer Student Program" against "Careers at The Jackson Laboratory", "Doodle for Google"
   against "Google Doodles". Fewer than two words left → unverifiable, leave it alone.
3. **No lost identity word.** If the OLD url used a word to identify this program and the new
   url and its title both lack it, we landed on a sibling. This is what catches a row whose
   **name and org are swapped in the catalog** (`name='University of Notre Dame'`,
   `org='Global Scholars Program'`, old slug `global-scholars` → `summer-scholars`), which
   passes tests 1 and 2 and is still wrong.

End to end on the same 148: **72 → 34 → 18 → 13 accepted**, and the 13 are right. Losing 59
proposals to keep the 13 honest is the intended trade — the unrepaired rows are not lost,
they keep their dead-link flag plus, where a candidate was found, an explicit
`possible replacement found but NOT verified` suggestion (47 rows got one), which is strictly
more than a reviewer had before.

**`--repair-flagged` is the only code path in this repo that sets `is_active = true`,** and
that is not the "never auto-activate" rule being bent. That rule protects rows **no person
has ever vetted** — a scraper's guess. These rows were in the live catalog because a person
put them there; a machine removed them over a link, and the same machine has now proven the
link. Restoring puts back what the automated check took out. It is bounded to rows carrying
**this agent's own `dead link (` flag**, so it can never touch a row a person rejected or one
that was never active, and each restored row keeps a flag naming its **old URL** so the edit
is auditable and hand-reversible. A row whose original URL simply comes back to life on its
own is still *not* restored — that is a person's call in the console.

Ran 2026-08-23 (`agent_runs` id=55): 148 flagged rows, **13 restored**, catalog 1226 → 1239
active, queue 268 → 255, 47 rows gained a suggestion. $0.00.
- **Two url_validate checks were tried here and rejected on measured noise**, and the
  reasoning is worth not re-deriving: `is_bare_domain()` fires on 16% of live rows and they
  are *correct* (`jshs.org`, `precollege.wisc.edu` — dedicated program sites whose homepage
  IS the program page), and `domain_matches_org()` on 9%, roughly one in seven of them real
  (the rest are university domain abbreviations no rule derives — `umd.edu`, `tamu.edu`,
  `gatech.edu`). Both earn their place in `scrape_opportunities.py`, where a fresh candidate
  has the opposite base rate. What replaced them is `FLAG_SOFT_404` — a deep link that
  redirects to a bare homepage, i.e. the program page deleted behind a 200. It fires on 10
  rows (1.0%) at about one-in-two precision. Ten rows at one-in-two beats eighty-eight at
  one-in-seven.
- **[link_health_schema.sql](link_health_schema.sql)** — **RUN 2026-08-23.** All four
  columns (`link_status`, `link_status_code`, `link_checked_at`, `link_dead_since`) are live
  and every active row is now recorded. It keeps the same ALTER block for the same reason as
  `mailing_list_schema.sql`. Until it ran the agent still worked and still deactivated — it drops those columns from its writes and loses only
  the 7-day staleness filter, so every run re-checks everything. That is free, so it
  degrades to *slower*, not *broken*. `link_dead_since` is not derivable from
  `link_checked_at` (which is stamped every pass): without it, a link broken in March and
  one broken this morning look identical.
- **Known gap:** a flag on a row that stays ACTIVE is written to `quality_flags` but has
  nowhere to show, because the console's Review queue lists `is_active = false` rows only.
  The run report in `agent_logs/link_check_<stamp>.json` is the only place to read those;
  the run summary says so rather than leaving it a mystery.
- `AGENT_CONFIGS_SCHEMA["links"]["free"] = True` is read by `estimate_agent_cost()` and by
  the console. A free agent's `$0.00` is a fact about its design, not a "no history yet"
  fallback, and the two must not render alike — nor may its confirm dialog say the run
  "spends real money", which is how a warning becomes something people click past. Its real
  warning is unrelated to cost and is stated in its own words: this run takes rows away from
  students.

**Activating scraped opportunities** — `GET /api/agents/pending` lists `is_active = false`
rows and `POST /api/agents/pending/activate` (`{ids, active}`) flips them, backing the
console's **Review queue** tab. Nothing in this repo ever sets `is_active = true`
automatically; a scrape always writes inactive and stays that way until a person activates it
here, because the scraper does return plausible-looking rows that are wrong and the catalog is
what students see. Activation takes an explicit id list — there is deliberately no
"activate everything matching" path. `active: false` reverses a mistake without a DB console.
Both paths bust `_opportunities_cache`, or the operator activates a row and then cannot find
it in the app for `OPPORTUNITIES_CACHE_TTL` seconds.

**Rejecting a queued row** — `POST /api/agents/pending/moderate` (`{ids, status}`) writes
`moderation_status` + `reviewed_by`/`reviewed_at`, backing the queue's **Queue / Rejected**
pills. It is deliberately *not* the same call as activate: `is_active` alone cannot say "a
human looked and declined", so without it a junk row sat inactive forever and was re-triaged
every time the queue was opened — the queue only ever grew.

- **Rejecting never deletes.** The row stays in the table so its URL keeps blocking
  re-submission through `url_dedupe`, and moderating it back to `pending_review` undoes the
  decision from the Rejected tab. Deleting is destructive and is still not offered anywhere.
- Rejecting/duplicating also forces `is_active = false`. **Approving does not activate** —
  that stays the separate, explicit Activate button. Activating *does* stamp
  `moderation_status = "approved"`, or an activated row keeps a NULL status and comes back
  round the queue.
- `GET /api/agents/pending?status=` is `queue` (default) / `rejected` / `all`. The queue
  filter must spell out the NULL case (`or=(moderation_status.is.null,…in.(…))`): every
  scraper row has a NULL `moderation_status`, and `NULL NOT IN (…)` is NULL in SQL, so a
  plain `not.in` would empty the queue outright.
- The moderation endpoints degrade if
  [user_submissions_schema.sql](user_submissions_schema.sql) has not been run — the list
  falls back to the base select (`moderation_ready: false`, the console hides Reject and
  shows the setup line), and activate drops the approve stamp rather than the whole write.

**Marking a row a duplicate** — the `duplicate` status is its own per-row **Duplicate**
modal, not a variant of the Reject button, because it is the one verdict that needs a
*target*.

- **`duplicate_of` is required for `duplicate` and refused for every other status.** A
  duplicate with no survivor is a rejection wearing a misleading label, and there is then no
  row for a reader to follow to.
- The target is checked: it must exist, must not be the row itself, and **must not itself be
  `rejected`/`duplicate`** — a chain the queue cannot follow, which quietly loses the real
  survivor.
- `duplicate_of` is **always written, never merely set**: restoring or rejecting a row that
  was previously a duplicate clears it, or it keeps naming a survivor for a relationship
  that no longer exists.
- `GET /api/agents/opportunities/search?q=&limit=` finds the survivor. It searches **active
  and inactive rows alike** — the survivor of two queued scrapes is itself still queued —
  by id (exact, tried first) then `name`/`org`/`url` ilike. Commas, parens, `*` and `%` are
  stripped from the term: PostgREST's `or=()` list is comma-separated and paren-delimited,
  so those would parse as syntax rather than as text.
- The modal offers each row's stored `dup_candidates` first, with the reason and confidence
  `url_dedupe` recorded at submission time — that is the whole reason the column is stored.
- **The queue itself also renders them inline** (`dupeBackLinks()`), one line per candidate:
  confidence, the suspected row's name as a link to its own page, its id, and the reason.
  Before that the queue showed only a `2 possible duplicates` count, so finding out *what* a
  row might duplicate meant opening the modal on every row in turn. Confidence drives the
  colour (strong = danger, weak = warn) because the two mean genuinely different things here —
  a strong match is usually a real duplicate and a weak one usually is not, and `url_dedupe`
  emits far more weak ones by design.
- `list_pending_opportunities` returns a `duplicate_targets` map (one extra request, only
  when something is actually marked duplicate) so the queue can name the survivor instead of
  showing a bare id.

**Editing a queued row** — `POST /api/agents/pending/update` `{id, fields}` fixes a row
before anyone activates it, backing the queue's per-row **Edit** modal.

- **Only `is_active = false` rows.** The endpoint reads the row first and refuses a live one.
  Without that guard it is a general catalog editor reachable by id, with no confirmation and
  no audit trail — not what the Activate/Reject buttons around it lead an operator to expect.
- `EDITABLE_OPPORTUNITY_FIELDS` is a **whitelist**, and an unknown key is refused *by name*
  rather than dropped (PostgREST would 400 the whole PATCH on it anyway). Not editable:
  `is_active`/`moderation_status` (those are the buttons), `id`/`source`/`created_at`
  (provenance), `review_*` (`check_reviews.py` owns them), and
  `status`/`important_dates`/`was_estimated`/`dates_last_checked_at` (`check_deadlines.py`
  owns them — a hand-typed date would be overwritten by the next check).
- `type` is validated against `OPPORTUNITY_TYPES`; a typo there makes the row invisible to
  the finder's `KIND_CONFIG` lookup rather than merely ugly. `category` is deliberately *not*
  validated — it is legacy free text holding `COMPETITION`, `SUMMER_PROGRAM`, mixed case.
- `_BASE_PENDING_SELECT` carries every editable column so the modal prefills from the list
  the console already has. **Add a field to the whitelist and it must go in that select too**,
  or the modal opens with it blank and saving writes the blank.
- The client sends **only changed fields**; a no-op save would still bump `updated_at` and
  make the row look freshly touched everywhere else.

**User-submitted opportunities** — the Quest Log's "Add Opportunity" form posts to
`/api/user-submitted-opportunities`, which writes an `is_active = false`,
`source = "user-submitted"` row into the same review queue the scraper feeds.
Matching lives in **[url_dedupe.py](url_dedupe.py)**, kept separate from the `normalize_url()`
that `scrape_opportunities.py` / `dryrun_common.py` / `migrate_to_supabase.py` each carry —
those three are deliberately identical to each other and are **not** to be changed to match
this one.

The governing rule, and the catalog measurements that force it:

- **Only "same normalized URL *and* similar name" is ever auto-rejected.** Not URL alone:
  `spicestanford.smapply.io` is the application portal for **six** distinct programs
  (Stanford E-Japan, Sejong Korea Scholars, …) and Girls Who Code's immersion URL backs two.
  Rejecting on URL alone makes those permanently unsubmittable. URL matches but name differs
  → insert with a **strong** flag instead. A wrongly-flagged row costs a reviewer seconds; a
  wrongly-rejected one is lost silently.
- **Never auto-reject on shared domain.** 969 of 1330 rows (73%) sit on a domain shared with
  a *different* opportunity — `nyu.edu` alone hosts 36. Bare "same site" is only emitted as a
  hint when the domain has ≤ `SAME_SITE_MAX_PEERS` existing rows, or it buries the reviewer.
- **Never auto-reject on name similarity.** The scraper used to, at `DEDUP_RATIO = 0.85`.
  Measured against the current catalog that threshold matches **264 pairs, 257 of which have
  different URLs** and are genuinely distinct opportunities — `'Summer Internship'` collides
  with everything, and `'1-Week Medical Academy'` vs `'3-Week Medical Academy'` scores 0.95.
  It was suppressing real opportunities silently and unlogged. `scrape_opportunities.py` now
  calls `find_duplicates()` here like everything else; do not reintroduce a private rule.
- **The stored `url` is never normalized.** 100 catalog rows have case-sensitive paths
  (`…/CNIX.html`) that 404 once folded. Normalization happens only in a throwaway
  `match_key()`. The bug this replaces lowercased the *needle* and compared it with PostgREST
  `eq.` against un-normalized stored values, so dedupe silently failed for the ~44% of rows
  holding an uppercase character or trailing slash — visible in the catalog today as
  `'Clinical Summer Internship'` twice and JSHS three times under three name variants.
- Tracking params (`utm_*`, `fbclid`, …) are stripped but **all other query params are
  preserved** — sites keying programs off `?id=` would otherwise collapse into one row.
- The dedupe read paginates past PostgREST's 1000-row cap (the table is ~1440 rows including
  inactive ones), and if the catalog can't be read it **refuses to insert** rather than
  inserting blind.
- **`apply_url`, `apply_label`, `meta`, `requirements`, `description` and `deadline` are NOT
  columns on `opportunities`.** PostgREST rejects an entire insert on one unknown key, so an
  earlier row builder that set them meant *no user submission ever wrote a row* — the empty
  review queue read as "nobody submitted", not "every insert 400'd". Selecting a nonexistent
  column 400s reads the same way. Confirm a column exists before adding it here; the AI
  extraction returns a wider shape than the catalog stores, and the surplus goes in
  `submission_payload`.

Review-queue columns (`moderation_status`, `submitted_by`, `dup_candidates`, `quality_flags`,
…) come from **[user_submissions_schema.sql](user_submissions_schema.sql)** — another one-time
manual DDL step in the Supabase SQL editor. Until it runs the insert is retried with the base
columns only and logs one warning naming the file; submissions still land inactive.
`moderation_status` is **separate from `is_active`** on purpose: the boolean alone cannot say
"a human looked at this and said no", so a rejected row would sit at `is_active = false`
forever and be re-triaged every time the queue is opened. Do **not** name it `state` (that is
the 2-letter US state code) or `review_status` (that is `check_reviews.py`'s org-legitimacy
verdict, already shown to students). The normalized-URL index there is deliberately **not
unique**, for the shared-portal reason above.

**Mailing-list signup** lets a student join one program's mailing list with one tap. It is
split into two halves that are deliberately far apart in trust, and the split *is* the
accuracy design:

- **Discovery** (`find_mailing_lists.py`) writes one **recipe** per opportunity into
  `opportunity_signups` — how to POST a signup for that program — always at
  `status = 'pending_review'`. It cannot verify its own work.
- **Execution** (`subscribe_user_to_list()` in server.py, `POST
  /api/opportunities/<id>/subscribe`) replays a recipe for one real user and **refuses
  anything not promoted to `verified`** by a person in the console's *Mailing lists* tab.
  No AI, no cost, fully replayable. Same shape as `is_active` on scraped rows, for the
  same reason — except here a wrong answer lands in a student's inbox.

- **The success state is `submitted`, never `subscribed`** — in
  [mailing_list_common.py](mailing_list_common.py), in the `state` column, and in the
  button label. Every supported provider double opt-ins, and we sign the student up with
  **their own address** (there is no wingman-owned relay to read), so nothing in this repo
  can observe the confirmation link being clicked. Claiming otherwise is the exact silent
  failure the feature is measured against; do not tidy the wording up.
- **Four providers only**: Mailchimp, Substack, Kit/ConvertKit, MailerLite — the ones with
  a documented, key-free public endpoint that answers with a readable success/failure.
  Deliberately excluded: **beehiiv** (its embed carries a bot-check token), **bare HTML
  forms** (no success signal; the failure mode is a 200 that did nothing), **portals**
  (that is account creation) and **anything CAPTCHA-guarded**. Those all degrade to the
  "Open signup page" handoff, which is the honest answer. `mailto:` "email us to join"
  programs were considered and dropped — they need a sending identity we do not have.
- **`scope_evidence` is the field that matters** on a recipe: the quote proving the form
  belongs to *this program*. 73% of the catalog shares a domain with a different
  opportunity and one SMApply portal backs six programs, so "a newsletter form exists on
  this page" proves nothing. The console shows it as a wide column and confidence as a
  narrow one on purpose — confidence is the model grading its own work, which is the thing
  being checked. There is deliberately **no** "verify everything above confidence X".
- **Consent is re-checked server-side**, like the signup checkboxes in `handle_register`:
  a per-list tap, an explicitly confirmed email address, and a ticked box. There is no
  bulk "subscribe me to all results" path and there must not be one — most users here are
  minors, and bulk sending is also how the outbound address gets blacklisted.
  `legal/terms.md` §14A and `legal/privacy.md` §6A cover it (re-run `build_legal.py`);
  `TERMS_VERSION` moved to `2026-08-22` for it.
- The email is **prefilled from the account but editable**. A Google signup often carries a
  school address that blocks outside mail, and making the address an explicit choice is
  better consent hygiene than silently using whatever is on file. The address actually used
  is stored per attempt — "which address did you sign up with" is the first question when a
  student says the mail never arrived.
- Attempts are one row per `(userid, opportunity_id)` in `mailing_list_subscriptions`, and
  a repeat tap **updates** it. The honest question is "did this student sign up", not "how
  many times did they press the button". A per-user throttle of 10/hour sits on top — not
  for our cost (a subscribe is free) but so a stuck button does not look like an attack to
  someone else's mail provider.
- If the attempt cannot be recorded the response carries **`recorded: false`**: the signup
  went out, but the button will reset and the Quest Log cannot list it. Surfaced rather
  than hidden, because it reads as a mystery otherwise.
- **[mailing_list_schema.sql](mailing_list_schema.sql)** is another one-time manual DDL
  step. Unlike the other schema files it ends with an **ALTER block**, because
  `create table if not exists` is a no-op against a table that already exists in an older
  shape — and PostgREST 400s an entire insert on one unknown key, so a single missing
  column means the finder writes *nothing* and the queue reads as "the agent found
  nothing" rather than "every insert failed". **Add a column to a CREATE there and you
  must add it to the ALTER block too.** Until the file is (re-)run, discovery aborts
  naming it, the console tab shows the setup step, and every opportunity degrades to the
  handoff — which is the correct behaviour with no verified recipe.
- **[grade_mailing_lists.py](grade_mailing_lists.py)** is the measuring instrument, and is
  free. `--sample` picks a deterministic, deliberately adversarial 10-row stratified
  sample (it includes shared-portal rows on purpose); `--worksheet` turns the finder's
  output into a form a human fills in by opening each page; `--score` computes
  **precision** (correct / proposed) and **recall** (correct / lists a human found).
  Recall can only be measured by a person, because the finder cannot report the lists it
  missed. `--verify` additionally POSTs each recipe for real using
  `MAILING_LIST_TEST_EMAIL`, which measures execution separately from discovery. There is
  **no confirmed-delivery metric and there cannot be one** under the current design — do
  not add a fourth number that quietly assumes it.

**The console's spend chart draws every key in the series, not the five with cards.**
`get_agents_summary()` returns `series_keys` (each labelled and grouped `agent`/`app`/
`other`) and folds on-demand deadline spend in via `fetch_deadline_check_cost_by_day()`.
Iterating the card list instead dropped the interactive rollups, the deadline checks (which
are in `deadline_check_log`, not `agent_runs`, so they were never in the series at all) and
any agent run from a script with no card — $4.84 of a $14.24 KPI printed ten pixels above
the bars.

The series is banded, not one key per `agent_runs.agent` literal. Each of the five console
agents keeps its own band because each is something an operator deliberately runs.
Everything else collapses into two, because splitting them answered a question nobody asks
of this chart:
- **`end_user` — "End User Initiated"**: both `interactive_*` rollups plus on-demand
  deadline checks. Spend the users caused, which nobody starts from this console. The
  per-user decomposition of exactly this money is the Cost per user tab.
- **`other` — "Other"**: standalone scripts with no card — `backfill_subject_tags.py` (a
  completed one-off) and `find_contact_emails.py` (a full-catalog pass; an ordinary
  `refresh_opportunities.py` run already resolves `contact_email` per row, so this is only
  for an initial backfill or a `--force` re-check).

Each band carries a `note` the console prints under the legend, and each per-day entry
carries `parts` so a collapsed band still names its components in the bar tooltip —
grouping hides nothing, it just stops the legend reading like an implementation detail. The chart hint now
states the charted total and flags any gap rather than letting two adjacent figures
disagree in silence. Note `fetch_deadline_check_cost(start, end)` **accepted and ignored
`end`** until 2026-08-22, which made the summary's previous-period deadline cost include
the current period too, so every KPI delta touching deadline spend was wrong.

**A committed snapshot is marked as committed.** `dryrun_common._pending_count()` is
computed purely from the file on disk and is never compared against the database, so a
snapshot read identically before and after being applied. `annotate_committed_snapshots()`
recovers the link from the `notes` string the commit already writes (`Committed dry-run
snapshot <file>`) rather than adding a column — no migration, and every historical commit
is recognised retroactively. The button becomes **Commit again…** and says when it was
applied. This is harmless for the scraper (inserts dedupe on normalized URL) but not for
the three PATCH agents, where a second commit re-applies days-old field values over
whatever has changed since.

**The scraper is a TWO-PHASE call per seed, and the split is the accuracy design.**

    Phase 1 (research)  prose out, googleSearch on  -> keeps groundingChunks/groundingSupports
    Phase 2 (extract)   phase 1 notes + RESOLVED urls in -> strict JSON out, no search

Do not collapse these back into one call. Gemini does not put a retrieved URL in its answer
text: `groundingChunks[].web.uri` is the only place the real URL exists, and a JSON-only
answer does not carry it back. When the model answers without searching it writes URLs from
memory, and they come out with the **right host and a path off by one segment** — measured,
**30 of 116 URLs in the 2026-08-20 batch were hard 404s (26%)**, every one a constructed deep
path and never a bare domain. Head to head, 4/4 model-typed URLs 404'd where 4/4
grounding-resolved URLs returned 200, including the catalog's own dead
`training.nih.gov/research-training/sip/` against the real `…/research-training/pb/sip/`.
Phase 2 needs no search, so a strict output format is free there.

- **`url_validate.py`** does both halves and is entirely free — no API calls, no keys.
  `resolve_grounding_chunks()` follows the one redirect hop from
  `vertexaisearch.cloud.google.com/grounding-api-redirect/…` to the real page (`web.title` is
  only a bare domain and `web.domain` does not exist on `v1beta/generateContent`, so the hop
  is the only way). `support_urls_by_span()` uses `groundingSupports` to tie a source to one
  opportunity's own span — without it you only know which pages were consulted for the whole
  answer, which cannot say which URL belongs to which of eight results. `check_urls()`
  separates **dead** (404/410) from **unverified** (403/429/timeout); 403 is ~9% on the
  existing catalog, so treating it as death would throw away good rows.
- `spans_for_name()` matches on significant words, not exact substring: a candidate named
  "NASA Internship Programs (Summer 2027)" whose span reads "NASA Internship Programs:"
  otherwise keeps its remembered URL while the retrieved one sits unused.
- **`call_gemini(..., return_grounding=True)`** returns a third element; the default stays a
  2-tuple so the five other call sites are untouched. `usage["server_tool_use"]` now also
  carries **`web_search_queries`** — the actual query strings, which were reduced to a
  `len()` until 2026-08-23, meaning nobody could see or tune what was being searched.
- Every seed's raw notes, queries, resolved URLs and candidates are written to
  `agent_logs/scraper_<stamp>_seed<id>.json`. No run before this kept any, which is why past
  failures ("5 candidates, all invalid, $0.09", twice) were undiagnosable afterwards.

**Silent search cannot be fixed by prompting — retry instead.** Gemini decides per call
whether to search, non-deterministically: seed 51 was run twice with an *identical* command
and returned 0 searches once and 6 the next time. Phase 1 therefore retries once on a
zero-search response (cheap — a silent call pays no $0.014/search fee) and flags whatever is
still silent. See `gemini_common.py`'s FIFTH and SIXTH findings. Do not try to force it; the
THIRD finding's conclusion that no reliable forcing mechanism exists is **correct**.

**Discard almost nothing, explain everything.** Every row lands `is_active=false` with
`moderation_status='pending_review'` and short `quality_flags` saying *what to go and check*.
Only two things never reach the table: an **exact duplicate** (same normalized URL *and*
matching name, via `url_dedupe.find_duplicates()`), and a candidate with **no URL** — the URL
is the row's identity. Both are written to the review snapshot with their raw JSON, so
nothing vanishes silently. The snapshot is now `{"inserted": [...], "rejected": [...]}` rather
than a bare list; `dryrun_common.py` reads these files, so **check it if you change that
shape**. Flags are the `FLAG_*` constants in `scrape_opportunities.py` and must stay short —
the console renders each as a pill truncated at 90 characters (with the full text in a
`title=` tooltip).

**A live URL is not a correct URL — the second failure mode.** Fixing the 26% dead-link rate
did not finish the job, it changed the shape of the problem. Auditing all 166 rows of the
2026-08-23 batch by fetching each page and comparing its `<title>` to the row found **10 (6%)
whose URL was a third-party SEO round-up** that merely mentions the program
(`ladderinternships.com/…/19-selective-internships…` stored for Stanford AIMI,
`indigoresearch.org/blog/…` for NASA OSTEM). **Every other check passes those**: they return
200, so `check_urls` is happy; they have a deep path, so `is_bare_domain` is happy; the path
is not `/faq/`, so `is_low_value_path` is happy. `url_validate.domain_matches_org()` is the
only signal that catches them, and `FLAG_OFFSITE` is what it writes.

Half of them were `reconcile_url`'s own doing: when grounding attributed a span it took
`span_urls[0]` **without first asking whether the model's URL was itself one of the retrieved
pages**. It was, in 33 of the 166 rows — `aimi.stanford.edu/education/summer-research-internship`,
`stemgateway.nasa.gov/…/high-school-internships` and `nhsjs.com/submit-your-work/` were each
discarded for a blog. That check is now hoisted above the span fallback: a URL the search
actually returned is verified no matter which sentence cited it. Replayed over the whole
batch the hoist changes 33 rows, improves 5 measurably and **worsens none**. The other half
the model simply typed itself, which no ranking can repair — those get the flag.

`domain_matches_org()` matches by **substring against each domain label**, not by whole
tokens, because a domain label is words run together with no separator: an exact-token rule
read `idyllwildarts.org`, `tellurideassociation.org` and `artandwriting.org` as unrelated to
their own owners and fired on **58%** of the batch against 16% for the substring rule (which
still catches 10/10). Abbreviations count in both directions (`colum.edu` for Columbia
College, the `umich`/`upenn` shape), as do acronyms and initials — and initials are taken
with parentheticals stripped, or "Fermi National Accelerator Laboratory (Fermilab)" yields
`fnalf` and misses `fnal.gov`. Being generous is the point: a wrong "unrelated" flags a good
row, and this is a review hint, never a rejection.

**A seed is an ANGLE — nothing else.** It used to be a `(category, angle)` pair; the category
was dropped 2026-08-23. It was never interpolated into either prompt, so it never influenced
the search; nothing in the student-facing app reads the `opportunities.category` column it was
written to (nullable, already NULL on 1139 of 1440 rows, and `preFilter` keys off `type`); and
its one live use was a silent `type` fallback. Measured across 238 scraper rows, the model's
type disagreed with the seed's category **27% of the time overall and 65% for Research seeds**
— so the fallback fired a guess that was usually wrong, at exactly the moment (a malformed
response) when guessing is least defensible. An invalid type is now a review flag.
`scraper_seeds.category` still exists because it is `not null` and dropping it needs DDL this
repo cannot run: `create_seed()` writes `SEED_CATEGORY_PLACEHOLDER` to satisfy the constraint,
`SEED_SELECT` does not read it, and the console no longer offers it.

**Scraper search angles** ("seeds") live in a Supabase `scraper_seeds` table
([scraper_seeds_schema.sql](scraper_seeds_schema.sql)), editable from the admin console, with
lifetime per-angle yield totals (`total_added`, `total_cost`, …) so unproductive angles can be
found and retired. `seeds_common.py` loads them and falls back to the hardcoded
`NATIONAL_SEEDS`/`SEATTLE_SEEDS` literals in `scrape_opportunities.py` if the table is empty or
unreachable — it logs loudly which source it used.
Yield totals are credited by `record_seed_result()`, which **re-reads the seed immediately
before adding** rather than adding to the copy loaded when the run began: PostgREST cannot
do `SET total = total + n` without a stored function (DDL this repo cannot run), so it
stays read-modify-write, but the window shrinks from the length of a 110-minute pass to one
round trip — the stale-snapshot version silently discarded any console edit made mid-run.
**Dry runs now credit `found`/`dupes`/`cost` but never `added`.** This is a deliberate
exception to "--dry-run skips DB writes", of the same kind `agent_runs` already makes: a dry
run spends real money, so its cost belongs against the angle that spent it. Crediting nothing
at all is why every angle still read zero after a month — the whole retire-bad-angles feature
had no data. `added` stays 0 because nothing was actually inserted. Fallback angles have no id
and still credit nothing; `seed_yield_state()` on `GET /api/seeds` says which case a grid of
zeros is, because it otherwise reads as a dead feature. Select angles with `--seed-ids` (stable);
`--seed-indices` is deprecated because positions shift whenever a seed is added or deleted.

**Admin console** is [admin_console.html](admin_console.html), served at `/admin`. It and every
`/api/agents/*` and `/api/seeds` route are **restricted to localhost** (`_require_local()`), since
the server binds all interfaces and these routes spend money. Agent output is streamed line by
line to a ring buffer and to `agent_logs/<agent>_<stamp>.log`, readable via
`GET /api/agents/log?agent=&since=`.

**Restarting the dev server**: use [restart_server.ps1](restart_server.ps1), never Bash `&`.
It kills whoever actually owns port 8000 (via `Get-NetTCPConnection`, not `pkill` — Git Bash
cannot see native Windows python processes), verifies the port is free, and records the *listening*
PID. The WindowsApps `python.exe` is an alias shim whose child holds the socket, so the PID
`Start-Process` returns is not the server. Getting this wrong once left 26 zombie processes
serving stale code for a whole session.

**Backend (`server.py`)** is a `ThreadingHTTPServer` with a `GET /api/opportunities` route
plus five POST endpoints:
- `/api/messages` — proxies to the real Gemini API (model `gemini-3.5-flash-lite`, pinned
  as `MESSAGES_MODEL` in server.py, see `callGemini()` in script.js) when `GEMINI_API_KEY`
  is set, otherwise fabricates a mock response by pattern-matching the `system` prompt text
  (`generate_mock_text`). Client sends a plain `{system, userContent, useWebSearch}` body
  (not Anthropic's content-block/messages envelope); server.py reuses `gemini_common.
  call_gemini()` — the same request-building, forced-search nudge, and thinking-budget
  handling used by the offline batch scripts (`check_deadlines.py`/`check_reviews.py`) —
  and re-wraps the result into a `{content:[{type:"text",text:...}]}` envelope so both live
  and mock responses parse the same way client-side. When adding a new AI-backed feature,
  add a matching mock branch here so the app stays usable without a live key.
- `/api/messages-claude` — the one deliberate holdout from the Gemini migration: the
  profile chat's `profileChatNextQuestion`/`profileChatStarterQuestionsFromAI` (see
  `callClaude()` in script.js) still run on the real Anthropic API (model
  `claude-haiku-4-5-20251001`, pinned as `CLAUDE_MODEL` in server.py) when
  `ANTHROPIC_API_KEY` is set. Client sends the same plain `{system, userContent,
  useWebSearch}` shape as `callGemini()`; `proxy_to_anthropic()` translates that into
  Anthropic's content-block/messages envelope server-side, so the client stays
  backend-agnostic regardless of which endpoint it's calling. The client may also send
  `maxTokens`, clamped server-side into `[CLAUDE_MAX_TOKENS, CLAUDE_MAX_TOKENS_CEILING]`
  by `_clamped_max_tokens()`. That exists for **profile synthesis**, which rewrites the
  whole profile on every merge and so produces a longer answer as the profile grows — at
  the flat 1000-token default it was silently cut off mid-sentence, and Anthropic hands
  back the partial text looking like a normal, complete response. `callClaudeDetailed()`
  in script.js surfaces `stop_reason` so `synthesizeProfile()` can retry at the ceiling
  and, if it is *still* truncated, throw rather than save a fragment over a complete
  profile. There is deliberately no word limit on the profile in the prompt, in storage,
  or in the display. Note **which end of the profile a truncation eats**: the prompt
  emits general paragraphs first, then `Passion Project: ` paragraphs, then
  `Research Project: ` ones, so a response that ran out of budget always lost its tail —
  i.e. the projects — which is how this surfaced ("passion projects cut off") rather than
  as a visibly half-written profile. A profile damaged that way does **not** heal on its
  own: `existing` is handed to the next merge as ground truth under "do not drop details
  from the current profile", so the fragment is copied forward indefinitely, and the card
  is read-only apart from Clear profile. The system prompt therefore carries a repair
  clause (finish the thought only if the rest of the profile makes it unambiguous,
  otherwise drop it, never invent), which fixes it on the next ordinary merge, and
  `profileHasTruncatedTail()` / `repairProfile()` back a **Tidy it up** button that runs
  that pass on its own for students who don't chat again.
- `/api/register`, `/api/login`, `/api/data/save`, `/api/data/load` — backed by a Supabase
  `users` table (`get_user`/`create_user`/`update_user_data` in server.py), queried with the
  `SUPABASE_SERVICE_KEY` (service_role — bypasses RLS). That table has RLS **enabled with no
  policies at all**, so the anon key gets zero access to it; only `server.py`'s service-role
  calls can read/write it, unlike the public read-only `opportunities` table. Client hashes
  passwords with SHA-256 (`crypto.subtle.digest`) before sending; the server only ever
  stores/sees the hash — no salting, no HTTPS enforcement, no rate limiting (fine for a
  prototype, not production-grade). `migrate_users_to_supabase.py` was the one-off script that
  moved the old flat-file `users_db.json` into this table — logic/shape is otherwise
  unchanged, this was a storage-backend swap only.

**Subscription, trial, and signup consent.** Every account starts a **3-day free trial**
that converts to a **$9.99/month** Stripe plan. `subscription_common.py` talks to Stripe
over raw HTTP (no SDK, matching the stdlib-only philosophy) and holds the `PROMO_CODES`
dict; four POST endpoints (`/api/subscription/status|checkout|cancel|validate-promo`) sit
in `server.py`.

- The `users` table needs the columns in **[subscription_schema.sql](subscription_schema.sql)**
  — a one-time manual DDL step in the Supabase SQL editor, same as `user_costs_schema.sql`.
  PostgREST has no DDL endpoint, so nothing in this repo can run it. **Until it runs,
  registration is down**: `create_user()` writes all of those columns and Postgres rejects
  the insert entirely if one is missing. `/api/register` detects that case (PostgREST
  reports it as `42703` on reads but `PGRST204` on writes — both are checked) and returns a
  **503 naming the file**, rather than the bare `502 Could not reach Supabase` that cost a
  session of debugging.
- **`subscription_state(record)` in `server.py` is the single source of truth** for whether
  an account may use the app. The client paywall and the server-side gate both derive from
  it, so they cannot disagree. `has_access` is: `active` → yes; `trial` → yes until
  `trial_ends_at`; `beta` → yes until `subscription_end_at`; `canceled` → yes until
  `subscription_end_at` (cancelling is cancel-at-period-end, they paid for that time);
  anything else → no.
- **Promo codes come in two incompatible kinds**, keyed by `kind` in `PROMO_CODES`.
  A **`grant`** code (`BETAUSER` → status `beta`, +7 days) is redeemed immediately against
  the user's row via `POST /api/subscription/redeem-promo`; it touches no Stripe and works
  with Stripe unconfigured. A **`checkout`** code (`FREEMONTH`, `WELCOME10`) is a discount
  that only exists once Stripe is involved and is passed to the Checkout Session. Redeeming
  a checkout code through the grant endpoint is refused — it would burn the code for
  nothing. `validate-promo` returns `kind` so the client knows which path to take.
  Grants extend from `max(now, current end)`, so they **add** to a running trial rather
  than replacing it, and `GRANTABLE_STATUSES` stops a typo'd status from writing a value
  `subscription_state()` has no branch for (which would read as no access and lock out the
  user who just redeemed).
- `promo_codes_used` is what makes a code one-per-account. Before the redeem endpoint
  existed nothing ever wrote that column, so "one-time use" was unenforced.
- **A `trial` row with a NULL `trial_ends_at` means "clock not started", not "expired".**
  That is every account predating the migration. `ensure_trial_started()` stamps a real
  window on first sign-in. Reading NULL as expired — which `is_trial_expired(None)` does if
  you take it literally — would paywall every existing user the moment the migration lands.
- Enforcement is deliberately in **both** halves. `showApp()` checks before the app shell is
  unhidden (no flash of a usable app), and `Handler._subscription_blocks()` returns **402**
  from the four endpoints that cost money per call. The client lock is a screen; the 402 is
  the control. Calls with no `userid` are not blocked — unidentifiable, same residual the
  cost attribution reports as unattributed.
- **Both `userid` and `email` must be unique across all accounts**, case-insensitively.
  `users` has no is_active/deleted column, so every row is a live account and any match is
  a real conflict. `handle_register()` checks both up front and names the field that
  clashed — Postgres alone returns a bare 409 that can't say which. Uniqueness is by
  **normalization, not `ILIKE`**: `userid` is lowercased everywhere already, and
  `normalize_email()` lowercases/trims on write so an exact `eq.` match *is* the
  case-insensitive lookup. Don't switch these to `ilike` — `_` is a legitimate email
  character and an ILIKE wildcard, so it would over-match and refuse valid signups.
  The check and the INSERT are two round-trips, so simultaneous signups can still race
  past it; the unique index in **[users_email_unique_schema.sql](users_email_unique_schema.sql)**
  is what actually closes that, and `EMAIL_UNIQUE_INDEX` in `server.py` must keep matching
  the index name there or an email collision gets reported as a userid collision.
- **Signup consent**: three checkboxes (18-or-older; if not, parent/guardian permission
  per Terms §2; and accepting the Terms + Privacy Policy). `handle_register()` re-checks all
  three server-side and refuses the account otherwise. What was accepted is stamped on the
  row (`is_adult`, `parental_consent`, `terms_accepted_at`, `privacy_accepted_at`,
  `terms_version`). **Bump `TERMS_VERSION` in `server.py` whenever `legal/*.md` changes
  materially** or old and new acceptances become indistinguishable.
- **The legal documents are generated.** `legal/terms.md` and `legal/privacy.md` are the
  source of record; `terms.html` / `privacy.html` are built from them by
  **`build_legal.py`** and must not be hand-edited — re-run it after any edit under
  `legal/`. Note Terms §3 still states the beta is free of charge, which the $9.99 plan
  contradicts.
- Stripe is **not configured**: `STRIPE_API_KEY`/`STRIPE_PRICE_ID` are absent from `.env`,
  so `upgradeSubscription()` fails at checkout. Everything upstream of the payment itself
  (trial, gating, promo validation, cancel bookkeeping) works without it.

**Two persistence layers on the client**, easy to conflate:
1. `window.storage` (get/set, async) — used for `currentUser` session cache, `studentProfile`,
   `trackerData`, `trackerSavedState`. This API is **not defined anywhere in this repo**; it's
   presumably injected by whatever runtime hosts the live preview, and calls are always
   guarded with `if(window.storage){...}` + try/catch. Running `python server.py` and opening
   a plain browser tab means these silently no-op — data won't persist across reloads in that
   environment.
2. The Supabase `users` table via `/api/register`/`/api/login`/`/api/data/save`/`/api/data/load`
   — this is the only storage that actually persists accounts and per-user data (profile,
   tracker) across server restarts and different browsers/devices.

**AI call flow**: most AI features funnel through `callGemini(system, userContent, useWebSearch)`
in script.js, which POSTs to `/api/messages` and returns cleaned text; `extractJSON()` then
pulls a JSON value out of that text via brace/bracket-depth scanning (handles trailing
commentary and attempts best-effort repair of truncated/token-limited responses). Callers:
`inferSubjects`, `rankCandidates`, `findVenuesViaWeb`, `synthesizeProfile`,
`assessProfileReadiness`, `extractTrackerInfo`/tracker classification. `findVenuesViaWeb`
(live `useWebSearch: true` search, bypassing local ranking) is currently unused by any
`KIND_CONFIG` kind — Conference/Journal Venue used it until the Supabase `opportunities`
table gained real `Conference`/`Journal`-typed rows and moved to the local-database path
like every other kind; it's kept as a fallback for a future kind whose type is too sparse
locally. The profile chat's `profileChatNextQuestion`/`profileChatStarterQuestionsFromAI`/
`starterQuestionPoolFromAI` are the one exception — they call `callClaude(system,
userContent, useWebSearch)` instead, POSTing to `/api/messages-claude` (Anthropic,
`claude-haiku-4-5-20251001`), same response parsing either way.

**The profile chat's two halves are cached asymmetrically, and the asymmetry is the point.**
Openers are cached; follow-ups are deliberately not. Both are `callClaude()`, so the
difference is not visible from the call sites — only from what each one depends on.

- **Openers** (the 3 questions offered when the drawer opens) depend on the profile text and
  nothing else. There is no conversation yet for them to react to, which is exactly what
  makes them safe to cache. They live in a `starterPool` slot in `PROFILE_DERIVED_SLOTS`
  alongside `filterValues`/`filterTags`/`basics`: **10** questions generated per profile
  "version", from which each drawer open serves a rotating window of **3**
  (`drawStarterWindow`), so four opens pass before anything repeats. Being a slot also means
  `refreshProfileFilterValues()` pre-warms the pool right after every merge — it walks every
  slot — so the drawer opens on a warm cache (measured: ~3.8s cold, 0ms warm) and
  regeneration is tied to the existing `PROFILE_FILTER_REFRESH_WORDS` bar rather than a
  second threshold meaning the same thing. **Regenerate stays a live call**: that button is
  the explicit "these don't suit me", which is the one place paying is clearly warranted.
- **Follow-ups** (`profileChatNextQuestion`) are one live call per bot turn, and must stay
  that way. A follow-up's whole job is to react to what the student just said, and a
  pre-generated question cannot. This was tried and reverted: with pooled follow-ups, a
  student who answered "I'm writing a paper on grapheme-to-phoneme error rates in
  Finno-Ugric languages with two friends from a summer camp" got a generic non-sequitur
  back, because that detail did not exist when the pool was built and does not reach the
  profile until the drawer closes and synthesis runs. **Do not "optimize" this into a pool.**
- The transcript sent with a follow-up includes the **bot lines, not just the student's
  answers**. Answers are routinely meaningless alone ("Yes." says nothing), and the bot lines
  are also what stop the model re-asking what it already asked.
- Both question prompts carry two style rules worth preserving: **one short sentence, never a
  run-on or two questions joined by "and"/"or"**, and **at most 2-3 profile details per
  question** — chaining four or more produces the elaborate connect-the-dots questions this
  replaced.
- Cost note: this is roughly a **wash in dollars**, and was never mainly a cost change — it
  spends where responsiveness is bought and caches where it cannot be. The per-turn question
  is not the expensive call in this flow; **synthesis on drawer close is** (it rewrites the
  whole profile at a 4-8k output budget, ~6x a follow-up turn). If real savings are ever
  wanted, that is where to look. Both opener paths classify as `chat_starters` in
  `_FEATURE_SIGNATURES` and follow-ups as `profile_chat`, so the console can tell them apart.
- **Closing the drawer always ends the session** (`resetProfileChatSession()` clears the
  transcript, the starters, and any unsent input). Synthesis still runs only when the student
  actually answered something — an empty transcript would pay the most expensive call in the
  flow to rewrite an unchanged profile. Before that reset existed, a starter question the
  student read but never answered stayed in `profileChatHistory`, and reopening rendered that
  stale bubble instead of a fresh set of starters.

**App pages** (single-page, no router — `showPage(name)` toggles `#page-*` sections).
Note two sections that live *outside* `#appShell` and are therefore not `showPage()`
targets: `#page-login` (the sign-in/registration gate) and `#page-locked` (the paywall) —
both replace the app wholesale rather than rendering inside it.
Home/Dashboard (progress bars, todo counts), Wizard/Finder (quiz or free-text profile →
`runSearch()`/`runProfileSuggestSearch()` → ranked results → `buildTracker()`), Tracker
(calendar + list views across buckets in `ALL_BUCKETS`: summerPrograms, internships,
researchCompetitions, pureCompetitions, conferences, journals).

**The landing page's walkthrough film** is [walkthrough.html](walkthrough.html) — a
**vendored, self-extracting bundle** exported from a design canvas, holding its own React
runtime, the composition source and every webfont in one ~1.5MB file. **Do not hand-edit
it**: the real source is a `<script type="__bundler/manifest">` block of gzipped,
base64'd assets, so every apparent line of it is machine-written. Re-export and replace
the whole file to change the film. It is 1920x1080, 37 seconds, autoplays **once** on
load, and ships its own dark play/scrub transport bar — that bar is the replay control,
which is why nothing on the landing page draws one.

Those two facts (heavy, and self-starting) are why it is **not** a plain `<iframe>` in
the markup. `mountWalkthrough()`/`unmountWalkthrough()`/`initWalkthrough()` in
[script.js](script.js) inject the frame only once `#page-landing-how` is ≥35% on screen,
so the film starts when it is actually being watched rather than finishing unseen while
the visitor is still reading the hero, and no landing visit pays the 1.5MB unless someone
scrolls that far. `prefers-reduced-motion` suppresses the auto-mount; the poster stays
clickable, which is also the fallback where `IntersectionObserver` is missing. The three
`showPage`-adjacent functions that hide the landing page (`showLoginGate`, `showPaywall`,
`showApp`) each call `unmountWalkthrough()` explicitly rather than trusting the observer
to notice the section went `display:none`.

It replaced the old "What You're Chasing" mock-progress-bar card, and took over that
section's `id="page-landing-how"` so the hero's **See how it works** button lands on the
film with the three explainer cards reading as its captions underneath. The film is
authored for desktop: below ~500px wide its in-frame text is too small to read, and those
three cards are deliberately the readable version of the same story. Note the *other*
"What You're Chasing" in [index.html](index.html) is the in-app tracker header inside
`#appShell` and is unrelated.

## Security notes for this repo

- `.env` (holds `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
  `SUPABASE_SERVICE_KEY`), `.claude/settings.local.json`, and `server.log` are gitignored —
  never `git add -f` them. `.gitignore` only prevents future tracking; if a secret is ever
  committed, `git rm --cached` is also required. `SUPABASE_SERVICE_KEY` in particular must
  never reach the browser/client code — it bypasses RLS and is only read by `server.py` and
  the one-off `migrate_*_to_supabase.py` scripts.
- If a secret is ever committed locally but not yet pushed, prefer `git reset --soft` to
  before the leaking commit + a fresh commit, then `git reflog expire --expire=now --all &&
  git gc --prune=now --aggressive` to purge the orphaned blob, verifying after with
  `git fsck --full --unreachable`. Do not rewrite history that's already been pushed without
  explicit user confirmation.
