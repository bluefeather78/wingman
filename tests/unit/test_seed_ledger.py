"""Unit tests for seed_ledger — the scraper's per-angle funnel and its diagnosis.

Pure module, no I/O, so everything here is hermetic by construction.
"""
from wingman import seed_ledger


# ---- reason_code / _negative_code -----------------------------------------------------

def test_reason_code_bare_and_with_note():
    assert seed_ledger.reason_code("duplicate") == "duplicate"
    assert seed_ledger.reason_code("wrong-page: landed on nd.edu homepage") == "wrong-page"
    assert seed_ledger.reason_code("  Other : whatever ") == "other"


def test_reason_code_empty():
    assert seed_ledger.reason_code(None) is None
    assert seed_ledger.reason_code("") is None
    assert seed_ledger.reason_code(":only a note") is None


def test_negative_code_bare_duplicate_status_files_as_duplicate():
    # A duplicate row whose reason was never filled is a duplicate by construction.
    assert seed_ledger._negative_code("duplicate", None) == "duplicate"
    # A reject with no code is legacy noise -> other (signals nothing, never auto-disables).
    assert seed_ledger._negative_code("rejected", None) == "other"
    # An explicit code always wins.
    assert seed_ledger._negative_code("duplicate", "third-party-url: x") == "third-party-url"


# ---- build_seed_funnels ---------------------------------------------------------------

def _seed(sid, **kw):
    base = {"id": sid, "total_found": 0, "total_added": 0, "total_dupes": 0,
            "total_runs": 0, "total_cost": 0.0}
    base.update(kw)
    return base


def test_build_funnels_groups_by_seed_and_counts_statuses():
    seeds = [_seed(1, total_found=20, total_added=8, total_runs=3, total_cost=1.0)]
    opps = [
        {"seed_id": 1, "moderation_status": "approved", "moderation_reason": None, "is_active": True},
        {"seed_id": 1, "moderation_status": "approved", "moderation_reason": None, "is_active": True},
        {"seed_id": 1, "moderation_status": "approved", "moderation_reason": None, "is_active": True},
        {"seed_id": 1, "moderation_status": "rejected", "moderation_reason": "duplicate: of ec1", "is_active": False},
        {"seed_id": 1, "moderation_status": "rejected", "moderation_reason": "duplicate: of ec2", "is_active": False},
        {"seed_id": 1, "moderation_status": "duplicate", "moderation_reason": "duplicate: survivor ec3", "is_active": False},
        {"seed_id": 1, "moderation_status": "pending_review", "moderation_reason": None, "is_active": False},
        {"seed_id": 1, "moderation_status": None, "moderation_reason": None, "is_active": False},
    ]
    f = seed_ledger.build_seed_funnels(opps, seeds)[1]
    assert f["approved"] == 3
    assert f["rejected"] == 2
    assert f["duplicate"] == 1
    assert f["pending"] == 2  # explicit pending + None status
    assert f["active"] == 3
    assert f["queue_total"] == 8
    assert f["adjudicated"] == 6  # 3 + 2 + 1
    assert f["reason_mix"] == {"duplicate": 3}
    # found=20, reached_queue=8 -> internal discards 12; + 2 rej + 1 dup = 15 / 20
    assert f["waste_rate"] == 0.75
    assert f["approval_rate"] == 0.5
    assert f["cost_per_approved"] == round(1.0 / 3, 4)


def test_build_funnels_seeds_every_known_angle_even_with_no_rows():
    seeds = [_seed(1), _seed(2)]
    f = seed_ledger.build_seed_funnels([], seeds)
    assert set(f) == {1, 2}
    assert f[1]["queue_total"] == 0
    assert f[1]["diagnosis"] == "insufficient_sample"


def test_build_funnels_keeps_orphan_seed_id_never_drops_a_row():
    # A backfilled row pointing at a seed we didn't load must still be counted.
    f = seed_ledger.build_seed_funnels(
        [{"seed_id": 99, "moderation_status": "approved", "moderation_reason": None, "is_active": True}],
        [_seed(1)])
    assert 99 in f and f[99]["approved"] == 1


def test_build_funnels_waste_rate_clamped_when_counter_undercounts():
    # A backfilled row can make queue_total exceed the seed's total_found counter; waste must
    # still stay in [0, 1] rather than reading as a >100% rate.
    seeds = [_seed(1, total_found=3, total_runs=2)]  # counter under-counts
    opps = ([{"seed_id": 1, "moderation_status": "rejected", "moderation_reason": "low-quality",
              "is_active": False}] * 4
            + [{"seed_id": 1, "moderation_status": "approved", "moderation_reason": None,
                "is_active": True}] * 6)
    f = seed_ledger.build_seed_funnels(opps, seeds)[1]
    assert f["queue_total"] == 10 and f["found"] == 3
    assert f["waste_rate"] == 0.4          # (0 internal + 4 rej) / max(3, 10)
    assert 0.0 <= f["waste_rate"] <= 1.0


def test_build_funnels_unknown_status_counts_as_pending_not_negative():
    f = seed_ledger.build_seed_funnels(
        [{"seed_id": 1, "moderation_status": "weird", "moderation_reason": None, "is_active": False}],
        [_seed(1)])[1]
    assert f["pending"] == 1
    assert f["rejected"] == 0 and f["duplicate"] == 0


# ---- diagnose: sample guard -----------------------------------------------------------

def _funnel(found=20, runs=3, adjudicated=8, approval_rate=0.2, mix=None):
    return {"found": found, "runs": runs, "adjudicated": adjudicated,
            "approval_rate": approval_rate, "reason_mix": mix or {},
            "approved": 0, "rejected": 0, "duplicate": 0}


def test_diagnose_insufficient_sample_low_found():
    assert seed_ledger.diagnose(_funnel(found=9)) == "insufficient_sample"


def test_diagnose_insufficient_sample_one_run():
    assert seed_ledger.diagnose(_funnel(runs=1)) == "insufficient_sample"


def test_diagnose_insufficient_sample_few_verdicts():
    assert seed_ledger.diagnose(_funnel(adjudicated=4)) == "insufficient_sample"


# ---- diagnose: healthy exempts from reason analysis -----------------------------------

def test_diagnose_healthy_even_with_dupes():
    # A productive angle (>=50% approval) is never retired for also re-buying some dupes.
    f = _funnel(approval_rate=0.6, mix={"duplicate": 4})
    assert seed_ledger.diagnose(f) == "healthy"


# ---- diagnose: reason-mix plurality ---------------------------------------------------

def test_diagnose_mined_out_when_duplicates_dominate():
    f = _funnel(approval_rate=0.3, mix={"duplicate": 6, "not-a-fit": 1})
    assert seed_ledger.diagnose(f) == "mined_out"
    f["diagnosis"] = "mined_out"
    assert seed_ledger.should_auto_disable(f) is True


def test_diagnose_thin_when_low_quality_dominates():
    f = _funnel(approval_rate=0.3, mix={"low-quality": 5, "duplicate": 1})
    assert seed_ledger.diagnose(f) == "thin"
    f["diagnosis"] = "thin"
    assert seed_ledger.should_auto_disable(f) is True


def test_diagnose_mis_aimed_never_auto_disables():
    f = _funnel(approval_rate=0.2, mix={"not-a-fit": 6, "duplicate": 1})
    assert seed_ledger.diagnose(f) == "mis_aimed"
    f["diagnosis"] = "mis_aimed"
    assert seed_ledger.should_auto_disable(f) is False


def test_diagnose_pipeline_limited_never_auto_disables():
    # third-party-url / wrong-page / dead-link all mean "the fix is the URL pipeline".
    f = _funnel(approval_rate=0.1, mix={"third-party-url": 3, "wrong-page": 2, "dead-link": 2,
                                        "duplicate": 1})
    assert seed_ledger.diagnose(f) == "pipeline_limited"
    f["diagnosis"] = "pipeline_limited"
    assert seed_ledger.should_auto_disable(f) is False


def test_diagnose_uncodeable_negatives_fall_to_pipeline_limited():
    # Negatives with no signal code cannot be diagnosed -> don't punish the angle.
    f = _funnel(approval_rate=0.2, mix={"other": 7})
    assert seed_ledger.diagnose(f) == "pipeline_limited"


def test_diagnose_coded_minority_amid_uncodeable_does_not_disable():
    # A handful of coded dupes amid a pile of unlabelled rejects (the backfill state) must
    # NOT auto-disable: the winning signal has to outweigh the uncodeable pile.
    f = _funnel(approval_rate=0.2, adjudicated=25, mix={"other": 20, "duplicate": 3})
    assert seed_ledger.diagnose(f) == "pipeline_limited"


def test_diagnose_tie_breaks_toward_not_disabling():
    # Equal duplicate (mined_out) and pipeline signal -> pipeline_limited wins the tie.
    f = _funnel(approval_rate=0.2, mix={"duplicate": 3, "wrong-page": 3})
    assert seed_ledger.diagnose(f) == "pipeline_limited"


# ---- should_auto_disable belt-and-braces ---------------------------------------------

def test_should_auto_disable_respects_sample_guard_independently():
    # Even if some caller hands us a mined_out diagnosis, too small a sample never disables.
    assert seed_ledger.should_auto_disable(
        {"diagnosis": "mined_out", "found": 5, "runs": 3}) is False
    assert seed_ledger.should_auto_disable(
        {"diagnosis": "mined_out", "found": 20, "runs": 1}) is False
    assert seed_ledger.should_auto_disable(
        {"diagnosis": "healthy", "found": 20, "runs": 3}) is False


def test_disable_reason_names_the_diagnosis_and_counts():
    f = {"diagnosis": "mined_out", "found": 20, "approved": 3, "duplicate": 6, "rejected": 2}
    r = seed_ledger.disable_reason(f)
    assert r.startswith("auto: mined_out")
    assert "20 found" in r and "3 approved" in r and "6 dup" in r
