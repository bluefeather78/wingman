"""Eligibility-only gate over a recall pool (docs/plans/RECALL_GRID_MERGE_PLAN.md).

Unlike curation (which verdicts only the rows it PICKS), the main-style grid shows the whole
recall pool, so every row that could carry a restriction must be verdicted. This runs one
model call that returns an eligibility verdict PER candidate, then re-verifies each exclusion
in code via `eligibility.apply_eligibility_verdict` — so the model can only ever make the list
MORE inclusive (a claimed exclusion with no verbatim quote in the row's own text is overridden
and the row kept).

Cost/latency guard: only rows whose own text carries restriction SIGNAL go to the model; rows
with no such signal are eligible without a call. This keeps the request small and dodges
truncation on a large-pool verdict response. The keyword set is deliberately GENEROUS — a
false positive just sends one more row to the model (cheap); a false negative would skip a real
gate (harmful).

The prompt (ELIGIBILITY_ONLY_SYSTEM) is MARQUEE M8. Its rules are Part 1 of curation's prompt
(the eligibility half) verbatim; only the framing changes from "pick + exclude" to "verdict
every candidate".

Pure except for the injected model-call function (unit-testable with a stub).
"""
from __future__ import annotations

import json
import re

from app.services.eligibility import apply_eligibility_verdict
from app.services.curation import build_candidate_view

# Restriction SIGNAL: if none of these appear in a row's name/org/summary/eligibility text, the
# row states no gate and needs no model call. Generous by design (see module docstring).
_RESTRICTION_WORDS = (
    # citizenship / residency
    "citizen", "resident", "residency", "permanent resident", "green card", "visa", "u.s.",
    "us citizen", "domestic", "international",
    # geographic scope
    "only", "restricted", "must reside", "must live", "students of", "public schools",
    "county", "district", "state of", "residing",
    # demographic
    "female", "male", "women", "woman", "girls", "boys", "non-binary", "nonbinary", "gender",
    "bipoc", "indigenous", "native american", "underrepresented", "first-generation",
    "first generation", "first-gen", "minority", "low-income", "low income", "income",
    "identify as", "identifying",
    # grade / age / entry window
    "grade", "grades", "rising", "freshman", "sophomore", "junior", "senior", "seniors",
    "age", "ages", "years old", "apply during", "enrolled", "current ", "must be",
    # prerequisite
    "prerequisite", "must have", "required course", "gpa", "eligible",
)
_RESTRICTION_RE = re.compile("|".join(re.escape(w) for w in _RESTRICTION_WORDS), re.IGNORECASE)

_SCAN_FIELDS = ("name", "org", "summary", "eligibility")


def has_restriction_signal(row: dict) -> bool:
    """True if any of the row's own text fields carry a word that could name an eligibility gate."""
    for f in _SCAN_FIELDS:
        v = row.get(f)
        if isinstance(v, str) and v and _RESTRICTION_RE.search(v):
            return True
    return False


def build_eligibility_user_content(student: dict, candidate_views: list[dict]) -> str:
    """The user message: the student (grade/location/funnel_answers carry the facts a gate is
    checked against — citizenship, gender when known) + the candidates to verdict."""
    ctx = {
        "grade": student.get("grade"),
        "location": student.get("location") or {},
        # Sensitive attributes are session-only and only present if the student volunteered them.
        "attributes": {k: v for k, v in (student.get("funnel_answers") or {}).items()
                       if k in ("citizenship", "gender")},
    }
    return (
        "STUDENT (verdict eligibility against these facts only):\n" + json.dumps(ctx, ensure_ascii=False)
        + "\n\nCANDIDATES (JSON):\n" + json.dumps(candidate_views, ensure_ascii=False)
        + "\n\nReturn the verdict for EVERY candidate per the schema."
    )


def gate_pool_eligibility(pool, student, gate_fn, parse_fn):
    """Drop verified-ineligible rows from `pool`, preserving order. Injected:
      gate_fn(system, user_content) -> raw_text ; parse_fn(raw_text) -> dict|None.

    Returns {"pool": survivors, "excluded": [ids], "checked": n, "called": bool}. Rows with no
    restriction signal skip the model. A row the model omits, or excludes without a verifiable
    quote, is KEPT (unknown != ineligible; the guard overrides an unverifiable exclusion)."""
    to_check = [r for r in pool if has_restriction_signal(r)]
    if not to_check or gate_fn is None:
        return {"pool": pool, "excluded": [], "checked": 0, "called": False}

    rows_by_id = {r.get("id"): r for r in to_check}
    user_content = build_eligibility_user_content(student, [build_candidate_view(r) for r in to_check])
    raw = gate_fn(ELIGIBILITY_ONLY_SYSTEM, user_content)
    try:
        parsed = parse_fn(raw) if raw else None
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        # Unreadable verdicts — keep everyone (never hide a row on a parse failure).
        return {"pool": pool, "excluded": [], "checked": len(to_check), "called": True}

    ineligible = set()
    for v in (parsed.get("verdicts") or []):
        if not isinstance(v, dict):
            continue
        row = rows_by_id.get(v.get("id"))
        if row is None:
            continue
        verdict = apply_eligibility_verdict(row, v)
        if not verdict["eligible"]:
            ineligible.add(v.get("id"))

    survivors = [r for r in pool if r.get("id") not in ineligible]
    return {"pool": survivors, "excluded": sorted(ineligible), "checked": len(to_check), "called": True}


# --------------------------------------------------------------------------- the prompt (M8)
ELIGIBILITY_ONLY_SYSTEM = """You are Wingman's eligibility checker. You are given a high school \
student and a list of candidate opportunities. Return an ELIGIBILITY VERDICT for EVERY \
candidate — nothing about fit or quality, only whether THIS student is allowed to apply.

Read ONLY what each candidate's own text says; never guess, never use outside knowledge. A \
candidate is INELIGIBLE only if its own text states a real restriction this student fails. If \
the text is silent or unclear, mark them ELIGIBLE — wrongly hiding a real match is worse than \
showing a long shot.

- GRADE: "rising Nth grader" means a student CURRENTLY FINISHING grade N-1. A grade-9 student \
is eligible for "rising 10th graders". Do not exclude on a numeric grade range if the wording \
is "rising"/age-based.
- ENTRY WINDOW (too late): a student IS INELIGIBLE if the text names an application or entry \
point in a grade they have ALREADY PASSED. Example: "Students apply during their 8th grade \
year" makes a current 9th (or higher) grader INELIGIBLE — they can no longer apply, even \
though they match the participation grade. Likewise "for current sophomores" excludes a junior \
or senior. This is the ONE grade case that excludes for being too OLD; the rising/age rule \
above only ever KEEPS a younger student and must NEVER rescue a student past a stated entry point.
- RESIDENCY: "Open only to Boston Public Schools students" is a hard gate; "Hosted at \
Northeastern in Boston" is NOT — it says where it runs, not who may apply.
- DEMOGRAPHIC: "Open to female, non-binary, and gender non-conforming students" is a hard gate \
(the named group IS who may apply) — a male student is ineligible even without the word "only". \
But "Students of any gender are welcome; we especially encourage young women" is NOT a gate — \
anyone may apply. Same "women"-framing, opposite verdicts, decided by that one phrase. Soft \
encouragement ("particularly", "especially", "we welcome") NEVER excludes.
- CITIZENSHIP: "Applicants must be U.S. citizens or permanent residents" excludes a non-citizen.
- Judge each restriction ONLY against a fact the student actually stated (grade, location, and \
the attributes they volunteered). If you would need an attribute the student did not provide \
(e.g. citizenship, gender) to decide, mark them ELIGIBLE — never assume a sensitive attribute.
- For every candidate you mark ineligible you MUST supply the exact sentence, verbatim, from \
that candidate's own text that states the restriction, and name which field it is in \
("eligibility", "summary", "name", or "org"). If you cannot quote it verbatim, do not exclude — \
mark them eligible.

Respond with ONLY raw JSON, no markdown, no preamble, matching:
{"verdicts":[{"id":"...","eligible":true,"exclusion_quote":null,"exclusion_source_field":null},\
{"id":"...","eligible":false,"exclusion_quote":"verbatim sentence","exclusion_source_field":"eligibility|summary|name|org"}]}
Return one verdict for EVERY candidate you were given, in any order. Default to eligible:true \
whenever the text does not clearly exclude this student."""
