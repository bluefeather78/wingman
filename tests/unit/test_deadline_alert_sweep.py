"""P3 — the deadline-alert sweep: roster read, per-unit claim, one digest send, finish.

Every network seam is monkeypatched (no Supabase, no Resend), same posture as
test_lifecycle_email.py. What these protect, in order of how expensive the bug would be:

  1. A repeated sweep must not re-mail a reminder. That is the per-unit claim, and it is the
     partial-digest case here (some units already sent -> the email carries only the rest).
  2. All units already sent -> NO provider call at all (the idempotent no-op).
  3. A provider failure marks every survivor row failed and is never auto-retried.
  4. Mock mode writes NO claim rows.
  5. The digest is per-STUDENT: the excluded (opted-out / lapsed / no-email) accounts never
     reach a claim, and one email covers all of a student's due deadlines.
  6. dry-run makes no writes and no sends.
"""
import datetime
import json

import pytest

from app.services import email as es
from app.services import deadline_alerts as da


def _iso(days):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _tracker(*deadlines):
    """deadlines: (id, name, days_out). Builds a JSON-string tracker blob."""
    bucket = [{"id": id_, "name": name, "status": "running",
               "importantDates": [{"label": "Application deadline", "dateISO": _iso(days),
                                   "type": "deadline", "estimated": False}]}
              for (id_, name, days) in deadlines]
    return json.dumps({"summerPrograms": bucket})


def _account(userid="stu", email="stu@example.com", deadlines=(("a", "Alpha", 1),), **over):
    rec = {
        "userid": userid, "first_name": "Ada", "email": email,
        "subscription_status": "trial",
        "data": {"hs-tracker-data": _tracker(*deadlines), "hs-tracker-saved": {}},
    }
    rec.update(over)
    return rec


def _http_error(code, body):
    import io
    import urllib.error
    return urllib.error.HTTPError("http://x", code, "err", {},
                                  io.BytesIO(json.dumps(body).encode()))


class FakeTable:
    """email_sends claim table + a preset users roster. Enforces the one constraint that
    matters: unique (userid, kind, dedupe_key), raising PostgREST's 23505 on a repeat."""

    def __init__(self, roster, missing_table=False):
        self.roster = roster
        self.rows = []
        self.deleted = []
        self._next_id = 1
        self.missing_table = missing_table

    def __call__(self, table, method="GET", params=None, data=None, extra_headers=None):
        if table == "users" and method == "GET":
            # One page then empty, so the sweep's pagination terminates.
            offset = 0
            rng = (extra_headers or {}).get("Range", "0-999")
            try:
                offset = int(rng.split("-")[0])
            except ValueError:
                offset = 0
            return list(self.roster) if offset == 0 else []
        if table == "email_sends":
            if self.missing_table:
                raise _http_error(404, {"code": "PGRST205", "message": "no table"})
            if method == "POST":
                key = (data["userid"], data["kind"], data.get("dedupe_key") or "")
                if any((r["userid"], r["kind"], r.get("dedupe_key") or "") == key
                       for r in self.rows):
                    raise _http_error(409, {"code": "23505", "message": "dup"})
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
        return []


@pytest.fixture
def live(monkeypatch):
    """A configured provider. Returns a helper that installs a FakeTable for a given roster
    and returns (fake, sent_list). subscription_state is stubbed to read _has_access
    (default True) off the record, so the access gate is exercised without real billing."""
    sent = []
    monkeypatch.setattr(es, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(es, "_resend_post",
                        lambda to, subj, html, text: (sent.append((to, subj)) or "msg_1", None))
    monkeypatch.setattr(es, "subscription_state",
                        lambda rec: {"has_access": rec.get("_has_access", True)})

    def install(roster, missing_table=False):
        fake = FakeTable(roster, missing_table=missing_table)
        monkeypatch.setattr(es, "_supabase_request_strict", fake)
        return fake, sent

    return install


# ---------- the digest is one email covering all of a student's due deadlines ----------

def test_one_student_with_three_deadlines_gets_one_email(live):
    fake, sent = live([_account(deadlines=[("a", "Alpha", 1), ("b", "Beta", 3),
                                           ("c", "Gamma", 6)])])
    result = es.run_deadline_alert_sweep()
    assert result["sent"] == 1
    assert len(sent) == 1                      # ONE email, not three
    assert result["units_alerted"] == 3        # covering three deadlines
    assert len(fake.rows) == 3                  # one claim per (opportunity, date, rung)
    assert all(r["state"] == "sent" for r in fake.rows)


# ---------- the per-unit claim: partial + full repeat ----------

def test_second_sweep_only_mails_the_newly_due_unit(live):
    """First sweep sends a 2-deadline digest. Then a third deadline enters the window. The
    second sweep must mail ONLY the new one — the two already-claimed units drop out."""
    acct = _account(deadlines=[("a", "Alpha", 1), ("b", "Beta", 3)])
    fake, sent = live([acct])
    first = es.run_deadline_alert_sweep()
    assert first["sent"] == 1 and first["units_alerted"] == 2

    # A new deadline appears; re-run against the SAME claim table.
    acct["data"]["hs-tracker-data"] = _tracker(("a", "Alpha", 1), ("b", "Beta", 3),
                                               ("c", "Gamma", 6))
    second = es.run_deadline_alert_sweep()
    assert second["sent"] == 1
    assert second["units_alerted"] == 1               # only Gamma
    assert second["units_already_sent"] == 2          # Alpha + Beta dropped
    assert len(sent) == 2                              # one email per sweep, two total


def test_fully_repeated_sweep_sends_nothing(live):
    fake, sent = live([_account(deadlines=[("a", "Alpha", 1), ("b", "Beta", 3)])])
    es.run_deadline_alert_sweep()
    sent.clear()
    again = es.run_deadline_alert_sweep()
    assert again["sent"] == 0
    assert again["skipped"] == 1
    assert sent == [], "every unit was already claimed — no provider call may happen"


# ---------- provider failure ----------

def test_provider_failure_marks_every_survivor_failed(live, monkeypatch):
    fake, _ = live([_account(deadlines=[("a", "Alpha", 1), ("b", "Beta", 3)])])
    monkeypatch.setattr(es, "_resend_post", lambda *a: (None, "Resend 500: boom"))
    result = es.run_deadline_alert_sweep()
    assert result["failed"] == 1
    assert all(r["state"] == "failed" for r in fake.rows)
    # A failed send keeps its claims, so it is not retried into a possible duplicate.
    monkeypatch.setattr(es, "_resend_post", lambda to, s, h, t: ("msg_x", None))
    retry = es.run_deadline_alert_sweep()
    assert retry["sent"] == 0 and retry["skipped"] == 1


# ---------- mock mode ----------

def test_mock_mode_writes_no_claims(live, monkeypatch):
    fake, sent = live([_account(deadlines=[("a", "Alpha", 1)])])
    monkeypatch.setattr(es, "RESEND_API_KEY", "")
    result = es.run_deadline_alert_sweep()
    assert result["mock"] == 1
    assert fake.rows == [], "a mock send must not claim, or it suppresses the real one later"
    assert sent == []


# ---------- the who-gets-it filters, applied before any claim ----------

def test_excluded_accounts_never_reach_a_claim(live):
    roster = [
        _account(userid="due", deadlines=[("a", "Alpha", 1)]),
        _account(userid="optout", lifecycle_email_optout=True,
                 deadlines=[("a", "Alpha", 1)]),
        _account(userid="noemail", email="", deadlines=[("a", "Alpha", 1)]),
        _account(userid="lapsed", _has_access=False, deadlines=[("a", "Alpha", 1)]),
        _account(userid="nothingdue", deadlines=[("a", "Alpha", 60)]),  # above the ladder
    ]
    fake, sent = live(roster)
    digests, stats = es.due_deadline_alert_digests()
    assert [d["record"]["userid"] for d in digests] == ["due"]
    assert stats["skipped_optout"] == 1
    assert stats["skipped_no_email"] == 1
    assert stats["skipped_no_access"] == 1


# ---------- dry run ----------

def test_dry_run_sends_nothing_and_writes_nothing(live):
    fake, sent = live([_account(deadlines=[("a", "Alpha", 1), ("b", "Beta", 3)])])
    result = es.run_deadline_alert_sweep(dry_run=True)
    assert result["accounts_with_due"] == 1
    assert result["sent"] == 0
    assert sent == [] and fake.rows == []
    entry = result["details"][0]
    assert entry["state"] == "would_send"
    assert len(entry["deadlines"]) == 2


# ---------- missing claim table ----------

def test_missing_table_reports_setup_not_a_wall_of_skips(live):
    fake, sent = live([_account(deadlines=[("a", "Alpha", 1)])], missing_table=True)
    result = es.run_deadline_alert_sweep()
    assert result.get("table_ready") is False
    assert "db/email_schema.sql" in result.get("setup_sql_file", "")
    assert sent == []


# ---------- a lapsed account with a real access-gate stub ----------

def test_lapsed_account_is_not_mailed(live):
    fake, sent = live([_account(userid="lapsed", _has_access=False,
                                deadlines=[("a", "Alpha", 1)])])
    result = es.run_deadline_alert_sweep()
    assert result["accounts_with_due"] == 0
    assert sent == []


# ---------- the combined /api/email/sweep endpoint ----------
#
# Called directly rather than through a TestClient: the suite's conftest blocks real sockets,
# and starlette's TestClient trips that guard setting up its event loop on Windows. The other
# route tests here take the same handler-level approach. json_response returns a Response
# whose .body is the JSON bytes.

class _Req:
    def __init__(self, secret=None):
        self.headers = {} if secret is None else {"X-Cron-Secret": secret}


@pytest.fixture
def route(monkeypatch):
    """The email route with EMAIL_CRON_SECRET set and both sweeps stubbed, so these tests are
    about the ROUTE (secret gating, kind selection, response shape), not the sweep bodies."""
    from app.routes import email as email_route
    monkeypatch.setattr(email_route, "EMAIL_CRON_SECRET", "s3cr3t")
    monkeypatch.setattr(email_route.email_service, "run_trial_sweep",
                        lambda **k: {"ok": True, "due": 2, "sent": 2, "details": [{"x": 1}]})
    monkeypatch.setattr(email_route.email_service, "run_deadline_alert_sweep",
                        lambda **k: {"ok": True, "accounts_with_due": 1, "sent": 1,
                                     "details": [{"y": 2}]})
    return email_route


def _call(route, body, secret="s3cr3t"):
    resp = route.handle_email_sweep(_Req(secret), body=body)
    return resp.status_code, json.loads(resp.body)


def test_sweep_requires_the_secret(route):
    status, _ = _call(route, {}, secret=None)
    assert status == 403
    status, _ = _call(route, {}, secret="wrong")
    assert status == 403
    status, _ = _call(route, {})
    assert status == 200


def test_sweep_runs_both_kinds_by_default(route):
    status, body = _call(route, {})
    assert status == 200 and body["ok"] is True
    assert "trial" in body and "deadline_alerts" in body


def test_sweep_kind_selects_one(route):
    _, body = _call(route, {"kind": "deadline"})
    assert "deadline_alerts" in body and "trial" not in body


def test_sweep_drops_detail_lists_unless_verbose(route):
    # The per-user detail (addresses) must not land in a scheduler log by default.
    _, quiet = _call(route, {})
    assert "details" not in quiet["deadline_alerts"]
    _, loud = _call(route, {"verbose": True})
    assert "details" in loud["deadline_alerts"]


def test_sweep_fails_closed_without_a_secret(monkeypatch):
    from app.routes import email as email_route
    monkeypatch.setattr(email_route, "EMAIL_CRON_SECRET", "")
    resp = email_route.handle_email_sweep(_Req("anything"), body={})
    assert resp.status_code == 503
