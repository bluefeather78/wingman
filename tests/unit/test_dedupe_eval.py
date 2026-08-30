"""Dedupe eval harness: pair generation, labelling, representations, the separation metric.

All pure. The paid --run path (Supabase read, page fetch, embedding) is not exercised here.
"""
import dedupe_eval as de


def _row(rid, name, url, org="", summary="", typ="", elig=""):
    return {"id": rid, "name": name, "url": url, "org": org, "summary": summary,
            "type": typ, "eligibility": elig}


# ---------- pair generation ----------

def test_pairs_same_domain_diff_url_name_similar():
    rows = [_row("a", "Accelerated Learning Program", "https://cmu.edu/alp/"),
            _row("b", "Accelerated Learning Program", "https://cmu.edu/accelerated-learning-program/")]
    pairs = de.candidate_pairs(rows)
    assert len(pairs) == 1


def test_no_pair_across_domains():
    rows = [_row("a", "Summer Science Program", "https://cmu.edu/x"),
            _row("b", "Summer Science Program", "https://mit.edu/x")]
    assert de.candidate_pairs(rows) == []


def test_no_pair_same_url():
    rows = [_row("a", "Program", "https://cmu.edu/x"),
            _row("b", "Program", "https://cmu.edu/x")]
    assert de.candidate_pairs(rows) == []


def test_no_pair_when_names_dissimilar():
    rows = [_row("a", "Robotics Camp", "https://cmu.edu/x"),
            _row("b", "Creative Writing Workshop", "https://cmu.edu/y")]
    assert de.candidate_pairs(rows) == []


# ---------- labelling ----------

def test_label_alias_from_known_id():
    known = next(iter(de.KNOWN_ALIAS_IDS))
    assert de.label_pair(_row(known, "X", "u1"), _row("zzz", "X", "u2")) == de.LABEL_ALIAS


def test_label_distinct_by_default():
    assert de.label_pair(_row("p", "X", "u1"), _row("q", "X", "u2")) == de.LABEL_DISTINCT


def test_label_override_wins():
    ov = {frozenset(("p", "q")): de.LABEL_ALIAS}
    assert de.label_pair(_row("p", "X", "u1"), _row("q", "X", "u2"), ov) == de.LABEL_ALIAS


def test_load_label_overrides_roundtrip(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text('{"a":"p","b":"q","label":"alias"}\nbad\n{"a":"r","b":"s","label":"nope"}\n',
                    encoding="utf-8")
    ov = de.load_label_overrides(str(path))
    assert ov == {frozenset(("p", "q")): de.LABEL_ALIAS}  # bad line + invalid label dropped


# ---------- representations ----------

def test_repr_fields_joins_present_fields_only():
    r = _row("a", "Name", "u", org="Org", summary="", typ="Competition", elig="Grades 9-12")
    text = de.repr_fields(r)
    assert "Name" in text and "Org" in text and "Competition" in text and "Grades 9-12" in text
    assert "\n\n" not in text  # no blank line where summary was empty


# ---------- separation metric ----------

def test_separation_perfectly_separable():
    s = de.separation([(de.LABEL_ALIAS, 0.95), (de.LABEL_ALIAS, 0.97),
                       (de.LABEL_DISTINCT, 0.50), (de.LABEL_DISTINCT, 0.60)])
    assert s["clean_gap"] > 0
    assert s["recall"] == 1.0 and s["fp"] == 0 and s["precision"] == 1.0


def test_separation_overlap_picks_a_threshold():
    s = de.separation([(de.LABEL_ALIAS, 0.90), (de.LABEL_ALIAS, 0.60),
                       (de.LABEL_DISTINCT, 0.70), (de.LABEL_DISTINCT, 0.50)])
    assert s["clean_gap"] < 0  # populations overlap
    assert s["best_threshold"] is not None and 0.0 <= s["youden_j"] <= 1.0


def test_separation_one_class_is_unmeasurable():
    s = de.separation([(de.LABEL_ALIAS, 0.9), (de.LABEL_ALIAS, 0.8)])
    assert s["best_threshold"] is None and s["n_distinct"] == 0
