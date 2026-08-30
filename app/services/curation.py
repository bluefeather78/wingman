"""The final cross-kind curation pass (OPPORTUNITY_MATCHING_PLAN.md, Phase 3 / the second
live call of Phase 1). ONE call, at the end: the funnel's survivors + the student blob in,
the curated <=10 out, each with a "why you" reason, a tier, and an eligibility verdict.

This replaces the 7-kind fan-out (40-70 rows) with a single pass that selects the best <=10
OVERALL — fit + feasibility + a couple of deliberate exploration slots.

Split like the rest of the pipeline: the PURE, testable pieces (build the user payload,
finalize the model's output through the eligibility guard) live here and are unit-tested; the
model CALL (call_gemini) lives in the route/orchestration.
"""
from __future__ import annotations

from app.services.eligibility import apply_eligibility_verdict

# Fields sent per candidate — the full blob Phase 1 reasons over, including `eligibility`
# (the whole point of the redesign) and the trust signal, but not `url` (no fit value).
CURATION_CANDIDATE_FIELDS = (
    "id", "name", "org", "type", "summary", "eligibility",
    "subject_tags", "price", "location", "state", "intl", "season",
    "review_status", "review_summary", "grade_min", "grade_max",
)

CURATED_LIMIT = 10


def build_candidate_view(row: dict) -> dict:
    """The compact-but-complete candidate shape the curation model sees (one per survivor)."""
    return {k: row.get(k) for k in CURATION_CANDIDATE_FIELDS}


def build_curation_user_content(student: dict, candidate_views: list[dict]) -> str:
    """The user-message payload for the curation call: the student blob + the candidate JSON.
    Pure string assembly (the system prompt is CURATION_SYSTEM). `student` is the Phase-2
    blob (grade, location, profile_themes, highlight_projects, funnel_answers), plus the
    optional `preferences` list — soft vibe phrases from the funnel that RE-RANK only."""
    import json
    prefs = [p for p in (student.get("preferences") or []) if isinstance(p, str) and p.strip()]
    # Surfaced as an explicit line (ported from the opportunity-matching curate prompt's
    # "What matters to them right now") so the model weights them in ordering, never as a filter.
    pref_text = ("\n\nWHAT MATTERS TO THEM RIGHT NOW (soft preferences — use to ORDER, never to exclude):\n"
                 + "; ".join(prefs)) if prefs else ""
    return (
        "STUDENT PROFILE (JSON):\n" + json.dumps(student, ensure_ascii=False)
        + pref_text
        + "\n\nCANDIDATE OPPORTUNITIES (JSON):\n" + json.dumps(candidate_views, ensure_ascii=False)
        + "\n\nSelect and rank the curated shortlist per the schema."
    )


def finalize_curation(parsed: dict, rows_by_id: dict[str, dict], limit: int = CURATED_LIMIT) -> dict:
    """Turn the raw curation output into the trusted final list, applying the eligibility guard.

    `parsed` shape (from the model):
      {
        "selected": [ {"id","reason","tier":"strong|look","exploration_pick":bool,
                       "eligible":true, "exclusion_quote":null, "exclusion_source_field":null}, ... ],
        "excluded_ineligible": [ {"id","eligible":false,"exclusion_quote","exclusion_source_field"}, ... ]
      }

    Returns:
      {
        "results":  [ selected picks that survive the guard, capped at `limit`, order preserved ],
        "rescued":  [ ids the model excluded for eligibility whose quote did NOT verify — they
                      should NOT have been hidden; surfaced for the eval / a follow-up re-rank ],
        "guard_overrode_count": int,   # the production signal to watch
      }

    The guard here only ever makes the list MORE inclusive: a `selected` pick the model marked
    ineligible-but-unverified is kept; a genuinely-verified ineligible pick is dropped from the
    results (it should not have been selected, but defense in depth). `excluded_ineligible`
    entries whose quote fails to verify are reported as `rescued` rather than silently lost."""
    selected = parsed.get("selected") or []
    excluded = parsed.get("excluded_ineligible") or []

    results = []
    for pick in selected:
        row = rows_by_id.get(pick.get("id"))
        if row is None:
            continue  # model hallucinated an id not in the pool — drop it
        verdict = apply_eligibility_verdict(row, pick)
        if not verdict["eligible"]:
            continue  # verified-ineligible pick — do not show it
        results.append({
            "id": pick.get("id"),
            "reason": pick.get("reason"),
            "tier": pick.get("tier"),
            "exploration_pick": bool(pick.get("exploration_pick")),
        })
        if len(results) >= limit:
            break

    rescued, overrode = [], 0
    for ex in excluded:
        row = rows_by_id.get(ex.get("id"))
        if row is None:
            continue
        verdict = apply_eligibility_verdict(row, ex)
        if verdict["guard_overrode"]:
            overrode += 1
            rescued.append(ex.get("id"))

    return {"results": results, "rescued": rescued, "guard_overrode_count": overrode}


# --------------------------------------------------------------------------- the prompt (M8)
CURATION_SYSTEM = """You are Wingman, building a high schooler's CURATED shortlist of at most 10 \
extracurricular opportunities — each one a genuinely great fit they can actually do. You are given \
the student's profile and a pool of candidate opportunities. Your job has two parts.

PART 1 — ELIGIBILITY. Read only what each candidate's own text says; never guess, never use outside \
knowledge. A candidate is INELIGIBLE only if its text states a real restriction this student fails. \
If the text is silent or unclear, treat them as ELIGIBLE — wrongly hiding a real match is worse than \
including a long shot.
- GRADE: "rising Nth grader" means a student CURRENTLY FINISHING grade N-1. A grade-9 student is \
eligible for "rising 10th graders". Do not exclude on a numeric grade range if the wording is \
"rising"/age-based.
- RESIDENCY: "Open only to Boston Public Schools students" is a hard gate; "Hosted at Northeastern \
in Boston" is NOT — it says where it runs, not who may apply.
- DEMOGRAPHIC: "Open to female, non-binary, and gender non-conforming students" is a hard gate (the \
named group IS who may apply). "Students of any gender are welcome; we especially encourage young \
women" is NOT — anyone may apply.
- For every candidate you mark ineligible you MUST supply the exact sentence, verbatim, from that \
candidate's own text that states the restriction, and name which field it is in ("eligibility", \
"summary", "name", or "org"). If you cannot quote it verbatim, do not exclude — mark them eligible.

PART 2 — FIT. Among the eligible candidates, pick the best <=10. Most slots go to the strongest, \
most SPECIFIC fits to this student's stated interests, goals, projects, AND their stated preferences \
(the "what matters to them right now" list, when present — use it to ORDER and break ties, never to \
exclude a candidate). \
Each reason is the WOW moment — it should make this student feel genuinely SEEN, like a mentor \
who knows BOTH them and this program picked it just for them, and give them enough to think "that \
is an amazing fit for me." Draw a concrete line connecting TWO specific halves: (1) a SPECIFIC \
thing about THEM — a project, skill, achievement, goal, or a choice they made this search (what \
they enjoy doing, what they want out of it, their budget or timing) — and (2) a SPECIFIC thing \
THIS program actually offers, from the candidate's own text — what they would build, do, compete \
in, publish, or walk away with. The more precisely those two halves lock together, the better. \
Write 1-3 sentences (roughly 25-55 words) — long enough to be truly convincing and to earn their \
trust, but every clause must carry real information, never filler. \
GOOD (both halves specific, ties their choice): "You built a computer-vision model to auto-referee \
robotics matches — here you'd take that further on a team designing fully autonomous robots and \
competing head-to-head, exactly the build-and-win challenge you're after, and it runs free over \
the summer." \
GOOD: "Your research on low-resource languages is exactly what this olympiad rewards: you'd crack \
original computational-linguistics problems against the country's strongest, the kind of \
recognition you told us you want." \
BAD (thin, could be anyone): "Great fit for your robotics interest." \
BAD (vague filler): "A wonderful opportunity to learn and grow your skills." \
BAD (invented — the candidate's text never said it): naming a mentor, prize, cohort size, or \
feature that is not in that candidate's own text. \
Use ONLY real details from the student's profile/choices and the candidate's OWN text — never \
invent a program feature or a student detail, and never assert something the text does not \
support. Second person ("you"/"your"). Do NOT state or infer any eligibility, grade, age, or \
citizenship restriction. \
Reserve 2-3 slots as deliberate EXPLORATION picks: a real stretch outside the student's usual lane \
that is still excellent AND still feasible — mark those exploration_pick:true, and in the reason \
name the leap honestly and why they'd shine anyway ("A new direction from your usual robotics, but \
your data-modeling skills would stand out here"). Assign each a tier: "strong" (excellent, \
specific) or "look" (solid, worth a look).

Respond with ONLY raw JSON, no markdown, no preamble, matching:
{"selected":[{"id":"...","reason":"...","tier":"strong|look","exploration_pick":false,\
"eligible":true,"exclusion_quote":null,"exclusion_source_field":null}],\
"excluded_ineligible":[{"id":"...","eligible":false,"exclusion_quote":"verbatim sentence",\
"exclusion_source_field":"eligibility|summary|name|org"}]}
"selected" is at most 10, best first. "excluded_ineligible" lists candidates you dropped for a \
stated eligibility restriction (with the required quote) — leave it [] if none."""
