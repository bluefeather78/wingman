"""Unit tests for app.core.provider_for_model.

This file used to test classify_feature, an ORDERED substring dispatch over
_FEATURE_SIGNATURES that guessed which feature a call belonged to by looking for phrases in
the system prompt — a prompt the CLIENT supplied. S1-1 deleted both: the client now names a
server-side feature id, so attribution is exact rather than a guess that could be gamed by
rewording a request. The registry is covered by tests/unit/test_prompt_registry.py.

provider_for_model is unrelated and stays here.
"""
import pytest

import app.core as core


# ---------------------------------------------------------------------------
# provider_for_model — model id first, surface fallback, then 'unknown'.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model,expected", [
    ("claude-haiku-4-5-20251001", "anthropic"),
    ("claude-sonnet-4-6", "anthropic"),
    ("CLAUDE-HAIKU", "anthropic"),          # case-insensitive
    ("gemini-3.6-flash", "google"),
    ("gemini-3.5-flash-lite", "google"),
    ("  gemini-3.6-flash  ", "google"),     # trimmed
])
def test_provider_from_model_prefix(model, expected):
    assert core.provider_for_model(model) == expected


def test_model_prefix_wins_over_surface():
    """A recognised model id takes precedence over the surface fallback."""
    assert core.provider_for_model("claude-haiku-4-5", surface="gemini") == "anthropic"


@pytest.mark.parametrize("surface,expected", [
    ("claude", "anthropic"),
    ("deadline_check", "anthropic"),
    ("gemini", "google"),
])
def test_surface_fallback_when_model_blank(surface, expected):
    """Rows written before the model column existed carry '' model — the surface
    fallback keeps them in the right provider bucket instead of 'unknown'."""
    assert core.provider_for_model("", surface=surface) == expected
    assert core.provider_for_model(None, surface=surface) == expected


def test_unknown_model_and_unknown_surface_is_unknown():
    assert core.provider_for_model("gpt-4o", surface=None) == "unknown"
    assert core.provider_for_model("gpt-4o", surface="mystery") == "unknown"


def test_empty_model_and_no_surface_is_unknown():
    assert core.provider_for_model("", surface=None) == "unknown"
    assert core.provider_for_model(None, surface=None) == "unknown"
    assert core.provider_for_model(None) == "unknown"
