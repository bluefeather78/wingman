"""On-demand action items for one opportunity — the interactive twin of the batch pass in
generate_action_items.py, which owns the logic. This module is plumbing only: read the row,
serve what is already stored, and generate-and-cache when nothing is.

WHY IT SHARES generate_action_items RATHER THAN REIMPLEMENTING
The batch loop and this endpoint must never disagree about what counts as a proven task.
That is the same rule check_deadlines.deadline_write_decision() enforces for dates, and it
exists because the two paths did drift there once. Every judgement here — fetch, verify,
what may be written, what may be stamped — comes from the agent module, imported. Nothing in
this file decides anything about trust.

WHAT IT IS FOR
The batch covers the active catalog. It cannot cover a row the scraper inserted last night,
a user-submitted opportunity resolved minutes ago, or a row whose page was refusing our
client the last time the agent ran. Without this, tracking any of those means falling back
to a task list nothing verified — which is the state the whole fix exists to leave behind.
"""
import datetime
import json
import urllib.parse
import urllib.request

from app.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY
from generate_action_items import (
    SELECT as ACTION_ITEM_FIELDS,
    STAMPING_SOURCES,
    generic_items,
    process_one,
)

# On-demand staleness window. Deliberately SHORTER than the batch agent's 90-day
# STALE_AFTER_DAYS: the batch is a bulk-coverage knob over the whole catalog, this is the
# per-view freshness a student actually experiences. Two things hinge on it (P1, 2026-08-25):
#   * A row whose last write did NOT stamp action_items_checked_at (a generic-fallback from
#     an unreachable page, or an unparsed model reply) has checked_at = NULL, reads as stale,
#     and so is retried on the next view — which is how a user-added / newly-scraped / then-
#     -unreachable row self-heals instead of serving an unverified list forever.
#   * A genuinely verified list (page-verified / page-empty, which DO stamp) is served free
#     for 7 days, then re-verified once. Requirements move ~annually, so the churn is only on
#     rows a student is actively looking at, and the cost is accepted (decision 7).
TASK_TTL_DAYS = 7

# PostgREST 400s an entire select on one unknown column, so a catalog that has not had
# action_items_schema.sql run against it would break the read outright. Latched after the
# first failure — the same degrade-rather-than-break shape get_user_account() uses, and for
# the same reason: this sits on a path a student is waiting on.
_columns_missing = False


def _get(query):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        headers={"apikey": SUPABASE_SERVICE_KEY,
                 "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def get_opportunity_for_action_items(opp_id):
    """The row, or None. Returns None for `columns_missing` too — the caller cannot tell the
    difference and does not need to; both mean 'serve nothing from the catalog'."""
    global _columns_missing
    if not _columns_missing:
        try:
            rows = _get(urllib.parse.urlencode(
                {"select": ACTION_ITEM_FIELDS, "id": f"eq.{opp_id}"}))
            return rows[0] if rows else None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if "action_items" not in body:
                raise
            _columns_missing = True
            print("[WARN] opportunities.action_items columns are missing — run "
                  "action_items_schema.sql. Serving unverified per-student tasks until then.")
    return None


def patch_action_items(opp_id, patch):
    query = urllib.parse.urlencode({"id": f"eq.{opp_id}"})
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        data=json.dumps(patch).encode(),
        method="PATCH",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


# Tiers that must NEVER reach a student (P5). Independent of how the item was tagged at
# generation time — the serve path filters again so a generation bug can't leak an
# un-approved aggregator source (DEADLINE_AND_TASK_PLAN.md §4, "enforced at BOTH generation
# and serve time"). A no-op today: nothing writes source_tier until P6, and legacy items
# (no tier / basis:'page' / basis:'generic') carry none, so they all pass.
WITHHELD_TIERS = {"pending", "blocked"}


def _servable(items):
    """Drop items whose trust tier is pending/blocked. An item with no source_tier is kept —
    absence of a tier is the pre-P6 official/generic case, not an un-approved aggregator."""
    kept = []
    for it in items or []:
        if isinstance(it, dict) and it.get("source_tier") in WITHHELD_TIERS:
            continue
        kept.append(it)
    return kept


def payload(items, source):
    return {"action_items": _servable(items), "source": source}


def _is_fresh(checked_at):
    """True when a stamp exists and is within TASK_TTL_DAYS. A NULL/blank/unparseable stamp
    reads as stale so the row is retried — the same direction check_deadlines errs, since a
    missing stamp means the last write never verified the page."""
    if not checked_at:
        return False
    try:
        stamped = datetime.datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=datetime.timezone.utc)
    age = datetime.datetime.now(datetime.timezone.utc) - stamped
    return age <= datetime.timedelta(days=TASK_TTL_DAYS)


def resolve(opp_id):
    """(payload, cost). Serves the stored list while it is fresh, otherwise re-runs the same
    fetch-verify-decide pipeline the batch uses and caches the result on the row.

    Freshness is TASK_TTL_DAYS on action_items_checked_at, the on-demand twin of the
    deadline endpoint's cache. A stamped, in-window list is served free; a stale or never-
    stamped one is re-verified (a re-verify writes nothing and does not stamp when the page
    cannot be read, so an unreachable row stays due rather than freezing an unverified list).
    """
    opp = get_opportunity_for_action_items(opp_id)
    if not opp:
        return None, 0.0

    stored = opp.get("action_items")
    has_stored = isinstance(stored, list) and bool(stored)
    if has_stored and _is_fresh(opp.get("action_items_checked_at")):
        return payload(stored, opp.get("action_items_source") or "stored"), 0.0

    # Stale, or nothing stored. With no key we cannot call a model — keep an existing list
    # rather than replacing it with generic (it is at least what the batch last verified),
    # and otherwise give the student an honest generic checklist for free. Generic items
    # assert nothing about the program, so producing them without reading anything is safe.
    if not ANTHROPIC_API_KEY:
        if has_stored:
            return payload(stored, opp.get("action_items_source") or "stored"), 0.0
        return payload(generic_items(opp), "generic-fallback"), 0.0

    try:
        # full_capture=True (T6): go through the shared finder with the date ladder too, so a
        # deadline check firing alongside this reads the program ONCE (the finder caches the
        # full capture per opportunity). Also gives tasks the same thorough sub-page discovery.
        decision, cost, _stats, _reason = process_one(opp, ANTHROPIC_API_KEY, full_capture=True)
    except Exception as e:
        print(f"[WARN] action-item generation failed for {opp_id}: {e}")
        # Never blank a verified list because a re-verify raised. Keep what we have.
        if has_stored:
            return payload(stored, opp.get("action_items_source") or "stored"), 0.0
        return payload(generic_items(opp), "generic-fallback"), 0.0

    if decision.write:
        patch = {"action_items": decision.items,
                 "action_items_source": decision.source}
        # Same stamping rule as the batch, imported rather than restated: only a genuine
        # read of the page marks the row done. A page that refused us leaves it due, so a
        # later run — or the batch — retries instead of the row being frozen for 90 days
        # behind a transient 403.
        if decision.stamp and decision.source in STAMPING_SOURCES:
            import datetime
            patch["action_items_checked_at"] = datetime.datetime.now(
                datetime.timezone.utc).isoformat()
        try:
            patch_action_items(opp_id, patch)
        except Exception as e:
            # The student still gets the list; it just was not cached. Worth a line in the
            # log because the symptom otherwise is "this row is paid for on every add".
            print(f"[WARN] could not cache action items for {opp_id}: {e}")

    return payload(decision.items, decision.source), cost
