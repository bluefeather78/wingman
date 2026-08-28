"""Phase-4 hub mining: the FREE harvest + two-stage audience filter. Pure, hermetic."""
import pytest

import mine_hub_pages as hub


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


def test_hub_leads_are_mined_off_domain():
    """A hub lead is qualified by the distinct OTHER sites it links (>= 6), so its programs are
    on those sites — mining it same-domain would follow exactly the links the router did not
    count, i.e. the page's own navigation. All 25 queued leads were affected by this."""
    src = open("mine_hub_pages.py", encoding="utf-8").read()
    assert "hubs += [(u, True) for u in lead_urls]" in src
    assert "hubs += [(u, False) for u in lead_urls]" not in src
