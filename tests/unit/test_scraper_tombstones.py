"""Tombstones: operator-deleted rows rejoin the scraper's dedupe pool.

Deleting a row from the opportunities table removes it from url_dedupe's pool, so the
next scrape run that finds the same URL re-inserts what an operator deliberately killed.
scraper_tombstones.json puts those rows back as tomb-* pool entries. These tests pin the
loader's degradation modes and the matching semantics: same URL + similar name is blocked,
a NEW url for the same program is not — a tombstone must never block re-finding a real
program at its current page.
"""
import json

import pytest

import scrape_opportunities as so
import url_dedupe


def _write(tmp_path, payload):
    path = tmp_path / "tombstones.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------ load_tombstones
def test_load_missing_file_is_empty(tmp_path):
    assert so.load_tombstones(str(tmp_path / "nope.json")) == []


def test_load_unreadable_file_degrades_to_empty(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert so.load_tombstones(str(path)) == []
    assert "unreadable" in capsys.readouterr().out


def test_load_keeps_only_entries_with_name_and_url(tmp_path):
    path = _write(tmp_path, {"entries": [
        {"id": "tomb-0001", "name": "Conrad Challenge", "url": "https://conradchallenge.org"},
        {"id": "tomb-0002", "name": "No URL"},                 # dropped
        {"id": "tomb-0003", "url": "https://x.org"},           # dropped: no name
        "not a dict",                                          # dropped
    ]})
    rows = so.load_tombstones(path)
    assert [r["id"] for r in rows] == ["tomb-0001"]
    # Pool shape only — extra metadata (orig_id, note) is deliberately not carried.
    assert set(rows[0]) == {"id", "name", "url"}


def test_real_tombstone_file_loads():
    """The checked-in file parses and every entry has the pool shape."""
    rows = so.load_tombstones()
    assert rows, "scraper_tombstones.json should ship with entries"
    for r in rows:
        assert r["id"].startswith("tomb-")
        assert r["name"] and r["url"]


# ------------------------------------------------------------------ matching semantics
TOMB = {"id": "tomb-0001", "name": "Conrad Challenge",
        "url": "https://www.conradchallenge.org/"}


def test_same_url_similar_name_is_blocked():
    exact, _ = url_dedupe.find_duplicates(
        "https://conradchallenge.org", "Conrad Challenge 2027", [TOMB])
    assert exact is TOMB
    assert so.is_tombstone(exact)


def test_new_url_for_same_program_is_not_blocked():
    # The program re-found at its CURRENT page must insert — resurrection depends on it.
    exact, _ = url_dedupe.find_duplicates(
        "https://conrad.spacecenter.org/", "Conrad Challenge", [TOMB])
    assert exact is None


def test_same_url_different_program_is_not_blocked():
    # Shared-portal rule survives tombstoning: same URL + unrelated name only hints.
    exact, cands = url_dedupe.find_duplicates(
        "https://conradchallenge.org", "Totally Unrelated Fellowship", [TOMB])
    assert exact is None
    assert any(c["confidence"] == "strong" for c in cands)


def test_is_tombstone():
    assert so.is_tombstone({"id": "tomb-0042"})
    assert not so.is_tombstone({"id": "ec18220"})
    assert not so.is_tombstone(None)


def test_mint_ids_ignore_tombstones():
    gen = so.next_id_generator({"ec18220", "tomb-9999"})
    assert next(gen) == "ec18221"
