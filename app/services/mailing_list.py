"""Mailing-list signup: recipe lookup, per-user availability, and the execution
path that replays a *verified* recipe against a real address. Extracted verbatim
from server.py (PLAN_1_decompose.md). Recipe review/moderation (writes) lives in
ops.core; this module is the read + student-facing subscribe half.
"""
import datetime
import json
import threading
import time
import urllib.parse
import urllib.request

import mailing_list_common
from app.config import *  # noqa: F401,F403
from app.core import (
    _supabase_request, _supabase_request_strict, _is_missing_column_error, get_user,
    _error_body, _MISSING_COLUMN_CODES, _missing_table_error,
)



# ---------- Mailing-list signup ----------
#
# Two halves, deliberately far apart in trust:
#
#   DISCOVERY (find_mailing_lists.py) writes a RECIPE per opportunity into
#   opportunity_signups, always at status 'pending_review'. It cannot verify its own work.
#
#   EXECUTION (here) replays a recipe for one real user, and refuses to touch anything a
#   person has not promoted to 'verified' in the admin console. There is no AI on this
#   path and it costs nothing — the model was spent once, offline, per opportunity.
#
# The wording matters as much as the mechanism. Every provider we support double opt-ins,
# and because the student's own address is used (there is no wingman-owned relay to read),
# nothing here can observe the confirmation being clicked. The success state is
# 'submitted', never 'subscribed', all the way from mailing_list_common through the
# database column to the button label. Do not "tidy" that up.

SIGNUPS_TABLE = "opportunity_signups"
SUBSCRIPTIONS_TABLE = "mailing_list_subscriptions"

# Recipe statuses a reviewer may set from the console. 'pending_review' is here so a
# verdict can be undone; discovery writes 'pending_review'/'none' and nothing else.
SIGNUP_REVIEW_STATUSES = ("verified", "rejected", "pending_review", "none")

# Per-user throttle on outbound subscribes. This is not about our cost — a subscribe is
# free — it is about not letting a stuck button, or a user working through a long result
# list, look like an attack to somebody else's mail provider. In-memory and per-process,
# which is enough for a single dev server; the unique (userid, opportunity_id) row is the
# real backstop against repeat submissions of the SAME list.
SUBSCRIBE_MAX_PER_HOUR = 10
_subscribe_history = {}  # {userid: [monotonic timestamps]}
_subscribe_lock = threading.Lock()


def _subscribe_rate_limited(userid):
    """True if this user has already sent SUBSCRIBE_MAX_PER_HOUR subscribes this hour."""
    now = time.monotonic()
    with _subscribe_lock:
        recent = [t for t in _subscribe_history.get(userid, []) if now - t < 3600]
        if len(recent) >= SUBSCRIBE_MAX_PER_HOUR:
            _subscribe_history[userid] = recent
            return True
        recent.append(now)
        _subscribe_history[userid] = recent
        return False


# Covers both halves of the same setup step: the tables missing entirely, and the tables
# existing in an older shape with columns missing. The fix is identical either way, and
# the second case is the easier one to misread as a bug.
MAILING_LIST_SETUP_HINT = ("The mailing-list tables are missing or out of date. Run "
                           "mailing_list_schema.sql in the Supabase SQL editor (it is "
                           "idempotent, and its ALTER block adds anything missing from an "
                           "existing table), then restart the server.")


def get_signup_recipe(opp_id):
    """The stored recipe for one opportunity, or None. Never raises."""
    rows = _supabase_request(SIGNUPS_TABLE, params={
        "select": "*", "opportunity_id": f"eq.{opp_id}", "limit": "1"})
    return (rows or [None])[0] if rows else None


def get_subscription_states(userid, opp_ids):
    """What this user has already attempted, keyed by opportunity id.

    Drives the button's third state ("Submitted 12 Aug") so a student is not invited to
    re-send a signup they already sent.
    """
    userid = (userid or "").strip().lower()
    ids = [str(i).strip() for i in (opp_ids or []) if str(i).strip()]
    if not userid or not ids:
        return {}
    safe = ",".join(re.sub(r"[(),*%]", "", i) for i in ids)
    rows = _supabase_request(SUBSCRIPTIONS_TABLE, params={
        "select": "opportunity_id,state,email,message,attempted_at",
        "userid": f"eq.{userid}", "opportunity_id": f"in.({safe})"}) or []
    return {r["opportunity_id"]: r for r in rows}


def get_signup_availability(userid, opp_ids):
    """Per-opportunity: can we subscribe automatically, and has this user already?

    One call for a whole screenful of cards. Returns only what the client needs to pick a
    button state — never the endpoint or field map, which are ours and are not a student's
    business to see or to be able to replay.
    """
    ids = [str(i).strip() for i in (opp_ids or []) if str(i).strip()][:200]
    if not ids:
        return {"ok": True, "items": {}}
    safe = ",".join(re.sub(r"[(),*%]", "", i) for i in ids)
    try:
        recipes = _supabase_request(SIGNUPS_TABLE, params={
            "select": "opportunity_id,method,status,double_optin",
            "opportunity_id": f"in.({safe})", "status": "eq.verified"}) or []
    except Exception:
        recipes = []
    verified = {r["opportunity_id"]: r for r in recipes}
    attempts = get_subscription_states(userid, ids)

    items = {}
    for opp_id in ids:
        recipe = verified.get(opp_id)
        attempt = attempts.get(opp_id)
        items[opp_id] = {
            "eligible": bool(recipe),
            "provider": mailing_list_common.PROVIDER_NAMES.get((recipe or {}).get("method")),
            "double_optin": bool((recipe or {}).get("double_optin")),
            "state": (attempt or {}).get("state"),
            "email": (attempt or {}).get("email"),
            "attempted_at": (attempt or {}).get("attempted_at"),
        }
    return {"ok": True, "items": items}


def _record_subscription_attempt(userid, opp_id, email, state, message, provider, detail):
    """Upsert the attempt. Returns True if it was recorded.

    Never raises: the provider has already accepted the signup by the time this runs, and
    a bookkeeping failure must not be reported to the student as a failed signup. But it
    is not nothing either — an unrecorded attempt means their button never updates and the
    Quest Log cannot show them what we sent, so the caller passes `recorded` back and the
    warning below names the row.
    """
    row = {
        "userid": userid, "opportunity_id": opp_id, "email": email,
        "state": state, "message": (message or "")[:500], "provider": provider,
        "provider_detail": (detail or "")[:500],
        "attempted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        _supabase_request_strict(
            SUBSCRIPTIONS_TABLE, method="POST",
            params={"on_conflict": "userid,opportunity_id"}, data=[row],
            extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return True
    except Exception as e:
        hint = f" {MAILING_LIST_SETUP_HINT}" if _missing_table_error(e) else ""
        print(f"[WARN] Signup for {userid}/{opp_id} was SENT but not recorded: {e}.{hint}")
        return False


def subscribe_user_to_list(userid, opp_id, email, consented):
    """Subscribe one user to one opportunity's mailing list. Returns a client payload.

    Consent is re-checked here and not merely in the UI, for the same reason the signup
    checkboxes are re-checked in handle_register: the client-side control is a screen, and
    this one sends a minor's name and address to a third party.
    """
    userid = (userid or "").strip().lower()
    email = (email or "").strip()
    if not userid:
        return 401, {"error": "Sign in to use automatic mailing-list signup."}
    if not consented:
        return 400, {"error": "We need your OK before sending your details to the organization."}
    if not EMAIL_RE.match(email):
        return 400, {"error": "Please enter a valid email address."}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 500, {"error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}

    opp = _supabase_request("opportunities", params={
        "select": "id,name,org,url", "id": f"eq.{opp_id}", "limit": "1"})
    if not opp:
        return 404, {"error": "Opportunity not found."}
    opp = opp[0]

    recipe = get_signup_recipe(opp_id)
    # The gate. An unreviewed recipe is not a slightly-worse verified one — it is an
    # unattributed form that may belong to the whole university, and sending a student
    # there is the exact silent failure this feature is measured against.
    if not recipe or recipe.get("status") != "verified":
        return 200, {"state": "handoff", "url": opp.get("url"),
                     "message": "We don't have a verified mailing list for this one yet — "
                                "open their page to sign up directly."}

    if _subscribe_rate_limited(userid):
        return 429, {"error": f"That's {SUBSCRIBE_MAX_PER_HOUR} signups in an hour — give it "
                              f"a little time before sending more."}

    user = None
    try:
        user = get_user(userid)
    except Exception:
        pass
    values = {
        "email": email,
        "first_name": (user or {}).get("first_name") or "",
        "last_name": (user or {}).get("last_name") or "",
    }
    values["full_name"] = " ".join(v for v in (values["first_name"], values["last_name"]) if v)

    state, message, detail = mailing_list_common.execute(recipe, values)
    recorded = _record_subscription_attempt(userid, opp_id, email, state, message,
                                            recipe.get("method"), detail)
    payload = {"state": state, "message": message, "email": email,
               "provider": mailing_list_common.PROVIDER_NAMES.get(recipe.get("method")),
               "double_optin": bool(recipe.get("double_optin")),
               # False means the signup went out but we could not store it — the button
               # will reset and the Quest Log will not list it. Surfaced rather than
               # hidden so this shows up as a bug report and not as a mystery.
               "recorded": recorded}
    if state in ("failed", "handoff"):
        payload["url"] = opp.get("url")
    return 200, payload


def list_user_subscriptions(userid, limit=200):
    """Every mailing list this user has signed up for, for the Quest Log's own list.

    A student must be able to find what we sent on their behalf. Without this the feature
    is a button that does something invisible.
    """
    userid = (userid or "").strip().lower()
    if not userid:
        return {"ok": True, "subscriptions": []}
    try:
        rows = _supabase_request(SUBSCRIPTIONS_TABLE, params={
            "select": "opportunity_id,state,email,message,provider,attempted_at",
            "userid": f"eq.{userid}", "order": "attempted_at.desc",
            "limit": str(limit)}) or []
    except Exception:
        rows = []
    if not rows:
        return {"ok": True, "subscriptions": []}
    safe = ",".join(re.sub(r"[(),*%]", "", r["opportunity_id"]) for r in rows)
    catalog = {c["id"]: c for c in (_supabase_request("opportunities", params={
        "select": "id,name,org,url", "id": f"in.({safe})"}) or [])}
    for row in rows:
        entry = catalog.get(row["opportunity_id"]) or {}
        row["name"] = entry.get("name") or row["opportunity_id"]
        row["org"] = entry.get("org")
        row["url"] = entry.get("url")
    return {"ok": True, "subscriptions": rows}
