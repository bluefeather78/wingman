"""Unit tests for app.services.email — the claim, the dedupe key, the sweep, and the
unsubscribe HMAC.

No Supabase and no Resend: every network seam is monkeypatched. The seams are
_supabase_request_strict (the claim/finish/read), _resend_post (the provider) and
RESEND_API_KEY (mock vs live), all patched on the module object rather than at import, so
the real module-level constants stay untouched for anything else importing it.

What these tests are actually protecting, in order of how expensive the bug would be:

  1. A repeated sweep must not re-mail anybody. That is the unique constraint, and here it
     is the "already_sent" branch of _claim.
  2. A pre-send guard (opted out, no address) must not leave a claim behind — a leftover
     claim permanently suppresses a legitimate later send.
  3. An extended trial must earn a new reminder. That is the whole reason dedupe_key exists.
  4. Mock mode must write no claim at all, or developing offline costs real users their
     welcome email.
"""
import datetime

import pytest

from app.services import email as es


def _iso(delta_days):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=delta_days)).isoformat()


def _record(**over):
    base = {
        "userid": "student1",
        "first_name": "Ada",
        "email": "ada@example.com",
        "subscription_status": "trial",
        "trial_ends_at": _iso(2),
    }
    base.update(over)
    return base


class FakeSupabase:
    """Stands in for _supabase_request_strict, enforcing the one constraint that matters:
    unique (userid, kind, dedupe_key). Raises the same PostgREST 23505 the real table does.
    """

    def __init__(self):
        self.rows = []
        self.deleted = []
        self._next_id = 1

    def __call__(self, table, method="GET", params=None, data=None, extra_headers=None):
        if method == "POST":
            key = (data["userid"], data["kind"], data.get("dedupe_key") or "")
            if any((r["userid"], r["kind"], r.get("dedupe_key") or "") == key
                   for r in self.rows):
                raise _http_error(409, {"code": "23505",
                                        "message": "duplicate key value"})
            row = dict(data, id=self._next_id)
            self._next_id += 1
            self.rows.append(row)
            return [row]
        if method == "PATCH":
            rid = int((params or {}).get("id", "eq.0").split(".")[-1])
            for r in self.rows:
                if r.get("id") == rid:
                    r.update(data)
            return []
        if method == "DELETE":
            rid = int((params or {}).get("id", "eq.0").split(".")[-1])
            self.deleted.append(rid)
            self.rows = [r for r in self.rows if r.get("id") != rid]
            return []
        return list(self.rows)


def _http_error(code, body):
    import io
    import json
    import urllib.error
    return urllib.error.HTTPError(
        "http://x", code, "err", {},
        io.BytesIO(json.dumps(body).encode()))


@pytest.fixture
def live(monkeypatch):
    """A configured provider plus a fake table. Returns (supabase, sent_list)."""
    fake = FakeSupabase()
    sent = []
    monkeypatch.setattr(es, "_supabase_request_strict", fake)
    monkeypatch.setattr(es, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(es, "_resend_post",
                        lambda to, subj, html, text: (sent.append((to, subj)) or "msg_1", None))
    return fake, sent


# ---------- the claim ----------

def test_second_send_is_skipped_not_duplicated(live):
    fake, sent = live
    first = es.send_lifecycle_email("student1", "welcome", record=_record())
    second = es.send_lifecycle_email("student1", "welcome", record=_record())

    assert first["state"] == "sent"
    assert second["state"] == "skipped" and second["reason"] == "already_sent"
    # The real assertion: the provider was called exactly once.
    assert len(sent) == 1
    assert len(fake.rows) == 1


def test_successful_send_is_recorded_as_sent(live):
    fake, _ = live
    es.send_lifecycle_email("student1", "welcome", record=_record())
    row = fake.rows[0]
    assert row["state"] == "sent"
    assert row["provider_message_id"] == "msg_1"
    assert row["sent_at"]


def test_provider_failure_is_recorded_and_not_retried(live, monkeypatch):
    fake, _ = live
    monkeypatch.setattr(es, "_resend_post",
                        lambda *a: (None, "Resend 403: domain not verified"))
    result = es.send_lifecycle_email("student1", "welcome", record=_record())

    assert result["state"] == "failed"
    assert fake.rows[0]["state"] == "failed"
    assert "403" in fake.rows[0]["error"]
    # A failed send still holds its claim: retrying automatically is how a transient
    # provider blip turns into a duplicate once the first attempt actually did deliver.
    assert es.send_lifecycle_email("student1", "welcome",
                                   record=_record())["reason"] == "already_sent"


def test_missing_table_means_no_send(live, monkeypatch):
    _, sent = live

    def missing(*a, **k):
        raise _http_error(404, {"code": "PGRST205", "message": "no table"})

    monkeypatch.setattr(es, "_supabase_request_strict", missing)
    result = es.send_lifecycle_email("student1", "welcome", record=_record())

    assert result["state"] == "skipped"
    assert result["table_ready"] is False
    assert sent == [], "a send with no claim row is how a sweep mails somebody every day"


# ---------- pre-send guards must not strand a claim ----------

def test_optout_blocks_send_and_leaves_no_claim(live):
    fake, sent = live
    result = es.send_lifecycle_email("student1", "welcome",
                                     record=_record(lifecycle_email_optout=True))
    assert result["state"] == "skipped"
    assert sent == []
    # Nothing claimed: an account that opts back in must still be able to receive later mail.
    assert fake.rows == []


def test_missing_address_blocks_send_and_leaves_no_claim(live):
    fake, sent = live
    result = es.send_lifecycle_email("student1", "welcome", record=_record(email=""))
    assert result["state"] == "skipped"
    assert sent == [] and fake.rows == []


# ---------- mock mode ----------

def test_mock_mode_writes_no_claim(monkeypatch):
    fake = FakeSupabase()
    monkeypatch.setattr(es, "_supabase_request_strict", fake)
    monkeypatch.setattr(es, "RESEND_API_KEY", "")

    result = es.send_lifecycle_email("student1", "welcome", record=_record())
    assert result["state"] == "mock"
    # The claim would suppress the real send once a key is configured — i.e. developing
    # offline would silently cost this user their welcome email.
    assert fake.rows == []


# ---------- dedupe key ----------

def test_dedupe_key_is_the_trial_end_date():
    assert es.trial_dedupe_key({"trial_ends_at": "2026-09-01T12:00:00+00:00"}) == "2026-09-01"
    assert es.trial_dedupe_key({}) == ""


def test_extended_trial_earns_a_second_reminder(live):
    fake, sent = live
    first = _record(trial_ends_at="2026-09-01T12:00:00+00:00")
    es.send_lifecycle_email("student1", "trial_ending", record=first,
                            dedupe_key=es.trial_dedupe_key(first))

    # Same window, later in the day: still one reminder. The key is the DATE, so a grant
    # that shifts the end by hours must not mint a second send.
    same = _record(trial_ends_at="2026-09-01T23:00:00+00:00")
    again = es.send_lifecycle_email("student1", "trial_ending", record=same,
                                    dedupe_key=es.trial_dedupe_key(same))
    assert again["reason"] == "already_sent"

    # A promo grant pushing the trial out a week is a new window and a new reminder.
    extended = _record(trial_ends_at="2026-09-08T12:00:00+00:00")
    third = es.send_lifecycle_email("student1", "trial_ending", record=extended,
                                    dedupe_key=es.trial_dedupe_key(extended))
    assert third["state"] == "sent"
    assert len(sent) == 2


# ---------- the sweep ----------

def test_sweep_excludes_paying_and_opted_out(live, monkeypatch):
    _, sent = live
    monkeypatch.setattr(es, "_supabase_request_strict", _reader([
        _record(userid="due", trial_ends_at=_iso(1)),
        # Already has a Stripe subscription: Stripe renews them and "your trial ends"
        # reads as us not knowing our own billing state.
        _record(userid="paying", trial_ends_at=_iso(1), stripe_subscription_id="sub_1"),
        _record(userid="quiet", trial_ends_at=_iso(1), lifecycle_email_optout=True),
        # Outside the window — the upper bound is applied client-side, so it needs a test.
        _record(userid="later", trial_ends_at=_iso(30)),
    ]))
    due = es.due_trial_reminders(days=2)
    assert [r["userid"] for r in due] == ["due"]


def test_sweep_dry_run_sends_nothing(live, monkeypatch):
    _, sent = live
    monkeypatch.setattr(es, "_supabase_request_strict",
                        _reader([_record(userid="due", trial_ends_at=_iso(1))]))
    result = es.run_trial_sweep(days=2, dry_run=True)
    assert result["ok"] and result["due"] == 1 and result["sent"] == 0
    assert sent == []
    assert result["details"][0]["state"] == "would_send"


def test_sweep_reports_setup_rather_than_zero_due(monkeypatch):
    def missing(*a, **k):
        raise _http_error(404, {"code": "PGRST205", "message": "no table"})

    monkeypatch.setattr(es, "_supabase_request_strict", missing)
    result = es.run_trial_sweep()
    # A zero here would read as "nobody is due" when the truth is "this never runs".
    assert result["ok"] is False and result["table_ready"] is False
    assert "db/email_schema.sql" in result["setup_sql_file"]


def _reader(rows):
    """A _supabase_request_strict stand-in that answers GETs with `rows` and accepts writes."""
    store = FakeSupabase()

    def call(table, method="GET", params=None, data=None, extra_headers=None):
        if method == "GET" and table == "users":
            return list(rows)
        return store(table, method, params, data, extra_headers)

    return call


# ---------- unsubscribe ----------

def test_unsubscribe_token_roundtrips(monkeypatch):
    monkeypatch.setattr(es, "JWT_SECRET", "test-secret")
    token = es.unsubscribe_token("student1")
    assert token and es.verify_unsubscribe_token("student1", token)
    # Guessing another account's id must not be enough to unsubscribe them.
    assert not es.verify_unsubscribe_token("student2", token)
    assert not es.verify_unsubscribe_token("student1", "wrong")


def test_unsubscribe_fails_closed_without_a_secret(monkeypatch):
    monkeypatch.setattr(es, "JWT_SECRET", "")
    assert es.unsubscribe_token("student1") == ""
    # An empty token must never verify, or an unset secret would open the endpoint to all.
    assert not es.verify_unsubscribe_token("student1", "")


# ---------- templates ----------

@pytest.mark.parametrize("kind", ["welcome", "trial_ending", "goodbye"])
def test_every_kind_renders_html_and_text(kind):
    subject, html, text = es.render_for(kind, _record())
    assert subject and html.lower().startswith("<!doctype html>")
    # A missing text/plain part is one of the strongest spam signals there is, and this
    # is a cold domain mailing school Workspace accounts. The designed HTML files this
    # layout was ported from had no text part at all.
    assert len(text) > 100
    assert "unsubscribe" in text.lower()


# ---------- the three fixes made while porting the designed templates ----------
#
# Each of these locks in a defect that was present in the source HTML. They are cheap and
# they are the ones most likely to come back if the templates are ever re-exported from
# the design files and pasted over.

@pytest.mark.parametrize("kind", ["welcome", "trial_ending", "goodbye"])
def test_unsubscribe_link_is_an_unsubscribe_not_a_login(kind):
    _, html, text = es.render_for(kind, _record())
    # The source pointed its "Unsubscribe" at /login, which signs a student IN rather than
    # opting them out — the silent failure this whole feature is measured against.
    assert "/api/email/unsubscribe" in html
    assert "/api/email/unsubscribe" in text


@pytest.mark.parametrize("kind", ["welcome", "trial_ending", "goodbye"])
def test_no_placeholder_survives_into_a_rendered_email(kind):
    _, html, text = es.render_for(kind, _record())
    for blob in (html, text):
        assert "{{" not in blob, "an unsubstituted mustache token reached a real email"
        assert "[Add your" not in blob
        assert "SET EMAIL_POSTAL_ADDRESS" not in blob


def test_trial_length_is_not_hardcoded_to_seven():
    """A `grant` promo code extends the trial, so a literal '7 days' is wrong for anyone
    who redeemed one — and the welcome email is exactly where they would read it."""
    _, html, _ = es.render_for("welcome", _record(trial_ends_at=_iso(21)))
    assert "21-day trial started" in html
    assert "7 days" not in html


@pytest.mark.parametrize("days,expected", [
    (0, "today"),
    (1, "tomorrow"),
    (4, "in 4 days"),
])
def test_trial_ending_relative_phrase_tracks_the_actual_date(days, expected):
    """The source hardcoded 'tomorrow' in the badge, the preheader AND the heading. The
    reminder window can fire on any of several days."""
    # days_until_trial_end CEILINGS, so a trial ending in N-0.4 days reads as N days left.
    # days=0 lands in the past, which subscription_state reports as expired / 0 left.
    subject, html, text = es.render_for(
        "trial_ending", _record(trial_ends_at=_iso(days - 0.4)))
    assert expected in subject
    assert expected in html and expected in text


def test_unknown_kind_is_refused_by_name():
    result = es.send_lifecycle_email("student1", "newsletter", record=_record())
    assert result["state"] == "failed" and "newsletter" in result["reason"]


def test_template_escapes_the_students_own_name():
    _, html, _ = es.render_for("welcome", _record(first_name="<script>x</script>"))
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


# ---------- the preview sample is staged per kind ----------

def test_welcome_preview_shows_the_real_trial_length():
    """The sample used to date every preview off TRIAL_REMINDER_DAYS, so the welcome
    preview announced a "2-day trial" for a product whose trial is TRIAL_DAYS long. Not a
    template bug — every date here is computed — but indistinguishable from one, and read
    as one."""
    from wingman.subscription_common import TRIAL_DAYS
    d = es.preview("welcome")
    assert f"{TRIAL_DAYS}-day trial started" in d["html"]
    assert f"next {TRIAL_DAYS} days" in d["html"]
    assert f"Your trial ends in {TRIAL_DAYS} days" in d["html"]


def test_trial_ending_preview_still_shows_an_expiring_trial():
    """The other half of the same trade: this one must preview inside the window the
    sweep actually fires in, not a fresh trial."""
    from app.services.email import TRIAL_REMINDER_DAYS
    d = es.preview("trial_ending")
    assert f"in {TRIAL_REMINDER_DAYS} days" in d["subject"]
