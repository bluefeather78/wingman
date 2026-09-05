"""AI proxy routes: /api/messages (Gemini) and /api/messages-claude (Anthropic).

Translated from server.py's handle_messages / proxy_to_gemini / mock_response /
handle_messages_claude / proxy_to_anthropic (docs/archive/PLAN_1_decompose.md). The client sends a
plain {system, userContent, userid} body either way; the response is the
{"content":[{"type":"text","text":...}]} envelope for both live and mock, so script.js
doesn't branch on mode.

The client may still send "useWebSearch" — it is read and IGNORED. Whether the server runs a
paid web search is a server-side decision now; see _USE_WEB_SEARCH below (M9 / S0-3).
"""
import json
import urllib.error
import urllib.request

from fastapi import APIRouter, Request, Response, Depends

from app.config import (
    GEMINI_API_KEY, MESSAGES_MODEL, MESSAGES_MAX_TOKENS, MESSAGES_MAX_TOKENS_CEILING,
    ANTHROPIC_API_KEY, ANTHROPIC_URL, CLAUDE_MODEL,
    CLAUDE_MAX_TOKENS, CLAUDE_MAX_TOKENS_CEILING, AI_MAX_BODY_BYTES,
    AI_UPSTREAM_TIMEOUT_SECONDS, ANTHROPIC_MAX_WEB_SEARCH_USES,
)
from app.core import (
    touch_user_activity, record_interactive_cost_async, log_conversation_async,
    record_api_error,
)
from app.deps import (json_response, json_error, subscription_block_reason, client_ip,
                      capped_raw_body, _ERROR_LOGGED_HEADER)
from app.auth import get_optional_user, AuthedUser
from app.auth.ratelimit import ai_ip_limiter, ai_user_limiter
from app.services.ai import generate_mock_text
from app.services import budget
from wingman.gemini_common import call_gemini

router = APIRouter()

# MARQUEE M9: the request cap in front of the paid proxies (S0-2). Both routes read the body
# through this rather than app.deps.raw_body, so an over-limit request is 413'd by the
# dependency — before the handler exists to make an upstream call.
ai_raw_body = capped_raw_body(AI_MAX_BODY_BYTES)

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


def _clamped_max_tokens(requested):
    """Client-requested output budget, clamped into [CLAUDE_MAX_TOKENS, ceiling]."""
    try:
        n = int(requested)
    except (TypeError, ValueError):
        return CLAUDE_MAX_TOKENS
    return max(CLAUDE_MAX_TOKENS, min(n, CLAUDE_MAX_TOKENS_CEILING))


def _clamped_gemini_max_tokens(requested):
    """Same, for /api/messages. A caller whose answer length scales with its input (profile
    tag extraction and enrichment) asks for its own budget; everyone else gets the uniform
    default. Never BELOW the default, so this can only ever raise a call's headroom."""
    try:
        n = int(requested)
    except (TypeError, ValueError):
        return MESSAGES_MAX_TOKENS
    return max(MESSAGES_MAX_TOKENS, min(n, MESSAGES_MAX_TOKENS_CEILING))


def _mock_response(raw_body, ip, userid):
    try:
        payload = json.loads(raw_body)
        system = payload.get("system", "") or ""
        user_content = payload.get("userContent", "")
    except Exception:
        system, user_content = "", ""
    text = generate_mock_text(system, user_content)
    resp = json_response(200, {"content": [{"type": "text", "text": text}]})
    log_conversation_async(userid, ip, "mock", system,
                           user_content if isinstance(user_content, str) else json.dumps(user_content),
                           text)
    return resp


def _proxy_to_gemini(raw_body, ip, userid):
    try:
        payload = json.loads(raw_body)
    except Exception:
        return json_error(400, "Malformed request body.")
    system = payload.get("system", "") or ""
    user_content = payload.get("userContent", "")
    user_content = user_content if isinstance(user_content, str) else json.dumps(user_content)
    try:
        text, usage = call_gemini(
            system, user_content, GEMINI_API_KEY,
            use_web_search=_USE_WEB_SEARCH,
            max_tokens=_clamped_gemini_max_tokens(payload.get("maxTokens")),
            model=MESSAGES_MODEL,
            # Explicit, not inherited from gemini_common's 120s default: this is an
            # interactive request holding an anyio threadpool slot, not a batch agent that
            # can afford to wait (M9 / S0-4).
            timeout=AI_UPSTREAM_TIMEOUT_SECONDS,
        )
    except urllib.error.HTTPError as e:
        body = e.read()
        _record_provider_failure("gemini", "/api/messages", e.code, _provider_detail(body))
        return _provider_error_response(e.code)
    except Exception as e:
        _record_provider_failure("gemini", "/api/messages", 0, str(e))
        return _mark_logged(json_error(502, _PROVIDER_DEFAULT))
    resp = json_response(200, {"content": [{"type": "text", "text": text}]})
    log_conversation_async(userid, ip, "live", system, user_content, text)
    record_interactive_cost_async("interactive_gemini", usage, MESSAGES_MODEL,
                                  userid=userid, system=system)
    return resp


def _proxy_to_anthropic(raw_body, ip, userid):
    try:
        payload = json.loads(raw_body)
    except Exception:
        return json_error(400, "Malformed request body.")
    system = payload.get("system", "") or ""
    user_content = payload.get("userContent", "")
    user_content = user_content if isinstance(user_content, str) else json.dumps(user_content)
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": _clamped_max_tokens(payload.get("maxTokens")),
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
    try:
        # No timeout here at all until S0-4: a hung socket permanently consumed one of the
        # anyio threadpool's 40 slots, capacity that never returned until a restart.
        with urllib.request.urlopen(req, timeout=AI_UPSTREAM_TIMEOUT_SECONDS) as upstream:
            data = upstream.read()
            resp = Response(content=data, status_code=upstream.status,
                            media_type="application/json")
    except urllib.error.HTTPError as e:
        body = e.read()
        _record_provider_failure("anthropic", "/api/messages-claude", e.code,
                                 _provider_detail(body))
        return _provider_error_response(e.code)
    except Exception as e:
        _record_provider_failure("anthropic", "/api/messages-claude", 0, str(e))
        return _mark_logged(json_error(502, _PROVIDER_DEFAULT))
    # Best-effort logging + cost, after the response body is captured.
    try:
        resp_json = json.loads(data)
        response_text = "\n".join(
            b.get("text", "") for b in resp_json.get("content", []) if b.get("type") == "text"
        )
        u = resp_json.get("usage") or {}
        record_interactive_cost_async("interactive_claude", {
            "input_tokens": u.get("input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
            "server_tool_use": u.get("server_tool_use") or {},
        }, CLAUDE_MODEL, userid=userid, system=system)
    except Exception:
        response_text = ""
    log_conversation_async(userid, ip, "live", system, user_content, response_text)
    return resp


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
def _live_branch(userid, key_configured):
    """(use_live_provider, error_response). error_response non-None means refuse outright."""
    if not key_configured:
        return False, None
    if budget.circuit_open():
        return False, None
    over = budget.over_user_budget(userid)
    if over:
        return False, json_error(429, over)
    return True, None


@router.post("/api/messages")
def handle_messages(request: Request, raw_body: bytes = Depends(ai_raw_body),
                    user: AuthedUser = Depends(get_optional_user)):
    # Identity comes from the token if present, else None. Signed-out is allowed only on the
    # mock branch — see _ai_access_error (M9 / S0-1).
    userid = user.id if user else None
    ip = client_ip(request)
    throttled = _rate_limit_error(ip, userid)
    if throttled:
        return throttled
    denied = _ai_access_error(userid, bool(GEMINI_API_KEY))
    if denied:
        return denied
    live, refused = _live_branch(userid, bool(GEMINI_API_KEY))
    if refused:
        return refused
    touch_user_activity(userid, "ai_gemini")
    if live:
        return _proxy_to_gemini(raw_body, ip, userid)
    return _mock_response(raw_body, ip, userid)


@router.post("/api/messages-claude")
def handle_messages_claude(request: Request, raw_body: bytes = Depends(ai_raw_body),
                           user: AuthedUser = Depends(get_optional_user)):
    userid = user.id if user else None
    ip = client_ip(request)
    throttled = _rate_limit_error(ip, userid)
    if throttled:
        return throttled
    denied = _ai_access_error(userid, bool(ANTHROPIC_API_KEY))
    if denied:
        return denied
    live, refused = _live_branch(userid, bool(ANTHROPIC_API_KEY))
    if refused:
        return refused
    touch_user_activity(userid, "ai_claude")
    if live:
        return _proxy_to_anthropic(raw_body, ip, userid)
    return _mock_response(raw_body, ip, userid)
