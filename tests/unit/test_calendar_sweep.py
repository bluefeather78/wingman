"""_sweep_stale_events — the removal half of Google Calendar sync.

Removing an opportunity from the Quest Log discards the item and its Google event ids
together, so nothing client-side can name the events to delete. The sweep instead deletes
every event the sync itself wrote whose wingmanId is no longer tracked. What must not
regress: it only ever touches events carrying that marker, so a student's own entries on
the Wingman calendar survive a sync.
"""
import urllib.error

import pytest

from app.routes import google_oauth as go


@pytest.fixture
def restore_calendar_request():
    original = go._calendar_request
    yield
    go._calendar_request = original


def _marked(event_id, marker):
    return {"id": event_id, "extendedProperties": {"private": {go.WINGMAN_EVENT_PROP: marker}}}


def test_sweep_mirrors_the_tracker_including_unmarked_events(restore_calendar_request):
    """The Wingman calendar MIRRORS the Quest Log: anything not currently tracked goes.

    This deliberately reversed on 2026-08-24. The sweep used to spare unmarked events so a
    calendar the student had also added their own entries to survived intact — but the marker
    only started being written on 2026-08-22, so every event older than that was permanently
    unsweepable. Measured on the first real account: 45 events for 17 tracked dates, 22 of
    them orphans no sync could ever remove, including deadlines for opportunities deleted
    weeks earlier. The calendar is app-created under calendar.app.created and exists to
    reflect the app, so an unmarked event on it is now swept like any other.
    """
    pages = iter([
        {"items": [_marked("ev1", "opp-a::0"), _marked("ev2", "opp-gone::0"),
                   {"id": "ev3"}],  # no marker: predates the marker, or hand-added
         "nextPageToken": "p2"},
        {"items": [_marked("ev4", "opp-gone::1"),
                   {"id": "ev5", "extendedProperties": {"private": {}}}]},
    ])
    deleted_ids = []

    def fake(method, url, token, payload=None):
        if method == "GET":
            return next(pages)
        if method == "DELETE":
            deleted_ids.append(url.rsplit("/", 1)[-1])
            return {}
        raise AssertionError(method)

    go._calendar_request = fake
    deleted, errors = go._sweep_stale_events("tok", "calendars/cal1", {"opp-a::0"})

    # ev1 is the ONLY survivor: it is the only currently-tracked date. ev3/ev5 carry no
    # marker and are swept too, which is the whole point of the change.
    assert (deleted, errors) == (4, [])
    assert sorted(deleted_ids) == ["ev2", "ev3", "ev4", "ev5"]


def test_sweep_spares_every_currently_tracked_event(restore_calendar_request):
    """The other half of mirroring: a tracked date is never removed, so a sync cannot
    delete an event it is about to need."""
    deleted_ids = []

    def fake(method, url, token, payload=None):
        if method == "GET":
            return {"items": [_marked("ev1", "opp-a::0"), _marked("ev2", "opp-a::1")]}
        if method == "DELETE":
            deleted_ids.append(url.rsplit("/", 1)[-1])
            return {}
        raise AssertionError(method)

    go._calendar_request = fake
    deleted, errors = go._sweep_stale_events("tok", "calendars/cal1", {"opp-a::0", "opp-a::1"})
    assert (deleted, errors, deleted_ids) == (0, [], [])


def test_already_deleted_event_is_not_a_failure(restore_calendar_request):
    def fake(method, url, token, payload=None):
        if method == "GET":
            return {"items": [_marked("evX", "gone")]}
        raise urllib.error.HTTPError(url, 410, "gone", None, None)

    go._calendar_request = fake
    # Gone on Google's side is the outcome the sweep wanted — counted, not errored.
    assert go._sweep_stale_events("tok", "calendars/c", set()) == (1, [])


def test_failed_listing_claims_no_deletions(restore_calendar_request):
    def fake(method, url, token, payload=None):
        raise RuntimeError("boom")

    go._calendar_request = fake
    deleted, errors = go._sweep_stale_events("tok", "calendars/c", set())
    assert deleted == 0
    assert len(errors) == 1 and "boom" in errors[0]


def test_other_http_errors_are_reported_per_event(restore_calendar_request):
    def fake(method, url, token, payload=None):
        if method == "GET":
            return {"items": [_marked("evX", "gone")]}
        raise urllib.error.HTTPError(url, 403, "forbidden", None, None)

    go._calendar_request = fake
    deleted, errors = go._sweep_stale_events("tok", "calendars/c", set())
    assert deleted == 0
    assert errors == ["Google API error 403 deleting an event"]
