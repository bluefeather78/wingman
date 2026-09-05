"""Per-account data + location routes. Translated from server.py's handle_data_save /
handle_data_load / handle_update_location (docs/archive/PLAN_1_decompose.md).
"""
import json

from fastapi import APIRouter, Depends

from app.config import USER_DATA_MAX_VALUE_BYTES
from app.core import (touch_user_activity, update_user_data, get_user_data,
                      update_user_location)
from app.deps import (json_body, json_response, json_error, require_subscription,
                      opaque_error, DB_UNAVAILABLE)
from app.auth import AuthedUser

router = APIRouter()


# These three routes were the IDOR (docs/archive/PLAN_2_auth.md): they read `userid` straight from the
# request body and returned/wrote THAT account's data with no proof the caller owned it.
# Identity now comes only from the verified access token (user.id); any userid in the body
# is ignored. require_subscription wraps get_current_user, so a missing/invalid token is
# still a hard 401 before the handler runs.
#
# They gate on SUBSCRIPTION as well as identity: a student's profile and Quest Log are the
# app, not an extra, so an account whose trial or subscription has ended cannot read or
# write them. Nothing is deleted — the row is untouched and comes straight back the moment
# they subscribe again. The client paywall derives from the same subscription_state(), so
# the two cannot disagree; this is the half that a stale bundle or a direct API call hits.
@router.post("/api/data/save")
def handle_data_save(body: dict = Depends(json_body),
                     user: AuthedUser = Depends(require_subscription)):
    userid = user.id
    key = body.get("key")
    if not key:
        return json_error(400, "Missing key.")
    # S1-5, finding M4. The request body is capped by json_body, but this row ACCUMULATES:
    # every key written stays in users.data, which is read in full on every app open. So the
    # per-value ceiling is the one that actually bounds growth, and it has to be checked on
    # the serialized form — the size that reaches the jsonb column, not len() of a dict.
    try:
        value_bytes = len(json.dumps(body.get("value")).encode("utf-8"))
    except (TypeError, ValueError):
        return json_error(400, "That value could not be stored.")
    if value_bytes > USER_DATA_MAX_VALUE_BYTES:
        return json_error(413, "That is too much data to save in one go.")
    # "Changed something", as opposed to data_load's "opened the app".
    touch_user_activity(userid, "data_save")
    try:
        ok = update_user_data(userid, key, body.get("value"))
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="user_data.db")
    if not ok:
        return json_error(404, "No account found with that user ID.")
    return json_response(200, {"ok": True})


@router.post("/api/data/load")
def handle_data_load(body: dict = Depends(json_body),
                     user: AuthedUser = Depends(require_subscription)):
    """{key} -> {value}, or {keys: [...]} -> {values: {key: value}}.

    The multi-key form exists because every screen needs two or three keys at once and
    they all live in the SAME jsonb column: Home Base asked for hs-tracker-data,
    hs-tracker-saved and student-profile as three requests, so one row was fetched from
    Supabase three times to read three of its own keys. One request, one read.

    The single-key form is unchanged and still answers {value} — native clients and the
    save path both still use it, and a stale bundle in a browser must keep working.
    """
    userid = user.id
    keys = body.get("keys")
    touch_user_activity(userid, "data_load")
    try:
        data = get_user_data(userid)
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="user_data.db")
    data = data or {}
    if isinstance(keys, list):
        # An unknown key answers null, exactly as the single-key form does — absent and
        # unset are the same thing to every caller here.
        return json_response(200, {"values": {str(k): data.get(str(k)) for k in keys}})
    return json_response(200, {"value": data.get(body.get("key"))})


@router.post("/api/account/location")
def handle_update_location(body: dict = Depends(json_body),
                           user: AuthedUser = Depends(require_subscription)):
    userid = user.id
    location = (body.get("location") or "").strip()
    if not location:
        return json_error(400, "Missing location.")
    try:
        ok = update_user_location(userid, location)
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="user_data.db")
    if not ok:
        return json_error(404, "No account found with that user ID.")
    return json_response(200, {"ok": True})
