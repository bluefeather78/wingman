"""Insert-degrade tiers and the run-end auto-disable sweep in scrape_opportunities.

Both talk to Supabase through module-level helpers, monkeypatched here — no sockets.
"""
import scrape_opportunities as so


def _rows():
    return [{"id": "ec1", "name": "N", "url": "https://x.org/p", "seed_id": 5}]


def _review():
    return {"ec1": {"moderation_status": "pending_review", "dup_candidates": None,
                    "quality_flags": None}}


def _fake_post(forbidden):
    """A supabase_post that 400s if any inserted row carries a 'missing' column."""
    calls = []

    def post(url, table, rows, key, **kw):
        calls.append([dict(r) for r in rows])
        for r in rows:
            for k in forbidden:
                if k in r:
                    raise RuntimeError(f"PGRST204: Could not find the '{k}' column")
        return None

    return post, calls


def test_without_strips_only_named_keys():
    out = so._without([{"a": 1, "seed_id": 2, "found_via": "h"}], so.ATTRIBUTION_KEYS)
    assert out == [{"a": 1}]


def test_insert_full_when_both_migrations_present(monkeypatch):
    post, calls = _fake_post(forbidden=set())
    monkeypatch.setattr(so, "supabase_post", post)
    tier = so.insert_rows("u", "k", _rows(), _review())
    assert tier == "full"
    assert len(calls) == 1
    assert calls[0][0]["moderation_status"] == "pending_review"
    assert calls[0][0]["seed_id"] == 5


def test_insert_keeps_review_when_only_attribution_pending(monkeypatch):
    # The expected Phase-1 deploy window: db/user_submissions_schema.sql applied, attribution not.
    # The review columns MUST survive — dropping them would strip the queue of its flags.
    post, calls = _fake_post(forbidden={"seed_id", "found_via"})
    monkeypatch.setattr(so, "supabase_post", post)
    tier = so.insert_rows("u", "k", _rows(), _review())
    assert tier == "no-attribution"
    assert calls[-1][0]["moderation_status"] == "pending_review"  # review columns kept
    assert "seed_id" not in calls[-1][0]                          # attribution dropped


def test_insert_drops_review_columns_when_that_migration_pending(monkeypatch):
    post, calls = _fake_post(forbidden={"moderation_status", "dup_candidates", "quality_flags"})
    monkeypatch.setattr(so, "supabase_post", post)
    tier = so.insert_rows("u", "k", _rows(), _review())
    assert tier == "no-review"
    # Final (successful) write keeps seed_id but not the review columns.
    assert "moderation_status" not in calls[-1][0]
    assert calls[-1][0]["seed_id"] == 5


def test_insert_drops_attribution_when_both_pending(monkeypatch):
    post, calls = _fake_post(forbidden={"moderation_status", "dup_candidates", "quality_flags",
                                        "seed_id", "found_via"})
    monkeypatch.setattr(so, "supabase_post", post)
    tier = so.insert_rows("u", "k", _rows(), _review())
    assert tier == "minimal"
    assert "seed_id" not in calls[-1][0]
    assert "moderation_status" not in calls[-1][0]
    assert calls[-1][0]["name"] == "N"  # the guaranteed columns still land


# ---- auto_disable_mined_seeds ---------------------------------------------------------

def _seed_row(sid, **kw):
    base = {"id": sid, "is_enabled": True, "total_runs": 3, "total_found": 20,
            "total_added": 8, "total_dupes": 0, "total_cost": 1.0}
    base.update(kw)
    return base


def _wire(monkeypatch, seed_rows, opp_rows):
    patched = []

    def fake_get(url, table, params, key):
        return seed_rows if table == "scraper_seeds" else opp_rows

    def fake_patch(url, table, match, body, key):
        patched.append((match, body))
        return None

    monkeypatch.setattr(so, "supabase_get", fake_get)
    monkeypatch.setattr(so, "supabase_patch", fake_patch)
    return patched


def test_auto_disable_retires_mined_out_but_spares_healthy(monkeypatch):
    seeds = [_seed_row(1), _seed_row(2)]
    opps = (
        [{"seed_id": 1, "moderation_status": "duplicate", "moderation_reason": "duplicate: x",
          "is_active": False}] * 6
        + [{"seed_id": 1, "moderation_status": "approved", "moderation_reason": None,
            "is_active": True}] * 2
        + [{"seed_id": 2, "moderation_status": "approved", "moderation_reason": None,
            "is_active": True}] * 6
        + [{"seed_id": 2, "moderation_status": "rejected", "moderation_reason": "low-quality",
            "is_active": False}] * 2
    )
    patched = _wire(monkeypatch, seeds, opps)
    disabled = so.auto_disable_mined_seeds("u", "k", [{"id": 1}, {"id": 2}])
    assert [sid for sid, _ in disabled] == [1]           # only the mined-out angle
    assert len(patched) == 1
    match, body = patched[0]
    assert match == {"id": "eq.1"}
    assert body["is_enabled"] is False
    assert body["disabled_reason"].startswith("auto: mined_out")


def test_auto_disable_falls_back_when_reason_columns_missing(monkeypatch):
    seeds = [_seed_row(1)]
    opps = ([{"seed_id": 1, "moderation_status": "duplicate", "moderation_reason": "duplicate: x",
              "is_active": False}] * 6
            + [{"seed_id": 1, "moderation_status": "approved", "moderation_reason": None,
                "is_active": True}] * 2)
    calls = []

    def fake_get(url, table, params, key):
        return seeds if table == "scraper_seeds" else opps

    def fake_patch(url, table, match, body, key):
        calls.append(body)
        if "disabled_reason" in body:
            raise RuntimeError("PGRST204: Could not find the 'disabled_reason' column")
        return None

    monkeypatch.setattr(so, "supabase_get", fake_get)
    monkeypatch.setattr(so, "supabase_patch", fake_patch)
    disabled = so.auto_disable_mined_seeds("u", "k", [{"id": 1}])
    assert [sid for sid, _ in disabled] == [1]
    # First attempt carried the reason (rejected), retry disabled the angle bare.
    assert len(calls) == 2 and calls[-1] == {"is_enabled": False}


def test_auto_disable_noop_without_ids(monkeypatch):
    # Fallback angles have no id; nothing to attribute, nothing to disable.
    assert so.auto_disable_mined_seeds("u", "k", [{"id": None}]) == []
