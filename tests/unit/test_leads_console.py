"""The console's view of the discovery lead queue. Pure, hermetic — no file, no network."""
import ops.core as core


class _Leads:
    """A stand-in for the discovered_leads module, so no real queue file is read."""
    STATUS_NEW, STATUS_DONE, STATUS_NOT_A_LEAD = "new", "processed", "not-a-lead"
    KIND_HUB, KIND_NAMES = "hub", "names"
    SCOPE_SAME_DOMAIN, SCOPE_OFF_DOMAIN = "same-domain", "off-domain"
    LEADS_PATH = "/tmp/discovered_leads.jsonl"

    def __init__(self, rows):
        self._rows = rows

    def load_leads(self):
        return self._rows

    def lead_scope(self, lead):
        return lead.get("scope") or self.SCOPE_OFF_DOMAIN


def _install(monkeypatch, rows):
    import sys
    monkeypatch.setitem(sys.modules, "discovered_leads", _Leads(rows))


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
