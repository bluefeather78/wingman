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


def run_match(
    rows: list[dict],
    student: dict,
    embed_themes_fn,
    curate_fn,
    parse_fn,
    recall_limit: int = RECALL_POOL_SIZE,
    curated_limit: int = CURATED_LIMIT,
) -> dict:
    """Recall -> curation, returning the curated shortlist.

    Injected effects:
      embed_themes_fn(texts) -> (vectors, cost_usd)          # student theme embeddings
      curate_fn(system, user_content) -> raw_text            # the curation model call
      parse_fn(raw_text) -> dict|None                        # JSON extraction (extract_json)

    Returns:
      {
        "results": [ {display fields..., "reason", "tier", "exploration_pick"} ],  # <= limit
        "pool_size": int,             # how many rows recall produced
        "rescued": [ids],             # curation exclusions the guard reverted
        "guard_overrode_count": int,
        "embed_cost_usd": float,
        "note": str|None,             # set when a fallback path was taken
      }
    """
    themes = student.get("profile_themes") or []
    theme_texts = [t for t in (theme_embed_text(x) for x in themes) if t]
    theme_vecs, embed_cost = ([], 0.0)
    if theme_texts:
        theme_vecs, embed_cost = embed_themes_fn(theme_texts)

    grade = student.get("grade")
    location = student.get("location") or {}
    state = location.get("state") if isinstance(location, dict) else None

    pool = recall(rows, theme_vecs, student_grade=grade, student_state=state, limit=recall_limit)
    if not pool:
        return {"results": [], "pool_size": 0, "rescued": [], "guard_overrode_count": 0,
                "embed_cost_usd": embed_cost, "note": "no candidates matched"}

    rows_by_id = {r.get("id"): r for r in pool}
    candidate_views = [build_candidate_view(r) for r in pool]
    user_content = build_curation_user_content(student, candidate_views)

    raw = curate_fn(CURATION_SYSTEM, user_content)
    try:
        parsed = parse_fn(raw) if raw else None
    except Exception:
        parsed = None  # a parser that raises on garbage is treated as unparseable, not a crash
    if not isinstance(parsed, dict):
        # Curation unreadable — fall back to recall order so the student still gets a list,
        # flagged so the UI (and the eval) can tell a curated list from a degraded one. Same
        # spirit as the old rankCandidates "ranking unavailable" fallback.
        results = [
            {**{k: r.get(k) for k in RESULT_DISPLAY_FIELDS}, "reason": None, "tier": "look",
             "exploration_pick": False}
            for r in pool[:curated_limit]
        ]
        return {"results": results, "pool_size": len(pool), "rescued": [],
                "guard_overrode_count": 0, "embed_cost_usd": embed_cost,
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
    return {
        "results": results,
        "pool_size": len(pool),
        "rescued": final["rescued"],
        "guard_overrode_count": final["guard_overrode_count"],
        "embed_cost_usd": embed_cost,
        "note": None,
    }
