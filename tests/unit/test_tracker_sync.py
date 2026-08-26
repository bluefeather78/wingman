"""The free batch tracker-sync read: id sanitising (PostgREST-injection guard + cap) and the
per-row source rule that decides whether an empty catalog list may clear a snapshot."""
from app.services.deadlines import _safe_ids, MAX_SYNC_IDS


def test_safe_ids_keeps_real_ids():
    assert _safe_ids(["ec18286", "ec17921", "user-added_1"]) == \
        ["ec18286", "ec17921", "user-added_1"]


def test_safe_ids_strips_postgrest_separators():
    # Commas, parens, quotes, dots and spaces are the characters an in.(...) list uses as
    # syntax — stripping them is the injection guard and a no-op for legitimate ids.
    assert _safe_ids(["ab,cd", "e(f)g", "h'i\"j", "k l"]) == ["abcd", "efg", "hij", "kl"]


def test_safe_ids_drops_empties():
    assert _safe_ids(["", "  ", "!!!", "ok"]) == ["ok"]


def test_safe_ids_caps_length():
    assert len(_safe_ids([f"id{i}" for i in range(MAX_SYNC_IDS + 50)])) == MAX_SYNC_IDS
