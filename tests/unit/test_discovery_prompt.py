"""P2 / MARQUEE M5+M8: the discovery prompt split.

`research_seed()` serves two very different jobs off one function:
  - default (system=None): the scraper's own DISCOVERY pass — broad, many programs.
  - system=RESOLVE_SYSTEM: the narrow name->page resolvers (refind, harvest, stage 1b),
    whose behaviour must stay byte-identical to before the split.

If DISCOVERY_SYSTEM ever leaked into the resolvers, they would be told "never search by name"
— the opposite of their job — so this routing is worth pinning.
"""
import scrape_opportunities as so


def _capture(monkeypatch):
    seen = {}

    def fake(system, user, key, **kw):
        seen["system"] = system
        seen["user"] = user
        return ("notes", {"server_tool_use": {"web_search_requests": 1, "web_search_queries": []}},
                {"grounding": {}})

    monkeypatch.setattr(so, "call_gemini", fake)
    return seen


class _Args:
    timeout = 10
    max_searches = 3


def test_default_uses_discovery_prompt(monkeypatch):
    seen = _capture(monkeypatch)
    so.research_seed("marine biology programs", "", "2026-08-28", "k", _Args)
    assert "Find as many DISTINCT" in seen["system"]
    assert "list every opportunity" in seen["user"]


def test_resolve_mode_keeps_the_old_prompt_and_user_turn(monkeypatch):
    seen = _capture(monkeypatch)
    so.research_seed('official page for "X"', "", "2026-08-28", "k", _Args,
                     system=so.RESOLVE_SYSTEM)
    assert "Find as many DISTINCT" not in seen["system"]          # discovery must not leak in
    assert "high-quality catalog of extracurricular" in seen["system"]
    assert "write up what you find" in seen["user"]               # unchanged narrow user turn


def test_discovery_prompt_defines_opportunity_and_shows_query_examples():
    # The definition + do/don't examples are the whole point of P2; a future edit that strips
    # them to "adjectives only" should trip this. (Supports the 'always use examples' convention.)
    p = so.DISCOVERY_SYSTEM
    assert "WHAT COUNTS AS AN OPPORTUNITY" in p
    assert "SOURCE to mine" in p                                  # listicle-is-feedstock rule
    assert "A GOOD (broad) search" in p and "A BAD search" in p
    # a concrete good example and a concrete bad example are both present
    assert "summer marine biology research programs" in p
    assert "MIT Research Science Institute application" in p
    # the tightened guideline: a lowercase org/program name is a named search too
    assert "in ANY capitalization" in p
    assert "columbia brainyac high school program" in p


def test_discovery_prompt_is_not_json():
    # M5: phase 1 stays prose-ish, never a JSON call. The format is markdown lines, not braces.
    assert '{"name"' not in so.DISCOVERY_SYSTEM
    assert "One opportunity per line" in so.DISCOVERY_SYSTEM
