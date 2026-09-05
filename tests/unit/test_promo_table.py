"""Promo codes live in a table, not in the source — S1-10, finding L3.

BETAUSER / FREEMONTH / WELCOME10 were literals in wingman/subscription_common.py, so
anybody who could read the repository had free access — and with M6's race, repeatedly.
There was also no way to expire a code, cap how often it could be handed out, or retire one
without a deploy.
"""
import datetime
import json

import pytest

from wingman import subscription_common as sc


@pytest.fixture(autouse=True)
def _cold_cache(monkeypatch):
    monkeypatch.setattr(sc, "_promo_cache", {"codes": None, "at": 0.0})
    monkeypatch.setattr(sc, "_promo_table_warned", False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)


def _with_table(monkeypatch, rows):
    monkeypatch.setenv("SUPABASE_URL", "https://db.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(sc.supabase_common, "supabase_get",
                        lambda url, table, params, key, **kw: rows)


def _iso(days):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=days)).isoformat()


# ---------------- the table is the source of record ----------------

def test_a_code_defined_only_in_the_table_validates(monkeypatch):
    _with_table(monkeypatch, [{"code": "SCHOOL25", "kind": "grant", "status": "beta",
                               "grant_days": 30, "description": "Pilot school"}])
    data, err = sc.validate_promo_code("school25")
    assert err is None
    assert data["grant_days"] == 30


def test_a_built_in_code_retired_in_the_table_stops_working(monkeypatch):
    """Retiring a code was previously a deploy. is_active=false is filtered in the query,
    so a retired code simply is not in the answer."""
    _with_table(monkeypatch, [{"code": "SCHOOL25", "kind": "grant"}])
    assert sc.validate_promo_code("BETAUSER") == (None, "Invalid promo code")


def test_the_lookup_is_case_and_whitespace_insensitive(monkeypatch):
    _with_table(monkeypatch, [{"code": "SCHOOL25", "kind": "checkout"}])
    assert sc.validate_promo_code("  school25  ")[1] is None


# ---------------- expiry and the cap ----------------

def test_an_expired_code_is_refused(monkeypatch):
    _with_table(monkeypatch, [{"code": "OLD", "kind": "grant", "status": "beta",
                               "grant_days": 7, "expires_at": _iso(-1)}])
    assert sc.validate_promo_code("OLD") == (None, "Invalid promo code")


def test_a_code_expiring_later_still_works(monkeypatch):
    _with_table(monkeypatch, [{"code": "SOON", "kind": "grant", "status": "beta",
                               "grant_days": 7, "expires_at": _iso(1)}])
    assert sc.validate_promo_code("SOON")[1] is None


def test_an_unparseable_expiry_is_treated_as_expired(monkeypatch):
    """A date nobody can read is not a reason to keep handing out free access."""
    _with_table(monkeypatch, [{"code": "WEIRD", "kind": "grant", "expires_at": "soon"}])
    assert sc.validate_promo_code("WEIRD") == (None, "Invalid promo code")


def test_an_exhausted_code_is_refused(monkeypatch):
    _with_table(monkeypatch, [{"code": "FIRST100", "kind": "grant", "status": "beta",
                               "grant_days": 7, "max_redemptions": 100,
                               "redemption_count": 100}])
    assert sc.validate_promo_code("FIRST100") == (None, "Invalid promo code")


def test_a_code_under_its_cap_still_works(monkeypatch):
    _with_table(monkeypatch, [{"code": "FIRST100", "kind": "grant", "status": "beta",
                               "grant_days": 7, "max_redemptions": 100,
                               "redemption_count": 99}])
    assert sc.validate_promo_code("FIRST100")[1] is None


def test_no_cap_means_unlimited(monkeypatch):
    _with_table(monkeypatch, [{"code": "OPEN", "kind": "grant", "status": "beta",
                               "grant_days": 7, "redemption_count": 100000}])
    assert sc.validate_promo_code("OPEN")[1] is None


def test_expired_and_exhausted_answer_the_same_thing_as_unknown(monkeypatch):
    """Distinguishing them would tell a caller which codes exist, which is the thing having
    them in a table is meant to stop."""
    _with_table(monkeypatch, [{"code": "OLD", "kind": "grant", "expires_at": _iso(-1)}])
    assert sc.validate_promo_code("OLD") == sc.validate_promo_code("NEVER_EXISTED")


# ---------------- degrade ----------------

def test_an_unmigrated_database_keeps_the_built_in_codes(monkeypatch, capsys):
    """Every code suddenly reading as invalid would look, to a student holding a valid one,
    identical to being cheated."""
    monkeypatch.setenv("SUPABASE_URL", "https://db.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")

    def _missing(*a, **k):
        raise RuntimeError('relation "promo_codes" does not exist')

    monkeypatch.setattr(sc.supabase_common, "supabase_get", _missing)
    assert sc.validate_promo_code("BETAUSER")[1] is None
    assert "promo_codes_schema.sql" in capsys.readouterr().out


def test_the_missing_table_warning_is_printed_once_per_process(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://db.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(sc.supabase_common, "supabase_get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    sc.load_promo_codes(force=True)
    capsys.readouterr()
    sc.load_promo_codes(force=True)
    assert "WARN" not in capsys.readouterr().out


def test_no_credentials_means_the_built_in_codes(monkeypatch):
    assert sc.validate_promo_code("FREEMONTH")[1] is None


def test_the_seeded_rows_match_the_built_in_table():
    """The seed in db/promo_codes_schema.sql is what makes running the migration a no-op
    rather than a behaviour change."""
    import pathlib
    sql = pathlib.Path("db/promo_codes_schema.sql").read_text()
    for code in sc.PROMO_CODES:
        assert f"'{code}'" in sql


# ---------------- the redemption counter ----------------

def test_the_counter_is_a_compare_and_swap(monkeypatch):
    """Two concurrent redemptions writing count+1 from the same starting value would let a
    max_redemptions of 100 hand out considerably more than 100."""
    seen = {}
    monkeypatch.setenv("SUPABASE_URL", "https://db.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(sc.supabase_common, "supabase_patch",
                        lambda url, table, params, body, key: seen.update(
                            params=params, body=body))
    sc.note_promo_redemption("first100", {"redemption_count": 41})
    assert seen["params"]["code"] == "eq.FIRST100"
    assert seen["params"]["redemption_count"] == "eq.41"
    assert seen["body"] == {"redemption_count": 42}


def test_a_failed_counter_bump_never_fails_the_redemption(monkeypatch, capsys):
    """The per-user guard is S1-6's conditional PATCH; this counter is the global cap and
    the operator's usage number. It must not be able to take away a grant already written."""
    monkeypatch.setenv("SUPABASE_URL", "https://db.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(sc.supabase_common, "supabase_patch",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert sc.note_promo_redemption("BETAUSER", {"redemption_count": 0}) is None
    assert "WARN" in capsys.readouterr().out


def test_the_cache_is_dropped_after_a_redemption(monkeypatch):
    """Without this a capped code keeps validating from a count that never moves."""
    _with_table(monkeypatch, [{"code": "X", "kind": "grant"}])
    sc.load_promo_codes(force=True)
    assert sc._promo_cache["codes"] is not None
    monkeypatch.setattr(sc.supabase_common, "supabase_patch", lambda *a, **k: None)
    sc.note_promo_redemption("X", {"redemption_count": 0})
    assert sc._promo_cache["codes"] is None


def test_the_table_is_cached_between_lookups(monkeypatch):
    calls = []
    monkeypatch.setenv("SUPABASE_URL", "https://db.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(sc.supabase_common, "supabase_get",
                        lambda *a, **k: calls.append(1) or [{"code": "X", "kind": "grant"}])
    sc.validate_promo_code("X")
    sc.validate_promo_code("X")
    assert len(calls) == 1


def test_only_active_rows_are_requested(monkeypatch):
    seen = {}
    monkeypatch.setenv("SUPABASE_URL", "https://db.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(sc.supabase_common, "supabase_get",
                        lambda url, table, params, key, **kw: seen.update(
                            table=table, params=params) or [])
    sc.load_promo_codes(force=True)
    assert seen["table"] == "promo_codes"
    assert seen["params"]["is_active"] == "eq.true"
