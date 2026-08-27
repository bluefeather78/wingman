"""P0 — the deadline-alert reader as a CONTRACT test against a realistic tracker blob.

test_deadline_alert_rungs.py covers the engine and the individual exclusion rules with
hand-built inputs; this file pins the whole reader against one fixture shaped exactly like
what the RN app writes to users.data['hs-tracker-data'] (frontend/src/api/trackerStore.ts).
The fixture is the client/server SHAPE CONTRACT: if the tracker shape changes, this test is
what makes the server-side impact visible instead of silent.

The fixture stores dates as relative day-offset tokens ('${+3}' = three days from today) so
it never goes stale; _resolve() turns each into a real ISO date against a fixed reference
day before the reader sees it.
"""
import datetime
import json
import re
from pathlib import Path

import pytest

from app.services import deadline_alerts as da

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tracker_data_deadline_alerts.json"
TODAY = datetime.date(2026, 9, 1)

_TOKEN = re.compile(r"^\$\{([+-]?\d+)\}$")


def _resolve(value):
    """Replace every '${N}' offset token with TODAY + N days, recursively."""
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    if isinstance(value, str):
        m = _TOKEN.match(value)
        if m:
            return (TODAY + datetime.timedelta(days=int(m.group(1)))).isoformat()
    return value


@pytest.fixture
def record():
    """A users row whose data carries the resolved tracker as a JSON STRING (the client's
    on-disk shape) plus a saved-for-later map, so this exercises the real parse path."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw.pop("_comment", None)
    tracker = _resolve(raw)
    return {"data": {
        da.TRACKER_KEY: json.dumps(tracker),
        da.SAVED_KEY: {"parked-for-later": True},
    }}


def test_reader_returns_exactly_the_expected_units(record):
    units, stats = da.extract_deadline_units(record, TODAY)
    by_id = {u["item_id"]: u for u in units}

    # Exactly the items that SHOULD survive, and nothing else.
    assert set(by_id) == {
        "confirmed-soon",       # a real near deadline
        "estimated-midrange",   # estimated:true
        "legacy-unknown-flag",  # no estimated key -> unknown -> rendered estimated
        "far-future",           # a real deadline, just above the ladder
        "past-annual-projects", # past cycle, rolled forward by projection
        "snake-case-source",    # date_iso / important_dates spelling
        "malformed-dates",      # the one good date among junk survives
    }

    # Excluded, each for its own rule:
    assert "discontinued" not in by_id        # status == not_running
    assert "parked-for-later" not in by_id    # hs-tracker-saved
    assert "opens-only" not in by_id          # no deadline-type date

    assert stats["items_skipped_not_running"] == 1
    assert stats["items_skipped_saved"] == 1
    assert stats["unparseable_blobs"] == 0
    # The 'this string is not an item' entry and the two junk dates are counted, not raised.
    assert stats["dates_skipped"] >= 3


def test_confirmed_deadline_carries_its_real_fields(record):
    units, _ = da.extract_deadline_units(record, TODAY)
    u = next(x for x in units if x["item_id"] == "confirmed-soon")
    assert u["item_name"] == "Bank of America Student Leaders"
    assert u["org"] == "Bank of America"
    assert u["label"] == "Application deadline"
    assert u["days_left"] == 1
    assert u["estimated"] is False
    assert u["projected"] is False


def test_estimated_and_unknown_flags_are_distinct_from_confirmed(record):
    units, _ = da.extract_deadline_units(record, TODAY)
    by_id = {u["item_id"]: u for u in units}
    assert by_id["estimated-midrange"]["estimated"] is True
    # No stored flag -> None (unknown). The renderer treats unknown AS estimated, but the
    # reader must report the honest tri-state, not collapse it to a boolean.
    assert by_id["legacy-unknown-flag"]["estimated"] is None


def test_past_annual_program_is_projected_not_dropped(record):
    units, _ = da.extract_deadline_units(record, TODAY)
    u = next(x for x in units if x["item_id"] == "past-annual-projects")
    # Stored deadline was TODAY-363; rolled a whole year forward it lands at TODAY+2 — a real,
    # in-window next-cycle deadline that projection surfaced from a wholly-past cycle.
    assert u["projected"] is True
    assert u["estimated"] is True          # projection forces estimated
    assert u["days_left"] == 2


def test_snake_case_dates_are_read(record):
    units, _ = da.extract_deadline_units(record, TODAY)
    u = next(x for x in units if x["item_id"] == "snake-case-source")
    assert u["days_left"] == 3


def test_reader_never_raises_on_the_malformed_bucket(record):
    # The whole point: researchCompetitions holds a bare string and an item with junk dates.
    units, _ = da.extract_deadline_units(record, TODAY)
    u = next(x for x in units if x["item_id"] == "malformed-dates")
    assert u["days_left"] == 5   # only the one parseable future deadline survives


def test_units_are_sorted_soonest_first(record):
    units, _ = da.extract_deadline_units(record, TODAY)
    days = [u["days_left"] for u in units]
    assert days == sorted(days)


def test_due_alerts_over_the_fixture_are_only_the_in_window_items(record):
    units, _ = da.extract_deadline_units(record, TODAY)
    due = da.due_alerts(units)
    due_ids = {u["item_id"] for (u, _rung) in due}
    # In-window: confirmed-soon (1 -> rung1), estimated-midrange (3 -> rung3),
    # legacy-unknown-flag (6 -> rung7), snake-case-source (3 -> rung3),
    # malformed-dates (5 -> rung7), past-annual-projects (projected to +2 -> rung3).
    # far-future (45) is above the ladder.
    assert due_ids == {"confirmed-soon", "estimated-midrange", "legacy-unknown-flag",
                       "snake-case-source", "malformed-dates", "past-annual-projects"}
    assert "far-future" not in due_ids
