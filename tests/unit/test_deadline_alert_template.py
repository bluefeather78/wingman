"""P2 — the deadline_alert digest template and its context wiring.

Renders through the real seam (email.render_for / email.preview), so these tests exercise
build_context -> the reader -> the template exactly as a send or a console preview does. What
they protect:

  1. The subject carries the count and the urgency — the only line most students read.
  2. An estimated (or unknown-provenance) date is labelled; a confirmed one is not.
  3. Every digest ships a text/plain part with a real unsubscribe link (spam-signal + the
     silent-failure the whole feature is measured against).
  4. An EMPTY digest is refused, never sent as "here are your 0 deadlines".
  5. The >N-items overflow line appears rather than an endless email.
"""
import datetime
import json

import pytest

from app.services import email as es
from app.config import DEADLINE_ALERT_MAX_ITEMS


def _now_iso(days):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=days)).isoformat()


def _tracker_record(items, url=None):
    """items: list of (id, name, org, days_out, type, estimated). Builds a users row whose
    data carries a JSON-string tracker in the shape the RN app writes. `url` (if given) is
    put on every item so the link-rendering tests have something to link to."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    bucket = []
    for id_, name, org, days, dtype, estimated in items:
        date_iso = (today + datetime.timedelta(days=days)).isoformat()
        d = {"label": "Application deadline", "dateISO": date_iso, "type": dtype}
        if estimated is not None:
            d["estimated"] = estimated
        item = {"id": id_, "name": name, "org": org, "status": "running",
                "importantDates": [d]}
        if url is not None:
            item["url"] = url
        bucket.append(item)
    return {
        "userid": "student1", "first_name": "Ada", "email": "ada@example.com",
        "subscription_status": "trial", "trial_ends_at": _now_iso(20),
        "data": {"hs-tracker-data": json.dumps({"summerPrograms": bucket}),
                 "hs-tracker-saved": {}},
    }


# ---------------- subject ----------------

def test_single_deadline_subject_names_it_and_the_timing():
    rec = _tracker_record([("a", "Bank of America Student Leaders", "BofA", 1, "deadline",
                            False)])
    subject, html, text = es.render_for("deadline_alert", rec)
    assert "tomorrow" in subject
    assert "Bank of America Student Leaders" in subject


def test_multiple_deadlines_subject_carries_the_count_and_soonest():
    rec = _tracker_record([
        ("a", "Alpha", "A", 1, "deadline", False),
        ("b", "Beta", "B", 3, "deadline", False),
        ("c", "Gamma", "C", 6, "deadline", False),
    ])
    subject, _, _ = es.render_for("deadline_alert", rec)
    assert subject.startswith("3 deadlines")
    assert "tomorrow" in subject  # the soonest sets the urgency


# ---------------- grouping + estimated label ----------------

def test_items_are_grouped_by_rung():
    rec = _tracker_record([
        ("a", "Alpha", "A", 1, "deadline", False),   # rung 1
        ("b", "Beta", "B", 3, "deadline", False),    # rung 3
        ("c", "Gamma", "C", 6, "deadline", False),   # rung 7
    ])
    _, html, _ = es.render_for("deadline_alert", rec)
    assert "Due today or tomorrow" in html
    assert "Due in the next few days" in html
    assert "Due this week" in html


def test_estimated_date_is_labelled_and_confirmed_date_is_not():
    rec = _tracker_record([
        ("conf", "Morning Robotics League", "Org", 3, "deadline", False),
        ("est", "Coastal Research Fellowship", "Org", 6, "deadline", True),
    ])
    _, html, text = es.render_for("deadline_alert", rec)
    # The distinctive note phrase, not the bare word "estimated" (a program name could carry
    # that). Exactly one item is estimated, so the note must appear exactly once.
    assert html.count("confirm on the program") == 1
    assert text.count("confirm on the program") == 1


def test_unknown_provenance_date_is_labelled_estimated():
    """A date with no `estimated` key (every date written before 2026-08-24) is unknown, and
    unknown is rendered as estimated — never confirmed on no evidence."""
    rec = _tracker_record([("u", "Legacy Program", "Org", 3, "deadline", None)])
    _, html, _ = es.render_for("deadline_alert", rec)
    assert "estimated" in html.lower()


# ---------------- the opportunity name links to its URL ----------------

def test_program_name_links_to_its_url():
    rec = _tracker_record([("a", "Bank of America Student Leaders", "BofA", 1, "deadline",
                            False)], url="https://about.bankofamerica.com/student-leaders")
    _, html, text = es.render_for("deadline_alert", rec)
    # HTML: the name is wrapped in an anchor to the URL.
    assert 'href="https://about.bankofamerica.com/student-leaders"' in html
    assert ">Bank of America Student Leaders</a>" in html
    # Text part: the URL appears on its own line under the item.
    assert "https://about.bankofamerica.com/student-leaders" in text


def test_missing_url_renders_the_name_as_plain_text():
    rec = _tracker_record([("a", "No Link Program", "Org", 1, "deadline", False)], url=None)
    _, html, _ = es.render_for("deadline_alert", rec)
    assert "No Link Program" in html
    # No anchor was invented for it.
    assert 'href="' not in html.split("No Link Program")[0][-120:]


def test_non_http_url_is_not_linked():
    """A javascript:/data: scheme must never become an email link."""
    rec = _tracker_record([("a", "Sneaky Program", "Org", 1, "deadline", False)],
                          url="javascript:alert(1)")
    _, html, _ = es.render_for("deadline_alert", rec)
    assert "javascript:alert(1)" not in html
    assert "Sneaky Program" in html


# ---------------- overflow ----------------

def test_overflow_line_appears_beyond_the_cap():
    items = [(f"id{i}", f"Program {i}", "Org", 5, "deadline", False)
             for i in range(DEADLINE_ALERT_MAX_ITEMS + 3)]
    rec = _tracker_record(items)
    _, html, text = es.render_for("deadline_alert", rec)
    assert "3 more in your Quest Log" in html
    assert "3 more in your Quest Log" in text


# ---------------- structural guarantees shared with the other kinds ----------------

def test_digest_ships_html_and_a_real_text_part():
    rec = _tracker_record([("a", "Alpha", "A", 1, "deadline", False)])
    subject, html, text = es.render_for("deadline_alert", rec)
    assert html.lower().startswith("<!doctype html>")
    assert len(text) > 100
    assert "/api/email/unsubscribe" in html
    assert "/api/email/unsubscribe" in text


def test_no_placeholder_survives_into_a_rendered_digest():
    rec = _tracker_record([("a", "Alpha", "A", 1, "deadline", False)])
    _, html, text = es.render_for("deadline_alert", rec)
    for blob in (html, text):
        assert "{{" not in blob
        assert "SET EMAIL_POSTAL_ADDRESS" not in blob


def test_program_name_is_escaped():
    rec = _tracker_record([("x", "<script>x</script>", "Org", 1, "deadline", False)])
    _, html, _ = es.render_for("deadline_alert", rec)
    assert "<script>x</script>" not in html


# ---------------- the empty digest is refused ----------------

def test_empty_digest_raises_rather_than_sending():
    """No due deadlines must never render as 'here are your 0 deadlines'. render_for surfaces
    the ValueError; the sweep (P3) also guards, so this is the second line of defence."""
    rec = _tracker_record([])  # nothing tracked
    with pytest.raises(ValueError):
        es.render_for("deadline_alert", rec)


def test_only_far_off_deadlines_is_an_empty_digest():
    rec = _tracker_record([("far", "Far Program", "Org", 30, "deadline", False)])
    with pytest.raises(ValueError):
        es.render_for("deadline_alert", rec)


# ---------------- the console preview ----------------

def test_preview_renders_the_sample_across_every_rung():
    d = es.preview("deadline_alert")
    assert d["ok"] and d["is_sample"]
    # The staged sample places one item in each rung bucket.
    assert "Due today or tomorrow" in d["html"]
    assert "Due in the next few days" in d["html"]
    assert "Due this week" in d["html"]
    # And the sample's rung-7 item is the estimated one.
    assert "estimated" in d["html"].lower()


def test_preview_subject_reflects_the_sample_count():
    d = es.preview("deadline_alert")
    assert d["subject"].startswith("3 deadlines")


# ---------------- mimic a real user (the console's on-demand feature) ----------------

def test_preview_for_a_real_user_uses_their_tracked_deadlines(monkeypatch):
    """A userid loads the FULL account (data included) so the deadline preview shows that
    student's own deadlines, not an empty digest. get_user is the full-row read."""
    rec = _tracker_record([("a", "Their Real Program", "Org", 2, "deadline", False)])
    rec["userid"] = "realstudent"
    monkeypatch.setattr(es, "get_user", lambda uid: rec)
    d = es.preview("deadline_alert", userid="realstudent")
    assert d["ok"] is True
    assert d["is_sample"] is False
    assert d["rendered_for"] == "realstudent"
    assert "Their Real Program" in d["html"]


def test_preview_for_a_user_with_nothing_due_is_a_clean_note_not_an_error(monkeypatch):
    rec = _tracker_record([("a", "Far Off", "Org", 90, "deadline", False)])
    rec["userid"] = "quietstudent"
    monkeypatch.setattr(es, "get_user", lambda uid: rec)
    d = es.preview("deadline_alert", userid="quietstudent")
    assert d["ok"] is False
    assert d["empty_digest"] is True
    assert "no deadlines due" in d["error"].lower()


def test_send_test_mimics_a_real_users_digest(monkeypatch):
    rec = _tracker_record([("a", "Mimic Me Program", "Org", 1, "deadline", False)])
    rec["userid"] = "realstudent"
    sent = []
    monkeypatch.setattr(es, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(es, "_resend_post",
                        lambda to, subj, html, text: (sent.append((to, subj, html)) or "m1", None))
    result = es.send_test("deadline_alert", "operator@example.com", record=rec)
    assert result["state"] == "sent"
    assert result["subject"].startswith("[TEST]")
    assert "Mimic Me Program" in sent[0][2]


def test_send_test_for_a_user_with_nothing_due_is_skipped_not_failed(monkeypatch):
    rec = _tracker_record([("a", "Far Off", "Org", 90, "deadline", False)])
    rec["userid"] = "quietstudent"
    monkeypatch.setattr(es, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(es, "_resend_post", lambda *a: ("m1", None))
    result = es.send_test("deadline_alert", "operator@example.com", record=rec)
    assert result["state"] == "skipped"
    assert "no deadlines due" in result["reason"].lower()
