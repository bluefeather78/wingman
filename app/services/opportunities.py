"""Opportunities catalog fetch + in-process cache for /api/opportunities.
Extracted verbatim from server.py (docs/archive/PLAN_1_decompose.md).
"""
import gzip
import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import *  # noqa: F401,F403
from app.core import _supabase_request
from app.http_pool import pooled_urlopen

# ---------- Two caches, not one (Phase 2 item 5) ----------
# There used to be ONE cache holding every row WITH its 768-dim `match_vector`, refreshed on a
# 5-minute TTL. That is ~20MB pulled from Supabase every five minutes to serve a payload the
# browser never sees — handle_opportunities stripped the column off every row on the way out —
# and it is what pushed the fetch into Supabase's statement timeout in the first place
# (the 57014 retry below is the scar).
#
# The two halves have completely different change rates, so they get completely different TTLs:
#   catalog  — name/dates/status/tags. An admin edit or an activation should show up fast, so
#              this keeps the original short TTL. Cheap: no vector, ~0.5s for the whole table.
#   vectors  — embeddings, which change only when something is re-embedded offline. A 5-minute
#              cadence on those was pure waste, so they sit behind a 24h backstop (decision 6,
#              Shama 2026-09-02).
#
# INSTANT-ON-CHANGE IS PRESERVED, and it is the part that would break silently: the ops console
# busts this cache when a row is activated or moderated, and a split that busted only the
# catalog would leave a newly-activated listing un-matchable for a DAY while looking perfectly
# fine in the browser. bust_catalog_cache() below clears BOTH, and ops/core.py calls it rather
# than reaching into a dict — so a third cache added later cannot be forgotten by four callers.
_catalog_cache = {"rows": None, "fetched_at": 0.0, "body": None, "gzip": None, "etag": None}
_catalog_lock = threading.Lock()

_vector_cache = {"vectors": None, "fetched_at": 0.0}
_vector_lock = threading.Lock()


def bust_catalog_cache():
    """Force the next read of BOTH caches to refetch. Called by the ops console on activate /
    moderate / bulk edit, which is what keeps a new listing instantly visible AND instantly
    matchable."""
    with _catalog_lock:
        _catalog_cache["fetched_at"] = 0.0
    with _vector_lock:
        _vector_cache["fetched_at"] = 0.0


# Latched OFF the first time a fetch 400s because opportunities.match_vector is not migrated
# yet (db/match_vector_schema.sql not run). Once off, the catalog is fetched WITHOUT the vector —
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


# A full page of 768-dim `match_vector`s is what pushed the old 1000-row page past Supabase's
# per-statement timeout: measured 2026-09-02, a 1000-row page of the vector column sits right at
# the edge (~6-7s) and intermittently 500s with PostgREST code 57014 ("canceling statement due to
# statement timeout"), while the same rows without the vector return in ~0.5s. That failure had no
# graceful path (the 57014 is a 500, not the 400 the match_vector-missing degrade handles), so a
# cold cache surfaced it to the app as a 502. Keep each page's vector payload well under the
# timeout AND retry the occasional slow page, since the timeout is load-dependent and variable.
CATALOG_PAGE_SIZE = 250
_PAGE_FETCH_ATTEMPTS = 4


def _is_statement_timeout(exc):
    """True if `exc` is a PostgREST 500 for a canceled statement (timeout, code 57014)."""
    if not isinstance(exc, urllib.error.HTTPError) or exc.code != 500:
        return False
    try:
        return "57014" in exc.read().decode("utf-8", "replace")
    except Exception:
        return False


def _fetch_catalog_page(select_fields, offset, page_size):
    query = urllib.parse.urlencode({
        "select": select_fields, "is_active": "eq.true", "order": "id",
    })
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            "Range": f"{offset}-{offset + page_size - 1}",
        },
    )
    with pooled_urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _paginated_catalog_fetch(select_fields):
    """Fetch all active rows for `select_fields`, paginating past PostgREST's 1000-row cap.

    Pages are deliberately small (CATALOG_PAGE_SIZE) so a page carrying the large `match_vector`
    column stays under Supabase's statement timeout, and a page that still times out (a load
    spike) is retried a few times with backoff rather than failing the whole fetch mid-way."""
    page_size = CATALOG_PAGE_SIZE
    data = []
    offset = 0
    while True:
        for attempt in range(_PAGE_FETCH_ATTEMPTS):
            try:
                page = _fetch_catalog_page(select_fields, offset, page_size)
                break
            except urllib.error.HTTPError as e:
                if _is_statement_timeout(e) and attempt < _PAGE_FETCH_ATTEMPTS - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise
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


def _serialise(rows):
    """Pre-compute what the route sends: JSON bytes, a gzip of them, and an ETag.

    Done ONCE per catalog refresh rather than once per request. Serialising ~1,700 rows and
    gzipping them is real CPU, and on 0.1 of a core it was being paid on every single call for
    a body that is identical between refreshes.
    """
    body = json.dumps(rows).encode()
    return {
        "body": body,
        "gzip": gzip.compress(body, compresslevel=6),
        # Weak-style quoted hash of the exact bytes served. Short because it goes out on every
        # response and back on every conditional request.
        "etag": '"' + hashlib.sha256(body).hexdigest()[:32] + '"',
    }


def _refresh_catalog_locked():
    """Refetch the vector-free catalog and re-serialise it. Caller holds _catalog_lock."""
    rows = _paginated_catalog_fetch(_select_without_match_vector())
    _catalog_cache.update(_serialise(rows))
    _catalog_cache["rows"] = rows
    _catalog_cache["fetched_at"] = time.time()
    return rows


def fetch_opportunities():
    """The catalog WITHOUT match_vector, cached for OPPORTUNITIES_CACHE_TTL.

    Raises on the first-ever fetch failure (there is nothing to serve yet); a stale cache is
    served on later failures rather than erroring, which is the behaviour this has always had.
    """
    with _catalog_lock:
        age = time.time() - _catalog_cache["fetched_at"]
        if _catalog_cache["rows"] is not None and age < OPPORTUNITIES_CACHE_TTL:
            return _catalog_cache["rows"]
        try:
            return _refresh_catalog_locked()
        except Exception:
            if _catalog_cache["rows"] is not None:
                return _catalog_cache["rows"]      # serve stale on a transient failure
            raise


def catalog_payload():
    """(body, gzip, etag) for /api/opportunities, refreshing the catalog if the TTL expired."""
    fetch_opportunities()
    with _catalog_lock:
        return _catalog_cache["body"], _catalog_cache["gzip"], _catalog_cache["etag"]


def fetch_vectors():
    """{id: match_vector} for every active row, cached for CATALOG_VECTOR_CACHE_TTL (24h).

    Degrades exactly as the combined fetch did: on the first 400 naming match_vector the column
    is latched off and this returns {} — the catalog endpoint keeps working and matching recall
    falls back to its thin-profile path until db/match_vector_schema.sql is run.
    """
    global _match_vector_available
    with _vector_lock:
        age = time.time() - _vector_cache["fetched_at"]
        if _vector_cache["vectors"] is not None and age < CATALOG_VECTOR_CACHE_TTL:
            return _vector_cache["vectors"]
        if not _match_vector_available:
            return _vector_cache["vectors"] or {}
        try:
            rows = _paginated_catalog_fetch("id,match_vector")
        except Exception as e:
            if _is_missing_match_vector_error(e):
                _match_vector_available = False
                print("[WARN] opportunities.match_vector not found — run "
                      "db/match_vector_schema.sql. Serving the catalog without embeddings; "
                      "matching recall is degraded until then.")
                _vector_cache["vectors"] = {}
                _vector_cache["fetched_at"] = time.time()
                return {}
            if _vector_cache["vectors"] is not None:
                return _vector_cache["vectors"]    # serve stale on a transient failure
            raise
        vectors = {r["id"]: r.get("match_vector") for r in rows if r.get("match_vector")}
        _vector_cache["vectors"] = vectors
        _vector_cache["fetched_at"] = time.time()
        return vectors


def fetch_opportunities_with_vectors():
    """Catalog rows with `match_vector` attached — what /api/match needs and nothing else does.

    The two caches are read independently and joined here, so browsing never pays for the
    vector pull and matching never re-reads the catalog just to get one.
    """
    rows = fetch_opportunities()
    vectors = fetch_vectors()
    if not vectors:
        return rows
    return [{**row, "match_vector": vectors.get(row.get("id"))} for row in rows]
