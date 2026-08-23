"""Unit tests for the two extract_json() ports, tested SEPARATELY.

gemini_common.extract_json and claude_common.extract_json are duplicate ports of
script.js's extractJSON() (duplicated rather than shared so neither module depends on
the other). Every case runs against BOTH so the two ports cannot drift apart.
"""
import pytest

import claude_common
import gemini_common

# The two implementations under test. Every test parametrizes over both.
EXTRACTORS = [gemini_common.extract_json, claude_common.extract_json]
IDS = ["gemini", "claude"]


def both(func):
    return pytest.mark.parametrize("extract", EXTRACTORS, ids=IDS)(func)


# ---------------------------------------------------------------------------
# Clean, well-formed input.
# ---------------------------------------------------------------------------
@both
def test_clean_object(extract):
    assert extract('{"a": 1, "b": "two"}') == {"a": 1, "b": "two"}


@both
def test_clean_array(extract):
    assert extract('[1, 2, 3]') == [1, 2, 3]


@both
def test_nested_structure(extract):
    text = '{"items": [{"id": 1}, {"id": 2}], "ok": true}'
    assert extract(text) == {"items": [{"id": 1}, {"id": 2}], "ok": True}


# ---------------------------------------------------------------------------
# JSON embedded in surrounding commentary — the reason for the depth scan.
# ---------------------------------------------------------------------------
@both
def test_trailing_commentary_is_ignored(extract):
    text = 'Here is your result: {"a": 1} — hope that helps!'
    assert extract(text) == {"a": 1}


@both
def test_leading_and_trailing_prose(extract):
    text = 'Sure thing.\n\n[10, 20, 30]\n\nLet me know if you need more.'
    assert extract(text) == [10, 20, 30]


@both
def test_a_brace_inside_a_string_does_not_close_early(extract):
    """A '}' inside a string value must not be read as the end of the object."""
    text = '{"note": "closes } here", "n": 5}'
    assert extract(text) == {"note": "closes } here", "n": 5}


@both
def test_fenced_json_block(extract):
    """```json fences: extract_json finds the first brace and stops at its match, so the
    surrounding fence characters are simply outside the scanned span."""
    text = '```json\n{"a": 1, "b": 2}\n```'
    assert extract(text) == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# Best-effort repair of truncated / token-limited responses.
# ---------------------------------------------------------------------------
@both
def test_truncated_mid_string_closes_the_string(extract):
    text = '{"name": "hello wor'
    assert extract(text) == {"name": "hello wor"}


@both
def test_truncated_object_is_closed(extract):
    text = '{"a": 1, "b": 2'
    assert extract(text) == {"a": 1, "b": 2}


@both
def test_truncated_array_is_closed(extract):
    text = '[1, 2, 3'
    assert extract(text) == [1, 2, 3]


@both
def test_truncated_nested_closes_all_open_levels(extract):
    text = '{"outer": [1, {"inner": "x'
    assert extract(text) == {"outer": [1, {"inner": "x"}]}


@both
def test_dangling_comma_in_object_is_trimmed(extract):
    text = '{"a": 1,'
    assert extract(text) == {"a": 1}


@both
def test_dangling_comma_in_array_is_trimmed(extract):
    text = '[1, 2, '
    assert extract(text) == [1, 2]


@both
def test_permissive_retry_on_raw_control_characters(extract):
    """A literal newline inside a string value is invalid strict JSON; the extractor
    retries with strict=False rather than giving up."""
    text = '{"a": "line1\nline2"}'   # real newline char inside the string value
    assert extract(text) == {"a": "line1\nline2"}


# ---------------------------------------------------------------------------
# No JSON present — pinned to raise ValueError.
# ---------------------------------------------------------------------------
@both
def test_no_json_raises_valueerror(extract):
    with pytest.raises(ValueError, match="No JSON found in response"):
        extract("there is nothing structured here at all")


@both
def test_empty_string_raises_valueerror(extract):
    with pytest.raises(ValueError, match="No JSON found in response"):
        extract("")
