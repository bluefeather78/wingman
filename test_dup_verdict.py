"""Unit tests for the pure dedupe-verdict resolver (dup_verdict.py). Free, offline.

These pin the RESOLVER's own behaviour — tier->confidence mapping, the sibling cosine floor,
single-strongest selection, self-skip — over the real dedupe_confidence engine, using synthetic
rows constructed to land each engine tier deterministically.
"""
import dup_verdict as dv


def _row(id, name, org="City STEM Alliance", url=None, **over):
    r = {
        "id": id, "name": name, "org": org,
        "url": url or f"https://example.org/{id}",
        "type": "Program", "season": "Summer", "grade_min": 9, "grade_max": 12, "price": "Free",
    }
    r.update(over)
    return r


def _const_cos(value):
    return lambda _oid: value


# Names constructed so the engine's name_relation lands where we need it:
#  identical  -> SAME    (a rename/duplicate)
#  swap one distinctive token -> CONFLICT (a sibling: 'Robotics' vs 'Chess')
BASE = _row("a", "Downtown Youth Robotics League")
DUP = _row("b", "Downtown Youth Robotics League")                 # identical name, same fields
SIBLING = _row("c", "Downtown Youth Chess League")                # conflicting distinctive token
UNRELATED = _row("d", "Advanced Marine Biology Institute", org="Coastal Research Foundation")


def test_clear_duplicate_is_likely_or_certain():
    v = dv.resolve_dup_verdict(BASE, [DUP], _const_cos(0.98))
    assert v is not None
    assert v.confidence in (dv.CONFIDENCE_LIKELY, dv.CONFIDENCE_CERTAIN)
    assert v.duplicate_of == "b"
    assert v.sibling is False


def test_unrelated_row_yields_no_verdict():
    assert dv.resolve_dup_verdict(BASE, [UNRELATED], _const_cos(0.30)) is None


def test_sibling_below_floor_is_hidden():
    # High-enough cosine to be similar, but below the show floor -> a real different program.
    assert dv.resolve_dup_verdict(BASE, [SIBLING], _const_cos(0.90)) is None


def test_sibling_above_floor_surfaces_as_possible():
    v = dv.resolve_dup_verdict(BASE, [SIBLING], _const_cos(0.97))
    assert v is not None
    assert v.confidence == dv.CONFIDENCE_POSSIBLE
    assert v.sibling is True
    assert any("different program" in r for r in v.reasons)


def test_strongest_candidate_wins():
    # A real duplicate AND a high-cosine sibling both present -> the duplicate must win.
    v = dv.resolve_dup_verdict(BASE, [SIBLING, DUP], _const_cos(0.97))
    assert v is not None
    assert v.duplicate_of == "b"
    assert v.sibling is False


def test_self_is_skipped():
    assert dv.resolve_dup_verdict(BASE, [BASE], _const_cos(1.0)) is None


def test_empty_candidates_yields_none():
    assert dv.resolve_dup_verdict(BASE, [], _const_cos(1.0)) is None


def test_verdict_serializes_round_fields():
    v = dv.resolve_dup_verdict(BASE, [DUP], _const_cos(0.98))
    d = v.as_dict()
    assert set(d) == {"confidence", "duplicate_of", "name", "url", "tier", "cosine",
                      "reasons", "sibling"}
    assert d["duplicate_of"] == "b"
    assert d["name"] == "Downtown Youth Robotics League"


if __name__ == "__main__":
    import sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for f in fns:
        try:
            f()
            print(f"  ok  {f.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {f.__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
