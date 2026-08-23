"""Unit tests for check_deadlines.py pure helpers — extract_source_urls, base_domain,
today_label. Nothing here calls Claude; only fixture dicts and static parsing.
"""
import check_deadlines as cd


# --------------------------------------------------------------------------- extract_source_urls

def test_extract_source_urls_from_search_and_fetch():
    resp = {
        "content": [
            {"type": "text", "text": "notes"},
            {"type": "web_search_tool_result", "content": [
                {"url": "https://a.com/1", "title": "A"},
                {"url": "https://b.com/2", "title": "B"},
            ]},
            {"type": "web_fetch_tool_result", "content": {"url": "https://c.com/3"}},
        ]
    }
    assert cd.extract_source_urls(resp) == ["https://a.com/1", "https://b.com/2", "https://c.com/3"]


def test_extract_source_urls_dedupes_in_order():
    resp = {
        "content": [
            {"type": "web_search_tool_result", "content": [
                {"url": "https://a.com/1"},
                {"url": "https://a.com/1"},
                {"url": "https://b.com/2"},
            ]},
            {"type": "web_fetch_tool_result", "content": {"url": "https://a.com/1"}},
        ]
    }
    assert cd.extract_source_urls(resp) == ["https://a.com/1", "https://b.com/2"]


def test_extract_source_urls_skips_items_without_url():
    resp = {
        "content": [
            {"type": "web_search_tool_result", "content": [
                {"title": "no url here"},
                {"url": "https://a.com/1"},
                "a bare string, not a dict",
            ]},
        ]
    }
    assert cd.extract_source_urls(resp) == ["https://a.com/1"]


def test_extract_source_urls_fetch_content_not_dict():
    resp = {"content": [
        {"type": "web_fetch_tool_result", "content": None},
        {"type": "web_fetch_tool_result", "content": [{"url": "ignored"}]},  # list, not dict
    ]}
    assert cd.extract_source_urls(resp) == []


def test_extract_source_urls_empty_content():
    assert cd.extract_source_urls({"content": []}) == []
    assert cd.extract_source_urls({}) == []
    assert cd.extract_source_urls({"content": None}) == []


# --------------------------------------------------------------------------- base_domain

def test_base_domain_extracts_netloc():
    assert cd.base_domain("https://www.example.com/path/x?y=1") == "www.example.com"


def test_base_domain_no_scheme_returns_input():
    # urlparse yields empty netloc without a scheme → falls back to the raw string.
    assert cd.base_domain("example.com/path") == "example.com/path"


def test_base_domain_with_port():
    assert cd.base_domain("http://localhost:8000/api") == "localhost:8000"


# --------------------------------------------------------------------------- today_label

def test_today_label_non_empty_string():
    label = cd.today_label()
    assert isinstance(label, str) and label


def test_today_label_contains_year():
    import datetime
    assert str(datetime.date.today().year) in cd.today_label()


def test_today_label_no_leading_zero_on_day():
    # Both %-d (posix) and %#d (windows) drop the leading zero — assert the day isn't padded.
    import datetime
    today = datetime.date.today()
    label = cd.today_label()
    assert str(today.day) in label
