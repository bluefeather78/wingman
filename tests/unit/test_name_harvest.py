"""Phase 4N name-harvest: the three FREE gates + the resolver's evidence bar. Pure, hermetic.

Nothing here makes a model call, a search or an HTTP request; `best_resolved_url` is exercised
against a stubbed fetcher so the evidence bar is tested without the network.
"""
import pytest

from agents import harvest_names as nh
from wingman import url_repair
from wingman import url_validate


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
    from agents import refind_dead_links
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


# ---------- ranking before the cap (weights taken from the first live run) ----------

@pytest.mark.parametrize("name", [
    "Drawing: Eye and Idea Pre-College Course at Columbia University",
    "Museum of Arts and Design Teen Programs: Artslife",
    "Interlochen Arts Camp - Fashion Program",
])
def test_looks_descriptive_true(name):
    """Every name carrying one of these markers came back unproven on the live run: a colon
    splits a label from a gloss, and ' at <Institution>' is the article placing the program."""
    assert nh.looks_descriptive(name) is True


@pytest.mark.parametrize("name", [
    "Career Insights Program", "New York TED Summer School",
    "American Mathematics Competitions (AMC)",          # parens are NOT a marker
    "Health Occupations Students of America (HOSA)",
])
def test_looks_descriptive_false(name):
    assert nh.looks_descriptive(name) is False


def test_fewer_identity_words_ranks_higher():
    """Measured: resolved names carried [2, 3, 4] identity words, unproven [4, 4, 4, 5, 5, 5, 7].
    title_proves needs EVERY word in the title, so each extra word is another chance to fail."""
    assert nh.name_rank_score("Career Insights Program") > \
        nh.name_rank_score("NYU Tisch School of the Arts: Drama, Production, and Design Workshop")


def test_source_brand_in_the_name_is_penalised():
    src = "https://www.immerse.education/knowledge-base/x/"
    assert nh.shares_source_identity("Immerse Education Fashion & Design School", src) is True
    assert nh.shares_source_identity("Otis College Summer of Art", src) is False
    assert nh.name_rank_score("Immerse Education Fashion School", src) < \
        nh.name_rank_score("Immerse Education Fashion School", "")


def test_ranking_reorders_but_never_drops():
    """The cap decides how many; ranking decides which. Total eligible must be unchanged."""
    names = ["Alpha Scholars Institute", "Beta: Gamma Institute at Delta University",
             "Epsilon Robotics Challenge"]
    text = " ".join(names)
    keep, dropped = nh.select_names(names, text, [], cap=99)
    assert sorted(keep) == sorted(names) and dropped["over_cap"] == []


def test_ranking_changes_what_the_cap_buys():
    """The live failure: a positional cap bought the top of the article. A descriptive name
    now sorts below a clean one even when it appears first."""
    names = ["Beta: Gamma Institute at Delta University", "Epsilon Robotics Challenge"]
    text = " ".join(names)
    keep, dropped = nh.select_names(names, text, [], cap=1)
    assert keep == ["Epsilon Robotics Challenge"]
    assert dropped["over_cap"] == ["Beta: Gamma Institute at Delta University"]


def test_rank_false_preserves_page_order():
    """The old behaviour stays reachable, so the two rules can be compared on one page."""
    names = ["Beta: Gamma Institute at Delta University", "Epsilon Robotics Challenge"]
    keep, _ = nh.select_names(names, " ".join(names), [], cap=1, rank=False)
    assert keep == ["Beta: Gamma Institute at Delta University"]


def test_ranking_is_stable_for_equal_scores():
    names = ["Alpha Scholars Institute", "Omega Robotics Challenge"]
    keep, _ = nh.select_names(names, " ".join(names), [], cap=2)
    assert keep == names


# ---------- the score floor: the cap became a ceiling ----------

def test_floor_drops_the_band_that_measured_zero():
    """score <= 0 resolved 0 of 3 live. Those names go to `below_score`, not `over_cap` —
    'not worth a search fee' and 'ran out of budget' are different facts about a run."""
    names = ["Epsilon Robotics Challenge",                              # clean, short -> 4
             "NYU Tisch School of the Arts: Drama, Production, and Design Workshop"]  # -> -3
    keep, dropped = nh.select_names(names, " ".join(names), [], cap=20, min_score=1)
    assert keep == ["Epsilon Robotics Challenge"]
    assert dropped["below_score"] == [names[1]]
    assert dropped["over_cap"] == []


def test_floor_self_sizes_rather_than_spending_to_the_ceiling():
    """The point of the floor: a ceiling of 20 must not mean 20 paid searches on a page that
    only has 1 name worth one."""
    # Five-plus identity words AND a descriptive marker, i.e. the shape that measured 0/3 —
    # a SHORT descriptive name scores 1 and is meant to pass (Interlochen scored 1, resolved).
    # Each filler needs its OWN distinctive token, or collapse_name_variants folds them into
    # one (digits are not identity words) and this stops testing the floor.
    names = ["Epsilon Robotics Challenge"] + [
        f"Zeta{i}world Quantum Robotics Alpha Beta Gamma: an elaborate gloss at Somewhere College"
        for i in range(19)]
    keep, dropped = nh.select_names(names, " ".join(names), [], cap=20, min_score=1)
    assert len(keep) == 1 and len(dropped["below_score"]) == 19


def test_ceiling_still_bounds_a_pathological_page():
    """The floor chooses; the ceiling is the spend backstop and must still bind."""
    names = [f"Alpha{i} Robotics Challenge" for i in range(30)]
    keep, dropped = nh.select_names(names, " ".join(names), [], cap=20, min_score=1)
    assert len(keep) == 20 and len(dropped["over_cap"]) == 10


def test_floor_cuts_a_resolvable_name_when_it_is_self_promotion():
    """Measured trade, stated so it is not mistaken for a regression: on the Immerse listicle
    the floor drops 'Immerse Education's Fashion & Design Summer School' (score -2 from the
    source-brand penalty) even though it resolved live — the row it produced carries
    FLAG_SELF_PROMOTED, so not paying for it is the intent."""
    src = "https://www.immerse.education/knowledge-base/x/"
    name = "Immerse Education's Fashion & Design Summer School"
    assert nh.name_rank_score(name, src) < 1
    keep, dropped = nh.select_names([name], name, [], cap=20, source_url=src, min_score=1)
    assert keep == [] and dropped["below_score"] == [name]


def test_min_score_none_disables_the_floor():
    """So the old count-capped behaviour stays reachable for comparison."""
    names = ["NYU Tisch School of the Arts: Drama, Production, and Design Workshop"]
    keep, _ = nh.select_names(names, names[0], [], cap=5, min_score=None)
    assert keep == names


# ---------- name variants: one page naming one program twice ----------

def test_collapse_name_variants_keeps_the_higher_ranked_form():
    """Measured live: an AI listicle named both 'MIT Beaver Works Summer Institute (BWSI)' and
    'MIT Beaver Works Summer Institute'. Their identity sets DIFFER, so is_known_name cannot
    see them as one — we paid for the second and inserted a twin of ec18343."""
    kept, collapsed = nh.collapse_name_variants(
        ["MIT Beaver Works Summer Institute (BWSI)", "MIT Beaver Works Summer Institute",
         "Cornell Summer Innovation Intensives"])
    assert kept == ["MIT Beaver Works Summer Institute", "Cornell Summer Innovation Intensives"]
    assert len(collapsed) == 1 and "(BWSI)" in collapsed[0]


def test_collapse_requires_a_subset_not_a_similarity():
    """url_dedupe measured what a ratio does here: 0.85 matched 264 catalog pairs, 257 of them
    genuinely distinct. Two names that merely overlap must both survive."""
    names = ["Stanford AI Scholars", "Carnegie Mellon AI Scholars"]
    kept, collapsed = nh.collapse_name_variants(names)
    assert sorted(kept) == sorted(names) and collapsed == []


def test_collapse_records_every_variant_it_removed():
    kept, collapsed = nh.collapse_name_variants(
        ["Georgetown University - Artificial Intelligence Academy",
         "Georgetown Artificial Intelligence Academy"])
    assert len(kept) == 1 and len(collapsed) == 1


def test_select_names_reports_variants_separately_from_the_cap():
    names = ["MIT Beaver Works Summer Institute", "MIT Beaver Works Summer Institute (BWSI)"]
    keep, dropped = nh.select_names(names, " ".join(names), [], cap=20)
    assert keep == ["MIT Beaver Works Summer Institute"]
    assert len(dropped["name_variant"]) == 1
    assert dropped["over_cap"] == [] and dropped["below_score"] == []


def test_a_listicle_operator_can_never_become_a_program_url():
    """ec18783 stored a veritasai.com round-up as NYU Tandon's own page. best_resolved_url
    rejects mills, so adding the host is what prevents it."""
    from wingman import url_validate as uv
    assert uv.is_content_mill(
        "https://www.veritasai.com/veritasaiblog/nyu-tandons-machine-learning-summer-program")
    assert nh.best_resolved_url(
        ["https://www.veritasai.com/veritasaiblog/nyu-tandons-ml-program"],
        "NYU Tandon Machine Learning Summer Program") is None
