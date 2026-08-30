"""Page classifier: parsing, the deterministic staleness gate, routing. Pure, hermetic.

The model call is injected, so nothing here touches the network. Staleness is a regex over text,
so it is tested with a fixed `today_year` rather than the wall clock.
"""
import json

import pytest

import classify_page as cp

_PAGE = ("Apply now for the Stanford AI4ALL Summer Program. "
         "Applications open October 1 2026 and close in March. How to apply: submit online.")


def _call(payload, usage=None):
    """A stand-in for call_gemini returning canned JSON + a usage dict."""
    usage = usage or {"input_tokens": 100, "output_tokens": 40,
                      "server_tool_use": {"web_search_requests": 0}}
    return lambda system, user: (json.dumps(payload) if isinstance(payload, dict) else payload,
                                 usage)


# ---------- staleness (deterministic, no model) ----------

def test_latest_page_year_takes_the_newest_in_window():
    assert cp.latest_page_year("cohorts in 2019, 2021, and 2024", today_year=2026) == 2024


def test_latest_page_year_ignores_out_of_window_noise():
    # a stray 1400 (address) and a far-future 2099 are not real program dates
    assert cp.latest_page_year("suite 1400, est. 1800, expires 2099", today_year=2026) is None


def test_latest_page_year_none_when_no_year():
    assert cp.latest_page_year("rolling admissions, apply any time", today_year=2026) is None


def test_is_stale_when_newest_is_three_plus_years_old():
    stale, latest = cp.is_stale_page("the 2023 cohort met weekly", today_year=2026)
    assert stale is True and latest == 2023


def test_not_stale_when_recent():
    stale, latest = cp.is_stale_page("applications for 2025 are open", today_year=2026)
    assert stale is False and latest == 2025


def test_undated_page_is_kept():
    stale, latest = cp.is_stale_page("apply any time, no deadline", today_year=2026)
    assert stale is False and latest is None


# ---------- parsing the model's answer ----------

def test_parse_program_with_on_page_evidence_is_verified():
    raw = json.dumps({"class": "program", "confidence": "high",
                      "evidence": "Applications open October 1 2026", "why": "one program, has a deadline"})
    c = cp.parse_classification(raw, _PAGE, today_year=2026)
    assert c.klass == cp.CLASS_PROGRAM and c.confidence == cp.CONF_HIGH
    assert c.evidence_verified is True and c.stale is False and c.latest_year == 2026


def test_parse_demotes_unquotable_positive_class():
    raw = json.dumps({"class": "program", "confidence": "high",
                      "evidence": "this sentence is nowhere on the page at all", "why": "x"})
    c = cp.parse_classification(raw, _PAGE, today_year=2026)
    assert c.klass == cp.CLASS_PROGRAM  # class kept for measurement...
    assert c.evidence_verified is False and c.confidence == cp.CONF_LOW  # ...but never "proven"


def test_parse_stale_program_flag():
    page = "The 2022 program was our last recorded cohort. Apply below."
    raw = json.dumps({"class": "program", "confidence": "high",
                      "evidence": "The 2022 program was our last recorded cohort", "why": "x"})
    c = cp.parse_classification(raw, page, today_year=2026)
    assert c.stale is True and c.latest_year == 2022


def test_parse_invalid_class_has_no_verdict():
    c = cp.parse_classification(json.dumps({"class": "banana"}), _PAGE, today_year=2026)
    assert c.klass is None and "invalid class" in c.error


def test_parse_none_class_needs_no_evidence():
    c = cp.parse_classification(json.dumps({"class": "none", "confidence": "high"}), _PAGE, 2026)
    assert c.klass == cp.CLASS_NONE and c.evidence_verified is True


def test_parse_bad_confidence_falls_to_low():
    raw = json.dumps({"class": "none", "confidence": "certain"})
    assert cp.parse_classification(raw, _PAGE, 2026).confidence == cp.CONF_LOW


def test_parse_non_json_has_no_verdict():
    c = cp.parse_classification("the model rambled with no json", _PAGE, 2026)
    assert c.klass is None


# ---------- routing ----------

@pytest.mark.parametrize("klass,stale,expected", [
    (cp.CLASS_PROGRAM, False, cp.ROUTE_ROW),
    (cp.CLASS_PROGRAM, True, cp.ROUTE_DROP_STALE),
    (cp.CLASS_FIRST_PARTY_HUB, False, cp.ROUTE_SAME_DOMAIN_LEAD),
    (cp.CLASS_THIRD_PARTY_HUB, False, cp.ROUTE_OFF_DOMAIN_LEAD),
    (cp.CLASS_NONE, False, cp.ROUTE_FLAG_NONE),
])
def test_route_for(klass, stale, expected):
    assert cp.route_for(cp.Classification(klass=klass, stale=stale)) == expected


def test_route_unreadable():
    assert cp.route_for(cp.Classification(klass=None, readable=False)) == cp.ROUTE_UNREADABLE
    assert cp.route_for(None) == cp.ROUTE_UNREADABLE


def test_route_unparsed_readable_page_is_flagged_not_dropped():
    # a readable page whose verdict would not parse stays queued (none-route), never dropped
    assert cp.route_for(cp.Classification(klass=None, readable=True)) == cp.ROUTE_FLAG_NONE


# ---------- classify_from_text (injected call, banks cost) ----------

def test_classify_from_text_banks_cost_and_parses():
    c = cp.classify_from_text(
        "https://x.edu/ai4all", _PAGE,
        _call({"class": "program", "confidence": "high",
               "evidence": "Applications open October 1 2026", "why": "x"}),
        today_year=2026)
    assert c.klass == cp.CLASS_PROGRAM and c.cost > 0


def test_classify_from_text_survives_a_bad_answer_keeping_cost():
    c = cp.classify_from_text("https://x.edu/y", _PAGE, _call("not json at all"), today_year=2026)
    assert c.klass is None and c.cost > 0  # money spent is never discarded by a parse failure


# ---------- request building ----------

def test_build_user_content_carries_url_and_labels_the_guess():
    u = cp.build_user_content("https://x.edu/y", "page words", name_hint="Guess", org_hint="Org")
    assert "https://x.edu/y" in u and "page words" in u
    assert "tentative guess" in u.lower()


def test_flag_summaries_are_readable():
    assert "unreadable" in cp.Classification(readable=False, error="403").flag()
    assert "program" in cp.Classification(klass=cp.CLASS_PROGRAM, confidence=cp.CONF_HIGH,
                                          evidence_verified=True).flag()
