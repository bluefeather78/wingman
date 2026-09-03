"""Unit tests for check_links.py — the free link-health agent's decision logic.

Only pure functions are exercised (classify / merge_flags / build_update / _path_of /
_parse_iso). Nothing here touches Supabase or the network.
"""
import datetime

import pytest

import check_links as cl
import url_validate as uv


NOW = "2026-08-23T12:00:00+00:00"


def _row(**kw):
    base = {"id": "ec1", "url": "https://x.com/program", "quality_flags": [], "is_active": True}
    base.update(kw)
    return base


# ------------------------------------------------------------------- classify
def test_classify_dead_with_repair_is_repair():
    action, flags = cl.classify(_row(), {"status": uv.DEAD, "code": 404},
                                repair={"url": "https://x.com/new"})
    assert action == "repair"
    assert flags[0].startswith("URL was dead (")
    assert "https://x.com/program" in flags[0]     # old url recorded in the flag


def test_classify_dead_no_repair_queues():
    action, flags = cl.classify(_row(), {"status": uv.DEAD, "code": 404}, repair=None)
    assert action == "queue"
    assert flags == [cl.FLAG_DEAD.format(code="404")]


def test_classify_dead_with_unverified_suggestion_appended():
    action, flags = cl.classify(_row(), {"status": uv.DEAD, "code": 404},
                                repair={"url": None, "suggestion": {"url": "https://x.com/maybe"}})
    assert action == "queue"
    assert len(flags) == 2
    assert flags[1].startswith("possible replacement found")
    assert "https://x.com/maybe" in flags[1]


def test_classify_unverified_numeric_code_blocked():
    action, flags = cl.classify(_row(), {"status": uv.UNVERIFIED, "code": 403})
    assert action == "flag"
    assert flags == [cl.FLAG_BLOCKED.format(code="403")]


def test_classify_unverified_digit_string_code_blocked():
    action, flags = cl.classify(_row(), {"status": uv.UNVERIFIED, "code": "429"})
    assert action == "flag"
    assert flags == [cl.FLAG_BLOCKED.format(code="429")]


def test_classify_unverified_nonnumeric_code_unreachable():
    action, flags = cl.classify(_row(), {"status": uv.UNVERIFIED, "code": "URLError"})
    assert action == "flag"
    assert flags == [cl.FLAG_UNREACHABLE.format(code="URLError")]


def test_classify_unverified_with_repair_is_repair():
    # An unverifiable row that repair proved a replacement for is a repair, not a flag —
    # this is what lets --repair-flagged rescue "unverifiable but actually gone" rows.
    action, flags = cl.classify(_row(), {"status": uv.UNVERIFIED, "code": 403},
                                repair={"url": "https://x.com/new"})
    assert action == "repair"
    assert flags[0].startswith("URL was dead (")


def test_classify_unverified_unproven_repair_keeps_suggestion():
    action, flags = cl.classify(_row(), {"status": uv.UNVERIFIED, "code": 403},
                                repair={"url": None, "suggestion": {"url": "https://x.com/maybe"}})
    assert action == "flag"
    assert flags[0] == cl.FLAG_BLOCKED.format(code="403")
    assert flags[1].startswith("possible replacement found")


def test_classify_live_ok():
    action, flags = cl.classify(_row(), {"status": uv.LIVE, "code": 200,
                                         "final_url": "https://x.com/program"})
    assert action == "ok"
    assert flags == []


def test_classify_live_soft_404_flag():
    # deep link that redirects to a bare homepage -> soft-404 flag, still live.
    action, flags = cl.classify(_row(url="https://x.com/deep/program"),
                                {"status": uv.LIVE, "code": 200,
                                 "final_url": "https://x.com/"})
    assert action == "flag"
    assert flags == [cl.FLAG_SOFT_404]


def test_classify_live_homepage_url_not_soft_404():
    # original url has no path -> not a soft-404 (there was nothing deep to lose).
    action, flags = cl.classify(_row(url="https://x.com/"),
                                {"status": uv.LIVE, "code": 200,
                                 "final_url": "https://x.com/"})
    assert action == "ok"


# ------------------------------------------------------------------- merge_flags
def test_merge_flags_replaces_own_keeps_others():
    existing = ["dead link (404) - page is gone; find the current URL or reject",
                "a human left this note"]
    out = cl.merge_flags(existing, ["dead link (410) - page is gone; find the current URL or reject"])
    assert "a human left this note" in out
    assert "dead link (404) - page is gone; find the current URL or reject" not in out
    assert "dead link (410) - page is gone; find the current URL or reject" in out


def test_merge_flags_idempotent():
    existing = ["dead link (404) - page is gone; find the current URL or reject"]
    new = ["dead link (404) - page is gone; find the current URL or reject"]
    once = cl.merge_flags(existing, new)
    twice = cl.merge_flags(once, new)
    assert once == twice == new


def test_merge_flags_none_existing():
    assert cl.merge_flags(None, [cl.FLAG_SOFT_404]) == [cl.FLAG_SOFT_404]


def test_merge_flags_no_duplicate_append():
    existing = ["keep me"]
    out = cl.merge_flags(existing, [cl.FLAG_SOFT_404, cl.FLAG_SOFT_404])
    assert out.count(cl.FLAG_SOFT_404) == 1


# ------------------------------------------------------------------- _path_of
@pytest.mark.parametrize("url,expected", [
    ("https://x.com/a/b/", "a/b"),
    ("https://x.com/", ""),
    ("https://x.com", ""),
    ("", ""),
])
def test_path_of(url, expected):
    assert cl._path_of(url) == expected


# ------------------------------------------------------------------- _parse_iso
def test_parse_iso_z_suffix():
    dt = cl._parse_iso("2026-08-23T12:00:00Z")
    assert dt.tzinfo is not None
    assert dt.year == 2026


def test_parse_iso_naive_gets_utc():
    dt = cl._parse_iso("2026-08-23T12:00:00")
    assert dt.tzinfo == datetime.timezone.utc


def test_parse_iso_none_and_bad():
    assert cl._parse_iso(None) is None
    assert cl._parse_iso("") is None
    assert cl._parse_iso("not a date") is None


# ------------------------------------------------------------------- build_update
def test_build_update_queue_never_deactivates_and_enqueues():
    # A dead link is QUEUED for review, not deactivated: is_active/moderation_status are never
    # touched, and the row is routed to the Links tab via link_review_status='pending'.
    row = _row(is_active=True, link_dead_since=None, link_review_status=None)
    upd = cl.build_update(row, "queue", [cl.FLAG_DEAD.format(code="404")],
                          {"status": uv.DEAD, "code": 404}, NOW, schema_ready=True,
                          needs_review=True)
    assert "is_active" not in upd
    assert "moderation_status" not in upd
    assert upd["link_review_status"] == "pending"
    assert upd["link_status"] == uv.DEAD
    assert upd["link_status_code"] == "404"
    assert upd["link_checked_at"] == NOW
    assert upd["link_dead_since"] == NOW           # first seen dead -> stamps now
    # Only link_* columns changed (quality_flags too) -> updated_at bump comes from the flag,
    # not from link_review_status, which rides with the link_* telemetry.
    assert upd["updated_at"] == NOW                # quality_flags is a non-link column


def test_build_update_queue_respects_human_verdict():
    # A person already cleared this row; a re-run that still finds it dead must NOT re-queue it.
    row = _row(is_active=True, link_review_status="cleared")
    upd = cl.build_update(row, "queue", [cl.FLAG_DEAD.format(code="404")],
                          {"status": uv.DEAD, "code": 404}, NOW, schema_ready=True,
                          needs_review=True)
    assert "link_review_status" not in upd         # 'cleared' is never overturned


def test_build_update_ok_clears_pending_finding():
    # A row that was in the queue and is now live drops out of the queue.
    row = _row(is_active=True, link_review_status="pending",
               link_dead_since="2026-01-01T00:00:00+00:00")
    upd = cl.build_update(row, "ok", [], {"status": uv.LIVE, "code": 200}, NOW,
                          schema_ready=True, needs_review=False)
    assert upd["link_review_status"] is None
    assert upd["link_dead_since"] is None


def test_build_update_dead_since_first_seen_wins():
    row = _row(is_active=True, link_dead_since="2026-01-01T00:00:00+00:00")
    upd = cl.build_update(row, "queue", [cl.FLAG_DEAD.format(code="404")],
                          {"status": uv.DEAD, "code": 404}, NOW, schema_ready=True,
                          needs_review=True)
    assert upd["link_dead_since"] == "2026-01-01T00:00:00+00:00"   # NOT overwritten


def test_build_update_live_clears_dead_since():
    row = _row(is_active=True, link_dead_since="2026-01-01T00:00:00+00:00")
    upd = cl.build_update(row, "ok", [], {"status": uv.LIVE, "code": 200}, NOW,
                          schema_ready=True)
    assert upd["link_dead_since"] is None


def test_build_update_repair_inactive_row_queues_not_activates():
    # A proven repair on an inactive row writes the new URL and parks it at 'repaired' for
    # manual activation — it must NOT flip is_active (no code path auto-activates any more).
    row = _row(is_active=False, url="https://x.com/dead", link_review_status="deactivated",
               quality_flags=["dead link (404) - page is gone; find the current URL or reject"])
    repair = {"url": "https://x.com/new"}
    upd = cl.build_update(row, "repair",
                          [cl.FLAG_REPAIRED.format(code="404", old="https://x.com/dead")],
                          {"status": uv.DEAD, "code": 404}, NOW, schema_ready=True, repair=repair,
                          needs_review=False)
    assert upd["url"] == "https://x.com/new"
    assert "is_active" not in upd                  # NEVER auto-activates
    assert upd["link_review_status"] == "repaired"  # parked for manual activation
    assert upd["link_status"] == uv.LIVE           # state AFTER the repair, not before
    assert upd["link_dead_since"] is None


def test_build_update_repair_on_active_row_fixes_url_in_place():
    # An active row whose URL was dead: a proven repair fixes the URL without touching
    # is_active and without queueing anything (the row was never off).
    row = _row(is_active=True, url="https://x.com/dead")
    upd = cl.build_update(row, "repair",
                          [cl.FLAG_REPAIRED.format(code="404", old="https://x.com/dead")],
                          {"status": uv.DEAD, "code": 404}, NOW, schema_ready=True,
                          repair={"url": "https://x.com/new"}, needs_review=False)
    assert upd["url"] == "https://x.com/new"
    assert "is_active" not in upd                  # already active -> not re-asserted
    assert "link_review_status" not in upd         # nothing to queue; row was never off


def test_build_update_none_when_nothing_changes():
    # schema not ready (no link cols), live+ok, no flag change, already active -> nothing.
    row = _row(is_active=True, quality_flags=[])
    upd = cl.build_update(row, "ok", [], {"status": uv.LIVE, "code": 200}, NOW,
                          schema_ready=False)
    assert upd is None


def test_build_update_queue_already_inactive_no_visibility_change():
    # --repair-flagged walks inactive rows: a still-dead one must not re-write is_active, and
    # this agent never writes moderation_status at all any more.
    row = _row(is_active=False, quality_flags=[], link_review_status=None)
    upd = cl.build_update(row, "queue", [cl.FLAG_DEAD.format(code="404")],
                          {"status": uv.DEAD, "code": 404}, NOW, schema_ready=True,
                          needs_review=True)
    assert "is_active" not in upd
    assert "moderation_status" not in upd


def test_build_update_only_link_columns_no_updated_at():
    # A live row whose only news is "checked again, still fine" must NOT bump updated_at.
    row = _row(is_active=True, quality_flags=[],
               link_status="live", link_dead_since=None)
    upd = cl.build_update(row, "ok", [], {"status": uv.LIVE, "code": 200,
                                          "final_url": "https://x.com/program"},
                          NOW, schema_ready=True)
    assert upd is not None
    assert "updated_at" not in upd                 # only link_* columns present
    assert all(k in cl.LINK_COLUMNS for k in upd)
