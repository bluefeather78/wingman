"""Shared FastAPI helpers: JSON responses that match the monolith's wire format,
request-body readers, and the subscription gate. Kept tiny so the routers read like
the old Handler methods they replace.
"""
import json
import secrets

from fastapi import Depends, HTTPException, Request, Response

from app.config import JSON_MAX_BODY_BYTES
from app.core import (get_user_account, get_user_subscription, subscription_state, _login_payload,
                      record_api_error, rotate_refresh_jti)
from app.auth import issue_tokens, get_current_user, get_optional_user, AuthedUser


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


def allowance_error(allowance):
    """A structured 429 for the Free-tier daily AI allowance (TWO_TIER_AI_PLAN.md §4.2).

    The body keeps `error` as the human string so an old client that reads only that still
    renders the message, and adds an `allowance` block (tier / used / limit / remaining /
    reset_at) so the client can show a real "resets in Nh — go Unlimited" state instead of
    parsing a sentence. Carries Retry-After (seconds to the UTC reset) like the other 429s.
    """
    import datetime
    reason = allowance.get("reason") or "You've used today's AI actions."
    resp = json_response(429, {"error": reason, "allowance": {
        "tier": allowance.get("tier"),
        "used": allowance.get("used"), "limit": allowance.get("limit"),
        "remaining": allowance.get("remaining"), "reset_at": allowance.get("reset_at"),
    }})
    try:
        reset = datetime.datetime.fromisoformat(str(allowance.get("reset_at")))
        secs = int((reset - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
        resp.headers["Retry-After"] = str(max(1, secs))
    except Exception:                                              # noqa: BLE001
        pass
    return resp


# --- Opaque failures with a correlation id (S1-13, finding L5) ----------------------
#
# Routes used to hand the caller the raw exception: `f"Could not reach Supabase: {e}"`,
# `f"Matching failed: {e}"`, `str(e)` out of the resume parser, and — worst — the AI
# proxies relayed the provider's own error JSON verbatim. None of that carries a key, but
# it names the database vendor, the HTTP library, quota states, model names and PostgREST
# error codes, which is a free map of the stack for anybody poking at the app.
#
# The detail is not discarded, it is MOVED: a short reference goes to the caller, and the
# full text goes to stdout and to api_errors, where the admin console's API Errors tab
# already shows it. A student who reports "it said ref 3f9c1a04" can be answered exactly.
_ERROR_LOGGED_HEADER = "x-wingman-error-logged"

# What the caller sees when a Supabase read or write fails. Deliberately does not name the
# vendor: "which database are they on" is not something an error message owes anyone.
DB_UNAVAILABLE = "We could not reach your account data just now. Please try again."


def opaque_error(status, public_message, exc, *, op, code=None):
    """json_error(status, public_message + a ref), with the real detail logged not sent.

    `op` is a short, stable label for the failing operation ("login.lookup",
    "calendar.sync") — it groups rows in the API Errors tab, so keep it stable rather than
    descriptive. Marks the response as already-recorded so app.main's capture middleware
    does not log a second, detail-free row for the same failure.

    `code` is an OPTIONAL stable, machine-readable marker added to the body alongside the
    human `error` string (same shape as allowance_error: old clients still read `error`,
    newer ones branch on `code`). It lets the client distinguish a specific recoverable
    failure — e.g. "calendar_reconnect" — from a generic one WITHOUT string-matching the
    user-facing copy, which carries the volatile "(ref …)" suffix and is free to be reworded.
    """
    ref = secrets.token_hex(4)
    detail = f"{type(exc).__name__}: {exc}"
    print(f"[error] ref={ref} op={op}: {detail}")
    try:
        record_api_error("", op, status, f"{op}_failed", message=f"ref={ref} {detail}")
    except Exception:                                      # noqa: BLE001
        pass                                               # logging must never be the fault
    message = f"{public_message} (ref {ref})"
    resp = (json_response(status, {"error": message, "code": code})
            if code else json_error(status, message))
    try:
        resp.headers[_ERROR_LOGGED_HEADER] = "1"
    except Exception:                                      # noqa: BLE001
        pass
    return resp


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
    """The parsed JSON body, as a dependency. Malformed/empty both give {}.

    Capped at JSON_MAX_BODY_BYTES (S1-5, finding M4). S0-2 capped the two AI proxies and
    the resume upload; every other route still read its body with no ceiling, so this is
    where the rest of the app gets one — putting it on the shared dependency rather than
    route by route means a NEW route is bounded by default instead of by remembering.
    An over-large body raises 413 before the handler runs.
    """
    raw = await _json_raw_body(request)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


async def raw_body(request: Request) -> bytes:
    """The undecoded body, for the routes that parse it themselves (AI proxies pass it
    straight through; the resume upload splits it on the multipart boundary)."""
    return await request.body()


def capped_raw_body(max_bytes):
    """A raw_body dependency that refuses an over-large request with 413 (S0-2, finding M4).

    Plain raw_body reads the whole request into memory with no ceiling, so a large body was
    both a memory lever and — on the AI proxies — a billing one (a 41 KB body billed 9,703
    input tokens, verified live 2026-09-03). Two checks, because either alone has a hole:

      * Content-Length first, so an honest oversized upload is refused before a single byte
        of it is buffered. Client-supplied, so it cannot be trusted on its own, and it is
        absent entirely on a chunked request.
      * The stream is then consumed with a running total and abandoned the moment it passes
        the cap, so a chunked or lying client cannot buffer more than the cap either.

    The assembled bytes are cached on request._body — the same attribute Starlette's
    Request.body() populates and checks — so anything downstream that reads the body again
    (an exception handler, a later dependency) still works instead of hitting a consumed
    stream.

    Returns a dependency; call it with the route's ceiling. Being a dependency, the 413 is
    raised BEFORE the handler runs, which is what makes "zero provider usage for an
    over-limit body" true rather than merely likely.
    """
    detail = "Request body is too large."

    async def _capped(request: Request) -> bytes:
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > max_bytes:
                    raise HTTPException(status_code=413, detail=detail)
            except ValueError:
                pass                       # unparseable header: fall through to the real count
        if hasattr(request, "_body"):      # already read upstream; just enforce the ceiling
            if len(request._body) > max_bytes:
                raise HTTPException(status_code=413, detail=detail)
            return request._body
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail=detail)
            chunks.append(chunk)
        request._body = b"".join(chunks)
        return request._body

    return _capped


# Bound here rather than beside json_body because capped_raw_body is defined above it.
_json_raw_body = capped_raw_body(JSON_MAX_BODY_BYTES)


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


def login_response(record, replaces_jti=None):
    """The signed-in payload every login path returns: the identity block _login_payload
    already built, plus a freshly minted access+refresh token pair (docs/archive/PLAN_2_auth.md).

    This is the single convergence point the plan asks for — password login
    (handle_login), Google sign-in (handle_google_session) and Google signup
    (handle_google_finish) all return through here, so all three get the same tokens with
    no duplicated minting. Wrapping _login_payload rather than editing it keeps app.core
    free of the auth layer (core is the seam ops/ imports too).
    """
    payload = _login_payload(record)
    tokens = issue_tokens(record["userid"], record.get("token_version") or 0)
    # S1-2: the jti is what the SERVER stores, not something the client is told. Popped
    # before the payload goes out — a jti on the wire would hand a thief the exact string
    # to look for. rotate_refresh_jti returns False when db/auth_schema.sql's refresh_jtis
    # column has not been run in, in which case rotation is simply off and this behaves as
    # it did before.
    jti = tokens.pop("refresh_jti", None)
    if jti:
        try:
            rotate_refresh_jti(record["userid"], jti, replaces=replaces_jti)
        except Exception as e:                                 # noqa: BLE001
            # Never fail a sign-in over the rotation bookkeeping. The lineage is then
            # unknown to the server, and its next refresh is treated as a legacy token.
            print(f"[WARN] Could not record refresh lineage: {type(e).__name__}")
    payload.update(tokens)
    return payload


def subscription_block_reason(userid):
    """The 402 reason if this account is locked out of the app, else None.

    Two-tier model (TWO_TIER_AI_PLAN.md §2b, §4.3): there is no app-access lockout anymore.
    subscription_state().has_access is always True for a signed-in account, so this ALWAYS
    returns None — every signed-in student has at least metered Free access, and a lapsed
    paid/comped account becomes Free rather than being walled off. The per-user AI allowance
    is a SEPARATE gate that lives at the AI route and answers 429, not 402 (step 4).

    Kept as the one function every access gate calls (require_subscription /
    optional_subscribed_user / the AI and resume handlers) so re-introducing a lockout later
    is a one-place change, and so the wiring tests still have a seam to assert. It no longer
    reads Supabase — there is nothing about the subscription that can block app access.
    """
    return None


# --- The gate as a DEPENDENCY ------------------------------------------------------
#
# subscription_block_reason() above is the rule; these two apply it. Every route that is
# "using the app" hangs off one of them, so an account whose trial or subscription has
# ended has no server-side access left — not just no access to the calls that cost money.
# The 402 body is the reason string, which the client shows on the paywall screen.
#
# require_subscription is the hard form (401 with no token, 402 with a lapsed one) and is
# what the owned-data routes use. optional_subscribed_user is for the routes that are
# legitimately reachable signed-out: an unidentified caller is never blocked (same
# residual the cost attribution reports as unattributed), but a caller who DOES identify
# as a lapsed account is.
def require_subscription(user: AuthedUser = Depends(get_current_user)) -> AuthedUser:
    reason = subscription_block_reason(user.id)
    if reason:
        raise HTTPException(status_code=402, detail=reason)
    return user


def optional_subscribed_user(user=Depends(get_optional_user)):
    if user is not None:
        reason = subscription_block_reason(user.id)
        if reason:
            raise HTTPException(status_code=402, detail=reason)
    return user
