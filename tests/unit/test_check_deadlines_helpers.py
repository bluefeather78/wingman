"""Unit tests for agents/check_deadlines.py pure helpers — extract_source_urls, base_domain,
today_label. Nothing here calls Claude; only fixture dicts and static parsing.

The escalation-loop tests at the bottom monkeypatch call_claude so no network/cost is
incurred — they exercise research_deadlines' round sequencing, early-exit and site_reached
aggregation, which is otherwise only-live logic.
"""
from agents import check_deadlines as cd
from wingman import source_capture


def _cap(urls, text=""):
    """CapturedSource objects for the fake call_claude's return_captured path (P6c). Text is
    empty by default — the sequencing tests do not exercise date verification."""
    return [source_capture.CapturedSource(u, cd.base_domain(u), "text/plain", text, None)
            for u in urls]


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
             return_sources=False, return_captured=False, cache_system=False):
        i = calls["n"]
        calls["n"] += 1
        notes, searches = per_round[min(i, len(per_round) - 1)]
        usage = {"server_tool_use": {"web_search_requests": searches}}
        urls = ["https://prog.example/apply"]
        if return_captured:
            return notes, usage, urls, _cap(urls)
        return (notes, usage, urls) if return_sources else (notes, usage)

    monkeypatch.setattr(cd, "call_claude", fake)
    return calls


def test_loop_stops_at_rung_one_on_confirmed_dates(monkeypatch):
    calls = _fake_rounds(monkeypatch, [(_signals(confirmed="yes"), 1)])
    notes, cost, searches, sources, attempts, reached, captured = cd.research_deadlines(OPP, "k")
    assert calls["n"] == 1  # confirmed incl. opening -> stop immediately
    assert searches == 1 and reached is True
    assert "SITE_REACHED" not in notes  # signal lines stripped before phase 2


def test_loop_climbs_to_rung_two_for_prior_cycle_basis(monkeypatch):
    calls = _fake_rounds(monkeypatch, [
        (_signals(confirmed="no", prior="no"), 1),   # rung 1: deadline only, no opening
        (_signals(confirmed="no", prior="yes"), 1),  # rung 2: prior-cycle basis found -> stop
    ])
    notes, cost, searches, sources, attempts, reached, captured = cd.research_deadlines(OPP, "k")
    assert calls["n"] == 2
    assert searches == 2 and reached is True
    assert "Round 1" in notes and "Round 2" in notes


# Rungs that actually run when there is NO trusted allowlist: every rung except rung 4
# (trusted third-party), which is skipped with nothing to search. Computed rather than
# hard-coded so adding/removing an own-site rung keeps these tests honest.
OWN_SITE_RUNGS = sum(1 for r in cd.RUNGS[:cd.ESCALATION_RUNGS]
                     if r[0] != cd.RUNG_TRUSTED_THIRD_PARTY)


def test_loop_runs_all_own_site_rungs_and_reports_unreachable(monkeypatch):
    calls = _fake_rounds(monkeypatch, [(_signals(site="no"), 1)])  # never satisfied, never reached
    # No allowlist -> rung 4 is a no-op, so the loop runs exactly the own-site rungs.
    _n, _c, searches, _s, _a, reached, _cd = cd.research_deadlines(OPP, "k", trusted_domains=[])
    assert calls["n"] == OWN_SITE_RUNGS
    assert reached is False  # feeds deadline_write_decision's unreachable-fallback


def test_loop_site_reached_is_or_across_rounds(monkeypatch):
    # Reached on rung 1 but not satisfied; unreachable on later rungs -> still True overall.
    calls = _fake_rounds(monkeypatch, [
        (_signals(site="yes", confirmed="no", prior="no"), 1),
        (_signals(site="no"), 1),
        (_signals(site="no"), 1),
    ])
    _n, _c, _searches, _s, _a, reached, _cd = cd.research_deadlines(OPP, "k", trusted_domains=[])
    assert calls["n"] == OWN_SITE_RUNGS
    assert reached is True


# --------------------------------------------------------------- rung 4 (trusted third-party, P5)

def test_rung4_skipped_without_allowlist(monkeypatch):
    # Every own-site rung fails to satisfy; with no trusted domains, rung 4 never runs, so the
    # loop stops after the own-site rungs (pre-P5 behaviour is preserved).
    calls = _fake_rounds(monkeypatch, [(_signals(site="no", confirmed="no", prior="no"), 1)])
    cd.research_deadlines(OPP, "k", trusted_domains=[])
    assert calls["n"] == OWN_SITE_RUNGS


def test_rung4_runs_when_own_site_fails_and_allowlist_present(monkeypatch):
    # Own-site rungs never satisfy -> rung 4 runs, injecting the allowlist into its focus.
    seen_focus = {}

    def fake(system, user_content, api_key, use_web_search=False, max_searches=None,
             return_sources=False, return_captured=False, cache_system=False):
        seen_focus.setdefault("last", user_content)
        seen_focus["last"] = user_content
        notes = _signals(site="no", confirmed="no", prior="no")
        usage = {"server_tool_use": {"web_search_requests": 1}}
        if return_captured:
            return notes, usage, [], []
        return (notes, usage, []) if return_sources else (notes, usage)

    monkeypatch.setattr(cd, "call_claude", fake)
    cd.research_deadlines(OPP, "k", trusted_domains=["lumiere-education.com"])
    # The final round is rung 4 and its user content names the trusted domain.
    assert "lumiere-education.com" in seen_focus["last"]


def test_rung4_sources_are_trust_filtered(monkeypatch):
    # Rung 4 returns one trusted and one untrusted source; only the trusted one survives into
    # the union handed to phase 2. Own-site rungs return an own-site source, which passes.
    round_idx = {"n": 0}

    def fake(system, user_content, api_key, use_web_search=False, max_searches=None,
             return_sources=False, return_captured=False, cache_system=False):
        i = round_idx["n"]
        round_idx["n"] += 1
        notes = _signals(site="no", confirmed="no", prior="no")
        usage = {"server_tool_use": {"web_search_requests": 1}}
        # Own-site rungs (0..OWN_SITE_RUNGS-1) return an own-domain source; rung 4 returns a
        # trusted + an untrusted source.
        if i < OWN_SITE_RUNGS:
            srcs = ["https://prog.example/apply"]
        else:
            srcs = ["https://lumiere-education.com/think", "https://spammy.example/listicle"]
        if return_captured:
            return notes, usage, srcs, _cap(srcs)
        return (notes, usage, srcs) if return_sources else (notes, usage)

    monkeypatch.setattr(cd, "call_claude", fake)
    _n, _c, _s, sources, _a, _r, captured = cd.research_deadlines(
        OPP, "k", trusted_domains=["lumiere-education.com"])
    assert "https://lumiere-education.com/think" in sources
    assert "https://spammy.example/listicle" not in sources  # untrusted -> dropped
    assert "https://prog.example/apply" in sources           # own-site rung, not filtered
    # Captured CONTENT is trust-filtered the same way, so a date is never verified against an
    # untrusted third-party page.
    cap_urls = [c.url for c in captured]
    assert "https://lumiere-education.com/think" in cap_urls
    assert "https://spammy.example/listicle" not in cap_urls


# --------------------------------------------------------------- D5 sitemap-first in the ladder

def test_sitemap_block_empty_and_populated():
    assert cd._sitemap_block([]) == ""
    assert cd._sitemap_block(None) == ""
    block = cd._sitemap_block(["https://prog.example/key-dates", "https://prog.example/apply"])
    assert "OWN sitemap" in block
    assert "https://prog.example/key-dates" in block and "https://prog.example/apply" in block


class _C:
    def __init__(self, url):
        self.url = url


def test_ladder_injects_sitemap_urls_into_own_site_rungs_not_rung4(monkeypatch):
    """D5: the own-site rungs get the program's sitemap candidate URLs in their prompt; rung 4
    (trusted third-party, off-site) does not. discover is INJECTED, so no network."""
    per_round_user = []

    def fake(system, user_content, api_key, use_web_search=False, max_searches=None,
             return_sources=False, return_captured=False, cache_system=False):
        per_round_user.append(user_content)
        notes = _signals(site="no", confirmed="no", prior="no")   # never satisfied -> climb
        usage = {"server_tool_use": {"web_search_requests": 1}}
        if return_captured:
            return notes, usage, [], []
        return (notes, usage, []) if return_sources else (notes, usage)

    monkeypatch.setattr(cd, "call_claude", fake)
    fake_discover = lambda opp, top_n=5: [_C("https://prog.example/key-dates"),
                                          _C("https://prog.example/apply")]
    cd.research_deadlines(OPP, "k", trusted_domains=["lumiere-education.com"],
                          discover=fake_discover)
    # Own-site rungs (all but the last) carry the sitemap URLs; rung 4 (last) does not.
    own_site = per_round_user[:OWN_SITE_RUNGS]
    rung4 = per_round_user[OWN_SITE_RUNGS]
    assert all("https://prog.example/key-dates" in u for u in own_site)
    assert "https://prog.example/key-dates" not in rung4
    assert "lumiere-education.com" in rung4          # rung 4 still gets the allowlist


def test_ladder_without_discover_is_unchanged(monkeypatch):
    """discover=None (the default) => no sitemap block => byte-identical to the pre-D5 prompt,
    so no row regresses and no network is touched."""
    seen = []

    def fake(system, user_content, api_key, use_web_search=False, max_searches=None,
             return_sources=False, return_captured=False, cache_system=False):
        seen.append(user_content)
        return _signals(confirmed="yes"), {"server_tool_use": {"web_search_requests": 1}}, [], []

    monkeypatch.setattr(cd, "call_claude", fake)
    cd.research_deadlines(OPP, "k")   # no discover -> off
    assert "sitemap" not in seen[0].lower()


# --------------------------------------------------------------- date-on-page (P6c / T7)

from wingman import page_text as _pt
from wingman import source_capture as _sc


def _src(url, text, tier=None):
    return _sc.CapturedSource(url, cd.base_domain(url), "text/plain", text, tier)


def test_date_on_page_matches_common_formats():
    for text in ["Applications close January 15, 2027.", "Deadline: Jan 15",
                 "due 1/15/2027", "submit by 15/01/2027", "2027-01-15"]:
        assert _pt.date_is_on_page("2027-01-15", text), text


def test_date_on_page_rejects_bare_number_and_wrong_date():
    assert not _pt.date_is_on_page("2027-01-15", "There were 150 applicants.")
    assert not _pt.date_is_on_page("2027-02-01", "Applications close January 15, 2027.")
    assert not _pt.date_is_on_page("not-a-date", "January 15, 2027")


def test_verify_dates_marks_confirmed_date_found():
    info = {"important_dates": [{"date_iso": "2027-01-15", "type": "deadline",
                                 "estimated": False}]}
    unverified = cd.verify_dates_against_capture(
        info, [_src("https://prog.example/apply", "Applications close January 15, 2027.")])
    d = info["important_dates"][0]
    assert d["verified"] is True
    assert d["source_url"] == "https://prog.example/apply"
    assert unverified == 0


def test_verify_dates_marks_confirmed_date_not_found_and_counts_it():
    info = {"important_dates": [{"date_iso": "2027-01-15", "type": "deadline",
                                 "estimated": False}]}
    unverified = cd.verify_dates_against_capture(
        info, [_src("https://prog.example/x", "nothing dated on this page")])
    d = info["important_dates"][0]
    assert d["verified"] is False and "source_url" not in d
    assert unverified == 1  # the quality signal


def test_verify_dates_never_counts_an_estimated_date():
    """A projected date is absent from every page BY DESIGN — mark it, never count it as a
    miss, never delete it."""
    info = {"important_dates": [{"date_iso": "2027-11-01", "type": "opens",
                                 "estimated": True}]}
    unverified = cd.verify_dates_against_capture(info, [_src("https://p/x", "no dates")])
    assert info["important_dates"][0]["verified"] is False
    assert unverified == 0  # estimated dates are not part of the signal


def test_verify_dates_handles_no_capture_and_no_dates():
    assert cd.verify_dates_against_capture({}, []) == 0
    assert cd.verify_dates_against_capture({"important_dates": []}, []) == 0
    info = {"important_dates": [{"date_iso": "2027-01-15", "estimated": False}]}
    # No captured pages -> nothing to verify against -> marked unverified, counted.
    assert cd.verify_dates_against_capture(info, []) == 1


# --------------------------------------------------------------- G6a today-anchoring backstop

def test_g6a_demotes_today_anchored_unverified_date():
    """A non-estimated date equal to the check date that is NOT on any page is the anchoring
    fingerprint -> demote to estimated, do NOT count as a confirmed miss, add a caveat."""
    info = {"important_dates": [{"date_iso": "2026-08-27", "type": "opens",
                                 "estimated": False}]}
    unverified = cd.verify_dates_against_capture(
        info, [_src("https://p/x", "no opening date on this page")], today="2026-08-27")
    d = info["important_dates"][0]
    assert d["estimated"] is True          # demoted, not confirmed
    assert d["verified"] is False
    assert "source_url" not in d
    assert unverified == 0                  # a flagged estimate, not a confirmed miss
    assert "estimate" in (info.get("important_date_note") or "").lower()


def test_g6a_leaves_a_genuinely_today_date_that_verifies():
    """A real same-day date verifies against the page, so the fingerprint never fires on it."""
    info = {"important_dates": [{"date_iso": "2026-08-27", "type": "opens",
                                 "estimated": False}]}
    unverified = cd.verify_dates_against_capture(
        info, [_src("https://p/apply", "Applications open August 27, 2026.")],
        today="2026-08-27")
    d = info["important_dates"][0]
    assert d["verified"] is True and d.get("estimated") is False
    assert d["source_url"] == "https://p/apply"
    assert unverified == 0
    assert not info.get("important_date_note")   # nothing demoted -> no caveat


def test_g6a_does_not_touch_an_unverified_non_today_date():
    """An unconfirmed date that is NOT today stays a counted confirmed miss (unchanged path)."""
    info = {"important_dates": [{"date_iso": "2027-01-15", "type": "deadline",
                                 "estimated": False}]}
    unverified = cd.verify_dates_against_capture(
        info, [_src("https://p/x", "no dates here")], today="2026-08-27")
    d = info["important_dates"][0]
    assert d.get("estimated") is False and d["verified"] is False
    assert unverified == 1


# ------------------------------------------------- status evidence gate (2026-08-26)
# A not_running verdict must be proven against a fetched page or it downgrades to unknown.
# Born from ec18599 (Impact Internships): the off-season of an annual program — "2026 cycle
# closed... No 2027 dates posted yet" — was written as `not_running`, burying a live program
# as a Past Event with no dates and no visible tasks.


def test_status_evidence_verified_keeps_not_running_and_records_evidence():
    quote = "The Impact Internships program has permanently ended and will not be returning."
    info = {"status": "not_running", "status_evidence": quote,
            "important_date_note": "Program discontinued."}
    out = cd.verify_status_evidence(
        info, [_src("https://prog.example/", f"About us. {quote} Thanks to all alumni.")])
    assert out == "verified"
    assert info["status"] == "not_running"
    assert "status_evidence" not in info  # consumed, never written to the row
    assert quote in info["important_date_note"]
    assert "https://prog.example/" in info["important_date_note"]


def test_status_evidence_missing_quote_downgrades_to_unknown():
    info = {"status": "not_running", "important_date_note": "2026 cycle closed."}
    out = cd.verify_status_evidence(
        info, [_src("https://prog.example/", "Apply to Summer 2026! Deadline June 3.")])
    assert out == "downgraded"
    assert info["status"] == "unknown"
    assert "unverified" in info["important_date_note"]
    # The original note survives alongside the caveat.
    assert "2026 cycle closed." in info["important_date_note"]


def test_status_evidence_quote_not_on_any_fetched_page_downgrades():
    info = {"status": "not_running",
            "status_evidence": "This program has been permanently discontinued."}
    out = cd.verify_status_evidence(
        info, [_src("https://prog.example/", "Apply to Summer 2026 HSIP today")])
    assert out == "downgraded"
    assert info["status"] == "unknown"
    assert "status_evidence" not in info


def test_status_evidence_downgrade_with_no_capture_at_all():
    info = {"status": "not_running", "status_evidence": "The program has ended for good."}
    assert cd.verify_status_evidence(info, []) == "downgraded"
    assert info["status"] == "unknown"


def test_status_evidence_other_statuses_untouched_and_stray_field_stripped():
    for status in ("running", "rolling", "unknown"):
        info = {"status": status, "status_evidence": "stray quote the model emitted anyway"}
        assert cd.verify_status_evidence(info, []) is None
        assert info["status"] == status
        assert "status_evidence" not in info
    # Non-dict outcomes ({} silent / None unparsed) pass through untouched.
    assert cd.verify_status_evidence(None, []) is None
    assert cd.verify_status_evidence({}, []) is None


# --------------------------------------------------------------- shared finder (T6)

def _fake_finder_halves(monkeypatch, date_captured=(), req_captured=(), date_cost=0.5,
                        req_cost=0.3, date_searches=1, req_reason="ok"):
    """Patch the two halves find_program_sources composes so no network is hit."""
    def fake_research(opp, api_key, retry_on_silent=True, trusted_domains=None, discover=None):
        return ("date notes", date_cost, date_searches, [c.url for c in date_captured],
                1, True, list(date_captured))
    monkeypatch.setattr(cd, "research_deadlines", fake_research)

    def fake_capture(opp, api_key, timeout=None, policy=None):
        return list(req_captured), req_cost, req_reason
    monkeypatch.setattr(cd.source_capture, "fetch_and_capture", fake_capture)


def _clear_cache():
    cd._shared_capture_cache.clear()


def test_finder_dates_only_runs_no_requirements(monkeypatch):
    _clear_cache()
    _fake_finder_halves(monkeypatch, date_captured=[_src("https://p/a", "dates page")])
    notes, cost, searches, urls, attempts, reached, captured = cd.find_program_sources(
        {"id": "x1", "url": "https://p"}, "k", want_dates=True, want_requirements=False)
    assert notes == "date notes" and cost == 0.5
    assert [c.url for c in captured] == ["https://p/a"]  # only the date half


def test_finder_requirements_only_skips_date_ladder(monkeypatch):
    _clear_cache()
    _fake_finder_halves(monkeypatch, req_captured=[_src("https://p/faq", "faq page")])
    notes, cost, searches, urls, attempts, reached, captured = cd.find_program_sources(
        {"id": "x2", "url": "https://p"}, "k", want_dates=False, want_requirements=True)
    assert notes == "" and cost == 0.3 and searches == 0     # no date ladder ran
    assert [c.url for c in captured] == ["https://p/faq"]
    assert reached is True                                    # requirements fetch reached site


def test_finder_both_merges_captures(monkeypatch):
    _clear_cache()
    _fake_finder_halves(monkeypatch,
                        date_captured=[_src("https://p/a", "dates")],
                        req_captured=[_src("https://p/faq", "faq"), _src("https://p/a", "dup")])
    _n, cost, _s, _u, _a, _r, captured = cd.find_program_sources(
        {"id": "x3", "url": "https://p"}, "k", want_dates=True, want_requirements=True)
    urls = [c.url for c in captured]
    assert cost == 0.8                                        # both halves billed
    assert urls == ["https://p/a", "https://p/faq"]           # merged, deduped (first wins)


def test_finder_full_result_is_cached_read_once(monkeypatch):
    _clear_cache()
    calls = {"n": 0}
    orig_research = cd.research_deadlines

    def counting_research(opp, api_key, retry_on_silent=True, trusted_domains=None, discover=None):
        calls["n"] += 1
        return ("notes", 0.5, 1, [], 1, True, [_src("https://p/a", "dates")])
    monkeypatch.setattr(cd, "research_deadlines", counting_research)
    monkeypatch.setattr(cd.source_capture, "fetch_and_capture",
                        lambda opp, api_key, timeout=None, policy=None: ([], 0.3, "ok"))

    opp = {"id": "x4", "url": "https://p"}
    first = cd.find_program_sources(opp, "k", want_dates=True, want_requirements=True)
    second = cd.find_program_sources(opp, "k", want_dates=True, want_requirements=True)
    assert calls["n"] == 1              # second call served from cache, no re-fetch
    assert first[1] == 0.8             # first pays for the fetch
    assert second[1] == 0.0            # cache hit costs nothing (already billed)


def test_finder_single_goal_is_not_cached(monkeypatch):
    _clear_cache()
    calls = {"n": 0}

    def counting_research(opp, api_key, retry_on_silent=True, trusted_domains=None, discover=None):
        calls["n"] += 1
        return ("notes", 0.5, 1, [], 1, True, [])
    monkeypatch.setattr(cd, "research_deadlines", counting_research)

    opp = {"id": "x5", "url": "https://p"}
    cd.find_program_sources(opp, "k", want_dates=True, want_requirements=False)
    cd.find_program_sources(opp, "k", want_dates=True, want_requirements=False)
    assert calls["n"] == 2              # dates-only calls never touch the read-once cache


def test_finder_retiers_captured_by_own_domain(monkeypatch):
    """The date half captures pages untiered (pending); after the finder merges, every page is
    re-tiered by the opportunity's own domain, so its OWN page reads 'official' and is not
    wrongly withheld from students. Regression guard for the T6 tier bug."""
    _clear_cache()
    _fake_finder_halves(
        monkeypatch,
        date_captured=[_src("https://prog.example/apply", "own page")],   # tier=None
        req_captured=[_src("https://lumiere-education.com/g", "third-party guide")])
    *_rest, captured = cd.find_program_sources(
        {"id": "x6", "url": "https://prog.example"}, "k",
        want_dates=True, want_requirements=True)
    tiers = {c.domain: c.tier for c in captured}
    assert tiers["prog.example"] == "official"           # own page, re-tiered from pending
    assert tiers["lumiere-education.com"] == "pending"   # no allowlist in the test env
