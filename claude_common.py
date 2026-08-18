#!/usr/bin/env python3
"""Shared helper for wingman's offline agent scripts (scrape_opportunities.py,
check_deadlines.py) that call the Anthropic Messages API directly (not through
server.py's /api/messages proxy, since these run standalone, off the dev server).

Mirrors two pieces of already-proven logic from script.js so the new scripts reuse
the same call shape and JSON-recovery behavior the app's live features depend on:
- `call_claude()` mirrors `callClaude()` (script.js) — same model, same
  `web_search_20250305` tool wiring — but returns the response's `usage` block too,
  since these scripts need to report real dollar cost (script.js's browser-side
  caller never needed to).
- `extract_json()` is a Python port of `extractJSON()` (script.js) — brace/bracket
  depth scan that tolerates trailing commentary and best-effort repairs a
  truncated/token-limited response.
"""
import json
import re
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

# Verified pricing (see plan doc / Anthropic web search tool docs) — used to turn a
# response's `usage` block into a real dollar estimate rather than a guess.
INPUT_PRICE_PER_TOKEN = 3 / 1_000_000
OUTPUT_PRICE_PER_TOKEN = 15 / 1_000_000
WEB_SEARCH_PRICE_PER_SEARCH = 0.01


def call_claude(system, user_content, api_key, use_web_search=True, max_tokens=4000, timeout=120,
                 max_searches=10):
    """POSTs directly to the Anthropic Messages API. Returns (text, usage) — text is
    the concatenated text content with any ```json fences stripped, usage is the raw
    `usage` dict from the response (for cost accounting).

    `max_searches` maps to the web_search tool's `max_uses`, a HARD, server-enforced cap
    on how many searches Claude runs for one request — the system prompt can only nudge
    search behavior, this actually bounds it. It matters for two reasons: each search is
    billed at $0.01 *and* pulls its results into the context as input tokens, so an
    unbounded agentic loop makes both cost and wall-clock time open-ended. Bounding wall
    time is the point: the first national scrape lost 11 of 16 seeds to client-side read
    timeouts on broad queries that searched far longer than expected — and a client
    timeout doesn't stop (or refund) the server-side work already in flight.

    Exceeding the cap is not a request failure: the offending search comes back as a
    `max_uses_exceeded` error block and Claude finishes its answer with what it already
    has, so a too-low cap degrades result quality rather than erroring out."""
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
    }
    if use_web_search:
        tool = {"type": "web_search_20250305", "name": "web_search"}
        if max_searches:
            tool["max_uses"] = max_searches
        body["tools"] = [tool]
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    text = "\n".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    text = re.sub(r"```json|```", "", text).strip()
    return text, data.get("usage", {}) or {}


def estimate_cost(usage):
    """Real dollar cost of one call, from its `usage` block: token cost (including
    cache-write/cache-read tokens, which are billed) plus $0.01 per web_search the
    model actually executed server-side."""
    input_tokens = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    output_tokens = usage.get("output_tokens", 0)
    web_searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
    return (
        input_tokens * INPUT_PRICE_PER_TOKEN
        + output_tokens * OUTPUT_PRICE_PER_TOKEN
        + web_searches * WEB_SEARCH_PRICE_PER_SEARCH
    )


def extract_json(text):
    """Python port of script.js's extractJSON(): finds the first JSON value (object
    or array) in `text` by scanning brace/bracket depth (string/escape-aware) rather
    than naive first/last index, so trailing commentary doesn't break parsing. If the
    JSON was cut off mid-generation, attempts a best-effort repair by closing any
    still-open string/array/object before parsing."""
    m = re.search(r"[\{\[]", text)
    if not m:
        raise ValueError("No JSON found in response")
    start = m.start()
    in_string = False
    escaped = False
    depth = 0
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end != -1:
        candidate = text[start:end + 1]
    else:
        # Truncated mid-structure — attempt a best-effort repair.
        candidate = text[start:]
        if in_string:
            candidate += '"'
        candidate = re.sub(r",\s*$", "", candidate)
        stack = []
        scan_in_string = False
        scan_escaped = False
        for ch in candidate:
            if scan_in_string:
                if scan_escaped:
                    scan_escaped = False
                elif ch == "\\":
                    scan_escaped = True
                elif ch == '"':
                    scan_in_string = False
                continue
            if ch == '"':
                scan_in_string = True
                continue
            if ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack:
                    stack.pop()
        while stack:
            opener = stack.pop()
            candidate += "}" if opener == "{" else "]"

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Claude's web_search results occasionally carry raw control characters
        # (e.g. literal newlines) inside string values, which strict JSON
        # disallows — retry permissively before giving up.
        return json.loads(candidate, strict=False)
