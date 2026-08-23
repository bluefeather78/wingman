"""Unit tests for agent_common.py — clean_email, snapshot_stamp (explicit `when`),
and emit_preview's PREVIEW_JSON contract. Pure; no network, no wall-clock reliance.
"""
import datetime
import json

import agent_common as ac


# --------------------------------------------------------------------------- clean_email

def test_clean_email_valid():
    assert ac.clean_email("a@b.com") == "a@b.com"


def test_clean_email_trims_whitespace():
    assert ac.clean_email("  a@b.com  ") == "a@b.com"


def test_clean_email_non_string_returns_none():
    assert ac.clean_email(None) is None
    assert ac.clean_email(123) is None
    assert ac.clean_email(["a@b.com"]) is None


def test_clean_email_rejects_prose():
    assert ac.clean_email("contact us via our website") is None


def test_clean_email_rejects_no_at():
    assert ac.clean_email("just.text") is None


def test_clean_email_rejects_short_tld():
    # EMAIL_RE requires 2+ chars after the final dot.
    assert ac.clean_email("a@b.c") is None


def test_clean_email_accepts_subdomain():
    assert ac.clean_email("x@mail.program.org") == "x@mail.program.org"


# --------------------------------------------------------------------------- snapshot_stamp

def test_snapshot_stamp_explicit_when_exact_string():
    when = datetime.datetime(2026, 8, 22, 14, 30, 5)
    assert ac.snapshot_stamp(when) == "20260822-143005"


def test_snapshot_stamp_pads_seconds():
    when = datetime.datetime(2026, 1, 2, 3, 4, 5)
    assert ac.snapshot_stamp(when) == "20260102-030405"


def test_snapshot_stamp_format_shape():
    stamp = ac.snapshot_stamp(datetime.datetime(2026, 12, 31, 23, 59, 59))
    assert stamp == "20261231-235959"
    assert len(stamp) == 15
    assert stamp[8] == "-"


# --------------------------------------------------------------------------- emit_preview

def test_emit_preview_returns_payload(capsys):
    payload = ac.emit_preview(5, "rows", sample=["A", "B"])
    assert payload["count"] == 5
    assert payload["unit"] == "rows"
    assert payload["sample"] == ["A", "B"]


def test_emit_preview_prints_contract_line(capsys):
    ac.emit_preview(3, "seeds", sample=["x"])
    out = capsys.readouterr().out
    # exactly one PREVIEW_JSON line, parseable.
    prefix_lines = [ln for ln in out.splitlines() if ln.startswith(ac.PREVIEW_PREFIX)]
    assert len(prefix_lines) == 1
    parsed = json.loads(prefix_lines[0][len(ac.PREVIEW_PREFIX):].strip())
    assert parsed["count"] == 3
    assert parsed["unit"] == "seeds"


def test_emit_preview_truncates_sample_to_limit(capsys):
    big = [f"n{i}" for i in range(ac.SAMPLE_LIMIT + 10)]
    payload = ac.emit_preview(len(big), "rows", sample=big)
    # payload's sample is capped at SAMPLE_LIMIT.
    assert len(payload["sample"]) == ac.SAMPLE_LIMIT
    out = capsys.readouterr().out
    assert "and 10 more" in out


def test_emit_preview_extra_fields_merged(capsys):
    payload = ac.emit_preview(1, "rows", sample=[], mode="national")
    assert payload["mode"] == "national"
    out = capsys.readouterr().out
    line = [ln for ln in out.splitlines() if ln.startswith(ac.PREVIEW_PREFIX)][0]
    assert json.loads(line[len(ac.PREVIEW_PREFIX):].strip())["mode"] == "national"


def test_emit_preview_no_sample(capsys):
    payload = ac.emit_preview(0, "rows")
    assert payload["sample"] == []
