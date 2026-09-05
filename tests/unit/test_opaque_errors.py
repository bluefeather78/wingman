"""Failures carry a reference, not the stack — S1-13, finding L5.

Routes handed the caller the raw exception: `f"Could not reach Supabase: {e}"`,
`f"Matching failed: {e}"`, `str(e)` out of the resume parser — and both AI proxies relayed
the PROVIDER's own error JSON verbatim. None of it carries a key, but it names the database
vendor, the HTTP library, quota states, model names and PostgREST error codes: a free map
of the stack for anybody poking at the app.

The detail is not discarded, it is moved. These tests check both halves — the caller gets
nothing useful, and the operator still gets everything.
"""
import json
import re
import urllib.error

import pytest

import app.deps as deps
import app.routes.ai as ai


REF_RE = re.compile(r"\(ref [0-9a-f]{8}\)$")


@pytest.fixture
def _recorded(monkeypatch):
    rows = []
    monkeypatch.setattr(deps, "record_api_error",
                        lambda *a, **k: rows.append((a, k)))
    return rows


def _err(exc=None, status=502, msg="Something went wrong.", op="test.op"):
    return deps.opaque_error(status, msg, exc or RuntimeError("PGRST301 JWT expired"), op=op)


# ---------------- what the caller gets ----------------

def test_the_exception_text_never_reaches_the_caller(_recorded):
    body = json.loads(_err().body)["error"]
    assert "PGRST301" not in body
    assert "RuntimeError" not in body


def test_the_caller_gets_a_reference_they_can_quote(_recorded):
    """A student who reports 'it said ref 3f9c1a04' can be answered exactly."""
    body = json.loads(_err().body)["error"]
    assert REF_RE.search(body), body


def test_each_failure_gets_its_own_reference(_recorded):
    refs = {json.loads(_err().body)["error"][-11:] for _ in range(20)}
    assert len(refs) == 20


def test_the_status_is_preserved(_recorded):
    assert _err(status=502).status_code == 502
    assert _err(status=500).status_code == 500


# ---------------- what the operator gets ----------------

def test_the_detail_reaches_api_errors_with_the_same_reference(_recorded):
    body = json.loads(_err().body)["error"]
    ref = REF_RE.search(body).group(0)[5:-1]
    (args, kw) = _recorded[0]
    recorded_message = kw["message"]
    assert ref in recorded_message
    assert "PGRST301 JWT expired" in recorded_message
    assert "RuntimeError" in recorded_message


def test_the_op_label_groups_the_row(_recorded):
    _err(op="matching.run")
    args, _ = _recorded[0]
    assert args[1] == "matching.run"
    assert args[3] == "matching.run_failed"


def test_a_broken_recorder_never_becomes_the_failure(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("api_errors is wedged")
    monkeypatch.setattr(deps, "record_api_error", _boom)
    assert _err().status_code == 502


def test_the_response_is_marked_so_the_middleware_does_not_double_log(_recorded):
    assert _err().headers[deps._ERROR_LOGGED_HEADER] == "1"


# ---------------- the AI proxies ----------------

def test_the_provider_error_body_is_not_relayed():
    """This was the worst of them: Anthropic's and Gemini's own error JSON, verbatim, to
    any browser that asked."""
    resp = ai._provider_error_response(429)
    body = json.loads(resp.body)
    assert set(body) == {"error"}
    assert isinstance(body["error"], str)
    assert "rate_limit_error" not in body["error"]


def test_the_upstream_status_is_still_passed_through():
    """The client branches on it — 429 is 'slow down', 5xx is 'try again' — so the status
    has to survive even though the body does not."""
    assert ai._provider_error_response(429).status_code == 429
    assert ai._provider_error_response(529).status_code == 529
    assert ai._provider_error_response(400).status_code == 400


def test_a_busy_provider_says_busy_and_anything_else_says_try_again():
    busy = json.loads(ai._provider_error_response(429).body)["error"]
    other = json.loads(ai._provider_error_response(500).body)["error"]
    assert "busy" in busy.lower()
    assert busy != other


def test_the_proxy_response_is_marked_as_already_logged():
    """_record_provider_failure already wrote the real message to api_errors with the
    provider's own status; a second, detail-free row would just be noise."""
    assert ai._provider_error_response(429).headers[deps._ERROR_LOGGED_HEADER] == "1"


def test_no_route_still_interpolates_an_exception_into_an_error_message():
    """The regression guard. If one comes back, it fails here rather than in production."""
    import pathlib
    offenders = []
    for path in pathlib.Path("app/routes").glob("*.py"):
        for i, line in enumerate(path.read_text().split("\n"), 1):
            code = line.split("#", 1)[0]
            if "json_error(" in code and ("{e}" in code or "str(e)" in code):
                offenders.append(f"{path}:{i}")
    assert not offenders, offenders
