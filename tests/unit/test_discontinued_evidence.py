"""`status=not_running` needs evidence on the program's own page — audit finding 4.9.

Two writers set this column. agents/check_deadlines.py has always demanded a verbatim
discontinuation quote found on a fetched page. agents/check_links.py used to write it off a
regex over the row's `summary` — model-written prose that every scrape rewrites, checked
against nothing at all.

The consequence is bigger than the row count suggests: not_running pulls a program out of Fresh
Finds and matching, and EMPTY_IS_VALID_STATUS then treats it as authoritative, so
deadline_write_decision's empty-result guard stops protecting that row's dates.
"""
import pytest

from agents import check_links as cl


def _fetch(text):
    return lambda url, timeout=None: (text, "ok")


def test_page_confirms_so_the_phrase_is_returned():
    got = cl.discontinued_on_page(
        "https://x.org/p", fetch=_fetch("The program has been discontinued as of 2026."))
    assert got and "discontinu" in got.lower()


def test_page_does_not_confirm():
    assert cl.discontinued_on_page(
        "https://x.org/p", fetch=_fetch("Applications open in January. Apply now!")) is None


def test_unreachable_page_is_not_evidence_of_death():
    """A site that blocks us, times out or 404s says nothing about whether the program runs."""
    def boom(url, timeout=None):
        raise TimeoutError("read timed out")
    assert cl.discontinued_on_page("https://x.org/p", fetch=boom) is None
    assert cl.discontinued_on_page("https://x.org/p", fetch=_fetch("")) is None
    assert cl.discontinued_on_page("https://x.org/p", fetch=_fetch(None)) is None


def test_the_measured_false_positive_no_longer_writes_status():
    """The audit's own example. 'has closed' matches the summary regex, but it describes one
    cycle of a healthy annual program — and the page says so."""
    row = {"summary": "Applications for the 2026 cohort has closed; the program runs annually.",
           "url": "https://x.org/p"}
    assert cl.discontinued_phrase(row), "the summary signal should still fire"
    page = ("Applications for the 2026 cohort are now closed. "
            "The 2027 application will open in October. Join the mailing list.")
    assert cl.discontinued_on_page(row["url"], fetch=_fetch(page)) is None


def test_summary_signal_still_fires_independently():
    """discontinued_phrase stays a signal — it is what decides to go and LOOK at the page."""
    assert cl.discontinued_phrase({"summary": "This camp is no longer running."})
    assert cl.discontinued_phrase({"summary": "Runs every July."}) is None
    assert cl.discontinued_phrase({}) is None


def test_both_flags_are_stripped_by_the_agents_own_prefix():
    """A re-run must be able to clear either flag, or a row keeps a stale verdict forever."""
    assert any(cl.FLAG_DISCONTINUED.startswith(p) for p in cl._OWNED_PREFIXES)
    assert any(cl.FLAG_DISCONTINUED_UNCONFIRMED.startswith(p) for p in cl._OWNED_PREFIXES)


def test_the_two_flags_say_different_things():
    """A reviewer must be able to tell 'we confirmed it' from 'we could not'."""
    assert cl.FLAG_DISCONTINUED != cl.FLAG_DISCONTINUED_UNCONFIRMED
    assert "does not say so" in cl.FLAG_DISCONTINUED_UNCONFIRMED


@pytest.mark.parametrize("page,expected", [
    ("The competition has ended and will not be offered again.", True),
    ("This program is permanently closed.", True),
    ("Please discontinue use of the old form.", True),   # regex is deliberately broad HERE...
    ("Winners announced in May. Registration closes March 1.", False),
])
def test_regex_still_applies_to_page_text(page, expected):
    """The same narrow regex now runs over the PAGE. It is still imperfect — 'discontinue use
    of the old form' matches — but a false positive now needs the phrase on the real page AND
    in the summary AND a blank status, instead of just the summary."""
    assert bool(cl.discontinued_on_page("https://x.org", fetch=_fetch(page))) is expected
