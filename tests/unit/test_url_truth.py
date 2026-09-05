"""Phase-2 URL truth: content-mill detection, primary-link rescue, title proof, and the
scraper's resolve_url_truth orchestration. The only network seam (url_repair._fetch) is
monkeypatched; everything else runs for real.
"""
import pytest

from agents import scrape_opportunities as so
from wingman import url_dedupe
from wingman import url_repair
from wingman import url_validate as uv


# ---- is_content_mill ------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://www.reddit.com/r/x", "https://youtube.com/watch?v=1", "https://youtu.be/abc",
    "https://lumiere-education.com/blog/best", "https://blog.ladderinternships.com/x",
    "https://en.wikipedia.org/wiki/Thing", "https://nshss.org/programs",
    "https://immerse.education/knowledge-base/summer-programs/",
])
def test_is_content_mill_true(url):
    assert uv.is_content_mill(url) is True


@pytest.mark.parametrize("url", [
    "https://stanford.edu/program", "https://nyu.edu/summer",
    "https://immerse.education/summer-schools/cambridge/",  # same host, legit sub-path
    "", None, "not a url",
])
def test_is_content_mill_false(url):
    assert uv.is_content_mill(url) is False


def test_immerse_education_both_ways():
    # The criterion-3 both-ways check spelled out.
    assert uv.is_content_mill("https://immerse.education/knowledge-base/x/") is True
    assert uv.is_content_mill("https://immerse.education/summer-schools/x/") is False


# ---- is_low_value_path extensions -----------------------------------------------------

@pytest.mark.parametrize("url,low", [
    ("https://a.edu/prog/rules.pdf", True),
    ("https://a.edu/prog/guidelines.PDF", True),
    ("https://a.edu/prog/admissions", True),
    ("https://a.edu/prog/costs", True),
    ("https://a.edu/prog/rules", True),
    ("https://a.edu/clark/scholars", False),
    ("https://a.edu/summer-research-institute", False),
    # Ancillary tail even when the program name is prepended, or it sits on a parent section --
    # the three Columbia walk-up cases the leaf-only rule let through (2026-08-28).
    ("https://precollege.sps.columbia.edu/admissions/program-costs/college-edge-tuition-and-fees", True),
    ("https://precollege.sps.columbia.edu/columbia-experience/commuting-campus", True),
    ("https://x.edu/programs/nyc-residential-summer/residential-life", True),
    # ...but the canonical program pages themselves are NOT low value.
    ("https://precollege.sps.columbia.edu/programs/summer-programs/college-edge-summer", False),
    ("https://precollege.sps.columbia.edu/programs/summer-programs/nyc-residential-summer", False),
    # A real program under /Admissions/ must stay high value (exact-word rule is leaf-only).
    ("https://www.usna.edu/Admissions/Programs/STEM", False),
])
def test_is_low_value_path(url, low):
    assert url_dedupe.is_low_value_path(url) is low


# ---- title_proof_url ------------------------------------------------------------------

NAME, ORG = "Clark Scholars Program", "Example University"


def _patch_fetch(monkeypatch, pages):
    """pages: {url: html} ; a value of "__PDF__" -> non-HTML 200; a missing url -> not fetched."""
    def fake(url, timeout=20):
        if url not in pages:
            return None, None
        val = pages[url]
        if val is None:
            return None, None
        if val == "__PDF__":
            return None, url            # non-HTML 200 (a PDF): loaded, but not a page
        return val, url
    monkeypatch.setattr(url_repair, "_fetch", fake)


def test_title_proof_proven(monkeypatch):
    _patch_fetch(monkeypatch, {"u": "<title>Clark Scholars Program — Example University</title>"})
    assert url_repair.title_proof_url("u", NAME, ORG) == (True, "Clark Scholars Program — Example University")


def test_title_proof_fails_when_title_does_not_name_program(monkeypatch):
    _patch_fetch(monkeypatch, {"u": "<title>Some Other Program</title>"})
    verdict, _ = url_repair.title_proof_url("u", NAME, ORG)
    assert verdict is False


def test_title_proof_pdf_auto_fails(monkeypatch):
    _patch_fetch(monkeypatch, {"u": "__PDF__"})
    assert url_repair.title_proof_url("u", NAME, ORG)[0] is False


def test_title_proof_blocked_is_none(monkeypatch):
    _patch_fetch(monkeypatch, {})  # url not fetchable -> (None, None)
    assert url_repair.title_proof_url("u", NAME, ORG)[0] is None


def test_title_proof_unverifiable_name_is_none(monkeypatch):
    _patch_fetch(monkeypatch, {"u": "<title>whatever</title>"})
    # "MIT" alone has fewer than two identity words -> unverifiable, not a failure.
    assert url_repair.title_proof_url("u", "MIT", "MIT")[0] is None


# ---- extract_primary_link -------------------------------------------------------------

def test_extract_primary_link_finds_org_page(monkeypatch):
    page = ('<a href="https://clark.example.edu/scholars">Clark Scholars Program</a>'
            '<a href="https://ladderinternships.com/list">A listicle</a>'
            '<a href="https://example.edu/">home</a>')
    _patch_fetch(monkeypatch, {"https://clark.example.edu/scholars":
                               "<title>Clark Scholars Program</title>"})
    link, title = url_repair.extract_primary_link(page, NAME, ORG, base_url="https://blog.com/x")
    assert link == "https://clark.example.edu/scholars"


def test_extract_primary_link_rejects_mill_and_homepage(monkeypatch):
    # Only off-target links present -> nothing accepted.
    page = ('<a href="https://ladderinternships.com/apply">Apply here</a>'
            '<a href="https://example.edu/">home</a>')
    _patch_fetch(monkeypatch, {})
    assert url_repair.extract_primary_link(page, NAME, ORG, base_url="https://blog.com/x") == (None, None)


# ---- resolve_url_truth (the orchestration) --------------------------------------------

CAND = {"name": NAME, "org": ORG}
REAL = "https://clark.example.edu/scholars"


def test_resolve_rescues_content_mill_via_grounding_sibling(monkeypatch):
    _patch_fetch(monkeypatch, {REAL: "<title>Clark Scholars Program</title>"})
    url, flags = so.resolve_url_truth(CAND, "https://ladderinternships.com/x", [], [REAL], 5)
    assert url == REAL
    assert any(f.startswith("URL rescued") for f in flags)


def test_resolve_rescues_offsite_via_on_page_link(monkeypatch):
    offsite = "https://someblog.com/best-programs"
    page = f'<a href="{REAL}">Clark Scholars Program</a>'
    _patch_fetch(monkeypatch, {offsite: page, REAL: "<title>Clark Scholars Program</title>"})
    url, flags = so.resolve_url_truth(CAND, offsite, [], [], 5)
    assert url == REAL and any(f.startswith("URL rescued") for f in flags)


def test_resolve_keeps_proven_on_org_url_untouched(monkeypatch):
    _patch_fetch(monkeypatch, {REAL: "<title>Clark Scholars Program</title>"})
    url, flags = so.resolve_url_truth(CAND, REAL, [], [], 5)
    assert url == REAL and flags == []


def test_resolve_flags_unproven_title(monkeypatch):
    other = "https://example.edu/mystery"
    _patch_fetch(monkeypatch, {other: "<title>Mystery Page</title>"})
    url, flags = so.resolve_url_truth(CAND, other, [], [], 5)
    assert url == other
    assert any(f.startswith("page title does not clearly name") for f in flags)


def test_resolve_trades_low_value_page_for_landing(monkeypatch):
    apply_url = "https://example.edu/clark/apply"
    _patch_fetch(monkeypatch, {REAL: "<title>Clark Scholars Program</title>"})
    url, flags = so.resolve_url_truth(CAND, apply_url, [], [REAL], 5)
    assert url == REAL
