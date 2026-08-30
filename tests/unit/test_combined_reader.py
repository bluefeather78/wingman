"""The combined discovery reader: one fetch -> classify (+ metadata + dedupe for a program). Pure.

Every model/embedding call is injected, so nothing here touches the network. The page text is
passed in directly (the live fetch is the wrapper's job and is not exercised here).
"""
import json

import classify_page as cp
import combined_reader as cr
import embed_common

_USAGE = {"input_tokens": 100, "output_tokens": 40, "server_tool_use": {"web_search_requests": 0}}
_PROGRAM_PAGE = ("Apply now for the MIT PRIMES research program. "
                 "Applications open October 1 2026 and close in December.")
_STALE_PAGE = "The 2022 cohort was our last. Apply below for the archived program."


def _reply(payload):
    return lambda system, user: (json.dumps(payload) if isinstance(payload, dict) else payload, _USAGE)


def _classify(klass, evidence):
    return _reply({"class": klass, "confidence": "high", "evidence": evidence, "why": "x"})


def _boom(*a, **k):
    raise AssertionError("this call must not fire on a non-program route")


# ---------- routing ----------

def test_unreadable_page_costs_nothing_and_never_classifies():
    r = cr.read_candidate("https://x.edu/y", "", classify_call=_boom)
    assert r.route == cp.ROUTE_UNREADABLE and r.cost == 0.0
    assert r.classification.readable is False


def test_program_route_extracts_metadata_and_dup_hint():
    idx = [embed_common.index_entry("ec1", [1.0, 0.0]), embed_common.index_entry("ec2", [0.0, 1.0])]
    r = cr.read_candidate(
        "https://mit.edu/primes", _PROGRAM_PAGE,
        classify_call=_classify(cp.CLASS_PROGRAM, "Applications open October 1 2026"),
        name_hint="MIT PRIMES",
        metadata_call=_reply({"name": "MIT PRIMES", "org": "MIT",
                              "summary": "A research program.", "type": "Research", "price": "Free"}),
        embed_fn=lambda t: ([1.0, 0.02], 0.0001), index=idx)
    assert r.route == cp.ROUTE_ROW and r.is_row
    assert r.metadata["name"] == "MIT PRIMES" and r.metadata["type"] == "Research"
    assert [c["id"] for c in r.dup_candidates] == ["ec1"]  # ec2 is orthogonal, below floor
    assert r.dup_candidates[0]["score"] >= cr.DEFAULT_DUP_THRESHOLD
    assert r.cost > 0


def test_stale_program_drops_without_enriching():
    r = cr.read_candidate(
        "https://x.edu/old", _STALE_PAGE,
        classify_call=_classify(cp.CLASS_PROGRAM, "The 2022 cohort was our last"),
        metadata_call=_boom, embed_fn=_boom, index=[embed_common.index_entry("e", [1.0])],
        today_year=2026)
    assert r.route == cp.ROUTE_DROP_STALE
    assert r.metadata == {} and r.dup_candidates == []


def test_first_party_hub_routes_to_lead_no_metadata():
    r = cr.read_candidate(
        "https://cmu.edu/pre-college", "Explore our fifteen Carnegie Mellon summer programs.",
        classify_call=_classify(cp.CLASS_FIRST_PARTY_HUB, "our fifteen Carnegie Mellon summer programs"),
        metadata_call=_boom)
    assert r.route == cp.ROUTE_SAME_DOMAIN_LEAD and r.metadata == {}


def test_none_routes_to_flag():
    r = cr.read_candidate("https://x.edu/tuition", "Tuition and fees for the academic year.",
                          classify_call=_classify(cp.CLASS_NONE, "Tuition and fees"),
                          metadata_call=_boom)
    assert r.route == cp.ROUTE_FLAG_NONE


def test_program_without_index_skips_dedupe_but_keeps_metadata():
    r = cr.read_candidate(
        "https://mit.edu/primes", _PROGRAM_PAGE,
        classify_call=_classify(cp.CLASS_PROGRAM, "Applications open October 1 2026"),
        metadata_call=_reply({"name": "MIT PRIMES", "type": "Research"}), embed_fn=_boom)
    assert r.route == cp.ROUTE_ROW and r.metadata["name"] == "MIT PRIMES"
    assert r.dup_candidates == []  # no index -> no embedding call, _boom never fired


# ---------- metadata reuse (refresh's extraction) ----------

def test_extract_metadata_uses_refresh_validation():
    update, cost = cr.extract_metadata(
        "https://x.edu/p", "page text here",
        _reply({"name": "Prog", "org": "Org", "type": "Competition",
                "price": "Paid", "grade_min": 9, "grade_max": 12, "type_bogus": "x"}),
        name_hint="Prog")
    assert update["name"] == "Prog" and update["type"] == "Competition"
    assert update["grade_min"] == 9 and update["grade_max"] == 12 and cost > 0


def test_extract_metadata_bad_json_is_empty_but_banks_cost():
    update, cost = cr.extract_metadata("https://x.edu/p", "page", _reply("not json"))
    assert update == {} and cost > 0


def test_extract_metadata_drops_invalid_enum():
    update, _ = cr.extract_metadata("https://x.edu/p", "page",
                                    _reply({"name": "P", "type": "Banana", "price": "Free"}))
    assert "type" not in update and update["price"] == "Free"


# ---------- dedupe hint ----------

def test_dedup_hint_respects_threshold_and_exclusions():
    idx = [embed_common.index_entry("ec1", [1.0, 0.0]), embed_common.index_entry("ec2", [0.5, 0.5])]
    cands, cost = cr.dedup_hint("rep", lambda t: ([1.0, 0.0], 0.0), idx, threshold=0.95)
    assert [c["id"] for c in cands] == ["ec1"]  # ec2 (cos ~0.71) below 0.95
    cands2, _ = cr.dedup_hint("rep", lambda t: ([1.0, 0.0], 0.0), idx, threshold=0.5,
                              exclude_ids={"ec1"})
    assert "ec1" not in [c["id"] for c in cands2]


def test_dedup_hint_no_index_is_free():
    assert cr.dedup_hint("rep", _boom, None) == ([], 0.0)


def test_default_representation_prefers_fields_then_page():
    assert "Prog" in cr.default_representation({"name": "Prog", "summary": "s"}, "PAGE")
    assert cr.default_representation({}, "PAGE FALLBACK") == "PAGE FALLBACK"
