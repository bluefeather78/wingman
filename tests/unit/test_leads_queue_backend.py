"""The lead queue on a shared table — PRODUCTION_READINESS_PLAN.md Phase 4.

agents_report finding 4.20 and operational risk 8: "Local-only state makes a second machine a
different pipeline. In particular discovered_leads.jsonl is the ONLY queue for hub mining and
name harvesting; there is no table, no backup, and mark_processed truncates-and-rewrites it."

This is the testable half of Phase 4's exit test — *a second machine sees the same lead queue*.

What these pin:

  * with the table, two machines share ONE queue: what one marks processed, the other does not
    re-pay for. That is the finding.
  * an EXPLICIT path= still means that one file. A --path caller and the tests genuinely mean
    a file, and silently redirecting them at a database would be a nasty surprise.
  * a missing table FALLS BACK to the file and warns once, because that is the state of every
    checkout — Phase 3's lesson was that "the migration has not been run yet" is the case
    nothing tests.
  * the dedupe key is url_dedupe.match_key on BOTH backends. Two normalisers disagreeing is
    finding 4.5, and here it would mean re-paying to mine a hub already in the queue.
"""
import json
import os

import pytest

from wingman import discovered_leads as dl


def _lead(url, kind=None, status=None):
    lead = {"url": url, "kind": kind or dl.KIND_HUB, "signal": "test",
            "first_seen": "2026-09-05"}
    if status:
        lead["status"] = status
    return lead


class _FakeSupabase:
    """A stand-in for the discovered_leads table: the unique constraint on url_key and the
    mark_leads_processed RPC, which is all this module relies on."""

    def __init__(self):
        self.rows = []                     # insertion order == id order, as in the real table

    def get(self, url, table, params, key, page_size=1000, order_by="id"):
        assert table == "discovered_leads"
        assert order_by == "id", "insertion order is what makes 'oldest first' true"
        out = self.rows
        if "kind" in params:
            out = [r for r in out if r["kind"] == params["kind"].split("eq.", 1)[-1]]
        if "status" in params:
            out = [r for r in out if r["status"] == params["status"].split("eq.", 1)[-1]]
        if "limit" in params:
            out = out[:int(params["limit"])]
        return [dict(r) for r in out]

    def post(self, url, table, rows, key, on_conflict=None, batch_size=500):
        assert on_conflict == "url_key", "without it a racing insert fails the whole batch"
        for row in rows:
            existing = next((r for r in self.rows if r["url_key"] == row["url_key"]), None)
            if existing:
                existing.update(row)
            else:
                self.rows.append(dict(row))

    def rpc(self, url, fn, payload, key, timeout=30):
        assert fn == "mark_leads_processed"
        n = 0
        for row in self.rows:
            if row["url_key"] in payload["keys"] and row["status"] != dl.STATUS_DONE:
                row["status"] = dl.STATUS_DONE
                row["processed_at"] = "2026-09-05"
                n += 1
        return n


@pytest.fixture
def db(monkeypatch):
    """The table backend, pinned regardless of whether this machine has a .env."""
    from wingman import supabase_common
    fake = _FakeSupabase()
    monkeypatch.setattr(dl, "_creds", lambda: ("https://example.supabase.co", "service-key"))
    monkeypatch.setattr(supabase_common, "supabase_get", fake.get)
    monkeypatch.setattr(supabase_common, "supabase_post", fake.post)
    monkeypatch.setattr(supabase_common, "supabase_rpc", fake.rpc)
    dl._reset_for_tests()
    yield fake
    dl._reset_for_tests()


@pytest.fixture
def no_db(monkeypatch):
    """No Supabase configured — the offline-dev case CLAUDE.md protects."""
    monkeypatch.setattr(dl, "_creds", lambda: (None, None))
    dl._reset_for_tests()
    yield
    dl._reset_for_tests()


@pytest.fixture
def leadfile(tmp_path):
    return str(tmp_path / "discovered_leads.jsonl")


# ---------- the finding ----------

def test_a_second_machine_sees_the_same_queue(db):
    """Machine A captures a lead; machine B — a different process with a different file, or
    none — finds it queued. Before Phase 4 machine B mined the hub again and paid again."""
    dl.append_leads([_lead("https://a.edu/programs")])
    assert [l["url"] for l in dl.pending(dl.KIND_HUB)] == ["https://a.edu/programs"]


def test_marking_processed_on_one_machine_stops_the_other_re_paying(db):
    dl.append_leads([_lead("https://a.edu/p"), _lead("https://b.edu/p")])
    assert dl.mark_processed(["https://a.edu/p"]) == 1
    assert [l["url"] for l in dl.pending(dl.KIND_HUB)] == ["https://b.edu/p"]


def test_marking_processed_is_idempotent(db):
    dl.append_leads([_lead("https://a.edu/p")])
    assert dl.mark_processed(["https://a.edu/p"]) == 1
    assert dl.mark_processed(["https://a.edu/p"]) == 0


def test_the_queue_is_oldest_first(db):
    for c in "abc":
        dl.append_leads([_lead(f"https://{c}.edu/p")])
    assert [l["url"] for l in dl.load_leads()] == [
        "https://a.edu/p", "https://b.edu/p", "https://c.edu/p"]


def test_a_processed_lead_is_not_re_queued_by_a_later_capture(db):
    """The scraper re-captures the same round-up on every run over the same angle. If a
    re-capture reset its status, a hub would be mined again on every pass — paying each time."""
    dl.append_leads([_lead("https://a.edu/p")])
    dl.mark_processed(["https://a.edu/p"])
    assert dl.append_leads([_lead("https://a.edu/p")]) == 0
    assert dl.pending(dl.KIND_HUB) == []


def test_duplicates_are_deduped_on_the_match_key_not_the_raw_url(db):
    """Same key both sides — finding 4.5 is about two normalisers disagreeing, and here that
    would mean paying to mine a hub already in the queue."""
    dl.append_leads([_lead("https://a.edu/p")])
    assert dl.append_leads([_lead("https://a.edu/p/")]) == 0
    assert len(dl.load_leads()) == 1


def test_kinds_are_filtered_by_the_query_not_in_python(db):
    dl.append_leads([_lead("https://a.edu/p", dl.KIND_HUB),
                     _lead("https://b.edu/p", dl.KIND_NAMES)])
    assert [l["url"] for l in dl.pending(dl.KIND_NAMES)] == ["https://b.edu/p"]
    assert [l["url"] for l in dl.pending(dl.KIND_HUB)] == ["https://a.edu/p"]


def test_a_limit_is_applied(db):
    dl.append_leads([_lead(f"https://{c}.edu/p") for c in "abcd"])
    assert len(dl.pending(dl.KIND_HUB, limit=2)) == 2


def test_the_stored_blob_keeps_every_field_the_capture_wrote(db):
    dl.append_leads([{"url": "https://a.edu/p", "kind": dl.KIND_HUB, "signal": "listicle",
                      "angle": "summer research", "seed_id": 12, "scope": dl.SCOPE_SAME_DOMAIN}])
    lead = dl.load_leads()[0]
    assert lead["angle"] == "summer research"
    assert lead["seed_id"] == 12
    assert dl.lead_scope(lead) == dl.SCOPE_SAME_DOMAIN


def test_the_row_columns_win_over_a_stale_status_inside_the_blob(db):
    """The blob is the lead as CAPTURED, so its `status` is always "new". Reading status from
    it would make every processed lead look pending and re-pay for the whole queue."""
    dl.append_leads([_lead("https://a.edu/p")])
    dl.mark_processed(["https://a.edu/p"])
    assert dl.load_leads()[0]["status"] == dl.STATUS_DONE


# ---------- an explicit path still means a file ----------

def test_an_explicit_path_bypasses_the_table_entirely(db, leadfile):
    """A --path caller and the tests mean one file. Redirecting them at the database
    would be a nasty surprise, and it would make an operator's --path silently a no-op."""
    dl.append_leads([_lead("https://file.edu/p")], leadfile)
    assert [l["url"] for l in dl.load_leads(leadfile)] == ["https://file.edu/p"]
    assert dl.load_leads() == [], "the file write leaked into the shared queue"


def test_the_file_backend_still_works_end_to_end(no_db, leadfile):
    dl.append_leads([_lead("https://a.edu/p"), _lead("https://b.edu/p")], leadfile)
    assert dl.mark_processed(["https://a.edu/p"], leadfile) == 1
    assert [l["url"] for l in dl.pending(dl.KIND_HUB, leadfile)] == ["https://b.edu/p"]
    lines = [json.loads(x) for x in open(leadfile, encoding="utf-8") if x.strip()]
    assert lines[0]["status"] == dl.STATUS_DONE


# ---------- the un-migrated database ----------

def test_a_missing_table_falls_back_to_the_file_and_warns_once(monkeypatch, tmp_path, capsys):
    """Phase 3's lesson: every unit test mocked the table as present, so 'the migration has
    not been run yet' — the state of every checkout — was the untested case."""
    import urllib.error
    import io as _io
    from wingman import supabase_common

    def missing(*a, **k):
        raise urllib.error.HTTPError(
            "https://x", 404, "err", {},
            _io.BytesIO(json.dumps({"code": "PGRST205"}).encode()))
    monkeypatch.setattr(dl, "_creds", lambda: ("https://example.supabase.co", "k"))
    monkeypatch.setattr(supabase_common, "supabase_get", missing)
    monkeypatch.setattr(dl, "LEADS_PATH", str(tmp_path / "discovered_leads.jsonl"))
    dl._reset_for_tests()
    try:
        assert dl.load_leads() == []
        assert dl.append_leads([_lead("https://a.edu/p")]) == 1
        assert [l["url"] for l in dl.load_leads()] == ["https://a.edu/p"]
        out = capsys.readouterr().out
        assert out.count("db/discovered_leads_schema.sql") == 1
        assert "LOCAL FILE" in out
    finally:
        dl._reset_for_tests()


def test_a_real_read_failure_raises_rather_than_silently_using_a_stale_file(monkeypatch):
    """An outage must not be answered with a different machine's leftover queue — that reads
    as success and re-mines whatever the local file happens to hold."""
    from wingman import supabase_common
    monkeypatch.setattr(dl, "_creds", lambda: ("https://example.supabase.co", "k"))
    monkeypatch.setattr(supabase_common, "supabase_get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("supabase down")))
    dl._reset_for_tests()
    try:
        with pytest.raises(RuntimeError):
            dl.load_leads()
    finally:
        dl._reset_for_tests()


def test_the_backend_is_reportable(no_db):
    """The console prints it. An operator who cannot tell which backend is live cannot tell
    whether the leads on screen are the ones the other machine sees."""
    assert dl.queue_backend() == "file"


def test_the_backend_reads_supabase_when_the_table_is_there(db):
    assert dl.queue_backend() == "supabase"


def test_no_credentials_means_the_file_without_a_missing_table_warning(no_db, tmp_path,
                                                                      capsys):
    """Offline dev is CLAUDE.md's standing constraint and is not a broken migration."""
    path = str(tmp_path / "leads.jsonl")
    assert dl.append_leads([_lead("https://a.edu/p")], path) == 1
    assert "discovered_leads table unavailable" not in capsys.readouterr().out


# ---------- require_db: the console never touches a file ----------

def test_require_db_reads_and_writes_the_table(db):
    """The strict path the console uses is an ordinary table read/write when the table is there."""
    assert dl.append_leads([_lead("https://a.edu/p")], require_db=True) == 1
    assert [l["url"] for l in dl.load_leads(require_db=True)] == ["https://a.edu/p"]
    assert dl.mark_processed(["https://a.edu/p"], require_db=True) == 1
    assert dl.pending(dl.KIND_HUB, require_db=True) == []


def test_require_db_raises_with_no_credentials_instead_of_using_a_file(no_db, tmp_path,
                                                                       monkeypatch):
    """This is the whole point: with no table the console must fail loudly, never write the
    laptop-local file that stranded 235 leads in the Phase 4 migration."""
    monkeypatch.setattr(dl, "LEADS_PATH", str(tmp_path / "discovered_leads.jsonl"))
    with pytest.raises(dl.LeadQueueUnavailable):
        dl.load_leads(require_db=True)
    with pytest.raises(dl.LeadQueueUnavailable):
        dl.append_leads([_lead("https://a.edu/p")], require_db=True)
    assert not os.path.exists(str(tmp_path / "discovered_leads.jsonl")), \
        "a strict write leaked to the local file"


def test_require_db_raises_on_a_missing_table_and_never_falls_back(monkeypatch, tmp_path):
    """A missing table is LeadQueueUnavailable, not a silent fall-through to the file — and it
    must not latch the process into file mode either."""
    import urllib.error
    import io as _io
    from wingman import supabase_common

    def missing(*a, **k):
        raise urllib.error.HTTPError(
            "https://x", 404, "err", {},
            _io.BytesIO(json.dumps({"code": "PGRST205"}).encode()))
    monkeypatch.setattr(dl, "_creds", lambda: ("https://example.supabase.co", "k"))
    monkeypatch.setattr(supabase_common, "supabase_get", missing)
    monkeypatch.setattr(dl, "LEADS_PATH", str(tmp_path / "discovered_leads.jsonl"))
    dl._reset_for_tests()
    try:
        with pytest.raises(dl.LeadQueueUnavailable):
            dl.load_leads(require_db=True)
        assert not os.path.exists(str(tmp_path / "discovered_leads.jsonl"))
    finally:
        dl._reset_for_tests()


def test_require_db_refuses_an_explicit_path(db, leadfile):
    """require_db is for the shared table; pairing it with a path is a contradiction, not a file
    write — so it raises rather than quietly doing one or the other."""
    with pytest.raises(dl.LeadQueueUnavailable):
        dl.load_leads(leadfile, require_db=True)


# ---------- the file -> table importer (closes the Phase 4 orphaning gap) ----------

def test_import_file_to_table_copies_leads_and_preserves_status(db, leadfile):
    dl.append_leads([_lead("https://a.edu/p"), _lead("https://b.edu/p")], leadfile)
    dl.mark_processed(["https://a.edu/p"], leadfile)          # one processed in the file
    result = dl.import_file_to_table(leadfile)
    assert result == {"file": 2, "written": 2, "skipped": 0, "dry_run": False}
    by_url = {l["url"]: l for l in dl.load_leads(require_db=True)}
    assert by_url["https://a.edu/p"]["status"] == dl.STATUS_DONE
    assert by_url["https://b.edu/p"]["status"] == dl.STATUS_NEW


def test_import_file_to_table_is_safe_to_run_twice(db, leadfile):
    dl.append_leads([_lead("https://a.edu/p")], leadfile)
    assert dl.import_file_to_table(leadfile)["written"] == 1
    again = dl.import_file_to_table(leadfile)
    assert again["written"] == 0 and again["skipped"] == 1


def test_import_file_dry_run_writes_nothing(db, leadfile):
    dl.append_leads([_lead("https://a.edu/p")], leadfile)
    result = dl.import_file_to_table(leadfile, dry_run=True)
    assert result["would_write"] == 1 and result["written"] == 0 and result["dry_run"] is True
    assert dl.load_leads(require_db=True) == [], "a dry run must not touch the table"


def test_import_file_with_no_file_is_a_noop(db, tmp_path):
    result = dl.import_file_to_table(str(tmp_path / "absent.jsonl"))
    assert result["file"] == 0 and result["written"] == 0


def test_import_file_requires_the_table(no_db, leadfile):
    dl.append_leads([_lead("https://a.edu/p")], leadfile)
    with pytest.raises(dl.LeadQueueUnavailable):
        dl.import_file_to_table(leadfile)


def test_the_schema_and_the_module_agree_on_the_status_strings():
    """Two backends spelling 'processed' differently would mean a queue half-read from each,
    re-paying for the overlap."""
    from wingman import REPO_ROOT
    sql = open(os.path.join(REPO_ROOT, "db", "discovered_leads_schema.sql"),
               encoding="utf-8").read()
    assert f"'{dl.STATUS_DONE}'" in sql
    assert f"default '{dl.STATUS_NEW}'" in sql
