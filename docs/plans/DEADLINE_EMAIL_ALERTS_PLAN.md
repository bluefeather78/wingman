# Deadline email alerts — plan

**Status: PLANNING ONLY (2026-08-26). Nothing here is built.**

This is F1's "deadline reminders" half from `DEADLINE_AND_TASK_PLAN.md` §13a, promoted to
its own plan as that section said it would be. The other half of F1 — change detection
("your deadline moved Jan 1 → Dec 15") — is **explicitly out of scope here**: it needs a
per-user last-seen snapshot the repo has no substrate for (there is no event log; see the
user-metrics notes), and bolting it onto this feature would hold a simple, high-value email
hostage to a net-new storage design. This plan is only: *a student who tracked an
opportunity gets an email before its deadline passes.*

## 1. Why this is the next thing the app owes its users

The app's whole promise is "find and track opportunities so you never miss one." Today every
surface that serves that promise is **pull**: the Quest Log shows deadlines if the student
opens it, the Google Calendar sync shows them if the student looks at the right calendar
(and the calendar section of CLAUDE.md documents how often "it isn't syncing" is really "I
didn't look there"). A high schooler who tracked a program in September and got busy in
October misses the November deadline with the app having been *right about the date the
whole time*. That is the app failing at its one job while every metric reads green.

Email is the correct first push channel, not native push: the Resend pipe, the claim table,
the opt-out, the templates, the sweep endpoint and the (disarmed) cron **all already exist**
for the three lifecycle emails. This feature is one new email `kind` riding proven rails —
and it is **free** (no model call anywhere in the path; Resend volume at this roster size is
nothing).

## 2. What gets alerted — scope rules, each with its reason

- **Tracked items only** — the Quest Log (`hs-tracker-data`), never the whole catalog. The
  catalog is 1200+ rows; the tracker is the student's own statement of intent. An email
  about something they never tracked is marketing, which this system deliberately is not
  (see the roster-privacy reasoning in `app/services/email.py`'s docstring — most users are
  minors).
- **Exclude saved-for-later** (`hs-tracker-saved[id] === true`). The student explicitly
  parked it; the metrics view already refuses to count these as actively tracked
  (`tracked_1`/`tracked_3` exclusion), and alerting on them would contradict that same
  judgment.
- **Exclude `status: not_running`** — the identical rule `collectTrackedDeadlineEvents`
  and every reader in `status.ts` follow. `cycleYearShift` deliberately does not project a
  next cycle for a discontinued program, so those rows carry REAL past/dead dates; the
  calendar sync already learned this the hard way (dead deadlines on a student's real
  calendar).
- **`deadline`-type dates only in v1.** `ImportantDate.type` is
  `opens | deadline | event_start | event_end | other`. The deadline is the date that can
  be *missed*; an "applications just opened" nudge is a genuinely good v2 (it is what the
  whole missing-opens-date backfill work was for) but it is a different message with a
  different tone, and shipping one kind first keeps the template, the dedupe semantics and
  the copy review small. `event_start` reminders ("your program begins Monday") are v2 for
  the same reason.
- **Estimated dates ARE included, always labeled.** A projected deadline is exactly the
  case where a nudge to go verify is most valuable — but the email must say
  "(estimated — confirm on the program's site)" wherever the app would say "Predicted dates
  from past cycle". Same per-date `estimated` flag the cards render (OR'd with `projected`
  like `getDisplayMilestones` does); a date with the flag absent (pre-2026-08-24 rows) is
  *unknown*, and unknown is labeled like estimated, never like confirmed — the standing
  rule.
- **User-added / slug-only items are included.** Their dates came from the student or from
  `extractTrackerInfo`; they live in the same tracker shape. The student typed it in
  precisely because they care about it — excluding it because it lacks a catalog row would
  punish the most engaged behavior in the app.
- **Past dates never alert.** Obvious, but stated because the failure mode ("your deadline
  was 3 days ago") is the single most trust-destroying email this system could send. Strict
  `days_left >= 0` on the UTC date.

## 3. Cadence: a rung ladder, computed to be self-healing

Reminder rungs: **T-7, T-3, T-1** (constants, e.g. `DEADLINE_ALERT_RUNGS = [7, 3, 1]`).
The F1 sketch said T-14/T-3/T-1; 14 days is dropped for v1 — at two weeks out the useful
message is "start your application," which is the action-items feature's job, and a
4-email ladder doubles the inbox load for marginal value. Add T-14 later if the T-7 open
data says people needed more runway.

**Window assignment, not day-exact firing.** Each sweep, for each eligible future date,
compute `days_left` (UTC date arithmetic — the dates are date-only ISO strings) and assign
it to the **smallest rung ≥ days_left**: days 4–7 → rung 7, days 2–3 → rung 3, days 0–1 →
rung 1. Fire the alert for that rung if it has not been claimed. This makes the system
self-healing in exactly the ways a day-exact `days_left == 7` check is not:

- **A missed cron day cannot skip a reminder.** If the sweep doesn't run at T-3, the item
  is still in the rung-3 window at T-2 and fires then.
- **An item tracked late gets exactly one catch-up alert, not the whole backlog.** Tracked
  at T-2, it lands in the rung-3 window and fires once; rung 7 is simply never its window,
  so there is no "3 stale reminders on day one" burst.
- **A deadline that moves re-arms naturally** — see the dedupe key below.

## 4. One digest email per student per sweep, claims per reminder-unit

**The email is a digest** — one message listing everything currently due an alert, grouped
by urgency — never one email per deadline. A student with five programs closing the same
week must not get five emails in one morning; that is how the unsubscribe link gets
clicked, and this system's opt-out (correctly) kills *all* lifecycle email at once.

**But the claim is per reminder-unit**, not per digest:

    kind        = "deadline_alert"
    dedupe_key  = f"{item_id}:{date_iso}:{rung}"

- One `email_sends` claim row per (opportunity, deadline date, rung), all the units for one
  student claimed first, then **one** Resend call carrying every unit whose claim
  succeeded. Units that lose the claim race (already sent by an earlier run that day) are
  silently dropped from the digest — the healthy path, exactly like the trial sweep's
  `skipped`.
- **The date is in the key on purpose** — the same reasoning as `trial_dedupe_key`: a
  deadline that MOVES mints new keys, and a moved deadline genuinely deserves fresh
  reminders (the student's mental model is now wrong). A deadline that stays put can only
  ever fire each rung once, no matter how many times the sweep runs.
- If the single Resend call fails, `_finish(failed)` every unit-row from that digest;
  they are then never auto-retried, matching the existing stuck-row philosophy (a visible
  gap beats a possible duplicate).
- `welcome`/`goodbye` use `''` for the key and this uses a rich key — both live happily
  under the same three-column unique constraint. **No schema change is needed**:
  `email_sends.kind` is free text *specifically* so a fourth kind needs no migration
  (../../db/email_schema.sql says so), and the claim/opt-out/mock-mode machinery is kind-agnostic.

**Volume guard inside the digest:** sort by `days_left` ascending, render at most ~10
items, close with "and N more in your Quest Log." An email that scrolls forever is read as
noise; the app is the place for the full list.

## 5. Where the data comes from, and the one real engineering risk

The sweep runs server-side, but tracked deadlines live in **client-owned state**: the
`users.data` jsonb, key `hs-tracker-data`, whose value is a JSON **string** of the 6-bucket
object (`ALL_BUCKETS`), each item carrying `info.important_dates` /
rebuilt-`importantDates` plus `status`. Nothing in `app/` parses this today — the calendar
sync's `collectTrackedDeadlineEvents` does it in TypeScript on the client.

So the core new module is a **Python reader that mirrors the client's rules**
(`app/services/deadline_alerts.py`, with the extraction logic kept pure and separately
testable):

- Parse defensively: the shape is written by whatever bundle version the student last ran.
  Missing keys, string-vs-object drift, malformed dates → skip the item, count it in the
  sweep summary as `unparseable`, **never** throw out of the sweep. One student's corrupt
  blob must not stop everyone else's reminders.
- Mirror `status.ts` / `collectTrackedDeadlineEvents` semantics exactly: `not_running`
  skip, saved-for-later skip, per-date `estimated`/`projected` OR.
- **Pin it with a fixture**: a real (anonymized) `hs-tracker-data` payload checked into
  `tests/fixtures/`, asserted item-by-item — the same move `pair_resolution_20260826.json`
  makes for the scraper. This is the drift guard: the client shape and the Python reader
  are two implementations of one contract, and the fixture is the contract's test. Any
  future change to the tracker shape must update the fixture, which is what surfaces the
  server-side impact.

**Selection is a full-roster scan** — read every `users` row's `data` and parse. At the
current account count (~15) this is trivially fine and honest; there is no index of
user→deadline and building one (a derived table refreshed on save) is premature. Note the
read must select `data` (the expensive column `get_user_account()` deliberately omits) —
use the existing full-row path, paginate like every other whole-table read here, and put a
`limit` on the sweep like `run_trial_sweep` has.

**Staleness is accepted and out of scope.** The email reports what the Quest Log itself
shows. The stored date may be up to 7 days stale (the TTL) or older (the student hasn't
opened the app — which is exactly when this email fires). The sweep must **never** trigger
paid deadline re-checks: it runs unattended, and "no agent spend without fresh explicit
approval" is a standing rule. Confidence/proximity-aware freshness is the parked item 2 in
DEADLINE_AND_TASK_PLAN §13, and this feature is the consumer that will eventually justify
it — but v1 mirrors the app, staleness included. The template mitigates: every item links
to the opportunity in-app, where opening it fires the on-demand (user-triggered, cached)
check.

## 6. Who gets it — eligibility and consent

- **`lifecycle_email_optout` is honoured**, like all kinds, minors-first reasoning
  unchanged. One boolean covers everything in v1; per-kind preferences ("remind me about
  deadlines but not billing") are a real v2 want but need a settings UI, a schema column,
  and copy — deferred, noted here so nobody rediscovers it.
- **Only accounts with access** (`subscription_state().has_access`). A lapsed student
  cannot open the Quest Log the email points into; "deadline in 3 days" + a paywall on
  click-through reads as ransom. (The counter-argument — it is the most compelling
  resubscribe nudge that exists — is real, but a reminder email that doubles as a win-back
  is precisely the transactional/commercial line the goodbye email refuses to cross. Same
  call here.) Fail the same direction as the paywall: Supabase error while checking →
  skip, never guess.
- **The school-address gap applies unchanged**: the account email may be a school domain
  that blocks outside mail, `email_sends.email` records what was used, and that is the
  first thing to check on "I never got it." No new mitigation in v1 (no editable
  alert-address field yet); stated so it is a known gap rather than a discovered one.
- Legal check before shipping: this is transactional (the student tracked the item; the
  email is the tracking feature working) and adds **no new data flow** — Resend still gets
  one address at a time at send time. Expected result: no `legal/*.md` change and no
  `TERMS_VERSION` bump. But do the read of `legal/privacy.md` §6-ish deliberately and
  record the conclusion; if any copy drifts toward "discover more opportunities," that's
  marketing and the answer changes.

## 7. The email itself

New kind in `app/services/email_templates.py` (`EMAIL_KINDS` + a renderer), under every
existing constraint: table layout, inline styles, no images, no tracking pixel, no
click-wrapping, text/plain part, `EMAIL_POSTAL_ADDRESS` footer, unsubscribe link.

- **Subject carries the urgency and the count**, most-urgent first:
  "1 deadline tomorrow — Bank of America Student Leaders" /
  "3 deadlines coming up this week". The subject is the only line most students read.
- **Body groups by rung**: "Due tomorrow" / "Due in 2–3 days" / "Due this week". Each item:
  program name, org, the date with its label, "(estimated — confirm on the program's
  site)" where the flag says so, and a link into the app
  (`{EMAIL_APP_URL}/tracker`-equivalent route). `EMAIL_APP_URL`'s loopback-refusal guard
  already protects these links in dev.
- **Context comes through `build_context()`** like the other three kinds, so the console
  preview and the real send stay byte-identical (`render_for` is the single renderer).
  `_sample_record()` grows a staged sample tracker payload — dates positioned so the
  preview shows every rung bucket at once, identity values obviously fake, per the
  existing sample philosophy.
- Tone: a helpful teammate, not an alarm. No "LAST CHANCE" — a third of these dates are
  estimates and the app must never be caught shouting about a date it guessed.

## 8. Trigger, scheduling, and the honest dormancy statement

- **Extend `POST /api/email/sweep`** to run both sweeps (trial + deadline) rather than
  adding a second endpoint: one cron, one secret, one workflow, and the response body
  reports each sweep's summary separately. Same `X-Cron-Secret` header (never query
  string), same fail-closed 503 on unset secret, same `verbose`-gated detail so a roster of
  minors' addresses never lands in an Actions log.
- **`wingman/send_lifecycle_emails.py` gains the kind** with the same free tiers: `--preview`
  (who is due, zero writes) and `--dry-run` (resolve + render, send nothing). All tiers
  stay free — no model in this path; what preview protects is inboxes, not money.
- **The cron is disarmed today, and this plan does not re-arm it.** The workflow carries
  `workflow_dispatch` only (deliberate, 2026-08-24), so at ship time this feature fires
  only when someone presses "Run workflow" or runs the local script. Arming the schedule
  is the last step, an explicit operator decision, and is *also* gated on the standing
  §13 disposition ("do not build proactive/scheduled runs until the deadline/task logic is
  proven stable" — this email is a *reader* of stored dates, not a proactive checker, so
  it is arguably outside that freeze, but the operator makes that call, not the code).
  The commented 15:00 UTC slot is right for this too: morning US time, off the
  oversubscribed midnight tick, and a reminder that lands at 8am beats one at 3am.
- **UTC-day arithmetic, stated plainly**: `days_left` is computed on UTC dates, the same
  grain everything else here uses, and the 15:00 UTC run means "due tomorrow" is computed
  ~8am Pacific — for a date-only deadline that is the correct, conservative reading (a
  Pacific student is told "tomorrow" while their whole tomorrow still lies ahead).

## 9. Console (ops) surface

Mostly free: the Emails tab's per-kind counts, recent log, stuck detection, previews and
test send are all kind-generic. Additions:

- **Due-now list for deadline alerts** beside the trial one — who would get a digest if the
  sweep ran now, with their item count. Same `due_error`-not-zero honesty rule.
- **Test send** uses the existing no-claim `send_test` path (its no-dedupe property matters
  even more here — an operator iterating on digest copy must not consume a student's
  rung).
- The sweep summary counts `unparseable` tracker blobs and `skipped_no_access` /
  `skipped_optout` separately — an empty digest run must be distinguishable from a broken
  reader, per the standing "empty and failed look identical" rule.

## 10. Implementation phases

Each phase lands green (`pytest` + `npx tsc --noEmit` clean) and independently shippable;
nothing can send until P4, nothing recurs until P5. All new server code lives in `app/`
(it ships to Render — the sweep must run there, not in localhost-only `ops/`), with
console-only surfacing in `ops/`.

### P0 — Server-side tracker reader (pure, fixture-pinned)

The only genuinely new engineering in the feature: a Python reader of the client-owned
tracker blob. Everything after this phase is assembly of existing parts.

**New file `app/services/deadline_alerts.py`**, starting with pure extraction:

- `extract_deadline_units(record, today) -> (units, stats)` — parses
  `record["data"]["hs-tracker-data"]` (a JSON **string** of the 6-bucket object; each
  bucket an array of `TrackerItem`s) plus `record["data"]["hs-tracker-saved"]`
  (`{id: bool}`). The stored shape, confirmed against
  `frontend/src/api/trackerStore.ts` 2026-08-26: items carry `id`, `name`, `status`,
  `importantDates: [{label, dateISO, type, estimated?, verified?, sourceUrl?}]` — note
  **camelCase `dateISO`**, not the API's `date_iso`; the reader accepts both spellings
  because the blob has been written by every bundle version a student has ever run.
- A **unit** is a plain dict: `item_id`, `item_name`, `date_iso`, `label`, `date_type`,
  `estimated` (tri-state: True / False / None-for-unknown — unknown renders as estimated,
  never as confirmed), `days_left` (UTC date arithmetic against `today`).
- Exclusions implemented here, mirroring `status.ts` / `collectTrackedDeadlineEvents`:
  `status == 'not_running'` skips the whole item; `hs-tracker-saved[id] === true` skips
  the whole item; `type != 'deadline'` skips the date; `days_left < 0` skips the date;
  anything malformed (bad JSON, non-list bucket, unparseable date) is skipped and counted
  in `stats` (`unparseable_blobs`, `skipped_dates`), **never raised** — one corrupt blob
  must not stop the roster sweep.
- **Fixture:** `tests/fixtures/tracker_data_deadline_alerts.json` — an anonymized real
  `hs-tracker-data` payload (taken from the dev test account, names scrubbed) staged to
  cover every rule: a `not_running` item with real dates, a saved-for-later item, an
  `opens`-only item, an estimated deadline, a flag-absent (pre-08-24) deadline, a past
  deadline, a camelCase and a snake_case date, and one malformed item.
- **Tests:** `tests/unit/test_deadline_alert_reader.py`, asserting the fixture
  item-by-item. This fixture is the client/server shape contract — any future tracker
  shape change must update it, which is what surfaces the server-side impact.

*Done when:* the fixture round-trips to the exact expected unit list; no email code
touched; suite green.

### P1 — Rung engine (pure, table-driven)

Still zero I/O — this phase is the semantics of "who is due what today."

- `DEADLINE_ALERT_RUNGS = (7, 3, 1)` in `app/config.py` (beside `TRIAL_REMINDER_DAYS`).
- In `deadline_alerts.py`: `assign_rung(days_left)` — smallest rung `>= days_left`, `None`
  above the ladder (no alert yet) or below zero; `alert_dedupe_key(unit, rung)` —
  `f"{item_id}:{date_iso}:{rung}"`; `due_alerts(units)` — the `(unit, rung)` pairs a sweep
  would claim today.
- **Tests** (`tests/unit/test_deadline_alert_rungs.py`), table-driven over the scenarios
  §3 promises: every boundary (`days_left` 0, 1, 2, 3, 4, 7, 8), the late-tracked item
  (first seen at T-2 → rung 3 only), the missed-sweep day (T-3 unclaimed, swept at T-2 →
  still rung 3), the moved deadline (new `date_iso` → all-new keys), estimated/unknown
  flag pass-through, and the digest ordering (soonest first).

*Done when:* the table passes and the dedupe-key format is pinned by a test (it is about
to become permanent — keys live forever in `email_sends`).

### P2 — Template + preview (reviewable before it can fire)

- **`app/services/email_templates.py`**: add `deadline_alert` to `EMAIL_KINDS` and a
  renderer under every existing constraint (§7): table layout, inline styles, no images,
  no tracking, text/plain part, postal-address footer, unsubscribe link. Subject from the
  most urgent unit + count; body grouped by rung ("Due tomorrow" / "Due in 2–3 days" /
  "Due this week"); per-item estimated label; ≤10 items + "and N more in your Quest Log";
  one link into the app (`EMAIL_APP_URL`).
- **`app/services/email.py`**: `build_context("deadline_alert", record)` runs the P0
  reader + P1 engine over `record["data"]` and hands the template plain display dicts —
  keeping the `render_for(kind, record)` signature intact is what makes the console
  preview and test send work unchanged, and keeps preview == send byte-identical.
  `_sample_record("deadline_alert")` grows a staged tracker blob (one unit per rung
  bucket, one estimated) with obviously-fake identity values, per the existing sample
  philosophy.
- Console: the Emails tab's preview/test-send iterate `kinds` from the API, so the new
  kind appears with at most a label tweak in `ops/admin_console.html`.
- **Tests:** render assertions in `tests/unit/test_deadline_alert_template.py` — subject
  count/urgency, estimated marker present exactly where flagged, text part non-empty,
  unsubscribe URL present, the >10-items truncation line, and (matching
  `test_action_items.py`'s spirit) zero units → renderer refuses rather than sending an
  empty digest.

*Done when:* the operator can open the console, preview the digest against the sample and
against a real account, and test-send it to their own address — with the sweep still
nonexistent.

### P3 — Sweep + endpoints (claim → batch → one send → finish)

- **`due_deadline_alert_digests(limit)`** in `deadline_alerts.py`: paginate the full
  `users` table (this read needs the `data` column `get_user_account()` deliberately
  omits, and must paginate past PostgREST's 1000-row cap like every whole-table read
  here), filter `lifecycle_email_optout`, no-email, and
  `subscription_state(record)["has_access"] is not True`, then run reader + engine per
  account. Returns per-user digests plus roster-level stats.
- **`run_deadline_alert_sweep(dry_run, limit)`** in `email.py` (beside
  `run_trial_sweep`, same result shape + `unparseable` / `skipped_optout` /
  `skipped_no_access` counters). Per user with a non-empty digest:
  1. `_claim(userid, "deadline_alert", key, email, subject)` for **each unit** — units
     answering `already_sent` drop out silently (the healthy repeat-run path);
  2. zero survivors → skip, no send;
  3. re-render the digest **from the surviving units only** (subject count must match the
     body), one `_resend_post`;
  4. `_finish(sent)` every survivor on success; `_finish(failed, error)` every survivor on
     failure — never retried, per the stuck-row philosophy.
  This uses the `_claim`/`_resend_post`/`_finish` primitives directly rather than
  `send_lifecycle_email()`, which is structurally one-claim-one-send; the digest is
  N-claims-one-send. Mock mode (no `RESEND_API_KEY`) writes **no claim rows** and prints,
  same rule and same reason as the module docstring.
- **`app/routes/email.py`**: the existing `POST /api/email/sweep` runs both sweeps and
  returns `{ok, trial: {...}, deadline_alerts: {...}}` — one cron, one secret; `verbose`
  gating and the fail-closed 503 unchanged.
- **`wingman/send_lifecycle_emails.py`** (local runner): gains the deadline sweep with the same
  free tiers — `--preview` (who is due, zero writes) and `--dry-run` (resolve + render,
  send nothing) — and a `--kind trial|deadline|all` selector defaulting to `all`.
- **`email_status()`**: add `deadline_due_now` (userid + unit count per due digest) with
  its own `deadline_due_error`, never a silent zero; per-kind counts/stuck detection are
  already kind-generic.
- **Tests** (`tests/unit/test_deadline_alert_sweep.py`, mocking the Supabase and Resend
  seams the way `test_subscription_gate.py` mocks its layer): partial-claim digest
  (2 of 3 units already sent → email renders 1), all-claimed → no Resend call, Resend
  failure → every survivor `failed`, mock mode → zero `email_sends` writes, opt-out /
  lapsed / no-email exclusions, dry-run makes no writes and no sends, and the route test
  for the combined sweep response + secret gating.

*Done when:* `python -m wingman.send_lifecycle_emails --kind deadline --preview` prints an honest
due-list against local data, and the full suite (currently 945 green) stays green.

### P4 — ~~First real sends, manually~~ DROPPED (2026-08-26)

Dropped by the operator: there are no real users yet, so there is nothing to send a real
sweep to. The build was instead validated against real data without any roster send — the
sweep dry-runs clean against the live roster (13 accounts, 31 deadlines parsed, 0 due), and
a real digest was delivered to the operator's own inbox via the console test-send path. The
"first real sweep" now happens naturally as the first armed run after Beta launch (P5).

### P5 — Arm the cron (READY; held until Beta ship)

Everything is in place and tested; this is now a pure config flip, deliberately NOT done
yet — the operator turns it on when shipping Beta. **Arming checklist (all in
`.github/workflows/lifecycle-emails.yml`, whose header now carries this too):**

1. Run `../../db/email_schema.sql` in the Supabase SQL editor if not already (until then every claim
   fails and nothing sends — by design).
2. Set repository secrets `WINGMAN_API_BASE` and `EMAIL_CRON_SECRET` (Actions), **and**
   `EMAIL_CRON_SECRET` in the Render dashboard (the endpoint 503s without it).
3. Uncomment the two `schedule:` lines (15:00 UTC).

The workflow already POSTs to `/api/email/sweep` with no `kind`, which the endpoint treats
as "all" — so **one flip arms BOTH the deadline alerts and the dormant trial-ending
reminder**. That shared trigger is intentional; just know both start at once.

*Done when (post-Beta):* two consecutive scheduled runs complete with sane summaries and no
duplicate sends.

### On-demand mimic (built 2026-08-26, alongside P0–P3)

The admin console's Emails tab can send **any user's email, of any kind, to an operator
address on demand** — enter a userid + pick a kind + "Send to me". It loads the FULL account
(data included) so a `deadline_alert` mimic renders that student's real tracked deadlines;
a user with nothing in-window returns a clean "nothing due" note rather than an error. Never
deduped, never recorded (same `send_test` guarantees) — it cannot consume a real user's one
send. The destination is remembered per-machine in localStorage.

## 11. Decisions to confirm before building (with recommendations)

1. **Rung ladder T-7/3/1** (vs F1's T-14/3/1) — recommend 7/3/1, add T-14 later on
   evidence.
2. **Lapsed accounts excluded** — recommend yes (paywall consistency over win-back value).
3. **`opens`/`event_start` alerts** — recommend defer to v2 as separate copy.
4. **Estimated-date inclusion** — recommend include-with-label (as planned above);
   the alternative (suppress the T-1 rung on estimates so we never say "tomorrow" about a
   guess) is defensible if the labeling feels insufficient in preview.

## 12. What this is deliberately not

- Not change detection (F1's other half — own plan, needs last-seen storage).
- Not native push (F1's "later" — needs this content model first, which is half the point
  of building this).
- Not a freshness/re-check trigger (parked §13 item 2; this sweep spends $0 forever).
- Not marketing, not a digest of *new* opportunities, not anything requiring a roster sync
  to a third party.
