"""Opportunities catalog fetch + in-process cache for /api/opportunities.
Extracted verbatim from server.py (PLAN_1_decompose.md).
"""
import io
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import *  # noqa: F401,F403
from app.core import _supabase_request

_opportunities_cache = {"data": None, "fetched_at": 0.0}
_opportunities_cache_lock = threading.Lock()

# Latched OFF the first time a fetch 400s because opportunities.match_vector is not migrated
# yet (match_vector_schema.sql not run). Once off, the catalog is fetched WITHOUT the vector —
# the client never sees it anyway (handle_opportunities strips it), and matching recall simply
# has no vectors until the column exists (it degrades to the thin-profile path). This is the
# same "degrade until migrated" convention every other schema-gated column here follows: a
# missing column must never take down /api/opportunities, which is the whole app's data source.
# Re-latches on process restart (a hopeful full select after the DDL is finally run).
_match_vector_available = True


def _select_without_match_vector():
    """OPPORTUNITIES_FIELDS minus the not-yet-migrated match_vector column."""
    return ",".join(
        f for f in OPPORTUNITIES_FIELDS.split(",")
        if f not in OPPORTUNITIES_CLIENT_STRIP_FIELDS
    )


def _paginated_catalog_fetch(select_fields):
    """Fetch all active rows for `select_fields`, paginating past PostgREST's 1000-row cap."""
    query = urllib.parse.urlencode({
        "select": select_fields, "is_active": "eq.true", "order": "id",
    })
    page_size = 1000
    data = []
    offset = 0
    while True:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
                "Range": f"{offset}-{offset + page_size - 1}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            page = json.loads(resp.read())
        data.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return data


def _is_missing_match_vector_error(exc):
    """True if `exc` is the PostgREST 400 for the un-migrated match_vector column."""
    if not isinstance(exc, urllib.error.HTTPError) or exc.code != 400:
        return False
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:
        return False
    return "match_vector" in body


def fetch_opportunities():
    """Returns the cached opportunities list, refreshing from Supabase if the
    TTL has expired. Raises on the first-ever fetch failure (nothing to serve
    yet); a stale cache is served on subsequent failures rather than erroring.

    Degrades gracefully if match_vector is not migrated: on the first 400 naming it, drops the
    column and refetches, so the catalog endpoint keeps working (matching recall just has no
    vectors until match_vector_schema.sql is run)."""
    global _match_vector_available
    with _opportunities_cache_lock:
        age = time.time() - _opportunities_cache["fetched_at"]
        if _opportunities_cache["data"] is not None and age < OPPORTUNITIES_CACHE_TTL:
            return _opportunities_cache["data"]

        select = OPPORTUNITIES_FIELDS if _match_vector_available else _select_without_match_vector()
        try:
            data = _paginated_catalog_fetch(select)
        except Exception as e:
            if _match_vector_available and _is_missing_match_vector_error(e):
                # Column not migrated yet — degrade once and retry without it.
                _match_vector_available = False
                print("[WARN] opportunities.match_vector not found — run match_vector_schema.sql. "
                      "Serving the catalog without embeddings; matching recall is degraded until then.")
                try:
                    data = _paginated_catalog_fetch(_select_without_match_vector())
                except Exception:
                    if _opportunities_cache["data"] is not None:
                        return _opportunities_cache["data"]
                    raise
            elif _opportunities_cache["data"] is not None:
                return _opportunities_cache["data"]  # serve stale on transient failure
            else:
                raise

        _opportunities_cache["data"] = data
        _opportunities_cache["fetched_at"] = time.time()
        return data
