"""triage_queue: the pure bucketing + rejection-plan logic. Hermetic (no network)."""
from agents import triage_queue as tq


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


def _hub_row(rid, flag, url):
    return {"id": rid, "quality_flags": [flag], "url": url, "name": f"row {rid}"}


def test_hub_leads_scope_both_kinds():
    from wingman import discovered_leads as dl
    rows = [_hub_row("h1", "classify: first_party_hub (high)", "https://uni.edu/precollege"),
            _hub_row("h2", "classify: third_party_hub (high)", "https://listicle.com/best-camps"),
            _row("p1", "classify: program (high)")]           # not a hub -> never a lead
    leads = tq.hub_leads_for(rows, ["h1", "h2", "p1"])
    by_url = {l["url"]: l for l in leads}
    assert len(leads) == 2
    assert all(l["kind"] == dl.KIND_HUB for l in leads)
    assert by_url["https://uni.edu/precollege"]["scope"] == dl.SCOPE_SAME_DOMAIN
    assert by_url["https://listicle.com/best-camps"]["scope"] == dl.SCOPE_OFF_DOMAIN


def test_hub_leads_only_for_selected_ids():
    rows = [_hub_row("h1", "classify: first_party_hub (high)", "https://a.edu/x"),
            _hub_row("h2", "classify: third_party_hub (high)", "https://b.com/y")]
    leads = tq.hub_leads_for(rows, ["h1"])          # only h1 is being rejected
    assert [l["url"] for l in leads] == ["https://a.edu/x"]


def test_hub_leads_skips_url_less_rows():
    rows = [_hub_row("h1", "classify: first_party_hub (high)", "")]
    assert tq.hub_leads_for(rows, ["h1"]) == []
