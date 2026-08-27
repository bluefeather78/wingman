"""P1 — the deadline-alert rung engine, plus the reader's exclusion rules.

The engine (assign_rung / due_alerts / alert_dedupe_key) is pure and table-driven here. The
reader (extract_deadline_units) gets its exclusion rules pinned here too; P0 adds the
exhaustive real-blob fixture. What these protect:

  1. Window assignment, not day-exact firing — the property that makes the ladder self-heal a
     missed sweep day and a late-tracked item (plan §3).
  2. The dedupe-key FORMAT, which becomes permanent the moment it is written to email_sends.
  3. The reader never alerting on the past, a saved-for-later item, a discontinued program,
     or a non-deadline date — and never raising on a malformed blob.
"""
import datetime
import json

import pytest

from app.services import deadline_alerts as da

TODAY = datetime.date(2026, 9, 1)


# ---------------- assign_rung: window assignment ----------------

@pytest.mark.parametrize("days_left,expected", [
    (0, 1),    # today -> the last rung
    (1, 1),    # tomorrow
    (2, 3),    # into the rung-3 window
    (3, 3),
    (4, 7),    # into the rung-7 window
    (7, 7),
    (8, None),  # above the ladder — not due yet
    (30, None),
    (-1, None),  # in the past — never
])
def test_assign_rung_picks_smallest_rung_at_or_above_days_left(days_left, expected):
    assert da.assign_rung(days_left) == expected


def test_assign_rung_handles_none():
    assert da.assign_rung(None) is None


def test_assign_rung_respects_a_custom_ladder():
    assert da.assign_rung(10, rungs=(14, 5)) == 14
    assert da.assign_rung(4, rungs=(14, 5)) == 5
    assert da.assign_rung(15, rungs=(14, 5)) is None


# ---------------- self-healing behaviour ----------------

def test_late_tracked_item_lands_in_one_rung_not_the_whole_backlog():
    """An item first seen at T-2 must produce a single alert (rung 3), never rungs 7+3 as a
    day-exact scheme would when it 'missed' the T-7 mark it was never tracked for."""
    unit = {"item_id": "x", "date_iso": "2026-09-03", "days_left": 2, "item_name": "X"}
    due = da.due_alerts([unit])
    assert len(due) == 1
    assert due[0][1] == 3


def test_a_missed_sweep_day_still_fires_within_the_window():
    """The sweep didn't run at T-3; at T-2 the item is still inside the rung-3 window and
    fires. Day-exact (days_left == 3) would have skipped it forever."""
    assert da.assign_rung(3) == 3
    assert da.assign_rung(2) == 3  # same rung one day later


# ---------------- dedupe key ----------------

def test_dedupe_key_format_is_item_date_rung():
    unit = {"item_id": "abc", "date_iso": "2026-09-03"}
    assert da.alert_dedupe_key(unit, 3) == "abc:2026-09-03:3"


def test_a_moved_deadline_mints_a_new_key():
    """The whole reason the date is in the key: a moved date is a new mental model and earns
    fresh reminders, while a stationary date can only ever fire each rung once."""
    unit_old = {"item_id": "abc", "date_iso": "2026-09-03"}
    unit_new = {"item_id": "abc", "date_iso": "2026-09-10"}
    assert da.alert_dedupe_key(unit_old, 3) != da.alert_dedupe_key(unit_new, 3)


# ---------------- due_alerts ordering + filtering ----------------

def test_due_alerts_are_sorted_soonest_first_and_drop_the_undue():
    units = [
        {"item_id": "far", "date_iso": "2026-09-20", "days_left": 19, "item_name": "Far"},
        {"item_id": "soon", "date_iso": "2026-09-02", "days_left": 1, "item_name": "Soon"},
        {"item_id": "mid", "date_iso": "2026-09-04", "days_left": 3, "item_name": "Mid"},
    ]
    due = da.due_alerts(units)
    assert [u["item_id"] for (u, _r) in due] == ["soon", "mid"]  # 'far' is above the ladder
    assert [rung for (_u, rung) in due] == [1, 3]


# ---------------- the reader: helpers ----------------

def _record(items_by_bucket, saved=None):
    """A users row whose data carries a JSON-STRING tracker, as the RN app writes it."""
    return {"data": {
        da.TRACKER_KEY: json.dumps(items_by_bucket),
        da.SAVED_KEY: saved or {},
    }}


def _item(id_, days, **over):
    date_iso = (TODAY + datetime.timedelta(days=days)).isoformat()
    item = {
        "id": id_, "name": f"Prog {id_}", "status": "running",
        "importantDates": [{"label": "Application deadline", "dateISO": date_iso,
                            "type": "deadline"}],
    }
    item.update(over)
    return item


# ---------------- the reader: exclusion rules ----------------

def test_reader_returns_future_deadlines_with_days_left():
    units, stats = da.extract_deadline_units(
        _record({"summerPrograms": [_item("a", 3)]}), TODAY)
    assert len(units) == 1
    u = units[0]
    assert u["item_id"] == "a" and u["days_left"] == 3
    assert u["date_iso"] == "2026-09-04" and u["date_type"] == "deadline"


def test_reader_projects_a_past_annual_deadline_forward():
    """A recurring program whose only stored deadline is in the past is NOT skipped — the
    app rolls it to next cycle and shows that, so the reader must too (status.ts
    cycleYearShift). It projects ~a year out, is flagged estimated + projected, and lands
    well above the ladder, so due_alerts drops it — no spurious 'deadline yesterday' alert
    and no spurious 'deadline in 364 days' one either."""
    units, _ = da.extract_deadline_units(
        _record({"summerPrograms": [_item("past", -1)]}), TODAY)
    assert len(units) == 1
    u = units[0]
    assert u["days_left"] > 300          # rolled forward, not skipped
    assert u["estimated"] is True        # a projected date is a guess by construction
    assert u["projected"] is True
    assert da.due_alerts(units) == []    # far future -> above the ladder -> not sent


def test_reader_skips_not_running_items():
    units, stats = da.extract_deadline_units(
        _record({"summerPrograms": [_item("dead", 3, status="not_running")]}), TODAY)
    assert units == []
    assert stats["items_skipped_not_running"] == 1


def test_reader_skips_saved_for_later_items():
    units, stats = da.extract_deadline_units(
        _record({"summerPrograms": [_item("parked", 3)]}, saved={"parked": True}), TODAY)
    assert units == []
    assert stats["items_skipped_saved"] == 1


def test_reader_skips_non_deadline_dates():
    item = _item("opens", 3)
    item["importantDates"][0]["type"] = "opens"
    units, _ = da.extract_deadline_units(_record({"summerPrograms": [item]}), TODAY)
    assert units == []


def test_reader_accepts_both_date_spellings():
    """camelCase off the client, snake_case off the API — the blob has been written by every
    bundle version a student ever ran."""
    snake = {"id": "s", "name": "Snake", "status": "running",
             "important_dates": [{"label": "Deadline",
                                  "date_iso": (TODAY + datetime.timedelta(days=2)).isoformat(),
                                  "type": "deadline"}]}
    units, _ = da.extract_deadline_units(_record({"summerPrograms": [snake]}), TODAY)
    assert len(units) == 1 and units[0]["days_left"] == 2


def test_reader_reports_estimated_as_tri_state():
    est_true = _item("t", 5)
    est_true["importantDates"][0]["estimated"] = True
    est_false = _item("f", 5)
    est_false["importantDates"][0]["estimated"] = False
    est_absent = _item("u", 5)  # no estimated key at all
    units, _ = da.extract_deadline_units(
        _record({"summerPrograms": [est_true, est_false, est_absent]}), TODAY)
    by_id = {u["item_id"]: u["estimated"] for u in units}
    assert by_id["t"] is True
    assert by_id["f"] is False
    assert by_id["u"] is None  # unknown — the renderer treats this as estimated


# ---------------- the reader: never raises ----------------

def test_reader_survives_a_malformed_blob():
    # tracker value is not JSON at all
    bad = {"data": {da.TRACKER_KEY: "{not json", da.SAVED_KEY: {}}}
    units, stats = da.extract_deadline_units(bad, TODAY)
    assert units == [] and stats["unparseable_blobs"] == 1


def test_reader_survives_junk_items_and_dates():
    tracker = {
        "summerPrograms": [
            "not a dict",
            {"id": "ok", "name": "OK", "status": "running",
             "importantDates": ["nope", {"type": "deadline", "dateISO": "garbage"},
                                {"type": "deadline",
                                 "dateISO": (TODAY + datetime.timedelta(days=1)).isoformat()}]},
        ],
        "internships": "should be a list",
    }
    units, stats = da.extract_deadline_units(_record(tracker), TODAY)
    # Only the one well-formed future deadline survives; nothing raises.
    assert len(units) == 1 and units[0]["days_left"] == 1
    assert stats["dates_skipped"] >= 2


def test_reader_empty_tracker_is_not_counted_as_corrupt():
    units, stats = da.extract_deadline_units({"data": {}}, TODAY)
    assert units == [] and stats["unparseable_blobs"] == 0
    units, stats = da.extract_deadline_units({}, TODAY)
    assert units == [] and stats["unparseable_blobs"] == 0
