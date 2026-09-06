"""Spend caps — the two layers that make the app refuse or degrade a paid call (S0-5, H4).

Everything else in this repo RECORDS spend: agent_runs rolls up what the app spent,
user_costs decomposes it per user and feature, deadline_check_log logs each check. Nothing
read any of it back to refuse anything. So one account could loop
GET /api/opportunities/<id>/deadline?refresh=1 across the catalog: refresh=1 bypassed the
7-day cache unconditionally and each verified check measures ~$0.07, i.e. ~$90 per pass over
1,300 rows, repeatable. /api/match is a few cents a call, also unbounded.

The per-user DOLLAR ceiling that used to sit here (over_user_budget / USER_DAILY_BUDGET_USD)
was REMOVED — a Free account is metered in ACTIONS/day now (ai_allowance_state below), which
is the user-facing usage limit. What remains are the two spend guards that are NOT about one
user's usage:

  1. forced_recheck_ok()  — a per-user, per-row cooldown on the refresh=1 cache bypass. The
                            bypass is the burst amplifier, so it keeps its own limit.
  2. circuit_open()       — a global daily ceiling. Above it every paid branch degrades to
                            its existing cached/mock path, turning a billing incident into a
                            degraded app rather than an invoice.

circuit_open() reads user_costs, the complete ledger for interactive spend: every paid branch
in app/ routes its cost through record_user_cost (the AI proxies and /api/match via
record_interactive_cost, the deadline check and action items via record_user_cost_async).

FAILING OPEN IS DELIBERATE. If Supabase cannot be read the global breaker does not trip,
exactly as subscription_block_reason already chooses: a database blip must not degrade every
paying user. The global total is cached for BUDGET_CACHE_TTL_SECONDS and bumped in-process by
note_spend() as costs are recorded, so a burst inside one window still counts toward it.
"""
import datetime
import threading
import time

from app.config import (GLOBAL_DAILY_BUDGET_USD,
                        BUDGET_EXEMPT_USERIDS, BUDGET_CACHE_TTL_SECONDS,
                        FORCED_RECHECK_WINDOW_SECONDS, FORCED_RECHECK_MAX_PER_WINDOW,
                        FREE_TIER_DAILY_AI_ACTIONS, FIRST_DAY_AI_ACTIONS,
                        FREE_TIER_ACTION_WINDOW_SECONDS, FREE_TIER_AI_GATE_ENFORCED,
                        SUPABASE_URL, SUPABASE_SERVICE_KEY)
from app.core import _supabase_request, get_user_subscription, ai_tier
from app.auth.ratelimit import RateLimiter

# One forced re-check per (user, opportunity) per window. RateLimiter is exactly this shape
# already — a sliding window with a max — so it is reused rather than reimplemented.
# In-process like the other limiters: see app/auth/ratelimit.py's note on multi-worker.
forced_recheck_limiter = RateLimiter(FORCED_RECHECK_MAX_PER_WINDOW,
                                     FORCED_RECHECK_WINDOW_SECONDS)

# PostgREST pages at 1000 rows by default. user_costs' grain is
# (userid, day, surface, feature, model), so a day's rows scale with active users, not with
# calls — but page anyway, and stop at a bound rather than walking an unbounded table if the
# app ever gets big enough for that to matter.
_PAGE = 1000
_MAX_PAGES = 20

_lock = threading.Lock()
_cache = {}      # key -> (total_usd, read_at_monotonic, day)


def _today():
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _sum_cost(params):
    """Sum user_costs.cost_usd over `params`, or None if it could not be read.

    None means "unknown", which every caller treats as "do not block" — see the failing-open
    note in the module docstring. It is distinct from 0.0, which means "read it, nothing spent".
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    total = 0.0
    offset = 0
    try:
        for _ in range(_MAX_PAGES):
            page = _supabase_request("user_costs", params={
                **params, "select": "cost_usd",
                "limit": str(_PAGE), "offset": str(offset)})
            if page is None:
                return None
            total += sum(float(r.get("cost_usd") or 0) for r in page)
            if len(page) < _PAGE:
                return round(total, 6)
            offset += _PAGE
    except Exception as e:                                         # noqa: BLE001
        print(f"[WARN] Could not read spend for a budget check: {e}")
        return None
    print(f"[WARN] Spend read hit the {_MAX_PAGES}-page bound; treating "
          f"${total:.4f} as the total.")
    return round(total, 6)


def _sum_calls(params):
    """Sum user_costs.calls over `params`, or None if it could not be read.

    The request-count analogue of _sum_cost. `calls` counts BILLED, attributable calls only —
    mock, cached, stale-fallback and signed-out calls are never recorded there (CLAUDE.md's
    user_costs exclusions), which is exactly what keeps ordinary browsing and repeat visits
    from decrementing a Free student's daily AI allowance. None means "unknown" and every
    caller treats it as "do not block", identical to _sum_cost.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    total = 0
    offset = 0
    try:
        for _ in range(_MAX_PAGES):
            page = _supabase_request("user_costs", params={
                **params, "select": "calls",
                "limit": str(_PAGE), "offset": str(offset)})
            if page is None:
                return None
            total += sum(int(r.get("calls") or 0) for r in page)
            if len(page) < _PAGE:
                return total
            offset += _PAGE
    except Exception as e:                                         # noqa: BLE001
        print(f"[WARN] Could not read request count for a tier check: {e}")
        return None
    print(f"[WARN] Request-count read hit the {_MAX_PAGES}-page bound; treating "
          f"{total} as the total.")
    return total


def _cached_calls(key, params):
    """Today's summed request count for `key`, re-read at most once per BUDGET_CACHE_TTL_SECONDS.

    A separate cache from _cached_total: dollars and call-counts are different quantities and
    note_spend() bumps only the dollar cache. The cached day is part of the entry, so the first
    read after UTC midnight starts a fresh count. (There is deliberately no in-process bump yet
    — step 1 of TWO_TIER_AI_PLAN.md is read-only instrumentation; the note_call() bump the
    gate needs lands with the gate itself, M9/M10.)
    """
    day = _today()
    now = time.monotonic()
    ckey = ("calls", key)
    with _lock:
        entry = _cache.get(ckey)
        if entry and entry[2] == day and (now - entry[1]) < BUDGET_CACHE_TTL_SECONDS:
            return entry[0]
    total = _sum_calls({**params, "day": f"eq.{day}"})
    if total is None:
        return None
    with _lock:
        _cache[ckey] = (total, now, day)
    return total


def user_requests_today(userid):
    """This user's billed AI request count today, or None if it could not be read.

    The per-user request-count read that backs the Free-tier daily allowance figure shown on
    the console. (The action ledger, not this, is what the gate actually meters against.)
    """
    userid = (userid or "").strip().lower()
    if not userid:
        return None
    return _cached_calls(userid, {"userid": f"eq.{userid}"})


def _cached_total(key, params):
    """Today's summed spend for `key`, re-read at most once per BUDGET_CACHE_TTL_SECONDS.

    The cached day is part of the entry, so the first call after UTC midnight re-reads
    instead of carrying yesterday's total into a fresh budget.
    """
    day = _today()
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if entry and entry[2] == day and (now - entry[1]) < BUDGET_CACHE_TTL_SECONDS:
            return entry[0]
    total = _sum_cost({**params, "day": f"eq.{day}"})
    if total is None:
        return None
    with _lock:
        _cache[key] = (total, now, day)
    return total


def note_spend(userid, cost):
    """Add a just-recorded cost to the cached GLOBAL total, so a burst inside one TTL window
    counts toward the circuit breaker instead of re-reading a stale figure up to a minute old.

    Called from record_user_cost's background thread — never on the request path. `userid` is
    accepted (and ignored) so the record_user_cost call site does not have to change; there is
    no per-user dollar total any more, only the global one under the "*" key.
    """
    try:
        cost = float(cost or 0)
    except (TypeError, ValueError):
        return
    if cost <= 0:
        return
    day = _today()
    with _lock:
        entry = _cache.get("*")
        if entry and entry[2] == day:
            _cache["*"] = (round(entry[0] + cost, 6), entry[1], day)


def global_spend_today():
    """Every user's attributed spend today in USD, or None if it could not be read."""
    return _cached_total("*", {})


def circuit_open():
    """True when today's TOTAL spend is past the global ceiling.

    Callers must DEGRADE rather than error: every paid branch already has a free path it
    takes when no API key is configured, and that is the path to take here. A degraded app is
    the correct failure direction for a billing incident; a broken one is not.
    """
    if GLOBAL_DAILY_BUDGET_USD <= 0:                    # layer disabled by the operator
        return False
    spent = global_spend_today()
    if spent is None or spent < GLOBAL_DAILY_BUDGET_USD:
        return False
    print(f"[ALERT] Global daily spend circuit breaker OPEN: ${spent:.4f} of "
          f"${GLOBAL_DAILY_BUDGET_USD:.2f}. Paid branches are serving cached/mock results.")
    return True


def forced_recheck_ok(userid, opp_id):
    """True if this user may force a paid re-check of this row past its fresh cache.

    Only consulted when refresh=1 would ACTUALLY bypass a fresh cache — a stale row would be
    re-checked by any passive load anyway, so charging the cooldown for it would penalise
    normal use while stopping nothing.
    """
    if FORCED_RECHECK_MAX_PER_WINDOW <= 0:
        return True
    return forced_recheck_limiter.allow(f"{(userid or '').strip().lower()}:{opp_id}")


def forced_recheck_retry_after(userid, opp_id):
    return forced_recheck_limiter.retry_after(f"{(userid or '').strip().lower()}:{opp_id}")


# ---------- The two-tier AI allowance (MARQUEE M11; TWO_TIER_AI_PLAN.md §3-4) ----------
#
# The Free tier is metered in ACTIONS/day, pooled and reset at midnight UTC. One user action
# can fan out to several billed provider calls, so calls are collapsed into actions here:
#
#   COLLAPSE classes (profile / find_matches / deadline) — a burst of same-class calls within
#     FREE_TIER_ACTION_WINDOW_SECONDS is ONE action. A profile chat session (~6 calls), a
#     find-matches run (2 calls) and a Quest-Log "check for updates" tap (fanned across every
#     tracked row) each cost the student 1 of their 10.
#   PER-CALL classes (track / resume) — each call is its own action: adding an opportunity is
#     "1 each" and a resume import is "1 per import" (the plan's own wording).
#
# The ledger is IN-PROCESS and best-effort, exactly like the spend cache and the rate limiter:
# it resets on restart (fail-OPEN — a user gets a fresh allowance, never a wrongful lockout) and
# each worker keeps its own copy. Acceptable because (a) the plan accepts a small overshoot,
# bounded by the per-user AI rate limiter, and (b) the dollar backstop and the global circuit
# breaker still apply underneath. Durable cross-worker state is a later step (a table) if ever
# wanted; it is not needed to ship this behind FREE_TIER_AI_GATE_ENFORCED.

# cost_feature -> action class. cost_feature is the value each paid call passes to
# record_user_cost (the M11 seam), so this is keyed on what actually reaches the ledger. A
# feature with NO class (e.g. action_items) is a paid call that is costed (M9/M11-counted) but
# is not one of the metered user actions — it is bounded by the dollar backstop instead.
ACTION_CLASSES = {
    # Profile: the whole chat session + synthesis + the derived-slot refresh are one "update".
    "profile_synthesis": "profile", "chat_starters": "profile", "profile_chat": "profile",
    "tag_intent": "profile", "profile_extract": "profile", "tag_suggestions": "profile",
    # Find matches: rankCandidates + inferSubjects are one "run".
    "ranking": "find_matches", "infer_subjects": "find_matches",
    # Track: one extraction per opportunity added — 1 each.
    "tracker_extract": "track",
    # Deadline "Check for updates": one tap fans out across the Quest Log — 1 per tap.
    "deadline_check": "deadline",
    # Resume / LinkedIn import — 1 per import. Both surfaces record cost as "resume_import".
    "resume_import": "resume",
}
# Classes where each call is its own action (no burst collapse).
PER_CALL_ACTION_CLASSES = frozenset({"track", "resume"})

_action_lock = threading.Lock()
_action_ledger = {}      # (userid, day) -> {"buckets": set((class, bucket)), "percall": int}


def action_class_for(feature):
    """The metered action class for a cost_feature, or None if it is not a metered action."""
    return ACTION_CLASSES.get(feature)


def _action_bucket(now=None):
    return int((now if now is not None else time.time())
               // max(1, FREE_TIER_ACTION_WINDOW_SECONDS))


def _utc_reset_iso(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    nxt = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.isoformat()


def _prune_action_ledger(day):
    for k in [k for k in _action_ledger if k[1] != day]:
        _action_ledger.pop(k, None)


def note_action(userid, feature):
    """Fold a billed call into the caller's daily action count.

    Called from record_user_cost (the M11 seam), so it fires for every paid call. Unmetered
    features are ignored here — they are still costed in user_costs, which is what M11 needs.
    """
    userid = (userid or "").strip().lower()
    cls = action_class_for(feature)
    if not userid or cls is None:
        return
    day = _today()
    with _action_lock:
        _prune_action_ledger(day)
        led = _action_ledger.setdefault((userid, day), {"buckets": set(), "percall": 0})
        if cls in PER_CALL_ACTION_CLASSES:
            led["percall"] += 1
        else:
            led["buckets"].add((cls, _action_bucket()))


def user_actions_today(userid):
    """Distinct metered actions this user has taken today (in-process, fail-open to 0)."""
    userid = (userid or "").strip().lower()
    if not userid:
        return 0
    day = _today()
    with _action_lock:
        led = _action_ledger.get((userid, day))
        return (len(led["buckets"]) + led["percall"]) if led else 0


def _is_new_action(userid, feature, now=None):
    """Would a call for `feature` right now start a NEW action (vs continue a counted one)?"""
    cls = action_class_for(feature)
    if cls is None:
        return False
    if cls in PER_CALL_ACTION_CLASSES:
        return True
    key = (cls, _action_bucket(now))
    with _action_lock:
        led = _action_ledger.get((userid, _today()))
        return not (led and key in led["buckets"])


def _created_today(record, now=None):
    created = (record or {}).get("created_at")
    if not created:
        return False
    try:
        when = datetime.datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    today = (now or datetime.datetime.now(datetime.timezone.utc)).date()
    return when.date() == today


def ai_allowance_state(userid, feature=None, now=None):
    """Resolve the Free-tier daily AI allowance for this user and this call.

    Both a GATE (`over` means refuse) and a REPORT (`used`/`limit`/`remaining` render the
    client's meter, even on a successful call). Paid tier is unlimited and reads no counters.

    Free tier is `over` only when the ACTION allowance trips: a call would start a NEW action
    beyond the day's limit, and FREE_TIER_AI_GATE_ENFORCED is on. Fail-open throughout: the
    action ledger reads 0 rather than None. (The per-user dollar backstop that used to also
    gate here was removed — the global circuit breaker is the only remaining spend guard, and
    it degrades the whole app rather than blocking one user, so it is not consulted here.)
    """
    userid = (userid or "").strip().lower()
    reset_at = _utc_reset_iso(now)
    record = None
    if userid:
        try:
            record = get_user_subscription(userid)
        except Exception:                                          # noqa: BLE001
            record = None
    tier = ai_tier(record or {})
    if userid and userid in BUDGET_EXEMPT_USERIDS:
        tier = "paid"                                              # operator override
    if tier == "paid":
        return {"tier": "paid", "unlimited": True, "over": False,
                "used": None, "limit": None, "remaining": None,
                "reset_at": reset_at, "reason": None}

    limit = FIRST_DAY_AI_ACTIONS if _created_today(record, now) else FREE_TIER_DAILY_AI_ACTIONS
    used = user_actions_today(userid)
    new_action = _is_new_action(userid, feature, now) if feature else False
    over = bool(FREE_TIER_AI_GATE_ENFORCED and new_action and used >= limit)
    reason = None
    if over:
        reason = ("You've used today's AI actions. They reset at midnight UTC — everything "
                  "you've saved to your profile and Quest Log stays put. Upgrade to Wingman "
                  "Unlimited for no daily cap.")
    return {"tier": "free", "unlimited": False, "over": over,
            "used": used, "limit": limit, "remaining": max(0, limit - used),
            "reset_at": reset_at, "reason": reason}
