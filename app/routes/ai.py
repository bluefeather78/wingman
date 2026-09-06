"""The AI route: POST /api/ai.

**MARQUEE M8 + M9.** M8 because the prompts this route sends live in
app/services/prompts.py; M9 because every branch below can make a paid call.

ONE endpoint, and it takes `{feature, inputs}`. It replaced /api/messages and
/api/messages-claude, which took `system`, `userContent`, `useWebSearch` and `maxTokens`
straight off the request body — S1-1, finding C1.2. Those two routes were a model
passthrough with an auth check in front: the client-visible contract was "send any prompt,
any input, search on, 8k output", so every product guardrail written into a prompt was one
curl away from being bypassed, on Wingman's keys and Wingman's bill.

Now the server owns the prompt text, the provider, the tool config, the token budget and
the feature id, and an unknown feature is a 400 that never reaches a provider. Removed
rather than deprecated: leaving the old routes accepting `system` would leave the finding
exactly where it was.

The response envelope is unchanged — {"content":[{"type":"text","text":...}]} for both live
and mock, plus stop_reason — so nothing downstream branches on mode.
"""
import functools
import json
import urllib.error
import urllib.request

from anyio import to_thread
from fastapi import APIRouter, Request, Response, Depends

from app.config import (
    GEMINI_API_KEY, MESSAGES_MODEL, MESSAGES_MAX_TOKENS, MESSAGES_MAX_TOKENS_CEILING,
    ANTHROPIC_API_KEY, ANTHROPIC_URL, CLAUDE_MODEL,
    CLAUDE_MAX_TOKENS, CLAUDE_MAX_TOKENS_CEILING, AI_MAX_BODY_BYTES,
    AI_UPSTREAM_TIMEOUT_SECONDS, ANTHROPIC_MAX_WEB_SEARCH_USES,
    AI_MAX_CONCURRENCY, AI_SHED_RETRY_AFTER_SECONDS,
)
from app.core import (
    touch_user_activity, record_interactive_cost_async,
    record_api_error,
)
from app.deps import (json_response, json_error, subscription_block_reason, client_ip,
                      capped_raw_body, allowance_error, _ERROR_LOGGED_HEADER)
from app.auth import get_optional_user, AuthedUser
from app.auth.ratelimit import ai_ip_limiter, ai_user_limiter
from app.services.ai import generate_mock_text
from app.services import budget
from app.services import prompts
from wingman.gemini_common import call_gemini

router = APIRouter()

# MARQUEE M9: the request cap in front of the paid proxies (S0-2). Both routes read the body
# through this rather than app.deps.raw_body, so an over-limit request is 413'd by the
# dependency — before the handler exists to make an upstream call.
ai_raw_body = capped_raw_body(AI_MAX_BODY_BYTES)


# MARQUEE M9: the concurrency bound in front of the paid provider calls (Phase 2 item 2,
# finding M5). See AI_MAX_CONCURRENCY in app/config.py for why the number is 12.
#
# WHY A COUNTER AND NOT asyncio.Semaphore: a semaphore's acquire() WAITS. Waiting is the one
# thing this must never do — a queued caller still holds its connection and still ends up
# stalling, which is the behaviour being removed. The requirement is "shed, don't stall", so
# the only acquire that makes sense is a non-blocking one, and asyncio.Semaphore has none.
#
# WHY A PLAIN INT IS SAFE: `handle_ai` is `async def`, so try_acquire() runs ON the event
# loop, and there is no `await` between reading `_in_flight` and incrementing it. The event
# loop is single-threaded and cannot interleave another task inside that window, so the
# check-and-increment is atomic without a lock. (A threading.Lock here would be misleading
# rather than wrong: it would suggest threads reach this, and none do.)
class _AiLane:
    """Bounds how many /api/ai requests may occupy a threadpool slot at once."""

    def __init__(self, limit):
        self.limit = limit
        self.in_flight = 0
        self.shed_count = 0        # observability without a metrics stack (item 9 is dropped)

    def try_acquire(self):
        """Take a lane slot if one is free. Never waits. True if acquired."""
        if self.in_flight >= self.limit:
            self.shed_count += 1
            return False
        self.in_flight += 1
        return True

    def release(self):
        # max() so a double-release can never drive the count negative and silently widen
        # the lane past its limit for the life of the process.
        self.in_flight = max(0, self.in_flight - 1)


ai_lane = _AiLane(AI_MAX_CONCURRENCY)


def _shed_response():
    """503 + Retry-After: the lane is full, try again shortly.

    503 rather than 429 on purpose. 429 means "you have asked for too much" and is aimed at
    one caller; this is "the service is at capacity right now" and is aimed at nobody in
    particular. The distinction matters on the dashboard — a spike of 503s is a capacity
    signal, a spike of 429s is an abuse signal, and collapsing them into one code would hide
    exactly the thing this phase is trying to make visible.
    """
    resp = json_error(503, "Wingman is busy right now. Give it a few seconds and try again.")
    resp.headers["Retry-After"] = str(AI_SHED_RETRY_AFTER_SECONDS)
    return resp

# MARQUEE M9 (S0-3, finding D3): whether the server performs PAID web searches is a
# server-side decision. It used to be the CLIENT's — both proxies read
# `use_web_search = bool(payload.get("useWebSearch"))` straight off the request body, so any
# caller could turn on billed searches. Verified live 2026-09-03: useWebSearch:true on
# /api/messages-claude produced web_search_requests=1 and +2,240 billed input tokens.
#
# Pinned False rather than feature-gated, because nothing needs it. Every live feature on
# both routes passes false; the ONLY `true` in the whole frontend is
# intakeExtractAndClassify (frontend/src/lib/tracker.ts), which has zero callers anywhere in
# the repo (frontend_report.md §14 lists it under dead code — re-verified 2026-09-04).
#
# A client "useWebSearch" is now read and ignored. When a feature genuinely needs search,
# derive it here from the S1-1 server-side feature id — never from a client flag.
_USE_WEB_SEARCH = False

# A response header the capture middleware (app/main.py) skips over, so a provider failure
# recorded HERE with its real upstream status + error message is not ALSO recorded generically
# as a 5xx server_error. The header never reaches the client — the middleware strips it.
# Imported, not re-declared: app.deps.opaque_error sets the same marker, and a header name
# copied into a second file is exactly the kind of pin that drifts.


def _mark_logged(resp):
    """Tag a response as already recorded to api_errors, so the middleware doesn't double-log it."""
    try:
        resp.headers[_ERROR_LOGGED_HEADER] = "1"
    except Exception:                                              # noqa: BLE001
        pass
    return resp


# What the caller is told when a provider fails. S1-13, finding L5: both proxies used to
# relay the provider's error JSON VERBATIM — quota states, model names, org ids, the
# provider's own error taxonomy — straight to any browser that asked. The upstream STATUS is
# kept, because the client already branches on it (429 is "slow down", 5xx is "try again"),
# but the body is ours. The provider's real message still reaches the API Errors tab through
# _record_provider_failure, which is where it was always the more useful thing to read.
_PROVIDER_MESSAGES = {
    429: "Wingman is busy right now. Give it a few seconds and try again.",
    529: "Wingman is busy right now. Give it a few seconds and try again.",
}
_PROVIDER_DEFAULT = "The AI service had a problem answering that. Please try again."


def _provider_error_response(status):
    """An opaque {"error": ...} at the provider's own status code."""
    return _mark_logged(json_error(
        status, _PROVIDER_MESSAGES.get(status, _PROVIDER_DEFAULT)))


def _provider_detail(body):
    """The provider's OWN error message out of its JSON body ("rate_limit_error",
    "Resource has been exhausted", …) — the thing you actually want on the dashboard —
    falling back to the raw body. Truncated; record_api_error truncates again as a backstop."""
    try:
        d = json.loads(body)
        err = d.get("error") if isinstance(d, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:500]
        return json.dumps(d)[:500]
    except Exception:                                              # noqa: BLE001
        try:
            return body.decode("utf-8", "replace")[:500]
        except Exception:                                          # noqa: BLE001
            return str(body)[:500]


def _record_provider_failure(provider, path, status, detail):
    """One api_errors row for an AI provider failure. `error_type` embeds the provider and the
    upstream status (gemini_http_429, anthropic_http_529, gemini_error for a network/timeout),
    so the dashboard groups them apart from ordinary 5xx and shows exactly what failed and why.
    Records a provider 429 too — passed straight through to the client, it is under 500 and the
    middleware never sees it, yet a rate-limit wall is the single most common real AI failure."""
    kind = f"{provider}_http_{status}" if status else f"{provider}_error"
    msg = f"{provider} API {('returned ' + str(status)) if status else 'call failed'}"
    if detail:
        msg += f": {detail}"
    record_api_error("POST", path, status or 502, kind, message=msg)


def _envelope(text, stop_reason=None, allowance=None):
    """The response shape both providers and the mock branch answer in.

    `content[0].text` plus `stop_reason` is unchanged on purpose — cleanAiText() and the
    profile-synthesis retry read it, so moving the prompts server-side did not move the wire
    format. `meta.allowance` is ADDITIVE (TWO_TIER_AI_PLAN.md §4.2): a Free-tier meter snapshot
    so the client counter ticks without a second request. Old clients ignore it; the
    mock/circuit paths pass None (nothing was metered).
    """
    body = {"content": [{"type": "text", "text": text}]}
    if stop_reason:
        body["stop_reason"] = stop_reason
    if allowance is not None:
        body["meta"] = {"allowance": {
            "tier": allowance.get("tier"), "unlimited": allowance.get("unlimited"),
            "used": allowance.get("used"), "limit": allowance.get("limit"),
            "remaining": allowance.get("remaining"), "reset_at": allowance.get("reset_at"),
        }}
    return json_response(200, body)


def _mock_response(system, user_content, userid):
    """The offline branch. generate_mock_text still pattern-matches on the SYSTEM PROMPT —
    which the server now builds — so mock mode is unchanged by S1-1 and the app stays fully
    click-through-able with no API keys, exactly as CLAUDE.md requires."""
    text = generate_mock_text(system, user_content)
    return _envelope(text)


def _proxy_to_gemini(system, user_content, max_tokens, userid, cost_feature, allowance=None):
    try:
        text, usage = call_gemini(
            system, user_content, GEMINI_API_KEY,
            use_web_search=_USE_WEB_SEARCH,
            max_tokens=min(int(max_tokens), MESSAGES_MAX_TOKENS_CEILING),
            model=MESSAGES_MODEL,
            # Explicit, not inherited from gemini_common's 120s default: this is an
            # interactive request holding an anyio threadpool slot, not a batch agent that
            # can afford to wait (M9 / S0-4).
            timeout=AI_UPSTREAM_TIMEOUT_SECONDS,
        )
    except urllib.error.HTTPError as e:
        body = e.read()
        _record_provider_failure("gemini", "/api/ai", e.code, _provider_detail(body))
        return _provider_error_response(e.code)
    except Exception as e:
        _record_provider_failure("gemini", "/api/ai", 0, str(e))
        return _mark_logged(json_error(502, _PROVIDER_DEFAULT))
    record_interactive_cost_async("interactive_gemini", usage, MESSAGES_MODEL,
                                  userid=userid, feature=cost_feature)
    return _envelope(text, allowance=allowance)


def _anthropic_call(system, user_content, max_tokens):
    """One Anthropic request. Returns the decoded JSON; raises HTTPError like urlopen."""
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": min(int(max_tokens), CLAUDE_MAX_TOKENS_CEILING),
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user_content}],
    }
    if _USE_WEB_SEARCH:
        # max_uses is the defence-in-depth layer under the _USE_WEB_SEARCH pin, and unlike
        # Gemini's prompt-level max_searches it is a REAL ceiling — Anthropic enforces it
        # server-side. The tool was attached with no cap at all, so a single request could
        # run unbounded $0.01 searches (M9 / S0-4, finding C1.4).
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search",
                          "max_uses": ANTHROPIC_MAX_WEB_SEARCH_USES}]
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    # No timeout here at all until S0-4: a hung socket permanently consumed one of the
    # anyio threadpool's 40 slots, capacity that never returned until a restart.
    with urllib.request.urlopen(req, timeout=AI_UPSTREAM_TIMEOUT_SECONDS) as upstream:
        return json.loads(upstream.read())


def _claude_text(data):
    return "\n".join(b.get("text", "") for b in (data.get("content") or [])
                      if b.get("type") == "text")


def _proxy_to_anthropic(feature, system, user_content, max_tokens, userid, cost_feature,
                        allowance=None):
    """The Claude branch, including the profile-synthesis retry.

    The retry used to live in the CLIENT (call at 4000, call again at 8000 if the answer
    stopped on max_tokens), which meant the client chose both budgets. S1-1 moves the budget
    server-side, so the retry has to come with it — a feature declares retry_max_tokens and
    this decides. Behaviour is identical; the ceiling is simply no longer negotiable.
    """
    attempts = [max_tokens]
    if feature.retry_max_tokens:
        attempts.append(feature.retry_max_tokens)

    data = None
    for budget_tokens in attempts:
        try:
            data = _anthropic_call(system, user_content, budget_tokens)
        except urllib.error.HTTPError as e:
            body = e.read()
            _record_provider_failure("anthropic", "/api/ai", e.code,
                                     _provider_detail(body))
            return _provider_error_response(e.code)
        except Exception as e:
            _record_provider_failure("anthropic", "/api/ai", 0, str(e))
            return _mark_logged(json_error(502, _PROVIDER_DEFAULT))
        # Each attempt is a real, billed call, so each is attributed. Best-effort, after
        # the body is in hand.
        try:
            usage = data.get("usage") or {}
            record_interactive_cost_async("interactive_claude", {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "server_tool_use": usage.get("server_tool_use") or {},
            }, CLAUDE_MODEL, userid=userid, feature=cost_feature)
        except Exception:                                          # noqa: BLE001
            pass
        if data.get("stop_reason") != "max_tokens":
            break

    text = _claude_text(data or {})
    return _envelope(text, (data or {}).get("stop_reason"), allowance=allowance)


# MARQUEE M9: this is the auth gate in front of the two paid AI proxies. See S0-1 in
# SECURITY_HARDENING_PLAN.md and finding C1 in docs/review-2026-09-02/security_report.md.
#
# get_optional_user never 401s, and subscription_block_reason(None) returns None for an
# unidentified caller — so before this gate existed a POST with NO Authorization header at
# all fell straight through to the provider. Verified live 2026-09-03: anonymous POST -> 200,
# a real billed call, spend attributed to nobody.
#
# The gate keys on WHETHER A LIVE KEY IS CONFIGURED, not on the route. That distinction is
# load-bearing: CLAUDE.md's standing constraint is that the app stays fully
# click-through-able with no API keys, so the mock branch must remain reachable signed-out.
# Gating the whole route would break offline development and the signed-out demo path.
#
#   live branch (key set)  -> 401 without a valid token, 402 for a lapsed account
#   mock branch (no key)   -> reachable signed-out; a caller who DOES identify as a lapsed
#                             account still gets its 402, exactly as before.
def _ai_access_error(userid, key_configured):
    """The error response this caller should get instead of an AI call, or None to proceed."""
    if key_configured and not userid:
        return json_error(401, "Please sign in to continue.")
    reason = subscription_block_reason(userid)
    if reason:
        return json_error(402, reason)
    return None


# MARQUEE M9: the throttle in front of the paid proxies (S0-2, findings D1/D4). Neither route
# had a limiter — verified live 2026-09-03, 12 rapid POSTs returned 200 twelve times with
# zero 429s. Both buckets are checked (see app/auth/ratelimit.py for why not one composite
# key); the per-IP one is consulted first so a flood is refused before the per-user bucket,
# and before the account lookup the subscription gate does, is reached at all.
def _rate_limit_error(ip, userid):
    """A 429 carrying Retry-After if this caller has spent either bucket, else None."""
    buckets = [(ai_ip_limiter, f"ip:{ip or '-'}")]
    if userid:
        buckets.append((ai_user_limiter, f"user:{userid}"))
    for limiter, key in buckets:
        if not limiter.allow(key):
            resp = json_error(429, "You're going a little fast for us — "
                                   "give it a moment and try again.")
            resp.headers["Retry-After"] = str(limiter.retry_after(key))
            return resp
    return None


# MARQUEE M9: the spend layers in front of the paid proxies (S0-5, finding H4). Two of the
# three apply here — the per-user daily budget and the global circuit breaker. They behave
# differently ON PURPOSE:
#
#   budget reached  -> 429 for THAT user. One account has used its allowance; everyone else
#                      is unaffected, so refusing is right and naming it is right.
#   circuit open    -> the request DEGRADES to the mock branch for everyone. A global spend
#                      incident should leave a working-but-dumber app, not a broken one, and
#                      the mock path already exists and is already exercised offline.
#
# The circuit does NOT relax the 401: the key is still configured, so a signed-out caller is
# still refused. Degrading is a spend decision, not an access decision.
# MARQUEE M11: the Free-tier daily AI allowance is enforced here, in front of the paid
# provider call. ai_allowance_state resolves the tier + the action limit; a Free user who is
# `over` is refused with a structured 429, and a successful call echoes the meter so the client
# counter ticks. Paid users are unlimited. Enforcement of the ACTION cap is behind
# FREE_TIER_AI_GATE_ENFORCED (observe mode until then); the global circuit breaker always
# applies. See MARQUEE_DECISIONS.md M11 and app/services/budget.py.
def _live_branch(userid, key_configured, cost_feature=None):
    """(use_live_provider, error_response, allowance).

    error_response non-None means refuse outright. `allowance` is the meter snapshot to echo on
    a successful live call (None on the mock / circuit-open paths, where nothing was metered).
    """
    if not key_configured:
        return False, None, None
    if budget.circuit_open():
        return False, None, None
    allowance = budget.ai_allowance_state(userid, feature=cost_feature)
    if allowance["over"]:
        return False, allowance_error(allowance), allowance
    return True, None, allowance


def _serve_ai(request, raw_body, user):
    """{feature, inputs} -> {"content":[{"type":"text","text":...}], "stop_reason"?}.

    The BLOCKING core of the route, unchanged by Phase 2 item 2 apart from this docstring and
    its name. It runs in a worker thread, which is where it already ran (FastAPI dispatches
    plain-`def` handlers to the anyio threadpool) — item 2 changed only how many of these may
    run at once, not what any one of them does. It stays a module-level function rather than a
    closure so the unit suite can call it directly, as it always has.

    MARQUEE M8 + M9. The one door to a model. S1-1, finding C1.2: this replaced
    /api/messages and /api/messages-claude, which forwarded a client-supplied `system`
    string, so any account holder could run arbitrary prompts on Wingman's keys.

    Order matters and is the same order the two old routes used, for the same reasons:
      throttle -> access -> feature lookup -> spend -> provider.
    The feature lookup sits BEFORE the spend layers and after the access ones, so a bad
    feature costs a 400 rather than a budget check, and an unauthenticated caller never
    learns which feature ids exist.

    The lane check sits in front of ALL of it, in handle_ai below — see there for why.
    """
    userid = user.id if user else None
    ip = client_ip(request)
    throttled = _rate_limit_error(ip, userid)
    if throttled:
        return throttled

    try:
        payload = json.loads(raw_body) if raw_body else {}
    except Exception:                                              # noqa: BLE001
        return json_error(400, "Malformed request body.")
    if not isinstance(payload, dict):
        return json_error(400, "Malformed request body.")

    name = payload.get("feature")
    # The provider is a property of the FEATURE, not of the route, so the access gate needs
    # to know which key it is gating on before it can answer. Resolve the feature first,
    # but only far enough to learn that — the prompt is not built until access is settled.
    try:
        feature = prompts.get_feature(name)
    except prompts.UnknownFeature:
        # No hint about which ids exist. The registry is the allow-list, and enumerating it
        # in an error message would hand back most of what S1-1 just took away.
        return json_error(400, "Unknown request.")

    key_configured = bool(ANTHROPIC_API_KEY if feature.provider == "claude"
                          else GEMINI_API_KEY)
    denied = _ai_access_error(userid, key_configured)
    if denied:
        return denied

    try:
        feature, system, user_content, max_tokens = prompts.build(name, payload.get("inputs"))
    except prompts.UnknownFeature:                                 # unreachable; belt
        return json_error(400, "Unknown request.")
    except Exception as e:                                         # noqa: BLE001
        # A malformed `inputs` is the caller's mistake, not a server fault — and it must not
        # reach a provider, which is where the money is.
        print(f"[WARN] Could not build feature {name!r}: {type(e).__name__}")
        return json_error(400, "That request could not be built.")

    cost_feature = feature.cost_feature or name
    live, refused, allowance = _live_branch(userid, key_configured, cost_feature)
    if refused:
        # A Free user has hit their daily allowance — record the cap-hit for the console's
        # tier metrics (§3.3). Only fires here, where `over` first blocks a call.
        touch_user_activity(userid, "ai_limit_hit")
        return refused

    touch_user_activity(userid, "ai_claude" if feature.provider == "claude" else "ai_gemini")
    if not live:
        return _mock_response(system, user_content, userid)
    if feature.provider == "claude":
        return _proxy_to_anthropic(feature, system, user_content, max_tokens, userid,
                                   cost_feature, allowance)
    return _proxy_to_gemini(system, user_content, max_tokens, userid, cost_feature, allowance)


# MARQUEE M9: the async shell that bounds the paid lane (Phase 2 item 2, finding M5).
#
# This is `async def` where the old handler was plain `def`, and that is the whole point.
# FastAPI runs an `async def` route ON the event loop and a plain `def` route in the 40-slot
# threadpool. Being on the loop is what lets a shed cost nothing: the lane is full, the
# caller is answered 503, and no threadpool slot is ever taken. Had this stayed synchronous,
# every request would first have to WIN a threadpool slot just to discover it should be shed
# — the flood would still drain the pool it is supposed to be kept out of.
#
# Nothing blocking may be added to this function. The three things it does — a counter check,
# and handing off to a thread — are all non-blocking. Both limiters, the subscription read,
# the budget lookups and the provider call itself are Supabase/HTTP round trips and stay
# inside _serve_ai, on the far side of to_thread.run_sync. Adding a blocking call HERE would
# stall the event loop for the entire process, which is a strictly worse version of the bug
# this fixes.
#
# abandon_on_cancel is left at its default of False: if the client disconnects mid-call,
# run_sync still waits for the worker thread to finish before returning. That is deliberate.
# Abandoning it would return control — and run the `finally` that releases the lane — while
# the thread is still holding a real provider connection, so the lane would report free while
# more than `limit` calls were genuinely in flight. A disconnected student costs one slot for
# the rest of one call; the alternative costs the bound itself.
@router.post("/api/ai")
async def handle_ai(request: Request, raw_body: bytes = Depends(ai_raw_body),
                    user: AuthedUser = Depends(get_optional_user)):
    """The route. Bounds the lane, then runs the blocking core in a worker thread."""
    if not ai_lane.try_acquire():
        return _shed_response()
    try:
        return await to_thread.run_sync(
            functools.partial(_serve_ai, request, raw_body, user))
    finally:
        ai_lane.release()
