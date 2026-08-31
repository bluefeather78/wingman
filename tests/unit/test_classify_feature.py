"""Unit tests for app.core.classify_feature and app.core.provider_for_model.

classify_feature is an ORDERED substring dispatch over _FEATURE_SIGNATURES: the first
needle that appears in the system prompt wins. Order is load-bearing in three places,
each of which gets a dedicated case here:
  * the two tracker_extract signatures (longer prefix first),
  * chat_starters before profile_chat (a chat-starter prompt also contains the
    profile_chat needle), and
  * the two ranking signatures.
"""
import pytest

import app.core as core


# ---------------------------------------------------------------------------
# classify_feature — every signature routes to its documented feature.
#
# Each case embeds the exact needle from _FEATURE_SIGNATURES so a change to a
# signature string that silently re-routes spend is caught here.
# ---------------------------------------------------------------------------
SIGNATURE_CASES = [
    # First in the source too: the merged pass does the work of the tag-extraction and
    # profile-basics prompts, so its wording matches theirs and precedence decides.
    ("pulling out everything an opportunity-matching app needs",   "profile_extract"),
    ("infer which subject categories",                            "infer_subjects"),
    ("Rank the best 10-12 matches",                               "ranking"),
    ("find real, current",                                        "venue_search"),
    ("maintain a single, coherent running profile",               "profile_synthesis"),
    ("decide whether a student's profile has enough detail",      "profile_readiness"),
    ("exactly THREE distinct",                                    "chat_starters"),
    ("exactly TEN distinct",                                      "chat_starters"),
    ("helping a high schooler build a detailed personal profile", "profile_chat"),
    ("distill a casual chat conversation into new facts",         "chat_findings"),
    ("classify and extract structured tracking data",             "tracker_extract"),
    ("extract structured tracking data",                          "tracker_extract"),
    ("pull out everything that would help understand this student", "resume_import"),
    ("pull out a small set of specific profile facts",            "profile_basics"),
    ("helping a student find the best-fit extracurricular",       "ranking"),
    ("interests/goals to the best opportunities",                 "tag_intent"),
    ("Write directly to them in second person",                   "tag_suggestions"),
    ("extracting specific interests, goals, and pursuits",        "infer_subjects"),
    ("building a high schooler's CURATED shortlist",              "match_curation"),
    ("narrowing a high schooler's list",                          "match_funnel"),
]


def test_real_matching_prompts_classify():
    """The actual Phase-1 prompt constants must route to their feature, not 'other' — a
    reworded opening line would silently dump match spend into 'other'."""
    from app.services.curation import CURATION_SYSTEM
    from app.services.funnel import FUNNEL_QUESTION_SYSTEM
    assert core.classify_feature(CURATION_SYSTEM) == "match_curation"
    assert core.classify_feature(FUNNEL_QUESTION_SYSTEM) == "match_funnel"


@pytest.mark.parametrize("needle,expected", SIGNATURE_CASES)
def test_each_signature_routes_to_its_feature(needle, expected):
    prompt = f"You are a helpful assistant. Your job is to {needle} for the student."
    assert core.classify_feature(prompt) == expected


def test_every_signature_in_the_source_is_covered_by_a_case():
    """Guard against a signature being added to the source without a test here."""
    source_needles = [needle for needle, _ in core._FEATURE_SIGNATURES]
    tested_needles = [needle for needle, _ in SIGNATURE_CASES]
    assert source_needles == tested_needles


def test_every_feature_key_is_a_known_label():
    """Every key a signature maps to must exist in FEATURE_LABELS (plus 'other')."""
    for _needle, key in core._FEATURE_SIGNATURES:
        assert key in core.FEATURE_LABELS


# ---------------------------------------------------------------------------
# Ordering — the cases that would misroute if the ordered dispatch broke.
# ---------------------------------------------------------------------------
def test_chat_starters_wins_over_profile_chat_when_both_needles_present():
    """A chat-starter prompt contains BOTH the chat_starters needle and the generic
    profile_chat needle. chat_starters is listed first and must win; if the order
    flipped this would misroute to profile_chat."""
    prompt = (
        "You are helping a high schooler build a detailed personal profile. "
        "Generate exactly THREE distinct opening questions."
    )
    # sanity: the profile_chat needle really is present, so this is a real ordering test
    assert "helping a high schooler build a detailed personal profile" in prompt
    assert core.classify_feature(prompt) == "chat_starters"


def test_ten_distinct_pool_also_routes_to_chat_starters_over_profile_chat():
    prompt = (
        "You are helping a high schooler build a detailed personal profile. "
        "Produce exactly TEN distinct starter questions for the cached pool."
    )
    assert core.classify_feature(prompt) == "chat_starters"


def test_longer_tracker_extract_signature_is_reachable():
    """The 'classify and extract structured tracking data' prompt contains the shorter
    'extract structured tracking data' as a substring. Both map to tracker_extract, so
    the result is stable either way — but the longer signature must be listed first
    (the same ordering constraint generate_mock_text lives under). Assert both prompts
    resolve to tracker_extract."""
    longer = "You will classify and extract structured tracking data from the message."
    shorter = "Please extract structured tracking data from this text."
    assert core.classify_feature(longer) == "tracker_extract"
    assert core.classify_feature(shorter) == "tracker_extract"


def test_ranking_matched_on_stable_opening_line():
    """The ranking prompt only contains 'Rank the best 10-12 matches' on one branch, so
    it also gets matched on its stable opening line. That opening line must not be
    shadowed by an earlier signature."""
    prompt = (
        "You are helping a student find the best-fit extracurricular opportunities "
        "given their profile."
    )
    assert core.classify_feature(prompt) == "ranking"


# ---------------------------------------------------------------------------
# The 'other' bucket.
# ---------------------------------------------------------------------------
def test_unrecognized_prompt_buckets_to_other():
    assert core.classify_feature("Some totally unrelated system prompt.") == "other"


def test_none_prompt_is_other():
    assert core.classify_feature(None) == "other"


def test_empty_string_is_other():
    assert core.classify_feature("") == "other"


# ---------------------------------------------------------------------------
# provider_for_model — model id first, surface fallback, then 'unknown'.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model,expected", [
    ("claude-haiku-4-5-20251001", "anthropic"),
    ("claude-sonnet-4-6", "anthropic"),
    ("CLAUDE-HAIKU", "anthropic"),          # case-insensitive
    ("gemini-3.6-flash", "google"),
    ("gemini-3.5-flash-lite", "google"),
    ("  gemini-3.6-flash  ", "google"),     # trimmed
])
def test_provider_from_model_prefix(model, expected):
    assert core.provider_for_model(model) == expected


def test_model_prefix_wins_over_surface():
    """A recognised model id takes precedence over the surface fallback."""
    assert core.provider_for_model("claude-haiku-4-5", surface="gemini") == "anthropic"


@pytest.mark.parametrize("surface,expected", [
    ("claude", "anthropic"),
    ("deadline_check", "anthropic"),
    ("gemini", "google"),
])
def test_surface_fallback_when_model_blank(surface, expected):
    """Rows written before the model column existed carry '' model — the surface
    fallback keeps them in the right provider bucket instead of 'unknown'."""
    assert core.provider_for_model("", surface=surface) == expected
    assert core.provider_for_model(None, surface=surface) == expected


def test_unknown_model_and_unknown_surface_is_unknown():
    assert core.provider_for_model("gpt-4o", surface=None) == "unknown"
    assert core.provider_for_model("gpt-4o", surface="mystery") == "unknown"


def test_empty_model_and_no_surface_is_unknown():
    assert core.provider_for_model("", surface=None) == "unknown"
    assert core.provider_for_model(None, surface=None) == "unknown"
    assert core.provider_for_model(None) == "unknown"
