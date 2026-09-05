"""Unit tests for ops/core.py run-shaping, config, and field coercion, plus
ops/admin.py's `_qs_int` query-int helper.

Everything here is pure over injected dicts — no Supabase, no subprocess. The one
piece of hidden state is the wall clock inside `_run_status`, which is frozen by
monkeypatching the module's `datetime`. The functions under test are module-level
(several private) and imported directly from ops.core / ops.admin.
"""
import datetime as _dt

import pytest

import ops.core as core
from ops.admin import _qs_int


# --------------------------------------------------------------------------- #
# A frozen "now" for _run_status. ops.core does `import datetime` and calls
# datetime.datetime.now(tz), so we swap datetime.datetime for a subclass whose
# now() is fixed while fromisoformat() still works.
# --------------------------------------------------------------------------- #
FROZEN_NOW = _dt.datetime(2026, 8, 23, 12, 0, 0, tzinfo=_dt.timezone.utc)


class _FrozenDatetime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW if tz is None else FROZEN_NOW.astimezone(tz)


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(core.datetime, "datetime", _FrozenDatetime)
    return FROZEN_NOW


def _iso(dt):
    return dt.isoformat()


# --------------------------------------------------------------------------- #
# _parse_iso
# --------------------------------------------------------------------------- #
class TestParseIso:
    def test_none_returns_none(self):
        assert core._parse_iso(None) is None

    def test_empty_string_returns_none(self):
        assert core._parse_iso("") is None

    def test_malformed_returns_none(self):
        assert core._parse_iso("not-a-date") is None

    def test_trailing_z_becomes_offset(self):
        got = core._parse_iso("2026-08-23T12:00:00Z")
        assert got == _dt.datetime(2026, 8, 23, 12, 0, 0, tzinfo=_dt.timezone.utc)
        assert got.utcoffset() == _dt.timedelta(0)

    def test_plain_offset_parses(self):
        got = core._parse_iso("2026-08-23T12:00:00+00:00")
        assert got.tzinfo is not None


# --------------------------------------------------------------------------- #
# _agent_key_for
# --------------------------------------------------------------------------- #
class TestAgentKeyFor:
    @pytest.mark.parametrize("db_agent,expected", [
        ("scraper", "scraper"),
        ("metadata_refresher", "metadata"),
        ("deadline_checker", "deadline"),
        ("review_checker", "reviews"),
        ("mailing_list_finder", "mailinglist"),
        ("link_checker", "links"),
    ])
    def test_known_literals_map_to_console_key(self, db_agent, expected):
        assert core._agent_key_for(db_agent) == expected

    def test_unknown_literal_falls_through_to_raw(self):
        # A script with no console card (e.g. a one-off backfill) keeps its raw literal
        # rather than vanishing from the activity log.
        assert core._agent_key_for("subject_tags_backfill") == "subject_tags_backfill"

    def test_interactive_literal_falls_through(self):
        assert core._agent_key_for("interactive_gemini") == "interactive_gemini"


# --------------------------------------------------------------------------- #
# _run_status  (clock frozen)
# --------------------------------------------------------------------------- #
class TestRunStatus:
    def test_errors_truthy_is_failed(self, frozen_clock):
        assert core._run_status({"errors": 3}) == "failed"

    def test_errors_short_circuit_even_with_finished_at(self, frozen_clock):
        # errors wins over a present finished_at.
        row = {"errors": 1, "finished_at": _iso(FROZEN_NOW)}
        assert core._run_status(row) == "failed"

    def test_errors_zero_is_not_failed(self, frozen_clock):
        # 0 is falsy → not failed; finished_at present → success.
        row = {"errors": 0, "finished_at": _iso(FROZEN_NOW)}
        assert core._run_status(row) == "success"

    def test_finished_at_is_success(self, frozen_clock):
        assert core._run_status({"finished_at": _iso(FROZEN_NOW)}) == "success"

    def test_recent_unfinished_is_running(self, frozen_clock):
        # started 60s ago, no finish, under the 3600s timeout → running.
        started = FROZEN_NOW - _dt.timedelta(seconds=60)
        assert core._run_status({"started_at": _iso(started)}) == "running"

    def test_old_unfinished_is_interrupted(self, frozen_clock):
        # started well past the timeout with no finish → interrupted.
        started = FROZEN_NOW - _dt.timedelta(seconds=core.AGENT_RUN_TIMEOUT_SECS + 60)
        assert core._run_status({"started_at": _iso(started)}) == "interrupted"

    def test_boundary_at_timeout_is_interrupted(self, frozen_clock):
        # age == AGENT_RUN_TIMEOUT_SECS is NOT < timeout → interrupted.
        started = FROZEN_NOW - _dt.timedelta(seconds=core.AGENT_RUN_TIMEOUT_SECS)
        assert core._run_status({"started_at": _iso(started)}) == "interrupted"

    def test_missing_started_at_is_interrupted(self, frozen_clock):
        # No started_at at all and no finish → interrupted (cannot be dated).
        assert core._run_status({}) == "interrupted"

    def test_unparseable_started_at_is_interrupted(self, frozen_clock):
        assert core._run_status({"started_at": "garbage"}) == "interrupted"


# --------------------------------------------------------------------------- #
# _shape_run
# --------------------------------------------------------------------------- #
class TestShapeRun:
    def test_known_agent_shape(self, frozen_clock):
        row = {
            "id": 7, "agent": "link_checker",
            "started_at": _iso(FROZEN_NOW - _dt.timedelta(seconds=30)),
            "finished_at": _iso(FROZEN_NOW),
            "items_processed": 100, "items_updated": 5, "items_added": None,
            "errors": None, "cost_usd": 0.0, "mode": "live",
        }
        out = core._shape_run(row)
        assert out["agent"] == "links"
        assert out["name"] == "Link Checker"
        assert out["known_agent"] is True
        assert out["unit"] == "rows"
        assert out["status"] == "success"
        assert out["duration_seconds"] == 30.0
        assert out["dry_run"] is False
        assert out["interactive"] is False
        # `or 0` coalescing on count fields.
        assert out["items_added"] == 0
        assert out["errors"] == 0

    def test_interactive_agent_branch(self, frozen_clock):
        # agent not in AGENT_CONFIGS_SCHEMA but in INTERACTIVE_AGENTS → name from map,
        # unit "calls", interactive True, known_agent False.
        row = {"id": 1, "agent": "interactive_gemini",
               "started_at": None, "finished_at": None}
        out = core._shape_run(row)
        assert out["name"] == core.INTERACTIVE_AGENTS["interactive_gemini"]
        assert out["unit"] == "calls"
        assert out["interactive"] is True
        assert out["known_agent"] is False

    def test_dryrun_mode_suffix(self, frozen_clock):
        row = {"agent": "scraper", "mode": "national-dryrun"}
        assert core._shape_run(row)["dry_run"] is True

    def test_duration_none_when_one_timestamp_missing(self, frozen_clock):
        row = {"agent": "scraper", "started_at": _iso(FROZEN_NOW), "finished_at": None}
        assert core._shape_run(row)["duration_seconds"] is None

    def test_unknown_agent_name_falls_back_to_literal(self, frozen_clock):
        row = {"agent": "subject_tags_backfill"}
        out = core._shape_run(row)
        # name falls back to the raw agent literal; unit falls back to "items".
        assert out["name"] == "subject_tags_backfill"
        assert out["unit"] == "items"
        assert out["known_agent"] is False


# --------------------------------------------------------------------------- #
# build_agent_args
# --------------------------------------------------------------------------- #
def _clear_overrides(monkeypatch):
    """Force agent_defaults() to return only built-ins (no saved override file)."""
    monkeypatch.setattr(core, "load_agent_settings", lambda: {})


class TestBuildAgentArgs:
    @pytest.fixture(autouse=True)
    def _no_overrides(self, monkeypatch):
        _clear_overrides(monkeypatch)

    def _tail_after_script(self, args, agent):
        # args[0]=python, [1]=-u, [2]=script; return the flag portion.
        assert args[2] == core.AGENT_CONFIGS_SCHEMA[agent]["script"]
        return args[3:]

    # -- metadata --------------------------------------------------------- #
    def test_metadata_sample_default_size(self):
        args = core.build_agent_args("metadata", {"scope": "sample"})
        assert "--sample" in args and "50" in args

    def test_metadata_sample_custom_size(self):
        args = core.build_agent_args("metadata", {"scope": "sample", "sampleSize": "12"})
        assert args[args.index("--sample") + 1] == "12"

    def test_metadata_all_when_not_sample(self):
        args = core.build_agent_args("metadata", {"scope": "all"})
        assert "--all" in args and "--sample" not in args

    def test_metadata_exclude_source(self):
        args = core.build_agent_args("metadata", {"scope": "all", "excludeSource": "scraper-x"})
        assert args[args.index("--exclude-source") + 1] == "scraper-x"

    def test_metadata_awaiting_drains_the_queue(self):
        args = core.build_agent_args("metadata", {"scope": "awaiting"})
        assert "--awaiting-refresh" in args
        assert "--all" not in args and "--sample" not in args

    def test_metadata_awaiting_ignores_exclude_source(self):
        # --exclude-source does not apply in awaiting mode and must not be forwarded.
        args = core.build_agent_args("metadata",
                                     {"scope": "awaiting", "excludeSource": "scraper-x"})
        assert "--awaiting-refresh" in args and "--exclude-source" not in args

    # -- reviews ---------------------------------------------------------- #
    def test_reviews_sample(self):
        args = core.build_agent_args("reviews", {"scope": "sample"})
        assert "--sample" in args

    def test_reviews_all(self):
        args = core.build_agent_args("reviews", {"scope": "all"})
        assert "--all" in args

    def test_reviews_default_scope_no_flag(self):
        # neither sample nor all → stale-only default, no scope flag.
        args = core.build_agent_args("reviews", {})
        assert "--all" not in args and "--sample" not in args

    def test_reviews_force(self):
        args = core.build_agent_args("reviews", {"scope": "all", "force": True})
        assert "--force" in args

    # -- scraper ---------------------------------------------------------- #
    def test_scraper_default_mode_national(self):
        args = core.build_agent_args("scraper", {})
        assert args[args.index("--mode") + 1] == "national"

    def test_scraper_mode_seattle_seedids_maxsearches(self):
        args = core.build_agent_args(
            "scraper", {"mode": "seattle", "seedIds": "1,2,3", "maxSearches": "2"})
        assert args[args.index("--mode") + 1] == "seattle"
        assert args[args.index("--seed-ids") + 1] == "1,2,3"
        assert args[args.index("--max-searches") + 1] == "2"

    def test_scraper_no_maxsearches_when_zero(self):
        # _int_or_none("0") -> 0 is falsy → flag omitted.
        args = core.build_agent_args("scraper", {"maxSearches": "0"})
        assert "--max-searches" not in args

    # -- deadline --------------------------------------------------------- #
    def test_deadline_sample(self):
        args = core.build_agent_args("deadline", {"scope": "sample", "sampleSize": "7"})
        assert args[args.index("--sample") + 1] == "7"

    def test_deadline_all_default(self):
        args = core.build_agent_args("deadline", {})
        assert "--all" in args

    # -- mailinglist ------------------------------------------------------ #
    def test_mailinglist_ids_beats_all(self):
        args = core.build_agent_args("mailinglist", {"ids": "a,b"})
        assert args[args.index("--ids") + 1] == "a,b"
        assert "--all" not in args

    def test_mailinglist_all_default(self):
        args = core.build_agent_args("mailinglist", {})
        assert "--all" in args

    def test_mailinglist_limit_and_force(self):
        args = core.build_agent_args("mailinglist", {"limit": "25", "force": True})
        assert args[args.index("--limit") + 1] == "25"
        assert "--force" in args

    # -- links ------------------------------------------------------------ #
    def test_links_default_all(self):
        args = core.build_agent_args("links", {})
        assert "--all" in args

    def test_links_sample_default_size_100(self):
        # links' sample default is 100, not 50.
        args = core.build_agent_args("links", {"scope": "sample"})
        assert args[args.index("--sample") + 1] == "100"

    def test_links_ids_beats_scope(self):
        args = core.build_agent_args("links", {"ids": "x,y", "scope": "all"})
        assert args[args.index("--ids") + 1] == "x,y"
        assert "--all" not in args

    def test_links_flagged_uses_repair_flagged(self):
        args = core.build_agent_args("links", {"scope": "flagged"})
        assert "--repair-flagged" in args
        assert "--all" not in args and "--sample" not in args

    def test_links_flagged_excludes_force(self):
        # --repair-flagged reads inactive rows; --force means nothing and is dropped.
        args = core.build_agent_args("links", {"scope": "flagged", "force": True})
        assert "--repair-flagged" in args
        assert "--force" not in args

    def test_links_force_applies_when_not_flagged(self):
        args = core.build_agent_args("links", {"scope": "all", "force": True})
        assert "--force" in args

    def test_links_no_repair_and_flag_only(self):
        args = core.build_agent_args("links", {"noRepair": True, "flagOnly": True})
        assert "--no-repair" in args
        assert "--flag-only" in args

    def test_links_workers(self):
        args = core.build_agent_args("links", {"workers": "4"})
        assert args[args.index("--workers") + 1] == "4"

    # -- timing precedence ------------------------------------------------ #
    def test_timing_config_over_defaults(self):
        # explicit minDelay/timeout in config win over the built-in defaults.
        args = core.build_agent_args("metadata", {"scope": "all", "minDelay": "9", "timeout": "77"})
        assert args[args.index("--min-delay") + 1] == "9"
        assert args[args.index("--timeout") + 1] == "77"

    def test_timing_defaults_when_config_absent(self):
        # metadata built-in defaults: min_delay 5, timeout 120.
        args = core.build_agent_args("metadata", {"scope": "all"})
        assert args[args.index("--min-delay") + 1] == "5"
        assert args[args.index("--timeout") + 1] == "120"

    def test_min_delay_zero_string_is_honoured(self):
        # minDelay "" is skipped, but explicit non-empty (even "0") is passed.
        args = core.build_agent_args("links", {"minDelay": "0"})
        assert args[args.index("--min-delay") + 1] == "0"

    # -- preview vs dry-run mutual exclusion ------------------------------ #
    def test_preview_flag(self):
        args = core.build_agent_args("metadata", {"scope": "all", "dryRun": True}, preview=True)
        # preview wins; dry-run is NOT also appended.
        assert "--preview" in args
        assert "--dry-run" not in args

    def test_dry_run_flag_when_not_preview(self):
        args = core.build_agent_args("metadata", {"scope": "all", "dryRun": True})
        assert "--dry-run" in args
        assert "--preview" not in args

    def test_no_run_tier_flag_by_default(self):
        args = core.build_agent_args("metadata", {"scope": "all"})
        assert "--preview" not in args and "--dry-run" not in args


# --------------------------------------------------------------------------- #
# _coerce_field
# --------------------------------------------------------------------------- #
class TestCoerceFieldInt:
    @pytest.mark.parametrize("value", [None, "", "—"])
    def test_blank_sentinels_to_none(self, value):
        assert core._coerce_field("grade", "int", value) is None

    def test_valid_int_string(self):
        assert core._coerce_field("grade", "int", "  9 ") == 9

    def test_valid_int_number(self):
        assert core._coerce_field("grade", "int", 13) == 13

    @pytest.mark.parametrize("value", [0, 14, "0", "14"])
    def test_out_of_range_raises(self, value):
        with pytest.raises(ValueError):
            core._coerce_field("grade", "int", value)

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            core._coerce_field("grade", "int", "nine")


class TestCoerceFieldList:
    def test_comma_split_string(self):
        assert core._coerce_field("tags", "list", "a, b ,c") == ["a", "b", "c"]

    def test_empty_string_to_none(self):
        assert core._coerce_field("tags", "list", "") is None

    def test_none_to_none(self):
        assert core._coerce_field("tags", "list", None) is None

    def test_empties_dropped_to_none(self):
        assert core._coerce_field("tags", "list", " , ,") is None

    def test_list_input_stripped(self):
        assert core._coerce_field("tags", "list", [" x ", "", "y"]) == ["x", "y"]

    def test_non_list_non_string_raises(self):
        with pytest.raises(ValueError):
            core._coerce_field("tags", "list", 5)


class TestCoerceFieldText:
    def test_strip_to_value(self):
        assert core._coerce_field("name", "text", "  hi ") == "hi"

    def test_empty_after_strip_to_none(self):
        assert core._coerce_field("name", "text", "   ") is None

    def test_none_to_none(self):
        assert core._coerce_field("name", "text", None) is None

    def test_number_coerced_to_str(self):
        assert core._coerce_field("name", "text", 42) == "42"


# --------------------------------------------------------------------------- #
# _int_or_none
# --------------------------------------------------------------------------- #
class TestIntOrNone:
    @pytest.mark.parametrize("value", [None, "", False])
    def test_falsy_sentinels_to_none(self, value):
        assert core._int_or_none(value) is None

    def test_float_string_accepted(self):
        assert core._int_or_none("5.0") == 5

    def test_int_string(self):
        assert core._int_or_none("42") == 42

    def test_junk_to_none(self):
        assert core._int_or_none("abc") is None

    def test_zero_string_is_zero(self):
        # "0" is not in the falsy-sentinel set; float("0")->0.
        assert core._int_or_none("0") == 0


# --------------------------------------------------------------------------- #
# ops.admin._qs_int
# --------------------------------------------------------------------------- #
class _StubRequest:
    """Minimal stand-in exposing `.query_params.get(key)` like a real Request."""
    def __init__(self, params):
        self.query_params = params


class TestQsInt:
    def test_valid_int(self):
        req = _StubRequest({"limit": "25"})
        assert _qs_int(req, "limit", 10) == 25

    def test_missing_key_returns_default(self):
        req = _StubRequest({})
        assert _qs_int(req, "limit", 10) == 10

    def test_non_numeric_returns_default(self):
        req = _StubRequest({"limit": "abc"})
        assert _qs_int(req, "limit", 10) == 10

    def test_default_none_path(self):
        req = _StubRequest({})
        assert _qs_int(req, "days", None) is None

    def test_clamp_low(self):
        req = _StubRequest({"limit": "-5"})
        assert _qs_int(req, "limit", 10, lo=1) == 1

    def test_clamp_high(self):
        req = _StubRequest({"limit": "9999"})
        assert _qs_int(req, "limit", 10, hi=500) == 500

    def test_clamp_within_bounds_unchanged(self):
        req = _StubRequest({"limit": "42"})
        assert _qs_int(req, "limit", 10, lo=1, hi=500) == 42

    def test_clamp_order_lo_then_hi(self):
        # value below lo is raised to lo, which sits within hi.
        req = _StubRequest({"limit": "0"})
        assert _qs_int(req, "limit", 10, lo=5, hi=500) == 5


# --------------------------------------------------------------------------- #
# _moderation_updates — the PATCH body for a human verdict, reason included.
# --------------------------------------------------------------------------- #
NOW = "2026-08-25T12:00:00+00:00"


def test_moderation_updates_rejected_stores_reason_and_deactivates():
    u = core._moderation_updates("rejected", "", "third-party-url: lumiere listicle", NOW)
    assert u["moderation_reason"] == "third-party-url: lumiere listicle"
    assert u["moderation_status"] == "rejected"
    assert u["is_active"] is False
    assert u["duplicate_of"] is None


def test_moderation_updates_restore_clears_reason_and_pointer():
    # Restoring must clear the old why, or it keeps explaining a dead verdict.
    u = core._moderation_updates("pending_review", "", "stale reason", NOW)
    assert u["moderation_reason"] is None
    assert u["duplicate_of"] is None
    assert "is_active" not in u          # restore does not touch visibility


def test_moderation_updates_duplicate_defaults_reason_from_survivor():
    u = core._moderation_updates("duplicate", "ec17096", None, NOW)
    assert u["moderation_reason"] == "duplicate: superseded by ec17096"
    assert u["duplicate_of"] == "ec17096"
    assert u["is_active"] is False


def test_moderation_updates_duplicate_explicit_reason_wins():
    u = core._moderation_updates("duplicate", "ec17096", "duplicate: kept the deeper URL", NOW)
    assert u["moderation_reason"] == "duplicate: kept the deeper URL"


def test_moderation_updates_reason_is_capped():
    u = core._moderation_updates("rejected", "", "x" * 2000, NOW)
    assert len(u["moderation_reason"]) == core.MODERATION_REASON_MAX_LEN


def test_moderation_updates_suspected_duplicate_stays_live():
    # Flag-in-place: keeps the survivor pointer and reason, but must NOT deactivate — the
    # whole point is the row stays visible to students until a human decides.
    u = core._moderation_updates("suspected_duplicate", "ec17096", "dedupe sweep: 92% name", NOW)
    assert u["moderation_status"] == "suspected_duplicate"
    assert u["duplicate_of"] == "ec17096"
    assert u["moderation_reason"] == "dedupe sweep: 92% name"
    assert "is_active" not in u          # NEVER hidden on a suspicion


def test_moderation_updates_suspected_duplicate_no_survivor_is_allowed():
    # The sweep may flag before a survivor is settled; pointer just stays empty.
    u = core._moderation_updates("suspected_duplicate", "", None, NOW)
    assert u["moderation_status"] == "suspected_duplicate"
    assert u["duplicate_of"] is None
    assert "is_active" not in u


def test_pick_survivor_prefers_approved_then_deeper_url():
    bare = {"id": "a", "is_active": True, "url": "https://x.com/", "moderation_status": None}
    good = {"id": "b", "is_active": True, "url": "https://x.com/programs/deep",
            "moderation_status": "approved"}
    keep, flag = core._pick_survivor(bare, good)
    assert keep["id"] == "b" and flag["id"] == "a"
    # symmetric — order of args must not change the verdict
    keep2, flag2 = core._pick_survivor(good, bare)
    assert keep2["id"] == "b" and flag2["id"] == "a"


def test_pick_survivor_tie_keeps_first():
    a = {"id": "a", "is_active": True, "url": "https://x.com/p", "moderation_status": None}
    b = {"id": "b", "is_active": True, "url": "https://x.com/q", "moderation_status": None}
    keep, flag = core._pick_survivor(a, b)
    assert keep["id"] == "a" and flag["id"] == "b"


def test_flag_suspected_pairs_rejects_incomplete():
    assert core.flag_suspected_duplicate_pairs([]) == {"ok": False, "error": "No pairs to flag."}
    r = core.flag_suspected_duplicate_pairs([{"id": "", "duplicate_of": "b"}])
    assert r["ok"] is False and r["errors"] == 1


def test_status_groupings_are_consistent():
    # suspected_duplicate is a valid status, is flag-in-place (not adjudicated), and is kept
    # out of the default queue slice so it cannot show up twice.
    assert "suspected_duplicate" in core.MODERATION_STATUSES
    assert "suspected_duplicate" in core.FLAGGED_STATUSES
    assert "suspected_duplicate" not in core.ADJUDICATED_STATUSES
    assert "suspected_duplicate" not in core.QUEUE_STATUSES
    assert set(core.QUEUE_STATUSES) == {"pending_review", "approved"}


def test_prefilled_ids_matches_scraper_and_hub_sources(monkeypatch):
    # Scraper and hub-miner rows now arrive prefilled with refresh_opportunities' own
    # extraction (2026-08-30) and must not be queued for it; everything else still might be
    # thin and should still queue.
    rows = [
        {"id": "1", "source": "scraper-national-20260830"},
        {"id": "2", "source": "hub-cmu.edu-20260827"},
        {"id": "3", "source": "user-submitted"},
        {"id": "4", "source": "wingman-seed"},
        {"id": "5", "source": None},
    ]
    monkeypatch.setattr(core, "_supabase_request", lambda *a, **k: rows)
    assert core._prefilled_ids(["1", "2", "3", "4", "5"]) == {"1", "2"}


def test_prefilled_ids_empty_on_lookup_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(core, "_supabase_request", boom)
    # Safe direction: a failed lookup queues everyone (empty prefilled set), never silently
    # skips a row that might actually need refreshing.
    assert core._prefilled_ids(["1", "2"]) == set()


def test_prefilled_ids_empty_for_no_ids():
    assert core._prefilled_ids([]) == set()
    assert core._prefilled_ids(None) == set()


# --------------------------------------------------------------------------- #
# Maintenance tools — the runnable standalone scripts in the Run view. Pure over
# the registry + argv builder; no subprocess is launched here.
# --------------------------------------------------------------------------- #
class TestMaintenanceTools:
    def test_public_registry_hides_script_paths(self):
        pub = core.maintenance_tools_public()
        assert set(pub) == set(core.MAINTENANCE_TOOLS)
        for entry in pub.values():
            assert "script" not in entry
            assert {"name", "description", "free", "writes", "params"} <= set(entry)

    def test_every_tool_script_exists(self):
        import os
        for key, cfg in core.MAINTENANCE_TOOLS.items():
            path = os.path.join(core.REPO_ROOT, cfg["script"])
            assert os.path.exists(path), f"{key} -> missing {cfg['script']}"

    def test_inspect_accepts_commas_or_spaces(self):
        args = core.build_tool_args("inspect", {"ids": "ec1, ec2 ec3"})
        assert args[2:] == ["-m", "agents.check_opp_data", "ec1", "ec2", "ec3"]

    def test_contactemail_all_and_force(self):
        args = core.build_tool_args("contactemail", {"scope": "all", "force": True})
        assert args[2:] == ["-m", "agents.find_contact_emails", "--all", "--force"]

    def test_contactemail_ids_limit_dryrun(self):
        args = core.build_tool_args(
            "contactemail", {"scope": "ids", "ids": "ec9", "limit": "5", "dryRun": True})
        assert args[2:] == ["-m", "agents.find_contact_emails", "--ids", "ec9", "--limit", "5", "--dry-run"]

    def test_contactemail_defaults_to_all_without_ids(self):
        # scope=ids but no ids given must not emit a bare --ids; falls back to --all.
        args = core.build_tool_args("contactemail", {"scope": "ids"})
        assert "--all" in args and "--ids" not in args

    def test_fixed_args_and_no_params(self):
        assert core.build_tool_args("mlgrader", {})[2:] == ["-m", "agents.grade_mailing_lists", "--sample"]
        assert core.build_tool_args("export", {})[2:] == ["-m", "agents.export_json"]

    def test_paid_tools(self):
        # The paid tools: the contact-email backfill, the dead-link re-finder, hub mining (its
        # extraction call), name harvesting (a search per name), the queue classifier and queue
        # embedder (both call Gemini), and the dedupe-embedding backfill (build_catalog_embeddings,
        # a paid embed). Angle proposing and every PREVIEW are free. Pinned so a new tool cannot
        # quietly join the list that spends.
        paid = {k for k, c in core.MAINTENANCE_TOOLS.items() if not c.get("free")}
        assert paid == {"contactemail", "refind", "minehub", "harvestnames",
                        "classifyqueue", "dedupequeue", "embedindex"}

    def test_harvestnames_args(self):
        # Operator-pointed only — the router never sends work here. Free preview by default;
        # a paid run drops --preview, and the name cap is a spend ceiling.
        prev = core.build_tool_args("harvestnames", {"url": "https://x.edu/list"})
        assert prev[2:] == ["-m", "agents.harvest_names", "--hubs", "https://x.edu/list", "--preview"]
        run = core.build_tool_args("harvestnames", {"url": "https://x.edu/list", "mode": "run",
                                                    "maxNames": "8"})
        assert "--preview" not in run and run[-2:] == ["--max-names", "8"]

    def test_minehub_args(self):
        # Needs a url; defaults to the free preview; a paid run drops --preview.
        prev = core.build_tool_args("minehub", {"url": "https://x.edu/programs"})
        assert prev[2:] == ["-m", "agents.mine_hub_pages", "--hubs", "https://x.edu/programs", "--preview"]
        run = core.build_tool_args("minehub", {"url": "https://x.edu/programs", "mode": "run",
                                               "offDomain": True})
        assert "--preview" not in run and "--off-domain" in run and "--hubs" in run

    def test_proposeangles_args(self):
        assert core.build_tool_args("proposeangles", {})[2:] == [
            "-m", "agents.propose_angles", "--mode", "national", "--preview"]
        commit = core.build_tool_args("proposeangles", {"mode": "seattle", "action": "commit"})
        assert commit[2:] == ["-m", "agents.propose_angles", "--mode", "seattle", "--commit"]

    def test_refind_defaults_to_free_preview(self):
        # A paid run must be chosen explicitly; anything else previews (free, no writes).
        assert core.build_tool_args("refind", {})[2:] == ["-m", "agents.refind_dead_links", "--preview"]
        assert core.build_tool_args("refind", {"mode": "preview"})[-1] == "--preview"
        run = core.build_tool_args("refind", {"mode": "run", "limit": 15})
        assert run[2:] == ["-m", "agents.refind_dead_links", "--limit", "15"]
        # missing limit on a paid run falls back to the script's own default of 20.
        assert core.build_tool_args("refind", {"mode": "run"})[-1] == "20"
