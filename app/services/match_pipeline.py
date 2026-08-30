"""End-to-end orchestration of the curated match: recall -> (funnel, later) -> curation.

The PURE core is run_match(): it takes the catalog rows (with embeddings), the student blob,
and two INJECTED effectful functions (embed the student's themes; call the curation model),
and returns the curated <=10 with display fields attached. Injecting the two paid calls keeps
run_match fully unit-testable with stubs — the FastAPI route supplies the real
gemini_common.call_gemini_embed / call_gemini and does the cost banking.

The progressive funnel (Phase 4) is not wired here yet — this is the recall -> curation spine
(Phase 3's curated output model), which the plan sequences to ship before the funnel. When the
funnel lands it narrows `pool` between recall and curation; run_match's shape does not change.
"""
from __future__ import annotations

from app.services.matching import recall, RECALL_POOL_SIZE
from app.services.curation import (
    CURATION_SYSTEM,
    CURATED_LIMIT,
    build_candidate_view,
    build_curation_user_content,
    finalize_curation,
)
from app.services.funnel import (
    FUNNEL_QUESTION_SYSTEM,
    CURATE_AT,
    MAX_RUNGS,
    FunnelAxisError,
    build_funnel_user_content,
    option_counts,
    sanitize_rung,
)

# Display fields attached to each curated result so the client can render a card without a
# second lookup. Mirrors what the old ranked list carried, minus the AI reason (which comes
# from curation) — url included here because the CARD needs it even though the embedding did not.
RESULT_DISPLAY_FIELDS = (
    "id", "name", "org", "type", "summary", "url", "price", "location", "state",
    "season", "subject_tags", "review_status",
)


def theme_embed_text(theme) -> str:
    """The text embedded for one profile theme. A theme is either a bare string or the
    {theme, intent, next_steps} shape the filterTags slot produces; compose the parts so the
    vector captures the interest AND what the student wants from it."""
    if isinstance(theme, str):
        return theme.strip()
    if not isinstance(theme, dict):
        return ""
    parts = [str(theme.get(k, "")).strip() for k in ("theme", "intent", "next_steps")]
    return ". ".join(p for p in parts if p)


def recall_pool(rows, student, embed_themes_fn, recall_limit=RECALL_POOL_SIZE):
    """The recall half: embed the student's themes, run recall. Returns (pool, embed_cost).
    A thin/empty profile (no themes) still gets a filtered, unscored pool — recall's contract."""
    themes = student.get("profile_themes") or []
    theme_texts = [t for t in (theme_embed_text(x) for x in themes) if t]
    theme_vecs, embed_cost = ([], 0.0)
    if theme_texts:
        theme_vecs, embed_cost = embed_themes_fn(theme_texts)
    location = student.get("location") or {}
    state = location.get("state") if isinstance(location, dict) else None
    pool = recall(rows, theme_vecs, student_grade=student.get("grade"),
                  student_state=state, limit=recall_limit)
    return pool, embed_cost


def curate_pool(pool, student, curate_fn, parse_fn, curated_limit=CURATED_LIMIT):
    """The curation half: curate a (recall- or funnel-narrowed) pool into the final <=10, with
    display fields attached and the eligibility guard applied via finalize_curation. Injected:
      curate_fn(system, user_content) -> raw_text ; parse_fn(raw_text) -> dict|None."""
    if not pool:
        return {"results": [], "pool_size": 0, "rescued": [], "guard_overrode_count": 0,
                "note": "no candidates matched"}
    rows_by_id = {r.get("id"): r for r in pool}
    candidate_views = [build_candidate_view(r) for r in pool]
    user_content = build_curation_user_content(student, candidate_views)
    raw = curate_fn(CURATION_SYSTEM, user_content)
    try:
        parsed = parse_fn(raw) if raw else None
    except Exception:
        parsed = None  # a parser that raises on garbage is treated as unparseable, not a crash
    if not isinstance(parsed, dict):
        # Curation unreadable — fall back to pool order so the student still gets a list,
        # flagged so the UI (and the eval) can tell a curated list from a degraded one.
        results = [
            {**{k: r.get(k) for k in RESULT_DISPLAY_FIELDS}, "reason": None, "tier": "look",
             "exploration_pick": False}
            for r in pool[:curated_limit]
        ]
        return {"results": results, "pool_size": len(pool), "rescued": [],
                "guard_overrode_count": 0,
                "note": "curation unavailable — showing top matches by relevance"}
    final = finalize_curation(parsed, rows_by_id, limit=curated_limit)
    results = []
    for pick in final["results"]:
        row = rows_by_id.get(pick["id"])
        if row is None:
            continue
        results.append({
            **{k: row.get(k) for k in RESULT_DISPLAY_FIELDS},
            "reason": pick.get("reason"),
            "tier": pick.get("tier"),
            "exploration_pick": pick.get("exploration_pick", False),
        })
    return {"results": results, "pool_size": len(pool), "rescued": final["rescued"],
            "guard_overrode_count": final["guard_overrode_count"], "note": None}


def run_match(
    rows: list[dict],
    student: dict,
    embed_themes_fn,
    curate_fn,
    parse_fn,
    recall_limit: int = RECALL_POOL_SIZE,
    curated_limit: int = CURATED_LIMIT,
) -> dict:
    """Recall -> curation (Phase 3, no funnel), returning the curated shortlist + embed cost."""
    pool, embed_cost = recall_pool(rows, student, embed_themes_fn, recall_limit)
    out = curate_pool(pool, student, curate_fn, parse_fn, curated_limit)
    out["embed_cost_usd"] = embed_cost
    return out


def next_funnel_rung(pool, student, ask_fn, parse_fn, rungs_done=0):
    """Decide the next funnel question over the CURRENT pool, or None to stop and curate.

    Returns None when: the pool is already small enough to curate (<= CURATE_AT), the rung cap
    is reached, the model finds nothing worth asking ({"axis": null}), the output is
    unparseable, or the model picks a non-whitelisted axis (fail closed — never cut on it).

    Otherwise returns the rung, quote-sanitized (the guard runs here, server-side, so the
    client can narrow naively), with per-option survivor counts and the current pool ids:
      {"axis", "question", "rationale", "options":[{label,value,count}], "classification",
       "pool_ids":[...]}
    Injected: ask_fn(system, user_content) -> raw_text ; parse_fn(raw_text) -> dict|None."""
    if len(pool) <= CURATE_AT or rungs_done >= MAX_RUNGS:
        return None
    raw = ask_fn(FUNNEL_QUESTION_SYSTEM, build_funnel_user_content(student, pool))
    try:
        parsed = parse_fn(raw) if raw else None
    except Exception:
        parsed = None
    if not isinstance(parsed, dict) or parsed.get("axis") is None:
        return None
    # The model is given the prior answers but sometimes re-asks an axis already answered
    # (observed live: hard_demographic twice), which wastes a rung and stalls the funnel. Guard
    # server-side: an already-answered filter axis means the model has run out of genuinely new
    # ones, so stop the filter phase and hand off (to a vibe question, then curation).
    if parsed.get("axis") in (student.get("funnel_answers") or {}):
        return None
    try:
        sanitized = sanitize_rung(pool, parsed)
    except FunnelAxisError:
        return None  # model picked a non-whitelisted axis -> refuse, hand off to curation
    counts = option_counts(pool, sanitized)
    options = [{**o, "count": counts.get(o.get("value"))} for o in (sanitized.get("options") or [])]
    return {
        "axis": sanitized["axis"],
        "question": sanitized.get("question"),
        "rationale": sanitized.get("rationale"),
        "options": options,
        "classification": sanitized.get("classification") or {},
        "pool_ids": [c.get("id") for c in pool],
    }
