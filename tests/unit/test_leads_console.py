"""The console's view of the discovery lead queue. Pure, hermetic — no file, no network."""
import ops.core as core


class _Leads:
    """A stand-in for the discovered_leads module, so no real queue file is read.

    The console reads the queue with require_db=True (table-only) as of the fix that stops it
    ever operating on a laptop-local file, so this fake models both the strict read and the
    "table missing" case that must degrade to a setup notice rather than showing file rows."""
    STATUS_NEW, STATUS_DONE, STATUS_NOT_A_LEAD = "new", "processed", "not-a-lead"
    KIND_HUB, KIND_NAMES = "hub", "names"
    SCOPE_SAME_DOMAIN, SCOPE_OFF_DOMAIN = "same-domain", "off-domain"
    LEADS_PATH = "/tmp/discovered_leads.jsonl"

    class LeadQueueUnavailable(RuntimeError):
        pass

    def __init__(self, rows, unavailable=False, stranded=0):
        self._rows = rows
        self._unavailable = unavailable
        self._stranded = stranded

    def load_leads(self, path=None, require_db=False):
        if require_db and self._unavailable:
            raise self.LeadQueueUnavailable(
                "the discovered_leads table is missing — run db/discovered_leads_schema.sql")
        return self._rows

    def lead_scope(self, lead):
        return lead.get("scope") or self.SCOPE_OFF_DOMAIN

    def file_lead_count(self, path=None):
        # Leads stranded in a local file the table-only console will not read.
        return self._stranded


def _install(monkeypatch, rows, unavailable=False, stranded=0):
    """ops.core does `from wingman import discovered_leads` inside the function, so BOTH the
    sys.modules entry and the attribute on the package have to be replaced: `from X import Y`
    resolves Y off the already-imported package object first and only falls back to
    sys.modules. Patching one of the two leaves the real module in play."""
    import sys
    import wingman
    fake = _Leads(rows, unavailable=unavailable, stranded=stranded)
    monkeypatch.setitem(sys.modules, "wingman.discovered_leads", fake)
    monkeypatch.setattr(wingman, "discovered_leads", fake, raising=False)


def test_counts_split_the_queue_the_way_the_operator_asks_about_it(monkeypatch):
    """own-site index vs round-up vs names-only, because each is drained by a different command
    and only one of them is even allowed to be router-fed."""
    _install(monkeypatch, [
        {"url": "https://x.edu/pre/", "kind": "hub", "scope": "same-domain", "status": "new"},
        {"url": "https://list.com/a", "kind": "hub", "scope": "off-domain", "status": "new"},
        {"url": "https://names.com/a", "kind": "names", "status": "new"},
        {"url": "https://done.com/a", "kind": "hub", "status": "processed"},
        {"url": "https://no.com/a", "kind": None, "status": "not-a-lead"},
    ])
    r = core.list_discovered_leads()
    assert r["ok"] is True
    assert r["counts"] == {"new": 3, "processed": 1, "not_a_lead": 1,
                           "hub_same_domain": 1, "hub_off_domain": 1, "names": 1}


def test_the_list_is_in_queue_order_not_sorted(monkeypatch):
    """It is the order --from-leads takes them in, so the top of the console list is exactly what
    a run of N would spend on. Sorting it prettily would make the console disagree with the tool."""
    _install(monkeypatch, [{"url": f"https://x/{i}", "kind": "hub", "status": "new"}
                           for i in range(5)])
    r = core.list_discovered_leads(limit=3)
    assert [l["url"] for l in r["leads"]] == ["https://x/0", "https://x/1", "https://x/2"]
    assert r["truncated"] == 2


def test_the_console_is_table_only_and_says_so(monkeypatch):
    """The strict read can only ever be the shared table now, so the console reports supabase
    and never a per-laptop file path — the divergence that stranded 235 leads is gone."""
    _install(monkeypatch, [{"url": "https://x.edu/a", "kind": "hub", "status": "new"}])
    r = core.list_discovered_leads()
    assert r["ok"] is True
    assert r["backend"] == "supabase"
    assert "jsonl" not in r["path"]


def test_a_missing_table_degrades_to_a_setup_notice_not_file_contents(monkeypatch):
    """With the table absent the console must NOT fall back to the local file — it shows a
    setup notice, the same way every other migration-gated tab does."""
    _install(monkeypatch, [{"url": "https://x.edu/a", "kind": "hub", "status": "new"}],
             unavailable=True)
    r = core.list_discovered_leads()
    assert r["ok"] is False
    assert r["needs_setup"] is True
    assert "discovered_leads_schema.sql" in r["error"]
    assert r["leads"] == []


def test_leads_stranded_in_a_local_file_are_flagged_with_the_import_command(monkeypatch):
    """A non-empty local file this table-only view cannot read is surfaced, not hidden, so an
    operator does not read an empty table as an empty queue."""
    _install(monkeypatch, [{"url": "https://x.edu/a", "kind": "hub", "status": "new"}],
             stranded=17)
    r = core.list_discovered_leads()
    assert r["ok"] is True
    assert r["stranded_file_leads"] == 17
    assert "--import-file" in r["stranded_file_hint"]


def test_a_lead_with_no_status_counts_as_waiting(monkeypatch):
    _install(monkeypatch, [{"url": "https://x/1", "kind": "hub"}])
    assert core.list_discovered_leads()["counts"]["new"] == 1


def test_queued_leads_do_not_take_the_off_domain_flag():
    """Each lead carries its own direction, so passing --off-domain alongside --from-leads would
    override half the queue with the wrong one."""
    argv = core.build_tool_args("minehub", {"fromLeads": "5", "offDomain": True, "mode": "run"})
    assert "--from-leads" in argv and "--off-domain" not in argv


def test_a_single_url_still_honours_the_checkbox_and_the_ceiling():
    argv = core.build_tool_args("minehub", {"url": "https://list.com/x", "offDomain": True,
                                            "maxPages": "40", "mode": "run"})
    assert argv[-4:] == ["--off-domain", "--max-pages", "40"] or (
        "--off-domain" in argv and ["--max-pages", "40"] == argv[-2:])
    assert "--preview" not in argv


def test_preview_is_the_default_for_the_tool():
    assert "--preview" in core.build_tool_args("minehub", {"fromLeads": "3"})
