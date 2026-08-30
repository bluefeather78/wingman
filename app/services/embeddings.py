"""Catalog + student embedding production for the matching pipeline (Phase 5).

Two producers, one pinned model (gemini_common.EMBED_MODEL — same on both sides so cosine is
meaningful):
  * catalog rows  — embedded via the ACTIVATION-GATED hook: recompute a row's `match_vector`
    only when a write leaves the row active AND the embed-fields content hash changed. The
    decision (should_recompute_embedding) is pure and tested; the recompute+persist is impure.
  * student themes — embedded at query time from the profile's theme text.

The cosine matching itself lives in app/services/matching.py; this module only turns text into
vectors and decides when a catalog vector is stale.
"""
from __future__ import annotations

import datetime

from app.services.matching import embed_text, match_vector_content_hash


def should_recompute_embedding(is_active_after: bool, stored_hash: str | None, current_hash: str) -> bool:
    """The activation-gated refresh rule, as a pure decision.

    Recompute iff the write leaves the row ACTIVE and the embed-fields content hash differs
    from what the stored vector was computed against (a missing/None stored hash counts as
    differing, so a newly-activated or never-embedded row recomputes). A row that stays
    inactive is skipped — a pending-review scrape/edit may never activate, so embedding it
    early is wasted spend. See the plan's write-path table:
      * scraper insert / console edit -> lands inactive -> skip
      * activation                    -> becomes active  -> recompute (first vector)
      * refresh_opportunities on a live row -> stays active -> recompute iff text changed
    """
    if not is_active_after:
        return False
    return stored_hash != current_hash


def refresh_row_embedding(row: dict, api_key: str, embed_fn=None):
    """Compute a fresh embedding for one catalog row and return the columns to PATCH, or None
    if nothing should change (row inactive, or hash unchanged from what's stored).

    `embed_fn(texts, api_key) -> (vectors, usage)` defaults to gemini_common.call_gemini_embed;
    injectable so callers/tests can stub the paid call. Returns a dict:
      {"match_vector", "match_vector_hash", "match_vector_computed_at", "_cost_usd", "_usage"}
    (the private keys are for cost banking, not for the PATCH — strip them before writing).
    `computed_at` is passed in by the caller via `now` is avoided here (this module has no
    wall-clock dependency in its pure decision); the impure path stamps it explicitly."""
    is_active = bool(row.get("is_active", True))  # rows fetched for embedding are active
    current_hash = match_vector_content_hash(row)
    stored_hash = row.get("match_vector_hash")
    if not should_recompute_embedding(is_active, stored_hash, current_hash):
        return None

    if embed_fn is None:
        from gemini_common import call_gemini_embed, estimate_embed_cost
        embed_fn = call_gemini_embed
        cost_fn = estimate_embed_cost
    else:
        from gemini_common import estimate_embed_cost as cost_fn

    vectors, usage = embed_fn([embed_text(row)], api_key)
    vec = vectors[0] if vectors else []
    if not vec:
        # An empty vector is a failed embed, not a valid "no interests" answer — do not
        # persist it (it would poison recall as an all-zero row). Signal by returning None.
        return None
    return {
        "match_vector": vec,
        "match_vector_hash": current_hash,
        "match_vector_computed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "_cost_usd": cost_fn(usage),
        "_usage": usage,
    }


def embed_student_themes(theme_texts: list[str], api_key: str, embed_fn=None):
    """Embed a student's profile-theme texts (one vector per theme) for the recall stage.
    Returns (vectors, cost_usd). Empty/blank themes are dropped before the call so a thin
    profile costs nothing. Same pinned model as the catalog side."""
    texts = [t for t in (theme_texts or []) if t and str(t).strip()]
    if not texts:
        return [], 0.0
    if embed_fn is None:
        from gemini_common import call_gemini_embed, estimate_embed_cost
        embed_fn = call_gemini_embed
        cost_fn = estimate_embed_cost
    else:
        from gemini_common import estimate_embed_cost as cost_fn
    vectors, usage = embed_fn(texts, api_key)
    return vectors, cost_fn(usage)
