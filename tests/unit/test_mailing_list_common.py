"""Unit tests for wingman/mailing_list_common.py — provider detection, form extraction,
field resolution, JSON/JSONP parsing, and recipe execution (with _post monkeypatched).

All hermetic: the only network seam is `_post`, which every test that reaches an adapter
replaces with a canned (status, body) tuple. Nothing here fetches a page.
"""
import pytest

from wingman import mailing_list_common as mlc


# --------------------------------------------------------------------------- extract_forms

def test_extract_forms_basic_action_inputs_text():
    html = (
        '<form action="/subscribe" method="post">'
        '<input name="EMAIL" type="email">'
        '<select name="INTEREST"></select>'
        'Join our list</form>'
    )
    forms = mlc.extract_forms(html, page_url="https://ex.com/programs/x")
    assert len(forms) == 1
    f = forms[0]
    # urljoin against page_url makes the relative action absolute.
    assert f["action"] == "https://ex.com/subscribe"
    assert f["inputs"] == ["EMAIL", "INTEREST"]
    assert "Join our list" in f["text"]


def test_extract_forms_amp_unescape_bug():
    """The documented &amp;-encoding bug: an action written with &amp;id must be unescaped
    BEFORE query parsing, or it parses as a param literally named 'amp;id'."""
    html = (
        '<form action="https://x.us1.list-manage.com/subscribe/post?u=abc&amp;id=def">'
        '<input name="EMAIL"></form>'
    )
    forms = mlc.extract_forms(html)
    assert forms[0]["action"] == "https://x.us1.list-manage.com/subscribe/post?u=abc&id=def"


def test_extract_forms_double_encoded_amp():
    """A doubly-encoded &amp;amp; — html.unescape only unwinds one level, leaving 'amp;id'.
    Pin the actual behavior rather than assume full repair."""
    html = '<form action="/x?u=1&amp;amp;id=2"><input name="EMAIL"></form>'
    action = mlc.extract_forms(html)[0]["action"]
    assert action == "/x?u=1&amp;id=2"


def test_extract_forms_no_action():
    html = '<form><input name="EMAIL"></form>'
    forms = mlc.extract_forms(html)
    assert forms[0]["action"] == ""
    assert forms[0]["inputs"] == ["EMAIL"]


def test_extract_forms_none_and_empty():
    assert mlc.extract_forms(None) == []
    assert mlc.extract_forms("") == []


def test_extract_forms_text_truncated_at_600():
    body = "x " * 500
    html = f'<form action="/s"><input name="E">{body}</form>'
    assert len(mlc.extract_forms(html)[0]["text"]) <= 600


# --------------------------------------------------------------------------- candidate_urls

def test_candidate_urls_landing_first_and_paths():
    urls = mlc.candidate_urls("https://ex.com/programs/x")
    assert urls[0] == "https://ex.com/programs/x"
    for p in mlc.CANDIDATE_PATHS:
        assert "https://ex.com" + p in urls


def test_candidate_urls_dedupes_landing():
    # Landing page equal to a candidate path collapses; landing stays first.
    urls = mlc.candidate_urls("https://ex.com/newsletter")
    assert urls[0] == "https://ex.com/newsletter"
    assert urls.count("https://ex.com/newsletter") == 1


def test_candidate_urls_bad_url_returns_just_landing():
    assert mlc.candidate_urls("not a url") == ["not a url"]


# --------------------------------------------------------------------------- _mailchimp_field_map

def test_mailchimp_field_map_casefold_and_optional():
    fm = mlc._mailchimp_field_map(["email", "fname", "LNAME"])
    # keys preserve the form's own casing; only present optionals are mapped.
    assert fm["email"] == "$email"
    assert fm["fname"] == "$first_name"
    assert fm["LNAME"] == "$last_name"


def test_mailchimp_field_map_defaults_email_when_absent():
    fm = mlc._mailchimp_field_map(["SomethingElse"])
    assert fm == {"EMAIL": "$email"}


# --------------------------------------------------------------------------- detect_provider

def test_detect_mailchimp_rewrites_to_post_json():
    form = {"action": "https://x.us1.list-manage.com/subscribe/post?u=aaa&id=bbb",
            "inputs": ["EMAIL", "FNAME"]}
    r = mlc.detect_provider(form)
    assert r["method"] == "mailchimp"
    assert r["endpoint"] == "https://x.us1.list-manage.com/subscribe/post-json"
    assert r["params"] == {"u": "aaa", "id": "bbb"}
    assert r["field_map"]["EMAIL"] == "$email"
    assert r["field_map"]["FNAME"] == "$first_name"


def test_detect_mailchimp_already_post_json_kept():
    form = {"action": "https://x.us1.list-manage.com/subscribe/post-json?u=1&id=2",
            "inputs": ["EMAIL"]}
    r = mlc.detect_provider(form)
    assert r["endpoint"] == "https://x.us1.list-manage.com/subscribe/post-json"


def test_detect_mailchimp_requires_u_and_id():
    form = {"action": "https://x.list-manage.com/subscribe/post?u=1", "inputs": ["EMAIL"]}
    assert mlc.detect_provider(form) is None
    form2 = {"action": "https://x.list-manage.com/subscribe/post?id=2", "inputs": ["EMAIL"]}
    assert mlc.detect_provider(form2) is None


def test_detect_convertkit_and_kit():
    for host in ("convertkit", "kit"):
        form = {"action": f"https://app.{host}.com/forms/12345/subscriptions", "inputs": []}
        r = mlc.detect_provider(form)
        assert r["method"] == "convertkit"
        assert r["endpoint"] == f"https://app.{host}.com/forms/12345/subscriptions"
        assert r["field_map"] == {"email_address": "$email", "first_name": "$first_name"}


def test_detect_mailerlite():
    form = {"action": "https://assets.mailerlite.com/jsonp/99/forms/77/subscribe?x=1",
            "inputs": []}
    r = mlc.detect_provider(form)
    assert r["method"] == "mailerlite"
    # query stripped from endpoint.
    assert r["endpoint"] == "https://assets.mailerlite.com/jsonp/99/forms/77/subscribe"
    assert r["field_map"] == {"fields[email]": "$email", "fields[name]": "$first_name"}


def test_detect_substack_host_from_action():
    form = {"action": "https://myhub.substack.com/api/v1/free", "inputs": ["email"]}
    r = mlc.detect_provider(form)
    assert r["method"] == "substack"
    assert r["endpoint"] == "https://myhub.substack.com/api/v1/free"


def test_detect_substack_host_from_page_url():
    # action has no substack host, but page_url does, and an email input is present.
    form = {"action": "/api/v1/free", "inputs": ["email"]}
    r = mlc.detect_provider(form, page_url="https://pub.substack.com/welcome")
    assert r["method"] == "substack"
    assert r["endpoint"] == "https://pub.substack.com/api/v1/free"


def test_detect_substack_needs_email_or_substack_signal():
    # host resolved from page_url only, no email input and no 'substack' in the action → None.
    form = {"action": "/other", "inputs": ["name"]}
    assert mlc.detect_provider(form, page_url="https://pub.substack.com/x") is None


def test_detect_substack_action_containing_substack_matches():
    # 'substack' appears in the action string, so it matches even without an email input.
    form = {"action": "https://pub.substack.com/other", "inputs": ["name"]}
    r = mlc.detect_provider(form)
    assert r["method"] == "substack"


def test_detect_provider_unknown_returns_none():
    assert mlc.detect_provider({"action": "https://example.com/form", "inputs": ["email"]}) is None


def test_detect_provider_empty_form():
    assert mlc.detect_provider({}) is None


# --------------------------------------------------------------------------- find_candidates

def test_find_candidates_attaches_source_and_text():
    html = (
        '<form action="https://x.us1.list-manage.com/subscribe/post?u=1&amp;id=2">'
        '<input name="EMAIL">Newsletter</form>'
    )
    out = mlc.find_candidates("https://x.us1.list-manage.com/page", html)
    assert len(out) == 1
    assert out[0]["method"] == "mailchimp"
    assert out[0]["source_url"] == "https://x.us1.list-manage.com/page"
    assert "Newsletter" in out[0]["form_text"]


def test_find_candidates_none_when_no_provider():
    html = '<form action="/plain"><input name="email"></form>'
    assert mlc.find_candidates("https://ex.com", html) == []


# --------------------------------------------------------------------------- resolve_fields

def test_resolve_fields_substitutes_and_drops_empty():
    fm = {"EMAIL": "$email", "FNAME": "$first_name"}
    resolved = mlc.resolve_fields(fm, {"email": "a@b.com", "first_name": ""})
    assert resolved == {"EMAIL": "a@b.com"}  # empty first_name dropped


def test_resolve_fields_keeps_literal_constant():
    fm = {"ml-submit": "1", "EMAIL": "$email"}
    resolved = mlc.resolve_fields(fm, {"email": "a@b.com"})
    assert resolved == {"ml-submit": "1", "EMAIL": "a@b.com"}


def test_resolve_fields_missing_value_dropped():
    fm = {"EMAIL": "$email"}
    assert mlc.resolve_fields(fm, {}) == {}


def test_resolve_fields_none_map():
    assert mlc.resolve_fields(None, {"email": "x"}) == {}


def test_resolve_fields_strips_whitespace():
    resolved = mlc.resolve_fields({"EMAIL": "$email"}, {"email": "  a@b.com  "})
    assert resolved == {"EMAIL": "a@b.com"}


# --------------------------------------------------------------------------- _loads

def test_loads_bare_json():
    assert mlc._loads('{"result": "success"}') == {"result": "success"}


def test_loads_jsonp_wrapper():
    assert mlc._loads('callback({"a": 1});') == {"a": 1}


def test_loads_jsonp_dotted_callback():
    assert mlc._loads('ml_webform.success({"success": true})') == {"success": True}


def test_loads_invalid_returns_none():
    assert mlc._loads("not json at all") is None
    assert mlc._loads("") is None
    assert mlc._loads(None) is None


# --------------------------------------------------------------------------- execute / adapters

def _patch_post(monkeypatch, status, body):
    calls = {}

    def fake_post(url, data, headers=None, timeout=mlc.SUBSCRIBE_TIMEOUT):
        calls["url"] = url
        calls["data"] = data
        return status, body

    monkeypatch.setattr(mlc, "_post", fake_post)
    return calls


def test_execute_handoff_for_unknown_method():
    state, msg, detail = mlc.execute({"method": "beehiiv", "endpoint": "x"}, {})
    assert state == "handoff"


def test_execute_handoff_when_no_endpoint():
    state, _, _ = mlc.execute({"method": "mailchimp"}, {})
    assert state == "handoff"


def test_execute_handoff_when_recipe_none():
    state, _, _ = mlc.execute(None, {})
    assert state == "handoff"


def test_execute_fails_when_no_email_field(monkeypatch):
    _patch_post(monkeypatch, 200, '{"result":"success"}')
    recipe = {"method": "mailchimp", "endpoint": "https://x/subscribe/post-json",
              "params": {"u": "1", "id": "2"}, "field_map": {"FNAME": "$first_name"}}
    state, msg, _ = mlc.execute(recipe, {"first_name": "Al"})
    assert state == "failed"
    assert "email" in msg.lower()


def test_execute_mailchimp_success_state_is_submitted(monkeypatch):
    _patch_post(monkeypatch, 200, '{"result":"success","msg":"Almost finished"}')
    recipe = {"method": "mailchimp", "endpoint": "https://x/subscribe/post-json",
              "params": {"u": "1", "id": "2"}, "field_map": {"EMAIL": "$email"}}
    state, msg, detail = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "submitted"  # NOT "subscribed"
    assert "confirmation" in msg.lower()


def test_execute_mailchimp_already_subscribed(monkeypatch):
    _patch_post(monkeypatch, 200,
                '{"result":"error","msg":"a@b.com is already subscribed to list"}')
    recipe = {"method": "mailchimp", "endpoint": "https://x/subscribe/post-json",
              "params": {"u": "1", "id": "2"}, "field_map": {"EMAIL": "$email"}}
    state, msg, _ = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "already_subscribed"


def test_execute_mailchimp_error_strips_html(monkeypatch):
    _patch_post(monkeypatch, 200,
                '{"result":"error","msg":"<b>Please</b> enter a valid email"}')
    recipe = {"method": "mailchimp", "endpoint": "https://x/subscribe/post-json",
              "params": {"u": "1", "id": "2"}, "field_map": {"EMAIL": "$email"}}
    state, msg, _ = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "failed"
    assert "<b>" not in msg
    assert "Please enter a valid email" in msg


def test_execute_mailchimp_unparseable_body(monkeypatch):
    _patch_post(monkeypatch, 500, "gateway error")
    recipe = {"method": "mailchimp", "endpoint": "https://x/subscribe/post-json",
              "params": {"u": "1", "id": "2"}, "field_map": {"EMAIL": "$email"}}
    state, msg, detail = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "failed"
    assert "HTTP 500" in detail


def test_execute_substack_success(monkeypatch):
    _patch_post(monkeypatch, 200, "{}")
    recipe = {"method": "substack", "endpoint": "https://p.substack.com/api/v1/free",
              "field_map": {"email": "$email"}}
    state, _, _ = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "submitted"


def test_execute_substack_failure(monkeypatch):
    _patch_post(monkeypatch, 403, "denied")
    recipe = {"method": "substack", "endpoint": "https://p.substack.com/api/v1/free",
              "field_map": {"email": "$email"}}
    state, _, detail = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "failed"
    assert "HTTP 403" in detail


@pytest.mark.parametrize("status,body", [
    (200, '{"status":"success"}'),
    (201, '{"status":"quarantined"}'),
    (200, '{"subscription":{"id":1}}'),
])
def test_execute_convertkit_success_variants(monkeypatch, status, body):
    _patch_post(monkeypatch, status, body)
    recipe = {"method": "convertkit", "endpoint": "https://app.kit.com/forms/1/subscriptions",
              "field_map": {"email_address": "$email"}}
    state, _, _ = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "submitted"


def test_execute_convertkit_failure(monkeypatch):
    _patch_post(monkeypatch, 200, '{"status":"error"}')
    recipe = {"method": "convertkit", "endpoint": "https://app.kit.com/forms/1/subscriptions",
              "field_map": {"email_address": "$email"}}
    state, _, _ = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "failed"


def test_execute_mailerlite_success(monkeypatch):
    _patch_post(monkeypatch, 200, '{"success":true}')
    recipe = {"method": "mailerlite",
              "endpoint": "https://assets.mailerlite.com/jsonp/1/forms/2/subscribe",
              "field_map": {"fields[email]": "$email"}}
    state, _, _ = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "submitted"


def test_execute_mailerlite_already_subscribed(monkeypatch):
    _patch_post(monkeypatch, 200, "you are already subscribed here")
    recipe = {"method": "mailerlite",
              "endpoint": "https://assets.mailerlite.com/jsonp/1/forms/2/subscribe",
              "field_map": {"fields[email]": "$email"}}
    state, _, _ = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "already_subscribed"


def test_execute_mailerlite_failure(monkeypatch):
    _patch_post(monkeypatch, 200, '{"success":false}')
    recipe = {"method": "mailerlite",
              "endpoint": "https://assets.mailerlite.com/jsonp/1/forms/2/subscribe",
              "field_map": {"fields[email]": "$email"}}
    state, _, _ = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "failed"


def test_execute_adapter_exception_is_caught(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network exploded")
    monkeypatch.setattr(mlc, "_post", boom)
    recipe = {"method": "substack", "endpoint": "https://p.substack.com/api/v1/free",
              "field_map": {"email": "$email"}}
    state, msg, detail = mlc.execute(recipe, {"email": "a@b.com"})
    assert state == "failed"
    assert "network exploded" in detail
