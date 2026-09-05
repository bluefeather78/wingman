"""Unit tests for wingman/url_validate.py — grounding resolution + liveness checks. FREE/HTTP only.

Network is mocked at the urlopen seam. Nothing here touches a real socket.
"""
import socket
import ssl
import urllib.error

import pytest

from wingman import url_validate as uv


# ------------------------------------------------------------------- _is_dns_failure
def test_is_dns_failure_direct_gaierror():
    assert uv._is_dns_failure(socket.gaierror("no such host")) is True


def test_is_dns_failure_wrapped_in_urlerror():
    # NXDOMAIN arrives as URLError(reason=gaierror) — must be read as absence.
    exc = urllib.error.URLError(socket.gaierror("Name or service not known"))
    assert uv._is_dns_failure(exc) is True


def test_is_dns_failure_tls_is_not_dns():
    # TLS failure is OUR client failing, not the page being gone -> False.
    exc = urllib.error.URLError(ssl.SSLError("TLSV1_UNRECOGNIZED_NAME"))
    assert uv._is_dns_failure(exc) is False


def test_is_dns_failure_timeout_is_not_dns():
    exc = urllib.error.URLError(socket.timeout("timed out"))
    assert uv._is_dns_failure(exc) is False


def test_is_dns_failure_plain_exception():
    assert uv._is_dns_failure(ValueError("nope")) is False


def test_is_dns_failure_none():
    assert uv._is_dns_failure(None) is False


def test_is_dns_failure_does_not_loop_on_self_reference():
    # A URLError whose reason points back at itself must terminate.
    exc = urllib.error.URLError("x")
    exc.reason = exc
    assert uv._is_dns_failure(exc) is False


# ------------------------------------------------------------------- same_host
def test_same_host_www_ignored():
    assert uv.same_host("https://www.example.com/a", "https://example.com/b") is True


def test_same_host_different():
    assert uv.same_host("https://a.com", "https://b.com") is False


def test_same_host_empty():
    assert uv.same_host("", "https://a.com") is False


# ------------------------------------------------------------------- is_bare_domain
@pytest.mark.parametrize("url,expected", [
    ("https://example.com", True),
    ("https://example.com/", True),
    ("https://example.com/program", False),
    ("https://example.com/?q=1", False),   # query present -> not bare
    ("", False),
])
def test_is_bare_domain(url, expected):
    assert uv.is_bare_domain(url) is expected


# ------------------------------------------------------------------- is_grounding_redirect
def test_is_grounding_redirect_true():
    assert uv.is_grounding_redirect(
        "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc") is True


def test_is_grounding_redirect_false():
    assert uv.is_grounding_redirect("https://nih.gov/x") is False


def test_is_grounding_redirect_empty():
    assert uv.is_grounding_redirect("") is False


# ------------------------------------------------------------------- domain_matches_org
def test_domain_matches_org_acronym_label():
    assert uv.domain_matches_org("https://mit.edu/wtp",
                                 "Massachusetts Institute of Technology") is True


def test_domain_matches_org_two_letter_initialism_boston():
    # bu.edu <- Boston University, via the exact 2-char initial form.
    assert uv.domain_matches_org("https://bu.edu/summer", "Boston University") is True


def test_domain_matches_org_two_letter_initialism_william_and_mary():
    # wm.edu <- College of William & Mary (generic words dropped).
    assert uv.domain_matches_org("https://wm.edu/scholars", "College of William & Mary") is True


def test_domain_matches_org_substring_against_label():
    assert uv.domain_matches_org("https://tellurideassociation.org/tasp",
                                 "Telluride Association") is True


def test_domain_matches_org_third_party_seo_listicle_flagged():
    # The whole point: an off-site round-up returns False.
    assert uv.domain_matches_org("https://ladderinternships.com/blog/19-internships",
                                 "Stanford AIMI") is False


def test_domain_matches_org_generous_default_no_host():
    # Nothing to compare against -> generous True (silence beats a bogus flag).
    assert uv.domain_matches_org("", "Any Org") is True


def test_domain_matches_org_generous_default_nothing_to_compare():
    # url has a host but org/name give no tokens/acronyms -> True.
    assert uv.domain_matches_org("https://x.com/p", "") is True


def test_domain_matches_org_all_labels_are_tld_returns_true():
    # host whose only label is a registry suffix -> no labels to compare -> generous True.
    assert uv.domain_matches_org("https://com/", "Some Org Name") is True


def test_domain_matches_org_label_is_abbreviation_of_org_word():
    # colum.edu (Columbia College Chicago): label 'colum' is a prefix-substring of 'columbia'.
    assert uv.domain_matches_org("https://colum.edu/x", "Columbia College Chicago") is True


def test_domain_matches_org_u_prefix_university_shape():
    # upenn.edu <- University of Pennsylvania (the "u" + short-form shape).
    assert uv.domain_matches_org("https://upenn.edu/x", "University of Pennsylvania") is True


# ------------------------------------------------------------------- support_urls_by_span
def _resolved(*pairs):
    return [{"index": i, "url": u} for i, u in pairs]


def test_support_urls_by_span_maps_spans_to_urls():
    grounding = {
        "groundingSupports": [
            {"segment": {"startIndex": 0, "endIndex": 10, "text": "Program A"},
             "groundingChunkIndices": [0, 1]},
            {"segment": {"startIndex": 11, "endIndex": 20, "text": "Program B"},
             "groundingChunkIndices": [2]},
        ]
    }
    resolved = _resolved((0, "https://a.com"), (1, "https://a2.com"), (2, "https://b.com"))
    spans = uv.support_urls_by_span(grounding, resolved)
    assert len(spans) == 2
    assert spans[0]["text"] == "Program A"
    assert spans[0]["urls"] == ["https://a.com", "https://a2.com"]
    assert spans[1]["urls"] == ["https://b.com"]


def test_support_urls_by_span_skips_spans_with_no_resolved_url():
    grounding = {
        "groundingSupports": [
            {"segment": {"text": "x"}, "groundingChunkIndices": [9]},  # index 9 unresolved
        ]
    }
    resolved = _resolved((0, "https://a.com"))
    assert uv.support_urls_by_span(grounding, resolved) == []


def test_support_urls_by_span_dedupes_within_span():
    grounding = {
        "groundingSupports": [
            {"segment": {"text": "x"}, "groundingChunkIndices": [0, 1]},
        ]
    }
    resolved = _resolved((0, "https://same.com"), (1, "https://same.com"))
    spans = uv.support_urls_by_span(grounding, resolved)
    assert spans[0]["urls"] == ["https://same.com"]


def test_support_urls_by_span_empty():
    assert uv.support_urls_by_span({}, []) == []
    assert uv.support_urls_by_span(None, None) == []


# ------------------------------------------------------------------- check_url (mocked)
class _FakeResp:
    def __init__(self, status, final_url):
        self.status = status
        self._final = final_url

    def geturl(self):
        return self._final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def patch_urlopen(monkeypatch):
    def _install(handler):
        monkeypatch.setattr(uv.urllib.request, "urlopen", handler)
    return _install


def test_check_url_malformed_is_dead(patch_urlopen):
    # non-http scheme is refused before any network call
    res = uv.check_url("ftp://x.com/a")
    assert res["status"] == uv.DEAD
    assert res["code"] == "malformed"


def test_check_url_live_200(patch_urlopen):
    patch_urlopen(lambda req, timeout=None: _FakeResp(200, "https://x.com/final"))
    res = uv.check_url("https://x.com/a")
    assert res["status"] == uv.LIVE
    assert res["code"] == 200
    assert res["final_url"] == "https://x.com/final"


def test_check_url_200ish_400_is_unverified(patch_urlopen):
    # A resp.status >= 400 that still came back as a normal response -> UNVERIFIED.
    patch_urlopen(lambda req, timeout=None: _FakeResp(418, "https://x.com/a"))
    res = uv.check_url("https://x.com/a")
    assert res["status"] == uv.UNVERIFIED


def _raise(exc):
    def _h(req, timeout=None):
        raise exc
    return _h


def test_check_url_404_is_dead(patch_urlopen):
    patch_urlopen(_raise(urllib.error.HTTPError("https://x.com/a", 404, "NF", {}, None)))
    res = uv.check_url("https://x.com/a")
    assert res["status"] == uv.DEAD
    assert res["code"] == 404


def test_check_url_410_is_dead(patch_urlopen):
    patch_urlopen(_raise(urllib.error.HTTPError("https://x.com/a", 410, "Gone", {}, None)))
    assert uv.check_url("https://x.com/a")["status"] == uv.DEAD


def test_check_url_403_is_unverified(patch_urlopen):
    patch_urlopen(_raise(urllib.error.HTTPError("https://x.com/a", 403, "Forbidden", {}, None)))
    res = uv.check_url("https://x.com/a")
    assert res["status"] == uv.UNVERIFIED
    assert res["code"] == 403


def test_check_url_429_is_unverified(patch_urlopen):
    patch_urlopen(_raise(urllib.error.HTTPError("https://x.com/a", 429, "Too Many", {}, None)))
    assert uv.check_url("https://x.com/a")["status"] == uv.UNVERIFIED


def test_check_url_dns_failure_is_dead(patch_urlopen):
    patch_urlopen(_raise(urllib.error.URLError(socket.gaierror("NXDOMAIN"))))
    res = uv.check_url("https://nope.invalid/a")
    assert res["status"] == uv.DEAD
    assert res["code"] == uv.DNS_FAILURE


def test_check_url_timeout_is_unverified(patch_urlopen):
    patch_urlopen(_raise(urllib.error.URLError(socket.timeout("timed out"))))
    res = uv.check_url("https://slow.com/a")
    assert res["status"] == uv.UNVERIFIED
    # code is the exception class name for a non-HTTP failure
    assert res["code"] == "URLError"
