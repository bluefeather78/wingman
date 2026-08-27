"""Deadline-alert reader and rung engine — the pure core of the deadline-reminder email.

This module answers two questions and touches no network:

  1. READER (extract_deadline_units): given one users row, which tracked deadlines are worth
     alerting on, and how many days out is each? This is the one genuinely new engineering in
     the feature — a SERVER-SIDE reader of client-owned tracker state. The tracker lives in
     the users.data jsonb under key `hs-tracker-data`, written by the RN app
     (frontend/src/api/trackerStore.ts). Nothing in app/ parses it today; the calendar sync
     does the equivalent in TypeScript (collectTrackedDeadlineEvents / status.ts). This is a
     second implementation of that one contract, so it MUST mirror those rules, and P0's job
     is to pin it with a fixture taken from a real blob.

  2. ENGINE (assign_rung / due_alerts): given the units and today, which (unit, rung) pairs a
     sweep would send right now, and what dedupe key does each claim under? See
     DEADLINE_EMAIL_ALERTS_PLAN.md §3-§4.

WHY THE READER IS DEFENSIVE TO A FAULT. The blob was written by whatever bundle version the
student last ran, so its shape drifts: dates are camelCase `dateISO` off the client but
`date_iso` off the API, a bucket may be missing, a value may be a string where an object is
expected. Every malformed thing is SKIPPED and COUNTED in stats, never raised — this reader
runs over the whole roster in an unattended sweep, and one student's corrupt blob must not
stop everyone else's reminders. Same posture as every other whole-catalog reader here.

SCOPE NOTE (v1, and the P0 boundary). This reads STORED dates as-is. It deliberately does
NOT replicate the client's cycle-year projection (status.ts cycleYearShift, which rolls an
annual program's past date forward to the next cycle for DISPLAY). Porting that exactly —
and proving it against a fixture — is P0 work; until then a program whose only stored date
is last cycle's simply produces no unit, which fails safe (no alert) rather than alerting on
a date we projected. What IS mirrored here already: the not_running exclusion, the
saved-for-later exclusion, deadline-type-only, and never alerting on a past date.
"""
import calendar
import datetime
import json

from app.config import DEADLINE_ALERT_RUNGS

# Only this ImportantDate.type can be MISSED, which is what a reminder is for. `opens`,
# `event_start`/`event_end` and `other` are real dates a v2 may alert on with different copy;
# v1 is deadlines only. See the plan §2.
DEADLINE_TYPE = "deadline"

TRACKER_KEY = "hs-tracker-data"
SAVED_KEY = "hs-tracker-saved"


def _loads(value):
    """A stored key is either a JSON *string* (the client's convention for hs-tracker-data)
    or an already-parsed object (jsonb that PostgREST decoded). Accept both; return None on
    anything unreadable rather than raising."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return None


def _safe_url(value):
    """A tracked opportunity's URL, but only if it is a plain web link. An email link must
    never carry a `javascript:`/`data:` scheme (the blob is the student's own tracked data,
    but it is still untrusted input being put into a document), so anything that is not
    http(s) is dropped and the name renders as plain text."""
    if not value:
        return ""
    s = str(value).strip()
    low = s.lower()
    return s if low.startswith("http://") or low.startswith("https://") else ""


def _parse_date(value):
    """A stored date is either date-only ('2026-09-01') or a full ISO timestamp. Only the
    calendar date matters for a days-out count, so take the first 10 chars and parse that.
    Returns a datetime.date or None."""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10])
    except (ValueError, TypeError):
        return None


# ---------------- Cycle-year projection (mirrors status.ts) ----------------
#
# An annual program whose whole stored cycle is in the past is NOT over — it recurs, and the
# app rolls its dates forward by a whole year (the smallest shift that brings the LAST date
# back into the future) and shows THOSE everywhere. This reader must do the same, or it would
# see only a past date, skip it, and never alert on a program that is in fact opening again
# soon. Ported verbatim from frontend/src/lib/status.ts cycleYearShift / addYearsISO.
#
# The shift is a single whole-year offset applied to EVERY date, never a per-date roll — that
# preserves the intervals between opens/deadline/event, which a per-date roll distorts when a
# cycle straddles a year boundary. A projected date is an estimate by construction, so the
# caller forces estimated=True on it whatever the stored flag says (see extract).
#
# Note: a far-forward projection (a genuinely one-time past program has no recurrence flag, so
# it projects too) is harmless here — its days_left lands well above the ladder, so due_alerts
# drops it and no spurious reminder fires. The projection only ever MATTERS when the next
# cycle is actually near.

def _add_years_iso(iso, years):
    """Add whole years to a 'YYYY-MM-DD' string, clamping Feb 29 -> Feb 28 in a non-leap
    target year rather than letting the date roll into March (which would move a deadline)."""
    y, m, d = (int(x) for x in iso.split("-"))
    ny = y + years
    last_day = calendar.monthrange(ny, m)[1]
    return f"{ny:04d}-{m:02d}-{min(d, last_day):02d}"


def _days_until(iso, today):
    parsed = _parse_date(iso)
    return None if parsed is None else (parsed - today).days


def _cycle_year_shift(status, raw_isos, today):
    """The whole-year offset to bring this item's cycle back into the future, or 0.

    `raw_isos` is EVERY date on the item (any type), date-only normalized — the shift is
    computed over the full set (its last date), then applied to the deadline dates the caller
    keeps. Mirrors status.ts: not_running / rolling never project; an item with a
    still-future last date needs no shift.
    """
    if status in ("not_running", "rolling"):
        return 0
    if not raw_isos:
        return 0
    last = max(raw_isos)  # ISO date strings sort chronologically
    if (_days_until(last, today) or 0) >= 0:
        return 0
    n = max(1, today.year - int(last[:4]))
    while (_days_until(_add_years_iso(last, n), today) or 0) < 0:
        n += 1
    return n


def _estimated_flag(date_obj):
    """Tri-state, deliberately. True/False are the stored ImportantDate.estimated; None means
    the field is ABSENT (every date written before 2026-08-24). Unknown is treated as
    estimated by the renderer, never as confirmed — the standing rule that a guess is never
    dressed as a fact on no evidence. The caller decides how to render None; this only
    reports what the blob says."""
    v = date_obj.get("estimated")
    if v is True:
        return True
    if v is False:
        return False
    return None


def extract_deadline_units(record, today):
    """Parse one users row into a list of deadline UNITS plus a stats dict.

    A unit is a plain dict: item_id, item_name, org, date_iso (normalized to date-only),
    label, date_type, estimated (True/False/None), days_left (>= 0, UTC-date arithmetic
    against `today`). Sorted soonest-first.

    Exclusions, mirroring status.ts / collectTrackedDeadlineEvents:
      - item.status == 'not_running'      -> whole item skipped (dead/discontinued program;
                                             its stored dates are real PAST dates)
      - hs-tracker-saved[id] is True      -> whole item skipped (explicitly parked)
      - date.type != 'deadline'           -> that date skipped (v1 scope)
      - days_left < 0                     -> that date skipped (never alert on the past)
    Anything malformed is skipped and counted, never raised.
    """
    stats = {
        "unparseable_blobs": 0,
        "items_seen": 0,
        "items_skipped_not_running": 0,
        "items_skipped_saved": 0,
        "dates_skipped": 0,
    }

    data = (record or {}).get("data")
    data = _loads(data) if isinstance(data, str) else (data or {})
    if not isinstance(data, dict):
        stats["unparseable_blobs"] += 1
        return [], stats

    tracker = _loads(data.get(TRACKER_KEY))
    if not isinstance(tracker, dict):
        # No tracker (or an unreadable one) is not an error for a real account — most rows
        # simply have nothing tracked. Only count it as unparseable when a value was present
        # but could not be read, so the stat means "corrupt", not "empty".
        if data.get(TRACKER_KEY) not in (None, "", {}):
            stats["unparseable_blobs"] += 1
        return [], stats

    saved = _loads(data.get(SAVED_KEY))
    if not isinstance(saved, dict):
        saved = {}

    units = []
    # Iterate the buckets present rather than a hardcoded ALL_BUCKETS list: a missing or an
    # extra bucket then costs nothing, and there is no Python copy of the bucket names to
    # drift from the client's.
    for items in tracker.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                stats["dates_skipped"] += 1
                continue
            stats["items_seen"] += 1

            if item.get("status") == "not_running":
                stats["items_skipped_not_running"] += 1
                continue

            item_id = item.get("id")
            if item_id is not None and saved.get(item_id) is True:
                stats["items_skipped_saved"] += 1
                continue

            dates = item.get("importantDates")
            if not isinstance(dates, list):
                dates = item.get("important_dates")
            if not isinstance(dates, list):
                continue

            # The cycle shift is computed over EVERY date on the item (any type), then applied
            # to the deadline dates below — see _cycle_year_shift. camelCase off the client,
            # snake_case off the API; accept both, because the blob has been written by every
            # bundle version a student ever ran.
            raw_isos = []
            for date_obj in dates:
                if isinstance(date_obj, dict):
                    parsed = _parse_date(date_obj.get("dateISO") or date_obj.get("date_iso"))
                    if parsed:
                        raw_isos.append(parsed.isoformat())
            shift = _cycle_year_shift(item.get("status"), raw_isos, today)

            for date_obj in dates:
                if not isinstance(date_obj, dict):
                    stats["dates_skipped"] += 1
                    continue
                if date_obj.get("type") != DEADLINE_TYPE:
                    continue
                parsed = _parse_date(date_obj.get("dateISO") or date_obj.get("date_iso"))
                if not parsed:
                    stats["dates_skipped"] += 1
                    continue
                date_iso = _add_years_iso(parsed.isoformat(), shift) if shift else parsed.isoformat()
                days_left = (_parse_date(date_iso) - today).days
                # Still skip a date in the past even after projection — e.g. a deadline that
                # already passed THIS cycle while a later event pulled the shift forward.
                if days_left < 0:
                    continue
                units.append({
                    "item_id": item_id,
                    "item_name": item.get("name") or "This opportunity",
                    "org": item.get("org") or "",
                    # The opportunity's own page, so the email can link the name to it. Falls
                    # back to the application link if there is no page URL.
                    "url": _safe_url(item.get("url") or item.get("applyUrl")
                                     or item.get("apply_url")),
                    "date_iso": date_iso,
                    "label": date_obj.get("label") or "Deadline",
                    "date_type": DEADLINE_TYPE,
                    # A projected date is a guess by construction, so it OVERRIDES the stored
                    # flag — exactly getDisplayMilestones' `shift > 0 || estimated === true`.
                    "estimated": True if shift else _estimated_flag(date_obj),
                    "projected": bool(shift),
                    "days_left": days_left,
                })

    units.sort(key=lambda u: (u["days_left"], u["item_name"]))
    return units, stats


# ---------------- The rung engine ----------------

def assign_rung(days_left, rungs=DEADLINE_ALERT_RUNGS):
    """The smallest rung >= days_left, or None if the date is above the ladder (too far out
    to alert yet) or in the past. Window assignment, not a day-exact match: days 4-7 -> 7,
    2-3 -> 3, 0-1 -> 1. See the config constant for why this is what makes the ladder
    self-healing."""
    if days_left is None or days_left < 0:
        return None
    eligible = sorted(r for r in rungs if r >= days_left)
    return eligible[0] if eligible else None


def alert_dedupe_key(unit, rung):
    """The email_sends dedupe key for one (unit, rung). The DATE is in the key on purpose,
    exactly like trial_dedupe_key: a deadline that MOVES mints new keys and earns fresh
    reminders (the student's mental model is now wrong), while a deadline that stays put can
    only ever fire each rung once. This format is permanent once written to the table — it is
    pinned by a test for that reason."""
    return f"{unit.get('item_id')}:{unit.get('date_iso')}:{rung}"


def due_alerts(units, rungs=DEADLINE_ALERT_RUNGS):
    """The (unit, rung) pairs a sweep would claim right now — every unit whose days_left
    falls on the ladder, tagged with its rung, soonest-first. The claim table is what makes a
    repeated sweep a no-op; this just says what is eligible today."""
    out = []
    for unit in units:
        rung = assign_rung(unit.get("days_left"), rungs)
        if rung is None:
            continue
        out.append((unit, rung))
    out.sort(key=lambda ur: (ur[0]["days_left"], ur[0]["item_name"]))
    return out
