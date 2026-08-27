"""Backfill URL->seed attribution: the run-date constraint that stops mis-attribution.

Pure file reads against tmp logs; no DB, no network.
"""
import json
import os

import backfill_seed_attribution as bf


def _write_log(dir_, stamp, seed_id, candidates=None, resolved=None):
    name = f"scraper_{stamp}_seed{seed_id}.json"
    with open(os.path.join(dir_, name), "w", encoding="utf-8") as f:
        json.dump({"candidates": [{"url": u} for u in (candidates or [])],
                   "resolved_urls": (resolved or [])}, f)


def _row(url, source):
    return {"id": "ec1", "url": url, "source": source}


def test_single_seed_same_day_attributes(tmp_path):
    _write_log(tmp_path, "20260823-010000", 5, candidates=["https://a.edu/prog"])
    cand, resolved, *_ = bf.build_url_maps(str(tmp_path))
    sid, how = bf.attribute(_row("https://a.edu/prog", "scraper-national-20260823"), cand, resolved)
    assert (sid, how) == (5, "candidate")


def test_cross_day_refind_is_not_attributed(tmp_path):
    # Seed 42 re-proposes (and gets rejected) a URL on the 24th; the row was created on the
    # 23rd by an unlogged run. The date mismatch must keep 42 from claiming it.
    _write_log(tmp_path, "20260824-010000", 42, candidates=["https://a.edu/prog"])
    cand, resolved, *_ = bf.build_url_maps(str(tmp_path))
    sid, how = bf.attribute(_row("https://a.edu/prog", "scraper-national-20260823"), cand, resolved)
    assert sid is None and how == "unmatched"


def test_intra_day_collision_is_ambiguous(tmp_path):
    # Creator and re-finder both ran (and both logged) the same day -> refuse, don't guess.
    _write_log(tmp_path, "20260823-010000", 1, candidates=["https://a.edu/prog"])
    _write_log(tmp_path, "20260823-020000", 2, candidates=["https://a.edu/prog"])
    cand, resolved, *_ = bf.build_url_maps(str(tmp_path))
    sid, how = bf.attribute(_row("https://a.edu/prog", "scraper-national-20260823"), cand, resolved)
    assert sid is None and how == "ambiguous"


def test_resolved_is_the_fallback(tmp_path):
    _write_log(tmp_path, "20260823-010000", 3, resolved=["https://b.edu/index"])
    cand, resolved, *_ = bf.build_url_maps(str(tmp_path))
    sid, how = bf.attribute(_row("https://b.edu/index", "scraper-national-20260823"), cand, resolved)
    assert (sid, how) == (3, "resolved")


def test_candidate_beats_resolved(tmp_path):
    _write_log(tmp_path, "20260823-010000", 7, candidates=["https://c.edu/p"])
    _write_log(tmp_path, "20260823-020000", 8, resolved=["https://c.edu/p"])
    cand, resolved, *_ = bf.build_url_maps(str(tmp_path))
    sid, how = bf.attribute(_row("https://c.edu/p", "scraper-national-20260823"), cand, resolved)
    assert how == "candidate" and sid == 7


def test_seed_none_logs_are_skipped(tmp_path):
    # A fallback-angle run logs seedNone; it has no id to attribute to.
    with open(os.path.join(tmp_path, "scraper_20260823-010000_seedNone.json"), "w",
              encoding="utf-8") as f:
        json.dump({"candidates": [{"url": "https://d.edu/x"}], "resolved_urls": []}, f)
    cand, resolved, scanned, total = bf.build_url_maps(str(tmp_path))
    assert cand == {} and scanned == 0 and total == 1


def test_no_url_and_unmatched(tmp_path):
    _write_log(tmp_path, "20260823-010000", 1, candidates=["https://a.edu/prog"])
    cand, resolved, *_ = bf.build_url_maps(str(tmp_path))
    assert bf.attribute(_row(None, "scraper-national-20260823"), cand, resolved) == (None, "no-url")
    assert bf.attribute(_row("https://z.edu/none", "scraper-national-20260823"),
                        cand, resolved) == (None, "unmatched")


def test_date_only_stamp_parses(tmp_path):
    # Pre-2026-08-22 snapshots have a date-only stamp (no -HHMMSS).
    _write_log(tmp_path, "20260819", 9, candidates=["https://e.edu/p"])
    cand, resolved, *_ = bf.build_url_maps(str(tmp_path))
    sid, how = bf.attribute(_row("https://e.edu/p", "scraper-national-20260819"), cand, resolved)
    assert (sid, how) == (9, "candidate")
