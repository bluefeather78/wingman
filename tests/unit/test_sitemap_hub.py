"""Offline tests for sitemap_hub — the pure pipeline (enumerate+scope+containment) with an
injected fetch and a fake classifier. No network, no API key. The paid Gemini classifier is
NOT exercised here (it is injected in production); these pin the free brain around it."""
from wingman import sitemap_hub as sh


HUB = "https://x.edu/programs/"

_SITEMAP = b"""<?xml version="1.0"?><urlset>
  <url><loc>https://staging.x.edu/programs/alpha</loc></url>
  <url><loc>https://staging.x.edu/programs/alpha/details</loc></url>
  <url><loc>https://staging.x.edu/about</loc></url>
</urlset>"""

_HUB_HTML = (b'<a href="/programs/beta">Beta</a>'
             b'<a href="/programs/alpha/%20">Alpha (encoded trailing space)</a>'
             b'<a href="https://other.com/x">off-domain</a>')


def _fake_fetch(url):
    if url == "https://x.edu/sitemap.xml":
        return _SITEMAP
    if url == HUB:
        return _HUB_HTML
    raise OSError("404")            # robots.txt, other sitemap probes -> not found


# ---------- unit pieces ----------

def test_normalize_collapses_encoded_trailing_space_and_slash():
    assert sh._normalize("/programs/alpha/%20", "https://x.edu") == "https://x.edu/programs/alpha"
    assert sh._normalize("/programs/alpha/", "https://x.edu") == "https://x.edu/programs/alpha"
    assert sh._normalize("/programs/alpha#section", "https://x.edu") == "https://x.edu/programs/alpha"


def test_enumerate_unions_sitemap_and_anchor_and_dedups():
    src, urls = sh.enumerate_urls(HUB, fetch=_fake_fetch)
    assert "sitemap" in src and "anchor" in src
    # staging host rewritten to the hub's public host; alpha appears in BOTH sources -> one entry
    assert "https://x.edu/programs/alpha" in urls
    assert "https://x.edu/programs/beta" in urls           # anchor-only page
    assert "https://x.edu/about" in urls                   # sitemap-only page
    assert not any("staging.x.edu" in u for u in urls)     # host-rewritten
    assert not any("other.com" in u for u in urls)         # off-domain anchor dropped
    assert urls.count("https://x.edu/programs/alpha") == 1  # deduped across the two sources


def test_scope_keeps_only_the_hub_section():
    _src, urls = sh.enumerate_urls(HUB, fetch=_fake_fetch)
    scoped = sh.scope_to_hub(urls, HUB)
    assert "https://x.edu/about" not in scoped             # outside /programs
    assert "https://x.edu/programs/alpha" in scoped
    assert "https://x.edu/programs/beta" in scoped


def test_drop_contained_removes_a_direct_child():
    kept = sh._drop_contained([
        "https://x.edu/programs/alpha",
        "https://x.edu/programs/alpha/details",
        "https://x.edu/programs/beta",
    ])
    assert "https://x.edu/programs/alpha/details" not in kept
    assert set(kept) == {"https://x.edu/programs/alpha", "https://x.edu/programs/beta"}


# ---------- end to end ----------

def test_preview_is_free_and_returns_the_scoped_set():
    urls, trace = sh.program_candidates(HUB, classify=None, fetch=_fake_fetch)
    assert trace["cost"] == 0.0 and trace["classified"] == 0
    assert set(urls) == {"https://x.edu/programs/alpha",
                         "https://x.edu/programs/alpha/details",
                         "https://x.edu/programs/beta"}


def test_classified_run_applies_containment():
    # a classifier that keeps everything it is given, at a fixed cost
    def echo(urls):
        return list(urls), 0.01
    urls, trace = sh.program_candidates(HUB, classify=echo, fetch=_fake_fetch)
    assert trace["cost"] == 0.01
    assert set(urls) == {"https://x.edu/programs/alpha", "https://x.edu/programs/beta"}
    assert "https://x.edu/programs/alpha/details" not in urls   # child dropped post-classify


def test_classifier_failure_degrades_to_empty_for_fallback():
    def boom(_urls):
        raise RuntimeError("model down")
    urls, trace = sh.program_candidates(HUB, classify=boom, fetch=_fake_fetch)
    assert urls == [] and "classify" in trace["error"]


def test_unfetchable_hub_degrades_cleanly():
    def dead(_url):
        raise OSError("no route")
    urls, trace = sh.program_candidates(HUB, classify=None, fetch=dead)
    assert urls == [] and trace["enumerated"] == 0
