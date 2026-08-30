"""queue_flags: the pure bridge that turns a discovery-gate verdict into a row edit the review
console renders. Hermetic -- no network, no Supabase."""
import classify_page
import queue_flags as qf


# --- upsert_flag: replace-or-append, never stack ------------------------------------------

def test_upsert_appends_when_absent():
    assert qf.upsert_flag([], qf.CLASSIFY_PREFIX, "classify: program (high)") == \
        ["classify: program (high)"]


def test_upsert_replaces_prior_classify_entry():
    flags = ["dead link (404)", "classify: none (low)", "merged 2026-08-30: x"]
    out = qf.upsert_flag(flags, qf.CLASSIFY_PREFIX, "classify: program (high)")
    # the stale classify entry is gone, exactly one remains, the others are untouched
    assert out.count("classify: none (low)") == 0
    assert [f for f in out if f.startswith("classify:")] == ["classify: program (high)"]
    assert "dead link (404)" in out and "merged 2026-08-30: x" in out


def test_upsert_leaves_unrelated_flags_and_handles_none():
    assert qf.upsert_flag(None, qf.CLASSIFY_PREFIX, "classify: none (low)") == \
        ["classify: none (low)"]


# --- flag_class: parse the class token back out -------------------------------------------

def test_flag_class_reads_the_class_token():
    assert qf.flag_class(["classify: program (high); STALE latest year 2012"]) == "program"
    assert qf.flag_class(["classify: first_party_hub (medium)"]) == "first_party_hub"
    assert qf.flag_class(["classify: unreadable (blocked)"]) == "unreadable"
    assert qf.flag_class(["classify: no verdict (unparsed)"]) == "no verdict"


def test_flag_class_none_when_no_classify_flag():
    assert qf.flag_class(["dead link (404)"]) is None
    assert qf.flag_class([]) is None
    assert qf.flag_class(None) is None


def test_flag_class_round_trips_a_real_classification_flag():
    # Whatever Classification.flag() emits, flag_class must recover the class from it.
    c = classify_page.Classification(klass=classify_page.CLASS_THIRD_PARTY_HUB,
                                     confidence=classify_page.CONF_MEDIUM, evidence_verified=True)
    flags = qf.upsert_flag([], qf.CLASSIFY_PREFIX, c.flag())
    assert qf.flag_class(flags) == "third_party_hub"


# --- dedupe_candidate / merge_candidates ---------------------------------------------------

def test_dedupe_candidate_shape_carries_tier_and_marker():
    survivor = {"id": "ec9", "name": "Stanford AI", "url": "https://x.edu/ai"}
    cand = qf.dedupe_candidate(survivor, "confident", 0.9634)
    assert cand["id"] == "ec9" and cand["name"] == "Stanford AI"
    assert cand["confidence"] == "confident"
    assert cand["via"] == qf.DEDUPE_VIA
    assert "cos=0.963" in cand["reason"]


def test_merge_keeps_url_dedupe_entries_and_replaces_our_own():
    existing = [
        {"id": "sub1", "confidence": "strong", "reason": "same normalized url"},   # url_dedupe's
        {"id": "old", "confidence": "hint", "reason": "content match cos=0.910",
         "via": qf.DEDUPE_VIA},                                                     # a prior run's
    ]
    fresh = [qf.dedupe_candidate({"id": "new", "name": "N", "url": "u"}, "adjudicate", 0.951)]
    out = qf.merge_candidates(existing, fresh)
    ids = [c["id"] for c in out]
    assert "sub1" in ids            # url_dedupe's submission candidate is preserved
    assert "old" not in ids         # our stale entry is dropped
    assert "new" in ids             # the fresh one is added
    assert sum(1 for c in out if c.get("via") == qf.DEDUPE_VIA) == 1


def test_merge_from_empty():
    fresh = [qf.dedupe_candidate({"id": "n", "name": "N", "url": "u"}, "proof", 1.0)]
    assert qf.merge_candidates(None, fresh) == fresh
