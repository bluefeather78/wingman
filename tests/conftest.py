"""Shared test fixtures and safety guards for the unit suite.

The whole unit suite is meant to be hermetic: pure functions in, values out, no
network. This conftest enforces that (blocks real socket connections) and sets the
couple of env vars the auth helpers need to run deterministically.
"""
import os
import socket
import sys

import pytest

# Auth token tests need a stable secret; set it before app.auth.tokens reads it.
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod-0123456789")

# `from wingman import ...` and `from agents import ...` resolve without help: pytest puts
# the repo ROOT on sys.path (the tests/ tree is a package, so it walks up to the first
# directory without an __init__.py), and both are real packages under it.
#
# eval/ and scripts/ are NOT packages -- they hold standalone scripts, run as
# `python eval/matching_eval.py`. Five unit tests import those modules by bare name, so
# their directories go on the path explicitly. Do not 'simplify' these away by assuming
# the package move covered them; it did not, and the failure is a collection error in
# test_matching_eval / test_dedupe_eval / test_grade_scraper_batch /
# test_backfill_match_vectors / test_backfill_attribution.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("eval", "scripts/one-off", "scripts/dev", "scripts/backfill"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Fail loudly if a unit test tries to open a real network connection.

    Anything that would hit Supabase / Gemini / Claude / Stripe must be mocked;
    a test that reaches this guard is testing the wrong seam.
    """
    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "Unit test attempted a real network connection. Mock the seam instead."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)


@pytest.fixture
def postal_address(monkeypatch):
    """A configured CAN-SPAM postal address, for the email-template tests.

    `EMAIL_POSTAL_ADDRESS` defaults to the deliberately obvious `[SET EMAIL_POSTAL_ADDRESS
    IN .env]` placeholder (app/config.py), so the "no placeholder reaches a rendered email"
    tests were really asserting that whoever ran them had a .env — green on a dev laptop,
    red in CI, which is what they did. What those tests are for is the TEMPLATE: that the
    configured address is substituted into both the HTML and the text footer. So pin the
    value here and let them assert that instead. Whether the real address is set in the
    Render dashboard is a deploy check, not something a hermetic unit test can see.

    Patched on app.services.email_templates, where the footer builders read it at call
    time; the app.config constant is bound at import and is not what renders.
    """
    from app.services import email_templates

    address = "Wingman, 1 Test Street, Testville CA 90000"
    monkeypatch.setattr(email_templates, "EMAIL_POSTAL_ADDRESS", address)
    return address
