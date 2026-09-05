"""Phase-4 FREE selection cores: dead-link refind targeting and coverage-gap angle proposals."""
from agents import refind_dead_links as rf
from agents import propose_angles as pa


# ---- refind selection -----------------------------------------------------------------

def test_dead_link_flag_is_a_target():
    row = {"quality_flags": ["dead link (404) — program may be real; find the correct URL"]}
    assert rf.is_dead_link_reject(row) is True


def test_dead_link_reason_is_a_target():
    assert rf.is_dead_link_reject({"moderation_reason": "dead-link: page 404s"}) is True


def test_already_attempted_is_excluded():
    row = {"quality_flags": ["dead link (404) — ...", "refind_attempted 20260827"]}
    assert rf.is_dead_link_reject(row) is False


def test_non_dead_link_reject_is_not_a_target():
    assert rf.is_dead_link_reject({"moderation_reason": "third-party-url: listicle"}) is False
    assert rf.is_dead_link_reject({"quality_flags": ["URL is on an unrelated site"]}) is False


def test_select_filters_to_targets():
    rows = [{"id": "a", "moderation_reason": "dead-link"},
            {"id": "b", "moderation_reason": "not-a-fit"},
            {"id": "c", "quality_flags": ["dead link (410) — gone"]}]
    assert [r["id"] for r in rf.select(rows)] == ["a", "c"]


def test_refind_angle_names_program_and_org():
    a = rf.refind_angle("Clark Scholars Program", "Texas Tech University")
    assert "Clark Scholars Program" in a and "Texas Tech University" in a


# ---- refind acceptance gate (tightened Aug-27: registrable domain + sibling guard) -----

_NAME, _ORG = "Clark Scholars Program", "Texas Tech University"
_OLD = "https://www.depts.ttu.edu/honors/clarkscholars/dead.html"


def _no_fetch(*a, **k):
    raise AssertionError("_fetch must not be called before the domain gate passes")


def test_refind_rejects_off_registrable_domain(monkeypatch):
    # A grounding sibling on ANOTHER registrable domain is never accepted — and never even
    # fetched (the northern.virginia.edu ⊃ 'virginia' false positive class).
    monkeypatch.setattr(rf.url_repair, "_fetch", _no_fetch)
    assert rf.best_refound_url(["https://scholarships360.org/clark"], _OLD, _NAME, _ORG, 5) is None
    assert rf.best_refound_url(["https://elsewhere.edu/clark-scholars/"], _OLD, _NAME, _ORG, 5) is None


def test_refind_rejects_empty_old_url(monkeypatch):
    monkeypatch.setattr(rf.url_repair, "_fetch", _no_fetch)
    assert rf.best_refound_url(["https://www.depts.ttu.edu/clark/"], "", _NAME, _ORG, 5) is None


def test_refind_accepts_same_domain_title_proven(monkeypatch):
    good = "https://www.depts.ttu.edu/honors/clarkscholars/"
    monkeypatch.setattr(rf.url_repair, "_fetch", lambda u, t: ("<html/>", good))
    monkeypatch.setattr(rf.url_repair, "page_title",
                        lambda p: "Clark Scholars Program | Texas Tech University")
    assert rf.best_refound_url([good], _OLD, _NAME, _ORG, 5) == good


def test_refind_rejects_when_title_unproven(monkeypatch):
    sib = "https://www.depts.ttu.edu/honors/other/"
    monkeypatch.setattr(rf.url_repair, "_fetch", lambda u, t: ("<html/>", sib))
    monkeypatch.setattr(rf.url_repair, "page_title", lambda p: "Texas Tech Honors College")
    assert rf.best_refound_url([sib], _OLD, _NAME, _ORG, 5) is None


def test_refind_rejects_same_domain_editorial_post(monkeypatch):
    # The live UVA mis-find (Aug-27): same registrable domain as the dead URL and carrying the
    # name's words, but the re-found URL is a /blog/ article — rejected before any fetch.
    monkeypatch.setattr(rf.url_repair, "_fetch", _no_fetch)
    old = "https://northern.virginia.edu/programs/creative-writing/"
    blog = "https://northern.virginia.edu/blog/inspire-spotlight-creative-writing/"
    assert rf.best_refound_url([blog], old, "Creative Writing Program",
                               "University of Virginia", 5) is None


def test_refind_rejects_when_sibling_drops_identity(monkeypatch):
    # Same domain, title proves the name, but the old URL carried an org identity word the new
    # page dropped -> keeps_identity (test 3) catches the sibling.
    old = "https://www.depts.ttu.edu/texas/clark-scholars.html"
    sib = "https://www.depts.ttu.edu/lubbock/clark-scholars/"
    monkeypatch.setattr(rf.url_repair, "_fetch", lambda u, t: ("<html/>", sib))
    monkeypatch.setattr(rf.url_repair, "page_title", lambda p: "Clark Scholars Program")
    assert rf.best_refound_url([sib], old, _NAME, _ORG, 5) is None


# ---- coverage-gap angle proposals -----------------------------------------------------

def _row(type_, is_active=True, season="Summer", subjects=None):
    return {"type": type_, "is_active": is_active, "season": season,
            "subject_tags": subjects or []}


def test_thin_type_is_proposed():
    # Many Programs, but zero Journals -> a Journal angle is proposed.
    rows = [_row("Program") for _ in range(10)]
    props = pa.analyze_gaps(rows, min_per_cell=4)
    assert any("journal" in p.lower() for p in props)
    assert not any(p.lower().startswith("national program") for p in props)  # well-covered


def test_only_active_rows_count_as_coverage():
    # 10 inactive Competitions do not count as coverage -> Competition still proposed.
    rows = [_row("Competition", is_active=False) for _ in range(10)]
    props = pa.analyze_gaps(rows, min_per_cell=4)
    assert any("competition" in p.lower() for p in props)


def test_proposals_are_deduped():
    rows = [_row("Program") for _ in range(10)]
    props = pa.analyze_gaps(rows, min_per_cell=4)
    assert len(props) == len(set(props))


def test_seattle_mode_scopes_the_angle():
    rows = [_row("Program") for _ in range(10)]
    props = pa.analyze_gaps(rows, mode="seattle", min_per_cell=4)
    assert all("Seattle-area" in p for p in props)
