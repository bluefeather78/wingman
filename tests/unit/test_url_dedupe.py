"""Unit tests for url_dedupe.py — URL/name matching for user-submitted opportunities.

All pure functions, no network. Behaviour is pinned against the real source; where the
source does something surprising it is called out in a comment rather than "fixed" in the
assertion.
"""
import pytest

import url_dedupe as ud


# --------------------------------------------------------------------------- split_url
def test_split_url_empty():
    assert ud.split_url("") == ("", "", "", "")
    assert ud.split_url(None) == ("", "", "", "")


def test_split_url_adds_scheme_and_lowercases_host():
    scheme, host, path, query = ud.split_url("WWW.Example.COM/Foo")
    assert scheme == "https"
    assert host == "example.com"        # www. stripped, host lowercased
    assert path == "/Foo"               # path CASE PRESERVED on purpose
    assert query == ""


def test_split_url_strips_www_and_index_file():
    scheme, host, path, query = ud.split_url("https://www.naclo.org/apply/index.html?x=1")
    assert host == "naclo.org"
    assert path == "/apply"             # index.html dropped as the directory itself
    assert query == "x=1"


def test_split_url_default_path_is_slash():
    assert ud.split_url("https://example.com")[2] == "/"


# --------------------------------------------------------------------------- _clean_query
def test_clean_query_drops_tracking_keeps_and_sorts_rest():
    # utm_source stripped; b and a preserved and sorted
    assert ud._clean_query("utm_source=x&b=2&a=1") == "a=1&b=2"


def test_clean_query_empty():
    assert ud._clean_query("") == ""


def test_clean_query_preserves_non_tracking_id():
    assert ud._clean_query("id=123") == "id=123"


# --------------------------------------------------------------------------- match_key
def test_match_key_normalizes_case_and_trailing_slash():
    a = ud.match_key("https://www.Example.com/Path/")
    b = ud.match_key("http://example.com/path")
    assert a == b == "example.com/path"


def test_match_key_empty_host_returns_empty():
    assert ud.match_key("") == ""
    assert ud.match_key("not a url at all ///") != ""  # gets coerced to a host, sanity only


def test_match_key_folds_query_and_tracking():
    a = ud.match_key("https://x.com/p?utm_source=g&id=5")
    b = ud.match_key("https://x.com/p?id=5")
    assert a == b


# --------------------------------------------------------------------- registrable_domain
@pytest.mark.parametrize("host,expected", [
    ("apply.naclo.org", "naclo.org"),
    ("www.naclo.org", "naclo.org"),
    ("naclo.org", "naclo.org"),
    ("med.cam.ac.uk", "cam.ac.uk"),        # multipart TLD head 'ac'
    ("med.stanford.edu", "stanford.edu"),
    ("", ""),
    ("localhost", "localhost"),
])
def test_registrable_domain(host, expected):
    assert ud.registrable_domain(host) == expected


# --------------------------------------------------------------------------- normalize_name
def test_normalize_name_strips_year():
    assert ud.normalize_name("ISEF 2025") == ud.normalize_name("ISEF 2026") == "isef"


def test_normalize_name_punctuation_and_whitespace():
    assert ud.normalize_name("  Foo-Bar!!  Baz ") == "foo bar baz"


def test_normalize_name_none():
    assert ud.normalize_name(None) == ""


# --------------------------------------------------------------------------- name_similarity
def test_name_similarity_identical():
    assert ud.name_similarity("Clark Scholars", "Clark Scholars") == 1.0


def test_name_similarity_generic_names_zero():
    # "internship" is in GENERIC_NAMES -> 0.0 even against itself
    assert ud.name_similarity("Internship", "Internship") == 0.0
    assert ud.name_similarity("Summer Program", "Summer Program") == 0.0


def test_name_similarity_short_name_zero():
    # normalized length < 4 -> 0.0 ("abc" is too short to be evidence)
    assert ud.name_similarity("abc", "abc") == 0.0


def test_name_similarity_year_makes_recurring_program_equal():
    assert ud.name_similarity("ISEF 2025", "ISEF 2026") == 1.0


# --------------------------------------------------------------------------- is_low_value_path
@pytest.mark.parametrize("url,expected", [
    ("https://x.com/program/faq", True),
    ("https://x.com/about-us", True),
    ("https://x.com/summer-research", False),
    ("https://x.com/", False),          # bare root, no segments
])
def test_is_low_value_path(url, expected):
    assert ud.is_low_value_path(url) is expected


# --------------------------------------------------------------------------- _prefix_relation
def test_prefix_relation_true_when_subpath():
    assert ud._prefix_relation("/a/b", "/a/b/c") is True


def test_prefix_relation_bare_root_guarded():
    # "/" is a prefix of everything but the guard refuses it (the shared-domain problem)
    assert ud._prefix_relation("/", "/anything/deep") is False


def test_prefix_relation_unrelated():
    assert ud._prefix_relation("/a/b", "/x/y") is False


def test_prefix_relation_equal_paths_not_prefix():
    # a == b after normalisation -> not a strict "sub-page of" relation
    assert ud._prefix_relation("/a/b", "/a/b/") is False


# --------------------------------------------------------------------------- find_duplicates
def _row(id_, name, url, **extra):
    d = {"id": id_, "name": name, "url": url}
    d.update(extra)
    return d


def test_find_duplicates_exact_url_and_name_rejects():
    rows = [_row("ec1", "Clark Scholars Program", "https://ttu.edu/clark")]
    exact, cands = ud.find_duplicates("https://ttu.edu/clark", "Clark Scholars Program", rows)
    assert exact is rows[0]
    assert cands == []


def test_find_duplicates_exact_url_different_name_flags_not_rejects():
    # spicestanford.smapply.io shared-portal case: same URL, different program.
    rows = [_row("ec1", "Stanford E-Japan", "https://spice.smapply.io/prog")]
    exact, cands = ud.find_duplicates("https://spice.smapply.io/prog",
                                      "Sejong Korea Scholars", rows)
    assert exact is None
    assert len(cands) == 1
    assert cands[0]["confidence"] == "strong"
    assert "identical URL" in cands[0]["reason"]


def test_find_duplicates_apply_url_cross_match():
    rows = [_row("ec1", "Some Program", "https://x.com/landing",
                 apply_url="https://x.com/apply-here")]
    exact, cands = ud.find_duplicates("https://x.com/apply-here", "Totally Different Name", rows)
    assert exact is None
    assert cands[0]["reason"] == "apply-url points at the same page"
    assert cands[0]["confidence"] == "strong"


def test_find_duplicates_same_site_gate_below_threshold():
    # 2 quiet peers on the same domain -> bare same-site hint fires.
    rows = [
        _row("ec1", "Alpha Zither Contest", "https://naclo.org/alpha"),
        _row("ec2", "Beta Quokka Meetup", "https://naclo.org/beta"),
    ]
    exact, cands = ud.find_duplicates("https://naclo.org/gamma", "Gamma Wombat Fair", rows)
    assert exact is None
    reasons = " ".join(c["reason"] for c in cands)
    assert "same site" in reasons


def test_find_duplicates_same_site_gate_above_threshold_suppressed():
    # 4 peers (> SAME_SITE_MAX_PEERS=3) with distinct paths and dissimilar names ->
    # bare same-site hint is dropped, no candidates.
    rows = [
        _row("ec1", "Alpha Zither Contest", "https://busy.edu/alpha"),
        _row("ec2", "Beta Quokka Meetup", "https://busy.edu/beta"),
        _row("ec3", "Delta Yak Symposium", "https://busy.edu/delta"),
        _row("ec4", "Kappa Xylophone Expo", "https://busy.edu/kappa"),
    ]
    exact, cands = ud.find_duplicates("https://busy.edu/omega", "Omega Narwhal Jam", rows)
    assert exact is None
    assert cands == []


def test_find_duplicates_confidence_ordering_and_trim_to_five():
    # Build 6 same-domain rows that all match by name similarity so >5 candidates exist,
    # plus one strong prefix match to verify strong sorts first.
    rows = [_row(f"ec{i}", "Marine Biology Summer Academy", f"https://ocean.org/p{i}")
            for i in range(6)]
    # a strong containment match (sub-page relation on same site)
    rows.append(_row("ecStrong", "Marine Biology Summer Academy",
                     "https://ocean.org/programs"))
    exact, cands = ud.find_duplicates("https://ocean.org/programs/marine",
                                      "Marine Biology Summer Academy", rows)
    assert exact is None
    assert len(cands) <= 5
    # strong candidates come first
    assert cands[0]["confidence"] == "strong"


def test_find_duplicates_no_match_returns_empty():
    rows = [_row("ec1", "Wildly Unrelated Thing", "https://other.com/z")]
    exact, cands = ud.find_duplicates("https://mine.com/a", "My Unique Program XYZ", rows)
    assert exact is None
    assert cands == []


def test_find_duplicates_empty_rows():
    assert ud.find_duplicates("https://x.com/a", "Name", []) == (None, [])
    assert ud.find_duplicates("https://x.com/a", "Name", None) == (None, [])


# ------------------------------------------------------- candidate id/name alignment
def test_find_duplicates_candidates_carry_their_own_rows_fields():
    """Each candidate's id, name, url and reason all come from the SAME pool row.

    Pinned after the 2026-08-23 review: a rejected NACLO row's dup_candidates named an
    id whose snapshot row was an unrelated film workshop with reason "name 100%
    similar". By construction (dict(row, ...)) that mismatch should be impossible in
    this function — the likely culprit is snapshot/DB drift from the old date-only
    snapshot overwrite bug — but the invariant is load-bearing for the console's
    dupe back-links, so it gets a pin.
    """
    pool = [
        _row("ecNaclo", "North American Computational Linguistics Open Competition",
             "https://naclo.org/register"),
        _row("ecFilm", "Summer High School Filmmakers Workshop",
             "https://tisch.nyu.edu/film"),
    ]
    exact, cands = ud.find_duplicates(
        "https://linguistics.example.edu/naclo",
        "North American Computational Linguistics Open Competition (NACLO)", pool)
    assert exact is None
    by_id = {c["id"]: c for c in cands}
    # The similar-name candidate is the NACLO row, carrying the NACLO row's own fields.
    assert "ecNaclo" in by_id
    assert by_id["ecNaclo"]["name"].startswith("North American")
    assert by_id["ecNaclo"]["url"] == "https://naclo.org/register"
    assert "similar" in by_id["ecNaclo"]["reason"]
    # The unrelated row must not appear under any id.
    assert "ecFilm" not in by_id
