"""Unit tests for dryrun_common.py — snapshot commit (dependency-injected fakes, no DB),
filename date parsing, the path-traversal guard in resolve(), snapshot-shape loading,
pending counts, and URL normalization.

REPO_DIR is monkeypatched to a tmp_path in the disk-touching tests so nothing reads the
real repo root.
"""
import datetime
import json

import pytest

import dryrun_common as dc


# --------------------------------------------------------------------------- normalize_url

@pytest.mark.parametrize("raw,expected", [
    ("https://Ex.com/Path/", "https://ex.com/path"),
    ("  https://ex.com/x  ", "https://ex.com/x"),
    ("https://ex.com/x/", "https://ex.com/x"),
    ("", ""),
    (None, ""),
])
def test_normalize_url(raw, expected):
    assert dc.normalize_url(raw) == expected


# --------------------------------------------------------------------------- _run_date

def test_run_date_full_stamp():
    d = dc._run_date("scrape_review_national_20260822-143005.json")
    assert d == datetime.datetime(2026, 8, 22, 14, 30, 5, tzinfo=datetime.timezone.utc)


def test_run_date_date_only_is_midnight_utc():
    d = dc._run_date("scrape_review_national_20260822.json")
    assert d == datetime.datetime(2026, 8, 22, 0, 0, 0, tzinfo=datetime.timezone.utc)


def test_run_date_no_stamp_returns_none():
    assert dc._run_date("no_digits_here.json") is None


def test_run_date_invalid_date_returns_none():
    # 8 digits that aren't a valid date.
    assert dc._run_date("file_20261399.json") is None


# --------------------------------------------------------------------------- resolve (security)

def test_resolve_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    for bad in ("../evil.json", "sub/dir.json", "/abs/path.json", "..\\evil.json"):
        assert dc.resolve(bad) == (None, None)


def test_resolve_rejects_empty():
    assert dc.resolve("") == (None, None)
    assert dc.resolve(None) == (None, None)


def test_resolve_unknown_pattern(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    (tmp_path / "random_file.json").write_text("[]")
    assert dc.resolve("random_file.json") == (None, None)


def test_resolve_known_pattern_but_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    # matches the scraper glob but no file on disk.
    assert dc.resolve("scrape_review_national_20260101.json") == (None, None)


def test_resolve_matches_scraper(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    name = "scrape_review_national_20260822-120000.json"
    (tmp_path / name).write_text("[]")
    agent, path = dc.resolve(name)
    assert agent == "scraper"
    assert path.endswith(name)


def test_resolve_matches_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    name = "refresh_opportunities_dry_run_20260822-120000.json"
    (tmp_path / name).write_text("[]")
    agent, _ = dc.resolve(name)
    assert agent == "metadata"


# --------------------------------------------------------------------------- _load

def test_load_bare_list(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps([{"id": 1}, {"id": 2}]))
    assert dc._load(str(p)) == [{"id": 1}, {"id": 2}]


def test_load_inserted_rejected_shape(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"inserted": [{"url": "u"}], "rejected": [{"url": "r"}]}))
    assert dc._load(str(p)) == [{"url": "u"}]


def test_load_unknown_dict_returns_empty(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"something": "else"}))
    assert dc._load(str(p)) == []


def test_load_inserted_not_a_list_returns_empty(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"inserted": "oops"}))
    assert dc._load(str(p)) == []


# --------------------------------------------------------------------------- _pending_count

def test_pending_count_metadata_counts_changes():
    entries = [{"changes": {"name": "x"}}, {"changes": {}}, {"other": 1}]
    assert dc._pending_count("metadata", entries) == 1


def test_pending_count_contact_email_like_metadata():
    entries = [{"changes": {"contact_email": "a@b.com"}}, {"changes": None}]
    assert dc._pending_count("contact_email", entries) == 1


def test_pending_count_reviews_counts_status_change():
    entries = [
        {"review_status": "legit", "previous_review_status": "unknown"},
        {"review_status": "legit", "previous_review_status": "legit"},
    ]
    assert dc._pending_count("reviews", entries) == 1


def test_pending_count_deadline_counts_changed_flag():
    entries = [{"changed": True}, {"changed": False}, {}]
    assert dc._pending_count("deadline", entries) == 1


def test_pending_count_scraper_counts_url():
    entries = [{"url": "u1"}, {"url": ""}, {"nope": 1}]
    assert dc._pending_count("scraper", entries) == 1


# --------------------------------------------------------------------------- commit_snapshot (insert)

def _write(tmp_path, monkeypatch, name, data):
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    (tmp_path / name).write_text(json.dumps(data))
    return name


def test_commit_insert_dedupes_and_forces_inactive(tmp_path, monkeypatch):
    name = _write(tmp_path, monkeypatch,
                  "scrape_review_national_20260822-120000.json",
                  [{"url": "https://A.com/x/", "name": "A", "is_active": True},
                   {"url": "https://a.com/x", "name": "A dup"},      # dup within snapshot
                   {"url": "https://live.com/y", "name": "Already"},  # dup vs live table
                   {"name": "no url"}])
    inserted_rows = []

    def insert_fn(rows):
        inserted_rows.extend(rows)

    def existing_urls_fn():
        return {"https://live.com/y"}

    result = dc.commit_snapshot(name, patch_fn=None, insert_fn=insert_fn,
                                existing_urls_fn=existing_urls_fn, dry=False)
    assert result["ok"] is True
    assert result["applied"] == 1              # only the unique, live-absent one
    assert result["skipped_duplicate"] == 2    # snapshot-dup + live-dup
    assert result["skipped_no_change"] == 1    # the url-less row
    assert len(inserted_rows) == 1
    assert inserted_rows[0]["is_active"] is False   # always forced inactive


def test_commit_insert_dry_does_not_call_insert(tmp_path, monkeypatch):
    name = _write(tmp_path, monkeypatch,
                  "scrape_review_national_20260822-120000.json",
                  [{"url": "https://a.com/x", "name": "A"}])
    called = {"n": 0}

    def insert_fn(rows):
        called["n"] += 1

    result = dc.commit_snapshot(name, patch_fn=None, insert_fn=insert_fn,
                                existing_urls_fn=lambda: set(), dry=True)
    assert called["n"] == 0
    assert result["applied"] == 1
    assert result["would_insert"] == 1


def test_commit_insert_error_sets_not_ok(tmp_path, monkeypatch):
    name = _write(tmp_path, monkeypatch,
                  "scrape_review_national_20260822-120000.json",
                  [{"url": "https://a.com/x", "name": "A"}])

    def insert_fn(rows):
        raise RuntimeError("db down")

    result = dc.commit_snapshot(name, patch_fn=None, insert_fn=insert_fn,
                                existing_urls_fn=lambda: set(), dry=False)
    assert result["ok"] is False
    assert result["errors"] == 1
    assert any("db down" in d for d in result["error_details"])


# --------------------------------------------------------------------------- commit_snapshot (patch)

def test_commit_patch_metadata_applies_changes(tmp_path, monkeypatch):
    name = _write(tmp_path, monkeypatch,
                  "refresh_opportunities_dry_run_20260822-120000.json",
                  [{"id": 1, "changes": {"name": "New"}},
                   {"id": 2, "changes": {}},      # no change → skipped
                   {"changes": {"name": "z"}}])   # no id → skipped
    patched = []

    def patch_fn(opp_id, updates):
        patched.append((opp_id, updates))

    result = dc.commit_snapshot(name, patch_fn=patch_fn, insert_fn=None,
                                existing_urls_fn=None, dry=False)
    assert result["applied"] == 1
    assert result["skipped_no_change"] == 2
    assert patched[0][0] == 1
    assert patched[0][1]["name"] == "New"
    assert "updated_at" in patched[0][1]


def test_commit_patch_dry_counts_without_patching(tmp_path, monkeypatch):
    name = _write(tmp_path, monkeypatch,
                  "review_check_dry_run_20260822-120000.json",
                  [{"id": 1, "review_status": "legit", "previous_review_status": "unknown"}])
    called = {"n": 0}

    def patch_fn(opp_id, updates):
        called["n"] += 1

    result = dc.commit_snapshot(name, patch_fn=patch_fn, insert_fn=None,
                                existing_urls_fn=None, dry=True)
    assert called["n"] == 0
    assert result["applied"] == 1


def test_commit_patch_records_errors(tmp_path, monkeypatch):
    name = _write(tmp_path, monkeypatch,
                  "deadline_check_dry_run_20260822-120000.json",
                  [{"id": 1, "changed": True, "status": "running", "important_dates": []}])

    def patch_fn(opp_id, updates):
        raise RuntimeError("patch failed")

    result = dc.commit_snapshot(name, patch_fn=patch_fn, insert_fn=None,
                                existing_urls_fn=None, dry=False)
    assert result["errors"] == 1
    assert "1: patch failed" in result["error_details"][0]


def test_commit_unrecognised_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    result = dc.commit_snapshot("../etc/passwd", patch_fn=None, insert_fn=None,
                                existing_urls_fn=None)
    assert result["ok"] is False
    assert "Not a recognised snapshot file" in result["error"]


# --------------------------------------------------------------------------- _patch_updates

def test_patch_updates_reviews_shape():
    upd = dc._patch_updates("reviews", {"review_status": "legit",
                                        "previous_review_status": "unknown",
                                        "review_summary": "ok", "review_sources": ["u"]})
    assert upd["review_status"] == "legit"
    assert upd["review_sources"] == ["u"]
    assert "last_reviewed_at" in upd and "updated_at" in upd


def test_patch_updates_reviews_no_change_returns_none():
    assert dc._patch_updates("reviews", {"review_status": "legit",
                                         "previous_review_status": "legit"}) is None


def test_patch_updates_deadline_shape():
    upd = dc._patch_updates("deadline", {"changed": True, "status": "running",
                                         "important_dates": [{"d": 1}], "was_estimated": 1})
    assert upd["status"] == "running"
    assert upd["was_estimated"] is True
    assert "dates_last_checked_at" in upd


def test_patch_updates_unknown_agent_none():
    assert dc._patch_updates("scraper", {"id": 1}) is None


# --------------------------------------------------------------------------- list_snapshots

def test_list_snapshots_reads_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    (tmp_path / "scrape_review_national_20260822-120000.json").write_text(
        json.dumps({"inserted": [{"url": "u"}], "rejected": []}))
    (tmp_path / "refresh_opportunities_dry_run_20260101.json").write_text(
        json.dumps([{"id": 1, "changes": {"name": "x"}}]))
    snaps = dc.list_snapshots()
    files = {s["file"]: s for s in snaps}
    assert "scrape_review_national_20260822-120000.json" in files
    scr = files["scrape_review_national_20260822-120000.json"]
    assert scr["agent"] == "scraper"
    assert scr["has_time"] is True
    assert scr["entries"] == 1
    assert scr["mode"] == "national"
    md = files["refresh_opportunities_dry_run_20260101.json"]
    assert md["has_time"] is False   # date-only name


def test_list_snapshots_unreadable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    (tmp_path / "scrape_review_national_20260822-120000.json").write_text("{ not json")
    snaps = dc.list_snapshots()
    assert len(snaps) == 1
    assert "error" in snaps[0]
