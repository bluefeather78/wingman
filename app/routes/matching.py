"""POST /api/match — semantic recall + eligibility, for the main-style grid
(docs/plans/RECALL_GRID_MERGE_PLAN.md).

The trimmed, funnel-free endpoint: embed the student's selected profile themes (+ projects),
recall the top-N by cosine, drop verified-ineligible rows, and return the whole scored pool for
the client grid to filter. No curation to <=10, no funnel questions — those live on the
opportunity-matching branch and are deliberately not here.

Kept as its OWN route module (not folded into opportunities.py) so main's route file stays
clean and this never entangles with the branch's funnel-laden /api/match.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import GEMINI_API_KEY, MESSAGES_MODEL, SUPABASE_URL, SUPABASE_ANON_KEY
from app.core import touch_user_activity, record_interactive_cost_async
from app.deps import (json_body, json_response, json_error, require_subscription,
                      opaque_error, DB_UNAVAILABLE)
from app.auth import AuthedUser
from app.services.opportunities import fetch_opportunities, fetch_opportunities_with_vectors
from app.services.embeddings import embed_student_themes
from app.services.recall_query import recall_pool, attach_display, student_embed_texts
from app.services.pool_eligibility import gate_pool_eligibility, ELIGIBILITY_ONLY_SYSTEM
from app.services import budget
from wingman.gemini_common import call_gemini, extract_json

router = APIRouter()

# The eligibility gate verdicts every restriction-bearing row in the pool, so its JSON can be
# large — give real headroom (also covers Gemini 3.x thinking tokens, which draw from the same
# budget). Undersizing truncates the verdict array; the parse then fails and the gate keeps
# everyone (safe, but the eligibility fix silently no-ops), so err high.
ELIGIBILITY_MAX_TOKENS = 8000


def _student_from_body(body: dict) -> dict:
    return {
        "grade": body.get("grade"),
        "location": body.get("location") or {},
        "profile_themes": body.get("profile_themes") or [],
        "highlight_projects": body.get("highlight_projects") or [],
        # Session-only sensitive attributes (citizenship/gender), present only if volunteered;
        # used by the eligibility gate to judge a stated restriction, never stored.
        "funnel_answers": body.get("funnel_answers") or {},
    }


@router.post("/api/match")
def handle_match(body: dict = Depends(json_body),
                 user: AuthedUser = Depends(require_subscription)):
    """Recall + eligibility over the whole catalog for one student.

    Body: {grade, location:{state,...}, profile_themes:[{theme,intent,next_steps}|str],
           highlight_projects:[str], funnel_answers:{citizenship?,gender?}}.
    Returns: {results:[{...row, score, strong, }...], pool_size, excluded_ineligible:[ids],
              embed_cost_usd, checked}.

    `{prewarm:true}` embeds the themes into the server cache while the student is still on the
    theme picker, so the real recall hits the cache. Gated by require_subscription (paid);
    mock/offline (no GEMINI_API_KEY) returns a recall-ordered, ungated list so the app stays
    click-through-able."""
    userid = user.id
    touch_user_activity(userid, "match")

    # MARQUEE M9 (S0-5, finding H4). /api/match is a few cents a call and was unbounded.
    # circuit open -> everyone falls through to the mock/offline branch below, which is already
    # an honest degraded list rather than a broken screen. The prewarm path embeds (a paid call
    # too), so it is behind the breaker as well. (The per-user dollar backstop that used to
    # refuse here was removed — a Free account is metered in actions/day, not dollars.)
    live = bool(GEMINI_API_KEY) and not budget.circuit_open()

    if body.get("prewarm"):
        if live:
            tt, pt = student_embed_texts(_student_from_body(body))
            if tt or pt:
                try:
                    embed_student_themes(tt + pt, GEMINI_API_KEY)  # populate the cache
                except Exception:
                    pass  # a failed prewarm is silent — recall just embeds normally
        return json_response(200, {"ok": True, "prewarmed": True})

    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return json_error(500, "SUPABASE_URL/SUPABASE_ANON_KEY not configured.")
    try:
        # Phase 2 item 5: the ONLY caller that needs embeddings, and now the only one that
        # pays for them. Browsing reads the vector-free catalog cache.
        rows = fetch_opportunities_with_vectors()
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="matching.db")

    student = _student_from_body(body)

    if not live:
        # Mock/offline (no key, or the spend circuit breaker is open): no embeddings (recall
        # returns the filtered set unscored), no eligibility model call. Honest degraded list
        # rather than a broken screen.
        pool, _cost, scores = recall_pool(rows, student, lambda texts: ([], 0.0))
        return json_response(200, {
            "results": attach_display(pool, scores), "pool_size": len(pool),
            "excluded_ineligible": [], "embed_cost_usd": 0.0, "checked": 0,
            "note": ("matching runs in mock mode (no model key) — showing catalog matches"
                     if not GEMINI_API_KEY else
                     "matching is running in a reduced mode right now — showing catalog "
                     "matches"),
        })

    elig_usage: dict = {}

    def _embed(texts):
        return embed_student_themes(texts, GEMINI_API_KEY)

    def _gate(system, user_content):
        text, usage = call_gemini(system, user_content, GEMINI_API_KEY,
                                  use_web_search=False, max_tokens=ELIGIBILITY_MAX_TOKENS,
                                  model=MESSAGES_MODEL)
        elig_usage.clear()
        elig_usage.update(usage)
        return text

    try:
        pool, embed_cost, scores = recall_pool(rows, student, _embed)
        gate = gate_pool_eligibility(pool, student, _gate, extract_json)
        results = attach_display(gate["pool"], scores)
    except Exception as e:
        return opaque_error(502, "We could not build your matches just now. "
                            "Please try again.", e, op="matching.run")

    if elig_usage:
        # The feature id directly (S1-1): this prompt was previously identified by the
        # substring "Wingman's eligibility checker", so rewording its first line silently
        # refiled the spend.
        record_interactive_cost_async("interactive_gemini", elig_usage, MESSAGES_MODEL,
                                      userid=userid, feature="match_eligibility")
    return json_response(200, {
        "results": results, "pool_size": len(gate["pool"]),
        "excluded_ineligible": gate["excluded"], "embed_cost_usd": embed_cost,
        "checked": gate["checked"],
    })


@router.post("/api/match/eligibility")
def handle_match_eligibility(body: dict = Depends(json_body),
                             user: AuthedUser = Depends(require_subscription)):
    """Eligibility-gate an already-chosen candidate set — the finder's form/quiz path.

    The theme path gets its eligibility gate inside /api/match, but the form/quiz path builds
    its candidate pool entirely client-side (preFilter + rankCandidates) and never touches that
    route, so its results were ungated for citizenship/geography/prerequisite restrictions
    (grade IS filtered there, in preFilter). This runs the SAME `gate_pool_eligibility` over the
    candidate ids the client chose, so both search paths drop the same verified-ineligible rows.

    Body: {candidate_ids:[str], grade, location:{state,...}, funnel_answers:{citizenship?,gender?}}.
    Returns: {excluded_ineligible:[ids], checked, called}. The client keeps every row NOT named
    in `excluded_ineligible`, so a mock/offline response, the open circuit breaker, or a parse
    failure all leave the list untouched (the gate can only ever make it more inclusive).

    MARQUEE M9: a new code path that makes a paid model call (the M8 ELIGIBILITY_ONLY_SYSTEM
    prompt, unchanged). Costed and gated exactly like /api/match — behind require_subscription,
    the global spend circuit breaker, and the mock/offline fallback; NOT metered as a separate
    Free-tier AI action (the search already spent one on ranking), matching /api/match.
    """
    userid = user.id
    touch_user_activity(userid, "match")

    ids = [str(i) for i in (body.get("candidate_ids") or []) if i is not None]
    if not ids:
        return json_response(200, {"excluded_ineligible": [], "checked": 0, "called": False})

    # No key or the breaker is open: gate nothing rather than 402/500 — an honest degrade that
    # keeps the (grade-filtered) form results showing, same posture as /api/match's mock branch.
    live = bool(GEMINI_API_KEY) and not budget.circuit_open()
    if not live:
        return json_response(200, {"excluded_ineligible": [], "checked": 0, "called": False})

    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return json_error(500, "SUPABASE_URL/SUPABASE_ANON_KEY not configured.")
    try:
        # The gate reads only text fields (name/org/summary/eligibility), so the vector-free
        # catalog cache is enough — no need to pay the ~20MB embeddings read here.
        rows = fetch_opportunities()
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="matching.elig.db")

    by_id = {r.get("id"): r for r in rows}
    # Preserve the client's order; silently drop ids not in the catalog (inactive/stale).
    pool = [by_id[i] for i in ids if i in by_id]
    if not pool:
        return json_response(200, {"excluded_ineligible": [], "checked": 0, "called": False})

    student = _student_from_body(body)
    elig_usage: dict = {}

    def _gate(system, user_content):
        text, usage = call_gemini(system, user_content, GEMINI_API_KEY,
                                  use_web_search=False, max_tokens=ELIGIBILITY_MAX_TOKENS,
                                  model=MESSAGES_MODEL)
        elig_usage.clear()
        elig_usage.update(usage)
        return text

    try:
        gate = gate_pool_eligibility(pool, student, _gate, extract_json)
    except Exception as e:
        return opaque_error(502, "We could not check eligibility just now. Please try again.",
                            e, op="matching.elig.run")

    if elig_usage:
        record_interactive_cost_async("interactive_gemini", elig_usage, MESSAGES_MODEL,
                                      userid=userid, feature="match_eligibility")
    return json_response(200, {
        "excluded_ineligible": gate["excluded"], "checked": gate["checked"],
        "called": gate["called"],
    })
