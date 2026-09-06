"""Self-serve data rights: assemble everything Wingman holds about ONE account.

DATA_DELETION_EXPORT_PLAN.md, P0. This module answers "export all my data" — the read
side. Deletion (P2) will live beside it and reuse the same source map, so the two cannot
drift about WHAT counts as a user's data.

The identity is always a `userid` the caller proved with a signed token (the route hangs
off get_current_user). Nothing here accepts a userid from a request body — see
DATA_DELETION_EXPORT_PLAN.md §1.

Two rules the tests pin, because getting either wrong is the whole risk of the feature:

  * The account block is an ALLOW-LIST, not a deny-list. A new column added to `users`
    later (a fresh token, another secret) is excluded by DEFAULT rather than exported until
    someone remembers to redact it. Secrets — password_hash, token_version, refresh_jtis,
    the Google OAuth tokens, the raw Stripe ids — are never in the output; the export is the
    STUDENT's data, not our credentials.
  * Every satellite read is best-effort and missing-table-safe. `user_events` in particular
    is not finished yet (DATA_DELETION_EXPORT_PLAN.md §0.1): a not-yet-migrated table must
    yield [] here, never an error — so when events capture lands it is already covered.
"""
import datetime

from app.core import get_user, _supabase_request, pseudonym

# The ONLY `users` columns that reach the export. Personal, non-secret. Anything not on this
# list — password_hash, token_version, refresh_jtis, google_calendar_access_token /
# _refresh_token / _token_expires_at, stripe_customer_id, stripe_subscription_id — is dropped.
# The two Stripe ids are surfaced as booleans below instead: "you have billing on file" is
# the student's business; the customer/subscription strings are our billing plumbing and an
# attacker's map, so the raw values stay out.
_EXPORT_ACCOUNT_COLUMNS = (
    "userid", "first_name", "last_name", "email", "location", "google_id",
    "created_at", "updated_at",
    "subscription_status", "trial_ends_at", "subscription_end_at", "promo_codes_used",
    "is_adult", "parental_consent", "terms_accepted_at", "privacy_accepted_at",
    "terms_version", "lifecycle_email_optout",
    "google_calendar_connected_at", "google_calendar_id",
)


def _read_user_rows(table, uid, select, order=None):
    """Every row of `table` for this userid, paginated past PostgREST's 1000-row cap.

    Best-effort: a missing table, an unrun migration, or an unreachable Supabase all return
    [] (the missing-table-safe contract above). _supabase_request already swallows the
    error and logs a WARN; we just treat None as "nothing to export from here".

    user_events can be large for a heavy user, so this pages rather than trusting a single
    request to return everything — the same reason every catalog read in this repo paginates.
    """
    rows = []
    page = 1000
    offset = 0
    while True:
        params = {"userid": f"eq.{uid}", "select": select, "limit": str(page),
                  "offset": str(offset)}
        if order:
            params["order"] = order
        batch = _supabase_request(table, params=params)
        if not batch:                       # None (failure/missing) or [] (done)
            break
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return rows


def _summarize_costs(rows):
    """user_costs, summarized (DECISION #4 / the operator's call): the AI spend attributable
    to this account, as totals plus a per-feature breakdown — not the raw per-day ledger.

    It is our operational data as much as theirs, so the honest, non-misleading form is a
    summary of what their usage cost, not a line item they cannot act on.
    """
    total_cost = 0.0
    total_calls = 0
    by_feature = {}
    for r in rows:
        try:
            cost = float(r.get("cost_usd") or 0)
        except (TypeError, ValueError):
            cost = 0.0
        calls = int(r.get("calls") or 0)
        total_cost += cost
        total_calls += calls
        feat = r.get("feature") or "other"
        slot = by_feature.setdefault(feat, {"cost_usd": 0.0, "calls": 0})
        slot["cost_usd"] += cost
        slot["calls"] += calls
    return {
        "total_cost_usd": round(total_cost, 6),
        "total_billed_calls": total_calls,
        "by_feature": {k: {"cost_usd": round(v["cost_usd"], 6), "calls": v["calls"]}
                       for k, v in sorted(by_feature.items())},
        "note": ("Estimated cost of the AI actions attributable to your account. This is "
                 "Wingman's own usage accounting, included here for transparency."),
    }


def assemble_export(userid):
    """Everything Wingman holds about this account, as one JSON-serializable dict.

    Returns None when there is no such account (the route turns that into a 404). The account
    exists in exactly one place — the `users` row — so a missing row is authoritative.
    """
    uid = (str(userid) or "").strip().lower()
    if not uid:
        return None
    row = get_user(uid)
    if not row:
        return None

    account = {col: row.get(col) for col in _EXPORT_ACCOUNT_COLUMNS}
    # Existence flags, never the raw ids (see _EXPORT_ACCOUNT_COLUMNS).
    account["has_stripe_customer"] = bool(row.get("stripe_customer_id"))
    account["has_stripe_subscription"] = bool(row.get("stripe_subscription_id"))

    export = {
        "export_generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "userid": uid,
        "account": account,
        # The student's whole app state: student-profile, hs-tracker-data, hs-tracker-saved,
        # and any other keys the client has written. This is the bulk of what they'd want.
        "app_data": row.get("data") or {},
        # Satellites, each best-effort / missing-table-safe.
        "ai_usage": _summarize_costs(
            _read_user_rows("user_costs", uid,
                            "day,surface,feature,model,calls,cost_usd")),
        "activity": _read_user_rows(
            "user_activity", uid, "day,hits,surfaces,first_at,last_at", order="day"),
        "behavioral_events": _read_user_rows(
            "user_events", uid, "ts,action,opportunity_id,context", order="ts"),
        "emails_sent": _read_user_rows(
            "email_sends", uid, "kind,email,subject,state,provider,claimed_at,sent_at",
            order="claimed_at"),
        "mailing_list_signups": _read_user_rows(
            "mailing_list_subscriptions", uid,
            "opportunity_id,email,state,provider,attempted_at", order="attempted_at"),
        # opportunities is keyed by `submitted_by`, not `userid`, so it can't use
        # _read_user_rows. Best-effort / missing-table-safe like the rest; small per user, so
        # no pagination loop.
        "submitted_opportunities": _supabase_request(
            "opportunities",
            params={"submitted_by": f"eq.{uid}",
                    "select": "id,name,org,url,moderation_status"}) or [],
    }

    print(f"[export] assembled data export for {pseudonym(uid)}")
    return export
