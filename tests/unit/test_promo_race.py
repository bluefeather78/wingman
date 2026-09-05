"""The promo redemption must be a compare-and-swap, not read-check-write — S1-6, M6.

The exploit: fire N parallel POST /api/subscription/redeem-promo with the same 7-day
code. All N read `promo_codes_used == []`, all N pass the "already used?" check, and
interleavings where a later reader sees an earlier writer's `subscription_end_at`
compound the grant (7 -> 14 -> 21 days). Free access indefinitely from a beta code.

Two layers are tested here: the filter redeem_promo_conditional builds (that is what
Postgres arbitrates on), and the route's behaviour when the write reports zero rows.
"""
import urllib.parse

import pytest

import app.core as core
import app.routes.subscription as sub


# ---------------- the conditional write itself ----------------

class _Capture:
    """Stands in for _users_request; records the PATCH and answers a canned row count."""
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, method, query="", data=None, prefer=None):
        self.calls.append({"method": method, "query": query, "data": data,
                           "prefer": prefer})
        return self.rows

    @property
    def params(self):
        """The last query string as a list of (key, value), duplicates preserved."""
        return urllib.parse.parse_qsl(self.calls[-1]["query"].lstrip("?"))


def test_the_write_is_filtered_on_the_array_we_read(monkeypatch):
    cap = _Capture([{"userid": "alice"}])
    monkeypatch.setattr(core, "_users_request", cap)

    assert core.redeem_promo_conditional("alice", "BETAUSER", ["WELCOME10"],
                                         {"subscription_status": "beta"}) is True

    params = cap.params
    assert ("userid", "eq.alice") in params
    # Compare-and-swap: the state we read is part of the WHERE clause.
    assert ("promo_codes_used", "eq.{WELCOME10}") in params
    # And the plan's own filter, belt and braces.
    assert ("promo_codes_used", "not.cs.{BETAUSER}") in params


def test_the_empty_case_matches_null_as_well_as_an_empty_array(monkeypatch):
    """A row predating the column's DEFAULT holds NULL, and every array operator against
    NULL evaluates to NULL — i.e. no match. Without the explicit is.null branch such a
    user could never redeem anything."""
    cap = _Capture([{"userid": "alice"}])
    monkeypatch.setattr(core, "_users_request", cap)

    core.redeem_promo_conditional("alice", "BETAUSER", [], {})

    assert ("or", "(promo_codes_used.is.null,promo_codes_used.eq.{})") in cap.params


def test_zero_rows_matched_reports_a_loss(monkeypatch):
    """This is the race arriving: Postgres re-evaluated the WHERE against the winner's
    row and matched nothing."""
    monkeypatch.setattr(core, "_users_request", _Capture([]))
    assert core.redeem_promo_conditional("alice", "BETAUSER", [], {}) is False


def test_the_write_asks_for_a_representation_so_it_can_count_rows(monkeypatch):
    """`return=minimal` answers an empty body whether one row or none matched, which
    would make every redemption look like a loss."""
    cap = _Capture([{"userid": "alice"}])
    monkeypatch.setattr(core, "_users_request", cap)
    core.redeem_promo_conditional("alice", "BETAUSER", [], {})
    assert cap.calls[-1]["prefer"] == "return=representation"
    assert cap.calls[-1]["method"] == "PATCH"


@pytest.mark.parametrize("code", ["BAD,CODE", "X}", "{Y", "lower", "", None,
                                  "A" * 65, "*"])
def test_a_code_that_could_rewrite_the_filter_is_refused(monkeypatch, code):
    """Codes are hard-coded today, but S1-10 moves them into a table — at which point a
    code containing `,` or `}` would rewrite the filter around it."""
    monkeypatch.setattr(core, "_users_request",
                        lambda *a, **k: pytest.fail("must not reach PostgREST"))
    assert core.redeem_promo_conditional("alice", code, [], {}) is False


def test_an_unexpressible_stored_code_refuses_rather_than_writing_unconditionally(
        monkeypatch):
    """Falling back to an unconditional PATCH here would be the bug, not a graceful
    degradation."""
    monkeypatch.setattr(core, "_users_request",
                        lambda *a, **k: pytest.fail("must not reach PostgREST"))
    assert core.redeem_promo_conditional("alice", "BETAUSER", ["WEIRD,CODE"], {}) is False


# ---------------- the route ----------------

class _User:
    id = "alice"


@pytest.fixture
def _account(monkeypatch):
    state = {"record": {"userid": "alice", "subscription_status": "trial",
                        "trial_ends_at": "2026-09-20T00:00:00+00:00",
                        "promo_codes_used": []}}
    monkeypatch.setattr(sub, "get_user_account", lambda _u: dict(state["record"]))
    return state


def test_the_winner_gets_the_grant(monkeypatch, _account):
    seen = {}
    monkeypatch.setattr(sub, "redeem_promo_conditional",
                        lambda u, c, prev, updates: seen.update(
                            code=c, prev=prev, updates=updates) or True)
    resp = sub.handle_redeem_promo(body={"promo_code": "betauser"}, user=_User())
    assert resp.status_code == 200
    assert seen["code"] == "BETAUSER"
    assert seen["prev"] == []
    # The code is appended by the write, not by the caller mutating its own read.
    assert seen["updates"]["promo_codes_used"] == ["BETAUSER"]
    assert seen["updates"]["subscription_status"] == "beta"


def test_the_loser_of_the_race_is_told_it_was_already_used(monkeypatch, _account):
    """The winner's row now carries the code, so the loser gets the ordinary message
    rather than a confusing conflict."""
    monkeypatch.setattr(sub, "redeem_promo_conditional", lambda *a, **k: False)
    monkeypatch.setattr(sub, "get_user_account",
                        lambda _u: {"userid": "alice", "subscription_status": "beta",
                                    "promo_codes_used": ["BETAUSER"]})
    resp = sub.handle_redeem_promo(body={"promo_code": "BETAUSER"}, user=_User())
    assert resp.status_code == 400


def test_a_lost_write_for_some_other_reason_is_a_conflict_not_a_silent_grant(
        monkeypatch, _account):
    monkeypatch.setattr(sub, "redeem_promo_conditional", lambda *a, **k: False)
    resp = sub.handle_redeem_promo(body={"promo_code": "BETAUSER"}, user=_User())
    assert resp.status_code == 409


def test_the_route_never_retries_a_lost_write(monkeypatch, _account):
    """A retry loop here is the exploit with extra steps."""
    calls = []
    monkeypatch.setattr(sub, "redeem_promo_conditional",
                        lambda *a, **k: calls.append(1) or False)
    sub.handle_redeem_promo(body={"promo_code": "BETAUSER"}, user=_User())
    assert len(calls) == 1


def test_an_already_used_code_never_reaches_the_write(monkeypatch, _account):
    _account["record"]["promo_codes_used"] = ["BETAUSER"]
    monkeypatch.setattr(sub, "get_user_account", lambda _u: dict(_account["record"]))
    monkeypatch.setattr(sub, "redeem_promo_conditional",
                        lambda *a, **k: pytest.fail("must not write"))
    resp = sub.handle_redeem_promo(body={"promo_code": "BETAUSER"}, user=_User())
    assert resp.status_code == 400
