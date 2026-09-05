"""MARQUEE M1: refresh_opportunities reads the live page, and NEVER falls back to memory.

These tests pin the invariant that was once reversed silently: a fetch failure must skip the
row, not answer from the model's training data. The fetch (`fetch`, page_text.fetch_page_text's
`(text, reason)` contract) and the model call (`call_gemini`) are both stubbed, so nothing
touches the network.
"""
import json
import urllib.error

import pytest

from agents import refresh_opportunities as r


OPP = {"id": "ec1", "name": "Marine Bio Institute", "org": "Woods Hole",
       "url": "https://example.org/mbi", "summary": "old"}


def _no_gemini(*a, **k):
    raise AssertionError("call_gemini must NOT be called when the page could not be fetched — "
                         "that would be the memory-fallback M1 forbids")


def test_unfetchable_page_skips_and_never_calls_the_model(monkeypatch):
    monkeypatch.setattr(r, "call_gemini", _no_gemini)
    fetch = lambda url: (None, "http-403")
    info, cost, reason = r.check_one(OPP, "gk", fetch=fetch)
    assert reason == "no-fetch"
    assert info == {}
    assert cost == 0.0   # a failed fetch is free — no model call was made


def test_blank_shell_is_treated_as_no_fetch(monkeypatch):
    # page_text returns reason 'empty-or-js' for a near-empty (JS-rendered) body. That is a
    # failure, not a page — skip, never let the model "quote" from nothing.
    monkeypatch.setattr(r, "call_gemini", _no_gemini)
    fetch = lambda url: (None, "empty-or-js")
    info, cost, reason = r.check_one(OPP, "gk", fetch=fetch)
    assert reason == "no-fetch" and info == {}


def test_page_text_is_handed_to_the_model_and_parsed(monkeypatch):
    seen = {}

    def fake_gemini(system, user_content, key, **kw):
        seen["system"] = system
        seen["user"] = user_content
        seen["web"] = kw.get("use_web_search")
        return (json.dumps({"eligibility": "grades 9-12", "cost": "Free", "url": "junk"}),
                {"input_tokens": 10, "output_tokens": 10})

    monkeypatch.setattr(r, "call_gemini", fake_gemini)
    fetch = lambda url: ("Open to students in grades 9-12. Free of charge.", "ok")
    info, cost, reason = r.check_one(OPP, "gk", fetch=fetch)

    assert reason == "ok"
    assert info["eligibility"] == "grades 9-12"
    # The actual page text must be in the prompt — this is what "reads the page" means.
    assert "grades 9-12" in seen["user"]
    # Extraction runs with search OFF (correct: the page is already in the prompt), and the
    # prompt must be the page-reading one, never the old memory prompt.
    assert seen["web"] is False
    assert "from the text of its OWN web page" in seen["system"]
    assert "recall" not in seen["system"].lower()


def test_unreadable_model_output_is_unparsed_not_a_write(monkeypatch):
    monkeypatch.setattr(r, "call_gemini",
                        lambda *a, **k: ("not json at all", {"input_tokens": 5, "output_tokens": 5}))
    fetch = lambda url: ("some real page text", "ok")
    info, cost, reason = r.check_one(OPP, "gk", fetch=fetch)
    assert reason == "unparsed" and info is None


def test_extracted_url_is_dropped_by_clean_update_dict():
    # P3 still holds under M1: even a page-read url is never written by this agent.
    update = r.clean_update_dict({"eligibility": "grades 9-12", "url": "https://example.org/x"})
    assert "url" not in update
    assert update["eligibility"] == "grades 9-12"


def test_prompt_no_longer_claims_no_web_access():
    # The exact strings the silent reversal introduced must be gone.
    sys_prompt = r.build_system(OPP)
    assert "NO WEB ACCESS" not in sys_prompt
    assert "extract" in sys_prompt.lower()


def test_numeric_zero_cost_is_dropped_not_written():
    # The model returns a bare "0"/"$0"/"0.00" for a free program. `price` already
    # carries Free/Paid, so a numeric-zero cost is noise that would OVERWRITE a good
    # curated value ("Free", "No cost; volunteer hours count...") — measured on the
    # 2026-08-28 sample (ASPIRE, ARC, Legacy). It must be dropped.
    for zero in ("0", "0.00", "$0", "$0.00", " 0 ", "$0.0"):
        assert "cost" not in r.clean_update_dict({"cost": zero}), zero


def test_real_cost_values_survive():
    # Anything that is not numeric-zero is preserved verbatim, including the word "Free"
    # (a real, informative cost string) and complex/range prices.
    for keep in ("$700", "$10-20", "Free", "$2,400",
                 "$675.00 per course; $1,225.00 for Full Day Combo"):
        assert r.clean_update_dict({"cost": keep})["cost"] == keep, keep


def test_activation_queue_fetch_degrades_when_migration_not_run(monkeypatch):
    # If db/activation_refresh_schema.sql has not been run, the queue column is absent and a
    # select naming it 400s. The agent must keep running (drop the column, latch the drain
    # off) rather than dying — the metadata refresh itself does not depend on the column.
    calls = []

    def fake_get(url, table, params, key):
        calls.append(params["select"])
        if r.ACTIVATION_REFRESH_COLUMN in params["select"]:
            raise urllib.error.HTTPError(url, 400, "column does not exist", {}, None)
        return [{"id": "ec1"}]

    monkeypatch.setattr(r, "supabase_get", fake_get)
    monkeypatch.setattr(r, "_queue_col_enabled", True)
    sel = "id,name," + r.ACTIVATION_REFRESH_COLUMN
    rows = r._get_opportunities("http://x", {"select": sel, "is_active": "eq.true"}, "k")
    assert rows == [{"id": "ec1"}]
    assert r._queue_col_enabled is False          # latched off for the rest of the run
    assert len(calls) == 2                         # tried with the column, then without
    assert r.ACTIVATION_REFRESH_COLUMN not in calls[1]


def test_activation_queue_fetch_passes_through_when_column_present(monkeypatch):
    # Happy path: the column exists, one call, drain stays enabled.
    monkeypatch.setattr(r, "supabase_get",
                        lambda url, table, params, key: [{"id": "ec1",
                                                          r.ACTIVATION_REFRESH_COLUMN: "2026-08-28T00:00:00Z"}])
    monkeypatch.setattr(r, "_queue_col_enabled", True)
    rows = r._get_opportunities("http://x",
                                {"select": "id," + r.ACTIVATION_REFRESH_COLUMN}, "k")
    assert rows[0]["id"] == "ec1"
    assert r._queue_col_enabled is True


def test_activation_queue_fetch_reraises_unrelated_400(monkeypatch):
    # A 400 that is NOT about the queue column (retry without it still fails) must surface,
    # not be silently swallowed as "migration missing".
    def fake_get(url, table, params, key):
        raise urllib.error.HTTPError(url, 400, "some other error", {}, None)

    monkeypatch.setattr(r, "supabase_get", fake_get)
    monkeypatch.setattr(r, "_queue_col_enabled", True)
    with pytest.raises(urllib.error.HTTPError):
        r._get_opportunities("http://x", {"select": "id,name"}, "k")  # no queue col -> reraise
