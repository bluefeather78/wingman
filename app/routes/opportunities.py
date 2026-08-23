"""Public opportunities routes: the catalog and the on-demand deadline check.

Translated from server.py's handle_opportunities / handle_deadline_check
(PLAN_1_decompose.md). Paths and JSON shapes are unchanged.
"""
import datetime
import json

from fastapi import APIRouter, Request

from app.config import (
    SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY,
)
from app.core import touch_user_activity, record_user_cost_async
from app.deps import json_response, json_error, subscription_block_reason
from app.services.opportunities import fetch_opportunities
from app.services import deadlines
from check_deadlines import (
    check_one as check_deadline_one,
    VALID_STATUS as DEADLINE_VALID_STATUS,
    CLAUDE_MODEL as DEADLINE_CHECK_MODEL,
)

router = APIRouter()


@router.get("/api/opportunities")
def handle_opportunities():
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return json_error(500, "SUPABASE_URL/SUPABASE_ANON_KEY not configured.")
    try:
        data = fetch_opportunities()
    except Exception as e:
        return json_error(502, f"Could not reach Supabase: {e}")
    return json_response(200, data)


@router.get("/api/opportunities/{opp_id}/deadline")
def handle_deadline_check(opp_id: str, request: Request):
    """On-demand, cross-user-cached deadline check. Serves cached status/important_dates
    if last_checked_at is under DEADLINE_STALE_DAYS old; otherwise runs a fresh Claude
    Haiku web_search check (check_deadlines.check_one), re-caches, and returns it.
    Rejects silent search skips and falls back to cache if no searches occurred."""
    # Gate before any Supabase or Claude work: a fresh check is a paid web-search call.
    # userid arrives on the query string (this is a GET).
    deadline_userid = request.query_params.get("userid")
    reason = subscription_block_reason(deadline_userid)
    if reason:
        return json_error(402, reason)
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

    if deadlines.deadline_cache_is_fresh(opp.get("last_checked_at")):
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
        info, _cost, searches, _attempts = check_deadline_one(opp, ANTHROPIC_API_KEY)

        # A still-silent check writes NOTHING and is served from cache instead.
        if searches == 0:
            print(f"[WARN] Deadline check for {opp_id} invoked no web search even after a "
                  f"retry; keeping the cached value and NOT stamping last_checked_at, so "
                  f"the next request tries again.")
            payload = deadlines.cached_deadline_payload(opp, "unverified-fallback")
            deadlines.log_deadline_check(opp_id, "unverified-fallback", opp.get("status"), 0,
                                         _cost, opp.get("was_estimated"),
                                         "Silent after retry; not written")
            record_user_cost_async(request.query_params.get("userid"), "deadline_check",
                                   "deadline_check", cost=_cost, searches=0,
                                   model=DEADLINE_CHECK_MODEL)
            return json_response(200, payload)

        status = info.get("status") if info.get("status") in DEADLINE_VALID_STATUS else "unknown"
        important_dates = info.get("important_dates") or []
        if not isinstance(important_dates, list):
            important_dates = []
        important_dates = [d for d in important_dates if isinstance(d, dict) and d.get("date_iso")]

        source_flag = "fresh, real search"
        print(f"[INFO] Deadline check for {opp_id}: {searches} web search(es) performed.")

        patch = {
            "status": status,
            "important_dates": important_dates,
            "was_estimated": bool(info.get("was_estimated")),
            "important_date_note": info.get("important_date_note"),
            "last_checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        deadlines.patch_opportunity_deadline(opp_id, patch)
        response = {**patch, "source": source_flag}
        deadlines.log_deadline_check(opp_id, source_flag, status, searches, _cost,
                                     bool(info.get("was_estimated")))
        record_user_cost_async(request.query_params.get("userid"), "deadline_check",
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
