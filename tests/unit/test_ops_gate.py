"""Unit tests for the ops console's gate — S1-8 in SECURITY_HARDENING_PLAN.md.

The hole: the console's ONLY protection was `request.client.host in ("127.0.0.1", ...)`.
That is defeated by any localhost tunnel — ngrok, VS Code port forwarding, a Cloudflare
tunnel — because the tunnel's peer IS 127.0.0.1. And it becomes attacker-controlled the
moment FORWARDED_ALLOW_IPS is set, which is the plausible "fix" for the login-limiter
finding (S0-7) — which is why the plan requires this to ship with or before it.

Behind that gate: subprocess launches that spend real money (/api/agents/run,
/api/agents/tools/run), a roster with the names, emails and plan status of minors
(/api/agents/metrics, /api/agents/user-costs), catalog activation, and test email sends to
arbitrary addresses (/api/agents/emails/test).
"""
import pytest
from fastapi import HTTPException

import ops.admin as admin


class FakeRequest:
    def __init__(self, path, headers=None, host="127.0.0.1"):
        self.url = type("U", (), {"path": path})()
        self.headers = headers or {}
        self.client = type("C", (), {"host": host})() if host else None


# ---------- the token ----------

def test_a_correct_token_is_accepted(monkeypatch):
    monkeypatch.setattr(admin, "WINGMAN_OPS_TOKEN", "s3cret")
    admin.require_ops_token(FakeRequest("/api/agents/run", {"X-Ops-Token": "s3cret"}))


@pytest.mark.parametrize("headers", [{}, {"X-Ops-Token": ""}, {"X-Ops-Token": "wrong"}])
def test_a_missing_or_wrong_token_is_403(monkeypatch, headers):
    monkeypatch.setattr(admin, "WINGMAN_OPS_TOKEN", "s3cret")
    with pytest.raises(HTTPException) as exc:
        admin.require_ops_token(FakeRequest("/api/agents/run", headers))
    assert exc.value.status_code == 403


def test_an_unset_token_fails_closed(monkeypatch):
    """An unset secret must never read as 'no check needed' on routes that spend money and
    expose a roster of minors — the same choice EMAIL_CRON_SECRET and JWT_SECRET make."""
    monkeypatch.setattr(admin, "WINGMAN_OPS_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        admin.require_ops_token(FakeRequest("/api/agents/run", {"X-Ops-Token": "anything"}))
    assert exc.value.status_code == 503


def test_a_non_ascii_token_does_not_500(monkeypatch):
    """hmac.compare_digest raises TypeError on str operands containing non-ASCII — the same
    bug S1-12 fixes on the cron secret. Bytes are compared here, so this is a 403."""
    monkeypatch.setattr(admin, "WINGMAN_OPS_TOKEN", "s3cret")
    with pytest.raises(HTTPException) as exc:
        admin.require_ops_token(FakeRequest("/api/agents/run", {"X-Ops-Token": "é"}))
    assert exc.value.status_code == 403


# ---------- which routes are exempt, and why ----------

@pytest.mark.parametrize("path", sorted(admin._TOKENLESS_PAGES))
def test_the_browser_navigable_shells_are_exempt(monkeypatch, path):
    """A page load cannot set a custom header. These shells carry no credential and no data —
    the console PROMPTS for the token and sends it on every API call — so a tunnel reaches a
    page asking for a secret, not a console."""
    monkeypatch.setattr(admin, "WINGMAN_OPS_TOKEN", "s3cret")
    admin.require_ops_token(FakeRequest(path))


def test_evals_data_is_not_exempt(monkeypatch):
    """Nothing navigates to it (the hub embeds the same payload), so it is tooling, and
    tooling can send a header."""
    monkeypatch.setattr(admin, "WINGMAN_OPS_TOKEN", "s3cret")
    with pytest.raises(HTTPException):
        admin.require_ops_token(FakeRequest("/evals/data"))


def test_every_money_or_pii_route_is_behind_the_token():
    """Named explicitly rather than counted: these are the four the security report calls out
    by name, and an exemption accidentally widened to cover one of them is the failure this
    catches."""
    for path in ("/api/agents/run", "/api/agents/tools/run", "/api/agents/metrics",
                 "/api/agents/user-costs", "/api/agents/emails/test", "/api/seeds"):
        assert path not in admin._TOKENLESS_PAGES


def test_both_gates_are_on_the_router():
    """Localhost-only is kept as well as the token: neither alone is sufficient, and dropping
    either is the regression."""
    deps = {d.dependency for d in admin.router.dependencies}
    assert admin.require_local in deps
    assert admin.require_ops_token in deps


# ---------- the mount ----------

def test_ops_refuses_to_mount_on_render():
    """A hard refusal, not a convention. server.py declining to SET the flag is not the same
    guarantee as the mount being unable to come up — a stray env var in the Render dashboard
    would be enough."""
    import inspect
    import app.main as main

    src = inspect.getsource(main)
    assert 'os.environ.get("WINGMAN_ENABLE_OPS") and os.environ.get("RENDER")' in src
    assert "REFUSING to mount" in src


def test_the_console_sends_the_token_on_every_call():
    """One api() wrapper is the console's only fetch, so the header goes on there. A second
    bare fetch() appearing in the file would bypass it — assert there is still exactly one."""
    from wingman import REPO_ROOT
    import os

    html = open(os.path.join(REPO_ROOT, "ops", "admin_console.html")).read()
    assert "'X-Ops-Token': opsToken()" in html
    assert html.count("fetch(") == 1
