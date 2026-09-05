"""The service key is mandatory for any job that reads inactive (review-queue) rows.

Audit finding 4.13: seven scripts fell back to SUPABASE_ANON_KEY. Under the anon key RLS
returns only `is_active=true` rows, so those reads came back missing every queued and rejected
row — a dedupe set that looks complete, omits the whole review queue, and makes the job re-pay
for pages already sitting in it. The fallback never produced a working run (the insert fails on
RLS regardless), only a misleading one.
"""
import importlib
import inspect

import pytest

from wingman import supabase_common as sc


def test_returns_url_and_service_key(monkeypatch):
    monkeypatch.setattr(sc, "load_dotenv", lambda: None)
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co/")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    assert sc.require_service_key() == ("https://x.supabase.co", "svc")  # trailing / stripped


def test_anon_key_is_not_accepted_as_a_substitute(monkeypatch):
    """The whole point: an anon key present must NOT let the job proceed."""
    monkeypatch.setattr(sc, "load_dotenv", lambda: None)
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    with pytest.raises(SystemExit) as e:
        sc.require_service_key()
    assert "SUPABASE_SERVICE_KEY" in str(e.value)


def test_names_every_missing_variable(monkeypatch):
    monkeypatch.setattr(sc, "load_dotenv", lambda: None)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    with pytest.raises(SystemExit) as e:
        sc.require_service_key()
    assert "SUPABASE_URL" in str(e.value) and "SUPABASE_SERVICE_KEY" in str(e.value)


# The seven scripts finding 4.13 named. A regrown `or ...ANON_KEY` in any of them restores the
# silent-truncation bug, so assert on the source rather than trusting review to catch it.
_MUST_REQUIRE_SERVICE_KEY = [
    "agents.mine_hub_pages", "agents.harvest_names", "wingman.walk_up_hubs",
    "agents.classify_queue", "agents.dedupe_queue", "agents.refind_dead_links",
    "wingman.discovered_leads",
]


@pytest.mark.parametrize("modname", _MUST_REQUIRE_SERVICE_KEY)
def test_no_anon_key_fallback_remains(modname):
    src = inspect.getsource(importlib.import_module(modname))
    assert "SUPABASE_ANON_KEY" not in src, (
        f"{modname} reads inactive rows; an anon-key fallback silently truncates its read")
    assert "require_service_key" in src, f"{modname} must use require_service_key()"
