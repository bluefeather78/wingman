"""AI proxy routes: /api/messages (Gemini) and /api/messages-claude (Anthropic).

Translated from server.py's handle_messages / proxy_to_gemini / mock_response /
handle_messages_claude / proxy_to_anthropic (docs/archive/PLAN_1_decompose.md). The client sends a
plain {system, userContent, useWebSearch, userid} body either way; the response is the
{"content":[{"type":"text","text":...}]} envelope for both live and mock, so script.js
doesn't branch on mode.
"""
import json
import urllib.error
import urllib.request

from fastapi import APIRouter, Request, Response, Depends

from app.config import (
    GEMINI_API_KEY, MESSAGES_MODEL, MESSAGES_MAX_TOKENS, MESSAGES_MAX_TOKENS_CEILING,
    ANTHROPIC_API_KEY, ANTHROPIC_URL, CLAUDE_MODEL,
    CLAUDE_MAX_TOKENS, CLAUDE_MAX_TOKENS_CEILING,
)
from app.core import (
    touch_user_activity, record_interactive_cost_async, log_conversation_async,
    record_api_error,
)
from app.deps import (json_response, json_error, subscription_block_reason, client_ip,
                      raw_body as raw_body_dep)
from app.auth import get_optional_user, AuthedUser
from app.services.ai import generate_mock_text
from wingman.gemini_common import call_gemini

router = APIRouter()

# A response header the capture middleware (app/main.py) skips over, so a provider failure
# recorded HERE with its real upstream status + error message is not ALSO recorded generically
# as a 5xx server_error. The header never reaches the client — the middleware strips it.
_ERROR_LOGGED_HEADER = "x-wingman-error-logged"


def _mark_logged(resp):
    """Tag a response as already recorded to api_errors, so the middleware doesn't double-log it."""
    try:
        resp.headers[_ERROR_LOGGED_HEADER] = "1"
    except Exception:                                              # noqa: BLE001
        pass
    return resp


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
    use_web_search = bool(payload.get("useWebSearch"))
    try:
        text, usage = call_gemini(
            system, user_content, GEMINI_API_KEY,
            use_web_search=use_web_search,
            max_tokens=_clamped_gemini_max_tokens(payload.get("maxTokens")),
            model=MESSAGES_MODEL,
        )
    except urllib.error.HTTPError as e:
        body = e.read()
        _record_provider_failure("gemini", "/api/messages", e.code, _provider_detail(body))
        return _mark_logged(Response(content=body, status_code=e.code,
                                     media_type="application/json"))
    except Exception as e:
        _record_provider_failure("gemini", "/api/messages", 0, str(e))
        return _mark_logged(json_error(502, str(e)))
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
    use_web_search = bool(payload.get("useWebSearch"))
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": _clamped_max_tokens(payload.get("maxTokens")),
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user_content}],
    }
    if use_web_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
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
        with urllib.request.urlopen(req) as upstream:
            data = upstream.read()
            resp = Response(content=data, status_code=upstream.status,
                            media_type="application/json")
    except urllib.error.HTTPError as e:
        body = e.read()
        _record_provider_failure("anthropic", "/api/messages-claude", e.code,
                                 _provider_detail(body))
        return _mark_logged(Response(content=body, status_code=e.code,
                                     media_type="application/json"))
    except Exception as e:
        _record_provider_failure("anthropic", "/api/messages-claude", 0, str(e))
        return _mark_logged(json_error(502, str(e)))
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


@router.post("/api/messages")
def handle_messages(request: Request, raw_body: bytes = Depends(raw_body_dep),
                    user: AuthedUser = Depends(get_optional_user)):
    # Identity comes from the token if present, else None. Signed-out is allowed only on the
    # mock branch — see _ai_access_error (M9 / S0-1).
    userid = user.id if user else None
    denied = _ai_access_error(userid, bool(GEMINI_API_KEY))
    if denied:
        return denied
    touch_user_activity(userid, "ai_gemini")
    ip = client_ip(request)
    if GEMINI_API_KEY:
        return _proxy_to_gemini(raw_body, ip, userid)
    return _mock_response(raw_body, ip, userid)


@router.post("/api/messages-claude")
def handle_messages_claude(request: Request, raw_body: bytes = Depends(raw_body_dep),
                           user: AuthedUser = Depends(get_optional_user)):
    userid = user.id if user else None
    denied = _ai_access_error(userid, bool(ANTHROPIC_API_KEY))
    if denied:
        return denied
    touch_user_activity(userid, "ai_claude")
    ip = client_ip(request)
    if ANTHROPIC_API_KEY:
        return _proxy_to_anthropic(raw_body, ip, userid)
    return _mock_response(raw_body, ip, userid)
