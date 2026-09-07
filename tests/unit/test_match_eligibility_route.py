"""POST /api/match/eligibility — the form/quiz path's eligibility gate (MARQUEE M9).

The theme path gets its gate inside /api/match; this route gives the client-side form/quiz
path the SAME gate over an already-chosen candidate set. Tests call the handler directly (the
style test_subscription_gate uses for the AI route) with the paid provider + Supabase reads
monkeypatched — no network. The gate's own verdict/quote logic is covered in
test_pool_eligibility; here we pin the ROUTE wiring: the degrade branches that must never spend,
and that a verified-ineligible id is dropped on the live path.
"""
import json

import pytest

from app.auth import AuthedUser
from app.routes import matching


def _body(resp):
    return json.loads(resp.body)


def _no_downstream(monkeypatch):
    # Any of these being reached on a degrade branch is the bug (a paid call with nothing to gate).
    for attr in ("fetch_opportunities", "call_gemini"):
        monkeypatch.setattr(matching, attr,
                            lambda *a, **k: pytest.fail(f"{attr} must not run on a degrade branch"))
    monkeypatch.setattr(matching, "touch_user_activity", lambda *a, **k: None)


def test_empty_candidate_ids_is_a_free_noop(monkeypatch):
    _no_downstream(monkeypatch)
    resp = matching.handle_match_eligibility({"candidate_ids": []}, AuthedUser(id="u"))
    assert resp.status_code == 200
    assert _body(resp) == {"excluded_ineligible": [], "checked": 0, "called": False}


def test_no_key_gates_nothing(monkeypatch):
    # No provider key -> honest degrade (gate nothing), never a 402/500 that blanks the grid.
    monkeypatch.setattr(matching, "GEMINI_API_KEY", "")
    _no_downstream(monkeypatch)
    resp = matching.handle_match_eligibility({"candidate_ids": ["a"]}, AuthedUser(id="u"))
    assert resp.status_code == 200
    assert _body(resp)["called"] is False


def test_open_circuit_breaker_gates_nothing(monkeypatch):
    monkeypatch.setattr(matching, "GEMINI_API_KEY", "live-key")
    monkeypatch.setattr(matching.budget, "circuit_open", lambda: True)
    _no_downstream(monkeypatch)
    resp = matching.handle_match_eligibility({"candidate_ids": ["a"]}, AuthedUser(id="u"))
    assert resp.status_code == 200
    assert _body(resp)["called"] is False


def test_live_path_drops_verified_ineligible(monkeypatch):
    monkeypatch.setattr(matching, "GEMINI_API_KEY", "live-key")
    monkeypatch.setattr(matching, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(matching, "SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(matching.budget, "circuit_open", lambda: False)
    monkeypatch.setattr(matching, "touch_user_activity", lambda *a, **k: None)
    monkeypatch.setattr(matching, "record_interactive_cost_async", lambda *a, **k: None)

    rows = [
        {"id": "a", "name": "Girls Who Code",
         "eligibility": "Open to female and non-binary students only."},
        {"id": "b", "name": "Plain Program", "summary": "A robotics build camp.",
         "eligibility": None},
    ]
    monkeypatch.setattr(matching, "fetch_opportunities", lambda: rows)

    # The model excludes 'a' with a VERBATIM quote from its own eligibility text -> the guard
    # lets the cut stand. 'b' carries no restriction signal, so it never reaches the model.
    def fake_gemini(system, user_content, key, **kwargs):
        verdicts = {"verdicts": [
            {"id": "a", "eligible": False,
             "exclusion_quote": "Open to female and non-binary students only.",
             "exclusion_source_field": "eligibility"},
        ]}
        return json.dumps(verdicts), {"input_tokens": 10, "output_tokens": 5}
    monkeypatch.setattr(matching, "call_gemini", fake_gemini)

    resp = matching.handle_match_eligibility(
        {"candidate_ids": ["a", "b"], "grade": 10,
         "funnel_answers": {"gender": "male"}},
        AuthedUser(id="u"))
    assert resp.status_code == 200
    body = _body(resp)
    assert body["excluded_ineligible"] == ["a"]
    assert body["called"] is True


def test_unknown_ids_are_a_free_noop(monkeypatch):
    # ids that no longer exist in the catalog (inactive/stale) -> nothing to gate, no model call.
    monkeypatch.setattr(matching, "GEMINI_API_KEY", "live-key")
    monkeypatch.setattr(matching, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(matching, "SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(matching.budget, "circuit_open", lambda: False)
    monkeypatch.setattr(matching, "touch_user_activity", lambda *a, **k: None)
    monkeypatch.setattr(matching, "fetch_opportunities", lambda: [{"id": "z"}])
    monkeypatch.setattr(matching, "call_gemini",
                        lambda *a, **k: pytest.fail("no candidate matched — must not call the model"))
    resp = matching.handle_match_eligibility({"candidate_ids": ["a", "b"]}, AuthedUser(id="u"))
    assert resp.status_code == 200
    assert _body(resp)["called"] is False
