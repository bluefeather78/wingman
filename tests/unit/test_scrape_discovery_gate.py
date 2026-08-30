"""Discovery-gate glue in scrape_opportunities: the pure helpers that shape a combined_reader
ReadResult into the row / dup_candidates the snapshot stores. Hermetic (no network, no model)."""
import scrape_opportunities as s
import queue_flags


# --- gate_metadata_overlay ----------------------------------------------------------------

def test_overlay_writes_page_fields_over_model_guess():
    row = {"id": "ec1", "url": "u", "source": "src", "is_active": False,
           "name": "Guessed Name", "type": "Program", "summary": None}
    changed = s.gate_metadata_overlay(row, {"name": "Real Name", "summary": "From the page",
                                            "type": "Competition"})
    assert row["name"] == "Real Name"          # page truth beats the model's phase-2 guess
    assert row["summary"] == "From the page"
    assert row["type"] == "Competition"
    assert changed == 3


def test_overlay_never_touches_identity_or_provenance():
    row = {"id": "ec1", "url": "keep", "source": "keep", "is_active": False, "seed_id": 7,
           "name": "N"}
    # a metadata dict that (wrongly) carries protected keys must not overwrite them
    s.gate_metadata_overlay(row, {"id": "HACKED", "url": "HACKED", "source": "HACKED",
                                  "is_active": True, "seed_id": 99})
    assert row["id"] == "ec1" and row["url"] == "keep" and row["source"] == "keep"
    assert row["is_active"] is False and row["seed_id"] == 7


def test_overlay_skips_nulls_and_counts_only_changes():
    row = {"id": "ec1", "url": "u", "source": "s", "is_active": False,
           "name": "Same", "org": "Old"}
    changed = s.gate_metadata_overlay(row, {"name": "Same", "org": "New", "summary": None})
    assert row["org"] == "New"
    assert row["name"] == "Same"            # unchanged value is not re-counted
    assert "summary" not in row             # a null field is not written
    assert changed == 1


def test_overlay_handles_empty_metadata():
    row = {"id": "ec1", "name": "N"}
    assert s.gate_metadata_overlay(row, {}) == 0
    assert s.gate_metadata_overlay(row, None) == 0


# --- gate_dup_candidates ------------------------------------------------------------------

def test_dup_hint_enriched_from_catalog_and_marked():
    by_id = {"ec9": {"id": "ec9", "name": "Stanford AI", "url": "https://x.edu/ai"}}
    hints = [{"id": "ec9", "score": 0.97, "reason": "0.97 page-content similarity"}]
    out = s.gate_dup_candidates(hints, by_id, existing_dups=None)
    assert len(out) == 1
    c = out[0]
    assert c["id"] == "ec9" and c["name"] == "Stanford AI" and c["url"] == "https://x.edu/ai"
    assert c["confidence"] == "hint" and c["via"] == queue_flags.DEDUPE_VIA
    assert "similarity" in c["reason"]


def test_dup_hint_merges_with_url_dedupe_entries():
    existing = [{"id": "sub1", "confidence": "strong", "reason": "identical URL"}]  # url_dedupe's
    hints = [{"id": "ec9", "score": 0.96, "reason": "0.96 page-content similarity"}]
    out = s.gate_dup_candidates(hints, {"ec9": {"id": "ec9", "name": "N", "url": "u"}}, existing)
    ids = [c["id"] for c in out]
    assert "sub1" in ids           # url_dedupe's submission-time candidate preserved
    assert "ec9" in ids            # the gate's hint added
    assert sum(1 for c in out if c.get("via") == queue_flags.DEDUPE_VIA) == 1


def test_dup_hint_unknown_id_still_stored_without_name():
    out = s.gate_dup_candidates([{"id": "ecX", "score": 0.94, "reason": "0.94 similarity"}],
                                by_id={}, existing_dups=None)
    assert out[0]["id"] == "ecX" and out[0]["name"] is None and out[0]["url"] is None


def test_no_hints_returns_existing_unchanged():
    existing = [{"id": "sub1", "confidence": "weak", "reason": "name 90% similar"}]
    assert s.gate_dup_candidates([], {}, existing) == existing
    assert s.gate_dup_candidates(None, {}, None) == []
