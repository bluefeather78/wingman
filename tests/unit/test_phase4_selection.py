"""Phase-4 FREE selection cores: dead-link refind targeting and coverage-gap angle proposals."""
import refind_dead_links as rf
import propose_angles as pa


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
