"""triage_queue: the pure bucketing + rejection-plan logic. Hermetic (no network)."""
import triage_queue as tq


def _row(rid, flag):
    return {"id": rid, "quality_flags": [flag] if flag else []}


def test_row_bucket_maps_each_class():
    assert tq.row_bucket(_row("a", "classify: first_party_hub (high)")) == "hub"
    assert tq.row_bucket(_row("b", "classify: third_party_hub (medium)")) == "hub"
    assert tq.row_bucket(_row("c", "classify: none (high)")) == "none"
    assert tq.row_bucket(_row("d", "classify: program (high)")) == "program"
    assert tq.row_bucket(_row("e", "classify: unreadable (blocked)")) == "unreadable"


def test_stale_program_is_its_own_bucket():
    r = _row("f", "classify: program (high); STALE latest year 2021")
    assert tq.row_bucket(r) == "stale"          # not 'program' — separable for rejection
    assert tq.row_bucket(_row("g", "classify: program (high)")) == "program"


def test_unclassified_row_has_empty_bucket():
    assert tq.row_bucket(_row("h", "dead link (404)")) == ""
    assert tq.row_bucket(_row("i", None)) == ""


def test_breakdown_counts_all_buckets():
    rows = [_row("a", "classify: first_party_hub (high)"),
            _row("b", "classify: none (high)"),
            _row("c", "classify: program (high)"),
            _row("d", "classify: program (high); STALE latest year 2020"),
            _row("e", "dead link (404)")]
    b = tq.breakdown(rows)
    assert b == {"hub": 1, "none": 1, "program": 1, "stale": 1, "(unclassified)": 1}


def test_plan_only_includes_enabled_junk_buckets():
    rows = [_row("hub1", "classify: first_party_hub (high)"),
            _row("none1", "classify: none (high)"),
            _row("stale1", "classify: program (high); STALE latest year 2019"),
            _row("prog1", "classify: program (high)"),          # never rejected
            _row("unrd1", "classify: unreadable (blocked)")]    # never rejected
    plan = tq.plan_triage(rows, reject_hubs=True, reject_none=True, reject_stale=True)
    picked = {p["bucket"]: p["ids"] for p in plan}
    assert picked["hub"] == ["hub1"]
    assert picked["none"] == ["none1"]
    assert picked["stale"] == ["stale1"]
    # program + unreadable never appear in any bucket
    all_ids = [i for p in plan for i in p["ids"]]
    assert "prog1" not in all_ids and "unrd1" not in all_ids


def test_plan_respects_individual_flags():
    rows = [_row("hub1", "classify: first_party_hub (high)"),
            _row("none1", "classify: none (high)")]
    plan = tq.plan_triage(rows, reject_hubs=True, reject_none=False, reject_stale=False)
    assert [p["bucket"] for p in plan] == ["hub"]


def test_plan_empty_when_nothing_enabled():
    rows = [_row("hub1", "classify: first_party_hub (high)")]
    assert tq.plan_triage(rows) == []


def test_every_plan_bucket_carries_a_reason():
    rows = [_row("hub1", "classify: first_party_hub (high)")]
    plan = tq.plan_triage(rows, reject_hubs=True)
    assert plan[0]["reason"] and "hub" in plan[0]["reason"].lower()
