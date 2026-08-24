"""Public opportunities routes: the catalog and the on-demand deadline check.

Translated from server.py's handle_opportunities / handle_deadline_check
(PLAN_1_decompose.md). Paths and JSON shapes are unchanged.
"""
import datetime
import json

from fastapi import APIRouter, Request, Depends

from app.config import (
    SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY,
)
from app.core import touch_user_activity, record_user_cost_async
from app.deps import json_response, json_error, subscription_block_reason
from app.auth import get_current_user, AuthedUser
from app.services.opportunities import fetch_opportunities
from app.services import deadlines
from check_deadlines import (
    check_one as check_deadline_one,
    deadline_write_decision,
    missing_opens_date,
    SOURCE_SILENT,
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
def handle_deadline_check(opp_id: str, request: Request,
                          user: AuthedUser = Depends(get_current_user)):
    """On-demand, cross-user-cached deadline check. Serves cached status/important_dates
    if last_checked_at is under DEADLINE_STALE_DAYS old; otherwise runs a fresh Claude
    Haiku web_search check (check_deadlines.check_one), re-caches, and returns it.

    Falls back to the cached value WITHOUT stamping the TTL whenever the check produced
    nothing trustworthy — no search ran, the extracted JSON was unreadable, or it found no
    dates for a row that already has some. `source` in the response names which happened."""
    # Gate before any Supabase or Claude work: a fresh check is a paid web-search call.
    # Identity is token-derived (was a query-string userid). Hard-gating also closes the
    # old fail-open, where omitting userid slipped past the subscription paywall.
    deadline_userid = user.id
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

    if deadlines.deadline_cache_is_fresh(opp.get("dates_last_checked_at")):
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

        # One shared decision with the batch loop (check_deadlines.deadline_write_decision),
        # so the two can never disagree about when a row may be overwritten. Three of its
        # four outcomes write NOTHING and, just as importantly, do NOT stamp
        # dates_last_checked_at — the row stays due and the next request re-rolls, instead of
        # a hole being served to every student for 7 days:
        #   silent   phase 1 never searched
        #   unparsed phase 1 searched but phase 2's JSON was unreadable
        #   kept     verified, but found no dates while the row already has some
        decision = deadline_write_decision(info, searches, opp.get("important_dates"))
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
