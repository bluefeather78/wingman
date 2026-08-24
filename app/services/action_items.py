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
import json
import urllib.parse
import urllib.request

from app.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY
from generate_action_items import (
    SELECT as ACTION_ITEM_FIELDS,
    STAMPING_SOURCES,
    generic_items,
    process_one,
)

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


def payload(items, source):
    return {"action_items": items or [], "source": source}


def resolve(opp_id):
    """(payload, cost). Serves the stored list when there is one, otherwise runs the same
    fetch-verify-decide pipeline the batch uses and caches the result on the row.

    Deliberately NOT re-checked on a schedule here. A stored list is served as-is however
    old it is: the batch agent owns freshness (its own staleness window), and re-billing a
    student's page load to re-verify a list that is almost certainly unchanged is the
    trade the deadline endpoint's 7-day cache already decided in the other direction, for
    data that actually moves weekly. Requirements do not.
    """
    opp = get_opportunity_for_action_items(opp_id)
    if not opp:
        return None, 0.0

    stored = opp.get("action_items")
    if isinstance(stored, list) and stored:
        return payload(stored, opp.get("action_items_source") or "stored"), 0.0

    # Nothing stored. With no key we cannot call a model, but we can still give the student
    # an honest checklist for free rather than nothing — generic items assert nothing about
    # the program, so producing them without reading anything is defensible.
    if not GEMINI_API_KEY:
        return payload(generic_items(opp), "generic-fallback"), 0.0

    try:
        decision, cost, _stats, _reason = process_one(opp, GEMINI_API_KEY)
    except Exception as e:
        print(f"[WARN] action-item generation failed for {opp_id}: {e}")
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
