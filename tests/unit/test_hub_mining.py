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
