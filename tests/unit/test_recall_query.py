"""Unit tests for the funnel-free recall-query helpers (docs/plans/RECALL_GRID_MERGE_PLAN.md)."""
from app.services import recall_query
from app.services.recall_query import (
    theme_embed_text, project_embed_text, student_embed_texts, recall_pool, attach_display,
)


def test_theme_and_project_embed_text():
    assert theme_embed_text("robotics") == "robotics"
    assert theme_embed_text({"theme": "Robotics", "intent": "compete", "next_steps": "win"}) \
        == "Robotics. compete. win"
    assert project_embed_text("Passion Project: a redstone economy mod") == "a redstone economy mod"
    assert project_embed_text("plain text") == "plain text"


def test_student_embed_texts_orders_themes_then_projects():
    student = {
        "profile_themes": ["ml", {"theme": "linguistics", "intent": "", "next_steps": ""}, ""],
        "highlight_projects": ["Research Project: g2p error rates", ""],
    }
    themes, projects = student_embed_texts(student)
    assert themes == ["ml", "linguistics"]         # blanks dropped
    assert projects == ["g2p error rates"]          # prefix stripped, blank dropped


def test_recall_pool_scores_and_orders_by_cosine():
    # Two catalog rows with unit vectors; theme vector [1,0]. r1 aligns exactly (1.0), r2 partial.
    rows = [
        {"id": "r1", "name": "A", "match_vector": [1.0, 0.0]},
        {"id": "r2", "name": "B", "match_vector": [0.8, 0.6]},  # cosine with [1,0] = 0.8
    ]
    student = {"profile_themes": ["ml"], "highlight_projects": [], "grade": None, "location": {}}

    def embed_fn(texts):
        assert texts == ["ml"]
        return [[1.0, 0.0]], 0.05

    pool, cost, scores = recall_pool(rows, student, embed_fn)
    assert [r["id"] for r in pool] == ["r1", "r2"]       # best-first
    assert cost == 0.05
    assert round(scores["r1"], 3) == 1.0
    assert round(scores["r2"], 3) == 0.8


def test_recall_pool_thin_profile_returns_unscored():
    rows = [{"id": "r1", "name": "A", "match_vector": [1.0, 0.0]}]
    student = {"profile_themes": [], "highlight_projects": [], "grade": None, "location": {}}
    pool, cost, scores = recall_pool(rows, student, lambda t: ([], 0.0))
    assert [r["id"] for r in pool] == ["r1"]  # recall's contract: still returns feasible rows
    assert cost == 0.0 and scores == {}


def test_attach_display_strong_badge(monkeypatch):
    monkeypatch.setattr(recall_query, "STRONG_MATCH_MIN", 0.6)
    pool = [{"id": "r1", "name": "A", "url": "u"}, {"id": "r2", "name": "B"}, {"id": "r3", "name": "C"}]
    scores = {"r1": 0.9, "r2": 0.55}  # r3 has no score
    out = attach_display(pool, scores)
    assert out[0]["strong"] is True and out[0]["score"] == 0.9
    assert out[1]["strong"] is False                 # 0.55 < 0.6
    assert out[2]["strong"] is False and out[2]["score"] is None
    assert out[0]["name"] == "A"                     # display fields carried
