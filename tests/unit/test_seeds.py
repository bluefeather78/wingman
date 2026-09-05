"""Unit tests for wingman/seeds_common.py — select_seeds (pure) and the load_seeds fallback path
reached by forcing supabase_get to return empty / raise.
"""
from wingman import seeds_common as sc


def _seeds():
    return [
        {"id": 10, "mode": "national", "angle": "A", "is_enabled": True, "sort_order": 0},
        {"id": 20, "mode": "national", "angle": "B", "is_enabled": True, "sort_order": 1},
        {"id": 30, "mode": "national", "angle": "C", "is_enabled": True, "sort_order": 2},
    ]


# --------------------------------------------------------------------------- select_seeds

def test_select_seeds_no_filter_returns_all():
    seeds = _seeds()
    assert sc.select_seeds(seeds) is seeds


def test_select_by_ids():
    chosen = sc.select_seeds(_seeds(), seed_ids="30,10")
    # order follows the requested id order.
    assert [s["id"] for s in chosen] == [30, 10]


def test_select_by_ids_skips_missing(capsys):
    chosen = sc.select_seeds(_seeds(), seed_ids="10,999")
    assert [s["id"] for s in chosen] == [10]
    out = capsys.readouterr().out
    assert "999" in out  # missing id warned


def test_select_by_ids_ignores_blank_tokens():
    chosen = sc.select_seeds(_seeds(), seed_ids=" 10 , , 20 ")
    assert [s["id"] for s in chosen] == [10, 20]


def test_select_by_indices_deprecated_positional():
    chosen = sc.select_seeds(_seeds(), seed_indices="0,2")
    assert [s["id"] for s in chosen] == [10, 30]


def test_select_by_indices_out_of_range_ignored(capsys):
    chosen = sc.select_seeds(_seeds(), seed_indices="0,99")
    assert [s["id"] for s in chosen] == [10]
    assert "out-of-range" in capsys.readouterr().out


def test_select_ids_take_priority_over_indices():
    chosen = sc.select_seeds(_seeds(), seed_ids="20", seed_indices="0")
    assert [s["id"] for s in chosen] == [20]


def test_select_ids_ignores_none_id_fallback_seeds():
    seeds = _seeds() + [{"id": None, "angle": "fallback"}]
    chosen = sc.select_seeds(seeds, seed_ids="10")
    assert [s["id"] for s in chosen] == [10]


# --------------------------------------------------------------------------- load_seeds fallback

def test_load_seeds_empty_table_uses_fallback(monkeypatch, capsys):
    monkeypatch.setattr(sc, "supabase_get", lambda *a, **k: [])
    seeds = sc.load_seeds("url", "key", "national", fallback=["angle one", "angle two"])
    assert [s["angle"] for s in seeds] == ["angle one", "angle two"]
    assert all(s["id"] is None for s in seeds)   # fallback seeds carry id=None
    assert "falling back" in capsys.readouterr().out


def test_load_seeds_unreachable_uses_fallback(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(sc, "supabase_get", boom)
    seeds = sc.load_seeds("url", "key", "national", fallback=["only angle"])
    assert len(seeds) == 1
    assert seeds[0]["id"] is None
    assert seeds[0]["angle"] == "only angle"


def test_load_seeds_no_fallback_returns_empty(monkeypatch):
    monkeypatch.setattr(sc, "supabase_get", lambda *a, **k: [])
    assert sc.load_seeds("url", "key", "national", fallback=None) == []


def test_load_seeds_returns_rows_when_present(monkeypatch):
    rows = _seeds()
    monkeypatch.setattr(sc, "supabase_get", lambda *a, **k: rows)
    assert sc.load_seeds("url", "key", "national", fallback=["x"]) == rows


def test_load_seeds_include_disabled_flag(monkeypatch):
    captured = {}

    def fake_get(url, table, params, key):
        captured["params"] = params
        return _seeds()

    monkeypatch.setattr(sc, "supabase_get", fake_get)
    sc.load_seeds("url", "key", "national", include_disabled=True)
    # is_enabled filter omitted when including disabled seeds.
    assert "is_enabled" not in captured["params"]

    sc.load_seeds("url", "key", "national", include_disabled=False)
    assert captured["params"]["is_enabled"] == "eq.true"
