"""On-demand deadline-check cache helpers (read/write the opportunities row and
the deadline_check_log). Extracted verbatim from server.py (docs/archive/PLAN_1_decompose.md).
The actual paid Claude check lives in check_deadlines.check_one(), called from the
route; this module only handles caching, logging, and the mock payload.
"""
import datetime
import json
import urllib.parse
import urllib.request

from app.config import *  # noqa: F401,F403
from app.core import _supabase_request
from app.services.ai import mock_deadline_iso

DEADLINE_STALE_DAYS = 7
# NOTE the column is dates_last_checked_at, NOT last_checked_at (that name only ever
# existed in agents/check_deadlines.py's DDL comment). Selecting the wrong name made PostgREST
# 400 the whole select, so every on-demand deadline check 502'd (found 2026-08-23).
DEADLINE_FIELDS = "id,name,org,url,summary,status,important_dates,was_estimated,important_date_note,dates_last_checked_at"


def get_opportunity_for_deadline_check(opp_id):
    query = urllib.parse.urlencode({"select": DEADLINE_FIELDS, "id": f"eq.{opp_id}"})
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        rows = json.loads(resp.read())
    return rows[0] if rows else None


# Columns the free batch sync reads. Deadline set (above) plus the task columns, so one
# request mirrors BOTH the deadline and action-item caches for a set of tracked ids.
SYNC_FIELDS = (DEADLINE_FIELDS
               + ",action_items,action_items_source,action_items_checked_at")

# A tracked id is our own catalog id (e.g. "ec18286") or a user-added slug. Neither can
# legitimately contain the characters PostgREST's `in.(...)` list uses as separators, so
# stripping them is both a sanitiser (no filter injection) and a no-op for real ids.
_ID_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")

# Cap per request so a pathological tracker cannot ask for the whole table in one `in.()`.
MAX_SYNC_IDS = 300


def _safe_ids(ids):
    out = []
    for raw in ids:
        cleaned = "".join(c for c in str(raw) if c in _ID_SAFE)
        if cleaned:
            out.append(cleaned)
    return out[:MAX_SYNC_IDS]


def get_cached_tracker_data(ids):
    """{id: cached deadline+task payload} for the given tracked ids — a READ-ONLY mirror of
    the catalog's CURRENT values. Never triggers a paid check; that stays on the deadline /
    action-items endpoints and the Update-now button. Ids with no catalog row are simply
    absent from the result (the client keeps its own snapshot for those).

    `source` per item follows the same rule the interactive endpoint uses so the client's
    verified-source gate behaves identically: a row that was really checked
    (dates_last_checked_at present) is `cached` (verified — an empty important_dates may clear
    a stale snapshot, e.g. a rolling program); a row never checked is `unverified-cache`
    (NOT verified — an empty list must not wipe the add-time snapshot).
    """
    ids = _safe_ids(ids)
    if not ids:
        return {}
    query = urllib.parse.urlencode({"select": SYNC_FIELDS, "id": f"in.({','.join(ids)})"})
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        rows = json.loads(resp.read())
    out = {}
    for r in rows:
        checked = r.get("dates_last_checked_at")
        out[r["id"]] = {
            "status": r.get("status"),
            "important_dates": r.get("important_dates") or [],
            "was_estimated": r.get("was_estimated"),
            "important_date_note": r.get("important_date_note"),
            "dates_last_checked_at": checked,
            "source": "cached" if checked else "unverified-cache",
            "action_items": r.get("action_items") or [],
            "action_items_source": r.get("action_items_source"),
        }
    return out


def patch_opportunity_deadline(opp_id, patch):
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


def log_deadline_check(opp_id, source, status, web_searches, cost_usd, was_estimated, notes=None):
    """Log a deadline check to the deadline_check_log table (non-blocking)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        log_entry = {
            "opportunity_id": opp_id,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": source,
            "status": status,
            "web_searches": web_searches,
            "cost_usd": round(cost_usd, 4) if cost_usd else None,
            "was_estimated": was_estimated,
            "notes": notes,
        }
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/deadline_check_log",
            data=json.dumps(log_entry).encode(),
            method="POST",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as e:
        # Logging failure should not break the main request
        print(f"[WARN] Failed to log deadline check for {opp_id}: {e}")


def deadline_cache_is_fresh(last_checked_at):
    if not last_checked_at:
        return False
    try:
        checked = datetime.datetime.fromisoformat(last_checked_at.replace("Z", "+00:00"))
    except Exception:
        return False
    return datetime.datetime.now(datetime.timezone.utc) - checked < datetime.timedelta(days=DEADLINE_STALE_DAYS)


def cached_deadline_payload(opp, source):
    return {
        "status": opp.get("status"),
        "important_dates": opp.get("important_dates") or [],
        "was_estimated": opp.get("was_estimated"),
        "important_date_note": opp.get("important_date_note"),
        "dates_last_checked_at": opp.get("dates_last_checked_at"),
        "source": source,
    }


def mock_deadline_check_payload(opp):
    # MOCK mode (no GEMINI_API_KEY): fabricate a plausible response, same spirit as
    # generate_mock_text()'s GEMINI_API_KEY fallback, but deliberately does NOT write
    # to Supabase — a mock value getting cached and served to real users for 7 days would
    # be worse than just re-fabricating it every time mock mode is active.
    deadline_iso = mock_deadline_iso((opp.get("name") or "") + (opp.get("url") or ""))
    return {
        "status": "running",
        "important_dates": [{"label": "Application Deadline", "date_iso": deadline_iso, "type": "deadline"}],
        "was_estimated": True,
        "important_date_note": "Mock data — set GEMINI_API_KEY for a real, live-searched check.",
        "dates_last_checked_at": None,
        "source": "mock",
    }
