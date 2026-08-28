#!/usr/bin/env python3
"""Shared helper for wingman's offline agent scripts (scrape_opportunities.py,
check_deadlines.py) that call the Anthropic Messages API directly (not through
server.py's /api/messages proxy, since these run standalone, off the dev server).

Mirrors two pieces of already-proven logic from script.js so the new scripts reuse
the same call shape and JSON-recovery behavior the app's live features depend on:
- `call_claude()` mirrors `callClaude()` (script.js) — same model (Haiku, as pinned by
  MODEL below and by server.py's CLAUDE_MODEL), same `web_search_20250305` tool wiring —
  but returns the response's `usage` block too,
  since these scripts need to report real dollar cost (script.js's browser-side
  caller never needed to).
- `extract_json()` is a Python port of `extractJSON()` (script.js) — brace/bracket
  depth scan that tolerates trailing commentary and best-effort repairs a
  truncated/token-limited response.
"""
import json
import os
import re
import time
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
# Haiku, matching server.py's CLAUDE_MODEL and check_deadlines.py's own pin. This module
# was left on Sonnet after those two moved, which made the repo's Anthropic model depend on
# which entry point you came through — and, worse, made this file's PRICE constants wrong
# for the calls server.py actually costs with them (see below).
MODEL = "claude-haiku-4-5-20251001"

# Rate limiting. This did NOT exist until now: check_deadlines.py carried a comment
# claiming "rate limiting is enforced at the API level in gemini_common.call_gemini()",
# but it calls THIS module, not gemini_common, so its batch mode ran completely
# unthrottled — the most likely explanation for the grounding-quota exhaustion its own
# docstring records after a full pass. Mirrors gemini_common's three-layer config
# (default -> CLAUDE_MIN_DELAY_SECS env -> set_min_delay()).
#
# The default is 0, unlike gemini_common's 5. That is deliberate: call_claude() is also
# on server.py's INTERACTIVE path (the on-demand per-opportunity deadline check behind
# GET /api/opportunities/<id>/deadline), where a process-wide inter-call delay would make
# one user's request block on another's. Throttling is a batch concern, so
# check_deadlines.py's --min-delay defaults to 5 and applies it at startup; the
# interactive path leaves it at 0 and is unaffected.
DEFAULT_MIN_DELAY_SECS = 0
BATCH_MIN_DELAY_SECS = 5  # what check_deadlines.py's --min-delay defaults to
DEFAULT_TIMEOUT_SECS = 120

_last_call_time = 0.0


def _env_number(name, fallback):
    raw = os.environ.get(name, "")
    if not raw:
        return fallback
    try:
        value = float(raw)
    except ValueError:
        print(f"[WARN] {name}={raw!r} is not a number; using {fallback}.")
        return fallback
    return value if value >= 0 else fallback


_min_delay_secs = _env_number("CLAUDE_MIN_DELAY_SECS", DEFAULT_MIN_DELAY_SECS)
_default_timeout_secs = _env_number("CLAUDE_TIMEOUT_SECS", DEFAULT_TIMEOUT_SECS)


def set_min_delay(secs):
    """Override the minimum delay between Anthropic calls (--min-delay)."""
    global _min_delay_secs
    if secs is None:
        return _min_delay_secs
    _min_delay_secs = max(0.0, float(secs))
    return _min_delay_secs


def get_min_delay():
    return _min_delay_secs


def set_default_timeout(secs):
    """Override the HTTP read timeout used when a caller doesn't pass one explicitly.
    A client-side timeout does not stop or refund server-side work already in flight."""
    global _default_timeout_secs
    if secs is None:
        return _default_timeout_secs
    _default_timeout_secs = max(1.0, float(secs))
    return _default_timeout_secs


def get_default_timeout():
    return _default_timeout_secs


def _enforce_rate_limit():
    """Sleep until the configured minimum delay has passed since the last call. The
    timestamp is stamped at call start, so this delay overlaps the call's own latency."""
    global _last_call_time
    now = time.time()
    elapsed = now - _last_call_time
    if elapsed < _min_delay_secs:
        time.sleep(_min_delay_secs - elapsed)
    _last_call_time = time.time()

# Pricing for MODEL above — used to turn a response's `usage` block into a real dollar
# estimate rather than a guess. These MUST track MODEL: server.py imports them to cost
# every `interactive_claude` call, and those run on CLAUDE_MODEL (Haiku). While this file
# said Sonnet, they were Sonnet's $3/$15 — so interactive Claude spend was estimated at
# 3x what it actually cost. Historical `agent_runs`/`user_costs` rows written before
# 2026-08-22 carry that overstatement and are not retroactively corrected.
# Claude Haiku 4.5: $1.00 / MTok input, $5.00 / MTok output.
INPUT_PRICE_PER_TOKEN = 1 / 1_000_000
OUTPUT_PRICE_PER_TOKEN = 5 / 1_000_000
# Web search is billed per search at a flat rate, not per model, so this is unchanged.
WEB_SEARCH_PRICE_PER_SEARCH = 0.01


def call_claude(system, user_content, api_key, use_web_search=True, max_tokens=4000, timeout=None,
                 max_searches=10):
    """MARQUEE M9 (MARQUEE_DECISIONS.md): this is a money seam. Changing whether/how callers spend
    here — `use_web_search`, `max_searches`, `max_tokens` ceilings, the model pin (MODEL) — is a
    marquee change: get Shama's approval first and make it its own dedicated commit.

    POSTs directly to the Anthropic Messages API. Returns (text, usage) — text is
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
    if timeout is None:
        timeout = _default_timeout_secs
    _enforce_rate_limit()
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
