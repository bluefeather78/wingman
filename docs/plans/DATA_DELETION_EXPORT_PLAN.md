# DATA DELETION & EXPORT PLAN

Two user-facing rights, and the full blast radius to implement them safely:

- **(A) Delete all my data** — a self-serve "delete my account" that erases everything Wingman
  holds about one student, across Supabase, Stripe, and Google Calendar.
- **(B) Export all my data** — a self-serve "download everything you have on me."

Status: **design / not started.** No `/api/account/delete` or `/api/account/export` exists today
(greenfield). Started 2026-09-06.

> **Why we're building this even though we're US-only** (the question that kicked this off):
> "US-only" does not exempt either feature.
> - **Deletion is an App Store / Google Play gate.** Apple Guideline 5.1.1(v) requires any app
>   with account creation to offer in-app account deletion; Google Play requires in-app deletion
>   **plus a web-accessible deletion-request URL**. Geography-independent. We ship to iOS + Android
>   via EAS, so this blocks store review regardless.
> - **US state privacy laws** (CCPA/CPRA, VA, CO, CT, TX, OR, …) all grant delete + access rights.
>   We may sit below their revenue/volume thresholds *today*, but cross them silently as we grow.
> - **Our users are minors.** "We serve high schoolers and cannot delete their data on request" is
>   the worst posture if a parent or regulator asks. This is the deciding factor.
> - **Export** is NOT an app-store gate and is lower legal exposure at our size — but it's nearly
>   free once deletion exists (same auth, same "gather everything for this userid" query), so we
>   build both. If time-constrained: **ship deletion with the store launch; export as a fast-follow.**
>
> Not legal advice — a privacy attorney should review the final Terms/Privacy language, especially
> the minors angle.

---

## 0. Decisions — CONFIRMED 2026-09-06

1. **Deletion model:** ✅ **immediate hard delete after re-authentication**, no grace period, plus a
   PII-free tombstone row for audit. (No soft-delete / `deleted_at` column, so no read-gate changes.)
2. **Re-auth on delete:** ✅ **require the user to re-enter their password**, re-verified server-side
   with argon2 against `password_hash`. Google-only accounts (no `password_hash`) re-auth via a fresh
   Google handoff.
3. **Their submitted opportunities** (`opportunities.submitted_by = userid`): ✅ **anonymize** (set
   `submitted_by = NULL`), keep the catalog row.
4. **Export format:** ✅ **one JSON file**, assembled server-side.
5. **Export delivery:** ✅ **direct download** from the app, **web-first**. Native share path
   (`expo-file-system` + `expo-sharing`) is deferred; native shows "export from the website" for v1.
6. **Google Play web deletion URL:** ✅ reuse the **web app** — user signs in at
   highschoolwingman.com and deletes from the account drawer; document that URL in the Play listing.
   No separate unauthenticated deletion page.

### 0.1 Sequencing vs. events tracking — build this NOW, don't wait

`user_events` tracking is not finished yet. **Delete/export does not wait on it**, and the risk runs
the other way: if events tracking shipped *first* and delete came later, we'd be collecting minors'
behavioral events with no way to delete them in the interval — the exact gap this feature closes.
Building delete/export first makes it the safety net that's ready *before* events starts collecting.
So `user_events` is in the blast-radius map from day one, wrapped in the repo's standard
**missing-table tolerance** (PostgREST `42P01`): export returns `[]` for that source when the table
is absent/empty, delete's DELETE is a no-op. When events tracking lands it is covered with **zero
retrofit** — every other satellite (`user_costs`, `user_activity`, `email_sends`,
`mailing_list_subscriptions`) gets the same defensive handling so a not-yet-migrated table never
breaks a delete or export.

---

## 1. Security model — the "can't touch another user's data" guarantee

This is the whole ballgame, and the good news from the audit is **it's already enforced by the
existing auth layer** — we inherit it for free by hanging both endpoints off `get_current_user`.

- The access token carries **`sub` = userid** and nothing else identity-wise (`app/auth/tokens.py`
  `issue_tokens`). Email is never in the token; it's looked up server-side from the row.
- `get_current_user` (`app/auth/dependencies.py:35`) produces `AuthedUser(id=userid)` **from the
  verified token only.** A caller cannot assert an identity they don't hold a signed token for.
- Owned-data routes already **ignore any `userid` in the request body** and use `user.id` from the
  token (explicit precedent + comment in `app/routes/user_data.py:18-28`).

**Rules for the new endpoints, non-negotiable:**
- Both `/api/account/export` and `/api/account/delete` take **no userid parameter**, ever. The only
  identity is `user.id` from `get_current_user`. An attacker cannot pass `?userid=someone-else`.
- Gate on `get_current_user` (hard 401 if unauthenticated) — **not** `require_subscription`. A lapsed
  account must still be able to export and delete its data (a paywall you can't delete through is a
  data-hostage situation, and stores will reject it). This mirrors why `/api/subscription/*` stays
  ungated.
- **Rate-limit both** (reuse `app/auth/ratelimit`): export assembles a lot of data; delete is
  irreversible. A few per hour per user is plenty.
- **Never log the export payload or the deleted data.** Log only `userid` + action + outcome.
- Delete additionally requires a **fresh re-auth proof** (decision #2), not just a valid session.

---

## 2. Feature A — Export ("download everything")

### 2.1 The data map (what goes in the export)

Everything keyed by this `userid`, assembled into one JSON document:

| Source | What | Notes |
|---|---|---|
| `users` row | profile fields, consent stamps, subscription status/dates, email prefs, `created_at` | **Redact secrets:** never include `password_hash`, `token_version`, `refresh_jtis`, `google_calendar_access_token`/`refresh_token`, raw `stripe_customer_id`/`stripe_subscription_id` (or include only last-4/existence flag). Export is *their* data, not our credentials. |
| `users.data` jsonb | `student-profile`, `hs-tracker-data`, `hs-tracker-saved`, any other keys | The bulk of it — profile text, the 6-bucket tracker, saved-for-later. |
| `user_costs` | their AI-spend rows | Arguably ours, but harmless and honest to include (per-user cost is *about* them). Include summarized. |
| `user_activity` | their `(userid, day)` activity counts | Include. |
| `user_events` | their event log | Include. |
| `email_sends` | which lifecycle emails they got, when, to which address | Include (kind, status, ts, email). |
| `mailing_list_subscriptions` | program mailing lists they joined | Include. |
| `opportunities WHERE submitted_by = userid` | opportunities they submitted | Include id/name/url + status. |

**Not per-user, excluded:** `user_metrics_daily` (aggregate, keyed by day), `promo_codes`/
`deadline_check_log`/`opportunity_signups`/`api_errors` (shared/no userid), `auth_handoffs`
(transient nonces).

### 2.2 Backend

- New service module **`app/services/account_data.py`** with `assemble_export(userid) -> dict`:
  reads each source above via the existing `app/core.py` helpers where possible (`get_user`,
  `get_user_data`) and new narrow reads for the satellites (small PostgREST GETs filtered
  `userid=eq.<userid>`, paginated past the 1000-row cap like the rest of the repo does).
  Applies the redaction list. Returns a plain dict.
- New route in a new **`app/routes/account_data.py`** (or fold into `account.py`):
  `POST /api/account/export`, dep `get_current_user`, rate-limited.
  - Return `application/json` as a **download**: `Content-Disposition: attachment;
    filename="wingman-my-data-<date>.json"`, `Content-Type: application/json`.
  - **Plain `def`, not `async def`** — it does blocking Supabase IO in a threadpool (the app-open
    latency note: an `async def` that does Supabase IO freezes the event loop). It awaits nothing in
    its own body, so `def` is correct.
- Register the router in `app/main.py`'s include list.

### 2.3 Frontend

- `frontend/src/api/ApiClient.ts` — add `exportData(): Promise<Blob>` to the interface.
- `frontend/src/api/httpClient.ts` — implement with the **non-JSON response** precedent
  (`request()` does `res.json()` and won't work for a file). Follow `extractFromResume` /
  `syncCalendar`: call `rawFetch('/api/account/export', {method:'POST'})` (bearer auto-attached),
  do the manual 401→refresh-once→retry, then `await res.blob()`.
  - **Web:** `URL.createObjectURL(blob)` + a hidden `<a download>` click.
  - **Native:** write to FS via `expo-file-system` and hand to `expo-sharing` (share sheet). If those
    libs aren't already deps, either add them or, for v1, gate export to web + show "export from the
    website" on native.
- Entry point: a **"Download my data"** button in the account `RightDrawer`
  (`frontend/src/ui/NavBar.tsx`, in the Account box) and/or a "Your data" section on Manage Plan
  (`subscription.tsx`). Show a spinner while assembling; it's one request.

---

## 3. Feature B — Delete ("erase everything")

### 3.1 The erase map (everything a single-user delete must touch)

Ordered by **sequencing** (see §3.2 for why the order matters):

1. **Stripe** — if `stripe_subscription_id` present, **cancel immediately** (not period-end; they're
   leaving). Needs a new `cancel_subscription_now()` in `wingman/subscription_common.py` (today's
   `cancel_subscription` only sets `cancel_at_period_end=true`). Optionally `DELETE /customers/<id>`
   too (new call). **Stripe retains billing/tax records by law** — that's fine and the Privacy policy
   must say so. *If Stripe is unconfigured (current state) this is a no-op.*
2. **Google Calendar** — `DELETE /calendars/<google_calendar_id>` (removes the whole "Highschool
   Wingman" calendar + all its events in one call; permitted by the `calendar.app.created` scope),
   then **revoke the refresh token** via `https://oauth2.googleapis.com/revoke`. New helper in
   `app/services/google_oauth.py` (today there's `_delete_events` per-event and `_sweep_stale_events`
   but no delete-the-calendar and no revoke). Best-effort — see §3.2.
3. **Satellite rows** by `userid`: DELETE from `user_costs`, `user_activity`, `user_events`,
   `email_sends`, `mailing_list_subscriptions`.
4. **`opportunities.submitted_by`** — `PATCH … set submitted_by=NULL WHERE submitted_by=eq.<userid>`
   (decision #3: anonymize, keep the row).
5. **The `users` row** — DELETE by `userid`. Authoritative last step; everything else reads from it.
6. **Tombstone** — insert a PII-free audit row (§3.4).
7. **In-flight buffers** — `_cost_buffer` / `_activity_buffer` / `_events_buffer` in `app/core.py`
   are best-effort and self-expiring; a stray flush after delete would re-insert a satellite row, so
   either drop the user's entries from them on delete or accept that the next daily
   satellite-sweep-delete/tombstone check cleans up. *Low priority — note it, don't over-engineer.*

### 3.2 Sequencing & failure policy

The hazard: deleting the `users` row first would strip the Stripe/Google ids we need for external
cleanup, orphaning a **live paid subscription** (keeps billing them) or a Google calendar.

- **Read the full row first** (`get_user`), capture the ids.
- **Stripe cancel must succeed** if a subscription is active. If it errors → **abort the whole
  delete**, return 502 "couldn't cancel your subscription, nothing was deleted, try again." Never
  delete an account while it's still being billed.
- **Google + satellite deletes are best-effort:** log failures, continue. A leftover calendar is a
  nuisance, not a billing or privacy-critical failure, and the tokens become useless once the row is
  gone. Report partial-failure in the response so it's visible.
- **Delete the `users` row last.** Once it's gone the account cannot authenticate (all its tokens
  fail `sub` lookup), so session teardown is implicit — but also proactively `forgetSession` client-
  side.
- Orphaned Stripe webhooks after deletion are **already safe** — `_apply_updates_for_customer`
  no-ops when the customer maps to no account (`get_userid_by_stripe_customer` returns nothing).

### 3.3 Backend plumbing

- **`app/core.py`** needs a `delete_user(userid)` and satellite deletes. `_users_request` today only
  does GET/POST/PATCH — **add DELETE handling.** The cache-bust in `_invalidate_identity_for_write`
  already parses `userid=eq.` filters, so it covers a DELETE with that filter.
- New `erase_account(userid) -> EraseResult` in `app/services/account_data.py`, orchestrating §3.1
  in order, returning per-step success/failure.
- New route `POST /api/account/delete` in `account_data.py`:
  - dep `get_current_user` (not `require_subscription`).
  - **Require re-auth proof in the body** (decision #2): a `password` the server re-verifies with
    argon2 against `password_hash`, OR a short-lived "re-auth token" minted by a fresh Google
    handoff for Google-only accounts. Reject with 401 if it doesn't verify. This is separate from the
    session token.
  - Rate-limited; plain `def`.
- Register router in `app/main.py`.

### 3.4 Tombstone / audit

New tiny table **`db/account_deletions_schema.sql`** (one-time manual DDL, same pattern as every
other schema file, with the ALTER-block convention):

```
account_deletions(
  userid_hash text primary key,   -- sha256(userid), NOT the userid itself
  deleted_at  timestamptz default now(),
  had_subscription boolean,
  notes text
)
```

Stores **no email, name, or raw userid** — just proof-of-deletion + timestamp for our own audit and
to answer "did you delete my account on date X." Hash so the table can't be used to reconstruct who
had an account. Optional but recommended for a minors-facing product.

### 3.5 Frontend

- `ApiClient.ts` — add `deleteAccount(reauth): Promise<void>`.
- `httpClient.ts` — authed POST via `request()` (JSON in/out is fine here); on success call
  `forgetSession()` (`:234`, clears tokens/session/caches) and route to `/landing`.
- Entry point: a **"Delete my account"** danger-zone button — recommend the **bottom of Manage Plan
  (`subscription.tsx`)** and/or the account drawer. Flow:
  1. Button → confirmation modal explaining **what's deleted, that it's permanent, and that an active
     subscription will be cancelled.**
  2. **Re-auth step** (password field, or "confirm with Google").
  3. Second explicit confirm ("Type DELETE" or a checkbox + Delete button — matches the repo's
     caution around irreversible actions).
  4. On success: signed out, sent to landing, brief "your account and data have been deleted."
- Keep **Log out always reachable** and don't hide the drawer during the flow.

---

## 4. Legal changes (source-of-record → build → version bump)

Edit the **markdown** (never the generated `.html`), then `python -m agents.build_legal`, then bump
`TERMS_VERSION` in `app/config.py` (currently `"2026-09-06"`).

- **`legal/privacy.md`:**
  - **§10 Deletion** — replace the email-only manual flow with the self-serve mechanism: "You can
    delete your account and all associated data at any time from within the app (Manage Plan →
    Delete my account, or the account menu)." State what's removed and what's **retained** (Stripe
    billing/tax records required by law; PII-free deletion audit).
  - **§14 Your Choices** — add the self-serve **access/portability** (export) path alongside deletion.
  - **§9 Retention** — note the export/delete rights and the billing-record retention exception.
- **`legal/terms.md`:**
  - **§7 Accounts** / **§18 Suspension and Termination** — add that the user may delete their account
    at any time and the effect (immediate loss of access, cancellation of any subscription with no
    refund of the current period unless required by law).
- Re-run `agents/build_legal.py`; confirm `public/terms.html` / `public/privacy.html` regenerated.
- **Bump `TERMS_VERSION`** (material change) so new consents are distinguishable from old.
- Consider adding an explicit **"we intend to serve US users only"** line (you asked about scope —
  stating it in the Terms is the cheap way to set that expectation, even though it doesn't hard-block
  non-US traffic).

---

## 5. Schema / DDL summary (all one-time manual steps in the Supabase SQL editor)

- **New:** `db/account_deletions_schema.sql` (§3.4). Follow the repo's ALTER-block + "add a column to
  a CREATE, add it to the ALTER too" convention.
- **No new columns on `users`** for the hard-delete model. (A soft-delete model *would* need a
  `deleted_at` column and every read/gate to honor it — another reason to prefer hard delete.)
- Everything else uses existing tables. DELETE goes through the service key (already how `users` is
  accessed; RLS is service-key-only there).

---

## 6. Testing

- **`tests/test_account_data.py`** (new):
  - Export includes every per-user source and **excludes** `password_hash` / tokens / raw Stripe ids
    (assert the redaction — this is the one that protects us).
  - Export/delete **reject unauthenticated** (401) and **ignore any body `userid`** (assert you get
    your own data even when you pass someone else's id — the IDOR test).
  - Delete removes the `users` row + all satellites; `opportunities.submitted_by` nulled, row kept.
  - Delete **aborts** if Stripe cancel fails and leaves the row intact.
  - Re-auth: wrong password → 401, no deletion.
  - Tombstone written with a **hash**, not the raw userid.
- Extend **`tests/test_subscription_gate.py`**: `/api/account/export` and `/api/account/delete` are
  reachable by a **lapsed** account (must NOT be behind `require_subscription`).
- Frontend: manual pass on web (download works, delete signs out) + native export path decision.

---

## 7. Phasing (suggested sequence)

1. **P0 — Export backend + tests. ✅ DONE 2026-09-06.** `app/services/account_data.py`
   (`assemble_export`: allow-list redaction, summarized `user_costs`, missing-table-safe +
   paginated satellite reads incl. `user_events`), `app/routes/account_data.py`
   (`POST /api/account/export`, `get_current_user` — NOT `require_subscription` — rate-limited via
   new `account_export_limiter`, returns a JSON attachment), registered in `app/main.py`.
   Tests: `tests/unit/test_account_data.py` (12) + gate wiring in `test_subscription_gate.py`.
   All green; 0 new failures vs. the tree's pre-existing environmental baseline.
2. **P1 — Export frontend.** Drawer/Manage-Plan button, blob download (web first).
3. **P2 — Delete backend + tests.** `erase_account`, DELETE in `_users_request`, Stripe-now +
   Google-calendar-delete + revoke helpers, re-auth check, tombstone, sequencing/abort policy.
4. **P3 — Delete frontend.** Danger-zone flow with re-auth + double confirm.
5. **P4 — Legal.** privacy.md/terms.md edits, `build_legal`, `TERMS_VERSION` bump. *(Its own commit;
   it's a consent-affecting change.)*
6. **P5 — Play/App Store.** Add the web deletion-request URL to the Play listing; confirm in-app
   deletion satisfies Apple 5.1.1(v).

Each of P0–P5 is its own commit. None of this touches a model prompt (M8) or a paid API call in the
matching/agent sense (M9) — the Stripe/Google calls are account-management, not model spend — so no
marquee gate applies. Flag if you'd rather I still treat the Stripe customer-delete as needing sign-off.

---

## 8. Post-deploy human actions (things code can't do)

- Configure **`STRIPE_API_KEY` / `STRIPE_PRICE_ID`** before the first real subscription exists, or
  delete's Stripe-cancel step has nothing to cancel (fine now, required once billing is live).
- Run **`db/account_deletions_schema.sql`** in the Supabase SQL editor.
- Update the **Google Play data-deletion URL** in the store listing.
- Have a **privacy attorney** review the final §10/§14 privacy language and the minors framing.
- Decide/enforce the **US-only** stance operationally (or accept the residual GDPR exposure and state
  US-intent in the Terms).
