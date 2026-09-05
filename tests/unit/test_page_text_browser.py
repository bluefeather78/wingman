"""Unit tests for the headless-browser fallback in page_text (2026-08-28, offline agents only).

The fallback must be a strict, opt-in ENHANCEMENT: identical behaviour when off (every existing
caller), the browser tried only when plain HTTP FAILS and only when asked, never for a URL a
browser can't rescue, and a graceful degrade to plain HTTP when Playwright is absent. None of
these need a real browser — the two halves (_fetch_urllib, _fetch_with_browser) are monkeypatched.
"""
import pytest

from wingman import page_text as pt


@pytest.fixture
def urllib_returns(monkeypatch):
    def set_result(text, reason, final="http://x/final"):
        monkeypatch.setattr(pt, "_fetch_urllib", lambda url, timeout: (text, reason, final))
    return set_result


@pytest.fixture
def browser_returns(monkeypatch):
    calls = []

    def set_result(text, reason, final="http://x/browser"):
        def fake(url, timeout):
            calls.append(url)
            return text, reason, final
        monkeypatch.setattr(pt, "_fetch_with_browser", fake)
        return calls
    return set_result


def _never_browser(monkeypatch):
    def boom(url, timeout):
        raise AssertionError("the browser fallback must not be reached here")
    monkeypatch.setattr(pt, "_fetch_with_browser", boom)


# ---------- off by default: behaviour is byte-identical to plain HTTP ----------

def test_allow_browser_off_never_touches_browser_even_on_failure(monkeypatch, urllib_returns):
    _never_browser(monkeypatch)
    urllib_returns(None, "http-403")
    assert pt.fetch_page_text("http://x", allow_browser=False) == (None, "http-403")


def test_default_is_off(monkeypatch, urllib_returns):
    _never_browser(monkeypatch)
    urllib_returns(None, "http-403")
    # No allow_browser kwarg at all -> default False -> no browser.
    assert pt.fetch_page_text("http://x") == (None, "http-403")


# ---------- on, but only as a FALLBACK ----------

def test_urllib_success_short_circuits_browser(monkeypatch, urllib_returns):
    _never_browser(monkeypatch)
    urllib_returns("real page text " * 20, "ok")
    text, reason = pt.fetch_page_text("http://x", allow_browser=True)
    assert reason == "ok" and text.startswith("real page text")


def test_browser_recovers_a_urllib_failure(urllib_returns, browser_returns):
    urllib_returns(None, "http-403")
    calls = browser_returns("BROWSER TEXT", "ok")
    text, reason, final = pt.fetch_page_text_resolved("http://x", allow_browser=True)
    assert (text, reason, final) == ("BROWSER TEXT", "ok", "http://x/browser")
    assert calls == ["http://x"]           # browser was actually invoked


def test_browser_failure_keeps_the_original_urllib_reason(urllib_returns, browser_returns):
    urllib_returns(None, "http-403")
    browser_returns(None, "browser-error-TimeoutError")
    # The more informative plain-HTTP reason survives, not the browser's.
    assert pt.fetch_page_text("http://x", allow_browser=True) == (None, "http-403")


# ---------- a browser cannot rescue these, so don't spend a page load ----------

@pytest.mark.parametrize("reason", ["no-url", "not-html"])
def test_no_browser_for_unrescuable_reasons(monkeypatch, urllib_returns, reason):
    _never_browser(monkeypatch)
    urllib_returns(None, reason)
    assert pt.fetch_page_text("http://x", allow_browser=True) == (None, reason)


# ---------- graceful degrade when Playwright is absent ----------

def test_degrades_to_plain_http_when_browser_unavailable(monkeypatch, urllib_returns):
    urllib_returns(None, "empty-or-js")
    # Simulate "Playwright not installed": the real _fetch_with_browser runs, its context
    # helper returns None, so it answers (None, 'no-browser', url) and the original reason wins.
    monkeypatch.setattr(pt, "_get_browser_context", lambda: None)
    assert pt.fetch_page_text("http://x", allow_browser=True) == (None, "empty-or-js")


def test_fetch_with_browser_returns_no_browser_when_unavailable(monkeypatch):
    monkeypatch.setattr(pt, "_get_browser_context", lambda: None)
    assert pt._fetch_with_browser("http://x", 20) == (None, "no-browser", "http://x")


# ---------- the M1 agent actually turns the fallback ON ----------

def test_refresh_default_fetch_enables_the_browser(monkeypatch):
    """refresh_opportunities is the one caller that opts in. If a refactor drops
    allow_browser=True the ~22% of bot-walled/SPA rows silently go back to being skipped,
    so pin it here."""
    from agents import refresh_opportunities as ro
    seen = {}
    monkeypatch.setattr(ro.page_text, "fetch_page_text",
                        lambda url, allow_browser=False: seen.update(url=url, allow_browser=allow_browser) or (None, "http-403"))
    ro._default_fetch("http://prog.example/x")
    assert seen == {"url": "http://prog.example/x", "allow_browser": True}
