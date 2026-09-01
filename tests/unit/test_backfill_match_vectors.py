"""Selection logic for the match_vector backfill (backfill_match_vectors.py). The paid embed
+ Supabase writes are not exercised; rows_needing_embedding is pure and is the piece that
decides what gets (re)embedded — it must agree with the runtime hook's hash so a row isn't
re-embedded forever.
"""
from backfill_match_vectors import rows_needing_embedding
from app.services.matching import match_vector_content_hash


def _row(id_, **extra):
    base = {"id": id_, "name": f"N{id_}", "org": "O", "summary": "S",
            "subject_tags": ["t"], "type": "Program"}
    base.update(extra)
    return base


def test_row_without_hash_needs_embedding():
    rows = [_row("a")]  # no match_vector_hash
    out = rows_needing_embedding(rows)
    assert [r["id"] for r, _ in out] == ["a"]
    # the returned hash is the current content hash (what gets stored)
    assert out[0][1] == match_vector_content_hash(rows[0])


def test_row_with_current_hash_is_skipped():
    r = _row("b")
    r["match_vector_hash"] = match_vector_content_hash(r)
    assert rows_needing_embedding([r]) == []


def test_row_with_stale_hash_needs_reembedding():
    r = _row("c")
    r["match_vector_hash"] = "stale-different-hash"
    out = rows_needing_embedding([r])
    assert [x["id"] for x, _ in out] == ["c"]


def test_text_change_invalidates_hash():
    # A row whose stored hash matched the OLD summary needs re-embedding after an edit.
    r = _row("d")
    r["match_vector_hash"] = match_vector_content_hash(r)
    r["summary"] = "a different summary now"
    assert [x["id"] for x, _ in rows_needing_embedding([r])] == ["d"]


def test_non_embed_field_change_does_not_invalidate():
    r = _row("e")
    r["match_vector_hash"] = match_vector_content_hash(r)
    r["eligibility"] = "US citizens only"  # not an embed field
    r["price"] = "Paid"
    assert rows_needing_embedding([r]) == []
