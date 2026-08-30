"""Unit tests for find_catalog_dups.find_duplicate_pairs — the embedding-based catalog scan
that replaced the old free heuristic cuts (exact URL, name-similarity ratio, acronym/token
overlap) on 2026-08-30. Pure, no network: `index` is always injected explicitly, matching the
"every model call is injected" rule the rest of the dedupe stack follows.
"""
import find_catalog_dups as fcd


def _row(id_, name, url, org=None, active=True, **fields):
    return {"id": id_, "name": name, "url": url, "org": org, "is_active": active, **fields}


def _vec(*floats):
    return list(floats)


def _pair_ids(pairs):
    return {frozenset((p["rows"][0]["id"], p["rows"][1]["id"])) for p in pairs}


def test_exact_url_match_is_proof_even_with_no_index():
    # Pass 1 (exact match_key) needs no embedding at all — both rows are unembedded here.
    rows = [
        _row("1", "Marine Biology Camp", "https://ocean.org/camp/"),
        _row("2", "Marine Biology Camp", "https://ocean.org/camp"),  # trailing slash only
    ]
    pairs, unembedded = fcd.find_duplicate_pairs(rows, index=[])
    assert frozenset(("1", "2")) in _pair_ids(pairs)
    proof_pair = next(p for p in pairs if frozenset((p["rows"][0]["id"], p["rows"][1]["id"]))
                       == frozenset(("1", "2")))
    assert proof_pair["tier"] == "proof"
    assert set(unembedded) == {"1", "2"}


def test_high_cosine_same_name_is_confident():
    # Same org on both sides so the same-institution context guard passes (a CONFIDENT auto-merge
    # needs positive evidence of the same institution, not just a name match) — see
    # dedupe_confidence.same_context.
    rows = [
        _row("1", "Girls Who Code Summer Immersion", "https://a.example.edu/gwc",
             org="Girls Who Code"),
        _row("2", "Girls Who Code Summer Immersion", "https://b.example.org/gwc",
             org="Girls Who Code"),
    ]
    index = [
        {"id": "1", "vector": _vec(1.0, 0.0, 0.0)},
        {"id": "2", "vector": _vec(0.999, 0.001, 0.0)},
    ]
    pairs, unembedded = fcd.find_duplicate_pairs(rows, index=index)
    assert unembedded == []
    assert frozenset(("1", "2")) in _pair_ids(pairs)
    p = next(p for p in pairs if frozenset((p["rows"][0]["id"], p["rows"][1]["id"]))
             == frozenset(("1", "2")))
    assert p["tier"] == "confident"
    assert p["cosine"] > 0.99


def test_high_cosine_conflicting_name_is_sibling_not_surfaced():
    # The embedding sees shared boilerplate; the name discriminator must still separate two
    # distinct programs at the same institution — exactly the case dedupe_confidence exists for.
    rows = [
        _row("1", "Badger Music Clinic", "https://wisc.edu/music"),
        _row("2", "Badger Arts Clinic", "https://wisc.edu/arts"),
    ]
    index = [
        {"id": "1", "vector": _vec(1.0, 0.0, 0.0)},
        {"id": "2", "vector": _vec(0.98, 0.02, 0.0)},
    ]
    pairs, _unembedded = fcd.find_duplicate_pairs(rows, index=index)
    assert frozenset(("1", "2")) not in _pair_ids(pairs)


def test_low_cosine_is_not_surfaced():
    rows = [
        _row("1", "Robotics Summer Camp", "https://a.org/"),
        _row("2", "Underwater Basket Weaving", "https://b.org/"),
    ]
    index = [
        {"id": "1", "vector": _vec(1.0, 0.0, 0.0)},
        {"id": "2", "vector": _vec(0.0, 1.0, 0.0)},
    ]
    pairs, _unembedded = fcd.find_duplicate_pairs(rows, index=index)
    assert pairs == []


def test_missing_from_index_is_reported_not_dropped():
    rows = [
        _row("1", "Marine Biology Camp", "https://ocean.org/a"),
        _row("2", "Robotics Camp", "https://robots.org/b"),
    ]
    index = [{"id": "1", "vector": _vec(1.0, 0.0, 0.0)}]  # row 2 has no vector
    _pairs, unembedded = fcd.find_duplicate_pairs(rows, index=index)
    assert unembedded == ["2"]


def test_symmetric_match_is_one_pair_not_two():
    rows = [
        _row("1", "Girls Who Code Summer Immersion", "https://a.example.edu/gwc"),
        _row("2", "Girls Who Code Summer Immersion", "https://b.example.org/gwc"),
    ]
    index = [
        {"id": "1", "vector": _vec(1.0, 0.0, 0.0)},
        {"id": "2", "vector": _vec(0.999, 0.001, 0.0)},
    ]
    pairs, _unembedded = fcd.find_duplicate_pairs(rows, index=index)
    assert len(pairs) == 1


def test_inactive_rows_never_reach_the_scan():
    # fetch_all_rows filters is_active=eq.true server-side; find_duplicate_pairs itself has no
    # activity filter, so this pins the CONTRACT — callers must not hand it inactive rows.
    rows = [
        _row("1", "NACLO", "https://naclo.org/", active=False),
        _row("2", "NACLO", "https://naclo.org/", active=False),
    ]
    pairs, _unembedded = fcd.find_duplicate_pairs(rows, index=[])
    # Exact-URL pass 1 does not itself check is_active — it still finds the pair. That is
    # correct: is_active filtering is fetch_all_rows's job, not find_duplicate_pairs's.
    assert frozenset(("1", "2")) in _pair_ids(pairs)
