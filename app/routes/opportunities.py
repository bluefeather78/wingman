"""Public opportunities routes: the catalog, the on-demand deadline check, and the
on-demand action-item generation.

Translated from server.py's handle_opportunities / handle_deadline_check
(docs/archive/PLAN_1_decompose.md). Paths and JSON shapes are unchanged.
"""
import datetime
import json

from fastapi import APIRouter, Request, Response, Depends

from app.config import (
    SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY,
    PAID_CHECK_MAX_CONCURRENCY, PAID_CHECK_SHED_RETRY_AFTER_SECONDS,
)
from app.core import touch_user_activity, record_user_cost_async, record_api_error
from app.deps import (json_response, json_error, require_subscription,
                      optional_subscribed_user, allowance_error,
                      opaque_error, DB_UNAVAILABLE)
from app.auth import AuthedUser
from app.services.opportunities import catalog_payload
from app.services import action_items as action_items_service
from app.services import deadlines
from app.services import budget
from app.services.lanes import PaidLane
# banked_cost recovers money a failed paid call already spent (audit 4.3).
from wingman import agent_common
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

# MARQUEE M9: the concurrency bound in front of this module's two paid calls (Phase 2 item 8).
# See app/services/lanes.py for why it is a semaphore rather than the event-loop counter
# /api/ai uses, and app/config.py for why the limit is 4.
#
# ONE lane shared by both routes, not one each. The two are the same scarce resource — a
# threadpool slot held against a slow Anthropic call — and giving each its own budget of 4
# would let 8 run at once, which is the number this is supposed to prevent. They are also
# genuinely sequential in the product: the Quest Log fires the deadline check and the
# checklist for the same card together, and want_requirements=True on the deadline check
# exists precisely so the second one reuses the first one's page capture.
paid_check_lane = PaidLane(PAID_CHECK_MAX_CONCURRENCY, "paid_check")


def _paid_lane_busy_response():
    """503 + Retry-After when four paid checks are already running.

    503, not 429, for the reason the AI lane gives: 429 is one caller's fault and the budget
    and cooldown layers above already own it, while this is "the service is at capacity" and
    is nobody's. Retry-After is longer than the AI lane's because the work is longer — a
    deadline check is tens of seconds, so telling a student to come back in 5 would mostly
    produce a second 503.

    Turning the student away here is the APPROVED behaviour, not a fallback: Shama, 2026-09-05,
    "ok to turn student away for now... we will revisit when I buy hosting on Render". Both
    routes do have a free degraded answer available (cached_deadline_payload / resolve with
    allow_paid=False, the paths the circuit breaker takes), so serving that instead of a 503
    is the obvious alternative to weigh at the launch gate. It is deliberately NOT done here:
    that is a product decision about what a student sees, and it was not the one approved.
    """
    resp = json_error(503, "We're checking a lot of programs right now. "
                           "Give it a few seconds and try again.")
    resp.headers["Retry-After"] = str(PAID_CHECK_SHED_RETRY_AFTER_SECONDS)
    return resp


def _etag_matches(if_none_match, etag):
    """True if the client already holds these exact bytes.

    If-None-Match is a LIST — a browser may send several, and a revalidating one sends `W/`
    prefixed entries. Comparing the raw header to the ETag would miss both and silently turn
    every conditional request back into a full catalog download, which is the whole saving.
    """
    if not if_none_match:
        return False
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate.removeprefix("W/") == etag:
            return True
    return False


@router.get("/api/opportunities")
def handle_opportunities(request: Request,
                         user: AuthedUser = Depends(optional_subscribed_user)):
    """The catalog. Soft auth (it is public, read-only data and the signed-out landing
    flow reaches it), but a caller who identifies as a lapsed account gets the 402 — the
    catalog is what the app is for, so an expired trial does not keep browsing it.

    Phase 2 item 5. The body, its gzip and its ETag are computed ONCE per catalog refresh
    (app/services/opportunities.catalog_payload) instead of once per request: serialising
    ~1,700 rows and gzipping them is real CPU, and on 0.1 of a core it was being paid on every
    call for bytes that are identical between refreshes. The per-row strip of `match_vector`
    that used to happen here is gone too — the catalog cache no longer holds the column at all,
    so there is nothing left to strip.
    """
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return json_error(500, "SUPABASE_URL/SUPABASE_ANON_KEY not configured.")
    try:
        body, gzipped, etag = catalog_payload()
    except Exception as e:
        return opaque_error(502, DB_UNAVAILABLE, e, op="opportunities.db")

    # `max-age=0, must-revalidate` rather than a 5-minute max-age. Both get the win that
    # matters — a returning student sends If-None-Match and gets a 304 instead of the largest
    # payload in the app — but this one never serves a stale catalog. The plan's trade-off
    # table sanctioned letting an activation show up to OPPORTUNITIES_CACHE_TTL late; that
    # allowance is not needed to get the bandwidth back, and app/main.py's no_cache middleware
    # exists precisely because a browser holding stale app state is the failure this codebase
    # has already been bitten by. Revalidating costs one conditional request per load, which
    # is a few hundred bytes.
    #
    # `private` because the 402 above makes this response depend on who is asking; a shared
    # proxy must not hand one student's 200 to a lapsed account.
    headers = {
        "ETag": etag,
        "Cache-Control": "private, max-age=0, must-revalidate",
        "Vary": "Accept-Encoding",
    }
    # A conditional request that already has these bytes costs a 304 and no body at all. This
    # is the single biggest win on a returning student's app open: the catalog is by far the
    # largest thing they download.
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    if "gzip" in (request.headers.get("accept-encoding") or "").lower():
        headers["Content-Encoding"] = "gzip"
        return Response(content=gzipped, media_type="application/json", headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


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

    # MARQUEE M11: the Free-tier daily AI allowance, in front of the paid Claude check. A
    # deadline "Check for updates" tap fans out across the whole Quest Log, so it counts as ONE
    # action (the "deadline" class collapses same-window calls). ai_allowance_state also carries
    # the dollar backstop, so it replaces the old over_user_budget check here. Paid users are
    # unlimited. See MARQUEE_DECISIONS.md M11 and app/services/budget.py.
    allowance = budget.ai_allowance_state(deadline_userid, feature="deadline_check")
    if allowance["over"]:
        touch_user_activity(deadline_userid, "ai_limit_hit")
        return allowance_error(allowance)

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

    # MARQUEE M9 (Phase 2 item 8): the lane, taken as late as possible. Everything above
    # this line is free — the cross-user cache hit, the cooldown, the budget and circuit
    # layers — and none of it may be shed, or four slow paid checks would start refusing the
    # cached reads that make up almost all of this route's traffic.
    if not paid_check_lane.try_acquire():
        return _paid_lane_busy_response()
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
        # Money the failed check had ALREADY spent (audit 4.3). check_one climbs up to four
        # paid rungs and retries a silent one; a timeout on a later rung used to discard every
        # earlier rung's cost, so this path recorded nothing at all in deadline_check_log and
        # nothing against the user's daily budget. That is the exact shape that lets a user
        # spend past their allowance: the failures were free as far as the ledger knew.
        spent = agent_common.banked_cost(e)
        payload = deadlines.cached_deadline_payload(opp, "stale-fallback")
        deadlines.log_deadline_check(opp_id, "stale-fallback", opp.get("status"), None,
                                     spent or None, opp.get("was_estimated"),
                                     f"Error: {str(e)[:100]}")
        if spent:
            record_user_cost_async(deadline_userid, "deadline_check", "deadline_check",
                                   cost=spent, searches=0, model=DEADLINE_CHECK_MODEL)
        return json_response(200, payload)
    finally:
        # Released on the degrade path as well as the success one. The except above returns a
        # 200, so a leak here would be invisible: a run of Anthropic failures would narrow the
        # lane one slot at a time until every fresh check 503s, and the only symptom would be
        # deadline checks quietly stopping while the route still looked healthy.
        paid_check_lane.release()


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
    # MARQUEE M9 + M11 (S0-5, finding H4). Same shape as the exploit: a user-submitted row is
    # never stamped with action_items_checked_at, so EVERY call on such a row takes the paid
    # generate branch. Action items are NOT one of the metered user actions (feature=None → the
    # action count is untouched), but a Free user is still bounded by the dollar backstop that
    # ai_allowance_state carries; a Paid user is unlimited. Global circuit breaker still applies.
    allowance = budget.ai_allowance_state(user.id, feature=None)
    if allowance["over"]:
        return allowance_error(allowance)
    # Degrade, don't error: allow_paid=False takes resolve()'s existing no-API-key path,
    # which serves the stored list if there is one and an honest generic checklist otherwise.
    try:
        # MARQUEE M9 (Phase 2 item 8): the lane is passed IN rather than wrapped around this
        # call, because resolve() is free almost every time — it serves a stored list — and
        # only it can tell, once it has the row, whether this particular call will pay. See
        # app/services/action_items.py for where it is taken.
        payload, cost = action_items_service.resolve(opp_id,
                                                     allow_paid=not budget.circuit_open(),
                                                     paid_gate=paid_check_lane)
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
