"""Phase 4F feed-forward: classification, capture, and the JSONL work-list. Pure, hermetic.

The hub probe is injected, so nothing here fetches anything; the lead file is written under
tmp_path, so nothing here touches the real queue.
"""
import json

import pytest

import discovered_leads as dl
import url_validate


# ---------- what can never be a lead ----------

@pytest.mark.parametrize("url", [
    "https://twitter.com/someprogram", "https://www.facebook.com/x", "https://amazon.com/dp/1",
    "https://x.edu/blog/a-program", "https://x.edu/news/a-program",
    "", None, "ftp://x.edu/a", "javascript:void(0)",
])
def test_is_ignorable_true(url):
    assert dl.is_ignorable(url) is True


@pytest.mark.parametrize("url", [
    "https://x.edu/programs", "https://www.collegetransitions.com/dataverse/x",
    "https://precollege.wisc.edu/",
])
def test_is_ignorable_false(url):
    assert dl.is_ignorable(url) is False


def test_classify_pure_calls_a_mill_a_name_harvest_lead():
    """The largest bucket costs no fetch at all — is_content_mill is a host/path test."""
    assert dl.classify_pure("https://www.aralia.com/helpful-information/summer-programs/") == dl.KIND_NAMES


def test_classify_pure_returns_none_for_an_ordinary_page():
    """An ordinary page might still be a hub, but only a fetch can say so."""
    assert dl.classify_pure("https://x.edu/programs") is None


def test_classify_pure_never_promotes_an_ignorable_url():
    assert dl.classify_pure("https://twitter.com/x") is None


# ---------- capture ----------

def _probe(counts):
    """A stand-in for hub_link_count: {url: n}, defaulting to 0 (unfetchable)."""
    return lambda url, timeout=None: counts.get(url, 0)


def test_capture_splits_mills_from_hubs():
    resolved = ["https://www.aralia.com/info/list", "https://x.edu/programs", "https://y.edu/one"]
    leads, trace = dl.capture(
        resolved, used_urls=[], probe_budget=4,
        probe=_probe({"https://x.edu/programs": 12, "https://y.edu/one": 1}))
    kinds = {l["url"]: l["kind"] for l in leads}
    assert kinds == {"https://www.aralia.com/info/list": dl.KIND_NAMES,
                     "https://x.edu/programs": dl.KIND_HUB}
    assert trace["names"] == 1 and trace["hub"] == 1 and trace["probed"] == 2


def test_capture_skips_a_page_that_became_a_row():
    """A page the run already turned into an opportunity is a result, not a lead."""
    leads, trace = dl.capture(["https://www.aralia.com/info/list"],
                              used_urls=["https://www.aralia.com/info/list"], probe=_probe({}))
    assert leads == [] and trace["already_used"] == 1


def test_capture_skips_a_page_already_in_the_catalog():
    leads, trace = dl.capture(
        ["https://www.aralia.com/info/list"], used_urls=[],
        existing_rows=[{"id": "ec1", "url": "https://www.aralia.com/info/list"}], probe=_probe({}))
    assert leads == [] and trace["already_known"] == 1


def test_capture_skips_a_lead_already_on_file():
    known = {dl._key("https://www.aralia.com/info/list")}
    leads, _ = dl.capture(["https://www.aralia.com/info/list"], used_urls=[],
                          known_keys=known, probe=_probe({}))
    assert leads == []


def test_capture_matches_used_urls_by_normalized_key():
    """Trailing slash / case must not make a used page look unused."""
    leads, trace = dl.capture(["https://www.aralia.com/Info/List/"],
                              used_urls=["https://www.aralia.com/Info/List"], probe=_probe({}))
    assert leads == [] and trace["already_used"] == 1


def test_capture_dedupes_within_one_seed():
    leads, _ = dl.capture(["https://www.aralia.com/info/list", "https://www.aralia.com/info/list/"],
                          used_urls=[], probe=_probe({}))
    assert len(leads) == 1


def test_capture_requires_the_minimum_link_count():
    just_under = dl.MIN_HUB_LINKS - 1
    leads, _ = dl.capture(["https://x.edu/p"], used_urls=[], probe_budget=4,
                          probe=_probe({"https://x.edu/p": just_under}))
    assert leads == []
    leads, _ = dl.capture(["https://x.edu/p"], used_urls=[], probe_budget=4,
                          probe=_probe({"https://x.edu/p": dl.MIN_HUB_LINKS}))
    assert len(leads) == 1 and leads[0]["kind"] == dl.KIND_HUB


def test_capture_honours_the_probe_budget_and_still_captures_mills():
    """The budget bounds FETCHES. Mills need none, so they must survive an exhausted budget —
    that asymmetry is the whole reason the pure check runs first."""
    urls = [f"https://x{i}.edu/p" for i in range(10)] + ["https://www.aralia.com/info/list"]
    calls = []

    def probe(url, timeout=None):
        calls.append(url)
        return 0
    leads, trace = dl.capture(urls, used_urls=[], probe=probe, probe_budget=3)
    assert len(calls) == 3 and trace["probed"] == 3
    assert [l["kind"] for l in leads] == [dl.KIND_NAMES]


def test_capture_records_attribution_and_a_signal():
    leads, _ = dl.capture(["https://x.edu/p"], used_urls=[], seed_id=42, angle="Marine Science",
                          probe_budget=4, probe=_probe({"https://x.edu/p": 9}))
    lead = leads[0]
    assert lead["seed_id"] == 42 and lead["angle"] == "Marine Science"
    assert "9" in lead["signal"] and lead["status"] == dl.STATUS_NEW
    assert lead["first_seen"]


def test_capture_tolerates_empty_input():
    assert dl.capture([], used_urls=[])[0] == []
    assert dl.capture(None, used_urls=None)[0] == []


def test_hub_link_count_is_zero_when_the_page_cannot_be_fetched(monkeypatch):
    """Unreachable is not evidence of a hub — and must not raise inside a paid scrape."""
    import mine_hub_pages
    monkeypatch.setattr(mine_hub_pages, "fetch_html", lambda u, t=None: "")
    assert dl.hub_link_count("https://x.edu/p") == 0


def test_hub_link_count_swallows_a_fetch_exception(monkeypatch):
    import mine_hub_pages

    def boom(u, t=None):
        raise RuntimeError("network on fire")
    monkeypatch.setattr(mine_hub_pages, "fetch_html", boom)
    assert dl.hub_link_count("https://x.edu/p") == 0


# ---------- the JSONL work-list ----------

@pytest.fixture
def leadfile(tmp_path):
    return str(tmp_path / "leads.jsonl")


def _lead(url, kind=dl.KIND_HUB):
    return {"url": url, "kind": kind, "seed_id": 1, "angle": "a", "signal": "s",
            "first_seen": "2026-08-27", "status": dl.STATUS_NEW}


def test_append_and_load_round_trip(leadfile):
    assert dl.append_leads([_lead("https://a.edu/p"), _lead("https://b.edu/p")], leadfile) == 2
    assert [l["url"] for l in dl.load_leads(leadfile)] == ["https://a.edu/p", "https://b.edu/p"]


def test_append_is_idempotent_on_url(leadfile):
    dl.append_leads([_lead("https://a.edu/p")], leadfile)
    assert dl.append_leads([_lead("https://a.edu/p/")], leadfile) == 0
    assert len(dl.load_leads(leadfile)) == 1


def test_load_leads_on_a_missing_file_is_empty_not_an_error(leadfile):
    assert dl.load_leads(leadfile) == []


def test_load_leads_skips_a_malformed_line(leadfile):
    with open(leadfile, "w", encoding="utf-8") as f:
        f.write(json.dumps(_lead("https://a.edu/p")) + "\n")
        f.write("{not json\n")
        f.write("\n")
        f.write(json.dumps({"kind": "hub"}) + "\n")          # no url
        f.write(json.dumps(_lead("https://b.edu/p")) + "\n")
    assert [l["url"] for l in dl.load_leads(leadfile)] == ["https://a.edu/p", "https://b.edu/p"]


def test_pending_filters_by_kind_and_status(leadfile):
    dl.append_leads([_lead("https://a.edu/p", dl.KIND_HUB),
                     _lead("https://b.edu/p", dl.KIND_NAMES),
                     _lead("https://c.edu/p", dl.KIND_HUB)], leadfile)
    dl.mark_processed(["https://a.edu/p"], leadfile)
    assert [l["url"] for l in dl.pending(dl.KIND_HUB, leadfile)] == ["https://c.edu/p"]
    assert [l["url"] for l in dl.pending(dl.KIND_NAMES, leadfile)] == ["https://b.edu/p"]


def test_pending_honours_a_limit(leadfile):
    dl.append_leads([_lead(f"https://{c}.edu/p") for c in "abcd"], leadfile)
    assert len(dl.pending(dl.KIND_HUB, leadfile, limit=2)) == 2


def test_mark_processed_stamps_and_is_idempotent(leadfile):
    dl.append_leads([_lead("https://a.edu/p")], leadfile)
    assert dl.mark_processed(["https://a.edu/p"], leadfile) == 1
    assert dl.mark_processed(["https://a.edu/p"], leadfile) == 0
    lead = dl.load_leads(leadfile)[0]
    assert lead["status"] == dl.STATUS_DONE and lead["processed_at"]


def test_mark_processed_rewrites_rather_than_growing_the_file(leadfile):
    dl.append_leads([_lead("https://a.edu/p"), _lead("https://b.edu/p")], leadfile)
    dl.mark_processed(["https://a.edu/p"], leadfile)
    assert len(dl.load_leads(leadfile)) == 2


def test_summarize_counts_only_unprocessed(leadfile):
    dl.append_leads([_lead("https://a.edu/p", dl.KIND_HUB),
                     _lead("https://b.edu/p", dl.KIND_NAMES),
                     _lead("https://c.edu/p", dl.KIND_NAMES)], leadfile)
    dl.mark_processed(["https://b.edu/p"], leadfile)
    assert dl.summarize(dl.load_leads(leadfile)) == {dl.KIND_HUB: 1, dl.KIND_NAMES: 1}


def test_captured_leads_flow_straight_into_the_queue(leadfile):
    """End to end, with no network: capture -> append -> pending -> mark processed."""
    leads, _ = dl.capture(
        ["https://www.aralia.com/info/list", "https://x.edu/programs"], used_urls=[],
        seed_id=7, probe_budget=4, probe=_probe({"https://x.edu/programs": 11}))
    assert dl.append_leads(leads, leadfile) == 2
    hub = dl.pending(dl.KIND_HUB, leadfile)
    names = dl.pending(dl.KIND_NAMES, leadfile)
    assert [l["url"] for l in hub] == ["https://x.edu/programs"]
    assert [l["url"] for l in names] == ["https://www.aralia.com/info/list"]
    dl.mark_processed([l["url"] for l in hub], leadfile)
    assert dl.pending(dl.KIND_HUB, leadfile) == []
    assert len(dl.pending(dl.KIND_NAMES, leadfile)) == 1


# ---------- not every content mill is a listicle ----------

@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc", "https://youtu.be/abc",
    "https://www.reddit.com/r/x/comments/1/y",
])
def test_a_mill_that_names_nothing_is_not_a_lead(url):
    """Measured on the archived logs: YouTube returns 24,000 chars of ytcfg JS (billable junk)
    and Reddit returns 0 (it refuses our client). Both are content mills; neither is feedstock."""
    assert url_validate.is_content_mill(url) is True
    assert dl.is_ignorable(url) is True
    assert dl.classify_pure(url) is None


@pytest.mark.parametrize("url", [
    "https://www.lumiere-education.com/post/15-pre-college-fashion-programs",
    "https://www.immerse.education/knowledge-base/15-summer-art-programs",
    "https://www.aralia.com/helpful-information/visual-art-competitions/",
    "https://en.wikipedia.org/wiki/YoungArts",
])
def test_a_listicle_mill_is_a_name_harvest_lead(url):
    """Wikipedia stays in: 7,713 chars of real prose, and a list article genuinely names
    programs. The three free gates in harvest_names are what judge the names themselves."""
    assert dl.classify_pure(url) == dl.KIND_NAMES


def test_hub_capture_is_off_by_default():
    """Measured twice: free link-counting cannot tell an index from a page with a big nav
    (raw count good 11-94 vs bad 7-53; nav-subtracted good 0-57 vs bad 0-35). A hub lead feeds
    a PAID extraction, so nothing is queued until a discriminator exists that actually works."""
    assert dl.HUB_PROBE_PER_SEED == 0
    calls = []
    leads, trace = dl.capture(
        ["https://x.edu/programs", "https://www.aralia.com/info/list"], used_urls=[],
        probe=lambda u, t=None: calls.append(u) or 99)
    assert calls == [], "no page may be probed at the default budget"
    assert [l["kind"] for l in leads] == [dl.KIND_NAMES]


def test_hub_capture_still_works_when_a_budget_is_passed():
    """The machinery is kept and tested so an experiment costs a keyword argument."""
    leads, _ = dl.capture(["https://x.edu/programs"], used_urls=[], probe_budget=4,
                          probe=lambda u, t=None: 20)
    assert [l["kind"] for l in leads] == [dl.KIND_HUB]
