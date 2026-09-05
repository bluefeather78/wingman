"""The one-time sign-in token leaves the query string — S0-9's second half.

The callback used to 302 to `<app>?google_token=<nonce>`, and the app resolved it with
`GET /api/auth/google/session?token=<nonce>`. Both put a credential in a query string, and
in production the app and the API share one origin — so both landed in Render's access log,
in browser history, and in the Referer of everything the page then loaded.

It is a single-use 5-minute nonce rather than a bearer, which is why this was hardening
rather than the H2 takeover (that one is closed by the exact-origin check). It is still a
credential, and the calendar flow's identical leak was closed by S1-3.
"""
import inspect
import json
import time
import urllib.parse

import pytest

import app.routes.google_oauth as gr
import app.services.google_oauth as g


# ---------------- the redirect ----------------

@pytest.mark.parametrize("dest", [
    "https://highschoolwingman.com/google-auth",
    "http://localhost:8081/google-auth",
    "/",
])
def test_a_web_destination_gets_the_token_in_the_fragment(dest):
    """That redirect hits our OWN origin, so a query string is an access-log entry."""
    url = gr._handoff_url(dest, "nonce-123")
    assert "#google_token=nonce-123" in url
    assert "?google_token" not in url


def test_a_web_destination_with_its_own_query_string_keeps_it():
    url = gr._handoff_url("https://app.example/google-auth?next=%2Ffinder", "n")
    assert url == "https://app.example/google-auth?next=%2Ffinder#google_token=n"


def test_an_existing_fragment_is_replaced_not_appended():
    """Two '#' is not a URL."""
    url = gr._handoff_url("https://app.example/google-auth#stale", "n")
    assert url.count("#") == 1
    assert url.endswith("#google_token=n")


@pytest.mark.parametrize("dest", ["wingman://google-auth", "exp://10.0.0.2:8081/--/google-auth"])
def test_a_custom_scheme_keeps_the_query_string(dest):
    """Not a leak being tolerated — a leak that does not exist. The OS resolves a
    custom-scheme redirect and hands it straight to the app; no HTTP server sees it, so
    there is no access log for it to appear in. Sending a fragment there would gamble on
    iOS and Android preserving fragments across a custom-scheme redirect, which cannot be
    checked from a laptop, and getting it wrong breaks sign-in outright."""
    url = gr._handoff_url(dest, "nonce-123")
    assert "google_token=nonce-123" in url
    assert "#" not in url


def test_the_token_is_url_quoted():
    assert "%2F" in gr._handoff_url("https://app.example/x", "a/b")


def test_the_callback_uses_the_helper():
    """A future edit that hand-builds the URL again reopens this."""
    src = inspect.getsource(gr.handle_google_callback)
    assert "_handoff_url(" in src
    assert "?google_token=" not in src


# ---------------- the session exchange ----------------

def test_session_is_a_post_with_the_token_in_the_body():
    """There is no GET form left; keeping one would keep the leak."""
    paths = {(r.path, tuple(sorted(r.methods))) for r in gr.router.routes
             if r.path == "/api/auth/google/session"}
    assert paths == {("/api/auth/google/session", ("POST",))}
    assert "query_params" not in inspect.getsource(gr.handle_google_session)


def test_a_valid_token_resolves_and_is_consumed(monkeypatch):
    monkeypatch.setattr(g, "_google_session_tokens", {})
    token = g._mint_google_token({"kind": "pending", "google_id": "gid",
                                  "email": "a@b.c", "first_name": "A", "last_name": "B"})
    resp = gr.handle_google_session(body={"token": token})
    assert resp.status_code == 200
    assert json.loads(resp.body)["pending"] is True
    # Single-use: a replayed URL out of history must not resolve twice.
    assert gr.handle_google_session(body={"token": token}).status_code == 400


def test_a_missing_or_unknown_token_is_refused(monkeypatch):
    monkeypatch.setattr(g, "_google_session_tokens", {})
    assert gr.handle_google_session(body={}).status_code == 400
    assert gr.handle_google_session(body={"token": "nope"}).status_code == 400


# ---------------- the client reads both forms ----------------

def _client_parser():
    """Exercise the TypeScript parser's logic against the same cases, in Python.

    The real function is frontend/src/auth/googleSignIn.ts; there is no node toolchain in
    this environment, so this asserts the CONTRACT the server depends on — fragment first,
    query second — and the test file names the source so the two are edited together.
    """
    def parse(url):
        before, _, after = url.partition("#")
        for blob in (after, before.partition("?")[2]):
            for pair in blob.split("&"):
                if not pair:
                    continue
                key, _, value = pair.partition("=")
                if urllib.parse.unquote(key) == "google_token":
                    return urllib.parse.unquote_plus(value)
        return None
    return parse


@pytest.mark.parametrize("dest", [
    "https://highschoolwingman.com/google-auth",
    "wingman://google-auth",
    "exp://10.0.0.2:8081/--/google-auth",
    "/",
])
def test_whatever_the_server_emits_the_client_contract_can_read(dest):
    """The two halves have to agree for every destination shape the allowlist permits."""
    parse = _client_parser()
    assert parse(gr._handoff_url(dest, "nonce-123")) == "nonce-123"


def test_the_client_prefers_the_fragment_when_both_are_present():
    parse = _client_parser()
    assert parse("https://app.example/x?google_token=old#google_token=new") == "new"
