"""Dedupe confidence: identity-token + hard-field discriminators, proofs, tier combiner. Pure."""
import dedupe_confidence as dc


# ---------- identity tokens ----------

def test_identity_tokens_strips_org_and_structure_keeps_subject():
    toks = dc.identity_tokens("Badger Summer Music Clinic", org="UW-Madison")
    assert "music" in toks and "clinic" in toks  # subject + type kept
    assert "summer" not in toks                   # structure stripped


def test_identity_tokens_singularizes():
    assert set(dc.identity_tokens("Robotics Competitions")) == set(dc.identity_tokens("Robotics Competition"))


# ---------- name relation ----------

def test_name_same_identical():
    assert dc.name_relation("Science Without Borders Challenge", "", "Science Without Borders Challenge", "") == dc.NAME_SAME


def test_name_conflict_music_vs_arts():
    assert dc.name_relation("Badger Summer Music Clinic", "UW", "Badger Summer Arts Clinic", "UW") == dc.NAME_CONFLICT


def test_name_subset_added_qualifier():
    assert dc.name_relation("Davidson Fellows Scholarship", "", "Fellows Scholarship", "") == dc.NAME_SUBSET


def test_name_unknown_when_only_structure_words():
    assert dc.name_relation("Summer Program", "", "Program", "") == dc.NAME_UNKNOWN


# ---------- field relation ----------

def test_field_conflict_on_type():
    rel, fields = dc.field_relation({"type": "Competition", "season": "Summer"},
                                    {"type": "Research", "season": "Summer"})
    assert rel == dc.FIELD_CONFLICT and fields == ["type"]


def test_field_agree_when_present_match():
    rel, fields = dc.field_relation({"type": "Program", "grade_min": 9}, {"type": "Program", "grade_min": 9})
    assert rel == dc.FIELD_AGREE and fields == []


def test_field_unknown_when_no_overlap():
    assert dc.field_relation({"type": "Program"}, {"season": "Summer"})[0] == dc.FIELD_UNKNOWN


# ---------- proofs ----------

def test_same_final_url_after_redirect():
    assert dc.same_final_url("https://cmu.edu/alp/", "https://cmu.edu/alp") is True
    assert dc.same_final_url("https://cmu.edu/a", "https://cmu.edu/b") is False


def test_canonical_extraction_and_match():
    html_a = '<link rel="canonical" href="https://x.edu/prog"/>'
    html_b = "<link rel='canonical' href='https://x.edu/prog/'>"
    ca, cb = dc.extract_canonical(html_a), dc.extract_canonical(html_b)
    assert ca and dc.same_canonical(ca, cb) is True
    assert dc.extract_canonical("<html>no canonical</html>") == ""


# ---------- tier combiner ----------

def test_tier_proof_overrides_everything():
    v = dc.classify_pair(0.10, dc.NAME_CONFLICT, dc.FIELD_CONFLICT, proof=True)
    assert v.tier == dc.TIER_PROOF and v.auto_mergeable


def test_tier_confident_high_sim_same_name_no_conflict():
    v = dc.classify_pair(0.97, dc.NAME_SAME, dc.FIELD_AGREE)
    assert v.tier == dc.TIER_CONFIDENT and v.auto_mergeable


def test_tier_sibling_when_name_conflicts_despite_high_sim():
    v = dc.classify_pair(0.97, dc.NAME_CONFLICT, dc.FIELD_AGREE)
    assert v.tier == dc.TIER_SIBLING and not v.auto_mergeable


def test_same_name_with_field_conflict_is_adjudicate_not_sibling():
    # identical identity + a differing hard field = likely a dup with a data discrepancy
    assert dc.classify_pair(0.97, dc.NAME_SAME, dc.FIELD_CONFLICT).tier == dc.TIER_ADJUDICATE


def test_subset_with_field_conflict_is_sibling():
    # a qualifier PLUS a differing hard field is a genuinely different program
    assert dc.classify_pair(0.97, dc.NAME_SUBSET, dc.FIELD_CONFLICT).tier == dc.TIER_SIBLING


def test_tier_adjudicate_on_subset():
    assert dc.classify_pair(0.97, dc.NAME_SUBSET, dc.FIELD_AGREE).tier == dc.TIER_ADJUDICATE


def test_tier_hint_moderate_sim():
    assert dc.classify_pair(0.91, dc.NAME_SAME, dc.FIELD_AGREE).tier == dc.TIER_HINT


def test_tier_none_low_sim():
    assert dc.classify_pair(0.80, dc.NAME_SAME, dc.FIELD_AGREE).tier == dc.TIER_NONE


def test_cosine_none_uses_discriminators_only():
    assert dc.classify_pair(None, dc.NAME_CONFLICT, dc.FIELD_AGREE).tier == dc.TIER_SIBLING
    assert dc.classify_pair(None, dc.NAME_SAME, dc.FIELD_AGREE).tier == dc.TIER_HINT
    assert dc.classify_pair(None, dc.NAME_UNKNOWN, dc.FIELD_AGREE).tier == dc.TIER_NONE


# ---------- acronym tie-breaker (guard 1) ----------

def test_extract_and_share_parenthetical_acronym():
    assert dc.extract_acronyms("Google Computer Science Summer Institute (CSSI)") == {"CSSI"}
    assert dc.shared_acronym("Foo (CSSI)", "Bar CS Institute (CSSI)") is True
    assert dc.extract_acronyms("Summer (STEM) Program") == set()  # generic acronym excluded


def test_shared_acronym_softens_conflict_to_subset():
    # CS vs Computer Science would be a CONFLICT; the shared (CSSI) recovers it to SUBSET
    r = dc.name_relation("Google Computer Science Summer Institute (CSSI)", "Google",
                         "Google CS Summer Institute (CSSI)", "Google")
    assert r == dc.NAME_SUBSET


# ---------- context guard (guard 2) ----------

def test_org_agrees():
    assert dc.org_agrees("FBI National Academy Associates", "FBI National Academy Associates (FBINAA)") is True
    assert dc.org_agrees("City of Boston", "YMCA of Greater Seattle") is False
    assert dc.org_agrees("", "Stanford") is None


def test_same_context_via_org_when_domains_differ():
    a = {"org": "Stanford University", "url": "https://empowerly.com/x"}
    b = {"org": "Stanford University", "url": "https://med.stanford.edu/y"}
    assert dc.same_context(a, b) is True   # different domain, same org -> same institution


def test_same_context_false_across_orgs():
    a = {"org": "City of Boston", "url": "https://boston.gov/x"}
    b = {"org": "YMCA", "url": "https://ymca.net/y"}
    assert dc.same_context(a, b) is False


def test_context_guard_downgrades_cross_institution_confident():
    # identical generic name, high sim, but different institution -> NOT auto-merge
    assert dc.classify_pair(0.97, dc.NAME_SAME, dc.FIELD_AGREE, context_ok=False).tier == dc.TIER_ADJUDICATE
    assert dc.classify_pair(0.97, dc.NAME_SAME, dc.FIELD_AGREE, context_ok=True).tier == dc.TIER_CONFIDENT
    assert dc.classify_pair(0.97, dc.NAME_SAME, dc.FIELD_AGREE, context_ok=None).tier == dc.TIER_CONFIDENT


def test_classify_rows_generic_name_across_orgs_is_not_auto_merge():
    a = {"name": "Youth Leadership Program", "org": "City of Boston", "url": "https://boston.gov/x"}
    b = {"name": "Youth Leadership Program", "org": "YMCA", "url": "https://ymca.net/y"}
    assert dc.classify_rows(a, b, cosine=0.97).tier == dc.TIER_ADJUDICATE


def test_classify_rows_same_org_generic_name_still_confident():
    a = {"name": "Youth Leadership Program", "org": "FBI National Academy Associates",
         "url": "https://facebook.com/x"}
    b = {"name": "Youth Leadership Program", "org": "FBI National Academy Associates (FBINAA)",
         "url": "https://fbinaa.org/y"}
    assert dc.classify_rows(a, b, cosine=0.955).tier == dc.TIER_CONFIDENT


# ---------- integration ----------

def test_classify_rows_sibling_on_name_conflict():
    a = {"name": "Badger Summer Music Clinic", "org": "UW", "type": "Program"}
    b = {"name": "Badger Summer Arts Clinic", "org": "UW", "type": "Program"}
    assert dc.classify_rows(a, b, cosine=0.96).tier == dc.TIER_SIBLING


def test_classify_rows_confident_on_identical():
    a = {"name": "NYU GSTEM", "org": "NYU", "type": "Program"}
    b = {"name": "NYU GSTEM", "org": "NYU", "type": "Program"}
    assert dc.classify_rows(a, b, cosine=0.98).tier == dc.TIER_CONFIDENT


def test_classify_rows_proof_via_final_url():
    a = {"name": "X", "org": "O"}
    b = {"name": "Y", "org": "O"}
    v = dc.classify_rows(a, b, cosine=0.1, final_a="https://x.edu/p", final_b="https://x.edu/p/")
    assert v.tier == dc.TIER_PROOF
