"""The zero-hallucination guard (app/services/eligibility.py).

The regression this pins down, concretely: the live curation call marks a program ineligible
and hands back a quote as proof. If that quote is not actually in the row's own text, the
program must NOT be hidden from a student who is in fact eligible — it reverts to eligible and
the override is flagged. Mirrors the action-item Algebra-2 lesson: a claim is trusted because
a source backs it, never because a model asserted it.
"""
import pytest

from app.services.eligibility import (
    apply_eligibility_verdict,
    verify_exclusion_quote,
)


# --------------------------------------------------------------------------- quote verify

def test_quote_verifies_against_named_eligibility_field():
    row = {"eligibility": "Open only to Boston Public Schools students."}
    assert verify_exclusion_quote(row, "Open only to Boston Public Schools students", "eligibility") is True


def test_quote_verifies_against_summary_when_eligibility_silent():
    # Restriction stated only in summary; the dedicated column is empty. Must still verify.
    row = {"eligibility": None, "summary": "A program designed exclusively for NYC high schoolers."}
    assert verify_exclusion_quote(row, "designed exclusively for NYC high schoolers", "summary") is True


def test_quote_verifies_via_fallback_on_mislabeled_field():
    # Model quoted the summary but labeled it 'eligibility'. Named-field-first fails, fallback
    # finds it — a benign mislabel must not throw away a real quote.
    row = {"eligibility": "High school students.", "summary": "For female-identifying students in California."}
    assert verify_exclusion_quote(row, "For female-identifying students in California", "eligibility") is True


def test_hallucinated_quote_does_not_verify():
    row = {"eligibility": "Open to all high school students."}
    assert verify_exclusion_quote(row, "Seniors only, ages 17 and up", "eligibility") is False


def test_quote_normalizes_curly_quotes_and_dashes():
    row = {"eligibility": "Open to rising juniors — U.S. citizens only."}
    # model reproduces straight quotes/hyphen; normalization must bridge them
    assert verify_exclusion_quote(row, "rising juniors - U.S. citizens only", "eligibility") is True


def test_too_short_quote_rejected():
    row = {"eligibility": "Girls only program for all ages."}
    assert verify_exclusion_quote(row, "Girls", "eligibility") is False  # under the char floor


def test_missing_quote_rejected():
    assert verify_exclusion_quote({"eligibility": "x"}, None, "eligibility") is False
    assert verify_exclusion_quote({"eligibility": "x"}, "", "eligibility") is False


# --------------------------------------------------------------------------- verdict apply

def test_eligible_verdict_passes_through():
    v = apply_eligibility_verdict({"eligibility": "anything"}, {"id": "a", "eligible": True})
    assert v["eligible"] is True
    assert v["quote_verified"] is None     # nothing to verify
    assert v["guard_overrode"] is False


def test_verified_exclusion_stands():
    row = {"eligibility": "Open only to Massachusetts residents."}
    v = apply_eligibility_verdict(row, {
        "id": "b", "eligible": False,
        "exclusion_quote": "Open only to Massachusetts residents",
        "exclusion_source_field": "eligibility",
    })
    assert v["eligible"] is False
    assert v["quote_verified"] is True
    assert v["guard_overrode"] is False
    assert v["exclusion_quote"] == "Open only to Massachusetts residents"


def test_unverifiable_exclusion_reverts_to_eligible_and_flags():
    row = {"eligibility": "Open to all high schoolers."}
    v = apply_eligibility_verdict(row, {
        "id": "c", "eligible": False,
        "exclusion_quote": "This program is seniors-only",   # not in the row
        "exclusion_source_field": "eligibility",
    })
    assert v["eligible"] is True            # reverted — unknown != ineligible
    assert v["quote_verified"] is False
    assert v["guard_overrode"] is True      # the signal to watch in production
    assert v["exclusion_quote"] is None     # not echoed when the cut is dropped


def test_exclusion_missing_quote_reverts():
    # Model said ineligible but supplied no quote at all -> cannot stand.
    v = apply_eligibility_verdict({"eligibility": "x"}, {"id": "d", "eligible": False})
    assert v["eligible"] is True
    assert v["guard_overrode"] is True
