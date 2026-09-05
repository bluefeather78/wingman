"""Dry-run snapshots mirrored to a table — PRODUCTION_READINESS_PLAN.md Phase 4.

agents_report finding 4.20 lists "the 10 snapshot families (commit path)" among the state that
exists only on the operator's machine, so a second checkout is a different pipeline.

A snapshot is not a log. A `--dry-run` SKIPS the database writes but STILL CALLS THE PAID API
AT FULL COST, which is the entire reason wingman/dryrun_common.py exists: it replays a snapshot
instead of paying to run the agent again. Leaving the only copy in a gitignored file on one
laptop meant that money could not be redeemed anywhere else.

What these pin:

  * a snapshot published from machine A can be listed and COMMITTED on machine B.
  * a materialised snapshot goes through the ordinary path. resolve() writes it to its real
    FILENAME first, because the filename is where the run's timestamp comes from (audit 4.5)
    and everything downstream — the stamp a patch writes, the STALE_DAYS refusal — is derived
    from it. A mirror that re-derived any of that would be the very defect 4.5 is about.
  * the local file always wins and is never overwritten. It is the original; clobbering it with
    a re-serialised copy would be a silent edit to evidence.
  * listing does NOT download payloads, and does not cost a Supabase round trip per console
    poll.
  * no table means local-only, exactly as today.
"""
import json
import os

import pytest

from wingman import dryrun_common as dc


SCRAPER_FILE = "scrape_review_full_20260905-141233.json"
PAYLOAD = {"inserted": [{"url": "https://a.edu/p", "name": "A"}],
           "rejected": [{"url": "https://b.edu/p", "reason": "hub"}]}


class _FakeSupabase:
    def __init__(self):
        self.rows = {}
        self.gets = 0
        self.selects = []

    def get(self, url, table, params, key, page_size=1000, order_by="id"):
        assert table == "agent_snapshots"
        self.gets += 1
        self.selects.append(params.get("select", ""))
        rows = list(self.rows.values())
        if "file" in params:
            wanted = params["file"].split("eq.", 1)[-1]
            rows = [r for r in rows if r["file"] == wanted]
        select = params.get("select", "")
        if "payload" not in select:
            rows = [{k: v for k, v in r.items() if k != "payload"} for r in rows]
        return [dict(r) for r in rows]

    def post(self, url, table, rows, key, on_conflict=None, batch_size=500):
        assert on_conflict == "file", "a snapshot's identity is its filename"
        for row in rows:
            self.rows[row["file"]] = dict(row)


@pytest.fixture
def mirror(monkeypatch, tmp_path):
    """The table backend, with REPO_DIR pointed at a scratch directory."""
    from wingman import supabase_common
    fake = _FakeSupabase()
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    monkeypatch.setattr(dc, "_mirror_creds",
                        lambda: ("https://example.supabase.co", "service-key"))
    monkeypatch.setattr(supabase_common, "supabase_get", fake.get)
    monkeypatch.setattr(supabase_common, "supabase_post", fake.post)
    dc._reset_mirror_for_tests()
    yield fake
    dc._reset_mirror_for_tests()


@pytest.fixture
def no_mirror(monkeypatch, tmp_path):
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    monkeypatch.setattr(dc, "_mirror_creds", lambda: (None, None))
    dc._reset_mirror_for_tests()
    yield
    dc._reset_mirror_for_tests()


def _write_local(payload=None, name=SCRAPER_FILE):
    path = os.path.join(dc.REPO_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload if payload is not None else PAYLOAD, f)
    return path


# ---------- the finding ----------

def test_a_snapshot_published_on_one_machine_is_committable_on_another(mirror):
    """Machine A dry-runs and publishes; machine B has no file at all and can still commit."""
    _write_local()
    assert dc.publish_snapshot(SCRAPER_FILE) is True

    os.remove(os.path.join(dc.REPO_DIR, SCRAPER_FILE))       # machine B: no local copy
    agent, path = dc.resolve(SCRAPER_FILE)
    assert agent == "scraper"
    assert os.path.isfile(path)
    assert dc._load(path) == PAYLOAD["inserted"]


def test_a_materialised_snapshot_keeps_its_filename_so_the_run_time_survives(mirror):
    """The freshness stamp a patch writes comes from the FILENAME, never `now` (audit 4.5).
    A mirror that landed the payload under any other name would break that."""
    _write_local()
    dc.publish_snapshot(SCRAPER_FILE)
    os.remove(os.path.join(dc.REPO_DIR, SCRAPER_FILE))
    _, path = dc.resolve(SCRAPER_FILE)
    assert os.path.basename(path) == SCRAPER_FILE
    assert dc._run_date(os.path.basename(path)).isoformat().startswith("2026-09-05T14:12:33")


def test_the_rejected_half_survives_the_round_trip(mirror):
    """`rejected` exists so a run's discards can be audited. Normalising the payload down to
    the committable entries on the way in would silently throw it away."""
    _write_local()
    dc.publish_snapshot(SCRAPER_FILE)
    os.remove(os.path.join(dc.REPO_DIR, SCRAPER_FILE))
    _, path = dc.resolve(SCRAPER_FILE)
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == PAYLOAD


def test_a_remote_snapshot_is_listed_with_the_local_ones(mirror):
    _write_local()
    dc.publish_snapshot(SCRAPER_FILE)
    os.remove(os.path.join(dc.REPO_DIR, SCRAPER_FILE))
    listed = [s for s in dc.list_snapshots() if s["file"] == SCRAPER_FILE]
    assert len(listed) == 1
    assert listed[0]["remote"] is True
    assert listed[0]["agent"] == "scraper"


def test_a_remote_only_snapshot_reports_unknown_counts_not_zero(mirror):
    """Listing must not download every payload on every console poll, so the counts are not
    known — and reporting 0 would read as "this snapshot is empty"."""
    _write_local()
    dc.publish_snapshot(SCRAPER_FILE)
    os.remove(os.path.join(dc.REPO_DIR, SCRAPER_FILE))
    listed = [s for s in dc.list_snapshots() if s["file"] == SCRAPER_FILE][0]
    assert listed["entries"] is None and listed["pending"] is None


def test_listing_does_not_download_payloads(mirror):
    """One scraper snapshot can be megabytes. Pulling every payload to render a list would
    make the console's own poll the most expensive request in the system."""
    _write_local()
    dc.publish_snapshot(SCRAPER_FILE)
    os.remove(os.path.join(dc.REPO_DIR, SCRAPER_FILE))
    mirror.selects.clear()
    dc.list_snapshots()
    assert mirror.selects, "the listing made no remote read at all"
    assert all("payload" not in sel for sel in mirror.selects)


def test_a_local_snapshot_is_not_listed_twice_when_it_is_also_mirrored(mirror):
    _write_local()
    dc.publish_snapshot(SCRAPER_FILE)
    listed = [s for s in dc.list_snapshots() if s["file"] == SCRAPER_FILE]
    assert len(listed) == 1
    assert not listed[0].get("remote"), "the local copy is the one with real counts"
    assert listed[0]["entries"] == 1


# ---------- the local file wins ----------

def test_a_local_file_is_never_overwritten_by_the_mirror(mirror):
    """The local copy is the original. Clobbering it with a re-serialised one is a silent
    edit to evidence a commit is about to be made from."""
    _write_local()
    dc.publish_snapshot(SCRAPER_FILE)
    _write_local({"inserted": [{"url": "https://local.edu/p", "name": "LOCAL"}]})
    _, path = dc.resolve(SCRAPER_FILE)
    assert dc._load(path)[0]["name"] == "LOCAL"


def test_resolve_still_refuses_a_path_traversal(mirror):
    """resolve() is reached from an HTTP handler and the value names a file to open."""
    for bad in ("../.env", "sub/dir.json", "", None, "not_a_snapshot.json"):
        assert dc.resolve(bad) == (None, None)


def test_fetch_refuses_a_name_that_matches_no_snapshot_family(mirror):
    mirror.rows["secrets.json"] = {"file": "secrets.json", "agent": "scraper",
                                   "payload": {"x": 1}}
    assert dc.fetch_snapshot("secrets.json") is None
    assert not os.path.exists(os.path.join(dc.REPO_DIR, "secrets.json"))


# ---------- syncing ----------

def test_sync_publishes_local_snapshots_the_mirror_lacks(mirror):
    _write_local()
    _write_local(name="deadline_check_dry_run_20260904-101010.json",
                 payload=[{"url": "https://c.edu/p", "changed": True}])
    assert dc.sync_snapshots(force=True) == 2
    assert set(mirror.rows) == {SCRAPER_FILE, "deadline_check_dry_run_20260904-101010.json"}
    assert dc.sync_snapshots(force=True) == 0, "it re-published what was already there"


def test_sync_is_throttled_so_a_console_poll_does_not_re_read_every_file(mirror):
    _write_local()
    assert dc.sync_snapshots() == 1
    _write_local(name="deadline_check_dry_run_20260904-101010.json", payload=[])
    assert dc.sync_snapshots() == 0, "the throttle did not hold"
    assert dc.sync_snapshots(force=True) == 1


def test_the_remote_listing_is_cached_between_polls(mirror):
    _write_local()
    dc.list_snapshots()
    before = mirror.gets
    for _ in range(5):
        dc.list_snapshots()
    assert mirror.gets == before, "each console poll cost a Supabase round trip"


def test_publishing_busts_the_listing_cache_immediately(mirror):
    dc.list_snapshots()                       # warms the cache with nothing in it
    _write_local()
    dc.sync_snapshots(force=True)
    assert any(s["file"] == SCRAPER_FILE for s in dc.list_snapshots())


# ---------- no mirror ----------

def test_no_credentials_means_local_only_and_everything_still_works(no_mirror):
    """Offline dev is CLAUDE.md's standing constraint."""
    _write_local()
    assert dc.mirror_backend() == "local"
    agent, path = dc.resolve(SCRAPER_FILE)
    assert agent == "scraper" and os.path.isfile(path)
    assert [s["file"] for s in dc.list_snapshots()] == [SCRAPER_FILE]


def test_a_missing_table_falls_back_to_local_and_warns_once(monkeypatch, tmp_path, capsys):
    """Phase 3's lesson: 'the migration has not been run yet' is the state of every checkout
    and the case nothing tests."""
    import io as _io
    import urllib.error
    from wingman import supabase_common

    def missing(*a, **k):
        raise urllib.error.HTTPError(
            "https://x", 404, "err", {},
            _io.BytesIO(json.dumps({"code": "PGRST205"}).encode()))
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    monkeypatch.setattr(dc, "_mirror_creds", lambda: ("https://example.supabase.co", "k"))
    monkeypatch.setattr(supabase_common, "supabase_get", missing)
    monkeypatch.setattr(supabase_common, "supabase_post", missing)
    dc._reset_mirror_for_tests()
    try:
        _write_local()
        listed = dc.list_snapshots()
        assert [s["file"] for s in listed] == [SCRAPER_FILE]
        agent, path = dc.resolve(SCRAPER_FILE)
        assert agent == "scraper" and os.path.isfile(path)
        out = capsys.readouterr().out
        assert out.count("db/agent_snapshots_schema.sql") == 1
    finally:
        dc._reset_mirror_for_tests()


def test_an_unreachable_mirror_never_blocks_committing_a_local_snapshot(monkeypatch,
                                                                       tmp_path):
    from wingman import supabase_common
    monkeypatch.setattr(dc, "REPO_DIR", str(tmp_path))
    monkeypatch.setattr(dc, "_mirror_creds", lambda: ("https://example.supabase.co", "k"))
    monkeypatch.setattr(supabase_common, "supabase_get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(supabase_common, "supabase_post",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    dc._reset_mirror_for_tests()
    try:
        _write_local()
        agent, path = dc.resolve(SCRAPER_FILE)
        assert agent == "scraper" and os.path.isfile(path)
        assert [s["file"] for s in dc.list_snapshots()] == [SCRAPER_FILE]
    finally:
        dc._reset_mirror_for_tests()


# ---------- what must NOT have changed ----------

def test_the_two_not_committable_families_are_still_refused(mirror):
    """Moving snapshots into a table must not quietly make link_check and mailing_list
    committable — the plan's Phase-4 handoff says so explicitly."""
    for agent in ("links", "mailing_list"):
        assert dc.SNAPSHOT_SPECS[agent].get("not_committable")
    _write_local(name="link_check_dry_run_20260905-141233.json", payload=[])
    result = dc.commit_snapshot("link_check_dry_run_20260905-141233.json",
                                patch_fn=None, insert_fn=None, existing_urls_fn=None)
    assert result["ok"] is False
    assert "cannot be committed" in result["error"]
