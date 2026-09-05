"""Money already spent survives an exception — audit finding 4.3.

Every paid agent here either retries a call or parses its answer, and both can raise AFTER the
provider has billed. Before Phase 3 the accumulated cost was a local that died with the frame,
so run totals, agent_runs.cost_usd and the per-user ledger were all low by exactly the amount
that failed hardest — and the app's degraded deadline path recorded nothing at all.
"""
import pytest

from wingman import agent_common as ac


def test_stamp_accumulates_across_frames():
    e = ValueError("boom")
    ac.bank_onto_exception(e, 0.5)
    ac.bank_onto_exception(e, 0.25)
    assert ac.banked_cost(e) == pytest.approx(0.75)


def test_unstamped_exception_reads_zero():
    assert ac.banked_cost(ValueError("nothing spent")) == 0.0


def test_stamping_never_masks_the_original_error():
    """An exception that refuses attributes must not turn error handling into a NEW error.
    Losing the number is acceptable; raising from inside an except block is not."""
    class Hostile(Exception):
        def __setattr__(self, name, value):
            raise AttributeError("refuses attributes")

    e = Hostile("x")
    assert ac.bank_onto_exception(e, 1.0) is e     # returns the ORIGINAL, does not raise
    assert ac.banked_cost(e) == 0.0


def test_banked_cost_survives_a_reraise():
    def inner():
        try:
            raise RuntimeError("provider 429")
        except Exception as e:
            raise ac.bank_onto_exception(e, 0.07)

    with pytest.raises(RuntimeError) as caught:
        inner()
    assert ac.banked_cost(caught.value) == pytest.approx(0.07)


def test_garbage_attribute_reads_zero_rather_than_raising():
    e = ValueError("x")
    setattr(e, "wingman_banked_cost", "not a number")
    assert ac.banked_cost(e) == 0.0


# --------------------------------------------------------------------------------------
# the real call sites
# --------------------------------------------------------------------------------------

def test_scraper_research_seed_keeps_attempt_1_cost_when_attempt_2_raises(monkeypatch):
    """The headline case: a silent first call is billed, the retry times out, and the money
    used to vanish."""
    from agents import scrape_opportunities as so

    calls = {"n": 0}

    def fake_call_gemini(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return "notes", {"usage": 1}, {"grounding": {}}   # billed, but silent
        raise TimeoutError("read timed out")

    monkeypatch.setattr(so, "call_gemini", fake_call_gemini)
    monkeypatch.setattr(so, "estimate_cost", lambda usage: 0.02)

    class Args:
        timeout = 10
        max_searches = 1

    with pytest.raises(TimeoutError) as e:
        so.research_seed("angle", "", "2026-09-05", "key", Args())
    assert ac.banked_cost(e.value) == pytest.approx(0.02)


def test_scraper_extract_candidates_keeps_cost_when_the_parse_raises(monkeypatch):
    from agents import scrape_opportunities as so

    monkeypatch.setattr(so, "call_gemini", lambda *a, **kw: ("{ truncated", {"usage": 1}))
    monkeypatch.setattr(so, "estimate_cost", lambda usage: 0.031)

    def boom(_text):
        raise ValueError("unterminated JSON")

    monkeypatch.setattr(so, "extract_json", boom)

    class Args:
        timeout = 10

    with pytest.raises(ValueError) as e:
        so.extract_candidates("notes", [], "key", Args())
    assert ac.banked_cost(e.value) == pytest.approx(0.031)


def test_check_reviews_research_keeps_attempt_1_cost(monkeypatch):
    from agents import check_reviews as cr

    calls = {"n": 0}

    def fake(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return "notes", {"u": 1}, {"grounding": {}}
        raise RuntimeError("429")

    monkeypatch.setattr(cr, "call_gemini", fake)
    monkeypatch.setattr(cr, "estimate_cost", lambda usage: 0.0166)
    with pytest.raises(RuntimeError) as e:
        cr.research_reviews({"name": "N", "url": "https://x", "org": "O"}, "key")
    assert ac.banked_cost(e.value) == pytest.approx(0.0166)


def test_check_deadlines_carries_every_earlier_rung(monkeypatch):
    """check_one climbs up to four paid rungs. A failure on rung 3 must carry rungs 1 and 2."""
    from agents import check_deadlines as cd

    rounds = {"n": 0}

    def fake_round(opp, api_key, focus, retry_on_silent, candidate_urls=None):
        rounds["n"] += 1
        if rounds["n"] < 3:
            return "notes", 0.07, 1, [], 1, []
        raise TimeoutError("anthropic timeout")

    monkeypatch.setattr(cd, "_search_round", fake_round)
    monkeypatch.setattr(cd, "_parse_signals",
                        lambda notes: ("", {"site_reached": True, "confirmed": False,
                                            "prior_basis": False}))
    monkeypatch.setattr(cd, "_discover_sitemap_urls", lambda opp, discover: None)

    with pytest.raises(TimeoutError) as e:
        cd.research_deadlines({"id": "ec1", "name": "N", "url": "https://x", "org": "O"},
                             "key", trusted_domains=[])
    assert ac.banked_cost(e.value) == pytest.approx(0.14)   # rungs 1 and 2, both billed
