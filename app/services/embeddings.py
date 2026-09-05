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
import hashlib

from app.services.matching import embed_text, match_vector_content_hash

# --------------------------------------------------------------------------- student-embed cache
#
# A student's theme/project embedding depends ONLY on the exact text list, so it is safe to
# reuse within a process. This exists so the pre-recall SearchSetup card can PREWARM the
# embedding (POST /api/match {prewarm:true}) while the student is still picking interest/budget/
# timing — then the real rung-0 recall is a cache HIT and the embedding round trip is off the
# critical path. Bounded (deterministic, no TTL needed — a model-pin change is a deploy that
# restarts the process and clears it); keyed by the pinned model + the joined texts so a pin
# change can never serve a vector from a different model.
_STUDENT_EMBED_CACHE: dict[str, list] = {}
_STUDENT_EMBED_ORDER: list[str] = []
_STUDENT_EMBED_MAX = 256


def _student_embed_key(texts: list[str]) -> str:
    from wingman.gemini_common import EMBED_MODEL
    joined = "\x00".join(texts)
    return hashlib.sha256(f"{EMBED_MODEL}\x00{joined}".encode("utf-8")).hexdigest()


def _cache_get(key: str):
    return _STUDENT_EMBED_CACHE.get(key)


def _cache_put(key: str, vectors: list) -> None:
    if key in _STUDENT_EMBED_CACHE:
        return
    _STUDENT_EMBED_CACHE[key] = vectors
    _STUDENT_EMBED_ORDER.append(key)
    while len(_STUDENT_EMBED_ORDER) > _STUDENT_EMBED_MAX:
        _STUDENT_EMBED_CACHE.pop(_STUDENT_EMBED_ORDER.pop(0), None)


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
        from wingman.gemini_common import call_gemini_embed, estimate_embed_cost
        embed_fn = call_gemini_embed
        cost_fn = estimate_embed_cost
    else:
        from wingman.gemini_common import estimate_embed_cost as cost_fn

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


def embed_student_themes(theme_texts: list[str], api_key: str, embed_fn=None, use_cache: bool = True):
    """Embed a student's profile-theme texts (one vector per theme) for the recall stage.
    Returns (vectors, cost_usd). Empty/blank themes are dropped before the call so a thin
    profile costs nothing. Same pinned model as the catalog side.

    A cache HIT returns cost 0.0 — the paid call happened once (usually at prewarm) and is not
    re-billed. `use_cache=False` forces a fresh call (and does not populate the cache), for the
    catalog-side and tests that must exercise the real path. A custom `embed_fn` skips the cache
    too, so stubbed tests are never served a real-model vector."""
    texts = [t for t in (theme_texts or []) if t and str(t).strip()]
    if not texts:
        return [], 0.0
    caching = use_cache and embed_fn is None
    if caching:
        key = _student_embed_key(texts)
        hit = _cache_get(key)
        if hit is not None:
            return hit, 0.0
    if embed_fn is None:
        from wingman.gemini_common import call_gemini_embed, estimate_embed_cost
        embed_fn = call_gemini_embed
        cost_fn = estimate_embed_cost
    else:
        from wingman.gemini_common import estimate_embed_cost as cost_fn
    vectors, usage = embed_fn(texts, api_key)
    if caching and vectors:
        _cache_put(key, vectors)
    return vectors, cost_fn(usage)
