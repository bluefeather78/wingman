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
    # "What do you enjoy doing" — a FILTER on the pool's own `type` distribution (structured,
    # no quote). Options are pool-derived and classification is computed LOCALLY (see
    # build_engagement_rung), never by the model — exact counts, no truncation risk.
    "engagement":       {"requires_quote": False},
}

# Below this many survivors, the funnel must stop / offer to relax rather than cut further —
# a tight list is the goal, an empty one is a dead end (T3).
POOL_FLOOR = 5

# Stop asking and hand off to curation once the pool is this small — a tight pool is what
# curation is for, and more questions past here are the "10 feasible-but-mediocre" trap.
CURATE_AT = 15
# A longer funnel is a chore (and a latency/cost budget — each rung is a model call).
MAX_RUNGS = 5

# Output-token budget for the funnel-QUESTION call. It classifies every candidate in the pool
# (up to RECALL_POOL_SIZE), so its JSON is large: a full ~100-row classification measures ~3k
# output tokens. The generic 2000 default truncated it — the parse then failed and the funnel
# silently skipped every filter question. 8000 gives headroom for the biggest (rung-0) pool
# plus Gemini 3.x thinking tokens (which draw from the same budget). Billing is on tokens
# produced, so unused headroom is free; a correct filter rung genuinely needs ~3k.
FUNNEL_MAX_TOKENS = 8000

# Fields the funnel-question model sees per candidate — enough to classify the whitelisted
# axes (cost/time/citizenship/demographic), no more. Mirrors the curation view but trimmed.
FUNNEL_CANDIDATE_FIELDS = (
    "id", "name", "org", "type", "summary", "eligibility",
    "price", "location", "state", "season",
)


def build_funnel_candidate_view(row: dict) -> dict:
    return {k: row.get(k) for k in FUNNEL_CANDIDATE_FIELDS}


def build_funnel_user_content(student: dict, pool: list[dict]) -> str:
    """The user-message payload for one funnel-question rung: the student blob + the CURRENT
    pool (already narrowed by prior answers). Pure string assembly; system is
    FUNNEL_QUESTION_SYSTEM."""
    import json
    views = [build_funnel_candidate_view(r) for r in pool]
    return (
        "STUDENT PROFILE (JSON):\n" + json.dumps(student, ensure_ascii=False)
        + "\n\nCURRENT CANDIDATE POOL (JSON):\n" + json.dumps(views, ensure_ascii=False)
        + "\n\nDecide the single next question (or {\"axis\": null} to stop) per the schema."
    )


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


def sanitize_rung(pool: list[dict], rung: dict) -> dict:
    """Apply the quote guard to a rung's classification ONCE, server-side, before it is sent to
    the client. For a quote-required axis (citizenship / hard_demographic), any candidate whose
    "cut" is not backed by a verbatim quote that verifies against its own text has its cuts
    downgraded to "keep" (unknown != ineligible), and is flagged `quote_reverted`.

    This is what lets the client narrow the pool naively (keep every candidate not marked
    "cut") and still be safe: the load-bearing guard already ran here. Structured axes
    (cost / time_commitment) need no quote, so their classification passes through unchanged.
    Raises FunnelAxisError for a non-whitelisted axis (T1/T2), same as apply_rung_answer."""
    axis = rung.get("axis")
    if axis not in FUNNEL_AXES:
        raise FunnelAxisError(f"axis {axis!r} is not a whitelisted funnel filter axis")
    if not FUNNEL_AXES[axis]["requires_quote"]:
        return rung
    classification = rung.get("classification") or {}
    by_id = {c.get("id"): c for c in pool}
    sanitized: dict = {}
    for cid, entry in classification.items():
        per_option = (entry or {}).get("per_option") or {}
        if any(d == "cut" for d in per_option.values()):
            cand = by_id.get(cid)
            quote = (entry or {}).get("quote")
            source_field = (entry or {}).get("source_field")
            if cand is None or not verify_exclusion_quote(cand, quote, source_field):
                per_option = {v: ("keep" if d == "cut" else d) for v, d in per_option.items()}
                sanitized[cid] = {**(entry or {}), "per_option": per_option, "quote_reverted": True}
                continue
        sanitized[cid] = entry
    return {**rung, "classification": sanitized}


def option_counts(pool: list[dict], rung: dict) -> dict:
    """Per-option surviving count for the live counter — {option_value: how many candidates
    would REMAIN if the student picked that option}. Computed over the (already sanitized)
    classification so the number the UI shows beside an option is exactly what the client's
    naive narrowing will produce. No model call."""
    classification = rung.get("classification") or {}
    counts: dict = {}
    for opt in rung.get("options") or []:
        value = opt.get("value")
        counts[value] = sum(
            1 for c in pool if _disposition(classification.get(c.get("id")), value) != "cut"
        )
    return counts


# ==================== ENGAGEMENT (dimension 2) — a pool-derived FILTER on `type` ====================
#
# "What do you enjoy doing?" (Shama 2026-08-30). A FILTER whose options are the opportunity
# TYPES actually present in the current pool, framed in enjoyment language and computed LOCALLY
# (exact per-type counts, no model call, no truncation). It also carries a free-form "Something
# else" escape that does NOT cut — its text becomes a curation rerank preference instead
# (handled in the route via collect_preferences, same as a vibe answer).
ENGAGEMENT_LABEL = {
    "Competition": "Competing head-to-head",
    "Academic Competition": "Competing head-to-head",
    "Research Competition": "Competing with a research project",
    "Internship": "Working somewhere real",
    "Research": "Doing hands-on research",
    "Summer Program": "An immersive program",
    "Program": "An immersive program",
    "Conference": "Events & networking",
    "Journal": "Publishing your work",
    "Volunteering": "Helping your community",
}
# The free-text escape's value is prefixed with this; the route folds "<sentinel><text>" into
# curation preferences and the client's naive narrowing keeps every candidate (no classification
# entry for it -> default keep). Kept distinct from the vibe axes so it reads as engagement.
ENGAGEMENT_OTHER = "__other__:"


def build_engagement_rung(pool: list[dict]) -> dict | None:
    """A pool-derived engagement FILTER rung, or None when there's nothing to split on (fewer
    than two distinct types present). Classification is LOCAL: a candidate keeps under the option
    for its own type and is cut under every other. Counts are attached from that classification."""
    from collections import Counter
    counts = Counter(str(r.get("type")).strip() for r in pool if r.get("type") and str(r.get("type")).strip())
    types = [t for t, _ in counts.most_common() if t and t.lower() != "none"]
    if len(types) < 2:
        return None
    classification = {}
    for r in pool:
        rt = str(r.get("type")).strip()
        classification[r.get("id")] = {"per_option": {t: ("keep" if rt == t else "cut") for t in types}}
    options = [{"label": ENGAGEMENT_LABEL.get(t, t), "value": t} for t in types]
    rung = {
        "axis": "engagement", "kind": "filter",
        "question": "What kind of experience are you most excited about?",
        "rationale": None,
        "options": options,
        "classification": classification,
        "pool_ids": [r.get("id") for r in pool],
        "allow_other": True,   # the UI shows a "Something else…" free-text escape (reranks, no cut)
    }
    counts_by_opt = option_counts(pool, rung)
    rung["options"] = [{**o, "count": counts_by_opt.get(o["value"])} for o in options]
    return rung


# ==================== BEHAVIORAL (vibe) rungs — rerank-only, never filter ====================
#
# Ported from the opportunity-matching branch's adaptiveFunnel.ts (Shama-approved M8), moved
# SERVER-SIDE because this branch's backend owns the funnel. A vibe axis has no per-row catalog
# data: it NEVER filters and NEVER moves the count. Its answer becomes a soft PREFERENCE phrase
# (behavioral_pref) folded into the student blob and handed to curation to RE-RANK. Asked only
# after the filter axes are exhausted and while the pool is still larger than CURATE_AT.
#
# Each axis: a blurb (for the model) and its TWO options — the app-owned `value`, the `pref`
# phrase curation reads, and an `example` label (local-fallback wording + a voice hint). "No
# preference" is a third choice the UI renders; it emits no phrase, just marks the axis asked.
BEHAVIORAL_AXES = {
    "selectivity": {"blurb": "how competitive / selective the program is to get into", "opts": [
        {"value": "competitive", "pref": "Leans toward selective, competitive programs", "example": "Bring it on"},
        {"value": "open", "pref": "Prefers open-access, low-pressure programs", "example": "Easy in"},
    ]},
    "residential": {"blurb": "living away from home vs staying local or online", "opts": [
        {"value": "away", "pref": "Prefers residential / live-away-from-home experiences", "example": "Somewhere new"},
        {"value": "home", "pref": "Prefers staying local or online, at home", "example": "Stay home"},
    ]},
    "collaboration": {"blurb": "working on a team vs solo", "opts": [
        {"value": "team", "pref": "Prefers team / cohort settings", "example": "Squad up"},
        {"value": "solo", "pref": "Prefers working independently", "example": "Just me"},
    ]},
    "structure": {"blurb": "a set curriculum vs open-ended self-direction", "opts": [
        {"value": "guided", "pref": "Prefers structured, guided programs", "example": "Set plan"},
        {"value": "freestyle", "pref": "Prefers open-ended, self-directed work", "example": "Freestyle"},
    ]},
    "intensity": {"blurb": "a full-time immersive commitment vs a light one", "opts": [
        {"value": "immersive", "pref": "Prefers full-time, immersive commitments", "example": "All in"},
        {"value": "light", "pref": "Prefers a light, few-hours-a-week commitment", "example": "Keep it light"},
    ]},
    "output": {"blurb": "producing something tangible (paper, project, award) vs exploring and learning", "opts": [
        {"value": "output", "pref": "Wants to produce something tangible (paper, project, award)", "example": "Make something"},
        {"value": "explore", "pref": "Here to explore and learn, no deliverable needed", "example": "Just explore"},
    ]},
}

# Local-fallback question wording per axis (used when the model output is unusable), ported verbatim.
_BEHAVIORAL_FALLBACK_Q = {
    "selectivity": "Fight to get in, or an easy yes?",
    "residential": "Somewhere new, or your own bed?",
    "collaboration": "Team sport, or solo mission?",
    "structure": "A set plan, or freestyle it?",
    "intensity": "All in, or keep it light?",
    "output": "Make something, or just soak it up?",
}


def behavioral_pref(axis: str, value: str) -> str:
    """The rerank phrase for a vibe answer (empty for 'no preference'/unknown — no signal)."""
    for o in BEHAVIORAL_AXES.get(axis, {}).get("opts", []):
        if o["value"] == value:
            return o["pref"]
    return ""


def collect_preferences(funnel_answers: dict | None) -> list[str]:
    """The soft preference phrases handed to curation to rerank: every vibe-axis answer, plus the
    free-text "Something else" escape on the engagement filter. Skipped/unknown answers add
    nothing. (Defined here because ENGAGEMENT_OTHER lives above; BEHAVIORAL_AXES is defined
    below and read at call time, not import time.)"""
    prefs = []
    for axis, val in (funnel_answers or {}).items():
        v = str(val)
        if axis in BEHAVIORAL_AXES:
            phrase = behavioral_pref(axis, v)
            if phrase:
                prefs.append(phrase)
        elif axis == "engagement" and v.startswith(ENGAGEMENT_OTHER):
            text = v[len(ENGAGEMENT_OTHER):].strip()
            if text:
                prefs.append(f"Enjoys: {text}")
    return prefs


def build_behavioral_user_content(pool: list[dict], remaining_axes: list[str]) -> str:
    """User payload for a vibe-question rung: the remaining axes (with options) + a sample of the
    program names still on the list, so the model picks the axis that best fits them."""
    axis_lines = "\n".join(
        f"- {a}: {BEHAVIORAL_AXES[a]['blurb']}. options: "
        + ", ".join(f'"{o["value"]}" (e.g. "{o["example"]}")' for o in BEHAVIORAL_AXES[a]["opts"])
        for a in remaining_axes
    )
    samples = "; ".join(str(r.get("name")) for r in pool[:14] if r.get("name"))
    return (f"Vibe axes:\n{axis_lines}\n\nSample programs still on the list: {samples}"
            f"\n\nDesign the single best vibe question.")


def _local_vibe_rung(axis: str, pool: list[dict]) -> dict:
    """A locally-worded vibe rung (no model), used as the fallback and when the model output is bad."""
    opts = BEHAVIORAL_AXES[axis]["opts"]
    return {
        "axis": axis, "kind": "vibe",
        "question": _BEHAVIORAL_FALLBACK_Q[axis], "rationale": None,
        "options": [{"label": o["example"], "value": o["value"]} for o in opts],
        "classification": {},                       # empty -> every candidate KEEPS under every option
        "pool_ids": [c.get("id") for c in pool],
    }


def build_vibe_rung(parsed: dict | None, axis: str, pool: list[dict]) -> dict:
    """Turn the model's vibe-question output into a rung. The model may only PHRASE the question
    and LABEL the axis's two (fixed) options — it cannot add, drop, or invent an option. Any
    deviation falls back to the local wording. A vibe rung never filters: its classification is
    empty, so the client's naive narrowing keeps every candidate."""
    opts = BEHAVIORAL_AXES[axis]["opts"]
    if not isinstance(parsed, dict) or not isinstance(parsed.get("question"), str) or not parsed["question"].strip():
        return _local_vibe_rung(axis, pool)
    raw_opts = parsed.get("options") if isinstance(parsed.get("options"), list) else []
    label_by_value = {o.get("value"): o.get("label") for o in raw_opts if isinstance(o, dict)}
    options = []
    for o in opts:
        lbl = label_by_value.get(o["value"])
        options.append({"label": lbl if isinstance(lbl, str) and lbl.strip() else o["example"], "value": o["value"]})
    return {
        "axis": axis, "kind": "vibe",
        "question": parsed["question"].strip(), "rationale": None,
        "options": options, "classification": {},
        "pool_ids": [c.get("id") for c in pool],
    }


def next_vibe_rung(pool, student, funnel_answers, ask_fn, parse_fn, rungs_done=0):
    """Decide the next VIBE question over the current pool, or None to stop and curate.

    Returns None when the rung cap is reached, the pool is already small enough to curate, or
    every vibe axis has been asked. Otherwise asks the model to pick + phrase one of the
    remaining axes (falling back to local wording on any bad output) and returns a rerank-only
    rung. Injected: ask_fn(system, user_content) -> raw_text ; parse_fn(raw_text) -> dict|None."""
    if rungs_done >= MAX_RUNGS or len(pool) <= CURATE_AT:
        return None
    remaining = [a for a in BEHAVIORAL_AXES if a not in (funnel_answers or {})]
    if not remaining:
        return None
    raw = ask_fn(BEHAVIORAL_QUESTION_SYSTEM, build_behavioral_user_content(pool, remaining))
    try:
        parsed = parse_fn(raw) if raw else None
    except Exception:
        parsed = None
    axis = parsed.get("axis") if isinstance(parsed, dict) else None
    if axis not in remaining:
        axis = remaining[0]           # model picked an already-asked/invalid axis -> first remaining
    return build_vibe_rung(parsed, axis, pool)


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


# The vibe-question prompt (M8). Ported VERBATIM from the opportunity-matching branch's
# adaptiveFunnel.ts designBehavioralQuestion (VOICE + system), Shama-approved 2026-08-29. The
# model may only PICK one of the given axes and PHRASE it + label its two fixed options; the
# server validates the choice and falls back to local wording on any mismatch.
_BEHAVIORAL_VOICE = (
    'Voice: warm, casual and a little playful — like a friend who gets you, not a form. '
    'Write ONE natural sentence (roughly 10-18 words) that sets up the choice with a bit of context or a scenario, so it reads as a real question rather than a label. '
    'Do NOT just restate the two options, and do NOT use the word "vibe". Second person. '
    'GOOD: "When you picture this, are you living somewhere new for a while, or staying close to home?" '
    'GOOD: "Would you rather earn a spot in something selective, or keep it low-key and open to anyone?" '
    'GOOD (filter): "Would you rather be there in person, or join in from wherever you already are?" '
    'BAD (just repeats the options): "Team or solo?". BAD (too formal): "Which type of environment do you prefer?". BAD (forced slang): "Yo, you tryna grind fr fr?". '
    'The option LABELS carry the SAME casual voice — short (1-3 words), concrete and human, never a bare category word. '
    'GOOD labels: "In the room" / "From my couch" / "Squad up" / "Just me" / "This summer" / "All year". '
    'BAD labels: "In-Person" / "Remote" / "Team" / "Solo" / "Summer".'
)

BEHAVIORAL_QUESTION_SYSTEM = (
    'You write ONE friendly question to learn what a high-schooler is looking for in an extracurricular — a preference we cannot read from data. '
    'You are given candidate AXES (each such a preference) with two options each. Pick the ONE axis that best fits the programs still on their list, then write the question and a punchy 1-3 word LABEL for each of that axis\'s two options. '
    + _BEHAVIORAL_VOICE + ' '
    + 'Rules: (1) pick an axis from the given list ONLY, by its exact key; (2) return BOTH of that axis\'s options, exact strings in `value`, each with a short natural `label`; (3) never add, drop, or invent an option or axis. '
    'Respond with ONLY raw JSON: {"axis":"<key>","question":"...","options":[{"value":"<exact>","label":"..."}]}'
)
