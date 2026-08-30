"""Behavioral (vibe) funnel rungs — the rerank-only questions ported server-side from the
opportunity-matching branch. Vibe rungs never filter (empty classification -> client keeps all),
and their answers become soft preference phrases handed to curation."""
import json

from app.services.funnel import (
    BEHAVIORAL_AXES, CURATE_AT, MAX_RUNGS,
    behavioral_pref, collect_preferences, build_vibe_rung, next_vibe_rung,
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
        "intensity": "__skip__",     # vibe but skipped -> no phrase
        "output": "output",          # vibe -> phrase
    }
    prefs = collect_preferences(answers)
    assert "Prefers team / cohort settings" in prefs
    assert "Wants to produce something tangible (paper, project, award)" in prefs
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
    parsed = {"axis": "output", "question": "Build a thing, or just explore?",
              "options": [{"value": "output", "label": "Ship it"}]}  # 'explore' label missing
    rung = build_vibe_rung(parsed, "output", _pool(1))
    by_value = {o["value"]: o["label"] for o in rung["options"]}
    assert by_value["output"] == "Ship it"
    assert by_value["explore"] == "Just explore"   # fell back to the example


def test_next_vibe_rung_stops_when_pool_small_or_capped():
    ask = lambda system, uc: json.dumps({"axis": "collaboration", "question": "q",
                                          "options": [{"value": "team", "label": "T"}, {"value": "solo", "label": "S"}]})
    parse = json.loads
    # Pool already small enough to curate -> no vibe question.
    assert next_vibe_rung(_pool(CURATE_AT), {}, {}, ask, parse, rungs_done=0) is None
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
