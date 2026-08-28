"""Phase 4B: deriving an institution's own index by walking UP from a program. Pure, hermetic.

Every fetch is injected, so nothing here touches the network or the catalog.
"""
import pytest

import discovered_leads as dl
import walk_up_hubs as wu


# ---------- where the parent is, and where there isn't one ----------

@pytest.mark.parametrize("url,expected", [
    ("https://ced.berkeley.edu/academics/summer-programs/summer-institute",
     "https://ced.berkeley.edu/academics/summer-programs/"),
    ("https://ced.berkeley.edu/academics/summer-programs/summer-institute/",
     "https://ced.berkeley.edu/academics/summer-programs/"),
    ("https://x.edu/programs/stem.php", "https://x.edu/programs/"),
    ("https://x.edu/a/b/c?utm_source=q#frag", "https://x.edu/a/b/"),
])
def test_parent_is_one_level_up(url, expected):
    assert wu.parent_url(url) == expected


@pytest.mark.parametrize("url", [
    "https://business.wisc.edu/",          # a bare domain has no parent
    "https://business.wisc.edu",
    "https://business.wisc.edu/precollege",  # one deep -> the parent IS the root homepage
    "", None, "javascript:void(0)", "ftp://x.edu/a/b",
])
def test_never_walks_up_to_a_root_homepage(url):
    """Measured on the first hub pilot: business.wisc.edu's ROOT gave 40 links and 2 gems, while
    its /precollege/ sub-hub was almost all programs. A homepage is a site, not an index, so a
    row one segment deep contributes no lead rather than a bad one."""
    assert wu.parent_url(url) is None


def test_rows_group_under_their_shared_parent():
    rows = [{"id": 1, "url": "https://x.edu/precollege/robotics"},
            {"id": 2, "url": "https://x.edu/precollege/writing"},
            {"id": 3, "url": "https://y.edu/summer/art"},
            {"id": 4, "url": "https://y.edu/shallow"}]
    groups = wu.group_by_parent(rows)
    assert sorted(groups) == ["https://x.edu/precollege/", "https://y.edu/summer/"]
    assert [r["id"] for r in groups["https://x.edu/precollege/"]] == [1, 2]


def test_a_mill_is_never_a_parent():
    rows = [{"id": 1, "url": "https://www.lumiere-education.com/post/summer-programs/x"}]
    assert wu.group_by_parent(rows) == {}


# ---------- the proof ----------

def _page(links):
    return "<html><body>" + "".join(
        f'<a href="{u}">{t}</a>' for u, t in links) + "</body></html>"


PARENT = "https://x.edu/precollege/"
CHILD = "https://x.edu/precollege/robotics"
SIBLINGS = [("https://x.edu/precollege/writing", "Creative Writing Institute"),
            ("https://x.edu/precollege/marine", "Marine Science Academy"),
            ("https://x.edu/precollege/design", "Design Lab")]


def test_an_index_that_links_the_program_qualifies():
    ok, signal, stats = wu.verify_index(PARENT, [CHILD],
                                        _page([(CHILD, "Robotics")] + SIBLINGS))
    assert ok is True
    assert stats["children_linked"] == 1 and stats["candidates"] == 3
    assert "3 more" in signal


def test_a_page_that_does_not_link_the_program_is_not_its_index():
    """THE rule. Free same-domain link counting calls 42% of all pages an index -- a costs page
    scored 401 links, a press release 318, a university FAQ 93 -- which is why detection was
    abandoned. None of them links the program pages, so none of them can pass this."""
    costs_page = _page([(f"https://x.edu/costs/item-{i}", f"Fee {i}") for i in range(60)]
                       + SIBLINGS)
    ok, signal, stats = wu.verify_index(PARENT, [CHILD], costs_page)
    assert ok is False
    assert "does not link the program" in signal
    assert stats["links"] >= 60          # link-rich, and still correctly refused


def test_a_program_subpage_is_not_a_list():
    """The parent links the child and almost nothing else -- that is the program's own section,
    not an index of siblings. Mining it would be a paid no-op."""
    ok, signal, _ = wu.verify_index(PARENT, [CHILD],
                                    _page([(CHILD, "Robotics"),
                                           ("https://x.edu/precollege/robotics/apply", "Apply")]))
    assert ok is False and "not a list" in signal


def test_a_parent_that_redirects_onto_the_child_is_refused():
    """A directory that is really just the program page. Following it re-mines what we have."""
    ok, signal, _ = wu.verify_index(PARENT, [CHILD], _page([(CHILD, "Robotics")] + SIBLINGS),
                                    final_url=CHILD + "/")
    assert ok is False and "redirects to the program itself" in signal


def test_an_unreadable_parent_gets_no_verdict_not_a_no():
    ok, signal, _ = wu.verify_index(PARENT, [CHILD], "")
    assert ok is False and "could not read" in signal


def test_programs_we_already_have_are_counted_but_do_not_block():
    """A parent whose siblings are all already ours still qualifies -- hub mining dedupes -- but
    the signal has to say so, because that is the difference between a rich lead and a dud."""
    known = {dl._key(u) for u, _ in SIBLINGS}
    ok, signal, stats = wu.verify_index(PARENT, [CHILD],
                                        _page([(CHILD, "Robotics")] + SIBLINGS),
                                        known_keys=known)
    assert ok is True and stats["new"] == 0
    assert "0 not in the catalog" in signal


# ---------- the sweep ----------

def _fake_fetch(pages):
    return lambda parent, timeout=None: (pages.get(parent), None)


def test_walk_up_writes_a_same_domain_lead():
    rows = [{"id": 7, "name": "Robotics Institute", "url": CHILD}]
    leads, trace = wu.walk_up(rows, fetch=_fake_fetch(
        {PARENT: _page([(CHILD, "Robotics")] + SIBLINGS)}))
    assert len(leads) == 1 and trace["leads"] == 1
    lead = leads[0]
    assert lead["url"] == PARENT
    assert lead["kind"] == dl.KIND_HUB
    # The direction is the whole point: a round-up is mined off-domain, an institution's own
    # index same-domain. Getting this wrong follows the links that did not qualify the page.
    assert lead["scope"] == dl.SCOPE_SAME_DOMAIN
    assert dl.lead_scope(lead) == dl.SCOPE_SAME_DOMAIN
    assert "walk-up from row 7" in lead["angle"]


def test_parents_we_already_know_are_never_re_fetched():
    rows = [{"id": 1, "url": CHILD}]
    fetched = []

    def fetch(parent, timeout=None):
        fetched.append(parent)
        return None, None

    leads, trace = wu.walk_up(rows, known_keys={dl._key(PARENT)}, fetch=fetch)
    assert leads == [] and fetched == []
    assert trace["already_known"] == 1 and trace["looked_at"] == 0


def test_a_limit_spends_its_fetches_on_the_densest_parents():
    """Ranking happens BEFORE the fetch and costs nothing: if five approved rows sit under one
    path, that path is an index and no fetch is needed to suspect it."""
    rows = ([{"id": i, "url": f"https://x.edu/precollege/p{i}"} for i in range(4)]
            + [{"id": 9, "url": "https://y.edu/lonely/one"}])
    fetched = []

    def fetch(parent, timeout=None):
        fetched.append(parent)
        return None, None

    wu.walk_up(rows, limit=1, fetch=fetch)
    assert fetched == ["https://x.edu/precollege/"]


def test_the_trace_separates_unreadable_from_judged():
    """A page we could not fetch is a fact about our HTTP client, never about the institution --
    it must not be reported as 'not an index'."""
    rows = [{"id": 1, "url": CHILD}, {"id": 2, "url": "https://y.edu/summer/art"}]
    pages = {PARENT: None, "https://y.edu/summer/": _page([("https://y.edu/other/x", "X")])}
    _leads, trace = wu.walk_up(rows, fetch=_fake_fetch(pages))
    assert trace["unreadable"] == 1 and trace["not_an_index"] == 1 and trace["leads"] == 0


def test_a_censored_link_count_is_reported_as_censored():
    """UCLA Anderson's student-experience page hit the scan cap on the first live run and the
    signal read '200 more', which is a bounded count printed as a total. Nothing is rejected for
    being link-heavy -- the back-link already proved the page -- but the operator is paying per
    page to mine it, so the number must not overstate what was actually counted."""
    many = [(f"https://x.edu/precollege/p{i}", f"Program {i}")
            for i in range(wu.LINK_SCAN_CAP + 50)]
    ok, signal, stats = wu.verify_index(PARENT, [CHILD], _page([(CHILD, "Robotics")] + many))
    assert ok is True and stats["capped"] is True
    assert f"{stats['candidates']}+ more" in signal
    assert "likely a site section" in signal


def test_leads_come_back_densest_first():
    """Ranked on what the fetch PROVED -- how many of our rows the page really links -- not on
    how many happened to sit under its path. That is what decides which to pay to mine first."""
    thin_parent, thin_child = "https://y.edu/summer/", "https://y.edu/summer/one"
    rows = [{"id": 1, "url": CHILD}, {"id": 2, "url": "https://x.edu/precollege/writing"},
            {"id": 3, "url": thin_child}]
    pages = {PARENT: _page([(CHILD, "Robotics"),
                            ("https://x.edu/precollege/writing", "Writing"),
                            ("https://x.edu/precollege/music", "Music Institute")] + SIBLINGS),
             thin_parent: _page([(thin_child, "One")]
                                + [(f"https://y.edu/summer/s{i}", f"S{i}") for i in range(3)])}
    leads, _trace = wu.walk_up(rows, fetch=_fake_fetch(pages))
    assert [l["url"] for l in leads] == [PARENT, thin_parent]


@pytest.mark.parametrize("url", [
    "https://x.edu/blog/why-our-summer-program-rocks",
    "https://x.edu/news/2026/summer-program-announced",
    "https://www.facebook.com/someorg/posts/1234",
])
def test_a_parent_that_can_never_be_a_source_is_skipped(url):
    """A row sitting at /blog/<post> has a blog index for a parent, and a row on a social host a
    profile page. Neither is a program list, and the router already owns that definition."""
    assert wu.group_by_parent([{"id": 1, "url": url}]) == {}


# ---------- the free-ness claim, pinned ----------

def test_walking_up_can_never_make_a_paid_call(monkeypatch):
    """`walk_up_hubs` documents itself as FREE at every tier. The paid libraries ARE reachable in
    its import graph -- walk_up_hubs -> discovered_leads -> mine_hub_pages -> gemini_common, among
    others -- so "it does not import a model client" is not the claim and could not be. The claim
    is that no model function is ever CALLED, and this fails loudly if that stops being true."""
    import gemini_common, claude_common

    def boom(*a, **k):
        raise AssertionError("walk_up_hubs made a paid model call")

    monkeypatch.setattr(gemini_common, "call_gemini", boom)
    monkeypatch.setattr(claude_common, "call_claude", boom)

    rows = [{"id": 1, "name": "Robotics Institute", "url": CHILD},
            {"id": 2, "url": "https://y.edu/summer/art"}]
    pages = {PARENT: _page([(CHILD, "Robotics")] + SIBLINGS), "https://y.edu/summer/": None}
    leads, trace = wu.walk_up(rows, fetch=_fake_fetch(pages))
    assert len(leads) == 1 and trace["unreadable"] == 1
