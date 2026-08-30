"""Catalog embedding backfill: the pure incremental-selection logic. Hermetic (no network)."""
import build_catalog_embeddings as bce


def _row(rid, name="A Program", summary="does things"):
    return {"id": rid, "name": name, "org": "Org", "type": "Program",
            "summary": summary, "eligibility": "grades 9-12"}


def test_representation_uses_fields():
    rep = bce.row_representation(_row("ec1", name="MIT PRIMES"))
    assert "MIT PRIMES" in rep and "Program" in rep


def test_incremental_skips_rows_already_indexed():
    rows = [_row("ec1"), _row("ec2"), _row("ec3")]
    todo = bce.select_rows_to_embed(rows, existing_ids={"ec1", "ec3"})
    assert [r["id"] for r in todo] == ["ec2"]


def test_rebuild_reembeds_everything():
    rows = [_row("ec1"), _row("ec2")]
    todo = bce.select_rows_to_embed(rows, existing_ids={"ec1", "ec2"}, rebuild=True)
    assert [r["id"] for r in todo] == ["ec1", "ec2"]


def test_skips_rows_with_no_id_or_empty_representation():
    rows = [_row(None), {"id": "ec9", "name": "", "org": "", "summary": "", "eligibility": ""},
            _row("ec1")]
    todo = bce.select_rows_to_embed(rows, existing_ids=set())
    assert [r["id"] for r in todo] == ["ec1"]
