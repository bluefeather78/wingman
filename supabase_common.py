#!/usr/bin/env python3
"""Shared helpers for wingman's offline/manual Supabase scripts (scrape_opportunities.py,
check_deadlines.py): a stdlib-only .env loader plus small wrappers around Supabase's
PostgREST REST API (paginated GET, batched upsert POST, single-row insert, PATCH).

This consolidates logic that was previously duplicated near-identically across
migrate_to_supabase.py, export_json.py, and migrate_users_to_supabase.py.
server.py intentionally keeps its own copy of load_dotenv()/the fetch logic — it's
the live server process, so minimizing its import surface is worth the small
duplication.
"""
import json
import os
import urllib.parse
import urllib.request

PAGE_SIZE = 1000  # PostgREST's default max-rows cap — paginate past it via Range


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and not os.environ.get(key):
                os.environ[key] = value


def supabase_get(supabase_url, table, params, key, page_size=PAGE_SIZE):
    """Paginated GET against a Supabase/PostgREST table. `params` is a dict of
    query params, e.g. {"select": "id,url", "is_active": "eq.true"}.

    `page_size` caps rows per PostgREST request (default PAGE_SIZE=1000, PostgREST's own max-rows
    cap). LOWER it for a SELECT whose columns are large — e.g. a jsonb embedding vector — where a
    full 1000-row page can exceed Supabase's ~8s statement timeout and 500 with code 57014
    ("canceling statement due to statement timeout"). The total result is identical either way; a
    smaller page just fetches it in more, smaller requests. Never RAISE it above 1000 — PostgREST
    caps a single response there regardless, so a larger value would silently under-read."""
    query = urllib.parse.urlencode(params)
    rows = []
    offset = 0
    while True:
        req = urllib.request.Request(
            f"{supabase_url}/rest/v1/{table}?{query}",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Range": f"{offset}-{offset + page_size - 1}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            page = json.loads(resp.read())
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def supabase_post(supabase_url, table, rows, key, on_conflict=None, batch_size=500):
    """Batched upsert (or plain insert, if on_conflict is None) POST. Raises on any
    batch failure."""
    conflict_q = f"?on_conflict={on_conflict}" if on_conflict else ""
    prefer = "return=minimal,resolution=merge-duplicates" if on_conflict else "return=minimal"
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        req = urllib.request.Request(
            f"{supabase_url}/rest/v1/{table}{conflict_q}",
            data=json.dumps(batch).encode("utf-8"),
            method="POST",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": prefer,
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()


def supabase_insert_one(supabase_url, table, row, key):
    """POST a single row, returning it back with server-generated fields (e.g. the
    `id` of a freshly inserted agent_runs row) so the caller can PATCH it later."""
    req = urllib.request.Request(
        f"{supabase_url}/rest/v1/{table}",
        data=json.dumps(row).encode("utf-8"),
        method="POST",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    return result[0] if result else None


def supabase_patch(supabase_url, table, params, body, key):
    """PATCH rows matching `params` (dict of PostgREST filters, e.g. {"id": "eq.ec123"})."""
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{supabase_url}/rest/v1/{table}?{query}",
        data=json.dumps(body).encode("utf-8"),
        method="PATCH",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
