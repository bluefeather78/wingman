"""Unit tests for the eligibility-only pool gate (docs/plans/RECALL_GRID_MERGE_PLAN.md)."""
import json

from app.services.pool_eligibility import (
    has_restriction_signal, gate_pool_eligibility, build_eligibility_user_content,
)


def test_restriction_signal_detects_gates_and_ignores_plain_rows():
    assert has_restriction_signal({"eligibility": "Open only to Boston Public Schools students."})
    assert has_restriction_signal({"name": "Girls Who Code Summer Immersion"})
    assert has_restriction_signal({"eligibility": "Applicants must be U.S. citizens."})
    # A plain row with no restriction wording needs no model call.
    assert not has_restriction_signal(
        {"name": "Coding Club", "org": "Library", "summary": "A weekly club for teens.",
         "eligibility": None})


def _stub_gate(verdicts):
    """A gate_fn that ignores its input and returns a fixed verdicts JSON."""
    def _fn(system, user_content):
        return json.dumps({"verdicts": verdicts})
    return _fn


def test_gate_drops_verified_ineligible_keeps_unverified_and_signal_free():
    pool = [
        {"id": "a", "name": "Girls Who Code", "eligibility": "Open to female and non-binary students only."},
        {"id": "b", "name": "Fake Gate", "eligibility": "Open to students in any grade."},
        {"id": "c", "name": "Plain Program", "summary": "A robotics build camp.", "eligibility": None},
    ]
    student = {"grade": 10, "location": {"state": "WA"}, "funnel_answers": {"gender": "male"}}
    # Model excludes a with a VERIFIABLE quote (real substring of a.eligibility) and excludes b
    # with an UNVERIFIABLE quote (not in b's text) -> guard reverts b to eligible. c never reaches
    # the model (no restriction signal).
    gate_fn = _stub_gate([
        {"id": "a", "eligible": False,
         "exclusion_quote": "Open to female and non-binary students only.",
         "exclusion_source_field": "eligibility"},
        {"id": "b", "eligible": False,
         "exclusion_quote": "seniors only, no exceptions", "exclusion_source_field": "eligibility"},
    ])
    out = gate_pool_eligibility(pool, student, gate_fn, json.loads)
    assert [r["id"] for r in out["pool"]] == ["b", "c"]  # a dropped, order preserved
    assert out["excluded"] == ["a"]
    assert out["checked"] == 2   # a and b carry restriction signal; c does not
    assert out["called"] is True


def test_gate_no_model_when_no_signal():
    pool = [{"id": "c", "name": "Plain", "summary": "A club.", "eligibility": None}]
    called = {"n": 0}

    def gate_fn(s, u):
        called["n"] += 1
        return "{}"

    out = gate_pool_eligibility(pool, {"grade": 9}, gate_fn, json.loads)
    assert out["pool"] == pool and out["called"] is False and called["n"] == 0


def test_gate_keeps_everyone_on_parse_failure():
    pool = [{"id": "a", "eligibility": "must be a U.S. citizen"}]

    def gate_fn(s, u):
        return "not json at all"

    def parse(_):
        raise ValueError("bad")

    out = gate_pool_eligibility(pool, {"grade": 10}, gate_fn, parse)
    assert [r["id"] for r in out["pool"]] == ["a"] and out["excluded"] == []


def test_user_content_only_includes_volunteered_attributes():
    uc = build_eligibility_user_content(
        {"grade": 11, "location": {"state": "CA"}, "funnel_answers": {"gender": "female", "cost": "free"}},
        [{"id": "x"}])
    assert "female" in uc and "\"cost\"" not in uc  # cost is not a sensitive attribute here
