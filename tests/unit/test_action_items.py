"""The verification layer that makes a task trustworthy, and the write decision that stops
a bad check destroying a good row.

The regression these pin down is concrete: a student tracking NYU's User Experience Design
summer program was shown "Review prerequisite requirements (Algebra 2)". No such
prerequisite exists on the program's page or in its catalog row. Every test named
`test_algebra` below is that exact failure, approached from a different angle — including
the angle where the model relabels the invented task "generic" to dodge the check.
"""
import pytest

import page_text
import source_capture
from generate_action_items import (
    SOURCE_GENERIC,
    SOURCE_PAGE_EMPTY,
    SOURCE_UNPARSED,
    SOURCE_VERIFIED,
    action_items_write_decision,
    generic_items,
    verify_items,
)


def src(text, tier="official", url="https://www.nyu.edu/example", domain="nyu.edu"):
    """Wrap page text as the CAPTURED sources verify_items now takes (substrate, P6b). Default
    tier 'official' preserves the pre-substrate behaviour of every existing test — they proved
    the verification LOGIC, which is tier-independent; the tier-specific tests pass an explicit
    tier below."""
    return [source_capture.CapturedSource(url, domain, "text/plain", text, tier)]

# A realistic slice of a program page: says plenty, but says nothing about Algebra.
PAGE = """
User Experience Design
Explore the intersection between art, design, and technology at NYU this summer.
Applications for the 2027 session open in November 2026 and close on February 6, 2027.
Students entering grades 10 through 12 in the fall are eligible to apply.
A complete application includes one letter of recommendation and an official transcript.
The program fee is $1,850, and need-based financial aid is available.
"""

OPP = {"id": "ec17542", "name": "User Experience Design", "org": "NYU", "type": "Program",
       "url": "https://www.nyu.edu/example"}

# The page as an official-tier captured source (nyu.edu matches OPP's own domain).
PAGE_SRC = src(PAGE)


def item(text, basis="page", evidence=None, url=None):
    return {"text": text, "basis": basis, "evidence": evidence, "url": url}


# ---------- test 1: a task may not assert what the page does not say ----------

def test_algebra_prerequisite_is_dropped():
    """The original bug. 'algebra' is not on the page, so the task cannot survive however
    confidently it is phrased or however real its quote looks."""
    kept, stats = verify_items(
        [item("Review prerequisite requirements (Algebra 2)",
              evidence="Students entering grades 10 through 12 in the fall are eligible to apply.")],
        OPP, PAGE_SRC)
    assert kept == []
    assert stats["dropped"] == 1


def test_algebra_relabelled_generic_is_still_dropped():
    """The loophole worth guarding: if verification only ran on tasks the model CALLED
    page-backed, relabelling the same invention 'generic' would walk straight through."""
    kept, _ = verify_items(
        [item("Review prerequisite requirements (Algebra 2)", basis="generic")], OPP, PAGE_SRC)
    assert kept == []


def test_invented_test_requirement_is_dropped():
    kept, _ = verify_items([item("Register for the required SAT subject test",
                                 basis="generic")], OPP, PAGE_SRC)
    assert kept == []


def test_real_page_claim_survives():
    kept, stats = verify_items(
        [item("Request one recommendation letter",
              evidence="A complete application includes one letter of recommendation and an official transcript.")],
        OPP, PAGE_SRC)
    assert len(kept) == 1
    assert kept[0]["basis"] == "page"
    assert stats["page_backed"] == 1


def test_generic_task_needs_no_proof():
    """A task making no program-specific claim has nothing to verify. That is the intended
    reading of 'no distinctive tokens', not a hole."""
    kept, _ = verify_items([item("Draft your personal statement", basis="generic")],
                           OPP, PAGE_SRC)
    assert len(kept) == 1
    assert kept[0]["basis"] == "generic"


def test_program_own_name_is_not_a_claim():
    """The row's own name and org are subtracted before the check — the same subtraction
    url_repair.py makes. Otherwise every task would have to prove the page repeats its own
    title."""
    kept, _ = verify_items([item("Submit the NYU User Experience Design application",
                                 basis="generic")], OPP, PAGE_SRC)
    assert len(kept) == 1


# ---------- test 2: a page-backed claim must prove its quote ----------

def test_fabricated_quote_demotes_rather_than_drops():
    """Words all present, quote invented. The task asserts nothing unsupported, so dropping
    it would be too harsh — but it has not proven a specific sentence, so it cannot keep the
    page-backed badge."""
    kept, stats = verify_items(
        [item("Submit an official transcript",
              evidence="Applicants must submit a sealed official transcript by the deadline.")],
        OPP, PAGE_SRC)
    assert len(kept) == 1
    assert kept[0]["basis"] == "generic"
    assert kept[0]["evidence"] is None
    assert stats["demoted"] == 1


def test_short_quote_cannot_prove_anything():
    """A two-word 'quote' appears on almost any program page and would let a fabricated
    claim borrow real words as proof."""
    assert not page_text.quote_is_on_page("eligible", PAGE)


def test_typographic_differences_do_not_fail_an_honest_quote():
    """Curly apostrophes and en-dashes differ between a page's HTML and a model's
    reproduction of it for no meaningful reason. Normalizing them is not fuzzy matching."""
    assert page_text.quote_is_on_page(
        "Students entering grades 10 – 12".replace(" – ", " through "), PAGE)
    assert page_text.quote_is_on_page(
        "APPLICATIONS FOR THE 2027 SESSION OPEN IN NOVEMBER 2026", PAGE)


def test_missing_basis_is_never_read_as_page_backed():
    kept, _ = verify_items([{"text": "Note the application deadline"}], OPP, PAGE_SRC)
    assert kept[0]["basis"] == "generic"


def test_item_cap_is_enforced():
    many = [item(f"Draft your personal statement {i}", basis="generic") for i in range(20)]
    kept, _ = verify_items(many, OPP, PAGE_SRC)
    assert len(kept) <= 5


def test_constructed_url_is_dropped():
    kept, _ = verify_items([item("Complete the application form", basis="generic",
                                 url="not-a-url")], OPP, PAGE_SRC)
    assert kept[0]["url"] is None


# ---------- eligibility-claim detector (T3) ----------

@pytest.mark.parametrize("task", [
    "Have completed Algebra 2 before applying",
    "Maintain a 3.5 GPA",
    "Be a U.S. citizen or permanent resident",
    "Score at least 1400 on the SAT",
    "Be in grades 9 through 12",
    "Must be a high school junior or senior",
    "Complete AP Calculus",
    "Applicants must have taken advanced placement courses",
])
def test_eligibility_claims_are_flagged(task):
    assert page_text.is_eligibility_claim(task) is True


@pytest.mark.parametrize("task", [
    "Review the eligibility requirements",       # safe advice, asserts no condition
    "Read the application page",
    "Submit your transcript",
    "Ask a teacher for a recommendation",
    "Draft your personal statement",
    "Register before the entry deadline",
    "Note the application deadline",
    "Complete the online application form",
])
def test_safe_advice_is_not_flagged(task):
    assert page_text.is_eligibility_claim(task) is False


def test_no_generic_checklist_line_is_an_eligibility_claim():
    """Belt-and-braces: a generic line must never read as an eligibility CONDITION, or it
    could be wrongly dropped if it ever landed on an off-domain source."""
    for opp_type in list(__import__("generate_action_items").GENERIC_BY_TYPE) + [None]:
        for it in generic_items({"type": opp_type, "url": "https://x.example"}):
            assert not page_text.is_eligibility_claim(it["text"]), it["text"]


# ---------- verify_items: trust tiers + the eligibility gate (substrate, P6b) ----------

def _src(text, tier, url="https://src.example/p", domain="src.example"):
    return [source_capture.CapturedSource(url, domain, "text/plain", text, tier)]


ELIG_PAGE = "Applicants must have completed Algebra 2 before the program begins."


def test_eligibility_claim_kept_at_official_tier():
    kept, _ = verify_items(
        [item("Complete Algebra 2 before applying", evidence=ELIG_PAGE)],
        OPP, _src(ELIG_PAGE, "official"))
    assert len(kept) == 1 and kept[0]["basis"] == "page"
    assert kept[0]["source_tier"] == "official"


def test_eligibility_claim_dropped_at_trusted_tier():
    """An aggregator being wrong about a prerequisite is the original harm with a citation —
    so an eligibility claim off the official page is DROPPED, not demoted."""
    kept, stats = verify_items(
        [item("Complete Algebra 2 before applying", evidence=ELIG_PAGE)],
        OPP, _src(ELIG_PAGE, "trusted"))
    assert kept == []
    assert stats["dropped_eligibility"] == 1


LOGI_PAGE = "Submit a one-page research abstract with your application."


def test_non_eligibility_claim_kept_at_trusted_tier_with_provenance():
    kept, _ = verify_items(
        [item("Submit a research abstract", evidence=LOGI_PAGE)],
        OPP, _src(LOGI_PAGE, "trusted", url="https://lumiere-education.com/g",
                  domain="lumiere-education.com"))
    assert len(kept) == 1 and kept[0]["basis"] == "page"
    assert kept[0]["source_tier"] == "trusted"
    assert kept[0]["source_url"] == "https://lumiere-education.com/g"
    assert kept[0]["source_domain"] == "lumiere-education.com"


def test_pending_tier_task_is_parked_not_dropped():
    """A not-yet-approved domain's non-eligibility claim is STORED with tier 'pending'; the
    serve path withholds it (P5) until the operator approves the domain."""
    kept, _ = verify_items(
        [item("Submit a research abstract", evidence=LOGI_PAGE)],
        OPP, _src(LOGI_PAGE, "pending"))
    assert len(kept) == 1 and kept[0]["source_tier"] == "pending"


def test_blocked_source_claim_is_dropped():
    kept, stats = verify_items(
        [item("Submit a research abstract", evidence=LOGI_PAGE)],
        OPP, _src(LOGI_PAGE, "blocked"))
    assert kept == [] and stats["dropped"] == 1


def test_tier_comes_from_the_source_holding_the_quote():
    """Two sources; the quote is only in the trusted one, so the task takes the trusted tier
    and the trusted source's url — not the official source it did not come from."""
    official = source_capture.CapturedSource(
        "https://prog.example/home", "prog.example", "text/plain",
        "Welcome to the program. General overview only.", "official")
    trusted = source_capture.CapturedSource(
        "https://lumiere-education.com/g", "lumiere-education.com", "text/plain",
        LOGI_PAGE, "trusted")
    kept, _ = verify_items(
        [item("Submit a research abstract", evidence=LOGI_PAGE)], OPP, [official, trusted])
    assert len(kept) == 1
    assert kept[0]["source_tier"] == "trusted"
    assert kept[0]["source_domain"] == "lumiere-education.com"


# ---------- the write decision ----------

VERIFIED = [{"text": "Request one recommendation letter", "url": None,
             "basis": "page", "evidence": "one letter of recommendation"}]


def test_verified_result_is_written_and_stamped():
    d = action_items_write_decision(VERIFIED, OPP, page_ok=True, model_ok=True, existing=[])
    assert (d.write, d.stamp, d.source) == (True, True, SOURCE_VERIFIED)


def test_page_read_but_nothing_specific_is_a_real_answer():
    """A page that states no requirements is a finding, not a failure. It stamps, or the row
    re-bills on every run forever."""
    d = action_items_write_decision([], OPP, page_ok=True, model_ok=True, existing=[])
    assert (d.write, d.stamp, d.source) == (True, True, SOURCE_PAGE_EMPTY)
    assert all(i["basis"] == "generic" for i in d.items)


def test_unreadable_page_never_stamps():
    """A fetch failure is a fact about our HTTP client, not the program. Leaving the row due
    costs nothing to retry and is what lets a transient 403 heal."""
    d = action_items_write_decision([], OPP, page_ok=False, model_ok=False, existing=[])
    assert (d.write, d.stamp, d.source) == (True, False, SOURCE_GENERIC)


def test_unreadable_page_does_not_clobber_a_verified_list():
    """The guard that matters most. Without it, one bad-network run replaces every verified
    list in the catalog with a generic checklist."""
    d = action_items_write_decision([], OPP, page_ok=False, model_ok=False,
                                    existing=VERIFIED)
    assert d.write is False
    assert d.items == VERIFIED


def test_unparsed_model_output_keeps_existing_and_stays_due():
    d = action_items_write_decision([], OPP, page_ok=True, model_ok=False,
                                    existing=VERIFIED)
    assert (d.write, d.stamp, d.source) == (False, False, SOURCE_UNPARSED)


def test_unparsed_with_no_existing_list_still_gives_the_student_something():
    d = action_items_write_decision([], OPP, page_ok=True, model_ok=False, existing=[])
    assert (d.write, d.stamp) == (True, False)
    assert d.items


# ---------- the generic checklists must stay generic ----------

@pytest.mark.parametrize("opp_type", ["Program", "Competition", "Conference", "Journal",
                                      "Internship", "Research", "Volunteer", "Unknown"])
def test_generic_checklists_assert_nothing_program_specific(opp_type):
    """These are written with no page in front of us, so every line must be true of any
    program of that kind. The verifier is the arbiter: run each line against an EMPTY page
    and it must still pass, i.e. contain no distinctive claim at all."""
    for it in generic_items({**OPP, "type": opp_type}):
        supported, missing = page_text.claim_is_supported(it["text"], "", OPP["name"],
                                                          OPP["org"])
        assert supported, f"{opp_type}: {it['text']!r} asserts {missing}"
        assert it["basis"] == "generic"
        assert it["evidence"] is None


# ---------- input quality: what the model is allowed to see ----------
#
# The first graded sample (20 rows, 2026-08-24) passed every verification test above and
# still produced a bad feature: 87% of the lines reaching the model were navigation chrome,
# so, told to quote verbatim, it quoted link labels. "Read frequently asked questions" is
# not false — it is on the page — so no amount of verification can catch it. The fix has to
# be at the input.

CHROME_PAGE = """
<html><head><title>Program</title><style>.x{color:red}</style></head><body>
<nav><a href="/">Home</a><a href="/apply">Apply now</a><a href="/faq">FAQ</a></nav>
<header><h1>University</h1><a href="/search">Search</a></header>
<main>
  <p>Applications for the 2027 session open in November and close on February 6.</p>
  <p>Students entering grades 10 through 12 in the fall are eligible to apply.</p>
  <ul><li>One letter of recommendation from a teacher</li>
      <li>An official transcript from your school</li></ul>
  <p>The program fee is $1,850, and need-based financial aid is available to applicants.</p>
</main>
<footer><a href="/privacy">Privacy Policy</a><a href="/terms">Terms</a></footer>
</body></html>
"""


def test_navigation_chrome_never_reaches_the_model():
    text = page_text.html_to_text(CHROME_PAGE)
    for label in ["Apply now", "Privacy Policy", "Home", "Search"]:
        assert label.lower() not in text.lower(), f"chrome leaked: {label}"


def test_real_content_survives_the_cleanup():
    text = page_text.html_to_text(CHROME_PAGE)
    for kept in ["November", "grades 10 through 12", "letter of recommendation",
                 "official transcript", "1,850"]:
        assert kept.lower() in text.lower(), f"content lost: {kept}"


def test_a_link_label_cannot_be_quoted_once_chrome_is_gone():
    """The exact failure from the first sample, end to end: a task lifted off the navbar can
    no longer prove itself, because the navbar is not part of the page any more."""
    text = page_text.html_to_text(CHROME_PAGE)
    assert not page_text.quote_is_on_page("Apply now", text)


def test_short_menu_lines_are_dropped_but_dated_ones_are_kept():
    text = page_text.html_to_text(
        "<main><p>" + "x " * 300 + "</p><div>Alumni</div><div>Deadline: March 1</div></main>")
    assert "Alumni" not in text
    assert "Deadline: March 1" in text


# ---------- the floor on list length ----------

def test_thin_verified_list_is_topped_up_to_a_usable_length():
    """A single proven step is not a checklist. Padding with generic items cannot
    reintroduce the original problem, because a generic item asserts nothing."""
    d = action_items_write_decision(VERIFIED, OPP, page_ok=True, model_ok=True, existing=[])
    assert len(d.items) >= 3
    assert d.items[0] == VERIFIED[0], "verified steps must come first"
    assert all(i["basis"] == "generic" for i in d.items[1:])


def test_top_up_does_not_duplicate_what_is_already_covered():
    kept = [{"text": "Read the eligibility and application page", "url": None,
             "basis": "page", "evidence": "read the eligibility and application page"}]
    out = generic_items(OPP)
    topped = action_items_write_decision(kept, OPP, page_ok=True, model_ok=True,
                                         existing=[]).items
    texts = [i["text"].lower() for i in topped]
    assert len(texts) == len(set(texts))
    assert sum(1 for t in texts if "eligibility and application page" in t) == 1
    assert out  # the generic pool is non-empty, so the assertion above is meaningful


def test_a_full_verified_list_is_not_padded():
    kept = [{"text": f"Submit document {i}", "url": None, "basis": "page",
             "evidence": "e"} for i in range(4)]
    d = action_items_write_decision(kept, OPP, page_ok=True, model_ok=True, existing=[])
    assert len(d.items) == 4
    assert all(i["basis"] == "page" for i in d.items)


# ---------- on-demand 7-day TTL (P1) ----------

def _iso(days_ago):
    import datetime
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days_ago)).isoformat()


def test_fresh_stamp_reads_fresh():
    from app.services.action_items import _is_fresh
    assert _is_fresh(_iso(1)) is True
    assert _is_fresh(_iso(6.9)) is True


def test_stale_stamp_reads_stale():
    from app.services.action_items import _is_fresh
    assert _is_fresh(_iso(8)) is False


def test_missing_or_garbage_stamp_reads_stale():
    # A NULL stamp is the never-verified case (generic-fallback / unparsed never stamp), and
    # must read stale so the row is retried rather than serving an unverified list forever.
    from app.services.action_items import _is_fresh
    assert _is_fresh(None) is False
    assert _is_fresh("") is False
    assert _is_fresh("not-a-date") is False


def test_z_suffixed_stamp_parses():
    from app.services.action_items import _is_fresh
    import datetime
    z = (datetime.datetime.now(datetime.timezone.utc)
         - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _is_fresh(z) is True
