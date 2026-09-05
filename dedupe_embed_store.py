#!/usr/bin/env python3
"""DB-backed store for the catalog DEDUPE embedding (opportunities.dedupe_vector).

The dedupe embedding (name+org+type+summary+eligibility, via combined_reader.default_representation)
used to live in a repo-root JSONL sidecar (catalog_embeddings.jsonl). That was per-checkout local
state: a fresh clone had no index, so the scraper's dedupe hint went dark and db_health_check read
"0% covered", until someone re-ran the paid build. It now lives in three columns on `opportunities`,
exactly like the recall match_vector — computed once at ACTIVATION, backfillable ad-hoc, and read
straight out of the catalog the scraper already loads. See db/dedupe_vector_schema.sql.

This module is the seam, mirroring app/services/embeddings.py for the recall vector:
  * dedupe_representation / dedupe_content_hash  -- the text embedded + its freshness hash (pure)
  * refresh_row_dedupe_embedding                 -- one row's PATCH columns (PAID call, injectable)
  * rows_to_dedupe_entries / fetch_dedupe_index  -- read the stored vectors back as the entry-shape
                                                    list embed_common.nearest() already consumes

Kept SEPARATE from the recall vector on purpose: dedupe embeds different fields for a different job
(finding a new candidate's twin in the catalog), and the two vectors are never compared. This is
the counterpart of app/services/embeddings.refresh_row_embedding, which owns the RECALL vector.

MARQUEE M9: refresh_row_dedupe_embedding makes a paid Gemini embedding call. Same money seam as the
match_vector path; do not change the model / pricing / whether it spends without approval.
"""
from __future__ import annotations

import datetime
import hashlib

import embed_common
# The recompute rule is identical to the recall vector's (recompute iff the write leaves the row
# ACTIVE and the content hash changed), so reuse it rather than restate it — one rule, one place.
from app.services.embeddings import should_recompute_embedding

# What refresh/backfill SELECT to decide + compute an embedding: the representation fields plus the
# stored hash. Deliberately NOT dedupe_vector itself — that is ~768 floats a row and the hash is all
# the freshness check needs (same reasoning backfill_match_vectors.SELECT_FIELDS documents).
DEDUPE_SELECT_FIELDS = "id,name,org,type,summary,eligibility,is_active,dedupe_vector_hash"
# What the READ path (the scraper gate index) selects: id + the vector itself.
DEDUPE_INDEX_SELECT = "id,dedupe_vector,dedupe_vector_computed_at"

# Rows per PostgREST page for the index read. The dedupe vector is gemini-embedding-001's full
# 3072-dim output stored as jsonb — ~42KB/row, ~4x the 768-dim recall match_vector — so at ~1,680
# active rows the whole index is ~70MB. A default 1000-row page (~42MB) exceeds Supabase's ~8s
# statement timeout and 500s with code 57014, taking the dedupe hint dark (measured 2026-09-01:
# 0-999 -> HTTP 500 at 8.6s; 0-199 -> 200 rows, 8.4MB, 1.3s). 200 keeps each request's serialize
# well under the ceiling with room to spare; the result is identical, just more requests. The
# ceiling is the DB STATEMENT timeout, not the client socket timeout — do not raise this without
# re-measuring against the live payload.
DEDUPE_INDEX_PAGE_SIZE = 200


def dedupe_representation(row: dict) -> str:
    """The exact text embedded for one catalog row — the reader's own fields representation, so the
    stored index and a NEW candidate's query vector can never drift apart. Pure."""
    # Imported lazily: combined_reader pulls in classify_page / refresh_opportunities (the offline
    # discovery layer), which the shipped app has no reason to load just to hash a row.
    from combined_reader import default_representation
    return default_representation(row, "")


def dedupe_content_hash(row: dict) -> str:
    """A stable hash of the representation the dedupe vector depends on. Recompute the embedding iff
    this differs from the row's stored `dedupe_vector_hash`. Uses dedupe_representation so the hash
    and the embedded text can never disagree about what went into the vector."""
    return hashlib.sha256(dedupe_representation(row).encode("utf-8")).hexdigest()


def refresh_row_dedupe_embedding(row: dict, api_key: str, embed_fn=None):
    """Compute a fresh dedupe embedding for one catalog row and return the columns to PATCH, or None
    if nothing should change (row inactive, hash unchanged, empty representation, or a failed embed).

    `embed_fn(text, api_key) -> (vector, cost)` defaults to embed_common.embed_text; injectable so
    callers/tests can stub the paid call. Returns a dict:
      {"dedupe_vector", "dedupe_vector_hash", "dedupe_vector_computed_at", "_cost_usd"}
    (strip the private `_cost_usd` before writing). Same contract as
    app.services.embeddings.refresh_row_embedding, for the recall vector."""
    is_active = bool(row.get("is_active", True))  # rows fetched for embedding are active
    current_hash = dedupe_content_hash(row)
    stored_hash = row.get("dedupe_vector_hash")
    if not should_recompute_embedding(is_active, stored_hash, current_hash):
        return None

    rep = dedupe_representation(row)
    if not rep.strip():
        return None  # nothing to embed; do not persist an all-zero row

    if embed_fn is None:
        embed_fn = embed_common.embed_text
    vector, cost = embed_fn(rep, api_key)
    if not vector:
        # An empty vector is a failed embed, not a valid answer — do not persist it (it would poison
        # the dedupe search as an all-zero row). Signal by returning None, exactly like the recall path.
        return None
    return {
        "dedupe_vector": vector,
        "dedupe_vector_hash": current_hash,
        "dedupe_vector_computed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "_cost_usd": cost,
    }


def rows_needing_dedupe_embedding(rows):
    """[(row, current_hash)] for ACTIVE rows whose stored hash differs from current content. Pure —
    the selection half of the ad-hoc backfill, unit-tested without the network. Mirrors
    backfill_match_vectors.rows_needing_embedding."""
    out = []
    for r in rows or []:
        current = dedupe_content_hash(r)
        if should_recompute_embedding(bool(r.get("is_active", True)),
                                      r.get("dedupe_vector_hash"), current):
            out.append((r, current))
    return out


def rows_to_dedupe_entries(rows):
    """Pure: DB rows (id + dedupe_vector) -> the [{id, vector, rep, source, embedded_at}] shape
    embed_common.nearest()/cosine() consume. A row with no stored vector is skipped, so the index
    holds exactly the rows that have been embedded — the same contract embed_common.load_index had
    for the JSONL, just sourced from the catalog instead of a sidecar file."""
    out = []
    for r in rows or []:
        rid, vec = r.get("id"), r.get("dedupe_vector")
        if rid and vec:
            out.append({"id": rid, "vector": list(vec), "rep": "fields", "source": "catalog",
                        "embedded_at": r.get("dedupe_vector_computed_at")})
    return out


def fetch_dedupe_index(supabase_url, key, active_only=True, include_inactive=False):
    """Read the stored dedupe vectors as an index (entry-shape list). Impure (one paginated GET).

    Replaces embed_common.load_index() for every live/analysis consumer. `supabase_get` pages past
    PostgREST's 1000-row cap, so the whole ~1,680-row catalog comes back — an unpaginated read would
    silently truncate the index and let duplicates through (the trap CLAUDE.md flags for this read).
    Pages at DEDUPE_INDEX_PAGE_SIZE, well below the 1000-row cap, because the ~42KB/row vector makes
    a full 1000-row page exceed Supabase's statement timeout (see that constant's note).
    A missing column (schema not run yet) surfaces as an empty index, which degrades the dedupe hint
    to off — never an exception, matching the JSONL "no file -> empty index" behaviour it replaces.
    Any OTHER failure (a statement timeout on the vector payload, an outage) also degrades to an
    empty index so a run is never crashed by a HINT — but it is announced, because a SILENT empty
    index reads as "no duplicates" when it means "we could not check", and that is the difference
    between the hint being off and being wrong.
    """
    import urllib.error
    from supabase_common import supabase_get
    params = {"select": DEDUPE_INDEX_SELECT}
    if active_only and not include_inactive:
        params["is_active"] = "eq.true"
    try:
        rows = supabase_get(supabase_url.rstrip("/"), "opportunities", params, key,
                            page_size=DEDUPE_INDEX_PAGE_SIZE) or []
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return []   # column not migrated yet -> hint simply off, as designed
        print(f"[WARN] dedupe index read failed (HTTP {e.code}) — dedupe hint OFF this run.")
        return []
    except Exception as e:                                          # noqa: BLE001
        print(f"[WARN] dedupe index read failed ({type(e).__name__}) — dedupe hint OFF this run.")
        return []
    return rows_to_dedupe_entries(rows)


def fetch_dedupe_index_from_env(active_only=True, include_inactive=False):
    """fetch_dedupe_index with SUPABASE_URL/key read from .env — the one-liner the offline analysis
    tools (dedupe_queue, dedupe_eval, find_catalog_dups) call in place of embed_common.load_index().
    Returns an empty index if no Supabase credentials are configured (never raises)."""
    from supabase_common import load_dotenv
    import os
    load_dotenv()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        return []
    return fetch_dedupe_index(url, key, active_only=active_only, include_inactive=include_inactive)
