"""Unit tests for check_deadlines.py pure helpers — extract_source_urls, base_domain,
today_label. Nothing here calls Claude; only fixture dicts and static parsing.

The escalation-loop tests at the bottom monkeypatch call_claude so no network/cost is
incurred — they exercise research_deadlines' round sequencing, early-exit and site_reached
aggregation, which is otherwise only-live logic.
"""
import check_deadlines as cd


# --------------------------------------------------------------------------- extract_source_urls

def test_extract_source_urls_from_search_and_fetch():
    resp = {
        "content": [
            {"type": "text", "text": "notes"},
            {"type": "web_search_tool_result", "content": [
                {"url": "https://a.com/1", "title": "A"},
                {"url": "https://b.com/2", "title": "B"},
            ]},
            {"type": "web_fetch_tool_result", "content": {"url": "https://c.com/3"}},
        ]
    }
    assert cd.extract_source_urls(resp) == ["https://a.com/1", "https://b.com/2", "https://c.com/3"]


def test_extract_source_urls_dedupes_in_order():
    resp = {
        "content": [
            {"type": "web_search_tool_result", "content": [
                {"url": "https://a.com/1"},
                {"url": "https://a.com/1"},
                {"url": "https://b.com/2"},
            ]},
            {"type": "web_fetch_tool_result", "content": {"url": "https://a.com/1"}},
        ]
    }
    assert cd.extract_source_urls(resp) == ["https://a.com/1", "https://b.com/2"]


def test_extract_source_urls_skips_items_without_url():
    resp = {
        "content": [
            {"type": "web_search_tool_result", "content": [
                {"title": "no url here"},
                {"url": "https://a.com/1"},
                "a bare string, not a dict",
            ]},
        ]
    }
    assert cd.extract_source_urls(resp) == ["https://a.com/1"]


def test_extract_source_urls_fetch_content_not_dict():
    resp = {"content": [
        {"type": "web_fetch_tool_result", "content": None},
        {"type": "web_fetch_tool_result", "content": [{"url": "ignored"}]},  # list, not dict
    ]}
    assert cd.extract_source_urls(resp) == []


def test_extract_source_urls_empty_content():
    assert cd.extract_source_urls({"content": []}) == []
    assert cd.extract_source_urls({}) == []
    assert cd.extract_source_urls({"content": None}) == []


# --------------------------------------------------------------------------- base_domain

def test_base_domain_extracts_netloc():
    assert cd.base_domain("https://www.example.com/path/x?y=1") == "www.example.com"


def test_base_domain_no_scheme_returns_input():
    # urlparse yields empty netloc without a scheme → falls back to the raw string.
    assert cd.base_domain("example.com/path") == "example.com/path"


def test_base_domain_with_port():
    assert cd.base_domain("http://localhost:8000/api") == "localhost:8000"


# --------------------------------------------------------------------------- today_label

def test_today_label_non_empty_string():
    label = cd.today_label()
    assert isinstance(label, str) and label


def test_today_label_contains_year():
    import datetime
    assert str(datetime.date.today().year) in cd.today_label()


def test_today_label_no_leading_zero_on_day():
    # Both %-d (posix) and %#d (windows) drop the leading zero — assert the day isn't padded.
    import datetime
    today = datetime.date.today()
    label = cd.today_label()
    assert str(today.day) in label


# --------------------------------------------------------------------------- normalize_deadline_info

def test_normalize_coerces_unknown_status():
    status, dates, est, note = cd.normalize_deadline_info({"status": "maybe"})
    assert status == "unknown"
    assert dates == []
    assert est is False
    assert note is None


def test_normalize_keeps_valid_status_and_note():
    status, _dates, est, note = cd.normalize_deadline_info(
        {"status": "not_running", "was_estimated": True, "important_date_note": "gone"})
    assert (status, est, note) == ("not_running", True, "gone")


def test_normalize_drops_dates_without_date_iso():
    _s, dates, _e, _n = cd.normalize_deadline_info({"important_dates": [
        {"label": "Deadline", "date_iso": "2027-04-19", "type": "deadline"},
        {"label": "No date"},
        "not a dict",
    ]})
    assert dates == [{"label": "Deadline", "date_iso": "2027-04-19", "type": "deadline"}]


def test_normalize_handles_non_list_dates():
    _s, dates, _e, _n = cd.normalize_deadline_info({"important_dates": "soon"})
    assert dates == []


def test_normalize_handles_non_dict_info():
    assert cd.normalize_deadline_info(None) == ("unknown", [], False, None)


# --------------------------------------------------------------------------- deadline_write_decision

DATES = [{"label": "Deadline", "date_iso": "2027-04-19", "type": "deadline"}]


def test_silent_search_never_writes():
    d = cd.deadline_write_decision({}, searches=0, existing_dates=DATES)
    assert d.write is False
    assert d.source == cd.SOURCE_SILENT


def test_silent_search_never_writes_even_with_a_populated_info():
    # searches==0 wins regardless of what came back — check_one returns {} there, and a
    # non-empty info could only be a memory-derived answer.
    d = cd.deadline_write_decision({"status": "running", "important_dates": DATES}, searches=0)
    assert d.write is False


def test_unparsed_extraction_never_writes():
    # THE regression this guards: info=None used to collapse into {} and be written as an
    # authoritative status=unknown with no dates, blanking a good row for 7 days.
    d = cd.deadline_write_decision(None, searches=1, existing_dates=DATES)
    assert d.write is False
    assert d.source == cd.SOURCE_UNPARSED


def test_verified_result_with_dates_writes():
    d = cd.deadline_write_decision(
        {"status": "running", "important_dates": DATES, "was_estimated": True,
         "important_date_note": "rolled forward"},
        searches=1, existing_dates=[])
    assert d.write is True
    assert d.source == cd.SOURCE_VERIFIED
    assert d.status == "running"
    assert d.important_dates == DATES
    assert d.was_estimated is True
    assert d.note == "rolled forward"


def test_empty_result_keeps_existing_dates():
    d = cd.deadline_write_decision({"status": "unknown", "important_dates": []},
                                   searches=1, existing_dates=DATES)
    assert d.write is False
    assert d.source == cd.SOURCE_KEPT


def test_empty_result_writes_when_row_has_nothing_to_lose():
    # Nothing to preserve, and NOT stamping here would re-bill this row on every view.
    d = cd.deadline_write_decision({"status": "unknown", "important_dates": []},
                                   searches=1, existing_dates=[])
    assert d.write is True
    assert d.important_dates == []


def test_empty_result_writes_when_existing_dates_are_none():
    d = cd.deadline_write_decision({"status": "unknown"}, searches=1, existing_dates=None)
    assert d.write is True


def test_not_running_writes_even_with_no_dates_over_existing_ones():
    # "This program is discontinued" is real information and an empty important_dates is
    # the correct answer for it — the keep-existing rule must not swallow it.
    d = cd.deadline_write_decision({"status": "not_running", "important_dates": []},
                                   searches=1, existing_dates=DATES)
    assert d.write is True
    assert d.status == "not_running"
    assert d.important_dates == []


def test_running_with_dates_overwrites_existing():
    newer = [{"label": "Opens", "date_iso": "2027-01-05", "type": "opens"}]
    d = cd.deadline_write_decision({"status": "running", "important_dates": newer},
                                   searches=1, existing_dates=DATES)
    assert d.write is True
    assert d.important_dates == newer


def test_every_non_writing_decision_carries_a_reason():
    for info, searches, existing in [({}, 0, DATES), (None, 1, DATES),
                                     ({"important_dates": []}, 1, DATES)]:
        d = cd.deadline_write_decision(info, searches, existing)
        assert d.write is False
        assert d.reason and isinstance(d.reason, str)
        assert d.source != cd.SOURCE_VERIFIED


# ---------------------------------------------------- unreachable-fallback (G2, site_reached)

def test_empty_and_site_unreachable_leaves_row_due_even_with_no_existing_dates():
    # The G2 fix: an empty result on a row whose OWN page we never reached must NOT be
    # written+stamped (which would freeze a hole for 7 days over a transient outage), even
    # though there are no existing dates to lose. It leaves the row due to auto-retry.
    d = cd.deadline_write_decision({"status": "unknown", "important_dates": []},
                                   searches=1, existing_dates=[], site_reached=False)
    assert d.write is False
    assert d.source == cd.SOURCE_UNREACHABLE


def test_empty_and_site_unreachable_beats_kept_existing():
    # Unreachable takes precedence over keep-existing: we still keep the row's dates, but the
    # source names the real reason (site down), which is what leaves it due to retry.
    d = cd.deadline_write_decision({"status": "unknown", "important_dates": []},
                                   searches=1, existing_dates=DATES, site_reached=False)
    assert d.write is False
    assert d.source == cd.SOURCE_UNREACHABLE


def test_empty_but_site_reached_with_no_dates_still_writes():
    # Reached the page, read it, genuinely nothing there, nothing to lose → write+stamp the
    # real absence (default site_reached=True preserves the old behaviour).
    d = cd.deadline_write_decision({"status": "unknown", "important_dates": []},
                                   searches=1, existing_dates=[], site_reached=True)
    assert d.write is True


def test_not_running_writes_even_when_site_unreachable():
    # A definite "discontinued" verdict is a real answer regardless of reachability.
    d = cd.deadline_write_decision({"status": "not_running", "important_dates": []},
                                   searches=1, existing_dates=DATES, site_reached=False)
    assert d.write is True
    assert d.status == "not_running"


# ---------------------------------------------------------------------- rolling status (G3)

def test_rolling_is_a_valid_status():
    assert "rolling" in cd.VALID_STATUS
    status, dates, _e, _n = cd.normalize_deadline_info(
        {"status": "rolling", "important_dates": []})
    assert status == "rolling"
    assert dates == []


def test_rolling_writes_with_empty_dates_over_existing():
    # "Open now, no deadline" is the correct answer with an empty important_dates — the
    # keep-existing guard must not swallow it, exactly like not_running.
    d = cd.deadline_write_decision({"status": "rolling", "important_dates": []},
                                   searches=1, existing_dates=DATES)
    assert d.write is True
    assert d.status == "rolling"
    assert d.important_dates == []


def test_rolling_writes_even_when_site_unreachable():
    d = cd.deadline_write_decision({"status": "rolling", "important_dates": []},
                                   searches=1, existing_dates=DATES, site_reached=False)
    assert d.write is True
    assert d.status == "rolling"


# ------------------------------------------------------------------------------ _parse_signals

def test_parse_signals_reads_and_strips_the_three_lines():
    notes = ("Status: running. Deadline Feb 6, 2027.\n"
             "SITE_REACHED: yes\n"
             "FOUND_CONFIRMED_DATES: no\n"
             "FOUND_PRIOR_CYCLE_BASIS: Yes\n")
    clean, sig = cd._parse_signals(notes)
    assert "SITE_REACHED" not in clean
    assert "FOUND_" not in clean
    assert clean.strip() == "Status: running. Deadline Feb 6, 2027."
    assert sig == {"site_reached": True, "confirmed": False, "prior_basis": True}


def test_parse_signals_missing_lines_default_false():
    clean, sig = cd._parse_signals("just prose, no machine lines")
    assert clean == "just prose, no machine lines"
    assert sig == {"site_reached": False, "confirmed": False, "prior_basis": False}


def test_parse_signals_handles_empty():
    clean, sig = cd._parse_signals("")
    assert clean == ""
    assert sig["confirmed"] is False


# --------------------------------------------------------------- escalation loop (research_deadlines)

OPP = {"name": "T", "org": "O", "url": "https://prog.example", "summary": ""}


def _signals(site="yes", confirmed="no", prior="no"):
    return (f"Notes about the program.\nSITE_REACHED: {site}\n"
            f"FOUND_CONFIRMED_DATES: {confirmed}\nFOUND_PRIOR_CYCLE_BASIS: {prior}")


def _fake_rounds(monkeypatch, per_round):
    """Patch call_claude so the Nth call returns per_round[N-1] = (notes, searches). Records
    how many rounds ran in `calls`."""
    calls = {"n": 0}

    def fake(system, user_content, api_key, use_web_search=False, max_searches=None,
             return_sources=False, cache_system=False):
        i = calls["n"]
        calls["n"] += 1
        notes, searches = per_round[min(i, len(per_round) - 1)]
        usage = {"server_tool_use": {"web_search_requests": searches}}
        return (notes, usage, ["https://prog.example/apply"]) if return_sources else (notes, usage)

    monkeypatch.setattr(cd, "call_claude", fake)
    return calls


def test_loop_stops_at_rung_one_on_confirmed_dates(monkeypatch):
    calls = _fake_rounds(monkeypatch, [(_signals(confirmed="yes"), 1)])
    notes, cost, searches, sources, attempts, reached = cd.research_deadlines(OPP, "k")
    assert calls["n"] == 1  # confirmed incl. opening -> stop immediately
    assert searches == 1 and reached is True
    assert "SITE_REACHED" not in notes  # signal lines stripped before phase 2


def test_loop_climbs_to_rung_two_for_prior_cycle_basis(monkeypatch):
    calls = _fake_rounds(monkeypatch, [
        (_signals(confirmed="no", prior="no"), 1),   # rung 1: deadline only, no opening
        (_signals(confirmed="no", prior="yes"), 1),  # rung 2: prior-cycle basis found -> stop
    ])
    notes, cost, searches, sources, attempts, reached = cd.research_deadlines(OPP, "k")
    assert calls["n"] == 2
    assert searches == 2 and reached is True
    assert "Round 1" in notes and "Round 2" in notes


def test_loop_runs_all_rungs_and_reports_unreachable(monkeypatch):
    calls = _fake_rounds(monkeypatch, [(_signals(site="no"), 1)])  # never satisfied, never reached
    _n, _c, searches, _s, _a, reached = cd.research_deadlines(OPP, "k")
    assert calls["n"] == cd.ESCALATION_RUNGS
    assert reached is False  # feeds deadline_write_decision's unreachable-fallback


def test_loop_site_reached_is_or_across_rounds(monkeypatch):
    # Reached on rung 1 but not satisfied; unreachable on later rungs -> still True overall.
    calls = _fake_rounds(monkeypatch, [
        (_signals(site="yes", confirmed="no", prior="no"), 1),
        (_signals(site="no"), 1),
        (_signals(site="no"), 1),
    ])
    _n, _c, _searches, _s, _a, reached = cd.research_deadlines(OPP, "k")
    assert calls["n"] == cd.ESCALATION_RUNGS
    assert reached is True
