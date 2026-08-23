"""Per-account data + location routes. Translated from server.py's handle_data_save /
handle_data_load / handle_update_location (PLAN_1_decompose.md).
"""
from fastapi import APIRouter, Request, Depends

from app.core import touch_user_activity, update_user_data, get_user, update_user_location
from app.deps import read_json_body, json_response, json_error
from app.auth import get_current_user, AuthedUser

router = APIRouter()


# These three routes were the IDOR (PLAN_2_auth.md): they read `userid` straight from the
# request body and returned/wrote THAT account's data with no proof the caller owned it.
# Identity now comes only from the verified access token (user.id); any userid in the body
# is ignored. get_current_user 401s a missing/invalid token before the handler runs.
@router.post("/api/data/save")
async def handle_data_save(request: Request, user: AuthedUser = Depends(get_current_user)):
    body = await read_json_body(request)
    userid = user.id
    key = body.get("key")
    if not key:
        return json_error(400, "Missing key.")
    # "Changed something", as opposed to data_load's "opened the app".
    touch_user_activity(userid, "data_save")
    try:
        ok = update_user_data(userid, key, body.get("value"))
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")
    if not ok:
        return json_error(404, "No account found with that user ID.")
    return json_response(200, {"ok": True})


@router.post("/api/data/load")
async def handle_data_load(request: Request, user: AuthedUser = Depends(get_current_user)):
    body = await read_json_body(request)
    userid = user.id
    key = body.get("key")
    touch_user_activity(userid, "data_load")
    try:
        record = get_user(userid)
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")
    value = (record.get("data") or {}).get(key) if record else None
    return json_response(200, {"value": value})


@router.post("/api/account/location")
async def handle_update_location(request: Request, user: AuthedUser = Depends(get_current_user)):
    body = await read_json_body(request)
    userid = user.id
    location = (body.get("location") or "").strip()
    if not location:
        return json_error(400, "Missing location.")
    try:
        ok = update_user_location(userid, location)
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")
    if not ok:
        return json_error(404, "No account found with that user ID.")
    return json_response(200, {"ok": True})
