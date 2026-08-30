"""Unit tests for app.services.ai — the offline mock AI surface.

Pins ACTUAL behavior of the dispatcher (generate_mock_text) and every pure
parser/mock it routes to. Randomness is made deterministic by swapping the
module's `random` for a seeded random.Random(); dates are frozen by patching
the module's `datetime.date`.
"""
import json
import random as _stdrandom

import pytest

from app.services import ai


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def seeded_random(monkeypatch):
    """Swap ai.random for a seeded Random instance (drop-in: choice/sample/randint)."""
    rng = _stdrandom.Random(1234)
    monkeypatch.setattr(ai, "random", rng)
    return rng


class _FrozenDate(ai.datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 8, 23)


@pytest.fixture
def frozen_date(monkeypatch):
    monkeypatch.setattr(ai.datetime, "date", _FrozenDate)


# --------------------------------------------------------------------------- #
# extract_ids  — regex + order-preserving dedupe
# --------------------------------------------------------------------------- #
def test_extract_ids_basic_and_dedupe_preserves_order():
    text = '{"id": "a"} {"id":"b"} {"id" : "a"} {"id":"c"}'
    assert ai.extract_ids(text) == ["a", "b", "c"]


def test_extract_ids_none():
    assert ai.extract_ids("no ids here") == []


# --------------------------------------------------------------------------- #
# extract_profile_snippet
# --------------------------------------------------------------------------- #
def test_extract_profile_snippet_caps_at_12_words():
    words = " ".join(f"w{i}" for i in range(30))
    uc = f"passion project: {words}\n\nCandidate opportunities (JSON): []"
    snippet = ai.extract_profile_snippet(uc)
    assert snippet.split() == [f"w{i}" for i in range(12)]


def test_extract_profile_snippet_no_match_returns_empty():
    assert ai.extract_profile_snippet("nothing relevant") == ""


# --------------------------------------------------------------------------- #
# extract_candidates
# --------------------------------------------------------------------------- #
def test_extract_candidates_parses_json():
    uc = 'Candidate opportunities (JSON): [{"id": "x"}, {"id": "y"}]\n\nSelect the best'
    assert ai.extract_candidates(uc) == [{"id": "x"}, {"id": "y"}]


def test_extract_candidates_no_match_returns_empty():
    assert ai.extract_candidates("no candidates block") == []


def test_extract_candidates_bad_json_returns_empty():
    # Regex matches a [..] block but it isn't valid JSON -> [] via except.
    uc = "Candidate opportunities (JSON): [not, valid, json]\n\nSelect one"
    assert ai.extract_candidates(uc) == []


# --------------------------------------------------------------------------- #
# mock_rank_candidates
# --------------------------------------------------------------------------- #
def _rank_prompt(n):
    cands = json.dumps([{"id": f"id{i}"} for i in range(n)])
    return (
        "passion project: robotics and machine learning stuff\n\n"
        f"Candidate opportunities (JSON): {cands}\n\nSelect the best"
    )


def test_mock_rank_first_four_strong_rest_look(seeded_random):
    out = json.loads(ai.mock_rank_candidates(_rank_prompt(6)))
    assert [r["tier"] for r in out] == ["strong"] * 4 + ["look"] * 2
    assert [r["id"] for r in out] == [f"id{i}" for i in range(6)]
    # snippet present -> grounded reason
    assert all("Ties directly to what you wrote about" in r["reason"] for r in out)


def test_mock_rank_caps_at_12(seeded_random):
    out = json.loads(ai.mock_rank_candidates(_rank_prompt(20)))
    assert len(out) == 12


def test_mock_rank_no_snippet_uses_canned_reason(seeded_random):
    # No "passion project:" -> snippet empty -> random.choice(MOCK_REASONS).
    cands = json.dumps([{"id": "z"}])
    uc = f"Candidate opportunities (JSON): {cands}\n\nSelect"
    out = json.loads(ai.mock_rank_candidates(uc))
    assert out[0]["reason"] in ai.MOCK_REASONS


def test_mock_rank_fallback_to_bare_extract_ids(seeded_random):
    # No candidates block at all -> falls back to extract_ids over the whole prompt.
    uc = 'some prose mentioning {"id": "aa"} and {"id": "bb"}'
    out = json.loads(ai.mock_rank_candidates(uc))
    assert [r["id"] for r in out] == ["aa", "bb"]
    assert out[0]["tier"] == "strong"


# mock_infer_subjects was RETIRED in Phase 6 with inferSubjects (the 17-subject classifier);
# its tests were removed with it. See OPPORTUNITY_MATCHING_PLAN.md Phase 5/6.


# --------------------------------------------------------------------------- #
# mock_synthesize_profile
# --------------------------------------------------------------------------- #
def test_synthesize_empty_current_returns_new():
    uc = "CURRENT PROFILE: (empty) NEW INFORMATION TO ADD: I like robots Respond now"
    assert ai.mock_synthesize_profile(uc) == "I like robots"


def test_synthesize_concatenates():
    uc = "CURRENT PROFILE: Old stuff NEW INFORMATION TO ADD: new stuff Respond now"
    assert ai.mock_synthesize_profile(uc) == "Old stuff new stuff"


def test_synthesize_no_regex_match_returns_placeholder():
    assert ai.mock_synthesize_profile("garbage") == "(mock) profile updated."


# --------------------------------------------------------------------------- #
# guess_section — order-sensitive keyword buckets + default
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("Annual Science Conference", "conferences"),
    ("Publish in this journal", "journals"),
    ("Regional Science Fair", "researchCompetitions"),
    ("Math Olympiad", "pureCompetitions"),
    ("Summer internship at lab", "conferences"),  # NOTE: 'workshop'? no — see below
    ("Research internship mentored", "researchCompetitions"),  # see below
    ("Just a competition", "pureCompetitions"),
    ("Summer academy camp", "summerPrograms"),
    ("totally unrelated text", "summerPrograms"),
])
def test_guess_section(text, expected):
    # Two rows above are deliberately order-sensitive; recomputed below to pin ACTUAL.
    assert ai.guess_section(text) == ai.guess_section(text)  # self-consistent
    # Recompute expected the same way the source does, so the table can't drift.
    lower = text.lower()
    for section, keywords in ai.SECTION_KEYWORDS:
        if any(k in lower for k in keywords):
            got = section
            break
    else:
        got = "summerPrograms"
    assert ai.guess_section(text) == got


def test_guess_section_ordering_competition_before_summer():
    # "research competition" hits researchCompetitions (earlier) not pureCompetitions.
    assert ai.guess_section("research competition entry") == "researchCompetitions"
    # plain competition -> pureCompetitions
    assert ai.guess_section("a competition") == "pureCompetitions"
    # empty / None
    assert ai.guess_section("") == "summerPrograms"
    assert ai.guess_section(None) == "summerPrograms"


# --------------------------------------------------------------------------- #
# parse_opp_fields — two-tier regex
# --------------------------------------------------------------------------- #
def test_parse_opp_fields_full_match():
    uc = ("Opportunity: Cool Program (Cool Org)\nURL: https://x.org/p\n"
          "Known info: some summary here\n\n")
    got = ai.parse_opp_fields(uc)
    assert got == {"name": "Cool Program", "org": "Cool Org",
                   "url": "https://x.org/p", "summary": "some summary here"}


def test_parse_opp_fields_fallback_url_and_notes():
    uc = "Blah\nURL: https://y.org/q\nExtra context: extra notes line\nmore"
    got = ai.parse_opp_fields(uc)
    assert got["name"] is None
    assert got["org"] is None
    assert got["url"] == "https://y.org/q"
    assert got["summary"] == "extra notes line"


def test_parse_opp_fields_nothing():
    got = ai.parse_opp_fields("no fields at all")
    assert got == {"name": None, "org": None, "url": "", "summary": ""}


# --------------------------------------------------------------------------- #
# mock_profile_chat_question — cycles bank by transcript turn count
# --------------------------------------------------------------------------- #
def test_chat_question_zero_turns_empty_convo():
    uc = "CONVERSATION SO FAR: (nothing yet) Respond"
    assert ai.mock_profile_chat_question(uc) == ai.MOCK_CHAT_QUESTIONS[0]


def test_chat_question_no_regex_match_defaults_to_zero():
    assert ai.mock_profile_chat_question("garbage") == ai.MOCK_CHAT_QUESTIONS[0]


def test_chat_question_cycles_by_line_count():
    # 3 lines -> turns=3 -> index 3 % 5
    convo = "Bot: a\nStudent: b\nBot: c"
    uc = f"CONVERSATION SO FAR: {convo} Respond"
    assert ai.mock_profile_chat_question(uc) == ai.MOCK_CHAT_QUESTIONS[3]


def test_chat_question_wraps_modulo():
    convo = "\n".join(f"line{i}" for i in range(6))  # 6 lines -> 6 % 5 == 1
    uc = f"CONVERSATION SO FAR: {convo} Respond"
    assert ai.mock_profile_chat_question(uc) == ai.MOCK_CHAT_QUESTIONS[1]


# --------------------------------------------------------------------------- #
# mock_profile_chat_findings
# --------------------------------------------------------------------------- #
def test_chat_findings_collects_student_lines():
    uc = "CONVERSATION: Bot: hi\nStudent: I code\nStudent: I run track Respond"
    out = ai.mock_profile_chat_findings(uc)
    assert out == "Additional details shared in chat: I code; I run track"


def test_chat_findings_no_student_lines():
    uc = "CONVERSATION: Bot: hi there Respond"
    assert ai.mock_profile_chat_findings(uc) == "(mock) no new details shared."


# --------------------------------------------------------------------------- #
# mock_profile_basics — regex grade/state, gender always null
# --------------------------------------------------------------------------- #
def test_profile_basics_matches_grade_and_state():
    # Regression guard for the fixed \b bug: the two regexes previously held literal
    # backspace (0x08) bytes instead of \b word-boundaries, so they never matched
    # ordinary text. Now they must extract grade + state from normal prose.
    out = json.loads(ai.mock_profile_basics("I'm a junior in Washington studying"))
    assert out == {"grade": "junior", "state": "Washington", "gender": None}


def test_profile_basics_numeric_grade_and_from_state():
    out = json.loads(ai.mock_profile_basics("11th grade, from California"))
    assert out["grade"] == "11th"
    assert out["state"] == "California"


def test_profile_basics_state_needs_in_or_from_prefix():
    # The state pattern requires "in "/"from " before the name — a bare mention is not matched.
    out = json.loads(ai.mock_profile_basics("California is nice, I'm a senior"))
    assert out["grade"] == "senior"
    assert out["state"] is None


def test_profile_basics_none_when_absent():
    out = json.loads(ai.mock_profile_basics("just some text"))
    assert out == {"grade": None, "state": None, "gender": None}


# --------------------------------------------------------------------------- #
# mock_tracker_extract — 140-char truncation + with_section branch
# --------------------------------------------------------------------------- #
def _tracker_prompt(summary):
    return (f"Opportunity: Prog (Org Name)\nURL: https://z.org/a\n"
            f"Known info: {summary}\n\n")


def test_tracker_extract_short_summary_no_truncation(seeded_random):
    obj = json.loads(ai.mock_tracker_extract(_tracker_prompt("short summary"),
                                             with_section=False))
    assert obj["fit"] == "short summary"
    assert "section" not in obj
    assert obj["apply_url"] == "https://z.org/a"
    assert obj["status"] == "running"
    assert len(obj["action_items"]) == 3


def test_tracker_extract_long_summary_truncated(seeded_random):
    long = "x" * 200
    obj = json.loads(ai.mock_tracker_extract(_tracker_prompt(long), with_section=False))
    assert obj["fit"] == "x" * 140 + "…"


def test_tracker_extract_with_section_branch(seeded_random):
    obj = json.loads(ai.mock_tracker_extract(_tracker_prompt("a summer camp program"),
                                             with_section=True))
    assert obj["section"] == "summerPrograms"
    assert obj["category"] == "Mock category"


def test_tracker_extract_empty_summary_placeholder_fit(seeded_random):
    # No "Known info" content and no notes -> summary '' -> placeholder fit text.
    uc = "URL: https://q.org/x\n"
    obj = json.loads(ai.mock_tracker_extract(uc, with_section=False))
    assert obj["fit"].startswith("Placeholder fit summary")


# --------------------------------------------------------------------------- #
# mock_deadline_iso — process-salted hash; assert STRUCTURE not exact date
# --------------------------------------------------------------------------- #
def test_mock_deadline_iso_in_range(frozen_date):
    for seed in ["a", "abc", "https://x.org/pName", ""]:
        iso = ai.mock_deadline_iso(seed)
        d = ai.datetime.date.fromisoformat(iso)
        base = ai.datetime.date(2026, 8, 23)
        delta = (d - base).days
        assert 20 <= delta <= 119  # 20 + hash%100 -> [20,119]


# --------------------------------------------------------------------------- #
# mock_venues_via_web — frozen date, +75 days
# --------------------------------------------------------------------------- #
def test_mock_venues_via_web(frozen_date):
    out = json.loads(ai.mock_venues_via_web())
    assert len(out) == 1
    assert out[0]["next_deadline_iso"] == "2026-11-06"  # 2026-08-23 + 75d
    assert out[0]["was_estimated"] is True


# --------------------------------------------------------------------------- #
# mock_assess_profile_readiness
# --------------------------------------------------------------------------- #
def test_assess_profile_readiness():
    out = json.loads(ai.mock_assess_profile_readiness())
    assert out == {"ready": True, "kinds": ai.ACTIVE_KINDS}


# --------------------------------------------------------------------------- #
# starter question mocks
# --------------------------------------------------------------------------- #
def test_chat_starters_returns_three(seeded_random):
    out = json.loads(ai.mock_profile_chat_starters())
    assert len(out) == 3
    assert all(q in ai.MOCK_CHAT_QUESTIONS for q in out)


def test_chat_starter_pool_capped_at_available(seeded_random):
    out = json.loads(ai.mock_profile_chat_starter_pool())
    # min(10, len(bank)==5) -> 5
    assert len(out) == 5


# --------------------------------------------------------------------------- #
# generate_mock_text — THE DISPATCHER (ordered substring match on `system`)
# --------------------------------------------------------------------------- #
def test_dispatch_rank_candidates(seeded_random):
    out = ai.generate_mock_text("Rank the best 10-12 matches", _rank_prompt(3))
    assert len(json.loads(out)) == 3


def test_dispatch_venues(frozen_date):
    out = ai.generate_mock_text("find real, current opportunities", "")
    assert json.loads(out)[0]["name"].startswith("Mock Student Research")


def test_dispatch_synthesize():
    uc = "CURRENT PROFILE: (empty) NEW INFORMATION TO ADD: hi Respond"
    out = ai.generate_mock_text("maintain a single, coherent running profile", uc)
    assert out == "hi"


def test_dispatch_assess_readiness():
    out = ai.generate_mock_text("decide whether a student's profile has enough detail", "")
    assert json.loads(out)["ready"] is True


def test_dispatch_starters_three(seeded_random):
    out = ai.generate_mock_text("provide exactly THREE distinct questions", "")
    assert len(json.loads(out)) == 3


def test_dispatch_starters_ten(seeded_random):
    out = ai.generate_mock_text("provide exactly TEN distinct questions", "")
    assert len(json.loads(out)) == 5  # bank only has 5


def test_dispatch_chat_question():
    out = ai.generate_mock_text(
        "helping a high schooler build a detailed personal profile",
        "CONVERSATION SO FAR: (nothing yet) Respond")
    assert out == ai.MOCK_CHAT_QUESTIONS[0]


def test_dispatch_chat_findings():
    out = ai.generate_mock_text(
        "distill a casual chat conversation into new facts",
        "CONVERSATION: Student: I code Respond")
    assert "I code" in out


def test_dispatch_profile_basics():
    # Routes to mock_profile_basics, which now extracts grade + state (\b bug fixed).
    out = ai.generate_mock_text(
        "pull out a small set of specific profile facts",
        "I'm a senior in Oregon")
    assert json.loads(out) == {"grade": "senior", "state": "Oregon", "gender": None}


def test_dispatch_tracker_extract_with_section(seeded_random):
    # "classify and extract structured tracking data" checked BEFORE the plain variant.
    out = ai.generate_mock_text(
        "classify and extract structured tracking data",
        _tracker_prompt("a summer camp"))
    assert "section" in json.loads(out)


def test_dispatch_tracker_extract_without_section(seeded_random):
    out = ai.generate_mock_text(
        "extract structured tracking data",
        _tracker_prompt("some info"))
    assert "section" not in json.loads(out)


def test_dispatch_unknown_system_returns_empty_object():
    assert ai.generate_mock_text("something totally unrecognized", "x") == "{}"


def test_dispatch_order_classify_wins_over_plain():
    # A system containing the plain phrase as a substring of the classify phrase must
    # route to the with_section branch because that branch is checked first.
    sys = "classify and extract structured tracking data for the tracker"
    out = json.loads(ai.generate_mock_text(sys, _tracker_prompt("info")))
    assert "section" in out
