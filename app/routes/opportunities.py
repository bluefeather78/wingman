"""Public opportunities routes: the catalog, the on-demand deadline check, and the
on-demand action-item generation.

Translated from server.py's handle_opportunities / handle_deadline_check
(docs/archive/PLAN_1_decompose.md). Paths and JSON shapes are unchanged.
"""
import datetime
import json

from fastapi import APIRouter, Request, Depends

from app.config import (
    SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY,
    OPPORTUNITIES_CLIENT_STRIP_FIELDS,
)
from app.core import touch_user_activity, record_user_cost_async, record_api_error
from app.deps import (json_response, json_error, require_subscription,
                      optional_subscribed_user,
                      opaque_error, DB_UNAVAILABLE)
from app.auth import AuthedUser
from app.services.opportunities import fetch_opportunities
from app.services import action_items as action_items_service
from app.services import deadlines
from app.services import budget
# Imported, never re-declared: user_costs.model must name the model that was actually
# billed. The Sonnet/Haiku drift this repo already paid for came from exactly that — a pin
# copied into a second file and left behind when the first one moved.
from agents.generate_action_items import MODEL as ACTION_ITEM_MODEL
from agents.check_deadlines import (
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
        return opaque_error(502, DB_UNAVAILABLE, e, op="opportunities.db")
    # Strip the server-only match_vector (~9MB across the catalog, no display value) before it
    # reaches the client — recall scores it server-side; the browser never needs it.
    strip = OPPORTUNITIES_CLIENT_STRIP_FIELDS
    if strip:
        data = [{k: v for k, v in row.items() if k not in strip} for row in data]
    return json_response(200, data)


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
        return opaque_error(502, DB_UNAVAILABLE, e, op="opportunities.db")
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
    fresh = deadlines.deadline_cache_is_fresh(opp.get("dates_last_checked_at"))
    if fresh and not force:
        payload = deadlines.cached_deadline_payload(opp, "cached")
        deadlines.log_deadline_check(opp_id, "cached", opp.get("status"), None, None,
                                     opp.get("was_estimated"))
        return json_response(200, payload)

    # MARQUEE M9 (S0-5, finding H4): the forced-recheck cooldown. THIS route is the exploit
    # in the security report — refresh=1 bypassed the 7-day cache unconditionally, and each
    # verified check measures ~$0.07, so a single free trial account could loop the catalog
    # for ~$90 a pass, repeatably. The cache bypass is the amplifier, so it gets its own
    # per-(user, row) limit on top of the daily budget below.
    #
    # Only a force that ACTUALLY bypasses a fresh cache is charged against it. A stale row
    # would be re-checked by any passive load anyway, so counting that would penalise normal
    # use and stop nothing.
    if fresh and force and not budget.forced_recheck_ok(deadline_userid, opp_id):
        resp = json_error(429, "You just refreshed this one. We re-check it automatically — "
                               "try again a little later.")
        resp.headers["Retry-After"] = str(
            budget.forced_recheck_retry_after(deadline_userid, opp_id))
        return resp

    # The per-user daily allowance (layer 1). Checked before the paid call, not after.
    over = budget.over_user_budget(deadline_userid)
    if over:
        return json_error(429, over)

    # The global circuit breaker (layer 3) degrades rather than errors: serve whatever is
    # cached, and fall through to the free mock payload when there is nothing cached. Same
    # shape as the no-API-key branch below, which is the app's existing honest degraded path.
    if budget.circuit_open():
        if opp.get("dates_last_checked_at"):
            payload = deadlines.cached_deadline_payload(opp, "cached")
            deadlines.log_deadline_check(opp_id, "cached", opp.get("status"), None, None,
                                         opp.get("was_estimated"),
                                         "Global spend circuit breaker open")
            return json_response(200, payload)
        payload = deadlines.mock_deadline_check_payload(opp)
        deadlines.log_deadline_check(opp_id, "mock", payload.get("status"), 0, 0.0,
                                     payload.get("was_estimated"),
                                     "Global spend circuit breaker open")
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
        # The client gets a 200 (a cached answer, not a failure), so this never becomes a 5xx
        # the capture middleware would see — record it here so the dashboard still shows that the
        # deadline check's Anthropic call failed and why. status=0: the client saw no error code.
        print(f"[WARN] Deadline check failed for {opp_id}: {e}")
        record_api_error("GET", "/api/opportunities/{id}/deadline", 0,
                         "deadline_degraded", f"deadline check degraded to cached: {e}")
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
        return opaque_error(502, "We could not refresh your Quest Log just now. "
                                 "Please try again.", e, op="opportunities.sync")
    return json_response(200, {"items": items})


@router.get("/api/opportunities/{opp_id}/action-items")
def handle_action_items(opp_id: str, user: AuthedUser = Depends(require_subscription)):
    """The application checklist for one opportunity, shared by every student tracking it.

    Almost always free: agents/generate_action_items.py has already written a verified list onto
    the row, and this just serves it. It generates only for a row the batch has not reached
    — a scrape from last night, a user submission resolved minutes ago, a page that was
    refusing our client when the agent last ran — and caches the result so the next student
    to track it pays nothing.

    Gated by require_subscription like the deadline check, for the same reason: the
    generate branch is a paid model call. Every task in the response carries a `basis`, and
    the client renders 'page' items plainly and everything else under "Typical steps".
    """
    touch_user_activity(user.id, "action_items")
    # MARQUEE M9 (S0-5, finding H4). Same two layers as the deadline check. This route has the
    # same shape as the exploit: a user-submitted row is never stamped with
    # action_items_checked_at, so EVERY call on such a row takes the paid generate branch.
    over = budget.over_user_budget(user.id)
    if over:
        return json_error(429, over)
    # Degrade, don't error: allow_paid=False takes resolve()'s existing no-API-key path,
    # which serves the stored list if there is one and an honest generic checklist otherwise.
    try:
        payload, cost = action_items_service.resolve(opp_id,
                                                     allow_paid=not budget.circuit_open())
    except Exception as e:
        return opaque_error(502, "We could not build the checklist just now. "
                                 "Please try again.", e, op="opportunities.tasks")
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
