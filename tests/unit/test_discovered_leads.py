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


def test_routing_is_structural_with_no_host_shortcut():
    """The routing decision is 'does this page LINK its programs or NAME them', full stop. A
    host list used to shortcut the look, and on the real rejected pile that routed a single
    lithub article and one scholarships360 scholarship page as round-ups on domain alone."""
    assert not hasattr(dl, "classify_pure")





# ---------- capture ----------

def _probe(verdicts):
    """A stand-in for classify_page: {url: kind}, defaulting to no verdict."""
    return lambda url, timeout=None: ((verdicts.get(url), "stub") if verdicts.get(url)
                                      else (None, "stub: no verdict"))


def test_capture_splits_round_ups_from_hubs():
    resolved = ["https://www.aralia.com/info/list", "https://x.edu/programs", "https://y.edu/one"]
    leads, trace = dl.capture(resolved, used_urls=[], classify=_probe({
        "https://www.aralia.com/info/list": dl.KIND_NAMES,
        "https://x.edu/programs": dl.KIND_HUB}))
    kinds = {l["url"]: l["kind"] for l in leads}
    assert kinds == {"https://www.aralia.com/info/list": dl.KIND_NAMES,
                     "https://x.edu/programs": dl.KIND_HUB}
    assert trace["names"] == 1 and trace["hub"] == 1 and trace["probed"] == 3


def test_capture_skips_a_page_that_became_a_row():
    """A page the run already turned into an opportunity is a result, not a lead."""
    leads, trace = dl.capture(["https://www.aralia.com/info/list"],
                              used_urls=["https://www.aralia.com/info/list"], classify=_probe({}))
    assert leads == [] and trace["already_used"] == 1


def test_capture_skips_a_page_already_in_the_catalog():
    leads, trace = dl.capture(
        ["https://www.aralia.com/info/list"], used_urls=[],
        existing_rows=[{"id": "ec1", "url": "https://www.aralia.com/info/list"}], classify=_probe({}))
    assert leads == [] and trace["already_known"] == 1


def test_capture_skips_a_lead_already_on_file():
    known = {dl._key("https://www.aralia.com/info/list")}
    leads, _ = dl.capture(["https://www.aralia.com/info/list"], used_urls=[],
                          known_keys=known, classify=_probe({}))
    assert leads == []


def test_capture_matches_used_urls_by_normalized_key():
    """Trailing slash / case must not make a used page look unused."""
    leads, trace = dl.capture(["https://www.aralia.com/Info/List/"],
                              used_urls=["https://www.aralia.com/Info/List"], classify=_probe({}))
    assert leads == [] and trace["already_used"] == 1


def test_capture_dedupes_within_one_seed():
    leads, _ = dl.capture(["https://www.aralia.com/info/list", "https://www.aralia.com/info/list/"],
                          used_urls=[],
                          classify=_probe({"https://www.aralia.com/info/list": dl.KIND_NAMES}))
    assert len(leads) == 1


def test_capture_honours_the_probe_budget():
    """The budget bounds free FETCHES, so lead capture stays a side-effect of a scrape rather
    than becoming its own crawl. Nothing bypasses it now that routing is structural."""
    urls = [f"https://x{i}.edu/p" for i in range(10)]
    calls = []

    def probe(url, timeout=None):
        calls.append(url)
        return None, "stub"
    leads, trace = dl.capture(urls, used_urls=[], classify=probe, probe_budget=3)
    assert len(calls) == 3 and trace["probed"] == 3 and leads == []


def test_capture_records_attribution_and_a_signal():
    leads, _ = dl.capture(["https://x.edu/p"], used_urls=[], seed_id=42, angle="Marine Science",
                          classify=lambda u, t=None: (dl.KIND_HUB, "links programs on 9 sites"))
    lead = leads[0]
    assert lead["seed_id"] == 42 and lead["angle"] == "Marine Science"
    assert "9" in lead["signal"] and lead["status"] == dl.STATUS_NEW
    assert lead["first_seen"]


def test_capture_tolerates_empty_input():
    assert dl.capture([], used_urls=[])[0] == []
    assert dl.capture(None, used_urls=None)[0] == []


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
        seed_id=7, classify=_probe({"https://www.aralia.com/info/list": dl.KIND_NAMES,
                                    "https://x.edu/programs": dl.KIND_HUB}))
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
    and Reddit returns 0 (it refuses our client). Both are content mills; neither is feedstock,
    so they are refused before any page is even fetched."""
    assert url_validate.is_content_mill(url) is True
    assert dl.is_ignorable(url) is True





def test_hub_capture_is_on_and_routes_to_the_hub_extractor():
    """Hub capture is ON: the structural classifier (distinct off-domain domains) separates a
    round-up from a page with a big nav, which raw link counting could not."""
    leads, _ = dl.capture(["https://x.edu/programs"], used_urls=[], classify=lambda u, t=None: (dl.KIND_HUB, "stub"))
    assert [l["kind"] for l in leads] == [dl.KIND_HUB]


# ---------- the structural classifier: links vs names ----------

def test_many_programs_title_test():
    """The signal that separates a round-up from one program's own FAQ. Measured on 9 real
    pages: kept 4 of 4 round-ups, rejected 4 of 4 non-lists."""
    assert dl._MANY_RE.search("15 Summer Art Programs for High School Students")
    assert dl._MANY_RE.search("22 Visual Art Competitions for High School Students")
    assert not dl._MANY_RE.search("FAQ | Wake Forest Summer Immersion Program")
    assert not dl._MANY_RE.search("Cornell Tech - Summer Innovation Intensives")
    # a single-program encyclopedia article is correctly NOT a lead; a LIST article is
    assert not dl._MANY_RE.search("YoungArts - Wikipedia")
    assert dl._MANY_RE.search("List of physics competitions - Wikipedia")





def test_capture_routes_both_kinds_from_one_seed():
    verdicts = {"https://roundup.example/a": (dl.KIND_HUB, "links programs on 20 distinct sites"),
                "https://prose.example/b": (dl.KIND_NAMES, "names many programs"),
                "https://faq.example/c": (None, "title does not promise many programs")}
    leads, trace = dl.capture(list(verdicts), used_urls=[],
                              classify=lambda u, t=None: verdicts[u])
    assert {l["url"]: l["kind"] for l in leads} == {
        "https://roundup.example/a": dl.KIND_HUB, "https://prose.example/b": dl.KIND_NAMES}
    assert trace["no_verdict"] == 1 and trace[dl.KIND_HUB] == 1 and trace[dl.KIND_NAMES] == 1


# ---------- second source: the review queue's rejected pile ----------

REJECTED = [
    {"id": "ec1", "name": "Top 15 Summer Programs", "url": "https://roundup.example/a"},
    {"id": "ec2", "name": "A blog post", "url": "https://prose.example/b"},
    {"id": "ec3", "name": "Not about programs", "url": "https://faq.example/c"},
]
VERDICTS = {"https://roundup.example/a": (dl.KIND_HUB, "links programs on 12 distinct sites"),
            "https://prose.example/b": (dl.KIND_NAMES, "names many programs"),
            "https://faq.example/c": (None, "no verdict")}


def test_rejected_rows_become_leads_of_the_right_kind():
    """A row a human rejected as a third-party round-up is confirmed evidence, not a guess —
    but it still has to be classified, because 'not a program page' does not say whether it
    LINKS the programs or merely NAMES them."""
    leads, trace = dl.from_rejected_rows(REJECTED, classify=lambda u, t=None: VERDICTS[u])
    assert [l["kind"] for l in leads] == [dl.KIND_HUB, dl.KIND_NAMES]
    assert trace["no_verdict"] == 1 and trace["rejected"] == 3


def test_rejected_leads_carry_their_source_row():
    leads, _ = dl.from_rejected_rows(REJECTED, classify=lambda u, t=None: VERDICTS[u])
    assert "ec1" in leads[0]["angle"] and "Top 15 Summer Programs" in leads[0]["angle"]


def test_rejected_rows_skip_leads_already_queued():
    known = {dl._key("https://roundup.example/a")}
    leads, trace = dl.from_rejected_rows(REJECTED, known_keys=known,
                                         classify=lambda u, t=None: VERDICTS[u])
    assert [l["url"] for l in leads] == ["https://prose.example/b"]
    assert trace["already_known"] == 1


def test_rejected_rows_honour_a_limit():
    leads, _ = dl.from_rejected_rows(REJECTED, classify=lambda u, t=None: VERDICTS[u], limit=1)
    assert len(leads) == 1


def test_rejected_rows_tolerate_junk():
    leads, _ = dl.from_rejected_rows(
        [{"id": "x", "url": None}, {"id": "y", "url": "javascript:void(0)"}, {}],
        classify=lambda u, t=None: (dl.KIND_HUB, "s"))
    assert leads == []


# ---------- a site's own name in the title suffix lies about the page ----------

def test_strip_site_name_removes_a_short_trailing_suffix():
    assert dl.strip_site_name(
        "15 Summer Art Programs in New York City for High School Students | Immerse Education"
    ) == "15 Summer Art Programs in New York City for High School Students"
    assert dl.strip_site_name("Contact - National Youth Leadership Council") == "Contact"


def test_site_name_plural_no_longer_fakes_a_round_up():
    """Measured live: opportunitiesforyouth.org publishes SINGLE-program articles whose titles
    end '... - Opportunities for Youth'. The plural in the site name made the title test call a
    one-program article a round-up, and it was queued as a paid name-harvest lead."""
    title = ("NYLC Youth Advisory Council 2026-2028: A National Leadership Opportunity "
             "for High School Changemakers - Opportunities for Youth")
    assert dl._MANY_RE.search(title)                       # the raw title is fooled
    assert not dl._MANY_RE.search(dl.strip_site_name(title))   # the stripped one is not


def test_strip_site_name_leaves_a_long_tail_alone():
    """Only a SHORT tail looks like a site name; a real title containing a dash keeps itself."""
    t = "Summer Programs - everything a rising junior needs to know before applying this year"
    assert dl.strip_site_name(t) == t


def test_strip_site_name_never_empties_a_title():
    assert dl.strip_site_name("Programs | MIT") == "Programs"
    assert dl.strip_site_name("| Immerse") == "| Immerse"
    assert dl.strip_site_name("") == ""
