"""finalize_curation (app/services/curation.py) — the pure step that turns the curation
model's raw output into the trusted <=10, applying the eligibility guard. The model call
itself is not exercised here; a stubbed `parsed` dict stands in for its output.
"""
from app.services.curation import build_candidate_view, finalize_curation


def _rows(*rows):
    return {r["id"]: r for r in rows}


def test_selected_picks_pass_through_capped_and_ordered():
    rows = _rows(*[{"id": f"e{i}", "eligibility": ""} for i in range(12)])
    parsed = {"selected": [
        {"id": f"e{i}", "reason": "fits your thing", "tier": "strong", "exploration_pick": False,
         "eligible": True} for i in range(12)
    ]}
    out = finalize_curation(parsed, rows, limit=10)
    assert len(out["results"]) == 10                       # capped
    assert [r["id"] for r in out["results"]] == [f"e{i}" for i in range(10)]  # order preserved


def test_hallucinated_id_dropped():
    rows = _rows({"id": "real", "eligibility": ""})
    parsed = {"selected": [
        {"id": "real", "reason": "r", "tier": "look", "eligible": True},
        {"id": "ghost", "reason": "r", "tier": "look", "eligible": True},
    ]}
    out = finalize_curation(parsed, rows)
    assert [r["id"] for r in out["results"]] == ["real"]


def test_verified_ineligible_pick_is_dropped():
    rows = _rows({"id": "x", "eligibility": "Open only to Massachusetts residents."})
    parsed = {"selected": [
        {"id": "x", "reason": "r", "tier": "strong", "eligible": False,
         "exclusion_quote": "Open only to Massachusetts residents", "exclusion_source_field": "eligibility"},
    ]}
    out = finalize_curation(parsed, rows)
    assert out["results"] == []   # genuinely ineligible -> not shown


def test_unverified_ineligible_pick_is_kept():
    # Model marked a pick ineligible but the quote doesn't verify -> guard keeps it in the list.
    rows = _rows({"id": "x", "eligibility": "Open to all high schoolers."})
    parsed = {"selected": [
        {"id": "x", "reason": "great fit for your robotics", "tier": "strong", "eligible": False,
         "exclusion_quote": "seniors only", "exclusion_source_field": "eligibility"},
    ]}
    out = finalize_curation(parsed, rows)
    assert [r["id"] for r in out["results"]] == ["x"]


def test_wrongly_excluded_row_is_rescued_and_counted():
    rows = _rows({"id": "y", "eligibility": "Open to all high schoolers."})
    parsed = {
        "selected": [],
        "excluded_ineligible": [
            {"id": "y", "eligible": False, "exclusion_quote": "citizens only",
             "exclusion_source_field": "eligibility"},  # not actually in the row
        ],
    }
    out = finalize_curation(parsed, rows)
    assert out["rescued"] == ["y"]
    assert out["guard_overrode_count"] == 1


def test_genuinely_excluded_row_not_rescued():
    rows = _rows({"id": "y", "eligibility": "Open only to US citizens."})
    parsed = {"selected": [], "excluded_ineligible": [
        {"id": "y", "eligible": False, "exclusion_quote": "Open only to US citizens",
         "exclusion_source_field": "eligibility"},
    ]}
    out = finalize_curation(parsed, rows)
    assert out["rescued"] == []
    assert out["guard_overrode_count"] == 0


def test_build_candidate_view_shape():
    row = {"id": "a", "name": "N", "eligibility": "E", "url": "http://x", "status": "running"}
    view = build_candidate_view(row)
    assert view["id"] == "a" and view["eligibility"] == "E"
    assert "url" not in view and "status" not in view   # dropped: no fit value / handled at recall
