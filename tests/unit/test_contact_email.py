"""Unit tests for contact_email_common.py — email extraction, generic filtering,
visible-text stripping, and contact-page URL guessing. Pure; scan_pages/resolve are
NOT tested (network).
"""
import pytest

import contact_email_common as ce


# --------------------------------------------------------------------------- _is_generic

@pytest.mark.parametrize("email", [
    "noreply@ex.com", "no-reply@ex.com", "webmaster@ex.com", "postmaster@ex.com",
    "privacy@ex.com", "abuse@ex.com",
])
def test_is_generic_localparts(email):
    assert ce._is_generic(email) is True


@pytest.mark.parametrize("email", [
    "a@sentry.io", "b@sentry.wixpress.com", "c@example.com", "d@yourdomain.com",
])
def test_is_generic_domains(email):
    assert ce._is_generic(email) is True


def test_is_generic_real_address():
    assert ce._is_generic("director@mitprimes.org") is False


def test_is_generic_casefold():
    assert ce._is_generic("NoReply@EX.com") is True


# --------------------------------------------------------------------------- _visible_text

def test_visible_text_strips_script_content():
    html = '<p>hello</p><script>var dsn="abc@sentry.io";</script>'
    text = ce._visible_text(html)
    assert "hello" in text
    assert "sentry.io" not in text  # script CONTENT removed, not just the tag


def test_visible_text_strips_style_content():
    html = "<style>.x{color:red}</style><div>visible</div>"
    text = ce._visible_text(html)
    assert "visible" in text
    assert "color" not in text


def test_visible_text_unescapes_entities():
    assert "a & b" in ce._visible_text("<p>a &amp; b</p>")


# --------------------------------------------------------------------------- extract_emails

def test_extract_emails_prefers_mailto():
    html = '<a href="mailto:program@org.edu">Email</a> also info@org.edu in text'
    # mailto present → only mailto addresses returned, text scan skipped.
    assert ce.extract_emails(html) == ["program@org.edu"]


def test_extract_emails_mailto_strips_query():
    html = '<a href="mailto:x@org.edu?subject=Hi">m</a>'
    assert ce.extract_emails(html) == ["x@org.edu"]


def test_extract_emails_plaintext_fallback():
    html = "<p>Contact us at hello@program.org for details.</p>"
    assert ce.extract_emails(html) == ["hello@program.org"]


def test_extract_emails_dedupes_casefold():
    html = "a@org.edu and A@ORG.EDU appear"
    out = ce.extract_emails(html)
    assert len(out) == 1


def test_extract_emails_filters_generic():
    html = "<p>webmaster@org.edu and real@program.org</p>"
    assert ce.extract_emails(html) == ["real@program.org"]


def test_extract_emails_strips_trailing_punctuation():
    html = "<p>Reach us: contact@program.org.</p>"
    assert ce.extract_emails(html) == ["contact@program.org"]


def test_extract_emails_dsn_in_script_not_picked_up():
    # the Sentry-DSN false-positive guard: an address only inside <script> is invisible.
    html = '<script>Sentry.init({dsn:"key@sentry.io"})</script><p>No contact here</p>'
    assert ce.extract_emails(html) == []


def test_extract_emails_empty():
    assert ce.extract_emails("") == []
    assert ce.extract_emails(None) == []


# --------------------------------------------------------------------------- candidate_urls

def test_candidate_urls_landing_first():
    urls = ce.candidate_urls("https://ex.com/dept/program/")
    assert urls[0] == "https://ex.com/dept/program/"


def test_candidate_urls_interleaves_dir_and_root():
    urls = ce.candidate_urls("https://ex.com/dept/program/page")
    # dir guess (same directory) then root guess, per slug, interleaved.
    assert "https://ex.com/dept/program/contact" in urls
    assert "https://ex.com/contact" in urls
    # dir 'contact' should come before root 'contact-us' (interleaving, not two blocks).
    assert urls.index("https://ex.com/dept/program/contact") < urls.index("https://ex.com/contact-us")


def test_candidate_urls_bare_domain_resolves_to_root_slash():
    urls = ce.candidate_urls("https://www.jshs.org")
    # no path → base becomes "/", guesses land at root, no hostless URLs.
    assert "https://www.jshs.org/contact" in urls
    for u in urls:
        assert "///" not in u


def test_candidate_urls_dedupes():
    urls = ce.candidate_urls("https://ex.com/contact")
    assert len(urls) == len(set(urls))
    assert urls[0] == "https://ex.com/contact"


def test_candidate_urls_all_slugs_present():
    urls = ce.candidate_urls("https://ex.com/a/b/page")
    for slug in ce.CONTACT_SLUGS:
        assert "https://ex.com/" + slug in urls


def test_candidate_urls_invalid_returns_landing_only():
    assert ce.candidate_urls("garbage") == ["garbage"]


# --------------------------------------------------- resolve_contact_email (M9: Gemini)

_OPP = {"id": "ec1", "name": "Prog", "org": "Org", "url": "https://ex.com/prog"}


def test_resolve_single_candidate_makes_no_model_call(monkeypatch):
    # Exactly one candidate resolves for free — the model function must NOT be called.
    monkeypatch.setattr(ce, "scan_pages", lambda opp: (["dir@ex.com"], ["u"]))
    monkeypatch.setattr(ce, "call_gemini", lambda *a, **k: pytest.fail("no model call expected"))
    email, cost, used_model, _ = ce.resolve_contact_email(_OPP, "gkey")
    assert email == "dir@ex.com" and cost == 0.0 and used_model is False


def test_resolve_multi_candidate_uses_gemini_and_picks_choice(monkeypatch):
    # Two candidates -> one Gemini call (M9 provider swap). The chosen index is applied,
    # and the call is pinned to the cheap no-search model.
    seen = {}

    def fake_gemini(system, user_content, api_key, **kw):
        seen.update(kw, api_key=api_key)
        return '{"choice": 1, "reason": "program-specific"}', {"usage": "x"}

    monkeypatch.setattr(ce, "scan_pages", lambda opp: (["info@ex.com", "prog@ex.com"], ["u"]))
    monkeypatch.setattr(ce, "call_gemini", fake_gemini)
    monkeypatch.setattr(ce, "estimate_cost", lambda usage: 0.001)
    email, cost, used_model, _ = ce.resolve_contact_email(_OPP, "gkey")
    assert email == "prog@ex.com"          # candidates[1]
    assert used_model is True and cost == 0.001
    assert seen["model"] == ce.CONTACT_MODEL and seen["use_web_search"] is False
    assert seen["api_key"] == "gkey"       # the Gemini key is what gets passed through


def test_resolve_multi_candidate_no_key_leaves_unresolved(monkeypatch):
    # Without a key the multi-candidate row is left unresolved rather than erroring.
    monkeypatch.setattr(ce, "scan_pages", lambda opp: (["info@ex.com", "prog@ex.com"], ["u"]))
    monkeypatch.setattr(ce, "call_gemini", lambda *a, **k: pytest.fail("no model call without key"))
    email, cost, used_model, _ = ce.resolve_contact_email(_OPP, "")
    assert email is None and cost == 0.0 and used_model is False
