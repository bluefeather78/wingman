"""Tests for wingman/seo_pages.py — the composite index bar and slug logic.

The bar (agreed 2026-09-06): a real summary PLUS >=2 of
{verified deadline, eligibility, cost/format, 3+ step checklist}. A verified deadline is the
strongest single signal but is deliberately NOT sufficient on its own — these tests pin both
directions so a future "just gate on the deadline" change fails loudly.
"""
from wingman.seo_pages import (
    evaluate_seo_page, slugify, assign_unique_slug,
    STATUS_INDEXED, STATUS_AWAITING, SUMMARY_MIN_CHARS,
)

GOOD_SUMMARY = "A free year-long research program in mathematics for high school students."
assert len(GOOD_SUMMARY) >= SUMMARY_MIN_CHARS

VERIFIED_DL = [{"type": "deadline", "date_iso": "2026-12-01", "estimated": False}]
ESTIMATED_DL = [{"type": "deadline", "date_iso": "2026-12-01", "estimated": True}]
CHECKLIST3 = [{"text": "Solve the problem set"}, {"text": "Submit the form"},
              {"text": "Provide a recommendation"}]


def test_summary_required():
    v = evaluate_seo_page({"summary": "too short", "important_dates": VERIFIED_DL,
                           "eligibility": "Grades 9-12"})
    assert not v["indexable"]
    assert v["status"] == STATUS_AWAITING
    assert "summary" in v["missing"]


def test_summary_plus_two_signals_indexes():
    v = evaluate_seo_page({"summary": GOOD_SUMMARY, "important_dates": VERIFIED_DL,
                           "eligibility": "Grades 9-12"})
    assert v["indexable"]
    assert v["status"] == STATUS_INDEXED
    assert v["has_verified_deadline"] is True


def test_verified_deadline_alone_is_not_enough():
    # summary + 1 signal (deadline) only -> awaiting. Deadline is not a sole gate.
    v = evaluate_seo_page({"summary": GOOD_SUMMARY, "important_dates": VERIFIED_DL})
    assert not v["indexable"]
    assert v["signal_count"] == 1
    assert "eligibility" in v["missing"]


def test_indexes_without_any_deadline():
    # eligibility + cost/format, no deadline at all -> still indexes (rolling-admissions row).
    v = evaluate_seo_page({"summary": GOOD_SUMMARY, "eligibility": "Open to grades 10-12",
                           "price": "Free"})
    assert v["indexable"]
    assert v["has_verified_deadline"] is False
    assert "deadline" in v["missing"]  # noted as absent, but not disqualifying


def test_estimated_deadline_counts_as_signal_but_not_verified():
    v = evaluate_seo_page({"summary": GOOD_SUMMARY, "important_dates": ESTIMATED_DL,
                           "checklist_ignored": True, "location": "Boston, MA"})
    # summary + deadline(estimated) + cost/format(location) = 2 signals
    assert v["indexable"]
    assert v["has_verified_deadline"] is False
    assert v["signals"]["deadline"] == "estimated"


def test_checklist_needs_three_items():
    two = [{"text": "a"}, {"text": "b"}]
    v = evaluate_seo_page({"summary": GOOD_SUMMARY, "action_items": two, "price": "Free"})
    # checklist(<3) does NOT count, so only cost/format counts -> 1 signal -> awaiting
    assert not v["indexable"]
    assert v["signals"]["checklist"] is False

    v3 = evaluate_seo_page({"summary": GOOD_SUMMARY, "action_items": CHECKLIST3, "price": "Free"})
    assert v3["indexable"]
    assert v3["signals"]["checklist"] is True


def test_grade_band_counts_as_eligibility():
    v = evaluate_seo_page({"summary": GOOD_SUMMARY, "grade_min": 9, "grade_max": 12,
                           "price": "Free"})
    assert v["signals"]["eligibility"] is True
    assert v["indexable"]


def test_score_weights_verified_deadline_highest():
    verified = evaluate_seo_page({"summary": GOOD_SUMMARY, "important_dates": VERIFIED_DL,
                                  "eligibility": "x" * 5})
    estimated = evaluate_seo_page({"summary": GOOD_SUMMARY, "important_dates": ESTIMATED_DL,
                                   "eligibility": "x" * 5})
    assert verified["score"] > estimated["score"]


def test_slugify():
    assert slugify("MIT PRIMES") == "mit-primes"
    assert slugify("  Summer/Research  Program!! ") == "summer-research-program"
    assert slugify("Café Résumé") == "cafe-resume"
    assert slugify("") == "opportunity"
    assert slugify("***") == "opportunity"


def test_assign_unique_slug_collision():
    taken = {"summer-research-program"}
    s = assign_unique_slug("Summer Research Program", "ec123", taken)
    assert s == "summer-research-program-ec123"
    # assign_unique_slug must not mutate the caller's set
    assert "summer-research-program-ec123" not in taken

    s2 = assign_unique_slug("Brand New Program", "ec999", taken)
    assert s2 == "brand-new-program"
