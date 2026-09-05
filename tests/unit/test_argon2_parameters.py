"""Unit tests for the Argon2id parameters — Phase 2 item 7.

Until this item the hasher was a bare PasswordHasher(), i.e. argon2-cffi's library defaults.
Those are HEAVIER than OWASP's recommendation, not lighter (64 MiB/t=3/p=4 against
19 MiB/t=2/p=1), so setting them explicitly makes sign-in cheaper — which is the point on a
512 MB, 0.1-CPU instance where 64 MiB per in-flight hash is an OOM reachable from an
unauthenticated endpoint.

The load-bearing test is the last one: changing these parameters must stay self-migrating, or
the change silently locks every existing account out.
"""
from argon2 import PasswordHasher

from app.auth import passwords


def test_parameters_are_owasps_argon2id_recommendation():
    assert passwords.ARGON2_TIME_COST == 2
    assert passwords.ARGON2_MEMORY_COST_KIB == 19456          # 19 MiB
    assert passwords.ARGON2_PARALLELISM == 1


def test_the_hasher_actually_uses_them():
    """A constant nobody passes to PasswordHasher() is documentation, not configuration."""
    assert passwords._ph.time_cost == passwords.ARGON2_TIME_COST
    assert passwords._ph.memory_cost == passwords.ARGON2_MEMORY_COST_KIB
    assert passwords._ph.parallelism == passwords.ARGON2_PARALLELISM


def test_memory_per_hash_leaves_room_on_a_512mb_instance():
    """The reason the number went down. At the library's 64 MiB default, eight concurrent
    sign-ins is the whole free-tier instance."""
    mib = passwords.ARGON2_MEMORY_COST_KIB / 1024
    assert mib <= 20
    assert mib * 25 < 512


def test_a_hash_verifies_round_trip():
    h = passwords.hash_password("a" * 64)
    ok, needs_upgrade = passwords.verify_password(h, "a" * 64)
    assert ok is True and needs_upgrade is False


def test_a_wrong_password_still_fails():
    h = passwords.hash_password("a" * 64)
    ok, _ = passwords.verify_password(h, "b" * 64)
    assert ok is False


def test_a_row_hashed_with_the_old_defaults_still_verifies_and_asks_for_rehash():
    """The load-bearing one. Every existing account's stored hash carries the OLD parameters;
    it must still verify, and must report needs_upgrade so the login route rewrites it. If this
    breaks, changing the parameters locks out the entire user base."""
    old = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
    stored = old.hash("c" * 64)
    ok, needs_upgrade = passwords.verify_password(stored, "c" * 64)
    assert ok is True, "existing accounts must still be able to sign in"
    assert needs_upgrade is True, "the row must be rewritten with the new parameters"


def test_legacy_sha256_rows_are_untouched_by_this():
    """The pre-argon2 migration path still works — it never reaches the hasher."""
    ok, needs_upgrade = passwords.verify_password("d" * 64, "d" * 64)
    assert ok is True and needs_upgrade is True
