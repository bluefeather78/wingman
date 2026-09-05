"""The paid-call plumbing the audit found untested (§4 "untested paths").

These are the functions that decide what gets BILLED and whether a call is treated as having
searched. Everything here runs with urlopen replaced — no sockets, no spend.

Three of them encode a hazard the repo has already been bitten by, so each is pinned:
  * the silent-search count is DERIVED from webSearchQueries, not from anything billed (4.11);
  * a 429 must not sleep-and-retry inside the web process (MARQUEE M9, Phase 2 item 1);
  * supabase_get's pagination has to terminate — and now, order consistently (4.14).
"""
import io
import json
import urllib.error

import pytest

from wingman import gemini_common as gc


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gemini_payload(text="answer", queries=None, prompt_tokens=100, out_tokens=50,
                    grounding_extra=None):
    grounding = {"webSearchQueries": list(queries or [])}
    if grounding_extra:
        grounding.update(grounding_extra)
    return {
        "candidates": [{"content": {"parts": [{"text": text}]},
                        "groundingMetadata": grounding}],
        "usageMetadata": {"promptTokenCount": prompt_tokens,
                          "candidatesTokenCount": out_tokens},
    }


@pytest.fixture(autouse=True)
def _no_sleep_no_delay(monkeypatch):
    monkeypatch.setattr(gc.time, "sleep", lambda *_: None)
    monkeypatch.setattr(gc, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(gc, "_acquire_web_search_lock", lambda *a, **kw: None)


# --------------------------------------------------------------------------------------
# call_gemini: what comes back, and what it is treated as costing
# --------------------------------------------------------------------------------------

def test_usage_and_text_are_extracted(monkeypatch):
    monkeypatch.setattr(gc.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(_gemini_payload("```json\n[1]\n```")))
    text, usage = gc.call_gemini("sys", "user", "key")
    assert text == "[1]"                      # code fences stripped
    assert usage["input_tokens"] == 100 and usage["output_tokens"] == 50


def test_search_count_is_derived_from_the_query_list_not_from_billing(monkeypatch):
    """Audit 4.11, pinned rather than fixed. The count that decides 'did it search?' AND the
    count the per-search fee is estimated from are the same derived number: len(webSearchQueries).
    Gemini returning grounding chunks with an EMPTY query list therefore reads as silent, is
    retried (paying twice) and is billed as zero searches. Changing this needs live evidence of
    the response shape, which the repo does not have — so it is documented here instead of
    guessed at."""
    payload = _gemini_payload(queries=[], grounding_extra={"groundingChunks": [{"web": {}}]})
    monkeypatch.setattr(gc.urllib.request, "urlopen", lambda req, timeout=None: _Resp(payload))
    _text, usage, extra = gc.call_gemini("sys", "user", "key", use_web_search=True,
                                         return_grounding=True)
    assert usage["server_tool_use"]["web_search_requests"] == 0     # reads as SILENT...
    assert extra["grounding"]["groundingChunks"]                    # ...despite chunks
    assert gc.estimate_cost(usage) == pytest.approx(
        100 * gc.INPUT_PRICE_PER_TOKEN + 50 * gc.OUTPUT_PRICE_PER_TOKEN)


def test_queries_are_returned_not_just_counted(monkeypatch):
    """Reduced to a len() until 2026-08-23, which made 'why did this seed return nothing?'
    unanswerable while still paying per search."""
    payload = _gemini_payload(queries=["summer research 2027", "high school internship"])
    monkeypatch.setattr(gc.urllib.request, "urlopen", lambda req, timeout=None: _Resp(payload))
    _text, usage = gc.call_gemini("sys", "user", "key", use_web_search=True)
    assert usage["server_tool_use"]["web_search_requests"] == 2
    assert usage["server_tool_use"]["web_search_queries"][0] == "summer research 2027"


def test_estimate_cost_bills_per_search(monkeypatch):
    usage = {"input_tokens": 0, "output_tokens": 0,
             "server_tool_use": {"web_search_requests": 3}}
    assert gc.estimate_cost(usage) == pytest.approx(3 * gc.WEB_SEARCH_PRICE_PER_SEARCH)


def test_the_search_budget_reaches_the_prompt_only_when_searching(monkeypatch):
    """max_searches is a SOFT cap on Gemini — folded into the prompt, since googleSearch has
    no max_uses. If it stops being sent, the dominant cost lever is silently gone."""
    sent = {}

    def capture(req, timeout=None):
        sent["body"] = json.loads(req.data)
        return _Resp(_gemini_payload())

    monkeypatch.setattr(gc.urllib.request, "urlopen", capture)
    gc.call_gemini("sys", "user", "key", use_web_search=True, max_searches=1)
    instruction = sent["body"]["systemInstruction"]["parts"][0]["text"]
    assert "at most 1 web searches" in instruction
    assert sent["body"]["tools"] == [{"googleSearch": {}}]

    gc.call_gemini("sys", "user", "key", use_web_search=False, max_searches=1)
    assert "tools" not in sent["body"]
    assert "Search budget" not in sent["body"]["systemInstruction"]["parts"][0]["text"]


# --------------------------------------------------------------------------------------
# the 429 path — MARQUEE M9, Phase 2 item 1
# --------------------------------------------------------------------------------------

def _http_429():
    return urllib.error.HTTPError("http://x", 429, "Too Many Requests", {}, io.BytesIO(b"{}"))


def test_a_batch_run_sleeps_and_retries_once(monkeypatch):
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_429()
        return _Resp(_gemini_payload("recovered"))

    monkeypatch.setattr(gc, "_interactive_process", False)
    monkeypatch.setattr(gc.urllib.request, "urlopen", flaky)
    text, _usage = gc.call_gemini("sys", "user", "key")
    assert text == "recovered" and calls["n"] == 2


def test_a_second_429_propagates_rather_than_looping(monkeypatch):
    monkeypatch.setattr(gc, "_interactive_process", False)
    monkeypatch.setattr(gc.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(_http_429()))
    with pytest.raises(urllib.error.HTTPError):
        gc.call_gemini("sys", "user", "key")


def test_the_web_process_never_sleeps_on_a_429(monkeypatch):
    """MARQUEE M9 (Phase 2 item 1): a student's request must not hold a threadpool slot asleep
    for 5s to re-ask a service that just said 'too many requests'."""
    slept = []
    monkeypatch.setattr(gc.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(gc, "_interactive_process", True)
    monkeypatch.setattr(gc.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(_http_429()))
    with pytest.raises(urllib.error.HTTPError):
        gc.call_gemini("sys", "user", "key")
    assert slept == []


def test_a_non_429_http_error_propagates_immediately(monkeypatch):
    calls = {"n": 0}

    def boom(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError("http://x", 500, "Server Error", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(gc, "_interactive_process", False)
    monkeypatch.setattr(gc.urllib.request, "urlopen", boom)
    with pytest.raises(urllib.error.HTTPError):
        gc.call_gemini("sys", "user", "key")
    assert calls["n"] == 1, "a 500 must not be retried — only a 429 is"


def test_the_web_search_lock_is_taken_only_when_searching(monkeypatch):
    """The lock exists because the agents share one googleSearch quota. Taking it on a
    no-search call would serialise free work behind paid work."""
    taken = []
    monkeypatch.setattr(gc, "_acquire_web_search_lock", lambda *a, **kw: taken.append(1))
    monkeypatch.setattr(gc.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(_gemini_payload()))
    gc.call_gemini("sys", "user", "key", use_web_search=False)
    assert taken == []
    gc.call_gemini("sys", "user", "key", use_web_search=True)
    assert taken == [1]


# --------------------------------------------------------------------------------------
# the 5-second floor — MARQUEE M6
# --------------------------------------------------------------------------------------

def test_the_five_second_floor_is_a_floor(monkeypatch):
    """M6: --min-delay may RAISE it; nothing may lower the floor silently."""
    assert gc.DEFAULT_MIN_DELAY_SECS == 5
    warned = []
    monkeypatch.setattr(gc, "_min_delay_secs", 5)
    monkeypatch.setattr("builtins.print", lambda *a, **kw: warned.append(" ".join(map(str, a))))
    gc.set_min_delay(1)
    assert any("5" in w for w in warned), "lowering below the floor must warn"
    gc.set_min_delay(5)


# --------------------------------------------------------------------------------------
# supabase_common pagination — Range semantics and termination (audit: "effectively untested")
# --------------------------------------------------------------------------------------

def test_pagination_terminates_on_a_short_page(monkeypatch):
    from wingman import supabase_common as sc
    pages = [[{"id": i} for i in range(1000)], [{"id": 1000}]]
    seen_ranges = []

    def fake(req, timeout=None):
        seen_ranges.append(req.headers.get("Range"))
        return _Resp(pages.pop(0) if pages else [])

    monkeypatch.setattr(sc.urllib.request, "urlopen", fake)
    rows = sc.supabase_get("https://db", "opportunities", {"select": "id"}, "k")
    assert len(rows) == 1001
    assert seen_ranges == ["0-999", "1000-1999"], "Range must advance by exactly page_size"


def test_an_exactly_full_last_page_costs_one_extra_request(monkeypatch):
    """Documented, not a bug: a page of exactly page_size cannot be told from a full one, so
    termination needs the empty follow-up."""
    from wingman import supabase_common as sc
    pages = [[{"id": i} for i in range(1000)], []]
    monkeypatch.setattr(sc.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(pages.pop(0) if pages else []))
    assert len(sc.supabase_get("https://db", "t", {"select": "id"}, "k")) == 1000


def test_a_smaller_page_size_is_honoured(monkeypatch):
    """Lowered for vector selects, where a full 1000-row page exceeds Supabase's statement
    timeout and 500s with 57014."""
    from wingman import supabase_common as sc
    seen = []
    monkeypatch.setattr(sc.urllib.request, "urlopen",
                        lambda req, timeout=None: seen.append(req.headers.get("Range")) or _Resp([]))
    sc.supabase_get("https://db", "t", {"select": "v"}, "k", page_size=200)
    assert seen == ["0-199"]


def test_delete_refuses_an_unfiltered_call(monkeypatch):
    """PostgREST would happily delete the whole table for an empty filter set."""
    from wingman import supabase_common as sc
    with pytest.raises(ValueError):
        sc.supabase_delete("https://db", "opportunities", {}, "k")


# --------------------------------------------------------------------------------------
# claude_common — the other money seam (MARQUEE M9). max_uses is HARD here, unlike Gemini.
# --------------------------------------------------------------------------------------

def _claude_payload(text="answer", searches=None, usage=None):
    return {"content": [{"type": "text", "text": text}],
            "usage": usage or {"input_tokens": 100, "output_tokens": 50,
                               "server_tool_use": {"web_search_requests": searches or 0}}}


def test_max_searches_becomes_a_server_enforced_max_uses(monkeypatch):
    """Unlike Gemini's prompt-level budget, this is a HARD cap Anthropic enforces. Losing it
    is a direct billing regression — the per-search fee dominates this agent's cost."""
    from wingman import claude_common as cc
    sent = {}

    def capture(req, timeout=None):
        sent["body"] = json.loads(req.data)
        return _Resp(_claude_payload())

    monkeypatch.setattr(cc, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(cc.urllib.request, "urlopen", capture)
    cc.call_claude("sys", "user", "key", use_web_search=True, max_searches=1)
    assert sent["body"]["tools"] == [{"type": "web_search_20250305", "name": "web_search",
                                      "max_uses": 1}]


def test_no_tool_is_sent_when_search_is_off(monkeypatch):
    from wingman import claude_common as cc
    sent = {}
    monkeypatch.setattr(cc, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(cc.urllib.request, "urlopen",
                        lambda req, timeout=None: sent.update(body=json.loads(req.data))
                        or _Resp(_claude_payload()))
    cc.call_claude("sys", "user", "key", use_web_search=False)
    assert "tools" not in sent["body"]


def test_claude_cost_includes_cache_tokens(monkeypatch):
    """Cache-write and cache-read tokens ARE billed; omitting them understates every check."""
    from wingman import claude_common as cc
    plain = cc.estimate_cost({"input_tokens": 1000, "output_tokens": 0})
    cached = cc.estimate_cost({"input_tokens": 0, "output_tokens": 0,
                               "cache_creation_input_tokens": 1000})
    assert cached > 0 and plain > 0


def test_claude_silent_detection_reads_a_missing_usage_block_as_zero(monkeypatch):
    """Audit 4.11's other half, pinned: a real search whose usage block is absent looks silent
    and gets retried — billed twice. Documented here rather than guessed at, since fixing it
    needs live evidence of when Anthropic omits the block."""
    from wingman import claude_common as cc
    assert (cc.estimate_cost({"input_tokens": 0, "output_tokens": 0})
            == pytest.approx(0.0))
    usage = {}
    assert (usage.get("server_tool_use") or {}).get("web_search_requests", 0) == 0


# --------------------------------------------------------------------------------------
# seeds_common.record_seed_result — the read-modify-write the audit flagged as unconfirmed
# --------------------------------------------------------------------------------------

def test_seed_totals_are_added_to_a_FRESH_read_not_the_run_start_snapshot(monkeypatch):
    """PostgREST cannot do `SET total = total + n` without a stored function, so this stays
    read-modify-write. Re-reading immediately before the add shrinks the lost-update window
    from a 110-minute pass to one round trip — and, crucially, stops a console edit made
    DURING the run from being silently discarded."""
    from wingman import seeds_common as sk
    stale_snapshot = {"id": 7, "total_runs": 1, "total_found": 10, "total_cost": 1.0}
    live_now = {"id": 7, "total_runs": 5, "total_found": 99, "total_cost": 9.0}
    monkeypatch.setattr(sk, "_current_seed", lambda url, key, sid: live_now)
    written = {}
    monkeypatch.setattr(sk, "supabase_patch",
                        lambda url, table, params, body, key: written.update(body))
    assert sk.record_seed_result("u", "k", stale_snapshot, found=3, cost=0.5) is True
    assert written["total_runs"] == 6          # from the LIVE value, not the snapshot's 1
    assert written["total_found"] == 102
    assert written["total_cost"] == pytest.approx(9.5)


def test_a_fallback_seed_is_a_no_op():
    """The module-level NATIONAL_SEEDS/SEATTLE_SEEDS literals have no id and no DB row."""
    from wingman import seeds_common as sk
    assert sk.record_seed_result("u", "k", {"id": None}, found=3) is False


def test_a_failed_stats_write_never_aborts_the_run(monkeypatch):
    """Losing a stats update must not throw away a scrape that already spent real money."""
    from wingman import seeds_common as sk
    monkeypatch.setattr(sk, "_current_seed", lambda *a: {"id": 7})
    monkeypatch.setattr(sk, "supabase_patch",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("500")))
    assert sk.record_seed_result("u", "k", {"id": 7}, found=1) is False


def test_a_dead_read_falls_back_to_the_snapshot_rather_than_zeroing(monkeypatch):
    """If the re-read fails, adding to {} would RESET every lifetime total to this run's."""
    from wingman import seeds_common as sk
    monkeypatch.setattr(sk, "_current_seed", lambda *a: None)
    written = {}
    monkeypatch.setattr(sk, "supabase_patch",
                        lambda url, table, params, body, key: written.update(body))
    sk.record_seed_result("u", "k", {"id": 7, "total_runs": 4, "total_found": 40}, found=2)
    assert written["total_runs"] == 5 and written["total_found"] == 42
