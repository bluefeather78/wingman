"""Phase-3 merge + in-run twin collapse. Both pure functions, hermetic."""
import scrape_opportunities as so


# ---- merge_row ------------------------------------------------------------------------

def test_merge_replaces_junk_name_when_candidate_is_title_proven():
    existing = {"name": "Columbia", "org": "", "summary": "", "subject_tags": []}
    candidate = {"name": "Clark Scholars Program", "org": "Columbia University",
                 "summary": "A research program", "subject_tags": ["earth science"]}
    title = "Clark Scholars Program — Columbia University"
    patch, notes = so.merge_row(candidate, existing, title)
    assert patch["name"] == "Clark Scholars Program"
    assert any("name was 'Columbia'" in n for n in notes)


def test_merge_keeps_incumbent_name_when_it_is_proven():
    existing = {"name": "Clark Scholars Program", "org": ""}
    candidate = {"name": "Clark Scholars Summer Program", "org": ""}
    title = "Clark Scholars Program"
    patch, _ = so.merge_row(candidate, existing, title)
    assert "name" not in patch  # incumbent already proves -> never replaced (never on length)


def test_merge_keeps_specific_incumbent_over_title_literal_subset():
    # The incumbent has a full, specific name whose page title spells a word differently
    # ("Mathematics" vs "Math"); it must NOT be traded for the shorter loser name that the
    # title happens to match verbatim. Only a <2-identity-word junk incumbent is replaced.
    existing = {"name": "Michigan State Honors Science and Mathematics Program",
                "org": "Michigan State University"}
    candidate = {"name": "Honors Science and Math Program", "org": "Michigan State University"}
    patch, _ = so.merge_row(candidate, existing, "Honors Science and Math Program")
    assert "name" not in patch


def test_merge_fills_only_empty_fields():
    existing = {"name": "X Program Institute", "org": "Real Org", "summary": "",
                "eligibility": None, "contact_email": "have@x.org", "subject_tags": []}
    candidate = {"name": "X Program Institute", "org": "Different Org", "summary": "New summary",
                 "eligibility": "Grades 9-12", "contact_email": "new@x.org",
                 "subject_tags": ["math"]}
    patch, _ = so.merge_row(candidate, existing, "X Program Institute")
    assert patch["summary"] == "New summary"          # was empty -> filled
    assert patch["eligibility"] == "Grades 9-12"       # was None -> filled
    assert patch["subject_tags"] == ["math"]           # was [] -> filled
    assert "org" not in patch                          # already had a value -> untouched
    assert "contact_email" not in patch                # already had a value -> untouched


def test_merge_never_touches_other_agent_columns():
    existing = {"name": "A Real Program Name", "status": "running", "important_dates": [{"x": 1}],
                "review_status": "legit", "link_status": "live", "id": "ec1", "source": "old"}
    candidate = {"name": "A Real Program Name", "status": "not_running", "important_dates": [],
                 "review_status": "junk", "link_status": "dead", "id": "ec2", "source": "new"}
    patch, _ = so.merge_row(candidate, existing, "A Real Program Name")
    for forbidden in ("status", "important_dates", "review_status", "link_status", "id", "source"):
        assert forbidden not in patch


def test_merge_empty_patch_when_nothing_improves():
    existing = {"name": "Full Program Name", "org": "Org", "summary": "s"}
    candidate = {"name": "Full Program Name", "org": "Org", "summary": "s"}
    patch, notes = so.merge_row(candidate, existing, "Full Program Name")
    assert patch == {} and notes == []


# ---- collapse_intra_run_twins ---------------------------------------------------------

def _r(rid, name, url):
    return {"id": rid, "name": name, "url": url}


def test_collapse_keeps_best_url_of_same_domain_twins():
    rows = [_r("a", "Clark Scholars Program", "https://x.edu/clark/apply"),
            _r("b", "Clark Scholars Program", "https://x.edu/clark")]
    flags = {"a": [so.FLAG_TITLE_UNPROVEN], "b": []}   # b proven + not low-value -> b wins
    kept, collapsed = so.collapse_intra_run_twins(rows, flags)
    assert [k["id"] for k in kept] == ["b"]
    assert collapsed[0] == {"loser": "a", "winner": "b", "name": "Clark Scholars Program"}


def test_collapse_ignores_different_domains():
    rows = [_r("a", "Clark Scholars Program", "https://x.edu/clark"),
            _r("b", "Clark Scholars Program", "https://y.edu/clark")]
    kept, collapsed = so.collapse_intra_run_twins(rows, {})
    assert len(kept) == 2 and collapsed == []


def test_collapse_ignores_dissimilar_names_on_same_domain():
    rows = [_r("a", "Clark Scholars Program", "https://x.edu/clark"),
            _r("b", "Marine Biology Field Institute", "https://x.edu/marine")]
    kept, collapsed = so.collapse_intra_run_twins(rows, {})
    assert len(kept) == 2 and collapsed == []


def test_collapse_prefers_shallower_path_when_both_proven():
    rows = [_r("a", "Clark Scholars Program", "https://x.edu/programs/summer/clark"),
            _r("b", "Clark Scholars Program", "https://x.edu/clark")]
    kept, _ = so.collapse_intra_run_twins(rows, {"a": [], "b": []})
    assert [k["id"] for k in kept] == ["b"]  # shallower path wins the tiebreak
