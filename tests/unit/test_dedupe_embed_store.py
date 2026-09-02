"""The DB-backed dedupe-embedding seam: representation/hash freshness, the row-selection logic,
the per-row PATCH builder, and the entry-shape reader. Hermetic — the paid embed call is injected."""
import urllib.error

import dedupe_embed_store as des
import supabase_common


def _row(rid="ec1", name="MIT PRIMES", active=True, stored_hash=None):
    r = {"id": rid, "name": name, "org": "MIT", "type": "Program",
         "summary": "advanced research", "eligibility": "grades 9-12", "is_active": active}
    if stored_hash is not None:
        r["dedupe_vector_hash"] = stored_hash
    return r


def _fake_embed(text, key):
    return [0.1, 0.2, 0.3], 0.0001  # (vector, cost)


# --- representation + hash ------------------------------------------------------------

def test_representation_uses_fields():
    rep = des.dedupe_representation(_row(name="Research Science Institute"))
    assert "Research Science Institute" in rep and "Program" in rep


def test_hash_is_stable_and_content_sensitive():
    a = des.dedupe_content_hash(_row(name="Alpha"))
    assert a == des.dedupe_content_hash(_row(name="Alpha"))   # same fields -> same hash
    assert a != des.dedupe_content_hash(_row(name="Beta"))    # changed field -> different hash


# --- selection (rows_needing_dedupe_embedding) ----------------------------------------

def test_selects_active_rows_with_no_stored_hash():
    rows = [_row("ec1"), _row("ec2")]
    todo = des.rows_needing_dedupe_embedding(rows)
    assert {r["id"] for r, _ in todo} == {"ec1", "ec2"}


def test_skips_row_whose_stored_hash_is_current():
    r = _row("ec1")
    r["dedupe_vector_hash"] = des.dedupe_content_hash(r)  # already fresh
    assert des.rows_needing_dedupe_embedding([r]) == []


def test_skips_inactive_rows():
    assert des.rows_needing_dedupe_embedding([_row("ec1", active=False)]) == []


def test_reembeds_when_content_changed():
    r = _row("ec1", name="Old Name")
    r["dedupe_vector_hash"] = des.dedupe_content_hash(r)
    r["name"] = "New Name"  # fields moved out from under the stored hash
    todo = des.rows_needing_dedupe_embedding([r])
    assert [rid for (row, _h), rid in [((row, h), row["id"]) for row, h in todo]] == ["ec1"]


# --- the per-row PATCH builder --------------------------------------------------------

def test_refresh_returns_patch_for_a_new_active_row():
    patch = des.refresh_row_dedupe_embedding(_row("ec1"), "key", embed_fn=_fake_embed)
    assert patch["dedupe_vector"] == [0.1, 0.2, 0.3]
    assert patch["dedupe_vector_hash"] == des.dedupe_content_hash(_row("ec1"))
    assert patch["dedupe_vector_computed_at"]
    assert patch["_cost_usd"] == 0.0001


def test_refresh_noops_when_hash_unchanged():
    r = _row("ec1")
    r["dedupe_vector_hash"] = des.dedupe_content_hash(r)
    assert des.refresh_row_dedupe_embedding(r, "key", embed_fn=_fake_embed) is None


def test_refresh_noops_for_inactive_row():
    assert des.refresh_row_dedupe_embedding(_row("ec1", active=False), "key",
                                            embed_fn=_fake_embed) is None


def test_refresh_noops_on_empty_vector():
    empty = lambda text, key: ([], 0.0)
    assert des.refresh_row_dedupe_embedding(_row("ec1"), "key", embed_fn=empty) is None


# --- the entry-shape reader (what embed_common.nearest consumes) ----------------------

def test_rows_to_dedupe_entries_shape():
    rows = [{"id": "ec1", "dedupe_vector": [0.1, 0.2], "dedupe_vector_computed_at": "2026-09-01"},
            {"id": "ec2", "dedupe_vector": None},           # not embedded yet -> skipped
            {"id": "ec3", "dedupe_vector": [0.3, 0.4]}]
    entries = des.rows_to_dedupe_entries(rows)
    assert [e["id"] for e in entries] == ["ec1", "ec3"]
    assert entries[0]["vector"] == [0.1, 0.2]
    assert entries[0]["rep"] == "fields" and entries[0]["source"] == "catalog"
    assert entries[0]["embedded_at"] == "2026-09-01"


# --- fetch_dedupe_index: the impure read, its paging and its degradation --------------
# fetch_dedupe_index does `from supabase_common import supabase_get` at call time, so patching the
# module attribute is what the read actually resolves.

def test_fetch_dedupe_index_pages_below_the_statement_timeout_cap(monkeypatch):
    # The 3072-dim jsonb vector (~42KB/row) makes a full 1000-row page 500 with a Postgres statement
    # timeout (57014), so the read MUST request the smaller DEDUPE_INDEX_PAGE_SIZE. Lock it in —
    # reverting to the 1000 default silently re-breaks the dedupe hint on the live catalog.
    captured = {}

    def fake_get(url, table, params, key, page_size=1000):
        captured["page_size"] = page_size
        captured["params"] = dict(params)
        return [{"id": "ec1", "dedupe_vector": [0.1, 0.2], "dedupe_vector_computed_at": "2026-09-01"}]

    monkeypatch.setattr(supabase_common, "supabase_get", fake_get)
    entries = des.fetch_dedupe_index("https://x.supabase.co", "key")
    assert captured["page_size"] == des.DEDUPE_INDEX_PAGE_SIZE
    assert des.DEDUPE_INDEX_PAGE_SIZE <= 1000        # never above PostgREST's own per-response cap
    assert captured["params"]["is_active"] == "eq.true"
    assert [e["id"] for e in entries] == ["ec1"]


def test_fetch_dedupe_index_degrades_to_empty_and_announces_on_500(monkeypatch, capsys):
    # A statement-timeout 500 must degrade to an empty index (hint off), never crash the run, and
    # ANNOUNCE it — a silent empty index reads as "no duplicates" when it means "we could not check".
    def boom(url, table, params, key, page_size=1000):
        raise urllib.error.HTTPError(url, 500, "statement timeout", {}, None)

    monkeypatch.setattr(supabase_common, "supabase_get", boom)
    assert des.fetch_dedupe_index("https://x.supabase.co", "key") == []
    assert "dedupe hint OFF" in capsys.readouterr().out


def test_fetch_dedupe_index_is_silent_when_column_not_migrated(monkeypatch, capsys):
    # A 400 (dedupe_vector not migrated yet) is the DESIGNED "hint simply off" path — empty AND
    # silent, matching the JSONL "no file -> empty index" behaviour it replaced.
    def missing(url, table, params, key, page_size=1000):
        raise urllib.error.HTTPError(url, 400, "column does not exist", {}, None)

    monkeypatch.setattr(supabase_common, "supabase_get", missing)
    assert des.fetch_dedupe_index("https://x.supabase.co", "key") == []
    assert "dedupe hint OFF" not in capsys.readouterr().out
