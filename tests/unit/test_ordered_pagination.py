"""Every paged read is ordered — audit finding 4.14.

PostgREST offset pagination over an UNORDERED query is not stable in Postgres. Rows come back
in heap order, and a row updated mid-scan moves, so it can be returned twice or SKIPPED. About
39 of the 45 `opportunities` reads passed no `order`, including the scraper's own dedupe set —
which pages a table that same run is concurrently PATCHing via apply_merge. A skipped row there
is a missed duplicate, i.e. the scraper re-inserting a program it already has.

Ordering is applied inside supabase_get rather than at 48 call sites, so it cannot be forgotten
by the next one written.
"""
import json

import pytest

from wingman import supabase_common as sc


class _Resp:
    def __init__(self, rows):
        self._body = json.dumps(rows).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def urls(monkeypatch):
    """Capture every request URL supabase_get builds."""
    seen = []
    pages = {"rows": [[]]}

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url)
        return _Resp(pages["rows"].pop(0) if pages["rows"] else [])

    monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)
    return seen, pages


def test_order_is_added_when_the_caller_omits_it(urls):
    seen, _ = urls
    sc.supabase_get("https://db", "opportunities", {"select": "id,name"}, "k")
    assert "order=id" in seen[0]


def test_caller_supplied_order_is_never_overridden(urls):
    seen, _ = urls
    sc.supabase_get("https://db", "opportunities",
                    {"select": "id", "order": "updated_at.desc"}, "k")
    assert "order=updated_at.desc" in seen[0]
    assert seen[0].count("order=") == 1


def test_order_by_can_name_a_different_key(urls):
    """opportunity_signups is keyed on opportunity_id; promo_codes on code."""
    seen, _ = urls
    sc.supabase_get("https://db", "opportunity_signups", {"select": "*"}, "k",
                    order_by="opportunity_id")
    assert "order=opportunity_id" in seen[0]


def test_order_by_none_opts_out(urls):
    seen, _ = urls
    sc.supabase_get("https://db", "anything", {"select": "*"}, "k", order_by=None)
    assert "order=" not in seen[0]


def test_the_same_order_is_used_on_every_page(urls):
    """The point of the fix: pages must share one ordering, or offsets mean nothing."""
    seen, pages = urls
    pages["rows"] = [[{"id": i} for i in range(1000)], [{"id": 1000}]]
    rows = sc.supabase_get("https://db", "opportunities", {"select": "id"}, "k")
    assert len(rows) == 1001
    assert len(seen) == 2
    assert all("order=id" in u for u in seen)


def test_params_are_not_mutated_for_the_caller(urls):
    """A caller reusing its params dict must not silently inherit an order it never set."""
    params = {"select": "id"}
    sc.supabase_get("https://db", "opportunities", params, "k")
    assert params == {"select": "id"}


# The tables read through supabase_get whose primary key is NOT `id`. If one of these regrows a
# bare call, it 400s in production rather than failing here — hence the source assertion.
_NON_ID_KEYED = {
    "opportunity_signups": "opportunity_id",
    "users": "userid",
    "promo_codes": "code",
    # Added after agent_locks 400'd the first time it ran against the real table: this list is
    # only worth anything if a NEW non-id-keyed table gets added to it, and the one added in
    # this very phase was missed.
    "agent_locks": "name",
}
# trusted_aggregators is keyed on `domain` too, but aggregators_common passes its table name
# through the module constant TABLE rather than a literal, so the source scan below cannot see
# it. Covered by its own test instead of being weakened into the scan.


def _supabase_get_calls():
    """(path, call_text) for every supabase_get( in the repo.

    A line window, not a regex: these calls routinely contain nested parentheses inside their
    `select`, so `supabase_get\((.*?)\)` stops at the wrong bracket. The window is generous
    (15 lines) because the longest real call spans nine.
    """
    import os
    from wingman import REPO_ROOT
    out = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs
                   if d not in {"node_modules", ".git", "dist", "__pycache__", ".venv"}]
        for f in files:
            if not f.endswith(".py") or f.startswith("test_"):
                continue
            path = os.path.join(root, f)
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            for i, line in enumerate(lines):
                if "supabase_get(" in line and "def supabase_get" not in line:
                    out.append((os.path.relpath(path, REPO_ROOT),
                                "".join(lines[i:i + 15])))
    return out


@pytest.mark.parametrize("table,key", sorted(_NON_ID_KEYED.items()))
def test_non_id_keyed_reads_name_their_key(table, key):
    """A bare read of one of these 400s in production (order=id, no such column) — which no
    unit test would catch, because nothing here talks to a real PostgREST."""
    offenders = [path for path, call in _supabase_get_calls()
                 if f'"{table}"' in call and f'order_by="{key}"' not in call]
    assert not offenders, (
        f"these read {table} (keyed on {key}) without naming it, so the default order=id "
        f"would 400: {sorted(set(offenders))}")


# Reached through a module constant rather than a literal, so the source scan cannot see them.
# Each has its own dedicated test below instead of being weakened into the scan.
_VIA_CONSTANT = {"agent_locks"}


def test_every_non_id_table_is_actually_covered():
    """Guards the guard: a stale entry would let the test above pass on nothing."""
    calls = _supabase_get_calls()
    found = {t for t in _NON_ID_KEYED for _, call in calls if f'"{t}"' in call} | _VIA_CONSTANT
    assert found == set(_NON_ID_KEYED), (
        f"_NON_ID_KEYED lists tables nothing reads through supabase_get any more: "
        f"{set(_NON_ID_KEYED) - found}")


def test_the_scan_finds_the_calls_it_claims_to():
    """If the window scan silently matched nothing, both tests above would be vacuous."""
    calls = _supabase_get_calls()
    assert len(calls) > 40, f"expected the repo's ~60 supabase_get calls, found {len(calls)}"
    assert any('"opportunities"' in c for _, c in calls)


def test_agent_locks_read_names_its_key():
    """Keyed on `name`. Reached through the LOCK_TABLE constant, so the literal scan misses it —
    and that is exactly how it shipped broken: every unit test mocked supabase_get away, so
    nothing exercised the real query until the table existed."""
    import inspect
    from wingman import run_lock
    src = inspect.getsource(run_lock)
    assert 'LOCK_TABLE = "agent_locks"' in src
    assert 'order_by="name"' in src


def test_trusted_aggregators_read_names_its_key():
    """Keyed on `domain`. Reached through the TABLE constant, so the scan above misses it."""
    import inspect
    from wingman import aggregators_common
    src = inspect.getsource(aggregators_common)
    assert 'TABLE = "trusted_aggregators"' in src
    assert 'order_by="domain"' in src
