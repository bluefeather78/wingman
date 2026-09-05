"""Phase-4 hub mining: the FREE harvest + two-stage audience filter. Pure, hermetic."""
import pytest

from agents import mine_hub_pages as hub


@pytest.mark.parametrize("text", [
    "Elementary School Science Camp", "Middle School Robotics", "Graduate Certificate",
    "PhD Fellowship", "MBA Summer Institute", "Faculty Resources", "For Parents",
    "Admitted Students", "Undergraduate Research", "Professional Development",
    "Programs for Grades K-8",
])
def test_is_wrong_audience_true(text):
    assert hub.is_wrong_audience(text) is True


@pytest.mark.parametrize("text", [
    "Summer Research Program", "Clark Scholars Program", "Robotics Competition",
    "High School Science Institute",
])
def test_is_wrong_audience_false(text):
    assert hub.is_wrong_audience(text) is False


@pytest.mark.parametrize("text", [
    "Open to high school students", "for grades 9-12", "rising juniors and seniors",
    "Pre-College Program", "a program for teens", "11th and 12th graders",
])
def test_has_hs_audience_true(text):
    assert hub.has_hs_audience(text) is True


def test_has_hs_audience_false():
    assert hub.has_hs_audience("A program for motivated learners") is False


@pytest.mark.parametrize("anchor", [
    "Pre-College Programs", "Summer Programs", "Explore Programs", "Program Directory",
    "Our Programs", "Youth Programs",
])
def test_looks_like_sub_hub_true(anchor):
    assert hub.looks_like_sub_hub(anchor) is True


def test_looks_like_sub_hub_false():
    assert hub.looks_like_sub_hub("Clark Scholars Program") is False


# ---- harvest_links + filter_hub_links -------------------------------------------------

HUB = "https://ceismc.gatech.edu/programs"
PAGE = (
    '<a href="/programs/summer-peaks">Summer PEAKS (High School)</a>'
    '<a href="/programs/k5-camp">Elementary K-5 Camp</a>'          # wrong audience
    '<a href="/programs/grad-cert">Graduate Certificate</a>'       # wrong audience
    '<a href="https://sponsor.com/x">Our Sponsor</a>'              # off-domain (institutional)
    '<a href="/pre-college">Pre-College Programs</a>'              # sub-hub
    '<a href="/">Home</a>'                                          # bare domain
)


def test_harvest_links_absolutizes_and_dedupes():
    links = hub.harvest_links(PAGE, HUB)
    urls = [u for u, _ in links]
    assert "https://ceismc.gatech.edu/programs/summer-peaks" in urls
    assert all(u.startswith("http") for u in urls)
    assert len(urls) == len(set(urls))


def test_filter_institutional_keeps_same_domain_hs_and_splits_sub_hubs():
    kept, subs = hub.filter_hub_links(hub.harvest_links(PAGE, HUB), HUB, off_domain=False)
    kept_urls = [u for u, _ in kept]
    # kept: the HS program on-domain; dropped: elementary, graduate, off-domain sponsor, homepage
    assert "https://ceismc.gatech.edu/programs/summer-peaks" in kept_urls
    assert "https://sponsor.com/x" not in kept_urls          # off-domain excluded for institutional
    assert not any("k5-camp" in u for u in kept_urls)         # wrong audience
    assert not any("grad-cert" in u for u in kept_urls)       # wrong audience
    # the pre-college index is routed to sub-hubs for one-level recursion
    assert any("pre-college" in u for u, _ in subs)


def test_filter_listicle_keeps_off_domain_only():
    listicle = ('<a href="https://ladderinternships.com/apply">Apply</a>'   # mill, dropped
                '<a href="https://realprogram.edu/summer">Summer Program (High School)</a>'
                '<a href="/internal-nav">Nav</a>')
    kept, _ = hub.filter_hub_links(hub.harvest_links(listicle, "https://blog.com/list"),
                                   "https://blog.com/list", off_domain=True)
    kept_urls = [u for u, _ in kept]
    assert "https://realprogram.edu/summer" in kept_urls
    assert not any("ladderinternships" in u for u in kept_urls)   # content mill excluded
    assert not any("blog.com" in u for u in kept_urls)            # on-domain excluded for listicle


def test_filter_respects_cap():
    many = "".join(f'<a href="https://x.edu/p{i}">Program {i} High School</a>' for i in range(60))
    kept, _ = hub.filter_hub_links(hub.harvest_links(many, "https://x.edu/index"),
                                   "https://x.edu/index", off_domain=False, cap=25)
    assert len(kept) <= 25


# ---- is_nonprogram_link (nav / non-HTML / adult / editorial drop) ----------------------

_HUBU = "https://precollege.wisc.edu/"


@pytest.mark.parametrize("url", [
    "https://precollege.wisc.edu/",                                   # the hub itself
    "https://www.usna.edu/Admissions/_files/USNA_Viewbook.pdf",       # PDF
    "https://precollege.wisc.edu/wp-content/uploads/x/photo.webp",    # image
    "https://www.usna.edu/Admissions/Apply/FAQ.php",                  # nav slug (faq)
    "https://www.usna.edu/Admissions/",                              # nav slug (admissions)
    "https://precollege.wisc.edu/donate/",                           # nav slug (donate)
    "https://online.wisc.edu/degrees/marketing/",                    # adult degree path
    "https://precollege.wisc.edu/blog/alinas-precollege-experience/", # editorial post
    "https://business.wisc.edu/news/some-headline/",                 # editorial post
    # social / share / commerce / list-signup hosts (off-domain listicle leaks, 2026-08-27)
    "https://www.facebook.com/CollegeTransitions",
    "https://twitter.com/eduTransitions",
    "https://www.tiktok.com/@nyu_k12stem",
    "https://www.linkedin.com/company/nyu-k12-stem/",
    "https://nyu.us10.list-manage.com/subscribe?u=77d1&id=f766",
    "https://www.amazon.com/Colleges-Worth-Your-Money/dp/B0F4RP8NB9",
    "https://www.facebook.com/dialog/send?app_id=140&link=https://blog.collegevine.com/x",
    # wrong audience named only in the URL (anchor omitted it)
    "https://spcs.stanford.edu/programs/stanford-middle-school-scholars-program",
    "https://example.edu/programs/elementary-science-camp",
    "https://example.edu/graduate-certificate-in-data",
    # local/civic chaff (2026-08-27 Seattle preview): branch locations, service categories,
    # and unrendered template placeholders
    "https://www.spl.org/hours-and-locations/ballard-branch",
    "https://kcls.org/ebooks/",
    "https://kcls.org/teens/{{url}}",
    "https://www.spl.org/donate",
    # inquiry / application-material forms (2026-08-28 Columbia re-preview)
    "https://apply.sps.columbia.edu/register/pre-college-rfi",
    "https://precollege.sps.columbia.edu/admissions/applying-pre-college-programs/application-materials",
    "https://x.edu/programs/summer-info-session",
    "https://x.edu/how-to-apply",
])
def test_is_nonprogram_link_true(url):
    assert hub.is_nonprogram_link(url, _HUBU) is True


def test_real_local_programs_survive_civic_filter():
    # The YMCA/4-H-shaped programs that DID yield on the Seattle preview must not be dropped.
    for url in ["https://www.seattleymca.org/programs/camp-and-outdoor-leadership/bold-gold",
                "https://extension.wsu.edu/king/4-h/king-county-4-h-clubs"]:
        assert hub.is_nonprogram_link(url, "https://www.seattleymca.org/programs") is False


@pytest.mark.parametrize("url", [
    "https://www.usna.edu/Admissions/Programs/STEM.php",             # USNA Summer STEM
    "https://www.usna.edu/Admissions/Programs/NASS.php",             # USNA Summer Seminar
    "https://business.wisc.edu/precollege/business-emerging-leaders/", # Wisconsin BEL
    "https://precollege.wisc.edu/badger-summer-scholars/",
    "https://pharmacy.wisc.edu/pharmd/high-school-summer-program/",
])
def test_is_nonprogram_link_false_for_real_programs(url):
    assert hub.is_nonprogram_link(url, _HUBU) is False


def test_filter_drops_nav_keeps_program_and_subhub():
    page = (
        '<a href="/Admissions/Programs/STEM.php">Summer STEM Program (High School)</a>'
        '<a href="/Admissions/Apply/FAQ.php">FAQ</a>'                # nav
        '<a href="/Admissions/_files/Viewbook.pdf">Viewbook</a>'    # PDF
        '<a href="/blog/a-post">A Student Story</a>'                 # editorial
        '<a href="/pre-college">Pre-College Programs</a>'           # sub-hub (kept for recursion)
    )
    hub_url = "https://www.usna.edu/Admissions/Programs/index.php"
    kept, subs = hub.filter_hub_links(hub.harvest_links(page, hub_url), hub_url, off_domain=False)
    kept_urls = [u for u, _ in kept]
    assert any("STEM.php" in u for u in kept_urls)
    assert not any("FAQ" in u for u in kept_urls)
    assert not any(".pdf" in u.lower() for u in kept_urls)
    assert not any("/blog/" in u for u in kept_urls)
    assert any("pre-college" in u for u, _ in subs)          # sub-hub survives for recursion


def test_each_lead_is_mined_the_way_it_qualified():
    """A router lead is qualified by the distinct OTHER sites it links (>= 6), so its programs
    are on those sites and it must be mined OFF-domain — mining it same-domain follows exactly
    the links the router did not count, i.e. the page's own navigation. All 25 queued leads were
    affected by that. A walk-up lead is the opposite: it is proven by linking a program on its
    OWN site. So the direction is carried on the lead, not decided by the miner."""
    from wingman import discovered_leads
    leads = [{"url": "https://listicle.example/15-programs"},                       # router lead
             {"url": "https://ok.example/x", "scope": discovered_leads.SCOPE_OFF_DOMAIN},
             {"url": "https://x.edu/precollege/", "scope": discovered_leads.SCOPE_SAME_DOMAIN}]
    assert hub.hubs_from_leads(leads) == [
        ("https://listicle.example/15-programs", True),
        ("https://ok.example/x", True),
        ("https://x.edu/precollege/", False)]


def test_a_lead_written_before_scope_existed_is_still_mined_off_domain():
    """Every lead already on file came from the router, which qualifies off-domain. A missing
    field must not silently flip 25 queued leads onto the wrong side."""
    assert hub.hubs_from_leads([{"url": "https://a.example/list"}])[0][1] is True


# ---------- the address we asked for vs the page we got ----------

def _hub_html(links):
    return "<html><body>" + "".join(
        f'<a href="{u}">{t}</a>' for u, t in links) + "</body></html>"


def test_candidates_landing_on_the_same_page_are_extracted_once(monkeypatch):
    """CMU's pre-college index links nine of its programs twice, at /pre-college/... and at
    /student-affairs/pre-college/..., and every one of the second set redirects back. Deduping on
    the REQUESTED url left 24 candidates where 15 pages exist, and each extra one is a paid call
    that inserts a row saying what another row already says."""
    HUB = "https://x.edu/pre-college/"
    monkeypatch.setattr(hub, "fetch_html", lambda u, t=None: _hub_html([
        ("https://x.edu/pre-college/art.html", "Art for high school students"),
        ("https://x.edu/student-affairs/pre-college/art.html", "Art for high school students"),
        ("https://x.edu/pre-college/drama.html", "Drama for high school students")]))
    landings = {"https://x.edu/student-affairs/pre-college/art.html":
                "https://x.edu/pre-college/art.html"}
    from wingman import page_text
    monkeypatch.setattr(page_text, "fetch_page_text_resolved",
                        lambda u, t=None: ("high school students " * 30, "ok",
                                           landings.get(u, u)))
    found, trace = hub.discover(HUB, recurse=False)
    assert found == ["https://x.edu/pre-college/art.html",
                     "https://x.edu/pre-college/drama.html"]
    assert trace["same_page_twice"] == 1


def test_a_candidate_that_redirects_onto_the_hub_is_dropped(monkeypatch):
    """There is no program at the other end of that link -- it lands on the index we are already
    standing on, so extracting it would pay to describe the hub itself."""
    HUB = "https://x.edu/pre-college/"
    monkeypatch.setattr(hub, "fetch_html", lambda u, t=None: _hub_html([
        ("https://x.edu/student-affairs/pre-college/gone.html", "Gone program for high school")]))
    from wingman import page_text
    monkeypatch.setattr(page_text, "fetch_page_text_resolved",
                        lambda u, t=None: ("high school students " * 30, "ok", HUB))
    found, trace = hub.discover(HUB, recurse=False)
    assert found == [] and trace["redirects_to_hub"] == 1


def test_a_candidate_landing_above_the_hub_is_dropped(monkeypatch):
    """A bare `== hub` check misses the real shape. On CMU the dead links redirect to
    /pre-college/, the PARENT of the /pre-college/academic-programs/ index being mined -- so the
    row would have described a section, not a program. A program page is never an ancestor of
    the index listing it, so this cannot drop a real find."""
    HUB = "https://x.edu/pre-college/academic-programs/"
    monkeypatch.setattr(hub, "fetch_html", lambda u, t=None: _hub_html([
        ("https://x.edu/student-affairs/pre-college/ai.html", "AI for high school students"),
        ("https://x.edu/pre-college/academic-programs/art.html", "Art for high school")]))
    landing = {"https://x.edu/student-affairs/pre-college/ai.html": "https://x.edu/pre-college/"}
    from wingman import page_text
    monkeypatch.setattr(page_text, "fetch_page_text_resolved",
                        lambda u, t=None: ("high school students " * 30, "ok",
                                           landing.get(u, u)))
    found, trace = hub.discover(HUB, recurse=False)
    assert found == ["https://x.edu/pre-college/academic-programs/art.html"]
    assert trace["redirects_to_hub"] == 1


def test_a_sibling_section_is_not_treated_as_an_ancestor(monkeypatch):
    """The guard must key on the hub's own path, not merely on being shallower -- a genuine
    redirect to a shorter canonical URL elsewhere on the site is a real program page."""
    HUB = "https://x.edu/pre-college/academic-programs/"
    monkeypatch.setattr(hub, "fetch_html", lambda u, t=None: _hub_html([
        ("https://x.edu/pre-college/academic-programs/art-camp.html", "Art for high school")]))
    from wingman import page_text
    monkeypatch.setattr(page_text, "fetch_page_text_resolved",
                        lambda u, t=None: ("high school students " * 30, "ok",
                                           "https://x.edu/artcamp"))
    found, trace = hub.discover(HUB, recurse=False)
    assert found == ["https://x.edu/pre-college/academic-programs/art-camp.html"]
    assert trace.get("redirects_to_hub", 0) == 0


def test_the_link_cap_never_truncates_by_position_in_the_page():
    """seattle.gov/parks/childcare/teen-programs/ carries 974 links. The filter used to `break`
    at 25 SURVIVORS, and a page's first links are its chrome -- so the cap filled with navigation
    and every real teen program, further down the document, was never judged at all. The hub
    reported zero programs and that was an artifact of the cap, not a fact about the site.
    Judging a link is pure and free; only stage 2 fetches and only extraction pays."""
    chrome = [(f"https://city.gov/about/thing-{i}", f"Thing {i} for high school") for i in range(40)]
    real = [("https://city.gov/parks/teen-programs/career-explorations", "Career Explorations")]
    page = _hub_html(chrome + real)
    HUB = "https://city.gov/parks/teen-programs/"
    kept, _subs, dropped = hub.filter_hub_links(hub.harvest_links(page, HUB), HUB,
                                                off_domain=False, cap=25, with_dropped=True)
    assert any("career-explorations" in u for u, _ in kept), "the real program must survive"
    assert dropped > 0, "and what the cap did drop must be reported, never hidden"
    assert len(kept) == 25


def test_links_under_the_hub_path_are_offered_first():
    """Not a detection rule -- an index's programs demonstrably need not sit under its path
    (Georgetown, Ringling and LIM all keep them elsewhere, which is why that test failed as a
    DETECTOR). It is only an ordering: when a cap forces a choice, spend it on the links most
    likely to be this hub's own programs rather than on whatever appeared first in the HTML."""
    page = _hub_html([("https://city.gov/about/newsroom-item", "Newsroom for high school"),
                      ("https://city.gov/parks/teen/art-club", "Art Club for high school")])
    HUB = "https://city.gov/parks/teen/"
    kept, _subs = hub.filter_hub_links(hub.harvest_links(page, HUB), HUB, off_domain=False)
    assert kept[0][0] == "https://city.gov/parks/teen/art-club"


@pytest.mark.parametrize("url", [
    "https://bit.ly/LadderInternshipsApplication",
    "https://calendly.com/heather-park/informational-session",
    "https://airtable.com/appx1OFdMpDfxtEkR/shrNdEykWdhc4yeSv",
    "https://docs.google.com/forms/d/e/1FAIpQLSc/viewform",
])
def test_shorteners_and_form_hosts_are_never_a_program_page(url):
    """A round-up's own funnel runs through these, and none can BE a program's page. A shortener
    is worse than useless: we would pay to extract whatever it points at today."""
    assert hub.is_nonprogram_link(url, "https://roundup.example/list") is True


# ---------- what is worth paying to extract ----------

def test_a_page_already_in_the_catalog_is_never_re_extracted():
    """The check that was supposed to do this suppressed NOTHING: it called
    find_duplicates(url, "") whose exact rule is "same URL AND similar name", so an empty name
    always fell through to a hint the caller ignored. Mining CMU's index inserted 14 rows of
    which 12 were pages the catalog already held -- and the walk-up lead had said so in advance."""
    from wingman import url_dedupe
    known = {url_dedupe.match_key("https://x.edu/pre-college/ai.html")}
    fresh, already, twice = hub.fresh_candidates(
        ["https://x.edu/pre-college/ai.html", "https://x.edu/pre-college/art.html"], known, set())
    assert fresh == ["https://x.edu/pre-college/art.html"]
    assert already == 1 and twice == 0


def test_two_hubs_linking_one_program_pay_for_it_once():
    """Measured: CMU AI Scholars appeared on two different Immerse round-ups in one 3-hub run."""
    seen = set()
    a, _, _ = hub.fresh_candidates(["https://x.edu/p/ai"], set(), seen)
    b, _, twice = hub.fresh_candidates(["https://x.edu/p/ai/"], set(), seen)
    assert a == ["https://x.edu/p/ai"] and b == [] and twice == 1


def test_a_malformed_href_cannot_stop_the_run():
    fresh, _, _ = hub.fresh_candidates(["http://a b c:99999/x", "https://x.edu/p/ok"], set(), set())
    assert fresh == ["https://x.edu/p/ok"]


# ---------- containment: a child page beside its parent is not its own program ----------

def test_child_page_dropped_when_parent_is_a_candidate():
    """The hub links a program's page AND its residential-life tab; only the parent is kept."""
    keep, dropped = hub.contained_children([
        "https://c.edu/programs/nyc-residential-summer",
        "https://c.edu/programs/nyc-residential-summer/residential-life",
    ])
    assert keep == ["https://c.edu/programs/nyc-residential-summer"]
    assert dropped == ["https://c.edu/programs/nyc-residential-summer/residential-life"]


def test_child_page_dropped_when_parent_is_in_the_catalog():
    """The canonical parent is already catalogued, so fresh_candidates never sees the child as a
    dup -- containment stops us paying to extract a worse-URL duplicate."""
    cat = hub.catalog_paths_by_host(
        [{"url": "https://c.edu/programs/nyc-residential-summer"}])
    keep, dropped = hub.contained_children(
        ["https://c.edu/programs/nyc-residential-summer/residential-life"], cat)
    assert keep == [] and len(dropped) == 1


def test_containment_is_direct_parent_only_and_same_host():
    # A grandparent relationship does NOT drop (direct parent only).
    keep, dropped = hub.contained_children([
        "https://c.edu/a",
        "https://c.edu/a/b/c",
    ])
    assert dropped == [] and len(keep) == 2
    # A same-path child on a DIFFERENT host is not contained.
    keep, dropped = hub.contained_children([
        "https://a.edu/p",
        "https://b.edu/p/child",
    ])
    assert dropped == [] and len(keep) == 2


def test_two_distinct_programs_under_one_index_both_survive():
    """The common case discover() yields: siblings, not parent/child -- neither is dropped."""
    keep, dropped = hub.contained_children([
        "https://x.edu/pre-college/ai",
        "https://x.edu/pre-college/art",
    ])
    assert dropped == [] and len(keep) == 2


# ---------- how a run spends its ceiling ----------

def test_the_ceiling_is_spread_across_hubs_not_taken_off_the_end():
    """Measured on the 43-hub run: a ceiling of 300 fell entirely on the hubs at the END of the
    file, which happened to be all ten walk-up leads -- round-ups took 248 extractions, walk-ups
    52, and 46 candidates were dropped. Brown, BU, Vanderbilt and UCSF reported zero rows because
    they never ran, which reads exactly like a hub that yielded nothing. A cap must bound the
    cost, never silently choose the winners."""
    all_new = ([(f"https://roundup{i}.com/list", [f"u{i}-{j}" for j in range(8)]) for i in range(33)]
               + [(f"https://uni{i}.edu/pre/", [f"w{i}-{j}" for j in range(12)]) for i in range(10)])
    trimmed, capped = hub.allocate_budget(all_new, 300)
    walk = sum(len(f) for u, f in trimmed if "uni" in u)
    assert walk >= 60, "the walk-up half must not be starved by being last in the list"
    assert sum(len(f) for _, f in trimmed) == 300
    assert sum(n for _, n in capped) == 384 - 300


def test_a_small_hub_is_never_trimmed_to_pay_for_a_big_one():
    all_new = [("https://a/1", ["x"]), ("https://b/2", [f"y{i}" for i in range(50)])]
    trimmed, capped = hub.allocate_budget(all_new, 20)
    assert dict(trimmed)["https://a/1"] == ["x"]
    assert [u for u, _ in capped] == ["https://b/2"]


def test_a_budget_that_covers_everything_changes_nothing():
    all_new = [("https://a/1", ["x", "y"]), ("https://b/2", ["z"])]
    assert hub.allocate_budget(all_new, 99) == (all_new, [])
    assert hub.allocate_budget(all_new, None) == (all_new, [])
