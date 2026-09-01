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

FIFTH finding (2026-08-23) — the THIRD finding above is CONFIRMED, and the search decision is
outright NON-DETERMINISTIC. scrape_opportunities.py seed 51 was run twice with an identical
command and prompt (see agent_logs/scraper_20260821-*.log) and returned `0 search(es)` the
first time and `6 search(es)` the second. There is a bias toward answering from memory when
the topic feels familiar — an angle like "pre college programs hosted in US universities"
went silent while "reddit /summerprogramresults" searched — but nothing about the request
controls it. Output format (JSON-array-only vs prose) was also tested and is NOT established
as a cause: n=1 per condition against that noise floor proves nothing. Thinking budget was
ruled out (162 thought tokens with no search, vs 107 with one).
    Practical consequence: RETRY, don't prompt harder. A silent call is cheap (no $0.014
    search fees, ~$0.006-0.01 for a scraper seed) and seed 51 shows a retry can flip to a
    real search. Retry on `web_search_requests == 0`, then flag whatever is still silent.

SIXTH finding (2026-08-23) — what a grounded response carries, and what this module used to
throw away. When search DOES fire, `candidate.groundingMetadata` holds four keys:
`searchEntryPoint`, `groundingChunks`, `groundingSupports`, `webSearchQueries`.
- `groundingChunks[].web.uri` is a `vertexaisearch.cloud.google.com/grounding-api-redirect/…`
  URL. It RESOLVES TO THE EXACT REAL PAGE in one ordinary (free) HTTP redirect hop.
  `web.title` is only a bare domain string ("nih.gov"); `web.domain` does not exist on
  `v1beta/generateContent`.
- `groundingSupports[]` gives `segment{startIndex, endIndex, text}` plus
  `groundingChunkIndices` — per-answer-span attribution, not just whole-response.
This matters because the model does NOT reliably put a retrieved URL in its own answer text:
with no search it writes URLs from memory, and they come out with the right host and a path
off by one segment (measured: 30/116 hard 404s in one scrape batch, every one a constructed
deep path). The grounding chunk is the only place the real URL exists. Measured head-to-head,
4/4 model-typed URLs 404'd where 4/4 grounding-resolved URLs returned 200 — including the
catalog's own dead `training.nih.gov/research-training/sip/` vs the real
`training.nih.gov/research-training/pb/sip/`. Pass `return_grounding=True` to get it, and see
url_validate.py for resolving and validating. Do not "simplify" that away.

SEVENTH finding (2026-08-23) — ASKING FOR JSON COLLAPSES THE SEARCH RATE. Measured, not
inferred, and it is the reason the scraper's two-phase split works.

Controlled A/B, one opportunity (`ec17455`, TASS/Telluride), identical research
instructions, identical user turn, identical max_tokens/model/thinking level, arms
ALTERNATED so drift cannot confound them. The only difference was the closing paragraph —
"respond with ONLY a raw JSON object matching this schema" versus "write up what you find
in plain prose":

    prose   4/4 calls searched    7 searches   34 grounding chunks   $0.1053
    json    0/4 calls searched    0 searches    0 grounding chunks   $0.0054

That is consistent with everything else on record. `check_reviews.py`, whose prompt demands
JSON, has made **22 searches across 3089 row-checks (0.007/item)** in its entire history,
and 5 further probe calls across 2 rows were silent 5/5. The scraper's prose phase 1, after
the rewrite, ran 213 searches across 40 seeds (5.3/item) with 0 silent.

READ THE LIMIT CAREFULLY, because a previous session over-claimed here and had to retract.
This is a large shift in PROBABILITY, not a deterministic gate, and both exceptions are on
record: run id=33 (2026-08-21, pre-rewrite, JSON prompt) fired 6 searches, and run id=48
(2026-08-23, post-rewrite, prose prompt) fired none. So:

    CORRECT   "a JSON-shaped answer format collapses the probability of a search"
    WRONG     "a JSON-only prompt suppresses search" (deterministic — id=33 refutes it)

The THIRD finding stands unchanged: there is still no reliable way to FORCE a search. What
changed is that there is now a measured way to stop discouraging one — ask for prose, and
extract the JSON in a second, search-free call. That is exactly the scraper's two-phase
design, and its justification is no longer only "a JSON answer cannot carry grounding back".

COST CONSEQUENCE, which is why this is not simply switched on everywhere: a searching prose
call measured $0.0263 against $0.00135 for a silent JSON one — roughly 20x, dominated by the
$0.014/search fee, not tokens. Making an agent actually search is a real spending decision,
not a free correctness fix.
"""
import atexit
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Rate limiting: a minimum delay between every Gemini API call, enforced process-wide.
# 5s is the value that resolved the repeated HTTP 429s this pipeline used to hit; treat it
# as the floor, not a starting point to tune down casually.
#
# Configurable in three layers, each overriding the one above:
#   1. DEFAULT_MIN_DELAY_SECS below
#   2. the GEMINI_MIN_DELAY_SECS env var (how server.py passes a value into a subprocess
#      without every script needing a new call signature)
#   3. set_min_delay(), which the scripts' --min-delay flag calls
DEFAULT_MIN_DELAY_SECS = 5
DEFAULT_TIMEOUT_SECS = 120

_last_call_time = 0.0


def _env_number(name, fallback):
    """Read a numeric env var, falling back silently on anything unparseable — a typo in
    .env should not take the whole pipeline down, and the default is always safe."""
    raw = os.environ.get(name, "")
    if not raw:
        return fallback
    try:
        value = float(raw)
    except ValueError:
        print(f"[WARN] {name}={raw!r} is not a number; using {fallback}.")
        return fallback
    return value if value >= 0 else fallback


_min_delay_secs = _env_number("GEMINI_MIN_DELAY_SECS", DEFAULT_MIN_DELAY_SECS)
_default_timeout_secs = _env_number("GEMINI_TIMEOUT_SECS", DEFAULT_TIMEOUT_SECS)


def set_min_delay(secs):
    """Override the minimum delay between Gemini calls. Called by each script's
    --min-delay flag. Warns below the 5s floor rather than refusing, since a small
    sample run at a lower delay is a legitimate (if riskier) thing to want."""
    global _min_delay_secs
    if secs is None:
        return _min_delay_secs
    secs = max(0.0, float(secs))
    if secs < DEFAULT_MIN_DELAY_SECS:
        print(f"[WARN] Gemini min delay set to {secs}s, below the {DEFAULT_MIN_DELAY_SECS}s "
              f"floor that fixed past HTTP 429 rate limiting. Expect 429s on longer runs.")
    _min_delay_secs = secs
    return _min_delay_secs


def get_min_delay():
    return _min_delay_secs


def set_default_timeout(secs):
    """Override the per-request HTTP read timeout used when a caller doesn't pass one
    explicitly. Note a client-side timeout does NOT stop or refund the server-side work
    already in flight — too short a timeout means paying for answers you never see."""
    global _default_timeout_secs
    if secs is None:
        return _default_timeout_secs
    _default_timeout_secs = max(1.0, float(secs))
    return _default_timeout_secs


def get_default_timeout():
    return _default_timeout_secs


def _enforce_rate_limit():
    """Sleep until at least the configured minimum delay has passed since the last call.

    Note the timestamp is stamped at call START, not completion, so the delay and the
    API call's own latency overlap: an agent whose calls take 3s sees ~5s per item at a
    5s delay, not 8s. Any extra per-item sleep in a calling script shorter than this
    window is therefore absorbed by it and has no effect."""
    global _last_call_time
    now = time.time()
    elapsed = now - _last_call_time
    if elapsed < _min_delay_secs:
        sleep_time = _min_delay_secs - elapsed
        time.sleep(sleep_time)
    _last_call_time = time.time()


# Parallel execution prevention: scripts using web_search share the same quota.
# Only one script can run at a time. Implemented via a lockfile that persists
# for the duration of the script's execution.
_lock_file = os.path.join(os.path.dirname(__file__), ".gemini_web_search.lock")
_lock_acquired = False


def _pid_is_alive(pid):
    """Best-effort liveness check for a PID recorded in a lock file. Stdlib-only (no
    extra deps per CLAUDE.md); Windows-first since this repo runs on Windows, with a
    POSIX fallback via os.kill(pid, 0). Any ambiguous/unknown result defaults to
    "assume alive" — a false "dead" verdict would let two web-search calls race on
    the shared quota, which is the exact thing this lock exists to prevent, so
    failing safe here means falling back to the old fail-fast behavior, not silently
    stealing a live lock."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in result.stdout
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True  # unknown state -> assume alive, fail safe


def _acquire_web_search_lock(_retried=False):
    """Acquire an exclusive lock for web-search-enabled scripts. Fails fast if
    another *live* instance is already running. Called automatically on first
    call_gemini(..., use_web_search=True). Registers cleanup on exit.

    The lock file's content is the acquiring process's PID (previously it was empty)
    so a process that finds an existing lock can check whether its creator is still
    alive, rather than relying solely on the 24h age heuristic below. This matters
    because server.py runs this same code inside a ThreadingHTTPServer request thread
    on every web-search-enabled /api/messages call — if that thread died abnormally,
    the old sys.exit(1) here (a SystemExit, which bypasses server.py's
    `except Exception` handler around call_gemini()) produced an empty HTTP response
    client-side and left the lock orphaned for up to 24h, blocking every subsequent
    web-search call project-wide. PID-liveness lets a server restart (new PID) self-heal
    immediately. Raises RuntimeError (instead of the old sys.exit(1)) on genuine,
    live contention so callers like server.py's proxy_to_gemini() can catch it and
    return a proper error response instead of the request thread dying silently."""
    global _lock_acquired
    if _lock_acquired:
        return  # Already acquired in this process

    # Clean up stale lock (older than 24 hours) — handles the case where the PID that
    # created it has since been reused by an unrelated process (PID check alone can't
    # catch that).
    try:
        if os.path.exists(_lock_file):
            age_secs = time.time() - os.path.getmtime(_lock_file)
            if age_secs > 86400:  # 24 hours
                os.remove(_lock_file)
    except Exception:
        pass  # Best-effort cleanup

    # Try to acquire lock exclusively (fails if file already exists)
    try:
        fd = os.open(_lock_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        _lock_acquired = True
        atexit.register(_release_web_search_lock)
        return
    except FileExistsError:
        pass  # existing lock -> check liveness below before treating as contention
    except Exception as e:
        raise RuntimeError(f"Could not acquire web_search lock: {e}") from e

    owner_pid = None
    try:
        with open(_lock_file, "r") as f:
            owner_pid = int(f.read().strip())
    except Exception:
        owner_pid = None  # unreadable/empty (e.g. a pre-hardening lock file) -> treat as stale

    if not _retried and (owner_pid is None or not _pid_is_alive(owner_pid)):
        try:
            os.remove(_lock_file)
        except Exception:
            pass
        _acquire_web_search_lock(_retried=True)
        return

    msg = (f"Another script using web_search is already running (pid {owner_pid}). "
           f"Remove {_lock_file} manually if you believe this is wrong.")
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise RuntimeError(msg)


def _release_web_search_lock():
    """Release the web-search lock. Called automatically on exit."""
    global _lock_acquired
    if _lock_acquired:
        try:
            os.remove(_lock_file)
        except Exception:
            pass  # Best-effort cleanup


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


def call_gemini(system, user_content, api_key, use_web_search=True, max_tokens=4000, timeout=None,
                 max_searches=None, thinking_level="low", model=None, return_grounding=False):
    """MARQUEE M9 (MARQUEE_DECISIONS.md): this is a money seam. Changing whether/how callers
    spend here — `use_web_search`, `max_searches`, `max_tokens` ceilings, the model pin — is a
    marquee change: get Shama's approval first and make it its own dedicated commit.

    POSTs directly to the Gemini generateContent API. Returns (text, usage) — text is
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
    Gemini's own default thinking level for the model).

    `model` overrides the module-level MODEL pin for this call only — added for
    server.py's /api/messages proxy (script.js's interactive UI calls), which uses the
    cheaper/faster gemini-3.5-flash-lite instead of this module's gemini-3.6-flash, since
    those calls are synchronous within a page interaction rather than an offline batch
    job. Defaults to None (use MODEL) so scrape_opportunities.py/check_deadlines.py/
    check_reviews.py are unaffected."""
    # Parallel execution prevention: if this call uses web_search, acquire an exclusive lock
    # to prevent multiple scripts from running simultaneously (they share the same quota).
    if use_web_search:
        _acquire_web_search_lock()

    body = {
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if timeout is None:
        timeout = _default_timeout_secs
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
        GEMINI_URL_TMPL.format(model=model or MODEL),
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )

    # Enforce minimum 5-second delay between API calls (Gemini rate limit policy).
    _enforce_rate_limit()

    # Rate limit handling: on 429 error, try one more time then abort.
    # Gemini's googleSearch quota can be exhausted during heavy batch runs.
    # On first 429, sleep briefly and retry once. If it fails again, propagate
    # the error — the calling script must handle the abort (typically logging
    # the item as failed and continuing to the next one).
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"[WARN] HTTP 429 (rate limited), retrying once after delay...")
            time.sleep(5)  # Wait before retry
            _enforce_rate_limit()
            # Retry exactly once — if this fails, let it propagate
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        else:
            raise  # Other HTTP errors propagate immediately

    candidate = (data.get("candidates") or [{}])[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "\n".join(p.get("text", "") for p in parts if "text" in p)
    text = re.sub(r"```json|```", "", text).strip()

    grounding = candidate.get("groundingMetadata") or {}
    # The QUERY STRINGS, not just how many there were. These were reduced to a len() until
    # 2026-08-23, which meant every caller paid for searches it could never see or tune —
    # "why did this seed return nothing?" was unanswerable. Additive, so existing callers
    # that only read web_search_requests are unaffected.
    queries = list(grounding.get("webSearchQueries") or [])
    usage_raw = data.get("usageMetadata") or {}
    usage = {
        "input_tokens": usage_raw.get("promptTokenCount", 0),
        "output_tokens": usage_raw.get("candidatesTokenCount", 0),
        "server_tool_use": {
            "web_search_requests": len(queries),
            "web_search_queries": queries,
        },
    }
    if return_grounding:
        # Third element only on request, so the five existing `text, usage = ...` call
        # sites keep working. `raw` is the untouched API response — no run in this repo
        # saved one before 2026-08-23, which is why several past failures were
        # undiagnosable after the fact.
        return text, usage, {"grounding": grounding, "raw": data}
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


# --------------------------------------------------------------------------- embeddings
#
# The catalog-matching pipeline (OPPORTUNITY_MATCHING_PLAN.md, Phase 5) embeds each active
# opportunity and each student profile theme, then matches by cosine similarity. The SAME
# pinned model + dimensionality MUST be used on both sides, or the cosine between a row vector
# and a theme vector is meaningless — that is why this is one pin, here, next to the pricing.
#
# MODEL-ID CHURN WARNING (see the MODEL comment above — this repo has been 404'd before):
# EMBED_MODEL is UNVERIFIED against the live API from the worktree it was written in. Confirm
# it resolves (a 404 means it was renamed) before relying on a real run, exactly as MODEL /
# MESSAGES_MODEL are periodically reconfirmed. gemini-embedding-001 supports a configurable
# outputDimensionality; 768 keeps the per-row payload ~9MB across ~1,500 rows (the plan's
# budget). If you must swap to text-embedding-004 it is natively 768-dim (ignore EMBED_DIM).
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768
GEMINI_EMBED_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
# batchEmbedContents accepts many requests per call; keep chunks modest so one oversized POST
# can't time out a whole backfill. The activation hook embeds ONE row at a time; only a full
# catalog backfill exercises the batching.
EMBED_BATCH_SIZE = 100
# Embedding input pricing (per token). gemini-embedding-001 is far cheaper than a generateContent
# call (no output tokens, no reasoning). VERIFY against the live pricing page before a large
# backfill — like every price constant here, this is an estimate feeding a local cost figure,
# never a bill.
EMBED_INPUT_PRICE_PER_TOKEN = 0.15 / 1_000_000


def estimate_embed_cost(usage):
    """Dollar cost of an embedding call from its usage dict ({"input_tokens": N}). Embeddings
    have no output/search cost — input tokens only."""
    return usage.get("input_tokens", 0) * EMBED_INPUT_PRICE_PER_TOKEN


def call_gemini_embed(texts, api_key, model=None, output_dim=None, timeout=None):
    """MARQUEE M9 (MARQUEE_DECISIONS.md): a money seam — a new paid call path. Approved for the
    matching pipeline; changing the model/dim or adding callers is a marquee change.

    Embed a list of texts via Gemini's batchEmbedContents endpoint. Returns
    (vectors, usage) where `vectors` is a list of float lists aligned 1:1 with `texts`
    (empty input -> []), and `usage` is {"input_tokens": N} for cost estimation. Chunks at
    EMBED_BATCH_SIZE so a large backfill can't build one oversized request.

    input_tokens is APPROXIMATE (~chars/4) — batchEmbedContents does not reliably return a
    per-request token count, and this only feeds a local cost estimate, never a bill."""
    if not texts:
        return [], {"input_tokens": 0}
    model = model or EMBED_MODEL
    dim = output_dim if output_dim is not None else EMBED_DIM
    url = GEMINI_EMBED_URL_TMPL.format(model=model)
    vectors = []
    approx_tokens = 0
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        chunk = texts[start:start + EMBED_BATCH_SIZE]
        requests_body = []
        for t in chunk:
            t = "" if t is None else str(t)
            approx_tokens += max(1, len(t) // 4)
            entry = {"model": f"models/{model}", "content": {"parts": [{"text": t}]}}
            if dim:
                entry["outputDimensionality"] = dim
            requests_body.append(entry)
        req = urllib.request.Request(
            url,
            data=json.dumps({"requests": requests_body}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        )
        _enforce_rate_limit()
        try:
            with urllib.request.urlopen(req, timeout=timeout or _default_timeout_secs) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("[WARN] HTTP 429 on embed (rate limited), retrying once after delay...")
                time.sleep(5)
                _enforce_rate_limit()
                with urllib.request.urlopen(req, timeout=timeout or _default_timeout_secs) as resp:
                    data = json.loads(resp.read())
            else:
                raise
        embeddings = data.get("embeddings") or []
        for emb in embeddings:
            vectors.append(list(emb.get("values") or []))
        # Defensive: the API should return one embedding per request, in order. If a chunk
        # comes back short, pad with empty vectors so alignment with `texts` is preserved —
        # a caller reading vectors[i] for texts[i] must never silently get another row's vector.
        while len(vectors) < start + len(chunk):
            vectors.append([])
    return vectors, {"input_tokens": approx_tokens}


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
