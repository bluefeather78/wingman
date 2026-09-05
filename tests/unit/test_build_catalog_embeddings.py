"""Catalog dedupe-embedding backfill: the pure representation alias. Hermetic (no network).

The incremental-selection logic moved to dedupe_embed_store (hash-gated, DB-backed) — see
test_dedupe_embed_store.py. build_catalog_embeddings now only keeps the representation alias."""
from agents import build_catalog_embeddings as bce


def _row(rid, name="A Program", summary="does things"):
    return {"id": rid, "name": name, "org": "Org", "type": "Program",
            "summary": summary, "eligibility": "grades 9-12"}


def test_representation_uses_fields():
    rep = bce.row_representation(_row("ec1", name="MIT PRIMES"))
    assert "MIT PRIMES" in rep and "Program" in rep
