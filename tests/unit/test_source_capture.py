"""The substrate CAPTURE layer (P6b): parsing Claude web_fetch responses into verifiable,
tier-tagged sources. Structure + graceful degradation are unit-tested here; real PDF text
extraction was proven live in the P6a probe (THINK's guidelines PDF -> 32k chars)."""
import base64

import aggregators_common as ag
import source_capture as sc


def _html_block(url, text):
    return {"type": "web_fetch_tool_result",
            "content": {"type": "web_fetch_result", "url": url,
                        "content": {"type": "document", "title": "t",
                                    "source": {"type": "text", "media_type": "text/plain",
                                               "data": text}}}}


def _pdf_block(url, b64):
    return {"type": "web_fetch_tool_result",
            "content": {"type": "web_fetch_result", "url": url,
                        "content": {"type": "document",
                                    "source": {"type": "base64",
                                               "media_type": "application/pdf", "data": b64}}}}


def _search_block():
    return {"type": "web_search_tool_result",
            "content": [{"type": "web_search_result", "title": "x",
                         "url": "https://third.example", "encrypted_content": "OPAQUE"}]}


# --------------------------------------------------------------- parse_captured_sources

def test_html_fetch_text_is_captured_directly():
    data = {"content": [_html_block("https://prog.example/apply",
                                    "Apply by Jan 1. Submit a transcript.")]}
    srcs = sc.parse_captured_sources(data, own_domain="prog.example")
    assert len(srcs) == 1
    assert srcs[0].text == "Apply by Jan 1. Submit a transcript."
    assert srcs[0].domain == "prog.example"
    assert srcs[0].media_type == "text/plain"


def test_search_results_are_ignored_encrypted_and_unverifiable():
    data = {"content": [_search_block(),
                        _html_block("https://prog.example/x", "real text here")]}
    srcs = sc.parse_captured_sources(data, own_domain="prog.example")
    assert [s.url for s in srcs] == ["https://prog.example/x"]  # search block dropped


def test_fetch_error_block_is_skipped():
    err = {"type": "web_fetch_tool_result",
           "content": {"type": "web_fetch_tool_error", "error_code": "unavailable"}}
    data = {"content": [err, _html_block("https://prog.example/y", "text yz here")]}
    srcs = sc.parse_captured_sources(data, own_domain="prog.example")
    assert [s.url for s in srcs] == ["https://prog.example/y"]


def test_sources_deduped_by_url():
    data = {"content": [_html_block("https://prog.example/a", "one one one"),
                        _html_block("https://prog.example/a", "two two two")]}
    srcs = sc.parse_captured_sources(data, own_domain="prog.example")
    assert len(srcs) == 1  # first wins


def test_pdf_media_routes_to_extractor_and_degrades_on_garbage():
    # Not real PDF bytes -> extraction returns "" rather than raising; the source is still
    # captured (media recorded) so a reader can see it was a PDF we could not read.
    data = {"content": [_pdf_block("https://prog.example/g.pdf",
                                   base64.b64encode(b"not a pdf").decode())]}
    srcs = sc.parse_captured_sources(data, own_domain="prog.example")
    assert len(srcs) == 1 and srcs[0].media_type == "application/pdf"
    assert srcs[0].text == ""


def test_unknown_media_type_yields_no_text_never_crashes():
    block = {"type": "web_fetch_tool_result",
             "content": {"type": "web_fetch_result", "url": "https://prog.example/z",
                         "content": {"type": "document",
                                     "source": {"type": "base64", "media_type": "image/png",
                                                "data": "AAAA"}}}}
    srcs = sc.parse_captured_sources({"content": [block]}, own_domain="prog.example")
    assert srcs[0].text == ""


def test_pdf_text_from_base64_handles_garbage():
    assert sc.pdf_text_from_base64("!!!not-base64!!!") == ""
    assert sc.pdf_text_from_base64(base64.b64encode(b"still not a pdf").decode()) == ""


# --------------------------------------------------------------- tier_for

def test_tier_official_for_own_domain_and_subdomain():
    assert sc.tier_for("https://think.mit.edu/guide.pdf", "think.mit.edu", None) == "official"
    assert sc.tier_for("https://apply.think.mit.edu/x", "think.mit.edu", None) == "official"


def test_tier_uses_policy_for_third_party():
    policy = ag.AggregatorPolicy({"lumiere-education.com": "trusted", "bad.com": "blocked"})
    assert sc.tier_for("https://lumiere-education.com/think", "think.mit.edu", policy) == "trusted"
    assert sc.tier_for("https://bad.com/x", "think.mit.edu", policy) == "blocked"
    assert sc.tier_for("https://unknown.org/x", "think.mit.edu", policy) == "pending"


def test_tier_pending_without_policy():
    assert sc.tier_for("https://third.example/x", "prog.example", None) == "pending"


def test_parse_applies_tier_per_source():
    policy = ag.AggregatorPolicy({"lumiere-education.com": "trusted"})
    data = {"content": [
        _html_block("https://think.mit.edu/apply", "official text"),
        _html_block("https://lumiere-education.com/think", "guide text"),
        _html_block("https://random.org/blog", "blog text"),
    ]}
    srcs = sc.parse_captured_sources(data, own_domain="think.mit.edu", policy=policy)
    tiers = {s.domain: s.tier for s in srcs}
    assert tiers == {"think.mit.edu": "official",
                     "lumiere-education.com": "trusted",
                     "random.org": "pending"}
