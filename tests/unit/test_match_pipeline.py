"""End-to-end recall->curation orchestration (app/services/match_pipeline.py), with the two
paid calls (theme embedding, curation model) stubbed. Verifies the pieces compose: recall
narrows + scores, curation output is finalized through the guard, display fields are attached,
and the fallback path triggers on unparseable curation.
"""
import json

from app.services import match_pipeline as mp


def _row(id_, vec, **extra):
    base = {"id": id_, "match_vector": vec, "status": "running", "name": f"Prog {id_}",
            "org": "Org", "type": "Program", "summary": "s", "url": f"http://x/{id_}",
            "eligibility": "", "subject_tags": ["t"]}
    base.update(extra)
    return base


def _embed_themes(_texts):
    # one 2-D theme vector aligned with row "a"
    return [[1.0, 0.0]], 0.0001


def test_theme_embed_text_variants():
    assert mp.theme_embed_text("robotics") == "robotics"
    assert mp.theme_embed_text({"theme": "Robotics", "intent": "build", "next_steps": "compete"}) \
        == "Robotics. build. compete"
    assert mp.theme_embed_text({"theme": "Debate"}) == "Debate"
    assert mp.theme_embed_text(123) == ""


def test_run_match_happy_path_curates_and_attaches_display():
    rows = [_row("a", [1.0, 0.0]), _row("b", [0.0, 1.0]), _row("c", [0.6, 0.6])]
    student = {"grade": 10, "location": {"state": "WA"},
               "profile_themes": [{"theme": "Robotics"}]}

    def curate_fn(system, user_content):
        # model returns a curated pick for the top-scoring row
        return json.dumps({"selected": [
            {"id": "a", "reason": "fits your robotics", "tier": "strong",
             "exploration_pick": False, "eligible": True}
        ], "excluded_ineligible": []})

    out = mp.run_match(rows, student, _embed_themes, curate_fn, json.loads)
    assert [r["id"] for r in out["results"]] == ["a"]
    r0 = out["results"][0]
    assert r0["reason"] == "fits your robotics" and r0["tier"] == "strong"
    assert r0["name"] == "Prog a" and r0["url"] == "http://x/a"   # display fields attached
    assert out["note"] is None
    assert out["pool_size"] >= 1


def test_run_match_curation_unparseable_falls_back_to_recall_order():
    rows = [_row("a", [1.0, 0.0]), _row("b", [0.9, 0.1])]
    student = {"grade": 10, "location": {"state": "WA"}, "profile_themes": ["robotics"]}
    out = mp.run_match(rows, student, _embed_themes, lambda s, u: "not json", json.loads)
    assert out["note"] and "curation unavailable" in out["note"]
    assert [r["id"] for r in out["results"]] == ["a", "b"]   # recall order preserved
    assert all(r["reason"] is None for r in out["results"])


def test_run_match_thin_profile_no_themes_skips_embedding():
    rows = [_row("a", [1.0, 0.0])]
    student = {"grade": 10, "location": {"state": "WA"}, "profile_themes": []}
    called = {"embed": False}
    def _embed(_):
        called["embed"] = True
        return [], 0.0
    # curation still runs over the (recall-filtered, unscored) pool
    out = mp.run_match(rows, student, _embed, lambda s, u: json.dumps(
        {"selected": [{"id": "a", "reason": "r", "tier": "look", "eligible": True}]}), json.loads)
    assert called["embed"] is False          # no themes -> no paid embed call
    assert [r["id"] for r in out["results"]] == ["a"]


def test_run_match_guard_rescues_wrongly_excluded():
    rows = [_row("a", [1.0, 0.0], eligibility="Open to all high schoolers.")]
    student = {"grade": 10, "location": {"state": "WA"}, "profile_themes": ["x"]}
    def curate_fn(s, u):
        return json.dumps({
            "selected": [],
            "excluded_ineligible": [
                {"id": "a", "eligible": False, "exclusion_quote": "citizens only",
                 "exclusion_source_field": "eligibility"},  # not in the row
            ],
        })
    out = mp.run_match(rows, student, _embed_themes, curate_fn, json.loads)
    assert out["rescued"] == ["a"]
    assert out["guard_overrode_count"] == 1


# --------------------------------------------------------------------------- next_funnel_rung

def _big_pool(n):
    # n rows, each with an eligibility field so quote-required cuts have something to check
    return [{"id": f"r{i}", "eligibility": "", "summary": "", "name": f"P{i}"} for i in range(n)]


def test_next_rung_stops_when_pool_small():
    # <= CURATE_AT -> curate (None), model never called
    called = {"n": 0}
    def ask(s, u):
        called["n"] += 1
        return "{}"
    assert mp.next_funnel_rung(_big_pool(10), {}, ask, __import__("json").loads) is None
    assert called["n"] == 0


def test_next_rung_stops_at_rung_cap():
    assert mp.next_funnel_rung(_big_pool(40), {}, lambda s, u: "{}", __import__("json").loads,
                               rungs_done=5) is None


def test_next_rung_stops_on_axis_null():
    rung = mp.next_funnel_rung(_big_pool(40), {}, lambda s, u: json.dumps({"axis": None}), json.loads)
    assert rung is None


def test_next_rung_stops_on_non_whitelisted_axis():
    # model picks a preference axis -> fail closed (curate), never cut on it
    raw = json.dumps({"axis": "subject", "question": "?", "options": [], "classification": {}})
    assert mp.next_funnel_rung(_big_pool(40), {}, lambda s, u: raw, json.loads) is None


def test_next_rung_returns_sanitized_rung_with_counts():
    pool = _big_pool(40)
    raw = json.dumps({
        "axis": "cost", "question": "Budget?",
        "options": [{"label": "Free only", "value": "free"}, {"label": "Any", "value": "any"}],
        "classification": {"r0": {"per_option": {"free": "cut", "any": "keep"}}},
    })
    rung = mp.next_funnel_rung(pool, {}, lambda s, u: raw, json.loads)
    assert rung["axis"] == "cost"
    counts = {o["value"]: o["count"] for o in rung["options"]}
    assert counts["free"] == 39 and counts["any"] == 40   # r0 cut under 'free'
    assert len(rung["pool_ids"]) == 40
