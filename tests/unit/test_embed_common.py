"""Embedding plumbing: cosine, nearest-neighbour, the on-disk index, cost, response parsing.

All pure. The vector CALL is network and is never exercised here — only its response parsers are.
"""
import math

from wingman import embed_common as ec


# ---------- cosine ----------

def test_cosine_identical_is_one():
    assert math.isclose(ec.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 1.0, rel_tol=1e-9)


def test_cosine_orthogonal_is_zero():
    assert math.isclose(ec.cosine([1.0, 0.0], [0.0, 1.0]), 0.0, abs_tol=1e-9)


def test_cosine_scale_invariant():
    assert math.isclose(ec.cosine([1.0, 2.0], [2.0, 4.0]), 1.0, rel_tol=1e-9)


def test_cosine_degenerate_inputs_are_zero():
    assert ec.cosine([], [1.0]) == 0.0
    assert ec.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert ec.cosine([1.0, 2.0], [1.0]) == 0.0  # length mismatch


# ---------- nearest ----------

def _idx():
    return [ec.index_entry("a", [1.0, 0.0]), ec.index_entry("b", [0.9, 0.1]),
            ec.index_entry("c", [0.0, 1.0])]


def test_nearest_sorts_by_score_and_caps_top_k():
    hits = ec.nearest([1.0, 0.0], _idx(), top_k=2)
    assert [h[0] for h in hits] == ["a", "b"]
    assert hits[0][1] >= hits[1][1]


def test_nearest_respects_min_score_and_exclude():
    hits = ec.nearest([1.0, 0.0], _idx(), top_k=5, min_score=0.5, exclude_ids={"a"})
    ids = [h[0] for h in hits]
    assert "a" not in ids and "c" not in ids and "b" in ids  # c below 0.5, a excluded


# ---------- index I/O ----------

def test_index_roundtrip_and_last_wins(tmp_path):
    path = tmp_path / "idx.jsonl"
    entries = [ec.index_entry("a", [1.0, 2.0]), ec.index_entry("b", [3.0, 4.0]),
               ec.index_entry("a", [9.0, 9.0])]  # a re-embed supersedes
    ec.save_index(entries, str(path))
    loaded = ec.load_index(str(path))
    by_id = {e["id"]: e["vector"] for e in loaded}
    assert by_id["a"] == [9.0, 9.0] and by_id["b"] == [3.0, 4.0]


def test_load_index_skips_garbage_and_vectorless(tmp_path):
    path = tmp_path / "idx.jsonl"
    path.write_text('{"id":"a","vector":[1,2]}\nnot json\n{"id":"b"}\n\n', encoding="utf-8")
    assert [e["id"] for e in ec.load_index(str(path))] == ["a"]


def test_load_missing_index_is_empty(tmp_path):
    assert ec.load_index(str(tmp_path / "nope.jsonl")) == []


# ---------- cost ----------

def test_approx_tokens_and_cost_scale_with_length():
    assert ec.approx_tokens("") >= 1
    assert ec.estimate_embed_cost(["x" * 400]) > 0
    assert ec.estimate_embed_cost(["x" * 800]) > ec.estimate_embed_cost(["x" * 400])


# ---------- response parsing ----------

def test_parse_embedding_and_batch():
    assert ec._parse_embedding({"embedding": {"values": [0.1, 0.2]}}) == [0.1, 0.2]
    assert ec._parse_embedding({}) == []
    assert ec._parse_batch({"embeddings": [{"values": [1.0]}, {"values": [2.0]}]}) == [[1.0], [2.0]]
    assert ec._parse_batch({}) == []
