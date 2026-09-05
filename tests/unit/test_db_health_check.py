"""Pure logic of the database health check: severity banding, run-status derivation, and the
run/age helpers. The Supabase reads and local-file reads are network/disk and are not exercised
here — only the pure functions that turn raw rows into the report's verdicts.
"""
import datetime

from wingman import db_health_check as d


def _iso(**delta):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(**delta)).isoformat()


# ---------- _sev: the queue/coverage band ----------

def test_sev_ok_below_warn():
    assert d._sev(5, 100, 300) == "ok"


def test_sev_warn_at_threshold():
    assert d._sev(100, 100, 300) == "warn"


def test_sev_alert_at_threshold():
    assert d._sev(300, 100, 300) == "alert"


def test_sev_none_is_ok_not_a_false_zero():
    # An unreadable count (None) must never read as a breach — it is "unavailable", not "0".
    assert d._sev(None, 1, 1) == "ok"


def test_sev_warn_only_metric_never_alerts():
    # Leads carry a warn threshold but no alert ceiling; a huge queue stays 'warn', never 'alert'.
    assert d._sev(10_000, 40, None) == "warn"


# ---------- _run_status: mirrors ops.core._run_status ----------

def test_run_status_never_for_missing_row():
    assert d._run_status(None) == "never"


def test_run_status_failed_when_errors():
    assert d._run_status({"errors": 3, "finished_at": _iso(minutes=1)}) == "failed"


def test_run_status_success_when_finished_no_errors():
    assert d._run_status({"errors": 0, "finished_at": _iso(minutes=1),
                          "started_at": _iso(minutes=2)}) == "success"


def test_run_status_running_when_recent_and_unfinished():
    assert d._run_status({"started_at": _iso(minutes=2)}) == "running"


def test_run_status_interrupted_when_old_and_unfinished():
    # Older than the 30-minute wedge window and never patched a finish → it vanished mid-pass.
    assert d._run_status({"started_at": _iso(hours=3)}) == "interrupted"


def test_run_status_interrupted_when_started_at_unparseable():
    assert d._run_status({"started_at": "not-a-date"}) == "interrupted"


# ---------- _shape_run / helpers ----------

def test_shape_run_none_passthrough():
    assert d._shape_run(None) is None


def test_shape_run_projects_counts_and_age():
    row = {"started_at": _iso(days=2), "finished_at": _iso(days=2), "errors": 0,
           "items_processed": 10, "items_updated": 4, "items_added": 1, "cost_usd": 0.5,
           "mode": "live"}
    shaped = d._shape_run(row)
    assert shaped["status"] == "success"
    assert shaped["items_processed"] == 10 and shaped["items_added"] == 1
    assert shaped["cost_usd"] == 0.5
    assert shaped["age_days"] is not None and shaped["age_days"] > 1.5


def test_age_days_none_for_missing_or_bad():
    assert d._age_days(None) is None
    assert d._age_days("nonsense") is None
    assert d._age_days(_iso(days=1)) > 0.5


def test_fmt_none_is_dash_and_thousands_grouped():
    assert d._fmt(None) == "—"
    assert d._fmt(1678) == "1,678"
