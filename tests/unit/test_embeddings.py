"""Embedding production (app/services/embeddings.py) + the embed cost estimate. The paid
Gemini call is stubbed via embed_fn injection; the activation-gated recompute decision and
the persist-shape logic are what's exercised. No network.
"""
import gemini_common
from app.services import embeddings as e
from app.services.matching import match_vector_content_hash


# --------------------------------------------------------------------------- should_recompute

def test_inactive_row_never_recomputes():
    assert e.should_recompute_embedding(False, None, "hash") is False
    assert e.should_recompute_embedding(False, "old", "new") is False


def test_active_row_missing_vector_recomputes():
    assert e.should_recompute_embedding(True, None, "hash") is True


def test_active_row_changed_hash_recomputes():
    assert e.should_recompute_embedding(True, "old", "new") is True


def test_active_row_unchanged_hash_skips():
    assert e.should_recompute_embedding(True, "same", "same") is False


# --------------------------------------------------------------------------- refresh_row

def _fake_embed(vec):
    def _fn(texts, api_key):
        return [list(vec) for _ in texts], {"input_tokens": 10}
    return _fn


def test_refresh_computes_and_stamps_when_stale():
    row = {"name": "N", "org": "O", "summary": "S", "subject_tags": ["t"], "type": "Program",
           "is_active": True, "match_vector_hash": None}
    out = e.refresh_row_embedding(row, "key", embed_fn=_fake_embed([0.1, 0.2, 0.3]))
    assert out["match_vector"] == [0.1, 0.2, 0.3]
    assert out["match_vector_hash"] == match_vector_content_hash(row)
    assert out["match_vector_computed_at"]           # stamped
    assert out["_cost_usd"] >= 0


def test_refresh_skips_when_hash_unchanged():
    row = {"name": "N", "org": "O", "summary": "S", "subject_tags": ["t"], "type": "Program",
           "is_active": True}
    row["match_vector_hash"] = match_vector_content_hash(row)   # already current
    assert e.refresh_row_embedding(row, "key", embed_fn=_fake_embed([1.0])) is None


def test_refresh_skips_inactive():
    row = {"name": "N", "is_active": False, "match_vector_hash": None}
    assert e.refresh_row_embedding(row, "key", embed_fn=_fake_embed([1.0])) is None


def test_refresh_does_not_persist_empty_vector():
    row = {"name": "N", "is_active": True, "match_vector_hash": None}
    assert e.refresh_row_embedding(row, "key", embed_fn=lambda texts, key: ([[]], {"input_tokens": 1})) is None


# --------------------------------------------------------------------------- student themes

def test_embed_student_themes_drops_blanks_and_returns_cost():
    calls = {}
    def _fn(texts, key):
        calls["texts"] = texts
        return [[1.0]] * len(texts), {"input_tokens": 5}
    vecs, cost = e.embed_student_themes(["robotics", "", "  ", "debate"], "key", embed_fn=_fn)
    assert calls["texts"] == ["robotics", "debate"]   # blanks dropped
    assert len(vecs) == 2
    assert cost >= 0


def test_embed_student_themes_empty_is_free():
    vecs, cost = e.embed_student_themes([], "key", embed_fn=lambda t, k: (_ for _ in ()).throw(AssertionError("should not call")))
    assert vecs == [] and cost == 0.0


# --------------------------------------------------------------------------- cost estimate

def test_estimate_embed_cost():
    assert gemini_common.estimate_embed_cost({"input_tokens": 0}) == 0
    assert gemini_common.estimate_embed_cost({"input_tokens": 1_000_000}) == gemini_common.EMBED_INPUT_PRICE_PER_TOKEN * 1_000_000


def test_call_gemini_embed_empty_is_free_no_network():
    # empty input returns immediately without touching the network (conftest would block it)
    vecs, usage = gemini_common.call_gemini_embed([], "key")
    assert vecs == [] and usage["input_tokens"] == 0
