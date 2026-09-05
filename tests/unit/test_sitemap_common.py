"""D0/D1 — offline unit tests for sitemap_common (docs/plans/DEADLINE_AND_TASK_PLAN.md §4/§9 G-D1).

No network: every fetch is a fake mapping URL -> recorded fixture bytes. Proves the discovery
brain end to end (locate -> parse -> scope -> rank) plus the fallback triggers ([] on no
sitemap / empty sitemap) that keep this from ever regressing a working row.
"""
import gzip
import pathlib

import pytest

import sitemap_common as sm

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "sitemaps"


def _load(name):
    return (FIX / name).read_bytes()


class FakeFetch:
    """fetch(url) -> bytes from a {url: fixture-name} map; raises on anything unmapped (a real
    404). Records requested URLs so a test can assert the fallback probing order."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        if url not in self.mapping:
            raise OSError(f"404 {url}")
        return _load(self.mapping[url])


CONGRESS = "https://www.congressionalaward.org"
CONGRESS_MAP = {
    f"{CONGRESS}/robots.txt": "congressionalaward_robots.txt",
    f"{CONGRESS}/wp-sitemap.xml": "congressionalaward_wp-sitemap.xml",
    f"{CONGRESS}/wp-sitemap-posts-page-1.xml": "congressionalaward_pages.xml",
    f"{CONGRESS}/wp-sitemap-posts-post-1.xml": "congressionalaward_posts.xml",
}


# ---------------- locate ----------------

def test_locate_reads_sitemap_from_robots():
    f = FakeFetch(CONGRESS_MAP)
    urls = sm.locate_sitemaps(f"{CONGRESS}/", f)
    assert urls == [f"{CONGRESS}/wp-sitemap.xml"]


def test_locate_probes_common_paths_when_no_robots_sitemap():
    # No robots.txt (it 404s) -> fall through to probing the common sitemap paths.
    m = {f"{CONGRESS}/sitemap.xml": "tisch_sitemap.xml"}   # any valid sitemap body
    f = FakeFetch(m)
    urls = sm.locate_sitemaps(f"{CONGRESS}/programs/x", f)
    assert urls == [f"{CONGRESS}/sitemap.xml"]
    # robots.txt was tried first (raised -> ignored), then the common paths.
    assert f.calls[0] == f"{CONGRESS}/robots.txt"


def test_locate_returns_empty_when_no_sitemap_anywhere():
    f = FakeFetch({})   # every fetch 404s (the www.nyu.edu / 202-empty case)
    assert sm.locate_sitemaps("https://www.nyu.edu/x.html", f) == []


# ---------------- parse ----------------

def test_parse_detects_index_vs_urlset():
    kind, entries = sm.parse_sitemap(_load("congressionalaward_wp-sitemap.xml"))
    assert kind == "index"
    assert entries[0][0] == f"{CONGRESS}/wp-sitemap-posts-page-1.xml"
    assert entries[0][1] == "2026-07-01T00:00:00+00:00"

    kind2, entries2 = sm.parse_sitemap(_load("tisch_sitemap.xml"))
    assert kind2 == "urlset"
    assert any(loc.endswith("/application-requirements") for loc, _ in entries2)


def test_parse_handles_cdata_wrapped_loc():
    _, entries = sm.parse_sitemap(_load("tisch_sitemap.xml"))
    locs = [loc for loc, _ in entries]
    assert "https://tisch.nyu.edu/" in locs           # CDATA unwrapped, no stray brackets
    assert all("CDATA" not in loc for loc in locs)


def test_parse_empty_on_garbage():
    assert sm.parse_sitemap(b"<html>not a sitemap</html>") == ("urlset", [])
    assert sm.parse_sitemap(b"") == ("urlset", [])


# ---------------- collect (recursion + gzip) ----------------

def test_collect_recurses_one_level_into_index():
    f = FakeFetch(CONGRESS_MAP)
    entries = sm.collect_urls([f"{CONGRESS}/wp-sitemap.xml"], f)
    locs = [loc for loc, _ in entries]
    assert f"{CONGRESS}/the-program/" in locs          # from child page sitemap
    assert f"{CONGRESS}/blog/summer-service-ideas/" in locs   # from child post sitemap
    # deduped, both children merged
    assert len(locs) == len(set(locs))


def test_collect_gunzips_gz_sitemap():
    m = {"https://gzhost.org/sitemap.xml.gz": "gzhost_sitemap.xml.gz"}
    f = FakeFetch(m)
    entries = sm.collect_urls(["https://gzhost.org/sitemap.xml.gz"], f)
    locs = [loc for loc, _ in entries]
    assert "https://gzhost.org/how-to-apply/" in locs


def test_default_fetch_gunzip_roundtrip(monkeypatch):
    # default_fetch must gunzip a .gz body without touching the network.
    payload = gzip.compress(b"<urlset><url><loc>https://h/x</loc></url></urlset>")

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n): return payload

    monkeypatch.setattr(sm.urllib.request, "urlopen", lambda *a, **k: Resp())
    body = sm.default_fetch("https://h/sitemap.xml.gz")
    assert b"<loc>https://h/x</loc>" in body


# ---------------- rank ----------------

def test_rank_puts_program_and_apply_pages_on_top():
    opp = {"name": "Congressional Award", "org": "The Congressional Award Foundation",
           "url": f"{CONGRESS}/"}
    f = FakeFetch(CONGRESS_MAP)
    cands = sm.discover_candidate_pages(opp, fetch=f, top_n=5)
    top_urls = [c.url for c in cands]
    # The steps pages web_search never reached both surface in the fetched shortlist, and the
    # richest one (prospective-participants: two apply stems) is the single best-scored page.
    assert f"{CONGRESS}/the-program/" in top_urls
    assert f"{CONGRESS}/prospective-participants/" in top_urls
    assert cands[0].url == f"{CONGRESS}/prospective-participants/"
    # every useful page outranks every chrome page (leadership / donor / news / contact)
    program = next(c for c in cands if c.url.endswith("/the-program/"))
    chrome_scores = [sm.score_slug(u, sm.name_tokens(opp)) for u in (
        f"{CONGRESS}/our-leadership/", f"{CONGRESS}/donor-recognition/",
        f"{CONGRESS}/news/2026-gala/", f"{CONGRESS}/contact/")]
    assert program.score > max(chrome_scores)


def test_score_penalizes_chrome_and_rewards_apply():
    toks = sm.name_tokens({"name": "Congressional Award"})
    apply = sm.score_slug("https://x.org/the-program/how-to-apply/", toks)
    chrome = sm.score_slug("https://x.org/our-leadership/donors/", toks)
    assert apply > 0 > chrome


def test_name_tokens_excludes_host_derived_words():
    """The org name repeats across a single-program host's slugs, so a host-derived token
    discriminates nothing and would reward chrome (the ec18244 bug). It is dropped."""
    toks = sm.name_tokens({"name": "Congressional Award", "org": "Congressional Award Foundation",
                           "url": "https://www.congressionalaward.org/"})
    assert "congressional" not in toks       # in the host -> dropped
    assert "foundation" in toks              # not in the host -> kept


def test_exact_nav_slug_beats_program_partnership_and_news():
    """The ec18244 regression, at the scoring level: /the-program/ (canonical nav) must outrank
    /program-partners/ (chrome that also contains 'program') and a news-headline slug."""
    toks = sm.name_tokens({"name": "Congressional Award",
                           "url": "https://www.congressionalaward.org/"})
    program = sm.score_slug("https://c.org/the-program/", toks)
    partners = sm.score_slug("https://c.org/program-partners/", toks)
    news = sm.score_slug(
        "https://c.org/the-congressional-award-foundation-honors-170-texas-youth-for-service/",
        toks)
    gala = sm.score_slug("https://c.org/2025-golf-tournament-registration/", toks)
    assert program > partners
    assert program > news
    assert program > gala


# ---------------- scope ----------------

def test_scope_bare_homepage_keeps_all_regardless_of_size():
    """A bare-homepage stored URL means the whole host IS the program — keep everything however
    large, so the real content pages (which do NOT repeat the org name) are not filtered out.
    This is the ec18244 regression: filtering congressionalaward.org by 'congressional' dropped
    /the-program/ and kept the news/gala pages."""
    big = [(f"https://congressionalaward.org/news-item-{i}", None) for i in range(600)]
    content = [("https://congressionalaward.org/the-program/", None)]
    opp = {"name": "Congressional Award", "url": "https://www.congressionalaward.org/"}
    kept = sm.scope(opp, big + content)
    assert ("https://congressionalaward.org/the-program/", None) in kept
    assert len(kept) == 601                                             # nothing filtered


def test_scope_filters_deep_path_multiprogram_host_by_name_and_prefix():
    # A DEEP-path stored URL on a large host is scoped to the program's section.
    big = [(f"https://nyu.edu/other-{i}/page", None) for i in range(450)]
    ours = [("https://nyu.edu/tisch/future-artists/apply", None),
            ("https://nyu.edu/steinhardt/summer/tisch-future-artists", None)]
    opp = {"name": "Tisch Future Artists", "org": "NYU",
           "url": "https://nyu.edu/tisch/future-artists"}
    kept = sm.scope(opp, big + ours)
    kept_urls = {u for u, _ in kept}
    assert "https://nyu.edu/tisch/future-artists/apply" in kept_urls        # path prefix /tisch
    assert "https://nyu.edu/steinhardt/summer/tisch-future-artists" in kept_urls  # name token
    assert "https://nyu.edu/other-0/page" not in kept_urls                  # unrelated dropped


# ---------------- discover: end to end + fallbacks ----------------

def test_discover_returns_empty_when_no_sitemap():
    opp = {"name": "X", "url": "https://www.nyu.edu/x.html"}
    assert sm.discover_candidate_pages(opp, fetch=FakeFetch({})) == []


def test_discover_returns_empty_on_empty_sitemap():
    m = {"https://h.org/robots.txt": None}
    # robots present but names a sitemap that is empty of URLs -> [].
    class F:
        def __call__(self, url, timeout=None):
            if url.endswith("/robots.txt"):
                return b"Sitemap: https://h.org/sitemap.xml\n"
            if url.endswith("/sitemap.xml"):
                return b"<urlset></urlset>"
            raise OSError("404")
    assert sm.discover_candidate_pages({"name": "X", "url": "https://h.org/"}, fetch=F()) == []


def test_discover_never_raises_on_fetch_error():
    def boom(url, timeout=None):
        raise RuntimeError("network on fire")
    assert sm.discover_candidate_pages({"name": "X", "url": "https://h.org/"}, fetch=boom) == []


def test_discover_honours_robots_disallow():
    opp = {"name": "Congressional Award", "url": f"{CONGRESS}/"}
    m = dict(CONGRESS_MAP)
    # Point robots at a disallow that would hide /register/; the fixture disallows /wp-admin/
    # only, so /register/ survives. Assert the disallow filter runs without dropping good pages.
    f = FakeFetch(m)
    cands = sm.discover_candidate_pages(opp, fetch=f)
    assert all("/wp-admin/" not in c.url for c in cands)
