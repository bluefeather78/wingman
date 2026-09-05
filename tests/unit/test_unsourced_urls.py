"""A model-typed URL never reaches the catalog — audit finding 4.1.

"A model-typed URL is not trustworthy anywhere in this repo" is the scraper rewrite's central
finding, and refresh_opportunities already stopped writing `url` for it. Two paths still did.
"""
import pytest

from agents import scrape_opportunities as so


# --------------------------------------------------------------------------------------
# reconcile_url still LABELS the case — the rejection happens at the call site, so the
# decider stays pure and gradeable by eval/grade_scraper_batch.
# --------------------------------------------------------------------------------------

def test_unsourced_is_still_flagged_by_the_decider():
    url, flags = so.reconcile_url("https://model-remembered.org/program", [], [])
    assert url == "https://model-remembered.org/program"
    assert so.FLAG_URL_UNSOURCED in flags


def test_a_retrieved_url_is_not_unsourced():
    url, flags = so.reconcile_url("https://real.org/p", ["https://real.org/p"], [])
    assert url == "https://real.org/p" and flags == []


def test_a_same_host_retrieved_page_is_preferred_not_rejected():
    """Right site, wrong path — the off-by-one-segment signature of a remembered URL. The
    retrieved page wins and the row is kept; this is NOT the unsourced case."""
    url, flags = so.reconcile_url("https://real.org/summer-program",
                                  ["https://real.org/programs/summer"], [])
    assert url == "https://real.org/programs/summer"
    assert so.FLAG_URL_UNSOURCED not in flags
    assert so.FLAG_URL_REPLACED in flags


def test_grounding_span_url_wins_and_is_never_unsourced():
    url, flags = so.reconcile_url("https://guess.org/p", [], ["https://grounded.org/p"])
    assert url == "https://grounded.org/p"
    assert so.FLAG_URL_UNSOURCED not in flags


def test_the_scrape_loop_rejects_rather_than_flags(tmp_path):
    """The call site must CLEAR the URL, so the candidate falls into the stage-1b branch
    instead of being staged for insert. Asserted on the source, because main() is a
    500-line loop with no seam and this single line is the entire fix."""
    import inspect
    src = inspect.getsource(so.main)
    assert "if url and FLAG_URL_UNSOURCED in flags:" in src, (
        "the unsourced URL is no longer rejected at the call site")
    assert 'url, flags = "", [f for f in flags if f != FLAG_URL_UNSOURCED]' in src, (
        "the URL must be CLEARED (handing the candidate to stage 1b), not merely counted")
    assert "unsourced_rejected" in src


def test_the_flag_is_never_written_onto_a_stored_row():
    """FLAG_URL_UNSOURCED survives only as an internal signal between reconcile_url and the
    rejection. If it reappears in the row-building path, rows are being stored again."""
    import inspect
    assert so.FLAG_URL_UNSOURCED not in inspect.getsource(so.build_row)
    assert so.FLAG_URL_UNSOURCED not in inspect.getsource(so.resolve_url_truth)


# --------------------------------------------------------------------------------------
# refind_dead_links deduped against inactive rows only
# --------------------------------------------------------------------------------------

def test_refind_dedupes_against_the_whole_catalog():
    """Its selection is is_active=eq.false, so deduping against that set never consulted a
    live row — a re-found URL already held by an ACTIVE row was inserted as a duplicate."""
    import inspect
    from agents import refind_dead_links as rd
    src = inspect.getsource(rd.main)
    assert 'find_duplicates(new_url, name, catalog)' in src, (
        "still deduping the re-found URL against the inactive-only selection")
    assert '"select": "id,name,url"' in src, (
        "the catalog dedupe set needs name and url, not just id")


def test_refind_recovers_banked_cost_on_failure():
    import inspect
    from agents import refind_dead_links as rd
    assert "agent_common.banked_cost(e)" in inspect.getsource(rd.main)
