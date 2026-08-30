"""Behavioral (vibe) funnel rungs — the rerank-only questions ported server-side from the
opportunity-matching branch. Vibe rungs never filter (empty classification -> client keeps all),
and their answers become soft preference phrases handed to curation."""
import json

from app.services.funnel import (
    BEHAVIORAL_AXES, CURATE_AT, MAX_RUNGS, POOL_FLOOR, ENGAGEMENT_OTHER,
    behavioral_pref, collect_preferences, build_vibe_rung, next_vibe_rung, build_engagement_rung,
    build_outcome_rung, next_outcome_rung, build_cost_rung, build_time_rung,
)
from app.services.curation import build_curation_user_content


def _pool(n):
    return [{"id": f"o{i}", "name": f"Program {i}"} for i in range(n)]


def test_behavioral_pref_maps_value_to_phrase():
    assert behavioral_pref("collaboration", "team") == "Prefers team / cohort settings"
    assert behavioral_pref("collaboration", "solo") == "Prefers working independently"
    # Unknown value / skip / unknown axis -> no signal.
    assert behavioral_pref("collaboration", "__skip__") == ""
    assert behavioral_pref("collaboration", "nope") == ""
    assert behavioral_pref("not_an_axis", "team") == ""


def test_collect_preferences_only_vibe_axes_and_real_answers():
    answers = {
        "cost": "0",                 # filter axis -> ignored
        "collaboration": "team",     # vibe -> phrase
        "structure": "__skip__",     # vibe but skipped -> no phrase
        "intensity": "immersive",    # vibe -> phrase
    }
    prefs = collect_preferences(answers)
    assert "Prefers team / cohort settings" in prefs
    assert "Prefers full-time, immersive commitments" in prefs
    assert len(prefs) == 2
    assert collect_preferences({}) == []
    assert collect_preferences(None) == []


def test_build_vibe_rung_uses_model_labels_when_valid():
    parsed = {"axis": "collaboration", "question": "Rather roll with a crew or fly solo?",
              "options": [{"value": "team", "label": "With a crew"}, {"value": "solo", "label": "Solo"}]}
    rung = build_vibe_rung(parsed, "collaboration", _pool(3))
    assert rung["kind"] == "vibe"
    assert rung["question"] == "Rather roll with a crew or fly solo?"
    assert rung["classification"] == {}          # never filters
    assert rung["pool_ids"] == ["o0", "o1", "o2"]
    by_value = {o["value"]: o["label"] for o in rung["options"]}
    assert by_value == {"team": "With a crew", "solo": "Solo"}


def test_build_vibe_rung_falls_back_to_local_on_bad_output():
    # Missing question -> local wording; options are the axis's fixed examples.
    rung = build_vibe_rung({"axis": "intensity"}, "intensity", _pool(2))
    assert rung["question"] == "All in, or keep it light?"
    assert {o["value"] for o in rung["options"]} == {"immersive", "light"}
    assert {o["label"] for o in rung["options"]} == {"All in", "Keep it light"}


def test_build_vibe_rung_fills_missing_option_label_from_example():
    parsed = {"axis": "intensity", "question": "All in, or take it easy?",
              "options": [{"value": "immersive", "label": "All in"}]}  # 'light' label missing
    rung = build_vibe_rung(parsed, "intensity", _pool(1))
    by_value = {o["value"]: o["label"] for o in rung["options"]}
    assert by_value["immersive"] == "All in"
    assert by_value["light"] == "Keep it light"    # fell back to the example


def test_next_vibe_rung_stops_when_pool_small_or_capped():
    ask = lambda system, uc: json.dumps({"axis": "collaboration", "question": "q",
                                          "options": [{"value": "team", "label": "T"}, {"value": "solo", "label": "S"}]})
    parse = json.loads
    # Pool at/below the rerank floor -> no vibe question.
    assert next_vibe_rung(_pool(POOL_FLOOR), {}, {}, ask, parse, rungs_done=0) is None
    # Rung cap reached.
    assert next_vibe_rung(_pool(CURATE_AT + 20), {}, {}, ask, parse, rungs_done=MAX_RUNGS) is None


def test_next_vibe_rung_stops_when_all_axes_asked():
    ask = lambda system, uc: "{}"
    answers = {a: "x" for a in BEHAVIORAL_AXES}
    assert next_vibe_rung(_pool(CURATE_AT + 20), {}, answers, ask, json.loads, rungs_done=0) is None


def test_next_vibe_rung_returns_rung_and_skips_already_asked_axis():
    # Model picks an axis already answered -> server falls back to the first REMAINING axis.
    asked = "selectivity"
    ask = lambda system, uc: json.dumps({"axis": asked, "question": "already asked!",
                                         "options": [{"value": "competitive", "label": "x"}]})
    rung = next_vibe_rung(_pool(CURATE_AT + 20), {}, {asked: "competitive"}, ask, json.loads, rungs_done=1)
    assert rung is not None
    assert rung["axis"] != asked          # not re-asked
    assert rung["axis"] in BEHAVIORAL_AXES
    assert rung["kind"] == "vibe"


def test_engagement_rung_is_pool_derived_and_filters_by_type():
    pool = ([{"id": f"c{i}", "type": "Competition"} for i in range(6)]
            + [{"id": f"i{i}", "type": "Internship"} for i in range(4)]
            + [{"id": "r0", "type": "Research"}])
    rung = build_engagement_rung(pool)
    assert rung["axis"] == "engagement" and rung["kind"] == "filter" and rung["allow_other"] is True
    # options are the TYPES present, most common first, with pool-derived counts.
    by_val = {o["value"]: o for o in rung["options"]}
    assert set(by_val) == {"Competition", "Internship", "Research"}
    assert by_val["Competition"]["count"] == 6 and by_val["Internship"]["count"] == 4 and by_val["Research"]["count"] == 1
    assert by_val["Competition"]["label"] == "Competing head-to-head"   # enjoyment framing
    # classification cuts every candidate not of the chosen type (a real filter).
    assert rung["classification"]["c0"]["per_option"] == {"Competition": "keep", "Internship": "cut", "Research": "cut"}


def test_engagement_rung_none_when_under_two_types():
    assert build_engagement_rung([{"id": "a", "type": "Competition"}, {"id": "b", "type": "Competition"}]) is None
    assert build_engagement_rung([{"id": "a"}]) is None   # no type at all


def test_engagement_other_folds_into_preferences_but_a_type_answer_does_not():
    # "Something else" free text -> a rerank preference; a plain type answer filtered instead.
    prefs = collect_preferences({"engagement": ENGAGEMENT_OTHER + "building assistive tech"})
    assert prefs == ["Enjoys: building assistive tech"]
    assert collect_preferences({"engagement": "Competition"}) == []


def test_outcome_rung_is_rerank_only_and_pool_derived():
    parsed = {"question": "What do you want to walk away with?",
              "options": [{"value": "build a finished project", "label": "Build it"},
                          {"value": "win an award", "label": "Win it"}]}
    rung = build_outcome_rung(parsed, _pool(30))
    assert rung["axis"] == "outcome" and rung["kind"] == "vibe" and rung["allow_other"] is True
    assert rung["classification"] == {}                 # never filters
    assert [o["value"] for o in rung["options"]] == ["build a finished project", "win an award"]


def test_outcome_rung_falls_back_locally_on_bad_output():
    rung = build_outcome_rung(None, _pool(30))
    assert rung["axis"] == "outcome" and len(rung["options"]) >= 2 and rung["classification"] == {}


def test_next_outcome_rung_stops_when_small_capped_or_already_asked():
    ask = lambda s, u: '{"question":"q","options":[{"value":"a","label":"A"},{"value":"b","label":"B"}]}'
    assert next_outcome_rung(_pool(POOL_FLOOR), {}, ask, json.loads, 0) is None          # pool at floor
    assert next_outcome_rung(_pool(CURATE_AT + 20), {}, ask, json.loads, MAX_RUNGS) is None   # capped
    assert next_outcome_rung(_pool(CURATE_AT + 20), {"outcome": "x"}, ask, json.loads, 0) is None  # asked


def test_outcome_answer_folds_into_preferences():
    assert collect_preferences({"outcome": "build a finished project"}) == ["Wants: build a finished project"]
    assert collect_preferences({"outcome": ENGAGEMENT_OTHER + "start a nonprofit"}) == ["Wants: start a nonprofit"]
    assert collect_preferences({"outcome": "__skip__"}) == []


def test_cost_rung_open_option_never_smaller_than_free_only():
    # The reported bug: "open to paid" must keep EVERYONE (never fewer than "free only").
    pool = ([{"id": f"f{i}", "price": "Free"} for i in range(20)]
            + [{"id": f"p{i}", "price": "Paid"} for i in range(7)]
            + [{"id": "u0", "price": None}])          # unknown price -> never cut
    rung = build_cost_rung(pool)
    counts = {o["value"]: o["count"] for o in rung["options"]}
    assert counts["any"] == 28                          # open keeps ALL 28
    assert counts["free"] == 21                         # 20 free + 1 unknown (paid cut)
    assert counts["any"] >= counts["free"]              # the invariant that was violated
    assert rung["classification"]["p0"]["per_option"] == {"free": "cut", "any": "keep"}


def test_cost_rung_none_when_no_split():
    assert build_cost_rung([{"id": "a", "price": "Free"}, {"id": "b", "price": "Free"}]) is None
    assert build_cost_rung([{"id": "a", "price": "Paid"}, {"id": "b", "price": "Paid"}]) is None


def test_time_rung_any_option_keeps_everyone():
    pool = ([{"id": f"s{i}", "season": "Summer"} for i in range(10)]
            + [{"id": f"y{i}", "season": "Year-Long"} for i in range(5)]
            + [{"id": f"w{i}", "season": "Fall"} for i in range(6)])
    rung = build_time_rung(pool)
    counts = {o["value"]: o["count"] for o in rung["options"]}
    assert counts["any"] == 21                          # everyone
    assert counts["summer"] == 15                        # 10 summer + 5 year-long
    assert counts["school_year"] == 11                   # 6 fall + 5 year-long
    assert counts["any"] >= counts["summer"] and counts["any"] >= counts["school_year"]


def test_time_rung_none_when_one_season_kind():
    assert build_time_rung([{"id": "a", "season": "Summer"}, {"id": "b", "season": "Year-Long"}]) is None


def test_curation_payload_surfaces_folded_preferences():
    # The vibe answers -> preference phrases -> curation user content (rerank signal).
    student = {"grade": 10, "preferences": collect_preferences({"collaboration": "team", "intensity": "immersive"})}
    content = build_curation_user_content(student, [{"id": "o1", "name": "X"}])
    assert "WHAT MATTERS TO THEM RIGHT NOW" in content
    assert "Prefers team / cohort settings" in content
    assert "Prefers full-time, immersive commitments" in content
    # No preferences -> no preferences section at all.
    assert "WHAT MATTERS TO THEM RIGHT NOW" not in build_curation_user_content({"grade": 10}, [{"id": "o1"}])


def test_next_vibe_rung_local_fallback_on_unparseable_model_output():
    ask = lambda system, uc: "not json at all"
    def parse(_):
        raise ValueError("bad")
    rung = next_vibe_rung(_pool(CURATE_AT + 20), {}, {}, ask, parse, rungs_done=0)
    assert rung is not None and rung["kind"] == "vibe"
    assert rung["classification"] == {}
    assert len(rung["options"]) == 2      # a valid local question despite the parse failure
