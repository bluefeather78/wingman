"""Opportunities catalog fetch + in-process cache for /api/opportunities.
Extracted verbatim from server.py (PLAN_1_decompose.md).
"""
import json
import threading
import time
import urllib.parse
import urllib.request

from app.config import *  # noqa: F401,F403
from app.core import _supabase_request

_opportunities_cache = {"data": None, "fetched_at": 0.0}
_opportunities_cache_lock = threading.Lock()


def fetch_opportunities():
    """Returns the cached opportunities list, refreshing from Supabase if the
    TTL has expired. Raises on the first-ever fetch failure (nothing to serve
    yet); a stale cache is served on subsequent failures rather than erroring."""
    with _opportunities_cache_lock:
        age = time.time() - _opportunities_cache["fetched_at"]
        if _opportunities_cache["data"] is not None and age < OPPORTUNITIES_CACHE_TTL:
            return _opportunities_cache["data"]

        query = urllib.parse.urlencode({
            "select": OPPORTUNITIES_FIELDS,
            "is_active": "eq.true",
            "order": "id",
        })
        page_size = 1000  # PostgREST's default max-rows cap — paginate past it via Range
        try:
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
        except Exception:
            if _opportunities_cache["data"] is not None:
                return _opportunities_cache["data"]  # serve stale on transient failure
            raise

        _opportunities_cache["data"] = data
        _opportunities_cache["fetched_at"] = time.time()
        return data
