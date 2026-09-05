"""P3 / stage 1b: resolve a discovered name to its own page, with free gates and budget.

resolve_missing_url must (a) never pay when a free gate rules the name out, (b) do exactly one
per-name search otherwise, and (c) return the title-proven URL that best_resolved_url picks.
The search + fetch are stubbed, so nothing touches the network.
"""
from agents import scrape_opportunities as so
from agents import harvest_names


NAME = "Marine Biology Research Academy"
ORG = "Woods Hole"


class _Args:
    timeout = 30
    max_searches = 1
    no_resolve = False
    no_verify_urls = False
    resolve_per_angle = 12
    resolve_per_run = 150


def test_unresolvable_name_never_pays(monkeypatch):
    # A one-word name can't clear title_proves (needs >=2 identity words) — don't pay to learn.
    def _boom(*a, **k):
        raise AssertionError("must not search for an unprovable name")
    monkeypatch.setattr(so, "research_seed", _boom)
    url, cost, queries = so.resolve_missing_url("Debate", "", [], "2026-08-28", "k", _Args)
    assert url is None and cost == 0.0 and queries == []


def test_known_name_never_pays(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not search for a name we already have")
    monkeypatch.setattr(so, "research_seed", _boom)
    existing = [{"id": "ec1", "name": NAME, "url": "https://whoi.edu/x"}]
    url, cost, queries = so.resolve_missing_url(NAME, ORG, existing, "2026-08-28", "k", _Args)
    assert url is None and cost == 0.0


def test_resolves_via_one_search_and_title_proof(monkeypatch):
    calls = {"n": 0}

    def fake_research(angle, addendum, today, key, args, system=None):
        calls["n"] += 1
        assert system is so.RESOLVE_SYSTEM      # narrow resolver prompt, not discovery
        assert args.max_searches == 1           # a per-name search caps at one
        usage = {"server_tool_use": {"web_search_queries": ["official page Marine Biology Research Academy"]}}
        return "notes", usage, {"grounding": "g"}, 0.03, 1

    monkeypatch.setattr(so, "research_seed", fake_research)
    monkeypatch.setattr(so.url_validate, "resolve_grounding_chunks",
                        lambda g: [{"url": "https://whoi.edu/mbra"}])
    # best_resolved_url does the title proof; stub it to accept the candidate.
    monkeypatch.setattr(harvest_names, "best_resolved_url",
                        lambda urls, name, org="", timeout=None: "https://whoi.edu/mbra")

    url, cost, queries = so.resolve_missing_url(NAME, ORG, [], "2026-08-28", "k", _Args)
    assert url == "https://whoi.edu/mbra"
    assert cost == 0.03 and calls["n"] == 1
    assert queries == ["official page Marine Biology Research Academy"]


def test_search_that_proves_nothing_returns_none(monkeypatch):
    monkeypatch.setattr(so, "research_seed",
                        lambda *a, **k: ("n", {"server_tool_use": {"web_search_queries": ["q"]}}, {}, 0.03, 1))
    monkeypatch.setattr(so.url_validate, "resolve_grounding_chunks", lambda g: [])
    monkeypatch.setattr(harvest_names, "best_resolved_url",
                        lambda urls, name, org="", timeout=None: None)
    url, cost, queries = so.resolve_missing_url(NAME, ORG, [], "2026-08-28", "k", _Args)
    assert url is None and cost == 0.03      # the search was paid for even though it proved nothing


def test_telemetry_keeps_resolution_out_of_breadth():
    from wingman import query_telemetry as qt
    row = qt.summarize_seed({
        "angle": "marine biology programs",
        "searches": 4,
        "queries": ["high school marine biology programs", "list of ocean science programs for teens"],
        "resolution_queries": ['"Woods Hole" official page', '"Scripps" official page'],
        "names_attempted": 3, "names_resolved": 2, "names_dropped": 1,
    })
    # breadth is over discovery queries only; both discovery queries are broad -> 1.0
    assert row["breadth"] == 1.0
    assert row["resolution_searches"] == 2
    assert row["names_resolved"] == 2 and row["names_dropped"] == 1
