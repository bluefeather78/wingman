# HANDOFF — Highschool Wingman

## Project
Static vanilla-JS single-page app ("Highschool Wingman" — finds/tracks extracurricular
opportunities for high schoolers) at **`C:\Users\shama\Documents\wingman`**. No build step,
Tailwind via CDN. `CLAUDE.md` exists in the repo root with architecture notes — read it first,
it's kept current. This folder **is** a git repo (`origin` =
`https://github.com/bluefeather78/wingman.git`, branch `main`).

---

# CURRENT THREAD: Review-queue actions, snapshot stamps, Haiku pin (2026-08-22)

## Goal
Close the gap the admin-console thread left open: the review queue could only *activate*.
Junk rows could be left inactive but never adjudicated, so they came back round every time
the queue was opened and the queue only ever grew — and a row that was nearly right could
not be fixed at all without a database console.

**Status: built, round-tripped against the live database, catalog left unchanged (113 rows
still pending, 0 rejected). Nothing is committed.**

## What was added
- `POST /api/agents/pending/moderate` `{ids, status}` — writes `moderation_status`,
  `reviewed_by = "admin-console"`, `reviewed_at`. Valid statuses mirror the CHECK constraint
  in `user_submissions_schema.sql`. `rejected`/`duplicate` also force `is_active = false`
  and bust `_opportunities_cache`.
- `GET /api/agents/pending?status=queue|rejected|all`. The console fetches `all` once and
  splits client-side, so the Queue/Rejected pills cost no round-trip.
- Console: Queue / Rejected `.tab` pills in the card head, a red **Reject selected**, and
  **Restore to queue** in place of it on the Rejected tab. Queued rows now show provenance
  badges — `from <userid>`, duplicate-candidate count, and each `quality_flags` entry —
  which is the whole point of the columns for user submissions.
- `activate_opportunities()` now stamps `moderation_status = "approved"` too. Approving does
  NOT activate; activating does approve.
- `POST /api/agents/pending/update` `{id, fields}` + a per-row **Edit** modal — 16 editable
  fields (name, org, type, url, summary, eligibility, grades, price/cost, location/state,
  intl, season, category, subject_tags), each validated server-side with a message the modal
  shows verbatim. `_BASE_PENDING_SELECT` was widened to carry them so the modal prefills from
  the list already in memory; the client sends only changed fields.
- A per-row **Duplicate** modal, plus `GET /api/agents/opportunities/search?q=&limit=` to
  find the surviving row (id-exact first, then name/org/url ilike; active and inactive
  alike). The modal offers the row's stored `dup_candidates` — with the reason and
  confidence `url_dedupe` recorded at submission time — before the free search.
  `duplicate_of` is now validated: required for `duplicate`, refused for every other status,
  cannot be the row itself, and cannot point at a row that is itself rejected/duplicate.
  It is cleared on restore/reject rather than left stale. `list_pending_opportunities`
  returns a `duplicate_targets` map so the queue names the survivor, not a bare id.

### Timestamped snapshot filenames (separate from the queue work)
Snapshot filenames were date-only, so a second run of the same agent on the same day
silently overwrote the first one's file — and a `--dry-run` has already paid the API in
full by the time it writes, so that destroyed work that cost real money. There is physical
evidence of the problem in the repo: someone had hand-renamed
`scrape_review_national_20260818.run1-backup.json` to save one.

- `agent_common.snapshot_stamp()` is the new shared helper; all four agents call it and now
  write `YYYYMMDD-HHMMSS`. It lives in `agent_common` for the same reason the flags do —
  four writers, one reader (`dryrun_common`), one contract.
- **Both shapes are read.** `dryrun_common._run_date()` takes `(\d{8})(?:-(\d{6}))?`, so
  every pre-existing date-only file still lists and still commits; those resolve to midnight,
  which is exactly what the old code returned, so their staleness and ordering are unchanged.
- The console prints a time **only** for filenames that carry one — a date-only snapshot
  showing `00:00` would read as a real run time rather than "unknown which run".
- Sorting moved from `run_date` to the full `ran_at`; same-day files would otherwise order by
  filename, i.e. alphabetically by agent instead of newest-first.
- The stamp is local time (matching `agent_logs/<agent>_<stamp>.log`) but parsed/served as
  UTC so snapshots stay comparable; the console reads it back with `getUTC*` so the printed
  digits match the filename's.
- **The scraper's `source` value stays date-only** (`scraper-national-20260820`). A whole
  day's scrape groups under one source and 113 existing rows carry those values — only the
  filename gained seconds.

### One Anthropic model everywhere: Haiku (also separate from the queue work)
`claude_common.MODEL` was still `claude-sonnet-4-6` while `server.py`'s `CLAUDE_MODEL` and
`check_deadlines.py`'s local pin were both `claude-haiku-4-5-20251001`. Asked to make it
consistent, and Haiku is now the single answer. Chasing it turned up two costing bugs that
were the real damage:

1. **`claude_common`'s price constants are imported by `server.py`** to cost every
   `interactive_claude` call — and those calls run on `CLAUDE_MODEL` (Haiku). With the file
   on Sonnet the constants were Sonnet's $3/$15 against Haiku's actual $1/$5, so interactive
   Claude spend was estimated at **3x** what it cost. Now $1/$5.
2. **`check_deadlines.py` imported `estimate_cost` from `gemini_common`** and used it on
   calls its own local `call_claude()` made to Anthropic — pricing a Claude call at Gemini's
   $0.75/$3.75 + $0.014/search. Now imports it from `claude_common` (which also counts the
   cache-token fields Anthropic returns and Gemini's version does not).

Also worth knowing: the resume/LinkedIn import calls `claude_common.call_claude` but records
its cost under `CLAUDE_MODEL`, so before this change `user_costs.model` named Haiku for a
call that had actually run on Sonnet. One pin fixes that too.

**Neither correction is retroactive.** `agent_runs`, `user_costs` and `deadline_check_log`
rows written before 2026-08-22 carry the old rates. Nothing recomputes them, and the
overstatement is not uniform (interactive Claude was too high, deadline checks were priced
on a different provider's curve entirely), so do not apply a blanket multiplier.

`WEB_SEARCH_PRICE_PER_SEARCH` stayed at $0.01 — web search is billed per search at a flat
rate, not per model. The date-suffixed id `claude-haiku-4-5-20251001` was kept rather than
the bare `claude-haiku-4-5`, to match the two pins that already existed and work in
production; introducing a third spelling was the opposite of the point.

### check_reviews.py's TEMP filter: removed
`"review_status": "is.null"` is gone from the row selection; `"order": "id"` stays (stable
resume ordering — never actually temporary, just mislabelled).

**The reason recorded for keeping it was already wrong.** The old note said the filter was
why the agent previews as 0 rows due. Measured against the live catalog: 1327 active rows,
**0** with a NULL `review_status`, **0** never-reviewed-or-stale. Preview was 0 because of
the staleness filter alone, and removing the TEMP filter changed today's count not at all
(verified: `--preview` still reports 0).

What it was actually doing was freezing the agent permanently. With no NULL rows left it
matched nothing, so `STALE_AFTER_DAYS = 182` was dead code and no row could ever be
re-checked. It also sat **outside** the `if not args.force` branch, so `--force` — which
documents itself as ignoring the staleness filter — returned 0 rows as well. `--force` now
resolves the full 1327 (verified through both the CLI and the console's preview endpoint:
$0.70, ~6635s estimated).

The 605 rows (46%) sitting at `insufficient_data` are the ones most worth a re-check, and
were exactly what the filter blocked.

**`STALE_AFTER_DAYS` lowered 182 -> 30** in the same pass, at the user's call. At 182 an
ad-hoc run did nothing for half a year, so `--force` (the entire catalog) was the only way
to make this agent act at all between passes — all-or-nothing. At 30 the catalog comes due
in five batches from **2026-09-17** (825 rows) through 2026-09-21 instead of one 1327-row
wall. The threshold was kept rather than removed so a plain run stays idempotent: rows
checked yesterday are not re-paid for, which is what protects an accidental run — or a
future scheduler — from re-spending ~$0.70 and 110 minutes every firing.

Console help text for the Force checkbox was updated to match (it said "182-day") and now
also says Force means the full catalog, not just what has aged. Verified: `--preview`
reports 0 rows due today under the new threshold.

## Traps hit (worth not re-learning)
- **`NULL NOT IN (...)` is NULL, not true.** Every scraper row has a NULL
  `moderation_status`, so filtering the queue with `moderation_status=not.in.(rejected,
  duplicate)` empties it completely. The filter spells the null case out with `or=`.
- **PostgREST 400s the whole select on one unknown column**, so a single wide select would
  take the queue down on a database missing the migration. The read is a two-step ladder
  (wide → base) and reports `moderation_ready`.
- `togglePending()` re-renders the table, so a scripted `cbs[0].click(); cbs[1].click()`
  only registers the first — the second element is detached by then. Not a bug for a human.
- **The edit endpoint is scoped to `is_active = false` and reads the row to check.** Without
  that it is a general catalog editor reachable by id, with no confirmation and no audit
  trail — reachable from a page whose other two buttons both confirm.
- **`type` is a clean 7-value enum in the data; `category` is not** ('COMPETITION',
  'SUMMER_PROGRAM', mixed case, 1139 NULLs). So type is validated and category is free text.
  Validating category would refuse most of the catalog's own values.
- **PostgREST's `or=()` filter is comma-separated and paren-delimited**, so a comma or paren
  typed into the survivor search would be parsed as syntax. They are stripped from the term
  rather than quoted — it is a search box, and a slightly broader match is harmless.

## Migration state, checked live this thread
`user_submissions_schema.sql` was found half-applied — eight of nine columns, no
`submission_payload`, so submissions would have inserted via step 2 of the ladder in
`_insert_opportunity_row()` and silently lost the AI-extracted extras (apply_url,
requirements, meta, note). It was **re-run mid-thread and all nine columns now exist**.
There are **0 user-submitted rows** so far, so nothing was lost.

User submissions have always shown up in this queue — `list_pending_opportunities()` filters
on `is_active = false` only, with no source restriction, and the submission path writes
exactly that. The Edit modal and the provenance badges are what make them actually
*reviewable* rather than merely present.

## Verification done
Reject → restore round-tripped through the UI on `ec18523`; edit round-tripped on the same
row (org, subject_tags array, grade_min changed and then reverted); duplicate round-tripped
too — searched, picked `ec17468` as survivor, the row left the queue with the survivor's
*name* rendered on it, then Restore put it back **and cleared `duplicate_of`**. Every
validation path exercised: unknown field, bad type, blank name, schemeless url, 3-letter
state, inverted grade range, non-numeric grade, missing id, empty payload, a live row
refused, duplicate with no target, duplicate of itself, duplicate of a nonexistent id,
`duplicate_of` on a non-duplicate status, and the chain guard (pointing at a row already
marked duplicate). Server errors surface inside the modal rather than silently.
**The catalog is unchanged: 113 rows pending, 0 rejected.** The one residue is that
`ec18523` now carries `moderation_status = 'pending_review'` instead of NULL — semantically
the same queue state.

Snapshots: two synthetic same-day files (`…20260822-091500` / `…-164200`) listed as separate
rows with their times, newest first, while the seven real date-only files kept their exact
previous display; a timestamped file resolved and preview-committed cleanly (2 entries, no
writes) and `../server.py` was still refused. Both test files deleted afterwards — the
snapshot directory holds the same seven files it started with.

## Next steps
1. ~~Re-run `user_submissions_schema.sql`~~ — **done**, `submission_payload` now exists.
2. Work the 113-row queue — rejecting is now possible.
3. ~~`duplicate` is reachable only via the API~~ — **done**, it has its own picker modal.
   Still no *merge*: marking a duplicate discards the losing row's data rather than folding
   anything into the survivor. That is the right default, but if the loser ever has better
   metadata, the fix today is Edit the survivor by hand first.
4. Editing a row's **URL does not re-run dedupe**, so a hand-edited URL can collide with an
   existing row. Deliberate (the check needs a full paginated catalog read per save); the
   duplicate would be caught by a human in the same queue.
5. Snapshot filenames now carry seconds, but **nothing links a snapshot to its `agent_runs`
   row** — that is still guesswork by timestamp. A run id in the filename would close it.
6. The **first list's items 4, 6 and 7 are deprioritized by the user** (scheduling, working
   the 113-row queue, and the 38%-failed-runs investigation). Item 8 (`check_reviews.py`'s
   TEMP filter) and item 10 (`user_costs` retention) were not raised either way.
7. `check_reviews.py`'s `--force` now reaches all 1327 active rows where it used to return
   0. Nothing was run — but the console's Live Run button on that agent is no longer a
   guaranteed no-op, so read the preview before pressing it.
8. Still uncommitted, along with everything from the prior threads.

## Files changed this thread
Modified: `server.py` (`moderate_opportunities()`, `update_pending_opportunity()`,
`_coerce_field()`, `_is_missing_column_error()`, status-filtered
`list_pending_opportunities()` + widened `_BASE_PENDING_SELECT`, approve-stamp in
`activate_opportunities()`, `search_opportunities()`, `_duplicate_targets()`,
`/api/agents/pending/moderate`, `/update` and `/api/agents/opportunities/search`),
`admin_console.html` (status pills, reject/restore, provenance badges, edit modal +
`OPP_FIELDS`, duplicate picker modal + `.dup-opt` styles,
`updateActivateBtn` → `updateReviewButtons`, snapshot time column + `clock()`),
`agent_common.py` (`snapshot_stamp()`), `dryrun_common.py` (both filename shapes, `ran_at`
/`has_time`, sort by instant), `refresh_opportunities.py`, `check_reviews.py`,
`check_deadlines.py`, `scrape_opportunities.py` (all four now stamp to the second),
`claude_common.py` (MODEL -> Haiku + matching prices), `check_deadlines.py`
(`estimate_cost` from claude_common), `check_reviews.py` (TEMP filter removed),
`CLAUDE.md`.

---

# PRIOR THREAD: Registration consent + trial/paid subscription

## Goal
Finish the subscription feature the previous thread left broken, and add the signup
consent the product needs to be lawful: an age checkbox, a parental-permission checkbox
for under-18s, a Terms/Privacy acceptance checkbox, and the two legal documents attached
in-app.

**Status: complete, migrated, and verified end to end against the live database.**
Registration works again. Nothing is committed.

## Migration: done

[subscription_schema.sql](subscription_schema.sql) was run in the Supabase SQL editor on
2026-08-21; all eleven columns are live. Re-running it is harmless (every statement is
`IF NOT EXISTS`), and it is still the file to point at if the table is ever rebuilt.

Verified after the migration: all 15 pre-existing accounts kept access (13 of them have
`trial_ends_at` NULL, read as "clock not started"), `ensure_trial_started()` stamps a real
3-day window on first sign-in, registration succeeds for both an 18+ and an under-18
account with the consent fields correctly recorded, a backdated trial 402s the AI endpoints
and swaps the live tab to the paywall without a reload, flipping the row to `active`
restores access, and signing back in as a lapsed account lands on the paywall without the
app ever becoming visible.

The previous thread's missing-column theory was **confirmed** before the fix with the literal
error: a `select=subscription_status` probe returns `42703, column
"users.subscription_status" does not exist`, and none of the six columns exist. Writes
report the same condition differently — `PGRST204, Could not find the 'subscription_status'
column of 'users' in the schema cache` — which is why `_is_missing_column_error()` checks
for both codes. Checking only 42703 (the obvious one, from the read probe) silently misses
every insert.

## What was built

**Consent at signup.** Three checkboxes on the register form: 18-or-older; if that is
unticked, a parental-permission box that also asserts 13-or-older (Terms §2 sets the floor
at 13 and requires guardian permission under 18); and Terms + Privacy acceptance with links
to the documents. The parental row hides *and clears itself* when someone ticks 18+, so a
box ticked before correcting an age can't still be submitted. `handle_register()` re-checks
all three and refuses the account — the browser half is the explanation, not the control.
Five new columns record what was accepted, stamped with `TERMS_VERSION`.

**The legal documents.** `legal/terms.md` and `legal/privacy.md` are the source of record;
`build_legal.py` renders them into `terms.html` / `privacy.html`. Linked from the register
form, the account drawer, and the paywall. The generated HTML must not be hand-edited.

**Trial enforcement**, which the previous thread flagged as a gap. `subscription_state()`
is now the single source of truth, and both halves derive from it: the client swaps in
`#page-locked` before the app shell is ever unhidden, and `Handler._subscription_blocks()`
returns 402 from the four endpoints that spend real money per call.

**Bugs found and fixed in the previous thread's code:**
- `create_checkout_session()` returned the session *id*, and the endpoint pasted it into
  `https://checkout.stripe.com/pay/{id}` — a retired URL form that 404s. It now returns
  the `url` Stripe actually gives back.
- `cancel_subscription()` DELETEd the subscription, revoking access immediately, while the
  confirmation dialog promised access "until the end of your billing period". Now
  cancel-at-period-end, with `current_period_end` recorded so the gate honors it.
- `days_until_trial_end()` floored, so a 3-day trial read "2 days left" one second in.
- A `trial` row with NULL `trial_ends_at` — i.e. **every account that predates the
  migration** — would have been read as expired and paywalled the instant the migration
  ran. Now read as "clock not started", with `ensure_trial_started()` stamping a real
  window on first sign-in. This one is worth remembering: it would have locked out the
  entire existing user base and looked like a subscription bug, not a migration bug.

**`BETAUSER` promo code + real redemption.** The promo system previously only *validated*
codes — nothing ever applied one, and nothing ever wrote `promo_codes_used`, so
"one-time use per account" was unenforced. Added `POST /api/subscription/redeem-promo`,
which writes: sets `subscription_status`, extends `subscription_end_at`, and records the
code. `PROMO_CODES` entries now carry a `kind`:

- **`grant`** — `BETAUSER`, status `beta`, +7 days. Applied server-side against the user's
  own row, no Stripe involved, so **it works right now with Stripe unconfigured**. This is
  the code to hand beta testers.
- **`checkout`** — `FREEMONTH` / `WELCOME10`, unchanged: discounts that only mean something
  at Stripe checkout. Redeeming one through the grant endpoint is refused rather than
  burning the code for nothing.

Grants extend from `max(now, current end)` so they **add** to a running trial (3 days left
+ BETAUSER = 10 days, not 7). Refused for `active` subscribers, since handing a payer a
7-day window is a downgrade. `beta` expires exactly like a trial, with its own copy on the
paywall and in the 402.

**Unique user ID + email at registration.** User IDs were already unique (primary key,
lowercased on every read/write). Emails were not enforced at all — the table had two
addresses shared across five accounts. `handle_register()` now checks both before
inserting and names whichever clashed, plus a basic email shape check (there was no
server-side email validation at all before). Case-insensitive by normalization rather than
`ILIKE`, because `_` is both a legal email character and an ILIKE wildcard.

The pre-insert check can still lose a race with a simultaneous signup;
[users_email_unique_schema.sql](users_email_unique_schema.sql) adds the unique index on
`lower(email)` that actually closes it. **That index cannot be created until the existing
duplicates are resolved** — CREATE INDEX fails outright while they exist. The file leads
with the query that finds them.

## What is still open

1. **Stripe is not configured.** `STRIPE_API_KEY` / `STRIPE_PRICE_ID` are still absent from
   `.env`, so `upgradeSubscription()` cannot complete a real checkout — untested against a
   live Stripe account across both threads now. It fails cleanly rather than crashing
   ("Failed to create Stripe customer: Stripe API key not configured"). Everything upstream
   of the payment (trial, gating, promo validation, cancel bookkeeping) works without it.
   **This is the blocking item for actually taking money.**
2. **No webhook.** Nothing flips `subscription_status` to `active` after a successful
   payment; `SUBSCRIPTION_SETUP.md` §2.4 documents the endpoint but it isn't implemented.
   Until it is, a paid subscription won't actually unlock the app.
3. **One conflict in the legal text**, flagged to the user, not changed — the
   documents are theirs to edit:
   - Terms §3 says the beta "is currently provided free of charge" and promises notice
     before charging. The $9.99 plan contradicts that as written.
   Editing it means re-running `build_legal.py` **and** bumping `TERMS_VERSION`.
4. Existing accounts have never accepted anything — `terms_accepted_at IS NULL` finds them.
   There is no re-consent prompt for existing users; the gate only runs at registration.
5. **Test accounts left in the `users` table** from this thread's verification:
   `adult1787351240`, `minor1787351240`, `uiflow1787351329353` (that last one has a
   deliberately backdated trial, so it always shows the paywall — handy for re-testing).
   `debugtest1` predates this thread; its status/trial were mutated during testing and
   restored afterwards. Delete them whenever.
6. **Unrelated pre-existing breakage spotted:** the Firebase block in `index.html`
   (~line 839) loads the v10 ES-module builds as classic scripts, so `firebase` is never
   defined and analytics silently never fires. Throws on every page load. Uncommitted work
   from another thread; not touched here.
7. Nothing is committed. Review the full diff before staging; do not blanket-stage.

## Files changed this thread
New: `subscription_schema.sql`, `users_email_unique_schema.sql`, `build_legal.py`, `legal/terms.md`, `legal/privacy.md`,
`terms.html`, `privacy.html`.
Modified: `server.py` (consent capture + enforcement, `subscription_state()` incl. the
`beta` status, `handle_redeem_promo()`,
`ensure_trial_started()`, `_subscription_blocks()` on four endpoints, `MissingUserColumns`,
login returns the subscription block), `subscription_common.py` (checkout URL, cancel
semantics, day rounding, `BETAUSER` + promo `kind` + `extend_from()`), `index.html` (consent checkboxes, `#page-locked`, legal links,
cache-bust to `?v=consent1`), `script.js` (consent validation, paywall, 402 handling,
promo/upgrade parameterized for reuse, promo redemption + `beta` rendering), `CLAUDE.md`, `SUBSCRIPTION_SETUP.md`.

---

# PRIOR THREAD: Snapshot commit + opportunity activation

## Goal
Two admin-console additions, both about acting on work that was already done and paid for:
1. **Commit a dry-run snapshot to the database** instead of re-running the agent live.
2. **Activate scraped opportunities** — flip `is_active` false → true from the console.

**Status: built, verified, nothing committed to git.** Neither feature has been *used* on real
data yet; that is the operator's call, not something to do unprompted.

## Why feature 1 matters
`--dry-run` calls the paid API at full cost and only skips the database writes. Until now the
only way to act on its answer was to run the whole agent again and pay a second time. Every
agent already dumps what it *would* have written to a local JSON file — committing that file
applies those writes for free.

**There is real money sitting on disk right now**: `review_check_dry_run_20260819.json` holds
303 already-paid review results, all of which would change a row.

## How it works
[dryrun_common.py](dryrun_common.py) discovers snapshots and replays them; server.py injects
the Supabase plumbing (`_commit_patch` / `_commit_insert` / `_existing_opportunity_urls`) so
the module has no database dependency and is testable on its own.

- `_patch_updates()` mirrors each agent's live PATCH **column for column**. If an agent's live
  write changes, this must change with it.
- **Two-step commit**: `preview: true` resolves real post-dedupe counts and writes nothing;
  the console shows those in the confirm dialog, so the operator sees "insert 96, skip 20"
  rather than the raw file count.
- A commit logs an `agent_runs` row with `mode = "snapshot-commit"`, `cost_usd = 0`. It pairs
  with the original `-dryrun` row: dry run has the cost and no row counts, commit has the row
  counts and no cost. Neither figure double-counts.

## Traps found while building this
- **The scraper's snapshot is written on live runs too**, not just dry runs — so it can name
  rows that already exist. Every insert is deduped by normalized URL against the whole table.
- **The dedupe set must paginate.** A single `limit=5000` request returns 1000 rows (PostgREST's
  cap), which would have silently let ~390 rows' worth of duplicates through. Verified: the
  paginated helper returns 1390 URLs, the naive request returns 1000.
- **Snapshot filenames carry a date, not a timestamp**, so a same-day rerun overwrites the
  file. Discovered concretely: `scrape_review_national_20260820.json` and the 113 DB rows
  tagged `scraper-national-20260820` overlap by only 20 — they are different runs from the
  same day. Do not assume a snapshot maps to a particular `agent_runs` row. Not fixed (it
  would mean touching all four agents' filenames); called out in the console's own copy.
- File mtime is useless for snapshot age here — a git checkout rewrites every mtime to "now".
  Staleness is judged from the `YYYYMMDD` in the filename instead.

## Feature 2 — Review queue
New third tab. Lists `is_active = false` rows with source filter, select-all, per-row link out,
and an "Activate N selected" button behind a confirm that says the rows become visible to
students immediately. 113 rows are waiting, all from `scraper-national-20260820`.

- **Nothing in this repo ever activates a row automatically** and that stays true: activation
  takes an explicit id list, there is no "activate all matching" endpoint.
- `active: false` reverses a mistake without needing a database console.
- Both paths bust `_opportunities_cache`, or the operator activates a row and then cannot see
  it in the app for the cache TTL.

## Verification done
- Snapshot discovery, preview counts, and path-traversal rejection (`../server.py`,
  `C:/Windows/win.ini`, bare `server.py` all refused).
- Preview against three snapshots of both kinds — no writes.
- Activation **round-tripped on one real row** (`ec18523` → active → back to inactive) to prove
  the write path. The catalog is unchanged: still 113 pending.
- Console: all three tabs, snapshot table, selection wiring, confirm copy, no console errors.
- **No snapshot has been committed.** That would insert 96 rows and is the operator's call.

## Next steps
1. Decide whether to commit `review_check_dry_run_20260819.json` (303 already-paid updates).
2. Work the 113-row review queue.
3. Consider adding a timestamp to snapshot filenames so same-day runs stop overwriting.
4. ~~The review queue has no reject/delete~~ — **done 2026-08-22**, see the current thread.
   Reject is now available (non-destructive and reversible); delete still is not.
5. Still uncommitted, along with everything from the prior threads.

## Files changed this thread
New: `dryrun_common.py`.
Modified: `server.py` (commit + activation helpers, four endpoints), `admin_console.html`
(snapshot card, Review queue tab, confirm copy), `CLAUDE.md`.

---

# PRIOR THREAD: Per-user cost attribution in the admin console

## Goal
Extend the admin dashboard so spend can be read **per user** — what each account's own
interaction with the product costs in AI, next to the $9.99/month plan it is supposed to be
covered by.

**Status: built and verified, blocked on one manual DDL step.** Nothing is committed.

## The one thing left to do
Run [user_costs_schema.sql](user_costs_schema.sql) once in the Supabase SQL editor, then
restart with `restart_server.ps1`. Until then the server logs one warning, attribution is
off, and the console's card shows the setup step instead of numbers — everything else on the
page is unaffected. Nothing else is pending.

## The central invariant — do not break this
`user_costs` is a **breakdown of interactive spend, not a second ledger.** Every dollar in it
is already counted in `agent_runs`' `interactive_gemini` / `interactive_claude` rollups, or in
`deadline_check_log`. `record_interactive_cost()` computes the cost **once** and writes both,
specifically so the aggregate and the per-user split cannot drift. Never add the two together
— this repo has already been burned twice by double-counting.

The residual (`interactive_total - attributed`) is real and is reported as **unattributed**:
spend from calls that arrived with no userid (signed-out, pre-login). It is shown with an
attribution rate rather than spread across users, on the same principle as showing interrupted
runs as `unknown` instead of `$0`.

## What was built

**Storage** — `user_costs`, one row per `(userid, UTC day, surface, feature)`. RLS enabled with
no policies, same posture as `users`: per-user spend is operator data and must never be
reachable from the browser. Read-then-PATCH, not a PostgREST upsert — upsert *replaces* a
conflicting row and these counters must *add*.

**Feature classification is server-side** (`classify_feature()` / `_FEATURE_SIGNATURES` in
server.py), pattern-matching the system prompt with the same signatures `generate_mock_text()`
already uses. This was chosen over having the client pass a label so that **no script.js call
site had to change**. Verified against every `const system = ...` literal in script.js: all 13
classify to a named feature, none fall into `other`. Two prompts needed signatures that the
mock list didn't have (`tag_intent`, `tag_suggestions`), and the ranking prompt needed its
opening line as a signature because `Rank the best 10-12 matches` only appears on one of its
two `selectionRule` branches. **Adding a new AI feature means adding a signature here, or its
spend silently lands in `other`.**

**Endpoint** — `GET /api/agents/user-costs?days=&limit=`, localhost-guarded with the rest of
`/api/agents/*`. That guard matters more on this route than the others: the response carries
every account's name, email and subscription status next to what they cost.

**Console tab** — the console gained a top-level view nav (`.viewtabs` / `showView()`): *Agents*
holds everything that was already there, *Cost per user* is the new view. It shows attributed
vs unattributed with an attribution rate, active users, avg and worst cost per user, a count of
accounts whose AI cost exceeds the plan price, a stacked by-feature bar, and a sortable
per-user table (cost, cost/call, % of $9.99, last active) with an expandable per-user
surface/feature breakdown. Accounts over the plan price are highlighted in red. The tab itself
carries the attributed total as a badge (blank, not `$0.0000`, before anything is attributed).

`.vtab` is styled deliberately unlike `.tab`: `.tab` is the inline pill inside a card head that
filters one table, `.vtab` swaps the whole page, and they should not read as interchangeable.
Copy that said "estimated spend **above**" was updated — nothing is above it any more.

**Two gaps found and closed along the way** (both were under-reporting, not cosmetic):
1. **Resume / LinkedIn import was entirely uncosted.** `_extract_profile_from_text()` called
   `call_claude()` and discarded the usage block, so those were real Anthropic charges that
   appeared in **no figure anywhere** on the console. Now rolled into `interactive_claude`.
2. Cached / mock / stale-fallback deadline checks are deliberately **not** attributed — they
   make no API call. Only the request that actually paid to populate the cache is charged.

**Two endpoints gained a `userid`**, passed on the query string because one is a GET and the
other is multipart: `/api/opportunities/<id>/deadline?userid=` and
`/api/extract-from-resume?userid=`. Both dispatch branches had to move off exact-`self.path`
comparison to survive it — the same trap the `/api/agents/*` routes already carry a comment
about. script.js got one shared `costAttributionQS()` helper next to `currentUser`.

## What worked
- **Asking what "user cost" meant before building.** The obvious reading (an end-user-facing
  usage page) was not what was wanted; one question saved building the wrong thing.
- **Deriving both figures from a single cost computation** rather than costing twice. It makes
  the aggregate/breakdown consistency structural instead of something to remember.
- **Testing the classifier against the real prompts in script.js** rather than trusting the
  signature list. That is how the three `other` cases surfaced.
- **Verifying the populated UI by injecting a fixture into `S.userCosts` in the browser** and
  re-rendering — full coverage of the table, expansion, sorting and the over-plan highlight
  without needing the table to exist or spending a cent.

## Traps hit
- `_qs()` takes a **parsed** `parse_qs` dict, not a raw query string.
- `_users_request()` **raises** on failure (unlike `_supabase_request()`, which returns None).
  The account-decoration lookup is wrapped so losing it degrades the card instead of failing it.
- Adding a query string to a route matched by exact `self.path` equality silently 404s it.
- `money()` keyed its precision off the raw value, so a negative margin rendered as
  `$-1.4300`. Now keyed off magnitude, printing `-$1.43`.
- Screenshots of the Browser pane fail when the pane isn't displayed; DOM reads via
  `javascript_tool` verified everything instead.

## Next steps
1. Run the SQL, restart, then use the app signed in for a few minutes and confirm rows appear
   and the attribution rate is sensible.
2. Watch the `other` feature slice — if it grows, a call site is missing a signature.
3. `user_costs` has no retention policy. It is a daily rollup so growth is slow, but at some
   point old days should be trimmed or aggregated.
4. Still uncommitted, together with everything from the prior thread.

## Files changed this thread
Modified: `server.py` (attribution recorder, feature classifier, `get_user_costs()`,
`/api/agents/user-costs`, resume/LinkedIn costing, two route-matching fixes),
`admin_console.html` (Cost per user card + renderers, `money()` fix), `script.js`
(`costAttributionQS()` + four call sites), `CLAUDE.md`.
New: `user_costs_schema.sql`.

---

# PRIOR THREAD: Admin console for the four background agents

## Goal
An operations dashboard at `/admin` to run and monitor the four offline agent scripts —
cost, rows updated, rows created, errors per run — with per-run configurability (sample,
dry run, zero-cost preview), editable timing, and editable scraper search angles.

**Status: complete and verified against live data.** Everything below works end to end in
the browser. Nothing is committed yet.

## Guardrails (carried forward, still in force)

- **Never run an agent without fresh explicit per-instance approval in chat.** All four
  scripts hit paid APIs. Building a button is not authorization to press it. `--preview` is
  the only free path and is safe to run unprompted.
- **Restart the dev server only via `restart_server.ps1`.** Never Bash `&`. See the
  server-restart section below — this has burned two sessions now.
- **Never copy `admin_console.html` back from the scratchpad** — that once silently reverted
  real wired-up code to an older mock version.
- Verify a script's real `argparse` before wiring any flag; guessed flags previously made
  every run fail.

---

## What exists now

### The four agents (authoritative mapping — also in CLAUDE.md)

| UI key | Script | `agent_runs.agent` | Display name | Web search |
|---|---|---|---|---|
| `scraper` | `scrape_opportunities.py` | `scraper` | New Opportunity Scout | Gemini |
| `metadata` | `refresh_opportunities.py` | `metadata_refresher` | Update Opportunity | no |
| `deadline` | `check_deadlines.py` | `deadline_checker` | Deadline Checker | Claude |
| `reviews` | `check_reviews.py` | `review_checker` | Review Checker | Gemini |

**The bug that shaped this thread:** the previous console had three cards, and the one
labelled "Refresh Agent — Update metadata" actually executed `check_reviews.py` (the
*reputation* checker) and read `review_checker` rows. The real metadata refresher,
`refresh_opportunities.py`, was wired in nowhere and **wrote nothing to `agent_runs`** — no
cost, no history, no metrics. Now fixed.

Two traps that remain live in the data:
- **The scraper's `items_processed` counts SEEDS, not rows.** Never sum it with the other
  three agents' row counts. `AGENT_CONFIGS_SCHEMA[key]["unit"]` encodes this.
- **`name` is a display label only**; the dict key and `db_agent` are the real identifiers.
  **Dict order in `AGENT_CONFIGS_SCHEMA` IS the UI order** — it drives cards, chart legend
  and stacking, timing table, and history filter together.

### Three run tiers — only one is free
- **`--preview`** — resolves scope, prints a `PREVIEW_JSON:` line, exits before the first
  API call. Zero cost, zero writes. Shared plumbing in `agent_common.py`.
- **`--dry-run`** — **still calls the paid API at full cost**; skips DB writes only.
- neither — full run.

### Editable timing
`--min-delay` / `--timeout` on all four agents, three layers: module default → env var →
flag. Overrides are edited inline in the console's timing table and persist to a gitignored
`agent_settings.json` (chosen over a Supabase table: these are per-machine operational knobs
that must be readable before any network call). Everything reads through `agent_defaults()`
in server.py, so an edit reaches the config endpoint, the argv builder, and the duration
estimator at once. Sub-5s values save but raise a "429 risk" flag.

### Editable scraper angles
Live in Supabase `scraper_seeds` (**table created and populated — 48 seeds: 40 national, 8
Seattle**). `seeds_common.py` loads them, falling back to the hardcoded
`NATIONAL_SEEDS`/`SEATTLE_SEEDS` literals if the table is unreachable, logging loudly which
it used. Per-angle lifetime yield totals accumulate (`total_added`, `total_cost`, …) so
angles can be ranked by **added-per-dollar** and retired. Select with `--seed-ids` (stable);
`--seed-indices` is deprecated because positions shift on add/delete.

### Cost accounting — the honest picture
The user correctly challenged an early "$9.41 spend" figure. It was an undercount. Now:

- **Dry runs are logged and counted as spend.** They previously skipped the `agent_runs`
  insert entirely while paying full API cost — the biggest hole. Marked by a `-dryrun`
  suffix on `agent_runs.mode` (deliberately chosen over a new column so no migration was
  needed). Cost counts; would-have-been row counts don't.
- **Interactive app calls are costed.** `/api/messages` and `/api/messages-claude` discarded
  their usage block. Now rolled into one `agent_runs` row per surface per UTC day
  (`interactive_gemini` / `interactive_claude`), priced with the correct provider's rates.
- **`deadline_check_log` is folded in** via `fetch_deadline_check_cost()` — that was $2.86
  invisible. Deliberately NOT also rolled into `agent_runs` (would double-count).
- **Interrupted runs show `unknown`, never `$0`.** Five such runs exist in history.
- KPI relabelled **"Estimated spend"** with an agents-vs-app split. 7-day total went
  $9.41 → **$12.27** once the gaps closed.

**Still uncaptured, and stated in the UI rather than hidden:** local estimation isn't
provider billing; a client-side timeout bills server-side and is never seen; runs that die
before reporting contribute $0.

### Billed-spend reconciliation (researched against live docs, not memory)
- **Anthropic: possible but needs a different key.** `GET /v1/organizations/cost_report`
  returns real billed cost. Needs an **Admin key** (`sk-ant-admin…`, read from
  `ANTHROPIC_ADMIN_KEY` in `.env`) or an `org:admin` OAuth token — a regular `sk-ant-api`
  key returns 401, and **the Admin API is unavailable to individual accounts** (an
  organization must be set up in Console first). Amounts return in **cents** as decimal
  strings (easy 100× bug — handled). Integration is built: drop the key in `.env` and the
  card switches from a note to live figures, no code change.
- **Google: not possible.** The Gemini API exposes no billing endpoint at all; the separate
  Cloud Billing API can't be authenticated with an AI Studio key. Gets a dashboard link.
- Dashboard links: `https://platform.claude.com/cost` and `https://aistudio.google.com/spend`.

### Other server work
- **Live streaming output.** `run_agent_subprocess()` used `proc.communicate()`, which
  blocks until exit — for a 100-minute pass the console showed nothing, then discarded the
  output. Now read line by line into a ring buffer plus `agent_logs/<agent>_<stamp>.log`,
  served incrementally by `GET /api/agents/log?agent=&since=`.
- **Localhost guard** (`_require_local()`) on `/admin`, `/api/agents/*`, `/api/seeds` — the
  server binds all interfaces and these routes spend money. Verified: blocked over LAN IP,
  main app still serves.
- **Batched status.** `/api/agents/status` made one Supabase round-trip *per agent per poll*
  on a 3s forever poll. Now one cached query for all four; poll backs off to 20s when idle.
- Query-string routes must use `urlparse(self.path).path` — the dispatch chain compares
  `self.path` by exact equality, so `?agent=…` would otherwise 404.

---

## What worked

- **Auditing the data before trusting a number.** The user's "are you sure the costs are
  correct?" was right, and querying `agent_runs` / `deadline_check_log` / `conversations`
  directly found four distinct blind spots. Don't defend a figure — go check it.
- **Verifying provider capabilities against live docs** rather than answering from memory.
  The Admin-API-key requirement and the "unavailable for individual accounts" restriction
  are both things a confident wrong answer would have missed.
- **Using `agent_runs.mode` suffixes and daily rollup rows** to add dry-run and interactive
  cost tracking with **zero schema migrations** — the user was already blocked on one manual
  DDL step, and adding more would have stalled the work.
- **`--preview` as a free verification path.** Every agent could be exercised end to end
  without spending anything, which is what made iterating on this safe.
- Reading each script's real `argparse` before wiring flags (the prior session's lesson held).

## What didn't work / traps to avoid

- **The server-restart problem, which is worse than it looks.** Three separate failure modes,
  all hit this session:
  1. Git Bash `pkill` **cannot see native Windows python processes** — they're `python3.13`,
     not `python`. Kills silently match nothing and the old process keeps port 8000, serving
     stale code while every "restart" appears to succeed. A zombie from a *previous* session
     (PID 26788, ~8h old) was still running at the start of this one.
  2. The WindowsApps `python.exe` is an **alias shim**: the PID `Start-Process` returns is
     the launcher; a *child* holds the socket. Killing the recorded PID kills the wrong one.
  3. **PowerShell scalar/array trap**: `Get-ListenerPids` returns a bare int for a single
     listener, so `$targets += [int]$recorded` did integer *addition* (7960 + 7960 = 15920)
     and the script tried to kill a PID that never existed, reporting no error. **The `@()`
     around that call in `restart_server.ps1` is load-bearing — do not remove it.**
  `restart_server.ps1` now handles all three and is verified idempotent across consecutive
  restarts. Use it.
- **`curl` on Windows sends CP1252, not UTF-8.** A seed angle containing an em-dash failed
  via curl but works fine from the browser. Don't conclude a bug from a curl-only failure —
  but do check the error message is truthful (this one wasn't; fixed).
- **Don't trust a test's own expectation over the data.** A preview check "failed" (9 seeds
  instead of 8) because a leftover test row was still enabled — the code was right.

---

## Next steps

1. **Nothing is committed.** The working tree holds all of this plus pre-existing
   uncommitted `index.html`/`script.js` resume-import work and untracked
   `SUBSCRIPTION_*.md` / `subscription_common.py` from another thread. Review before staging
   — do not blanket `git add`.
2. **Data-quality issue the new dashboard surfaced:** 2,136 errors across 29 runs — **38% of
   runs failed**, concentrated in the Review Checker (1,524 errors / 11 runs). This was
   invisible before. Worth investigating what those errors actually are before spending more
   on that agent.
3. ~~**`check_reviews.py` carries a TEMP filter**~~ — **removed 2026-08-22**. The stated
   reason (that it caused the 0-row preview) turned out to be wrong; see the current thread.
4. **Yield columns are all zero** until the scraper runs for real. After the first run, sort
   the angles table by added-per-dollar to find dead weight.
5. ~~`claude_common.py`'s `MODEL` is `claude-sonnet-4-6` while `check_deadlines.py` pins
   `claude-haiku-4-5-20251001`~~ — **resolved 2026-08-22**: Haiku everywhere. It was not
   cosmetic; see the current thread for the two costing bugs it was hiding.
6. **Scheduling remains deferred** by choice. The run pipeline was built so a scheduler can
   call the same `run_agent_subprocess()` path later without rework.
7. **SECURITY, unrelated but unresolved:** the git remote URL still contains a live GitHub
   PAT in plaintext (`https://bluefeather78:github_pat_11BNM7JAA0…@github.com/...`).
   `HANDOFF.md` claimed this was sanitized in an earlier session; it was not. Revoke at
   github.com/settings/tokens and reset the remote to the bare HTTPS URL.

## Files changed this thread

Modified: `server.py` (largest — agent schema, preview/log/summary/billed/seeds endpoints,
streaming runner, timing overrides, localhost guard), `admin_console.html` (full rebuild),
`gemini_common.py`, `claude_common.py`, `check_deadlines.py`, `check_reviews.py`,
`refresh_opportunities.py`, `scrape_opportunities.py`, `.gitignore`, `CLAUDE.md`.

New: `agent_common.py` (preview/timing CLI plumbing), `seeds_common.py`,
`migrate_seeds_to_supabase.py`, `scraper_seeds_schema.sql`, `restart_server.ps1`.
Gitignored runtime files: `agent_settings.json`, `agent_logs/`.

---

# PRIOR THREAD: Gemini migration for the 3 offline agent scripts (2026-08-18 to 2026-08-19)

*(Preserved from before — still has open next steps, e.g. `check_deadlines.py`/
`check_reviews.py` had not been live-tested under Gemini as of this writing. Not touched
during the admin-dashboard session above.)*

## Goal (at the time)
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

## Next Steps (Gemini migration thread)
1. **Immediate, in-progress at the time**: finish strengthening `SYSTEM_BASE` in
   `scrape_opportunities.py` (~lines 111-139) with an explicit imperative instruction forcing
   Gemini to actually invoke `googleSearch` before answering (e.g. "You MUST call the web
   search tool at least once before answering — do not rely on training data alone"). Then
   **re-test on a seed that previously showed 0 searches** (e.g. index 1 or 4) to confirm the
   stronger wording actually changes behavior — this technique is unvalidated so far.
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

## Rate limiting & 429 error handling & parallel execution prevention (2026-08-19, v2)
**Comprehensive quota management** for web-search scripts (was hitting HTTP 429 errors):
- **Enforced 5-second minimum delay between ALL Gemini API calls** (per Gemini's documented policy).
  Implemented via module-level rate limit enforcer in `gemini_common.py` — all callers get this
  automatically with zero code changes.
- **Simplified 429 error strategy**: on rate limit error, retry exactly once then abort. No more
  exponential backoff with 5 retries — calling scripts now see a clear "this item failed, continue
  to next" signal. Failed items are logged and the batch continues.
- **Parallel execution prevention**: lockfile mechanism ensures only one web-search-enabled script can run
  at a time (they share Google Search quota). On first `call_gemini(..., use_web_search=True)`, an
  exclusive lock is acquired; fails fast if another instance is running. Lock auto-releases on exit or
  after 24 hours if stale.
- **Removed loop-level throttles** from `check_reviews.py` and `check_deadlines.py`. Rate limiting is
  now enforced at the API call level, making per-item explicit sleeps redundant.

## Future optimization: consider model swap for batch scripts
**Evaluated 2026-08-19** (deferred, keeping as-is for now): Currently using `gemini-3.6-flash`
(reasoning model with internal thinking tokens) for all three batch scripts (`scrape_opportunities`,
`check_deadlines`, `check_reviews`). Investigated whether `gemini-3.5-flash-lite` (already in use
for interactive UI calls) could be a viable, cheaper alternative. **Key finding**:
`gemini-3.6-flash-lite` does not yet exist (confirmed against live API 2026-08-18). If/when it
ships, it should be the first target to test. Before then, **could consider**:
- **`check_deadlines.py`**: Most straightforward task (structured extraction: status, deadlines, dates).
  Already uses similar extraction logic in UI via `gemini-3.5-flash-lite` successfully. **Candidate
  for trial swap** to save ~$0.01/item across 1200+ rows (~$12/full pass).
- **`scrape_opportunities.py`**: Higher judgment/reasoning (relevance + legitimacy assessment). Likely
  benefits from 3.6's reasoning tokens. **Keep as-is**.
- **`check_reviews.py`**: Borderline (search + reputation judgment). Could test but worth keeping 3.6
  if 3.6-lite ships. **Keep as-is for now**.
Not implemented yet — only run the swap if you see unnecessary token overhead or cost pressures in 3.6
runs, or once 3.6-flash-lite becomes available.
