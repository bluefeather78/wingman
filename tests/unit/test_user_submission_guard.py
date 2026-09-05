"""The catalog write is authenticated, bounded and public-URL-only — S1-4, M10 + M1.

POST /api/user-submitted-opportunities used to take a row from anybody with no token. Each
call also reads the WHOLE catalog (~1,400 rows over two pages) for dedupe, so a script both
amplified against a free-tier instance and buried real submissions under thousands of fakes
— with the stored name/summary rendering in the admin console. And because the deadline
check has no is_active filter by design, a submitted URL became a standing SSRF target.
"""
import inspect

import pytest

import app.routes.resume as resume_route
from app.auth.ratelimit import RateLimiter


class _User:
    id = "alice"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """A fresh limiter per test, a stub insert, and deterministic URL judgement."""
    monkeypatch.setattr(resume_route, "user_submission_limiter", RateLimiter(3, 3600))
    calls = []
    monkeypatch.setattr(resume_route.resume_service, "insert_user_opportunity",
                        lambda *a, **k: calls.append((a, k)) or "us123")
    monkeypatch.setattr(
        resume_route, "url_block_reason",
        lambda u: None if str(u).startswith("https://ok.example") else "not public")
    return calls


def _submit(**over):
    body = {"name": "Cool Program", "url": "https://ok.example/p"}
    body.update(over)
    return resume_route.handle_user_submitted_opportunity(body=body, user=_User())


# ---------------- auth ----------------

def test_the_route_requires_a_subscription_not_optional_auth():
    """The finding IS the dependency. Assert the wiring, because the handler body cannot
    see which dependency FastAPI resolved."""
    sig = inspect.signature(resume_route.handle_user_submitted_opportunity)
    dep = sig.parameters["user"].default
    assert dep.dependency is resume_route.require_subscription


# ---------------- the URL check ----------------

def test_a_private_url_is_refused_before_it_is_stored(_isolated):
    resp = _submit(url="http://10.0.0.5:8080/")
    assert resp.status_code == 400
    assert not _isolated, "a blocked URL must never reach the insert"


def test_the_refusal_does_not_echo_the_internal_detail(_isolated):
    """'10.0.0.5 is not a public address' confirms the probe for the attacker."""
    import json
    body = json.loads(_submit(url="http://10.0.0.5:8080/").body)
    assert "10.0.0.5" not in body["error"]


def test_a_public_url_still_goes_through(_isolated):
    assert _submit().status_code == 200
    assert len(_isolated) == 1


def test_a_bad_apply_url_is_dropped_not_fatal(_isolated):
    """apply_url only lands in submission_payload, so losing it beats losing the row."""
    assert _submit(apply_url="http://127.0.0.1/x").status_code == 200
    args = _isolated[0][0]
    assert args[9] == ""          # apply_url, positionally


# ---------------- the daily cap ----------------

def test_the_daily_cap_stops_a_flood(_isolated):
    for _ in range(3):
        assert _submit().status_code == 200
    resp = _submit()
    assert resp.status_code == 429
    assert resp.headers["Retry-After"]


def test_the_cap_is_checked_before_the_catalog_read(_isolated):
    """The catalog read is the expensive half — the amplification the finding names."""
    for _ in range(3):
        _submit()
    before = len(_isolated)
    _submit()
    assert len(_isolated) == before


def test_the_cap_is_per_account(monkeypatch, _isolated):
    for _ in range(3):
        _submit()

    class _Bob:
        id = "bob"

    resp = resume_route.handle_user_submitted_opportunity(
        body={"name": "n", "url": "https://ok.example/p"}, user=_Bob())
    assert resp.status_code == 200


# ---------------- field limits ----------------

def test_long_text_is_truncated_not_stored_whole(_isolated):
    _submit(name="A" * 5000, fit="B" * 50_000)
    name, _url, _t, _s, meta, fit = _isolated[0][0][:6]
    assert len(name) == resume_route.USER_SUBMISSION_MAX_NAME
    assert len(fit) == resume_route.USER_SUBMISSION_MAX_TEXT


def test_the_array_fields_are_bounded(_isolated):
    _submit(important_dates=[{"d": i} for i in range(500)],
            requirements=["r"] * 500)
    args = _isolated[0][0]
    assert len(args[7]) == resume_route.USER_SUBMISSION_MAX_LIST   # important_dates
    assert len(args[8]) == resume_route.USER_SUBMISSION_MAX_LIST   # requirements


def test_a_non_list_array_field_is_dropped_rather_than_sliced(_isolated):
    """A string sliced to 40 characters would be stored as if it were a list."""
    _submit(important_dates="not a list")
    assert _isolated[0][0][7] == []


def test_a_non_string_text_field_is_dropped_rather_than_coerced(_isolated):
    _submit(fit={"nested": "object"})
    assert _isolated[0][0][5] == ""


def test_name_and_url_are_still_required(_isolated):
    assert _submit(name="").status_code == 400
    assert _submit(url="").status_code == 400
