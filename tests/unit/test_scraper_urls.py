"""Unit tests for the pure URL/row helpers in agents/scrape_opportunities.py.

Only the non-Gemini functions are imported/exercised: reconcile_url, spans_for_name,
build_row, next_id_generator, clean_value, _name_key. Nothing here calls the model.
"""
import pytest

from agents import scrape_opportunities as so


# ------------------------------------------------------------------- next_id_generator
def test_next_id_generator_empty_starts_at_default():
    g = so.next_id_generator([])
    assert next(g) == "ec18221"        # (18220) + 1
    assert next(g) == "ec18222"


def test_next_id_generator_uses_max_existing():
    g = so.next_id_generator(["ec5", "ec9", "ecX", "foo", "ec3"])
    assert next(g) == "ec10"


def test_next_id_generator_ignores_malformed_ids():
    g = so.next_id_generator(["ecabc", "banana"])
    assert next(g) == "ec18221"


# ------------------------------------------------------------------- clean_value
def test_clean_value_in_set():
    assert so.clean_value("Free", so.VALID_PRICE) == "Free"


def test_clean_value_not_in_set_is_none():
    assert so.clean_value("Cheap", so.VALID_PRICE) is None
    assert so.clean_value(None, so.VALID_PRICE) is None


# ------------------------------------------------------------------- _name_key
def test_name_key_drops_parenthetical_tail_and_stopwords():
    # split at "(" then drop stopwords. Note the scraper's _NAME_STOPWORDS does NOT
    # include "internship" (only "program"/"programs"), so it survives -> ["nasa","internship"].
    assert so._name_key("NASA Internship Programs (Summer 2027)") == ["nasa", "internship"]


def test_name_key_drops_after_colon():
    assert so._name_key("Clark Scholars: Apply Now") == ["clark", "scholars"]


def test_name_key_empty():
    assert so._name_key("") == []
    assert so._name_key(None) == []


# ------------------------------------------------------------------- spans_for_name
def test_spans_for_name_matches_all_significant_words():
    spans = [
        {"text": "Robotics Olympiad application details", "urls": ["https://a.com"]},
        {"text": "Unrelated coding camp", "urls": ["https://b.com"]},
    ]
    assert so.spans_for_name("Robotics Olympiad", spans) == ["https://a.com"]


def test_spans_for_name_tolerates_suffix_and_punctuation():
    # candidate "NASA Internship Programs (Summer 2027)" vs span "NASA Internship Programs:"
    spans = [{"text": "NASA Internship Programs:", "urls": ["https://nasa.gov/x"]}]
    assert so.spans_for_name("NASA Internship Programs (Summer 2027)", spans) == \
        ["https://nasa.gov/x"]


def test_spans_for_name_sorted_most_specific_first():
    spans = [
        {"text": "Clark Scholars program and a long list of many other unrelated things here",
         "urls": ["https://long.com"]},
        {"text": "Clark Scholars", "urls": ["https://short.com"]},
    ]
    # shorter (more specific) span's URLs come first
    result = so.spans_for_name("Clark Scholars", spans)
    assert result[0] == "https://short.com"


def test_spans_for_name_no_significant_words_returns_empty():
    # a name that is entirely stopwords -> no key -> []
    assert so.spans_for_name("Summer Program", [{"text": "anything", "urls": ["x"]}]) == []


# ------------------------------------------------------------------- reconcile_url
RESOLVED = ["https://prog.edu/page", "https://other.edu/x"]


def test_reconcile_span_and_model_agree():
    url, flags = so.reconcile_url("https://prog.edu/page", RESOLVED,
                                  span_urls=["https://prog.edu/page"])
    assert url == "https://prog.edu/page"
    assert flags == []


def test_reconcile_model_url_is_retrieved_beats_unrelated_span():
    # model url is itself a retrieved page -> keep it even though a span points elsewhere
    # (this is the branch that stopped SEO articles replacing the program's own page).
    url, flags = so.reconcile_url("https://prog.edu/page", RESOLVED,
                                  span_urls=["https://ladderinternships.com/blog"])
    assert url == "https://prog.edu/page"
    assert flags == []


def test_reconcile_model_same_host_as_span_replaced():
    url, flags = so.reconcile_url("https://prog.edu/wrong-path",
                                  ["https://unrelated.edu/z"],
                                  span_urls=["https://prog.edu/right-path"])
    assert url == "https://prog.edu/right-path"
    assert flags == [so.FLAG_URL_REPLACED]


def test_reconcile_no_model_url_takes_span():
    url, flags = so.reconcile_url("", RESOLVED, span_urls=["https://prog.edu/page"])
    assert url == "https://prog.edu/page"
    assert flags == []


def test_reconcile_span_unrelated_model_replaced_flagged():
    url, flags = so.reconcile_url("https://memory.com/guess",
                                  ["https://x.edu/y"],
                                  span_urls=["https://prog.edu/page"])
    assert url == "https://prog.edu/page"
    assert flags == [so.FLAG_URL_REPLACED]


def test_reconcile_no_span_model_is_retrieved():
    url, flags = so.reconcile_url("https://prog.edu/page", RESOLVED, span_urls=[])
    assert url == "https://prog.edu/page"
    assert flags == []


def test_reconcile_no_span_same_host_retrieved_replaced():
    url, flags = so.reconcile_url("https://prog.edu/wrong", RESOLVED, span_urls=[])
    assert url == "https://prog.edu/page"      # same-host retrieved page preferred
    assert flags == [so.FLAG_URL_REPLACED]


def test_reconcile_no_span_unsourced_model_flagged():
    url, flags = so.reconcile_url("https://memory.com/guess",
                                  ["https://elsewhere.edu/a"], span_urls=[])
    assert url == "https://memory.com/guess"
    assert flags == [so.FLAG_URL_UNSOURCED]


def test_reconcile_nothing_at_all():
    assert so.reconcile_url("", [], span_urls=[]) == ("", [])


# ------------------------------------------------------------------- build_row
def _cand(**kw):
    base = {"name": "Clark Scholars", "type": "Program"}
    base.update(kw)
    return base


def test_build_row_none_on_missing_name():
    assert so.build_row(_cand(name=""), "ec1", "src", "https://x.com", []) is None


def test_build_row_none_on_missing_url():
    assert so.build_row(_cand(), "ec1", "src", "", []) is None


def test_build_row_valid():
    row = so.build_row(_cand(org="Texas Tech", price="Free", location="Remote"),
                       "ec99", "scraper-national-20260823", "https://ttu.edu/clark", [])
    assert row["id"] == "ec99"
    assert row["name"] == "Clark Scholars"
    assert row["org"] == "Texas Tech"
    assert row["price"] == "Free"
    assert row["location"] == "Remote"
    assert row["type"] == "Program"
    assert row["is_active"] is False
    assert row["source"] == "scraper-national-20260823"


def test_build_row_invalid_type_parked_on_program():
    # invalid type -> parked as "Program" so the row inserts; caller adds FLAG_NO_TYPE.
    row = so.build_row(_cand(type="Bogus"), "ec1", "src", "https://x.com", [])
    assert row["type"] == "Program"


def test_build_row_invalid_enum_values_null():
    row = so.build_row(_cand(price="cheap", location="orbit", intl="martians", season="monsoon"),
                       "ec1", "src", "https://x.com", [])
    assert row["price"] is None
    assert row["location"] is None
    assert row["intl"] is None
    assert row["season"] is None


def test_build_row_non_list_subject_tags_wrapped():
    row = so.build_row(_cand(subject_tags="STEM"), "ec1", "src", "https://x.com", [])
    assert row["subject_tags"] == ["STEM"]


def test_build_row_grade_ints_only():
    row = so.build_row(_cand(grade_min=9, grade_max="twelve"), "ec1", "src", "https://x.com", [])
    assert row["grade_min"] == 9
    assert row["grade_max"] is None       # non-int dropped
