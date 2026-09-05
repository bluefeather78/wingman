"""P5 trusted-domain allowlist: aggregators_common (read side) + the action-items serve-path
tier filter. Hermetic — the one Supabase read is monkeypatched."""
import urllib.error

import pytest

from wingman import aggregators_common as ag
from app.services import action_items


# --------------------------------------------------------------- normalize_domain

@pytest.mark.parametrize("raw,expected", [
    ("https://www.Lumiere-Education.com/guides?x=1", "lumiere-education.com"),
    ("http://blog.lumiere-education.com:8080/x", "blog.lumiere-education.com"),
    ("Lumiere-Education.com", "lumiere-education.com"),
    ("https://user:pass@example.com/path", "example.com"),
    ("//example.com", "example.com"),
    ("", ""),
    ("   ", ""),
    ("mailto:hi@example.com", ""),   # no host -> unusable -> pending, never trusted
    ("/relative/path", ""),
])
def test_normalize_domain(raw, expected):
    assert ag.normalize_domain(raw) == expected


# --------------------------------------------------------------- classify / domain_matches

def _policy():
    return ag.AggregatorPolicy({"lumiere-education.com": "trusted", "bad.com": "blocked"})


def test_classify_trusted_exact_and_subdomain():
    p = _policy()
    assert p.classify("https://lumiere-education.com/x") == "trusted"
    assert p.classify("https://blog.lumiere-education.com/x") == "trusted"
    assert p.is_trusted("lumiere-education.com") is True


def test_classify_blocked_wins_over_trusted():
    # A domain both trusted and blocked resolves to blocked (safe direction).
    p = ag.AggregatorPolicy({"x.com": "trusted"})
    p2 = ag.AggregatorPolicy({"x.com": "blocked"})
    assert p.classify("x.com") == "trusted"
    assert p2.classify("x.com") == "blocked"


def test_classify_unknown_is_pending():
    assert _policy().classify("https://random.org/x") == "pending"
    assert _policy().classify("") == "pending"          # unusable -> pending


def test_subdomain_does_not_trust_parent():
    p = ag.AggregatorPolicy({"sub.example.com": "trusted"})
    assert p.classify("example.com") == "pending"       # parent not trusted by a child


def test_domain_matches_suffix_only():
    assert ag.domain_matches("x.lumiere-education.com", ["lumiere-education.com"]) is True
    assert ag.domain_matches("lumiere-education.com", ["lumiere-education.com"]) is True
    assert ag.domain_matches("notlumiere-education.com", ["lumiere-education.com"]) is False
    assert ag.domain_matches("", ["lumiere-education.com"]) is False


def test_trusted_domains_sorted_list():
    p = ag.AggregatorPolicy({"b.com": "trusted", "a.com": "trusted", "z.com": "blocked"})
    assert p.trusted_domains() == ["a.com", "b.com"]


# --------------------------------------------------------------- load_aggregator_policy

def test_load_policy_missing_creds_is_pending_everything():
    p = ag.load_aggregator_policy("", "")
    assert p.present is False
    assert p.classify("anything.com") == "pending"


def test_load_policy_maps_rows(monkeypatch):
    monkeypatch.setattr(ag, "supabase_get", lambda url, table, params, key: [
        {"domain": "lumiere-education.com", "status": "trusted"},
        {"domain": "BAD.com", "status": "blocked"},
        {"domain": "ignored.com", "status": "weird-status"},  # invalid status -> dropped
        {"domain": "", "status": "trusted"},                  # no domain -> dropped
    ])
    p = ag.load_aggregator_policy("https://x.supabase.co", "key")
    assert p.present is True
    assert p.classify("lumiere-education.com") == "trusted"
    assert p.classify("bad.com") == "blocked"                 # normalized on read
    assert p.classify("ignored.com") == "pending"


def test_load_policy_missing_table_is_present_false(monkeypatch):
    def raise_404(*a, **k):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    monkeypatch.setattr(ag, "supabase_get", raise_404)
    p = ag.load_aggregator_policy("https://x.supabase.co", "key")
    assert p.present is False and p.error is None            # -> console shows setup step
    assert p.classify("anything.com") == "pending"


# --------------------------------------------------------------- cache

def test_get_policy_caches(monkeypatch):
    ag.invalidate_policy_cache()
    calls = {"n": 0}

    def counting(url, table, params, key):
        calls["n"] += 1
        return [{"domain": "a.com", "status": "trusted"}]

    monkeypatch.setattr(ag, "supabase_get", counting)
    ag.get_policy("https://x.supabase.co", "key")
    ag.get_policy("https://x.supabase.co", "key")
    assert calls["n"] == 1                                    # second call served from cache
    ag.invalidate_policy_cache()
    ag.get_policy("https://x.supabase.co", "key")
    assert calls["n"] == 2                                    # invalidation forces a reload


# --------------------------------------------------------------- serve-path tier filter (action_items)

def test_servable_drops_pending_and_blocked():
    items = [
        {"text": "official", "source_tier": "official"},
        {"text": "trusted", "source_tier": "trusted"},
        {"text": "pending", "source_tier": "pending"},
        {"text": "blocked", "source_tier": "blocked"},
        {"text": "legacy-no-tier"},                           # kept: pre-P6 official/generic
    ]
    kept = [i["text"] for i in action_items._servable(items)]
    assert kept == ["official", "trusted", "legacy-no-tier"]


def test_payload_filters_untrusted():
    p = action_items.payload(
        [{"text": "ok"}, {"text": "hide", "source_tier": "pending"}], "page-verified")
    assert p == {"action_items": [{"text": "ok"}], "source": "page-verified"}
