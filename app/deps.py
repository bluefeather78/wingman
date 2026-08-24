"""Shared FastAPI helpers: JSON responses that match the monolith's wire format,
request-body readers, and the subscription gate. Kept tiny so the routers read like
the old Handler methods they replace.
"""
import json

from fastapi import Request, Response

from app.core import get_user_account, subscription_state, _login_payload
from app.auth import issue_tokens


def json_response(status, obj, default=None):
    """Mirror the old Handler._relay: a JSON body with an explicit status code.

    `default=str` is passed by the ops routes (their payloads carry datetimes); the
    public routes pass default=None, exactly as the monolith did.
    """
    body = json.dumps(obj, default=default).encode()
    return Response(content=body, media_type="application/json", status_code=status)


def json_error(code, message):
    """Mirror Handler.send_json_error: {"error": message} at the given status."""
    return json_response(code, {"error": message})


async def read_json_body(request: Request):
    """Mirror Handler._read_json_body: parse the body, swallow failures into {}."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


# --- Body readers as DEPENDENCIES, so handlers can be plain `def` -------------------
#
# Every Supabase/provider call in this repo is blocking urllib (app.core._users_request and
# friends), and FastAPI runs an `async def` handler ON the event loop. So one blocking call
# froze the whole process: Home Base's three parallel /api/data/load calls were serialized
# end to end (measured 2026-08-24 — 164ms each alone, 660ms wall for the three together).
#
# A plain `def` handler is run by FastAPI in a threadpool instead, which restores real
# concurrency. The only thing forcing handlers to be async was `await request.body()`, so
# that await moves into these dependencies: FastAPI resolves them on the loop, then calls
# the sync handler off it. Semantics are identical to calling read_json_body() directly.
#
# Anything that awaits inside the handler body must STAY `async def` — the routes here
# don't, but that is the line.
async def json_body(request: Request):
    """The parsed JSON body, as a dependency. Malformed/empty both give {}."""
    return await read_json_body(request)


async def raw_body(request: Request) -> bytes:
    """The undecoded body, for the routes that parse it themselves (AI proxies pass it
    straight through; the resume upload splits it on the multipart boundary)."""
    return await request.body()


async def read_json_body_strict(request: Request):
    """Mirror Handler._read_json_body_strict: (data, error), distinguishing malformed
    from empty and naming a non-UTF-8 body specifically."""
    raw = await request.body()
    if not raw:
        return {}, None
    try:
        return json.loads(raw.decode("utf-8")), None
    except UnicodeDecodeError:
        return None, ("Request body is not valid UTF-8. Send the JSON as UTF-8 "
                      "(browsers do this automatically).")
    except Exception as e:
        return None, f"Malformed JSON body: {e}"


def client_ip(request: Request):
    return request.client.host if request.client else ""


def login_response(record):
    """The signed-in payload every login path returns: the identity block _login_payload
    already built, plus a freshly minted access+refresh token pair (PLAN_2_auth.md).

    This is the single convergence point the plan asks for — password login
    (handle_login), Google sign-in (handle_google_session) and Google signup
    (handle_google_finish) all return through here, so all three get the same tokens with
    no duplicated minting. Wrapping _login_payload rather than editing it keeps app.core
    free of the auth layer (core is the seam ops/ imports too).
    """
    payload = _login_payload(record)
    payload.update(issue_tokens(record["userid"], record.get("token_version") or 0))
    return payload


def subscription_block_reason(userid):
    """The 402 reason if this account's trial has lapsed with nothing paid, else None.

    Same logic as the old Handler._subscription_blocks, minus the response-writing:
    the caller turns a non-None reason into a 402. A missing userid is never blocked
    (signed-out calls can't be identified), and a Supabase failure fails open rather
    than locking everyone out.
    """
    userid = (userid or "").strip().lower()
    if not userid:
        return None
    try:
        record = get_user_account(userid)
    except Exception:
        return None
    if not record:
        return None
    state = subscription_state(record)
    if state["has_access"]:
        return None
    if state["status"] == "past_due":
        return ("We could not charge your card. Update your payment details to "
                "restore access to Wingman's AI features.")
    if state["status"] == "canceled":
        return ("Your subscription has ended. Resubscribe to keep using "
                "Wingman's AI features.")
    if state["status"] == "beta":
        return ("Your beta access has ended. Subscribe to keep using Wingman's "
                "AI features.")
    return ("Your free trial has ended. Subscribe to keep using Wingman's "
            "AI features.")
