#!/usr/bin/env python3
"""Shared helper for wingman's offline agent scripts (scrape_opportunities.py,
check_deadlines.py, check_reviews.py) that call the Gemini API directly (not through
server.py's /api/messages proxy, since these run standalone, off the dev server).

Replaces claude_common.py as of the Gemini migration (2026-08-18) — same public shape
(`call_gemini`/`extract_json`/`estimate_cost`) so the three calling scripts only needed
their import line and api-key env var swapped, not their call sites. claude_common.py
is left in place, unused, in case of rollback; it is not imported by anything anymore.

Why Gemini over the two alternatives considered:
- DeepSeek was ruled out entirely — its API has no hosted/server-executed search tool at
  all (per DeepSeek's own tool-calling docs: "the functionality... needs to be provided
  by the user. The model itself does not execute specific functions"). Using DeepSeek
  here would mean standing up and hosting a separate search backend (Bing/Brave/Tavily)
  and hand-rolling the agentic search loop — a new subsystem, not a swap.
- Gemini's `googleSearch` tool is architecturally the same shape as Anthropic's
  `web_search`: one call in, the model autonomously decides how many searches to run,
  results come back with citations in the same response. Genuinely closer to a drop-in
  replacement, and its tokens are far cheaper (~4-10x), which dominates cost here since
  in testing, search fees were a small fraction of a seed's total cost vs. token spend.

IMPORTANT gap vs. Anthropic: Gemini's googleSearch tool has NO server-enforced cap on
search count (no equivalent of Anthropic's `max_uses`) as of this writing — confirmed via
Gemini's own docs, which state the model "automatically determines" how many searches to
run with no limiting parameter documented. `max_searches` below is therefore a SOFT,
prompt-level request only (folded into the system prompt), not a hard guarantee — unlike
the old Anthropic path, a run could still search more than requested. Cheaper tokens make
this less financially dangerous than the original Anthropic timeout incident, but it is
not eliminated; keep an eye on `web_search_requests` in returned usage to catch runaway
seeds.

SECOND gap vs. Anthropic, discovered empirically (2026-08-18, seed-0 retest of scrape_
opportunities.py's national mode): unlike Anthropic's web_search, which reliably invokes a
search when the prompt instructs it to, Gemini's googleSearch tool is NOT guaranteed to fire
at all — the model can choose to answer entirely from its own training data and skip search
silently. Observed directly: a national-scope seed returned 3 candidates with
`groundingMetadata.webSearchQueries` empty (0 searches) and a token-only cost ($0.0009, no
$0.014/search fee), meaning the model recalled well-known programs from memory instead of
verifying anything live. There is no error, warning, or distinct status code for this — the
only tell is `usage["server_tool_use"]["web_search_requests"] == 0` on a call where you
expected real search activity.

This is LOW risk for scrape_opportunities.py (worst case: a recalled-from-memory candidate is
already in the catalog and gets deduped, as happened in the observed case, or is a genuinely
real but not-live-verified program that a human still reviews before is_active=true is ever
set).

This is a HIGHER risk for check_deadlines.py and check_reviews.py specifically, because their
entire premise is "answer must reflect CURRENT state, not the model's training-data snapshot."
A silent skip-search there could write back a stale or hallucinated status/deadline/review
verdict with no signal it happened, and last_checked_at / last_reviewed_at would still get
stamped with the current timestamp — making a stale answer look freshly verified. Any future
work on those two scripts should check `usage["server_tool_use"]["web_search_requests"]` per
item and flag (not silently trust) any zero-search result before writing it back, rather than
assuming a 200-OK response means the answer was actually verified live.

THIRD finding (2026-08-18, direct empirical test against three forcing strategies, all failed):
there is currently NO reliable way to force googleSearch to fire for a query the model is
already confident it knows from training data (tested on "prestigious national summer
programs" — RSI/TASS/MITES/etc., all answered from memory every time):
1. A hard "you MUST call googleSearch, never answer from memory" instruction in
   systemInstruction — no effect, 0 searches, no groundingMetadata in the response.
2. Moving that same directive into the user turn instead of systemInstruction — no effect,
   same result.
3. `toolConfig: {"functionCallingConfig": {"mode": "ANY"}}` (the mechanism that force-invokes
   user-declared functions) — do NOT use this with the built-in googleSearch tool. It does not
   force a search; it silently breaks generation instead, burning 1,000-3,000+ thinking tokens
   and returning a response with **no "candidates" key at all** (not even an error/finishReason
   to explain why) — confirmed reproducible.
`max_searches` (the soft prompt-level cap already implemented below) and the "MUST search"
instruction are still worth keeping — they may help on queries the model is less confident
about — but do not treat either as a real guarantee. The only dependable mitigation right now
is what check_deadlines.py/check_reviews.py/scrape_opportunities.py already do: check
`usage["server_tool_use"]["web_search_requests"]` after the fact and flag zero-search results,
rather than trying to prevent them up front.
"""
import json
import re
import urllib.request

GEMINI_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# gemini-2.5-flash (originally chosen as the "stable, best price-performance" model per
# Gemini's own docs) returned a 404 on first real use: "no longer available to new
# users... use models/gemini-3.6-flash". Confirmed live against the API on 2026-08-18 —
# revisit this pin periodically, these model IDs churn.
MODEL = "gemini-3.6-flash"

# Verified pricing (see Gemini API pricing page) — used to turn a response's usage/
# grounding data into a real dollar estimate rather than a guess. gemini-3.6-flash rates
# (valid through Dec 31, 2026 per the pricing page); revisit if MODEL above changes.
INPUT_PRICE_PER_TOKEN = 0.75 / 1_000_000
OUTPUT_PRICE_PER_TOKEN = 3.75 / 1_000_000
# $14 per 1,000 grounded search requests, after a 5,000-request/month free allowance
# shared across Gemini 3.x models (gemini-2.5-flash's exact free-tier terms should be
# reconfirmed against the live pricing page before relying on this for large runs).
WEB_SEARCH_PRICE_PER_SEARCH = 14 / 1000


def call_gemini(system, user_content, api_key, use_web_search=True, max_tokens=4000, timeout=120,
                 max_searches=None, thinking_level="low"):
    """POSTs directly to the Gemini generateContent API. Returns (text, usage) — text is
    the concatenated text output with any ```json fences stripped, usage is a dict shaped
    like {"input_tokens", "output_tokens", "server_tool_use": {"web_search_requests"}} —
    deliberately mirroring claude_common.call_claude()'s usage shape so estimate_cost()
    and calling scripts that read usage["server_tool_use"]["web_search_requests"] don't
    need to change.

    FOURTH finding (2026-08-18): gemini-3.6-flash is a thinking/reasoning model whose
    internal "thinking" tokens are drawn from the SAME maxOutputTokens budget as the
    visible output text (confirmed via usageMetadata.thoughtsTokenCount) — this is what
    silently truncated check_reviews.py's review_summary/review_sources at max_tokens=700:
    thinking alone consumed 673 of the 700 tokens, leaving ~23 for the actual JSON answer,
    finishReason came back MAX_TOKENS, and extract_json()'s truncation-repair then produced
    "valid" but garbled/cut-off JSON with no error or warning to signal it happened.

    Fix: pass thinkingConfig.thinkingLevel (Gemini 3.x's replacement for the legacy,
    2.5-era thinking_budget — the two cannot be combined in one request, that's a 400
    error). Confirmed empirically against gemini-3.6-flash: "low" cut thoughtsTokenCount
    from 673 to ~90-95 on both a trivial prompt and a real check_reviews.py-shaped prompt
    with web_search enabled, changing finishReason from MAX_TOKENS to STOP while still
    producing a complete, well-formed answer. Defaults to "low" here since every current
    caller (scrape/deadlines/reviews) wants a fast structured-extraction answer, not deep
    reasoning; pass thinking_level=None to omit thinkingConfig entirely (legacy behavior,
    Gemini's own default thinking level for the model)."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if thinking_level:
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": thinking_level}
    if use_web_search:
        body["tools"] = [{"googleSearch": {}}]
        # Forced-search instruction: see module docstring's "SECOND gap" — googleSearch is
        # NOT guaranteed to fire even when the caller's prompt asks for a search; the model
        # can silently answer from training data instead, with no error/warning. This is a
        # prompt-level nudge, not a guarantee (no equivalent of a forced tool_choice exists
        # for this tool as of this writing) — callers must still check
        # usage["server_tool_use"]["web_search_requests"] rather than trust this alone.
        body["systemInstruction"]["parts"][0]["text"] += (
            "\n\nYou MUST call the googleSearch tool at least once before writing your "
            "final answer — this is a hard requirement, not a suggestion. Do this even if "
            "you are confident you already know the answer from your training data: that "
            "data can be stale or wrong, and an unverified answer is not acceptable here. "
            "Never answer directly from memory alone. If an initial search doesn't return "
            "anything useful, try at least one different query before giving up."
        )
        if max_searches:
            # Soft cap only — see module docstring. googleSearch has no max_uses
            # equivalent, so this is a request to the model, not an enforced limit.
            body["systemInstruction"]["parts"][0]["text"] += (
                f"\n\nSearch budget: use at most {max_searches} web searches total for "
                f"this request — stop searching and answer with what you have once you "
                f"reach that many."
            )
    req = urllib.request.Request(
        GEMINI_URL_TMPL.format(model=MODEL),
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())

    candidate = (data.get("candidates") or [{}])[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "\n".join(p.get("text", "") for p in parts if "text" in p)
    text = re.sub(r"```json|```", "", text).strip()

    grounding = candidate.get("groundingMetadata") or {}
    web_search_requests = len(grounding.get("webSearchQueries") or [])
    usage_raw = data.get("usageMetadata") or {}
    usage = {
        "input_tokens": usage_raw.get("promptTokenCount", 0),
        "output_tokens": usage_raw.get("candidatesTokenCount", 0),
        "server_tool_use": {"web_search_requests": web_search_requests},
    }
    return text, usage


def estimate_cost(usage):
    """Real dollar cost of one call, from the usage dict call_gemini() returns: token
    cost plus $0.014 per web search Gemini actually executed (grounding requests are
    billed per search query, same principle as Anthropic's web_search)."""
    input_tokens = usage.get("input_tokens", 0)
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
    still-open string/array/object before parsing.

    Identical to claude_common.extract_json() — duplicated rather than imported so this
    module has zero dependency on the Anthropic-specific file post-migration."""
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
        # Occasionally responses carry raw control characters (e.g. literal newlines)
        # inside string values, which strict JSON disallows — retry permissively.
        return json.loads(candidate, strict=False)
