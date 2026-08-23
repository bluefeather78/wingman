"""Unit tests for url_repair.py — the three acceptance tests that keep a repair honest.

All the functions under test are pure string/URL logic; nothing here fetches.
"""
import pytest

import url_repair as ur


# ------------------------------------------------------------------- _words
def test_words_drops_stopwords_and_short():
    # "summer", "high", "school", "program", "research" are stopwords; "of" too short.
    assert ur._words("Summer High School Research Program") == set()


def test_words_keeps_identifying_words():
    assert ur._words("Clark Scholars") == {"clark", "scholars"}


# ------------------------------------------------------------------- identity_words
def test_identity_words_name_minus_org():
    # org words subtracted so a match on the institution can't stand in for the program.
    assert ur.identity_words("Clark Scholars Program", "Texas Tech University") == \
        {"clark", "scholars"}


def test_identity_words_doodle_for_google_is_unverifiable():
    # "Doodle for Google" vs org "Google": only {doodle} survives -> < 2 words.
    words = ur.identity_words("Doodle for Google", "Google")
    assert words == {"doodle"}
    assert len(words) < 2


# ------------------------------------------------------------------- page_title
def test_page_title_extracts_and_cleans():
    html = "<html><head><title>  Clark &amp; Scholars </title></head></html>"
    assert ur.page_title(html) == "Clark & Scholars"


def test_page_title_missing():
    assert ur.page_title("<html></html>") == ""
    assert ur.page_title("") == ""


# ------------------------------------------------------------------- title_proves (Test 1+2)
def test_title_proves_all_words_present():
    ok, why = ur.title_proves("Clark Scholars Program - Texas Tech",
                              "Clark Scholars Program", "Texas Tech University")
    assert ok is True
    assert "all" in why


def test_title_proves_missing_word_fails():
    ok, why = ur.title_proves("Scholars Program - Texas Tech",
                              "Clark Scholars Program", "Texas Tech University")
    assert ok is False
    assert "clark" in why


def test_title_proves_no_title():
    ok, why = ur.title_proves("", "Clark Scholars Program", "Texas Tech")
    assert ok is False
    assert "no title" in why


def test_title_proves_fewer_than_two_identity_words_unverifiable():
    # "Doodle for Google" / org "Google" -> one identity word -> refuse.
    ok, why = ur.title_proves("Google Doodles", "Doodle for Google", "Google")
    assert ok is False
    assert "fewer than two" in why


# ------------------------------------------------------------------- keeps_identity (Test 3)
def test_keeps_identity_ok_when_no_old_identity_word():
    # old URL carries none of the identity words -> nothing to lose -> True.
    ok, why = ur.keeps_identity("https://x.edu/home", "https://x.edu/summer",
                               "Clark Scholars", "Clark Scholars", "Texas Tech")
    assert ok is True
    assert why == ""


def test_keeps_identity_catches_sibling_program_name_org_swap():
    # name/org swapped in the catalog: name='University of Notre Dame',
    # org='Global Scholars Program', old slug 'global-scholars', candidate 'summer-scholars'.
    ok, why = ur.keeps_identity(
        "https://nd.edu/global-scholars",
        "https://nd.edu/summer-scholars",
        "Summer Scholars",                      # candidate title
        "University of Notre Dame",
        "Global Scholars Program",
    )
    assert ok is False
    assert "global" in why


def test_keeps_identity_ok_when_identity_word_preserved():
    ok, why = ur.keeps_identity(
        "https://nd.edu/global-scholars",
        "https://nd.edu/global-scholars-2027",
        "Global Scholars",
        "University of Notre Dame",
        "Global Scholars Program",
    )
    assert ok is True


# ------------------------------------------------------------------- _variants
def test_variants_excludes_original_and_adds_www_toggle():
    variants = ur._variants("https://example.com/Path")
    assert "https://example.com/Path" not in variants
    # www toggle present
    assert any(v.startswith("https://www.example.com") for v in variants)


def test_variants_lowercases_mixed_case_path():
    variants = ur._variants("https://example.com/CNIX")
    assert any(v.endswith("/cnix") for v in variants)


def test_variants_strips_index_file():
    variants = ur._variants("https://example.com/dir/index.html")
    # a variant with index.html removed exists
    assert any(v.endswith("/dir/") or v.endswith("/dir") for v in variants)


def test_variants_malformed_returns_empty():
    # urlsplit raising ValueError -> [] (an unparseable bracketed host/port)
    assert ur._variants("http://[::1") == []
