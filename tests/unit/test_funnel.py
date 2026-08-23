"""Unit tests for ops/core.py funnel-stage math.

Every function here is pure over injected dicts (a `users` row's `data` jsonb, plus
the sets of searched/returned userids) — no Supabase reads. Constants pinned against
the real source: PROFILE_SUFFICIENT_WORDS=20, PROFILE_RICH_WORDS=100,
PROFILE_RICH_ROUNDS=3, TRACKER_BUCKETS, and the 8-stage FUNNEL_STAGE_KEYS.
"""
import datetime as _dt

import pytest

import ops.core as core


# --------------------------------------------------------------------------- #
# _json_obj
# --------------------------------------------------------------------------- #
class TestJsonObj:
    def test_dict_passthrough(self):
        d = {"a": 1}
        assert core._json_obj(d) is d

    def test_json_string_parsed(self):
        assert core._json_obj('{"a": 1}') == {"a": 1}

    def test_bad_json_string_to_empty(self):
        assert core._json_obj("{not json") == {}

    def test_non_dict_json_to_empty(self):
        # valid JSON but not an object (a list) → {}
        assert core._json_obj("[1, 2]") == {}

    def test_none_to_empty(self):
        assert core._json_obj(None) == {}

    def test_int_to_empty(self):
        assert core._json_obj(5) == {}


# --------------------------------------------------------------------------- #
# _profile_facts  -> (word_count, rounds, has_profile, has_filters)
# --------------------------------------------------------------------------- #
def _profile_data(**profile):
    return {core.PROFILE_DATA_KEY: profile}


class TestProfileFacts:
    def test_empty_data(self):
        assert core._profile_facts({}) == (0, 0, False, False)

    def test_none_data(self):
        assert core._profile_facts(None) == (0, 0, False, False)

    def test_word_count_and_has_profile(self):
        data = _profile_data(synthesized="one two three")
        words, rounds, has_profile, has_filters = core._profile_facts(data)
        assert words == 3
        assert has_profile is True
        assert has_filters is False

    def test_synthesized_stripped_whitespace_only_is_no_profile(self):
        data = _profile_data(synthesized="   ")
        assert core._profile_facts(data) == (0, 0, False, False)

    def test_chat_rounds_int_coerced_only_if_numeric(self):
        assert core._profile_facts(_profile_data(synthesized="x", chatRounds=4))[1] == 4
        assert core._profile_facts(_profile_data(synthesized="x", chatRounds=4.0))[1] == 4
        # non-numeric chatRounds → 0
        assert core._profile_facts(_profile_data(synthesized="x", chatRounds="lots"))[1] == 0
        assert core._profile_facts(_profile_data(synthesized="x", chatRounds=None))[1] == 0

    def test_has_filters_from_filtervalues(self):
        data = _profile_data(synthesized="x", filterValues=["stem"])
        assert core._profile_facts(data)[3] is True

    def test_has_filters_from_filtertags(self):
        data = _profile_data(synthesized="x", filterTags={"a": 1})
        assert core._profile_facts(data)[3] is True

    def test_has_filters_false_when_both_empty(self):
        data = _profile_data(synthesized="x", filterValues=[], filterTags=[])
        assert core._profile_facts(data)[3] is False

    def test_profile_stored_as_json_string(self):
        # AppStorage can hand round a JSON *string*; _json_obj tolerates it.
        data = {core.PROFILE_DATA_KEY: '{"synthesized": "a b c d"}'}
        assert core._profile_facts(data)[0] == 4


# --------------------------------------------------------------------------- #
# _tracker_facts  -> (actively_tracked_count, action_started)
# --------------------------------------------------------------------------- #
def _tracker_data(buckets, saved=None):
    data = {core.TRACKER_DATA_KEY: buckets}
    if saved is not None:
        data[core.TRACKER_SAVED_KEY] = saved
    return data


class TestTrackerFacts:
    def test_empty(self):
        assert core._tracker_facts({}) == (0, False)

    def test_counts_across_buckets(self):
        buckets = {
            "summerPrograms": [{"id": 1}, {"id": 2}],
            "internships": [{"id": 3}],
        }
        assert core._tracker_facts(_tracker_data(buckets)) == (2 + 1, False)

    def test_saved_for_later_excluded(self):
        buckets = {"summerPrograms": [{"id": 1}, {"id": 2}]}
        saved = {"1": True}  # id 1 explicitly parked
        assert core._tracker_facts(_tracker_data(buckets, saved)) == (1, False)

    def test_non_dict_items_skipped(self):
        buckets = {"internships": [{"id": 1}, "junk", None, 5]}
        assert core._tracker_facts(_tracker_data(buckets)) == (1, False)

    def test_ignores_unknown_buckets(self):
        buckets = {"notABucket": [{"id": 1}, {"id": 2}]}
        assert core._tracker_facts(_tracker_data(buckets)) == (0, False)

    @pytest.mark.parametrize("state,expected", [
        (None, False), ("", False), ("not_started", False),
        ("in_progress", True), ("completed", True),
    ])
    def test_action_started_state(self, state, expected):
        buckets = {"internships": [{"id": 1, "actionItems": [{"state": state}]}]}
        assert core._tracker_facts(_tracker_data(buckets))[1] is expected

    def test_action_items_non_dict_skipped(self):
        buckets = {"internships": [{"id": 1, "actionItems": ["x", None]}]}
        assert core._tracker_facts(_tracker_data(buckets))[1] is False

    def test_all_buckets_iterated(self):
        buckets = {b: [{"id": i}] for i, b in enumerate(core.TRACKER_BUCKETS)}
        count, _ = core._tracker_facts(_tracker_data(buckets))
        assert count == len(core.TRACKER_BUCKETS)


# --------------------------------------------------------------------------- #
# _stage_flags
# --------------------------------------------------------------------------- #
def _record(userid="U1", data=None, **extra):
    r = {"userid": userid, "data": data or {}}
    r.update(extra)
    return r


class TestStageFlags:
    def test_bare_account(self):
        flags = core._stage_flags(_record(data={}), set(), set())
        assert flags["signed_up"] is True
        assert flags["saved_data"] is False
        assert flags["has_profile"] is False
        assert flags["ran_search"] is False

    def test_saved_data_true_when_data_present(self):
        data = _profile_data(synthesized="hi")
        flags = core._stage_flags(_record(data=data), set(), set())
        assert flags["saved_data"] is True

    def test_meaningful_profile_at_threshold(self):
        text = " ".join(["w"] * core.PROFILE_SUFFICIENT_WORDS)  # exactly 20
        flags = core._stage_flags(_record(data=_profile_data(synthesized=text)), set(), set())
        assert flags["meaningful_profile"] is True

    def test_meaningful_profile_below_threshold(self):
        text = " ".join(["w"] * (core.PROFILE_SUFFICIENT_WORDS - 1))
        flags = core._stage_flags(_record(data=_profile_data(synthesized=text)), set(), set())
        assert flags["meaningful_profile"] is False

    def test_ran_search_via_searched_ids_proxy(self):
        # billed-call proxy: userid (lowercased) present in searched_ids.
        flags = core._stage_flags(_record(userid="ABC", data={}), {"abc"}, set())
        assert flags["ran_search"] is True

    def test_ran_search_via_has_filters_implication(self):
        # deliberately-incomplete proxy: no billed call, but filters present.
        data = _profile_data(synthesized="x", filterValues=["a"])
        flags = core._stage_flags(_record(data=data), set(), set())
        assert flags["ran_search"] is True

    def test_ran_search_via_tracked_implication(self):
        data = _tracker_data({"internships": [{"id": 1}]})
        flags = core._stage_flags(_record(data=data), set(), set())
        assert flags["ran_search"] is True

    def test_tracked_thresholds(self):
        buckets = {"internships": [{"id": i} for i in range(3)]}
        flags = core._stage_flags(_record(data=_tracker_data(buckets)), set(), set())
        assert flags["tracked_1"] is True
        assert flags["tracked_3"] is True

    def test_tracked_1_but_not_3(self):
        buckets = {"internships": [{"id": 1}]}
        flags = core._stage_flags(_record(data=_tracker_data(buckets)), set(), set())
        assert flags["tracked_1"] is True
        assert flags["tracked_3"] is False

    def test_userid_lowercased_stripped_for_matching(self):
        flags = core._stage_flags(_record(userid="  MixedCase  ", data={}),
                                  {"mixedcase"}, set())
        assert flags["ran_search"] is True

    def test_rich_profile_gate_needs_words_and_rounds_or_length(self):
        # >=20 words alone is NOT rich; needs >=100 words OR >=3 rounds.
        short = " ".join(["w"] * 25)
        flags = core._stage_flags(_record(data=_profile_data(synthesized=short)), set(), set())
        assert flags["_rich_profile"] is False

        rich_by_rounds = _profile_data(synthesized=short, chatRounds=3)
        flags = core._stage_flags(_record(data=rich_by_rounds), set(), set())
        assert flags["_rich_profile"] is True

        rich_by_words = _profile_data(synthesized=" ".join(["w"] * 100))
        flags = core._stage_flags(_record(data=rich_by_words), set(), set())
        assert flags["_rich_profile"] is True

    def test_rich_profile_false_below_20_even_with_rounds(self):
        # gate requires >=20 words AND (words>=100 OR rounds>=3); <20 words fails.
        data = _profile_data(synthesized="a b c", chatRounds=9)
        flags = core._stage_flags(_record(data=data), set(), set())
        assert flags["_rich_profile"] is False

    def test_returned_side_metric(self):
        flags = core._stage_flags(_record(userid="u1", data={}), set(), {"u1"})
        assert flags["_returned"] is True

    def test_google_side_metrics(self):
        rec = _record(data={}, google_id="g", google_calendar_connected_at="2026-01-01")
        flags = core._stage_flags(rec, set(), set())
        assert flags["_google_signup"] is True
        assert flags["_calendar"] is True


# --------------------------------------------------------------------------- #
# _cumulative_stage
# --------------------------------------------------------------------------- #
class TestCumulativeStage:
    def test_stage_zero_always_true(self):
        # Even an otherwise-empty flag dict: signed_up drives index 0.
        flags = {k: False for k in core.FUNNEL_STAGE_KEYS}
        flags["signed_up"] = True
        assert core._cumulative_stage(flags) == 0

    def test_stops_at_first_false(self):
        flags = {k: True for k in core.FUNNEL_STAGE_KEYS}
        flags["has_profile"] = False  # index 2
        # reached is the index BEFORE the first failed stage.
        assert core._cumulative_stage(flags) == 1

    def test_all_true_reaches_last_index(self):
        flags = {k: True for k in core.FUNNEL_STAGE_KEYS}
        assert core._cumulative_stage(flags) == len(core.FUNNEL_STAGE_KEYS) - 1

    def test_signed_up_false_still_zero(self):
        # break on the very first stage → reached stays at its initial 0.
        flags = {k: True for k in core.FUNNEL_STAGE_KEYS}
        flags["signed_up"] = False
        assert core._cumulative_stage(flags) == 0

    def test_cumulative_drop_invariant(self):
        # A later stage being true cannot rescue an earlier false one.
        flags = {k: True for k in core.FUNNEL_STAGE_KEYS}
        flags["meaningful_profile"] = False  # index 3
        reached = core._cumulative_stage(flags)
        assert reached == 2  # index before the first false
        # every stage strictly after the gap is ignored regardless of value
        assert reached < core.FUNNEL_STAGE_KEYS.index("meaningful_profile")


# --------------------------------------------------------------------------- #
# _week_start
# --------------------------------------------------------------------------- #
class TestWeekStart:
    def test_iso_monday_of_week(self):
        # 2026-08-23 is a Sunday; its ISO week Monday is 2026-08-17.
        d = _dt.date(2026, 8, 23)
        assert core._week_start(d) == "2026-08-17"

    def test_monday_maps_to_itself(self):
        d = _dt.date(2026, 8, 17)  # a Monday
        assert core._week_start(d) == "2026-08-17"

    def test_midweek(self):
        d = _dt.date(2026, 8, 19)  # Wednesday
        assert core._week_start(d) == "2026-08-17"
