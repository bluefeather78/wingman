"""The funnel's per-rung apply logic (app/services/funnel.py) — the deterministic half of the
progressive elicitation funnel. Enforces the three traps in code:
  T1: cannot cut on a preference axis (it isn't in the whitelist -> raises).
  T2: cannot ask a non-whitelisted axis (raises).
  T3: reports would_collapse so the caller offers relax instead of walling the student.
Plus: a quote-required cut (citizenship/demographic) reverts to keep when its quote doesn't
verify, exactly like the curation guard; a structured cut (cost/time) needs no quote.
"""
import pytest

from app.services import funnel


def _pool(*ids):
    # rows carry an eligibility field so quote-required cuts have something to verify against
    return [{"id": i, "eligibility": "", "summary": ""} for i in ids]


# --------------------------------------------------------------------------- T1 / T2

def test_unknown_axis_raises():
    with pytest.raises(funnel.FunnelAxisError):
        funnel.apply_rung_answer(_pool("a"), {"axis": "subject", "classification": {}}, "x")


def test_preference_axis_cannot_cut():
    # "work_style" is a preference axis — deliberately absent from FUNNEL_AXES, so a rung that
    # tries to cut on it fails closed rather than silently narrowing.
    with pytest.raises(funnel.FunnelAxisError):
        funnel.apply_rung_answer(_pool("a"), {"axis": "work_style", "classification": {}}, "collab")


# --------------------------------------------------------------------------- structured cut

def test_cost_cut_no_quote_needed():
    pool = _pool("free1", "paid1")
    rung = {
        "axis": "cost",
        "classification": {
            "free1": {"per_option": {"free_only": "keep"}},
            "paid1": {"per_option": {"free_only": "cut"}},
        },
    }
    out = funnel.apply_rung_answer(pool, rung, "free_only")
    assert [r["id"] for r in out["narrowed"]] == ["free1"]
    assert out["cut_ids"] == ["paid1"]
    assert out["reverted_ids"] == []


def test_missing_entry_defaults_to_keep():
    pool = _pool("a", "b")
    rung = {"axis": "cost", "classification": {"a": {"per_option": {"free_only": "cut"}}}}
    out = funnel.apply_rung_answer(pool, rung, "free_only")
    # 'b' has no classification entry -> kept (never cut on absence)
    assert set(r["id"] for r in out["narrowed"]) == {"b"}


# --------------------------------------------------------------------------- caveat

def test_caveat_is_kept_and_flagged():
    pool = _pool("a")
    rung = {"axis": "time_commitment", "classification": {"a": {"per_option": {"summer": "caveat"}}}}
    out = funnel.apply_rung_answer(pool, rung, "summer")
    assert [r["id"] for r in out["narrowed"]] == ["a"]
    assert out["caveat_ids"] == ["a"]
    assert out["cut_ids"] == []


# --------------------------------------------------------------------------- quote-required cut

def test_citizenship_cut_stands_with_verifying_quote():
    pool = [{"id": "x", "eligibility": "Open to US citizens only.", "summary": ""}]
    rung = {
        "axis": "citizenship",
        "classification": {
            "x": {"per_option": {"non_citizen": "cut"}, "quote": "Open to US citizens only",
                  "source_field": "eligibility"},
        },
    }
    out = funnel.apply_rung_answer(pool, rung, "non_citizen")
    assert out["cut_ids"] == ["x"]
    assert out["narrowed"] == []


def test_citizenship_cut_reverts_when_quote_absent():
    # Model claims a citizenship cut but the row never says so -> revert to keep (the same
    # unknown != ineligible safety the curation guard applies).
    pool = [{"id": "x", "eligibility": "Open to all high schoolers.", "summary": ""}]
    rung = {
        "axis": "citizenship",
        "classification": {
            "x": {"per_option": {"non_citizen": "cut"}, "quote": "US citizens only",
                  "source_field": "eligibility"},
        },
    }
    out = funnel.apply_rung_answer(pool, rung, "non_citizen")
    assert out["cut_ids"] == []
    assert out["reverted_ids"] == ["x"]
    assert [r["id"] for r in out["narrowed"]] == ["x"]


# --------------------------------------------------------------------------- T3 + counts

def test_would_collapse_flag_and_count():
    pool = _pool("a", "b", "c")
    rung = {
        "axis": "cost",
        "classification": {i: {"per_option": {"free_only": "cut"}} for i in ("a", "b")},
    }
    out = funnel.apply_rung_answer(pool, rung, "free_only")
    assert out["count"] == 1
    assert out["would_collapse"] is True   # 1 < POOL_FLOOR (5)


def test_count_after_matches_apply():
    pool = _pool("a", "b", "c")
    rung = {"axis": "cost", "classification": {"a": {"per_option": {"free_only": "cut"}}}}
    assert funnel.count_after(pool, rung, "free_only") == \
        funnel.apply_rung_answer(pool, rung, "free_only")["count"]
