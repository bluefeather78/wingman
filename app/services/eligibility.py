"""The zero-hallucination guard for Phase 1's live eligibility reasoning
(docs/plans/OPPORTUNITY_MATCHING_PLAN.md). Pure, offline-testable — the model CALL that produces a
verdict lives with the curation orchestration; this module only decides whether a verdict's
claimed exclusion is trustworthy enough to act on.

THE GUARD, IN ONE SENTENCE: a candidate may be marked ineligible only if the model supplies
a verbatim quote that really appears in one of the candidate's own text fields; a quote that
verifies nowhere is discarded and the candidate reverts to ELIGIBLE (unknown != ineligible).

WHAT THIS CATCHES AND WHAT IT DOES NOT (stated plainly, per the plan):
  * CATCHES over-exclusion — a hallucinated exclusion ("this program is seniors-only" when
    the row never says so). No quote -> no cut.
  * DOES NOT CATCH under-exclusion — a real hard-scope gate the model reads as open
    ("female-identifying students", no "only", shown to a boy). There is no quote to verify
    when the model FAILS to exclude, so no substring guard is possible; that direction is the
    WORSE harm and is covered by the Phase-1 gate's wrong-inclusion metric + the Phase-7 eval
    on the labeled hard-scope sample, NOT here.
  * ACCEPTED RESIDUAL — marketing hyperbole in `summary` ("exclusively designed to challenge
    top students") can verify as a quote and read as an exclusion. Not guarded in code (it IS
    a real substring); the eval's over-exclusion metric watches for it and the prompt's worked
    examples teach the distinction.
"""
from __future__ import annotations

# Reuse the exact normalization the action-item verifier uses (NFKC, curly quotes/dashes/
# spaces folded, whitespace collapsed, casefolded) so a quote and a field are always compared
# in the same alphabet. Not fuzzy matching — those characters carry no information. page_text
# is the repo-root, stdlib-only module app/ already depends on elsewhere.
from page_text import normalize_for_match

# The candidate fields an exclusion quote may be verified against. `eligibility` is the
# obvious home, but a restriction is sometimes stated only in `summary` ("designed
# exclusively for NYC high schoolers") with the dedicated column empty — so the check is NOT
# eligibility-only. `name`/`org` are included because a program's own title can carry the
# scope ("Girls Who Code ...").
EXCLUSION_QUOTE_FIELDS = ("eligibility", "summary", "name", "org")

# A floor so a 3-character "quote" ("all") cannot borrow a common word as proof. Lower than
# page_text's MIN_QUOTE_CHARS=24 on purpose: eligibility fields are short and specific, and
# real restrictions are short clauses ("US citizens only" is 16 chars and legitimate), so a
# 24-char floor would reject honest gates. The protection here is verbatim-substring-of-the-
# row's-own-short-field, not clause length.
MIN_EXCLUSION_QUOTE_CHARS = 8


def _field_text(row: dict, field: str) -> str:
    val = row.get(field)
    return "" if val is None else str(val)


def verify_exclusion_quote(row: dict, quote: str | None, source_field: str | None) -> bool:
    """True iff `quote` (normalized) is a real substring of one of the row's own text fields.

    Checks the model-named `source_field` FIRST; only if that fails does it fall back to every
    field in EXCLUSION_QUOTE_FIELDS — a benign field-mislabel (the model quoted `summary` but
    said `eligibility`) must not throw away a real, verifiable quote. Returns False for a
    missing/too-short quote."""
    if not quote or not isinstance(quote, str):
        return False
    q = normalize_for_match(quote)
    if len(q) < MIN_EXCLUSION_QUOTE_CHARS:
        return False

    # Named field first.
    if source_field and source_field in EXCLUSION_QUOTE_FIELDS:
        if q in normalize_for_match(_field_text(row, source_field)):
            return True

    # Fallback: any known text field (covers a mislabeled source_field).
    for field in EXCLUSION_QUOTE_FIELDS:
        if field == source_field:
            continue  # already tried
        if q in normalize_for_match(_field_text(row, field)):
            return True
    return False


def apply_eligibility_verdict(row: dict, verdict: dict) -> dict:
    """Turn a raw model verdict into a TRUSTED one, applying the guard.

    `verdict` is one element of the curation call's output, shaped:
      {"id", "eligible": bool, "exclusion_quote": str|None,
       "exclusion_source_field": str|None, ...(fit fields)...}

    Returns a dict carrying:
      eligible            — the trusted decision (an unverified exclusion is flipped to True)
      exclusion_quote     — echoed through, for the card/audit, only when the cut stands
      exclusion_source_field
      quote_verified      — bool|None: True/False when the model claimed an exclusion, None
                            when it did not (nothing to verify). This is the eval signal —
                            log the (quote, source_field, quote_verified) triple.
      guard_overrode      — True when the model said ineligible but the quote did not verify,
                            so the guard reverted it to eligible. The single most important
                            thing to watch in production.
    """
    eligible = verdict.get("eligible", True)
    if eligible is not False:
        # Model considers the candidate eligible — nothing to verify, pass through.
        return {
            "id": verdict.get("id"),
            "eligible": True,
            "exclusion_quote": None,
            "exclusion_source_field": None,
            "quote_verified": None,
            "guard_overrode": False,
        }

    quote = verdict.get("exclusion_quote")
    source_field = verdict.get("exclusion_source_field")
    verified = verify_exclusion_quote(row, quote, source_field)
    if verified:
        return {
            "id": verdict.get("id"),
            "eligible": False,
            "exclusion_quote": quote,
            "exclusion_source_field": source_field,
            "quote_verified": True,
            "guard_overrode": False,
        }
    # Unverifiable exclusion -> revert to eligible (unknown != ineligible), and flag it.
    return {
        "id": verdict.get("id"),
        "eligible": True,
        "exclusion_quote": None,
        "exclusion_source_field": None,
        "quote_verified": False,
        "guard_overrode": True,
    }
