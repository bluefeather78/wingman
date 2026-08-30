"""Phase 7 eval scoring math (matching_eval.py). The live model call is not exercised; the
scoring — especially the asymmetric over/under-exclusion accounting — is what's pinned, since
the under-exclusion metric is the ONLY measure of the eligibility reasoning's worst failure.
"""
import matching_eval as ev


def _g(gold, pred, dim="d"):
    return {"gold_eligible": gold, "predicted_eligible": pred, "dimension": dim}


def test_under_exclusion_is_gold_ineligible_predicted_eligible():
    # a girls-only program (ineligible) shown to a boy (predicted eligible) = under-exclusion
    report = ev.score_eligibility([_g(False, True)])
    o = report["overall"]
    assert o["under_exclusions"] == 1
    assert o["over_exclusions"] == 0
    assert o["true_exclusions"] == 0
    assert o["correct"] == 0


def test_over_exclusion_is_gold_eligible_predicted_ineligible():
    # an open program (eligible) wrongly hidden (predicted ineligible) = over-exclusion
    report = ev.score_eligibility([_g(True, False)])
    o = report["overall"]
    assert o["over_exclusions"] == 1
    assert o["under_exclusions"] == 0
    assert o["correct"] == 0


def test_correct_exclusion_and_keep():
    report = ev.score_eligibility([_g(False, False), _g(True, True)])
    o = report["overall"]
    assert o["true_exclusions"] == 1 and o["correct_keeps"] == 1
    assert o["correct"] == 2 and o["under_exclusions"] == 0 and o["over_exclusions"] == 0


def test_precision_recall_math():
    # 2 true exclusions, 1 over (fp), 1 under (fn)
    graded = [_g(False, False), _g(False, False), _g(True, False), _g(False, True)]
    o = ev.score_eligibility(graded)["overall"]
    assert o["true_exclusions"] == 2 and o["over_exclusions"] == 1 and o["under_exclusions"] == 1
    assert o["exclusion_precision"] == 2 / 3   # tp/(tp+fp)
    assert o["exclusion_recall"] == 2 / 3      # tp/(tp+fn)


def test_precision_recall_none_when_no_exclusions():
    o = ev.score_eligibility([_g(True, True)])["overall"]
    assert o["exclusion_precision"] is None and o["exclusion_recall"] is None


def test_by_dimension_breakdown():
    graded = [_g(False, True, "demographic"), _g(True, True, "open")]
    by = ev.score_eligibility(graded)["by_dimension"]
    assert by["demographic"]["under_exclusions"] == 1
    assert by["open"]["correct"] == 1


def test_run_eligibility_eval_with_stub():
    # stub verdict_fn: everyone eligible -> the two gold-ineligible seed cases become under-exclusions
    graded = ev.run_eligibility_eval(ev.SEED_ELIGIBILITY_CASES, lambda case: True)
    report = ev.score_eligibility(graded)
    gold_ineligible = sum(1 for c in ev.SEED_ELIGIBILITY_CASES if not c["gold_eligible"])
    assert report["overall"]["under_exclusions"] == gold_ineligible


def test_seed_cases_cover_both_directions_and_key_dimensions():
    dims = {c["dimension"] for c in ev.SEED_ELIGIBILITY_CASES}
    assert {"demographic", "residency", "grade", "citizenship", "marketing"} <= dims
    assert any(c["gold_eligible"] for c in ev.SEED_ELIGIBILITY_CASES)       # eligible cases
    assert any(not c["gold_eligible"] for c in ev.SEED_ELIGIBILITY_CASES)   # ineligible cases
