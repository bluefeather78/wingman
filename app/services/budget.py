"""Spend caps — the three layers that make the app refuse a paid call (S0-5, finding H4).

Everything else in this repo RECORDS spend: agent_runs rolls up what the app spent,
user_costs decomposes it per user and feature, deadline_check_log logs each check. Nothing
read any of it back to refuse anything. So one 7-day trial account — which costs $0 — could
loop GET /api/opportunities/<id>/deadline?refresh=1 across the catalog: refresh=1 bypassed
the 7-day cache unconditionally and each verified check measures ~$0.07, i.e. ~$90 per pass
over 1,300 rows, repeatable. /api/match is a few cents a call, also unbounded.

Three independent layers, because each covers a hole the others do not:

  1. over_user_budget()   — a per-user daily ceiling. Bounds what one account can spend.
  2. forced_recheck_ok()  — a per-user, per-row cooldown on the refresh=1 cache bypass.
                            The budget alone still allows a fast burn; the bypass is the
                            amplifier, so it gets its own limit.
  3. circuit_open()       — a global daily ceiling. Above it every paid branch degrades to
                            its existing cached/mock path, turning a billing incident into a
                            degraded app rather than an invoice.

Layers 1 and 3 read user_costs, which is the complete ledger for interactive spend: every
paid branch in app/ routes its cost through record_user_cost (the AI proxies and /api/match
via record_interactive_cost, the deadline check and action items via record_user_cost_async).

FAILING OPEN IS DELIBERATE. If Supabase cannot be read the caps do not apply, exactly as
subscription_block_reason already chooses: a database blip must not lock out or degrade
every paying user. It does mean the caps are not a defence against an attacker who can break
the read — they are a spend bound, not an access control. The access control is S0-1's gate.

The reads are cached for BUDGET_CACHE_TTL_SECONDS and bumped in-process by note_spend() as
costs are recorded, so a burst inside one window still sees its own spending. How far a user
can overshoot inside a window is bounded by the AI rate limiter (S0-2).
"""
import datetime
import threading
import time

from app.config import (USER_DAILY_BUDGET_USD, GLOBAL_DAILY_BUDGET_USD,
                        BUDGET_EXEMPT_USERIDS, BUDGET_CACHE_TTL_SECONDS,
                        FORCED_RECHECK_WINDOW_SECONDS, FORCED_RECHECK_MAX_PER_WINDOW,
                        SUPABASE_URL, SUPABASE_SERVICE_KEY)
from app.core import _supabase_request, pseudonym
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
    """Add a just-recorded cost to the cached totals, so a burst inside one TTL window sees
    its own spending instead of re-reading a stale figure up to a minute old.

    Called from record_user_cost's background thread — never on the request path.
    """
    try:
        cost = float(cost or 0)
    except (TypeError, ValueError):
        return
    if cost <= 0:
        return
    day = _today()
    keys = ["*"]
    if userid:
        keys.append(str(userid).strip().lower())
    with _lock:
        for key in keys:
            entry = _cache.get(key)
            if entry and entry[2] == day:
                _cache[key] = (round(entry[0] + cost, 6), entry[1], day)


def user_spend_today(userid):
    """This user's attributed spend today in USD, or None if it could not be read."""
    userid = (userid or "").strip().lower()
    if not userid:
        return None
    return _cached_total(userid, {"userid": f"eq.{userid}"})


def global_spend_today():
    """Every user's attributed spend today in USD, or None if it could not be read."""
    return _cached_total("*", {})


def over_user_budget(userid):
    """The message to show this user if they have spent their daily allowance, else None.

    Wording is deliberately not an accusation: the overwhelming majority of anyone who ever
    sees this will be a student who used the app hard, not an attacker.
    """
    if USER_DAILY_BUDGET_USD <= 0:                      # layer disabled by the operator
        return None
    userid = (userid or "").strip().lower()
    if not userid or userid in BUDGET_EXEMPT_USERIDS:   # the operator override
        return None
    spent = user_spend_today(userid)
    if spent is None or spent < USER_DAILY_BUDGET_USD:
        return None
    print(f"[WARN] Daily budget reached for user {pseudonym(userid)}: ${spent:.4f} of "
          f"${USER_DAILY_BUDGET_USD:.2f}")
    return ("You've used up today's AI allowance. It resets at midnight UTC — "
            "everything already saved to your profile and Quest Log stays put.")


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
