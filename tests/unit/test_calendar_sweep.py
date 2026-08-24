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


def test_sweep_deletes_only_untracked_marked_events(restore_calendar_request):
    pages = iter([
        {"items": [_marked("ev1", "opp-a::0"), _marked("ev2", "opp-gone::0"),
                   {"id": "ev3"}],  # hand-added by the student — no marker
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

    assert (deleted, errors) == (2, [])
    # Paginates, and spares both the still-tracked event and the unmarked ones.
    assert sorted(deleted_ids) == ["ev2", "ev4"]


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
