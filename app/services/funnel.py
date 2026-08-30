"""The progressive elicitation funnel — one rung at a time (OPPORTUNITY_MATCHING_PLAN.md,
Phase 4 + the per-rung contract in Phase 1).

Split cleanly into:
  * PURE, offline-testable logic (this is most of the file): the decision-axis whitelist that
    enforces T1/T2, and apply_rung_answer() which turns a rung's returned classification +
    the student's answer into a deterministically narrowed pool + live counts — NO second
    model call to apply an answer, and the counter can never disagree with the actual cut.
  * The FUNNEL_QUESTION_SYSTEM prompt (M8) the per-rung call uses. The call itself (build
    payload -> call_gemini -> parse) lives in the route/orchestration; kept out of here so the
    pure logic stays testable.

The three traps, enforced in CODE not prompt:
  T1 — a question may CUT only on a genuine constraint. Preference axes are simply ABSENT from
       FUNNEL_AXES, so apply_rung_answer physically cannot cut on one (it raises on an
       unknown/preference axis). Preferences rank later, in curation — never here.
  T2 — questions come only from the whitelist. apply_rung_answer rejects any axis not in
       FUNNEL_AXES.
  T3 — never a dead end. apply_rung_answer reports the post-cut count and whether it would
       collapse below POOL_FLOOR, so the caller offers "relax?" instead of walling the student.
"""
from __future__ import annotations

from app.services.eligibility import verify_exclusion_quote

# --------------------------------------------------------------------------- the whitelist
#
# The ONLY axes the funnel may ask a cutting question about. Grade + location are resolved up
# front (Phase 2's first-search ask) and are not funnel rungs; residency is DERIVED from the
# stored location, not asked. Everything here is a session-only filter (never persisted). No
# preference axis appears — that is T1, enforced by absence.
#
#   requires_quote: True  -> a CUT is an eligibility-text judgment ("US citizens only") and
#                            must carry a verbatim quote that verifies against the row, exactly
#                            like the curation guard. An unverifiable cut reverts to KEEP.
#   requires_quote: False -> a CUT is a structured comparison of the student's declared
#                            constraint against a structured field (budget vs price, availability
#                            vs season). No quote to verify; the model's disposition stands.
FUNNEL_AXES = {
    "cost":             {"requires_quote": False},
    "time_commitment":  {"requires_quote": False},
    "citizenship":      {"requires_quote": True},
    "hard_demographic": {"requires_quote": True},
}

# Below this many survivors, the funnel must stop / offer to relax rather than cut further —
# a tight list is the goal, an empty one is a dead end (T3).
POOL_FLOOR = 5


class FunnelAxisError(ValueError):
    """Raised when a rung names an axis outside FUNNEL_AXES — a T1/T2 violation. Fail closed:
    a malformed or preference-axis rung must never silently cut the pool."""


def _disposition(entry: dict | None, chosen_value: str) -> str:
    """The rung's disposition for one candidate under the chosen answer. Missing entry or
    missing option defaults to KEEP — the safe direction (never cut on absence)."""
    if not entry:
        return "keep"
    per_option = entry.get("per_option") or {}
    disp = per_option.get(chosen_value, "keep")
    return disp if disp in ("cut", "keep", "caveat") else "keep"


def apply_rung_answer(pool: list[dict], rung: dict, chosen_value: str) -> dict:
    """Deterministically narrow `pool` by the student's answer to one rung.

    `rung` shape (produced by the funnel-question model call):
      {
        "axis": "cost" | "citizenship" | ...,          # must be in FUNNEL_AXES
        "classification": {
          "<candidate_id>": {
            "per_option": {"<option_value>": "cut"|"keep"|"caveat", ...},
            "quote": "<verbatim restriction text>",     # only for requires_quote axes + a cut
            "source_field": "eligibility"|"summary"|...  # which field the quote is from
          }, ...
        }
      }

    Returns:
      {
        "narrowed":   [rows kept],          # caveat rows are KEPT (shown with a flag)
        "cut_ids":    [ids removed],
        "caveat_ids": [ids kept-with-caveat],
        "reverted_ids": [ids whose CUT was dropped because its quote didn't verify],
        "count":      len(narrowed),
        "would_collapse": bool,             # count < POOL_FLOOR -> caller should offer relax (T3)
      }

    Raises FunnelAxisError if the axis is not whitelisted (T1/T2)."""
    axis = rung.get("axis")
    if axis not in FUNNEL_AXES:
        raise FunnelAxisError(f"axis {axis!r} is not a whitelisted funnel filter axis")
    requires_quote = FUNNEL_AXES[axis]["requires_quote"]
    classification = rung.get("classification") or {}

    narrowed, cut_ids, caveat_ids, reverted_ids = [], [], [], []
    for cand in pool:
        cid = cand.get("id")
        entry = classification.get(cid)
        disp = _disposition(entry, chosen_value)

        if disp == "cut":
            if requires_quote:
                quote = (entry or {}).get("quote")
                source_field = (entry or {}).get("source_field")
                if verify_exclusion_quote(cand, quote, source_field):
                    cut_ids.append(cid)          # verified eligibility cut stands
                    continue
                reverted_ids.append(cid)         # unverifiable cut -> keep (unknown != ineligible)
                narrowed.append(cand)
                continue
            cut_ids.append(cid)                  # structured cut (budget/availability) stands
            continue

        if disp == "caveat":
            caveat_ids.append(cid)
        narrowed.append(cand)

    return {
        "narrowed": narrowed,
        "cut_ids": cut_ids,
        "caveat_ids": caveat_ids,
        "reverted_ids": reverted_ids,
        "count": len(narrowed),
        "would_collapse": len(narrowed) < POOL_FLOOR,
    }


def count_after(pool: list[dict], rung: dict, chosen_value: str) -> int:
    """The live "matches" count the UX shows the instant an answer is tapped — free
    arithmetic over the returned classification, no model call. Same result apply_rung_answer
    would produce, so the counter can never disagree with the actual cut."""
    return apply_rung_answer(pool, rung, chosen_value)["count"]


# --------------------------------------------------------------------------- the prompt (M8)
#
# One call PER RUNG. Sees the pool as narrowed by every prior answer + the student blob, and
# returns the single next most-discriminating question OR a signal to stop. Adaptive and
# sequential by construction — never one call producing the whole question set.
FUNNEL_QUESTION_SYSTEM = """You are Wingman, narrowing a high schooler's list of extracurricular \
opportunities by asking the single most useful next question. You are given the student's profile \
and the CURRENT candidate pool (already narrowed by any earlier answers). Decide the ONE question \
that would most usefully narrow THIS specific pool — or say there is nothing left worth asking.

You may ONLY ask about one of these axes (nothing else, ever):
- "cost": the student's hard budget for a program (some cost money).
- "time_commitment": when the student is actually available (e.g. summer-only vs school-year).
- "citizenship": a citizenship/residency requirement some programs state.
- "hard_demographic": a program open ONLY to a specific group the student may not be in \
(e.g. "female-identifying and non-binary students"). Ask ONLY when a program in the pool truly \
restricts this way — never as a demographic survey.

RULES:
- Ask an axis ONLY if the answer would actually change the pool. If every candidate agrees on it \
(all are free, all are remote), it is useless — do not ask it.
- Do NOT ask anything the student's profile already states.
- Do NOT ask about subject, interest, activity type, work style, or fit — those decide RANKING \
later, not who is cut, and are never funnel questions.
- If no whitelisted axis meaningfully splits the pool, return {"axis": null} to stop.

For the axis you choose, classify EVERY candidate under EACH answer option: "cut" (this answer \
makes them ineligible), "keep" (unaffected), or "caveat" (shown but flagged). For a "citizenship" \
or "hard_demographic" cut you MUST also supply the verbatim sentence from that candidate's own \
text that states the restriction, and name which field it is in — a cut with no real quote will be \
dropped. For "cost"/"time_commitment" no quote is needed (it is a structured comparison).

Worked examples of the eligibility distinction (read the WHOLE sentence):
- "Open to female, non-binary, and gender non-conforming students" -> hard_demographic restriction \
(the named group IS who may apply; no word "only" needed).
- "Students of any gender are welcome; we especially encourage young women to apply" -> NOT a \
restriction; anyone may apply. Do not cut.
- "Hosted at Northeastern University in Boston" -> says where it RUNS, not who may apply. Not a \
citizenship/residency cut.

Respond with ONLY raw JSON, no markdown, no preamble, matching:
{"axis":"cost|time_commitment|citizenship|hard_demographic"|null,"question":"one short question",\
"rationale":"why this splits the pool","options":[{"label":"...","value":"..."}],\
"classification":{"<id>":{"per_option":{"<value>":"cut|keep|caveat"},"quote":"...or omit",\
"source_field":"eligibility|summary|name|org or omit"}}}"""
