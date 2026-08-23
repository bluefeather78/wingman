"""Shared test fixtures and safety guards for the unit suite.

The whole unit suite is meant to be hermetic: pure functions in, values out, no
network. This conftest enforces that (blocks real socket connections) and sets the
couple of env vars the auth helpers need to run deterministically.
"""
import os
import socket

import pytest

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
