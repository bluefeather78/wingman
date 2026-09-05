"""Unit tests for dedupe_queue.select_rows — the --source queue/flagged row selection. Pure,
no network.
"""
from agents import dedupe_queue as dq


def _row(id_, active, status, flags=None):
    return {"id": id_, "is_active": active, "moderation_status": status,
            "quality_flags": flags or []}


def test_source_queue_selects_pending_inactive_rows():
    rows = [
        _row("1", False, None),
        _row("2", False, "pending_review"),
        _row("3", False, "rejected"),          # adjudicated away -- not the queue
        _row("4", True, None),                 # active -- not the queue regardless of status
        _row("5", True, "suspected_duplicate"),
    ]
    selected = dq.select_rows(rows, "queue")
    assert {r["id"] for r in selected} == {"1", "2"}


def test_source_flagged_selects_active_suspected_duplicate_rows():
    rows = [
        _row("1", False, "pending_review"),
        _row("2", True, "suspected_duplicate"),
        _row("3", True, "approved"),            # active but not flagged
        _row("4", False, "suspected_duplicate"),  # inactive -- can't happen, but must not select
    ]
    selected = dq.select_rows(rows, "flagged")
    assert {r["id"] for r in selected} == {"2"}


def test_classified_only_narrows_queue_but_is_ignored_for_flagged():
    rows = [
        _row("1", False, "pending_review", flags=["classify: program"]),
        _row("2", False, "pending_review", flags=[]),
        _row("3", True, "suspected_duplicate", flags=[]),
    ]
    queue_narrowed = dq.select_rows(rows, "queue", classified_only=True)
    assert {r["id"] for r in queue_narrowed} == {"1"}

    flagged = dq.select_rows(rows, "flagged", classified_only=True)
    assert {r["id"] for r in flagged} == {"3"}


def test_empty_input_selects_nothing():
    assert dq.select_rows([], "queue") == []
    assert dq.select_rows([], "flagged") == []
