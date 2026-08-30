"""Recall stage (app/services/matching.py) — the pure, offline-testable core of Phase 5.

Covers: the content hash / embed-text contract, the LOOSENED grade filter (incl. the
rising/age escape hatches and the grade_min-vs-grade_max interpretation flagged in the plan),
the geo-scope filter, the vectorized best-theme cosine (incl. zero-vector and dim-mismatch
edges), and the recall orchestration (filters -> score -> top-N, thin-profile fallback).
No network, no numpy randomness — fixed vectors in, deterministic scores out.
"""
import math

import numpy as np
import pytest

from app.services import matching as m


# --------------------------------------------------------------------------- embed / hash

def test_embed_text_is_stable_and_labeled():
    row = {"name": "USABO", "org": "CEE", "summary": "Biology olympiad.",
           "subject_tags": ["Biology", "Olympiad"], "type": "Competition"}
    text = m.embed_text(row)
    assert "name: USABO" in text
    assert "subject_tags: Biology, Olympiad" in text
    # deterministic
    assert m.embed_text(row) == text


def test_embed_text_excludes_eligibility_and_logistics():
    row = {"name": "X", "org": "Y", "summary": "Z", "subject_tags": [], "type": "Program",
           "eligibility": "US citizens only", "price": "Paid", "state": "MA",
           "location": "In-Person", "season": "Summer"}
    text = m.embed_text(row)
    for leaked in ("citizens", "Paid", "MA", "In-Person", "Summer"):
        assert leaked not in text, f"{leaked!r} must not enter the fit embedding"


def test_hash_changes_only_on_embed_fields():
    base = {"name": "A", "org": "B", "summary": "C", "subject_tags": ["t"], "type": "Program"}
    h0 = m.match_vector_content_hash(base)
    # changing a NON-embed field does not change the hash
    assert m.match_vector_content_hash({**base, "eligibility": "new", "price": "Free"}) == h0
    # changing an embed field does
    assert m.match_vector_content_hash({**base, "summary": "different"}) != h0
    assert m.match_vector_content_hash({**base, "subject_tags": ["t", "u"]}) != h0


# --------------------------------------------------------------------------- grade filter

def test_grade_unknown_student_keeps_everything():
    assert m.recall_grade_ok({"grade_min": 11}, None) is True


def test_grade_no_bounds_keeps():
    assert m.recall_grade_ok({}, 9) is True
    assert m.recall_grade_ok({"grade_min": None}, 9) is True


def test_grade_student_old_enough_keeps():
    assert m.recall_grade_ok({"grade_min": 9}, 9) is True   # exactly at min
    assert m.recall_grade_ok({"grade_min": 9}, 11) is True  # above min


def test_grade_older_only_program_dropped_for_young_student():
    # 9th grader vs juniors-only (grade_min 11) with no escape phrasing -> DROP.
    assert m.recall_grade_ok({"grade_min": 11, "eligibility": "Open to all high schoolers."}, 9) is False


def test_grade_rising_phrasing_is_an_escape_hatch():
    # "rising 10th graders" means a current 9th grader IS eligible; the numeric grade_min may
    # have encoded it backwards, so a rising phrasing must NOT be dropped.
    row = {"grade_min": 10, "eligibility": "For rising 10th graders."}
    assert m.recall_grade_ok(row, 9) is True


def test_grade_age_phrasing_is_an_escape_hatch():
    row = {"grade_min": 11, "eligibility": "Youth ages 13-19 welcome."}
    assert m.recall_grade_ok(row, 9) is True


def test_grade_does_not_drop_on_upper_bound():
    # A senior (12) vs a program capped at grade 11: recall keeps it (Phase 1 decides).
    assert m.recall_grade_ok({"grade_min": 9, "grade_max": 11}, 12) is True


# --------------------------------------------------------------------------- geo scope

def test_geo_remote_always_passes():
    for loc in ("Remote", "In-Person and Remote", "online"):
        assert m.geo_scope_ok({"location": loc, "state": "MA"}, "WA") is True


def test_geo_in_person_matching_state_passes():
    assert m.geo_scope_ok({"location": "In-Person", "state": "WA"}, "WA") is True


def test_geo_in_person_other_state_dropped():
    assert m.geo_scope_ok({"location": "In-Person", "state": "MA"}, "WA") is False


def test_geo_unknown_either_side_passes():
    assert m.geo_scope_ok({"location": "In-Person", "state": None}, "WA") is True
    assert m.geo_scope_ok({"location": "In-Person", "state": "MA"}, None) is True


def test_geo_normalizes_full_state_name_against_code():
    # catalog stores "WA"; student says "Washington" — must still match (not be dropped)
    assert m.geo_scope_ok({"location": "In-Person", "state": "WA"}, "Washington") is True
    assert m.geo_scope_ok({"location": "In-Person", "state": "WA"}, "washington") is True
    # and a genuine mismatch by full name still drops
    assert m.geo_scope_ok({"location": "In-Person", "state": "MA"}, "Washington") is False


# --------------------------------------------------------------------------- cosine

def test_best_theme_scores_picks_max_over_themes():
    rows = np.array([[1.0, 0.0], [0.0, 1.0]])
    themes = np.array([[1.0, 0.0]])  # aligns with row 0, orthogonal to row 1
    scores = m.best_theme_scores(rows, themes)
    assert scores[0] == pytest.approx(1.0)
    assert scores[1] == pytest.approx(0.0)


def test_best_theme_scores_takes_best_of_several_themes():
    rows = np.array([[0.0, 1.0]])
    themes = np.array([[1.0, 0.0], [0.0, 1.0]])  # second theme aligns
    assert m.best_theme_scores(rows, themes)[0] == pytest.approx(1.0)


def test_best_theme_scores_zero_vector_is_zero_not_nan():
    rows = np.array([[0.0, 0.0]])           # all-zero row embedding (a QA-flag row)
    themes = np.array([[1.0, 1.0]])
    score = m.best_theme_scores(rows, themes)[0]
    assert score == 0.0 and not math.isnan(score)


def test_best_theme_scores_empty_themes_is_zeros():
    rows = np.array([[1.0, 0.0], [0.0, 1.0]])
    scores = m.best_theme_scores(rows, np.zeros((0, 0)))
    assert list(scores) == [0.0, 0.0]


# --------------------------------------------------------------------------- recall()

def _row(id_, vec, **extra):
    return {"id": id_, "match_vector": vec, "status": "running", **extra}


def test_recall_ranks_by_similarity_and_caps():
    themes = [[1.0, 0.0]]
    rows = [
        _row("aligned", [1.0, 0.0]),
        _row("orthogonal", [0.0, 1.0]),
        _row("partial", [0.7, 0.7]),
    ]
    out = m.recall(rows, themes, limit=2)
    assert [r["id"] for r in out] == ["aligned", "partial"]  # top 2 by cosine


def test_recall_drops_not_running():
    themes = [[1.0, 0.0]]
    rows = [_row("live", [1.0, 0.0]), _row("dead", [1.0, 0.0], status="not_running")]
    out = m.recall(rows, themes)
    assert [r["id"] for r in out] == ["live"]


def test_recall_applies_grade_and_geo():
    themes = [[1.0, 0.0]]
    rows = [
        _row("ok", [1.0, 0.0]),
        _row("tooOld", [1.0, 0.0], grade_min=11, eligibility="Juniors and seniors."),
        _row("farAway", [1.0, 0.0], location="In-Person", state="MA"),
    ]
    out = m.recall(rows, themes, student_grade=9, student_state="WA")
    assert [r["id"] for r in out] == ["ok"]


def test_recall_drops_rows_without_vectors_when_scoring():
    themes = [[1.0, 0.0]]
    rows = [_row("has", [1.0, 0.0]), {"id": "novec", "status": "running"}]
    out = m.recall(rows, themes)
    assert [r["id"] for r in out] == ["has"]


def test_recall_thin_profile_returns_filtered_unscored():
    # No theme vectors -> no semantic signal -> return the filtered set order-preserved.
    rows = [_row("a", [1.0, 0.0]), _row("b", [0.0, 1.0]), _row("dead", [1.0, 0.0], status="not_running")]
    out = m.recall(rows, [], limit=10)
    assert [r["id"] for r in out] == ["a", "b"]  # 'dead' filtered, order kept, no ranking


def test_recall_dim_mismatch_raises():
    with pytest.raises(ValueError):
        m.recall([_row("x", [1.0, 0.0, 0.0])], [[1.0, 0.0]])


def test_recall_cost_pref_free_cuts_paid_keeps_free_and_unknown():
    themes = [[1.0, 0.0]]
    rows = [_row("free", [1.0, 0.0], price="Free"),
            _row("paid", [1.0, 0.0], price="Paid"),
            _row("unknown", [1.0, 0.0])]                 # no price -> never cut
    ids = {r["id"] for r in m.recall(rows, themes, cost_pref="free")}
    assert ids == {"free", "unknown"}                    # paid dropped
    # "any"/None keeps everyone.
    assert {r["id"] for r in m.recall(rows, themes, cost_pref="any")} == {"free", "paid", "unknown"}
    assert {r["id"] for r in m.recall(rows, themes)} == {"free", "paid", "unknown"}


def test_recall_project_match_boosted_over_theme_match():
    # A row matching a PROJECT vector out-ranks a row matching a THEME vector at the same cosine.
    themes = [[1.0, 0.0]]
    projects = [[0.0, 1.0]]
    rows = [_row("theme_hit", [1.0, 0.0]),      # cosine 1.0 with theme, 0 with project
            _row("project_hit", [0.0, 1.0])]    # cosine 0 with theme, 1.0 with project -> *1.2
    out = m.recall(rows, themes, project_vectors=projects)
    assert [r["id"] for r in out] == ["project_hit", "theme_hit"]   # boosted project wins


def test_recall_projects_only_still_scores():
    # No themes, only a project vector -> ranks by the (boosted) project cosine, still works.
    out = m.recall([_row("a", [0.0, 1.0]), _row("b", [1.0, 0.0])], [], project_vectors=[[0.0, 1.0]])
    assert [r["id"] for r in out] == ["a", "b"]


def test_recall_time_pref_summer_keeps_summer_yearlong_unknown():
    themes = [[1.0, 0.0]]
    rows = [_row("summer", [1.0, 0.0], season="Summer"),
            _row("fall", [1.0, 0.0], season="Fall"),
            _row("year", [1.0, 0.0], season="Year-Long"),
            _row("unknown", [1.0, 0.0])]                 # no season -> never cut
    ids = {r["id"] for r in m.recall(rows, themes, time_pref="summer")}
    assert ids == {"summer", "year", "unknown"}          # school-year-only (fall) dropped
    assert {r["id"] for r in m.recall(rows, themes, time_pref="any")} == {"summer", "fall", "year", "unknown"}
