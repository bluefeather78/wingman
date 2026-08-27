"""The scraper grading harness: scoring semantics and fixture integrity.

The harness is what makes pipeline changes provable against the 2026-08-23 human
grading BEFORE the next paid run. These tests pin the scoring rules — a suppressed
approved row is a REGRESSION, a suppressed rejected/deleted row is a win, an ungraded
row is reported rather than silently passed — and assert the checked-in fixture still
carries the exact verdict distribution that was measured (115/44/7). Snapshots are
synthesized in tmp_path: the real ones are gitignored run artifacts.
"""
import json
import os

import pytest

import grade_scraper_batch as gb


def _row(rid, name="Row", flags=None, dups=None):
    return {"id": rid, "name": name, "url": f"https://example.org/{rid}",
            "review": {"quality_flags": flags or [], "dup_candidates": dups or []}}


# ----------------------------------------------------------------------- evaluate()
def test_evaluate_classifies_all_four_outcomes():
    rows = [
        _row("a", flags=["URL is on an unrelated site — may be an article"]),  # suppressed
        _row("b", flags=["URL is on an unrelated site — may be an article"]),  # suppressed
        _row("c"),                                                             # inserted
        _row("d"),                                                             # inserted
        _row("e"),                                                             # ungraded
    ]
    verdicts = {"a": {"verdict": "approved"}, "b": {"verdict": "deleted"},
                "c": {"verdict": "rejected"}, "d": {"verdict": "approved"}}
    result = gb.evaluate(rows, verdicts, gb.decide_flag_offsite)
    assert [r["id"] for r in result["regressions"]] == ["a"]   # approved but suppressed
    assert [w["id"] for w in result["wins"]] == ["b"]          # deleted and suppressed
    assert result["kept_negative"] == 1                        # c: rejected, still inserted
    assert result["ungraded"] == ["e"]
    assert result["graded"] == 4


def test_baseline_never_suppresses():
    rows = [_row("a", flags=["anything"]), _row("b")]
    verdicts = {"a": {"verdict": "rejected"}, "b": {"verdict": "approved"}}
    result = gb.evaluate(rows, verdicts, gb.decide_baseline)
    assert result["wins"] == [] and result["regressions"] == []
    assert result["kept_negative"] == 1


def test_strong_dup_probe_reads_candidates():
    rows = [_row("a", dups=[{"id": "ec1", "confidence": "strong", "reason": "same site"}]),
            _row("b", dups=[{"id": "ec2", "confidence": "weak", "reason": "name"}])]
    verdicts = {"a": {"verdict": "rejected"}, "b": {"verdict": "rejected"}}
    result = gb.evaluate(rows, verdicts, gb.decide_strong_dup)
    assert [w["id"] for w in result["wins"]] == ["a"]
    assert result["kept_negative"] == 1


# ------------------------------------------------------------ Phase-5 loop guarantees
def test_suppress_all_gate_fails_loudly():
    # A deliberately-broken policy that drops everything MUST register as regressions, or the
    # harness would rubber-stamp a bad change instead of catching it (Phase 5 criterion 2).
    rows = [_row("a"), _row("b")]
    verdicts = {"a": {"verdict": "approved"}, "b": {"verdict": "approved"}}
    result = gb.evaluate(rows, verdicts, gb.decide_suppress_all)
    assert len(result["regressions"]) == 2


def test_url_dup_uses_real_rule_and_scores_merge_non_harmful():
    # decide_url_dup calls the SAME classify_same_url the live scraper runs. A same-URL dup on a
    # dedicated page -> merge; an approved row merged is PRESERVED, never a regression.
    row = _row("a", dups=[{"id": "ec1", "reason": "identical URL but a different name"}])
    result = gb.evaluate([row], {"a": {"verdict": "approved"}}, gb.decide_url_dup)
    assert result["regressions"] == []
    assert [m["id"] for m in result["merges"]] == ["a"]


def test_url_dup_keeps_bare_domain_shared_homepage():
    row = {"id": "y", "name": "R", "url": "https://example.org",   # bare domain
           "review": {"quality_flags": [],
                      "dup_candidates": [{"id": "ec1", "reason": "identical URL but a different name"}]}}
    action, _ = gb.decide_url_dup(row)
    assert action == "insert"   # kept for review, not merged


# ------------------------------------------------------------------ snapshot loading
def test_load_snapshot_rows_dict_and_bare_list_shapes(tmp_path):
    (tmp_path / "new.json").write_text(json.dumps(
        {"inserted": [_row("x")], "rejected": []}), encoding="utf-8")
    (tmp_path / "old.json").write_text(json.dumps([_row("y")]), encoding="utf-8")
    fixture = {"snapshots": ["new.json", "old.json"]}
    rows = gb.load_snapshot_rows(fixture, str(tmp_path))
    assert [r["id"] for r in rows] == ["x", "y"]


# ------------------------------------------------------------------ fixture integrity
def test_checked_in_fixture_matches_the_measured_grading():
    fixture = gb.load_fixture(gb.DEFAULT_FIXTURE)
    verdicts = fixture["verdicts"]
    counts = {}
    for v in verdicts.values():
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
    # The frozen human grading of the 2026-08-23 batch. If this fails, the fixture was
    # edited — regenerate it from the DB/snapshots deliberately, never tweak by hand.
    assert counts == {"approved": 115, "rejected": 44, "deleted": 7}
    assert len(verdicts) == 166
    assert all(v["verdict"] in {"approved", "rejected", "deleted"}
               for v in verdicts.values())
