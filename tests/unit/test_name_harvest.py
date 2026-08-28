"""Phase 4N name-harvest: the three FREE gates + the resolver's evidence bar. Pure, hermetic.

Nothing here makes a model call, a search or an HTTP request; `best_resolved_url` is exercised
against a stubbed fetcher so the evidence bar is tested without the network.
"""
import pytest

import harvest_names as nh
import url_repair
import url_validate


# ---------- parse_names: a malformed answer must SHRINK the work list, never invent in it ----

def test_parse_names_plain_array():
    assert nh.parse_names(["MIT PRIMES", "Regeneron Science Talent Search"]) == [
        "MIT PRIMES", "Regeneron Science Talent Search"]


def test_parse_names_accepts_objects_and_wrapper():
    assert nh.parse_names([{"name": "MIT PRIMES"}, {"name": "Clark Scholars"}]) == [
        "MIT PRIMES", "Clark Scholars"]
    assert nh.parse_names({"programs": ["MIT PRIMES"]}) == ["MIT PRIMES"]


def test_parse_names_dedupes_case_insensitively_keeping_first():
    assert nh.parse_names(["MIT PRIMES", "mit primes", "Clark Scholars"]) == [
        "MIT PRIMES", "Clark Scholars"]


def test_parse_names_strips_list_furniture_and_whitespace():
    assert nh.parse_names(["  - MIT   PRIMES ", "• Clark Scholars"]) == [
        "MIT PRIMES", "Clark Scholars"]


@pytest.mark.parametrize("raw", ["not a list", 7, None, [1, 2, None], [{"title": "x"}]])
def test_parse_names_drops_unusable_shapes(raw):
    assert nh.parse_names(raw) == []


def test_parse_names_drops_overlong_entries():
    assert nh.parse_names(["x" * 200, "MIT PRIMES"]) == ["MIT PRIMES"]


def test_parse_names_honours_cap():
    assert nh.parse_names([f"Program Alpha{i} Beta" for i in range(50)], cap=3) == [
        "Program Alpha0 Beta", "Program Alpha1 Beta", "Program Alpha2 Beta"]


# ---------- gate 1: the name must actually be on the page ----------

def test_name_is_on_page_true_when_every_identity_word_present():
    text = "Our summer offerings include the Clark Scholars Program for rising seniors."
    assert nh.name_is_on_page("Clark Scholars Program", text) is True


def test_name_is_on_page_false_when_a_word_is_missing():
    """The invented-program case: the model lists something it remembers, not something it read."""
    text = "Our summer offerings include the Clark Scholars Program."
    assert nh.name_is_on_page("Telluride Association Summer Seminar", text) is False


def test_name_is_on_page_matches_whole_words_not_substrings():
    """'art' must not be proven by 'start' — identity words are short by construction."""
    assert nh.name_is_on_page("Art Portfolio", "Getting started with your portfolio") is False


def test_name_is_on_page_is_case_and_punctuation_insensitive():
    assert nh.name_is_on_page("Kenyon Review Workshop",
                              "THE KENYON  REVIEW’s workshop") is True


def test_name_is_on_page_false_for_a_name_with_no_identity_words():
    """A name made only of generic words asserts nothing, so a page cannot vouch for it."""
    assert nh.name_is_on_page("Summer Program", "Summer Program details here") is False


# ---------- gate 2: the name must be provable at all ----------

@pytest.mark.parametrize("name", ["Debate", "Summer Internship", "Research Program", ""])
def test_name_is_resolvable_false_when_title_proof_could_never_pass(name):
    assert nh.name_is_resolvable(name) is False
    # and the gate agrees with the test it is protecting
    assert url_repair.title_proves("anything at all", name, "")[0] is False


@pytest.mark.parametrize("name", ["Clark Scholars Program", "Kenyon Review Young Writers"])
def test_name_is_resolvable_true_for_a_name_with_two_own_words(name):
    assert nh.name_is_resolvable(name) is True


def test_name_is_resolvable_subtracts_the_org():
    """The org's words cannot stand in for the program's — url_repair's test 2."""
    assert nh.name_is_resolvable("Notre Dame Program", org="University of Notre Dame") is False


# ---------- gate 3: strict catalog dedup, biased toward paying for the search ----------

CATALOG = [
    {"id": "ec1", "name": "Clark Scholars Program", "url": "https://a.edu/clark"},
    {"id": "ec2", "name": "1-Week Medical Academy", "url": "https://b.edu/med1"},
    {"id": "ec3", "name": "Summer Internship", "url": "https://c.edu/si"},
]


def test_is_known_name_matches_the_same_program():
    assert nh.is_known_name("Clark Scholars Program", CATALOG) == "ec1"


def test_is_known_name_ignores_wording_that_carries_no_identity():
    """'Program' is a stopword, so the same program under a shorter label still matches."""
    assert nh.is_known_name("Clark Scholars", CATALOG) == "ec1"


def test_is_known_name_does_not_suppress_the_measured_collision():
    """CLAUDE.md's case: 1-Week vs 3-Week Medical Academy score 0.95 on a ratio and are
    distinct programs. Their identity words are IDENTICAL — identity_words drops tokens under
    three characters, so the digit that distinguishes them disappears — which is exactly why
    the signature carries the discarded marks too."""
    assert (url_repair.identity_words("3-Week Medical Academy")
            == url_repair.identity_words("1-Week Medical Academy"))
    assert nh.is_known_name("3-Week Medical Academy", CATALOG) is None


def test_is_known_name_signature_separates_only_on_the_marks():
    """Sanity-check the mechanism, not just the outcome."""
    assert (nh._name_signature("1-Week Medical Academy")
            != nh._name_signature("3-Week Medical Academy"))
    assert nh._name_signature("Clark Scholars Program") == nh._name_signature("Clark Scholars")


def test_is_known_name_never_suppresses_a_generic_name():
    assert nh.is_known_name("Summer Internship", CATALOG) is None


def test_is_known_name_requires_equality_not_a_subset():
    """A more specific program must not be swallowed by a broader incumbent."""
    assert nh.is_known_name("Clark Scholars Program in Astrophysics", CATALOG) is None


def test_is_known_name_tolerates_an_empty_catalog():
    assert nh.is_known_name("Clark Scholars Program", []) is None
    assert nh.is_known_name("Clark Scholars Program", None) is None


# ---------- select_names: the gates in order, with a cap and an audit trail ----------

PAGE = ("We list the Clark Scholars Program, the 3-Week Medical Academy, a Debate club, "
        "and the Kenyon Review Young Writers workshop.")


def test_select_names_applies_every_gate_and_says_what_each_cost():
    keep, dropped = nh.select_names(
        ["Clark Scholars Program",              # in the catalog already
         "3-Week Medical Academy",              # new (the collision is not suppressed)
         "Debate",                              # unprovable
         "Telluride Association Summer Seminar",  # not on the page
         "Kenyon Review Young Writers"],        # new
        PAGE, CATALOG)
    assert keep == ["3-Week Medical Academy", "Kenyon Review Young Writers"]
    assert dropped["already_in_catalog"] == ["Clark Scholars Program (= ec1)"]
    assert dropped["unprovable"] == ["Debate"]
    assert dropped["not_on_page"] == ["Telluride Association Summer Seminar"]


def test_select_names_caps_and_records_the_overflow():
    """A silent cap reads as 'the page named that many'. It must be reported instead."""
    text = "Alpha Scholars Institute, Beta Scholars Institute, Gamma Scholars Institute"
    keep, dropped = nh.select_names(
        ["Alpha Scholars Institute", "Beta Scholars Institute", "Gamma Scholars Institute"],
        text, [], cap=2)
    assert keep == ["Alpha Scholars Institute", "Beta Scholars Institute"]
    assert dropped["over_cap"] == ["Gamma Scholars Institute"]


def test_select_names_on_page_gate_runs_before_the_paid_work():
    keep, _ = nh.select_names(["Anything At All"], "", CATALOG)
    assert keep == []


# ---------- best_resolved_url: the evidence bar, with the network stubbed out ----------

def _stub_fetch(monkeypatch, pages):
    """pages: {url: title}. A url absent from the map fetches as blocked."""
    def fake_fetch(url, timeout=None):
        if url not in pages:
            return None, None
        return f"<html><head><title>{pages[url]}</title></head><body>x</body></html>", url
    monkeypatch.setattr(url_repair, "_fetch", fake_fetch)


def test_best_resolved_url_takes_the_title_proven_page(monkeypatch):
    _stub_fetch(monkeypatch, {
        "https://x.edu/other": "Something Else Entirely",
        "https://x.edu/clark": "Clark Scholars Program | X University",
    })
    got = nh.best_resolved_url(["https://x.edu/other", "https://x.edu/clark"],
                               "Clark Scholars Program")
    assert got == "https://x.edu/clark"


def test_best_resolved_url_returns_none_when_nothing_proves(monkeypatch):
    _stub_fetch(monkeypatch, {"https://x.edu/other": "Admissions"})
    assert nh.best_resolved_url(["https://x.edu/other"], "Clark Scholars Program") is None


def test_best_resolved_url_rejects_an_editorial_post(monkeypatch):
    """The live UVA class: same words in the title, but it is an article about the program."""
    _stub_fetch(monkeypatch, {
        "https://x.edu/blog/clark-scholars-program-spotlight": "Clark Scholars Program Spotlight",
    })
    assert nh.best_resolved_url(
        ["https://x.edu/blog/clark-scholars-program-spotlight"], "Clark Scholars Program") is None


def test_best_resolved_url_rejects_a_content_mill(monkeypatch):
    mill = "https://www.reddit.com/r/x/clark-scholars-program"
    assert url_validate.is_content_mill(mill) is True
    _stub_fetch(monkeypatch, {mill: "Clark Scholars Program - reddit"})
    assert nh.best_resolved_url([mill], "Clark Scholars Program") is None


def test_best_resolved_url_prefers_a_dedicated_page_over_a_homepage(monkeypatch):
    _stub_fetch(monkeypatch, {
        "https://clarkscholars.org/": "Clark Scholars Program",
        "https://x.edu/clark": "Clark Scholars Program | X University",
    })
    got = nh.best_resolved_url(["https://clarkscholars.org/", "https://x.edu/clark"],
                               "Clark Scholars Program")
    assert got == "https://x.edu/clark"


def test_best_resolved_url_accepts_a_proven_homepage_when_it_is_all_there_is(monkeypatch):
    """Unlike a re-find, a harvested name has no deep page that was deleted — for a dedicated
    program site the homepage IS the program page (jshs.org, nacloweb.org)."""
    _stub_fetch(monkeypatch, {"https://clarkscholars.org/": "Clark Scholars Program"})
    assert nh.best_resolved_url(["https://clarkscholars.org/"],
                                "Clark Scholars Program") == "https://clarkscholars.org/"


def test_best_resolved_url_stops_after_the_fetch_cap(monkeypatch):
    fetched = []

    def fake_fetch(url, timeout=None):
        fetched.append(url)
        return None, None
    monkeypatch.setattr(url_repair, "_fetch", fake_fetch)
    urls = [f"https://x.edu/p{i}" for i in range(10)]
    assert nh.best_resolved_url(urls, "Clark Scholars Program") is None
    assert len(fetched) == nh.MAX_SIBLING_FETCH


def test_best_resolved_url_tolerates_an_empty_grounding():
    assert nh.best_resolved_url([], "Clark Scholars Program") is None
    assert nh.best_resolved_url(None, "Clark Scholars Program") is None


def test_resolve_angle_names_the_program_and_asks_for_its_own_page():
    angle = nh.resolve_angle("Clark Scholars Program", "Texas Tech")
    assert '"Clark Scholars Program"' in angle
    assert "Texas Tech" in angle
    assert "own page" in angle


# ---------- the shared editorial test promoted out of refind_dead_links ----------

@pytest.mark.parametrize("url", [
    "https://x.edu/blog/a-program", "https://x.edu/news/a-program",
    "https://x.edu/press-releases/a", "https://x.edu/stories/a",
])
def test_is_editorial_url_true(url):
    assert url_validate.is_editorial_url(url) is True


@pytest.mark.parametrize("url", [
    "https://x.edu/clark-scholars", "https://x.edu/", "https://x.edu/summer/clark", "",
])
def test_is_editorial_url_false(url):
    assert url_validate.is_editorial_url(url) is False


def test_refind_still_sees_the_same_editorial_test():
    """The promotion must be behaviour-neutral for the caller it came from."""
    import refind_dead_links
    assert refind_dead_links._is_editorial_url is url_validate.is_editorial_url
    assert refind_dead_links._EDITORIAL_SEGMENTS == url_validate.EDITORIAL_SEGMENTS


def test_is_known_name_matches_across_a_short_qualifier():
    """Measured live 2026-08-27: the catalog holds 'US Academic Decathlon' (ec17937). Carrying
    short alphabetic tokens as marks made the harvested 'Academic Decathlon' miss it and re-pay
    for a search on a program we already have. A digit says WHICH program; 'US' says where."""
    catalog = [{"id": "ec17937", "name": "US Academic Decathlon", "url": "https://x.org/ad"}]
    assert nh.is_known_name("Academic Decathlon", catalog) == "ec17937"


def test_name_signature_carries_digits_but_not_short_words():
    assert nh._name_signature("1-Week Medical Academy")[1] == frozenset({"1"})
    assert nh._name_signature("US Academic Decathlon")[1] == frozenset()
    assert nh._name_signature("Academic Decathlon")[1] == frozenset()


# ---------- self-promotion: measured on the first live run ----------

def test_is_self_promoted_true_when_a_page_names_its_own_product():
    """All 3 rows the first live run produced were Immerse products, two of them harvested
    from Immerse's own listicle. Flagged, never rejected — a provider can host a real program."""
    assert nh.is_self_promoted(
        "https://www.immerse.education/summer-schools/fashion-design",
        "https://www.immerse.education/knowledge-base/15-summer-art-programs/") is True


def test_is_self_promoted_matches_across_a_subdomain():
    assert nh.is_self_promoted("https://www.ted.immerse.education/locations/new-york",
                               "https://www.immerse.education/knowledge-base/x/") is True


def test_is_self_promoted_false_for_an_independent_program():
    assert nh.is_self_promoted("https://www.otis.edu/summer-of-art",
                               "https://www.immerse.education/knowledge-base/x/") is False


@pytest.mark.parametrize("a,b", [("", "https://x.com/a"), ("https://x.com/a", ""), (None, None)])
def test_is_self_promoted_tolerates_missing_input(a, b):
    assert nh.is_self_promoted(a, b) is False


def test_row_flags_attaches_the_self_promotion_flag():
    flags = nh._row_flags("https://www.immerse.education/summer-schools/x", "X Program",
                          "Immerse", {"type": "Program"},
                          source_url="https://www.immerse.education/knowledge-base/y/")
    assert nh.FLAG_SELF_PROMOTED in flags
