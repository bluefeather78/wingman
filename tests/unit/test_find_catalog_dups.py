"""Unit tests for find_catalog_dups.extra_name_pairs — cut 3 (acronym / token overlap).

Pure, no network. These are HINTS fed to the scan, so the bar is "worth a look", and the
tests pin the two things that must hold: it catches the misses cuts 1/2 cannot see, and it
does NOT manufacture noise from bare-institution names or single shared category words.
"""
import find_catalog_dups as fcd


def _row(id_, name, url, active=True):
    return {"id": id_, "name": name, "url": url, "is_active": active}


def _pair_ids(pairs):
    return {frozenset((p["rows"][0]["id"], p["rows"][1]["id"])) for p in pairs}


def test_acronym_matches_expansion_cross_host():
    rows = [
        _row("1", "NACLO", "https://naclo.org/"),
        _row("2", "North American Computational Linguistics Olympiad",
             "https://linguistics.example.edu/naclo"),
    ]
    pairs = fcd.extra_name_pairs(rows)
    assert frozenset(("1", "2")) in _pair_ids(pairs)
    assert "acronym" in pairs[0]["reason"]


def test_token_overlap_catches_reordering_cross_host():
    # Same words, reordered, on two different domains — invisible to cuts 1 and 2.
    rows = [
        _row("1", "Stanford Summer Research Program", "https://a.stanford.edu/x"),
        _row("2", "Summer Research Program at Stanford", "https://aggregator.com/stanford"),
    ]
    assert frozenset(("1", "2")) in _pair_ids(fcd.extra_name_pairs(rows))


def test_single_shared_category_word_does_not_pair():
    # Two different programs sharing only 'robotics' must NOT pair (needs >= 2 shared tokens).
    rows = [
        _row("1", "Robotics Summer Camp", "https://a.org/"),
        _row("2", "Underwater Robotics League", "https://b.org/"),
    ]
    assert fcd.extra_name_pairs(rows) == []


def test_two_bare_institutions_are_suppressed():
    # Both names are just the institution + campus; the guard keeps them out even if tokens
    # overlap, because an institution name is not evidence of a duplicate program.
    rows = [
        _row("1", "University of Texas", "https://utexas.edu/a"),
        _row("2", "University of Texas", "https://utexas.edu/b"),
    ]
    assert fcd.extra_name_pairs(rows) == []


def test_different_campuses_do_not_pair():
    rows = [
        _row("1", "University of Texas at Austin", "https://utexas.edu/a"),
        _row("2", "University of Texas at El Paso", "https://utep.edu/b"),
    ]
    assert fcd.extra_name_pairs(rows) == []


def test_inactive_rows_are_ignored():
    rows = [
        _row("1", "NACLO", "https://naclo.org/", active=False),
        _row("2", "North American Computational Linguistics Olympiad",
             "https://x.edu/naclo", active=False),
    ]
    assert fcd.extra_name_pairs(rows) == []


def test_generic_names_carry_no_evidence():
    rows = [
        _row("1", "Summer Program", "https://a.org/"),
        _row("2", "Summer Program", "https://b.org/"),
    ]
    assert fcd.extra_name_pairs(rows) == []


def test_same_site_high_overlap_is_strong():
    rows = [
        _row("1", "Marine Biology Summer Academy", "https://ocean.org/a"),
        _row("2", "Marine Biology Summer Academy", "https://ocean.org/b"),
    ]
    pairs = fcd.extra_name_pairs(rows)
    assert pairs and pairs[0]["confidence"] == "strong"
