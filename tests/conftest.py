"""Shared test fixtures and safety guards for the unit suite.

The whole unit suite is meant to be hermetic: pure functions in, values out, no
network. This conftest enforces that (blocks real socket connections) and sets the
couple of env vars the auth helpers need to run deterministically.
"""
import os
import socket
import sys

import pytest

# Five unit tests import a script by bare name (`import matching_eval`) that no
# longer lives at the repo root -- the 2026-09-04 tidy-up moved the leaf scripts
# nothing imports into eval/ and scripts/. pytest puts the ROOT on sys.path (the
# tests/ tree is a package, so it walks up to the first directory without an
# __init__.py), which is what makes `import gemini_common` work; these
# directories are not packages and are not on that path, so they are added here.
# The scripts themselves carry their own ROOT shim for when they are RUN; this
# is the mirror of it for when they are IMPORTED.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("eval", "scripts/one-off", "scripts/dev", "scripts/backfill"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Auth token tests need a stable secret; set it before app.auth.tokens reads it.
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod-0123456789")


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
