"""Public opportunities routes: the catalog, the on-demand deadline check, and the
on-demand action-item generation.

Translated from server.py's handle_opportunities / handle_deadline_check
(PLAN_1_decompose.md). Paths and JSON shapes are unchanged.
"""
import datetime
import json

from fastapi import APIRouter, Request, Depends

from app.config import (
    SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY,
    GEMINI_API_KEY, MESSAGES_MODEL, OPPORTUNITIES_CLIENT_STRIP_FIELDS,
)
from app.core import (touch_user_activity, record_user_cost_async,
                      record_interactive_cost_async)
from app.deps import (json_response, json_error, require_subscription,
                      optional_subscribed_user, json_body)
from app.auth import AuthedUser
from app.services.opportunities import fetch_opportunities
from app.services import action_items as action_items_service
from app.services import deadlines
from app.services.match_pipeline import (
    run_match, recall_pool, curate_pool, next_funnel_rung,
)
from app.services.curation import CURATION_SYSTEM
from app.services.funnel import (
    FUNNEL_QUESTION_SYSTEM, BEHAVIORAL_QUESTION_SYSTEM, OUTCOME_QUESTION_SYSTEM,
    CURATE_AT, FUNNEL_MAX_TOKENS,
    next_vibe_rung, next_outcome_rung, collect_preferences, describe_funnel_choices,
    build_engagement_rung,
)
from app.services.embeddings import embed_student_themes
from gemini_common import call_gemini, extract_json
# Imported, never re-declared: user_costs.model must name the model that was actually
# billed. The Sonnet/Haiku drift this repo already paid for came from exactly that — a pin
# copied into a second file and left behind when the first one moved.
from generate_action_items import MODEL as ACTION_ITEM_MODEL
from check_deadlines import (
    check_one as check_deadline_one,
    deadline_write_decision,
    missing_opens_date,
    SOURCE_SILENT,
    CLAUDE_MODEL as DEADLINE_CHECK_MODEL,
)

router = APIRouter()


@router.get("/api/opportunities")
def handle_opportunities(user: AuthedUser = Depends(optional_subscribed_user)):
    """The catalog. Soft auth (it is public, read-only data and the signed-out landing
    flow reaches it), but a caller who identifies as a lapsed account gets the 402 — the
    catalog is what the app is for, so an expired trial does not keep browsing it."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return json_error(500, "SUPABASE_URL/SUPABASE_ANON_KEY not configured.")
    try:
        data = fetch_opportunities()
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")
    # Strip the server-only match_vector (~9MB across the catalog, no display value) before it
    # ever reaches a client. The cache holds it for the recall stage; the client never gets it.
    strip = OPPORTUNITIES_CLIENT_STRIP_FIELDS
    if strip:
        data = [{k: v for k, v in row.items() if k not in strip} for row in data]
    return json_response(200, data)


@router.post("/api/match")
def handle_match(body: dict = Depends(json_body),
                 user: AuthedUser = Depends(require_subscription)):
    """The curated match (OPPORTUNITY_MATCHING_PLAN.md). Body is the Phase-2 student blob:
      {grade, location:{state,...}, profile_themes:[{theme,intent,next_steps}|str],
       highlight_projects:[str], funnel_answers:{axis:value}}

    Two modes:
      * default (no `funnel`): recall -> curation, returns {results:[<=10 cards], pool_size,
        rescued, guard_overrode_count, note} (Phase 3).
      * `funnel: true` (Phase 4): the progressive funnel. Rung 0 (no `pool_ids`) runs recall,
        then returns EITHER the next question (`{done:false, axis, question, options:[{label,
        value,count}], classification, pool_ids}`) or, if nothing is worth asking / the pool is
        already small, the curated list (`{done:true, results, ...}`). Subsequent rungs send
        the client-narrowed `pool_ids` + the accumulated `funnel_answers`; recall does NOT
        re-run (no re-embedding), the server just asks the next question or curates.

    Gated by require_subscription (paid model calls). Mock/offline (no GEMINI_API_KEY) degrades
    to a recall-ordered list so the app stays click-through-able."""
    userid = user.id
    touch_user_activity(userid, "match")
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return json_error(500, "SUPABASE_URL/SUPABASE_ANON_KEY not configured.")
    try:
        rows = fetch_opportunities()
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")

    student = {
        "grade": body.get("grade"),
        "location": body.get("location") or {},
        "profile_themes": body.get("profile_themes") or [],
        "highlight_projects": body.get("highlight_projects") or [],
        "funnel_answers": body.get("funnel_answers") or {},
    }
    funnel_mode = bool(body.get("funnel"))
    pool_ids = body.get("pool_ids") if isinstance(body.get("pool_ids"), list) else None
    # "Show my matches now" (the funnel escape hatch): curate whatever pool the student has
    # narrowed to so far instead of asking another question. No recall re-run — it rides on the
    # client-narrowed pool_ids like any later rung.
    curate_now = bool(body.get("curate_now"))

    if not GEMINI_API_KEY:
        # Mock/offline: no embeddings (recall returns the filtered set unscored) and no
        # curation model. Honest degraded list rather than a broken screen (funnel too).
        from app.services.matching import recall
        loc = student["location"]
        pool = recall(rows, [], student_grade=student["grade"],
                      student_state=(loc.get("state") if isinstance(loc, dict) else None), limit=10)
        from app.services.match_pipeline import RESULT_DISPLAY_FIELDS
        results = [{**{k: r.get(k) for k in RESULT_DISPLAY_FIELDS},
                    "reason": None, "tier": "look", "exploration_pick": False} for r in pool]
        return json_response(200, {"results": results, "pool_size": len(pool), "rescued": [],
                                   "guard_overrode_count": 0, "done": True,
                                   "note": "matching runs in mock mode (no model key) — showing catalog matches"})

    # Capture each model call's usage so its cost can be banked under the right feature. The
    # embedding cost is returned separately but recorded with the correct (cheaper) embed
    # pricing in a follow-up (see c9ccae8) — recording it through the generateContent pricer
    # would overstate it ~4x.
    curation_usage: dict = {}
    funnel_usage: dict = {}

    def _embed(texts):
        return embed_student_themes(texts, GEMINI_API_KEY)

    def _model_call(usage_sink, max_tokens=2000):
        def _fn(system, user_content):
            text, usage = call_gemini(system, user_content, GEMINI_API_KEY,
                                      use_web_search=False, max_tokens=max_tokens, model=MESSAGES_MODEL)
            usage_sink.clear()
            usage_sink.update(usage)
            return text
        return _fn

    _curate = _model_call(curation_usage)
    # The funnel-question call classifies EVERY candidate in the pool (up to RECALL_POOL_SIZE),
    # so its JSON is large — a full 100-row classification runs ~3k output tokens. At the 2000
    # default it truncated, the parse failed, and next_funnel_rung returned None, silently
    # dropping the student straight to the vibe questions with no filter rung. Give it real
    # headroom (also covers Gemini 3.x thinking tokens, which draw from the same budget).
    _ask = _model_call(funnel_usage, max_tokens=FUNNEL_MAX_TOKENS)

    try:
        if not funnel_mode:
            out = run_match(rows, student, _embed, _curate, extract_json)
        else:
            if pool_ids is not None:
                by_id = {r.get("id"): r for r in rows}
                pool = [by_id[i] for i in pool_ids if i in by_id]  # client-narrowed survivors
                embed_cost = 0.0
            else:
                pool, embed_cost = recall_pool(rows, student, _embed)
            answers = student.get("funnel_answers") or {}
            rungs_done = len(answers)
            rung = None
            if not curate_now:
                # The funnel order puts the student's "what am I here for" dimensions FIRST so they
                # are reliably asked, THEN the practical feasibility filters, THEN the softer vibe:
                #   dim 2 engagement (filter) -> dim 3 outcome (rerank) -> feasibility filters ->
                #   vibe. Filters gate on CURATE_AT (stop narrowing when tight); rerank questions
                #   gate on POOL_FLOOR (reorder a meaningful list, right up to the shortlist).
                # Dimension 2: pool-derived engagement FILTER, built locally from the pool's `type`.
                if "engagement" not in answers and len(pool) > CURATE_AT:
                    rung = build_engagement_rung(pool)
                # Dimension 3: pool-derived OUTCOME rerank ("what do you want out of it").
                if rung is None:
                    rung = next_outcome_rung(pool, answers, _ask, extract_json, rungs_done)
                # Eligibility filter questions that need the prose + quote guard (citizenship /
                # hard_demographic) — these still go to the model. (cost + time are asked BEFORE
                # recall now, alongside interest, so they are not funnel rungs.)
                if rung is None:
                    rung = next_funnel_rung(pool, student, _ask, extract_json, rungs_done)
                # Remaining rerank-only VIBE questions (never cut, only reorder curation).
                if rung is None:
                    rung = next_vibe_rung(pool, student, answers, _ask, extract_json, rungs_done)
            if rung is None:
                # Give curation the WHOLE funnel journey so the "why you" reason is contextual to
                # the student's choices: soft vibe/outcome/free-text prefs + the structured
                # engagement/budget/timing picks, in plain language.
                student_for_curation = {
                    **student,
                    "preferences": collect_preferences(answers) + describe_funnel_choices(answers),
                }
                out = curate_pool(pool, student_for_curation, _curate, extract_json)
                out["done"] = True
            else:
                out = {"done": False, **rung}
            out["embed_cost_usd"] = embed_cost
    except Exception as e:
        return json_error(502, f"Matching failed: {e}")

    if curation_usage:
        record_interactive_cost_async("interactive_gemini", curation_usage, MESSAGES_MODEL,
                                      userid=userid, system=CURATION_SYSTEM)
    if funnel_usage:
        record_interactive_cost_async("interactive_gemini", funnel_usage, MESSAGES_MODEL,
                                      userid=userid, system=FUNNEL_QUESTION_SYSTEM)
    return json_response(200, out)


@router.get("/api/opportunities/{opp_id}/deadline")
def handle_deadline_check(opp_id: str, request: Request,
                          user: AuthedUser = Depends(require_subscription)):
    """On-demand, cross-user-cached deadline check. Serves cached status/important_dates
    if last_checked_at is under DEADLINE_STALE_DAYS old; otherwise runs a fresh Claude
    Haiku web_search check (check_deadlines.check_one), re-caches, and returns it.

    Falls back to the cached value WITHOUT stamping the TTL whenever the check produced
    nothing trustworthy — no search ran, the extracted JSON was unreadable, or it found no
    dates for a row that already has some. `source` in the response names which happened."""
    # Gated before any Supabase or Claude work by require_subscription: a fresh check is a
    # paid web-search call. Identity is token-derived (was a query-string userid), which
    # also closed the old fail-open where omitting userid slipped past the paywall.
    deadline_userid = user.id
    # Counts as activity even when the answer comes from cache and costs nothing —
    # this measures use of the app, not spend.
    touch_user_activity(deadline_userid, "deadline_check")
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return json_error(500, "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured.")
    try:
        opp = deadlines.get_opportunity_for_deadline_check(opp_id)
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")
    if not opp:
        return json_error(404, "Opportunity not found.")

    # `refresh=1` (the Quest Log's "Check for updates" button) forces a real, paid check
    # even when the 7-day cache is still fresh — an explicit user action meaning "look again
    # now". A successful check re-stamps dates_last_checked_at below, so the answer is then
    # cached for another 7 days exactly like any other. Passive loads (opening a card, a
    # Fresh Finds add) do NOT pass it, so they still ride the free cross-user cache. Note this
    # bypasses only the STALENESS check, not the paywall (require_subscription already ran)
    # nor the write guards: a forced check that comes back silent/unparsed/empty still writes
    # nothing and does not stamp, so it cannot cache a hole for 7 days.
    force = str(request.query_params.get("refresh", "")).strip().lower() in ("1", "true", "yes")
    if not force and deadlines.deadline_cache_is_fresh(opp.get("dates_last_checked_at")):
        payload = deadlines.cached_deadline_payload(opp, "cached")
        deadlines.log_deadline_check(opp_id, "cached", opp.get("status"), None, None,
                                     opp.get("was_estimated"))
        return json_response(200, payload)

    if not ANTHROPIC_API_KEY:
        payload = deadlines.mock_deadline_check_payload(opp)
        deadlines.log_deadline_check(opp_id, "mock", payload.get("status"), 0, 0.0,
                                     payload.get("was_estimated"), "Mock mode - no API key")
        return json_response(200, payload)

    try:
        # retry_on_silent (check_one's default) costs one extra round-trip when Claude
        # answers without searching. Worth it: the answer is cached for 7 days, so a
        # single silent set of dates would be served to every student for a week.
        # want_requirements=True (T6): the shared finder also fetches this program's
        # how-to-apply / FAQ pages and caches the full capture, so the action-item endpoint
        # firing right after reads the program ONCE instead of fetching it again. The deadline
        # result itself is identical.
        info, _cost, searches, _attempts, site_reached = check_deadline_one(
            opp, ANTHROPIC_API_KEY, want_requirements=True)

        # One shared decision with the batch loop (check_deadlines.deadline_write_decision),
        # so the two can never disagree about when a row may be overwritten. Three of its
        # four outcomes write NOTHING and, just as importantly, do NOT stamp
        # dates_last_checked_at — the row stays due and the next request re-rolls, instead of
        # a hole being served to every student for 7 days:
        #   silent      phase 1 never searched
        #   unparsed    phase 1 searched but phase 2's JSON was unreadable
        #   kept        verified, but found no dates while the row already has some
        #   unreachable searched, empty, but never reached the program's own page (SPA/down) —
        #               leaves the row due so the next view retries rather than caching a hole
        decision = deadline_write_decision(info, searches, opp.get("important_dates"),
                                           site_reached=site_reached)
        if not decision.write:
            print(f"[WARN] Deadline check for {opp_id} not written ({decision.reason}); "
                  f"keeping the cached value and NOT stamping dates_last_checked_at, so the "
                  f"next request tries again.")
            payload = deadlines.cached_deadline_payload(opp, decision.source)
            deadlines.log_deadline_check(opp_id, decision.source, opp.get("status"),
                                         searches, _cost, opp.get("was_estimated"),
                                         decision.reason)
            # Billed either way — the tokens were spent even though nothing was written.
            # A silent call made no search, so it carries no per-search fee.
            record_user_cost_async(deadline_userid, "deadline_check",
                                   "deadline_check", cost=_cost,
                                   searches=0 if decision.source == SOURCE_SILENT else searches,
                                   model=DEADLINE_CHECK_MODEL)
            return json_response(200, payload)

        status = decision.status
        important_dates = decision.important_dates

        source_flag = decision.source
        # A deadline with no opens date is a silent downgrade, not an error: the app can
        # never mark that opportunity "Happening Now", because that is driven by its FIRST
        # date having passed. Logged so the gap is measurable rather than invisible.
        no_opens = missing_opens_date(important_dates)
        print(f"[INFO] Deadline check for {opp_id}: {searches} web search(es) performed."
              + (" No opens date found — this row can never read Happening Now." if no_opens else ""))

        patch = {
            "status": status,
            "important_dates": important_dates,
            "was_estimated": decision.was_estimated,
            "important_date_note": decision.note,
            "dates_last_checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        deadlines.patch_opportunity_deadline(opp_id, patch)
        response = {**patch, "source": source_flag}
        deadlines.log_deadline_check(opp_id, source_flag, status, searches, _cost,
                                     decision.was_estimated,
                                     "no opens date" if no_opens else None)
        record_user_cost_async(deadline_userid, "deadline_check",
                               "deadline_check", cost=_cost, searches=searches,
                               model=DEADLINE_CHECK_MODEL)
        return json_response(200, response)
    except Exception as e:
        # Claude API error / network hiccup: degrade to whatever was cached, even if stale.
        print(f"[WARN] Deadline check failed for {opp_id}: {e}")
        payload = deadlines.cached_deadline_payload(opp, "stale-fallback")
        deadlines.log_deadline_check(opp_id, "stale-fallback", opp.get("status"), None, None,
                                     opp.get("was_estimated"), f"Error: {str(e)[:100]}")
        return json_response(200, payload)


@router.get("/api/tracker/sync")
def handle_tracker_sync(ids: str = "", user: AuthedUser = Depends(require_subscription)):
    """FREE, read-only mirror of the catalog's CURRENT cached deadline+task data for a set of
    tracked ids, in ONE round trip. This is the SYNC half of the tracker's freshness model
    (2026-08-25): the per-user snapshot in users.data is frozen at add-time, so without this
    an already-tracking student never sees a catalog update (an agent run, another student's
    on-demand check) until they pay to re-verify. This endpoint NEVER triggers a paid check —
    that stays on `/deadline`, `/action-items` and the Update-now button (the VERIFY half).

    Fired by the client on app-open/login and on Quest Log / Home Base focus (throttled), so
    it must stay cheap: it is a single PostgREST read, no model call, no write. Gated by
    require_subscription like the rest of the app data — a lapsed account is paywalled here
    too, which is fine because it is paywalled everywhere else.
    """
    touch_user_activity(user.id, "tracker_sync")
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return json_error(500, "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured.")
    id_list = [i for i in (ids or "").split(",") if i.strip()]
    if not id_list:
        return json_response(200, {"items": {}})
    try:
        items = deadlines.get_cached_tracker_data(id_list)
    except Exception as e:
        return json_error(502, f"Could not read tracker sync data: {e}")
    return json_response(200, {"items": items})


@router.get("/api/opportunities/{opp_id}/action-items")
def handle_action_items(opp_id: str, user: AuthedUser = Depends(require_subscription)):
    """The application checklist for one opportunity, shared by every student tracking it.

    Almost always free: generate_action_items.py has already written a verified list onto
    the row, and this just serves it. It generates only for a row the batch has not reached
    — a scrape from last night, a user submission resolved minutes ago, a page that was
    refusing our client when the agent last ran — and caches the result so the next student
    to track it pays nothing.

    Gated by require_subscription like the deadline check, for the same reason: the
    generate branch is a paid model call. Every task in the response carries a `basis`, and
    the client renders 'page' items plainly and everything else under "Typical steps".
    """
    touch_user_activity(user.id, "action_items")
    try:
        payload, cost = action_items_service.resolve(opp_id)
    except Exception as e:
        return json_error(502, f"Could not resolve action items: {e}")
    if payload is None:
        # No catalog row (a tracker item with no id we know), or the columns have not been
        # migrated in yet. 404 rather than an empty list: the client must be able to tell
        # "this program has no checklist" from "we could not look", exactly as
        # refreshTrackerDeadlines distinguishes not-found from failed.
        return json_error(404, "No catalog row for that opportunity.")
    if cost:
        record_user_cost_async(user.id, "claude", "action_items", cost=cost,
                               searches=0, model=ACTION_ITEM_MODEL)
    return json_response(200, payload)
