"""AI proxy routes: /api/messages (Gemini) and /api/messages-claude (Anthropic).

Translated from server.py's handle_messages / proxy_to_gemini / mock_response /
handle_messages_claude / proxy_to_anthropic (PLAN_1_decompose.md). The client sends a
plain {system, userContent, useWebSearch, userid} body either way; the response is the
{"content":[{"type":"text","text":...}]} envelope for both live and mock, so script.js
doesn't branch on mode.
"""
import json
import urllib.error
import urllib.request

from fastapi import APIRouter, Request, Response

from app.config import (
    GEMINI_API_KEY, MESSAGES_MODEL, MESSAGES_MAX_TOKENS,
    ANTHROPIC_API_KEY, ANTHROPIC_URL, CLAUDE_MODEL,
    CLAUDE_MAX_TOKENS, CLAUDE_MAX_TOKENS_CEILING,
)
from app.core import (
    touch_user_activity, record_interactive_cost_async, log_conversation_async,
)
from app.deps import json_response, json_error, subscription_block_reason, client_ip
from app.services.ai import generate_mock_text
from gemini_common import call_gemini

router = APIRouter()


def _userid_from_body(raw_body):
    try:
        return json.loads(raw_body).get("userid")
    except Exception:
        return None


def _clamped_max_tokens(requested):
    """Client-requested output budget, clamped into [CLAUDE_MAX_TOKENS, ceiling]."""
    try:
        n = int(requested)
    except (TypeError, ValueError):
        return CLAUDE_MAX_TOKENS
    return max(CLAUDE_MAX_TOKENS, min(n, CLAUDE_MAX_TOKENS_CEILING))


def _mock_response(raw_body, ip):
    try:
        payload = json.loads(raw_body)
        system = payload.get("system", "") or ""
        user_content = payload.get("userContent", "")
        userid = payload.get("userid")
    except Exception:
        system, user_content, userid = "", "", None
    text = generate_mock_text(system, user_content)
    resp = json_response(200, {"content": [{"type": "text", "text": text}]})
    log_conversation_async(userid, ip, "mock", system,
                           user_content if isinstance(user_content, str) else json.dumps(user_content),
                           text)
    return resp


def _proxy_to_gemini(raw_body, ip):
    try:
        payload = json.loads(raw_body)
    except Exception:
        return json_error(400, "Malformed request body.")
    system = payload.get("system", "") or ""
    user_content = payload.get("userContent", "")
    user_content = user_content if isinstance(user_content, str) else json.dumps(user_content)
    userid = payload.get("userid")
    use_web_search = bool(payload.get("useWebSearch"))
    try:
        text, usage = call_gemini(
            system, user_content, GEMINI_API_KEY,
            use_web_search=use_web_search, max_tokens=MESSAGES_MAX_TOKENS,
            model=MESSAGES_MODEL,
        )
    except urllib.error.HTTPError as e:
        return Response(content=e.read(), status_code=e.code, media_type="application/json")
    except Exception as e:
        return json_error(502, str(e))
    resp = json_response(200, {"content": [{"type": "text", "text": text}]})
    log_conversation_async(userid, ip, "live", system, user_content, text)
    record_interactive_cost_async("interactive_gemini", usage, MESSAGES_MODEL,
                                  userid=userid, system=system)
    return resp


def _proxy_to_anthropic(raw_body, ip):
    try:
        payload = json.loads(raw_body)
    except Exception:
        return json_error(400, "Malformed request body.")
    system = payload.get("system", "") or ""
    user_content = payload.get("userContent", "")
    user_content = user_content if isinstance(user_content, str) else json.dumps(user_content)
    userid = payload.get("userid")
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
        return Response(content=e.read(), status_code=e.code, media_type="application/json")
    except Exception as e:
        return json_error(502, str(e))
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


@router.post("/api/messages")
async def handle_messages(request: Request):
    raw_body = await request.body()
    userid = _userid_from_body(raw_body)
    reason = subscription_block_reason(userid)
    if reason:
        return json_error(402, reason)
    touch_user_activity(userid, "ai_gemini")
    ip = client_ip(request)
    if GEMINI_API_KEY:
        return _proxy_to_gemini(raw_body, ip)
    return _mock_response(raw_body, ip)


@router.post("/api/messages-claude")
async def handle_messages_claude(request: Request):
    raw_body = await request.body()
    userid = _userid_from_body(raw_body)
    reason = subscription_block_reason(userid)
    if reason:
        return json_error(402, reason)
    touch_user_activity(userid, "ai_claude")
    ip = client_ip(request)
    if ANTHROPIC_API_KEY:
        return _proxy_to_anthropic(raw_body, ip)
    return _mock_response(raw_body, ip)
